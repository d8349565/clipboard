from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QKeySequenceEdit,
    QVBoxLayout,
    QWidget,
)

from .settings import AppSettings


ApplyHotkeys = Callable[[str, str], tuple[bool, str | None]]


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, apply_hotkeys: ApplyHotkeys, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._apply_hotkeys = apply_hotkeys

        self.setWindowTitle("设置")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self._hotkey_show = QKeySequenceEdit(self)
        self._hotkey_pause = QKeySequenceEdit(self)

        self._hotkey_show.setKeySequence(QKeySequence(settings.hotkey_show_panel))
        self._hotkey_pause.setKeySequence(QKeySequence(settings.hotkey_toggle_pause))

        form = QFormLayout()
        form.addRow("打开面板：", self._hotkey_show)
        form.addRow("暂停监听：", self._hotkey_pause)

        hint = QLabel("仅支持 Ctrl/Alt/Shift/Win + A-Z/0-9/F1-F24/方向键等常见组合键。", self)
        hint.setWordWrap(True)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        btn_box.accepted.connect(self._on_ok)
        btn_box.rejected.connect(self.reject)

        btn_reset = QPushButton("恢复默认", self)
        btn_reset.clicked.connect(self._reset_defaults)
        extra = QHBoxLayout()
        extra.addWidget(btn_reset)
        extra.addStretch(1)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(hint)
        root.addLayout(extra)
        root.addWidget(btn_box)
        self.setLayout(root)

        self.resize(420, 180)

    def _reset_defaults(self) -> None:
        self._hotkey_show.setKeySequence(QKeySequence("Ctrl+Shift+V"))
        self._hotkey_pause.setKeySequence(QKeySequence("Ctrl+Shift+P"))

    def _on_ok(self) -> None:
        show_seq = self._hotkey_show.keySequence().toString(QKeySequence.PortableText).strip()
        pause_seq = self._hotkey_pause.keySequence().toString(QKeySequence.PortableText).strip()
        if show_seq == "None":
            show_seq = ""
        if pause_seq == "None":
            pause_seq = ""

        ok, msg = self._apply_hotkeys(show_seq, pause_seq)
        if not ok:
            QMessageBox.warning(self, "设置", msg or "保存失败")
            return
        if msg:
            QMessageBox.information(self, "设置", msg)
        self.accept()
