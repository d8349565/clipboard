from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class AppSettings:
    max_items: int = 200
    persist_enabled: bool = False
    db_path: str | None = None
    hotkey_show_panel: str = "Ctrl+Shift+V"
    hotkey_toggle_pause: str = "Ctrl+Shift+P"


def default_app_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ClipHist")


def default_config_path() -> str:
    return os.path.join(default_app_dir(), "config.json")


def default_db_path() -> str:
    return os.path.join(default_app_dir(), "history.sqlite3")


def load_settings() -> AppSettings:
    path = default_config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}

    max_items = int(data.get("max_items", 200))
    if max_items <= 0:
        max_items = 200
    persist_enabled = bool(data.get("persist_enabled", False))
    db_path = data.get("db_path") or None
    hotkey_show_panel = str(data.get("hotkey_show_panel") or "Ctrl+Shift+V")
    hotkey_toggle_pause = str(data.get("hotkey_toggle_pause") or "Ctrl+Shift+P")
    return AppSettings(
        max_items=max_items,
        persist_enabled=persist_enabled,
        db_path=db_path,
        hotkey_show_panel=hotkey_show_panel,
        hotkey_toggle_pause=hotkey_toggle_pause,
    )


def save_settings(settings: AppSettings) -> None:
    app_dir = default_app_dir()
    os.makedirs(app_dir, exist_ok=True)
    path = default_config_path()
    data = asdict(settings)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

