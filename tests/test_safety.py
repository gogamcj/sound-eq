"""안전 기능 및 전체 흐름 통합 테스트 (WING 시뮬레이터 + 가상 오디오 — 실장비 검증 아님)."""
import time

import numpy as np
import pytest

from audio.channel_mapping import ChannelConfig, ChannelMap
from common.states import OperatingMode
from core.controller import AppController, AppSettings
from eq.safety_controller import SafetySettings
from mixer.eq_controller import ApplyStatus
from simulator.audio_sources import SynthChannel, SynthSpec

from .conftest import ManualSource

SR = 48000
GUITAR = SynthSpec(f0_range=(82, 330), n_harm=30, rolloff_db_oct=-5, resonance=(180, 9, 4.0), pink_db=-38)


@pytest.fixture
def app(fake_wing, tmp_path):
    st = AppSettings(safety=SafetySettings(min_interval_s=0.0, auto_min_confidence=0.3))
    st.engine.min_analysis_seconds = 5
    st.engine.full_confidence_seconds = 10
    a = AppController(st, db_path=str(tmp_path / "t.db"))
    assert a.connect_mixer("127.0.0.1", fake_wing.port, wait=3)
    t0 = time.time()
    while a.eq_ctl is None and time.time() - t0 < 5:
        time.sleep(0.05)
    assert a.eq_ctl is not None
    yield a
    a.shutdown()


def feed(app, specs, seconds):
    src = ManualSource(len(specs), SR)
    app.start_audio(src)
    chans = [SynthChannel(s, SR, i) for i, s in enumerate(specs)]
    for _ in range(int(seconds * SR / 512)):
        app.analyzer.process_block(np.stack([c.render(512) for c in chans], axis=1))
    return src


def setup_map(app, n, auto=True, confirmed=True, inst="acoustic_guitar"):
    app.channel_map = ChannelMap([ChannelConfig(i + 1, i + 1, f"c{i + 1}", inst, confirmed, "mic", auto)
                                  for i in range(n)])


def test_default_mode_is_analysis_only_and_blocks_writes(app, fake_wing):
    assert app.safety.mode == OperatingMode.ANALYSIS_ONLY
    feed(app, [GUITAR], 12)
    setup_map(app, 1)
    rec = app.recommend(0)
    assert rec.items
    res = app.apply_recommendation(0)
    assert res[0].status == ApplyStatus.REJECTED_VALIDATION
    assert not fake_wing.write_log


def test_mode_b_user_apply_verified_and_recorded(app, fake_wing):
    feed(app, [GUITAR], 12)
    setup_map(app, 1)
    app.set_mode(OperatingMode.RECOMMEND_APPROVE)
    app.recommend(0)
    assert app.auto_tick() == []          # 모드 B: 자동 적용 없음
    assert not fake_wing.write_log
    res = app.apply_recommendation(0, [0])
    assert res[0].status == ApplyStatus.VERIFIED, res[0].message
    rows = app.history.changes(1)
    assert rows and rows[0].source == "user_approved" and rows[0].simulated


def test_restore_only_program_changes_and_respects_user_edits(app, fake_wing):
    feed(app, [GUITAR], 12)
    setup_map(app, 1)
    app.set_mode(OperatingMode.RECOMMEND_APPROVE)
    app.recommend(0)
    res = app.apply_recommendation(0, [0])[0]
    assert res.status == ApplyStatus.VERIFIED
    keys = list(res.requested)
    gain_key = next(k for k in keys if k.endswith("g"))
    other = [k for k in keys if k != gain_key]
    # 사용자가 콘솔에서 게인을 다시 바꿈 → 복구 시 게인은 유지, 나머지만 복구
    fake_wing.channels[1][gain_key] = -1.0
    rep = app.restore_channel(1)
    assert gain_key in " ".join(rep[0]["skipped"])
    assert fake_wing.channels[1][gain_key] == -1.0
    for k in other:
        assert fake_wing.channels[1][k] == pytest.approx(res.before[k], abs=0.2)


def test_auto_requires_mode_c_explicit_enable_and_limits_step(app, fake_wing):
    feed(app, [GUITAR], 12)
    setup_map(app, 1)
    assert not app.enable_auto().allowed            # 모드 A
    app.set_mode(OperatingMode.AUTO_LIMITED)
    assert app.auto_tick() == []                      # 켜지 않음
    assert app.enable_auto().allowed
    done = app.auto_tick()
    assert done and "VERIFIED" in done[0]
    writes = [v for a, v in fake_wing.write_log if a.endswith("g")]
    assert all(abs(v) <= app.safety.s.max_step_db + 1e-6 for v in writes)


def test_auto_denied_for_unconfirmed_instrument_or_not_allowed(app, fake_wing):
    feed(app, [GUITAR, GUITAR], 12)
    app.channel_map = ChannelMap([ChannelConfig(1, 1, "a", "acoustic_guitar", False, "mic", True),
                                  ChannelConfig(2, 2, "b", "acoustic_guitar", True, "mic", False)])
    app.set_mode(OperatingMode.AUTO_LIMITED)
    app.enable_auto()
    assert app.auto_tick() == []
    assert not fake_wing.write_log


def test_auto_denied_on_silence_and_clipping(app, fake_wing):
    feed(app, [SynthSpec(kind="silence"), SynthSpec(level_db=-6, clip=True)], 8)
    setup_map(app, 2)
    app.set_mode(OperatingMode.AUTO_LIMITED)
    app.enable_auto()
    assert app.auto_tick() == []
    assert not fake_wing.write_log


def test_stop_button_halts_auto(app, fake_wing):
    feed(app, [GUITAR], 12)
    setup_map(app, 1)
    app.set_mode(OperatingMode.AUTO_LIMITED)
    app.enable_auto()
    app.stop_all_auto()
    assert not app.safety.auto_enabled
    assert app.auto_tick() == []
    assert not fake_wing.write_log


def test_comm_failure_stops_auto(app, fake_wing):
    feed(app, [GUITAR], 12)
    setup_map(app, 1)
    app.set_mode(OperatingMode.AUTO_LIMITED)
    app.enable_auto()
    fake_wing.offline = True
    t0 = time.time()
    while app.safety.auto_enabled and time.time() - t0 < 6:
        time.sleep(0.1)
    assert not app.safety.auto_enabled
    assert "LAN" in app.safety.stop_reason or "통신" in app.safety.stop_reason
    assert app.auto_tick() == []


def test_audio_error_stops_auto(app):
    src = feed(app, [GUITAR], 12)
    setup_map(app, 1)
    app.set_mode(OperatingMode.AUTO_LIMITED)
    app.enable_auto()
    from common.states import AudioState
    src._set_state(AudioState.ERROR)
    assert not app.safety.auto_enabled


def test_cumulative_limit(app, fake_wing):
    app.settings.safety.max_cumulative_db = 2.0
    feed(app, [GUITAR], 12)
    setup_map(app, 1)
    app.set_mode(OperatingMode.AUTO_LIMITED)
    app.enable_auto()
    for _ in range(6):
        app.auto_tick()
        app.eq_cache.clear()
    gains = [v for a, v in fake_wing.write_log if a.endswith("g")]
    assert gains and min(gains) >= -2.0 - 1e-6


def test_shutdown_stops_auto_and_does_not_touch_mixer(app, fake_wing):
    feed(app, [GUITAR], 12)
    setup_map(app, 1)
    app.set_mode(OperatingMode.RECOMMEND_APPROVE)
    app.recommend(0)
    app.apply_recommendation(0, [0])
    n = len(fake_wing.write_log)
    snapshot = dict(fake_wing.channels[1])
    app.shutdown()
    assert len(fake_wing.write_log) == n
    assert fake_wing.channels[1] == snapshot    # 믹서 설정은 그대로 유지
    assert not app.safety.auto_enabled


def test_long_run_stability_accelerated():
    """가속 장시간 실행: 48채널 x 120초 분량 처리 후 값이 유한하고 버퍼가 커지지 않는지 (실시간 장시간 테스트 아님)."""
    from audio.dsp_analyzer import DspAnalyzer
    an = DspAnalyzer(48, SR)
    rng = np.random.default_rng(1)
    for i in range(int(120 * SR / 512)):
        x = (rng.standard_normal((512, 48)) * 0.05).astype(np.float32)
        if i % 500 == 0:
            x[:, 3] = 0
        an.process_block(x)
    s = an.snapshot(0)
    assert np.all(np.isfinite(s.long_db)) and np.all(np.isfinite(s.persistence))
    assert all(len(h) <= 400 for h in an._f0_hist)
