from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import html as _html
import re
from typing import Callable

from PySide6.QtCore import Qt, QMimeData, QTimer, QUrl, QRect
from PySide6.QtGui import QColor, QCursor, QDrag, QGuiApplication, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QTabWidget,
    QStackedWidget,
    QToolButton,
    QTextBrowser,
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
    import io
    import struct

    try:
        from PIL import Image
    except Exception:
        return None

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


def _html_fragment_from_clipboard(raw: bytes, max_len: int = 12000) -> str | None:
    if not raw:
        return None
    try:
        header = raw[:4096].decode("ascii", errors="ignore")
        start_key = "StartFragment:"
        end_key = "EndFragment:"
        start_i = header.find(start_key)
        end_i = header.find(end_key)
        if start_i != -1 and end_i != -1:
            start_line = header[start_i : header.find("\n", start_i)].strip()
            end_line = header[end_i : header.find("\n", end_i)].strip()
            start = int(start_line.split(":", 1)[1].strip())
            end = int(end_line.split(":", 1)[1].strip())
            frag = raw[start:end]
            s = frag.decode("utf-8", errors="ignore").strip()
            return s[:max_len]
    except Exception:
        pass
    try:
        s = raw.decode("utf-8", errors="ignore").strip()
        return s[:max_len]
    except Exception:
        return None


_RE_HTML_TAG = re.compile(r"<[^>]+>")
_RE_RTF_CTRL = re.compile(r"\\[a-zA-Z]+\d* ?|[{}]")
_RE_WS = re.compile(r"\s+")
_RE_HTML_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


def _rtf_to_plain(raw: bytes, max_len: int = 12000) -> str:
    if not raw:
        return ""
    try:
        s = raw.decode("latin-1", errors="ignore").replace("\r\n", "\n").replace("\r", "\n")
    except Exception:
        return ""
    s = _RE_RTF_CTRL.sub(" ", s)
    s = _RE_WS.sub(" ", s).strip()
    return s[:max_len]


def _html_to_plain(s: str, max_len: int = 12000) -> str:
    if not s:
        return ""
    s = _RE_HTML_SCRIPT_STYLE.sub(" ", s)
    s = _RE_HTML_TAG.sub(" ", s)
    lt = s.rfind("<")
    gt = s.rfind(">")
    if lt > gt:
        s = s[:lt]
    s = _html.unescape(s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _RE_WS.sub(" ", s).strip()
    return s[:max_len]


def _clean_preview(item: ClipboardItem, max_len: int = 120) -> str:
    s = item.preview(10_000)
    if item.item_type == "html":
        s = _html_to_plain(s, max_len=10_000)
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
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._thumb_cache: dict[int, QPixmap] = {}

    def paint(self, painter: QPainter, option, index):  # type: ignore[override]
        it: ClipboardItem | None = index.data(Qt.UserRole)
        if it is None:
            super().paint(painter, option, index)
            return

        is_fav = bool(index.data(Qt.UserRole + 2))
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect: QRect = option.rect

        is_selected = bool(option.state & QStyle.State_Selected)
        is_hover = bool(option.state & QStyle.State_MouseOver)
        bg = QColor("#FFFFFF")
        if is_selected:
            bg = QColor("#1D4ED8")
        elif is_hover:
            bg = QColor("#F1F5F9")
        fg = QColor("#0F172A") if not is_selected else QColor("#FFFFFF")
        sub_fg = QColor("#64748B") if not is_selected else QColor(255, 255, 255, 220)

        painter.fillRect(rect, bg)
        if is_selected:
            painter.fillRect(QRect(rect.left(), rect.top(), 4, rect.height()), QColor("#60A5FA"))

        badge_size = 34
        left_pad = 12
        badge_rect = QRect(
            rect.left() + left_pad,
            rect.top() + (rect.height() - badge_size) // 2,
            badge_size,
            badge_size,
        )
        if it.item_type == "image":
            thumb = self._image_thumb(it, badge_size - 4)
            if thumb is not None:
                clip = QPainterPath()
                clip.addRoundedRect(badge_rect, 8, 8)
                painter.setClipPath(clip)
                painter.drawPixmap(badge_rect, thumb)
                painter.setClipping(False)
                painter.setPen(QColor(15, 23, 42, 40))
                painter.drawRoundedRect(badge_rect.adjusted(0, 0, -1, -1), 8, 8)
            else:
                self._paint_icon_badge(painter, badge_rect, it, is_selected, option.widget)
        else:
            self._paint_icon_badge(painter, badge_rect, it, is_selected, option.widget)

        x0 = badge_rect.right() + 12
        x1 = rect.right() - 14 - 20
        w = max(0, x1 - x0)
        y0 = rect.top() + 8

        fm1 = option.fontMetrics
        title = _clean_preview(it, 150)
        title = fm1.elidedText(title, Qt.ElideRight, w)

        font_title = option.font
        font_title.setBold(True)
        painter.setFont(font_title)
        painter.setPen(fg)
        painter.drawText(QRect(x0, y0, w, fm1.height() + 2), Qt.AlignLeft | Qt.AlignVCenter, title)

        subtitle = _secondary_text(it)
        painter.setFont(option.font)
        painter.setPen(sub_fg)
        painter.drawText(
            QRect(x0, y0 + fm1.height() + 6, w, fm1.height() + 2),
            Qt.AlignLeft | Qt.AlignVCenter,
            fm1.elidedText(subtitle, Qt.ElideRight, w),
        )

        if is_fav:
            painter.setPen(QColor("#F59E0B") if not is_selected else QColor("#FFFFFF"))
            painter.drawText(QRect(rect.right() - 28, rect.top(), 20, rect.height()), Qt.AlignCenter, "★")

        painter.restore()

    def sizeHint(self, option, index):  # type: ignore[override]
        base = super().sizeHint(option, index)
        return base.expandedTo(base.__class__(base.width(), 58))

    def _image_thumb(self, it: ClipboardItem, size: int) -> QPixmap | None:
        key = id(it)
        cached = self._thumb_cache.get(key)
        if cached is not None:
            return cached
        img = _qimage_from_dib(it.raw_bytes or b"")
        if img is None or img.isNull():
            return None
        pix = QPixmap.fromImage(img).scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._thumb_cache[key] = pix
        if len(self._thumb_cache) > 260:
            self._thumb_cache.pop(next(iter(self._thumb_cache)))
        return pix

    def _paint_icon_badge(
        self,
        painter: QPainter,
        rect: QRect,
        it: ClipboardItem,
        is_selected: bool,
        widget: QWidget | None,
    ) -> None:
        bg = QColor(255, 255, 255, 60) if is_selected else self._type_color(it.item_type)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawEllipse(rect)
        icon = self._icon_for(it, widget)
        icon_rect = rect.adjusted(7, 7, -7, -7)
        icon.paint(painter, icon_rect, Qt.AlignCenter)

    def _type_color(self, item_type: str) -> QColor:
        return {
            "text": QColor("#E0F2FE"),
            "files": QColor("#ECFDF3"),
            "image": QColor("#FEF3C7"),
            "html": QColor("#FCE7F3"),
            "rtf": QColor("#EDE9FE"),
        }.get(item_type, QColor("#E2E8F0"))

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
        get_favorites: Callable[[], list[tuple[str, ClipboardItem]]] | None = None,
        toggle_favorite: Callable[[ClipboardItem], tuple[bool, str | None]] | None = None,
        remove_favorite: Callable[[str], tuple[bool, str | None]] | None = None,
        reorder_favorites: Callable[[list[str]], tuple[bool, str | None]] | None = None,
    ) -> None:
        super().__init__()
        self._on_activate = on_activate
        self._on_clear = on_clear
        self._on_open_settings = on_open_settings
        self._get_favorites = get_favorites
        self._toggle_favorite = toggle_favorite
        self._remove_favorite = remove_favorite
        self._reorder_favorites = reorder_favorites
        self._all_items: list[ClipboardItem] = []
        self._filtered_items: list[ClipboardItem] = []
        self._favorites: list[tuple[str, ClipboardItem]] = []
        self._fav_filtered: list[tuple[str, ClipboardItem]] = []
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

        self._tabs = QTabWidget(card)
        self._tabs.setObjectName("tabs")

        self._list_all = _ClipListWidget(self._get_filtered_item, card)
        self._list_all.setUniformItemSizes(True)
        self._list_all.setItemDelegate(_ClipItemDelegate(self._list_all))
        self._list_all.itemActivated.connect(lambda _: self._activate_current())
        self._list_all.setMouseTracking(True)
        self._list_all.currentItemChanged.connect(lambda *_: self._update_preview())
        self._list_all.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list_all.customContextMenuRequested.connect(lambda pos: self._show_context_menu(self._list_all, pos))

        self._list_fav = _ClipListWidget(self._get_fav_filtered_item, card)
        self._list_fav.setUniformItemSizes(True)
        self._list_fav.setItemDelegate(_ClipItemDelegate(self._list_fav))
        self._list_fav.itemActivated.connect(lambda _: self._activate_current())
        self._list_fav.setMouseTracking(True)
        self._list_fav.currentItemChanged.connect(lambda *_: self._update_preview())
        self._list_fav.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list_fav.customContextMenuRequested.connect(lambda pos: self._show_context_menu(self._list_fav, pos))

        tab_all = QWidget(card)
        tab_all_layout = QVBoxLayout(tab_all)
        tab_all_layout.setContentsMargins(0, 0, 0, 0)
        tab_all_layout.addWidget(self._list_all)
        tab_all.setLayout(tab_all_layout)

        tab_fav = QWidget(card)
        tab_fav_layout = QVBoxLayout(tab_fav)
        tab_fav_layout.setContentsMargins(0, 0, 0, 0)
        tab_fav_layout.addWidget(self._list_fav)
        tab_fav.setLayout(tab_fav_layout)

        self._tabs.addTab(tab_all, "全部")
        self._tabs.addTab(tab_fav, "收藏")

        self._preview = QFrame(card)
        self._preview.setObjectName("previewCard")
        self._preview.setMinimumHeight(170)

        preview_header = QHBoxLayout()
        self._preview_title = QLabel("预览", self._preview)
        self._preview_title.setObjectName("previewTitle")
        preview_header.addWidget(self._preview_title)
        preview_header.addStretch(1)
        self._preview_meta = QLabel("", self._preview)
        self._preview_meta.setObjectName("previewMeta")
        preview_header.addWidget(self._preview_meta)

        self._preview_stack = QStackedWidget(self._preview)
        self._preview_empty = QLabel("选择一条记录以预览内容", self._preview)
        self._preview_empty.setAlignment(Qt.AlignCenter)
        self._preview_empty.setObjectName("previewEmpty")

        self._preview_text = QTextBrowser(self._preview)
        self._preview_text.setOpenExternalLinks(False)
        self._preview_text.setReadOnly(True)
        self._preview_text.setFrameStyle(QFrame.NoFrame)
        self._preview_text.setObjectName("previewText")

        self._preview_image_label = QLabel(self._preview)
        self._preview_image_label.setAlignment(Qt.AlignCenter)
        self._preview_image_label.setObjectName("previewImage")

        self._preview_stack.addWidget(self._preview_empty)
        self._preview_stack.addWidget(self._preview_text)
        self._preview_stack.addWidget(self._preview_image_label)
        self._preview_stack.setCurrentWidget(self._preview_empty)

        preview_body = QVBoxLayout(self._preview)
        preview_body.setContentsMargins(10, 10, 10, 10)
        preview_body.setSpacing(8)
        preview_body.addLayout(preview_header)
        preview_body.addWidget(self._preview_stack)
        self._preview.setLayout(preview_body)
        self._preview_image: QImage | None = None

        header = QHBoxLayout()
        self._title = QLabel("剪切板历史", card)
        self._title.setObjectName("title")
        header.addWidget(self._title)

        self._status = QLabel("", card)
        self._status.setObjectName("status")
        header.addWidget(self._status)

        header.addStretch(1)

        self._btn_fav = QToolButton(card)
        self._btn_fav.setObjectName("btnAction")
        self._btn_fav.setIcon(self.style().standardIcon(QStyle.SP_DialogYesButton))
        self._btn_fav.setText("收藏")
        self._btn_fav.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._btn_fav.setToolTip("收藏/取消收藏")
        self._btn_fav.clicked.connect(self._toggle_current_favorite)
        header.addWidget(self._btn_fav)

        self._btn_up = QToolButton(card)
        self._btn_up.setObjectName("btnCompact")
        self._btn_up.setIcon(self.style().standardIcon(QStyle.SP_ArrowUp))
        self._btn_up.setToolTip("上移（仅收藏）")
        self._btn_up.clicked.connect(lambda: self._move_favorite(-1))
        header.addWidget(self._btn_up)

        self._btn_down = QToolButton(card)
        self._btn_down.setObjectName("btnCompact")
        self._btn_down.setIcon(self.style().standardIcon(QStyle.SP_ArrowDown))
        self._btn_down.setToolTip("下移（仅收藏）")
        self._btn_down.clicked.connect(lambda: self._move_favorite(1))
        header.addWidget(self._btn_down)

        self._btn_del_fav = QToolButton(card)
        self._btn_del_fav.setObjectName("btnCompact")
        self._btn_del_fav.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        self._btn_del_fav.setToolTip("删除收藏")
        self._btn_del_fav.clicked.connect(self._remove_current_favorite)
        header.addWidget(self._btn_del_fav)

        self._tabs.currentChanged.connect(lambda _: self._on_tab_changed())

        self._btn_pin = QToolButton(card)
        self._btn_pin.setObjectName("btnAction")
        self._btn_pin.setCheckable(True)
        self._btn_pin.setIcon(self.style().standardIcon(QStyle.SP_TitleBarUnshadeButton))
        self._btn_pin.setText("置顶")
        self._btn_pin.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._btn_pin.setToolTip("固定（选择后不自动关闭）")
        self._btn_pin.toggled.connect(self._set_pinned)
        header.addWidget(self._btn_pin)

        self._btn_clear = QToolButton(card)
        self._btn_clear.setObjectName("btnAction")
        self._btn_clear.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        self._btn_clear.setText("清空")
        self._btn_clear.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._btn_clear.setToolTip("清空历史")
        self._btn_clear.clicked.connect(lambda: self._on_clear() if self._on_clear else None)
        header.addWidget(self._btn_clear)

        self._btn_settings = QToolButton(card)
        self._btn_settings.setObjectName("btnAction")
        self._btn_settings.setIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))
        self._btn_settings.setText("设置")
        self._btn_settings.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._btn_settings.setToolTip("设置")
        self._btn_settings.clicked.connect(lambda: self._on_open_settings() if self._on_open_settings else None)
        header.addWidget(self._btn_settings)

        body = QVBoxLayout(card)
        body.setContentsMargins(14, 14, 14, 14)
        body.setSpacing(10)
        body.addLayout(header)
        body.addWidget(self._search)
        body.addWidget(self._tabs, 1)
        body.addWidget(self._preview)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.addWidget(card)

        self.resize(560, 560)
        self._apply_styles()
        self._sync_status()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self._sync_status()

    def set_items(self, items: list[ClipboardItem]) -> None:
        self._all_items = items
        self._apply_filter()

    def set_favorites(self, favorites: list[tuple[str, ClipboardItem]]) -> None:
        self._favorites = favorites
        self._apply_filter()

    def toggle_visible(self) -> None:
        if self.isVisible():
            self.hide()
            return
        self._show_near_cursor()

    def _show_near_cursor(self) -> None:
        self._search.setText("")
        if self._get_favorites is not None:
            try:
                self._favorites = self._get_favorites()
            except Exception:
                self._favorites = self._favorites
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
        if event.key() == Qt.Key_F and event.modifiers() & Qt.AltModifier:
            self._toggle_current_favorite()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self._current_list().currentRow() >= 0:
            self._activate_current()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self._render_preview_image()

    def _apply_filter(self) -> None:
        q = (self._search.text() or "").strip().lower()
        fav_ids = {fid for fid, _ in self._favorites}

        if not q:
            self._filtered_items = self._all_items[:]
            self._fav_filtered = self._favorites[:]
        else:
            self._filtered_items = [
                it
                for it in self._all_items
                if q in _clean_preview(it, 10_000).lower()
                or (it.item_type == "files" and any(q in p.lower() for p in (it.file_paths or ())))
            ]
            self._fav_filtered = [
                (fid, it)
                for fid, it in self._favorites
                if q in _clean_preview(it, 10_000).lower()
                or (it.item_type == "files" and any(q in p.lower() for p in (it.file_paths or ())))
            ]

        self._list_all.clear()
        for it in self._filtered_items:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, it)
            item.setData(Qt.UserRole + 2, it is not None and (self._fav_id_for_item(it, fav_ids) is not None))
            item.setToolTip(it.preview(10_000))
            self._list_all.addItem(item)

        self._list_fav.clear()
        for fid, it in self._fav_filtered:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, it)
            item.setData(Qt.UserRole + 1, fid)
            item.setData(Qt.UserRole + 2, True)
            item.setToolTip(it.preview(10_000))
            self._list_fav.addItem(item)

        if self._tabs.currentIndex() == 0 and self._filtered_items:
            self._list_all.setCurrentRow(0)
        if self._tabs.currentIndex() == 1 and self._fav_filtered:
            self._list_fav.setCurrentRow(0)
        self._sync_tooltips()
        self._update_preview()

    def _update_preview(self) -> None:
        it = self._item_at_current_row()
        if it is None:
            self._preview_meta.setText("")
            self._preview_stack.setCurrentWidget(self._preview_empty)
            self._preview_image = None
            self._preview_image_label.clear()
            return

        ts = it.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        self._preview_meta.setText(f"{it.item_type.upper()} · {ts}")

        if it.item_type == "image":
            img = _qimage_from_dib(it.raw_bytes or b"")
            if img is None or img.isNull():
                self._preview_text.setPlainText("图片预览不可用")
                self._preview_stack.setCurrentWidget(self._preview_text)
                self._preview_image = None
            else:
                self._preview_image = img
                self._render_preview_image()
                self._preview_stack.setCurrentWidget(self._preview_image_label)
            return

        self._preview_image = None
        self._preview_image_label.clear()

        if it.item_type == "html":
            html = _html_fragment_from_clipboard(it.raw_bytes or b"")
            text = _html_to_plain(html or (it.text or ""), max_len=12000)
            self._preview_text.setPlainText(text or "(HTML 内容为空)")
            self._preview_stack.setCurrentWidget(self._preview_text)
            return

        if it.item_type == "rtf":
            text = _rtf_to_plain(it.raw_bytes or b"", max_len=12000) or (it.text or "")
            self._preview_text.setPlainText(text)
            self._preview_stack.setCurrentWidget(self._preview_text)
            return

        if it.item_type == "files":
            paths = it.file_paths or ()
            if paths:
                safe = "<br>".join(_html.escape(p) for p in paths[:80])
                if len(paths) > 80:
                    safe += "<br>…"
                self._preview_text.setHtml(safe)
            else:
                self._preview_text.setPlainText("（空文件列表）")
            self._preview_stack.setCurrentWidget(self._preview_text)
            return

        text = it.text or ""
        if len(text) > 12000:
            text = text[:12000] + "\n…"
        self._preview_text.setPlainText(text)
        self._preview_stack.setCurrentWidget(self._preview_text)

    def _render_preview_image(self) -> None:
        if self._preview_image is None:
            return
        size = self._preview_stack.size()
        if size.width() <= 0 or size.height() <= 0:
            return
        pix = QPixmap.fromImage(self._preview_image)
        scaled = pix.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._preview_image_label.setPixmap(scaled)

    def _get_filtered_item(self, row: int) -> ClipboardItem | None:
        if row < 0 or row >= len(self._filtered_items):
            return None
        return self._filtered_items[row]

    def _get_fav_filtered_item(self, row: int) -> ClipboardItem | None:
        if row < 0 or row >= len(self._fav_filtered):
            return None
        return self._fav_filtered[row][1]

    def _activate_current(self) -> None:
        w = self._current_list()
        row = w.currentRow()
        if row < 0:
            return
        it = self._item_at_current_row()
        if it is None:
            return
        self._on_activate(it)
        if not self._pinned:
            self.hide()

    def _current_list(self) -> QListWidget:
        return self._list_fav if self._tabs.currentIndex() == 1 else self._list_all

    def _item_at_current_row(self) -> ClipboardItem | None:
        row = self._current_list().currentRow()
        if self._tabs.currentIndex() == 1:
            if row < 0 or row >= len(self._fav_filtered):
                return None
            return self._fav_filtered[row][1]
        if row < 0 or row >= len(self._filtered_items):
            return None
        return self._filtered_items[row]

    def _fav_id_at_current_row(self) -> str | None:
        if self._tabs.currentIndex() != 1:
            return None
        row = self._list_fav.currentRow()
        if row < 0 or row >= len(self._fav_filtered):
            return None
        return self._fav_filtered[row][0]

    def _toggle_current_favorite(self) -> None:
        it = self._item_at_current_row()
        if it is None or self._toggle_favorite is None:
            return
        ok, _ = self._toggle_favorite(it)
        if ok and self._get_favorites is not None:
            try:
                self._favorites = self._get_favorites()
            except Exception:
                pass
        self._apply_filter()

    def _remove_current_favorite(self) -> None:
        fid = self._fav_id_at_current_row()
        if fid is None or self._remove_favorite is None:
            return
        self._remove_favorite(fid)
        if self._get_favorites is not None:
            try:
                self._favorites = self._get_favorites()
            except Exception:
                pass
        self._apply_filter()

    def _move_favorite(self, delta: int) -> None:
        if self._tabs.currentIndex() != 1 or self._reorder_favorites is None:
            return
        row = self._list_fav.currentRow()
        if row < 0 or row >= len(self._fav_filtered):
            return
        target = row + delta
        if target < 0 or target >= len(self._fav_filtered):
            return
        ids = [fid for fid, _ in self._fav_filtered]
        ids[row], ids[target] = ids[target], ids[row]
        self._reorder_favorites(ids)
        if self._get_favorites is not None:
            try:
                self._favorites = self._get_favorites()
            except Exception:
                pass
        self._apply_filter()
        self._tabs.setCurrentIndex(1)
        self._list_fav.setCurrentRow(target)

    def _on_tab_changed(self) -> None:
        self._sync_tooltips()
        self._apply_filter()

    def _fav_id_for_item(self, it: ClipboardItem, fav_ids: set[str]) -> str | None:
        fid = None
        if it is None:
            return None
        try:
            from .favorites import item_fingerprint

            fid = item_fingerprint(it)
        except Exception:
            fid = None
        return fid if fid in fav_ids else None

    def _show_context_menu(self, widget: QListWidget, pos) -> None:
        it = widget.itemAt(pos)
        if it is None:
            return
        item_obj: ClipboardItem | None = it.data(Qt.UserRole)
        if item_obj is None:
            return
        menu = QMenu(widget)
        act_fav = menu.addAction("收藏/取消收藏")
        act_del = None
        act_up = None
        act_down = None
        if widget is self._list_fav:
            act_del = menu.addAction("删除收藏")
            act_up = menu.addAction("上移")
            act_down = menu.addAction("下移")
        chosen = menu.exec(widget.mapToGlobal(pos))
        if chosen == act_fav:
            self._toggle_current_favorite()
        elif act_del is not None and chosen == act_del:
            self._remove_current_favorite()
        elif act_up is not None and chosen == act_up:
            self._move_favorite(-1)
        elif act_down is not None and chosen == act_down:
            self._move_favorite(1)

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
        self._btn_fav.setVisible(bool(self._toggle_favorite))
        is_fav_tab = self._tabs.currentIndex() == 1
        self._btn_up.setVisible(is_fav_tab)
        self._btn_down.setVisible(is_fav_tab)
        self._btn_del_fav.setVisible(is_fav_tab and bool(self._remove_favorite))
        can_reorder = is_fav_tab and not (self._search.text() or "").strip()
        self._btn_up.setEnabled(can_reorder)
        self._btn_down.setEnabled(can_reorder)
        if has_actions:
            return

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            #card {
              background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                          stop:0 #F8FAFC, stop:1 #EEF2FF);
              border: 1px solid rgba(148, 163, 184, 0.45);
              border-radius: 14px;
            }
            QLabel#title {
              font-size: 15px;
              font-weight: 700;
              color: #0F172A;
            }
            QLabel#status {
              padding: 2px 10px;
              border-radius: 10px;
              background: rgba(15, 23, 42, 0.08);
              color: #0F172A;
            }
            QLabel#status[paused="true"] {
              background: rgba(239, 68, 68, 0.16);
              color: #991B1B;
            }
            QLineEdit {
              padding: 8px 12px;
              border-radius: 10px;
              border: 1px solid rgba(148, 163, 184, 0.6);
              background: #FFFFFF;
            }
            QLineEdit:focus {
              border: 1px solid #3B82F6;
              background: #F8FAFC;
            }
            QListWidget {
              border: 1px solid rgba(148, 163, 184, 0.5);
              border-radius: 12px;
              background: #FFFFFF;
              outline: 0;
            }
            QTabWidget::pane {
              border: 0;
            }
            QTabBar::tab {
              padding: 6px 12px;
              border-radius: 10px;
              background: rgba(148, 163, 184, 0.18);
              color: #334155;
              margin-right: 6px;
            }
            QTabBar::tab:selected {
              background: #2563EB;
              color: #FFFFFF;
            }
            QListWidget::item {
              border-bottom: 1px solid rgba(148, 163, 184, 0.25);
            }
            QListWidget::item:selected {
              background: transparent;
            }
            QToolButton#btnAction {
              padding: 4px 10px;
              border-radius: 9px;
              border: 1px solid rgba(148, 163, 184, 0.4);
              background: #FFFFFF;
              color: #0F172A;
            }
            QToolButton#btnAction:hover {
              background: #F1F5F9;
            }
            QToolButton#btnAction:checked {
              background: rgba(59, 130, 246, 0.18);
              border: 1px solid rgba(37, 99, 235, 0.5);
            }
            QToolButton#btnCompact {
              padding: 4px;
              border-radius: 8px;
              border: 1px solid transparent;
            }
            QToolButton#btnCompact:hover {
              background: rgba(148, 163, 184, 0.22);
              border: 1px solid rgba(148, 163, 184, 0.35);
            }
            #previewCard {
              border: 1px solid rgba(148, 163, 184, 0.45);
              border-radius: 12px;
              background: #FFFFFF;
            }
            QLabel#previewTitle {
              font-weight: 600;
              color: #0F172A;
            }
            QLabel#previewMeta {
              color: #64748B;
            }
            QLabel#previewEmpty {
              color: #94A3B8;
            }
            QTextBrowser#previewText {
              background: transparent;
              color: #0F172A;
              border: 0;
              padding: 2px 2px;
            }
            QLabel#previewImage {
              background: #F8FAFC;
              border-radius: 8px;
            }
            """
        )
