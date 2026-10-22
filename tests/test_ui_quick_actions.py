from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from cliphist.models import ClipboardItem
from cliphist.ui_panel import ClipPanel


def test_tab_switches_empty_favorites_and_arrows_move_before_activation():
    app = QApplication.instance() or QApplication([])
    activated = []
    items = [
        ClipboardItem(ClipboardItem.now_utc(), "text", text="first").prepared(),
        ClipboardItem(ClipboardItem.now_utc(), "text", text="second").prepared(),
    ]
    panel = ClipPanel(on_activate=lambda item, paste: activated.append((item, paste)))
    try:
        panel.set_data(items, [])
        panel._show_near_cursor()
        app.processEvents()
        panel._search.setFocus()
        app.processEvents()
        assert panel._hover_preview
        assert not panel._preview.isVisible()

        tab = QKeyEvent(QEvent.KeyPress, Qt.Key_Tab, Qt.NoModifier)
        assert panel.eventFilter(panel._search, tab)
        assert panel._tabs.currentIndex() == 1
        assert panel._search.hasFocus()  # An empty tab must remain keyboard-operable.
        backtab = QKeyEvent(QEvent.KeyPress, Qt.Key_Backtab, Qt.ShiftModifier)
        assert panel.eventFilter(panel._search, backtab)
        assert panel._tabs.currentIndex() == 0

        down = QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier)
        assert panel.eventFilter(panel._search, down)
        assert panel._list_all.currentRow() == 1
        enter = QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.ControlModifier)
        assert panel.eventFilter(panel._list_all, enter)
        assert activated == [(items[1], False)]
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()
