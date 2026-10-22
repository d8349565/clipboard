from __future__ import annotations

import os

from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable

from PySide6.QtCore import (
    QObject,
    QEvent,
    QItemSelectionModel,
    QMimeData,
    QPoint,
    QRect,
    QRunnable,
    QThreadPool,
    QTimer,
    QUrl,
    Qt,
    Signal,
)
from PySide6.QtGui import QFont, QColor, QCursor, QDrag, QGuiApplication, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QAbstractItemView,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QStyle,
    QStyledItemDelegate,
    QTabWidget,
    QTabBar,
    QStackedWidget,
    QToolButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .models import ClipboardItem
from .ui_icons import type_icon
from .themes import dialog_styles, theme_colors
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

MIME_FAV_REORDER = "application/x-cliphist-fav-reorder"


_HELP_SECTIONS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "🚀", "快速上手",
        [
            ("启动", "程序启动后最小化至系统托盘，不占用任务栏。"),
            ("打开面板", "按 Alt+C（默认）或双击托盘图标，面板会在鼠标附近弹出。"),
            ("使用记录", "双击列表项或选中后按 Enter，即可将内容重新写入剪切板并自动关闭面板。"),
            ("关闭面板", "按 Esc 或点击右上角 ✕，面板隐藏到后台，程序继续监听剪切板。"),
        ],
    ),
    (
        "📋", "剪切板记录",
        [
            ("自动捕获", "复制任意内容（文字、图片、文件、HTML、RTF）后，ClipHist 自动保存至历史顶部。"),
            ("支持类型", "文本 · 链接 · 图片（位图）· 文件路径 · HTML 富文本 · RTF 富文本。"),
            ("自动去重", "连续复制相同内容只保留一条，不会重复叠加。"),
            ("容量上限", "默认保存最近 1000 条，可在设置中调整（最多 10000 条）。"),
            ("暂停监听", "按 Alt+P 或托盘菜单「暂停」，暂停期间复制的内容不会被记录。"),
        ],
    ),
    (
        "🔍", "搜索与筛选",
        [
            ("关键词搜索", "在顶部搜索框输入文字，列表实时筛选匹配内容（支持文件路径搜索）。"),
            ("聚焦搜索框", "按 Ctrl+F 快速聚焦搜索框并全选已有文字。"),
            ("类型筛选", "点击最近记录旁的「全部类型」，筛选文本、图片、文件、链接或富文本。"),
            ("组合筛选", "类别标签与关键词可同时生效，例如在「图片」类中搜索特定内容。"),
        ],
    ),
    (
        "⌨️", "快捷键一览",
        [
            ("Alt+C", "打开 / 隐藏主面板（可在设置中自定义）。"),
            ("Alt+P", "切换暂停 / 继续监听（可在设置中自定义）。"),
            ("Enter / 双击", "复制记录；启用直接粘贴后会自动粘贴到原窗口。"),
            ("Ctrl+Enter", "始终只复制，不触发直接粘贴。"),
            ("Delete", "删除选中的历史记录；收藏标签页中批量移出所选收藏。"),
            ("Esc", "隐藏面板。"),
            ("Ctrl+F", "聚焦搜索框。"),
            ("Alt+F", "收藏 / 取消收藏当前选中的记录。"),
            ("Tab / Shift+Tab", "在最近记录与收藏之间切换；Ctrl+Tab 也可切换，F6 / Shift+F6 移动控件焦点。"),
            ("空格", "列表获得焦点时，展开 / 收起选中内容预览。"),
        ],
    ),
    (
        "★", "收藏功能",
        [
            ("添加收藏", "在「最近记录」列表点击记录右侧星标；也可选中后按 Alt+F 或使用右键菜单。"),
            ("查看收藏", "点击「收藏」标签页，收藏内容持久保存，不受历史上限影响。"),
            ("批量管理", "收藏页支持 Ctrl / Shift 多选；点击「移出收藏」可一次处理多条内容。"),
            ("调整顺序", "在「收藏」列表按住鼠标左键拖动记录，蓝色指示线位置松开即可排序；也可「置顶」或右键「上移 / 下移」。"),
            ("编辑文本", "对文本类收藏右键选择「编辑文本」，可直接修改内容后保存。"),
        ],
    ),
    (
        "👁", "预览功能",
        [
            ("内容预览", "默认收起以留出列表空间；点击底部「预览」或列表中按空格展开，再次操作收起。"),
            ("小窗口", "主动展开后使用浮动预览，鼠标经过记录不会自动弹出内容。"),
            ("图片预览", "图片类记录在列表中显示缩略图，预览区显示完整图片并随窗口自动缩放。"),
            ("富文本", "HTML / RTF 内容自动转换为纯文本预览，防止渲染异常。"),
        ],
    ),
    (
        "⚙️", "设置说明",
        [
            ("打开设置", "点击工具栏右侧齿轮按钮，或托盘右键菜单「设置」。"),
            ("自定义热键", "支持 Ctrl / Alt / Shift / Win 与字母、数字、方向键、F1–F24 的组合。"),
            ("历史条数", "最大保存条数（50–10000），超出后自动淘汰最旧的记录。"),
            ("开机自启", "勾选后在 Windows 启动文件夹创建快捷方式，开机自动运行。"),
            ("窗口大小", "可设置面板初始宽度（400–1600 px）和高度（440–1200 px），保存后立即生效。"),
            ("持久化存储", "托盘菜单「启用持久化」后，历史写入 SQLite 数据库，重启不丢失。"),
        ],
    ),
    (
        "💡", "实用技巧",
        [
            ("拖拽使用", "直接把列表项拖拽到其他应用（文本→编辑器、图片→画图软件等）。"),
            ("移动窗口", "拖动面板顶部标题栏可移动窗口；右下角拖动手柄可调整大小。"),
            ("分页浏览", "历史记录过多时，列表底部显示分页控件，可快速跳转到上一页 / 下一页。"),
            ("清空历史", "在更多菜单中选择「清空历史」；收藏内容不受影响。"),
            ("数据位置", "配置文件和数据库位于 %APPDATA%\\ClipHist，可手动备份或迁移。"),
        ],
    ),
]


class HelpDialog(QDialog):
    """使用指南对话框。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("使用指南")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self._drag_pos: QPoint | None = None

        card = QFrame(self)
        card.setObjectName("helpCard")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 100))
        card.setGraphicsEffect(shadow)

        # ── Title bar ──
        title_bar = QHBoxLayout()
        title_bar.setSpacing(8)

        icon_lbl = QLabel("?", card)
        icon_lbl.setObjectName("helpIcon")
        icon_lbl.setFixedSize(32, 32)
        icon_lbl.setAlignment(Qt.AlignCenter)
        title_bar.addWidget(icon_lbl)

        title_lbl = QLabel("ClipHist 使用指南", card)
        title_lbl.setObjectName("helpTitle")
        title_bar.addWidget(title_lbl)
        title_bar.addStretch(1)

        ver_lbl = QLabel(f"v{__version__}", card)
        ver_lbl.setObjectName("helpVer")
        title_bar.addWidget(ver_lbl)

        btn_close = QToolButton(card)
        btn_close.setObjectName("btnWinControl")
        btn_close.setText("✕")
        btn_close.setFixedSize(28, 28)
        btn_close.clicked.connect(self.accept)
        title_bar.addWidget(btn_close)

        # ── Separator ──
        sep = QFrame(card)
        sep.setFrameShape(QFrame.HLine)
        sep.setObjectName("helpSep")

        # ── Scrollable content ──
        scroll = QScrollArea(card)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setObjectName("helpScroll")

        content = QWidget()
        content.setObjectName("helpContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(4, 4, 8, 4)
        content_layout.setSpacing(10)

        for emoji, section_title, items in _HELP_SECTIONS:
            content_layout.addWidget(self._build_section(content, emoji, section_title, items))

        content_layout.addStretch(1)
        content.setLayout(content_layout)
        scroll.setWidget(content)

        # ── Footer ──
        footer = QLabel(
            "数据目录：<b>%APPDATA%\\ClipHist</b>　·　托盘右键菜单可快速访问所有功能",
            card,
        )
        footer.setObjectName("helpFooter")
        footer.setWordWrap(True)

        body = QVBoxLayout(card)
        body.setContentsMargins(20, 16, 20, 18)
        body.setSpacing(10)
        body.addLayout(title_bar)
        body.addWidget(sep)
        body.addWidget(scroll, 1)
        body.addWidget(footer)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.addWidget(card)

        self.resize(560, 640)
        self._apply_styles()

    def _build_section(self, parent: QWidget, emoji: str, title: str, items: list[tuple[str, str]]) -> QFrame:
        frame = QFrame(parent)
        frame.setObjectName("helpSection")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(7)

        header = QHBoxLayout()
        header.setSpacing(7)
        emoji_lbl = QLabel(emoji, frame)
        emoji_lbl.setObjectName("helpEmoji")
        emoji_lbl.setFixedWidth(22)
        title_lbl = QLabel(title, frame)
        title_lbl.setObjectName("helpSectionTitle")
        header.addWidget(emoji_lbl)
        header.addWidget(title_lbl)
        header.addStretch(1)
        layout.addLayout(header)

        for term, desc in items:
            row = QHBoxLayout()
            row.setSpacing(10)
            row.setContentsMargins(4, 0, 0, 0)
            term_lbl = QLabel(term, frame)
            term_lbl.setObjectName("helpTerm")
            term_lbl.setFixedWidth(86)
            term_lbl.setAlignment(Qt.AlignRight | Qt.AlignTop)
            term_lbl.setWordWrap(False)
            desc_lbl = QLabel(desc, frame)
            desc_lbl.setObjectName("helpDesc")
            desc_lbl.setWordWrap(True)
            row.addWidget(term_lbl)
            row.addWidget(desc_lbl, 1)
            layout.addLayout(row)

        return frame

    def _apply_styles(self) -> None:
        mode = getattr(self.parentWidget(), "_theme_mode", "system")
        self.setStyleSheet(dialog_styles(mode))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.parent() is not None:
            p = self.parent()
            cx = p.x() + (p.width() - self.width()) // 2
            cy = p.y() + (p.height() - self.height()) // 2
            self.move(cx, cy)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and int(event.position().y()) <= 52:
            self._drag_pos = QPoint(int(event.globalPosition().x()), int(event.globalPosition().y()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            cur = QPoint(int(event.globalPosition().x()), int(event.globalPosition().y()))
            self.move(self.pos() + cur - self._drag_pos)
            self._drag_pos = cur
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = None
        super().mouseReleaseEvent(event)


class _ClipListWidget(QListWidget):
    def __init__(self, get_item: Callable[[int], ClipboardItem | None], parent: QWidget | None = None,
                 load_blobs: Callable[[ClipboardItem], ClipboardItem] | None = None,
                 on_reorder: Callable[[str, str, bool], None] | None = None) -> None:
        super().__init__(parent)
        self._get_item = get_item
        self._load_blobs = load_blobs
        self._on_reorder = on_reorder
        self._drop_line: QWidget | None = None
        self.setDragEnabled(True)
        if on_reorder is not None:
            self.setAcceptDrops(True)

    def _reorder_id_at(self, row: int) -> str | None:
        item = self.item(row)
        if item is None:
            return None
        fav_id = item.data(ROLE_FAV_ID)
        return fav_id if isinstance(fav_id, str) else None

    def _reorder_anchor_at(self, pos: QPoint) -> tuple[str, bool, int] | None:
        """Return (anchor fav id, insert after anchor, indicator y) for a drop at pos."""
        item = self.itemAt(pos)
        if item is None:
            first = self.item(0)
            last = self.item(self.count() - 1)
            if first is None or last is None:
                return None
            first_rect = self.visualItemRect(first)
            if pos.y() < first_rect.top():
                item, after, y = first, False, first_rect.top()
            else:
                last_rect = self.visualItemRect(last)
                item, after, y = last, True, last_rect.bottom() + 1
        else:
            rect = self.visualItemRect(item)
            after = pos.y() >= rect.top() + rect.height() / 2
            y = rect.bottom() + 1 if after else rect.top()
        fav_id = item.data(ROLE_FAV_ID)
        if not isinstance(fav_id, str):
            return None
        return fav_id, after, y

    def _show_drop_line(self, y: int) -> None:
        if self._drop_line is None:
            line = QWidget(self.viewport())
            line.setAttribute(Qt.WA_TransparentForMouseEvents)
            line.setFixedHeight(2)
            line.setStyleSheet(f"background: {self.palette().highlight().color().name()};")
            self._drop_line = line
        self._drop_line.setGeometry(0, max(0, y - 1), self.viewport().width(), 2)
        self._drop_line.show()
        self._drop_line.raise_()

    def _hide_drop_line(self) -> None:
        if self._drop_line is not None:
            self._drop_line.hide()

    def _start_reorder_drag(self, reorder_id: str | None) -> None:
        """Fallback drag carrying only the reorder marker when no payload can be built."""
        if reorder_id is None:
            return
        mime = QMimeData()
        mime.setData(MIME_FAV_REORDER, reorder_id.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction | Qt.CopyAction)

    def startDrag(self, supportedActions):  # type: ignore[override]
        row = self.currentRow()
        it = self._get_item(row)
        if it is None:
            return
        reorder_id = self._reorder_id_at(row)

        # Load blobs on-demand for image drag
        if it.needs_blob_load and self._load_blobs is not None:
            it = self._load_blobs(it)
            # The UI blob loader schedules a background read and returns the
            # slim item on a cache miss.  Do not start a drag with an empty
            # image payload; retry after the pending load has completed.
            if it is None or it.needs_blob_load:
                self._start_reorder_drag(reorder_id)
                return

        mime = QMimeData()
        image = None
        copy_only = False
        if it.item_type in ("text", "html", "rtf"):
            mime.setText(it.text or "")
        elif it.item_type == "files":
            urls = [QUrl.fromLocalFile(p) for p in (it.file_paths or ())]
            mime.setUrls(urls)
            copy_only = True
        elif it.item_type == "image":
            image = _qimage_from_dib(it.raw_bytes or b"")
            if image is None or image.isNull():
                self._start_reorder_drag(reorder_id)
                return
            mime.setImageData(image)
        else:
            self._start_reorder_drag(reorder_id)
            return

        if reorder_id is not None:
            mime.setData(MIME_FAV_REORDER, reorder_id.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        if image is not None:
            drag.setPixmap(QPixmap.fromImage(image).scaled(128, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        # File drags should always behave like copy to avoid moving source files.
        if copy_only:
            drag.exec(Qt.CopyAction)
        else:
            drag.exec(supportedActions)

    def dragEnterEvent(self, event):  # type: ignore[override]
        if self._on_reorder is not None and event.mimeData().hasFormat(MIME_FAV_REORDER):
            if event.possibleActions() & Qt.MoveAction:
                event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):  # type: ignore[override]
        if self._on_reorder is None or not event.mimeData().hasFormat(MIME_FAV_REORDER):
            event.ignore()
            return
        pos = event.position().toPoint()
        anchor = self._reorder_anchor_at(pos)
        if anchor is None:
            self._hide_drop_line()
            event.ignore()
            return
        self._show_drop_line(anchor[2])
        if event.possibleActions() & Qt.MoveAction:
            event.setDropAction(Qt.MoveAction)
        event.accept()
        bar = self.verticalScrollBar()
        if pos.y() < 20:
            bar.setValue(bar.value() - 16)
        elif pos.y() > self.viewport().height() - 20:
            bar.setValue(bar.value() + 16)

    def dragLeaveEvent(self, event):  # type: ignore[override]
        self._hide_drop_line()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):  # type: ignore[override]
        self._hide_drop_line()
        if self._on_reorder is None or not event.mimeData().hasFormat(MIME_FAV_REORDER):
            event.ignore()
            return
        anchor = self._reorder_anchor_at(event.position().toPoint())
        if anchor is None:
            event.ignore()
            return
        try:
            src_id = bytes(event.mimeData().data(MIME_FAV_REORDER)).decode("utf-8")
        except UnicodeDecodeError:
            event.ignore()
            return
        anchor_id, after, _y = anchor
        if event.possibleActions() & Qt.MoveAction:
            event.setDropAction(Qt.MoveAction)
        event.accept()
        if anchor_id != src_id:
            self._on_reorder(src_id, anchor_id, after)


def _qimage_from_dib(dib: bytes) -> QImage | None:
    if not dib:
        return None
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
        img = QImage.fromData(bmp_bytes, "BMP")
        if img is None or img.isNull():
            return None
        return img
    except Exception:
        return None


@dataclass(frozen=True, slots=True)
class _ImageDecodeRequest:
    key: tuple
    raw: bytes
    width: int
    height: int
    dpr: float


@dataclass(frozen=True, slots=True)
class _ImageDecodeResult:
    key: tuple
    image: QImage | None
    dpr: float
    error: str | None = None


class _ImageDecodeSignals(QObject):
    finished = Signal(object)


class _ImageDecodeTask(QRunnable):
    """Decode and scale one DIB away from the GUI thread."""

    def __init__(self, request: _ImageDecodeRequest) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self.request = request
        self.signals = _ImageDecodeSignals()

    def run(self) -> None:  # type: ignore[override]
        request = self.request
        try:
            image = _qimage_from_dib(request.raw)
            if image is None or image.isNull():
                raise ValueError("图片数据不可用")
            image = image.scaled(
                request.width,
                request.height,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            if image.isNull():
                raise ValueError("图片缩放失败")
            image.setDevicePixelRatio(request.dpr)
            result = _ImageDecodeResult(request.key, image, request.dpr)
        except Exception as exc:
            result = _ImageDecodeResult(request.key, None, request.dpr, str(exc))
        self.signals.finished.emit(result)


class _ImageDecodeQueue(QObject):
    """Bounded image decode queue whose cache is owned by the UI thread."""

    _MAX_ACTIVE = 2
    _MAX_IN_FLIGHT = 32
    _MAX_IN_FLIGHT_BYTES = 64 * 1024 * 1024
    _MAX_CACHE_ENTRIES = 384
    _MAX_CACHE_BYTES = 48 * 1024 * 1024
    _MAX_FAILED_ENTRIES = 128

    def __init__(
        self,
        on_ready: Callable[[tuple, QPixmap | None], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_ready = on_ready
        self._pool = QThreadPool.globalInstance()
        self._pending: OrderedDict[tuple, _ImageDecodeRequest] = OrderedDict()
        self._active: dict[tuple, _ImageDecodeTask] = {}
        self._inflight_bytes = 0
        self._cache: OrderedDict[tuple, QPixmap] = OrderedDict()
        self._cache_bytes = 0
        self._failed: OrderedDict[tuple, None] = OrderedDict()
        self._closed = False

    @staticmethod
    def make_key(source_key: tuple, kind: str, width: int, height: int, dpr: float) -> tuple:
        return (
            "image",
            kind,
            source_key,
            max(1, int(width)),
            max(1, int(height)),
            round(max(1.0, float(dpr)), 3),
        )

    @staticmethod
    def _pixmap_bytes(pixmap: QPixmap) -> int:
        try:
            value = int(pixmap.sizeInBytes())
            if value > 0:
                return value
        except Exception:
            pass
        return max(1, pixmap.width() * pixmap.height() * 4)

    def request(
        self,
        key: tuple,
        raw: bytes,
        width: int,
        height: int,
        dpr: float,
    ) -> QPixmap | None:
        """Return a cached pixmap or enqueue one without doing image work here."""
        if self._closed or not raw:
            return None

        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        if key in self._failed or key in self._pending or key in self._active:
            return None

        # Keep the submitted work bounded. Old queued rows are less useful than
        # the row the user is looking at now; running tasks are never cancelled.
        raw_size = len(raw)
        if raw_size > self._MAX_IN_FLIGHT_BYTES:
            return None
        while (
            len(self._pending) + len(self._active) >= self._MAX_IN_FLIGHT
            or self._inflight_bytes + raw_size > self._MAX_IN_FLIGHT_BYTES
        ):
            if not self._pending:
                return None
            _, dropped = self._pending.popitem(last=False)
            self._inflight_bytes -= len(dropped.raw)

        self._pending[key] = _ImageDecodeRequest(
            key=key,
            raw=raw,
            width=max(1, int(width * max(1.0, float(dpr)))),
            height=max(1, int(height * max(1.0, float(dpr)))),
            dpr=max(1.0, float(dpr)),
        )
        self._inflight_bytes += raw_size
        self._pump()
        return None

    def cached(self, key: tuple) -> QPixmap | None:
        """Return a decoded image without requiring its source blob."""
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
        return cached

    def _pump(self) -> None:
        if self._closed:
            return
        while len(self._active) < self._MAX_ACTIVE and self._pending:
            key, request = self._pending.popitem(last=False)
            task = _ImageDecodeTask(request)
            task.signals.finished.connect(self._task_finished, Qt.QueuedConnection)
            self._active[key] = task
            try:
                self._pool.start(task)
            except Exception as exc:
                self._active.pop(key, None)
                self._inflight_bytes -= len(request.raw)
                self._failed[key] = None
                self._failed.move_to_end(key)
                self._trim_failed()
                try:
                    self._on_ready(key, None)
                except Exception:
                    pass
                # Continue pumping any remaining queued requests.
                if exc:
                    continue

    def _trim_failed(self) -> None:
        while len(self._failed) > self._MAX_FAILED_ENTRIES:
            self._failed.popitem(last=False)

    def _task_finished(self, result: _ImageDecodeResult) -> None:
        key = result.key
        task = self._active.pop(key, None)
        if task is not None:
            self._inflight_bytes -= len(task.request.raw)
        if self._closed:
            return

        pixmap: QPixmap | None = None
        if result.image is None or result.image.isNull():
            self._failed[key] = None
            self._failed.move_to_end(key)
            self._trim_failed()
        else:
            try:
                # This is deliberately the only QPixmap conversion in the
                # worker pipeline, and this slot always runs in the UI thread.
                pixmap = QPixmap.fromImage(result.image)
                pixmap.setDevicePixelRatio(result.dpr)
                if pixmap.isNull():
                    pixmap = None
            except Exception:
                pixmap = None
            if pixmap is None:
                self._failed[key] = None
                self._failed.move_to_end(key)
                self._trim_failed()
            else:
                previous = self._cache.pop(key, None)
                if previous is not None:
                    self._cache_bytes -= self._pixmap_bytes(previous)
                self._cache[key] = pixmap
                self._cache_bytes += self._pixmap_bytes(pixmap)
                while (
                    len(self._cache) > self._MAX_CACHE_ENTRIES
                    or self._cache_bytes > self._MAX_CACHE_BYTES
                ) and len(self._cache) > 1:
                    _, evicted = self._cache.popitem(last=False)
                    self._cache_bytes -= self._pixmap_bytes(evicted)

        try:
            self._on_ready(key, pixmap)
        except Exception:
            # A panel can be closing while a queued completion is delivered.
            pass
        self._pump()

    def close(self) -> None:
        """Drop queued work and ignore completions from already running tasks."""
        self._closed = True
        self._inflight_bytes -= sum(len(request.raw) for request in self._pending.values())
        self._pending.clear()
        self._on_ready = lambda *_: None

    def reopen(self) -> None:
        self._closed = False


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


def _favorite_title(item: ClipboardItem, max_len: int = 150) -> str:
    """收藏列表中文件项只显示文件名，不显示完整路径。"""
    if item.item_type == "files":
        paths = item.file_paths or ()
        if paths:
            name = os.path.basename(paths[0].rstrip("\\/")) or paths[0]
            if len(paths) > 1:
                name = f"{name} +{len(paths) - 1}"
            return name if len(name) <= max_len else name[: max_len - 1] + "…"
    if item.item_type == "image":
        return "图片"
    return _clean_preview(item, max_len)


def _secondary_text(item: ClipboardItem) -> str:
    kind = _ClipItemDelegate._TYPE_LABELS.get(item.display_type, "内容")
    if item.item_type == "files":
        return f"文件 · {len(item.file_paths or ())} 个文件"
    if item.item_type in ("image", "html", "rtf"):
        size = item.raw_size or len(item.raw_bytes or b"")
        return f"{kind} · {max(1, size // 1024):,} KB" if size else kind
    return f"{kind} · {len(item.text or ''):,} 字"


@dataclass(frozen=True, slots=True)
class _RowModel:
    title: str
    subtitle: str
    item_type: str


class _ClipItemDelegate(QStyledItemDelegate):
    _TYPE_LABELS: dict[str, str] = {
        "text": "文本",
        "files": "文件",
        "image": "图片",
        "html": "HTML",
        "rtf": "RTF",
        "link": "链接",
    }

    def __init__(
        self,
        parent: QWidget | None = None,
        load_blobs: Callable[[ClipboardItem], ClipboardItem] | None = None,
        request_thumbnail: Callable[[ClipboardItem, int, float], QPixmap | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._load_blobs = load_blobs
        self._request_thumbnail = request_thumbnail
        self._preview_cache: dict[int, str] = {}
        self._secondary_cache: dict[int, str] = {}
        self._colors = theme_colors()

    def clear_caches(self, keep_thumbnails: bool = False) -> None:
        """Clear text caches when items change."""
        self._preview_cache.clear()
        self._secondary_cache.clear()

    @staticmethod
    def _cache_key(it: ClipboardItem) -> int:
        # Avoid hashing large bytes payloads during paint (image/html/rtf items).
        if it.db_id is not None:
            return hash(("db", it.db_id))
        if it._fingerprint:
            return hash(("fp", it._fingerprint))
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

    def set_colors(self, colors: dict[str, str]) -> None:
        self._colors = colors

    def paint(self, painter: QPainter, option, index):  # type: ignore[override]
        it: ClipboardItem | None = index.data(ROLE_ITEM)
        if it is None:
            super().paint(painter, option, index)
            return
        painter.save()
        rect = option.rect
        colors = self._colors
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        painter.fillRect(rect, QColor(colors["selection"] if selected else colors["hover"] if hovered else colors["surface"]))
        if selected:
            painter.fillRect(QRect(rect.left(), rect.top() + 4, 3, rect.height() - 8), QColor(colors["accent"]))
        if option.state & QStyle.State_HasFocus:
            painter.setPen(QColor(colors["accent"]))
            painter.drawRect(rect.adjusted(1, 1, -2, -2))

        badge_rect = QRect(rect.left() + 12, rect.top() + (rect.height() - 38) // 2, 40, 38)
        thumb = self._image_thumb(it, 40) if it.item_type == "image" else None
        if thumb is not None:
            clip = QPainterPath()
            clip.addRoundedRect(badge_rect, 6, 6)
            painter.setClipPath(clip)
            tw, th = thumb.width() / thumb.devicePixelRatio(), thumb.height() / thumb.devicePixelRatio()
            painter.drawPixmap(QPoint(int(badge_rect.center().x() - tw / 2), int(badge_rect.center().y() - th / 2)), thumb)
            painter.setClipping(False)
        else:
            self._paint_type_icon(painter, badge_rect, it.display_type)

        created = it.created_at.astimezone()
        now = datetime.now().astimezone()
        timestamp = created.strftime("%H:%M" if created.date() == now.date() else "%m-%d")
        time_width = 54
        x0 = badge_rect.right() + 12
        width = max(0, rect.right() - 46 - time_width - 12 - x0)
        title = str(index.data(ROLE_TITLE) or self._cached_preview(it, 150))
        font = option.font
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(colors["text"]))
        metrics = painter.fontMetrics()
        painter.drawText(QRect(x0, rect.top() + 12, width, 22), Qt.AlignLeft | Qt.AlignVCenter, metrics.elidedText(title, Qt.ElideRight, width))
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(colors["muted"]))
        subtitle = str(index.data(ROLE_SUBTITLE) or self._cached_secondary(it))
        painter.drawText(QRect(x0, rect.top() + 39, width, 18), Qt.AlignLeft | Qt.AlignVCenter, painter.fontMetrics().elidedText(subtitle, Qt.ElideRight, width))
        painter.drawText(QRect(rect.right() - 46 - time_width, rect.top(), time_width, rect.height()), Qt.AlignRight | Qt.AlignVCenter, timestamp)
        favorite = bool(index.data(ROLE_IS_FAVORITE))
        dpr = self.parent().devicePixelRatioF() if self.parent() is not None else 1.0
        star = type_icon("star", colors["accent"] if favorite else colors["muted"], 20, dpr, favorite)
        painter.drawPixmap(QPoint(rect.right() - 33, rect.top() + (rect.height() - 20) // 2), star)
        painter.restore()

    def sizeHint(self, option, index):  # type: ignore[override]
        base = super().sizeHint(option, index)
        return base.expandedTo(base.__class__(base.width(), 72))

    def _image_thumb(self, it: ClipboardItem, size: int) -> QPixmap | None:
        if self._request_thumbnail is None:
            return None

        # 按屏幕设备像素比渲染高清缩略图，避免在高 DPI 屏上被放大导致模糊。
        dpr = 1.0
        parent = self.parent()
        if parent is not None:
            try:
                dpr = float(parent.devicePixelRatioF())
            except Exception:
                dpr = 1.0
        if dpr < 1.0:
            dpr = 1.0
        # Paint must stay allocation and decode free.  The panel callback only
        # enqueues work and returns a cached pixmap when one is already ready.
        return self._request_thumbnail(it, size, dpr)

    def _paint_type_icon(self, painter: QPainter, rect: QRect, display_type: str) -> None:
        dpr = self.parent().devicePixelRatioF() if self.parent() is not None else 1.0
        pixmap = type_icon(display_type, self._colors["muted"], 24, dpr)
        painter.drawPixmap(QPoint(rect.center().x() - 12, rect.center().y() - 12), pixmap)


class ClipPanel(QWidget):
    _PAGE_SIZE = 100

    def __init__(
        self,
        on_activate: Callable[[ClipboardItem, bool | None], None],
        on_clear: Callable[[], None] | None = None,
        on_open_settings: Callable[[], None] | None = None,
        get_favorites: Callable[[], list[tuple[str, ClipboardItem]]] | None = None,
        toggle_favorite: Callable[[ClipboardItem], tuple[bool, str | None]] | None = None,
        remove_favorite: Callable[[str], tuple[bool, str | None]] | None = None,
        remove_favorites: Callable[[list[str]], tuple[bool, str | None]] | None = None,
        reorder_favorites: Callable[[list[str]], tuple[bool, str | None]] | None = None,
        edit_item: Callable[[ClipboardItem, ClipboardItem], tuple[bool, str | None]] | None = None,
        delete_items: Callable[[list[ClipboardItem]], tuple[bool, str | None]] | None = None,
        load_blobs: Callable[[ClipboardItem], ClipboardItem] | None = None,
        on_toggle_pause: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._on_toggle_pause = on_toggle_pause
        self._theme_mode = "system"
        self._auto_paste = False
        self._ignore_star_release = False
        self._last_filter = ("", "all")
        self._on_activate = on_activate
        self._on_clear = on_clear
        self._on_open_settings = on_open_settings
        self._get_favorites = get_favorites
        self._toggle_favorite = toggle_favorite
        self._remove_favorite = remove_favorite
        self._remove_favorites = remove_favorites
        self._reorder_favorites = reorder_favorites
        self._edit_item = edit_item
        self._delete_items = delete_items
        self._load_blobs = load_blobs
        self._all_items: list[ClipboardItem] = []
        self._filtered_items: list[ClipboardItem] = []
        self._favorites: list[tuple[str, ClipboardItem]] = []
        self._fav_filtered: list[tuple[str, ClipboardItem]] = []
        self._search_cache: dict[tuple, str] = {}
        self._all_page = 0
        self._fav_page = 0
        self._paused = False
        self._drag_pos: QPoint | None = None
        self._hover_preview = True
        self._active_category: str = "all"  # "all", "text", "image", "files", "link", "html", "rtf"
        self._panel_closed = False
        self._visual_blob_pending: set[tuple] = set()
        self._visual_blob_waiting: set[tuple] = set()
        self._visual_blob_cache: OrderedDict[tuple, ClipboardItem] = OrderedDict()
        self._visual_blob_cache_bytes = 0
        self._visual_blob_cache_limit = 16 * 1024 * 1024
        self._image_decode = _ImageDecodeQueue(self._on_image_decode_ready, self)
        self._preview_image: QPixmap | None = None
        self._preview_image_key: tuple | None = None
        self._popup_image_data: QPixmap | None = None
        self._popup_image_key: tuple | None = None
        self._popup_item: ClipboardItem | None = None

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
        self._search.setPlaceholderText("搜索复制过的内容…")
        self._search.setAccessibleName("搜索剪贴板历史")
        self._search.setMinimumHeight(44)
        self._search.setToolTip("输入关键词搜索；Ctrl+F 聚焦搜索")
        self._search.installEventFilter(self)
        self._search.setClearButtonEnabled(True)

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(40)  # Coalesce typing without a noticeable search delay.
        self._filter_timer.timeout.connect(self._apply_filter)
        self._search.textChanged.connect(lambda: self._filter_timer.start())

        # Tabs and type filter share one row to leave more room for records.
        self._tabs = QTabWidget(card)
        self._tabs.setObjectName("tabs")
        self._tabs.setMinimumHeight(64)
        self._navigation = QTabBar(card)
        self._navigation.setObjectName("navigation")
        self._navigation.setExpanding(False)
        self._navigation.setDrawBase(False)
        self._navigation.addTab("最近记录 0")
        self._navigation.addTab("收藏 0")
        self._navigation.currentChanged.connect(self._tabs.setCurrentIndex)
        self._tabs.currentChanged.connect(self._navigation.setCurrentIndex)

        self._btn_more_types = QToolButton(card)
        self._btn_more_types.setObjectName("btnCategoryChip")
        self._btn_more_types.setText("全部类型")
        self._btn_more_types.setCheckable(True)
        self._btn_more_types.setFixedHeight(34)
        self._btn_more_types.setToolTip("筛选记录类型")
        self._btn_more_types.setPopupMode(QToolButton.InstantPopup)
        type_menu = QMenu(self._btn_more_types)
        self._type_actions = {}
        self._type_labels = {"all": "全部类型", "text": "文本", "image": "图片", "files": "文件", "link": "链接", "html": "HTML", "rtf": "RTF"}
        for key, label in self._type_labels.items():
            action = type_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(key == "all")
            action.triggered.connect(lambda checked, k=key: self._set_category(k))
            self._type_actions[key] = action
        self._btn_more_types.setMenu(type_menu)

        self._list_all = _ClipListWidget(self._get_filtered_item, card, load_blobs=self._load_blobs)
        self._list_all.setUniformItemSizes(True)
        self._list_all.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._delegate_all = _ClipItemDelegate(
            self._list_all,
            load_blobs=self._load_blobs,
            request_thumbnail=self._request_thumbnail,
        )
        self._list_all.setItemDelegate(self._delegate_all)
        self._list_all.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self._list_all.verticalScrollBar().setSingleStep(18)
        self._list_all.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list_all.itemActivated.connect(lambda _: self._activate_current())
        self._list_all.setMouseTracking(True)
        self._list_all.currentItemChanged.connect(lambda *_: self._update_preview())
        self._list_all.currentItemChanged.connect(lambda *_: self._sync_tooltips())
        self._list_all.itemSelectionChanged.connect(self._sync_tooltips)
        self._list_all.installEventFilter(self)
        self._list_all.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list_all.customContextMenuRequested.connect(lambda pos: self._show_context_menu(self._list_all, pos))
        self._list_all.viewport().installEventFilter(self)

        self._list_fav = _ClipListWidget(
            self._get_fav_filtered_item,
            card,
            load_blobs=self._load_blobs,
            on_reorder=self._on_fav_reorder_drop if self._reorder_favorites is not None else None,
        )
        self._list_fav.setUniformItemSizes(True)
        self._list_fav.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._delegate_fav = _ClipItemDelegate(
            self._list_fav,
            load_blobs=self._load_blobs,
            request_thumbnail=self._request_thumbnail,
        )
        self._list_fav.setItemDelegate(self._delegate_fav)
        self._list_fav.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self._list_fav.verticalScrollBar().setSingleStep(18)
        self._list_fav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list_fav.itemActivated.connect(lambda _: self._activate_current())
        self._list_fav.setMouseTracking(True)
        self._list_fav.currentItemChanged.connect(lambda *_: self._update_preview())
        self._list_fav.currentItemChanged.connect(lambda *_: self._sync_tooltips())
        self._list_fav.itemSelectionChanged.connect(self._sync_tooltips)
        self._list_fav.installEventFilter(self)
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

        self._tabs.addTab(tab_all, "最近记录")
        self._tabs.addTab(tab_fav, "收藏")
        self._tabs.tabBar().hide()

        pager = QHBoxLayout()
        pager.setContentsMargins(0, 0, 0, 0)
        pager.addStretch(1)

        self._btn_prev_page = QToolButton(card)
        self._btn_prev_page.setObjectName("btnIcon")
        self._btn_prev_page.setIcon(self.style().standardIcon(QStyle.SP_ArrowBack))
        self._btn_prev_page.setToolTip("上一页")
        self._btn_prev_page.setFixedSize(28, 28)
        self._btn_prev_page.clicked.connect(lambda: self._change_page(-1))
        pager.addWidget(self._btn_prev_page)

        self._page_label = QLabel("第 0/0 页", card)
        self._page_label.setObjectName("pageLabel")
        pager.addWidget(self._page_label)

        self._btn_next_page = QToolButton(card)
        self._btn_next_page.setObjectName("btnIcon")
        self._btn_next_page.setIcon(self.style().standardIcon(QStyle.SP_ArrowForward))
        self._btn_next_page.setToolTip("下一页")
        self._btn_next_page.setFixedSize(28, 28)
        self._btn_next_page.clicked.connect(lambda: self._change_page(1))
        pager.addWidget(self._btn_next_page)

        self._preview = QFrame(card)
        self._preview.setObjectName("previewCard")
        self._preview.setMinimumHeight(104)
        self._preview.setMaximumHeight(176)

        preview_header = QHBoxLayout()
        self._preview_title = QLabel("内容预览", self._preview)
        self._preview_title.setObjectName("previewTitle")
        preview_header.addWidget(self._preview_title)
        preview_header.addStretch(1)
        self._preview_meta = QLabel("", self._preview)
        self._preview_meta.setObjectName("previewMeta")
        preview_header.addWidget(self._preview_meta)
        self._btn_collapse_preview = QToolButton(self._preview)
        self._btn_collapse_preview.setText("收起")
        self._btn_collapse_preview.setObjectName("btnText")
        self._btn_collapse_preview.setToolTip("收起内容预览，仍可通过预览按钮展开")
        self._btn_collapse_preview.clicked.connect(lambda: self._set_preview_mode(True))
        preview_header.addWidget(self._btn_collapse_preview)

        self._preview_stack = QStackedWidget(self._preview)
        self._preview_empty = QLabel("选择一条记录以预览内容", self._preview)
        self._preview_empty.setAlignment(Qt.AlignCenter)
        self._preview_empty.setObjectName("previewEmpty")

        self._preview_text = QTextBrowser(self._preview)
        self._preview_text.setOpenExternalLinks(False)
        self._preview_text.setReadOnly(True)
        self._preview_text.setTabChangesFocus(True)
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

        self._preview_resize_timer = QTimer(self)
        self._preview_resize_timer.setSingleShot(True)
        self._preview_resize_timer.setInterval(80)
        self._preview_resize_timer.timeout.connect(self._refresh_image_previews)

        title_bar = QHBoxLayout()
        title_bar.setSpacing(8)
        self._title = QLabel("ClipHist", card)
        self._title.setObjectName("title")
        title_bar.addWidget(self._title)
        self._status = QLabel("", card)
        self._status.setObjectName("status")
        title_bar.addWidget(self._status)
        self._btn_pause = QToolButton(card)
        self._btn_pause.setObjectName("btnText")
        self._btn_pause.setFixedHeight(34)
        self._btn_pause.setVisible(on_toggle_pause is not None)
        self._btn_pause.clicked.connect(lambda: self._on_toggle_pause() if self._on_toggle_pause else None)
        title_bar.addWidget(self._btn_pause)
        title_bar.addStretch(1)
        self._btn_settings = QToolButton(card)
        self._btn_settings.setObjectName("btnText")
        self._btn_settings.setText("设置")
        self._btn_settings.setFixedHeight(34)
        self._btn_settings.clicked.connect(lambda: self._on_open_settings() if self._on_open_settings else None)
        title_bar.addWidget(self._btn_settings)
        self._btn_close = QToolButton(card)
        self._btn_close.setObjectName("btnWinClose")
        self._btn_close.setText("×")
        self._btn_close.setToolTip("隐藏面板（Esc）；后台继续记录")
        self._btn_close.setAccessibleName("隐藏面板")
        self._btn_close.setFixedSize(34, 34)
        self._btn_close.clicked.connect(self.hide)
        title_bar.addWidget(self._btn_close)

        navigation_row = QHBoxLayout()
        navigation_row.addWidget(self._navigation)
        navigation_row.addStretch(1)
        navigation_row.addWidget(self._btn_more_types)
        self._btn_more = QToolButton(card)
        self._btn_more.setObjectName("btnText")
        self._btn_more.setText("···")
        self._btn_more.setAccessibleName("更多操作")
        self._btn_more.setToolTip("更多操作")
        self._btn_more.setFixedSize(34, 34)
        self._btn_more.setPopupMode(QToolButton.InstantPopup)
        self._more_menu = QMenu(self._btn_more)
        self._act_copy_only = self._more_menu.addAction("仅复制所选内容（Ctrl+Enter）")
        self._act_copy_only.triggered.connect(lambda: self._activate_current(paste_override=False))
        self._act_edit = self._more_menu.addAction("编辑文本")
        self._act_edit.triggered.connect(self._edit_current_text)
        self._act_favorite = self._more_menu.addAction("收藏所选记录（Alt+F）")
        self._act_favorite.triggered.connect(self._toggle_current_favorite)
        self._more_menu.addSeparator()
        self._act_help = self._more_menu.addAction("使用指南")
        self._act_help.triggered.connect(self._show_help)
        self._act_clear = self._more_menu.addAction("清空历史…")
        self._act_clear.setVisible(self._on_clear is not None)
        self._act_clear.triggered.connect(lambda: self._on_clear() if self._on_clear else None)
        self._btn_more.setMenu(self._more_menu)
        navigation_row.addWidget(self._btn_more)

        # Multi-selection tools appear only when relevant.
        self._batch_bar = QWidget(card)
        toolbar = QHBoxLayout(self._batch_bar)
        toolbar.setContentsMargins(0, 0, 0, 0)
        self._selection_label = QLabel("", self._batch_bar)
        self._selection_label.setObjectName("selectionLabel")
        toolbar.addWidget(self._selection_label)
        toolbar.addStretch(1)
        self._btn_top = QToolButton(self._batch_bar)
        self._btn_top.setObjectName("btnAction")
        self._btn_top.setText("移到最前")
        self._btn_top.clicked.connect(self._move_selected_favorites_to_top)
        toolbar.addWidget(self._btn_top)
        self._btn_del_fav = QToolButton(self._batch_bar)
        self._btn_del_fav.setObjectName("btnDangerAction")
        self._btn_del_fav.setText("移出收藏")
        self._btn_del_fav.clicked.connect(self._remove_selected_favorites)
        toolbar.addWidget(self._btn_del_fav)
        self._btn_del_history = QToolButton(self._batch_bar)
        self._btn_del_history.setObjectName("btnDangerAction")
        self._btn_del_history.setText("删除所选…")
        self._btn_del_history.clicked.connect(self._delete_selected_history)
        toolbar.addWidget(self._btn_del_history)
        self._batch_bar.hide()
        self._tabs.currentChanged.connect(lambda _: self._on_tab_changed())

        self._empty_state = QLabel("", card)
        self._empty_state.setObjectName("emptyState")
        self._empty_state.setAlignment(Qt.AlignCenter)
        self._empty_state.setWordWrap(True)
        self._empty_state.setMinimumHeight(68)
        self._empty_state.hide()

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self._keyboard_hint = QLabel("Tab 切换  ·  ↑↓ 选择", card)
        self._keyboard_hint.setObjectName("keyboardHint")
        footer.addWidget(self._keyboard_hint)
        footer.addStretch(1)
        self._btn_preview_mode = QToolButton(card)
        self._btn_preview_mode.setObjectName("btnText")
        self._btn_preview_mode.setFixedHeight(40)
        self._btn_preview_mode.clicked.connect(self._toggle_preview)
        footer.addWidget(self._btn_preview_mode)
        self._btn_activate = QPushButton("复制内容  ↵", card)
        self._btn_activate.setObjectName("btnPrimary")
        self._btn_activate.setMinimumSize(136, 44)
        self._btn_activate.setAutoDefault(False)
        self._btn_activate.clicked.connect(lambda: self._activate_current())
        footer.addWidget(self._btn_activate)

        body = QVBoxLayout(card)
        self._body_layout = body
        body.setContentsMargins(16, 12, 16, 14)
        body.setSpacing(8)
        body.addLayout(title_bar)
        body.addWidget(self._search)
        body.addLayout(navigation_row)
        body.addWidget(self._batch_bar)
        body.addWidget(self._tabs, 1)
        body.addWidget(self._empty_state)
        body.addLayout(pager)
        body.addWidget(self._preview)
        body.addLayout(footer)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.addWidget(card)

        self.setMinimumSize(400, 440)
        # QSizeGrip must be child of the top-level widget (self), not card,
        # so its coordinate space matches self.width()/height() used in resizeEvent.
        self._grip = QSizeGrip(self)
        self._grip.raise_()
        self._apply_styles()
        self._sync_status()
        self._set_preview_mode(True)
        self._sync_tooltips()
        QApplication.instance().installEventFilter(self)

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self._sync_status()

    def set_data(self, items: list[ClipboardItem], favorites: list[tuple[str, ClipboardItem]]) -> None:
        self._all_items = items
        self._favorites = favorites
        self._delegate_all.clear_caches(keep_thumbnails=True)
        self._delegate_fav.clear_caches(keep_thumbnails=True)
        live_keys = {self._item_cache_key(item) for item in items}
        live_keys.update(self._item_cache_key(item) for _, item in favorites)
        self._search_cache = {key: value for key, value in self._search_cache.items() if key in live_keys}
        self._visual_blob_waiting.intersection_update(live_keys)
        self._visual_blob_pending.intersection_update(live_keys)
        self._visual_blob_cache = OrderedDict(
            (key, item) for key, item in self._visual_blob_cache.items() if key in live_keys
        )
        self._visual_blob_cache_bytes = sum(
            len(item.raw_bytes or b"") + len(item.image_bytes or b"")
            for item in self._visual_blob_cache.values()
        )
        if self.isVisible():
            self._apply_filter()

    def set_items(self, items: list[ClipboardItem]) -> None:
        self.set_data(items, self._favorites)

    def set_favorites(self, favorites: list[tuple[str, ClipboardItem]]) -> None:
        self.set_data(self._all_items, favorites)

    @staticmethod
    def _item_cache_key(item: ClipboardItem) -> tuple:
        if item.db_id is not None:
            return ("db", item.db_id)
        if item._fingerprint:
            return ("fp", item._fingerprint)
        return ("obj", id(item))

    def _is_live_item_key(self, key: tuple) -> bool:
        return any(
            self._item_cache_key(item) == key
            for item in self._all_items
        ) or any(
            self._item_cache_key(item) == key
            for _, item in self._favorites
        )

    def _cache_visual_blob(self, key: tuple, item: ClipboardItem) -> None:
        previous = self._visual_blob_cache.pop(key, None)
        if previous is not None:
            self._visual_blob_cache_bytes -= len(previous.raw_bytes or b"") + len(previous.image_bytes or b"")
        self._visual_blob_cache[key] = item
        self._visual_blob_cache_bytes += len(item.raw_bytes or b"") + len(item.image_bytes or b"")
        while self._visual_blob_cache_bytes > self._visual_blob_cache_limit and len(self._visual_blob_cache) > 1:
            _, evicted = self._visual_blob_cache.popitem(last=False)
            self._visual_blob_cache_bytes -= len(evicted.raw_bytes or b"") + len(evicted.image_bytes or b"")

    def _schedule_visual_blob_load(self, item: ClipboardItem) -> None:
        if self._panel_closed or self._load_blobs is None:
            return
        key = self._item_cache_key(item)
        if key in self._visual_blob_pending or key in self._visual_blob_waiting:
            return
        self._visual_blob_pending.add(key)
        QTimer.singleShot(0, lambda key=key, item=item: self._load_visual_blob(key, item))

    def _load_visual_blob(self, key: tuple, item: ClipboardItem) -> None:
        self._visual_blob_pending.discard(key)
        if self._panel_closed or not self._is_live_item_key(key) or self._load_blobs is None:
            return
        try:
            loaded = self._load_blobs(item)
        except Exception:
            loaded = item
        if loaded is not None and not loaded.needs_blob_load and (
            loaded.raw_bytes is not None or loaded.image_bytes is not None
        ):
            self._cache_visual_blob(key, loaded)
        elif loaded is not None and loaded.needs_blob_load:
            # The application loader has queued the DB read.  Wait for its
            # notify_blob_loaded() callback before trying again.
            self._visual_blob_waiting.add(key)
        self.notify_blob_loaded()

    def _request_thumbnail(self, item: ClipboardItem, size: int, dpr: float) -> QPixmap | None:
        source_key = self._item_cache_key(item)
        key = _ImageDecodeQueue.make_key(source_key, "thumb", size, size, dpr)
        cached = self._image_decode.cached(key)
        if cached is not None:
            return cached
        loaded = self._visual_blob_cache.get(source_key)
        if loaded is not None:
            self._visual_blob_cache.move_to_end(source_key)
            item = loaded
        raw = item.raw_bytes
        if not raw:
            if item.needs_blob_load:
                self._schedule_visual_blob_load(item)
            return None
        return self._image_decode.request(key, raw, size, size, dpr)

    @staticmethod
    def _image_target_size(stack: QStackedWidget, docked: bool) -> tuple[int, int]:
        width = int(stack.width())
        height = int(stack.height())
        if width <= 0:
            width = 620 if not docked else 420
        if height <= 0:
            height = 350 if not docked else 140
        # A preview never needs an unbounded source-sized pixmap.  The worker
        # still decodes the source DIB, but the retained result stays small.
        return min(width, 1600), min(height, 1200)

    def _image_dpr(self, widget: QWidget) -> float:
        try:
            return max(1.0, float(widget.devicePixelRatioF()))
        except Exception:
            return 1.0

    def _set_image_waiting(
        self,
        image_label: QLabel,
        stack: QStackedWidget,
        text: str,
    ) -> None:
        image_label.clear()
        image_label.setText(text)
        stack.setCurrentWidget(image_label)

    def _on_image_decode_ready(self, key: tuple, pixmap: QPixmap | None) -> None:
        if self._panel_closed:
            return
        self._list_all.viewport().update()
        self._list_fav.viewport().update()

        if key == self._preview_image_key:
            self._preview_image = pixmap
            if pixmap is None:
                self._set_image_waiting(self._preview_image_label, self._preview_stack, "图片预览不可用")
            else:
                self._preview_image_label.clear()
                self._preview_image_label.setPixmap(pixmap)
                self._preview_stack.setCurrentWidget(self._preview_image_label)

        if key == self._popup_image_key:
            self._popup_image_data = pixmap
            if pixmap is None:
                self._set_image_waiting(self._popup_image, self._popup_stack, "图片预览不可用")
            else:
                self._popup_image.clear()
                self._popup_image.setPixmap(pixmap)
                self._popup_stack.setCurrentWidget(self._popup_image)

    def notify_blob_loaded(self) -> None:
        """Repaint placeholders after an asynchronous payload load completes."""
        if self._panel_closed:
            return
        self._visual_blob_waiting.clear()
        self._list_all.viewport().update()
        self._list_fav.viewport().update()
        if not self._hover_preview:
            self._update_preview()
        elif self._preview_popup.isVisible() and self._popup_item is not None:
            self._show_preview_popup(self._popup_item, self._btn_preview_mode.mapToGlobal(QPoint(0, 0)))

    def _ensure_blobs(self, it: ClipboardItem) -> ClipboardItem:
        """Load blob data on-demand from the DB via the callback."""
        if not it.needs_blob_load or self._load_blobs is None:
            return it
        return self._load_blobs(it)

    def toggle_visible(self) -> None:
        if self.isVisible():
            self.hide()
            return
        self._show_near_cursor()

    def _show_near_cursor(self) -> None:
        self._filter_timer.stop()
        self._search.blockSignals(True)
        self._search.setText("")
        self._search.blockSignals(False)
        self._active_category = "all"
        self._sync_more_types()
        self._tabs.blockSignals(True)
        self._tabs.setCurrentIndex(0)
        self._tabs.blockSignals(False)
        self._all_page = 0
        self._fav_page = 0
        self._navigation.setCurrentIndex(0)
        self._apply_filter(reset_selection=True)

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
        if event.key() == Qt.Key_Delete:
            if self._tabs.currentIndex() == 1:
                self._remove_selected_favorites()
            else:
                self._delete_selected_history()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.hasFocus():
            paste_override = False if event.modifiers() & Qt.ControlModifier else None
            self._activate_current(paste_override=paste_override)
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self._render_preview_image()
        try:
            grip_size = self._grip.sizeHint()
            # root layout has 10px margins for drop shadow; align grip with card corner
            self._grip.move(
                self.width() - grip_size.width() - 10,
                self.height() - grip_size.height() - 10,
            )
            self._grip.raise_()
        except Exception:
            pass
        self._render_popup_image()
        if hasattr(self, "_body_layout"):
            self._body_layout.setSpacing(4 if self.height() < 500 else 8)
            self._keyboard_hint.setVisible(self.width() >= 500)
            if (event.oldSize().height() < 500) != (self.height() < 500):
                self._hide_preview_popup()
            self._btn_preview_mode.setText("预览内容" if self.height() < 500 else "预览" if self._hover_preview else "收起预览")
            self._preview.setVisible(not self._hover_preview and self.height() >= 500 and self._item_at_current_row() is not None)
        preview_resize_timer = getattr(self, "_preview_resize_timer", None)
        if preview_resize_timer is not None:
            preview_resize_timer.start()

    def showEvent(self, event) -> None:  # type: ignore[override]
        self._panel_closed = False
        self._image_decode.reopen()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._hide_preview_popup()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._panel_closed = True
        self._filter_timer.stop()
        self._preview_resize_timer.stop()
        self._visual_blob_pending.clear()
        self._visual_blob_waiting.clear()
        self._image_decode.close()
        self._preview_popup.hide()
        super().closeEvent(event)

    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            pos = event.position()
            # Don't intercept clicks in the grip area (bottom-right 24x24 px)
            grip_size = self._grip.sizeHint()
            in_grip = (
                pos.x() >= self.width() - grip_size.width() - 10
                and pos.y() >= self.height() - grip_size.height() - 10
            )
            if not in_grip and pos.y() <= 58:
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
        if event.type() in (QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick) and event.button() == Qt.LeftButton:
            if event.type() == QEvent.Type.MouseButtonRelease and self._ignore_star_release:
                self._ignore_star_release = False
                if obj in (self._list_all.viewport(), self._list_fav.viewport()):
                    return True
            for widget in (self._list_all, self._list_fav):
                if obj is widget.viewport():
                    pos = event.position().toPoint()
                    if pos.x() >= widget.viewport().width() - 44 and self._toggle_favorite is not None:
                        widget_item = widget.itemAt(pos)
                        if widget_item is not None:
                            if event.type() == QEvent.Type.MouseButtonDblClick:
                                self._ignore_star_release = True
                            else:
                                self._toggle_item_favorite(widget_item.data(ROLE_ITEM))
                            return True

        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            content_focus = obj in (self._search, self._list_all, self._list_fav, self._list_all.viewport(), self._list_fav.viewport())
            main_focus = self._panel_has_focus() and QApplication.focusWidget().window() is self
            if main_focus and key in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
                self._activate_current(paste_override=False)
                return True
            if main_focus and isinstance(obj, QAbstractButton) and obj.hasFocus() and key in (Qt.Key_Return, Qt.Key_Enter):
                obj.click()
                return True
            if content_focus and key in (Qt.Key_Return, Qt.Key_Enter):
                self._activate_current(paste_override=False if event.modifiers() & Qt.ControlModifier else None)
                return True
            if obj is self._search and key in (Qt.Key_Up, Qt.Key_Down):
                if self._filter_timer.isActive():
                    self._filter_timer.stop()
                    self._apply_filter()
                widget = self._current_list()
                if widget.count():
                    row = widget.currentRow()
                    step = 1 if key == Qt.Key_Down else -1
                    target = 0 if row < 0 else max(0, min(row + step, widget.count() - 1))
                    widget.setCurrentRow(target)
                    widget.scrollToItem(widget.currentItem(), QAbstractItemView.EnsureVisible)
                    widget.setFocus()
                return True
            if main_focus and key == Qt.Key_Space and event.modifiers() == Qt.NoModifier and obj in (self._list_all, self._list_fav):
                self._toggle_preview()
                return True
            if main_focus and key == Qt.Key_F6:
                self.focusNextPrevChild(not bool(event.modifiers() & Qt.ShiftModifier))
                return True
            if main_focus and key in (Qt.Key_Tab, Qt.Key_Backtab):
                backwards = key == Qt.Key_Backtab or bool(event.modifiers() & Qt.ShiftModifier)
                self._cycle_tabs(-1 if backwards else 1)
                return True

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
        search_focus = self._search.hasFocus()
        if self._filter_timer.isActive():
            self._filter_timer.stop()
            self._apply_filter()
        idx = (self._tabs.currentIndex() + step) % count
        self._tabs.setCurrentIndex(idx)
        current = self._current_list()
        if current.count() > 0 and current.currentRow() < 0:
            current.setCurrentRow(0)
        # Keep typing into the search field; empty tabs must not lose focus.
        if search_focus or current.count() == 0:
            self._search.setFocus()
        else:
            current.setFocus()

    def _page_count_for(self, total: int) -> int:
        if total <= 0:
            return 0
        return (total + self._PAGE_SIZE - 1) // self._PAGE_SIZE

    def _get_page_index(self, favorites: bool) -> int:
        return self._fav_page if favorites else self._all_page

    def _set_page_index(self, favorites: bool, value: int) -> None:
        if favorites:
            self._fav_page = max(0, value)
        else:
            self._all_page = max(0, value)

    def _page_start(self, favorites: bool) -> int:
        return self._get_page_index(favorites) * self._PAGE_SIZE

    def _clamp_page_index(self, total: int, favorites: bool) -> int:
        page_count = self._page_count_for(total)
        if page_count <= 0:
            self._set_page_index(favorites, 0)
            return 0
        current = min(self._get_page_index(favorites), page_count - 1)
        self._set_page_index(favorites, current)
        return current

    def _sync_pagination(self) -> None:
        is_fav_tab = self._tabs.currentIndex() == 1
        total = len(self._fav_filtered) if is_fav_tab else len(self._filtered_items)
        page_count = self._page_count_for(total)
        page_index = self._clamp_page_index(total, is_fav_tab)
        for control in (self._btn_prev_page, self._btn_next_page, self._page_label):
            control.setVisible(page_count > 1)

        if total <= 0 or page_count <= 0:
            self._page_label.setText("第 0/0 页")
            self._btn_prev_page.setEnabled(False)
            self._btn_next_page.setEnabled(False)
            return

        self._page_label.setText(f"第 {page_index + 1}/{page_count} 页 · 共 {total} 条")
        self._btn_prev_page.setEnabled(page_index > 0)
        self._btn_next_page.setEnabled(page_index + 1 < page_count)

    def _change_page(self, delta: int) -> None:
        is_fav_tab = self._tabs.currentIndex() == 1
        total = len(self._fav_filtered) if is_fav_tab else len(self._filtered_items)
        page_count = self._page_count_for(total)
        if page_count <= 1:
            return

        current = self._clamp_page_index(total, is_fav_tab)
        target = max(0, min(page_count - 1, current + delta))
        if target == current:
            return

        self._set_page_index(is_fav_tab, target)
        self._refresh_lists(preserve_selection=False)

    def _refresh_lists(self, preserve_selection: bool = True) -> None:
        fav_ids = {fid for fid, _ in self._favorites}

        def key(widget_item):
            if widget_item is None:
                return None
            favorite_id = widget_item.data(ROLE_FAV_ID)
            if favorite_id is not None:
                return ("favorite", favorite_id)
            value = widget_item.data(ROLE_ITEM)
            if value is None:
                return None
            return ("fingerprint", value._fingerprint) if value._fingerprint else self._item_cache_key(value)

        for widget, favorites in ((self._list_all, False), (self._list_fav, True)):
            rows = self._fav_filtered if favorites else self._filtered_items
            start = self._clamp_page_index(len(rows), favorites) * self._PAGE_SIZE
            page = [(fid, it, True) for fid, it in rows[start:start + self._PAGE_SIZE]] if favorites else [
                (None, it, self._fav_id_for_item(it, fav_ids) is not None)
                for it in rows[start:start + self._PAGE_SIZE]
            ]
            unchanged = widget.count() == len(page) and all(
                widget.item(index).data(ROLE_ITEM) is it
                and widget.item(index).data(ROLE_FAV_ID) == fid
                and bool(widget.item(index).data(ROLE_IS_FAVORITE)) == favorite
                for index, (fid, it, favorite) in enumerate(page)
            )
            if unchanged:
                if not preserve_selection and widget.count():
                    widget.setCurrentRow(0, QItemSelectionModel.ClearAndSelect)
                    widget.scrollToTop()
                continue
            selected = {key(item) for item in widget.selectedItems()} if preserve_selection else set()
            current_key = key(widget.currentItem()) if preserve_selection else None
            old_row = widget.currentRow() if preserve_selection else 0
            top_item = widget.itemAt(QPoint(4, 0))
            anchor_key = key(top_item) if preserve_selection else None
            anchor_offset = widget.visualItemRect(top_item).top() if top_item is not None else 0
            widget.setUpdatesEnabled(False)
            widget.blockSignals(True)
            try:
                widget.clear()
                current_row = None
                anchor_item = None
                for fid, it, favorite in page:
                    item = QListWidgetItem()
                    item.setData(ROLE_ITEM, it)
                    item.setData(ROLE_FAV_ID, fid)
                    item.setData(ROLE_IS_FAVORITE, favorite)
                    title, subtitle = _favorite_title(it, 150), _secondary_text(it)
                    item.setData(ROLE_TITLE, title)
                    item.setData(ROLE_SUBTITLE, subtitle)
                    item.setData(Qt.AccessibleTextRole, title)
                    item.setData(Qt.AccessibleDescriptionRole, subtitle)
                    widget.addItem(item)
                    item_key = key(item)
                    if item_key in selected:
                        item.setSelected(True)
                    if item_key == current_key:
                        current_row = widget.row(item)
                    if item_key == anchor_key:
                        anchor_item = item
                if widget.count():
                    row = current_row if current_row is not None else max(0, min(old_row, widget.count() - 1))
                    widget.setCurrentRow(row, QItemSelectionModel.NoUpdate)
                    if not widget.selectedItems():
                        widget.currentItem().setSelected(True)
                widget.doItemsLayout()
                if anchor_item is not None:
                    widget.scrollToItem(anchor_item, QAbstractItemView.PositionAtTop)
                    bar = widget.verticalScrollBar()
                    bar.setValue(bar.value() - anchor_offset)
                elif not preserve_selection:
                    widget.scrollToTop()
            finally:
                widget.blockSignals(False)
                widget.setUpdatesEnabled(True)

        self._sync_tooltips()
        self._sync_pagination()
        self._update_preview()

    def _set_category(self, category: str) -> None:
        self._active_category = category
        self._sync_more_types()
        self._all_page = 0
        self._fav_page = 0
        self._apply_filter()

    @staticmethod
    def _item_display_type(it: ClipboardItem) -> str:
        """Return the precomputed display category for an item."""
        return it.display_type

    def _apply_filter(self, reset_selection: bool = False) -> None:
        q = (self._search.text() or "").strip().lower()
        cat = self._active_category
        changed = (q, cat) != self._last_filter
        if changed or reset_selection:
            self._all_page = self._fav_page = 0
        self._last_filter = (q, cat)

        def _preview_lc(it: ClipboardItem) -> str:
            if it._search_index is not None:
                return it._search_index
            key = self._item_cache_key(it)
            cached = self._search_cache.get(key)
            if cached is not None:
                return cached
            val = _clean_preview(it, 10_000).lower()
            self._search_cache[key] = val
            return val

        def _matches_query(it: ClipboardItem) -> bool:
            if q and q not in _preview_lc(it):
                if not (it.item_type == "files" and any(q in p.lower() for p in (it.file_paths or ()))):
                    return False
            if cat != "all":
                if self._item_display_type(it) != cat:
                    return False
            return True

        if not q and cat == "all":
            self._filtered_items = self._all_items[:]
            self._fav_filtered = self._favorites[:]
        else:
            self._filtered_items = [it for it in self._all_items if _matches_query(it)]
            self._fav_filtered = [(fid, it) for fid, it in self._favorites if _matches_query(it)]

        for index, title in enumerate((f"最近记录 {len(self._filtered_items)}", f"收藏 {len(self._fav_filtered)}")):
            self._tabs.setTabText(index, title)
            self._navigation.setTabText(index, title)
        self._refresh_lists(preserve_selection=not (changed or reset_selection))

    def _update_preview(self) -> None:
        it = self._item_at_current_row()
        if not self._hover_preview and self.height() < 500 and self._preview_popup.isVisible():
            if it is None:
                self._hide_preview_popup()
            else:
                self._show_preview_popup(it, self._btn_preview_mode.mapToGlobal(QPoint(0, 0)))
            return
        if self._hover_preview:
            return
        if it is None:
            self._preview_meta.setText("")
            self._preview_stack.setCurrentWidget(self._preview_empty)
            self._preview_image = None
            self._preview_image_key = None
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
        self._preview_image_label.clear()
        self._preview_image_label.setPixmap(self._preview_image)
        self._preview_stack.setCurrentWidget(self._preview_image_label)

    def _render_popup_image(self) -> None:
        if self._popup_image_data is None:
            return
        self._popup_image.clear()
        self._popup_image.setPixmap(self._popup_image_data)
        self._popup_stack.setCurrentWidget(self._popup_image)

    def _refresh_image_previews(self) -> None:
        """Request a size-appropriate image after a resize settles."""
        if self._panel_closed:
            return
        if not self._hover_preview:
            it = self._item_at_current_row()
            if it is not None and it.item_type == "image":
                self._render_preview_content(
                    it,
                    self._preview_meta,
                    self._preview_text,
                    self._preview_image_label,
                    self._preview_stack,
                    docked=True,
                )
        if self._preview_popup.isVisible() and self._popup_item is not None:
            self._render_preview_content(
                self._popup_item,
                self._popup_meta,
                self._popup_text,
                self._popup_image,
                self._popup_stack,
                docked=False,
            )

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

        source_key = self._item_cache_key(it)
        loaded = self._visual_blob_cache.get(source_key)
        if loaded is not None:
            self._visual_blob_cache.move_to_end(source_key)
            it = loaded

        if it.item_type == "image":
            self._render_image_preview(it, source_key, image_label, stack, docked)
            return

        # Keep rich-text preview behavior unchanged; the application callback
        # is non-blocking for lazy records and the retry arrives via notify.
        if it.item_type in ("html", "rtf") and it.needs_blob_load:
            it = self._ensure_blobs(it)

        if docked:
            self._preview_image = None
            self._preview_image_key = None
            self._preview_image_label.clear()
        else:
            self._popup_image_data = None
            self._popup_image_key = None
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

    def _render_image_preview(
        self,
        it: ClipboardItem,
        source_key: tuple,
        image_label: QLabel,
        stack: QStackedWidget,
        docked: bool,
    ) -> None:
        if it.needs_blob_load:
            self._schedule_visual_blob_load(it)
            if docked:
                self._preview_image = None
                self._preview_image_key = None
            else:
                self._popup_image_data = None
                self._popup_image_key = None
            self._set_image_waiting(image_label, stack, "正在加载图片…")
            return

        raw = it.raw_bytes
        if not raw:
            if docked:
                self._preview_image = None
                self._preview_image_key = None
            else:
                self._popup_image_data = None
                self._popup_image_key = None
            self._set_image_waiting(image_label, stack, "图片预览不可用")
            return

        width, height = self._image_target_size(stack, docked)
        dpr = self._image_dpr(stack)
        kind = "preview" if docked else "popup"
        key = _ImageDecodeQueue.make_key(source_key, kind, width, height, dpr)
        if docked:
            self._preview_image_key = key
        else:
            self._popup_image_key = key
        pixmap = self._image_decode.request(key, raw, width, height, dpr)
        if docked:
            self._preview_image = pixmap
        else:
            self._popup_image_data = pixmap
        if pixmap is None:
            self._set_image_waiting(image_label, stack, "正在生成图片预览…")
        else:
            image_label.clear()
            image_label.setPixmap(pixmap)
            stack.setCurrentWidget(image_label)

    def _show_preview_popup(self, it: ClipboardItem, pos: QPoint) -> None:
        self._popup_item = it
        if it.item_type == "image":
            popup_w = min(640, max(420, self.width()))
            popup_h = 420
        else:
            popup_w = min(520, max(360, self.width() - 40))
            popup_h = 220
        # Set the final geometry before requesting the worker target size.
        self._preview_popup.resize(popup_w, popup_h)
        self._render_preview_content(
            it,
            self._popup_meta,
            self._popup_text,
            self._popup_image,
            self._popup_stack,
            docked=False,
        )
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        screen_geo = screen.availableGeometry()
        x = min(max(pos.x() + 16, screen_geo.left()), screen_geo.right() - popup_w)
        y = min(max(pos.y() + 16, screen_geo.top()), screen_geo.bottom() - popup_h)
        self._preview_popup.move(x, y)
        self._preview_popup.show()

    def _hide_preview_popup(self) -> None:
        if self._preview_popup.isVisible():
            self._preview_popup.hide()
        self._popup_image_key = None
        self._popup_item = None

    def _set_preview_mode(self, hover: bool) -> None:
        self._hover_preview = bool(hover)
        self._hide_preview_popup()
        current = self._item_at_current_row()
        compact = self.height() < 500
        if not hover and compact and current is not None:
            self._show_preview_popup(current, self._btn_preview_mode.mapToGlobal(QPoint(0, 0)))
        self._btn_preview_mode.setText("预览" if hover else "关闭预览" if compact else "收起预览")
        self._btn_preview_mode.setToolTip("列表中按空格或点击切换预览；鼠标经过记录不会弹出内容")
        self._preview.setVisible(not hover and not compact and current is not None)
        self._update_preview()

    def _toggle_preview(self) -> None:
        if self.height() < 500:
            self._set_preview_mode(self._preview_popup.isVisible() and not self._hover_preview)
        else:
            self._set_preview_mode(not self._hover_preview)

    def _sync_more_types(self) -> None:
        self._btn_more_types.setChecked(self._active_category != "all")
        self._btn_more_types.setText(self._type_labels[self._active_category])
        for key, action in self._type_actions.items():
            action.setChecked(key == self._active_category)

    def _get_filtered_item(self, row: int) -> ClipboardItem | None:
        item = self._list_all.item(row)
        if item is None:
            return None
        return item.data(ROLE_ITEM)

    def _get_fav_filtered_item(self, row: int) -> ClipboardItem | None:
        item = self._list_fav.item(row)
        if item is None:
            return None
        return item.data(ROLE_ITEM)

    def _activate_current(self, paste_override: bool | None = None) -> None:
        if self._filter_timer.isActive():
            self._filter_timer.stop()
            self._apply_filter()
        w = self._current_list()
        row = w.currentRow()
        if row < 0:
            return
        it = self._item_at_current_row()
        if it is None:
            return
        self._on_activate(it, paste_override)
        self.hide()

    def _selected_history_items(self) -> list[ClipboardItem]:
        result: list[ClipboardItem] = []
        for widget_item in self._list_all.selectedItems():
            item = widget_item.data(ROLE_ITEM)
            if isinstance(item, ClipboardItem):
                result.append(item)
        return result

    def _delete_selected_history(self) -> None:
        if self._delete_items is None:
            return
        items = self._selected_history_items()
        if not items:
            current = self._item_at_current_row()
            items = [current] if current is not None else []
        if not items:
            return
        reply = QMessageBox.question(
            self,
            "删除历史记录",
            f"确定删除选中的 {len(items)} 条历史记录吗？收藏内容不受影响。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        ok, msg = self._delete_items(items)
        if not ok:
            QMessageBox.warning(self, "删除失败", msg or "历史记录删除失败")
            return
        for item in items:
            self._search_cache.pop(self._item_cache_key(item), None)
        self._all_items = [item for item in self._all_items if item not in items]
        self._apply_filter()

    def _current_list(self) -> QListWidget:
        return self._list_fav if self._tabs.currentIndex() == 1 else self._list_all

    def _item_at_current_row(self) -> ClipboardItem | None:
        current_item = self._current_list().currentItem()
        if current_item is None:
            return None
        return current_item.data(ROLE_ITEM)

    def _fav_id_at_current_row(self) -> str | None:
        if self._tabs.currentIndex() != 1:
            return None
        current_item = self._list_fav.currentItem()
        if current_item is None:
            return None
        return current_item.data(ROLE_FAV_ID)

    def _toggle_current_favorite(self) -> None:
        it = self._item_at_current_row()
        if it is None:
            return
        self._toggle_item_favorite(it)

    def _toggle_item_favorite(self, item: ClipboardItem) -> None:
        if self._toggle_favorite is None:
            return
        ok, msg = self._toggle_favorite(item)
        if not ok:
            QMessageBox.warning(self, "收藏失败", msg or "收藏状态更新失败")
            return
        if ok and self._get_favorites is not None:
            try:
                self._favorites = self._get_favorites()
            except Exception:
                pass
        self._apply_filter()

    def _remove_current_favorite(self) -> None:
        self._remove_selected_favorites()

    def _selected_favorite_ids(self) -> list[str]:
        selected: list[str] = []
        for widget_item in self._list_fav.selectedItems():
            fav_id = widget_item.data(ROLE_FAV_ID)
            if isinstance(fav_id, str):
                selected.append(fav_id)
        if selected:
            return selected
        current = self._fav_id_at_current_row()
        return [current] if current else []

    def _remove_selected_favorites(self) -> None:
        fav_ids = self._selected_favorite_ids()
        if not fav_ids:
            return

        count = len(fav_ids)
        reply = QMessageBox.question(
            self,
            "移出收藏",
            f"确定将所选 {count} 条内容移出收藏夹吗？\n\n历史记录中的相同内容不会被删除。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        if self._remove_favorites is not None:
            ok, msg = self._remove_favorites(fav_ids)
        elif self._remove_favorite is not None:
            results = [self._remove_favorite(fid) for fid in fav_ids]
            ok = all(result[0] for result in results)
            msg = next((result[1] for result in results if not result[0]), None)
        else:
            return
        if not ok:
            QMessageBox.warning(self, "移出失败", msg or "收藏内容更新失败")
            return
        if self._get_favorites is not None:
            try:
                self._favorites = self._get_favorites()
            except Exception:
                pass
        self._apply_filter()

    def _move_selected_favorites_to_top(self) -> None:
        if self._tabs.currentIndex() != 1 or self._reorder_favorites is None:
            return
        if (self._search.text() or "").strip() or self._active_category != "all":
            return
        selected_ids = set(self._selected_favorite_ids())
        if not selected_ids:
            return
        current_ids = [fav_id for fav_id, _ in self._favorites]
        ordered_selected = [fav_id for fav_id in current_ids if fav_id in selected_ids]
        new_order = ordered_selected + [fav_id for fav_id in current_ids if fav_id not in selected_ids]
        if new_order == current_ids:
            return
        ok, msg = self._reorder_favorites(new_order)
        if not ok:
            QMessageBox.warning(self, "置顶失败", msg or "收藏排序更新失败")
            return
        if self._get_favorites is not None:
            try:
                self._favorites = self._get_favorites()
            except Exception:
                pass
        self._fav_page = 0
        self._apply_filter()
        self._list_fav.clearSelection()
        for row in range(min(len(ordered_selected), self._list_fav.count())):
            self._list_fav.item(row).setSelected(True)
        if ordered_selected:
            self._list_fav.setCurrentRow(0, QItemSelectionModel.NoUpdate)

    def _move_favorite(self, delta: int) -> None:
        if self._tabs.currentIndex() != 1 or self._reorder_favorites is None:
            return
        row = self._list_fav.currentRow()
        if row < 0:
            return
        global_row = self._page_start(True) + row
        if global_row < 0 or global_row >= len(self._fav_filtered):
            return
        target = global_row + delta
        if target < 0 or target >= len(self._fav_filtered):
            return
        ids = [fid for fid, _ in self._fav_filtered]
        ids[global_row], ids[target] = ids[target], ids[global_row]
        self._reorder_favorites(ids)
        if self._get_favorites is not None:
            try:
                self._favorites = self._get_favorites()
            except Exception:
                pass
        self._fav_page = target // self._PAGE_SIZE
        self._apply_filter()
        self._tabs.setCurrentIndex(1)
        self._list_fav.setCurrentRow(target % self._PAGE_SIZE)

    def _on_fav_reorder_drop(self, src_id: str, anchor_id: str, after: bool) -> None:
        if self._reorder_favorites is None:
            return
        full_ids = [fav_id for fav_id, _ in self._favorites]
        if src_id not in full_ids or anchor_id not in full_ids or anchor_id == src_id:
            return
        rest = [fav_id for fav_id in full_ids if fav_id != src_id]
        anchor_index = rest.index(anchor_id)
        rest.insert(anchor_index + 1 if after else anchor_index, src_id)
        if rest == full_ids:
            return
        ok, msg = self._reorder_favorites(rest)
        if not ok:
            QMessageBox.warning(self, "排序失败", msg or "收藏排序更新失败")
            return
        if self._get_favorites is not None:
            try:
                self._favorites = self._get_favorites()
            except Exception:
                pass
        self._apply_filter()
        for row_index, (fav_id, _) in enumerate(self._fav_filtered):
            if fav_id == src_id:
                self._fav_page = row_index // self._PAGE_SIZE
                self._apply_filter()
                self._list_fav.setCurrentRow(row_index % self._PAGE_SIZE)
                break

    def _on_tab_changed(self) -> None:
        self._sync_tooltips()
        self._sync_pagination()
        self._update_preview()

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
        row = widget.row(it)
        if row >= 0:
            if it.isSelected():
                widget.setCurrentItem(it, QItemSelectionModel.NoUpdate)
            else:
                widget.setCurrentItem(it, QItemSelectionModel.ClearAndSelect)
        item_obj: ClipboardItem | None = it.data(ROLE_ITEM)
        if item_obj is None:
            return
        menu = QMenu(widget)
        act_fav = None
        if widget is self._list_all:
            act_fav = menu.addAction("取消收藏" if bool(it.data(ROLE_IS_FAVORITE)) else "收藏")
        act_edit = None
        if item_obj.item_type == "text":
            act_edit = menu.addAction("编辑文本")
        act_del = None
        act_del_history = None
        act_up = None
        act_down = None
        act_top = None
        if widget is self._list_fav:
            selected_count = len(self._selected_favorite_ids())
            act_del = menu.addAction(f"移出收藏（{selected_count} 项）")
            menu.addSeparator()
            act_top = menu.addAction("置顶")
            act_up = menu.addAction("上移")
            act_down = menu.addAction("下移")
        else:
            act_del_history = menu.addAction("删除历史记录")
        chosen = menu.exec(widget.mapToGlobal(pos))
        if act_fav is not None and chosen == act_fav:
            self._toggle_current_favorite()
        elif act_edit is not None and chosen == act_edit:
            self._edit_current_text()
        elif act_del is not None and chosen == act_del:
            self._remove_selected_favorites()
        elif act_del_history is not None and chosen == act_del_history:
            self._delete_selected_history()
        elif act_top is not None and chosen == act_top:
            self._move_selected_favorites_to_top()
        elif act_up is not None and chosen == act_up:
            self._move_favorite(-1)
        elif act_down is not None and chosen == act_down:
            self._move_favorite(1)

    def _edit_current_text(self) -> None:
        if self._edit_item is None:
            return
        it = self._item_at_current_row()
        if it is None or it.item_type != "text":
            return

        old_text = it.text or ""
        new_text = self._open_text_edit_dialog(old_text)
        if new_text is None or new_text == old_text:
            return

        updated = replace(
            it,
            text=new_text,
            _fingerprint=None,
            _raw_hash=None,
            _image_hash=None,
            _search_index=None,
            _display_type=None,
        ).prepared()
        ok, msg = self._edit_item(it, updated)
        if not ok:
            QMessageBox.warning(self, "编辑失败", msg or "内容更新失败")
            return

        self._search_cache.pop(self._item_cache_key(it), None)
        self._all_items = [updated if current is it or current == it else current for current in self._all_items]

        if self._get_favorites is not None:
            try:
                self._favorites = self._get_favorites()
            except Exception:
                pass
        self._apply_filter()

    def _open_text_edit_dialog(self, initial_text: str) -> str | None:
        dlg = QDialog(self)
        dlg.setWindowTitle("编辑文本")
        dlg.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint)
        dlg.resize(460, 280)

        editor = QPlainTextEdit(dlg)
        editor.setPlainText(initial_text)

        btn_ok = QPushButton("确定", dlg)
        btn_cancel = QPushButton("取消", dlg)
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)

        root = QVBoxLayout(dlg)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        root.addWidget(editor)
        root.addLayout(btn_row)

        dlg.setStyleSheet(dialog_styles(self._theme_mode))
        btn_ok.setObjectName("settingsBtnPrimary")

        editor.selectAll()
        editor.setFocus()
        if dlg.exec() != QDialog.Accepted:
            return None
        return editor.toPlainText()

    def _sync_status(self) -> None:
        self._status.setText("已暂停记录" if self._paused else "正在记录")
        self._status.setProperty("paused", self._paused)
        self._btn_pause.setText("继续" if self._paused else "暂停")
        self._btn_pause.setToolTip("继续记录剪贴板" if self._paused else "暂停记录剪贴板")
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def set_auto_paste(self, enabled: bool) -> None:
        self._auto_paste = bool(enabled)
        self._btn_activate.setText("粘贴到原窗口  ↵" if enabled else "复制内容  ↵")
        self._btn_activate.setToolTip("Enter 或双击执行；Ctrl+Enter 始终仅复制")

    def set_theme(self, mode: str) -> None:
        self._theme_mode = mode
        colors = theme_colors(mode)
        for delegate in (self._delegate_all, self._delegate_fav):
            delegate.set_colors(colors)
        self._apply_styles()
        for widget in (self._list_all, self._list_fav):
            widget.viewport().update()
            if widget._drop_line is not None:
                widget._drop_line.setStyleSheet(f"background: {colors['accent']};")
        for dialog in self.findChildren(QDialog):
            dialog.setStyleSheet(dialog_styles(mode))

    def _show_help(self) -> None:
        dlg = HelpDialog(self)
        dlg.exec()

    def _sync_tooltips(self) -> None:
        is_fav = self._tabs.currentIndex() == 1
        current = self._item_at_current_row()
        selected_count = len(self._current_list().selectedItems())
        total = self._current_list().count()
        self._btn_settings.setVisible(self._on_open_settings is not None)
        self._btn_activate.setEnabled(current is not None)
        self._btn_preview_mode.setEnabled(current is not None)
        self._act_copy_only.setEnabled(current is not None)
        self._act_edit.setEnabled(current is not None and current.item_type == "text" and self._edit_item is not None)
        self._act_favorite.setEnabled(current is not None and self._toggle_favorite is not None)
        self._act_favorite.setText("取消收藏（Alt+F）" if current is not None and bool(self._current_list().currentItem().data(ROLE_IS_FAVORITE)) else "收藏所选记录（Alt+F）")
        self._selection_label.setText(f"已选 {selected_count} 项")
        self._batch_bar.setVisible(selected_count > 1)
        self._btn_top.setVisible(is_fav)
        self._btn_del_fav.setVisible(is_fav and bool(self._remove_favorites or self._remove_favorite))
        self._btn_del_history.setVisible(not is_fav and self._delete_items is not None)
        can_reorder = is_fav and self._reorder_favorites is not None and not self._search.text().strip() and self._active_category == "all"
        self._btn_top.setEnabled(can_reorder)
        self._btn_top.setToolTip("将所选收藏移到最前" if can_reorder else "清除搜索和类型筛选后可调整排序")
        self._tabs.setVisible(total > 0)
        self._empty_state.setVisible(total == 0)
        if total == 0:
            filtered = bool(self._search.text().strip()) or self._active_category != "all"
            self._empty_state.setText("没有匹配的内容\n试试其他关键词，或切换到「全部」" if filtered else "还没有收藏\n点击记录右侧的星标，常用内容随时可取" if is_fav else "复制一段内容，从这里开始\n复制的文本、图片和文件会自动出现在这里")
        compact = self.height() < 500
        self._preview.setVisible(current is not None and not self._hover_preview and not compact)
        self._btn_preview_mode.setText("预览" if self._hover_preview else "关闭预览" if compact and self._preview_popup.isVisible() else "预览内容" if compact else "收起预览")

    def _apply_styles(self) -> None:
        c = theme_colors(self._theme_mode)
        self.setStyleSheet(f"""
            QWidget {{ font-family: "Microsoft YaHei"; font-size: 14px; color: {c['text']}; }}
            #card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px; }}
            QLabel {{ background: transparent; }}
            #title {{ font-size: 18px; font-weight: 600; }}
            #status, #pageLabel, #keyboardHint, #selectionLabel {{ color: {c['muted']}; font-size: 12px; }}
            #status[paused="true"] {{ color: {c['danger']}; }}
            QLineEdit {{ background: {c['surface_alt']}; border: 1px solid {c['border']};
                border-radius: 8px; padding: 4px 12px; selection-background-color: {c['accent']};
                selection-color: {c['accent_text']}; }}
            QLineEdit:focus {{ border-color: {c['accent']}; }}
            QTabWidget::pane {{ border: 0; background: {c['surface']}; }}
            QTabWidget > QWidget {{ background: {c['surface']}; }}
            #navigation {{ background: transparent; }}
            #navigation::tab {{ background: transparent; color: {c['muted']};
                border: 0; border-bottom: 2px solid transparent; padding: 9px 12px; margin-right: 12px; }}
            #navigation::tab:selected {{ color: {c['text']}; border-bottom-color: {c['accent']}; font-weight: 600; }}
            #navigation::tab:hover {{ background: {c['hover']}; }}
            #navigation::tab:focus {{ background: {c['selection']}; }}
            QToolButton, QPushButton {{ background: transparent; border: 1px solid transparent;
                border-radius: 6px; padding: 5px 9px; }}
            QToolButton:hover, QPushButton:hover {{ background: {c['hover']}; }}
            QToolButton:pressed, QPushButton:pressed {{ background: {c['selection']}; }}
            QToolButton:focus, QPushButton:focus {{ border-color: {c['accent']}; }}
            QToolButton:disabled, QPushButton:disabled {{ color: {c['disabled']}; }}
            #btnCategoryChip {{ color: {c['muted']}; padding: 4px 20px 4px 10px; }}
            QToolButton#btnCategoryChip::menu-indicator {{ subcontrol-origin: padding;
                subcontrol-position: center right; right: 5px; width: 8px; height: 8px; }}
            QToolButton#btnText::menu-indicator {{ image: none; width: 0; }}
            #btnCategoryChip:checked {{ color: {c['accent']}; background: {c['selection']}; font-weight: 600; }}
            #btnPrimary {{ background: {c['accent']}; color: {c['accent_text']}; font-weight: 600; padding: 4px 16px; }}
            #btnPrimary:hover {{ background: {c['accent_hover']}; }}
            #btnPrimary:pressed {{ background: {c['accent_pressed']}; }}
            #btnPrimary:focus {{ border: 2px solid {c['text']}; padding: 3px 15px; }}
            #btnPrimary:disabled {{ background: {c['surface_alt']}; color: {c['disabled']}; border-color: {c['border']}; }}
            #btnWinClose {{ font-size: 24px; padding: 0; }}
            #btnWinClose:hover, #btnDangerAction:hover {{ color: {c['danger']}; background: {c['danger_bg']}; }}
            #btnAction, #btnDangerAction {{ border-color: {c['border']}; }}
            QListWidget {{ border: 0; background: {c['surface']}; outline: none; }}
            QListWidget::item {{ border-bottom: 1px solid {c['border']}; }}
            QListWidget::item:selected {{ background: transparent; }}
            #emptyState {{ color: {c['muted']}; font-size: 14px; padding: 20px; }}
            #previewCard {{ border: 0; border-top: 1px solid {c['border']}; background: {c['surface']}; }}
            #previewPopup {{ border: 1px solid {c['border']}; background: {c['surface']}; border-radius: 8px; }}
            #previewTitle {{ font-weight: 600; }}
            #previewMeta, #previewEmpty {{ color: {c['muted']}; font-size: 12px; }}
            #previewText {{ color: {c['text']}; background: transparent; border: 0; padding: 2px; }}
            #previewImage {{ background: {c['surface_alt']}; border-radius: 6px; }}
            QScrollBar:vertical {{ border: 0; background: transparent; width: 8px; margin: 2px; }}
            QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 3px; min-height: 26px; }}
            QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        """)
