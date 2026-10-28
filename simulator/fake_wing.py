"""WING OSC 시뮬레이터 (테스트·오프라인 개발 전용).

!! 실제 장비가 아니다 !!
* 응답 형식(',sff' / ',sfi' / ',s', '/?' 정보 문자열, 설정 미에코)은 WING Remote Protocols V3.0.6-30 의
  예시를 따라 만들었다.
* 파라미터 범위(SIM_RANGES)와 초기값은 시뮬레이터용 임의값이다. 실제 장비의 범위가 아니다.
* '/?' 응답의 model 필드를 'SIMULATOR' 로 시작하게 해 프로그램이 실장비와 구분하도록 한다.
"""
from __future__ import annotations

import socket
import threading
import time

from mixer import osc_codec
from mixer import wing_addresses as wa

SIM_RANGES = {  # 시뮬레이터 전용 임의 범위
    "gain": (-15.0, 15.0),
    "freq": (20.0, 20000.0),
    "q": (0.44, 10.0),
}


def fmt_value(kind: str, v: float) -> str:
    if kind == "freq":
        if v >= 1000:
            s = f"{v / 1000:.2f}"
            whole, frac = s.split(".")
            return f"{whole}k{frac}"
        return f"{v:.1f}"
    if kind == "gain":
        return f"{v:.1f}"
    if kind == "q":
        return f"{v:.2f}"
    return str(v)


def default_channel(ch: int) -> dict:
    d = {"name": f"CH{ch}", "eq/on": 1, "eq/mdl": "STD", "eq/mix": 100.0,
         "eq/lg": 0.0, "eq/lf": 80.2, "eq/lq": 1.0, "eq/leq": "SHV",
         "eq/hg": 0.0, "eq/hf": 12000.0, "eq/hq": 1.0, "eq/heq": "SHV",
         "flt/lc": 0, "flt/lcf": 100.2, "flt/lcs": "24", "flt/hc": 0, "flt/hcf": 10020.0, "flt/hcs": "12"}
    for b, f in zip(wa.PEQ_BANDS, (200.0, 601.4, 1500.0, 3990.0)):
        d[f"eq/{b}g"] = 0.0
        d[f"eq/{b}f"] = f
        d[f"eq/{b}q"] = 1.0
    return d


class FakeWing:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, n_channels: int = 40,
                 support_describe: bool = True):
        self.n_channels = n_channels
        self.support_describe = support_describe
        self.channels = {c: default_channel(c) for c in range(1, n_channels + 1)}
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        self.sock.settimeout(0.1)
        self.address = self.sock.getsockname()
        self.offline = False          # True 면 응답하지 않음 (LAN 단절 시뮬레이션)
        self.ignore_writes = False    # True 면 쓰기를 무시 (적용 실패 시뮬레이션)
        self.write_log: list[tuple[str, object]] = []
        self.latency_s = 0.0
        self._running = False
        self._thread: threading.Thread | None = None
        self.lock = threading.Lock()

    @property
    def port(self) -> int:
        return self.address[1]

    def start(self) -> "FakeWing":
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="fake-wing", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
        self.sock.close()

    def _reply(self, addr, address: str, *args) -> None:
        if self.latency_s:
            time.sleep(self.latency_s)
        self.sock.sendto(osc_codec.encode(address, *args), addr)

    def _loop(self) -> None:
        while self._running:
            try:
                data, addr = self.sock.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if self.offline:
                continue
            try:
                msg = osc_codec.decode(data)
            except osc_codec.OscDecodeError:
                continue
            self._handle(msg, addr)

    def _handle(self, msg: osc_codec.OscMessage, addr) -> None:
        if msg.address == "/?":
            ip = self.address[0]
            self._reply(addr, "/*", f"WING,{ip},SIM,SIMULATOR-not-real-hardware,NO_SERIAL,sim-0.1")
            return
        parts = msg.address.strip("/").split("/")
        if len(parts) < 3 or parts[0] != "ch":
            return  # 모르는 주소는 무응답 (실장비 동작은 미확인)
        try:
            ch = int(parts[1])
        except ValueError:
            return
        key = "/".join(parts[2:])
        with self.lock:
            chan = self.channels.get(ch)
            if chan is None or key not in chan:
                return
            kind = wa.key_kind(key)
            if msg.args == ["?"]:
                if self.support_describe and kind in SIM_RANGES:
                    lo, hi = SIM_RANGES[kind]
                    unit = {"gain": "dB", "freq": "Hz", "q": ""}[kind]
                    t = "log" if kind in ("freq", "q") else "lin"
                    self._reply(addr, msg.address, f"{key.split('/')[-1]} {t} [{lo} .. {hi} {unit}], 121 steps")
                return
            if msg.args:
                if self.ignore_writes:
                    return
                v = msg.args[0]
                if kind in SIM_RANGES:
                    lo, hi = SIM_RANGES[kind]
                    v = min(max(float(v), lo), hi)
                    v = round(v, 1) if kind != "q" else round(v, 2)
                elif kind == "bool":
                    v = 1 if int(v) else 0
                chan[key] = v
                self.write_log.append((msg.address, v))
                return  # WING 은 UDP 설정을 에코하지 않는다 (문서)
            v = chan[key]
            if kind in SIM_RANGES:
                lo, hi = SIM_RANGES[kind]
                raw = (float(v) - lo) / (hi - lo)
                self._reply(addr, msg.address, fmt_value(kind, float(v)), float(raw), float(v))
            elif kind == "bool":
                self._reply(addr, msg.address, str(v), float(v), int(v))
            else:
                self._reply(addr, msg.address, str(v))


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="WING OSC 시뮬레이터 (실제 장비 아님)")
    p.add_argument("--port", type=int, default=wa.OSC_PORT)
    p.add_argument("--host", default="127.0.0.1")
    a = p.parse_args()
    fw = FakeWing(a.host, a.port).start()
    print(f"[시뮬레이터] WING OSC 시뮬레이터 실행 중 {fw.address} (Ctrl+C 종료)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        fw.stop()
