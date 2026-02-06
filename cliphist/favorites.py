from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger(__name__)

from .models import ClipboardItem
from .settings import default_app_dir


def item_fingerprint(item: ClipboardItem) -> str:
    h = hashlib.sha1()
    t = item.item_type.encode("utf-8")
    h.update(t)
    h.update(b"\0")
    if item.item_type == "text":
        h.update((item.text or "").encode("utf-8", errors="replace"))
    elif item.item_type == "files":
        for p in (item.file_paths or ()):
            h.update(p.encode("utf-8", errors="replace"))
            h.update(b"\n")
    elif item.item_type in ("image", "html", "rtf"):
        if item.raw_bytes is not None:
            h.update(item.raw_bytes)
        else:
            h.update((item.text or "").encode("utf-8", errors="replace"))
    else:
        h.update((item.text or "").encode("utf-8", errors="replace"))
        h.update(b"\0")
        for p in (item.file_paths or ()):
            h.update(p.encode("utf-8", errors="replace"))
            h.update(b"\n")
        h.update(b"\0")
        h.update(item.raw_bytes or b"")
    return h.hexdigest()


def _encode_item(it: ClipboardItem) -> dict:
    return {
        "created_at": it.created_at.isoformat(),
        "item_type": it.item_type,
        "text": it.text,
        "file_paths": list(it.file_paths) if it.file_paths else None,
        "raw_b64": base64.b64encode(it.raw_bytes).decode("ascii") if it.raw_bytes is not None else None,
    }


def _decode_item(data: dict) -> ClipboardItem | None:
    try:
        created_at = datetime.fromisoformat(str(data.get("created_at") or ""))
        item_type = str(data.get("item_type") or "unknown")
        text = data.get("text")
        file_paths = data.get("file_paths")
        raw_b64 = data.get("raw_b64")
        raw_bytes = base64.b64decode(raw_b64) if raw_b64 else None
        fp = tuple(file_paths) if isinstance(file_paths, list) else None
        return ClipboardItem(
            created_at=created_at,
            item_type=item_type,  # type: ignore[arg-type]
            text=str(text) if text is not None else None,
            file_paths=fp,
            raw_bytes=raw_bytes,
        )
    except Exception:
        return None


@dataclass(slots=True)
class FavoriteEntry:
    fav_id: str
    item: ClipboardItem


class FavoritesStore:
    def __init__(self, entries: list[FavoriteEntry] | None = None) -> None:
        self._entries: list[FavoriteEntry] = entries[:] if entries else []

    @property
    def entries(self) -> list[FavoriteEntry]:
        return self._entries[:]

    def ids(self) -> list[str]:
        return [e.fav_id for e in self._entries]

    def contains(self, item: ClipboardItem) -> bool:
        fid = item_fingerprint(item)
        return any(e.fav_id == fid for e in self._entries)

    def add_or_promote(self, item: ClipboardItem) -> str:
        fid = item_fingerprint(item)
        for i, e in enumerate(self._entries):
            if e.fav_id == fid:
                self._entries.insert(0, self._entries.pop(i))
                return fid
        self._entries.insert(0, FavoriteEntry(fav_id=fid, item=item))
        return fid

    def remove_by_id(self, fav_id: str) -> bool:
        for i, e in enumerate(self._entries):
            if e.fav_id == fav_id:
                self._entries.pop(i)
                return True
        return False

    def toggle(self, item: ClipboardItem) -> tuple[bool, str]:
        fid = item_fingerprint(item)
        if self.remove_by_id(fid):
            return False, fid
        self._entries.insert(0, FavoriteEntry(fav_id=fid, item=item))
        return True, fid

    def move(self, from_index: int, to_index: int) -> None:
        if from_index < 0 or from_index >= len(self._entries):
            return
        if to_index < 0:
            to_index = 0
        if to_index >= len(self._entries):
            to_index = len(self._entries) - 1
        if from_index == to_index:
            return
        e = self._entries.pop(from_index)
        self._entries.insert(to_index, e)

    def set_order(self, fav_ids_in_order: list[str]) -> None:
        lookup = {e.fav_id: e for e in self._entries}
        new_entries: list[FavoriteEntry] = []
        seen: set[str] = set()
        for fid in fav_ids_in_order:
            e = lookup.get(fid)
            if e is not None:
                new_entries.append(e)
                seen.add(fid)
        for e in self._entries:
            if e.fav_id not in seen:
                new_entries.append(e)
        self._entries = new_entries

    def path(self) -> str:
        return os.path.join(default_app_dir(), "favorites.json")

    def load(self) -> None:
        path = self.path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        raw_entries = data.get("favorites") if isinstance(data, dict) else None
        entries: list[FavoriteEntry] = []
        if isinstance(raw_entries, list):
            for row in raw_entries:
                if not isinstance(row, dict):
                    continue
                fav_id = str(row.get("id") or "")
                item_data = row.get("item")
                if not fav_id or not isinstance(item_data, dict):
                    continue
                it = _decode_item(item_data)
                if it is None:
                    continue
                entries.append(FavoriteEntry(fav_id=fav_id, item=it))
        self._entries = entries

    def save(self) -> None:
        path = self.path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {"favorites": [{"id": e.fav_id, "item": _encode_item(e.item)} for e in self._entries]}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
