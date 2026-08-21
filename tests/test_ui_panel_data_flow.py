from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QApplication, QMessageBox

from cliphist.models import ClipboardItem
from cliphist.ui_panel import ClipPanel


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
