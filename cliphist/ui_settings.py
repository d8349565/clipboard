from __future__ import annotations

import os
from typing import Callable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .settings import AppSettings, MAX_ITEMS_LIMIT, default_db_path
from .themes import THEME_NAMES, dialog_styles


ApplySettings = Callable[[str, str, int, bool, bool, bool, int, int, str, str], tuple[bool, str | None]]
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
        preview_theme: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._apply_settings = apply_settings
        self._backup_database = backup_database
        self._database_summary = database_summary
        self._preview_theme = preview_theme
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
        btn_close.setText("×")
        btn_close.setToolTip("关闭")
        btn_close.setFixedSize(36, 36)
        btn_close.clicked.connect(self.reject)
        title_bar.addWidget(btn_close)

        # -- form --
        self._theme = QComboBox(card)
        self._theme.setAccessibleName("界面主题")
        for value, name in THEME_NAMES.items():
            self._theme.addItem(name, value)
        self._theme.setCurrentIndex(max(0, self._theme.findData(settings.theme)))
        self._theme.setToolTip("立即预览；点击保存后记住选择，取消则恢复原主题")
        self._hotkey_show = QKeySequenceEdit(card)
        self._hotkey_pause = QKeySequenceEdit(card)
        self._max_items = QSpinBox(card)
        self._persist = QCheckBox("启用 SQLite 持久化历史", card)
        self._auto_paste = QCheckBox("回车 / 双击时，直接粘贴到原窗口", card)
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
        self._panel_height.setRange(440, 1200)
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
        self._db_summary.setWordWrap(True)
        self._db_path.textChanged.connect(self._refresh_db_summary)

        form = QFormLayout()
        form.setSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.addRow("界面主题", self._theme)
        form.addRow("打开面板热键：", self._hotkey_show)
        form.addRow("暂停热键：", self._hotkey_pause)
        form.addRow("直接粘贴：", self._auto_paste)
        form.addRow("开机自启：", self._autostart)
        form.addRow("面板宽度：", self._panel_width)
        form.addRow("面板高度：", self._panel_height)

        self._advanced = QWidget(card)
        advanced_form = QFormLayout(self._advanced)
        advanced_form.setContentsMargins(0, 8, 0, 0)
        advanced_form.setSpacing(12)
        advanced_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        advanced_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        advanced_form.addRow("历史条数：", self._max_items)
        advanced_form.addRow("持久化：", self._persist)
        advanced_form.addRow("数据库文件：", db_row_widget)
        advanced_form.addRow("数据库状态：", self._db_summary)
        self._advanced.hide()

        self._btn_advanced = QToolButton(card)
        self._btn_advanced.setText("数据与高级设置  ›")
        self._btn_advanced.setCheckable(True)
        self._btn_advanced.setAccessibleName("展开数据与高级设置")
        self._btn_advanced.toggled.connect(self._toggle_advanced)

        hint = QLabel(
            "主题可即时预览；跟随系统会自动切换浅色与深色。\n"
            "Ctrl+Enter 始终仅复制。热键支持 Ctrl/Alt/Shift/Win 与字母、数字、方向键或功能键组合。",
            card,
        )
        hint.setWordWrap(True)
        hint.setObjectName("settingsHint")

        # -- buttons --
        btn_reset = QPushButton("恢复默认", card)
        btn_reset.setObjectName("settingsBtn")
        btn_reset.clicked.connect(self._reset_defaults)

        btn_ok = QPushButton("保存设置", card)
        btn_ok.setObjectName("settingsBtnPrimary")
        btn_ok.clicked.connect(self._on_ok)

        btn_cancel = QPushButton("取消", card)
        btn_cancel.setObjectName("settingsBtn")
        btn_cancel.clicked.connect(self.reject)
        for button in (btn_reset, btn_ok, btn_cancel):
            button.setMinimumHeight(38)
        btn_ok.setDefault(True)

        btn_row = QHBoxLayout()
        btn_row.addWidget(btn_reset)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)

        scroll = QScrollArea(card)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget(scroll)
        content.setObjectName("settingsContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 8, 10, 8)
        content_layout.setSpacing(14)
        content_layout.addLayout(form)
        content_layout.addWidget(hint)
        content_layout.addWidget(self._btn_advanced, 0, Qt.AlignLeft)
        content_layout.addWidget(self._advanced)
        content_layout.addStretch(1)
        scroll.setWidget(content)

        body = QVBoxLayout(card)
        body.setContentsMargins(22, 16, 22, 20)
        body.setSpacing(14)
        body.addLayout(title_bar)
        body.addWidget(scroll, 1)
        body.addLayout(btn_row)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.addWidget(card)
        self.setLayout(root)

        self.setMinimumSize(520, 460)
        self.resize(640, 610)
        self._theme.currentIndexChanged.connect(self._on_theme_changed)
        self._apply_styles()

    def _apply_styles(self) -> None:
        self.setStyleSheet(dialog_styles(self._theme.currentData()))

    def _on_theme_changed(self, _index: int) -> None:
        self._apply_styles()
        if self._preview_theme is not None:
            self._preview_theme(self._theme.currentData())

    def _toggle_advanced(self, expanded: bool) -> None:
        self._advanced.setVisible(expanded)
        self._btn_advanced.setText("数据与高级设置  ⌄" if expanded else "数据与高级设置  ›")
        self._btn_advanced.setAccessibleName("收起数据与高级设置" if expanded else "展开数据与高级设置")
        if expanded:
            self._refresh_db_summary()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.parent() is not None:
            p = self.parent()
            cx = p.x() + (p.width() - self.width()) // 2
            cy = p.y() + (p.height() - self.height()) // 2
            area = p.screen().availableGeometry()
            cx = max(area.left(), min(cx, area.right() - self.width() + 1))
            cy = max(area.top(), min(cy, area.bottom() - self.height() + 1))
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
        if not self._advanced.isVisible():
            return
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
        self._theme.setCurrentIndex(self._theme.findData("system"))
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
            self._theme.currentData(),
        )
        if not ok:
            QMessageBox.warning(self, "设置", msg or "保存失败")
            return
        self.accept()
