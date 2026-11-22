from __future__ import annotations

from collections import Counter, deque
from dataclasses import replace
from datetime import datetime, timezone
from threading import RLock
from typing import Deque, Iterator

from .models import ClipboardItem, content_fingerprint


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

    def contains_record(self, item: ClipboardItem) -> bool:
        with self._lock:
            return any(
                current.record_key is item.record_key
                and current.db_id == item.db_id
                and current._fingerprint == item._fingerprint
                for current in self._items
            )

    def edit_item(
        self, old_item: ClipboardItem, new_item: ClipboardItem, *, from_favorites: bool
    ) -> ClipboardItem | None:
        with self._lock:
            index = next(
                (i for i, current in enumerate(self._items) if current.record_key is old_item.record_key),
                None,
            )
            if index is None and from_favorites and old_item._fingerprint:
                # Favorites retain the capture time; SQLite stores whole milliseconds.
                created_ms = int(old_item.created_at.timestamp() * 1000)
                stored_time = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
                for candidate, current in enumerate(self._items):
                    if current.created_at not in (old_item.created_at, stored_time):
                        continue
                    if current._fingerprint is None and current.needs_blob_load:
                        continue
                    if content_fingerprint(current) == old_item._fingerprint:
                        if index is not None:
                            return None
                        index = candidate
            if index is None:
                return None
            current = self._items[index]
            self._items[index] = replace(
                new_item, record_key=current.record_key, db_id=current.db_id, created_at=current.created_at
            )
            return current

    def mark_persisted(self, original: ClipboardItem, row_id: int) -> ClipboardItem | None:
        with self._lock:
            for index, current in enumerate(self._items):
                if current.record_key is original.record_key:
                    stored = current.prepared().with_db_id(row_id).slim()
                    self._items[index] = stored
                    return stored
        return None

    def remove_many_items(self, items: list[ClipboardItem]) -> list[ClipboardItem]:
        if not items:
            return []
        keys = {item.record_key for item in items}
        with self._lock:
            removed: list[ClipboardItem] = []
            kept: list[ClipboardItem] = []
            for current in self._items:
                if current.record_key in keys:
                    removed.append(current)
                else:
                    kept.append(current)
            self._items = deque(kept, maxlen=self._max_items)
            return removed

    def remove_many(self, items: list[ClipboardItem]) -> int:
        """Remove exact records and resolve content-only requests once per item."""
        with self._lock:
            existing = {item.record_key for item in self._items}
            targets = [item for item in items if item.record_key in existing]
            selected_keys = {item.record_key for item in targets}
            missing = Counter(
                item._fingerprint for item in items if item.record_key not in existing and item._fingerprint
            )
            for current in self._items:
                if current.record_key not in selected_keys and missing[current._fingerprint] > 0:
                    targets.append(current)
                    missing[current._fingerprint] -= 1
            return len(self.remove_many_items(targets))

    def items(self) -> list[ClipboardItem]:
        with self._lock:
            return list(self._items)

    def __iter__(self) -> Iterator[ClipboardItem]:
        return iter(self.items())
