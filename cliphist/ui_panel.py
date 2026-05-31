from __future__ import annotations

import os
import re

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable

from PySide6.QtCore import Qt, QMimeData, QTimer, QUrl, QRect, QPoint, QEvent
from PySide6.QtGui import QColor, QCursor, QDrag, QGuiApplication, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication,
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

_RE_URL = re.compile(r'https?://[^\s<>"]+|www\.[^\s<>"]+', re.IGNORECASE)

ROLE_ITEM = int(Qt.UserRole)
ROLE_FAV_ID = int(Qt.UserRole + 1)
ROLE_IS_FAVORITE = int(Qt.UserRole + 2)
ROLE_TITLE = int(Qt.UserRole + 3)
ROLE_SUBTITLE = int(Qt.UserRole + 4)


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
            ("类别标签", "搜索框下方有「全部 / 文本 / 图片 / 文件 / 链接 / HTML / RTF」标签，点击过滤类型。"),
            ("组合筛选", "类别标签与关键词可同时生效，例如在「图片」类中搜索特定内容。"),
        ],
    ),
    (
        "⌨️", "快捷键一览",
        [
            ("Alt+C", "打开 / 隐藏主面板（可在设置中自定义）。"),
            ("Alt+P", "切换暂停 / 继续监听（可在设置中自定义）。"),
            ("Enter / 双击", "将选中记录写入剪切板并关闭面板。"),
            ("Esc", "隐藏面板。"),
            ("Ctrl+F", "聚焦搜索框。"),
            ("Alt+F", "收藏 / 取消收藏当前选中的记录。"),
            ("Tab / Shift+Tab", "在「全部」与「收藏」标签页之间循环切换。"),
            ("Ctrl+悬停", "按住 Ctrl 后将鼠标悬停在列表项上，弹出浮动内容预览。"),
        ],
    ),
    (
        "★", "收藏功能",
        [
            ("添加收藏", "选中记录后按 Alt+F，或点击工具栏星号按钮，或右键菜单选择「收藏」。"),
            ("查看收藏", "点击「收藏」标签页，收藏内容持久保存，不受历史上限影响。"),
            ("删除收藏", "在收藏标签页选中记录后，点击工具栏垃圾桶按钮或右键「删除收藏」。"),
            ("调整顺序", "选中记录后点击 ▲ / ▼ 按钮，或右键「上移 / 下移」调整收藏排序。"),
            ("编辑文本", "对文本类收藏右键选择「编辑文本」，可直接修改内容后保存。"),
        ],
    ),
    (
        "👁", "预览功能",
        [
            ("底部预览", "点击工具栏预览按钮切换到底部预览模式，选中记录后在面板下方显示完整内容。"),
            ("悬浮预览", "默认为悬浮模式，按住 Ctrl 悬停在列表项上弹出浮动预览窗口。"),
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
            ("窗口大小", "可设置面板初始宽度（400–1600 px）和高度（350–1200 px），保存后立即生效。"),
            ("持久化存储", "托盘菜单「启用持久化」后，历史写入 SQLite 数据库，重启不丢失。"),
        ],
    ),
    (
        "💡", "实用技巧",
        [
            ("拖拽使用", "直接把列表项拖拽到其他应用（文本→编辑器、图片→画图软件等）。"),
            ("移动窗口", "拖动面板顶部标题栏可移动窗口；右下角拖动手柄可调整大小。"),
            ("分页浏览", "历史记录过多时，列表底部显示分页控件，可快速跳转到上一页 / 下一页。"),
            ("清空历史", "工具栏垃圾桶按钮可清空全部历史，收藏内容不受影响。"),
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

        ver_lbl = QLabel("v0.1", card)
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
        self.setStyleSheet(
            """
            #helpCard {
              background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                          stop:0 #F8FAFC, stop:1 #EEF2FF);
              border: 1px solid rgba(148, 163, 184, 0.4);
              border-radius: 16px;
            }
            #helpIcon {
              background: #2563EB;
              color: #FFFFFF;
              font-size: 18px;
              font-weight: 900;
              border-radius: 10px;
            }
            #helpTitle {
              font-size: 15px;
              font-weight: 700;
              color: #0F172A;
            }
            #helpVer {
              font-size: 11px;
              color: #94A3B8;
              padding: 2px 7px;
              border: 1px solid rgba(148,163,184,0.4);
              border-radius: 8px;
              background: #FFFFFF;
            }
            #helpSep {
              border: none;
              border-top: 1px solid rgba(148, 163, 184, 0.35);
              margin: 0 -4px;
            }
            #helpScroll { background: transparent; }
            QScrollBar:vertical {
              border: none;
              background: transparent;
              width: 6px;
              margin: 0;
            }
            QScrollBar::handle:vertical {
              background: rgba(148, 163, 184, 0.5);
              border-radius: 3px;
              min-height: 24px;
            }
            QScrollBar::handle:vertical:hover {
              background: rgba(100, 116, 139, 0.65);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            #helpContent { background: transparent; }
            #helpSection {
              background: #FFFFFF;
              border: 1px solid rgba(148, 163, 184, 0.28);
              border-radius: 12px;
            }
            #helpEmoji { font-size: 15px; }
            #helpSectionTitle {
              font-size: 13px;
              font-weight: 700;
              color: #1E3A5F;
            }
            #helpTerm {
              font-size: 11px;
              font-weight: 600;
              color: #1D4ED8;
              padding: 2px 6px;
              background: rgba(219, 234, 254, 0.7);
              border-radius: 5px;
              margin-top: 1px;
            }
            #helpDesc {
              font-size: 12px;
              color: #334155;
            }
            #helpFooter {
              font-size: 11px;
              color: #94A3B8;
              padding-top: 2px;
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
                 load_blobs: Callable[[ClipboardItem], ClipboardItem] | None = None) -> None:
        super().__init__(parent)
        self._get_item = get_item
        self._load_blobs = load_blobs
        self.setDragEnabled(True)

    def startDrag(self, supportedActions):  # type: ignore[override]
        row = self.currentRow()
        it = self._get_item(row)
        if it is None:
            return

        # Load blobs on-demand for image drag
        if it.needs_blob_load and self._load_blobs is not None:
            it = self._load_blobs(it)

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
        # File drags should always behave like copy to avoid moving source files.
        if it.item_type == "files":
            drag.exec(Qt.CopyAction)
        else:
            drag.exec(supportedActions)


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
    return _clean_preview(item, max_len)


def _secondary_text(item: ClipboardItem) -> str:
    ts = item.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    if item.item_type == "files":
        n = len(item.file_paths or ())
        return f"{ts} · {n} 个文件"
    if item.item_type == "image":
        size = item.raw_size or len(item.raw_bytes or b"")
        kb = max(1, size // 1024)
        return f"{ts} · {kb} KB"
    if item.item_type in ("html", "rtf"):
        size = item.raw_size or len(item.raw_bytes or b"")
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
    _TYPE_COLORS: dict[str, tuple[QColor, QColor, QColor]] = {
        # (background, border, foreground)
        "text": (QColor("#DBEAFE"), QColor("#93C5FD"), QColor("#1E40AF")),
        "files": (QColor("#DCFCE7"), QColor("#86EFAC"), QColor("#166534")),
        "image": (QColor("#FEF9C3"), QColor("#FDE047"), QColor("#854D0E")),
        "html": (QColor("#FCE7F3"), QColor("#F9A8D4"), QColor("#9D174D")),
        "rtf": (QColor("#F3E8FF"), QColor("#D8B4FE"), QColor("#7E22CE")),
        "link": (QColor("#CFFAFE"), QColor("#67E8F9"), QColor("#155E75")),
    }
    _DEFAULT_TYPE_COLOR = (QColor("#F1F5F9"), QColor("#CBD5E1"), QColor("#475569"))
    _TYPE_LABELS: dict[str, str] = {
        "text": "文本",
        "files": "文件",
        "image": "图片",
        "html": "HTML",
        "rtf": "RTF",
        "link": "链接",
    }

    def __init__(self, parent: QWidget | None = None, load_blobs: Callable[[ClipboardItem], ClipboardItem] | None = None) -> None:
        super().__init__(parent)
        self._load_blobs = load_blobs
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

        left_pad = 12
        # Determine display type (detect links in text items)
        display_type = it.item_type
        if it.item_type == "text" and it.text and _RE_URL.search(it.text):
            display_type = "link"

        badge_h = 22
        badge_w = 46
        badge_rect = QRect(
            rect.left() + left_pad,
            rect.top() + (rect.height() - badge_h) // 2,
            badge_w,
            badge_h,
        )
        if it.item_type == "image":
            thumb_size = 42
            thumb_rect = QRect(
                rect.left() + left_pad,
                rect.top() + (rect.height() - thumb_size) // 2,
                thumb_size,
                thumb_size,
            )
            thumb = self._image_thumb(it, thumb_size - 4)
            if thumb is not None:
                clip = QPainterPath()
                clip.addRoundedRect(thumb_rect, 8, 8)
                painter.setClipPath(clip)
                # 按缩略图自身逻辑尺寸居中绘制，避免拉伸变形与二次放大模糊。
                tw = thumb.width() / thumb.devicePixelRatio()
                th = thumb.height() / thumb.devicePixelRatio()
                tx = thumb_rect.left() + (thumb_rect.width() - tw) / 2
                ty = thumb_rect.top() + (thumb_rect.height() - th) / 2
                painter.drawPixmap(QPoint(int(round(tx)), int(round(ty))), thumb)
                painter.setClipping(False)
                painter.setPen(QColor(15, 23, 42, 40))
                painter.drawRoundedRect(thumb_rect.adjusted(0, 0, -1, -1), 8, 8)
                badge_rect = thumb_rect
            else:
                self._paint_icon_badge(painter, badge_rect, display_type, is_selected)
        else:
            self._paint_icon_badge(painter, badge_rect, display_type, is_selected)

        x0 = badge_rect.right() + 10
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
        key = hash((self._cache_key(it), size, round(dpr, 3)))
        cached = self._thumb_cache.get(key)
        if cached is not None:
            return cached
        raw = it.raw_bytes
        # 列表中的图片项通常为 slim 模式（raw_bytes 为空），按需从数据库加载一次用于生成缩略图。
        if not raw and it.needs_blob_load and self._load_blobs is not None:
            try:
                loaded = self._load_blobs(it)
                raw = loaded.raw_bytes
            except Exception:
                raw = None
        if not raw:
            return None
        img = _qimage_from_dib(raw)
        if img is None or img.isNull():
            return None
        target = max(1, int(round(size * dpr)))
        pix = QPixmap.fromImage(img).scaled(
            target, target, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        pix.setDevicePixelRatio(dpr)
        self._thumb_cache[key] = pix
        if len(self._thumb_cache) > 260:
            self._thumb_cache.pop(next(iter(self._thumb_cache)))
        return pix

    def _paint_icon_badge(
        self,
        painter: QPainter,
        rect: QRect,
        display_type: str,
        is_selected: bool,
    ) -> None:
        colors = self._TYPE_COLORS.get(display_type, self._DEFAULT_TYPE_COLOR)
        if is_selected:
            bg = QColor(255, 255, 255, 45)
            border = QColor(255, 255, 255, 80)
            fg = QColor("#FFFFFF")
        else:
            bg, border, fg = colors

        radius = 6
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(bg)
        painter.setPen(border)
        painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), radius, radius)

        label = self._TYPE_LABELS.get(display_type, display_type[:3])
        font = painter.font()
        font.setBold(True)
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(fg)
        painter.drawText(rect, Qt.AlignCenter, label)


class ClipPanel(QWidget):
    _PAGE_SIZE = 100

    def __init__(
        self,
        on_activate: Callable[[ClipboardItem], None],
        on_clear: Callable[[], None] | None = None,
        on_open_settings: Callable[[], None] | None = None,
        get_favorites: Callable[[], list[tuple[str, ClipboardItem]]] | None = None,
        toggle_favorite: Callable[[ClipboardItem], tuple[bool, str | None]] | None = None,
        remove_favorite: Callable[[str], tuple[bool, str | None]] | None = None,
        reorder_favorites: Callable[[list[str]], tuple[bool, str | None]] | None = None,
        edit_item: Callable[[ClipboardItem, ClipboardItem], tuple[bool, str | None]] | None = None,
        load_blobs: Callable[[ClipboardItem], ClipboardItem] | None = None,
    ) -> None:
        super().__init__()
        self._on_activate = on_activate
        self._on_clear = on_clear
        self._on_open_settings = on_open_settings
        self._get_favorites = get_favorites
        self._toggle_favorite = toggle_favorite
        self._remove_favorite = remove_favorite
        self._reorder_favorites = reorder_favorites
        self._edit_item = edit_item
        self._load_blobs = load_blobs
        self._all_items: list[ClipboardItem] = []
        self._filtered_items: list[ClipboardItem] = []
        self._favorites: list[tuple[str, ClipboardItem]] = []
        self._fav_filtered: list[tuple[str, ClipboardItem]] = []
        self._all_page = 0
        self._fav_page = 0
        self._paused = False
        self._drag_pos: QPoint | None = None
        self._hover_preview = True
        self._active_category: str = "all"  # "all", "text", "image", "files", "link", "html", "rtf"

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

        # ── Category filter chips ──
        self._category_row = QHBoxLayout()
        self._category_row.setSpacing(6)
        self._category_row.setContentsMargins(0, 0, 0, 0)
        self._category_buttons: dict[str, QToolButton] = {}
        _categories = [
            ("all", "全部"),
            ("text", "文本"),
            ("image", "图片"),
            ("files", "文件"),
            ("link", "链接"),
            ("html", "HTML"),
            ("rtf", "RTF"),
        ]
        for cat_key, cat_label in _categories:
            btn = QToolButton(card)
            btn.setText(cat_label)
            btn.setObjectName("btnCategoryChip")
            btn.setCheckable(True)
            btn.setChecked(cat_key == "all")
            btn.setFixedHeight(26)
            btn.setMinimumWidth(40)
            btn.clicked.connect(lambda checked, k=cat_key: self._set_category(k))
            self._category_buttons[cat_key] = btn
            self._category_row.addWidget(btn)
        self._category_row.addStretch(1)

        self._tabs = QTabWidget(card)
        self._tabs.setObjectName("tabs")

        self._list_all = _ClipListWidget(self._get_filtered_item, card, load_blobs=self._load_blobs)
        self._list_all.setUniformItemSizes(True)
        self._delegate_all = _ClipItemDelegate(self._list_all, load_blobs=self._load_blobs)
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

        self._list_fav = _ClipListWidget(self._get_fav_filtered_item, card, load_blobs=self._load_blobs)
        self._list_fav.setUniformItemSizes(True)
        self._delegate_fav = _ClipItemDelegate(self._list_fav, load_blobs=self._load_blobs)
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
        self._title = QLabel("剪切板历史（吾爱破解）", card)
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

        self._btn_help = QToolButton(card)
        self._btn_help.setObjectName("btnHelp")
        self._btn_help.setText("?")
        self._btn_help.setToolTip("使用指南")
        self._btn_help.setFixedSize(32, 32)
        self._btn_help.clicked.connect(self._show_help)
        toolbar.addWidget(self._btn_help)

        body = QVBoxLayout(card)
        body.setContentsMargins(14, 12, 14, 14)
        body.setSpacing(8)
        body.addLayout(title_bar)
        body.addWidget(self._search)
        body.addLayout(self._category_row)
        body.addLayout(toolbar)
        body.addWidget(self._tabs, 1)
        body.addLayout(pager)
        body.addWidget(self._preview)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.addWidget(card)

        self.setMinimumSize(400, 350)
        # QSizeGrip must be child of the top-level widget (self), not card,
        # so its coordinate space matches self.width()/height() used in resizeEvent.
        self._grip = QSizeGrip(self)
        self._grip.raise_()
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
            # root layout has 10px margins for drop shadow; align grip with card corner
            self._grip.move(
                self.width() - grip_size.width() - 10,
                self.height() - grip_size.height() - 10,
            )
            self._grip.raise_()
        except Exception:
            pass
        self._render_popup_image()

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
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
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
            elif event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key_Control:
                self._sync_hover_popup_from_cursor()
            elif event.type() == QEvent.Type.KeyRelease and event.key() == Qt.Key_Control:
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
        return obj is self._current_list().viewport()

    def _hover_target_under_cursor(self) -> tuple[QListWidget, QListWidgetItem] | None:
        pos = QCursor.pos()
        widget = self._current_list()
        vp = widget.viewport()
        local = vp.mapFromGlobal(pos)
        if vp.rect().contains(local):
            it = widget.itemAt(local)
            if it is not None:
                return widget, it
        return None

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
        self._refresh_lists()

    def _refresh_lists(self) -> None:
        fav_ids = {fid for fid, _ in self._favorites}

        # Batch update with signals/painting suppressed for performance
        self._list_all.setUpdatesEnabled(False)
        self._list_all.blockSignals(True)
        self._list_all.clear()
        all_page = self._clamp_page_index(len(self._filtered_items), False)
        all_start = all_page * self._PAGE_SIZE
        all_end = all_start + self._PAGE_SIZE
        for it in self._filtered_items[all_start:all_end]:
            item = QListWidgetItem()
            item.setData(ROLE_ITEM, it)
            item.setData(ROLE_IS_FAVORITE, self._fav_id_for_item(it, fav_ids) is not None)
            item.setData(ROLE_TITLE, _clean_preview(it, 150))
            item.setData(ROLE_SUBTITLE, _secondary_text(it))
            self._list_all.addItem(item)
        self._list_all.blockSignals(False)
        self._list_all.setUpdatesEnabled(True)

        self._list_fav.setUpdatesEnabled(False)
        self._list_fav.blockSignals(True)
        self._list_fav.clear()
        fav_page = self._clamp_page_index(len(self._fav_filtered), True)
        fav_start = fav_page * self._PAGE_SIZE
        fav_end = fav_start + self._PAGE_SIZE
        for fid, it in self._fav_filtered[fav_start:fav_end]:
            item = QListWidgetItem()
            item.setData(ROLE_ITEM, it)
            item.setData(ROLE_FAV_ID, fid)
            item.setData(ROLE_IS_FAVORITE, True)
            item.setData(ROLE_TITLE, _favorite_title(it, 150))
            item.setData(ROLE_SUBTITLE, _secondary_text(it))
            self._list_fav.addItem(item)
        self._list_fav.blockSignals(False)
        self._list_fav.setUpdatesEnabled(True)

        current = self._current_list()
        if current.count() > 0:
            current.setCurrentRow(0)

        self._sync_tooltips()
        self._sync_pagination()
        self._update_preview()

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

    def _set_category(self, category: str) -> None:
        self._active_category = category
        for key, btn in self._category_buttons.items():
            btn.setChecked(key == category)
        self._all_page = 0
        self._fav_page = 0
        self._apply_filter()

    @staticmethod
    def _item_display_type(it: ClipboardItem) -> str:
        """Return the display category for an item (detects links in text)."""
        if it.item_type == "text" and it.text and _RE_URL.search(it.text):
            return "link"
        return it.item_type

    def _apply_filter(self) -> None:
        q = (self._search.text() or "").strip().lower()
        cat = self._active_category
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

        self._refresh_lists()

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

        # Load blobs on-demand for types that need them
        if it.item_type in ("image", "html", "rtf") and it.needs_blob_load:
            it = self._ensure_blobs(it)

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
        if it.item_type == "image":
            popup_w = min(640, max(420, self.width()))
            popup_h = 420
        else:
            popup_w = min(520, max(360, self.width() - 40))
            popup_h = 220
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
        item = self._list_all.item(row)
        if item is None:
            return None
        return item.data(ROLE_ITEM)

    def _get_fav_filtered_item(self, row: int) -> ClipboardItem | None:
        item = self._list_fav.item(row)
        if item is None:
            return None
        return item.data(ROLE_ITEM)

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
        row = widget.row(it)
        if row >= 0:
            widget.setCurrentRow(row)
        item_obj: ClipboardItem | None = it.data(ROLE_ITEM)
        if item_obj is None:
            return
        menu = QMenu(widget)
        act_fav = menu.addAction("收藏/取消收藏")
        act_edit = None
        if item_obj.item_type == "text":
            act_edit = menu.addAction("编辑文本")
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
        elif act_edit is not None and chosen == act_edit:
            self._edit_current_text()
        elif act_del is not None and chosen == act_del:
            self._remove_current_favorite()
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

        updated = replace(it, text=new_text)
        ok, msg = self._edit_item(it, updated)
        if not ok:
            QMessageBox.warning(self, "编辑失败", msg or "内容更新失败")
            return

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

        dlg.setStyleSheet(
            """
            QDialog { background: #FFFFFF; }
            QPlainTextEdit {
              border: 1px solid rgba(148, 163, 184, 0.65);
              border-radius: 8px;
              padding: 6px;
              background: #FFFFFF;
              color: #0F172A;
            }
            QPushButton {
              min-width: 72px;
              padding: 5px 12px;
              border-radius: 8px;
              border: 1px solid rgba(148, 163, 184, 0.45);
              background: #FFFFFF;
              color: #0F172A;
            }
            QPushButton:hover { background: #F8FAFC; }
            """
        )

        editor.selectAll()
        editor.setFocus()
        if dlg.exec() != QDialog.Accepted:
            return None
        return editor.toPlainText()

    def _sync_status(self) -> None:
        self._status.setText("暂停" if self._paused else "监听中")
        self._status.setProperty("paused", self._paused)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def _show_help(self) -> None:
        dlg = HelpDialog(self)
        dlg.exec()

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
                        QLabel#pageLabel {
                            color: #64748B;
                            font-size: 12px;
                            padding: 0 4px;
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
            QToolButton#btnCategoryChip {
              padding: 3px 10px;
              border-radius: 13px;
              border: 1px solid rgba(148, 163, 184, 0.5);
              background: #FFFFFF;
              color: #475569;
              font-size: 12px;
              font-weight: 500;
            }
            QToolButton#btnCategoryChip:hover {
              background: #F1F5F9;
              border: 1px solid rgba(148, 163, 184, 0.7);
              color: #1E293B;
            }
            QToolButton#btnCategoryChip:checked {
              background: #2563EB;
              border: 1px solid #1D4ED8;
              color: #FFFFFF;
              font-weight: 600;
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
            QToolButton#btnHelp {
              font-size: 14px;
              font-weight: 800;
              padding: 0px;
              border-radius: 16px;
              border: 1.5px solid rgba(37, 99, 235, 0.35);
              background: rgba(219, 234, 254, 0.55);
              color: #2563EB;
            }
            QToolButton#btnHelp:hover {
              background: rgba(37, 99, 235, 0.15);
              border: 1.5px solid #2563EB;
              color: #1D4ED8;
            }
            QToolButton#btnHelp:pressed {
              background: #2563EB;
              color: #FFFFFF;
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
