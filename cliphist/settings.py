from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from tempfile import NamedTemporaryFile

from .autostart import is_autostart_enabled
from .themes import normalize_theme

log = logging.getLogger(__name__)


MAX_ITEMS_LIMIT: int = 10000


@dataclass(frozen=True, slots=True)
class AppSettings:
    max_items: int = 1000
    persist_enabled: bool = False
    auto_paste: bool = False
    autostart_enabled: bool = False
    db_path: str | None = None
    hotkey_show_panel: str = "Alt+C"
    hotkey_toggle_pause: str = "Alt+P"
    panel_width: int = 640
    panel_height: int = 620
    theme: str = "system"


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
    auto_paste = bool(data.get("auto_paste", False))
    autostart_enabled = bool(data.get("autostart_enabled", is_autostart_enabled()))
    db_path = data.get("db_path") or None
    hotkey_show_panel = str(data["hotkey_show_panel"]) if "hotkey_show_panel" in data and data["hotkey_show_panel"] is not None else "Alt+C"
    hotkey_toggle_pause = str(data["hotkey_toggle_pause"]) if "hotkey_toggle_pause" in data and data["hotkey_toggle_pause"] is not None else "Alt+P"
    panel_width = int(data.get("panel_width", 640))
    panel_height = int(data.get("panel_height", 620))
    panel_width = max(400, min(panel_width, 1600))
    panel_height = max(440, min(panel_height, 1200))
    return AppSettings(
        max_items=max_items,
        persist_enabled=persist_enabled,
        auto_paste=auto_paste,
        autostart_enabled=autostart_enabled,
        db_path=db_path,
        hotkey_show_panel=hotkey_show_panel,
        hotkey_toggle_pause=hotkey_toggle_pause,
        panel_width=panel_width,
        panel_height=panel_height,
        theme=normalize_theme(data.get("theme", "system")),
    )


def save_settings(settings: AppSettings) -> None:
    path = default_config_path()
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    temp_path: str | None = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory, prefix=".config-", suffix=".tmp", delete=False,
        ) as file:
            temp_path = file.name
            json.dump(asdict(settings), file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError:
                log.debug("清理临时配置文件失败", exc_info=True)
