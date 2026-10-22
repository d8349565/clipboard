import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QBuffer, QIODevice, QTimer, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractItemView, QListWidgetItem

from cliphist.models import ClipboardItem
from cliphist.ui_panel import MIME_FAV_REORDER, ROLE_FAV_ID, ROLE_ITEM, _ClipListWidget


def _image_item():
    image = QImage(48, 32, QImage.Format_RGB32)
    image.fill(0xFF336699)
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    assert image.save(buffer, "BMP")
    return ClipboardItem(ClipboardItem.now_utc(), "image", raw_bytes=bytes(buffer.data())[14:]).prepared()


def _wait(app, predicate):
    deadline = time.monotonic() + 4
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert predicate()


@pytest.mark.parametrize("favorite,lazy", [(False, False), (True, False), (False, True)])
def test_image_drag_exports_full_size_png_and_keeps_file_after_drop(monkeypatch, favorite, lazy):
    app = QApplication.instance() or QApplication([])
    item = _image_item()
    source = item.with_db_id(7).slim() if lazy else item
    calls, threads = [], []
    from cliphist import ui_panel
    decode = ui_panel._qimage_from_dib

    def decoding(raw):
        threads.append(threading.get_ident())
        return decode(raw)

    class Drag:
        def __init__(self, *_):
            pass

        def setMimeData(self, mime):
            self.mime = mime

        def setPixmap(self, _pixmap):
            pass

        def exec(self, actions):
            calls.append((self.mime, actions))
            return Qt.CopyAction

    monkeypatch.setattr(ui_panel, "QDrag", Drag)
    monkeypatch.setattr(ui_panel, "_qimage_from_dib", decoding)
    monkeypatch.setattr(QApplication, "mouseButtons", lambda: Qt.LeftButton)
    widget = _ClipListWidget(lambda row: source if row == 0 else None, load_blobs=lambda _: item)
    row = QListWidgetItem()
    row.setData(ROLE_ITEM, source)
    if favorite:
        row.setData(ROLE_FAV_ID, "favorite-image")
    widget.addItem(row)
    widget.setCurrentRow(0)
    try:
        widget.startDrag(Qt.CopyAction | Qt.MoveAction)
        _wait(app, lambda: bool(calls))
        mime, actions = calls[0]
        assert actions == Qt.CopyAction
        assert mime.hasUrls() and mime.hasImage()
        path = Path(mime.urls()[0].toLocalFile())
        assert path.suffix == ".png" and path.is_file()
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert QImage(str(path)).size().width() == 48
        assert QImage(str(path)).size().height() == 32
        assert QImage(str(path)).pixelColor(0, 0).name() == "#336699"
        assert threads and all(thread != threading.get_ident() for thread in threads)
        if favorite:
            assert bytes(mime.data(MIME_FAV_REORDER)) == b"favorite-image"
        # Native targets can read the source after exec() has returned.
        assert path.is_file()
    finally:
        widget.deleteLater()
        app.processEvents()


def test_releasing_mouse_during_preparation_does_not_start_a_late_drag(monkeypatch):
    app = QApplication.instance() or QApplication([])
    item = _image_item()
    widget = _ClipListWidget(lambda _: item)
    row = QListWidgetItem()
    row.setData(ROLE_ITEM, item)
    widget.addItem(row)
    widget.setCurrentRow(0)
    held = [True]
    monkeypatch.setattr(QApplication, "mouseButtons", lambda: Qt.LeftButton if held[0] else Qt.NoButton)
    QTimer.singleShot(0, lambda: held.__setitem__(0, False))
    try:
        widget.startDrag(Qt.CopyAction)
        _wait(app, lambda: widget._pending_drag is None and not widget._image_drag_store._active)
        assert not widget._drag_retry.isActive()
        assert widget._image_drag_store._images
    finally:
        widget.deleteLater()
        app.processEvents()


def test_first_drag_keeps_original_mouse_gesture_until_file_is_ready(monkeypatch):
    app = QApplication.instance() or QApplication([])
    item = _image_item()
    calls = []
    returned = False

    class Drag:
        def __init__(self, *_):
            pass

        def setMimeData(self, mime):
            self.mime = mime

        def setPixmap(self, _pixmap):
            pass

        def exec(self, _actions):
            calls.append((returned, widget.state(), self.mime.hasUrls()))
            return Qt.CopyAction

    monkeypatch.setattr("cliphist.ui_panel.QDrag", Drag)
    monkeypatch.setattr(QApplication, "mouseButtons", lambda: Qt.LeftButton)
    widget = _ClipListWidget(lambda _: item)
    widget.resize(320, 180)
    row = QListWidgetItem("sample")
    row.setData(ROLE_ITEM, item)
    widget.addItem(row)
    widget.show()
    app.processEvents()
    position = widget.visualItemRect(row).center()
    try:
        QTest.mousePress(widget.viewport(), Qt.LeftButton, pos=position)
        QTest.mouseMove(widget.viewport(), position + type(position)(40, 0))
        returned = True
        _wait(app, lambda: bool(calls))
        assert calls == [(False, QAbstractItemView.DraggingState, True)]
    finally:
        QTest.mouseRelease(widget.viewport(), Qt.LeftButton, pos=position)
        widget.deleteLater()
        app.processEvents()


def test_invalid_image_never_offers_a_file_and_keeps_failure_feedback(monkeypatch):
    app = QApplication.instance() or QApplication([])
    item = ClipboardItem(ClipboardItem.now_utc(), "image", raw_bytes=b"invalid-image").prepared()
    widget = _ClipListWidget(lambda _: item)
    row = QListWidgetItem("sample")
    row.setData(ROLE_ITEM, item)
    widget.addItem(row)
    widget.setCurrentRow(0)
    messages = []
    widget.drag_status.connect(messages.append)
    monkeypatch.setattr(QApplication, "mouseButtons", lambda: Qt.LeftButton)
    try:
        widget.startDrag(Qt.CopyAction)
        assert messages[-1] == "图片准备失败，请重新拖拽"
        assert widget._pending_drag is None
        assert not widget._drag_retry.isActive()
        assert not widget._image_drag_store._images
    finally:
        widget.deleteLater()
        app.processEvents()
