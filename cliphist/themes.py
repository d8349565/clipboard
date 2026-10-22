from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication


THEME_NAMES = {"system": "跟随系统", "light": "灰白", "blue": "雾蓝", "dark": "深色"}

_PALETTES = {
    "light": {
        "surface": "#FFFFFF", "surface_alt": "#F7F7F8", "text": "#202124",
        "muted": "#62666D", "border": "#DDE0E5", "hover": "#F1F2F4",
        "selection": "#E9EBEF", "accent": "#30343B", "accent_text": "#FFFFFF",
        "accent_hover": "#454B54", "accent_pressed": "#1D2025",
        "danger": "#B42332", "danger_bg": "#FFF0F1", "disabled": "#8B9099",
    },
    "blue": {
        "surface": "#FBFCFE", "surface_alt": "#F1F4F8", "text": "#243347",
        "muted": "#58687D", "border": "#D8E1EC", "hover": "#EFF3F9",
        "selection": "#E5EDF8", "accent": "#365F94", "accent_text": "#FFFFFF",
        "accent_hover": "#284E80", "accent_pressed": "#203F69",
        "danger": "#B42332", "danger_bg": "#FFF0F1", "disabled": "#8693A4",
    },
    "dark": {
        "surface": "#24272C", "surface_alt": "#2B2F35", "text": "#F0F2F5",
        "muted": "#B2BAC6", "border": "#454C57", "hover": "#30353D",
        "selection": "#363F4D", "accent": "#B4C7EA", "accent_text": "#1E293C",
        "accent_hover": "#C5D4EF", "accent_pressed": "#9FB8E2",
        "danger": "#FFAFB7", "danger_bg": "#493038", "disabled": "#8993A1",
    },
}


def normalize_theme(mode: object) -> str:
    return mode if isinstance(mode, str) and mode in THEME_NAMES else "system"


def _system_dark() -> bool:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        scheme = app.styleHints().colorScheme()
        if scheme != Qt.ColorScheme.Unknown:
            return scheme == Qt.ColorScheme.Dark
    if sys.platform == "win32":
        # Older Qt/Windows combinations may not report a color scheme.
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        except OSError:
            pass
    return False


def theme_colors(mode: str = "system") -> dict[str, str]:
    mode = normalize_theme(mode)
    if mode == "system":
        mode = "dark" if _system_dark() else "light"
    return dict(_PALETTES[mode])


def apply_application_theme(app: QApplication, mode: str) -> None:
    from PySide6.QtGui import QColor, QPalette

    colors = theme_colors(mode)
    palette = QPalette()
    roles = {
        QPalette.Window: "surface", QPalette.WindowText: "text",
        QPalette.Base: "surface", QPalette.AlternateBase: "surface_alt",
        QPalette.Text: "text", QPalette.Button: "surface_alt",
        QPalette.ButtonText: "text", QPalette.ToolTipBase: "surface_alt",
        QPalette.ToolTipText: "text", QPalette.Highlight: "accent",
        QPalette.HighlightedText: "accent_text", QPalette.Link: "accent",
        QPalette.PlaceholderText: "muted", QPalette.Mid: "border",
        QPalette.Light: "surface_alt", QPalette.Dark: "border",
    }
    for role, token in roles.items():
        palette.setColor(role, QColor(colors[token]))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(colors["disabled"]))
    app.setPalette(palette)
    app.setProperty("cliphistTheme", normalize_theme(mode))
    # Native Windows menu painting can otherwise ignore a custom dark palette.
    app.setStyleSheet(f"""
        QMenu {{ background: {colors['surface']}; color: {colors['text']};
            border: 1px solid {colors['border']}; padding: 6px; }}
        QMenu::item {{ padding: 8px 24px; border-radius: 4px; }}
        QMenu::item:selected {{ background: {colors['selection']}; color: {colors['text']}; }}
        QMenu::item:disabled {{ color: {colors['disabled']}; }}
        QMenu::separator {{ height: 1px; background: {colors['border']}; margin: 5px; }}
        QToolTip {{ background: {colors['surface_alt']}; color: {colors['text']};
            border: 1px solid {colors['border']}; padding: 5px 8px; }}
    """)


def dialog_styles(mode: str = "system") -> str:
    c = theme_colors(mode)
    return f"""
        QDialog {{ color: {c['text']}; font-family: "Microsoft YaHei";
            font-size: 14px; }}
        #settingsCard, #helpCard {{ background: {c['surface']};
            border: 1px solid {c['border']}; border-radius: 14px; }}
        QLabel, QCheckBox {{ color: {c['text']}; background: transparent; }}
        #settingsTitle, #helpTitle {{ font-size: 18px; font-weight: 600; }}
        #settingsSection {{ font-weight: 600; padding-top: 4px; }}
        #settingsHint, #helpFooter, #helpVer {{ color: {c['muted']}; font-size: 12px; }}
        QScrollArea, #settingsContent, #helpContent {{ background: {c['surface']}; border: 0; }}
        QLineEdit, QSpinBox, QComboBox, QKeySequenceEdit, QPlainTextEdit {{
            color: {c['text']}; background: {c['surface_alt']};
            border: 1px solid {c['border']}; border-radius: 6px;
            padding: 7px 10px; selection-background-color: {c['accent']};
            selection-color: {c['accent_text']}; }}
        QKeySequenceEdit QLineEdit {{ border: 0; padding: 0; background: transparent; }}
        QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QKeySequenceEdit:focus,
        QPlainTextEdit:focus {{ border-color: {c['accent']}; }}
        QComboBox QAbstractItemView {{ color: {c['text']}; background: {c['surface']};
            selection-background-color: {c['selection']}; selection-color: {c['text']};
            border: 1px solid {c['border']}; padding: 4px; }}
        QCheckBox {{ spacing: 8px; padding: 4px 0; }}
        QPushButton, QToolButton {{ color: {c['text']}; background: {c['surface_alt']};
            border: 1px solid {c['border']}; border-radius: 6px; padding: 7px 12px; }}
        QPushButton:hover, QToolButton:hover {{ background: {c['hover']}; }}
        QPushButton:focus, QToolButton:focus {{ border-color: {c['accent']}; }}
        QPushButton:pressed, QToolButton:pressed {{ background: {c['selection']}; }}
        QPushButton:disabled, QToolButton:disabled {{ color: {c['disabled']}; }}
        #settingsBtnPrimary {{ color: {c['accent_text']}; background: {c['accent']};
            border-color: {c['accent']}; font-weight: 600; }}
        #settingsBtnPrimary:hover {{ background: {c['accent_hover']}; }}
        #settingsBtnPrimary:pressed {{ background: {c['accent_pressed']}; }}
        #btnWinControl {{ background: transparent; border: 1px solid transparent; padding: 0; }}
        #btnWinControl:hover {{ background: {c['hover']}; }}
        #helpSection {{ background: {c['surface_alt']}; border: 0; border-radius: 8px; }}
        #helpIcon {{ color: {c['accent_text']}; background: {c['accent']}; border-radius: 8px; }}
        #helpSectionTitle, #helpTerm {{ color: {c['text']}; font-weight: 600; }}
        #helpDesc {{ color: {c['text']}; }}
        #helpEmoji {{ color: {c['accent']}; }}
        #helpSep {{ background: {c['border']}; max-height: 1px; border: 0; }}
        QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 3px; min-height: 24px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """
