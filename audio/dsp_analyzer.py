"""실시간 다채널 DSP 분석 엔진.

오디오 콜백(audio_stream.py)은 블록을 큐에 넣기만 하고, 이 모듈의 AnalysisWorker 스레드가 큐를 비우며
분석한다. GUI 는 snapshot() 을 타이머로 읽는다 (콜백·분석·GUI 분리).

분석 항목: RMS/피크/클리핑(디지털 풀스케일 근접), 무음/신호 부족, FFT 스펙트럼(창 함수·FFT 크기·홉 설정 가능),
1/24 옥타브 로그 대역 스펙트럼, 장기 평균 스펙트럼(어택 프레임 제외), 대역별 지속성(공진 후보 판단용),
기본음(f0) 추적, 어택(트랜지언트) 비율.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from common.latency import LATENCY

from .spectrum import LogBands, MultiChannelStft, db, estimate_f0, smooth_octave

log = logging.getLogger(__name__)


@dataclass
class AnalyzerConfig:
    n_fft: int = 4096
    hop: int = 1024
    window: str = "hann"
    silence_db: float = -60.0          # 이 아래는 무음
    low_signal_db: float = -45.0       # 이 아래는 분석 신호 부족
    clip_threshold: float = 0.999      # 디지털 풀스케일 근접 (USB 수신 신호 기준)
    clip_hold_s: float = 2.0
    long_tc_s: float = 12.0            # 장기 평균 시간상수 (EQ 판단용, 짧으면 음 몇 개에 좌우됨)
    short_tc_s: float = 0.15           # 화면 표시 스펙트럼
    transient_jump_db: float = 6.0
    prominence_db: float = 3.0


@dataclass
class ChannelSnapshot:
    index: int                 # 0-based 입력(USB) 채널
    rms_db: float
    peak_db: float
    peak_hold_db: float
    clipping: bool
    clip_count: int
    silent: bool
    low_signal: bool
    active_seconds: float
    centers: np.ndarray
    display_db: np.ndarray
    long_db: np.ndarray
    persistence: np.ndarray
    f0_median: float | None
    f0_spread_semitones: float | None
    f0_confidence: float
    transient_ratio: float
    updated: float = field(default_factory=time.monotonic)


class DspAnalyzer:
    def __init__(self, n_channels: int, samplerate: int, cfg: AnalyzerConfig | None = None):
        self.cfg = cfg or AnalyzerConfig()
        self.n = n_channels
        self.sr = samplerate
        c = self.cfg
        self.stft = MultiChannelStft(n_channels, samplerate, c.n_fft, c.hop, c.window)
        self.bands = LogBands(self.stft.freqs)
        nb = len(self.bands)
        frame_dt = c.hop / samplerate
        self.a_long = 1.0 - np.exp(-frame_dt / c.long_tc_s)
        self.a_short = 1.0 - np.exp(-frame_dt / c.short_tc_s)
        self.frame_dt = frame_dt
        self.rms_db = np.full(n_channels, -120.0)
        self.peak_db = np.full(n_channels, -120.0)
        self.peak_hold = np.full(n_channels, -120.0)
        self.clip_count = np.zeros(n_channels, int)
        self.last_clip = np.full(n_channels, -1e9)
        self.display_pow = np.full((nb, n_channels), 1e-12)
        self.long_pow = np.full((nb, n_channels), 1e-12)
        self.persist = np.zeros((nb, n_channels))
        self.frame_level_db = np.full(n_channels, -120.0)
        self.active_seconds = np.zeros(n_channels)
        self.active_frames = np.zeros(n_channels, int)
        self.transient_frames = np.zeros(n_channels, int)
        self._f0_hist = [[] for _ in range(n_channels)]
        self._f0_conf = np.zeros(n_channels)
        self.frame_listeners = []   # fn(bands_db, prom_db, f0, f0_conf, active_mask, t)
        self._lock = threading.Lock()

    # --------------------------------------------------------------
    def reset_long_term(self, ch: int | None = None) -> None:
        """EQ 변경 후 재분석을 위해 장기 통계를 초기화."""
        with self._lock:
            sl = slice(None) if ch is None else slice(ch, ch + 1)
            self.long_pow[:, sl] = 1e-12
            self.persist[:, sl] = 0
            self.active_seconds[sl] = 0
            self.active_frames[sl] = 0
            self.transient_frames[sl] = 0
            for i in range(self.n)[sl]:
                self._f0_hist[i] = []

    def process_block(self, block: np.ndarray) -> int:
        if block.ndim != 2 or block.shape[1] != self.n:
            raise ValueError(f"블록 형태 {block.shape} 가 채널 수 {self.n} 와 다르다")
        c = self.cfg
        now = time.monotonic()
        x = block.astype(np.float32, copy=False)
        absx = np.abs(x)
        peak = absx.max(axis=0)
        rms = np.sqrt(np.mean(x.astype(np.float64) ** 2, axis=0))
        clips = (absx >= c.clip_threshold).sum(axis=0)
        frames = self.stft.feed(x)
        with self._lock:
            self.rms_db = 20 * np.log10(np.maximum(rms, 1e-9))
            self.peak_db = 20 * np.log10(np.maximum(peak, 1e-9))
            decay = 20.0 * block.shape[0] / self.sr   # 20 dB/s 하강
            self.peak_hold = np.maximum(self.peak_hold - decay, self.peak_db)
            self.clip_count += clips
            self.last_clip[clips > 0] = now
            for p in frames:
                self._process_frame(p, now)
        return len(frames)

    def _process_frame(self, power: np.ndarray, now: float) -> None:
        c = self.cfg
        bp = self.bands.apply(power)                       # (nb, ch)
        bdb = db(bp)
        level = db(power.sum(axis=0))                      # 프레임 총 파워 dB
        active = level > c.low_signal_db
        transient = (level - self.frame_level_db) > c.transient_jump_db
        self.frame_level_db = np.where(active, 0.7 * self.frame_level_db + 0.3 * level, level)
        self.display_pow += self.a_short * (bp - self.display_pow)
        steady = active & ~transient
        # 장기 평균과 지속성은 어택 프레임을 제외한 유효 프레임만 반영
        m = steady[None, :]
        self.long_pow = np.where(m, self.long_pow + self.a_long * (bp - self.long_pow), self.long_pow)
        prom = bdb - smooth_octave(bdb, self.bands.per_octave, 1 / 3)
        ind = (prom > c.prominence_db).astype(float)
        self.persist = np.where(m, self.persist + self.a_long * (ind - self.persist), self.persist)
        self.active_seconds += active * self.frame_dt
        self.active_frames += active
        self.transient_frames += active & transient
        f0, conf = estimate_f0(power, self.stft.freqs)
        self._f0_conf = 0.9 * self._f0_conf + 0.1 * conf
        for i in np.nonzero(steady & (conf > 0.35))[0]:
            h = self._f0_hist[i]
            h.append(float(f0[i]))
            if len(h) > 400:
                del h[:100]
        for fn in self.frame_listeners:
            fn(bdb, prom, f0, conf, active, now)

    # --------------------------------------------------------------
    def snapshot(self, ch: int) -> ChannelSnapshot:
        c = self.cfg
        with self._lock:
            hist = np.array(self._f0_hist[ch][-200:])
            if len(hist) >= 10:
                f0m = float(np.median(hist))
                semis = 12 * np.log2(hist / f0m)
                spread = float(np.percentile(semis, 75) - np.percentile(semis, 25))
            else:
                f0m, spread = None, None
            af = max(1, self.active_frames[ch])
            return ChannelSnapshot(
                index=ch,
                rms_db=float(self.rms_db[ch]),
                peak_db=float(self.peak_db[ch]),
                peak_hold_db=float(self.peak_hold[ch]),
                clipping=(time.monotonic() - self.last_clip[ch]) < c.clip_hold_s,
                clip_count=int(self.clip_count[ch]),
                silent=bool(self.rms_db[ch] < c.silence_db),
                low_signal=bool(self.rms_db[ch] < c.low_signal_db),
                active_seconds=float(self.active_seconds[ch]),
                centers=self.bands.centers,
                display_db=db(self.display_pow[:, ch]).copy(),
                long_db=db(self.long_pow[:, ch]).copy(),
                persistence=self.persist[:, ch].copy(),
                f0_median=f0m,
                f0_spread_semitones=spread,
                f0_confidence=float(self._f0_conf[ch]),
                transient_ratio=float(self.transient_frames[ch] / af),
            )


class AnalysisWorker(threading.Thread):
    """오디오 입력 큐 → DspAnalyzer. 분석 지연(블록 수신 → 분석 완료)을 기록한다."""

    def __init__(self, source_queue: "queue.Queue", analyzer: DspAnalyzer):
        super().__init__(name="dsp-analysis", daemon=True)
        self.q = source_queue
        self.analyzer = analyzer
        self._stop = threading.Event()
        self.blocks = 0
        self.errors = 0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            block, t_rx = item[0], item[1]
            try:
                if block.shape[1] != self.analyzer.n:
                    continue
                self.analyzer.process_block(block)
                self.blocks += 1
                LATENCY.add("analysis", (time.monotonic() - t_rx) * 1000)
            except Exception:
                self.errors += 1
                log.exception("분석 오류")
