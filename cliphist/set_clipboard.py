from __future__ import annotations

from contextlib import contextmanager
import time

import win32clipboard
import win32con

from .capture import _extract_html_fragment_preview, _rtf_preview
from .models import ClipboardItem


HTML_FORMAT_NAME = "HTML Format"
RTF_FORMAT_NAME = "Rich Text Format"


@contextmanager
def _open_clipboard(hwnd: int | None, retries: int = 10, delay_s: float = 0.02):
    last_exc: Exception | None = None
    for _ in range(max(1, retries)):
        try:
            win32clipboard.OpenClipboard(hwnd)
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            time.sleep(delay_s)
    if last_exc is not None:
        raise last_exc
    try:
        yield
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass


def set_clipboard_item(item: ClipboardItem, hwnd: int | None = None) -> None:
    with _open_clipboard(hwnd):
        win32clipboard.EmptyClipboard()

        if item.item_type == "text":
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
            plain_text = item.text or ""
            if item.raw_bytes:
                plain_text = _extract_html_fragment_preview(item.raw_bytes, max_len=12000) or plain_text
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, plain_text)
            return

        if item.item_type == "rtf":
            fmt = win32clipboard.RegisterClipboardFormat(RTF_FORMAT_NAME)
            if item.raw_bytes:
                win32clipboard.SetClipboardData(fmt, item.raw_bytes)
            plain_text = item.text or ""
            if item.raw_bytes:
                plain_text = _rtf_preview(item.raw_bytes, max_len=12000) or plain_text
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, plain_text)
            return

        raise ValueError(f"unsupported item_type: {item.item_type}")
