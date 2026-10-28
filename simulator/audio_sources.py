"""시뮬레이션/파일 오디오 입력 (실제 USB 수신이 아님 — is_simulated=True).

* SyntheticAudioSource: 테스트 신호 합성 (음정이 바뀌는 하모닉 신호, 고정 공진, 저역 럼블, 하울링 톤, 무음, 클리핑).
* WavFileAudioSource: 녹음된 멀티트랙 WAV(다채널 1파일)를 실시간 속도로 재생해 분석기에 공급.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np
from scipy.signal import lfilter

from audio.audio_stream import AudioSource
from common.states import AudioState


def peaking_biquad(f0: float, gain_db: float, q: float, sr: int):
    a = 10 ** (gain_db / 40)
    w = 2 * np.pi * f0 / sr
    alpha = np.sin(w) / (2 * q)
    b = np.array([1 + alpha * a, -2 * np.cos(w), 1 - alpha * a])
    aa = np.array([1 + alpha / a, -2 * np.cos(w), 1 - alpha / a])
    return b / aa[0], aa / aa[0]


@dataclass
class SynthSpec:
    kind: str = "harmonic"        # harmonic | silence | noise
    f0_range: tuple = (110.0, 330.0)
    n_harm: int = 12
    rolloff_db_oct: float = -6.0
    level_db: float = -20.0
    note_s: float = 0.4
    resonance: tuple | None = None    # (freq, gain_db, q) 고정 공진
    rumble_db: float | None = None    # 40Hz 이하 저역 잡음 레벨
    feedback: tuple | None = None     # (freq, level_db, start_s) 지속 톤
    clip: bool = False
    noise_db: float = -70.0
    pink_db: float | None = None      # 광대역 핑크 잡음 (현 잡음/숨소리 등 공진을 드러내는 성분)


class SynthChannel:
    def __init__(self, spec: SynthSpec, sr: int, seed: int):
        self.s = spec
        self.sr = sr
        self.rng = np.random.default_rng(seed)
        self.t = 0
        self.phase = np.zeros(spec.n_harm)
        self.f0 = spec.f0_range[0]
        self.next_note = 0
        self.zi_res = None
        self.ba_res = peaking_biquad(*spec.resonance, sr) if spec.resonance else None
        self.fb_phase = 0.0
        self.zi_pink = np.zeros(3)

    def render(self, n: int) -> np.ndarray:
        s, sr = self.s, self.sr
        out = self.rng.standard_normal(n) * 10 ** (s.noise_db / 20)
        if s.kind == "silence":
            self.t += n
            return out * 0.0
        if s.kind == "noise":
            out += self.rng.standard_normal(n) * 10 ** (s.level_db / 20)
        elif s.kind == "harmonic":
            if self.t >= self.next_note:
                lo, hi = s.f0_range
                self.f0 = float(np.exp(self.rng.uniform(np.log(lo), np.log(hi))))
                self.next_note = self.t + int(s.note_s * sr)
            idx = np.arange(1, s.n_harm + 1)
            amps = 10 ** ((s.rolloff_db_oct * np.log2(idx)) / 20)
            amps *= 10 ** (s.level_db / 20) / np.sqrt(np.sum(amps ** 2) / 2)
            w = 2 * np.pi * self.f0 * idx / sr
            ph = self.phase[:, None] + np.outer(w, np.arange(1, n + 1))
            mask = (self.f0 * idx) < sr / 2.2
            out += (amps[mask, None] * np.sin(ph[mask])).sum(axis=0)
            self.phase = ph[:, -1] % (2 * np.pi)
        if s.pink_db is not None and s.kind != "silence":
            # Paul Kellet 의 간이 핑크 잡음 필터
            pb = [0.049922035, -0.095993537, 0.050612699, -0.004408786]
            pa = [1, -2.494956002, 2.017265875, -0.522189400]
            pk, self.zi_pink = lfilter(pb, pa, self.rng.standard_normal(n), zi=self.zi_pink)
            out += pk * 10 ** (s.pink_db / 20) / 0.11
        if self.ba_res is not None:
            b, a = self.ba_res
            if self.zi_res is None:
                self.zi_res = np.zeros(2)
            out, self.zi_res = lfilter(b, a, out, zi=self.zi_res)
        if s.rumble_db is not None:
            r = np.cumsum(self.rng.standard_normal(n)) * 0.02
            r -= r.mean()
            out += np.clip(r, -1, 1) * 10 ** (s.rumble_db / 20) * 3
        if s.feedback and self.t / sr >= s.feedback[2]:
            f, lvl, _ = s.feedback
            ph = self.fb_phase + 2 * np.pi * f * np.arange(1, n + 1) / sr
            out += 10 ** (lvl / 20) * np.sqrt(2) * np.sin(ph)
            self.fb_phase = ph[-1] % (2 * np.pi)
        if s.clip:
            out = np.clip(out * 4.0, -1.0, 1.0)
        self.t += n
        return out.astype(np.float32)


def render_offline(specs: list[SynthSpec], sr: int, seconds: float, block: int = 512, seed: int = 1):
    """테스트용: 블록 리스트 생성 (실시간 대기 없음)."""
    chans = [SynthChannel(s, sr, seed + i) for i, s in enumerate(specs)]
    n_blocks = int(seconds * sr / block)
    for _ in range(n_blocks):
        yield np.stack([c.render(block) for c in chans], axis=1)


class _ThreadedSource(AudioSource):
    is_simulated = True

    def __init__(self, channels: int, samplerate: int, blocksize: int = 512):
        super().__init__(channels, samplerate, blocksize)
        self._run = threading.Event()
        self._th: threading.Thread | None = None
        self.paused = False   # True 면 블록을 내보내지 않음 (장치 분리 시뮬레이션)

    def _next_block(self) -> np.ndarray | None:
        raise NotImplementedError

    def start(self) -> None:
        self._run.set()
        self._set_state(AudioState.STARTING)
        self._th = threading.Thread(target=self._loop, name="sim-audio", daemon=True)
        self._th.start()

    def stop(self) -> None:
        self._run.clear()
        if self._th:
            self._th.join(timeout=2)
        self._set_state(AudioState.STOPPED)

    def _loop(self) -> None:
        period = self.blocksize / self.samplerate
        t_next = time.monotonic()
        while self._run.is_set():
            t_next += period
            if self.paused:
                if time.monotonic() - self.last_block_time > 1.0:
                    self._set_state(AudioState.ERROR)
            else:
                blk = self._next_block()
                if blk is None:
                    self._set_state(AudioState.STOPPED)
                    break
                self._push(blk)
                if self.state != AudioState.RECEIVING:
                    self._set_state(AudioState.RECEIVING)
            delay = t_next - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                t_next = time.monotonic()


class SyntheticAudioSource(_ThreadedSource):
    def __init__(self, specs: list[SynthSpec], samplerate: int = 48000, blocksize: int = 512):
        super().__init__(len(specs), samplerate, blocksize)
        self.chans = [SynthChannel(s, samplerate, i + 7) for i, s in enumerate(specs)]

    def _next_block(self):
        return np.stack([c.render(self.blocksize) for c in self.chans], axis=1)


class WavFileAudioSource(_ThreadedSource):
    def __init__(self, path: str, blocksize: int = 512, loop: bool = True):
        from scipy.io import wavfile
        sr, data = wavfile.read(path)
        if data.ndim == 1:
            data = data[:, None]
        if np.issubdtype(data.dtype, np.integer):
            data = data.astype(np.float32) / np.iinfo(data.dtype).max
        super().__init__(data.shape[1], sr, blocksize)
        self.data = data.astype(np.float32)
        self.pos = 0
        self.loop = loop

    def _next_block(self):
        if self.pos + self.blocksize > len(self.data):
            if not self.loop:
                return None
            self.pos = 0
        b = self.data[self.pos:self.pos + self.blocksize]
        self.pos += self.blocksize
        return b


def demo_band_specs() -> list[tuple[str, str, SynthSpec]]:
    """시뮬레이션 모드 기본 채널 구성: (이름, 악기키, 합성 사양). 실제 악기 녹음이 아니다."""
    return [
        ("Vox M (sim)", "vocal_male", SynthSpec(f0_range=(98, 260), n_harm=24, rolloff_db_oct=-7,
                                                resonance=(320, 8, 3.0), rumble_db=-28)),
        ("Vox F (sim)", "vocal_female", SynthSpec(f0_range=(196, 520), n_harm=18, rolloff_db_oct=-6,
                                                  resonance=(3200, 6, 2.0))),
        ("AcGtr (sim)", "acoustic_guitar", SynthSpec(f0_range=(82, 330), n_harm=30, rolloff_db_oct=-5,
                                                     resonance=(180, 9, 4.0))),
        ("Bass (sim)", "bass_guitar", SynthSpec(f0_range=(41, 98), n_harm=16, rolloff_db_oct=-8)),
        ("Keys (sim)", "piano", SynthSpec(f0_range=(65, 1000), n_harm=20, rolloff_db_oct=-6)),
        ("Feedback test (sim)", "vocal_male", SynthSpec(f0_range=(110, 220), level_db=-30,
                                                        feedback=(2520.0, -18, 3.0))),
        ("Silent (sim)", "unknown", SynthSpec(kind="silence")),
        ("Clipped (sim)", "electric_guitar", SynthSpec(f0_range=(82, 330), level_db=-6, clip=True)),
    ]
