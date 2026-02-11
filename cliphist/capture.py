from __future__ import annotations

import logging
from typing import Final

log = logging.getLogger(__name__)

import win32clipboard
import win32con

from .clipboard_util import open_clipboard
from .models import ClipboardItem
from .text_util import (
    html_fragment_preview as _extract_html_fragment_preview,
    rtf_to_plain_text as _rtf_preview,
)


HTML_FORMAT_NAME: Final[str] = "HTML Format"
RTF_FORMAT_NAME: Final[str] = "Rich Text Format"
MAX_IMAGE_BYTES: Final[int] = 50 * 1024 * 1024  # 50 MB

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


def _capture_image_bytes() -> bytes | None:
    dibv5_fmt = getattr(win32con, "CF_DIBV5", 17)
    for fmt in (dibv5_fmt, win32con.CF_DIB):
        if not win32clipboard.IsClipboardFormatAvailable(fmt):
            continue
        dib = _to_bytes(win32clipboard.GetClipboardData(fmt))
        if not dib:
            continue
        if len(dib) > MAX_IMAGE_BYTES:
            log.warning("图片超出大小限制 (%d bytes)，已跳过", len(dib))
            continue
        return dib
    return None


def capture_clipboard(hwnd: int | None = None) -> ClipboardItem | None:
    with open_clipboard(hwnd):
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
            file_paths = tuple(win32clipboard.GetClipboardData(win32con.CF_HDROP))
            return ClipboardItem(
                created_at=ClipboardItem.now_utc(),
                item_type="files",
                file_paths=file_paths,
            )

        image_bytes = _capture_image_bytes()

        html_fmt = _get_html_fmt()
        if win32clipboard.IsClipboardFormatAvailable(html_fmt):
            raw_b = _to_bytes(win32clipboard.GetClipboardData(html_fmt))
            if not raw_b:
                raw_b = b""
            preview = _extract_html_fragment_preview(raw_b)
            return ClipboardItem(
                created_at=ClipboardItem.now_utc(),
                item_type="html",
                text=preview,
                raw_bytes=raw_b,
                image_bytes=image_bytes,
            )

        rtf_fmt = _get_rtf_fmt()
        if win32clipboard.IsClipboardFormatAvailable(rtf_fmt):
            raw_b = _to_bytes(win32clipboard.GetClipboardData(rtf_fmt))
            if not raw_b:
                raw_b = b""
            preview = _rtf_preview(raw_b)
            return ClipboardItem(
                created_at=ClipboardItem.now_utc(),
                item_type="rtf",
                text=preview,
                raw_bytes=raw_b,
                image_bytes=image_bytes,
            )

        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            if isinstance(text, bytes):
                text = text.decode("utf-16-le", errors="replace")
            return ClipboardItem(
                created_at=ClipboardItem.now_utc(),
                item_type="text",
                text=str(text),
                image_bytes=image_bytes,
            )

        if image_bytes:
            return ClipboardItem(
                created_at=ClipboardItem.now_utc(),
                item_type="image",
                raw_bytes=image_bytes,
            )

    return None
