from __future__ import annotations

import json
from datetime import datetime, timezone

import cliphist.favorites as favorites_module
from cliphist.favorites import FavoritesStore
from cliphist.models import ClipboardItem


def test_favorite_blobs_are_lazy_and_saved_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(favorites_module, "default_app_dir", lambda: str(tmp_path))
    payload = b"z" * (2 * 1024 * 1024)
    item = ClipboardItem(
        datetime.now(timezone.utc),
        "image",
        raw_bytes=payload,
    ).prepared()

    store = FavoritesStore()
    store.toggle(item)
    store.save()

    saved_entry = store.entries[0]
    assert saved_entry.item.raw_bytes is None
    assert saved_entry.item.raw_size == len(payload)
    assert saved_entry.raw_blob

    calls = 0
    original_read = FavoritesStore._read_blob

    def counting_read(self, digest):
        nonlocal calls
        calls += 1
        return original_read(self, digest)

    monkeypatch.setattr(FavoritesStore, "_read_blob", counting_read)
    reloaded = FavoritesStore()
    reloaded.load()

    assert calls == 0
    lazy_item = reloaded.entries[0].item
    assert lazy_item.raw_bytes is None
    assert lazy_item.needs_blob_load
    assert reloaded.load_blobs(lazy_item).raw_bytes == payload
    assert calls == 1

    data = json.loads((tmp_path / "favorites.json").read_text(encoding="utf-8"))
    assert data["favorites"][0]["item"]["raw_size"] == len(payload)
    assert "raw_b64" not in data["favorites"][0]["item"]


def test_favorite_save_is_atomic_and_prunes_removed_blobs(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(favorites_module, "default_app_dir", lambda: str(tmp_path))
    item = ClipboardItem(datetime.now(timezone.utc), "image", raw_bytes=b"payload").prepared()
    store = FavoritesStore()
    _, fav_id = store.toggle(item)
    store.save()
    blob_path = next((tmp_path / "favorites_blobs").glob("*.bin"))

    assert store.remove_by_id(fav_id)
    store.save()

    assert not blob_path.exists()
    assert not (tmp_path / "favorites.json.tmp").exists()
