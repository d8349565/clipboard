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


def capture_clipboard(hwnd: int | None = None) -> ClipboardItem | None:
    with open_clipboard(hwnd):
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
            file_paths = tuple(win32clipboard.GetClipboardData(win32con.CF_HDROP))
            return ClipboardItem(
                created_at=ClipboardItem.now_utc(),
                item_type="files",
                file_paths=file_paths,
            )

        if win32clipboard.IsClipboardFormatAvailable(getattr(win32con, "CF_DIBV5", 17)):
            dib = win32clipboard.GetClipboardData(getattr(win32con, "CF_DIBV5", 17))
            if isinstance(dib, (bytes, bytearray)) and dib:
                if len(dib) > MAX_IMAGE_BYTES:
                    log.warning("\u56fe\u7247\u8d85\u51fa\u5927\u5c0f\u9650\u5236 (%d bytes)\uff0c\u5df2\u8df3\u8fc7", len(dib))
                else:
                    return ClipboardItem(
                        created_at=ClipboardItem.now_utc(),
                        item_type="image",
                        raw_bytes=bytes(dib),
                    )

        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_DIB):
            dib = win32clipboard.GetClipboardData(win32con.CF_DIB)
            if isinstance(dib, (bytes, bytearray)) and dib:
                if len(dib) > MAX_IMAGE_BYTES:
                    log.warning("\u56fe\u7247\u8d85\u51fa\u5927\u5c0f\u9650\u5236 (%d bytes)\uff0c\u5df2\u8df3\u8fc7", len(dib))
                else:
                    return ClipboardItem(
                        created_at=ClipboardItem.now_utc(),
                        item_type="image",
                        raw_bytes=bytes(dib),
                    )

        html_fmt = _get_html_fmt()
        if win32clipboard.IsClipboardFormatAvailable(html_fmt):
            raw = win32clipboard.GetClipboardData(html_fmt)
            raw_b = raw if isinstance(raw, (bytes, bytearray)) else bytes(raw)
            preview = _extract_html_fragment_preview(raw_b)
            return ClipboardItem(
                created_at=ClipboardItem.now_utc(),
                item_type="html",
                text=preview,
                raw_bytes=raw_b,
            )

        rtf_fmt = _get_rtf_fmt()
        if win32clipboard.IsClipboardFormatAvailable(rtf_fmt):
            raw = win32clipboard.GetClipboardData(rtf_fmt)
            raw_b = raw if isinstance(raw, (bytes, bytearray)) else bytes(raw)
            preview = _rtf_preview(raw_b)
            return ClipboardItem(
                created_at=ClipboardItem.now_utc(),
                item_type="rtf",
                text=preview,
                raw_bytes=raw_b,
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

    return None
