"""OSC 코덱, WING 값 형식, 어댑터/컨트롤러 통신 (시뮬레이터 대상 — 실장비 검증 아님)."""
import math
import socket
import time

import pytest

from common.states import LinkState
from mixer import osc_codec
from mixer import wing_addresses as wa
from mixer.channel_reader import learn_ranges, probe_channel_count, read_channel
from mixer.connection_manager import ConnectionManager
from mixer.eq_controller import ApplyStatus, EqController
from mixer.parameter_validation import ParameterValidator, ValidationError
from mixer.protocol_adapter import MixerTimeout, WingOscAdapter


# ---- 문서 예시 바이트 형식 -------------------------------------------------
def test_get_request_is_address_only_with_padding():
    # 문서: "/ch/1/fdr~~~" (12 B) — 인자 없는 조회
    assert osc_codec.encode("/ch/1/fdr") == b"/ch/1/fdr\x00\x00\x00,\x00\x00\x00"
    # 문서: "/?~~" = 2f3f0000
    assert osc_codec.encode("/?") == bytes.fromhex("2f3f00002c000000")


def test_set_float_matches_doc_layout():
    # 문서: /ch/2/fdr~~~,f~~[-3.0000]
    data = osc_codec.encode("/ch/2/fdr", -3.0)
    assert data.startswith(b"/ch/2/fdr\x00\x00\x00,f\x00\x00")
    assert len(data) == 20


def test_decode_sff_reply_like_doc():
    # 문서: /ch/1/fdr~~~,sff~~~~-oo~[0.0000][-144.0000]
    raw = osc_codec.encode("/ch/1/fdr", "-oo", 0.0, -144.0)
    m = osc_codec.decode(raw)
    assert m.address == "/ch/1/fdr" and m.args[0] == "-oo"
    assert m.args[2] == pytest.approx(-144.0)


def test_parse_wing_numbers():
    assert wa.parse_wing_number("1k50") == 1500
    assert wa.parse_wing_number("12k00") == 12000
    assert wa.parse_wing_number("10k02") == pytest.approx(10020)
    assert wa.parse_wing_number("100.2") == pytest.approx(100.2)
    assert wa.parse_wing_number("-oo") == -math.inf


def test_parse_description_doc_examples():
    d = wa.parse_description("fxmix lin [0 .. 100 %], 101 steps")
    assert d == {"type": "lin", "min": 0.0, "max": 100.0, "unit": "%", "steps": 101, "read_only": False}
    d = wa.parse_description("$esrc 0 r/o int [0 .. 400]")
    assert d["read_only"] and d["max"] == 400
    d = wa.parse_description("mdl list [NONE, EXT, HALL]")
    assert d["options"] == ["NONE", "EXT", "HALL"]


def test_info_reply_doc_example():
    d = wa.parse_info_reply("WING,192.168.1.71,PGM,ngc-full,NO_SERIAL,1.07.2-40-g1b1b292b:develop")
    assert d["model"] == "ngc-full" and d["ip"] == "192.168.1.71"


# ---- 시뮬레이터 통신 --------------------------------------------------------
def adapter_for(fw):
    ad = WingOscAdapter("127.0.0.1", fw.port, timeout=0.3, retries=1)
    ad.open()
    return ad


def test_read_channel_and_ranges(fake_wing):
    ad = adapter_for(fake_wing)
    info = ad.ping()
    assert ad.is_simulator and info.model.startswith("SIMULATOR")
    st = read_channel(ad, 1)
    assert st.complete and st.name == "CH1" and st.eq_model == "STD"
    assert st.get("eq/2f") == pytest.approx(601.4, abs=0.1)
    v = ParameterValidator()
    learn_ranges(ad, v, 1)
    assert v.range_for("eq/1g").source == "device"
    assert probe_channel_count(ad) == 40
    ad.close()


def test_mixer_not_connected_times_out():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]  # 아무도 응답하지 않는 포트
    ad = WingOscAdapter("127.0.0.1", port, timeout=0.1, retries=0)
    ad.open()
    with pytest.raises(MixerTimeout):
        ad.ping()
    ad.close()
    s.close()


def test_write_verified_by_readback(fake_wing):
    ad = adapter_for(fake_wing)
    v = ParameterValidator()
    learn_ranges(ad, v, 1)
    ctl = EqController(ad, v, settle_s=0.02)
    r = ctl.apply(5, {"eq/1g": -2.5, "eq/1f": 350.0})
    assert r.status == ApplyStatus.VERIFIED, r.message
    assert r.after["eq/1g"] == pytest.approx(-2.5)
    # 같은 값 재적용 → 전송하지 않음
    n = len(fake_wing.write_log)
    r2 = ctl.apply(5, {"eq/1g": -2.5})
    assert r2.status == ApplyStatus.NO_CHANGE and len(fake_wing.write_log) == n
    ad.close()


def test_write_not_applied_is_reported_as_failure(fake_wing):
    ad = adapter_for(fake_wing)
    v = ParameterValidator()
    learn_ranges(ad, v, 1)
    fake_wing.ignore_writes = True
    r = EqController(ad, v, settle_s=0.02).apply(2, {"eq/3g": -3.0})
    assert r.status == ApplyStatus.FAILED_MISMATCH
    ad.close()


def test_validation_blocks_unknown_range_and_forbidden_params(fake_wing):
    ad = adapter_for(fake_wing)
    v = ParameterValidator(allow_fallback_ranges=False)  # 범위 학습 안 함
    r = EqController(ad, v).apply(1, {"eq/1g": -1.0})
    assert r.status == ApplyStatus.REJECTED_VALIDATION
    learn_ranges(ad, v, 1)
    for bad in ({"in/set/trim": 3.0}, {"fdr": 0.0}, {"eq/1g": 99.0}, {"eq/1g": float("nan")}):
        assert EqController(ad, v).apply(1, bad).status == ApplyStatus.REJECTED_VALIDATION
    with pytest.raises(ValidationError):
        v.validate(99, "eq/1g", 0.0)
    assert not fake_wing.write_log
    ad.close()


def test_external_change_aborts_apply(fake_wing):
    ad = adapter_for(fake_wing)
    v = ParameterValidator()
    learn_ranges(ad, v, 1)
    fake_wing.channels[3]["eq/2g"] = -4.0  # 사용자가 콘솔에서 바꿈
    r = EqController(ad, v).apply(3, {"eq/2g": -1.0}, expected_current={"eq/2g": 0.0})
    assert r.status == ApplyStatus.ABORTED_EXTERNAL_CHANGE
    assert fake_wing.channels[3]["eq/2g"] == -4.0
    ad.close()


def test_connection_lost_and_reconnect(fake_wing):
    states = []
    cm = ConnectionManager(lambda: WingOscAdapter("127.0.0.1", fake_wing.port, timeout=0.1, retries=0),
                           heartbeat_s=0.1, fail_threshold=2, reconnect_s=0.2)
    cm.add_listener(lambda o, n: states.append(n))
    cm.start()
    assert cm.connected_event.wait(2)
    fake_wing.offline = True
    t0 = time.time()
    while cm.state == LinkState.CONNECTED and time.time() - t0 < 3:
        time.sleep(0.05)
    assert cm.state in (LinkState.LOST, LinkState.CONNECTING, LinkState.DISCONNECTED)
    fake_wing.offline = False
    assert cm.connected_event.wait(3), "자동 재연결 실패"
    cm.stop()
    assert LinkState.LOST in states


def test_verify_tool_runs_lan_diagnostics_when_no_reply(tmp_path):
    from tools import verify_connection as vc
    rep = vc.Report(simulated=False)
    vc.check_lan(rep, "127.0.0.1", 1, 1, None, False)   # 아무도 듣지 않는 포트
    steps = [i["step"] for i in rep.items]
    assert steps[0] == "OSC 응답 (/?)" and rep.items[0]["result"] == "FAIL"
    assert "진단: 이 PC 의 IP" in steps and "진단: 디스커버리 (UDP 2222)" in steps
