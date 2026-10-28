"""하울링(피드백) 의심 보조 감지.

단일 FFT 피크로 하울링을 확정하지 않는다. 다음을 종합해 의심도(0..1)를 계산한다.
 * 돌출도: 1/3 옥타브 주변 평균보다 얼마나 좁고 크게 튀어나왔는가
 * 지속성: 같은 주파수(±1/24 옥타브)에 얼마나 오래 머무는가 (악기 음은 음정이 바뀌면 이동한다)
 * 추세: 레벨이 유지·증가하는가 (자연 감쇠하는 음은 감점)
 * 배음 관계: 현재 기본음(f0)의 정수배에 해당하면 악기 배음일 가능성이 커서 크게 감점
의심도가 낮으면 경고만 표시하고, 노치 추천은 높은 의심도에서만 만든다.
이 모듈은 하울링을 완벽히 감지/제거한다고 보장하지 않는다. 마이크 위치, 스피커 배치, 게인 구조,
공간 음향 문제는 별도로 다뤄야 한다.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np


@dataclass
class FeedbackSuspect:
    ch: int
    freq: float
    score: float
    duration_s: float
    prominence_db: float
    harmonic_of_f0: bool

    @property
    def level(self) -> str:
        if self.score >= 0.8:
            return "높음"
        if self.score >= 0.5:
            return "경고"
        return "낮음"


class FeedbackMonitor:
    def __init__(self, centers: np.ndarray, n_channels: int, frame_dt: float,
                 min_prominence_db: float = 10.0, min_duration_s: float = 0.7):
        self.centers = centers
        self.n = n_channels
        self.dt = frame_dt
        self.min_prom = min_prominence_db
        self.min_dur = min_duration_s
        nb = len(centers)
        self.dur = np.zeros((nb, n_channels))
        self.level_ema = np.full((nb, n_channels), -120.0)
        self.trend = np.zeros((nb, n_channels))
        self.last_prom = np.zeros((nb, n_channels))
        self._per_oct = 1.0 / np.log2(centers[1] / centers[0])
        self.harm = np.zeros((nb, n_channels))   # 배음 일치 비율 (EMA)
        self.level_now = np.full((nb, n_channels), -120.0)
        self.f0 = np.zeros(n_channels)
        self.f0_conf = np.zeros(n_channels)
        self._lock = threading.Lock()

    def on_frame(self, bands_db, prom_db, f0, f0_conf, active, t) -> None:
        with self._lock:
            cand = (prom_db > self.min_prom) & active[None, :]
            self.dur = np.where(cand, self.dur + self.dt, np.maximum(0.0, self.dur - 3 * self.dt))
            prev = self.level_ema
            self.level_ema = np.where(cand, 0.8 * prev + 0.2 * bands_db, bands_db)
            self.trend = np.where(cand, 0.9 * self.trend + 0.1 * (self.level_ema - prev), 0.0)
            self.last_prom = prom_db
            self.level_now = bands_db
            # 현재 프레임의 f0 정수배(±10% 간격)에 해당하는지. 고정 주파수 하울링은 음정이 바뀌면 일치율이 낮아진다.
            ratio = self.centers[:, None] / np.maximum(f0[None, :], 1.0)
            k = np.round(ratio)
            match = (k >= 1) & (k <= 10) & (np.abs(ratio - k) < 0.1) & (f0_conf[None, :] > 0.35)
            # 배음이라면 이웃 배음(f±f0)도 돌출돼 있어야 한다. 순음 하울링은 이웃 배음이 없다.
            support = np.zeros_like(match)
            for sign in (-1.0, 1.0):
                fn = self.centers[:, None] + sign * f0[None, :]
                ok = (fn > self.centers[0]) & (fn < self.centers[-1])
                idx = np.clip(np.round(np.log2(np.maximum(fn, 1e-3) / self.centers[0]) * self._per_oct), 0,
                              len(self.centers) - 1).astype(int)
                support |= ok & (np.take_along_axis(prom_db, idx, axis=0) > 6.0)
            match &= support
            self.harm = np.where(cand, 0.95 * self.harm + 0.05 * match, self.harm)

    def suspects(self, ch: int, top: int = 3) -> list[FeedbackSuspect]:
        with self._lock:
            d = self.dur[:, ch]
            # 인접 대역으로 이동하는 경우를 하나로 본다
            dd = np.maximum(d, np.maximum(np.roll(d, 1), np.roll(d, -1)))
            # 지속시간이 같으면 현재 레벨이 큰 대역을 우선 (톤이 인접 대역으로 번지는 것을 하나로 묶음)
            order = np.lexsort((-self.level_now[:, ch], -np.round(dd, 1)))
            out, used = [], []
            for b in order[: top * 6]:
                if dd[b] < self.min_dur or any(abs(b - u) <= 3 for u in used):
                    continue
                used.append(b)
                lo, hi = max(b - 2, 0), b + 3
                lv = 10 ** (self.level_now[lo:hi, ch] / 10)
                f = float(np.sum(self.centers[lo:hi] * lv) / np.sum(lv))
                prom = float(max(self.last_prom[lo:hi, ch]))
                harm = bool(self.harm[lo:hi, ch].max() > 0.5)
                s_dur = np.clip((dd[b] - self.min_dur) / 2.0, 0, 1)
                s_prom = np.clip((prom - self.min_prom) / 12.0, 0, 1)
                s = 0.25 + 0.45 * s_dur + 0.3 * s_prom
                if self.trend[b, ch] < -0.05:
                    s *= 0.7   # 감쇠 중
                if harm:
                    s *= 0.4   # 악기 배음 가능성
                out.append(FeedbackSuspect(ch, f, float(np.clip(s, 0, 1)), float(dd[b]), prom, harm))
                if len(out) >= top:
                    break
            return sorted(out, key=lambda x: -x.score)
