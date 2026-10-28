"""단계별 지연 측정 (오디오 입력 / 분석 / 화면 갱신 / 믹서 제어 를 구분해 기록)."""
from __future__ import annotations

import threading
from collections import deque


class LatencyTracker:
    STAGES = ("audio_input", "analysis", "gui", "mixer_control")
    LABELS = {"audio_input": "오디오 입력", "analysis": "분석", "gui": "화면 갱신", "mixer_control": "믹서 제어"}

    def __init__(self, window: int = 200):
        self._d = {s: deque(maxlen=window) for s in self.STAGES}
        self._lock = threading.Lock()

    def add(self, stage: str, ms: float | None) -> None:
        if ms is None:
            return
        with self._lock:
            self._d[stage].append(float(ms))

    def summary(self) -> dict[str, dict]:
        out = {}
        with self._lock:
            for s, d in self._d.items():
                if d:
                    v = sorted(d)
                    out[s] = {"avg": sum(v) / len(v), "p95": v[int(0.95 * (len(v) - 1))], "max": v[-1], "n": len(v)}
        return out

    def text(self) -> str:
        parts = []
        for s, x in self.summary().items():
            parts.append(f"{self.LABELS[s]} {x['avg']:.1f}ms (p95 {x['p95']:.1f})")
        return " · ".join(parts) or "측정 전"


LATENCY = LatencyTracker()
