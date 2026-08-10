from __future__ import annotations

import json
import logging
import os
import sqlite3
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

from .models import ClipboardItem, ClipboardItemType, _bytes_hash, MAX_IMAGE_BYTES, MAX_RICH_RAW_BYTES


class SQLiteHistoryStore:
    _TRIM_BATCH = 50

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_schema()
        self._count_hint = self.count()

    @property
    def path(self) -> str:
        return self._path

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clipboard_items (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at_ms INTEGER NOT NULL,
              item_type TEXT NOT NULL,
              text TEXT,
              file_paths_json TEXT,
              raw_bytes BLOB,
              image_bytes BLOB
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON clipboard_items(created_at_ms DESC)")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_created_at_id ON clipboard_items(created_at_ms DESC, id DESC)"
        )
        self._ensure_column("image_bytes", "BLOB")
        self._ensure_column("fingerprint", "TEXT")
        self._conn.commit()

    def _ensure_column(self, column_name: str, column_type: str) -> None:
        cur = self._conn.execute("PRAGMA table_info(clipboard_items)")
        existing = {str(row[1]).lower() for row in cur.fetchall()}
        if column_name.lower() in existing:
            return
        self._conn.execute(f"ALTER TABLE clipboard_items ADD COLUMN {column_name} {column_type}")

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def insert(self, item: ClipboardItem) -> int:
        row_id = self._do_insert(item)
        self._conn.commit()
        return row_id

    def insert_and_trim(self, item: ClipboardItem, limit: int) -> int:
        """Insert and periodically trim in one transaction.

        Trimming every clipboard event rescans the retained index. A small
        overflow window amortizes that work while load_recent still enforces
        the user-visible limit.
        """
        row_id = self._do_insert(item)
        self._count_hint += 1
        if limit > 0 and self._count_hint > limit + self._TRIM_BATCH:
            self._do_trim(limit)
            self._count_hint = limit
        self._conn.commit()
        return row_id

    def _do_insert(self, item: ClipboardItem) -> int:
        created_at_ms = int(item.created_at.timestamp() * 1000)
        file_paths_json = None
        if item.file_paths is not None:
            file_paths_json = json.dumps(list(item.file_paths), ensure_ascii=False)
        cur = self._conn.execute(
            "INSERT INTO clipboard_items(created_at_ms, item_type, text, file_paths_json, raw_bytes, image_bytes, fingerprint) VALUES (?,?,?,?,?,?,?)",
            (created_at_ms, item.item_type, item.text, file_paths_json, item.raw_bytes, item.image_bytes, item._fingerprint),
        )
        return cur.lastrowid or 0

    def trim_to_limit(self, limit: int) -> None:
        if limit <= 0:
            return
        self._do_trim(limit)
        self._conn.commit()
        self._count_hint = min(self._count_hint, limit)

    def _do_trim(self, limit: int) -> None:
        self._conn.execute(
            """
            DELETE FROM clipboard_items
            WHERE id NOT IN (
              SELECT id FROM clipboard_items
              ORDER BY created_at_ms DESC, id DESC
              LIMIT ?
            )
            """,
            (limit,),
        )

    def clear(self) -> None:
        self._conn.execute("DELETE FROM clipboard_items")
        self._conn.commit()
        self._count_hint = 0
        # DELETE 不会回收已分配的磁盘空间，VACUUM 重建数据库以缩小文件体积。
        # WAL 模式下需先做一次检查点，确保 WAL 中的删除已合并进主库。
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            self._conn.execute("VACUUM;")
            self._conn.commit()
        except Exception:
            log.warning("清空历史后 VACUUM 回收空间失败", exc_info=True)

    def replace_all(self, items: list[ClipboardItem]) -> None:
        self._conn.execute("BEGIN TRANSACTION")
        try:
            self._conn.execute("DELETE FROM clipboard_items")
            for item in items:
                self._do_insert(item)
            self._conn.commit()
            self._count_hint = len(items)
        except Exception:
            self._conn.rollback()
            raise

    def load_recent(self, limit: int, offset: int = 0) -> list[ClipboardItem]:
        if limit <= 0:
            return []
        cur = self._conn.execute(
            "SELECT id, created_at_ms, item_type, text, file_paths_json, raw_bytes, image_bytes FROM clipboard_items ORDER BY created_at_ms DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        items: list[ClipboardItem] = []
        for row_id, created_at_ms, item_type, text, file_paths_json, raw_bytes, image_bytes in cur.fetchall():
            created_at = datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc)
            file_paths = None
            if file_paths_json:
                try:
                    file_paths = tuple(json.loads(file_paths_json))
                except Exception:
                    file_paths = None
            coerced_type = _coerce_item_type(item_type)
            raw_bytes, image_bytes = _sanitize_payload(coerced_type, raw_bytes, image_bytes)
            if coerced_type == "image" and not raw_bytes:
                continue
            items.append(
                ClipboardItem(
                    created_at=created_at,
                    item_type=coerced_type,
                    text=text,
                    file_paths=file_paths,
                    raw_bytes=raw_bytes,
                    image_bytes=image_bytes,
                    db_id=row_id,
                ).prepared()
            )
        return items

    def load_recent_slim(self, limit: int, offset: int = 0) -> list[ClipboardItem]:
        """Load recent items without blob data — only metadata for display."""
        if limit <= 0:
            return []
        cur = self._conn.execute(
            "SELECT id, created_at_ms, item_type, text, file_paths_json,"
            " LENGTH(raw_bytes), LENGTH(image_bytes), fingerprint FROM clipboard_items"
            " ORDER BY created_at_ms DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        items: list[ClipboardItem] = []
        for row_id, created_at_ms, item_type, text, file_paths_json, raw_len, img_len, fingerprint in cur.fetchall():
            created_at = datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc)
            file_paths = None
            if file_paths_json:
                try:
                    file_paths = tuple(json.loads(file_paths_json))
                except Exception:
                    file_paths = None
            coerced_type = _coerce_item_type(item_type)
            raw_size = raw_len or 0
            img_size = img_len or 0
            if coerced_type == "image" and raw_size == 0:
                continue
            items.append(
                ClipboardItem(
                    created_at=created_at,
                    item_type=coerced_type,
                    text=text,
                    file_paths=file_paths,
                    raw_bytes=None,
                    image_bytes=None,
                    db_id=row_id,
                    raw_size=raw_size,
                    image_size=img_size,
                    _fingerprint=fingerprint,
                ).with_search_index()
            )
        return items

    def load_blobs(self, db_id: int) -> tuple[bytes | None, bytes | None]:
        """Load raw_bytes and image_bytes for a single item by its DB id."""
        cur = self._conn.execute(
            "SELECT raw_bytes, image_bytes FROM clipboard_items WHERE id = ?",
            (db_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None, None
        return row[0], row[1]

    def update_text(self, db_id: int, text: str | None, fingerprint: str | None = None) -> None:
        """Update only the text field for an item (used by text editing)."""
        self._conn.execute(
            "UPDATE clipboard_items SET text = ?, fingerprint = ? WHERE id = ?",
            (text, fingerprint, db_id),
        )
        self._conn.commit()

    def delete_by_ids(self, db_ids: list[int]) -> int:
        ids = sorted({int(value) for value in db_ids if int(value) > 0})
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        cur = self._conn.execute(f"DELETE FROM clipboard_items WHERE id IN ({placeholders})", ids)
        self._conn.commit()
        deleted = max(0, int(cur.rowcount or 0))
        self._count_hint = max(0, self._count_hint - deleted)
        return deleted

    def count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM clipboard_items")
        row = cur.fetchone()
        return row[0] if row else 0

    def backup_to(self, target_path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
        destination = sqlite3.connect(target_path)
        try:
            self._conn.backup(destination)
        finally:
            destination.close()


def _coerce_item_type(v: str) -> ClipboardItemType:
    if v in ("text", "files", "image", "html", "rtf", "unknown"):
        return v  # type: ignore[return-value]
    return "unknown"


def _sanitize_payload(
    item_type: ClipboardItemType,
    raw_bytes: bytes | None,
    image_bytes: bytes | None,
) -> tuple[bytes | None, bytes | None]:
    if item_type in ("text", "files"):
        return raw_bytes, None
    if item_type in ("html", "rtf"):
        if raw_bytes is not None and len(raw_bytes) > MAX_RICH_RAW_BYTES:
            raw_bytes = None
        return raw_bytes, None
    if item_type == "image":
        if raw_bytes is not None and len(raw_bytes) > MAX_IMAGE_BYTES:
            return None, None
        return raw_bytes, None
    return raw_bytes, None


class AsyncSQLiteHistoryStore:
    """Serialize every SQLite operation on one dedicated worker thread."""

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ClipHistSQLite")
        self._backend: SQLiteHistoryStore | None = None
        self._closed = False

    @property
    def path(self) -> str:
        return self._path

    def _invoke(self, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        if self._backend is None:
            self._backend = SQLiteHistoryStore(self._path)
        return getattr(self._backend, method)(*args, **kwargs)

    def submit(self, method: str, *args: Any, **kwargs: Any) -> Future:
        if self._closed:
            raise RuntimeError("history store is closed")
        return self._executor.submit(self._invoke, method, args, kwargs)

    def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        return self.submit(method, *args, **kwargs).result()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        def _close() -> None:
            if self._backend is not None:
                self._backend.close()

        try:
            self._executor.submit(_close).result()
        finally:
            self._executor.shutdown(wait=True, cancel_futures=False)
