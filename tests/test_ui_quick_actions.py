from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtTest import QTest
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
        assert not panel._preview_open
        assert not panel._preview_popup.isVisible()

        QTest.keyClick(panel._search, Qt.Key_Tab)
        assert panel._tabs.currentIndex() == 1
        assert panel._search.hasFocus()  # An empty tab must remain keyboard-operable.
        QTest.keyClick(panel._search, Qt.Key_Tab, Qt.ShiftModifier)
        assert panel._tabs.currentIndex() == 0
        QTest.keyClick(panel._search, Qt.Key_Tab, Qt.ControlModifier)
        assert panel._tabs.currentIndex() == 1
        QTest.keyClick(panel._search, Qt.Key_Tab, Qt.ControlModifier | Qt.ShiftModifier)
        assert panel._tabs.currentIndex() == 0

        QTest.keyClick(panel._search, Qt.Key_Down)
        assert panel._list_all.currentRow() == 1
        QTest.keyClick(panel._list_all, Qt.Key_Return, Qt.ControlModifier)
        assert activated == [(items[1], False)]
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_old_shortcuts_work_from_search_list_and_buttons(monkeypatch):
    app = QApplication.instance() or QApplication([])
    favorite_calls, deleted, activated = [], [], []
    item = ClipboardItem(ClipboardItem.now_utc(), "text", text="sample").prepared()
    panel = ClipPanel(on_activate=lambda value, paste: activated.append((value, paste)))
    monkeypatch.setattr(panel, "_toggle_current_favorite", lambda: favorite_calls.append(True))
    monkeypatch.setattr(panel, "_delete_selected_history", lambda: deleted.append(True))
    try:
        panel.set_data([item], [])
        panel._show_near_cursor()
        app.processEvents()
        for widget in (panel._search, panel._list_all, panel._btn_preview_mode):
            widget.setFocus()
            QTest.keyClick(widget, Qt.Key_F, Qt.AltModifier)
            QTest.keyClick(widget, Qt.Key_F, Qt.ControlModifier)
            assert panel._search.hasFocus()
        assert len(favorite_calls) == 3
        panel._search.setText("sample")
        panel._search.selectAll()
        QTest.keyClick(panel._search, Qt.Key_Delete)
        assert panel._search.text() == "" and not deleted
        panel._list_all.setFocus()
        QTest.keyClick(panel._list_all, Qt.Key_Delete)
        assert deleted == [True]
        QTest.keyClick(panel._list_all, Qt.Key_Space)
        assert panel._preview_open and panel._preview_popup.isVisible()
        QTest.keyClick(panel._list_all, Qt.Key_Space)
        assert not panel._preview_popup.isVisible()
        panel.set_auto_paste(True)
        QTest.keyClick(panel._list_all, Qt.Key_Return, Qt.ControlModifier)
        assert activated == [(item, False)]
        panel._show_near_cursor()
        app.processEvents()
        QTest.keyClick(panel._search, Qt.Key_Return)
        assert activated[-1] == (item, None)
        panel._show_near_cursor()
        app.processEvents()
        position = panel._list_all.visualItemRect(panel._list_all.item(0)).center()
        QTest.mouseClick(panel._list_all.viewport(), Qt.LeftButton, pos=position)
        QTest.mouseDClick(panel._list_all.viewport(), Qt.LeftButton, pos=position)
        assert activated[-1] == (item, None) and not panel.isVisible()
        panel._show_near_cursor()
        app.processEvents()
        panel._list_all._pending_drag = (item, Qt.CopyAction)
        panel._list_all._drag_retry.start()
        QTest.keyClick(panel._search, Qt.Key_Escape)
        assert not panel.isVisible()
        assert panel._list_all._pending_drag is None
        assert not panel._list_all._drag_retry.isActive()
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_ctrl_hover_is_temporary_and_explicit_preview_survives_ctrl_release():
    app = QApplication.instance() or QApplication([])
    panel = ClipPanel(on_activate=lambda *_: None)
    item = ClipboardItem(ClipboardItem.now_utc(), "text", text="sample").prepared()
    try:
        panel.set_data([item], [])
        panel._show_near_cursor()
        app.processEvents()
        widget = panel._list_all
        position = QPointF(widget.visualItemRect(widget.item(0)).center())
        move = QMouseEvent(QEvent.MouseMove, position, position, Qt.NoButton, Qt.NoButton, Qt.ControlModifier)
        panel.eventFilter(widget.viewport(), move)
        assert panel._preview_popup.isVisible() and not panel._preview_open
        release = QKeyEvent(QEvent.KeyRelease, Qt.Key_Control, Qt.NoModifier)
        panel.eventFilter(widget, release)
        assert not panel._preview_popup.isVisible()
        panel._toggle_preview()
        assert panel._preview_open and panel._preview_popup.isVisible()
        panel.eventFilter(widget, release)
        assert panel._preview_popup.isVisible()
        panel.hide()
        assert not panel._preview_popup.isVisible()
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_global_shortcut_dispatch_does_not_depend_on_panel_focus():
    from cliphist.qt_app import ClipHistApp, HOTKEY_TOGGLE_PANEL, HOTKEY_TOGGLE_PAUSE
    from cliphist.win_listener import HotkeyEvent
    from cliphist.hotkeys import parse_hotkey_sequence
    calls = []
    instance = ClipHistApp.__new__(ClipHistApp)
    instance.paused = False
    instance._open_panel = lambda: calls.append("panel")
    instance._set_paused = lambda value: calls.append(value)
    assert parse_hotkey_sequence("Alt+C").display == "Alt+C"
    assert parse_hotkey_sequence("Alt+P").display == "Alt+P"
    instance._handle_event(HotkeyEvent(HOTKEY_TOGGLE_PANEL))
    instance._handle_event(HotkeyEvent(HOTKEY_TOGGLE_PAUSE))
    assert calls == ["panel", True]
