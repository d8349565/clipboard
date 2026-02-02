from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import html as _html
import re
from typing import Callable

from PySide6.QtCore import Qt, QMimeData, QTimer, QUrl, QRect
from PySide6.QtGui import QColor, QCursor, QDrag, QGuiApplication, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .models import ClipboardItem


class _ClipListWidget(QListWidget):
    def __init__(self, get_item: Callable[[int], ClipboardItem | None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._get_item = get_item
        self.setDragEnabled(True)

    def startDrag(self, supportedActions):  # type: ignore[override]
        row = self.currentRow()
        it = self._get_item(row)
        if it is None:
            return

        mime = QMimeData()
        if it.item_type in ("text", "html", "rtf"):
            mime.setText(it.text or "")
        elif it.item_type == "files":
            urls = [QUrl.fromLocalFile(p) for p in (it.file_paths or ())]
            mime.setUrls(urls)
        elif it.item_type == "image":
            img = _qimage_from_dib(it.raw_bytes or b"")
            if img is not None and not img.isNull():
                mime.setImageData(img)
        else:
            return

        drag = QDrag(self)
        drag.setMimeData(mime)
        if it.item_type == "image":
            img = _qimage_from_dib(it.raw_bytes or b"")
            if img is not None and not img.isNull():
                drag.setPixmap(QPixmap.fromImage(img).scaled(128, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        drag.exec(supportedActions)


def _qimage_from_dib(dib: bytes) -> QImage | None:
    if not dib:
        return None
    try:
        from PIL import Image
    except Exception:
        return None

    import io
    import struct

    try:
        header_size = struct.unpack_from("<I", dib, 0)[0]
        if header_size < 40:
            return None
        bit_count = struct.unpack_from("<H", dib, 14)[0]
        clr_used = struct.unpack_from("<I", dib, 32)[0]
        if bit_count <= 8:
            palette_entries = clr_used or (1 << bit_count)
        else:
            palette_entries = 0
        offset = 14 + header_size + palette_entries * 4
        file_size = 14 + len(dib)
        bf = b"BM" + struct.pack("<IHHI", file_size, 0, 0, offset)
        bmp_bytes = bf + dib
        im = Image.open(io.BytesIO(bmp_bytes))
        out = io.BytesIO()
        im.save(out, format="PNG")
        return QImage.fromData(out.getvalue(), "PNG")
    except Exception:
        return None


_RE_HTML_TAG = re.compile(r"<[^>]+>")
_RE_RTF_CTRL = re.compile(r"\\[a-zA-Z]+\d* ?|[{}]")
_RE_WS = re.compile(r"\s+")


def _clean_preview(item: ClipboardItem, max_len: int = 120) -> str:
    s = item.preview(10_000)
    if item.item_type == "html":
        s = _RE_HTML_TAG.sub(" ", s)
        s = _html.unescape(s)
    elif item.item_type == "rtf":
        s = _RE_RTF_CTRL.sub(" ", s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _RE_WS.sub(" ", s).strip()
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def _secondary_text(item: ClipboardItem) -> str:
    ts = item.created_at.astimezone().strftime("%H:%M:%S")
    if item.item_type == "files":
        n = len(item.file_paths or ())
        return f"{ts} · {n} 个文件"
    if item.item_type == "image":
        size = len(item.raw_bytes or b"")
        kb = max(1, size // 1024)
        return f"{ts} · {kb} KB"
    if item.item_type in ("html", "rtf"):
        size = len(item.raw_bytes or b"")
        if size:
            kb = max(1, size // 1024)
            return f"{ts} · {kb} KB"
    return ts


@dataclass(frozen=True, slots=True)
class _RowModel:
    title: str
    subtitle: str
    item_type: str


class _ClipItemDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option, index):  # type: ignore[override]
        it: ClipboardItem | None = index.data(Qt.UserRole)
        if it is None:
            super().paint(painter, option, index)
            return

        painter.save()
        rect: QRect = option.rect

        is_selected = bool(option.state & QStyle.State_Selected)
        bg = QColor("#FFFFFF") if not is_selected else QColor("#22B3C7")
        fg = QColor("#0F172A") if not is_selected else QColor("#FFFFFF")
        sub_fg = QColor("#475569") if not is_selected else QColor(255, 255, 255, 220)

        painter.fillRect(rect, bg)

        icon = self._icon_for(it, option.widget)
        icon_size = 18
        left_pad = 12
        icon_x = rect.left() + left_pad
        icon_y = rect.top() + (rect.height() - icon_size) // 2
        icon.paint(painter, QRect(icon_x, icon_y, icon_size, icon_size), Qt.AlignCenter)

        x0 = icon_x + icon_size + 10
        x1 = rect.right() - 12
        w = max(0, x1 - x0)
        y0 = rect.top() + 8

        fm1 = option.fontMetrics
        title = _clean_preview(it, 150)
        title = fm1.elidedText(title, Qt.ElideRight, w)

        painter.setPen(fg)
        painter.drawText(QRect(x0, y0, w, fm1.height() + 2), Qt.AlignLeft | Qt.AlignVCenter, title)

        subtitle = _secondary_text(it)
        painter.setPen(sub_fg)
        painter.drawText(
            QRect(x0, y0 + fm1.height() + 6, w, fm1.height() + 2),
            Qt.AlignLeft | Qt.AlignVCenter,
            fm1.elidedText(subtitle, Qt.ElideRight, w),
        )

        painter.restore()

    def sizeHint(self, option, index):  # type: ignore[override]
        base = super().sizeHint(option, index)
        return base.expandedTo(base.__class__(base.width(), 46))

    def _icon_for(self, it: ClipboardItem, widget: QWidget | None):
        w = widget or QWidget()
        style = w.style()
        if it.item_type == "text":
            return style.standardIcon(QStyle.SP_FileIcon)
        if it.item_type == "files":
            return style.standardIcon(QStyle.SP_DirIcon)
        if it.item_type == "image":
            return style.standardIcon(QStyle.SP_FileDialogContentsView)
        if it.item_type == "html":
            return style.standardIcon(QStyle.SP_BrowserReload)
        if it.item_type == "rtf":
            return style.standardIcon(QStyle.SP_FileDialogInfoView)
        return style.standardIcon(QStyle.SP_MessageBoxQuestion)


class ClipPanel(QWidget):
    def __init__(
        self,
        on_activate: Callable[[ClipboardItem], None],
        on_clear: Callable[[], None] | None = None,
        on_open_settings: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._on_activate = on_activate
        self._on_clear = on_clear
        self._on_open_settings = on_open_settings
        self._all_items: list[ClipboardItem] = []
        self._filtered_items: list[ClipboardItem] = []
        self._paused = False
        self._pinned = False

        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        card = QFrame(self)
        card.setObjectName("card")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 90))
        card.setGraphicsEffect(shadow)

        self._search = QLineEdit(card)
        self._search.setPlaceholderText("搜索…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)

        self._list = _ClipListWidget(self._get_filtered_item, card)
        self._list.setUniformItemSizes(True)
        self._list.setItemDelegate(_ClipItemDelegate(self._list))
        self._list.itemActivated.connect(self._on_item_activated)
        self._list.currentRowChanged.connect(lambda _: self._sync_tooltips())

        header = QHBoxLayout()
        self._title = QLabel("剪切板历史", card)
        self._title.setObjectName("title")
        header.addWidget(self._title)

        self._status = QLabel("", card)
        self._status.setObjectName("status")
        header.addWidget(self._status)

        header.addStretch(1)

        self._btn_pin = QToolButton(card)
        self._btn_pin.setCheckable(True)
        self._btn_pin.setIcon(self.style().standardIcon(QStyle.SP_TitleBarUnshadeButton))
        self._btn_pin.setToolTip("固定（选择后不自动关闭）")
        self._btn_pin.toggled.connect(self._set_pinned)
        header.addWidget(self._btn_pin)

        self._btn_clear = QToolButton(card)
        self._btn_clear.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        self._btn_clear.setToolTip("清空历史")
        self._btn_clear.clicked.connect(lambda: self._on_clear() if self._on_clear else None)
        header.addWidget(self._btn_clear)

        self._btn_settings = QToolButton(card)
        self._btn_settings.setIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))
        self._btn_settings.setToolTip("设置")
        self._btn_settings.clicked.connect(lambda: self._on_open_settings() if self._on_open_settings else None)
        header.addWidget(self._btn_settings)

        body = QVBoxLayout(card)
        body.setContentsMargins(14, 14, 14, 14)
        body.setSpacing(10)
        body.addLayout(header)
        body.addWidget(self._search)
        body.addWidget(self._list)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.addWidget(card)

        self.resize(520, 420)
        self._apply_styles()
        self._sync_status()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self._sync_status()

    def set_items(self, items: list[ClipboardItem]) -> None:
        self._all_items = items
        self._apply_filter()

    def toggle_visible(self) -> None:
        if self.isVisible():
            self.hide()
            return
        self._show_near_cursor()

    def _show_near_cursor(self) -> None:
        self._search.setText("")
        self._apply_filter()

        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos) or QGuiApplication.primaryScreen()
        screen_geo = screen.availableGeometry()

        x = min(max(cursor_pos.x() - self.width() // 2, screen_geo.left()), screen_geo.right() - self.width())
        y = min(max(cursor_pos.y() + 18, screen_geo.top()), screen_geo.bottom() - self.height())
        self.move(x, y)

        self.show()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(0, self._search.setFocus)

    def keyPressEvent(self, event):  # type: ignore[override]
        if event.key() == Qt.Key_F and event.modifiers() & Qt.ControlModifier:
            self._search.setFocus()
            self._search.selectAll()
            return
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self._list.currentRow() >= 0:
            self._activate_row(self._list.currentRow())
            return
        super().keyPressEvent(event)

    def _apply_filter(self) -> None:
        q = (self._search.text() or "").strip().lower()
        if not q:
            self._filtered_items = self._all_items[:]
        else:
            self._filtered_items = [
                it
                for it in self._all_items
                if q in _clean_preview(it, 10_000).lower()
                or (it.item_type == "files" and any(q in p.lower() for p in (it.file_paths or ())))
            ]

        self._list.clear()
        for it in self._filtered_items:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, it)
            item.setToolTip(it.preview(10_000))
            self._list.addItem(item)

        if self._filtered_items:
            self._list.setCurrentRow(0)
        self._sync_tooltips()

    def _get_filtered_item(self, row: int) -> ClipboardItem | None:
        if row < 0 or row >= len(self._filtered_items):
            return None
        return self._filtered_items[row]

    def _on_item_activated(self, _: QListWidgetItem) -> None:
        row = self._list.currentRow()
        if row < 0:
            return
        self._activate_row(row)

    def _activate_row(self, row: int) -> None:
        if row < 0 or row >= len(self._filtered_items):
            return
        it = self._filtered_items[row]
        self._on_activate(it)
        if not self._pinned:
            self.hide()

    def _set_pinned(self, pinned: bool) -> None:
        self._pinned = pinned

    def _sync_status(self) -> None:
        self._status.setText("暂停" if self._paused else "监听中")
        self._status.setProperty("paused", self._paused)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def _sync_tooltips(self) -> None:
        has_actions = bool(self._on_clear) or bool(self._on_open_settings)
        self._btn_clear.setVisible(bool(self._on_clear))
        self._btn_settings.setVisible(bool(self._on_open_settings))
        self._btn_pin.setVisible(True)
        if has_actions:
            return

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            #card {
              background: #F7F7FA;
              border: 1px solid rgba(120, 120, 120, 0.28);
              border-radius: 12px;
            }
            QLabel#title {
              font-size: 14px;
              font-weight: 600;
            }
            QLabel#status {
              padding: 2px 8px;
              border-radius: 10px;
              background: rgba(15, 23, 42, 0.08);
            }
            QLabel#status[paused="true"] {
              background: rgba(220, 80, 80, 0.18);
            }
            QLineEdit {
              padding: 7px 10px;
              border-radius: 10px;
              border: 1px solid rgba(120, 120, 120, 0.28);
              background: #FFFFFF;
            }
            QListWidget {
              border: 1px solid rgba(120, 120, 120, 0.20);
              border-radius: 10px;
              background: #FFFFFF;
              outline: 0;
            }
            QListWidget::item {
              border-bottom: 1px solid rgba(120, 120, 120, 0.10);
            }
            QListWidget::item:selected {
              background: #22B3C7;
            }
            QToolButton {
              padding: 4px;
              border-radius: 8px;
            }
            QToolButton:hover {
              background: rgba(120, 120, 120, 0.16);
            }
            """
        )
