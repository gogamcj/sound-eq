"""USB 오디오 채널 ↔ 믹서 입력 채널 매핑.

USB 수신 채널 번호만으로 WING 내부 채널을 알 수 없다. 믹서의 USB 출력 라우팅(어떤 소스를 USB 몇 번으로
보내는지)을 사용자가 확인한 뒤 매핑을 입력한다. 매핑이 잘못되면(중복, 범위 밖) 저장 단계에서 거부하고,
매핑이 없는 채널은 분석만 하고 믹서 제어 대상에서 제외한다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class ChannelConfig:
    usb_input: int                  # 1-based USB 오디오 채널
    mixer_ch: int | None = None     # 1-based WING 입력 채널 (/ch/N). None = 매핑 안 됨
    name: str = ""
    instrument: str = "unknown"     # instrument_profiles 키
    instrument_confirmed: bool = False   # 사용자가 직접 지정했는지 (자동 보정 조건)
    source_type: str = "mic"        # 'mic' | 'di'
    auto_allowed: bool = False      # 자동 보정 허용 (기본 금지)

    def to_dict(self) -> dict:
        return asdict(self)


class MappingError(ValueError):
    pass


@dataclass
class ChannelMap:
    channels: list[ChannelConfig] = field(default_factory=list)

    @classmethod
    def identity(cls, n_usb: int, max_mixer_ch: int = 40) -> "ChannelMap":
        """초기 제안값: USB n → ch n (사용자가 믹서 라우팅을 확인해야 함)."""
        return cls([ChannelConfig(i, i if i <= max_mixer_ch else None) for i in range(1, n_usb + 1)])

    def validate(self, n_usb: int, max_mixer_ch: int) -> list[str]:
        errs = []
        seen_usb, seen_mix = set(), {}
        for c in self.channels:
            if not (1 <= c.usb_input <= n_usb):
                errs.append(f"USB {c.usb_input}: 활성 USB 채널 수({n_usb}) 범위 밖")
            if c.usb_input in seen_usb:
                errs.append(f"USB {c.usb_input}: 중복")
            seen_usb.add(c.usb_input)
            if c.mixer_ch is not None:
                if not (1 <= c.mixer_ch <= max_mixer_ch):
                    errs.append(f"USB {c.usb_input}: 믹서 채널 {c.mixer_ch} 범위 밖 (1..{max_mixer_ch})")
                if c.mixer_ch in seen_mix:
                    errs.append(f"믹서 ch{c.mixer_ch}: USB {seen_mix[c.mixer_ch]} 와 {c.usb_input} 에 중복 매핑")
                seen_mix[c.mixer_ch] = c.usb_input
        return errs

    def require_valid(self, n_usb: int, max_mixer_ch: int) -> None:
        errs = self.validate(n_usb, max_mixer_ch)
        if errs:
            raise MappingError("; ".join(errs))

    def by_usb(self, usb_input: int) -> ChannelConfig | None:
        return next((c for c in self.channels if c.usb_input == usb_input), None)

    def by_mixer(self, mixer_ch: int) -> ChannelConfig | None:
        return next((c for c in self.channels if c.mixer_ch == mixer_ch), None)

    def to_list(self) -> list[dict]:
        return [c.to_dict() for c in self.channels]

    @classmethod
    def from_list(cls, items: list[dict]) -> "ChannelMap":
        return cls([ChannelConfig(**d) for d in items])
