from __future__ import annotations

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
