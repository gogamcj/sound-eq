"""GUI 스모크 테스트 (offscreen): 시뮬레이터 연결 상태로 창을 만들고 갱신·모드 변경·정지 버튼을 실행."""
import time

import numpy as np
import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from common.states import OperatingMode  # noqa: E402
from core.controller import AppController  # noqa: E402
from simulator.audio_sources import SynthChannel, SynthSpec  # noqa: E402

from .conftest import ManualSource  # noqa: E402


def test_main_window_smoke(fake_wing, tmp_path):
    from gui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    ctl = AppController(db_path=str(tmp_path / "g.db"))
    ctl.connect_mixer("127.0.0.1", fake_wing.port, wait=3)
    src = ManualSource(2, 48000)
    ctl.start_audio(src)
    chans = [SynthChannel(SynthSpec(pink_db=-40), 48000, 1), SynthChannel(SynthSpec(kind="silence"), 48000, 2)]
    for _ in range(int(10 * 48000 / 512)):
        ctl.analyzer.process_block(np.stack([c.render(512) for c in chans], axis=1))
    w = MainWindow(ctl, "test")
    w.table.selectRow(0)
    for _ in range(20):
        w._tick()
        app.processEvents()
        time.sleep(0.02)
    assert w.table.rowCount() == 2
    assert "연결됨" in w.chip_lan.text()
    ctl.set_mode(OperatingMode.AUTO_LIMITED)
    w._tick()
    w._stop_all()
    assert not ctl.safety.auto_enabled
    w.close()
    assert not fake_wing.write_log
