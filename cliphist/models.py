from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace as _dc_replace
from datetime import datetime, timezone
from typing import Literal, Sequence


ClipboardItemType = Literal["text", "files", "image", "html", "rtf", "unknown"]

# 集中定义负载大小上限，供 capture/persistence 共用，避免各处重复定义。
MAX_IMAGE_BYTES: int = 8 * 1024 * 1024  # 8 MB
MAX_RICH_RAW_BYTES: int = 2 * 1024 * 1024  # 2 MB


def _bytes_hash(b: bytes) -> str:
    """Return a short hex digest for deduplication without keeping full bytes in memory."""
    return hashlib.sha1(b).hexdigest()


def content_fingerprint(item: "ClipboardItem") -> str:
    """Return the stable content id shared by history and favorites."""
    if item._fingerprint:
        return item._fingerprint
    h = hashlib.sha1()
    h.update(item.item_type.encode("utf-8"))
    h.update(b"\0")
    if item.item_type == "text":
        h.update((item.text or "").encode("utf-8", errors="replace"))
        h.update(b"\0")
        h.update(item.image_bytes or b"")
    elif item.item_type == "files":
        for path in item.file_paths or ():
            h.update(path.encode("utf-8", errors="replace"))
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
        for path in item.file_paths or ():
            h.update(path.encode("utf-8", errors="replace"))
            h.update(b"\n")
        h.update(b"\0")
        h.update(item.raw_bytes or b"")
    return h.hexdigest()


def _search_text_lower(item: "ClipboardItem") -> str:
    if item.item_type == "files":
        source = "\n".join(item.file_paths or ())
    else:
        source = item.text or ""
    return " ".join(source.replace("\r", "\n").split()).lower()


@dataclass(frozen=True, slots=True)
class ClipboardItem:
    created_at: datetime
    item_type: ClipboardItemType
    text: str | None = None
    file_paths: tuple[str, ...] | None = None
    raw_bytes: bytes | None = None
    image_bytes: bytes | None = None
    db_id: int | None = None
    raw_size: int = 0
    image_size: int = 0
    _raw_hash: str | None = None
    _image_hash: str | None = None
    _fingerprint: str | None = None
    _search_index: str | None = None

    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(timezone.utc)

    def slim(self, fingerprint: str | None = None) -> ClipboardItem:
        """Return a copy without heavy blob fields, preserving metadata for display."""
        if self.raw_bytes is None and self.image_bytes is None:
            if fingerprint and not self._fingerprint:
                return _dc_replace(self, _fingerprint=fingerprint)
            return self
        cached_fingerprint = fingerprint or self._fingerprint
        return _dc_replace(
            self,
            raw_bytes=None,
            image_bytes=None,
            raw_size=self.raw_size or len(self.raw_bytes or b""),
            image_size=self.image_size or len(self.image_bytes or b""),
            _raw_hash=self._raw_hash or (_bytes_hash(self.raw_bytes) if self.raw_bytes and not cached_fingerprint else None),
            _image_hash=self._image_hash or (_bytes_hash(self.image_bytes) if self.image_bytes and not cached_fingerprint else None),
            _fingerprint=cached_fingerprint,
        )

    def prepared(self) -> ClipboardItem:
        """Cache the content fingerprint before crossing into the Qt thread."""
        fingerprint = self._fingerprint or content_fingerprint(self)
        search_index = self._search_index if self._search_index is not None else _search_text_lower(self)
        if fingerprint == self._fingerprint and search_index == self._search_index:
            return self
        return _dc_replace(self, _fingerprint=fingerprint, _search_index=search_index)

    def with_search_index(self) -> ClipboardItem:
        if self._search_index is not None:
            return self
        return _dc_replace(self, _search_index=_search_text_lower(self))

    def with_db_id(self, db_id: int) -> ClipboardItem:
        return _dc_replace(self, db_id=db_id)

    def with_blobs(self, raw_bytes: bytes | None, image_bytes: bytes | None) -> ClipboardItem:
        """Return a copy with blob fields restored (from DB on-demand load)."""
        return _dc_replace(self, raw_bytes=raw_bytes, image_bytes=image_bytes)

    @property
    def needs_blob_load(self) -> bool:
        """True if this item has blobs in DB but they are not loaded in memory."""
        if self.raw_bytes is not None or self.image_bytes is not None:
            return False
        return self.raw_size > 0 or self.image_size > 0

    def dedupe_key(self) -> tuple:
        if self._fingerprint:
            return (self.item_type, self._fingerprint)
        if self.item_type == "text":
            ih = self._image_hash or (_bytes_hash(self.image_bytes) if self.image_bytes else "")
            return ("text", self.text or "", ih)
        if self.item_type == "files":
            return ("files", self.file_paths or ())
        if self.item_type == "image":
            h = self._raw_hash or (_bytes_hash(self.raw_bytes) if self.raw_bytes else "")
            return ("image", h)
        if self.item_type in ("html", "rtf"):
            rh = self._raw_hash or (_bytes_hash(self.raw_bytes) if self.raw_bytes else "")
            ih = self._image_hash or (_bytes_hash(self.image_bytes) if self.image_bytes else "")
            if rh:
                return (self.item_type, rh, ih)
            return (self.item_type, self.text or "", ih)
        return ("unknown", self.text or "", self.file_paths or (),
                self._raw_hash or (_bytes_hash(self.raw_bytes) if self.raw_bytes else ""),
                self._image_hash or (_bytes_hash(self.image_bytes) if self.image_bytes else ""))

    def preview(self, max_len: int = 120) -> str:
        if self.item_type == "text":
            s = (self.text or "").replace("\r\n", "\n").replace("\r", "\n")
            return s if len(s) <= max_len else s[: max_len - 1] + "…"
        if self.item_type == "files":
            paths: Sequence[str] = self.file_paths or ()
            if not paths:
                return "(空文件列表)"
            if len(paths) == 1:
                return paths[0]
            return f"{paths[0]} +{len(paths)-1}"
        if self.item_type == "image":
            size = self.raw_size or len(self.raw_bytes or b"")
            return f"(图片 {size} bytes)"
        if self.item_type in ("html", "rtf"):
            s = (self.text or "").replace("\r\n", "\n").replace("\r", "\n")
            if s:
                return s if len(s) <= max_len else s[: max_len - 1] + "…"
        return f"({self.item_type})"
