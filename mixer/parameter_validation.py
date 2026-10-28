"""파라미터 사전 검증.

* 쓰기 허용 키(WRITABLE_KEYS)만 통과. 프리앰프 게인, 팬텀, 페이더, 라우팅 등은 항상 거부.
* 숫자 범위는 장비가 ',s ?' 로 알려준 범위를 사용한다. 장비 범위를 모르면 기본적으로 쓰기를 거부한다.
  (설정 allow_fallback_ranges=True 일 때만 아래 FALLBACK_RANGES 를 쓴다. 이 값은 공식 문서에서
  확인한 값이 아니라, 일반 콘솔 EQ 범위 안쪽으로 좁게 잡은 보수적 값이다.)
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass

from . import wing_addresses as wa


class ValidationError(ValueError):
    pass


# 공식 문서 값 아님. 장비 범위 질의가 실패했을 때 사용자가 명시적으로 허용한 경우에만 사용.
FALLBACK_RANGES = {
    "gain": (-12.0, 12.0),
    "freq": (40.0, 16000.0),
    "q": (0.5, 8.0),
}


@dataclass
class Range:
    min: float
    max: float
    source: str  # 'device' | 'fallback'


def readback_tolerance(kind: str, target: float) -> float:
    """재조회 값 비교 허용 오차. 장비의 내부 양자화(스텝)를 감안한다."""
    if kind == "gain":
        return 0.15
    if kind == "freq":
        return max(1.0, abs(target) * 0.02)
    if kind == "q":
        return max(0.03, abs(target) * 0.05)
    return 0.0


def values_equal(key: str, a, b) -> bool:
    kind = wa.key_kind(key)
    if kind in ("gain", "freq", "q"):
        try:
            return abs(float(a) - float(b)) <= readback_tolerance(kind, float(b))
        except (TypeError, ValueError):
            return False
    if kind == "bool":
        return bool(int(a)) == bool(int(b))
    return str(a) == str(b)


class ParameterValidator:
    def __init__(self, allow_fallback_ranges: bool = False):
        self.allow_fallback_ranges = allow_fallback_ranges
        self._ranges: dict[str, Range] = {}  # key(채널 무관) → 범위
        self._lock = threading.Lock()

    def learn_from_description(self, key: str, desc: dict | None) -> None:
        if not desc or "min" not in desc:
            return
        with self._lock:
            self._ranges[key] = Range(float(desc["min"]), float(desc["max"]), "device")

    def range_for(self, key: str) -> Range | None:
        with self._lock:
            r = self._ranges.get(key)
        if r:
            return r
        kind = wa.key_kind(key)
        if self.allow_fallback_ranges and kind in FALLBACK_RANGES:
            lo, hi = FALLBACK_RANGES[kind]
            return Range(lo, hi, "fallback")
        return None

    def known_keys(self) -> list[str]:
        with self._lock:
            return sorted(self._ranges)

    def validate(self, ch: int, key: str, value, max_ch: int = wa.DEFAULT_MAX_CH):
        if not (1 <= ch <= max_ch):
            raise ValidationError(f"채널 번호 범위 밖: {ch}")
        if key not in wa.WRITABLE_KEYS:
            raise ValidationError(f"쓰기 허용되지 않은 파라미터: {key}")
        addr = wa.ch_param(ch, key)
        if any(s in addr for s in wa.FORBIDDEN_SUBSTRINGS):
            raise ValidationError(f"금지된 주소: {addr}")
        kind = wa.key_kind(key)
        if kind == "bool":
            if value not in (0, 1, True, False):
                raise ValidationError(f"{key} 는 0/1 이어야 한다")
            return int(bool(value))
        if kind in ("gain", "freq", "q"):
            try:
                v = float(value)
            except (TypeError, ValueError):
                raise ValidationError(f"{key} 값이 숫자가 아니다: {value!r}")
            if not math.isfinite(v):
                raise ValidationError(f"{key} 값이 유한하지 않다")
            r = self.range_for(key)
            if r is None:
                raise ValidationError(
                    f"{key} 의 장비 범위를 모른다. 연결 후 범위 조회(',s ?')가 성공해야 쓸 수 있다")
            if not (r.min <= v <= r.max):
                raise ValidationError(f"{key}={v} 가 범위 [{r.min}, {r.max}] ({r.source}) 밖이다")
            return v
        raise ValidationError(f"지원하지 않는 파라미터 종류: {key}")
