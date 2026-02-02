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
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QTabWidget,
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

        is_fav = bool(index.data(Qt.UserRole + 2))
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
        x1 = rect.right() - 12 - 20
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

        if is_fav:
            painter.setPen(QColor("#F59E0B") if not is_selected else QColor("#FFFFFF"))
            painter.drawText(QRect(rect.right() - 28, rect.top(), 20, rect.height()), Qt.AlignCenter, "★")

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
        self._list_all.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list_all.customContextMenuRequested.connect(lambda pos: self._show_context_menu(self._list_all, pos))

        self._list_fav = _ClipListWidget(self._get_fav_filtered_item, card)
        self._list_fav.setUniformItemSizes(True)
        self._list_fav.setItemDelegate(_ClipItemDelegate(self._list_fav))
        self._list_fav.itemActivated.connect(lambda _: self._activate_current())
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

        header = QHBoxLayout()
        self._title = QLabel("剪切板历史", card)
        self._title.setObjectName("title")
        header.addWidget(self._title)

        self._status = QLabel("", card)
        self._status.setObjectName("status")
        header.addWidget(self._status)

        header.addStretch(1)

        self._btn_fav = QToolButton(card)
        self._btn_fav.setIcon(self.style().standardIcon(QStyle.SP_DialogYesButton))
        self._btn_fav.setToolTip("收藏/取消收藏")
        self._btn_fav.clicked.connect(self._toggle_current_favorite)
        header.addWidget(self._btn_fav)

        self._btn_up = QToolButton(card)
        self._btn_up.setIcon(self.style().standardIcon(QStyle.SP_ArrowUp))
        self._btn_up.setToolTip("上移（仅收藏）")
        self._btn_up.clicked.connect(lambda: self._move_favorite(-1))
        header.addWidget(self._btn_up)

        self._btn_down = QToolButton(card)
        self._btn_down.setIcon(self.style().standardIcon(QStyle.SP_ArrowDown))
        self._btn_down.setToolTip("下移（仅收藏）")
        self._btn_down.clicked.connect(lambda: self._move_favorite(1))
        header.addWidget(self._btn_down)

        self._btn_del_fav = QToolButton(card)
        self._btn_del_fav.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        self._btn_del_fav.setToolTip("删除收藏")
        self._btn_del_fav.clicked.connect(self._remove_current_favorite)
        header.addWidget(self._btn_del_fav)

        self._tabs.currentChanged.connect(lambda _: self._on_tab_changed())

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
        body.addWidget(self._tabs)

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
            QTabWidget::pane {
              border: 0;
            }
            QTabBar::tab {
              padding: 6px 12px;
              border-radius: 10px;
              background: rgba(15, 23, 42, 0.06);
              margin-right: 6px;
            }
            QTabBar::tab:selected {
              background: rgba(34, 179, 199, 0.25);
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
