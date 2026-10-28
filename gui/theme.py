"""어두운 콘솔 스타일 테마."""

COLORS = {
    "bg": "#15171a",
    "panel": "#1d2025",
    "panel2": "#252930",
    "border": "#353a43",
    "text": "#e6e8eb",
    "muted": "#9aa1ab",
    "ok": "#3ecf6e",
    "warn": "#f2b33d",
    "err": "#ff5252",
    "info": "#4fb3ff",
    "accent": "#ff9f43",
    "sim": "#b388ff",
    "meter_lo": "#2fbf62",
    "meter_mid": "#e5c33b",
    "meter_hi": "#ff4a4a",
}

STYLE = f"""
QWidget {{ background: {COLORS['bg']}; color: {COLORS['text']}; font-family: 'Segoe UI', 'Malgun Gothic', sans-serif;
          font-size: 10pt; }}
QFrame#panel, QGroupBox {{ background: {COLORS['panel']}; border: 1px solid {COLORS['border']}; border-radius: 6px; }}
QGroupBox {{ margin-top: 14px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {COLORS['muted']}; }}
QTableWidget {{ background: {COLORS['panel']}; gridline-color: {COLORS['border']}; selection-background-color: #2e4a6b;
               border: 1px solid {COLORS['border']}; }}
QHeaderView::section {{ background: {COLORS['panel2']}; color: {COLORS['muted']}; border: none; padding: 4px;
                       border-right: 1px solid {COLORS['border']}; }}
QPushButton {{ background: {COLORS['panel2']}; border: 1px solid {COLORS['border']}; border-radius: 4px; padding: 6px 12px; }}
QPushButton:hover {{ border-color: {COLORS['info']}; }}
QPushButton:disabled {{ color: #5d636c; border-color: #2a2e35; }}
QPushButton#stopAll {{ background: #8b1010; border: 2px solid {COLORS['err']}; color: white; font-weight: bold;
                      font-size: 12pt; padding: 8px 18px; }}
QPushButton#stopAll:hover {{ background: #b51717; }}
QPushButton#apply {{ background: #1f5130; border-color: {COLORS['ok']}; font-weight: bold; }}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{ background: {COLORS['panel2']}; border: 1px solid {COLORS['border']};
          border-radius: 4px; padding: 3px 6px; }}
QListWidget {{ background: {COLORS['panel']}; border: 1px solid {COLORS['border']}; }}
QLabel#chip {{ border-radius: 4px; padding: 4px 8px; font-weight: bold; }}
QLabel#banner {{ background: #3b2a5c; color: white; font-weight: bold; padding: 6px; border: 1px solid {COLORS['sim']}; }}
QTabWidget::pane {{ border: 1px solid {COLORS['border']}; }}
QTabBar::tab {{ background: {COLORS['panel2']}; padding: 6px 12px; }}
QTabBar::tab:selected {{ background: {COLORS['panel']}; color: {COLORS['info']}; }}
"""


def chip_style(color: str) -> str:
    return f"background: {color}22; color: {color}; border: 1px solid {color};"
