"""추천 엔진, 하울링 감지, EQ 커브 (가상 입력)."""
import numpy as np
import pytest

from audio.dsp_analyzer import DspAnalyzer
from eq import eq_curve
from eq.feedback_monitor import FeedbackMonitor
from eq.instrument_classifier import MAX_CONFIDENCE, classify
from eq.instrument_profiles import PROFILES, get_profile
from eq.recommendation_engine import RecommendationEngine
from mixer.models import ChannelEqState, ParamValue
from simulator.audio_sources import SynthSpec, render_offline
from simulator.fake_wing import default_channel

SR = 48000

REQUIRED_INSTRUMENTS = ["vocal_male", "vocal_female", "chorus", "acoustic_guitar", "electric_guitar", "bass_guitar",
                        "piano", "synth_organ", "kick", "snare", "tom", "overhead", "drum_bus", "cello", "violin",
                        "viola", "accordion", "saxophone", "brass", "woodwind", "custom"]


def eq_state(ch=1, **over):
    d = default_channel(ch)
    d.update(over)
    return ChannelEqState(ch, values={k: ParamValue(k, v) for k, v in d.items()})


def analyze(specs, seconds=20.0):
    an = DspAnalyzer(len(specs), SR)
    fm = FeedbackMonitor(an.bands.centers, len(specs), an.frame_dt)
    an.frame_listeners.append(fm.on_frame)
    for b in render_offline(specs, SR, seconds):
        an.process_block(b)
    return an, fm


def test_all_required_instrument_profiles_exist():
    for k in REQUIRED_INSTRUMENTS:
        assert k in PROFILES, k


def test_clean_signal_gets_no_eq_change():
    an, fm = analyze([SynthSpec(f0_range=(98, 260), n_harm=24, rolloff_db_oct=-7)])
    r = RecommendationEngine().analyze(an.snapshot(0), get_profile("vocal_male"), eq_state(), fm.suspects(0), True, 1)
    assert r.status == "no_issue" and not r.items


def test_feedback_tone_detected_and_notch_recommended():
    an, fm = analyze([SynthSpec(f0_range=(110, 220), level_db=-30, feedback=(2520.0, -18, 2.0))])
    sus = fm.suspects(0)
    assert sus and abs(sus[0].freq - 2520) < 60 and sus[0].score >= 0.8 and not sus[0].harmonic_of_f0
    r = RecommendationEngine().analyze(an.snapshot(0), get_profile("vocal_male"), eq_state(), sus, True, 1)
    notch = [i for i in r.items if i.is_notch]
    assert notch and notch[0].as_dict()[notch[0].target + "q"] >= 6
    assert -6.0 <= notch[0].as_dict()[notch[0].target + "g"] < 0


def test_instrument_harmonics_not_flagged_as_feedback():
    an, fm = analyze([SynthSpec(f0_range=(100, 400), n_harm=20)], 15)
    assert all(s.score < 0.5 for s in fm.suspects(0))


def test_resonance_detected_in_guitar_body():
    an, fm = analyze([SynthSpec(f0_range=(82, 330), n_harm=30, rolloff_db_oct=-5, resonance=(180, 9, 4.0), pink_db=-38)])
    r = RecommendationEngine().analyze(an.snapshot(0), get_profile("acoustic_guitar"), eq_state(), [], True, 1)
    assert r.items, r.message
    it = r.items[0]
    f = float(it.as_dict().get(it.target + "f", 0))
    assert 140 < f < 240 and it.as_dict()[it.target + "g"] < 0


def test_silent_and_clipping_produce_no_recommendation():
    an, fm = analyze([SynthSpec(kind="silence"), SynthSpec(level_db=-6, clip=True)], 6)
    eng = RecommendationEngine()
    assert eng.analyze(an.snapshot(0), get_profile("piano"), eq_state(), [], True, 1).status == "silent"
    assert eng.analyze(an.snapshot(1), get_profile("piano"), eq_state(), [], True, 1).status == "clipping"


def test_insufficient_data_no_recommendation():
    an, fm = analyze([SynthSpec()], 3)
    r = RecommendationEngine().analyze(an.snapshot(0), get_profile("piano"), eq_state(), [], True, 1)
    assert r.status == "insufficient"


def test_no_current_eq_means_no_recommendation():
    an, fm = analyze([SynthSpec(resonance=(500, 12, 4))], 10)
    r = RecommendationEngine().analyze(an.snapshot(0), get_profile("piano"), None, [], True, 1)
    assert r.status == "unsupported"


def test_existing_eq_band_preserved():
    """이미 사용 중인 밴드(게인≠0)의 주파수/Q 는 바꾸지 않는다."""
    an, fm = analyze([SynthSpec(f0_range=(82, 330), n_harm=30, rolloff_db_oct=-5, resonance=(180, 9, 4.0), pink_db=-38)])
    st = eq_state(**{"eq/1g": -2.0, "eq/1f": 190.0, "eq/1q": 1.5})
    r = RecommendationEngine().analyze(an.snapshot(0), get_profile("acoustic_guitar"), st, [], True, 1)
    for it in r.items:
        if it.target == "eq/1":
            assert set(it.as_dict()) <= {"eq/1g"}


def test_unconfirmed_instrument_lowers_confidence():
    an, fm = analyze([SynthSpec(f0_range=(82, 330), n_harm=30, rolloff_db_oct=-5, resonance=(180, 9, 4.0), pink_db=-38)])
    eng = RecommendationEngine()
    a = eng.analyze(an.snapshot(0), get_profile("acoustic_guitar"), eq_state(), [], True, 1)
    b = eng.analyze(an.snapshot(0), get_profile("acoustic_guitar"), eq_state(), [], False, 1)
    assert b.items[0].confidence < a.items[0].confidence


def test_multiple_instruments_classifier_is_only_a_hint():
    """여러 악기가 섞이면 분류 신뢰도는 상한(0.6)을 넘지 않는다."""
    an, fm = analyze([SynthSpec(f0_range=(82, 600), n_harm=25, note_s=0.15)], 10)
    for _, conf in classify(an.snapshot(0)):
        assert conf <= MAX_CONFIDENCE


def test_eq_curve_basic_shapes():
    f = np.array([50.0, 100.0, 1000.0, 10000.0])
    vals = {"eq/on": 1, "eq/1g": -6.0, "eq/1f": 1000.0, "eq/1q": 2.0, "flt/lc": 1, "flt/lcf": 100.0, "flt/lcs": "24"}
    r = eq_curve.response_db(vals, f)
    assert r[2] == pytest.approx(-6.0, abs=0.3)
    assert r[1] == pytest.approx(-3.0, abs=0.5)  # HPF 컷오프 -3dB
    assert r[0] < -20
    off = eq_curve.response_db({"eq/on": 0, "eq/1g": -6.0, "eq/1f": 1000.0}, f)
    assert np.allclose(off, 0)
