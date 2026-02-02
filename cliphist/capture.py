from __future__ import annotations

from contextlib import contextmanager
import time
from typing import Final

import win32clipboard
import win32con

from .models import ClipboardItem


HTML_FORMAT_NAME: Final[str] = "HTML Format"
RTF_FORMAT_NAME: Final[str] = "Rich Text Format"


@contextmanager
def _open_clipboard(hwnd: int | None, retries: int = 6, delay_s: float = 0.02):
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


def capture_clipboard(hwnd: int | None = None) -> ClipboardItem | None:
    with _open_clipboard(hwnd):
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
                return ClipboardItem(
                    created_at=ClipboardItem.now_utc(),
                    item_type="image",
                    raw_bytes=bytes(dib),
                )

        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_DIB):
            dib = win32clipboard.GetClipboardData(win32con.CF_DIB)
            if isinstance(dib, (bytes, bytearray)) and dib:
                return ClipboardItem(
                    created_at=ClipboardItem.now_utc(),
                    item_type="image",
                    raw_bytes=bytes(dib),
                )

        html_fmt = win32clipboard.RegisterClipboardFormat(HTML_FORMAT_NAME)
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

        rtf_fmt = win32clipboard.RegisterClipboardFormat(RTF_FORMAT_NAME)
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


def _extract_html_fragment_preview(raw: bytes, max_len: int = 400) -> str:
    try:
        header = raw[:4096].decode("ascii", errors="ignore")
        start_key = "StartFragment:"
        end_key = "EndFragment:"
        start_i = header.find(start_key)
        end_i = header.find(end_key)
        if start_i != -1 and end_i != -1:
            start_line = header[start_i : header.find("\n", start_i)].strip()
            end_line = header[end_i : header.find("\n", end_i)].strip()
            start = int(start_line.split(":", 1)[1].strip())
            end = int(end_line.split(":", 1)[1].strip())
            frag = raw[start:end]
            s = frag.decode("utf-8", errors="ignore")
            s = s.replace("\r\n", "\n").replace("\r", "\n").strip()
            return s[:max_len]
    except Exception:
        pass
    try:
        s = raw.decode("utf-8", errors="ignore").replace("\r\n", "\n").replace("\r", "\n").strip()
        return s[:max_len]
    except Exception:
        return "(HTML)"


def _rtf_preview(raw: bytes, max_len: int = 400) -> str:
    try:
        s = raw.decode("latin-1", errors="ignore").replace("\r\n", "\n").replace("\r", "\n").strip()
        return s[:max_len]
    except Exception:
        return "(RTF)"
