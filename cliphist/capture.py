from __future__ import annotations

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


def _capture_image_bytes(max_bytes: int = MAX_IMAGE_BYTES) -> tuple[bytes | None, int]:
    """返回 (图片字节, 超限大小)。超限大小 > 0 表示存在被跳过的过大图片。"""
    dibv5_fmt = getattr(win32con, "CF_DIBV5", 17)
    oversized = 0
    for fmt in (dibv5_fmt, win32con.CF_DIB):
        if not win32clipboard.IsClipboardFormatAvailable(fmt):
            continue
        dib = _to_bytes(win32clipboard.GetClipboardData(fmt))
        if not dib:
            continue
        if len(dib) > max_bytes:
            log.warning("Image payload exceeds limit (%d bytes), skipped", len(dib))
            oversized = len(dib)
            continue
        return dib, 0
    return None, oversized


def capture_clipboard(hwnd: int | None = None) -> ClipboardItem | OversizedImageNotice | None:
    with open_clipboard(hwnd):
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
            file_paths = tuple(win32clipboard.GetClipboardData(win32con.CF_HDROP))
            return ClipboardItem(
                created_at=ClipboardItem.now_utc(),
                item_type="files",
                file_paths=file_paths,
            )

        html_fmt = _get_html_fmt()
        if win32clipboard.IsClipboardFormatAvailable(html_fmt):
            raw_b = _to_bytes(win32clipboard.GetClipboardData(html_fmt))
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
            )

        rtf_fmt = _get_rtf_fmt()
        if win32clipboard.IsClipboardFormatAvailable(rtf_fmt):
            raw_b = _to_bytes(win32clipboard.GetClipboardData(rtf_fmt))
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
            )

        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            if isinstance(text, bytes):
                text = text.decode("utf-16-le", errors="replace")
            return ClipboardItem(
                created_at=ClipboardItem.now_utc(),
                item_type="text",
                text=str(text),
            )

        image_bytes, oversized = _capture_image_bytes(MAX_IMAGE_BYTES)
        if image_bytes:
            return ClipboardItem(
                created_at=ClipboardItem.now_utc(),
                item_type="image",
                raw_bytes=image_bytes,
            )
        if oversized:
            return OversizedImageNotice(size=oversized, limit=MAX_IMAGE_BYTES)

    return None
