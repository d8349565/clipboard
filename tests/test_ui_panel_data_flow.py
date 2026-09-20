from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QListWidgetItem, QMessageBox

from cliphist.models import ClipboardItem
from cliphist.ui_panel import (
    ROLE_ITEM,
    ClipPanel,
    _ClipListWidget,
    _ImageDecodeQueue,
)


def _process_until(app: QApplication, predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()


def test_panel_filters_preindexed_items_and_forwards_copy_only_override() -> None:
    app = QApplication.instance() or QApplication([])
    activated: list[tuple[ClipboardItem, bool | None]] = []
    panel = ClipPanel(on_activate=lambda item, paste: activated.append((item, paste)))
    items = [
        ClipboardItem(datetime.now(timezone.utc), "text", text="Alpha value").prepared(),
        ClipboardItem(datetime.now(timezone.utc), "text", text="Beta value").prepared(),
    ]
    try:
        panel.set_data(items, [])
        panel._search.setText("beta")
        panel._apply_filter()

        assert panel._list_all.count() == 1
        assert panel._filtered_items == [items[1]]
        panel._list_all.setCurrentRow(0)
        panel._activate_current(paste_override=False)
        assert activated == [(items[1], False)]
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_panel_category_filter_uses_cached_display_type(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    import cliphist.models as models_module

    items = [
        ClipboardItem(datetime.now(timezone.utc), "text", text="plain text").prepared(),
        ClipboardItem(datetime.now(timezone.utc), "text", text="https://example.com").prepared(),
    ]

    class _UnexpectedUrlScan:
        def search(self, _value):
            raise AssertionError("category filtering rescanned text")

    panel = ClipPanel(on_activate=lambda *_: None)
    try:
        panel.set_data(items, [])
        monkeypatch.setattr(models_module, "_RE_URL", _UnexpectedUrlScan())

        panel._active_category = "link"
        panel._apply_filter()
        assert panel._filtered_items == [items[1]]

        panel._active_category = "text"
        panel._apply_filter()
        assert panel._filtered_items == [items[0]]
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_favorite_management_supports_batch_remove_and_move_to_top(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    items = [
        ClipboardItem(datetime.now(timezone.utc), "text", text=f"favorite-{index}").prepared()
        for index in range(4)
    ]
    state = [(f"fav-{index}", item) for index, item in enumerate(items)]
    removed_calls: list[list[str]] = []
    reordered_calls: list[list[str]] = []

    def get_favorites():
        return state[:]

    def remove_favorites(fav_ids: list[str]):
        nonlocal state
        removed_calls.append(fav_ids[:])
        targets = set(fav_ids)
        state = [entry for entry in state if entry[0] not in targets]
        return True, None

    def reorder_favorites(fav_ids: list[str]):
        nonlocal state
        reordered_calls.append(fav_ids[:])
        lookup = dict(state)
        state = [(fav_id, lookup[fav_id]) for fav_id in fav_ids]
        return True, None

    panel = ClipPanel(
        on_activate=lambda *_: None,
        get_favorites=get_favorites,
        remove_favorites=remove_favorites,
        reorder_favorites=reorder_favorites,
    )
    try:
        panel.set_data([], state)
        panel._tabs.setCurrentIndex(1)
        panel._apply_filter()
        panel._list_fav.setCurrentRow(1, QItemSelectionModel.ClearAndSelect)
        panel._list_fav.item(2).setSelected(True)

        panel._move_selected_favorites_to_top()
        assert reordered_calls == [["fav-1", "fav-2", "fav-0", "fav-3"]]
        assert panel._list_fav.selectedItems()

        monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
        panel._remove_selected_favorites()
        assert removed_calls == [["fav-1", "fav-2"]]
        assert [fav_id for fav_id, _ in panel._favorites] == ["fav-0", "fav-3"]
        assert panel._list_fav.count() == 2
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_image_decode_queue_deduplicates_and_runs_decode_off_gui_thread(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    main_thread = threading.get_ident()
    decode_threads: list[int] = []
    ready: list[tuple[tuple, object]] = []

    def fake_decode(_raw: bytes) -> QImage:
        decode_threads.append(threading.get_ident())
        image = QImage(80, 60, QImage.Format_ARGB32)
        image.fill(0xFF336699)
        return image

    monkeypatch.setattr("cliphist.ui_panel._qimage_from_dib", fake_decode)
    queue = _ImageDecodeQueue(lambda key, pixmap: ready.append((key, pixmap)))
    key = _ImageDecodeQueue.make_key(("obj", 1), "thumb", 24, 24, 1.0)
    try:
        assert queue.request(key, b"dib", 24, 24, 1.0) is None
        assert queue.request(key, b"dib", 24, 24, 1.0) is None
        assert len(queue._pending) + len(queue._active) == 1

        _process_until(app, lambda: bool(ready))
        assert len(ready) == 1
        assert decode_threads and decode_threads[0] != main_thread
        assert ready[0][1] is not None
    finally:
        queue.close()


def test_panel_defers_lazy_blob_loader_until_after_image_request() -> None:
    app = QApplication.instance() or QApplication([])
    calls: list[ClipboardItem] = []
    item = ClipboardItem(
        datetime.now(timezone.utc),
        "image",
        db_id=7,
        raw_size=4,
    )
    panel = ClipPanel(
        on_activate=lambda *_: None,
        load_blobs=lambda current: (calls.append(current) or current),
    )
    try:
        panel.set_data([item], [])
        assert panel._request_thumbnail(item, 32, 1.0) is None
        assert calls == []
        app.processEvents()
        assert calls == [item]
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_drag_waits_for_pending_image_blob_instead_of_starting_empty_drag(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    calls: list[ClipboardItem] = []
    item = ClipboardItem(
        datetime.now(timezone.utc),
        "image",
        db_id=8,
        raw_size=5,
    )
    widget = _ClipListWidget(
        lambda row: item if row == 0 else None,
        load_blobs=lambda current: (calls.append(current) or current),
    )
    row = QListWidgetItem()
    row.setData(ROLE_ITEM, item)
    widget.addItem(row)
    widget.setCurrentRow(0)

    class UnexpectedDrag:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("empty image drag must not be started")

    monkeypatch.setattr("cliphist.ui_panel.QDrag", UnexpectedDrag)
    try:
        widget.startDrag(Qt.CopyAction)
        assert calls == [item]
    finally:
        widget.deleteLater()
        app.processEvents()


def test_repeated_preview_requests_share_one_decode(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    decode_calls = 0

    def fake_decode(_raw: bytes) -> QImage:
        nonlocal decode_calls
        decode_calls += 1
        image = QImage(160, 100, QImage.Format_ARGB32)
        image.fill(0xFF336699)
        return image

    monkeypatch.setattr("cliphist.ui_panel._qimage_from_dib", fake_decode)
    panel = ClipPanel(on_activate=lambda *_: None)
    item = ClipboardItem(datetime.now(timezone.utc), "image", raw_bytes=b"dib")
    try:
        panel.resize(640, 620)
        panel._render_preview_content(
            item,
            panel._preview_meta,
            panel._preview_text,
            panel._preview_image_label,
            panel._preview_stack,
            docked=True,
        )
        panel._render_preview_content(
            item,
            panel._preview_meta,
            panel._preview_text,
            panel._preview_image_label,
            panel._preview_stack,
            docked=True,
        )
        _process_until(app, lambda: panel._preview_image is not None)
        assert decode_calls == 1
        assert panel._preview_image is not None
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_thumbnail_survives_source_blob_cache_eviction(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])

    def fake_decode(raw: bytes) -> QImage:
        image = QImage(80, 60, QImage.Format_ARGB32)
        image.fill(0xFF000000 | raw[0])
        return image

    monkeypatch.setattr("cliphist.ui_panel._qimage_from_dib", fake_decode)
    panel = ClipPanel(on_activate=lambda *_: None)
    panel._visual_blob_cache_limit = 150
    first = ClipboardItem(ClipboardItem.now_utc(), "image", db_id=1, raw_size=100)
    second = ClipboardItem(ClipboardItem.now_utc(), "image", db_id=2, raw_size=100)
    try:
        first_key = panel._item_cache_key(first)
        panel._cache_visual_blob(first_key, first.with_blobs(b"\x11" * 100, None))
        assert panel._request_thumbnail(first, 32, 1.0) is None
        thumb_key = _ImageDecodeQueue.make_key(first_key, "thumb", 32, 32, 1.0)
        _process_until(app, lambda: thumb_key in panel._image_decode._cache)

        panel._cache_visual_blob(
            panel._item_cache_key(second),
            second.with_blobs(b"\x22" * 100, None),
        )

        assert first_key not in panel._visual_blob_cache
        assert panel._request_thumbnail(first, 32, 1.0) is not None
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()
