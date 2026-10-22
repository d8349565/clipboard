"""Lucide outline icons, rendered once per theme and display scale.

Upstream SVGs and their license are bundled in assets/ui.
"""
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


_ICONS = {
    "text": "file-text", "files": "files", "link": "link", "image": "image",
    "html": "file-code", "rtf": "file-type", "star": "star",
}
_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "ui"


@lru_cache(maxsize=96)
def type_icon(kind: str, color: str, size: int, dpr: float, filled: bool = False) -> QPixmap:
    source = (_ASSETS / (_ICONS.get(kind, "file-text") + ".svg")).read_text(encoding="utf-8")
    source = source.replace("currentColor", color)
    if filled:
        source = source.replace('fill="none"', f'fill="{color}"')
    pixmap = QPixmap(round(size * dpr), round(size * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.transparent)
    renderer = QSvgRenderer(source.encode("utf-8"))
    painter = QPainter(pixmap)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pixmap
