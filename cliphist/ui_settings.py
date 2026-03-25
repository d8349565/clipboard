from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import (
  QCheckBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QKeySequenceEdit,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .settings import AppSettings, MAX_ITEMS_LIMIT


ApplySettings = Callable[[str, str, int, bool, int, int], tuple[bool, str | None]]


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, apply_settings: ApplySettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._apply_settings = apply_settings
        self._drag_pos: QPoint | None = None

        self.setWindowTitle("设置")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)

        card = QFrame(self)
        card.setObjectName("settingsCard")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 90))
        card.setGraphicsEffect(shadow)

        # -- title bar --
        title_bar = QHBoxLayout()
        title_label = QLabel("设置", card)
        title_label.setObjectName("settingsTitle")
        title_bar.addWidget(title_label)
        title_bar.addStretch(1)

        btn_close = QToolButton(card)
        btn_close.setObjectName("btnWinControl")
        btn_close.setText("✕")
        btn_close.setToolTip("关闭")
        btn_close.setFixedSize(28, 28)
        btn_close.clicked.connect(self.reject)
        title_bar.addWidget(btn_close)

        # -- form --
        self._hotkey_show = QKeySequenceEdit(card)
        self._hotkey_pause = QKeySequenceEdit(card)
        self._max_items = QSpinBox(card)
        self._autostart = QCheckBox("开机自动启动 ClipHist", card)
        self._panel_width = QSpinBox(card)
        self._panel_height = QSpinBox(card)
        self._hotkey_show.setKeySequence(QKeySequence(settings.hotkey_show_panel))
        self._hotkey_pause.setKeySequence(QKeySequence(settings.hotkey_toggle_pause))
        self._max_items.setRange(50, MAX_ITEMS_LIMIT)
        self._max_items.setSingleStep(50)
        self._max_items.setValue(settings.max_items)
        self._autostart.setChecked(settings.autostart_enabled)
        self._panel_width.setRange(400, 1600)
        self._panel_width.setSingleStep(20)
        self._panel_width.setValue(settings.panel_width)
        self._panel_width.setSuffix(" px")
        self._panel_height.setRange(350, 1200)
        self._panel_height.setSingleStep(20)
        self._panel_height.setValue(settings.panel_height)
        self._panel_height.setSuffix(" px")

        form = QFormLayout()
        form.addRow("打开面板：", self._hotkey_show)
        form.addRow("暂停监听：", self._hotkey_pause)
        form.addRow("历史条数：", self._max_items)
        form.addRow("开机自启：", self._autostart)
        form.addRow("窗口宽度：", self._panel_width)
        form.addRow("窗口高度：", self._panel_height)

        hint = QLabel("仅支持 Ctrl/Alt/Shift/Win + A-Z/0-9/F1-F24/方向键等常见组合键。历史条数决定当前会加载多少条历史记录；开机自启会在 Windows 启动目录创建或移除快捷方式；窗口宽高设置重启后生效。", card)
        hint.setWordWrap(True)
        hint.setObjectName("settingsHint")

        # -- buttons --
        btn_reset = QPushButton("恢复默认", card)
        btn_reset.setObjectName("settingsBtn")
        btn_reset.clicked.connect(self._reset_defaults)

        btn_ok = QPushButton("确定", card)
        btn_ok.setObjectName("settingsBtnPrimary")
        btn_ok.clicked.connect(self._on_ok)

        btn_cancel = QPushButton("取消", card)
        btn_cancel.setObjectName("settingsBtn")
        btn_cancel.clicked.connect(self.reject)

        btn_row = QHBoxLayout()
        btn_row.addWidget(btn_reset)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)

        body = QVBoxLayout(card)
        body.setContentsMargins(18, 14, 18, 18)
        body.setSpacing(12)
        body.addLayout(title_bar)
        body.addLayout(form)
        body.addWidget(hint)
        body.addLayout(btn_row)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.addWidget(card)
        self.setLayout(root)

        self.resize(460, 360)
        self._apply_styles()

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            #settingsCard {
              background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                          stop:0 #F8FAFC, stop:1 #EEF2FF);
              border: 1px solid rgba(148, 163, 184, 0.45);
              border-radius: 14px;
            }
            QLabel#settingsTitle {
              font-size: 15px;
              font-weight: 700;
              color: #0F172A;
            }
            QLabel#settingsHint {
              color: #64748B;
              font-size: 12px;
            }
            QToolButton#btnWinControl {
              font-size: 14px;
              font-weight: 700;
              padding: 0px;
              border-radius: 14px;
              border: none;
              background: transparent;
              color: #64748B;
            }
            QToolButton#btnWinControl:hover {
              background: rgba(148, 163, 184, 0.28);
              color: #0F172A;
            }
            QKeySequenceEdit {
              padding: 6px 10px;
              border-radius: 8px;
              border: 1px solid rgba(148, 163, 184, 0.6);
              background: #FFFFFF;
            }
            QKeySequenceEdit:focus, QSpinBox:focus {
              border: 1px solid #3B82F6;
              background: #F8FAFC;
            }
            QSpinBox {
              padding: 6px 10px;
              border-radius: 8px;
              border: 1px solid rgba(148, 163, 184, 0.6);
              background: #FFFFFF;
            }
            QLabel {
              color: #0F172A;
            }
            QCheckBox {
              color: #0F172A;
              spacing: 8px;
            }
            QPushButton#settingsBtn {
              padding: 6px 16px;
              border-radius: 9px;
              border: 1px solid rgba(148, 163, 184, 0.4);
              background: #FFFFFF;
              color: #0F172A;
            }
            QPushButton#settingsBtn:hover {
              background: #F1F5F9;
            }
            QPushButton#settingsBtnPrimary {
              padding: 6px 16px;
              border-radius: 9px;
              border: 1px solid rgba(37, 99, 235, 0.5);
              background: #2563EB;
              color: #FFFFFF;
              font-weight: 600;
            }
            QPushButton#settingsBtnPrimary:hover {
              background: #1D4ED8;
            }
            """
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Center on parent if available
        if self.parent() is not None:
            p = self.parent()
            cx = p.x() + (p.width() - self.width()) // 2
            cy = p.y() + (p.height() - self.height()) // 2
            self.move(cx, cy)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if event.position().y() <= 48:
                self._drag_pos = QPoint(int(event.globalPosition().x()), int(event.globalPosition().y()))
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            current = QPoint(int(event.globalPosition().x()), int(event.globalPosition().y()))
            delta = current - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = current
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = None
        super().mouseReleaseEvent(event)

    def _reset_defaults(self) -> None:
        self._hotkey_show.setKeySequence(QKeySequence("Alt+C"))
        self._hotkey_pause.setKeySequence(QKeySequence("Alt+P"))
        self._max_items.setValue(5000)
        self._autostart.setChecked(False)
        self._panel_width.setValue(640)
        self._panel_height.setValue(620)

    def _on_ok(self) -> None:
        show_seq = self._hotkey_show.keySequence().toString(QKeySequence.PortableText).strip()
        pause_seq = self._hotkey_pause.keySequence().toString(QKeySequence.PortableText).strip()
        if show_seq == "None":
            show_seq = ""
        if pause_seq == "None":
            pause_seq = ""

        ok, msg = self._apply_settings(
            show_seq, pause_seq, int(self._max_items.value()), self._autostart.isChecked(),
            int(self._panel_width.value()), int(self._panel_height.value()),
        )
        if not ok:
            QMessageBox.warning(self, "设置", msg or "保存失败")
            return
        # Successful apply should be silent even when optional hotkeys degrade.
        self.accept()
