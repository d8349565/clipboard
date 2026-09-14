from collections import OrderedDict
from concurrent.futures import Future
from types import SimpleNamespace

from cliphist.models import ClipboardItem
from cliphist.qt_app import ClipHistApp, _BlobResult


def _app():
    app = ClipHistApp.__new__(ClipHistApp)
    app._closing = False
    app._pending_blob_requests = {}
    app._blob_cache = OrderedDict()
    app._blob_cache_bytes = 0
    app._blob_cache_limit = 128 * 1024 * 1024
    return app


def test_repeated_paints_share_one_blob_request_and_refresh():
    app = _app()
    submitted, emitted, refreshed = [], [], []
    future = Future()
    app._store = SimpleNamespace(submit=lambda *args: (submitted.append(args), future)[1])
    app._bridge = SimpleNamespace(event=SimpleNamespace(emit=emitted.append))
    app.panel = SimpleNamespace(notify_blob_loaded=lambda: refreshed.append(True))
    item = ClipboardItem(ClipboardItem.now_utc(), "image", raw_bytes=b"dib").prepared().with_db_id(1).slim()
    for _ in range(100):
        app._request_blob_load(item, "ui")
    assert len(submitted) == 1
    assert app._pending_blob_requests[app._blob_key(item)] == [("ui", None)]
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
    app._pending_blob_requests[key] = [("activate", False)]
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
    app._finish_activation(item, False)
    assert app._clipboard_write_pending
    assert not future.done()
    app._finish_activation(item, False)
    assert len(submitted) == 1
    future.set_exception(RuntimeError("clipboard busy"))
    app._handle_event(emitted[0])
    assert not app._clipboard_write_pending
    assert errors
