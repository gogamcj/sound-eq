"""오디오 입력 스트림.

규칙: 오디오 콜백에서는 블록 복사와 큐 삽입(put_nowait)만 한다. FFT·네트워크·파일·GUI 작업 금지.
큐가 가득 차면(분석이 밀리면) 블록을 버리고 카운트한다. PortAudio 상태 플래그(input overflow 등)는
드롭아웃으로 집계한다. 워치독이 수신 중단을 감지하면 상태를 ERROR 로 바꾸고 같은 이름의 장치로 재연결을 시도한다.

AudioSource 공통 인터페이스를 실제 USB 입력(UsbAudioInput)과 파일/시뮬레이션 입력(simulator/audio_sources.py)이
공유한다. is_simulated 로 실제 수신 여부를 구분한다.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

import numpy as np

from common.latency import LATENCY
from common.states import AudioState

from . import device_manager as dm

log = logging.getLogger(__name__)


class AudioSource:
    is_simulated = True

    def __init__(self, channels: int, samplerate: int, blocksize: int = 512, queue_blocks: int = 256):
        self.channels = channels
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.queue: queue.Queue = queue.Queue(maxsize=queue_blocks)
        self.state = AudioState.STOPPED
        self.listeners: list[Callable[[AudioState, AudioState], None]] = []
        self.dropped_blocks = 0
        self.status_errors = 0          # PortAudio 오버플로 등
        self.last_block_time = 0.0
        self.blocks = 0
        self.last_error = ""

    def add_listener(self, fn) -> None:
        self.listeners.append(fn)

    def _set_state(self, new: AudioState) -> None:
        old, self.state = self.state, new
        if old != new:
            log.info("오디오 상태: %s → %s", old.value, new.value)
            for fn in list(self.listeners):
                try:
                    fn(old, new)
                except Exception:
                    log.exception("오디오 리스너 오류")

    def _push(self, block: np.ndarray, input_latency_ms: float | None = None) -> None:
        """콜백 안에서 호출: 가볍게 유지."""
        now = time.monotonic()
        self.last_block_time = now
        self.blocks += 1
        try:
            self.queue.put_nowait((block, now))
        except queue.Full:
            self.dropped_blocks += 1
        if input_latency_ms is not None and (self.blocks & 31) == 0:
            LATENCY.add("audio_input", input_latency_ms)

    def start(self) -> None: ...

    def stop(self) -> None: ...

    @property
    def healthy(self) -> bool:
        return self.state == AudioState.RECEIVING


class UsbAudioInput(AudioSource):
    """실제 오디오 장치 입력 (WING USB). Windows 실장비 검증 전."""
    is_simulated = False

    def __init__(self, device: dm.InputDevice, channels: int, samplerate: int = 48000, blocksize: int = 512,
                 watchdog_s: float = 1.0, reconnect_s: float = 3.0):
        super().__init__(channels, samplerate, blocksize)
        self.device = device
        self.watchdog_s = watchdog_s
        self.reconnect_s = reconnect_s
        self._stream = None
        self._run = threading.Event()
        self._wd: threading.Thread | None = None

    def _callback(self, indata, frames, time_info, status):  # PortAudio 스레드
        if status:
            self.status_errors += 1
        lat = None
        try:
            lat = (time_info.currentTime - time_info.inputBufferAdcTime) * 1000.0
        except Exception:
            pass
        self._push(indata.copy(), lat if lat and lat > 0 else None)

    def _open(self) -> bool:
        sd = dm.load_backend()
        if sd is None:
            self.last_error = dm.backend_error() or "오디오 라이브러리 없음"
            self._set_state(AudioState.NO_DEVICE)
            return False
        # 재연결 시 장치 번호가 바뀔 수 있어 이름으로 다시 찾는다
        devs = dm.list_input_devices(sd)
        dev = next((d for d in devs if d.name == self.device.name and d.hostapi == self.device.hostapi), None)
        if dev is None:
            self.last_error = f"장치를 찾을 수 없음: {self.device.name}"
            self._set_state(AudioState.NO_DEVICE)
            return False
        self.device = dev
        try:
            self._stream = sd.InputStream(device=dev.index, channels=self.channels, samplerate=self.samplerate,
                                          blocksize=self.blocksize, dtype="float32", callback=self._callback,
                                          latency="low")
            self._stream.start()
        except Exception as e:
            self.last_error = str(e)
            self._stream = None
            self._set_state(AudioState.ERROR)
            return False
        self.last_block_time = time.monotonic()
        return True

    def _close(self) -> None:
        s, self._stream = self._stream, None
        if s is not None:
            try:
                s.abort()
                s.close()
            except Exception:
                pass

    def start(self) -> None:
        self._run.set()
        self._set_state(AudioState.STARTING)
        self._open()
        self._wd = threading.Thread(target=self._watchdog, name="audio-watchdog", daemon=True)
        self._wd.start()

    def stop(self) -> None:
        self._run.clear()
        self._close()
        if self._wd:
            self._wd.join(timeout=2)
        self._set_state(AudioState.STOPPED)

    def _watchdog(self) -> None:
        while self._run.is_set():
            time.sleep(0.25)
            if self._stream is None:
                time.sleep(self.reconnect_s)
                if self._run.is_set():
                    self._set_state(AudioState.STARTING)
                    self._open()
                continue
            stale = time.monotonic() - self.last_block_time > self.watchdog_s
            if stale or not self._stream.active:
                self.last_error = "오디오 블록 수신 중단 (장치 분리 또는 드라이버 오류)"
                self._set_state(AudioState.ERROR)
                self._close()
            elif self.state != AudioState.RECEIVING and self.blocks > 0:
                self._set_state(AudioState.RECEIVING)
