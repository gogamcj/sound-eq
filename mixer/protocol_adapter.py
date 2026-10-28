"""믹서 프로토콜 어댑터.

상위 계층(channel_reader, eq_controller)은 MixerAdapter 인터페이스만 사용한다.
* WingOscAdapter: 실제 WING (OSC/UDP 2223). 문서 기반.
* 시뮬레이터는 simulator/fake_wing.py 의 UDP 서버로 따로 존재하며, 같은 WingOscAdapter 로 접속한다.
  즉 코드 경로는 실장비와 동일하고, 접속 대상만 다르다. 시뮬레이터 접속 여부는 is_simulator 로 구분한다.
* 네이티브(TCP 2222) 어댑터는 문서의 바이너리 토큰 형식을 아직 확인하지 못해 구현하지 않았다.
"""
from __future__ import annotations

import abc
import logging
import socket

from . import wing_addresses as wa
from .models import MixerInfo, ParamValue
from .osc_client import OscClient, OscNotConnected, OscTimeout, WingValue

log = logging.getLogger(__name__)


class MixerError(Exception):
    pass


class MixerTimeout(MixerError):
    pass


class MixerAdapter(abc.ABC):
    is_simulator: bool = False

    @abc.abstractmethod
    def open(self) -> None: ...

    @abc.abstractmethod
    def close(self) -> None: ...

    @abc.abstractmethod
    def ping(self, fast: bool = False) -> MixerInfo: ...

    @abc.abstractmethod
    def read_param(self, ch: int, key: str) -> ParamValue: ...

    @abc.abstractmethod
    def write_param(self, ch: int, key: str, value) -> None: ...

    @abc.abstractmethod
    def describe_param(self, ch: int, key: str) -> dict | None: ...

    def describe_node(self, ch: int, node: str) -> dict[str, dict]:
        """노드 단위 범위 설명 (키 → 범위). 지원하지 않으면 빈 dict."""
        return {}

    @property
    @abc.abstractmethod
    def last_rtt_ms(self) -> float | None: ...


class WingOscAdapter(MixerAdapter):
    def __init__(self, host: str, port: int = wa.OSC_PORT, timeout: float = 0.5, retries: int = 2):
        self.host = host
        self.client = OscClient(host, port, timeout=timeout, retries=retries)
        self.info: MixerInfo | None = None

    def open(self) -> None:
        self.client.open()

    def close(self) -> None:
        self.client.close()

    @property
    def last_rtt_ms(self) -> float | None:
        return self.client.last_rtt_ms

    def _req(self, address: str, *args, retries: int | None = None):
        try:
            return self.client.request(address, *args, retries=retries)
        except OscTimeout as e:
            raise MixerTimeout(str(e)) from e
        except OscNotConnected as e:
            raise MixerError(str(e)) from e

    def ping(self, fast: bool = False) -> MixerInfo:
        """fast=True: 재시도 없이 1회 (주기적 상태 확인용 — 단절을 빨리 감지)"""
        msg = self._req(wa.INFO_ADDRESS, retries=0 if fast else None)
        if not msg.args or not isinstance(msg.args[0], str):
            raise MixerError(f"/? 응답 형식 오류: {msg.args!r}")
        d = wa.parse_info_reply(msg.args[0])
        self.info = MixerInfo(**d)
        self.is_simulator = self.info.model.startswith("SIMULATOR")
        return self.info

    def read_param(self, ch: int, key: str) -> ParamValue:
        msg = self._req(wa.ch_param(ch, key))
        try:
            wv = WingValue.from_message(msg)
        except ValueError as e:
            raise MixerError(str(e)) from e
        kind = wa.key_kind(key)
        if kind in ("enum", "text"):
            return ParamValue(key, wv.text, wv.text, wv.raw)
        if kind == "bool":
            v = int(wv.value) if wv.value is not None else int(float(wv.text))
            return ParamValue(key, v, wv.text, wv.raw)
        return ParamValue(key, wv.as_number(), wv.text, wv.raw)

    def write_param(self, ch: int, key: str, value) -> None:
        addr = wa.ch_param(ch, key)
        kind = wa.key_kind(key)
        if kind == "bool":
            args = (int(bool(value)),)
        elif kind in ("gain", "freq", "q"):
            args = (float(value),)
        else:
            args = (str(value),)
        try:
            self.client.send(addr, *args)
        except OscNotConnected as e:
            raise MixerError(str(e)) from e

    def describe_param(self, ch: int, key: str) -> dict | None:
        """',s ?' 로 장비가 알려주는 범위. 응답 형식이 해석되지 않으면 None."""
        addr = wa.ch_param(ch, key)
        try:
            msg = self.client.request(addr, "?")
        except (OscTimeout, OscNotConnected):
            return None
        for a in msg.args:
            if isinstance(a, str):
                d = wa.parse_description(a)
                if d:
                    return d
        return None

    def describe_node(self, ch: int, node: str) -> dict[str, dict]:
        """'/ch/N/eq' 같은 노드에 ',s ?' 를 보내 하위 키 범위를 한 번에 받는다 (실장비에서 확인된 방식)."""
        try:
            msg = self.client.request(f"/ch/{ch}/{node}", "?")
        except (OscTimeout, OscNotConnected):
            return {}
        out = {}
        for a in msg.args:
            if isinstance(a, str):
                out.update({f"{node}/{k}": v for k, v in wa.parse_node_description(a).items()})
        return out


def discover(broadcast: str = "255.255.255.255", timeout: float = 1.0) -> list[dict]:
    """문서의 디스커버리: 'WING?' 5바이트를 UDP 2222 로 보내고 'WING,ip,name,model,serial,fw' 응답을 모은다."""
    found = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(timeout)
        s.sendto(wa.DISCOVERY_MESSAGE, (broadcast, wa.DISCOVERY_PORT))
        while True:
            try:
                data, addr = s.recvfrom(2048)
            except socket.timeout:
                break
            try:
                d = wa.parse_info_reply(data.decode("ascii", errors="replace").strip("\x00"))
                d["from"] = addr[0]
                found.append(d)
            except ValueError:
                continue
    except OSError as e:
        log.warning("디스커버리 실패: %s", e)
    finally:
        s.close()
    return found
