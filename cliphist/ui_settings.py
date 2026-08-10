from __future__ import annotations

import os
from typing import Callable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QKeySequenceEdit,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .settings import AppSettings, MAX_ITEMS_LIMIT, default_db_path


ApplySettings = Callable[[str, str, int, bool, bool, bool, int, int, str], tuple[bool, str | None]]
BackupDatabase = Callable[[str], tuple[bool, str | None]]
DatabaseSummary = Callable[[str], str]


class SettingsDialog(QDialog):
    def __init__(
        self,
        settings: AppSettings,
        apply_settings: ApplySettings,
        backup_database: BackupDatabase | None = None,
        database_summary: DatabaseSummary | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._apply_settings = apply_settings
        self._backup_database = backup_database
        self._database_summary = database_summary
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
        btn_close.setText("x")
        btn_close.setToolTip("关闭")
        btn_close.setFixedSize(28, 28)
        btn_close.clicked.connect(self.reject)
        title_bar.addWidget(btn_close)

        # -- form --
        self._hotkey_show = QKeySequenceEdit(card)
        self._hotkey_pause = QKeySequenceEdit(card)
        self._max_items = QSpinBox(card)
        self._persist = QCheckBox("启用 SQLite 持久化历史", card)
        self._auto_paste = QCheckBox("选择记录后直接粘贴到原窗口", card)
        self._autostart = QCheckBox("开机时随 Windows 启动 ClipHist", card)
        self._panel_width = QSpinBox(card)
        self._panel_height = QSpinBox(card)
        self._hotkey_show.setKeySequence(QKeySequence(settings.hotkey_show_panel))
        self._hotkey_pause.setKeySequence(QKeySequence(settings.hotkey_toggle_pause))
        self._max_items.setRange(50, MAX_ITEMS_LIMIT)
        self._max_items.setSingleStep(50)
        self._max_items.setValue(settings.max_items)
        self._persist.setChecked(settings.persist_enabled)
        self._auto_paste.setChecked(settings.auto_paste)
        self._autostart.setChecked(settings.autostart_enabled)
        self._panel_width.setRange(400, 1600)
        self._panel_width.setSingleStep(20)
        self._panel_width.setValue(settings.panel_width)
        self._panel_width.setSuffix(" px")
        self._panel_height.setRange(350, 1200)
        self._panel_height.setSingleStep(20)
        self._panel_height.setValue(settings.panel_height)
        self._panel_height.setSuffix(" px")

        # -- database path --
        self._db_path = QLineEdit(card)
        self._db_path.setPlaceholderText(default_db_path())
        self._db_path.setText(settings.db_path or "")
        self._db_path.setToolTip("留空则使用默认位置：" + default_db_path())
        btn_browse = QToolButton(card)
        btn_browse.setObjectName("settingsBtn")
        btn_browse.setText("...")
        btn_browse.setToolTip("选择数据库文件位置")
        btn_browse.setFixedHeight(30)
        btn_browse.clicked.connect(self._browse_db_path)
        btn_open_dir = QToolButton(card)
        btn_open_dir.setObjectName("settingsBtn")
        btn_open_dir.setText("打开目录")
        btn_open_dir.setToolTip("打开数据库和日志所在目录")
        btn_open_dir.setFixedHeight(30)
        btn_open_dir.clicked.connect(self._open_data_folder)
        btn_backup = QToolButton(card)
        btn_backup.setObjectName("settingsBtn")
        btn_backup.setText("备份")
        btn_backup.setToolTip("创建一致的 SQLite 数据库备份")
        btn_backup.setFixedHeight(30)
        btn_backup.clicked.connect(self._backup_db)
        db_row = QHBoxLayout()
        db_row.setContentsMargins(0, 0, 0, 0)
        db_row.setSpacing(6)
        db_row.addWidget(self._db_path, 1)
        db_row.addWidget(btn_browse)
        db_row.addWidget(btn_open_dir)
        db_row.addWidget(btn_backup)
        db_row_widget = QWidget(card)
        db_row_widget.setLayout(db_row)
        self._db_summary = QLabel("", card)
        self._db_summary.setObjectName("settingsHint")
        self._db_path.textChanged.connect(self._refresh_db_summary)

        form = QFormLayout()
        form.addRow("打开面板热键：", self._hotkey_show)
        form.addRow("暂停热键：", self._hotkey_pause)
        form.addRow("历史条数：", self._max_items)
        form.addRow("持久化：", self._persist)
        form.addRow("直接粘贴：", self._auto_paste)
        form.addRow("开机自启：", self._autostart)
        form.addRow("面板宽度：", self._panel_width)
        form.addRow("面板高度：", self._panel_height)
        form.addRow("数据库文件：", db_row_widget)
        form.addRow("数据库状态：", self._db_summary)

        hint = QLabel(
            "热键支持 Ctrl/Alt/Shift/Win 与 A-Z、0-9、方向键、F1-F24 的组合。"
            "直接粘贴启用后，Enter/双击会粘贴到打开面板前的窗口；Ctrl+Enter 始终仅复制。"
            "数据库文件留空则使用默认位置。",
            card,
        )
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

        self.resize(520, 480)
        self._apply_styles()
        self._refresh_db_summary()

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

    def _browse_db_path(self) -> None:
        current = self._db_path.text().strip() or default_db_path()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "选择数据库文件",
            current,
            "SQLite 数据库 (*.sqlite3 *.db);;所有文件 (*.*)",
        )
        if path:
            self._db_path.setText(path)

    def _open_data_folder(self) -> None:
        path = self._db_path.text().strip() or default_db_path()
        folder = os.path.dirname(os.path.abspath(path))
        try:
            os.makedirs(folder, exist_ok=True)
            os.startfile(folder)  # type: ignore[attr-defined]
        except Exception as exc:
            QMessageBox.warning(self, "打开目录", f"无法打开数据目录：{exc}")

    def _refresh_db_summary(self) -> None:
        path = self._db_path.text().strip() or default_db_path()
        if self._database_summary is None:
            self._db_summary.setText("未提供状态信息")
            return
        try:
            self._db_summary.setText(self._database_summary(path))
        except Exception:
            self._db_summary.setText("数据库状态不可用")

    def _backup_db(self) -> None:
        if self._backup_database is None:
            QMessageBox.warning(self, "备份数据库", "当前版本未提供备份功能")
            return
        current = self._db_path.text().strip() or default_db_path()
        default_backup = os.path.splitext(current)[0] + "-backup.sqlite3"
        target, _ = QFileDialog.getSaveFileName(
            self,
            "备份数据库",
            default_backup,
            "SQLite 数据库 (*.sqlite3 *.db);;所有文件 (*.*)",
        )
        if not target:
            return
        ok, message = self._backup_database(target)
        if ok:
            QMessageBox.information(self, "备份数据库", message or "备份完成")
        else:
            QMessageBox.warning(self, "备份数据库", message or "备份失败")

    def _reset_defaults(self) -> None:
        self._hotkey_show.setKeySequence(QKeySequence("Alt+C"))
        self._hotkey_pause.setKeySequence(QKeySequence("Alt+P"))
        self._max_items.setValue(1000)
        self._persist.setChecked(False)
        self._auto_paste.setChecked(False)
        self._autostart.setChecked(False)
        self._panel_width.setValue(640)
        self._panel_height.setValue(620)
        self._db_path.clear()

    def _on_ok(self) -> None:
        show_seq = self._hotkey_show.keySequence().toString(QKeySequence.PortableText).strip()
        pause_seq = self._hotkey_pause.keySequence().toString(QKeySequence.PortableText).strip()
        if show_seq == "None":
            show_seq = ""
        if pause_seq == "None":
            pause_seq = ""

        ok, msg = self._apply_settings(
            show_seq,
            pause_seq,
            int(self._max_items.value()),
            self._persist.isChecked(),
            self._auto_paste.isChecked(),
            self._autostart.isChecked(),
            int(self._panel_width.value()),
            int(self._panel_height.value()),
            self._db_path.text().strip(),
        )
        if not ok:
            QMessageBox.warning(self, "设置", msg or "保存失败")
            return
        self.accept()
