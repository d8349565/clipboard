"""Shared clipboard utilities used by capture and set_clipboard modules."""
from __future__ import annotations

import ctypes
import logging
import time
from contextlib import contextmanager

import win32clipboard

log = logging.getLogger(__name__)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetClipboardSequenceNumber.argtypes = []
_user32.GetClipboardSequenceNumber.restype = ctypes.c_uint32


def get_clipboard_sequence_number() -> int | None:
    """Return the current Win32 clipboard sequence number, or None on failure."""
    try:
        seq = int(_user32.GetClipboardSequenceNumber())
        return seq if seq > 0 else None
    except Exception:
        return None


def clipboard_is_owned_by(hwnd: int) -> bool:
    """Check the owner while the caller holds the open clipboard."""
    return win32clipboard.GetClipboardOwner() == hwnd


@contextmanager
def open_clipboard(hwnd: int | None, retries: int = 10, delay_s: float = 0.02):
    """Open the Windows clipboard with retry logic, yielding inside the lock."""
    last_exc: Exception | None = None
    attempts = max(1, int(retries))
    delay_s = max(0.0, float(delay_s))
    for attempt in range(attempts):
        try:
            win32clipboard.OpenClipboard(hwnd)
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            # Do not sleep after the final failed attempt. This keeps the
            # bounded retry period predictable for background clipboard work.
            if attempt + 1 < attempts and delay_s:
                time.sleep(delay_s)
    if last_exc is not None:
        raise last_exc
    try:
        yield
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            log.debug("CloseClipboard 异常", exc_info=True)
