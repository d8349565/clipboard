from __future__ import annotations

import sqlite3
import random
import zlib
from datetime import datetime, timedelta, timezone

from cliphist.models import ClipboardItem
from cliphist.persistence import AsyncSQLiteHistoryStore, SQLiteHistoryStore


def _item(index: int) -> ClipboardItem:
    return ClipboardItem(
        datetime.now(timezone.utc) + timedelta(milliseconds=index),
        "text",
        text=f"item-{index}",
    ).prepared()


def test_batched_trim_and_delete_by_ids(tmp_path) -> None:
    store = SQLiteHistoryStore(str(tmp_path / "history.sqlite3"))
    try:
        ids = [store.insert_and_trim(_item(index), 10) for index in range(70)]
        assert store.count() <= 60
        recent = store.load_recent_slim(10)
        assert len(recent) == 10

        deleted = store.delete_by_ids([ids[-1], ids[-2]])
        assert deleted == 2
        assert ids[-1] not in {item.db_id for item in store.load_recent_slim(70)}
    finally:
        store.close()


def test_async_store_serializes_operations(tmp_path) -> None:
    store = AsyncSQLiteHistoryStore(str(tmp_path / "async.sqlite3"))
    try:
        futures = [store.submit("insert_and_trim", _item(index), 100) for index in range(20)]
        row_ids = [future.result(timeout=5) for future in futures]

        assert len(set(row_ids)) == 20
        assert store.call("count") == 20
        assert len(store.call("load_recent_slim", 20)) == 20
        backup_path = tmp_path / "backup.sqlite3"
        store.call("backup_to", str(backup_path))
        backup = SQLiteHistoryStore(str(backup_path))
        try:
            assert backup.count() == 20
        finally:
            backup.close()
    finally:
        store.close()


def _image(payload: bytes) -> ClipboardItem:
    return ClipboardItem(datetime.now(timezone.utc), "image", raw_bytes=payload).prepared()


def test_images_are_losslessly_compressed_and_slim_uses_original_size(tmp_path) -> None:
    store = SQLiteHistoryStore(str(tmp_path / "images.sqlite3"))
    raw = (b"DIB-pixels-" * 20_000) + b"tail"
    try:
        row_id = store.insert(_image(raw))
        stored_length, encoding, raw_size = store._conn.execute(
            "SELECT LENGTH(raw_bytes), raw_encoding, raw_size FROM clipboard_items WHERE id = ?",
            (row_id,),
        ).fetchone()

        assert encoding == "zlib"
        assert raw_size == len(raw)
        assert stored_length < len(raw)
        assert store.load_blobs(row_id) == (raw, None)
        assert store.load_recent(1)[0].raw_bytes == raw
        assert store.load_recent_slim(1)[0].raw_size == len(raw)
        backup_path = tmp_path / "images-backup.sqlite3"
        store.backup_to(str(backup_path))
        backup = SQLiteHistoryStore(str(backup_path))
        try:
            assert backup.load_blobs(row_id) == (raw, None)
        finally:
            backup.close()
    finally:
        store.close()


def test_incompressible_images_keep_identity_bytes(tmp_path) -> None:
    store = SQLiteHistoryStore(str(tmp_path / "incompressible.sqlite3"))
    raw = random.Random(1234).randbytes(16_384)
    try:
        row_id = store.insert(_image(raw))
        stored, encoding, raw_size = store._conn.execute(
            "SELECT raw_bytes, raw_encoding, raw_size FROM clipboard_items WHERE id = ?",
            (row_id,),
        ).fetchone()

        assert encoding == "identity"
        assert raw_size == len(raw)
        assert stored == raw
        assert store.load_blobs(row_id) == (raw, None)
    finally:
        store.close()


def test_load_blobs_preserves_auxiliary_image_bytes_for_non_image_items(tmp_path) -> None:
    store = SQLiteHistoryStore(str(tmp_path / "auxiliary.sqlite3"))
    raw = b"rich-format"
    auxiliary = b"auxiliary-dib"
    try:
        row_id = store.insert(
            ClipboardItem(
                datetime.now(timezone.utc),
                "html",
                text="preview",
                raw_bytes=raw,
                image_bytes=auxiliary,
            ).prepared()
        )
        assert store.load_blobs(row_id) == (raw, auxiliary)
    finally:
        store.close()


def test_legacy_database_without_encoding_columns_still_loads(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    raw = b"legacy-dib" * 100
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE clipboard_items ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, created_at_ms INTEGER NOT NULL, "
            "item_type TEXT NOT NULL, text TEXT, file_paths_json TEXT, "
            "raw_bytes BLOB, image_bytes BLOB)"
        )
        conn.execute(
            "INSERT INTO clipboard_items(created_at_ms, item_type, raw_bytes) VALUES (?,?,?)",
            (1, "image", raw),
        )
        conn.commit()
    finally:
        conn.close()

    store = SQLiteHistoryStore(str(path))
    try:
        assert store.load_blobs(1) == (raw, None)
        assert store.load_recent_slim(1)[0].raw_size == len(raw)
        status = store.compact_images(limit=1)
        assert status == {"last_id": 1, "processed": 1, "saved_bytes": len(raw) - len(zlib.compress(raw)), "done": True}
        assert store.load_blobs(1) == (raw, None)
    finally:
        store.close()


def test_corrupt_compressed_image_is_skipped_without_breaking_other_rows(tmp_path) -> None:
    store = SQLiteHistoryStore(str(tmp_path / "corrupt.sqlite3"))
    raw = b"safe-pixels" * 1000
    try:
        bad_id = store.insert(_image(raw))
        good_id = store.insert(_item(1))
        store._conn.execute(
            "UPDATE clipboard_items SET raw_bytes = ?, raw_encoding = 'zlib', raw_size = ? WHERE id = ?",
            (b"not-a-zlib-stream", len(raw), bad_id),
        )
        store._conn.commit()

        assert store.load_blobs(bad_id) == (None, None)
        recent = store.load_recent(10)
        assert [item.db_id for item in recent] == [good_id]
    finally:
        store.close()


def test_compact_images_is_bounded_and_reports_saved_bytes(tmp_path) -> None:
    store = SQLiteHistoryStore(str(tmp_path / "compact.sqlite3"))
    raw = b"old-image" * 2000
    try:
        ids = []
        for index in range(3):
            payload = raw + bytes([index])
            row_id = store.insert(_image(payload))
            # Make these rows look like legacy identity rows so the explicit
            # maintenance pass has work to do.
            store._conn.execute(
                "UPDATE clipboard_items SET raw_bytes = ?, raw_encoding = 'identity', raw_size = 0 WHERE id = ?",
                (payload, row_id),
            )
            ids.append(row_id)
        store._conn.commit()
        first = store.compact_images(limit=2)
        expected = sum(len(raw + bytes([index])) - len(zlib.compress(raw + bytes([index]))) for index in range(2))
        assert first == {"last_id": ids[1], "processed": 2, "saved_bytes": expected, "done": False}

        second = store.compact_images(after_id=first["last_id"], limit=2)
        expected_last = len(raw + bytes([2])) - len(zlib.compress(raw + bytes([2])))
        assert second == {"last_id": ids[2], "processed": 1, "saved_bytes": expected_last, "done": True}
        assert all(store.load_blobs(row_id)[0] == raw + bytes([index]) for index, row_id in enumerate(ids))
    finally:
        store.close()
