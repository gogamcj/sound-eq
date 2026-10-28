"""오디오 장치 처리 및 DSP 분석 (가상 입력)."""
import numpy as np
import pytest

from audio import device_manager as dm
from audio.audio_stream import UsbAudioInput
from audio.channel_mapping import ChannelConfig, ChannelMap
from audio.dsp_analyzer import AnalyzerConfig, DspAnalyzer
from audio.spectrum import MultiChannelStft, estimate_f0
from common.states import AudioState
from simulator.audio_sources import SynthSpec, render_offline

SR = 48000


class FakeSd:
    """sounddevice 대역 (장치 목록만 흉내)."""

    def __init__(self, devices):
        self._d = devices

    def query_devices(self):
        return self._d

    def query_hostapis(self):
        return [{"name": "ASIO"}, {"name": "Windows WASAPI"}]

    def check_input_settings(self, device, channels, samplerate, dtype):
        d = self._d[device]
        if channels > d["max_input_channels"] or samplerate not in (48000, 44100):
            raise ValueError("unsupported")


def test_no_audio_device_returns_empty():
    assert dm.list_input_devices(FakeSd([])) == []
    assert dm.find_wing_devices([]) == []


def test_wing_device_detection_and_capabilities():
    sd = FakeSd([
        {"name": "Microphone", "hostapi": 1, "max_input_channels": 2, "default_samplerate": 48000},
        {"name": "WING USB ASIO Driver", "hostapi": 0, "max_input_channels": 48, "default_samplerate": 48000},
    ])
    devs = dm.list_input_devices(sd)
    wing = dm.find_wing_devices(devs)
    assert wing[0].name.startswith("WING")
    assert dm.supported_samplerates(wing[0], 2, sd) == [48000, 44100]
    assert dm.max_supported_channels(wing[0], 48000, sd) == 48


def test_usb_input_missing_device_reports_no_device(monkeypatch):
    monkeypatch.setattr(dm, "load_backend", lambda *a, **k: FakeSd([]))
    src = UsbAudioInput(dm.InputDevice(3, "WING", "ASIO", 48, 48000), channels=2)
    assert src._open() is False
    assert src.state == AudioState.NO_DEVICE


def test_stft_sine_level_and_frequency():
    st = MultiChannelStft(1, SR, 4096, 1024)
    t = np.arange(SR) / SR
    x = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)[:, None]
    frames = st.feed(x)
    p = frames[-1][:, 0]
    k = int(np.argmax(p))
    assert abs(st.freqs[k] - 1000) < 12
    total_db = 10 * np.log10(p.sum())
    assert total_db == pytest.approx(20 * np.log10(0.5 / np.sqrt(2)), abs=1.0)


def test_f0_estimate_harmonic_signal():
    t = np.arange(8192) / SR
    x = sum((1 / h) * np.sin(2 * np.pi * 220 * h * t) for h in range(1, 8))
    st = MultiChannelStft(1, SR, 8192, 8192)
    p = st.feed(x.astype(np.float32)[:, None])[-1]
    f0, conf = estimate_f0(p, st.freqs)
    assert abs(f0[0] - 220) < 8 and conf[0] > 0.3


def run(specs, seconds=10.0, cfg=None):
    an = DspAnalyzer(len(specs), SR, cfg)
    for b in render_offline(specs, SR, seconds):
        an.process_block(b)
    return an


def test_silence_and_clipping_detection():
    an = run([SynthSpec(kind="silence"), SynthSpec(level_db=-6, clip=True), SynthSpec(level_db=-20)], 4)
    s0, s1, s2 = (an.snapshot(i) for i in range(3))
    assert s0.silent and s0.low_signal and s0.active_seconds == 0
    assert s1.clipping and s1.clip_count > 0
    assert not s2.clipping and not s2.silent and s2.active_seconds > 3


def test_rms_peak_levels():
    an = run([SynthSpec(level_db=-20)], 3)
    s = an.snapshot(0)
    assert s.rms_db == pytest.approx(-20, abs=2.0)
    assert s.peak_db > s.rms_db


def test_configurable_fft_and_hop():
    an = run([SynthSpec()], 2, AnalyzerConfig(n_fft=8192, hop=2048, window="blackmanharris"))
    assert an.stft.n_fft == 8192 and an.stft.hop == 2048
    with pytest.raises(ValueError):
        DspAnalyzer(1, SR, AnalyzerConfig(n_fft=1024, hop=4096))


def test_fixed_resonance_stands_out_in_long_term_average():
    """음정이 바뀌어도 같은 주파수에 남는 공진은 장기 평균 스펙트럼에서 돌출된다 (어택 프레임 제외 평균)."""
    from eq.recommendation_engine import _smooth_power_db
    an = run([SynthSpec(f0_range=(100, 300), n_harm=20, resonance=(700, 12, 6.0), pink_db=-40),
              SynthSpec(f0_range=(100, 300), n_harm=20, pink_db=-40)], 20)
    b = an.bands.index_of(700)
    nar = [(_smooth_power_db(an.snapshot(i).long_db, 1 / 6) - _smooth_power_db(an.snapshot(i).long_db, 1.0))
           for i in range(2)]
    assert nar[0][b - 2:b + 3].max() > 3.0
    assert nar[0][b - 2:b + 3].max() > nar[1][b - 2:b + 3].max() + 1.0
    assert an.snapshot(0).f0_spread_semitones > 3


def test_wrong_channel_count_block_rejected():
    an = DspAnalyzer(2, SR)
    with pytest.raises(ValueError):
        an.process_block(np.zeros((512, 3), np.float32))


def test_channel_mapping_validation():
    m = ChannelMap([ChannelConfig(1, 1), ChannelConfig(2, 1), ChannelConfig(5, 41), ChannelConfig(2, 3)])
    errs = m.validate(n_usb=4, max_mixer_ch=40)
    joined = " ".join(errs)
    assert "중복" in joined and "범위 밖" in joined
    ok = ChannelMap.identity(8, 40)
    assert ok.validate(8, 40) == []
    assert ok.by_usb(3).mixer_ch == 3
