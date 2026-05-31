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


# 收藏夹上限，超出后自动淘汰最旧的收藏，避免无限增长。
MAX_FAVORITES: int = 500


def item_fingerprint(item: ClipboardItem) -> str:
    # Use cached fingerprint when available (computed before slimming)
    if item._fingerprint:
        return item._fingerprint
    h = hashlib.sha1()
    t = item.item_type.encode("utf-8")
    h.update(t)
    h.update(b"\0")
    if item.item_type == "text":
        h.update((item.text or "").encode("utf-8", errors="replace"))
        h.update(b"\0")
        h.update(item.image_bytes or b"")
    elif item.item_type == "files":
        for p in (item.file_paths or ()):
            h.update(p.encode("utf-8", errors="replace"))
            h.update(b"\n")
        h.update(b"\0")
        h.update(item.image_bytes or b"")
    elif item.item_type in ("image", "html", "rtf"):
        if item.raw_bytes is not None:
            h.update(item.raw_bytes)
        else:
            h.update((item.text or "").encode("utf-8", errors="replace"))
        h.update(b"\0")
        h.update(item.image_bytes or b"")
    else:
        h.update((item.text or "").encode("utf-8", errors="replace"))
        h.update(b"\0")
        for p in (item.file_paths or ()):
            h.update(p.encode("utf-8", errors="replace"))
            h.update(b"\n")
        h.update(b"\0")
        h.update(item.raw_bytes or b"")
    return h.hexdigest()


def _encode_item(it: ClipboardItem, write_blob) -> dict:
    """编码收藏项。大的二进制负载通过 write_blob 写入独立文件，仅在 JSON 中保留摘要引用，避免 favorites.json 膨胀。"""
    raw_ref = write_blob(it.raw_bytes) if it.raw_bytes is not None else None
    image_ref = write_blob(it.image_bytes) if it.image_bytes is not None else None
    return {
        "created_at": it.created_at.isoformat(),
        "item_type": it.item_type,
        "text": it.text,
        "file_paths": list(it.file_paths) if it.file_paths else None,
        "raw_blob": raw_ref,
        "image_blob": image_ref,
    }


def _decode_item(data: dict, read_blob) -> ClipboardItem | None:
    try:
        created_at = datetime.fromisoformat(str(data.get("created_at") or ""))
        item_type = str(data.get("item_type") or "unknown")
        text = data.get("text")
        file_paths = data.get("file_paths")
        # 新格式：blob 摘要引用；旧格式：内联 base64，保持向后兼容。
        raw_ref = data.get("raw_blob")
        image_ref = data.get("image_blob")
        if raw_ref:
            raw_bytes = read_blob(str(raw_ref))
        else:
            raw_b64 = data.get("raw_b64")
            raw_bytes = base64.b64decode(raw_b64) if raw_b64 else None
        if image_ref:
            image_bytes = read_blob(str(image_ref))
        else:
            image_b64 = data.get("image_b64")
            image_bytes = base64.b64decode(image_b64) if image_b64 else None
        fp = tuple(file_paths) if isinstance(file_paths, list) else None
        return ClipboardItem(
            created_at=created_at,
            item_type=item_type,  # type: ignore[arg-type]
            text=str(text) if text is not None else None,
            file_paths=fp,
            raw_bytes=raw_bytes,
            image_bytes=image_bytes,
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
        self._enforce_limit()
        return fid

    def remove_by_id(self, fav_id: str) -> bool:
        for i, e in enumerate(self._entries):
            if e.fav_id == fav_id:
                self._entries.pop(i)
                return True
        return False

    def replace_item(self, old_item: ClipboardItem, new_item: ClipboardItem) -> bool:
        old_id = item_fingerprint(old_item)
        new_id = item_fingerprint(new_item)
        old_idx = -1
        new_idx = -1
        for i, e in enumerate(self._entries):
            if e.fav_id == old_id and old_idx < 0:
                old_idx = i
            if e.fav_id == new_id and new_idx < 0:
                new_idx = i
        if old_idx < 0:
            return False

        if new_idx >= 0 and new_idx != old_idx:
            self._entries.pop(old_idx)
            if old_idx < new_idx:
                new_idx -= 1
            self._entries[new_idx] = FavoriteEntry(fav_id=new_id, item=new_item)
            return True

        self._entries[old_idx] = FavoriteEntry(fav_id=new_id, item=new_item)
        return True

    def toggle(self, item: ClipboardItem) -> tuple[bool, str]:
        fid = item_fingerprint(item)
        if self.remove_by_id(fid):
            return False, fid
        self._entries.insert(0, FavoriteEntry(fav_id=fid, item=item))
        self._enforce_limit()
        return True, fid

    def _enforce_limit(self) -> None:
        if len(self._entries) > MAX_FAVORITES:
            del self._entries[MAX_FAVORITES:]

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

    def _blob_dir(self) -> str:
        return os.path.join(default_app_dir(), "favorites_blobs")

    def _write_blob(self, b: bytes) -> str:
        digest = hashlib.sha1(b).hexdigest()
        d = self._blob_dir()
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, digest + ".bin")
        if not os.path.exists(p):
            with open(p, "wb") as f:
                f.write(b)
        return digest

    def _read_blob(self, digest: str) -> bytes | None:
        p = os.path.join(self._blob_dir(), digest + ".bin")
        try:
            with open(p, "rb") as f:
                return f.read()
        except Exception:
            return None

    def _prune_blobs(self, referenced: set[str]) -> None:
        d = self._blob_dir()
        if not os.path.isdir(d):
            return
        try:
            for name in os.listdir(d):
                if not name.endswith(".bin"):
                    continue
                if name[:-4] not in referenced:
                    try:
                        os.remove(os.path.join(d, name))
                    except Exception:
                        log.debug("删除未引用收藏 blob 失败: %s", name, exc_info=True)
        except Exception:
            log.debug("清理收藏 blob 目录异常", exc_info=True)

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
                it = _decode_item(item_data, self._read_blob)
                if it is None:
                    continue
                entries.append(FavoriteEntry(fav_id=fav_id, item=it))
        self._entries = entries

    def save(self) -> None:
        path = self.path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        referenced: set[str] = set()

        def write_blob(b: bytes) -> str:
            digest = self._write_blob(b)
            referenced.add(digest)
            return digest

        favorites = [{"id": e.fav_id, "item": _encode_item(e.item, write_blob)} for e in self._entries]
        data = {"favorites": favorites}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self._prune_blobs(referenced)
