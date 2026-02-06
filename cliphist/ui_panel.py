from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from PySide6.QtCore import Qt, QMimeData, QTimer, QUrl, QRect, QPoint, QEvent
from PySide6.QtGui import QColor, QCursor, QDrag, QGuiApplication, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QSizeGrip,
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
from .text_util import (
    _RE_RTF_CTRL,
    _RE_WS,
    extract_html_fragment,
    html_to_plain_text,
    rtf_to_plain_text,
)

ROLE_ITEM = int(Qt.UserRole)
ROLE_FAV_ID = int(Qt.UserRole + 1)
ROLE_IS_FAVORITE = int(Qt.UserRole + 2)
ROLE_TITLE = int(Qt.UserRole + 3)
ROLE_SUBTITLE = int(Qt.UserRole + 4)


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
    """Extract the HTML fragment from raw CF_HTML bytes, truncated to *max_len*."""
    if not raw:
        return None
    frag = extract_html_fragment(raw)
    if frag:
        return frag.strip()[:max_len]
    try:
        s = raw.decode("utf-8", errors="ignore").strip()
        return s[:max_len]
    except Exception:
        return None


def _rtf_to_plain(raw: bytes, max_len: int = 12000) -> str:
    return rtf_to_plain_text(raw, max_len=max_len)


def _html_to_plain(s: str, max_len: int = 12000) -> str:
    return html_to_plain_text(s, max_len=max_len)


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
    _TYPE_COLORS: dict[str, QColor] = {
        "text": QColor("#E0F2FE"),
        "files": QColor("#ECFDF3"),
        "image": QColor("#FEF3C7"),
        "html": QColor("#FCE7F3"),
        "rtf": QColor("#EDE9FE"),
    }
    _DEFAULT_TYPE_COLOR = QColor("#E2E8F0")
    _TYPE_SYMBOLS: dict[str, str] = {
        "text": "T",
        "files": "F",
        "image": "I",
        "html": "H",
        "rtf": "R",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._thumb_cache: dict[int, QPixmap] = {}
        self._preview_cache: dict[int, str] = {}
        self._secondary_cache: dict[int, str] = {}

    def clear_caches(self) -> None:
        """Clear text caches when items change."""
        self._thumb_cache.clear()
        self._preview_cache.clear()
        self._secondary_cache.clear()

    @staticmethod
    def _cache_key(it: ClipboardItem) -> int:
        # Avoid hashing large bytes payloads during paint (image/html/rtf items).
        return id(it)

    def _cached_preview(self, it: ClipboardItem, max_len: int = 150) -> str:
        key = self._cache_key(it)
        cached = self._preview_cache.get(key)
        if cached is not None:
            return cached
        val = _clean_preview(it, max_len)
        self._preview_cache[key] = val
        if len(self._preview_cache) > 500:
            self._preview_cache.pop(next(iter(self._preview_cache)))
        return val

    def _cached_secondary(self, it: ClipboardItem) -> str:
        key = self._cache_key(it)
        cached = self._secondary_cache.get(key)
        if cached is not None:
            return cached
        val = _secondary_text(it)
        self._secondary_cache[key] = val
        if len(self._secondary_cache) > 500:
            self._secondary_cache.pop(next(iter(self._secondary_cache)))
        return val

    def paint(self, painter: QPainter, option, index):  # type: ignore[override]
        it: ClipboardItem | None = index.data(ROLE_ITEM)
        if it is None:
            super().paint(painter, option, index)
            return

        is_fav = bool(index.data(ROLE_IS_FAVORITE))
        painter.save()
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
                self._paint_icon_badge(painter, badge_rect, it, is_selected)
        else:
            self._paint_icon_badge(painter, badge_rect, it, is_selected)

        x0 = badge_rect.right() + 12
        x1 = rect.right() - 14 - 20
        w = max(0, x1 - x0)
        y0 = rect.top() + 8

        fm1 = option.fontMetrics
        title = str(index.data(ROLE_TITLE) or "")
        if not title:
            title = self._cached_preview(it, 150)
        title = fm1.elidedText(title, Qt.ElideRight, w)

        font_title = option.font
        font_title.setBold(True)
        painter.setFont(font_title)
        painter.setPen(fg)
        painter.drawText(QRect(x0, y0, w, fm1.height() + 2), Qt.AlignLeft | Qt.AlignVCenter, title)

        subtitle = str(index.data(ROLE_SUBTITLE) or "")
        if not subtitle:
            subtitle = self._cached_secondary(it)
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
        key = self._cache_key(it)
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
    ) -> None:
        bg = QColor(255, 255, 255, 60) if is_selected else self._type_color(it.item_type)
        painter.setBrush(bg)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(rect)

        symbol = self._TYPE_SYMBOLS.get(it.item_type, "?")
        font = painter.font()
        font.setBold(True)
        font.setPointSize(max(8, font.pointSize() - 1))
        painter.setFont(font)
        painter.setPen(QColor("#0F172A") if not is_selected else QColor("#FFFFFF"))
        painter.drawText(rect, Qt.AlignCenter, symbol)

    def _type_color(self, item_type: str) -> QColor:
        return self._TYPE_COLORS.get(item_type, self._DEFAULT_TYPE_COLOR)


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
        self._drag_pos: QPoint | None = None
        self._hover_preview = True

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

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)  # 150ms debounce
        self._filter_timer.timeout.connect(self._apply_filter)
        self._search.textChanged.connect(lambda: self._filter_timer.start())

        self._tabs = QTabWidget(card)
        self._tabs.setObjectName("tabs")

        self._list_all = _ClipListWidget(self._get_filtered_item, card)
        self._list_all.setUniformItemSizes(True)
        self._delegate_all = _ClipItemDelegate(self._list_all)
        self._list_all.setItemDelegate(self._delegate_all)
        self._list_all.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self._list_all.verticalScrollBar().setSingleStep(18)
        self._list_all.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list_all.itemActivated.connect(lambda _: self._activate_current())
        self._list_all.setMouseTracking(True)
        self._list_all.currentItemChanged.connect(lambda *_: self._update_preview())
        self._list_all.itemEntered.connect(lambda item: self._on_item_hover(self._list_all, item))
        self._list_all.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list_all.customContextMenuRequested.connect(lambda pos: self._show_context_menu(self._list_all, pos))
        self._list_all.viewport().installEventFilter(self)

        self._list_fav = _ClipListWidget(self._get_fav_filtered_item, card)
        self._list_fav.setUniformItemSizes(True)
        self._delegate_fav = _ClipItemDelegate(self._list_fav)
        self._list_fav.setItemDelegate(self._delegate_fav)
        self._list_fav.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self._list_fav.verticalScrollBar().setSingleStep(18)
        self._list_fav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list_fav.itemActivated.connect(lambda _: self._activate_current())
        self._list_fav.setMouseTracking(True)
        self._list_fav.currentItemChanged.connect(lambda *_: self._update_preview())
        self._list_fav.itemEntered.connect(lambda item: self._on_item_hover(self._list_fav, item))
        self._list_fav.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list_fav.customContextMenuRequested.connect(lambda pos: self._show_context_menu(self._list_fav, pos))
        self._list_fav.viewport().installEventFilter(self)

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

        self._preview_popup = QFrame(self, Qt.ToolTip | Qt.FramelessWindowHint)
        self._preview_popup.setObjectName("previewPopup")
        self._preview_popup.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._preview_popup.setAttribute(Qt.WA_TranslucentBackground, False)
        self._preview_popup.hide()
        self._popup_title = QLabel("预览", self._preview_popup)
        self._popup_title.setObjectName("previewTitle")
        self._popup_meta = QLabel("", self._preview_popup)
        self._popup_meta.setObjectName("previewMeta")
        popup_header = QHBoxLayout()
        popup_header.addWidget(self._popup_title)
        popup_header.addStretch(1)
        popup_header.addWidget(self._popup_meta)
        self._popup_stack = QStackedWidget(self._preview_popup)
        self._popup_empty = QLabel("暂无预览", self._preview_popup)
        self._popup_empty.setAlignment(Qt.AlignCenter)
        self._popup_empty.setObjectName("previewEmpty")
        self._popup_text = QTextBrowser(self._preview_popup)
        self._popup_text.setOpenExternalLinks(False)
        self._popup_text.setReadOnly(True)
        self._popup_text.setFrameStyle(QFrame.NoFrame)
        self._popup_text.setObjectName("previewText")
        self._popup_image = QLabel(self._preview_popup)
        self._popup_image.setAlignment(Qt.AlignCenter)
        self._popup_image.setObjectName("previewImage")
        self._popup_stack.addWidget(self._popup_empty)
        self._popup_stack.addWidget(self._popup_text)
        self._popup_stack.addWidget(self._popup_image)
        self._popup_stack.setCurrentWidget(self._popup_empty)
        popup_body = QVBoxLayout(self._preview_popup)
        popup_body.setContentsMargins(10, 10, 10, 10)
        popup_body.setSpacing(8)
        popup_body.addLayout(popup_header)
        popup_body.addWidget(self._popup_stack)
        self._popup_image_data: QImage | None = None

        # ── Row 1: Title bar ──
        title_bar = QHBoxLayout()
        title_bar.setSpacing(8)
        self._title = QLabel("剪切板历史", card)
        self._title.setObjectName("title")
        title_bar.addWidget(self._title)

        self._status = QLabel("", card)
        self._status.setObjectName("status")
        title_bar.addWidget(self._status)

        title_bar.addStretch(1)

        self._btn_minimize = QToolButton(card)
        self._btn_minimize.setObjectName("btnWinControl")
        self._btn_minimize.setIcon(self.style().standardIcon(QStyle.SP_TitleBarMinButton))
        self._btn_minimize.setToolTip("最小化")
        self._btn_minimize.setFixedSize(28, 28)
        self._btn_minimize.clicked.connect(self.showMinimized)
        title_bar.addWidget(self._btn_minimize)

        self._btn_close = QToolButton(card)
        self._btn_close.setObjectName("btnWinClose")
        self._btn_close.setIcon(self.style().standardIcon(QStyle.SP_TitleBarCloseButton))
        self._btn_close.setToolTip("关闭")
        self._btn_close.setFixedSize(28, 28)
        self._btn_close.clicked.connect(self.hide)
        title_bar.addWidget(self._btn_close)

        # ── Row 2: Toolbar (icon-only) ──
        toolbar = QHBoxLayout()
        toolbar.setSpacing(2)

        self._btn_fav = QToolButton(card)
        self._btn_fav.setObjectName("btnIcon")
        self._btn_fav.setIcon(self.style().standardIcon(QStyle.SP_DialogYesButton))
        self._btn_fav.setToolTip("收藏/取消收藏")
        self._btn_fav.setFixedSize(32, 32)
        self._btn_fav.clicked.connect(self._toggle_current_favorite)
        toolbar.addWidget(self._btn_fav)

        self._btn_up = QToolButton(card)
        self._btn_up.setObjectName("btnIcon")
        self._btn_up.setIcon(self.style().standardIcon(QStyle.SP_ArrowUp))
        self._btn_up.setToolTip("上移（仅收藏）")
        self._btn_up.setFixedSize(32, 32)
        self._btn_up.clicked.connect(lambda: self._move_favorite(-1))
        toolbar.addWidget(self._btn_up)

        self._btn_down = QToolButton(card)
        self._btn_down.setObjectName("btnIcon")
        self._btn_down.setIcon(self.style().standardIcon(QStyle.SP_ArrowDown))
        self._btn_down.setToolTip("下移（仅收藏）")
        self._btn_down.setFixedSize(32, 32)
        self._btn_down.clicked.connect(lambda: self._move_favorite(1))
        toolbar.addWidget(self._btn_down)

        self._btn_del_fav = QToolButton(card)
        self._btn_del_fav.setObjectName("btnIcon")
        self._btn_del_fav.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        self._btn_del_fav.setToolTip("删除收藏")
        self._btn_del_fav.setFixedSize(32, 32)
        self._btn_del_fav.clicked.connect(self._remove_current_favorite)
        toolbar.addWidget(self._btn_del_fav)

        self._tabs.currentChanged.connect(lambda _: self._on_tab_changed())

        self._sep1 = QFrame(card)
        self._sep1.setObjectName("toolSep")
        self._sep1.setFixedSize(1, 20)
        toolbar.addWidget(self._sep1)

        self._btn_preview_mode = QToolButton(card)
        self._btn_preview_mode.setObjectName("btnIcon")
        self._btn_preview_mode.setCheckable(True)
        self._btn_preview_mode.setChecked(True)
        self._btn_preview_mode.setIcon(self.style().standardIcon(QStyle.SP_FileDialogContentsView))
        self._btn_preview_mode.setToolTip("悬浮预览 / 底部预览")
        self._btn_preview_mode.setFixedSize(32, 32)
        self._btn_preview_mode.toggled.connect(self._set_preview_mode)
        toolbar.addWidget(self._btn_preview_mode)

        toolbar.addStretch(1)

        self._btn_clear = QToolButton(card)
        self._btn_clear.setObjectName("btnIcon")
        self._btn_clear.setIcon(self.style().standardIcon(QStyle.SP_DialogResetButton))
        self._btn_clear.setToolTip("清空历史")
        self._btn_clear.setFixedSize(32, 32)
        self._btn_clear.clicked.connect(lambda: self._on_clear() if self._on_clear else None)
        toolbar.addWidget(self._btn_clear)

        self._btn_settings = QToolButton(card)
        self._btn_settings.setObjectName("btnIcon")
        self._btn_settings.setIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))
        self._btn_settings.setToolTip("设置")
        self._btn_settings.setFixedSize(32, 32)
        self._btn_settings.clicked.connect(lambda: self._on_open_settings() if self._on_open_settings else None)
        toolbar.addWidget(self._btn_settings)

        body = QVBoxLayout(card)
        body.setContentsMargins(14, 12, 14, 14)
        body.setSpacing(8)
        body.addLayout(title_bar)
        body.addWidget(self._search)
        body.addLayout(toolbar)
        body.addWidget(self._tabs, 1)
        body.addWidget(self._preview)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.addWidget(card)

        self.setMinimumSize(460, 420)
        self.resize(640, 620)
        self._grip = QSizeGrip(card)
        self._apply_styles()
        self._sync_status()
        self._set_preview_mode(True)
        QApplication.instance().installEventFilter(self)

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self._sync_status()

    def set_items(self, items: list[ClipboardItem]) -> None:
        self._all_items = items
        self._delegate_all.clear_caches()
        self._apply_filter()

    def set_favorites(self, favorites: list[tuple[str, ClipboardItem]]) -> None:
        self._favorites = favorites
        self._delegate_fav.clear_caches()
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
        try:
            grip_size = self._grip.sizeHint()
            self._grip.move(
                self.width() - grip_size.width() - 8,
                self.height() - grip_size.height() - 8,
            )
        except Exception:
            pass
        self._render_popup_image()

    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            if event.position().y() <= 48:
                self._drag_pos = QPoint(int(event.globalPosition().x()), int(event.globalPosition().y()))
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # type: ignore[override]
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            current = QPoint(int(event.globalPosition().x()), int(event.globalPosition().y()))
            delta = current - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = current
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self._drag_pos = None
        super().mouseReleaseEvent(event)

    def eventFilter(self, obj, event):  # type: ignore[override]
        if event.type() == QEvent.Type.KeyPress:
            key = getattr(event, "key", lambda: None)()
            if key in (Qt.Key_Tab, Qt.Key_Backtab) and self._panel_has_focus():
                self._cycle_tabs(1 if key == Qt.Key_Tab else -1)
                return True

        if self._hover_preview:
            if event.type() == QEvent.Type.Leave and self._is_preview_list_viewport(obj):
                self._hide_preview_popup()
            elif event.type() == QEvent.Type.MouseMove and self._is_preview_list_viewport(obj):
                if QApplication.keyboardModifiers() & Qt.ControlModifier:
                    self._sync_hover_popup_from_cursor()
                else:
                    self._hide_preview_popup()
            elif event.type() == QEvent.Type.KeyPress and getattr(event, "key", lambda: None)() == Qt.Key_Control:
                self._sync_hover_popup_from_cursor()
            elif event.type() == QEvent.Type.KeyRelease and getattr(event, "key", lambda: None)() == Qt.Key_Control:
                self._hide_preview_popup()
        return super().eventFilter(obj, event)

    def _panel_has_focus(self) -> bool:
        if not self.isVisible():
            return False
        fw = QApplication.focusWidget()
        if fw is None:
            return False
        return fw is self or self.isAncestorOf(fw)

    def _cycle_tabs(self, step: int) -> None:
        count = self._tabs.count()
        if count <= 1:
            return
        idx = (self._tabs.currentIndex() + step) % count
        self._tabs.setCurrentIndex(idx)
        current = self._current_list()
        if current.count() > 0 and current.currentRow() < 0:
            current.setCurrentRow(0)
        current.setFocus()

    def _is_preview_list_viewport(self, obj) -> bool:
        return obj is self._list_all.viewport() or obj is self._list_fav.viewport()

    def _hover_target_under_cursor(self) -> tuple[QListWidget, QListWidgetItem] | None:
        pos = QCursor.pos()
        for widget in (self._list_all, self._list_fav):
            vp = widget.viewport()
            local = vp.mapFromGlobal(pos)
            if vp.rect().contains(local):
                it = widget.itemAt(local)
                if it is not None:
                    return widget, it
        return None

    def _sync_hover_popup_from_cursor(self) -> None:
        if not self._hover_preview or not self.isVisible():
            self._hide_preview_popup()
            return
        if not (QApplication.keyboardModifiers() & Qt.ControlModifier):
            self._hide_preview_popup()
            return
        target = self._hover_target_under_cursor()
        if target is None:
            self._hide_preview_popup()
            return
        widget, item = target
        self._on_item_hover(widget, item)

    def _apply_filter(self) -> None:
        q = (self._search.text() or "").strip().lower()
        fav_ids = {fid for fid, _ in self._favorites}
        preview_lc_cache: dict[int, str] = {}

        def _preview_lc(it: ClipboardItem) -> str:
            key = id(it)
            cached = preview_lc_cache.get(key)
            if cached is not None:
                return cached
            val = _clean_preview(it, 10_000).lower()
            preview_lc_cache[key] = val
            return val

        def _matches_query(it: ClipboardItem) -> bool:
            if q in _preview_lc(it):
                return True
            return it.item_type == "files" and any(q in p.lower() for p in (it.file_paths or ()))

        if not q:
            self._filtered_items = self._all_items[:]
            self._fav_filtered = self._favorites[:]
        else:
            self._filtered_items = [
                it
                for it in self._all_items
                if _matches_query(it)
            ]
            self._fav_filtered = [
                (fid, it)
                for fid, it in self._favorites
                if _matches_query(it)
            ]

        self._list_all.clear()
        for it in self._filtered_items:
            item = QListWidgetItem()
            item.setData(ROLE_ITEM, it)
            item.setData(ROLE_IS_FAVORITE, self._fav_id_for_item(it, fav_ids) is not None)
            item.setData(ROLE_TITLE, _clean_preview(it, 150))
            item.setData(ROLE_SUBTITLE, _secondary_text(it))
            item.setToolTip(it.preview(10_000))
            self._list_all.addItem(item)

        self._list_fav.clear()
        for fid, it in self._fav_filtered:
            item = QListWidgetItem()
            item.setData(ROLE_ITEM, it)
            item.setData(ROLE_FAV_ID, fid)
            item.setData(ROLE_IS_FAVORITE, True)
            item.setData(ROLE_TITLE, _clean_preview(it, 150))
            item.setData(ROLE_SUBTITLE, _secondary_text(it))
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
        if self._hover_preview:
            return
        if it is None:
            self._preview_meta.setText("")
            self._preview_stack.setCurrentWidget(self._preview_empty)
            self._preview_image = None
            self._preview_image_label.clear()
            return

        ts = it.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        self._preview_meta.setText(f"{it.item_type.upper()} · {ts}")

        self._render_preview_content(
            it,
            self._preview_meta,
            self._preview_text,
            self._preview_image_label,
            self._preview_stack,
            docked=True,
        )

    def _render_preview_image(self) -> None:
        if self._preview_image is None:
            return
        size = self._preview_stack.size()
        if size.width() <= 0 or size.height() <= 0:
            return
        pix = QPixmap.fromImage(self._preview_image)
        scaled = pix.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._preview_image_label.setPixmap(scaled)

    def _render_popup_image(self) -> None:
        if self._popup_image_data is None:
            return
        size = self._popup_stack.size()
        if size.width() <= 0 or size.height() <= 0:
            return
        pix = QPixmap.fromImage(self._popup_image_data)
        scaled = pix.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._popup_image.setPixmap(scaled)

    def _render_preview_content(
        self,
        it: ClipboardItem,
        meta_label: QLabel,
        text_widget: QTextBrowser,
        image_label: QLabel,
        stack: QStackedWidget,
        docked: bool,
    ) -> None:
        ts = it.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        meta_label.setText(f"{it.item_type.upper()} · {ts}")

        if it.item_type == "image":
            img = _qimage_from_dib(it.raw_bytes or b"")
            if img is None or img.isNull():
                text_widget.setPlainText("图片预览不可用")
                stack.setCurrentWidget(text_widget)
                if docked:
                    self._preview_image = None
                else:
                    self._popup_image_data = None
            else:
                if docked:
                    self._preview_image = img
                    self._render_preview_image()
                else:
                    self._popup_image_data = img
                    self._render_popup_image()
                stack.setCurrentWidget(image_label)
            return

        if docked:
            self._preview_image = None
            self._preview_image_label.clear()
        else:
            self._popup_image_data = None
            self._popup_image.clear()

        if it.item_type == "html":
            html = _html_fragment_from_clipboard(it.raw_bytes or b"")
            text = _html_to_plain(html or (it.text or ""), max_len=12000)
            text_widget.setPlainText(text or "(HTML 内容为空)")
            stack.setCurrentWidget(text_widget)
            return

        if it.item_type == "rtf":
            text = _rtf_to_plain(it.raw_bytes or b"", max_len=12000) or (it.text or "")
            text_widget.setPlainText(text)
            stack.setCurrentWidget(text_widget)
            return

        if it.item_type == "files":
            paths = it.file_paths or ()
            if paths:
                text_widget.setPlainText("\n".join(paths[:80]) + ("…\n" if len(paths) > 80 else ""))
            else:
                text_widget.setPlainText("（空文件列表）")
            stack.setCurrentWidget(text_widget)
            return

        text = it.text or ""
        if len(text) > 12000:
            text = text[:12000] + "\n…"
        text_widget.setPlainText(text)
        stack.setCurrentWidget(text_widget)

    def _on_item_hover(self, widget: QListWidget, item: QListWidgetItem) -> None:
        if not self._hover_preview:
            return
        if not (QApplication.keyboardModifiers() & Qt.ControlModifier):
            self._hide_preview_popup()
            return
        it: ClipboardItem | None = item.data(ROLE_ITEM)
        if it is None:
            return
        pos = QCursor.pos()
        self._show_preview_popup(it, pos)

    def _show_preview_popup(self, it: ClipboardItem, pos: QPoint) -> None:
        self._render_preview_content(
            it,
            self._popup_meta,
            self._popup_text,
            self._popup_image,
            self._popup_stack,
            docked=False,
        )
        popup_w = min(520, max(360, self.width() - 40))
        popup_h = 220 if it.item_type != "image" else 280
        self._preview_popup.resize(popup_w, popup_h)
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        screen_geo = screen.availableGeometry()
        x = min(max(pos.x() + 16, screen_geo.left()), screen_geo.right() - popup_w)
        y = min(max(pos.y() + 16, screen_geo.top()), screen_geo.bottom() - popup_h)
        self._preview_popup.move(x, y)
        self._preview_popup.show()

    def _hide_preview_popup(self) -> None:
        if self._preview_popup.isVisible():
            self._preview_popup.hide()

    def _set_preview_mode(self, hover: bool) -> None:
        self._hover_preview = bool(hover)
        self._preview.setVisible(not self._hover_preview)
        if self._hover_preview:
            self._update_preview()
        else:
            self._hide_preview_popup()
            self._update_preview()

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
        item_obj: ClipboardItem | None = it.data(ROLE_ITEM)
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

    def _sync_status(self) -> None:
        self._status.setText("暂停" if self._paused else "监听中")
        self._status.setProperty("paused", self._paused)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def _sync_tooltips(self) -> None:
        has_actions = bool(self._on_clear) or bool(self._on_open_settings)
        self._btn_clear.setVisible(bool(self._on_clear))
        self._btn_settings.setVisible(bool(self._on_open_settings))
        self._btn_fav.setVisible(bool(self._toggle_favorite))
        is_fav_tab = self._tabs.currentIndex() == 1
        self._btn_up.setVisible(is_fav_tab)
        self._btn_down.setVisible(is_fav_tab)
        self._btn_del_fav.setVisible(is_fav_tab and bool(self._remove_favorite))
        self._sep1.setVisible(is_fav_tab)
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
            QToolButton#btnIcon {
              font-size: 15px;
              padding: 0px;
              border-radius: 8px;
              border: 1px solid transparent;
              background: transparent;
              color: #475569;
            }
            QToolButton#btnIcon:hover {
              background: rgba(148, 163, 184, 0.22);
              border: 1px solid rgba(148, 163, 184, 0.35);
              color: #0F172A;
            }
            QToolButton#btnIcon:checked {
              background: rgba(59, 130, 246, 0.18);
              border: 1px solid rgba(37, 99, 235, 0.45);
              color: #1D4ED8;
            }
            #toolSep {
              background: rgba(148, 163, 184, 0.35);
            }
            #previewCard {
              border: 1px solid rgba(148, 163, 184, 0.45);
              border-radius: 12px;
              background: #FFFFFF;
            }
            #previewPopup {
              border: 1px solid rgba(148, 163, 184, 0.6);
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
            QToolButton#btnWinClose {
              font-size: 14px;
              font-weight: 700;
              padding: 0px;
              border-radius: 14px;
              border: none;
              background: transparent;
              color: #64748B;
            }
            QToolButton#btnWinClose:hover {
              background: rgba(239, 68, 68, 0.18);
              color: #DC2626;
            }
            QScrollBar:vertical {
              border: none;
              background: transparent;
              width: 6px;
              margin: 4px 0;
            }
            QScrollBar::handle:vertical {
              background: rgba(148, 163, 184, 0.45);
              border-radius: 3px;
              min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
              background: rgba(100, 116, 139, 0.6);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
              height: 0;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
              background: transparent;
            }
            """
        )
