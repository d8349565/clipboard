"""Shared clipboard utilities used by capture and set_clipboard modules."""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager

import win32clipboard

log = logging.getLogger(__name__)


@contextmanager
def open_clipboard(hwnd: int | None, retries: int = 10, delay_s: float = 0.02):
    """Open the Windows clipboard with retry logic, yielding inside the lock."""
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
            log.debug("CloseClipboard 异常", exc_info=True)
