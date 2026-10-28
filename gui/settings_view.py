"""연결 및 설정 화면: 믹서(LAN), 오디오(USB), 분석, 안전 제한."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QTabWidget, QVBoxLayout,
                               QWidget)

from audio import device_manager as dm
from core.controller import AppSettings
from mixer.protocol_adapter import MixerError, WingOscAdapter, discover

from .workers import run_job


def dspin(lo, hi, val, step=0.5, dec=1):
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(dec)
    w.setSingleStep(step)
    w.setValue(val)
    return w


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None, audio_enabled: bool = True):
        super().__init__(parent)
        self.setWindowTitle("연결 및 설정")
        self.resize(560, 480)
        self.s = settings
        self.audio_choice = None
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # ---- 믹서
        w = QWidget()
        f = QFormLayout(w)
        self.ed_ip = QLineEdit(settings.mixer_ip)
        self.ed_ip.setPlaceholderText("예: 192.168.1.71 (WING 의 SETUP > 네트워크에서 확인)")
        self.sp_port = QSpinBox()
        self.sp_port.setRange(1, 65535)
        self.sp_port.setValue(settings.mixer_port)
        self.sp_timeout = dspin(0.1, 5.0, settings.osc_timeout_s, 0.1)
        self.chk_fallback = QCheckBox("장비가 범위를 알려주지 않을 때 보수적 기본 범위 사용 (공식 문서 값 아님)")
        self.chk_fallback.setChecked(settings.allow_fallback_ranges)
        self.lbl_test = QLabel("")
        self.lbl_test.setWordWrap(True)
        row = QHBoxLayout()
        b_test = QPushButton("연결 테스트 (/?)")
        b_disc = QPushButton("자동 검색 (WING? → 2222)")
        row.addWidget(b_test)
        row.addWidget(b_disc)
        f.addRow("믹서 IP", self.ed_ip)
        f.addRow("OSC 포트 (문서: 2223)", self.sp_port)
        f.addRow("응답 대기(초)", self.sp_timeout)
        f.addRow(self.chk_fallback)
        f.addRow(row)
        f.addRow(self.lbl_test)
        b_test.clicked.connect(self._test)
        b_disc.clicked.connect(self._discover)
        tabs.addTab(w, "믹서 (LAN)")

        # ---- 오디오
        w = QWidget()
        f = QFormLayout(w)
        self.cb_dev = QComboBox()
        self.cb_sr = QComboBox()
        self.sp_ch = QSpinBox()
        self.sp_ch.setRange(1, 64)
        self.lbl_audio = QLabel("")
        self.lbl_audio.setWordWrap(True)
        b_scan = QPushButton("장치 다시 검색")
        b_check = QPushButton("선택 장치 확인 (샘플레이트/최대 채널)")
        f.addRow("입력 장치", self.cb_dev)
        f.addRow(b_scan)
        f.addRow(b_check)
        f.addRow("샘플레이트", self.cb_sr)
        f.addRow("수신 채널 수", self.sp_ch)
        f.addRow(self.lbl_audio)
        b_scan.clicked.connect(self._scan)
        b_check.clicked.connect(self._check_dev)
        self.cb_dev.currentIndexChanged.connect(self._dev_changed)
        w.setEnabled(audio_enabled)
        tabs.addTab(w, "오디오 (USB)")

        # ---- 분석
        w = QWidget()
        f = QFormLayout(w)
        a = settings.analyzer
        self.cb_fft = QComboBox()
        for n in (2048, 4096, 8192, 16384):
            self.cb_fft.addItem(str(n), n)
        self.cb_fft.setCurrentIndex(self.cb_fft.findData(a.n_fft))
        self.cb_hop = QComboBox()
        for n in (256, 512, 1024, 2048, 4096):
            self.cb_hop.addItem(str(n), n)
        self.cb_hop.setCurrentIndex(self.cb_hop.findData(a.hop))
        self.cb_win = QComboBox()
        for n in ("hann", "hamming", "blackmanharris"):
            self.cb_win.addItem(n, n)
        self.cb_win.setCurrentIndex(self.cb_win.findData(a.window))
        self.sp_sil = dspin(-90, -30, a.silence_db, 1, 0)
        self.sp_low = dspin(-80, -20, a.low_signal_db, 1, 0)
        self.cb_tap = QComboBox()
        self.cb_tap.addItem("EQ 이전 신호 (입력 소스를 USB 로 라우팅)", "pre_eq")
        self.cb_tap.addItem("EQ 이후 신호 (다이렉트 아웃 등 EQ 뒤 탭)", "post_eq")
        self.cb_tap.setCurrentIndex(self.cb_tap.findData(settings.engine.analysis_tap))
        self.sp_minsec = dspin(3, 120, settings.engine.min_analysis_seconds, 1, 0)
        f.addRow("FFT 크기", self.cb_fft)
        f.addRow("홉 크기", self.cb_hop)
        f.addRow("창 함수", self.cb_win)
        f.addRow("무음 기준 (dBFS)", self.sp_sil)
        f.addRow("신호 부족 기준 (dBFS)", self.sp_low)
        f.addRow("USB 신호 탭 위치", self.cb_tap)
        f.addRow("추천 전 최소 분석 시간(초)", self.sp_minsec)
        f.addRow(QLabel("FFT/홉 변경은 오디오를 다시 시작하면 적용됩니다."))
        tabs.addTab(w, "분석")

        # ---- 안전
        w = QWidget()
        f = QFormLayout(w)
        sf = settings.safety
        self.sp_step = dspin(0.1, 6, sf.max_step_db, 0.1)
        self.sp_cum = dspin(0.5, 12, sf.max_cumulative_db, 0.5)
        self.sp_int = dspin(2, 600, sf.min_interval_s, 1, 0)
        self.sp_conf = dspin(0.3, 1.0, sf.auto_min_confidence, 0.05, 2)
        self.chk_boost = QCheckBox("자동 모드 부스트 허용")
        self.chk_boost.setChecked(sf.auto_allow_boost)
        self.chk_notch = QCheckBox("자동 노치 허용 (하울링 의심도 높음 + 모드 C 에서만)")
        self.chk_notch.setChecked(sf.auto_allow_notch)
        self.sp_notch = dspin(1, 12, sf.max_notch_cut_db, 0.5)
        self.sp_nn = QSpinBox()
        self.sp_nn.setRange(0, 4)
        self.sp_nn.setValue(sf.max_notches_per_channel)
        f.addRow("자동 1회 최대 변경 (dB)", self.sp_step)
        f.addRow("누적 최대 변경 (dB)", self.sp_cum)
        f.addRow("채널별 최소 조정 간격 (초)", self.sp_int)
        f.addRow("자동 적용 최소 신뢰도", self.sp_conf)
        f.addRow(self.chk_boost)
        f.addRow(self.chk_notch)
        f.addRow("노치 최대 감쇠 (dB)", self.sp_notch)
        f.addRow("채널당 노치 최대 개수", self.sp_nn)
        f.addRow(QLabel("프리앰프 게인·팬텀·출력 레벨·라우팅은 이 프로그램이 절대 변경하지 않습니다."))
        tabs.addTab(w, "안전 제한")

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._devices: list[dm.InputDevice] = []
        if audio_enabled:
            self._scan()

    # ------------------------------------------------------------------
    def _test(self):
        ip, port = self.ed_ip.text().strip(), self.sp_port.value()
        self.lbl_test.setText("연결 확인 중...")

        def job():
            ad = WingOscAdapter(ip, port, timeout=self.sp_timeout.value(), retries=1)
            ad.open()
            try:
                info = ad.ping()
                return f"응답 OK ({ad.last_rtt_ms:.1f} ms): {info.name} / {info.model} / FW {info.firmware}" + (
                    "  ← 시뮬레이터" if ad.is_simulator else "")
            except MixerError as e:
                return f"응답 없음: {e}. IP·서브넷·케이블·방화벽(UDP {port}) 확인"
            finally:
                ad.close()
        run_job(job, on_done=self.lbl_test.setText, on_fail=self.lbl_test.setText)

    def _discover(self):
        self.lbl_test.setText("검색 중...")

        def done(found):
            if not found:
                self.lbl_test.setText("응답한 WING 없음 (브로드캐스트가 막혀 있을 수 있음 — IP 직접 입력)")
                return
            self.ed_ip.setText(found[0]["ip"] or found[0]["from"])
            self.lbl_test.setText("발견: " + "; ".join(f"{d['name']} {d['ip']} {d['model']} FW {d['firmware']}"
                                                      for d in found))
        run_job(discover, on_done=done, on_fail=self.lbl_test.setText)

    def _scan(self):
        self._devices = dm.list_input_devices()
        self.cb_dev.clear()
        if not self._devices:
            self.lbl_audio.setText(dm.backend_error() or "입력 장치가 없습니다. WING USB 케이블/드라이버를 확인하세요.")
            return
        wings = dm.find_wing_devices(self._devices)
        for d in self._devices:
            self.cb_dev.addItem(d.label(), d.index)
        if wings:
            self.cb_dev.setCurrentIndex(self.cb_dev.findData(wings[0].index))
            self.lbl_audio.setText("WING 후보 장치를 선택했습니다. '선택 장치 확인'을 누르세요.")
        else:
            self.lbl_audio.setText("이름에 WING 이 들어간 장치가 없습니다. 직접 선택하세요.")

    def _dev(self):
        idx = self.cb_dev.currentData()
        return next((d for d in self._devices if d.index == idx), None)

    def _dev_changed(self, *_):
        d = self._dev()
        if d:
            self.sp_ch.setMaximum(max(1, d.max_input_channels))
            self.sp_ch.setValue(min(d.max_input_channels, 48))

    def _check_dev(self):
        d = self._dev()
        if not d:
            return
        rates = dm.supported_samplerates(d, min(2, d.max_input_channels))
        self.cb_sr.clear()
        for r in rates:
            self.cb_sr.addItem(str(r), r)
        sr = 48000 if 48000 in rates else (rates[0] if rates else int(d.default_samplerate))
        n = dm.max_supported_channels(d, sr)
        self.sp_ch.setValue(n or 1)
        self.lbl_audio.setText(f"지원 샘플레이트: {rates or '확인 실패'} / {sr}Hz 에서 열 수 있는 최대 {n}ch "
                               f"(드라이버 보고 {d.max_input_channels}ch). 실제 신호가 오는 채널은 믹서 USB 출력 "
                               f"라우팅에 따라 다릅니다.")

    def _accept(self):
        s = self.s
        s.mixer_ip = self.ed_ip.text().strip()
        s.mixer_port = self.sp_port.value()
        s.osc_timeout_s = self.sp_timeout.value()
        s.allow_fallback_ranges = self.chk_fallback.isChecked()
        s.analyzer.n_fft = self.cb_fft.currentData()
        s.analyzer.hop = min(self.cb_hop.currentData(), s.analyzer.n_fft)
        s.analyzer.window = self.cb_win.currentData()
        s.analyzer.silence_db = self.sp_sil.value()
        s.analyzer.low_signal_db = self.sp_low.value()
        s.engine.analysis_tap = self.cb_tap.currentData()
        s.engine.min_analysis_seconds = self.sp_minsec.value()
        sf = s.safety
        sf.max_step_db = self.sp_step.value()
        sf.max_cumulative_db = self.sp_cum.value()
        sf.min_interval_s = self.sp_int.value()
        sf.auto_min_confidence = self.sp_conf.value()
        sf.auto_allow_boost = self.chk_boost.isChecked()
        sf.auto_allow_notch = self.chk_notch.isChecked()
        sf.max_notch_cut_db = self.sp_notch.value()
        sf.max_notches_per_channel = self.sp_nn.value()
        d = self._dev()
        if d is not None and self.cb_sr.count():
            self.audio_choice = (d, self.cb_sr.currentData(), self.sp_ch.value())
        self.accept()

