from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

log = logging.getLogger(__name__)

from .models import ClipboardItem, ClipboardItemType


class SQLiteHistoryStore:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clipboard_items (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at_ms INTEGER NOT NULL,
              item_type TEXT NOT NULL,
              text TEXT,
              file_paths_json TEXT,
              raw_bytes BLOB
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON clipboard_items(created_at_ms DESC)")
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def insert(self, item: ClipboardItem) -> None:
        self._do_insert(item)
        self._conn.commit()

    def insert_and_trim(self, item: ClipboardItem, limit: int) -> None:
        """Insert an item and trim old rows in a single transaction (one commit)."""
        self._do_insert(item)
        if limit > 0:
            self._do_trim(limit)
        self._conn.commit()

    def _do_insert(self, item: ClipboardItem) -> None:
        created_at_ms = int(item.created_at.timestamp() * 1000)
        file_paths_json = None
        if item.file_paths is not None:
            file_paths_json = json.dumps(list(item.file_paths), ensure_ascii=False)
        self._conn.execute(
            "INSERT INTO clipboard_items(created_at_ms, item_type, text, file_paths_json, raw_bytes) VALUES (?,?,?,?,?)",
            (created_at_ms, item.item_type, item.text, file_paths_json, item.raw_bytes),
        )

    def trim_to_limit(self, limit: int) -> None:
        if limit <= 0:
            return
        self._do_trim(limit)
        self._conn.commit()

    def _do_trim(self, limit: int) -> None:
        self._conn.execute(
            """
            DELETE FROM clipboard_items
            WHERE id NOT IN (
              SELECT id FROM clipboard_items
              ORDER BY created_at_ms DESC
              LIMIT ?
            )
            """,
            (limit,),
        )

    def clear(self) -> None:
        self._conn.execute("DELETE FROM clipboard_items")
        self._conn.commit()

    def load_recent(self, limit: int) -> list[ClipboardItem]:
        if limit <= 0:
            return []
        cur = self._conn.execute(
            "SELECT created_at_ms, item_type, text, file_paths_json, raw_bytes FROM clipboard_items ORDER BY created_at_ms DESC LIMIT ?",
            (limit,),
        )
        items: list[ClipboardItem] = []
        for created_at_ms, item_type, text, file_paths_json, raw_bytes in cur.fetchall():
            created_at = datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc)
            file_paths = None
            if file_paths_json:
                try:
                    file_paths = tuple(json.loads(file_paths_json))
                except Exception:
                    file_paths = None
            items.append(
                ClipboardItem(
                    created_at=created_at,
                    item_type=_coerce_item_type(item_type),
                    text=text,
                    file_paths=file_paths,
                    raw_bytes=raw_bytes,
                )
            )
        return items


def _coerce_item_type(v: str) -> ClipboardItemType:
    if v in ("text", "files", "image", "html", "rtf", "unknown"):
        return v  # type: ignore[return-value]
    return "unknown"

