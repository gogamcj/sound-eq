"""채널 목록 (레벨 미터 포함)."""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QStyledItemDelegate, QTableWidget, QTableWidgetItem

from .theme import COLORS

COLS = ["USB", "믹서 CH", "이름", "악기", "레벨", "피크", "EQ", "분석", "추천/상태", "자동"]
METER_COL = 4
LEVEL_ROLE = Qt.UserRole + 1


class MeterDelegate(QStyledItemDelegate):
    """RMS(막대) + 피크 홀드(선). 값은 dBFS (-60..0)."""

    def paint(self, p: QPainter, opt, idx):
        data = idx.data(LEVEL_ROLE)
        r: QRect = opt.rect.adjusted(4, 6, -4, -6)
        p.fillRect(r, QColor("#0d0f12"))
        if not data:
            return
        rms, peak, clip = data

        def frac(db):
            return max(0.0, min(1.0, (db + 60.0) / 60.0))

        w = int(r.width() * frac(rms))
        color = COLORS["meter_lo"] if rms < -18 else (COLORS["meter_mid"] if rms < -6 else COLORS["meter_hi"])
        p.fillRect(QRect(r.left(), r.top(), w, r.height()), QColor(color))
        x = r.left() + int(r.width() * frac(peak))
        p.fillRect(QRect(x - 1, r.top(), 2, r.height()), QColor("#ffffff"))
        if clip:
            p.fillRect(QRect(r.right() - 8, r.top(), 8, r.height()), QColor(COLORS["err"]))


class ChannelTable(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(0, len(COLS), parent)
        self.setHorizontalHeaderLabels(COLS)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.setItemDelegateForColumn(METER_COL, MeterDelegate(self))
        h = self.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setStretchLastSection(False)
        for i, w in enumerate((44, 64, 120, 110, 150, 60, 80, 90, 190, 46)):
            self.setColumnWidth(i, w)

    def ensure_rows(self, n: int) -> None:
        if self.rowCount() == n:
            return
        self.setRowCount(n)
        for r in range(n):
            for c in range(len(COLS)):
                if self.item(r, c) is None:
                    it = QTableWidgetItem("")
                    it.setTextAlignment(Qt.AlignVCenter | (Qt.AlignCenter if c in (0, 1, 5, 9) else Qt.AlignLeft))
                    self.setItem(r, c, it)

    def set_row(self, r: int, values: dict) -> None:
        for c, key in enumerate(COLS):
            if key not in values:
                continue
            it = self.item(r, c)
            v = values[key]
            if c == METER_COL:
                it.setData(LEVEL_ROLE, v)
            elif isinstance(v, tuple):
                it.setText(v[0])
                it.setForeground(QColor(v[1]))
            else:
                it.setText(str(v))
