from __future__ import annotations

import logging

log = logging.getLogger(__name__)

import win32clipboard
import win32con

from .clipboard_util import open_clipboard
from .models import ClipboardItem
from .text_util import html_fragment_preview as _extract_html_fragment_preview
from .text_util import rtf_to_plain_text as _rtf_preview


HTML_FORMAT_NAME = "HTML Format"
RTF_FORMAT_NAME = "Rich Text Format"


def _set_image_if_any(item: ClipboardItem) -> None:
    if not item.image_bytes:
        return
    win32clipboard.SetClipboardData(win32con.CF_DIB, item.image_bytes)


def set_clipboard_item(item: ClipboardItem, hwnd: int | None = None) -> None:
    with open_clipboard(hwnd):
        win32clipboard.EmptyClipboard()

        if item.item_type == "text":
            _set_image_if_any(item)
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, item.text or "")
            return

        if item.item_type == "files":
            win32clipboard.SetClipboardData(win32con.CF_HDROP, list(item.file_paths or ()))
            return

        if item.item_type == "image":
            if not item.raw_bytes:
                return
            win32clipboard.SetClipboardData(win32con.CF_DIB, item.raw_bytes)
            return

        if item.item_type == "html":
            fmt = win32clipboard.RegisterClipboardFormat(HTML_FORMAT_NAME)
            if item.raw_bytes:
                win32clipboard.SetClipboardData(fmt, item.raw_bytes)
            _set_image_if_any(item)
            plain_text = item.text or ""
            if item.raw_bytes:
                plain_text = _extract_html_fragment_preview(item.raw_bytes, max_len=12000) or plain_text
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, plain_text)
            return

        if item.item_type == "rtf":
            fmt = win32clipboard.RegisterClipboardFormat(RTF_FORMAT_NAME)
            if item.raw_bytes:
                win32clipboard.SetClipboardData(fmt, item.raw_bytes)
            _set_image_if_any(item)
            plain_text = item.text or ""
            if item.raw_bytes:
                plain_text = _rtf_preview(item.raw_bytes, max_len=12000) or plain_text
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, plain_text)
            return

        raise ValueError(f"unsupported item_type: {item.item_type}")
