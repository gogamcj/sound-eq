"""단계 1: 하드웨어 연결 검증 도구.

사용 예 (Windows, 프로젝트 폴더에서):
    python -m tools.verify_connection --audio                   # USB 오디오 장치 검색
    python -m tools.verify_connection --audio --audio-test 5    # 선택 장치에서 5초 수신해 채널별 레벨 측정
    python -m tools.verify_connection --ip 192.168.1.71 --ch 1  # LAN: 응답, 채널 이름, EQ/필터 읽기, 범위 질의
    python -m tools.verify_connection --discover                # UDP 2222 'WING?' 디스커버리
    python -m tools.verify_connection --ip ... --write-test 40 --confirm-write
        # 채널 40 의 EQ 밴드1 게인을 +0.5dB 바꾼 뒤 재조회하고 원래 값으로 되돌린다 (믹서 설정을 실제로 변경함!)
    python -m tools.verify_connection --simulate --write-test 1 --confirm-write   # 시뮬레이터 대상 (실장비 아님)

결과는 reports/verify_YYYYmmdd_HHMMSS.json / .md 로 저장한다. 시뮬레이터 결과에는 SIMULATION 표시가 붙는다.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import math
import platform
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audio import device_manager as dm  # noqa: E402
from mixer import wing_addresses as wa  # noqa: E402
from mixer.channel_reader import learn_ranges, probe_channel_count, read_channel  # noqa: E402
from mixer.eq_controller import EqController  # noqa: E402
from mixer.parameter_validation import ParameterValidator  # noqa: E402
from mixer.protocol_adapter import MixerError, WingOscAdapter, discover  # noqa: E402


class Report:
    def __init__(self, simulated: bool):
        self.simulated = simulated
        self.items: list[dict] = []
        self.data: dict = {"simulated": simulated, "time": time.strftime("%Y-%m-%d %H:%M:%S")}

    def add(self, step: str, ok: bool | None, detail: str) -> None:
        mark = {True: "PASS", False: "FAIL", None: "INFO"}[ok]
        self.items.append({"step": step, "result": mark, "detail": detail})
        prefix = "[시뮬레이터] " if self.simulated else ""
        print(f"{prefix}[{mark}] {step}: {detail}")

    def save(self, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        base = out_dir / f"verify_{stamp}{'_SIMULATION' if self.simulated else ''}"
        self.data["items"] = self.items
        base.with_suffix(".json").write_text(json.dumps(self.data, ensure_ascii=False, indent=2), "utf-8")
        lines = [f"# 연결 검증 결과 ({self.data['time']})", ""]
        lines.append("**시뮬레이터 결과 — 실제 장비 검증 아님**" if self.simulated else "**실제 장비 대상 실행**")
        lines += ["", "| 단계 | 결과 | 내용 |", "|---|---|---|"]
        lines += [f"| {i['step']} | {i['result']} | {i['detail'].replace('|', '/')} |" for i in self.items]
        base.with_suffix(".md").write_text("\n".join(lines) + "\n", "utf-8")
        return base.with_suffix(".md")


def check_audio(rep: Report, device_index: int | None, test_seconds: float) -> None:
    sd = dm.load_backend()
    if sd is None:
        rep.add("오디오 라이브러리", False, dm.backend_error() or "sounddevice 없음")
        return
    devs = dm.list_input_devices(sd)
    rep.data["audio_devices"] = [d.__dict__ for d in devs]
    if not devs:
        rep.add("오디오 장치 검색", False, "입력 장치가 없다")
        return
    rep.add("오디오 장치 검색", True, f"입력 장치 {len(devs)}개")
    for d in devs:
        rep.add("  장치", None, d.label())
    wings = dm.find_wing_devices(devs)
    if device_index is None:
        if not wings:
            rep.add("WING USB 장치", False, "이름에 'WING' 이 포함된 장치가 없다. --device 로 직접 지정 가능")
            return
        dev = wings[0]
    else:
        dev = next((d for d in devs if d.index == device_index), None)
        if dev is None:
            rep.add("장치 선택", False, f"장치 {device_index} 없음")
            return
    rep.add("선택 장치", True, dev.label())
    rates = dm.supported_samplerates(dev, min(2, dev.max_input_channels), sd)
    rep.add("지원 샘플레이트", bool(rates), ", ".join(map(str, rates)) or "확인 실패")
    sr = 48000 if 48000 in rates else (rates[0] if rates else int(dev.default_samplerate))
    nmax = dm.max_supported_channels(dev, sr, sd)
    rep.add("열 수 있는 최대 채널", nmax > 0, f"{nmax}ch @ {sr}Hz (드라이버 보고 {dev.max_input_channels}ch)")
    rep.data["audio_selected"] = {"device": dev.__dict__, "samplerate": sr, "channels": nmax}
    if test_seconds <= 0 or nmax <= 0:
        return
    import numpy as np
    frames = int(sr * test_seconds)
    try:
        rec = sd.rec(frames, samplerate=sr, channels=nmax, device=dev.index, dtype="float32")
        sd.wait()
    except Exception as e:
        rep.add("오디오 수신", False, str(e))
        return
    rms = np.sqrt(np.mean(rec.astype(np.float64) ** 2, axis=0))
    peak = np.max(np.abs(rec), axis=0)
    db = lambda x: 20 * math.log10(max(float(x), 1e-9))  # noqa: E731
    active = [i + 1 for i, r in enumerate(rms) if db(r) > -60]
    rep.add("오디오 수신", True, f"{test_seconds}s 수신. 신호 있는 USB 채널(>-60dBFS): {active or '없음'}")
    rep.data["audio_levels"] = [{"usb_ch": i + 1, "rms_db": round(db(r), 1), "peak_db": round(db(p), 1)}
                                for i, (r, p) in enumerate(zip(rms, peak))]
    for x in rep.data["audio_levels"]:
        if x["rms_db"] > -60:
            rep.add("  레벨", None, f"USB {x['usb_ch']}: RMS {x['rms_db']} dBFS / Peak {x['peak_db']} dBFS")


def local_ipv4_for(target: str) -> str | None:
    """target 으로 갈 때 Windows 가 쓰는 이 PC 의 IP (UDP connect 는 패킷을 보내지 않는다)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target, wa.OSC_PORT))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def icmp_ping(ip: str) -> bool | None:
    flag = ["-n", "1", "-w", "1000"] if platform.system() == "Windows" else ["-c", "1", "-W", "1"]
    try:
        return subprocess.run(["ping", *flag, ip], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return None


def diagnose_lan(rep: Report, ip: str) -> None:
    """OSC 응답이 없을 때 원인을 좁히기 위한 점검. 믹서 설정은 바꾸지 않는다."""
    local = local_ipv4_for(ip)
    if local == ip:
        rep.add("진단: 이 PC 의 IP", False, f"{ip} 는 이 PC 자신의 주소다. WING 화면의 SETUP 에서 WING 의 IP 를 확인")
    elif local is None:
        rep.add("진단: 이 PC 의 IP", False, f"{ip} 로 가는 경로가 없다. 랜 케이블과 네트워크 어댑터 확인")
    else:
        same = ipaddress.ip_address(local) in ipaddress.ip_network(f"{ip}/24", strict=False)
        rep.add("진단: 이 PC 의 IP", same,
                f"{local} → WING {ip}" + ("" if same else
                " — 앞 세 자리가 다르다. 같은 공유기에 연결하거나 PC IP 를 같은 대역으로 설정 (LAN_SETUP.md)"))
    pong = icmp_ping(ip)
    rep.add("진단: ping", pong,
            {True: "응답 있음 (케이블과 IP 는 정상. 방화벽이나 포트 문제일 가능성이 큼)",
             False: "응답 없음 (IP 가 틀렸거나, 케이블이 다른 네트워크에 연결됨)",
             None: "ping 실행 실패"}[pong])
    found = discover(timeout=1.5) + discover(broadcast=ip, timeout=1.0)
    if found:
        ips = sorted({d.get("ip") or d.get("from") for d in found})
        rep.add("진단: 디스커버리 (UDP 2222)", True, f"응답: {json.dumps(found, ensure_ascii=False)}")
        if ip not in ips:
            rep.add("진단: IP 불일치", False, f"찾은 WING 의 IP 는 {ips} 이다. 이 주소로 다시 실행")
        else:
            rep.add("진단: 포트", None, "2222 는 응답하는데 2223 은 응답하지 않는다. 방화벽 규칙이나 WING 설정 확인 필요")
    else:
        rep.add("진단: 디스커버리 (UDP 2222)", False, "응답 없음")
    if pong and not found:
        rep.add("진단: 방화벽", None, "ping 은 되는데 UDP 응답이 없다. Windows 방화벽에서 Python 을 '개인'과 '공용' 모두 허용")


def check_lan(rep: Report, ip: str, port: int, ch: int, write_ch: int | None, confirm_write: bool) -> None:
    ad = WingOscAdapter(ip, port, timeout=0.5, retries=1)
    ad.open()
    try:
        t0 = time.perf_counter()
        try:
            info = ad.ping()
        except MixerError as e:
            rep.add("OSC 응답 (/?)", False, f"{ip}:{port} 응답 없음: {e}. IP, 같은 서브넷, 방화벽(UDP) 확인")
            diagnose_lan(rep, ip)
            return
        rep.add("OSC 응답 (/?)", True, f"{(time.perf_counter() - t0) * 1000:.1f} ms, model={info.model}, "
                f"name={info.name}, fw={info.firmware}, serial={info.serial}")
        rep.data["mixer_info"] = info.__dict__
        if ad.is_simulator != rep.simulated:
            rep.add("대상 구분", False, "시뮬레이터/실장비 여부가 실행 옵션과 다르다")
        n = probe_channel_count(ad)
        rep.add("입력 채널 수 (/ch/N/name 응답)", n > 0, f"{n}개")
        rep.data["channel_count"] = n
        st = read_channel(ad, ch)
        rep.data["channel_read"] = {k: {"value": v.value, "text": v.text, "raw": v.raw} for k, v in st.values.items()}
        rep.add(f"ch{ch} 이름", "name" in st.values, repr(st.name))
        rep.add(f"ch{ch} EQ/필터 읽기", st.complete,
                f"{len(st.values)}개 읽음" + (f", 응답 없음: {st.missing}" if st.missing else ""))
        rep.add(f"ch{ch} EQ 상태", None, f"on={st.eq_on}, model={st.eq_model}")
        for k in wa.EQ_KEYS + wa.FILTER_KEYS:
            if k in st.values:
                pv = st.values[k]
                rep.add("  값", None, f"{k} = {pv.value} (text {pv.text!r}, raw {pv.raw})")
        val = ParameterValidator(allow_fallback_ranges=False)
        ranges = learn_ranges(ad, val, ch)
        rep.data["ranges"] = ranges
        got = {k: v for k, v in ranges.items() if v}
        rep.add("파라미터 범위 질의 (',s ?')", bool(got),
                f"{len(got)}/{len(ranges)}개 해석됨" + ("" if got else " — 범위를 모르면 프로그램은 쓰기를 거부한다"))
        for k, v in got.items():
            rep.add("  범위", None, f"{k}: {v['min']} .. {v['max']} {v.get('unit', '')}")
        if write_ch is None:
            return
        if not confirm_write:
            rep.add("단일 파라미터 쓰기", None, "--confirm-write 가 없어 건너뜀 (믹서를 실제로 바꾸는 테스트)")
            return
        key = "eq/1g"
        ctl = EqController(ad, val, settle_s=0.1, max_ch=max(n, 1))
        orig = ad.read_param(write_ch, key).value
        r = val.range_for(key)
        target = float(orig) + 0.5
        if r and target > r.max:
            target = float(orig) - 0.5
        res = ctl.apply(write_ch, {key: target})
        rep.add(f"ch{write_ch} {key} 쓰기 {orig} → {target}", res.ok,
                f"{res.status.value}; 재조회 {res.after.get(key)} ({res.latency_ms or 0:.0f} ms) {res.message}")
        if res.sent_keys:
            back = ctl.apply(write_ch, {key: float(orig)})
            rep.add(f"ch{write_ch} {key} 원래 값 복구", back.ok, f"{back.status.value}; 재조회 {back.after.get(key)}")
    finally:
        ad.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="WING COMPACT 연결 검증 (단계 1)")
    p.add_argument("--audio", action="store_true", help="USB 오디오 장치 검색")
    p.add_argument("--device", type=int, help="오디오 장치 번호 직접 지정")
    p.add_argument("--audio-test", type=float, default=0, help="N초 수신해 채널별 레벨 측정")
    p.add_argument("--discover", action="store_true", help="UDP 2222 디스커버리")
    p.add_argument("--ip", help="WING IP 주소")
    p.add_argument("--port", type=int, default=wa.OSC_PORT)
    p.add_argument("--ch", type=int, default=1, help="읽기 검증 채널")
    p.add_argument("--write-test", type=int, metavar="CH", help="단일 파라미터 쓰기/복구 검증 채널")
    p.add_argument("--confirm-write", action="store_true", help="믹서 설정 변경 테스트를 실제로 수행")
    p.add_argument("--simulate", action="store_true", help="로컬 시뮬레이터 대상 (실장비 아님)")
    p.add_argument("--out", default=str(ROOT / "reports"))
    a = p.parse_args(argv)

    rep = Report(simulated=a.simulate)
    fake = None
    if a.simulate:
        from simulator.fake_wing import FakeWing
        fake = FakeWing().start()
        a.ip, a.port = "127.0.0.1", fake.port
    try:
        if a.audio or a.audio_test:
            check_audio(rep, a.device, a.audio_test)
        if a.discover:
            found = discover()
            rep.add("디스커버리 (WING? → UDP 2222)", bool(found), json.dumps(found, ensure_ascii=False) or "응답 없음")
        if a.ip:
            check_lan(rep, a.ip, a.port, a.ch, a.write_test, a.confirm_write)
        if not (a.audio or a.audio_test or a.discover or a.ip):
            p.print_help()
            return 2
    finally:
        if fake:
            fake.stop()
    path = rep.save(Path(a.out))
    print(f"\n보고서: {path}")
    return 1 if any(i["result"] == "FAIL" for i in rep.items) else 0


if __name__ == "__main__":
    sys.exit(main())
