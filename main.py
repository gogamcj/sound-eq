"""WING Auto EQ Live — 진입점.

    python main.py                 # 실제 장비 모드: 메뉴 '연결 > 연결 및 설정' 에서 믹서 IP 와 USB 오디오 장치 선택
    python main.py --ip 192.168.1.71 --device 12 --channels 48
    python main.py --simulate      # WING 시뮬레이터 + 합성 오디오 (실제 장비 아님, 화면에 표시됨)
    python main.py --wav multitrack.wav --ip 192.168.1.71   # 녹음 멀티트랙 재생으로 분석 (오디오는 시뮬레이션 입력)
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys
from pathlib import Path


def app_dir() -> Path:
    # PyInstaller 실행 파일이면 exe 옆, 아니면 소스 폴더
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def setup_logging(d: Path) -> None:
    (d / "logs").mkdir(exist_ok=True)
    h = logging.handlers.RotatingFileHandler(d / "logs" / "wing_auto_eq.log", maxBytes=5_000_000, backupCount=5,
                                             encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[h, logging.StreamHandler()],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="WING Auto EQ Live")
    p.add_argument("--simulate", action="store_true", help="WING 시뮬레이터 + 합성 오디오")
    p.add_argument("--wav", help="멀티채널 WAV 파일을 오디오 입력으로 사용 (시뮬레이션 입력)")
    p.add_argument("--ip", help="WING IP")
    p.add_argument("--device", type=int, help="오디오 입력 장치 번호")
    p.add_argument("--channels", type=int, default=0, help="수신 채널 수 (0=장치 최대, 최대 48)")
    p.add_argument("--samplerate", type=int, default=48000)
    p.add_argument("--settings", default=None, help="설정 JSON 경로 (기본 config/settings.json)")
    p.add_argument("--screenshot", help=argparse.SUPPRESS)   # 개발용: 몇 초 후 화면 캡처 후 종료
    a = p.parse_args(argv)

    base = app_dir()
    setup_logging(base)
    log = logging.getLogger("main")

    from audio import device_manager as dm
    dm.load_backend()   # SD_ENABLE_ASIO 를 먼저 설정하고 로드

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from audio.audio_stream import UsbAudioInput
    from core.controller import AppController, AppSettings
    from eq.instrument_profiles import load_overrides
    from gui.main_window import MainWindow
    from gui.theme import STYLE

    settings_path = Path(a.settings) if a.settings else base / "config" / "settings.json"
    settings = AppSettings()
    if settings_path.exists():
        settings = AppSettings.from_dict(json.loads(settings_path.read_text("utf-8")))
    load_overrides(base / "config" / "instrument_overrides.json")

    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    db = base / "data" / ("simulation.db" if a.simulate else "wing_auto_eq.db")
    ctl = AppController(settings, db_path=str(db))
    sim_note = ""
    fake = None

    if a.simulate:
        from audio.channel_mapping import ChannelConfig, ChannelMap
        from simulator.audio_sources import SyntheticAudioSource, demo_band_specs
        from simulator.fake_wing import FakeWing
        fake = FakeWing().start()
        sim_note = "WING 시뮬레이터(127.0.0.1) + 합성 오디오. 실제 장비가 아니며 결과는 실장비 검증이 아닙니다."
        specs = demo_band_specs()
        ctl.channel_map = ChannelMap([ChannelConfig(i + 1, i + 1, n, k, False, "mic", False)
                                      for i, (n, k, _) in enumerate(specs)])
        ctl.start_audio(SyntheticAudioSource([s for _, _, s in specs], 48000))
        ctl.connect_mixer("127.0.0.1", fake.port, wait=2)
    else:
        if a.wav:
            from simulator.audio_sources import WavFileAudioSource
            ctl.start_audio(WavFileAudioSource(a.wav))
            sim_note = f"오디오 입력: 파일 재생 ({Path(a.wav).name}) — 실시간 USB 수신 아님"
        elif a.device is not None:
            devs = {d.index: d for d in dm.list_input_devices()}
            dev = devs.get(a.device)
            if dev is None:
                log.error("오디오 장치 %s 없음", a.device)
            else:
                n = a.channels or min(48, dm.max_supported_channels(dev, a.samplerate) or dev.max_input_channels)
                ctl.start_audio(UsbAudioInput(dev, n, a.samplerate))
        if a.ip:
            ctl.connect_mixer(a.ip, wait=0.1)

    win = MainWindow(ctl, sim_note)
    win.show()
    if a.screenshot:
        def shot():
            win.table.selectRow(int(Path(a.screenshot).stem.split("_")[-1]) if "_" in Path(a.screenshot).stem else 0)
            QTimer.singleShot(3500, lambda: (win.grab().save(a.screenshot), win.close()))
        QTimer.singleShot(16000, shot)
    rc = app.exec()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(ctl.settings.to_dict(), ensure_ascii=False, indent=2), "utf-8")
    if fake:
        fake.stop()
    return rc


if __name__ == "__main__":
    sys.exit(main())
