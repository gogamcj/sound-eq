"""실시간 스펙트럼 + EQ 커브 표시 (QPainter, 로그 주파수 축)."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .theme import COLORS

F_MIN, F_MAX = 20.0, 20000.0
DB_MIN, DB_MAX = -100.0, 0.0
EQ_RANGE = 18.0


class SpectrumView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(240)
        self.freqs = None
        self.live = None
        self.longterm = None
        self.current_eq = None
        self.recommended_eq = None
        self.markers: list[tuple[float, str, str]] = []   # (freq, label, color)
        self.title = ""

    def set_data(self, freqs, live, longterm, current_eq=None, recommended_eq=None, markers=None, title=""):
        self.freqs, self.live, self.longterm = freqs, live, longterm
        self.current_eq, self.recommended_eq = current_eq, recommended_eq
        self.markers = markers or []
        self.title = title
        self.update()

    def _x(self, f, r: QRectF) -> float:
        return r.left() + (math.log10(max(f, F_MIN)) - math.log10(F_MIN)) / (math.log10(F_MAX) - math.log10(F_MIN)) * r.width()

    def _y(self, db, r: QRectF) -> float:
        return r.bottom() - (np.clip(db, DB_MIN, DB_MAX) - DB_MIN) / (DB_MAX - DB_MIN) * r.height()

    def _yeq(self, db, r: QRectF) -> float:
        return r.center().y() - np.clip(db, -EQ_RANGE, EQ_RANGE) / EQ_RANGE * r.height() / 2

    def _path(self, xs, ys) -> QPainterPath:
        p = QPainterPath()
        for i, (x, y) in enumerate(zip(xs, ys)):
            if i == 0:
                p.moveTo(x, y)
            else:
                p.lineTo(x, y)
        return p

    def paintEvent(self, _):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.fillRect(self.rect(), QColor(COLORS["panel"]))
        r = QRectF(self.rect()).adjusted(44, 22, -40, -22)
        qp.setFont(QFont("Segoe UI", 8))
        grid = QPen(QColor(COLORS["border"]), 1)
        qp.setPen(grid)
        for f in (31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000):
            x = self._x(f, r)
            qp.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            qp.setPen(QColor(COLORS["muted"]))
            qp.drawText(QPointF(x - 12, r.bottom() + 14), f"{f / 1000:g}k" if f >= 1000 else f"{f:g}")
            qp.setPen(grid)
        for d in range(int(DB_MIN), int(DB_MAX) + 1, 20):
            y = self._y(d, r)
            qp.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            qp.setPen(QColor(COLORS["muted"]))
            qp.drawText(QPointF(4, y + 4), f"{d}")
            qp.setPen(grid)
        for d in (-12, -6, 6, 12):
            qp.setPen(QColor(COLORS["muted"]))
            qp.drawText(QPointF(r.right() + 6, self._yeq(d, r) + 4), f"{d:+d}")
        qp.setPen(QPen(QColor(COLORS["muted"]), 1, Qt.DashLine))
        qp.drawLine(QPointF(r.left(), r.center().y()), QPointF(r.right(), r.center().y()))
        qp.drawText(QPointF(r.right() - 160, r.top() - 6), "좌: 입력 dBFS / 우: EQ dB")
        qp.setPen(QColor(COLORS["text"]))
        qp.drawText(QPointF(r.left(), r.top() - 6), self.title)

        if self.freqs is not None and self.live is not None:
            xs = [self._x(f, r) for f in self.freqs]
            if self.longterm is not None:
                qp.setPen(QPen(QColor("#5c7cfa"), 1.2))
                qp.drawPath(self._path(xs, [self._y(v, r) for v in self.longterm]))
            qp.setPen(QPen(QColor("#9ad0ff"), 1.0))
            qp.drawPath(self._path(xs, [self._y(v, r) for v in self.live]))
            if self.current_eq is not None:
                qp.setPen(QPen(QColor(COLORS["ok"]), 2.0))
                qp.drawPath(self._path(xs, [self._yeq(v, r) for v in self.current_eq]))
            if self.recommended_eq is not None:
                qp.setPen(QPen(QColor(COLORS["accent"]), 2.0, Qt.DashLine))
                qp.drawPath(self._path(xs, [self._yeq(v, r) for v in self.recommended_eq]))
        for f, label, color in self.markers:
            x = self._x(f, r)
            qp.setPen(QPen(QColor(color), 1.5, Qt.DotLine))
            qp.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            qp.drawText(QPointF(x + 3, r.top() + 12), label)
        # 범례
        x = r.left() + 8
        y = r.bottom() - 8
        for color, name, dash in (("#9ad0ff", "실시간", False), ("#5c7cfa", "장기 평균", False),
                                  (COLORS["ok"], "현재 EQ(근사)", False), (COLORS["accent"], "추천 EQ(근사)", True)):
            qp.setPen(QPen(QColor(color), 2, Qt.DashLine if dash else Qt.SolidLine))
            qp.drawLine(QPointF(x, y - 4), QPointF(x + 18, y - 4))
            qp.setPen(QColor(COLORS["text"]))
            qp.drawText(QPointF(x + 22, y), name)
            x += 30 + qp.fontMetrics().horizontalAdvance(name)
        qp.end()
