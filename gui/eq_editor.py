"""채널 상세: 채널 설정(매핑/악기/자동 허용), 스펙트럼·EQ 커브, 현재값 vs 추천값 비교, 적용/거부/복구."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTextEdit,
                               QVBoxLayout, QWidget)

from audio.channel_mapping import ChannelConfig
from eq.instrument_profiles import PROFILES

from .spectrum_view import SpectrumView
from .theme import COLORS

CMP_COLS = ["적용", "대상", "파라미터", "현재값", "추천값", "변경 이유", "신뢰도", "근거", "청음"]


def fmt(v):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return str(v)


class ChannelDetail(QWidget):
    apply_requested = Signal(list)
    reject_requested = Signal()
    restore_requested = Signal()
    reread_requested = Signal()
    config_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cfg: ChannelConfig | None = None
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        # ---- 채널 설정
        box = QGroupBox("채널 설정 (USB 입력 ↔ 믹서 채널 매핑)")
        g = QGridLayout(box)
        self.lbl_usb = QLabel("USB —")
        self.sp_mixer = QSpinBox()
        self.sp_mixer.setRange(0, 48)
        self.sp_mixer.setSpecialValueText("매핑 안 함")
        self.ed_name = QLineEdit()
        self.cb_inst = QComboBox()
        for k, p in PROFILES.items():
            self.cb_inst.addItem(p.label, k)
        self.cb_src = QComboBox()
        self.cb_src.addItem("마이크", "mic")
        self.cb_src.addItem("DI", "di")
        self.chk_confirm = QCheckBox("악기 유형 확정 (자동 보정 조건)")
        self.chk_auto = QCheckBox("자동 보정 허용")
        self.lbl_suggest = QLabel("")
        self.lbl_suggest.setStyleSheet(f"color: {COLORS['muted']}")
        g.addWidget(self.lbl_usb, 0, 0)
        g.addWidget(QLabel("믹서 채널"), 0, 1)
        g.addWidget(self.sp_mixer, 0, 2)
        g.addWidget(QLabel("이름"), 0, 3)
        g.addWidget(self.ed_name, 0, 4)
        g.addWidget(QLabel("악기"), 1, 0)
        g.addWidget(self.cb_inst, 1, 1, 1, 2)
        g.addWidget(self.cb_src, 1, 3)
        g.addWidget(self.chk_confirm, 1, 4)
        g.addWidget(self.chk_auto, 2, 4)
        g.addWidget(self.lbl_suggest, 2, 0, 1, 4)
        for w in (self.sp_mixer,):
            w.valueChanged.connect(self._emit_cfg)
        self.ed_name.editingFinished.connect(self._emit_cfg)
        self.cb_inst.currentIndexChanged.connect(self._emit_cfg)
        self.cb_src.currentIndexChanged.connect(self._emit_cfg)
        self.chk_confirm.toggled.connect(self._emit_cfg)
        self.chk_auto.toggled.connect(self._emit_cfg)
        lay.addWidget(box)

        # ---- 스펙트럼
        self.spectrum = SpectrumView()
        lay.addWidget(self.spectrum, 3)

        # ---- 상태/비교
        self.lbl_status = QLabel("채널을 선택하세요")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("font-weight: bold; padding: 4px;")
        lay.addWidget(self.lbl_status)
        self.lbl_eq = QLabel("")
        self.lbl_eq.setWordWrap(True)
        self.lbl_eq.setStyleSheet(f"color: {COLORS['muted']}")
        lay.addWidget(self.lbl_eq)
        self.cmp = QTableWidget(0, len(CMP_COLS))
        self.cmp.setHorizontalHeaderLabels(CMP_COLS)
        self.cmp.verticalHeader().setVisible(False)
        self.cmp.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        for i, w in enumerate((40, 60, 70, 70, 70, 0, 56, 50, 44)):
            if w:
                self.cmp.setColumnWidth(i, w)
        lay.addWidget(self.cmp, 2)
        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(90)
        lay.addWidget(self.notes)

        row = QHBoxLayout()
        self.btn_apply = QPushButton("선택 항목 믹서에 적용")
        self.btn_apply.setObjectName("apply")
        self.btn_reject = QPushButton("거부 (현재 설정 유지)")
        self.btn_restore = QPushButton("이전 설정 복구 (마지막 변경)")
        self.btn_reread = QPushButton("믹서 EQ 다시 읽기")
        for b in (self.btn_apply, self.btn_reject, self.btn_restore, self.btn_reread):
            row.addWidget(b)
        lay.addLayout(row)
        self.btn_apply.clicked.connect(lambda: self.apply_requested.emit(self.checked_items()))
        self.btn_reject.clicked.connect(self.reject_requested.emit)
        self.btn_restore.clicked.connect(self.restore_requested.emit)
        self.btn_reread.clicked.connect(self.reread_requested.emit)
        self._row_item: list[int] = []

    # ------------------------------------------------------------------
    def load_config(self, cfg: ChannelConfig, suggestions) -> None:
        self._loading = True
        self._cfg = cfg
        self.lbl_usb.setText(f"USB 입력 {cfg.usb_input}")
        self.sp_mixer.setValue(cfg.mixer_ch or 0)
        self.ed_name.setText(cfg.name)
        i = self.cb_inst.findData(cfg.instrument)
        self.cb_inst.setCurrentIndex(max(i, 0))
        self.cb_src.setCurrentIndex(0 if cfg.source_type == "mic" else 1)
        self.chk_confirm.setChecked(cfg.instrument_confirmed)
        self.chk_auto.setChecked(cfg.auto_allowed)
        self._loading = False
        self.set_suggestions(suggestions)

    def set_suggestions(self, suggestions) -> None:
        if suggestions:
            s = ", ".join(f"{PROFILES[k].label} {c:.0%}" for k, c in suggestions if k in PROFILES)
            self.lbl_suggest.setText(f"자동 악기 감지(보조, 참고용): {s}")
        else:
            self.lbl_suggest.setText("자동 악기 감지(보조): 데이터 부족")

    def _emit_cfg(self, *_):
        if self._loading or self._cfg is None:
            return
        c = self._cfg
        c.mixer_ch = self.sp_mixer.value() or None
        c.name = self.ed_name.text()
        new_inst = self.cb_inst.currentData()
        if new_inst != c.instrument and self.sender() is self.cb_inst:
            # 악기를 바꾸면 확정 상태를 해제 (사용자가 다시 확인)
            self._loading = True
            self.chk_confirm.setChecked(False)
            self._loading = False
        c.instrument = new_inst
        c.source_type = self.cb_src.currentData()
        c.instrument_confirmed = self.chk_confirm.isChecked()
        c.auto_allowed = self.chk_auto.isChecked()
        self.config_changed.emit(c)

    def checked_items(self) -> list[int]:
        out = []
        for r, idx in enumerate(self._row_item):
            it = self.cmp.item(r, 0)
            if it and it.checkState() == Qt.Checked and idx not in out:
                out.append(idx)
        return out

    def show_recommendation(self, rec, eq_state, can_apply: bool, status_text: str) -> None:
        self.lbl_status.setText(status_text)
        if eq_state is not None and eq_state.values:
            v = eq_state.values
            parts = [f"EQ {'ON' if eq_state.eq_on else 'OFF'} ({eq_state.eq_model})"]
            lc = v.get("flt/lc")
            if lc is not None:
                parts.append(f"HPF {'ON' if int(lc.value) else 'OFF'} {fmt(v.get('flt/lcf').value if v.get('flt/lcf') else None)}Hz")
            for b in (1, 2, 3, 4):
                g, f, q = v.get(f"eq/{b}g"), v.get(f"eq/{b}f"), v.get(f"eq/{b}q")
                if g and f and q:
                    parts.append(f"B{b} {fmt(f.value)}Hz {float(g.value):+.1f}dB Q{fmt(q.value)}")
            missing = f" | 응답 없음: {eq_state.missing}" if eq_state.missing else ""
            self.lbl_eq.setText("현재 믹서 값: " + " · ".join(parts) + missing)
        else:
            self.lbl_eq.setText("현재 믹서 값: 읽지 못함 (LAN 미연결 또는 매핑 없음)")
        self.cmp.setRowCount(0)
        self._row_item = []
        notes = []
        if rec is not None:
            for i, it in enumerate(rec.items):
                for c in it.changes:
                    if not c.changed:
                        continue
                    r = self.cmp.rowCount()
                    self.cmp.insertRow(r)
                    self._row_item.append(i)
                    chk = QTableWidgetItem()
                    chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                    chk.setCheckState(Qt.Checked if it.evidence_sufficient else Qt.Unchecked)
                    cells = [chk, it.target, c.key.split("/")[-1], fmt(c.current), fmt(c.recommended), it.reason,
                             f"{it.confidence:.0%}", "충분" if it.evidence_sufficient else "부족",
                             "필요" if it.listen_check else ""]
                    for col, val in enumerate(cells):
                        item = val if isinstance(val, QTableWidgetItem) else QTableWidgetItem(str(val))
                        if col == 7 and not it.evidence_sufficient:
                            item.setForeground(QColor(COLORS["warn"]))
                        if col == 4:
                            item.setForeground(QColor(COLORS["accent"]))
                        self.cmp.setItem(r, col, item)
            notes += [f"⚠ {w}" for w in rec.warnings]
            notes += [f"청음 확인: {n}" for n in rec.listen_notes]
            notes.append("EQ 커브는 일반 필터 공식 근사이며 WING 내부 커브와 다를 수 있음. "
                         "'적용 후 예상값'도 근사 — 실제 적용 결과는 믹서 재조회 값으로만 판단.")
        self.notes.setPlainText("\n".join(notes))
        has = rec is not None and bool(rec.items)
        self.btn_apply.setEnabled(can_apply and has)
        self.btn_reject.setEnabled(has)
