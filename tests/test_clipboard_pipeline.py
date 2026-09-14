from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
import win32con
import win32clipboard

import cliphist.capture as capture
import cliphist.set_clipboard as set_clipboard
import cliphist.win_listener as win_listener
from cliphist.clipboard_util import open_clipboard
from cliphist.models import ClipboardItem


def _image(payload: bytes) -> ClipboardItem:
    return ClipboardItem(datetime.now(timezone.utc), "image", raw_bytes=payload)


def test_image_capture_checks_hglobal_size_before_copying(monkeypatch) -> None:
    dibv5_fmt = getattr(win32con, "CF_DIBV5", 17)
    available = {dibv5_fmt, win32con.CF_DIB}
    copied_formats: list[int] = []

    monkeypatch.setattr(
        capture.win32clipboard,
        "IsClipboardFormatAvailable",
        lambda fmt: fmt in available,
    )
    monkeypatch.setattr(
        capture,
        "_clipboard_data_size",
        lambda fmt: 10 if fmt == dibv5_fmt else None,
    )

    def get_data(fmt: int) -> bytes:
        copied_formats.append(fmt)
        return b"dib"

    monkeypatch.setattr(capture.win32clipboard, "GetClipboardData", get_data)

    payload, oversized = capture._capture_image_bytes(max_bytes=4)

    assert payload == b"dib"
    assert oversized == 0
    assert copied_formats == [win32con.CF_DIB]


def test_image_capture_reports_largest_oversized_format_without_copy(monkeypatch) -> None:
    dibv5_fmt = getattr(win32con, "CF_DIBV5", 17)
    available = {dibv5_fmt, win32con.CF_DIB}
    get_data = Mock(side_effect=AssertionError("oversized HGLOBAL must not be copied"))

    monkeypatch.setattr(
        capture.win32clipboard,
        "IsClipboardFormatAvailable",
        lambda fmt: fmt in available,
    )
    monkeypatch.setattr(
        capture,
        "_clipboard_data_size",
        lambda fmt: 10 if fmt == dibv5_fmt else 7,
    )
    monkeypatch.setattr(capture.win32clipboard, "GetClipboardData", get_data)

    payload, oversized = capture._capture_image_bytes(max_bytes=4)

    assert payload is None
    assert oversized == 10
    get_data.assert_not_called()


def test_image_capture_falls_back_when_one_format_cannot_be_read(monkeypatch) -> None:
    dibv5_fmt = getattr(win32con, "CF_DIBV5", 17)
    monkeypatch.setattr(capture.win32clipboard, "IsClipboardFormatAvailable", lambda fmt: True)
    monkeypatch.setattr(capture, "_clipboard_data_size", lambda fmt: None)
    monkeypatch.setattr(
        capture.win32clipboard,
        "GetClipboardData",
        Mock(side_effect=[RuntimeError("provider busy"), b"dib"]),
    )

    payload, oversized = capture._capture_image_bytes(max_bytes=64)

    assert payload == b"dib"
    assert oversized == 0


def test_capture_returns_oversized_notice_without_history_item(monkeypatch) -> None:
    dibv5_fmt = getattr(win32con, "CF_DIBV5", 17)
    image_formats = {dibv5_fmt, win32con.CF_DIB}
    monkeypatch.setattr(capture, "open_clipboard", lambda hwnd: nullcontext())
    monkeypatch.setattr(capture, "_cached_html_fmt", 1001)
    monkeypatch.setattr(capture, "_cached_rtf_fmt", 1002)
    monkeypatch.setattr(
        capture.win32clipboard,
        "IsClipboardFormatAvailable",
        lambda fmt: fmt in image_formats,
    )
    monkeypatch.setattr(
        capture,
        "_clipboard_data_size",
        lambda fmt: capture.MAX_IMAGE_BYTES + (100 if fmt == dibv5_fmt else 90),
    )
    monkeypatch.setattr(capture.win32clipboard, "GetClipboardData", Mock())

    result = capture.capture_clipboard()

    assert isinstance(result, capture.OversizedImageNotice)
    assert result.size == capture.MAX_IMAGE_BYTES + 100
    assert result.limit == capture.MAX_IMAGE_BYTES


def test_capture_continues_to_image_when_rich_format_read_fails(monkeypatch) -> None:
    dibv5_fmt = getattr(win32con, "CF_DIBV5", 17)
    monkeypatch.setattr(capture, "open_clipboard", lambda hwnd: nullcontext())
    monkeypatch.setattr(capture, "_cached_html_fmt", 1001)
    monkeypatch.setattr(capture, "_cached_rtf_fmt", 1002)
    monkeypatch.setattr(
        capture.win32clipboard,
        "IsClipboardFormatAvailable",
        lambda fmt: fmt in {1001, dibv5_fmt},
    )
    monkeypatch.setattr(capture, "_clipboard_data_size", lambda fmt: None)
    monkeypatch.setattr(
        capture.win32clipboard,
        "GetClipboardData",
        Mock(side_effect=[RuntimeError("HTML provider busy"), b"dib"]),
    )

    result = capture.capture_clipboard()

    assert isinstance(result, ClipboardItem)
    assert result.item_type == "image"
    assert result.raw_bytes == b"dib"


def test_open_clipboard_does_not_sleep_after_final_failed_attempt(monkeypatch) -> None:
    open_clipboard_mock = Mock(side_effect=[RuntimeError("busy")] * 3)
    close_clipboard_mock = Mock()
    sleeps: list[float] = []
    monkeypatch.setattr(win32clipboard, "OpenClipboard", open_clipboard_mock)
    monkeypatch.setattr(win32clipboard, "CloseClipboard", close_clipboard_mock)
    monkeypatch.setattr("cliphist.clipboard_util.time.sleep", sleeps.append)

    with pytest.raises(RuntimeError, match="busy"):
        with open_clipboard(hwnd=1, retries=3, delay_s=0.01):
            pass

    assert open_clipboard_mock.call_count == 3
    assert sleeps == [0.01, 0.01]
    close_clipboard_mock.assert_not_called()


def test_set_image_rejects_missing_blob_before_emptying_clipboard(monkeypatch) -> None:
    empty = Mock()
    monkeypatch.setattr(set_clipboard, "open_clipboard", Mock(side_effect=AssertionError("must not open")))
    monkeypatch.setattr(set_clipboard.win32clipboard, "EmptyClipboard", empty)

    with pytest.raises(set_clipboard.ClipboardPayloadError, match="不可用"):
        set_clipboard.set_clipboard_item(_image(b""))

    empty.assert_not_called()


def test_set_image_rejects_oversized_blob_before_emptying_clipboard(monkeypatch) -> None:
    empty = Mock()
    monkeypatch.setattr(set_clipboard, "MAX_IMAGE_BYTES", 4)
    monkeypatch.setattr(set_clipboard, "open_clipboard", Mock(side_effect=AssertionError("must not open")))
    monkeypatch.setattr(set_clipboard.win32clipboard, "EmptyClipboard", empty)

    with pytest.raises(set_clipboard.ClipboardPayloadError, match="超过上限"):
        set_clipboard.set_clipboard_item(_image(b"12345"))

    empty.assert_not_called()


def test_set_image_preserves_original_dib_bytes(monkeypatch) -> None:
    calls: list[tuple[int, object]] = []
    monkeypatch.setattr(set_clipboard, "open_clipboard", lambda hwnd: nullcontext())
    monkeypatch.setattr(set_clipboard.win32clipboard, "EmptyClipboard", lambda: calls.append((-1, None)))
    monkeypatch.setattr(
        set_clipboard.win32clipboard,
        "SetClipboardData",
        lambda fmt, value: calls.append((fmt, value)),
    )

    payload = b"DIBV5-payload-without-reencoding"
    set_clipboard.set_clipboard_item(_image(payload))

    assert calls == [(-1, None), (win32con.CF_DIB, payload)]


def test_listener_ignores_cancelled_timer_generation(monkeypatch) -> None:
    timers = []

    class FakeTimer:
        def __init__(self, interval, callback, args=()):
            self.interval = interval
            self.callback = callback
            self.args = args
            self.cancelled = False
            timers.append(self)

        def cancel(self):
            self.cancelled = True

        def start(self):
            pass

    monkeypatch.setattr(win_listener.threading, "Timer", FakeTimer)
    capture_mock = Mock(return_value=None)
    monkeypatch.setattr(win_listener, "capture_clipboard", capture_mock)

    listener = win_listener.ClipboardListener(Mock())
    listener._hwnd = 123
    listener._stop_event.clear()
    listener._schedule_capture()
    listener._schedule_capture()

    assert timers[0].cancelled
    timers[0].callback(*timers[0].args)
    capture_mock.assert_not_called()
    timers[1].callback(*timers[1].args)
    capture_mock.assert_called_once_with(hwnd=123)
