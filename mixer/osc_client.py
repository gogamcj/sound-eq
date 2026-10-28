"""UDP OSC 클라이언트 (요청-응답 매칭, 타임아웃, 재시도).

WING 은 조회 요청에 같은 주소로 응답한다. 설정(set)은 에코하지 않으므로
쓰기 확인은 항상 별도 조회로 한다 (eq_controller 참고).
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass

from . import osc_codec
from .wing_addresses import OSC_PORT, parse_wing_number

log = logging.getLogger(__name__)


class OscTimeout(TimeoutError):
    pass


class OscNotConnected(ConnectionError):
    pass


@dataclass
class WingValue:
    """조회 결과. kind: 'float'(,sff) | 'int'(,sfi) | 'string'(,s)"""
    address: str
    kind: str
    text: str
    raw: float | None = None      # 0..1 정규화값
    value: float | int | None = None

    @classmethod
    def from_message(cls, msg: osc_codec.OscMessage) -> "WingValue":
        a = msg.args
        if len(a) == 3 and isinstance(a[0], str) and isinstance(a[1], float):
            if isinstance(a[2], float):
                return cls(msg.address, "float", a[0], a[1], a[2])
            if isinstance(a[2], int):
                return cls(msg.address, "int", a[0], a[1], a[2])
        if len(a) >= 1 and isinstance(a[0], str):
            return cls(msg.address, "string", a[0])
        raise ValueError(f"예상하지 못한 WING 응답 형식: {msg.address} {a!r}")

    def as_number(self) -> float:
        if self.value is not None:
            return float(self.value)
        return parse_wing_number(self.text)


class OscClient:
    def __init__(self, host: str, port: int = OSC_PORT, timeout: float = 0.5, retries: int = 2):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self._sock: socket.socket | None = None
        self._rx_thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._waiters: dict[str, list] = {}
        self.last_rx_time = 0.0
        self.stats = {"sent": 0, "received": 0, "timeouts": 0, "decode_errors": 0}
        self.last_rtt_ms: float | None = None

    # -- lifecycle -------------------------------------------------------
    def open(self) -> None:
        if self._sock:
            return
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("0.0.0.0", 0))
        s.settimeout(0.2)
        self._sock = s
        self._running = True
        self._rx_thread = threading.Thread(target=self._rx_loop, name="osc-rx", daemon=True)
        self._rx_thread.start()

    def close(self) -> None:
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        if self._rx_thread:
            self._rx_thread.join(timeout=1.0)
        self._rx_thread = None

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    # -- io ----------------------------------------------------------------
    def _rx_loop(self) -> None:
        while self._running and self._sock:
            try:
                data, _ = self._sock.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                msg = osc_codec.decode(data)
            except osc_codec.OscDecodeError:
                self.stats["decode_errors"] += 1
                continue
            self.stats["received"] += 1
            self.last_rx_time = time.monotonic()
            key = msg.address.rstrip("*")
            with self._lock:
                waiters = self._waiters.get(key)
                if waiters:
                    ev, box = waiters.pop(0)
                    box.append(msg)
                    ev.set()

    def send(self, address: str, *args) -> None:
        if not self._sock:
            raise OscNotConnected("OSC 소켓이 열려 있지 않다")
        data = osc_codec.encode(address, *args)
        with self._send_lock:
            try:
                self._sock.sendto(data, (self.host, self.port))
            except OSError as e:
                raise OscNotConnected(str(e)) from e
        self.stats["sent"] += 1

    def request(self, address: str, *args, timeout: float | None = None,
                retries: int | None = None) -> osc_codec.OscMessage:
        """요청을 보내고 같은 주소의 응답을 기다린다. 재시도 후에도 없으면 OscTimeout."""
        timeout = self.timeout if timeout is None else timeout
        retries = self.retries if retries is None else retries
        for attempt in range(retries + 1):
            ev = threading.Event()
            box: list = []
            with self._lock:
                self._waiters.setdefault(address, []).append((ev, box))
            t0 = time.perf_counter()
            self.send(address, *args)
            if ev.wait(timeout):
                self.last_rtt_ms = (time.perf_counter() - t0) * 1000
                return box[0]
            with self._lock:
                lst = self._waiters.get(address, [])
                if (ev, box) in lst:
                    lst.remove((ev, box))
            self.stats["timeouts"] += 1
            log.debug("OSC timeout %s (attempt %d)", address, attempt + 1)
        raise OscTimeout(f"응답 없음: {address}")

    def get(self, address: str, timeout: float | None = None) -> WingValue:
        return WingValue.from_message(self.request(address, timeout=timeout))
