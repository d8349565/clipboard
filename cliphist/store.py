from __future__ import annotations

from collections import deque
from threading import RLock
from typing import Deque, Iterable

from .models import ClipboardItem


class ClipboardHistory:
    def __init__(self, max_items: int = 200) -> None:
        if max_items <= 0:
            raise ValueError("max_items must be > 0")
        self._max_items = max_items
        self._items: Deque[ClipboardItem] = deque(maxlen=max_items)
        self._lock = RLock()

    @property
    def max_items(self) -> int:
        return self._max_items

    def add(self, item: ClipboardItem) -> bool:
        with self._lock:
            if self._items and self._items[0].dedupe_key() == item.dedupe_key():
                return False
            self._items.appendleft(item)
            return True

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def items(self) -> list[ClipboardItem]:
        with self._lock:
            return list(self._items)

    def __iter__(self) -> Iterable[ClipboardItem]:
        return iter(self.items())
