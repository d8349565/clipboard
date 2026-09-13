from __future__ import annotations

import ctypes
import logging
from dataclasses import dataclass
from typing import Final

log = logging.getLogger(__name__)

import win32clipboard
import win32con

from .clipboard_util import open_clipboard
from .models import ClipboardItem, MAX_IMAGE_BYTES, MAX_RICH_RAW_BYTES
from .text_util import (
    html_fragment_preview as _extract_html_fragment_preview,
    rtf_to_plain_text as _rtf_preview,
)


HTML_FORMAT_NAME: Final[str] = "HTML Format"
RTF_FORMAT_NAME: Final[str] = "Rich Text Format"


@dataclass(frozen=True, slots=True)
class OversizedImageNotice:
    """剪贴板中存在超过大小上限的图片，已跳过记录。用于提示用户而非静默丢弃。"""

    size: int
    limit: int

_cached_html_fmt: int | None = None
_cached_rtf_fmt: int | None = None


def _get_html_fmt() -> int:
    global _cached_html_fmt
    if _cached_html_fmt is None:
        _cached_html_fmt = win32clipboard.RegisterClipboardFormat(HTML_FORMAT_NAME)
    return _cached_html_fmt


def _get_rtf_fmt() -> int:
    global _cached_rtf_fmt
    if _cached_rtf_fmt is None:
        _cached_rtf_fmt = win32clipboard.RegisterClipboardFormat(RTF_FORMAT_NAME)
    return _cached_rtf_fmt


def _to_bytes(v: object) -> bytes | None:
    if isinstance(v, bytes):
        return v
    if isinstance(v, bytearray):
        return bytes(v)
    if isinstance(v, memoryview):
        return v.tobytes()
    if isinstance(v, str):
        return v.encode("utf-8", errors="replace")
    try:
        return bytes(v)  # type: ignore[arg-type]
    except Exception:
        return None


def _global_memory_size(handle: object) -> int | None:
    """Return a clipboard HGLOBAL's allocated size without copying it.

    ``GetClipboardData`` materializes the whole payload. For bitmap formats
    that can be surprisingly large, so use the borrowed clipboard handle as a
    cheap preflight when the Win32 APIs are available. The handle remains
    owned by the clipboard and is never freed here.
    """
    if not handle:
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        global_size = kernel32.GlobalSize
        global_size.argtypes = [ctypes.c_void_p]
        global_size.restype = ctypes.c_size_t
        size = int(global_size(ctypes.c_void_p(int(handle))))
        return size if size > 0 else None
    except Exception:
        # Some clipboard providers use delayed rendering or return a handle
        # that is not an HGLOBAL. Fall back to GetClipboardData in that case.
        log.debug("读取剪贴板全局内存大小失败", exc_info=True)
        return None


def _clipboard_data_size(fmt: int) -> int | None:
    """Best-effort size lookup for a clipboard format's HGLOBAL payload."""
    get_handle = getattr(win32clipboard, "GetClipboardDataHandle", None)
    if get_handle is None:
        return None
    try:
        return _global_memory_size(get_handle(fmt))
    except Exception:
        log.debug("读取剪贴板格式句柄失败: %s", fmt, exc_info=True)
        return None


def _capture_image_bytes(max_bytes: int = MAX_IMAGE_BYTES) -> tuple[bytes | None, int]:
    """返回 (图片字节, 超限大小)。超限大小 > 0 表示存在被跳过的过大图片。"""
    dibv5_fmt = getattr(win32con, "CF_DIBV5", 17)
    oversized = 0
    for fmt in (dibv5_fmt, win32con.CF_DIB):
        if not win32clipboard.IsClipboardFormatAvailable(fmt):
            continue

        # Avoid materializing a large bitmap solely to discover that it is
        # over the application limit. This is best effort because delayed
        # rendering and non-HGLOBAL providers may not expose a size.
        allocated_size = _clipboard_data_size(fmt)
        if allocated_size is not None and allocated_size > max_bytes:
            log.warning("Image payload exceeds limit (%d bytes), skipped", allocated_size)
            oversized = max(oversized, allocated_size)
            continue

        try:
            dib = _to_bytes(win32clipboard.GetClipboardData(fmt))
        except Exception:
            log.debug("读取剪贴板图片格式失败: %s", fmt, exc_info=True)
            continue
        if not dib:
            continue
        if len(dib) > max_bytes:
            log.warning("Image payload exceeds limit (%d bytes), skipped", len(dib))
            oversized = max(oversized, len(dib))
            continue
        return dib, 0
    return None, oversized


def capture_clipboard(hwnd: int | None = None) -> ClipboardItem | OversizedImageNotice | None:
    with open_clipboard(hwnd):
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
            try:
                file_paths = tuple(win32clipboard.GetClipboardData(win32con.CF_HDROP))
            except Exception:
                log.debug("读取剪贴板文件列表失败", exc_info=True)
            else:
                return ClipboardItem(
                    created_at=ClipboardItem.now_utc(),
                    item_type="files",
                    file_paths=file_paths,
                ).prepared()

        html_fmt = _get_html_fmt()
        if win32clipboard.IsClipboardFormatAvailable(html_fmt):
            try:
                raw_b = _to_bytes(win32clipboard.GetClipboardData(html_fmt))
            except Exception:
                log.debug("读取剪贴板 HTML 失败", exc_info=True)
            else:
                if not raw_b:
                    raw_b = b""
                preview = _extract_html_fragment_preview(raw_b)
                keep_raw = raw_b
                if len(raw_b) > MAX_RICH_RAW_BYTES:
                    log.warning("HTML payload exceeds limit (%d bytes), keep preview only", len(raw_b))
                    keep_raw = None
                return ClipboardItem(
                    created_at=ClipboardItem.now_utc(),
                    item_type="html",
                    text=preview,
                    raw_bytes=keep_raw,
                ).prepared()

        rtf_fmt = _get_rtf_fmt()
        if win32clipboard.IsClipboardFormatAvailable(rtf_fmt):
            try:
                raw_b = _to_bytes(win32clipboard.GetClipboardData(rtf_fmt))
            except Exception:
                log.debug("读取剪贴板 RTF 失败", exc_info=True)
            else:
                if not raw_b:
                    raw_b = b""
                preview = _rtf_preview(raw_b)
                keep_raw = raw_b
                if len(raw_b) > MAX_RICH_RAW_BYTES:
                    log.warning("RTF payload exceeds limit (%d bytes), keep preview only", len(raw_b))
                    keep_raw = None
                return ClipboardItem(
                    created_at=ClipboardItem.now_utc(),
                    item_type="rtf",
                    text=preview,
                    raw_bytes=keep_raw,
                ).prepared()

        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            try:
                text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            except Exception:
                log.debug("读取剪贴板文本失败", exc_info=True)
            else:
                if isinstance(text, bytes):
                    text = text.decode("utf-16-le", errors="replace")
                return ClipboardItem(
                    created_at=ClipboardItem.now_utc(),
                    item_type="text",
                    text=str(text),
                ).prepared()

        image_bytes, oversized = _capture_image_bytes(MAX_IMAGE_BYTES)
        if image_bytes:
            return ClipboardItem(
                created_at=ClipboardItem.now_utc(),
                item_type="image",
                raw_bytes=image_bytes,
            ).prepared()
        if oversized:
            return OversizedImageNotice(size=oversized, limit=MAX_IMAGE_BYTES)

    return None
