"""Dark blue/cyan palette shared with the user's FOCTwin and Xray_labs apps."""

ACCENT_FRAME = "#3e80a3"
ACCENT_GLOW = "#00bfff"
ACCENT_TEAL = "#40e0d0"
DEPTH_BLUE = "#215175"
BG_DARK = "#0f141b"
BG_SIDEBAR = "#0a111c"
BG_PANEL = "#0a1a2e"
BG_TRACK = "#12233a"
BG_SELECTED = "#1e3a5f"
TEXT_PRIMARY = "#e8f4ff"
TEXT_SECONDARY = "#a8d4f0"
TEXT_MUTED = "#5c7a9a"
ERROR = "#ff6b6b"
WARNING = "#f2c14e"
SUCCESS = "#40e0d0"


def stylesheet() -> str:
    return f"""
    QMainWindow, QDialog, QWidget {{
        background: {BG_DARK}; color: {TEXT_PRIMARY};
        font-family: "Segoe UI"; font-size: 10pt;
    }}
    QFrame#header {{ background: {BG_SIDEBAR}; border-bottom: 1px solid {DEPTH_BLUE}; }}
    QFrame#panel, QGroupBox {{
        background: {BG_PANEL}; border: 1px solid {DEPTH_BLUE}; border-radius: 5px;
    }}
    QGroupBox {{ margin-top: 10px; padding: 12px 8px 8px 8px; font-weight: 600; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
    QLabel#title {{ font-size: 18pt; font-weight: 700; color: {TEXT_PRIMARY}; }}
    QLabel#subtitle {{ color: {TEXT_SECONDARY}; }}
    QLabel#muted {{ color: {TEXT_MUTED}; }}
    QLabel#success {{ color: {SUCCESS}; font-weight: 600; }}
    QLabel#error {{ color: {ERROR}; font-weight: 600; }}
    QPushButton {{
        background: {DEPTH_BLUE}; border: 1px solid {ACCENT_FRAME}; border-radius: 4px;
        padding: 6px 12px; font-weight: 600;
    }}
    QPushButton:hover {{ background: {ACCENT_FRAME}; }}
    QPushButton:pressed {{ background: {ACCENT_GLOW}; color: {BG_DARK}; }}
    QPushButton:disabled {{
        background: {BG_TRACK}; color: {TEXT_MUTED}; border-color: {DEPTH_BLUE};
    }}
    QPushButton#primary {{ background: {ACCENT_FRAME}; border-color: {ACCENT_GLOW}; }}
    QPushButton#danger {{ background: #5a2028; border-color: {ERROR}; }}
    QPushButton#danger:hover {{ background: {ERROR}; color: {BG_DARK}; }}
    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox, QTableWidget {{
        background: {BG_TRACK}; color: {TEXT_PRIMARY}; border: 1px solid {ACCENT_FRAME};
        border-radius: 3px; padding: 4px;
    }}
    QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {ACCENT_GLOW}; }}
    QComboBox QAbstractItemView {{
        background: {BG_TRACK}; color: {TEXT_PRIMARY}; selection-background-color: {BG_SELECTED};
    }}
    QTableWidget {{
        alternate-background-color: {BG_PANEL}; gridline-color: {DEPTH_BLUE};
        selection-background-color: {BG_SELECTED}; selection-color: {TEXT_PRIMARY};
    }}
    QTableWidget::item {{ padding: 5px; }}
    QHeaderView::section {{
        background: {BG_SELECTED}; color: {TEXT_PRIMARY}; padding: 6px;
        border: 0; border-right: 1px solid {DEPTH_BLUE};
    }}
    QCheckBox {{ spacing: 6px; }}
    QProgressBar {{
        background: {BG_TRACK}; color: {TEXT_PRIMARY}; border: 1px solid {DEPTH_BLUE};
        border-radius: 3px; text-align: center;
    }}
    QProgressBar::chunk {{ background: {ACCENT_GLOW}; }}
    QScrollBar:vertical {{ background: {BG_PANEL}; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{
        background: {ACCENT_FRAME}; min-height: 20px; border-radius: 4px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {ACCENT_GLOW}; }}
    QStatusBar {{ background: {BG_SIDEBAR}; color: {TEXT_SECONDARY}; }}
    QToolTip {{ background: {BG_PANEL}; color: {TEXT_PRIMARY}; border: 1px solid {ACCENT_GLOW}; }}
    """
