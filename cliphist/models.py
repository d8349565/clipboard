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

    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(timezone.utc)

    def slim(self, fingerprint: str | None = None) -> ClipboardItem:
        """Return a copy without heavy blob fields, preserving metadata for display."""
        if self.raw_bytes is None and self.image_bytes is None:
            if fingerprint and not self._fingerprint:
                return _dc_replace(self, _fingerprint=fingerprint)
            return self
        return _dc_replace(
            self,
            raw_bytes=None,
            image_bytes=None,
            raw_size=self.raw_size or len(self.raw_bytes or b""),
            image_size=self.image_size or len(self.image_bytes or b""),
            _raw_hash=self._raw_hash or (_bytes_hash(self.raw_bytes) if self.raw_bytes else None),
            _image_hash=self._image_hash or (_bytes_hash(self.image_bytes) if self.image_bytes else None),
            _fingerprint=fingerprint or self._fingerprint,
        )

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
        return (self.raw_size > 0 or self.image_size > 0) and self.db_id is not None

    def dedupe_key(self) -> tuple:
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
