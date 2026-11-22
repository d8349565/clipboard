from collections import OrderedDict
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

import cliphist.qt_app as qt_app_module
import cliphist.set_clipboard as clipboard_module
from cliphist.models import ClipboardItem
from cliphist.qt_app import ClipHistApp, _Activation, _BlobResult, _ClipboardWriteResult
from cliphist.store import ClipboardHistory


def _app():
    app = ClipHistApp.__new__(ClipHistApp)
    app._closing = False
    app.history = ClipboardHistory()
    app._activation = None
    app._queued_activation = None
    app._pending_blob_requests = {}
    app._blob_cache = OrderedDict()
    app._blob_cache_bytes = 0
    app._blob_cache_limit = 128 * 1024 * 1024
    app.panel = SimpleNamespace(set_status_hint=lambda *_: None, isVisible=lambda: False)
    return app


def test_repeated_paints_share_one_blob_request_and_refresh():
    app = _app()
    submitted, emitted, refreshed = [], [], []
    future = Future()
    app._store = SimpleNamespace(submit=lambda *args: (submitted.append(args), future)[1])
    app._bridge = SimpleNamespace(event=SimpleNamespace(emit=emitted.append))
    app.panel = SimpleNamespace(notify_blob_loaded=lambda: refreshed.append(True))
    item = ClipboardItem(ClipboardItem.now_utc(), "image", raw_bytes=b"dib").prepared().with_db_id(1).slim()
    app.history.add(item)
    for _ in range(100):
        app._request_blob_load(item, "ui")
    assert len(submitted) == 1
    assert app._pending_blob_requests[app._blob_key(item)] == {"ui": None}
    future.set_result((b"dib", None))
    app._handle_blob_result(emitted[0])
    assert refreshed == [True]


def test_missing_blob_cannot_activate_or_enter_cache():
    app = _app()
    errors, activated = [], []
    app._notify_error = lambda *args: errors.append(args)
    app._finish_activation = lambda *args: activated.append(args)
    item = ClipboardItem(ClipboardItem.now_utc(), "image", db_id=1, raw_size=4)
    key = app._blob_key(item)
    app._activation = _Activation(False, None)
    app._pending_blob_requests[key] = {"activate": app._activation}
    app._handle_blob_result(_BlobResult(key, item))
    assert errors
    assert not activated
    assert not app._blob_cache


def test_favorite_requests_blob_without_waiting_for_database():
    app = _app()
    requests = []
    app._request_blob_load = lambda *args: requests.append(args)
    item = ClipboardItem(ClipboardItem.now_utc(), "image", db_id=1, raw_size=4)
    assert app._toggle_favorite(item) == (True, None)
    assert requests == [(item, "favorite")]


def test_clipboard_write_returns_before_worker_finishes_and_recovers_on_failure():
    app = _app()
    submitted, emitted, errors = [], [], []
    future = Future()
    app._clipboard_write_pending = False
    app._clipboard_executor = SimpleNamespace(submit=lambda *args, **kwargs: (submitted.append(args), future)[1])
    app._bridge = SimpleNamespace(event=SimpleNamespace(emit=emitted.append))
    app._notify_error = lambda *args: errors.append(args)
    app.settings = SimpleNamespace(auto_paste=True)
    app.listener = SimpleNamespace(hwnd=123)
    app._previous_foreground_hwnd = 456
    item = ClipboardItem(ClipboardItem.now_utc(), "text", text="hello")
    app._activate_item(item, False)
    assert app._clipboard_write_pending
    assert not future.done()
    assert len(submitted) == 1
    future.set_exception(RuntimeError("clipboard busy"))
    app._handle_event(emitted[0])
    assert not app._clipboard_write_pending
    assert errors


def test_copy_only_supersedes_an_older_image_that_is_still_loading():
    app = _app()
    emitted, written = [], []
    blob_future, write_future = Future(), Future()
    app._store = SimpleNamespace(submit=lambda *_: blob_future)
    app._clipboard_executor = SimpleNamespace(
        submit=lambda _fn, item, **_kwargs: (written.append(item), write_future)[1]
    )
    app._bridge = SimpleNamespace(event=SimpleNamespace(emit=emitted.append))
    app._clipboard_write_pending = False
    app.settings = SimpleNamespace(auto_paste=True)
    app.listener = SimpleNamespace(hwnd=123)
    app._previous_foreground_hwnd = 456
    image = ClipboardItem(ClipboardItem.now_utc(), "image", db_id=1, raw_size=3)
    app.history.add(image)
    text = ClipboardItem(ClipboardItem.now_utc(), "text", text="latest copy")

    app._activate_item(image)
    image_activation = app._activation
    app._previous_foreground_hwnd = 789
    assert image_activation.target == 456
    app._activate_item(text, False)
    write_future.set_result(None)
    app._handle_event(emitted.pop(0))
    blob_future.set_result((b"dib", None))
    app._handle_event(emitted.pop(0))

    assert written == [text]


def test_copying_an_edited_record_does_not_reuse_old_text_from_the_blob_cache():
    app = _app()
    events, written = [], []
    original = ClipboardItem(
        ClipboardItem.now_utc(), "text", text="before edit", image_bytes=b"dib"
    ).prepared().with_db_id(1).slim()
    app.history.add(original)
    blob = Future()
    blob.set_result((None, b"dib"))
    app._store = SimpleNamespace(submit=lambda *_: blob)
    app._bridge = SimpleNamespace(event=SimpleNamespace(emit=events.append))
    app.panel.notify_blob_loaded = lambda: None
    app._load_blobs_for_ui(original)
    app._handle_event(events.pop(0))

    edited = replace(original, text="after edit", _fingerprint=None, _search_index=None).prepared()
    assert app.history.edit_item(original, edited, from_favorites=False) is not None
    app.settings = SimpleNamespace(auto_paste=False)
    app._previous_foreground_hwnd = None
    app._clipboard_write_pending = False
    app._clipboard_executor = SimpleNamespace(submit=lambda _fn, item, **_: (written.append(item), Future())[1])
    app.listener = SimpleNamespace(hwnd=123)
    app._activate_item(app.history.items()[0])
    while events:
        app._handle_event(events.pop(0))

    assert [(item.text, item.image_bytes) for item in written] == [("after edit", b"dib")]


def test_scheduled_paste_uses_the_original_window_and_stops_after_a_new_selection(monkeypatch):
    app = _app()
    timers, focused, keys = [], [], []
    monkeypatch.setattr(qt_app_module, "get_clipboard_sequence_number", lambda: 11)
    monkeypatch.setattr(qt_app_module, "QTimer", SimpleNamespace(singleShot=lambda _ms, callback: timers.append(callback)))
    monkeypatch.setattr(qt_app_module.win32gui, "IsWindow", lambda _: True)
    monkeypatch.setattr(qt_app_module.win32gui, "SetForegroundWindow", focused.append)
    monkeypatch.setattr(qt_app_module.win32gui, "GetForegroundWindow", lambda: 456)
    monkeypatch.setattr(qt_app_module.win32api, "GetAsyncKeyState", lambda _: 0)
    monkeypatch.setattr(qt_app_module.win32api, "keybd_event", lambda *args: keys.append(args))

    app._activation = _Activation(True, 456)
    app._previous_foreground_hwnd = 789
    app._handle_event(_ClipboardWriteResult(app._activation, sequence=11))
    timers.pop(0)()
    assert focused == [456]
    app._activation = _Activation(False, 789)
    timers.pop(0)()
    assert not keys


@pytest.mark.parametrize("change", ["external copy", "copy at close", "held modifier", "neither"])
def test_auto_paste_only_sends_keys_for_the_selected_clipboard_write(monkeypatch, change):
    app = _app()
    timers, keys, events, errors = [], [], [], []
    clipboard = SimpleNamespace(sequence=10, text=None, owner=None, written=False)

    @contextmanager
    def open_clipboard(hwnd):
        yield
        if clipboard.written:
            clipboard.written = False
            clipboard.sequence += 1  # Windows adds synthesized formats on close.
            clipboard.owner = hwnd
            if change == "copy at close":
                clipboard.owner = 789
                clipboard.sequence += 1
                clipboard.text = "copied immediately by another application"

    def write_text(_format, text):
        clipboard.text = text
        clipboard.sequence += 1
        clipboard.written = True

    def submit(write, *args, **kwargs):
        future = Future()
        future.set_result(write(*args, **kwargs))
        return future

    monkeypatch.setattr(clipboard_module, "open_clipboard", open_clipboard)
    monkeypatch.setattr(clipboard_module.win32clipboard, "EmptyClipboard", lambda: None)
    monkeypatch.setattr(clipboard_module.win32clipboard, "SetClipboardData", write_text)
    monkeypatch.setattr(clipboard_module.win32clipboard, "GetClipboardOwner", lambda: clipboard.owner)
    monkeypatch.setattr(clipboard_module, "get_clipboard_sequence_number", lambda: clipboard.sequence)
    monkeypatch.setattr(qt_app_module, "get_clipboard_sequence_number", lambda: clipboard.sequence)
    monkeypatch.setattr(qt_app_module, "QTimer", SimpleNamespace(singleShot=lambda _ms, callback: timers.append(callback)))
    monkeypatch.setattr(qt_app_module.win32gui, "IsWindow", lambda _: True)
    monkeypatch.setattr(qt_app_module.win32gui, "SetForegroundWindow", lambda _: None)
    monkeypatch.setattr(qt_app_module.win32gui, "GetForegroundWindow", lambda: 456)
    monkeypatch.setattr(qt_app_module.win32api, "GetAsyncKeyState", lambda _: 0x8000 if change == "held modifier" else 0)
    monkeypatch.setattr(qt_app_module.win32api, "keybd_event", lambda *args: keys.append(args))

    app._clipboard_write_pending = False
    app._clipboard_executor = SimpleNamespace(submit=submit)
    app._bridge = SimpleNamespace(event=SimpleNamespace(emit=events.append))
    app._notify_error = lambda *args: errors.append(args)
    app.settings = SimpleNamespace(auto_paste=True)
    app.listener = SimpleNamespace(hwnd=123)
    app._previous_foreground_hwnd = 456
    app._activate_item(ClipboardItem(ClipboardItem.now_utc(), "text", text="selected record"))
    if change != "copy at close":
        assert clipboard.text == "selected record"
    app._handle_event(events.pop(0))
    timers.pop(0)()  # Focus the original window before the delayed key press.
    if change == "external copy":
        clipboard.owner = 789
        clipboard.sequence += 1
        clipboard.text = "copied by another application"
    while timers:
        timers.pop(0)()

    if change == "neither":
        assert len(keys) == 4 and not errors
    else:
        assert not keys and errors
