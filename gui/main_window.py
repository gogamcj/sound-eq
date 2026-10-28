"""메인 화면."""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QListWidget, QMainWindow,
                               QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget)

from audio.audio_stream import UsbAudioInput
from common.latency import LATENCY
from common.states import AudioState, ChannelStatus, LinkState, OperatingMode
from core.controller import AppController
from eq.instrument_profiles import get_profile
from mixer.eq_controller import ApplyStatus

from .channel_view import ChannelTable
from .eq_editor import ChannelDetail
from .settings_view import SettingsDialog
from .theme import COLORS, chip_style
from .workers import run_job

log = logging.getLogger(__name__)

LINK_COLORS = {LinkState.DISCONNECTED: COLORS["muted"], LinkState.CONNECTING: COLORS["warn"],
               LinkState.CONNECTED: COLORS["ok"], LinkState.LOST: COLORS["err"]}
AUDIO_COLORS = {AudioState.NO_DEVICE: COLORS["err"], AudioState.STOPPED: COLORS["muted"],
                AudioState.STARTING: COLORS["warn"], AudioState.RECEIVING: COLORS["ok"], AudioState.ERROR: COLORS["err"]}
STATUS_COLORS = {ChannelStatus.APPLIED_VERIFIED: COLORS["ok"], ChannelStatus.APPLY_FAILED: COLORS["err"],
                 ChannelStatus.AWAITING_APPROVAL: COLORS["accent"], ChannelStatus.APPLYING: COLORS["warn"],
                 ChannelStatus.AUTO_STOPPED: COLORS["err"]}


class MainWindow(QMainWindow):
    def __init__(self, ctl: AppController, simulation_note: str = ""):
        super().__init__()
        self.ctl = ctl
        self.sim_note = simulation_note
        self.selected = -1
        self._busy = False
        self._last_overview = 0.0
        self._last_detail = 0.0
        self._msg_seen = 0
        self.setWindowTitle("WING Auto EQ Live" + (" — 시뮬레이션" if simulation_note else ""))
        self.resize(1500, 900)
        self._build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(66)   # ~15fps

    # ================================================================ UI 구성
    def _build(self):
        root = QWidget()
        v = QVBoxLayout(root)
        if self.sim_note:
            b = QLabel(f"시뮬레이션 모드 — {self.sim_note}")
            b.setObjectName("banner")
            b.setAlignment(Qt.AlignCenter)
            v.addWidget(b)
        bar = QFrame()
        bar.setObjectName("panel")
        h = QHBoxLayout(bar)
        self.chip_mixer = QLabel()
        self.chip_audio = QLabel()
        self.chip_lan = QLabel()
        for c in (self.chip_mixer, self.chip_audio, self.chip_lan):
            c.setObjectName("chip")
            h.addWidget(c)
        self.lbl_audioinfo = QLabel("")
        h.addWidget(self.lbl_audioinfo)
        h.addStretch(1)
        h.addWidget(QLabel("운영 모드"))
        self.cb_mode = QComboBox()
        for m in OperatingMode:
            self.cb_mode.addItem(m.value, m)
        self.cb_mode.currentIndexChanged.connect(self._mode_changed)
        h.addWidget(self.cb_mode)
        self.btn_auto = QPushButton("자동 보정 시작")
        self.btn_auto.clicked.connect(self._enable_auto)
        h.addWidget(self.btn_auto)
        self.lbl_auto = QLabel("")
        h.addWidget(self.lbl_auto)
        self.btn_stop = QPushButton("■ 자동 보정 전체 정지")
        self.btn_stop.setObjectName("stopAll")
        self.btn_stop.clicked.connect(self._stop_all)
        h.addWidget(self.btn_stop)
        v.addWidget(bar)

        split = QSplitter(Qt.Horizontal)
        self.table = ChannelTable()
        self.table.itemSelectionChanged.connect(self._sel_changed)
        split.addWidget(self.table)
        self.detail = ChannelDetail()
        self.detail.apply_requested.connect(self._apply)
        self.detail.reject_requested.connect(self._reject)
        self.detail.restore_requested.connect(self._restore)
        self.detail.reread_requested.connect(self._reread)
        self.detail.config_changed.connect(self._cfg_changed)
        split.addWidget(self.detail)
        split.setSizes([760, 740])
        v.addWidget(split, 1)
        bottom = QHBoxLayout()
        self.log_list = QListWidget()
        self.log_list.setMaximumHeight(120)
        bottom.addWidget(self.log_list, 1)
        self.lbl_latency = QLabel("")
        self.lbl_latency.setStyleSheet(f"color: {COLORS['muted']}")
        self.lbl_latency.setWordWrap(True)
        self.lbl_latency.setMaximumWidth(380)
        bottom.addWidget(self.lbl_latency)
        v.addLayout(bottom)
        self.setCentralWidget(root)

        mb = self.menuBar()
        m = mb.addMenu("파일")
        self._act(m, "공연 프로필 저장...", self._save_profile)
        self._act(m, "공연 프로필 불러오기...", self._load_profile)
        m.addSeparator()
        self._act(m, "종료", self.close)
        m = mb.addMenu("연결")
        self._act(m, "연결 및 설정...", self._open_settings)
        self._act(m, "믹서 연결 끊기", lambda: run_job(self.ctl.disconnect_mixer))
        m = mb.addMenu("기록")
        self._act(m, "변경 이력 보기", self._show_history)

    def _act(self, menu, text, fn):
        a = QAction(text, self)
        a.triggered.connect(fn)
        menu.addAction(a)

    # ================================================================ 주기 갱신
    def _tick(self):
        t0 = time.perf_counter()
        ctl = self.ctl
        ls = ctl.link_state
        sim = " [시뮬레이터]" if ctl.mixer_is_simulator else ""
        info = ctl.link.info if ctl.link and ctl.link.info and ls == LinkState.CONNECTED else None
        self.chip_mixer.setText(f"WING COMPACT: {(info.model + sim) if info else '미연결'}")
        self.chip_mixer.setStyleSheet(chip_style(COLORS["sim"] if sim else LINK_COLORS[ls]))
        self.chip_lan.setText(f"LAN 제어: {ls.value}")
        self.chip_lan.setStyleSheet(chip_style(LINK_COLORS[ls]))
        a = ctl.audio
        ast = ctl.audio_state
        simtag = " [시뮬레이션 입력]" if a and a.is_simulated else ""
        self.chip_audio.setText(f"USB 오디오: {ast.value}{simtag}")
        self.chip_audio.setStyleSheet(chip_style(COLORS["sim"] if simtag else AUDIO_COLORS[ast]))
        if a:
            self.lbl_audioinfo.setText(f"{a.samplerate} Hz · 입력 {a.channels}ch · 드롭 {a.dropped_blocks} · "
                                       f"오류 {a.status_errors}")
        sf = ctl.safety
        if sf.auto_enabled:
            self.lbl_auto.setText("자동 보정 작동 중")
            self.lbl_auto.setStyleSheet(f"color: {COLORS['accent']}; font-weight: bold")
        else:
            self.lbl_auto.setText(f"자동 보정 꺼짐{(' — ' + sf.stop_reason) if sf.stop_reason else ''}")
            self.lbl_auto.setStyleSheet(f"color: {COLORS['muted']}")
        self.btn_auto.setEnabled(sf.mode == OperatingMode.AUTO_LIMITED and not sf.auto_enabled)
        if self.cb_mode.currentData() != sf.mode:
            self.cb_mode.blockSignals(True)
            self.cb_mode.setCurrentIndex(self.cb_mode.findData(sf.mode))
            self.cb_mode.blockSignals(False)
        self._update_table()
        now = time.monotonic()
        if self.selected >= 0 and ctl.analyzer:
            self._update_spectrum()
            if now - self._last_detail > 2.0 and not self._busy:
                self._last_detail = now
                self._refresh_recommendation()
        if now - self._last_overview > 5.0 and ctl.analyzer:
            self._last_overview = now
            run_job(self._overview_job)
        for ts, lvl, text in ctl.messages[self._msg_seen:]:
            self.log_list.insertItem(0, f"{time.strftime('%H:%M:%S', time.localtime(ts))} [{lvl}] {text}")
        self._msg_seen = len(ctl.messages)
        LATENCY.add("gui", (time.perf_counter() - t0) * 1000)
        self.lbl_latency.setText("지연: " + LATENCY.text())

    def _overview_job(self):
        """전체 채널 추천 상태 갱신 (작업 스레드). 무음/데이터 부족 채널은 믹서를 조회하지 않는다."""
        for cfg in self.ctl.channel_map.channels:
            i = cfg.usb_input - 1
            s = self.ctl.snapshot(i)
            if s is None or s.silent or s.active_seconds < self.ctl.settings.engine.min_analysis_seconds:
                continue
            self.ctl.recommend(i, refresh_eq=False)

    def _update_table(self):
        ctl = self.ctl
        if not ctl.analyzer:
            return
        n = ctl.analyzer.n
        self.table.ensure_rows(n)
        for i in range(n):
            s = ctl.snapshot(i)
            cfg = ctl.channel_map.by_usb(i + 1)
            eq = ctl.eq_cache.get(cfg.mixer_ch) if cfg and cfg.mixer_ch else None
            rec = ctl.recs.get(i)
            if s.silent:
                ana = ("무음", COLORS["muted"])
            elif s.clipping:
                ana = ("클리핑!", COLORS["err"])
            elif s.low_signal:
                ana = ("신호 부족", COLORS["warn"])
            else:
                ana = (f"분석 중 {s.active_seconds:.0f}s", COLORS["info"])
            st = ctl.ch_status.get(i, ChannelStatus.IDLE)
            if st in (ChannelStatus.APPLIED_VERIFIED, ChannelStatus.APPLY_FAILED, ChannelStatus.APPLYING,
                      ChannelStatus.AUTO_STOPPED):
                rtxt = (st.value, STATUS_COLORS.get(st, COLORS["text"]))
            elif rec is None:
                rtxt = ("추천 대기", COLORS["muted"])
            elif rec.items:
                rtxt = (f"추천 {len(rec.items)}건" + (" · 승인 대기" if st == ChannelStatus.AWAITING_APPROVAL else ""),
                        COLORS["accent"])
            else:
                rtxt = (rec.message[:24], COLORS["muted"])
            fz = cfg and cfg.mixer_ch in ctl.safety.frozen
            self.table.set_row(i, {
                "USB": str(i + 1),
                "믹서 CH": str(cfg.mixer_ch) if cfg and cfg.mixer_ch else "—",
                "이름": (cfg.name or (eq.name if eq else "")) if cfg else "",
                "악기": (get_profile(cfg.instrument).label + ("" if cfg.instrument_confirmed else " ?")) if cfg else "",
                "레벨": (s.rms_db, s.peak_hold_db, s.clipping),
                "피크": f"{s.peak_hold_db:.0f}",
                "EQ": ("—" if not eq else (f"{'ON' if eq.eq_on else 'OFF'} {eq.eq_model or ''}")),
                "분석": ana,
                "추천/상태": rtxt if not fz else ("자동 정지: " + ctl.safety.frozen[cfg.mixer_ch][:16], COLORS["err"]),
                "자동": ("●", COLORS["accent"]) if cfg and cfg.auto_allowed else ("", COLORS["muted"]),
            })

    def _update_spectrum(self):
        i = self.selected
        s = self.ctl.snapshot(i)
        rec = self.ctl.recs.get(i)
        markers = []
        if self.ctl.feedback:
            for f in self.ctl.feedback.suspects(i):
                if f.score >= 0.5:
                    markers.append((f.freq, f"하울링 의심 {f.score:.2f}", COLORS["err"]))
        if s.f0_median:
            markers.append((s.f0_median, "f0", COLORS["muted"]))
        cfg = self.ctl.channel_map.by_usb(i + 1)
        title = f"USB {i + 1}" + (f" → ch{cfg.mixer_ch} {cfg.name}" if cfg and cfg.mixer_ch else " (매핑 없음)")
        self.detail.spectrum.set_data(s.centers, s.display_db, s.long_db,
                                      rec.current_curve if rec else None,
                                      rec.expected_curve if rec and rec.items else None, markers, title)

    def _refresh_recommendation(self):
        i = self.selected
        self._busy = True

        def done(rec):
            self._busy = False
            if i != self.selected:
                return
            cfg = self.ctl.channel_map.by_usb(i + 1)
            eq = self.ctl.eq_cache.get(cfg.mixer_ch) if cfg and cfg.mixer_ch else None
            mode = self.ctl.safety.mode
            can = (mode != OperatingMode.ANALYSIS_ONLY and self.ctl.link_state == LinkState.CONNECTED
                   and cfg is not None and cfg.mixer_ch is not None)
            st = self.ctl.ch_status.get(i, ChannelStatus.IDLE)
            head = rec.message if rec else "분석 데이터 없음"
            if mode == OperatingMode.ANALYSIS_ONLY:
                head += "  ·  [모드 A: 분석 전용 — 믹서 변경 안 함]"
            head += f"  ·  상태: {st.value}"
            self.detail.show_recommendation(rec, eq, can, head)
            self.detail.set_suggestions(self.ctl.instrument_suggestions(i))

        run_job(self.ctl.recommend, i, True, on_done=done, on_fail=lambda e: setattr(self, "_busy", False))

    # ================================================================ 이벤트
    def _sel_changed(self):
        rows = self.table.selectionModel().selectedRows()
        self.selected = rows[0].row() if rows else -1
        if self.selected >= 0:
            cfg = self.ctl.channel_map.by_usb(self.selected + 1)
            if cfg:
                self.detail.load_config(cfg, self.ctl.instrument_suggestions(self.selected))
            self._last_detail = 0

    def _cfg_changed(self, cfg):
        n_usb = self.ctl.analyzer.n if self.ctl.analyzer else len(self.ctl.channel_map.channels)
        errs = self.ctl.channel_map.validate(n_usb, self.ctl.mixer_channel_count)
        if errs:
            self.ctl.log("warning", "mapping", "매핑 오류: " + "; ".join(errs))
        self.ctl.recs.pop(cfg.usb_input - 1, None)
        self._last_detail = 0

    def _mode_changed(self, *_):
        mode = self.cb_mode.currentData()
        if mode == OperatingMode.AUTO_LIMITED:
            r = QMessageBox.question(self, "모드 C 확인",
                                     "제한적 자동 보정 모드로 전환합니다.\n자동 보정은 '자동 보정 시작'을 눌러야 작동하며, "
                                     "'자동 보정 허용' + '악기 확정' 채널만 제한된 양으로 조정합니다.\n계속할까요?")
            if r != QMessageBox.Yes:
                self.cb_mode.blockSignals(True)
                self.cb_mode.setCurrentIndex(self.cb_mode.findData(self.ctl.safety.mode))
                self.cb_mode.blockSignals(False)
                return
        self.ctl.set_mode(mode)
        self._last_detail = 0

    def _enable_auto(self):
        allowed = [c for c in self.ctl.channel_map.channels if c.auto_allowed]
        if not allowed:
            QMessageBox.information(self, "자동 보정", "자동 보정 허용 채널이 없습니다. 채널 설정에서 지정하세요.")
            return
        d = self.ctl.enable_auto()
        if not d.allowed:
            QMessageBox.warning(self, "자동 보정", d.reason)

    def _stop_all(self):
        self.ctl.stop_all_auto()

    def _apply(self, items):
        i = self.selected
        if i < 0:
            QMessageBox.information(self, "적용", "왼쪽 목록에서 채널을 먼저 선택하세요.")
            return
        if not items:
            QMessageBox.information(self, "적용", "추천 표에서 적용할 항목의 체크칸을 켠 뒤 다시 누르세요.")
            return
        self.ctl.ch_status[i] = ChannelStatus.APPLYING
        self.detail.btn_apply.setEnabled(False)

        def done(results):
            r = results[0]
            if r.status == ApplyStatus.VERIFIED:
                QMessageBox.information(self, "적용 결과", f"믹서 {r.ch}번 채널에 적용 완료 및 재조회 확인\n"
                                                           f"재조회 값: {r.after}")
            elif r.ok:
                QMessageBox.information(self, "적용 결과", r.status.value)
            else:
                QMessageBox.warning(self, "적용 결과", f"{r.status.value}\n{r.message}")
            self._last_detail = 0

        run_job(self.ctl.apply_recommendation, i, items, on_done=done,
                on_fail=lambda e: QMessageBox.critical(self, "적용 오류", e))

    def _reject(self):
        if self.selected >= 0:
            self.ctl.reject_recommendation(self.selected)
            self._last_detail = time.monotonic() + 10  # 잠시 동안 같은 추천을 다시 띄우지 않음

    def _restore(self):
        cfg = self.ctl.channel_map.by_usb(self.selected + 1) if self.selected >= 0 else None
        if not cfg or not cfg.mixer_ch:
            return
        if QMessageBox.question(self, "복구", f"ch{cfg.mixer_ch} 에서 이 프로그램이 마지막으로 바꾼 값을 되돌립니다.\n"
                                "현재 값이 프로그램이 적용한 값과 다르면(사용자 변경) 그 항목은 건너뜁니다.") != QMessageBox.Yes:
            return

        def done(rep):
            if not rep:
                QMessageBox.information(self, "복구", "되돌릴 프로그램 변경 이력이 없습니다.")
                return
            QMessageBox.information(self, "복구 결과", "\n".join(str(x) for x in rep))
        run_job(self.ctl.restore_channel, cfg.mixer_ch, on_done=done,
                on_fail=lambda e: QMessageBox.warning(self, "복구 불가", e))

    def _reread(self):
        cfg = self.ctl.channel_map.by_usb(self.selected + 1) if self.selected >= 0 else None
        if cfg and cfg.mixer_ch:
            run_job(self.ctl.read_eq, cfg.mixer_ch, True)
            self._last_detail = 0

    def _open_settings(self):
        s = self.ctl.settings
        prev = (s.mixer_ip, s.mixer_port)
        dlg = SettingsDialog(s, self, audio_enabled=not (self.ctl.audio and self.ctl.audio.is_simulated))
        if not dlg.exec():
            return
        self.ctl.validator.allow_fallback_ranges = s.allow_fallback_ranges
        if s.mixer_ip and (self.ctl.link is None or (s.mixer_ip, s.mixer_port) != prev):
            run_job(self.ctl.connect_mixer, s.mixer_ip, s.mixer_port)
        if dlg.audio_choice:
            dev, sr, ch = dlg.audio_choice
            self.ctl.start_audio(UsbAudioInput(dev, ch, sr))

    def _save_profile(self):
        name, ok = QInputDialog.getText(self, "프로필 저장", "공연 프로필 이름:")
        if ok and name:
            self.ctl.save_profile(name)
            self.ctl.log("info", "profile", f"프로필 저장: {name} (믹서 SHOW/SNAP 과 별개)")

    def _load_profile(self):
        names = self.ctl.profiles.list()
        if not names:
            QMessageBox.information(self, "프로필", "저장된 프로필이 없습니다.")
            return
        name, ok = QInputDialog.getItem(self, "프로필 불러오기", "프로필:", names, 0, False)
        if ok:
            self.ctl.load_profile(name)
            self.ctl.log("info", "profile", f"프로필 불러옴: {name} (믹서 설정은 변경하지 않음)")
            self._sel_changed()

    def _show_history(self):
        rows = self.ctl.history.changes(limit=50)
        txt = "\n".join(f"#{r.id} {time.strftime('%m-%d %H:%M:%S', time.localtime(r.ts))} ch{r.mixer_ch} "
                        f"{r.source} {r.status}{' [SIM]' if r.simulated else ''} {r.before} → {r.after}"
                        f"{' (복구됨)' if r.restored_by else ''}" for r in rows)
        QMessageBox.information(self, "변경 이력 (최근 50건)", txt or "기록 없음")

    def closeEvent(self, e):
        self.timer.stop()
        self.ctl.shutdown()   # 자동 적용 중지. 믹서에는 아무것도 쓰지 않음
        e.accept()
