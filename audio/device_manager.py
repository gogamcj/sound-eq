"""Windows 오디오 장치 검색 및 WING USB 오디오 장치 선택.

* sounddevice(PortAudio) 를 사용한다. Windows 용 sounddevice 휠에는 PortAudio DLL 이 포함된다.
* WING 의 Windows USB 드라이버는 ASIO 를 제공하는 것으로 알려져 있다. sounddevice 는 환경변수
  SD_ENABLE_ASIO=1 이 import 전에 설정돼 있으면 ASIO 지원 PortAudio 를 로드한다.
  (WING COMPACT 실제 장치명·호스트 API·채널 수는 실장비에서 확인 필요 → HARDWARE_VERIFICATION.md)
* 장치 이름에 'WING' 이 포함된 입력 장치를 후보로 표시하지만, 사용자가 최종 선택한다.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)

_sd = None
_sd_error: str | None = None


def load_backend(enable_asio: bool = True):
    """sounddevice 를 지연 로드. 실패 이유를 보관한다 (장치/라이브러리 없음 테스트용)."""
    global _sd, _sd_error
    if _sd is not None:
        return _sd
    if enable_asio:
        os.environ.setdefault("SD_ENABLE_ASIO", "1")
    try:
        import sounddevice as sd  # noqa: WPS433
    except (ImportError, OSError) as e:
        _sd_error = f"오디오 라이브러리 로드 실패: {e}"
        log.warning(_sd_error)
        return None
    _sd = sd
    return sd


def backend_error() -> str | None:
    return _sd_error


@dataclass
class InputDevice:
    index: int
    name: str
    hostapi: str
    max_input_channels: int
    default_samplerate: float

    @property
    def looks_like_wing(self) -> bool:
        return "wing" in self.name.lower()

    def label(self) -> str:
        tag = " ★WING 후보" if self.looks_like_wing else ""
        return f"[{self.index}] {self.name} ({self.hostapi}, 입력 {self.max_input_channels}ch){tag}"


def list_input_devices(sd=None) -> list[InputDevice]:
    sd = sd or load_backend()
    if sd is None:
        return []
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception as e:  # PortAudio 오류
        log.warning("장치 조회 실패: %s", e)
        return []
    out = []
    for i, d in enumerate(devices):
        if d.get("max_input_channels", 0) > 0:
            out.append(InputDevice(i, d["name"], hostapis[d["hostapi"]]["name"],
                                   int(d["max_input_channels"]), float(d["default_samplerate"])))
    return out


def find_wing_devices(devices: list[InputDevice]) -> list[InputDevice]:
    """WING 후보를 채널 수 많은 순, ASIO 우선으로 정렬."""
    c = [d for d in devices if d.looks_like_wing]
    return sorted(c, key=lambda d: (("asio" not in d.hostapi.lower()), -d.max_input_channels))


CANDIDATE_RATES = (48000, 44100, 96000)


def supported_samplerates(device: InputDevice, channels: int, sd=None) -> list[int]:
    sd = sd or load_backend()
    ok = []
    if sd is None:
        return ok
    for sr in CANDIDATE_RATES:
        try:
            sd.check_input_settings(device=device.index, channels=channels, samplerate=sr, dtype="float32")
            ok.append(sr)
        except Exception:
            pass
    return ok


def max_supported_channels(device: InputDevice, samplerate: int, sd=None, limit: int = 48) -> int:
    """실제로 열 수 있는 최대 채널 수 (드라이버 보고값과 다를 수 있어 직접 확인)."""
    sd = sd or load_backend()
    if sd is None:
        return 0
    for n in range(min(limit, device.max_input_channels), 0, -1):
        try:
            sd.check_input_settings(device=device.index, channels=n, samplerate=samplerate, dtype="float32")
            return n
        except Exception:
            continue
    return 0
