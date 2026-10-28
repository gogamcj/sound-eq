"""믹서 측 데이터 모델."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ParamValue:
    key: str                 # 'eq/1g' 등 채널 상대 키
    value: float | int | str
    text: str = ""
    raw: float | None = None


@dataclass
class ChannelEqState:
    """믹서에서 실제로 읽어온 채널 EQ/필터 상태. values 는 키 → ParamValue."""
    ch: int
    name: str = ""
    values: dict[str, ParamValue] = field(default_factory=dict)
    read_time: float = field(default_factory=time.time)
    missing: list[str] = field(default_factory=list)   # 응답이 없던 키

    def get(self, key: str, default=None):
        pv = self.values.get(key)
        return default if pv is None else pv.value

    @property
    def eq_on(self) -> bool | None:
        v = self.get("eq/on")
        return None if v is None else bool(int(v))

    @property
    def eq_model(self) -> str | None:
        v = self.get("eq/mdl")
        return None if v is None else str(v)

    @property
    def complete(self) -> bool:
        return not self.missing


@dataclass
class MixerInfo:
    ip: str
    name: str
    model: str
    serial: str
    firmware: str
