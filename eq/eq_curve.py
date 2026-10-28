"""EQ 커브(크기 응답) 계산 — 화면 표시와 '적용 후 예상값' 계산용 근사 모델.

WING 내부 필터의 정확한 수식은 공개 문서에서 확인하지 못했다. 여기서는 일반적인 RBJ 쿡북 바이쿼드
(피킹/셸빙)와 버터워스형 하이패스 근사를 쓴다. 따라서 실제 믹서 커브와 세부 모양이 다를 수 있다.
"""
from __future__ import annotations

import numpy as np

from mixer import wing_addresses as wa

SR = 48000.0


def _biquad_mag_db(b, a, f: np.ndarray) -> np.ndarray:
    w = 2 * np.pi * f / SR
    z = np.exp(-1j * w)
    h = (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)
    return 20 * np.log10(np.maximum(np.abs(h), 1e-9))


def peaking_db(f, f0, gain_db, q):
    if abs(gain_db) < 1e-6:
        return np.zeros_like(f)
    A = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * min(f0, SR * 0.49) / SR
    alpha = np.sin(w0) / (2 * max(q, 0.05))
    b = [1 + alpha * A, -2 * np.cos(w0), 1 - alpha * A]
    a = [1 + alpha / A, -2 * np.cos(w0), 1 - alpha / A]
    return _biquad_mag_db(b, a, f)


def shelf_db(f, f0, gain_db, q, high: bool):
    if abs(gain_db) < 1e-6:
        return np.zeros_like(f)
    A = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * min(f0, SR * 0.49) / SR
    alpha = np.sin(w0) / (2 * max(q, 0.05))
    c, sA = np.cos(w0), 2 * np.sqrt(A) * alpha
    if high:
        b = [A * ((A + 1) + (A - 1) * c + sA), -2 * A * ((A - 1) + (A + 1) * c), A * ((A + 1) + (A - 1) * c - sA)]
        a = [(A + 1) - (A - 1) * c + sA, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - sA]
    else:
        b = [A * ((A + 1) - (A - 1) * c + sA), 2 * A * ((A - 1) - (A + 1) * c), A * ((A + 1) - (A - 1) * c - sA)]
        a = [(A + 1) + (A - 1) * c + sA, -2 * ((A - 1) + (A + 1) * c), (A + 1) + (A - 1) * c - sA]
    return _biquad_mag_db(b, a, f)


def highpass_db(f, fc, slope_db_oct: float = 24.0):
    order = max(1, int(round(slope_db_oct / 6)))
    return -10 * np.log10(1 + (fc / np.maximum(f, 1e-3)) ** (2 * order))


def _num(values: dict, key: str, default: float) -> float:
    v = values.get(key, default)
    try:
        return float(v)
    except (TypeError, ValueError):
        try:
            return wa.parse_wing_number(str(v))
        except ValueError:
            return default


def response_db(values: dict, f: np.ndarray) -> np.ndarray:
    """values: 키→값 (eq/on, eq/1g ..., flt/lc, flt/lcf, flt/lcs). 누락 키는 0dB/꺼짐으로 본다."""
    f = np.asarray(f, float)
    out = np.zeros_like(f)
    if int(_num(values, "flt/lc", 0)):
        out += highpass_db(f, _num(values, "flt/lcf", 100.0), _num(values, "flt/lcs", 24.0))
    if int(_num(values, "eq/on", 0)):
        for b in wa.PEQ_BANDS:
            out += peaking_db(f, _num(values, f"eq/{b}f", 1000), _num(values, f"eq/{b}g", 0), _num(values, f"eq/{b}q", 1))
        if str(values.get("eq/leq", "SHV")).upper().startswith("SH"):
            out += shelf_db(f, _num(values, "eq/lf", 80), _num(values, "eq/lg", 0), _num(values, "eq/lq", 1), False)
        else:
            out += peaking_db(f, _num(values, "eq/lf", 80), _num(values, "eq/lg", 0), _num(values, "eq/lq", 1))
        if str(values.get("eq/heq", "SHV")).upper().startswith("SH"):
            out += shelf_db(f, _num(values, "eq/hf", 12000), _num(values, "eq/hg", 0), _num(values, "eq/hq", 1), True)
        else:
            out += peaking_db(f, _num(values, "eq/hf", 12000), _num(values, "eq/hg", 0), _num(values, "eq/hq", 1))
    return out
