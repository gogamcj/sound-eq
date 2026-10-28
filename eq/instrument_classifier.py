"""보조 악기 분류 (규칙 기반, 신뢰도 상한 0.6).

음색이 비슷하거나 여러 악기가 섞이면 틀릴 수 있으므로 '제안'으로만 쓰인다. 자동 보정은 사용자가 직접
확정한 악기 유형(instrument_confirmed)이 있어야만 동작한다 (safety_controller).
"""
from __future__ import annotations

import numpy as np

from audio.dsp_analyzer import ChannelSnapshot

MAX_CONFIDENCE = 0.6


def spectral_centroid(s: ChannelSnapshot) -> float:
    p = 10 ** (s.long_db / 10)
    return float(np.sum(s.centers * p) / max(np.sum(p), 1e-12))


def classify(s: ChannelSnapshot) -> list[tuple[str, float]]:
    if s.silent or s.low_signal or s.active_seconds < 5:
        return []
    cen = spectral_centroid(s)
    f0 = s.f0_median or 0.0
    tr = s.transient_ratio
    scores: dict[str, float] = {}
    if tr > 0.2:
        if cen < 300:
            scores["kick"] = 0.5
        elif cen < 1200:
            scores["tom"] = 0.35
            scores["snare"] = 0.3
        elif cen < 5000:
            scores["snare"] = 0.45
        else:
            scores["overhead"] = 0.45
    else:
        if f0 and f0 < 110 and cen < 600:
            scores["bass_guitar"] = 0.5
        if 85 <= f0 <= 260 and 400 < cen < 3000:
            scores["vocal_male"] = 0.35
            scores["acoustic_guitar"] = 0.2
        if 165 <= f0 <= 700 and 600 < cen < 4000:
            scores["vocal_female"] = 0.35
            scores["violin"] = 0.15
        if s.f0_spread_semitones and s.f0_spread_semitones > 12:
            scores["piano"] = 0.3
        if not scores:
            scores["custom"] = 0.1
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:3]
    return [(k, min(v, MAX_CONFIDENCE)) for k, v in ranked]
