import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from audio.audio_stream import AudioSource  # noqa: E402
from common.states import AudioState  # noqa: E402
from simulator.fake_wing import FakeWing  # noqa: E402


class ManualSource(AudioSource):
    """테스트용 입력: 블록을 직접 주입한다 (is_simulated=True)."""

    def start(self):
        self._set_state(AudioState.RECEIVING)

    def stop(self):
        self._set_state(AudioState.STOPPED)


@pytest.fixture
def fake_wing():
    fw = FakeWing().start()
    yield fw
    fw.stop()


@pytest.fixture
def rng():
    return np.random.default_rng(0)
