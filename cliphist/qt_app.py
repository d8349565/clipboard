from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import sys
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

import win32api
import win32con
import win32gui

log = logging.getLogger(__name__)

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QStyle, QSystemTrayIcon

from .autostart import is_autostart_enabled, set_autostart_enabled
from .capture import OversizedImageNotice
from .favorites import FavoritesStore, item_fingerprint
from .hotkeys import HotkeySpec, parse_hotkey_sequence
from .models import ClipboardItem
from .persistence import AsyncSQLiteHistoryStore
from .settings import AppSettings, default_db_path, load_settings, save_settings
from .set_clipboard import set_clipboard_item
from .store import ClipboardHistory
from .ui_panel import ClipPanel
from .ui_settings import SettingsDialog
from .win_listener import ClipboardListener, HotkeyEvent


HOTKEY_TOGGLE_PANEL = 1
HOTKEY_TOGGLE_PAUSE = 2


class _Bridge(QObject):
    event = Signal(object)


@dataclass(frozen=True, slots=True)
class _PersistResult:
    original: ClipboardItem
    stored: ClipboardItem | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _BlobResult:
    key: tuple
    item: ClipboardItem
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _AsyncResult:
    operation: str
    error: str | None = None


class ClipHistApp:
    def __init__(self) -> None:
        self.qt_app = QApplication(sys.argv)
        self.qt_app.setQuitOnLastWindowClosed(False)

        self._app_icon = self._default_icon()
        try:
            self.qt_app.setWindowIcon(self._app_icon)
        except Exception:
            log.debug("设置应用图标失败", exc_info=True)

        self.settings = load_settings()
        actual_autostart = is_autostart_enabled()
        if actual_autostart != self.settings.autostart_enabled:
            self.settings = replace(self.settings, autostart_enabled=actual_autostart)
            try:
                save_settings(self.settings)
            except Exception:
                log.debug("同步开机自启设置失败", exc_info=True)
        self.paused = False
        self.history = ClipboardHistory(max_items=self.settings.max_items)
        self._store: AsyncSQLiteHistoryStore | None = None
        self._persistence_enabled = False
        self.favorites = FavoritesStore()
        self.favorites.load()
        self._blob_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ClipHistBlob")
        self._blob_cache: OrderedDict[tuple, ClipboardItem] = OrderedDict()
        self._blob_cache_bytes = 0
        self._blob_cache_limit = 128 * 1024 * 1024
        self._pending_blob_requests: dict[tuple, list[tuple[str, bool | None]]] = {}
        self._previous_foreground_hwnd: int | None = None
        self._closing = False

        self.hotkey_show_hint: str | None = None
        self.hotkey_pause_hint: str | None = None
        self._hotkey_specs: dict[int, HotkeySpec] = {}

        self.tray = QSystemTrayIcon(self._app_icon, self.qt_app)
        self.tray.setToolTip("ClipHist")
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.setContextMenu(self._build_tray_menu())
        self.tray.show()

        self.panel = ClipPanel(
            on_activate=self._activate_item,
            on_clear=self._clear_history,
            on_open_settings=self._open_settings,
            get_favorites=self._get_favorites,
            toggle_favorite=self._toggle_favorite,
            remove_favorite=self._remove_favorite,
            reorder_favorites=self._reorder_favorites,
            edit_item=self._edit_item,
            delete_items=self._delete_history_items,
            load_blobs=self._load_blobs_for_ui,
        )
        self.panel.resize(self.settings.panel_width, self.settings.panel_height)
        try:
            self.panel.setWindowIcon(self._app_icon)
        except Exception:
            log.debug("设置面板窗口图标失败", exc_info=True)

        self._bridge = _Bridge()
        self._bridge.event.connect(self._handle_event)

        self.listener = ClipboardListener(on_event=self._bridge.event.emit)
        self.listener.start()
        if not self.listener.wait_ready(2.0):
            raise RuntimeError("clipboard listener not ready")

        self._register_hotkeys_with_fallback()

        if self.settings.persist_enabled:
            self._enable_persistence(True)
        self._sync_ui_state()

    def _default_icon(self) -> QIcon:
        # 优先使用自定义图标
        icon_candidates = [
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "icon.ico"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icon.ico"),
        ]
        # PyInstaller 打包后的路径
        if getattr(sys, "_MEIPASS", None):
            icon_candidates.insert(0, os.path.join(sys._MEIPASS, "assets", "icon.ico"))
        for p in icon_candidates:
            if os.path.isfile(p):
                return QIcon(p)
        return self.qt_app.style().standardIcon(QStyle.SP_FileDialogDetailedView)

    def _build_tray_menu(self) -> QMenu:
        menu = QMenu()

        act_open = QAction("打开面板", menu)
        act_open.triggered.connect(self._open_panel)
        menu.addAction(act_open)

        act_settings = QAction("设置…", menu)
        act_settings.triggered.connect(self._open_settings)
        menu.addAction(act_settings)

        act_data_dir = QAction("打开数据目录", menu)
        act_data_dir.triggered.connect(self._open_data_dir)
        menu.addAction(act_data_dir)

        self._act_pause = QAction("暂停监听", menu)
        self._act_pause.setCheckable(True)
        self._act_pause.triggered.connect(lambda checked: self._set_paused(bool(checked)))
        menu.addAction(self._act_pause)

        self._act_persist = QAction("启用持久化", menu)
        self._act_persist.setCheckable(True)
        self._act_persist.triggered.connect(lambda checked: self._enable_persistence(bool(checked)))
        menu.addAction(self._act_persist)

        act_clear = QAction("清空历史", menu)
        act_clear.triggered.connect(self._clear_history)
        menu.addAction(act_clear)

        menu.addSeparator()

        act_exit = QAction("退出", menu)
        act_exit.triggered.connect(self.quit)
        menu.addAction(act_exit)

        return menu

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self._open_panel()

    def _open_panel(self) -> None:
        if not self.panel.isVisible():
            try:
                hwnd = int(win32gui.GetForegroundWindow() or 0)
                window_class = win32gui.GetClassName(hwnd) if hwnd else ""
                if window_class in {"Shell_TrayWnd", "NotifyIconOverflowWindow"}:
                    hwnd = 0
                self._previous_foreground_hwnd = hwnd or None
            except Exception:
                self._previous_foreground_hwnd = None
        self.panel.set_data(self.history.items(), self._get_favorites())
        self.panel.toggle_visible()

    def _open_settings(self) -> None:
        dlg = SettingsDialog(
            self.settings,
            self._apply_settings,
            backup_database=self._backup_database,
            database_summary=self._database_summary,
            parent=self.panel,
        )
        dlg.exec()

    def _database_summary(self, path: str) -> str:
        target = os.path.abspath(path or default_db_path())
        if not os.path.exists(target):
            return "尚未创建"
        size = sum(
            os.path.getsize(candidate)
            for candidate in (target, target + "-wal", target + "-shm")
            if os.path.exists(candidate)
        )
        count: int | None = None
        try:
            if self._store is not None and os.path.abspath(self._store.path) == target:
                count = int(self._store.call("count"))
            else:
                uri = Path(target).resolve().as_uri() + "?mode=ro"
                connection = sqlite3.connect(uri, uri=True, timeout=0.2)
                try:
                    row = connection.execute("SELECT COUNT(*) FROM clipboard_items").fetchone()
                    count = int(row[0]) if row else 0
                finally:
                    connection.close()
        except Exception:
            count = None
        size_text = f"{size / (1024 * 1024):.1f} MB" if size >= 1024 * 1024 else f"{max(1, size // 1024)} KB"
        return f"{count} 条 · {size_text}" if count is not None else size_text

    def _backup_database(self, target_path: str) -> tuple[bool, str | None]:
        source = os.path.abspath(self.settings.db_path or default_db_path())
        target = os.path.abspath(target_path)
        if source == target:
            return False, "备份文件不能与当前数据库相同"
        if not os.path.exists(source):
            return False, "当前数据库尚未创建"
        try:
            if self._store is not None and os.path.abspath(self._store.path) == source:
                self._store.call("backup_to", target)
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(source, target)
            return True, f"已备份到：{target}"
        except Exception as exc:
            log.exception("备份数据库失败")
            return False, str(exc)

    def _open_data_dir(self) -> None:
        db_path = self.settings.db_path or default_db_path()
        folder = os.path.dirname(os.path.abspath(db_path))
        try:
            os.makedirs(folder, exist_ok=True)
            os.startfile(folder)  # type: ignore[attr-defined]
        except Exception as exc:
            QMessageBox.warning(self.panel if self.panel.isVisible() else None, "打开目录", str(exc))

    def _handle_event(self, evt: object) -> None:
        if isinstance(evt, _PersistResult):
            if evt.stored is not None:
                replaced = self.history.replace_first(evt.original, evt.stored)
                if not replaced and self._store is not None and evt.stored.db_id is not None:
                    try:
                        self._store.submit("delete_by_ids", [evt.stored.db_id])
                    except Exception as exc:
                        self._notify_error("清理已删除记录失败", str(exc))
            if evt.error:
                self._notify_error("持久化写入失败", evt.error)
            if self.panel.isVisible():
                self.panel.set_data(self.history.items(), self._get_favorites())
            return

        if isinstance(evt, _BlobResult):
            self._handle_blob_result(evt)
            return

        if isinstance(evt, _AsyncResult):
            if evt.error:
                self._notify_error(evt.operation, evt.error)
            elif "收藏" in evt.operation:
                self.panel.set_data(self.history.items(), self._get_favorites())
            return

        if isinstance(evt, HotkeyEvent):
            if evt.hotkey_id == HOTKEY_TOGGLE_PANEL:
                self._open_panel()
            elif evt.hotkey_id == HOTKEY_TOGGLE_PAUSE:
                self._set_paused(not self.paused)
            return

        if isinstance(evt, OversizedImageNotice):
            if not self.paused:
                self._notify_oversized_image(evt)
            return

        if isinstance(evt, ClipboardItem):
            if self.paused:
                return
            added = self.history.add(evt)
            if not added:
                return
            if self._persistence_enabled and self._store is not None:
                try:
                    future = self._store.submit("insert_and_trim", evt, self.history.max_items)

                    def _persist_done(done: Future, original: ClipboardItem = evt) -> None:
                        try:
                            row_id = int(done.result())
                            stored = original.with_db_id(row_id).slim(fingerprint=item_fingerprint(original))
                            self._bridge.event.emit(_PersistResult(original=original, stored=stored))
                        except Exception as exc:
                            self._bridge.event.emit(_PersistResult(original=original, error=str(exc)))

                    future.add_done_callback(_persist_done)
                except Exception as exc:
                    self._notify_error("持久化写入失败", str(exc))
            if self.panel.isVisible():
                self.panel.set_data(self.history.items(), self._get_favorites())

    def _activate_item(self, item: ClipboardItem, paste_override: bool | None = None) -> None:
        if item.needs_blob_load:
            loaded = self._cached_blob(item)
            if loaded is None:
                self._request_blob_load(item, "activate", paste_override)
                return
            item = loaded
        self._finish_activation(item, paste_override)

    def _finish_activation(self, item: ClipboardItem, paste_override: bool | None) -> None:
        try:
            set_clipboard_item(item, hwnd=self.listener.hwnd)
        except Exception as exc:
            self._notify_error("写入剪贴板失败", str(exc))
            return
        should_paste = self.settings.auto_paste if paste_override is None else paste_override
        if should_paste:
            QTimer.singleShot(80, self._paste_to_previous_window)

    def _paste_to_previous_window(self) -> None:
        hwnd = self._previous_foreground_hwnd
        if not hwnd:
            return
        try:
            if not win32gui.IsWindow(hwnd):
                return
            win32gui.SetForegroundWindow(hwnd)
            QTimer.singleShot(60, lambda target=hwnd: self._send_paste_keys(target))
        except Exception as exc:
            self._notify_error("自动粘贴失败", f"内容已复制，可手动按 Ctrl+V。{exc}")

    def _send_paste_keys(self, hwnd: int) -> None:
        control_down = False
        v_down = False
        try:
            if not win32gui.IsWindow(hwnd) or win32gui.GetForegroundWindow() != hwnd:
                raise RuntimeError("原窗口未能获得焦点")
            win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
            control_down = True
            win32api.keybd_event(ord("V"), 0, 0, 0)
            v_down = True
            win32api.keybd_event(ord("V"), 0, win32con.KEYEVENTF_KEYUP, 0)
            v_down = False
            win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
            control_down = False
        except Exception as exc:
            self._notify_error("自动粘贴失败", f"内容已复制，可手动按 Ctrl+V。{exc}")
        finally:
            if v_down:
                try:
                    win32api.keybd_event(ord("V"), 0, win32con.KEYEVENTF_KEYUP, 0)
                except Exception:
                    pass
            if control_down:
                try:
                    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
                except Exception:
                    pass

    @staticmethod
    def _blob_key(item: ClipboardItem) -> tuple | None:
        if item.db_id is not None:
            return ("db", item.db_id)
        if item._fingerprint:
            return ("fav", item._fingerprint)
        return None

    def _cached_blob(self, item: ClipboardItem) -> ClipboardItem | None:
        key = self._blob_key(item)
        if key is None:
            return None
        loaded = self._blob_cache.get(key)
        if loaded is not None:
            self._blob_cache.move_to_end(key)
        return loaded

    def _cache_blob(self, key: tuple, item: ClipboardItem) -> None:
        previous = self._blob_cache.pop(key, None)
        if previous is not None:
            self._blob_cache_bytes -= len(previous.raw_bytes or b"") + len(previous.image_bytes or b"")
        self._blob_cache[key] = item
        self._blob_cache_bytes += len(item.raw_bytes or b"") + len(item.image_bytes or b"")
        while self._blob_cache_bytes > self._blob_cache_limit and len(self._blob_cache) > 1:
            _, evicted = self._blob_cache.popitem(last=False)
            self._blob_cache_bytes -= len(evicted.raw_bytes or b"") + len(evicted.image_bytes or b"")

    def _request_blob_load(self, item: ClipboardItem, purpose: str, paste_override: bool | None = None) -> None:
        key = self._blob_key(item)
        if key is None:
            if purpose == "activate":
                self._finish_activation(item, paste_override)
            return
        pending = self._pending_blob_requests.setdefault(key, [])
        pending.append((purpose, paste_override))
        if len(pending) > 1:
            return
        try:
            if item.db_id is not None and self._store is not None:
                future = self._store.submit("load_blobs", item.db_id)

                def _db_done(done: Future) -> None:
                    try:
                        raw, image = done.result()
                        self._bridge.event.emit(_BlobResult(key, item.with_blobs(raw, image)))
                    except Exception as exc:
                        self._bridge.event.emit(_BlobResult(key, item, str(exc)))

                future.add_done_callback(_db_done)
                return
            if self.favorites.contains(item):
                future = self._blob_executor.submit(self.favorites.load_blobs, item)

                def _favorite_done(done: Future) -> None:
                    try:
                        self._bridge.event.emit(_BlobResult(key, done.result()))
                    except Exception as exc:
                        self._bridge.event.emit(_BlobResult(key, item, str(exc)))

                future.add_done_callback(_favorite_done)
                return
            self._bridge.event.emit(_BlobResult(key, item, "未找到二进制负载"))
        except Exception as exc:
            self._bridge.event.emit(_BlobResult(key, item, str(exc)))

    def _handle_blob_result(self, result: _BlobResult) -> None:
        requests = self._pending_blob_requests.pop(result.key, [])
        if not result.error:
            self._cache_blob(result.key, result.item)
        for purpose, paste_override in requests:
            if result.error:
                if purpose == "activate":
                    self._notify_error("加载剪贴板内容失败", result.error)
                continue
            if purpose == "activate":
                self._finish_activation(result.item, paste_override)
            else:
                self.panel.notify_blob_loaded()

    def _ensure_blobs(self, item: ClipboardItem) -> ClipboardItem:
        """Synchronous fallback for actions that must persist the full item."""
        if not item.needs_blob_load:
            return item
        cached = self._cached_blob(item)
        if cached is not None:
            return cached
        try:
            if item.db_id is not None and self._store is not None:
                raw, img = self._store.call("load_blobs", item.db_id)
                loaded = item.with_blobs(raw, img)
            else:
                loaded = self.favorites.load_blobs(item)
            key = self._blob_key(item)
            if key is not None:
                self._cache_blob(key, loaded)
            return loaded
        except Exception as exc:
            self._notify_error("加载剪贴板内容失败", str(exc))
            return item

    def _load_blobs_for_ui(self, item: ClipboardItem) -> ClipboardItem:
        """Return cached data immediately and schedule misses without blocking paint."""
        if not item.needs_blob_load:
            return item
        cached = self._cached_blob(item)
        if cached is not None:
            return cached
        self._request_blob_load(item, "ui")
        return item

    def _notify_error(self, title: str, detail: str) -> None:
        log.error("%s: %s", title, detail)
        try:
            self.tray.showMessage("ClipHist - " + title, detail, QSystemTrayIcon.Warning, 5000)
        except Exception:
            pass

    def _set_paused(self, paused: bool) -> None:
        if self.paused == paused:
            return
        self.paused = paused
        self._sync_ui_state()

    def _notify_oversized_image(self, notice: OversizedImageNotice) -> None:
        size_mb = notice.size / (1024 * 1024)
        limit_mb = notice.limit / (1024 * 1024)
        try:
            self.tray.showMessage(
                "ClipHist",
                f"复制的图片约 {size_mb:.1f} MB，超过 {limit_mb:.0f} MB 上限，未加入历史。",
                QSystemTrayIcon.Information,
                4000,
            )
        except Exception:
            log.debug("显示超大图片提示异常", exc_info=True)

    def _clear_history(self) -> None:
        if not self._confirm_clear():
            return
        self.history.clear()
        self._blob_cache.clear()
        self._blob_cache_bytes = 0
        if self._persistence_enabled and self._store is not None:
            try:
                future = self._store.submit("clear")
                future.add_done_callback(lambda done: self._emit_async_result(done, "清空持久化历史失败"))
            except Exception as exc:
                self._notify_error("清空持久化历史失败", str(exc))
        if self.panel.isVisible():
            self.panel.set_data(self.history.items(), self._get_favorites())

    def _delete_history_items(self, items: list[ClipboardItem]) -> tuple[bool, str | None]:
        removed = self.history.remove_many(items)
        if removed <= 0:
            return False, "未找到要删除的历史记录"
        ids = [item.db_id for item in items if item.db_id is not None]
        for item in items:
            key = self._blob_key(item)
            if key is not None:
                cached = self._blob_cache.pop(key, None)
                if cached is not None:
                    self._blob_cache_bytes -= len(cached.raw_bytes or b"") + len(cached.image_bytes or b"")
        if ids and self._persistence_enabled and self._store is not None:
            try:
                future = self._store.submit("delete_by_ids", ids)
                future.add_done_callback(lambda done: self._emit_async_result(done, "删除持久化历史失败"))
            except Exception as exc:
                self._notify_error("删除持久化历史失败", str(exc))
        return True, None

    def _emit_async_result(self, future: Future, operation: str) -> None:
        try:
            future.result()
            self._bridge.event.emit(_AsyncResult(operation))
        except Exception as exc:
            self._bridge.event.emit(_AsyncResult(operation, str(exc)))

    def _confirm_clear(self) -> bool:
        parent = self.panel if self.panel.isVisible() else None
        reply = QMessageBox.question(
            parent,
            "清空历史",
            "确定要清空全部历史记录吗？收藏的内容不受影响。此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    def _get_favorites(self) -> list[tuple[str, ClipboardItem]]:
        return [(e.fav_id, e.item) for e in self.favorites.entries]

    def _toggle_favorite(self, item: ClipboardItem) -> tuple[bool, str | None]:
        # Load blobs so that favorites are stored with full data
        item = self._ensure_blobs(item)
        if item.needs_blob_load:
            return False, "收藏内容加载失败，请稍后重试"
        self.favorites.toggle(item)
        self._save_favorites_async("保存收藏失败")
        return True, None

    def _remove_favorite(self, fav_id: str) -> tuple[bool, str | None]:
        if not self.favorites.remove_by_id(fav_id):
            return False, "未找到要删除的收藏"
        self._save_favorites_async("保存收藏失败")
        return True, None

    def _reorder_favorites(self, fav_ids_in_order: list[str]) -> tuple[bool, str | None]:
        self.favorites.set_order(fav_ids_in_order)
        self._save_favorites_async("保存收藏排序失败")
        return True, None

    def _save_favorites_async(self, operation: str) -> None:
        try:
            future = self._blob_executor.submit(self.favorites.save)
            future.add_done_callback(lambda done: self._emit_async_result(done, operation))
        except Exception as exc:
            self._notify_error(operation, str(exc))

    def _edit_item(self, old_item: ClipboardItem, new_item: ClipboardItem) -> tuple[bool, str | None]:
        replaced = self.history.replace_first(old_item, new_item)
        if not replaced:
            return False, "未找到要编辑的历史记录"

        changed_fav = self.favorites.replace_item(old_item, new_item)
        if changed_fav:
            self._save_favorites_async("保存编辑后的收藏失败")

        if self._persistence_enabled and self._store is not None and old_item.db_id is not None:
            try:
                future = self._store.submit(
                    "update_text", old_item.db_id, new_item.text, new_item._fingerprint
                )
                future.add_done_callback(lambda done: self._emit_async_result(done, "同步编辑后的持久化历史失败"))
            except Exception as exc:
                self._notify_error("同步编辑后的持久化历史失败", str(exc))
        return True, None

    def _switch_db_path(self, new_db_path: str | None) -> tuple[bool, str | None]:
        """关闭当前持久化存储，将历史库迁移到新位置并重新打开。"""
        if self._store is None:
            return True, None
        current_path = os.path.abspath(self._store.path)
        target_path = os.path.abspath(new_db_path or default_db_path())
        if current_path == target_path:
            return True, None
        try:
            self._store.close()
        except Exception:
            log.debug("切换数据库前关闭存储异常", exc_info=True)
        self._store = None
        moved_files: list[tuple[str, str]] = []
        candidate: AsyncSQLiteHistoryStore | None = None
        try:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            # 目标不存在时迁移现有数据库（含 WAL/SHM 辅助文件）。
            if not os.path.exists(target_path) and os.path.exists(current_path):
                for suffix in ("", "-wal", "-shm"):
                    src = current_path + suffix
                    dst = target_path + suffix
                    if os.path.exists(src):
                        shutil.move(src, dst)
                        moved_files.append((src, dst))
            candidate = AsyncSQLiteHistoryStore(target_path)
            loaded = candidate.call("load_recent_slim", self.history.max_items)
            self._store = candidate
            self.history.reset(loaded)
        except Exception:
            log.exception("切换数据库位置失败")
            if candidate is not None:
                try:
                    candidate.close()
                except Exception:
                    pass
            for src, dst in reversed(moved_files):
                try:
                    if os.path.exists(dst) and not os.path.exists(src):
                        shutil.move(dst, src)
                except Exception:
                    log.exception("回滚数据库迁移文件失败: %s", dst)
            # 尝试回退到原位置，避免丢失持久化能力。
            try:
                self._store = AsyncSQLiteHistoryStore(current_path)
                self._store.call("count")
            except Exception:
                if self._store is not None:
                    try:
                        self._store.close()
                    except Exception:
                        pass
                self._store = None
                self._persistence_enabled = False
            return False, "数据库位置切换失败，请检查目标路径是否可写"
        return True, None

    def _enable_persistence(self, enabled: bool) -> None:
        if enabled and self._store is None:
            db_path = os.path.abspath(self.settings.db_path or default_db_path())
            candidate: AsyncSQLiteHistoryStore | None = None
            try:
                os.makedirs(os.path.dirname(db_path), exist_ok=True)
                candidate = AsyncSQLiteHistoryStore(db_path)
                loaded = candidate.call("load_recent_slim", self.history.max_items)
                self._store = candidate
                existing = self.history.items()
                known = {item._fingerprint for item in loaded if item._fingerprint}
                merged = loaded + [item for item in existing if not item._fingerprint or item._fingerprint not in known]
                self.history.reset(merged)
            except Exception:
                log.exception("启用持久化失败")
                if candidate is not None:
                    try:
                        candidate.close()
                    except Exception:
                        pass
                self._store = None
                enabled = False

        # When disabling, keep the already-open coordinator for this session so
        # slim history rows can still restore their blobs. New mutations are no
        # longer written and the coordinator is closed normally on exit.
        self._persistence_enabled = bool(enabled and self._store is not None)

        self.settings = replace(self.settings, persist_enabled=self._persistence_enabled)
        try:
            save_settings(self.settings)
        except Exception:
            log.exception("保存设置失败")

        if self.panel.isVisible():
            self.panel.set_data(self.history.items(), self._get_favorites())
        self._sync_ui_state()

    def _sync_ui_state(self) -> None:
        try:
            self._act_pause.setChecked(self.paused)
        except Exception:
            log.debug("更新托盘菜单异常", exc_info=True)
        try:
            self._act_persist.setChecked(self._persistence_enabled)
        except Exception:
            log.debug("更新托盘菜单异常", exc_info=True)

        self.panel.set_paused(self.paused)

        show_hint = self.hotkey_show_hint or "(无)"
        pause_hint = self.hotkey_pause_hint or "(无)"
        hint = f"面板: {show_hint}\n暂停: {pause_hint}"
        persist = "持久化: 开" if self._persistence_enabled else "持久化: 关"
        tip = "ClipHist\n" + hint + ("\n状态: 暂停" if self.paused else "\n状态: 监听中") + f"\n{persist}"
        try:
            self.tray.setToolTip(tip)
        except Exception:
            log.debug("更新托盘提示异常", exc_info=True)

    def _register_hotkeys_with_fallback(self) -> None:
        ok, _ = self._apply_hotkeys(self.settings.hotkey_show_panel, self.settings.hotkey_toggle_pause, save=False)
        if ok:
            return
        ok, _ = self._apply_hotkeys(self.settings.hotkey_show_panel, "", save=False)
        if ok:
            return

        candidates = [
            ("Alt+C", "Alt+P"),
            ("Alt+V", "Alt+P"),
            ("Ctrl+Shift+V", "Ctrl+Shift+P"),
            ("Ctrl+Alt+V", "Ctrl+Alt+P"),
            ("Alt+Shift+V", "Alt+Shift+P"),
            ("Ctrl+Shift+F8", "Ctrl+Shift+F9"),
            ("Ctrl+Alt+F8", "Ctrl+Alt+F9"),
            ("Win+Alt+V", "Win+Alt+P"),
        ]
        for show_seq, pause_seq in candidates:
            ok, _ = self._apply_hotkeys(show_seq, pause_seq, save=False)
            if ok:
                self._save_hotkey_settings(show_seq, pause_seq)
                return
            ok, _ = self._apply_hotkeys(show_seq, "", save=False)
            if ok:
                self._save_hotkey_settings(show_seq, "")
                return

        try:
            self.tray.showMessage(
                "ClipHist",
                "全局热键注册失败（可能被其他程序或另一个 ClipHist 占用），可通过托盘打开面板。",
                QSystemTrayIcon.Warning,
                5000,
            )
        except Exception:
            log.debug("显示热键警告异常", exc_info=True)

    def _apply_hotkeys(self, show_seq: str, pause_seq: str, save: bool = True) -> tuple[bool, str | None]:
        show_seq = (show_seq or "").strip()
        pause_seq = (pause_seq or "").strip()

        show_spec = parse_hotkey_sequence(show_seq) if show_seq else None
        pause_spec = parse_hotkey_sequence(pause_seq) if pause_seq else None
        prev_show_spec = self._hotkey_specs.get(HOTKEY_TOGGLE_PANEL)
        prev_pause_spec = self._hotkey_specs.get(HOTKEY_TOGGLE_PAUSE)

        if show_seq and show_spec is None:
            return False, "面板热键格式不支持"
        if pause_seq and pause_spec is None:
            return False, "暂停热键格式不支持"
        if show_spec and pause_spec and (show_spec.modifiers, show_spec.vk) == (pause_spec.modifiers, pause_spec.vk):
            return False, "两个功能不能设置为相同热键"
        if show_spec == prev_show_spec and pause_spec == prev_pause_spec:
            self.hotkey_show_hint = show_spec.display if show_spec is not None else None
            self.hotkey_pause_hint = pause_spec.display if pause_spec is not None else None
            if save:
                self._save_hotkey_settings(show_seq, pause_seq)
            self._sync_ui_state()
            return True, None

        self.listener.unregister_hotkey(HOTKEY_TOGGLE_PANEL)
        self.listener.unregister_hotkey(HOTKEY_TOGGLE_PAUSE)
        self._hotkey_specs.pop(HOTKEY_TOGGLE_PANEL, None)
        self._hotkey_specs.pop(HOTKEY_TOGGLE_PAUSE, None)
        self.hotkey_show_hint = None
        self.hotkey_pause_hint = None

        warn: str | None = None
        if show_spec is not None:
            ok, err = self.listener.register_hotkey_with_error(HOTKEY_TOGGLE_PANEL, show_spec.modifiers, show_spec.vk)
            if not ok:
                self._restore_hotkeys(prev_show_spec, prev_pause_spec)
                return False, self._format_hotkey_error("面板", err)
            self._hotkey_specs[HOTKEY_TOGGLE_PANEL] = show_spec
            self.hotkey_show_hint = show_spec.display

        if pause_spec is not None:
            ok, err = self.listener.register_hotkey_with_error(HOTKEY_TOGGLE_PAUSE, pause_spec.modifiers, pause_spec.vk)
            if not ok:
                warn = self._format_hotkey_error("暂停", err) + "，已禁用暂停热键"
                pause_spec = None
                pause_seq = ""
        if pause_spec is not None:
            self._hotkey_specs[HOTKEY_TOGGLE_PAUSE] = pause_spec
            self.hotkey_pause_hint = pause_spec.display
        else:
            self.hotkey_pause_hint = None

        if save:
            self._save_hotkey_settings(show_seq, pause_seq)

        self._sync_ui_state()
        return True, warn

    def _apply_settings(
        self,
        show_seq: str,
        pause_seq: str,
        max_items: int,
        persist_enabled: bool,
        auto_paste: bool,
        autostart_enabled: bool,
        panel_width: int = 640,
        panel_height: int = 620,
        db_path: str = "",
    ) -> tuple[bool, str | None]:
        max_items = max(1, int(max_items))
        panel_width = max(400, min(int(panel_width), 1600))
        panel_height = max(350, min(int(panel_height), 1200))
        new_db_path = (db_path or "").strip() or None

        ok, warn = self._apply_hotkeys(show_seq, pause_seq, save=False)
        if not ok:
            return False, warn

        if autostart_enabled != self.settings.autostart_enabled:
            try:
                set_autostart_enabled(autostart_enabled)
            except Exception:
                log.exception("更新开机自启设置失败")
                return False, "热键已更新，但开机自启设置失败"

        db_changed = new_db_path != self.settings.db_path
        if db_changed and self._store is not None:
            if self._persistence_enabled:
                ok_db, err_db = self._switch_db_path(new_db_path)
                if not ok_db:
                    return False, err_db
            else:
                try:
                    self._store.close()
                except Exception:
                    log.debug("关闭旧数据库协调器失败", exc_info=True)
                self._store = None

        previous_max_items = self.history.max_items
        if max_items != previous_max_items:
            self.history.set_max_items(max_items)
            if self._persistence_enabled and self._store is not None:
                try:
                    self._store.call("trim_to_limit", max_items)
                    loaded = self._store.call("load_recent_slim", max_items)
                    self.history.reset(loaded)
                except Exception:
                    log.exception("更新历史条数后重新加载持久化历史失败")
                    return False, "热键已更新，但重新加载历史失败"

        # Make the selected path visible to _enable_persistence before opening
        # a new coordinator, then persist the complete settings atomically.
        self.settings = replace(self.settings, db_path=new_db_path)
        if persist_enabled != self._persistence_enabled:
            self._enable_persistence(persist_enabled)
            if persist_enabled and not self._persistence_enabled:
                return False, "无法启用持久化，请检查数据库路径"

        self.settings = replace(
            self.settings,
            max_items=max_items,
            persist_enabled=persist_enabled,
            auto_paste=bool(auto_paste),
            autostart_enabled=autostart_enabled,
            hotkey_show_panel=(show_seq or "").strip(),
            hotkey_toggle_pause=(pause_seq or "").strip(),
            panel_width=panel_width,
            panel_height=panel_height,
            db_path=new_db_path,
        )
        try:
            save_settings(self.settings)
        except Exception:
            log.exception("保存设置失败")
            return False, "设置已应用，但保存到配置文件失败"

        self.panel.resize(panel_width, panel_height)

        if self.panel.isVisible():
            self.panel.set_data(self.history.items(), self._get_favorites())
        self._sync_ui_state()
        return True, warn

    def _save_hotkey_settings(self, show_seq: str, pause_seq: str) -> None:
        self.settings = replace(
            self.settings,
            hotkey_show_panel=show_seq,
            hotkey_toggle_pause=pause_seq,
        )
        try:
            save_settings(self.settings)
        except Exception:
            log.exception("保存热键设置失败")

    def _restore_hotkeys(self, show_spec: HotkeySpec | None, pause_spec: HotkeySpec | None) -> None:
        self.listener.unregister_hotkey(HOTKEY_TOGGLE_PANEL)
        self.listener.unregister_hotkey(HOTKEY_TOGGLE_PAUSE)
        self._hotkey_specs.pop(HOTKEY_TOGGLE_PANEL, None)
        self._hotkey_specs.pop(HOTKEY_TOGGLE_PAUSE, None)
        self.hotkey_show_hint = None
        self.hotkey_pause_hint = None

        if show_spec is not None:
            ok, _ = self.listener.register_hotkey_with_error(HOTKEY_TOGGLE_PANEL, show_spec.modifiers, show_spec.vk)
            if ok:
                self._hotkey_specs[HOTKEY_TOGGLE_PANEL] = show_spec
                self.hotkey_show_hint = show_spec.display
        if pause_spec is not None:
            ok, _ = self.listener.register_hotkey_with_error(HOTKEY_TOGGLE_PAUSE, pause_spec.modifiers, pause_spec.vk)
            if ok:
                self._hotkey_specs[HOTKEY_TOGGLE_PAUSE] = pause_spec
                self.hotkey_pause_hint = pause_spec.display

    def _format_hotkey_error(self, name: str, err: int) -> str:
        if err == 1409:
            return f"{name}热键注册失败：已被其他程序占用（错误码 {err}）"
        if err == 1408:
            return f"{name}热键注册失败：窗口属于其他线程（错误码 {err}）"
        if err == 1400:
            return f"{name}热键注册失败：窗口句柄无效（错误码 {err}）"
        if err:
            return f"{name}热键注册失败（错误码 {err}）"
        return f"{name}热键注册失败"

    def run(self) -> int:
        try:
            return self.qt_app.exec()
        finally:
            self.quit()

    def quit(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self.listener.stop()
        except Exception:
            log.debug("停止监听器异常", exc_info=True)
        try:
            if self._store is not None:
                self._store.close()
        except Exception:
            log.debug("关闭持久化存储异常", exc_info=True)
        try:
            self._blob_executor.shutdown(wait=True, cancel_futures=False)
        except Exception:
            log.debug("关闭 blob 工作线程异常", exc_info=True)
        try:
            self.tray.hide()
        except Exception:
            log.debug("隐藏托盘异常", exc_info=True)
        try:
            self.qt_app.quit()
        except Exception:
            log.debug("退出应用异常", exc_info=True)
