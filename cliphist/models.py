from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Sequence


ClipboardItemType = Literal["text", "files", "image", "html", "rtf", "unknown"]


@dataclass(frozen=True, slots=True)
class ClipboardItem:
    created_at: datetime
    item_type: ClipboardItemType
    text: str | None = None
    file_paths: tuple[str, ...] | None = None
    raw_bytes: bytes | None = None
    image_bytes: bytes | None = None

    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(timezone.utc)

    def dedupe_key(self) -> tuple:
        if self.item_type == "text":
            return ("text", self.text or "", self.image_bytes or b"")
        if self.item_type == "files":
            return ("files", self.file_paths or ())
        if self.item_type == "image":
            return ("image", self.raw_bytes or b"")
        if self.item_type in ("html", "rtf"):
            if self.raw_bytes is not None:
                return (self.item_type, self.raw_bytes, self.image_bytes or b"")
            return (self.item_type, self.text or "", self.image_bytes or b"")
        return ("unknown", self.text or "", self.file_paths or (), self.raw_bytes or b"", self.image_bytes or b"")

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
            size = len(self.raw_bytes or b"")
            return f"(图片 {size} bytes)"
        if self.item_type in ("html", "rtf"):
            s = (self.text or "").replace("\r\n", "\n").replace("\r", "\n")
            if s:
                return s if len(s) <= max_len else s[: max_len - 1] + "…"
        return f"({self.item_type})"
