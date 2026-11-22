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
from tempfile import TemporaryDirectory
from uuid import uuid4

import win32api
import win32con
import win32gui

log = logging.getLogger(__name__)

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QStyle, QSystemTrayIcon

from .autostart import is_autostart_enabled, set_autostart_enabled
from .capture import OversizedClipboardNotice
from .clipboard_util import get_clipboard_sequence_number
from .favorites import FavoritesStore
from .hotkeys import HotkeySpec, parse_hotkey_sequence
from .models import ClipboardItem
from .persistence import AsyncSQLiteHistoryStore
from .settings import AppSettings, default_db_path, load_settings, save_settings
from .set_clipboard import set_clipboard_item
from .store import ClipboardHistory
from .themes import apply_application_theme, normalize_theme
from .ui_panel import ClipPanel
from .ui_settings import SettingsDialog
from .win_listener import ClipboardListener, HotkeyEvent


HOTKEY_TOGGLE_PANEL = 1
HOTKEY_TOGGLE_PAUSE = 2
_HOTKEY_FALLBACKS = (
    ("Alt+C", "Alt+P"),
    ("Alt+V", "Alt+P"),
    ("Ctrl+Shift+V", "Ctrl+Shift+P"),
    ("Ctrl+Alt+V", "Ctrl+Alt+P"),
    ("Alt+Shift+V", "Alt+Shift+P"),
    ("Ctrl+Shift+F8", "Ctrl+Shift+F9"),
    ("Ctrl+Alt+F8", "Ctrl+Alt+F9"),
    ("Meta+Alt+V", "Meta+Alt+P"),
)


class _Bridge(QObject):
    event = Signal(object)


@dataclass(slots=True)
class _PersistResult:
    original: ClipboardItem
    insert: Future[int]
    edited: ClipboardItem | None = None


@dataclass(frozen=True, slots=True)
class _BlobResult:
    key: tuple
    item: ClipboardItem
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _AsyncResult:
    operation: str
    error: str | None = None
    result: object | None = None


@dataclass(frozen=True, slots=True, eq=False)
class _Activation:
    should_paste: bool
    target: int | None


@dataclass(frozen=True, slots=True)
class _ClipboardWriteResult:
    activation: _Activation
    sequence: int | None = None
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
        self._database_migration_sources: list[str] = []
        self._theme_mode = self.settings.theme
        self._settings_dialog: SettingsDialog | None = None
        self.qt_app.setStyle("Fusion")
        apply_application_theme(self.qt_app, self._theme_mode)
        actual_autostart = is_autostart_enabled()
        if actual_autostart != self.settings.autostart_enabled:
            self.settings = replace(self.settings, autostart_enabled=actual_autostart)
            try:
                self._save_current_settings()
            except Exception:
                log.debug("同步开机自启设置失败", exc_info=True)
        self.paused = False
        self.history = ClipboardHistory(max_items=self.settings.max_items)
        self._store: AsyncSQLiteHistoryStore | None = None
        self._pending_inserts: dict[object, _PersistResult] = {}
        self._persistence_enabled = False
        self.favorites = FavoritesStore()
        self.favorites.load()
        self._blob_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ClipHistBlob")
        self._clipboard_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ClipHistWrite")
        self._activation: _Activation | None = None
        self._clipboard_write_pending = False
        self._queued_activation: tuple[ClipboardItem, _Activation] | None = None
        self._blob_cache: OrderedDict[tuple, ClipboardItem] = OrderedDict()
        self._blob_cache_bytes = 0
        self._blob_cache_limit = 128 * 1024 * 1024
        self._pending_blob_requests: dict[tuple, dict[str, _Activation | None]] = {}
        self._failed_blob_keys: OrderedDict[tuple, None] = OrderedDict()
        self._blob_generation = 0
        self._data_version = 0
        self._panel_data_version = -1
        self._hotkeys_suspended = False
        self._compaction_active = False
        self._compaction_after_id = 0
        self._compaction_saved_bytes = 0
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
            remove_favorites=self._remove_favorites,
            reorder_favorites=self._reorder_favorites,
            edit_item=self._edit_item,
            delete_items=self._delete_history_items,
            load_blobs=self._load_blobs_for_ui,
            on_toggle_pause=lambda: self._set_paused(not self.paused),
            on_resize=self._on_panel_resized,
            blob_load_failed=lambda item: self._blob_key(item) in self._failed_blob_keys,
        )
        self.panel.set_theme(self._theme_mode)
        self.panel.set_auto_paste(self.settings.auto_paste)
        self.panel.apply_configured_size(self.settings.panel_width, self.settings.panel_height)
        self.qt_app.styleHints().colorSchemeChanged.connect(self._on_system_theme_changed)
        try:
            self.panel.setWindowIcon(self._app_icon)
        except Exception:
            log.debug("设置面板窗口图标失败", exc_info=True)

        self._bridge = _Bridge()
        self._bridge.event.connect(self._handle_event)
        self._refresh_timer = QTimer(self._bridge)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(40)
        self._refresh_timer.timeout.connect(self._refresh_panel)

        self.listener = ClipboardListener(on_event=self._bridge.event.emit)
        self.listener.start()
        if not self.listener.wait_ready(2.0):
            raise RuntimeError("clipboard listener not ready")

        self._register_hotkeys_with_fallback()

        if self.settings.persist_enabled:
            self._enable_persistence(True)
        self._sync_ui_state()
        QTimer.singleShot(150, self._prewarm_panel)

    def _prewarm_panel(self) -> None:
        if self._closing:
            return
        self._sync_panel_data()
        self.panel.prewarm()

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
        act_open.triggered.connect(self._open_panel_from_tray)
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

        self._act_compact = QAction("优化数据库图片", menu)
        self._act_compact.setToolTip("无损压缩历史图片并回收数据库空间")
        self._act_compact.triggered.connect(self._compact_database)
        menu.addAction(self._act_compact)

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
            self._open_panel_from_tray()

    def _mark_data_dirty(self) -> None:
        self._data_version = getattr(self, "_data_version", 0) + 1

    def _sync_panel_data(self, force: bool = False) -> None:
        data_version = getattr(self, "_data_version", 0)
        if not force and getattr(self, "_panel_data_version", -1) == data_version:
            return
        self.panel.set_data(self.history.items(), self._get_favorites())
        self._panel_data_version = data_version

    def _record_previous_foreground(self) -> None:
        try:
            hwnd = int(win32gui.GetForegroundWindow() or 0)
            panel_hwnd = int(self.panel.winId()) if self.panel.isVisible() else 0
            if hwnd and hwnd == panel_hwnd:
                return
            window_class = win32gui.GetClassName(hwnd) if hwnd else ""
            if window_class in {"Shell_TrayWnd", "NotifyIconOverflowWindow"}:
                hwnd = 0
            self._previous_foreground_hwnd = hwnd or None
        except Exception:
            self._previous_foreground_hwnd = None

    def _focus_panel_window(self) -> None:
        try:
            hwnd = int(self.panel.winId())
            if hwnd:
                win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def _open_panel_from_tray(self) -> None:
        if self.panel.isVisible():
            self.panel.hide()
            return
        self._record_previous_foreground()
        self._sync_panel_data()
        self.panel._show_near_cursor()
        self._focus_panel_window()

    def _open_panel(self) -> None:
        if self._settings_dialog is not None:
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        if self.panel.isVisible() and self.panel.isActiveWindow():
            self.panel.hide()
            return
        self._record_previous_foreground()
        self._sync_panel_data()
        self.panel._show_near_cursor()
        self._focus_panel_window()

    def _on_panel_resized(self, width: int, height: int) -> None:
        width = max(400, min(int(width), 1600))
        height = max(440, min(int(height), 1200))
        if width == self.settings.panel_width and height == self.settings.panel_height:
            return
        self.settings = replace(self.settings, panel_width=width, panel_height=height)
        try:
            self._save_current_settings()
        except Exception:
            log.debug("保存面板尺寸失败", exc_info=True)

    def _open_settings(self) -> None:
        if self._settings_dialog is not None:
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        saved_theme = self.settings.theme
        saved_show_spec = self._hotkey_specs.get(HOTKEY_TOGGLE_PANEL)
        saved_pause_spec = self._hotkey_specs.get(HOTKEY_TOGGLE_PAUSE)
        dlg = SettingsDialog(
            self.settings,
            self._apply_settings,
            backup_database=self._backup_database,
            database_summary=self._database_summary,
            parent=self.panel,
            preview_theme=self._apply_theme,
        )
        self.listener.unregister_hotkey(HOTKEY_TOGGLE_PANEL)
        self.listener.unregister_hotkey(HOTKEY_TOGGLE_PAUSE)
        self._hotkeys_suspended = True
        self._settings_dialog = dlg
        try:
            dlg.exec()
        finally:
            self._settings_dialog = None
            if self._hotkeys_suspended:
                self._hotkeys_suspended = False
                self._restore_hotkeys(saved_show_spec, saved_pause_spec)
                self._sync_ui_state()
            if dlg.result() != SettingsDialog.Accepted:
                self.settings = replace(self.settings, theme=saved_theme)
            self._apply_theme(self.settings.theme)
            dlg.deleteLater()

    def _apply_theme(self, mode: str) -> None:
        self._theme_mode = normalize_theme(mode)
        apply_application_theme(self.qt_app, self._theme_mode)
        self.panel.set_theme(self._theme_mode)
        if self._settings_dialog is not None:
            self._settings_dialog._apply_styles()

    def _on_system_theme_changed(self, *_args) -> None:
        if self._theme_mode == "system":
            self._apply_theme("system")

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
            # Always query with a short-timeout read-only connection so opening
            # Advanced settings never blocks the UI thread behind VACUUM/writes.
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
        if isinstance(evt, _ClipboardWriteResult):
            self._clipboard_write_pending = False
            if self._closing:
                return
            queued, self._queued_activation = self._queued_activation, None
            if queued is not None:
                self._finish_activation(*queued)
            activation = evt.activation
            if self._activation is not activation:
                return
            if evt.error:
                self.panel.set_status_hint("")
                self._notify_error("写入剪贴板失败", evt.error)
                return
            if self.panel.isVisible():
                self.panel.hide()
            if activation.should_paste:
                if activation.target:
                    QTimer.singleShot(60, lambda: self._paste_to_previous_window(evt))
                else:
                    try:
                        self.tray.showMessage(
                            "ClipHist",
                            "内容已复制，未检测到原窗口，请手动按 Ctrl+V。",
                            QSystemTrayIcon.Information,
                            3000,
                        )
                    except Exception:
                        pass
            return

        if isinstance(evt, _PersistResult):
            self._finish_insert(evt)
            self._schedule_panel_refresh()
            return

        if isinstance(evt, _BlobResult):
            self._handle_blob_result(evt)
            return

        if isinstance(evt, _AsyncResult):
            if evt.operation == "优化数据库图片完成":
                try:
                    self._finish_compaction_success(max(0, int(evt.result or 0)))
                except (TypeError, ValueError, OverflowError) as exc:
                    self._finish_compaction_error(str(exc))
                return
            if evt.operation == "优化数据库图片":
                if evt.error:
                    self._finish_compaction_error(evt.error)
                else:
                    self._handle_compaction_status(evt.result)
                return
            if evt.error:
                self._notify_error(evt.operation, evt.error)
            elif "收藏" in evt.operation:
                self._mark_data_dirty()
                if self.panel.isVisible():
                    self._sync_panel_data()
            return

        if isinstance(evt, HotkeyEvent):
            if evt.hotkey_id == HOTKEY_TOGGLE_PANEL:
                self._open_panel()
            elif evt.hotkey_id == HOTKEY_TOGGLE_PAUSE:
                self._set_paused(not self.paused)
            return

        if isinstance(evt, OversizedClipboardNotice):
            if not self.paused:
                self._notify_oversized_clipboard(evt)
            return

        if isinstance(evt, ClipboardItem):
            if self.paused:
                return
            added = self.history.add(evt)
            if not added:
                return
            self._mark_data_dirty()
            if self._persistence_enabled and self._store is not None:
                try:
                    future = self._store.submit("insert_and_trim", evt, self.history.max_items)
                    result = _PersistResult(evt, future)
                    self._pending_inserts[evt.record_key] = result
                    future.add_done_callback(lambda _done: self._bridge.event.emit(result))
                except Exception as exc:
                    self._notify_error("持久化写入失败", str(exc))
            self._schedule_panel_refresh()

    def _finish_insert(self, result: _PersistResult) -> None:
        key = result.original.record_key
        if self._pending_inserts.get(key) is not result:
            return
        del self._pending_inserts[key]
        try:
            row_id = result.insert.result()
        except Exception as exc:
            self._notify_error("持久化写入失败", str(exc))
            return
        stored = self.history.mark_persisted(result.original, row_id)
        if stored is None and self._store is not None:
            try:
                future = self._store.submit("delete_by_ids", [row_id])
                future.add_done_callback(lambda done: self._emit_async_result(done, "清理已删除记录失败"))
            except Exception as exc:
                self._notify_error("清理已删除记录失败", str(exc))
        elif stored is not None:
            self._mark_data_dirty()
            if result.edited is not None:
                self._update_persisted_text(row_id, result.edited)

    def _finish_pending_inserts(self) -> None:
        # Reconcile with the store that accepted these inserts before closing it or reloading history.
        for result in list(self._pending_inserts.values()):
            self._finish_insert(result)

    def _schedule_panel_refresh(self) -> None:
        if self.panel.isVisible() and not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _refresh_panel(self) -> None:
        if not self._closing and self.panel.isVisible():
            self._sync_panel_data()

    def _activate_item(self, item: ClipboardItem, paste_override: bool | None = None) -> bool:
        should_paste = self.settings.auto_paste if paste_override is None else paste_override
        activation = _Activation(should_paste, self._previous_foreground_hwnd)
        self._activation = activation
        self._queued_activation = None
        if item.needs_blob_load:
            loaded = self._cached_blob(item)
            if loaded is None:
                self.panel.set_status_hint("正在加载内容…")
                self._request_blob_load(item, "activate", activation)
                return False
            item = loaded
        self._finish_activation(item, activation)
        return False

    def _finish_activation(self, item: ClipboardItem, activation: _Activation) -> None:
        if self._closing or self._activation is not activation:
            return
        if self._clipboard_write_pending:
            self._queued_activation = (item, activation)
            return
        self.panel.set_status_hint("正在写入剪贴板…")
        self._clipboard_write_pending = True
        try:
            future = self._clipboard_executor.submit(
                set_clipboard_item, item, hwnd=self.listener.hwnd,
                track_sequence=bool(activation.should_paste and activation.target),
            )

            def _written(done: Future) -> None:
                try:
                    result = _ClipboardWriteResult(activation, sequence=done.result())
                except Exception as exc:
                    result = _ClipboardWriteResult(activation, error=str(exc))
                self._bridge.event.emit(result)

            future.add_done_callback(_written)
        except Exception as exc:
            self._clipboard_write_pending = False
            self.panel.set_status_hint("")
            self._notify_error("写入剪贴板失败", str(exc))

    def _paste_clipboard_matches(self, expected: int | None) -> bool:
        current = get_clipboard_sequence_number()
        if current is not None and current == expected:
            return True
        detail = "无法确认剪贴板内容。" if expected is None or current is None else "剪贴板内容已变化。"
        self._notify_error("自动粘贴已取消", detail + "请重新选择要粘贴的记录。")
        return False

    def _paste_to_previous_window(self, result: _ClipboardWriteResult) -> None:
        activation = result.activation
        hwnd = activation.target
        if self._closing or self._activation is not activation or not hwnd:
            return
        if not self._paste_clipboard_matches(result.sequence):
            return
        try:
            if not win32gui.IsWindow(hwnd):
                return
            win32gui.SetForegroundWindow(hwnd)
            QTimer.singleShot(60, lambda: self._send_paste_keys(result))
        except Exception as exc:
            self._notify_error("自动粘贴失败", f"内容已复制，可手动按 Ctrl+V。{exc}")

    def _send_paste_keys(self, result: _ClipboardWriteResult, attempt: int = 0) -> None:
        activation = result.activation
        hwnd = activation.target
        if self._closing or self._activation is not activation or not hwnd:
            return
        control_down = False
        v_down = False
        try:
            if not win32gui.IsWindow(hwnd) or win32gui.GetForegroundWindow() != hwnd:
                raise RuntimeError("原窗口未能获得焦点")
            modifiers = (win32con.VK_CONTROL, win32con.VK_MENU, win32con.VK_SHIFT, win32con.VK_LWIN, win32con.VK_RWIN)
            if any(win32api.GetAsyncKeyState(key) & 0x8000 for key in modifiers):
                if attempt < 5:
                    QTimer.singleShot(50, lambda: self._send_paste_keys(result, attempt + 1))
                else:
                    self._notify_error("自动粘贴已取消", "请松开 Ctrl、Alt、Shift 或 Win 后，重新选择记录。")
                return
            if not self._paste_clipboard_matches(result.sequence):
                return
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

    def _blob_key(self, item: ClipboardItem) -> tuple | None:
        if item.db_id is not None:
            return ("db", getattr(self, "_blob_generation", 0), item.record_key, item._fingerprint)
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

    def _invalidate_blob_state(
        self,
        advance_generation: bool = True,
        database_only: bool = False,
    ) -> None:
        """Drop loaded/pending blobs before the backing history changes."""
        failed = getattr(self, "_failed_blob_keys", None)
        if database_only:
            db_keys = [key for key in self._pending_blob_requests if key and key[0] == "db"]
            for key in db_keys:
                self._pending_blob_requests.pop(key, None)
            if failed is not None:
                for key in [k for k in failed if k and k[0] == "db"]:
                    failed.pop(key, None)
            self._blob_cache = OrderedDict(
                (key, item) for key, item in self._blob_cache.items() if not key or key[0] != "db"
            )
            self._blob_cache_bytes = sum(
                len(item.raw_bytes or b"") + len(item.image_bytes or b"")
                for item in self._blob_cache.values()
            )
        else:
            self._pending_blob_requests.clear()
            if failed is not None:
                failed.clear()
            self._blob_cache.clear()
            self._blob_cache_bytes = 0
        if advance_generation:
            self._blob_generation = getattr(self, "_blob_generation", 0) + 1

    def _request_blob_load(self, item: ClipboardItem, purpose: str, activation: _Activation | None = None) -> None:
        key = self._blob_key(item)
        if key is None:
            if activation is not None:
                self._finish_activation(item, activation)
            return
        failed = getattr(self, "_failed_blob_keys", None)
        if failed is not None and key in failed:
            if purpose == "ui":
                return
            failed.pop(key, None)
        pending = self._pending_blob_requests.setdefault(key, {})
        already_loading = bool(pending)
        pending[purpose] = activation
        if already_loading:
            return
        try:
            if item.db_id is not None and self._store is not None:
                if not self.history.contains_record(item):
                    self._bridge.event.emit(_BlobResult(key, item, "该记录已不在当前历史中，请重新选择。"))
                    return
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
        requests = self._pending_blob_requests.pop(result.key, {})
        if not requests or self._closing:
            return
        if not result.error and result.item.needs_blob_load:
            result = replace(result, error="未找到完整的剪贴板内容")
        failed = getattr(self, "_failed_blob_keys", None)
        if result.error:
            if failed is not None:
                failed[result.key] = None
                failed.move_to_end(result.key)
                while len(failed) > 256:
                    failed.popitem(last=False)
        else:
            if failed is not None:
                failed.pop(result.key, None)
            self._cache_blob(result.key, result.item)
        for purpose, activation in requests.items():
            if purpose == "activate" and (activation is None or self._activation is not activation):
                continue
            if result.error:
                if purpose == "activate":
                    self.panel.set_status_hint("")
                if purpose in ("activate", "favorite"):
                    self._notify_error("加载剪贴板内容失败", result.error)
                continue
            if activation is not None:
                self._finish_activation(result.item, activation)
            elif purpose == "favorite":
                self._toggle_favorite(result.item)
                panel = getattr(self, "panel", None)
                if panel is not None and panel.isVisible():
                    self._sync_panel_data()
        if "ui" in requests:
            self.panel.notify_blob_loaded()

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

    def _notify_oversized_clipboard(self, notice: OversizedClipboardNotice) -> None:
        size_mb = notice.size / (1024 * 1024)
        limit_mb = notice.limit / (1024 * 1024)
        format_name = "图片" if notice.item_type == "image" else notice.item_type.upper()
        try:
            self.tray.showMessage(
                "ClipHist",
                f"复制的 {format_name} 约 {size_mb:.1f} MB，超过 {limit_mb:.0f} MB 上限，未加入历史。",
                QSystemTrayIcon.Information,
                4000,
            )
        except Exception:
            log.debug("显示剪贴板大小上限提示失败", exc_info=True)

    def _clear_history(self) -> None:
        if not self._confirm_clear():
            return
        self.history.clear()
        self._mark_data_dirty()
        self._invalidate_blob_state()
        if self._persistence_enabled and self._store is not None:
            try:
                future = self._store.submit("clear")
                future.add_done_callback(lambda done: self._emit_async_result(done, "清空持久化历史失败"))
            except Exception as exc:
                self._notify_error("清空持久化历史失败", str(exc))
        if self.panel.isVisible():
            self._sync_panel_data()

    def _delete_history_items(self, items: list[ClipboardItem]) -> tuple[bool, str | None]:
        removed_items = self.history.remove_many_items(items)
        if not removed_items:
            return False, "未找到要删除的历史记录"
        self._mark_data_dirty()
        ids = [item.db_id for item in removed_items if item.db_id is not None]
        failed = getattr(self, "_failed_blob_keys", None)
        for item in removed_items:
            key = self._blob_key(item)
            if key is not None:
                self._pending_blob_requests.pop(key, None)
                if failed is not None:
                    failed.pop(key, None)
                cached = self._blob_cache.pop(key, None)
                if cached is not None:
                    self._blob_cache_bytes -= len(cached.raw_bytes or b"") + len(cached.image_bytes or b"")
        if ids and self._persistence_enabled and self._store is not None:
            try:
                future = self._store.submit("delete_by_ids", ids)
                future.add_done_callback(lambda done: self._emit_async_result(done, "删除持久化历史失败"))
            except Exception as exc:
                self._notify_error("删除持久化历史失败", str(exc))
        self._sync_panel_data()
        return True, None

    def _emit_async_result(self, future: Future, operation: str) -> None:
        try:
            future.result()
            self._bridge.event.emit(_AsyncResult(operation))
        except Exception as exc:
            self._bridge.event.emit(_AsyncResult(operation, str(exc)))

    def _compact_database(self) -> None:
        if self._compaction_active:
            return
        if self._store is None:
            self._notify_error("优化数据库图片", "请先启用持久化并创建数据库")
            return
        self._compaction_active = True
        self._compaction_after_id = 0
        self._compaction_saved_bytes = 0
        self._sync_ui_state()
        self._submit_compaction_batch()

    def _submit_compaction_batch(self) -> None:
        if not self._compaction_active or self._closing:
            return
        store = self._store
        if store is None:
            self._finish_compaction_error("数据库存储已关闭")
            return
        try:
            future = store.submit("compact_images", self._compaction_after_id, 8)

            def _compacted(done: Future, expected_store: AsyncSQLiteHistoryStore = store) -> None:
                try:
                    status = done.result()
                    if self._store is not expected_store:
                        raise RuntimeError("数据库存储已切换")
                    self._bridge.event.emit(_AsyncResult("优化数据库图片", result=status))
                except Exception as exc:
                    self._bridge.event.emit(_AsyncResult("优化数据库图片", str(exc)))

            future.add_done_callback(_compacted)
        except Exception as exc:
            self._finish_compaction_error(str(exc))

    def _handle_compaction_status(self, status: object) -> None:
        if not self._compaction_active:
            return
        if not isinstance(status, dict):
            self._finish_compaction_error("数据库优化返回了无效状态")
            return
        try:
            self._compaction_after_id = int(status["last_id"])
            self._compaction_saved_bytes += max(0, int(status["saved_bytes"]))
            done = bool(status["done"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            self._finish_compaction_error(f"数据库优化返回了无效状态：{exc}")
            return
        if not done:
            self._submit_compaction_batch()
            return

        store = self._store
        if store is None:
            self._finish_compaction_error("数据库存储已关闭")
            return
        try:
            future = store.submit("vacuum")

            def _vacuumed(done_future: Future, expected_store: AsyncSQLiteHistoryStore = store) -> None:
                try:
                    done_future.result()
                    if self._store is not expected_store:
                        raise RuntimeError("数据库存储已切换")
                    self._bridge.event.emit(
                        _AsyncResult("优化数据库图片完成", result=self._compaction_saved_bytes)
                    )
                except Exception as exc:
                    self._bridge.event.emit(_AsyncResult("优化数据库图片", str(exc)))

            future.add_done_callback(_vacuumed)
        except Exception as exc:
            self._finish_compaction_error(str(exc))

    def _finish_compaction_error(self, detail: str) -> None:
        if not self._compaction_active:
            return
        self._compaction_active = False
        self._sync_ui_state()
        self._notify_error("优化数据库图片失败", detail)

    def _finish_compaction_success(self, saved_bytes: int) -> None:
        if not self._compaction_active:
            return
        self._compaction_active = False
        self._sync_ui_state()
        if saved_bytes >= 1024 * 1024:
            saved_text = f"{saved_bytes / (1024 * 1024):.1f} MB"
        elif saved_bytes >= 1024:
            saved_text = f"{saved_bytes / 1024:.1f} KB"
        else:
            saved_text = f"{saved_bytes} bytes"
        try:
            self.tray.showMessage(
                "ClipHist - 优化数据库图片",
                f"无损优化完成，节省 {saved_text}。",
                QSystemTrayIcon.Information,
                5000,
            )
        except Exception:
            log.debug("显示数据库优化结果异常", exc_info=True)

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
        if item.needs_blob_load:
            cached = self._cached_blob(item)
            if cached is None:
                self._request_blob_load(item, "favorite")
                return True, None
            item = cached
        self.favorites.toggle(item)
        self._mark_data_dirty()
        self._save_favorites_async("保存收藏失败")
        return True, None

    def _remove_favorite(self, fav_id: str) -> tuple[bool, str | None]:
        return self._remove_favorites([fav_id])

    def _remove_favorites(self, fav_ids: list[str]) -> tuple[bool, str | None]:
        removed = self.favorites.remove_many_by_id(fav_ids)
        if removed <= 0:
            return False, "未找到要移出的收藏"
        self._mark_data_dirty()
        self._save_favorites_async("保存收藏失败")
        return True, None

    def _reorder_favorites(self, fav_ids_in_order: list[str]) -> tuple[bool, str | None]:
        self.favorites.set_order(fav_ids_in_order)
        self._mark_data_dirty()
        self._save_favorites_async("保存收藏排序失败")
        return True, None

    def _save_favorites_async(self, operation: str) -> None:
        try:
            future = self._blob_executor.submit(self.favorites.save)
            future.add_done_callback(lambda done: self._emit_async_result(done, operation))
        except Exception as exc:
            self._notify_error(operation, str(exc))

    def _edit_item(
        self, old_item: ClipboardItem, new_item: ClipboardItem, from_favorites: bool
    ) -> tuple[bool, str | None]:
        original = self.history.edit_item(old_item, new_item, from_favorites=from_favorites)
        changed_fav = (from_favorites or original is not None) and self.favorites.replace_item(old_item, new_item)
        if original is None and not changed_fav:
            return False, "未找到要编辑的记录"

        self._mark_data_dirty()
        if changed_fav:
            self._save_favorites_async("保存编辑后的收藏失败")

        if original is not None and self._persistence_enabled:
            if original.db_id is not None:
                self._update_persisted_text(original.db_id, new_item)
            elif pending := self._pending_inserts.get(original.record_key):
                pending.edited = new_item
        self._sync_panel_data()
        return True, None

    def _update_persisted_text(self, row_id: int, item: ClipboardItem) -> None:
        if self._store is None:
            return
        try:
            future = self._store.submit("update_text", row_id, item.text, item._fingerprint)
            future.add_done_callback(lambda done: self._emit_async_result(done, "同步编辑后的持久化历史失败"))
        except Exception as exc:
            self._notify_error("同步编辑后的持久化历史失败", str(exc))

    def _switch_db_path(self, new_db_path: str | None) -> tuple[bool, str | None]:
        """Open the requested store, keeping the old path until settings are saved."""
        current = self._store
        if current is None:
            return True, None
        current_path = os.path.abspath(current.path)
        target_path = os.path.abspath(new_db_path or default_db_path())
        if os.path.normcase(current_path) == os.path.normcase(target_path):
            return True, None
        self._finish_pending_inserts()
        candidate: AsyncSQLiteHistoryStore | None = None
        try:
            target_dir = os.path.dirname(target_path)
            os.makedirs(target_dir, exist_ok=True)
            copied = not os.path.exists(target_path)
            if copied:
                # Keep the original database usable if saving the new path fails.
                with TemporaryDirectory(prefix=".cliphist-migrate-", dir=target_dir) as backup_dir:
                    staged = os.path.join(backup_dir, "history.sqlite3")
                    current.call("backup_to", staged)
                    os.rename(staged, target_path)
            candidate = AsyncSQLiteHistoryStore(target_path)
            loaded = candidate.call("load_recent_slim", self.history.max_items)
        except Exception:
            log.exception("切换数据库位置失败")
            if candidate is not None:
                try:
                    candidate.close()
                except Exception:
                    pass
            return False, "数据库位置切换失败，请检查目标路径是否可写"
        self._invalidate_blob_state(database_only=True)
        self._store = candidate
        if copied:
            self._database_migration_sources.append(current_path)
        else:
            self._database_migration_sources.clear()
        self.history.reset(loaded)
        self._mark_data_dirty()
        try:
            current.close()
        except Exception:
            log.debug("关闭旧数据库协调器失败", exc_info=True)
        self._sync_panel_data()
        return True, None

    def _enable_persistence(self, enabled: bool, *, save: bool = True) -> None:
        if enabled and self._store is None:
            db_path = os.path.abspath(self.settings.db_path or default_db_path())
            candidate: AsyncSQLiteHistoryStore | None = None
            try:
                os.makedirs(os.path.dirname(db_path), exist_ok=True)
                candidate = AsyncSQLiteHistoryStore(db_path)
                loaded = candidate.call("load_recent_slim", self.history.max_items)
                self._invalidate_blob_state(database_only=True)
                self._store = candidate
                existing = [item for item in self.history.items() if item.db_id is None]
                known = {item._fingerprint for item in loaded if item._fingerprint}
                merged = loaded + [item for item in existing if not item._fingerprint or item._fingerprint not in known]
                self.history.reset(merged)
                self._mark_data_dirty()
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
        if save:
            try:
                self._save_current_settings()
            except Exception:
                log.exception("保存设置失败")

        if self.panel.isVisible():
            self._sync_panel_data()
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
        try:
            self._act_compact.setEnabled(not self._compaction_active)
        except Exception:
            log.debug("更新数据库优化菜单异常", exc_info=True)

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
        ok, warning = self._apply_hotkeys(self.settings.hotkey_show_panel, self.settings.hotkey_toggle_pause, save=False)
        if ok:
            if warning:
                self._notify_error("全局热键", warning)
            return
        ok, _ = self._apply_hotkeys(self.settings.hotkey_show_panel, "", save=False)
        if ok:
            return

        for show_seq, pause_seq in _HOTKEY_FALLBACKS:
            ok, warning = self._apply_hotkeys(show_seq, pause_seq, save=False)
            if ok:
                self._save_hotkey_settings(show_seq, pause_seq)
                if warning:
                    self._notify_error("全局热键", warning)
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
        if not getattr(self, "_hotkeys_suspended", False) and show_spec == prev_show_spec and pause_spec == prev_pause_spec:
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

        warnings: list[str] = []
        if show_spec is not None:
            ok, err = self._bind_hotkey(HOTKEY_TOGGLE_PANEL, show_spec)
            if not ok:
                if show_spec != parse_hotkey_sequence(self.settings.hotkey_show_panel):
                    if not self._hotkeys_suspended:
                        self._restore_hotkeys(prev_show_spec, prev_pause_spec)
                    return False, self._format_hotkey_error("面板", err)
                show_spec = self._bind_hotkey_fallback(HOTKEY_TOGGLE_PANEL, show_spec, reserved=pause_spec)
                warnings.append(self._hotkey_fallback_message("面板", err, show_spec))
        self.hotkey_show_hint = show_spec.display if show_spec is not None else None

        if pause_spec is not None:
            ok, err = self._bind_hotkey(HOTKEY_TOGGLE_PAUSE, pause_spec)
            if not ok:
                pause_spec = self._bind_hotkey_fallback(HOTKEY_TOGGLE_PAUSE, pause_spec)
                warnings.append(self._hotkey_fallback_message("暂停", err, pause_spec))
        self.hotkey_pause_hint = pause_spec.display if pause_spec is not None else None

        self._hotkeys_suspended = False
        if save:
            self._save_hotkey_settings(show_seq, pause_seq)

        self._sync_ui_state()
        return True, "\n".join(warnings) or None

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
        theme: str | None = None,
    ) -> tuple[bool, str | None]:
        max_items = max(1, int(max_items))
        panel_width = max(400, min(int(panel_width), 1600))
        panel_height = max(440, min(int(panel_height), 1200))
        new_db_path = (db_path or "").strip() or None
        db_changed = new_db_path != self.settings.db_path
        if db_changed and getattr(self, "_compaction_active", False):
            return False, "数据库优化进行中，暂不能切换数据库位置"

        ok, warn = self._apply_hotkeys(show_seq, pause_seq, save=False)
        if not ok:
            return False, warn

        if autostart_enabled != self.settings.autostart_enabled:
            try:
                set_autostart_enabled(autostart_enabled)
            except Exception:
                log.exception("更新开机自启设置失败")
                return False, "热键已更新，但开机自启设置失败"

        if db_changed and self._store is not None:
            if self._persistence_enabled:
                ok_db, err_db = self._switch_db_path(new_db_path)
                if not ok_db:
                    return False, err_db
            else:
                self._finish_pending_inserts()
                self._invalidate_blob_state(database_only=True)
                try:
                    self._store.close()
                except Exception:
                    log.debug("关闭旧数据库协调器失败", exc_info=True)
                self._store = None
                self._database_migration_sources.clear()

        previous_max_items = self.history.max_items
        if max_items != previous_max_items:
            self.history.set_max_items(max_items)
            self._mark_data_dirty()
            if self._persistence_enabled and self._store is not None:
                try:
                    self._finish_pending_inserts()
                    self._store.call("trim_to_limit", max_items)
                    loaded = self._store.call("load_recent_slim", max_items)
                    self.history.reset(loaded)
                except Exception:
                    log.exception("更新历史条数后重新加载持久化历史失败")
                    self._sync_panel_data()
                    return False, "热键已更新，但重新加载历史失败"

        # Make the selected path visible to _enable_persistence before opening
        # a new coordinator, then persist the complete settings atomically.
        self.settings = replace(self.settings, db_path=new_db_path)
        if persist_enabled != self._persistence_enabled:
            self._enable_persistence(persist_enabled, save=False)
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
            theme=normalize_theme(self.settings.theme if theme is None else theme),
        )
        self._apply_theme(self.settings.theme)
        self.panel.set_auto_paste(self.settings.auto_paste)
        self._sync_panel_data()
        try:
            self._save_current_settings()
        except Exception:
            log.exception("保存设置失败")
            return False, "设置已应用，但保存到配置文件失败"

        self.panel.apply_configured_size(panel_width, panel_height)

        self._sync_ui_state()
        return True, warn

    def _save_hotkey_settings(self, show_seq: str, pause_seq: str) -> None:
        self.settings = replace(
            self.settings,
            hotkey_show_panel=show_seq,
            hotkey_toggle_pause=pause_seq,
        )
        try:
            self._save_current_settings()
        except Exception:
            log.exception("保存热键设置失败")

    def _save_current_settings(self) -> None:
        save_settings(self.settings)
        if not self._database_migration_sources or self._store is None:
            return
        configured_path = os.path.normcase(os.path.abspath(self.settings.db_path or default_db_path()))
        if configured_path != os.path.normcase(os.path.abspath(self._store.path)):
            return
        sources, self._database_migration_sources = self._database_migration_sources, []
        for path in sources:
            if os.path.normcase(path) == configured_path:
                continue
            # The active database has the complete backup. Retire the previous
            # pathname so switching back can migrate the current history again.
            old = Path(path)
            backup = old.with_name(f"{old.stem}.migrated-{uuid4().hex[:8]}{old.suffix}")
            try:
                if Path(path + "-wal").exists() or Path(path + "-shm").exists():
                    raise OSError("原数据库仍有活动日志，原文件已保留。")
                old.rename(backup)
            except FileNotFoundError:
                continue
            except OSError as exc:
                self._notify_error("原数据库归档失败", str(exc))

    def _bind_hotkey(self, hotkey_id: int, spec: HotkeySpec) -> tuple[bool, int]:
        ok, error = self.listener.register_hotkey_with_error(hotkey_id, spec.modifiers, spec.vk)
        if ok:
            self._hotkey_specs[hotkey_id] = spec
        return ok, error

    def _bind_hotkey_fallback(
        self, hotkey_id: int, unavailable: HotkeySpec, *, reserved: HotkeySpec | None = None,
    ) -> HotkeySpec | None:
        tried = {*self._hotkey_specs.values(), unavailable}
        if reserved is not None:
            tried.add(reserved)
        column = 0 if hotkey_id == HOTKEY_TOGGLE_PANEL else 1
        for pair in _HOTKEY_FALLBACKS:
            spec = parse_hotkey_sequence(pair[column])
            if spec is None or spec in tried:
                continue
            tried.add(spec)
            ok, _ = self._bind_hotkey(hotkey_id, spec)
            if ok:
                return spec
        return None

    def _restore_hotkeys(self, show_spec: HotkeySpec | None, pause_spec: HotkeySpec | None) -> None:
        self.listener.unregister_hotkey(HOTKEY_TOGGLE_PANEL)
        self.listener.unregister_hotkey(HOTKEY_TOGGLE_PAUSE)
        self._hotkey_specs.clear()

        failures: list[tuple[int, str, HotkeySpec, int]] = []
        for hotkey_id, name, spec in (
            (HOTKEY_TOGGLE_PANEL, "面板", show_spec),
            (HOTKEY_TOGGLE_PAUSE, "暂停", pause_spec),
        ):
            if spec is not None:
                ok, err = self._bind_hotkey(hotkey_id, spec)
                if not ok:
                    failures.append((hotkey_id, name, spec, err))

        warnings = []
        for hotkey_id, name, spec, err in failures:
            fallback = self._bind_hotkey_fallback(hotkey_id, spec)
            warnings.append(self._hotkey_fallback_message(name, err, fallback))
        active_show = self._hotkey_specs.get(HOTKEY_TOGGLE_PANEL)
        active_pause = self._hotkey_specs.get(HOTKEY_TOGGLE_PAUSE)
        self.hotkey_show_hint = active_show.display if active_show is not None else None
        self.hotkey_pause_hint = active_pause.display if active_pause is not None else None
        if warnings:
            self._notify_error("恢复快捷键", "\n".join(warnings))

    def _hotkey_fallback_message(self, name: str, err: int, fallback: HotkeySpec | None) -> str:
        detail = self._format_hotkey_error(name, err)
        if fallback is not None:
            return f"{detail}，本次会话改用 {fallback.display}。"
        return detail + "，可通过托盘菜单操作。"

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
        self._refresh_timer.stop()
        self._clipboard_executor.shutdown(wait=True, cancel_futures=True)
        try:
            self.listener.stop()
        except Exception:
            log.debug("停止监听器异常", exc_info=True)
        try:
            if self._store is not None:
                self._finish_pending_inserts()
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
