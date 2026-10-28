"""채널 이름/EQ/필터 상태 읽기."""
from __future__ import annotations

import logging

from . import wing_addresses as wa
from .models import ChannelEqState
from .parameter_validation import ParameterValidator
from .protocol_adapter import MixerAdapter, MixerError, MixerTimeout

log = logging.getLogger(__name__)

READ_KEYS = ["name"] + wa.EQ_KEYS + wa.FILTER_KEYS


def read_channel(adapter: MixerAdapter, ch: int, keys: list[str] | None = None) -> ChannelEqState:
    """모든 키를 개별 조회한다. 일부 키가 응답하지 않으면 missing 에 기록 (통신 끊김이면 예외)."""
    state = ChannelEqState(ch=ch)
    timeouts = 0
    for key in keys or READ_KEYS:
        try:
            pv = adapter.read_param(ch, key)
        except MixerTimeout:
            timeouts += 1
            state.missing.append(key)
            if timeouts >= 3 and not state.values:
                raise  # 하나도 응답이 없으면 통신 문제로 본다
            continue
        except MixerError as e:
            log.warning("ch%d %s 읽기 실패: %s", ch, key, e)
            state.missing.append(key)
            continue
        if key == "name":
            state.name = str(pv.value)
        state.values[key] = pv
    return state


def read_values(adapter: MixerAdapter, ch: int, keys: list[str]) -> dict:
    """지정 키만 조회 (적용 전후 확인용). 하나라도 실패하면 예외."""
    return {k: adapter.read_param(ch, k).value for k in keys}


def learn_ranges(adapter: MixerAdapter, validator: ParameterValidator, ch: int = 1) -> dict[str, dict | None]:
    """쓰기 대상 키의 범위를 장비에 질의 (',s ?'). 결과는 validator 에 저장."""
    out = {}
    for key in sorted(wa.WRITABLE_KEYS):
        if wa.key_kind(key) not in ("gain", "freq", "q"):
            continue
        desc = adapter.describe_param(ch, key)
        out[key] = desc
        validator.learn_from_description(key, desc)
    return out


def probe_channel_count(adapter: MixerAdapter, upper: int = 48) -> int:
    """/ch/N/name 응답 여부로 존재하는 입력 채널 수를 추정 (이분 탐색)."""
    def exists(n: int) -> bool:
        try:
            adapter.read_param(n, "name")
            return True
        except (MixerTimeout, MixerError):
            return False

    if not exists(1):
        return 0
    lo, hi = 1, upper
    if exists(hi):
        return hi
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if exists(mid):
            lo = mid
        else:
            hi = mid
    return lo
