"""FFT/STFT 및 로그 주파수 대역 변환 (모든 채널을 한 번에 벡터 연산)."""
from __future__ import annotations

import numpy as np
from scipy.signal import get_window

EPS = 1e-12


def db(x, floor: float = -160.0):
    return np.maximum(10.0 * np.log10(np.maximum(x, EPS)), floor)


class MultiChannelStft:
    """채널 묶음 입력을 받아 hop 마다 파워 스펙트럼을 낸다.

    feed() 는 (frames, ch) 블록을 받고 완성된 프레임들의 파워 스펙트럼 리스트 [(n_bins, ch), ...] 와
    각 프레임의 시간영역 RMS/피크를 돌려준다.
    """

    def __init__(self, n_channels: int, samplerate: int, n_fft: int = 4096, hop: int = 1024,
                 window: str = "hann"):
        if hop <= 0 or hop > n_fft:
            raise ValueError("hop 은 1..n_fft")
        self.n_channels = n_channels
        self.sr = samplerate
        self.n_fft = n_fft
        self.hop = hop
        self.window = get_window(window, n_fft, fftbins=True).astype(np.float32)
        # 파서발 보정: 대역 내 빈 파워를 더하면 그 대역 신호의 평균제곱(mean square)이 되도록 한다
        self.scale = 2.0 / (n_fft * np.sum(self.window.astype(np.float64) ** 2))
        self.freqs = np.fft.rfftfreq(n_fft, 1.0 / samplerate)
        self._buf = np.zeros((n_fft, n_channels), np.float32)
        self._fill = 0          # 버퍼에 쌓인 샘플 수 (최대 n_fft)
        self._since_hop = 0

    def reset(self) -> None:
        self._buf[:] = 0
        self._fill = 0
        self._since_hop = 0

    def feed(self, block: np.ndarray) -> list[np.ndarray]:
        out = []
        pos = 0
        n = block.shape[0]
        while pos < n:
            need = self.hop - self._since_hop
            take = min(need, n - pos)
            chunk = block[pos:pos + take]
            self._buf = np.roll(self._buf, -take, axis=0)
            self._buf[-take:] = chunk
            self._fill = min(self.n_fft, self._fill + take)
            self._since_hop += take
            pos += take
            if self._since_hop >= self.hop:
                self._since_hop = 0
                if self._fill >= self.n_fft:
                    spec = np.fft.rfft(self._buf * self.window[:, None], axis=0)
                    out.append((np.abs(spec) ** 2) * self.scale)
        return out


class LogBands:
    """선형 FFT 빈을 로그 간격 대역(기본 1/24 옥타브)으로 묶는다. 저역에서 대역이 빈보다 좁으면 가까운 빈을 쓴다."""

    def __init__(self, freqs: np.ndarray, f_lo: float = 20.0, f_hi: float = 20000.0, per_octave: int = 24):
        f_hi = min(f_hi, freqs[-1])
        n = int(np.floor(np.log2(f_hi / f_lo) * per_octave)) + 1
        self.centers = f_lo * 2.0 ** (np.arange(n) / per_octave)
        half = 2.0 ** (0.5 / per_octave)
        df = freqs[1] - freqs[0]
        lo = np.floor(self.centers / half / df).astype(int)
        hi = np.ceil(self.centers * half / df).astype(int)
        lo = np.clip(lo, 0, len(freqs) - 1)
        hi = np.clip(np.maximum(hi, lo + 1), 1, len(freqs))
        self.lo, self.hi = lo, hi
        self.per_octave = per_octave

    def __len__(self) -> int:
        return len(self.centers)

    def apply(self, power: np.ndarray) -> np.ndarray:
        """power: (n_bins, ch) → 대역 평균 파워 (n_bands, ch)"""
        cs = np.concatenate([np.zeros((1,) + power.shape[1:]), np.cumsum(power, axis=0)], axis=0)
        return (cs[self.hi] - cs[self.lo]) / (self.hi - self.lo)[:, None]

    def index_of(self, f: float) -> int:
        return int(np.argmin(np.abs(np.log2(self.centers / f))))

    def mask(self, f_lo: float, f_hi: float) -> np.ndarray:
        return (self.centers >= f_lo) & (self.centers < f_hi)


def smooth_octave(x_db: np.ndarray, per_octave: int, width_oct: float = 1 / 3) -> np.ndarray:
    """로그 대역 dB 스펙트럼을 width_oct 옥타브 폭으로 이동평균 (가장자리 보정). x_db: (n_bands, ch)"""
    w = max(1, int(round(width_oct * per_octave)))
    if w % 2 == 0:
        w += 1
    pad = w // 2
    xp = np.pad(x_db, ((pad, pad), (0, 0)), mode="edge")
    cs = np.concatenate([np.zeros((1, xp.shape[1])), np.cumsum(xp, axis=0)], axis=0)
    return (cs[w:] - cs[:-w]) / w


def estimate_f0(power: np.ndarray, freqs: np.ndarray, f_lo: float = 40.0, f_hi: float = 1000.0,
                n_harm: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """하모닉 곱 스펙트럼(HPS)으로 채널별 기본음 추정. 반환 (f0[ch], 신뢰도 0..1[ch])."""
    nb = power.shape[0]
    kmax = (nb - 1) // n_harm
    lp = np.log(np.maximum(power, EPS))
    hps = lp[:kmax].copy()
    for h in range(2, n_harm + 1):
        hps += lp[: kmax * h: h][:kmax]
    lo = max(1, int(f_lo / freqs[1]))
    hi = min(kmax, int(f_hi / freqs[1]))
    if hi <= lo:
        return np.zeros(power.shape[1]), np.zeros(power.shape[1])
    seg = hps[lo:hi]
    idx = np.argmax(seg, axis=0)
    f0 = freqs[lo + idx]
    med = np.median(seg, axis=0)
    peak = seg[idx, np.arange(seg.shape[1])]
    conf = np.clip((peak - med) / (n_harm * 6.0), 0.0, 1.0)  # 하모닉당 ~6 (ln) 이상 차이면 확신
    return f0, conf
