from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtWidgets import QApplication

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
