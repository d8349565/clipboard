from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass

from .autostart import is_autostart_enabled

log = logging.getLogger(__name__)


MAX_ITEMS_LIMIT: int = 10000


@dataclass(frozen=True, slots=True)
class AppSettings:
    max_items: int = 1000
    persist_enabled: bool = False
    autostart_enabled: bool = False
    db_path: str | None = None
    hotkey_show_panel: str = "Alt+C"
    hotkey_toggle_pause: str = "Alt+P"
    panel_width: int = 640
    panel_height: int = 620


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

    max_items = int(data.get("max_items", 1000))
    if max_items <= 0:
        max_items = 1000
    max_items = min(max_items, MAX_ITEMS_LIMIT)
    persist_enabled = bool(data.get("persist_enabled", False))
    autostart_enabled = bool(data.get("autostart_enabled", is_autostart_enabled()))
    db_path = data.get("db_path") or None
    hotkey_show_panel = str(data.get("hotkey_show_panel") or "Alt+C")
    hotkey_toggle_pause = str(data.get("hotkey_toggle_pause") or "Alt+P")
    panel_width = int(data.get("panel_width", 640))
    panel_height = int(data.get("panel_height", 620))
    panel_width = max(400, min(panel_width, 1600))
    panel_height = max(350, min(panel_height, 1200))
    return AppSettings(
        max_items=max_items,
        persist_enabled=persist_enabled,
        autostart_enabled=autostart_enabled,
        db_path=db_path,
        hotkey_show_panel=hotkey_show_panel,
        hotkey_toggle_pause=hotkey_toggle_pause,
        panel_width=panel_width,
        panel_height=panel_height,
    )


def save_settings(settings: AppSettings) -> None:
    app_dir = default_app_dir()
    os.makedirs(app_dir, exist_ok=True)
    path = default_config_path()
    data = asdict(settings)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
