from __future__ import annotations

from collections import deque
from threading import RLock
from typing import Deque, Iterator

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

    def set_max_items(self, max_items: int) -> None:
        if max_items <= 0:
            raise ValueError("max_items must be > 0")
        with self._lock:
            if max_items == self._max_items:
                return
            items = list(self._items)[:max_items]
            self._max_items = max_items
            self._items = deque(items, maxlen=max_items)

    def reset(self, items: list[ClipboardItem]) -> None:
        with self._lock:
            self._items = deque(items[: self._max_items], maxlen=self._max_items)

    def add(self, item: ClipboardItem) -> bool:
        with self._lock:
            if self._items and self._items[0].dedupe_key() == item.dedupe_key():
                return False
            self._items.appendleft(item)
            return True

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def replace_first(self, old_item: ClipboardItem, new_item: ClipboardItem) -> bool:
        with self._lock:
            for idx, current in enumerate(self._items):
                if current is old_item or current == old_item:
                    self._items[idx] = new_item
                    return True
        return False

    def remove_many(self, items: list[ClipboardItem]) -> int:
        if not items:
            return 0
        identities = {id(item) for item in items}
        fingerprints = {item._fingerprint for item in items if item._fingerprint}
        with self._lock:
            before = len(self._items)
            kept = [
                current
                for current in self._items
                if id(current) not in identities
                and not (current._fingerprint and current._fingerprint in fingerprints)
            ]
            self._items = deque(kept, maxlen=self._max_items)
            return before - len(self._items)

    def items(self) -> list[ClipboardItem]:
        with self._lock:
            return list(self._items)

    def __iter__(self) -> Iterator[ClipboardItem]:
        return iter(self.items())
