from __future__ import annotations

from datetime import datetime, timezone

from cliphist.models import ClipboardItem, content_fingerprint
from cliphist.store import ClipboardHistory


def _text(value: str) -> ClipboardItem:
    return ClipboardItem(datetime.now(timezone.utc), "text", text=value).prepared()


def test_prepared_fingerprint_is_stable_and_reused_for_dedupe() -> None:
    first = _text("same")
    second = _text("same")

    assert first._fingerprint == content_fingerprint(second)
    assert first.dedupe_key() == second.dedupe_key()


def test_slim_favorite_payload_still_requests_lazy_load_without_db_id() -> None:
    item = ClipboardItem(
        datetime.now(timezone.utc),
        "image",
        raw_bytes=b"image-payload",
    ).prepared()

    slim = item.slim(item._fingerprint)

    assert slim.db_id is None
    assert slim.raw_bytes is None
    assert slim.raw_size == len(b"image-payload")
    assert slim.needs_blob_load


def test_history_remove_many_uses_stable_fingerprints() -> None:
    history = ClipboardHistory(max_items=10)
    first = _text("first")
    second = _text("second")
    history.add(first)
    history.add(second)

    removed = history.remove_many([_text("first")])

    assert removed == 1
    assert [item.text for item in history.items()] == ["second"]
