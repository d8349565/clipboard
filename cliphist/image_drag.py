"""Prepare temporary PNG files off the GUI thread for native file drops."""
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import Callable
from uuid import uuid4

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtGui import QImage

from .models import ClipboardItem, content_fingerprint


@dataclass
class DragImage:
    key: str
    path: Path
    image: QImage | None = None
    thumbnail: QImage | None = None
    error: str | None = None


class _ExportSignals(QObject):
    finished = Signal(object)


class _ExportTask(QRunnable):
    def __init__(self, key: str, raw: bytes, path: Path, decode: Callable[[bytes], QImage | None]):
        super().__init__()
        self.key, self.raw, self.path, self.decode = key, raw, path, decode
        self.signals = _ExportSignals()

    def run(self) -> None:
        result = DragImage(self.key, self.path)
        try:
            image = QImage(str(self.path)) if self.path.exists() else self.decode(self.raw)
            if image is None or image.isNull():
                raise ValueError("图片内容不可用")
            if not self.path.exists() and not image.save(str(self.path), "PNG"):
                raise OSError("无法创建拖拽临时图片")
            result.image = image
            result.thumbnail = image.scaled(128, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        except Exception:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            result.error = "图片准备失败，请重新拖拽"
        self.signals.finished.emit(result)


class ImageDragStore(QObject):
    """Keep source files until process exit, so delayed browser reads still work."""
    ready = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._directory: tempfile.TemporaryDirectory | None = None
        self._files: dict[str, Path] = {}
        self._images: OrderedDict[str, DragImage] = OrderedDict()
        self._active: dict[str, _ExportTask] = {}
        self._errors: dict[str, str] = {}

    def request(self, item: ClipboardItem, decode: Callable[[bytes], QImage | None]) -> DragImage | None:
        key = content_fingerprint(item)
        error = self._errors.pop(key, None)
        if error:
            raise ValueError(error)
        if key in self._images:
            result = self._images.pop(key)
            if result.path.exists():
                self._images[key] = result
                return result
        if key not in self._active:
            if self._directory is None:
                self._directory = tempfile.TemporaryDirectory(prefix="ClipHist-drag-", ignore_cleanup_errors=True)
            path = self._files.setdefault(key, Path(self._directory.name) / ("ClipHist-" + uuid4().hex + ".png"))
            task = _ExportTask(key, item.raw_bytes or b"", path, decode)
            task.signals.finished.connect(self._finished)
            self._active[key] = task
            self._pool.start(task)
        return None

    def _finished(self, result: DragImage) -> None:
        self._active.pop(result.key, None)
        if result.error:
            self._errors[result.key] = result.error
        else:
            self._images[result.key] = result
            # Keep at most two full-resolution images; older PNG files remain usable.
            while len(self._images) > 2:
                self._images.popitem(last=False)
        self.ready.emit(result.key)
