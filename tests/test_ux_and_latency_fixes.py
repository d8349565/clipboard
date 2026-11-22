from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import win32con
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import cliphist.capture as capture
import cliphist.favorites as favorites_module
import cliphist.qt_app as qt_app_module
import cliphist.settings as settings_module
import cliphist.win_listener as win_listener
from cliphist.favorites import FavoritesStore
from cliphist.hotkeys import parse_hotkey_sequence
from cliphist.models import ClipboardItem
from cliphist.persistence import AsyncSQLiteHistoryStore, SQLiteHistoryStore
from cliphist.qt_app import (
    HOTKEY_TOGGLE_PANEL,
    HOTKEY_TOGGLE_PAUSE,
    ClipHistApp,
    _Activation,
    _BlobResult,
    _ClipboardWriteResult,
)
from cliphist.settings import AppSettings, load_settings
from cliphist.store import ClipboardHistory
from cliphist.text_util import rtf_to_plain_text
from cliphist.ui_panel import ROLE_TITLE, ClipPanel
from cliphist.ui_settings import SettingsDialog


def test_lazy_image_wait_does_not_spin_repaint_loop() -> None:
    app = QApplication.instance() or QApplication([])
    calls: list[int | None] = []

    def slow_loader(item: ClipboardItem) -> ClipboardItem:
        calls.append(item.db_id)
        return item  # simulates async DB read still in flight

    now = datetime.now(timezone.utc)
    items = [
        ClipboardItem(now, "image", raw_bytes=None, raw_size=1024, db_id=i + 1, _fingerprint=f"img-{i}")
        for i in range(5)
    ]
    panel = ClipPanel(on_activate=lambda *_: None, load_blobs=slow_loader)
    try:
        panel.apply_configured_size(640, 620)
        panel.set_data(items, [])
        panel._show_near_cursor()
        deadline = time.monotonic() + 0.15
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        # Each visible image should trigger at most one load request while in flight.
        assert len(calls) == len(set(calls))
        assert 1 <= len(calls) <= 5
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_failed_blob_does_not_retrigger_ui_load_loop() -> None:
    app = ClipHistApp.__new__(ClipHistApp)
    app._closing = False
    app._pending_blob_requests = {}
    app._failed_blob_keys = OrderedDict()
    app._blob_cache = OrderedDict()
    app._blob_cache_bytes = 0
    app._blob_cache_limit = 128 * 1024 * 1024
    submitted: list[tuple] = []
    emitted: list[object] = []
    errors: list[tuple] = []

    def submit(*args):
        fut = Future()
        submitted.append(args)
        fut.set_result((None, None))
        return fut

    app._store = SimpleNamespace(submit=submit)
    app._bridge = SimpleNamespace(event=SimpleNamespace(emit=emitted.append))
    app._notify_error = lambda *args: errors.append(args)
    app.panel = SimpleNamespace(notify_blob_loaded=lambda: None, set_status_hint=lambda *_: None)

    item = ClipboardItem(ClipboardItem.now_utc(), "image", db_id=99, raw_size=128)
    app.history = ClipboardHistory()
    app.history.add(item)
    app._request_blob_load(item, "ui")
    assert len(submitted) == 1
    app._handle_blob_result(emitted.pop())
    assert app._blob_key(item) in app._failed_blob_keys

    # Subsequent UI paint requests must not spam the DB.
    app._request_blob_load(item, "ui")
    assert len(submitted) == 1

    # Explicit user activation clears the failed mark and retries once.
    app._activation = _Activation(False, None)
    app._request_blob_load(item, "activate", app._activation)
    assert len(submitted) == 2
    app._handle_blob_result(emitted.pop())
    assert errors

    qt = QApplication.instance() or QApplication([])
    panel = ClipPanel(
        on_activate=lambda *_: None,
        load_blobs=app._load_blobs_for_ui,
        blob_load_failed=lambda candidate: app._blob_key(candidate) in app._failed_blob_keys,
    )
    try:
        panel.set_data([item], [])
        panel._show_near_cursor()
        panel._toggle_preview()
        assert panel._popup_image.text() == "图片预览不可用"
        qt.processEvents()
        assert len(submitted) == 2
    finally:
        panel.close()
        panel.deleteLater()
        qt.processEvents()


def test_favorites_save_does_not_block_entries_read_during_blob_write(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(favorites_module, "default_app_dir", lambda: str(tmp_path))
    store = FavoritesStore()
    item = ClipboardItem(ClipboardItem.now_utc(), "image", raw_bytes=b"payload-bytes").prepared()
    store.toggle(item)

    in_write = threading.Event()
    release_write = threading.Event()
    original_write = store._write_blob

    def slow_write(data: bytes) -> str:
        in_write.set()
        release_write.wait(timeout=2.0)
        return original_write(data)

    monkeypatch.setattr(store, "_write_blob", slow_write)
    worker = threading.Thread(target=store.save)
    worker.start()
    try:
        assert in_write.wait(timeout=1.0)
        t0 = time.perf_counter()
        entries = store.entries
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert len(entries) == 1
        assert elapsed_ms < 50.0
    finally:
        release_write.set()
        worker.join(timeout=2.0)


def test_rtf_skips_pict_and_stops_early_for_preview() -> None:
    rtf = b"{\\rtf1\\ansi {\\pict " + (b"a1b2c3d4" * 250_000) + b"} Hello " + (b"word " * 5000) + b"}"
    t0 = time.perf_counter()
    text = rtf_to_plain_text(rtf, max_len=100)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert text.startswith("Hello word")
    assert len(text) == 100
    assert elapsed_ms < 100.0


def test_rtf_preview_counts_visible_text_after_long_whitespace() -> None:
    rtf = b"{\\rtf1\\ansi " + (b"\\par " * 1500) + b"Alpha" + (b" " * 1500) + b"Beta}"
    assert rtf_to_plain_text(rtf) == "Alpha Beta"


def test_register_hotkey_uses_mod_norepeat(monkeypatch) -> None:
    calls: list[tuple[int, int, int, int]] = []
    monkeypatch.setattr(
        win_listener.user32,
        "RegisterHotKey",
        lambda hwnd, hid, mods, vk: (calls.append((hwnd, hid, mods, vk)) or True),
    )
    listener = win_listener.ClipboardListener(Mock())
    lparam = (win32con.MOD_ALT & 0xFFFF) | ((ord("C") & 0xFFFF) << 16)
    res = listener._wnd_proc(100, win_listener.WM_APP_REGISTER_HOTKEY, 1, lparam)
    assert res == 0
    assert calls == [(100, 1, win32con.MOD_ALT | win_listener.MOD_NOREPEAT, ord("C"))]


@pytest.mark.parametrize("can_recover", [False, True])
def test_hotkey_restore_keeps_working_bindings_and_reports_the_fallback(can_recover) -> None:
    app = ClipHistApp.__new__(ClipHistApp)
    app._hotkey_specs = {}
    app.listener = SimpleNamespace(
        unregister_hotkey=lambda *_: None,
        register_hotkey_with_error=lambda hotkey_id, modifiers, _vk: (
            (True, 0)
            if hotkey_id == HOTKEY_TOGGLE_PANEL or (can_recover and modifiers != win32con.MOD_ALT)
            else (False, 1409)
        ),
    )
    errors = []
    app._notify_error = lambda *args: errors.append(args)
    panel_hotkey = parse_hotkey_sequence("Alt+C")
    app._restore_hotkeys(panel_hotkey, parse_hotkey_sequence("Alt+P"))

    assert app._hotkey_specs[HOTKEY_TOGGLE_PANEL] == panel_hotkey
    assert app.hotkey_pause_hint == ("Ctrl+Shift+P" if can_recover else None)
    assert len(errors) == 1 and "暂停热键注册失败" in errors[0][1]


def test_listener_checks_which_clipboard_it_actually_opens(monkeypatch) -> None:
    clipboard = SimpleNamespace(owner=789, owner_on_open=123, text="own write")

    @contextmanager
    def open_clipboard(_hwnd):
        clipboard.owner = clipboard.owner_on_open
        yield

    monkeypatch.setattr(capture, "open_clipboard", open_clipboard)
    monkeypatch.setattr(capture.win32clipboard, "GetClipboardOwner", lambda: clipboard.owner)
    monkeypatch.setattr(capture, "_cached_html_fmt", 1001)
    monkeypatch.setattr(capture, "_cached_rtf_fmt", 1002)
    monkeypatch.setattr(capture.win32clipboard, "IsClipboardFormatAvailable", lambda fmt: fmt == win32con.CF_UNICODETEXT)
    monkeypatch.setattr(capture.win32clipboard, "GetClipboardData", lambda _: clipboard.text)

    captured = []
    listener = win_listener.ClipboardListener(captured.append)
    listener._hwnd = 123
    listener._stop_event.clear()

    listener._do_capture()
    listener._do_capture()
    assert not captured

    # An external update during OpenClipboard is captured even if the prior owner was our own window.
    clipboard.owner_on_open = 789
    clipboard.text = "external copy"
    listener._do_capture()
    assert [item.text for item in captured] == ["external copy"]


def test_capture_prefers_image_over_image_only_html(monkeypatch) -> None:
    dibv5_fmt = getattr(win32con, "CF_DIBV5", 17)
    monkeypatch.setattr(capture, "open_clipboard", lambda hwnd: nullcontext())
    monkeypatch.setattr(capture, "_cached_html_fmt", 1001)
    monkeypatch.setattr(capture, "_cached_rtf_fmt", 1002)
    monkeypatch.setattr(capture, "_clipboard_data_size", lambda fmt: None)
    monkeypatch.setattr(
        capture.win32clipboard,
        "IsClipboardFormatAvailable",
        lambda fmt: fmt in {1001, dibv5_fmt},
    )
    frag = b'<img src="https://x.png">'
    header = f"Version:0.9\r\nStartFragment:00000060\r\nEndFragment:{60 + len(frag):08d}\r\n".encode("ascii")
    html_img_only = header.ljust(60, b" ") + frag
    monkeypatch.setattr(
        capture.win32clipboard,
        "GetClipboardData",
        lambda fmt: html_img_only if fmt == 1001 else b"bitmap-bytes",
    )

    result = capture.capture_clipboard()
    assert isinstance(result, ClipboardItem)
    assert result.item_type == "image"
    assert result.raw_bytes == b"bitmap-bytes"

    # Text-bearing HTML should still be captured as HTML.
    text_frag = b"<p>Hello <b>world</b></p>"
    text_header = f"Version:0.9\r\nStartFragment:00000060\r\nEndFragment:{60 + len(text_frag):08d}\r\n".encode("ascii")
    html_with_text = text_header.ljust(60, b" ") + text_frag
    monkeypatch.setattr(
        capture.win32clipboard,
        "GetClipboardData",
        lambda fmt: html_with_text if fmt == 1001 else b"bitmap-bytes",
    )
    text_result = capture.capture_clipboard()
    assert isinstance(text_result, ClipboardItem)
    assert text_result.item_type == "html"
    assert text_result.text == "Hello world"


@pytest.mark.parametrize("kind,payload", [("html", b'<img src="picture.png">'), ("rtf", b"{\\rtf1{\\pict 0123}}")])
@pytest.mark.parametrize("oversized", [False, True])
def test_capture_keeps_rich_format_when_advertised_bitmap_is_unusable(monkeypatch, kind, payload, oversized) -> None:
    rich_format = 1001 if kind == "html" else 1002
    bitmap_format = win32con.CF_DIB
    monkeypatch.setattr(capture, "open_clipboard", lambda _: nullcontext())
    monkeypatch.setattr(capture, "_cached_html_fmt", 1001)
    monkeypatch.setattr(capture, "_cached_rtf_fmt", 1002)
    monkeypatch.setattr(capture.win32clipboard, "IsClipboardFormatAvailable", {rich_format, bitmap_format}.__contains__)
    monkeypatch.setattr(
        capture, "_clipboard_data_size",
        lambda fmt: capture.MAX_IMAGE_BYTES + 1 if oversized and fmt == bitmap_format else None,
    )
    get_data = Mock(side_effect=[payload, OSError("bitmap provider failed")])
    monkeypatch.setattr(capture.win32clipboard, "GetClipboardData", get_data)

    result = capture.capture_clipboard()
    assert isinstance(result, ClipboardItem)
    assert (result.item_type, result.raw_bytes) == (kind, payload)
    assert get_data.call_count == (1 if oversized else 2)


def test_capture_skips_oversized_rtf_for_lighter_format(monkeypatch) -> None:
    monkeypatch.setattr(capture, "open_clipboard", lambda hwnd: nullcontext())
    monkeypatch.setattr(capture, "_cached_html_fmt", 1001)
    monkeypatch.setattr(capture, "_cached_rtf_fmt", 1002)
    monkeypatch.setattr(
        capture.win32clipboard,
        "IsClipboardFormatAvailable",
        lambda fmt: fmt in {1002, win32con.CF_UNICODETEXT},
    )
    monkeypatch.setattr(
        capture,
        "_clipboard_data_size",
        lambda fmt: capture.MAX_RICH_RAW_BYTES + 1 if fmt == 1002 else None,
    )
    read_formats: list[int] = []

    def fake_get(fmt: int):
        read_formats.append(fmt)
        if fmt == 1002:
            raise AssertionError("oversized RTF must not be copied")
        return "plain fallback text"

    monkeypatch.setattr(capture.win32clipboard, "GetClipboardData", fake_get)
    result = capture.capture_clipboard()
    assert isinstance(result, ClipboardItem)
    assert result.item_type == "text"
    assert result.text == "plain fallback text"
    assert read_formats == [win32con.CF_UNICODETEXT]


@pytest.mark.parametrize("kind, empty_text", [("html", False), ("rtf", True)])
def test_capture_reports_oversized_rich_text_when_no_usable_fallback_exists(monkeypatch, kind, empty_text):
    rich_format = 1001 if kind == "html" else 1002
    available = {rich_format}
    if empty_text:
        available.add(win32con.CF_UNICODETEXT)
    monkeypatch.setattr(capture, "open_clipboard", lambda _: nullcontext())
    monkeypatch.setattr(capture, "_cached_html_fmt", 1001)
    monkeypatch.setattr(capture, "_cached_rtf_fmt", 1002)
    monkeypatch.setattr(capture.win32clipboard, "IsClipboardFormatAvailable", available.__contains__)
    monkeypatch.setattr(capture, "_clipboard_data_size", lambda _: capture.MAX_RICH_RAW_BYTES + 1024)
    read_data = Mock(return_value="  ")
    monkeypatch.setattr(capture.win32clipboard, "GetClipboardData", read_data)

    notice = capture.capture_clipboard()
    assert notice is not None and not isinstance(notice, ClipboardItem)
    assert notice.item_type == kind
    assert notice.limit == capture.MAX_RICH_RAW_BYTES
    assert read_data.call_count == int(empty_text)

    messages = []
    host = ClipHistApp.__new__(ClipHistApp)
    host.paused = False
    host.tray = SimpleNamespace(showMessage=lambda _title, detail, *_: messages.append(detail))
    host._handle_event(notice)
    assert len(messages) == 1 and f"复制的 {kind.upper()}" in messages[0] and "未加入历史" in messages[0]


@pytest.mark.parametrize("kind, raw", [("html", b"<p>(HTML)</p>"), ("rtf", b"{\\rtf1 (RTF)}")])
def test_capture_keeps_visible_rich_text_that_looks_like_a_placeholder(monkeypatch, kind, raw):
    rich_format = 1001 if kind == "html" else 1002
    monkeypatch.setattr(capture, "open_clipboard", lambda _: nullcontext())
    monkeypatch.setattr(capture, "_cached_html_fmt", 1001)
    monkeypatch.setattr(capture, "_cached_rtf_fmt", 1002)
    monkeypatch.setattr(capture, "_clipboard_data_size", lambda _: None)
    monkeypatch.setattr(capture.win32clipboard, "IsClipboardFormatAvailable", {rich_format, win32con.CF_DIB}.__contains__)
    monkeypatch.setattr(capture.win32clipboard, "GetClipboardData", lambda fmt: raw if fmt == rich_format else b"dib")

    item = capture.capture_clipboard()
    assert isinstance(item, ClipboardItem)
    assert (item.item_type, item.text, item.raw_bytes) == (kind, f"({kind.upper()})", raw)


@pytest.mark.parametrize("legacy", [False, True])
def test_edit_favorite_works_after_restart_and_when_missing_from_history(legacy) -> None:
    app = ClipHistApp.__new__(ClipHistApp)
    app.history = ClipboardHistory(10)
    app.favorites = FavoritesStore()
    app._persistence_enabled = True
    db_updates: list[tuple] = []
    app._store = SimpleNamespace(submit=lambda *args: (db_updates.append(args) or Future()))
    app._save_favorites_async = lambda *_: None
    app._sync_panel_data = lambda: None

    now = datetime.now(timezone.utc)
    stored_time = datetime.fromtimestamp(int(now.timestamp() * 1000) / 1000, tz=timezone.utc)
    hist_item = ClipboardItem(stored_time, "text", text="orig", db_id=42).with_search_index()
    if not legacy:
        hist_item = hist_item.prepared()
    fav_item_reloaded = ClipboardItem(now, "text", text="orig", db_id=None).prepared()
    app.history.add(hist_item)
    app.favorites.add_or_promote(fav_item_reloaded)

    edited = ClipboardItem(now, "text", text="updated").prepared()
    ok, err = app._edit_item(fav_item_reloaded, edited, True)
    assert ok and err is None
    assert app.history.items()[0].text == "updated"
    assert app.history.items()[0].db_id == 42
    assert app.favorites.entries[0].item.text == "updated"
    assert db_updates == [("update_text", 42, "updated", edited._fingerprint)]

    # Clear history; editing the surviving favorite must still succeed.
    app.history.clear()
    edited2 = ClipboardItem(now, "text", text="fav-only-edit").prepared()
    ok2, err2 = app._edit_item(app.favorites.entries[0].item, edited2, True)
    assert ok2 and err2 is None
    assert app.favorites.entries[0].item.text == "fav-only-edit"

    # A favorite left over from a former database cannot use its row ID in the new database.
    app.favorites.add_or_promote(hist_item)
    favorite = app.favorites.entries[0].item
    other = ClipboardItem(now, "text", text="other database", db_id=42).prepared()
    app.history.reset([other])
    assert favorite.db_id is None
    assert app._edit_item(favorite, edited, True) == (True, None)
    assert app.history.items() == [other]
    assert db_updates == [("update_text", 42, "updated", edited._fingerprint)]


def test_enabling_a_new_database_keeps_only_unpersisted_session_records(tmp_path, monkeypatch) -> None:
    now = ClipboardItem.now_utc()
    old = ClipboardItem(now, "text", text="old database", db_id=1).prepared()
    session = ClipboardItem(now, "text", text="session only").prepared()
    new = ClipboardItem(now, "text", text="new database").prepared()
    path = str(tmp_path / "new.sqlite3")
    store = AsyncSQLiteHistoryStore(path)
    try:
        assert store.call("insert", new) == 1
    finally:
        store.close()

    app = ClipHistApp.__new__(ClipHistApp)
    app.history = ClipboardHistory(10)
    app.history.reset([session, old])
    app.settings = AppSettings(db_path=path)
    app._database_migration_sources = []
    app._store = None
    app._invalidate_blob_state = lambda **_: None
    app._sync_ui_state = lambda: None
    app.panel = SimpleNamespace(isVisible=lambda: False)
    monkeypatch.setattr(qt_app_module, "save_settings", lambda _: None)
    try:
        app._enable_persistence(True)
        assert app._persistence_enabled
        assert [(item.db_id, item.text) for item in app.history.items()] == [(1, "new database"), (None, "session only")]
    finally:
        if app._store is not None:
            app._store.close()


@pytest.fixture
def settings_host(tmp_path, monkeypatch):
    qt = QApplication.instance() or QApplication([])
    host = ClipHistApp.__new__(ClipHistApp)
    host.qt_app = qt
    host.settings = AppSettings()
    host._database_migration_sources = []
    host._settings_dialog = None
    host._closing = False
    host.paused = False
    host.history = ClipboardHistory(host.settings.max_items)
    host.favorites = FavoritesStore()
    host._persistence_enabled = False
    host._store = None
    host._pending_inserts = {}
    host._pending_blob_requests = {}
    host._blob_cache = OrderedDict()
    host._blob_cache_bytes = 0
    host._blob_cache_limit = 1024
    host._clipboard_write_pending = False
    host._activation = None
    host._previous_foreground_hwnd = None
    host._hotkey_specs = {}
    host._hotkeys_suspended = False
    host.listener = SimpleNamespace(
        hwnd=123,
        unregister_hotkey=lambda *_: None,
        register_hotkey_with_error=lambda *_: (True, 0),
    )
    host.panel = ClipPanel(on_activate=host._activate_item)
    monkeypatch.setattr(settings_module, "default_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(settings_module, "is_autostart_enabled", lambda: False)
    try:
        yield host
    finally:
        if host._store is not None:
            host._store.close()
        host.panel.close()
        host.panel.deleteLater()
        qt.processEvents()


def test_failed_settings_save_after_database_switch_cannot_copy_a_stale_images_reused_row(
    settings_host, tmp_path, monkeypatch
):
    host = settings_host
    old_path, new_path = (str(tmp_path / f"{name}.sqlite3") for name in ("old", "new"))
    old_image, new_image = b"old database image", b"new database image"
    now = ClipboardItem.now_utc()
    for path, image in ((old_path, old_image), (new_path, new_image)):
        store = AsyncSQLiteHistoryStore(path)
        try:
            assert store.call("insert", ClipboardItem(now, "image", raw_bytes=image).prepared()) == 1
        finally:
            store.close()

    def copy(_write, item, **_kwargs):
        written.append(item.raw_bytes)
        return Future()

    host.settings = AppSettings(db_path=old_path, persist_enabled=True)
    host._store = AsyncSQLiteHistoryStore(old_path)
    host._persistence_enabled = True
    host.history.reset(host._store.call("load_recent_slim", 10))
    host._sync_panel_data()
    host.panel._show_near_cursor()
    stale = host.panel._item_at_current_row()
    events, written, errors = [], [], []
    host._clipboard_executor = SimpleNamespace(submit=copy)
    host._bridge = SimpleNamespace(event=SimpleNamespace(emit=events.append))
    host._notify_error = lambda *args: errors.append(args)
    blocked_config = tmp_path / "blocked-config"
    blocked_config.mkdir()
    monkeypatch.setattr(settings_module, "default_config_path", lambda: str(blocked_config))

    ok, message = host._apply_settings("", "", host.history.max_items, True, False, False, db_path=new_path)
    assert not ok and "保存到配置文件失败" in message

    host._activate_item(stale, False)
    host._store.call("count")  # Wait for any queued read from the new database.
    while events:
        host._handle_event(events.pop(0))
    assert not written and errors
    assert host.panel._item_at_current_row().record_key is host.history.items()[0].record_key

    host.panel._activate_current(paste_override=False)
    host._store.call("count")
    while events:
        host._handle_event(events.pop(0))
    assert written == [new_image]


@pytest.mark.parametrize("failure", ["saving the new path", "trimming before save"])
def test_failed_settings_change_after_database_migration_keeps_old_history(
    settings_host, tmp_path, monkeypatch, failure,
):
    host = settings_host
    original_path = str(tmp_path / "old.sqlite3")
    target_path = str(tmp_path / "new.sqlite3")
    host.settings = AppSettings(db_path=original_path, persist_enabled=True)
    settings_module.save_settings(host.settings)
    host._store = AsyncSQLiteHistoryStore(original_path)
    host._persistence_enabled = True
    item = ClipboardItem(ClipboardItem.now_utc(), "text", text="saved before migration").prepared()
    host._store.call("insert", item)
    host.history.reset(host._store.call("load_recent_slim", 10))

    def fail_operation(*_args):
        raise PermissionError("write failed")

    with monkeypatch.context() as patch:
        if failure == "saving the new path":
            patch.setattr(qt_app_module, "save_settings", fail_operation)
            limit = host.history.max_items
            expected = "保存到配置文件失败"
        else:
            patch.setattr(SQLiteHistoryStore, "trim_to_limit", fail_operation)
            limit = host.history.max_items - 1
            expected = "重新加载历史失败"
        ok, message = host._apply_settings("", "", limit, True, False, False, db_path=target_path)
    assert not ok and expected in message
    assert [row.text for row in host.history.items()] == [item.text]
    if failure == "trimming before save":
        host._on_panel_resized(800, 680)
    restarted = AsyncSQLiteHistoryStore(load_settings().db_path)
    try:
        assert [row.text for row in restarted.call("load_recent_slim", 10)] == [item.text]
    finally:
        restarted.close()
    assert host._apply_settings("", "", host.history.max_items, True, False, False, db_path=target_path) == (True, None)
    assert not (tmp_path / "old.sqlite3").exists()
    assert len(list(tmp_path.glob("old.migrated-*.sqlite3"))) == 1


def test_switching_back_to_default_after_migration_keeps_new_history(settings_host, tmp_path):
    host = settings_host
    host.settings = AppSettings(persist_enabled=True)
    host._store = AsyncSQLiteHistoryStore(settings_module.default_db_path())
    host._persistence_enabled = True
    old = ClipboardItem(ClipboardItem.now_utc(), "text", text="before moving").prepared()
    host._store.call("insert", old)
    host.history.reset(host._store.call("load_recent_slim", 10))

    custom_path = str(tmp_path / "custom.sqlite3")
    assert host._apply_settings("", "", host.history.max_items, True, False, False, db_path=custom_path) == (True, None)
    new = ClipboardItem(ClipboardItem.now_utc(), "text", text="after moving").prepared()
    host._store.call("insert", new)

    assert host._apply_settings("", "", host.history.max_items, True, False, False, db_path="") == (True, None)
    assert {row.text for row in host.history.items()} == {old.text, new.text}
    assert load_settings().db_path is None


def test_interrupted_config_write_preserves_the_last_saved_settings(settings_host, monkeypatch):
    original = AppSettings(theme="blue", hotkey_show_panel="Ctrl+PgUp")
    settings_module.save_settings(original)

    def fail_during_serialization(_data, file, **_kwargs):
        file.write('{"theme":')
        raise OSError("disk full")

    monkeypatch.setattr(settings_module.json, "dump", fail_during_serialization)
    with pytest.raises(OSError, match="disk full"):
        settings_module.save_settings(AppSettings(theme="dark"))
    assert load_settings() == original


@pytest.mark.parametrize(
    "stored_shortcuts, expected_shortcuts",
    [
        (("Ctrl+PgUp", "Meta+Alt+P"), ("Ctrl+PgUp", "Meta+Alt+P")),
        (("Ctrl+PageUp", "Alt+Win+P"), ("Ctrl+PgUp", "Meta+Alt+P")),
        (("Ctrl+PgDown", ""), ("Ctrl+PgDown", "")),
    ],
)
def test_saving_and_reopening_settings_preserves_custom_hotkeys(
    settings_host, monkeypatch, stored_shortcuts, expected_shortcuts
):
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: None)
    host = settings_host
    settings_module.save_settings(
        AppSettings(hotkey_show_panel=stored_shortcuts[0], hotkey_toggle_pause=stored_shortcuts[1])
    )
    for theme in ("blue", "dark"):
        host.settings = load_settings()
        dialog = SettingsDialog(host.settings, host._apply_settings)
        try:
            dialog._theme.setCurrentIndex(dialog._theme.findData(theme))
            dialog._on_ok()
            assert dialog.result() == SettingsDialog.Accepted
        finally:
            dialog.deleteLater()
    saved = load_settings()
    assert (saved.hotkey_show_panel, saved.hotkey_toggle_pause) == expected_shortcuts
    assert saved.theme == "dark"


@pytest.mark.parametrize(
    "hotkey, key, fallback",
    [(HOTKEY_TOGGLE_PANEL, "C", "Alt+V"), (HOTKEY_TOGGLE_PAUSE, "P", "Ctrl+Shift+P")],
)
def test_saving_settings_keeps_configured_hotkeys_during_a_temporary_conflict(settings_host, hotkey, key, fallback):
    host = settings_host
    host._hotkeys_suspended = True
    host.listener.register_hotkey_with_error = lambda hotkey_id, mods, vk: (
        (False, 1409) if (hotkey_id, mods, vk) == (hotkey, win32con.MOD_ALT, ord(key)) else (True, 0)
    )

    ok, warning = host._apply_settings("Alt+C", "Alt+P", host.history.max_items, False, False, False, theme="dark")

    assert ok and fallback in warning
    assert host._hotkey_specs[hotkey].display == fallback
    saved = load_settings()
    assert (saved.hotkey_show_panel, saved.hotkey_toggle_pause, saved.theme) == ("Alt+C", "Alt+P", "dark")


@pytest.mark.parametrize("db_ids", [(None, None, None), (1, 2, 3)])
def test_delete_single_duplicate_history_row_keeps_the_other_copy(db_ids) -> None:
    app = ClipHistApp.__new__(ClipHistApp)
    app.history = ClipboardHistory(10)
    app._persistence_enabled = True
    app._pending_blob_requests = {}
    app._failed_blob_keys = OrderedDict()
    app._blob_cache = OrderedDict()
    app._blob_cache_bytes = 0
    app._sync_panel_data = lambda: None
    deleted_db_ids: list[list[int]] = []
    app._store = SimpleNamespace(submit=lambda method, ids: (deleted_db_ids.append(ids) or Future()))

    now = datetime.now(timezone.utc)
    a1 = ClipboardItem(now, "text", text="dup", db_id=db_ids[0]).prepared()
    mid = ClipboardItem(now, "text", text="mid", db_id=db_ids[1]).prepared()
    a2 = ClipboardItem(now, "text", text="dup", db_id=db_ids[2]).prepared()
    app.history.add(a1)
    app.history.add(mid)
    app.history.add(a2)

    ok, err = app._delete_history_items([a1])
    assert ok and err is None
    assert [item.record_key for item in app.history.items()] == [a2.record_key, mid.record_key]
    assert deleted_db_ids == ([[1]] if db_ids[0] is not None else [])


@pytest.mark.parametrize("delete", [True, False])
def test_pending_insert_respects_removal_or_edit_of_the_exact_record(tmp_path, delete) -> None:
    app = ClipHistApp.__new__(ClipHistApp)
    app.history = ClipboardHistory(10)
    app.favorites = FavoritesStore()
    app.paused = False
    app._persistence_enabled = True
    app._pending_inserts = {}
    app._pending_blob_requests = {}
    app._blob_cache = OrderedDict()
    app._bridge = SimpleNamespace(event=SimpleNamespace(emit=lambda _: None))
    app._sync_panel_data = lambda: None
    app._schedule_panel_refresh = lambda: None
    app._store = AsyncSQLiteHistoryStore(str(tmp_path / "history.sqlite3"))
    now = ClipboardItem.now_utc()
    try:
        for content in ("duplicate", "separator"):
            app._store.call("insert", ClipboardItem(now, "text", text=content).prepared())
        app.history.reset(app._store.call("load_recent_slim", 10))
        pending = ClipboardItem(now, "text", text="duplicate").prepared()
        app._handle_event(pending)
        if delete:
            assert app._delete_history_items([pending]) == (True, None)
            expected = [(2, "separator"), (1, "duplicate")]
        else:
            edit = ClipboardItem(now, "text", text="updated").prepared()
            assert app._edit_item(pending, edit, False) == (True, None)
            expected = [(3, "updated"), (2, "separator"), (1, "duplicate")]
        app._finish_pending_inserts()
        assert [(item.db_id, item.text) for item in app.history.items()] == expected
        assert [(item.db_id, item.text) for item in app._store.call("load_recent_slim", 10)] == expected
    finally:
        app._store.close()


def test_panel_refreshes_title_for_a_reused_row_id_and_searches_the_entire_text() -> None:
    app = QApplication.instance() or QApplication([])
    panel = ClipPanel(on_activate=lambda *_: None)
    now = ClipboardItem.now_utc()
    old = ClipboardItem(now, "text", text="old database", db_id=1).prepared()
    new = ClipboardItem(now, "text", text="new database " + "x" * 66000 + " Tail\r\nKeyword", db_id=1).prepared()
    try:
        assert new._search_index.endswith("tail keyword")
        panel.set_data([old], [])
        panel._apply_filter()
        assert panel._list_all.item(0).data(ROLE_TITLE) == "old database"
        panel.set_data([new], [])
        panel._search.setText("tail keyword")
        panel._apply_filter()
        assert panel._filtered_items == [new]
        assert panel._list_all.item(0).data(ROLE_TITLE).startswith("new database")
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_filtered_favorite_move_does_not_scramble_order() -> None:
    app = QApplication.instance() or QApplication([])
    now = datetime.now(timezone.utc)
    items = [ClipboardItem(now, "text", text=t).prepared() for t in ["x1", "alpha1", "x2", "alpha2", "x3"]]
    state = [(f"fav-{i}", it) for i, it in enumerate(items)]
    reordered: list[list[str]] = []

    panel = ClipPanel(
        on_activate=lambda *_: None,
        get_favorites=lambda: state[:],
        reorder_favorites=lambda ids: (reordered.append(ids) or (True, None)),
    )
    try:
        panel.set_data([], state)
        panel._tabs.setCurrentIndex(1)
        panel._search.setText("alpha")
        panel._apply_filter()
        panel._list_fav.setCurrentRow(0)
        panel._move_favorite(1)
        assert reordered == []
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_activate_defers_panel_hide_until_clipboard_write_completes() -> None:
    app = QApplication.instance() or QApplication([])
    item = ClipboardItem(datetime.now(timezone.utc), "text", text="ready").prepared()
    panel = ClipPanel(on_activate=lambda *_: False)
    try:
        panel.set_data([item], [])
        panel._show_near_cursor()
        app.processEvents()
        panel._activate_current()
        assert panel.isVisible()

        host = ClipHistApp.__new__(ClipHistApp)
        host._closing = False
        host._clipboard_write_pending = True
        host._queued_activation = None
        host._activation = _Activation(True, None)
        host.panel = panel
        tray_msgs: list[tuple] = []
        host.tray = SimpleNamespace(showMessage=lambda *args: tray_msgs.append(args))
        host._handle_event(_ClipboardWriteResult(host._activation))
        assert not panel.isVisible()
        assert tray_msgs
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_escape_closes_preview_first_typing_focuses_search_and_settings_preserves_empty_hotkey(
    tmp_path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    item = ClipboardItem(datetime.now(timezone.utc), "text", text="Check https://example.com in prose").prepared()
    pure_link = ClipboardItem(datetime.now(timezone.utc), "text", text="https://example.com/path").prepared()
    assert item.display_type == "text"
    assert pure_link.display_type == "link"

    panel = ClipPanel(on_activate=lambda *_: None)
    try:
        panel.set_data([item, pure_link], [])
        panel._show_near_cursor()
        app.processEvents()
        panel._list_all.setFocus()
        panel._toggle_preview()
        assert panel._preview_open and panel._preview_popup.isVisible()

        QTest.keyClick(panel._list_all, Qt.Key_Escape)
        assert not panel._preview_open and panel.isVisible()

        QTest.keyClick(panel._list_all, Qt.Key_A)
        assert panel._search.hasFocus()
        assert panel._search.text() == "a"

        QTest.keyClick(panel._search, Qt.Key_Escape)
        assert not panel.isVisible()
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()

    # Empty pause hotkey saved in config.json must stay empty on reload.
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"hotkey_show_panel": "Alt+C", "hotkey_toggle_pause": ""}), encoding="utf-8")
    monkeypatch.setattr(settings_module, "default_config_path", lambda: str(cfg))
    monkeypatch.setattr(settings_module, "is_autostart_enabled", lambda: False)
    loaded = load_settings()
    assert loaded.hotkey_toggle_pause == ""

    # SettingsDialog surfaces non-fatal warnings returned by apply_settings.
    warnings: list[tuple] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    dlg = SettingsDialog(AppSettings(), lambda *args: (True, "暂停热键冲突警告"))
    try:
        dlg._on_ok()
        assert warnings and "暂停热键冲突警告" in str(warnings[0])
    finally:
        dlg.close()
        dlg.deleteLater()
        app.processEvents()
