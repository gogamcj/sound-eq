"""LAN 제어 연결 관리: 연결 확인, 주기적 응답 확인(heartbeat), 단절 감지, 자동 재연결.

통신 단절이 감지되면 on_lost 콜백을 호출한다. 메인 앱은 이 콜백에서 자동 보정을 즉시 중지한다.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from common.states import LinkState

from .models import MixerInfo
from .protocol_adapter import MixerAdapter, MixerError

log = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self, adapter_factory: Callable[[], MixerAdapter], heartbeat_s: float = 0.5,
                 fail_threshold: int = 2, reconnect_s: float = 3.0):
        self.adapter_factory = adapter_factory
        self.heartbeat_s = heartbeat_s
        self.fail_threshold = fail_threshold
        self.reconnect_s = reconnect_s
        self.adapter: MixerAdapter | None = None
        self.state = LinkState.DISCONNECTED
        self.info: MixerInfo | None = None
        self.last_error: str = ""
        self.listeners: list[Callable[[LinkState, LinkState], None]] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._fails = 0
        self.connected_event = threading.Event()

    def add_listener(self, fn: Callable[[LinkState, LinkState], None]) -> None:
        self.listeners.append(fn)

    def _set_state(self, new: LinkState) -> None:
        old = self.state
        if old == new:
            return
        self.state = new
        if new == LinkState.CONNECTED:
            self.connected_event.set()
        else:
            self.connected_event.clear()
        log.info("LAN 상태: %s → %s", old.value, new.value)
        for fn in list(self.listeners):
            try:
                fn(old, new)
            except Exception:  # 리스너 오류가 연결 관리를 멈추지 않게
                log.exception("연결 리스너 오류")

    @property
    def is_connected(self) -> bool:
        return self.state == LinkState.CONNECTED

    def connect_once(self) -> bool:
        """한 번 연결 시도 (동기). 성공 시 True."""
        self._set_state(LinkState.CONNECTING)
        try:
            if self.adapter is None:
                self.adapter = self.adapter_factory()
            self.adapter.open()
            self.info = self.adapter.ping()
            self._fails = 0
            self._set_state(LinkState.CONNECTED)
            return True
        except (MixerError, OSError) as e:
            self.last_error = str(e)
            self._set_state(LinkState.DISCONNECTED)
            return False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="lan-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        if self.adapter:
            self.adapter.close()
        self.adapter = None
        self._set_state(LinkState.DISCONNECTED)

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.state in (LinkState.DISCONNECTED, LinkState.LOST):
                if not self.connect_once():
                    if self._stop.wait(self.reconnect_s):
                        break
                    continue
            try:
                self.info = self.adapter.ping(fast=True)
                self._fails = 0
                if self.state != LinkState.CONNECTED:
                    self._set_state(LinkState.CONNECTED)
            except MixerError as e:
                self._fails += 1
                self.last_error = str(e)
                if self._fails >= self.fail_threshold:
                    self._set_state(LinkState.LOST)
            self._stop.wait(self.heartbeat_s)
