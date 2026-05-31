from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import replace

import win32con

log = logging.getLogger(__name__)

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QStyle, QSystemTrayIcon

from .autostart import is_autostart_enabled, set_autostart_enabled
from .capture import OversizedImageNotice
from .favorites import FavoritesStore, item_fingerprint
from .hotkeys import HotkeySpec, parse_hotkey_sequence
from .models import ClipboardItem
from .persistence import SQLiteHistoryStore
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
        self._store: SQLiteHistoryStore | None = None
        self.favorites = FavoritesStore()
        self.favorites.load()

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
        self.panel.set_items(self.history.items())
        self.panel.set_favorites(self._get_favorites())
        self.panel.toggle_visible()

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self._apply_settings, parent=self.panel)
        dlg.exec()

    def _handle_event(self, evt: object) -> None:
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
            # Persist first, then slim the in-memory copy
            if self._store is not None:
                try:
                    fp = item_fingerprint(evt)
                    row_id = self._store.insert_and_trim(evt, self.history.max_items)
                    slim = evt.with_db_id(row_id).slim(fingerprint=fp)
                    self.history.replace_first(evt, slim)
                except Exception:
                    log.exception("持久化写入失败")
            if self.panel.isVisible():
                self.panel.set_items(self.history.items())
                self.panel.set_favorites(self._get_favorites())

    def _activate_item(self, item: ClipboardItem) -> None:
        item = self._ensure_blobs(item)
        set_clipboard_item(item, hwnd=self.listener.hwnd)

    def _ensure_blobs(self, item: ClipboardItem) -> ClipboardItem:
        """Load blob data from DB if the item is slim and has a db_id."""
        if not item.needs_blob_load:
            return item
        if self._store is None or item.db_id is None:
            return item
        try:
            raw, img = self._store.load_blobs(item.db_id)
            return item.with_blobs(raw, img)
        except Exception:
            log.exception("按需加载 blob 失败 (db_id=%s)", item.db_id)
            return item

    def _load_blobs_for_ui(self, item: ClipboardItem) -> ClipboardItem:
        """Public blob loader exposed to UI panel for preview/drag."""
        return self._ensure_blobs(item)

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
        if self._store is not None:
            try:
                self._store.clear()
            except Exception:
                log.exception("清空持久化历史失败")
        if self.panel.isVisible():
            self.panel.set_items(self.history.items())
            self.panel.set_favorites(self._get_favorites())

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
        is_now_fav, _ = self.favorites.toggle(item)
        try:
            self.favorites.save()
        except Exception:
            log.exception("保存收藏失败")
        if self.panel.isVisible():
            self.panel.set_favorites(self._get_favorites())
        return True, None

    def _remove_favorite(self, fav_id: str) -> tuple[bool, str | None]:
        self.favorites.remove_by_id(fav_id)
        try:
            self.favorites.save()
        except Exception:
            log.exception("保存收藏失败")
        if self.panel.isVisible():
            self.panel.set_favorites(self._get_favorites())
        return True, None

    def _reorder_favorites(self, fav_ids_in_order: list[str]) -> tuple[bool, str | None]:
        self.favorites.set_order(fav_ids_in_order)
        try:
            self.favorites.save()
        except Exception:
            log.exception("保存收藏排序失败")
        if self.panel.isVisible():
            self.panel.set_favorites(self._get_favorites())
        return True, None

    def _edit_item(self, old_item: ClipboardItem, new_item: ClipboardItem) -> tuple[bool, str | None]:
        replaced = self.history.replace_first(old_item, new_item)
        if not replaced:
            return False, "未找到要编辑的历史记录"

        changed_fav = self.favorites.replace_item(old_item, new_item)
        if changed_fav:
            try:
                self.favorites.save()
            except Exception:
                log.exception("保存编辑后的收藏失败")

        if self._store is not None and old_item.db_id is not None:
            try:
                self._store.update_text(old_item.db_id, new_item.text)
            except Exception:
                log.exception("同步编辑后的持久化历史失败")
                return False, "已更新内存历史，但持久化同步失败"

        if self.panel.isVisible():
            self.panel.set_items(self.history.items())
            self.panel.set_favorites(self._get_favorites())
        return True, None

    def _switch_db_path(self, new_db_path: str | None) -> tuple[bool, str | None]:
        """关闭当前持久化存储，将历史库迁移到新位置并重新打开。"""
        if self._store is None:
            return True, None
        current_path = self._store.path
        target_path = new_db_path or default_db_path()
        if os.path.abspath(current_path) == os.path.abspath(target_path):
            return True, None
        try:
            self._store.close()
        except Exception:
            log.debug("切换数据库前关闭存储异常", exc_info=True)
        self._store = None
        try:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            # 目标不存在时迁移现有数据库（含 WAL/SHM 辅助文件）。
            if not os.path.exists(target_path) and os.path.exists(current_path):
                for suffix in ("", "-wal", "-shm"):
                    src = current_path + suffix
                    dst = target_path + suffix
                    if os.path.exists(src):
                        shutil.move(src, dst)
            self._store = SQLiteHistoryStore(target_path)
            loaded = self._store.load_recent_slim(self.history.max_items)
            self.history.reset(loaded)
        except Exception:
            log.exception("切换数据库位置失败")
            # 尝试回退到原位置，避免丢失持久化能力。
            try:
                self._store = SQLiteHistoryStore(current_path)
            except Exception:
                self._store = None
            return False, "数据库位置切换失败，请检查目标路径是否可写"
        return True, None

    def _enable_persistence(self, enabled: bool) -> None:
        if enabled and self._store is None:
            db_path = self.settings.db_path or default_db_path()
            try:
                os.makedirs(os.path.dirname(db_path), exist_ok=True)
                self._store = SQLiteHistoryStore(db_path)
                loaded = self._store.load_recent_slim(self.history.max_items)
                for it in reversed(loaded):
                    self.history.add(it)
            except Exception:
                log.exception("启用持久化失败")
                self._store = None
                enabled = False
        elif not enabled and self._store is not None:
            try:
                self._store.close()
            except Exception:
                log.debug("关闭持久化存储异常", exc_info=True)
            self._store = None

        self.settings = replace(self.settings, persist_enabled=enabled)
        try:
            save_settings(self.settings)
        except Exception:
            log.exception("保存设置失败")

        if self.panel.isVisible():
            self.panel.set_items(self.history.items())
        self._sync_ui_state()

    def _sync_ui_state(self) -> None:
        try:
            self._act_pause.setChecked(self.paused)
        except Exception:
            log.debug("更新托盘菜单异常", exc_info=True)
        try:
            self._act_persist.setChecked(self._store is not None)
        except Exception:
            log.debug("更新托盘菜单异常", exc_info=True)

        self.panel.set_paused(self.paused)

        show_hint = self.hotkey_show_hint or "(无)"
        pause_hint = self.hotkey_pause_hint or "(无)"
        hint = f"面板: {show_hint}\n暂停: {pause_hint}"
        persist = "持久化: 开" if self._store is not None else "持久化: 关"
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

    def _apply_settings(self, show_seq: str, pause_seq: str, max_items: int, autostart_enabled: bool, panel_width: int = 640, panel_height: int = 620, db_path: str = "") -> tuple[bool, str | None]:
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
            ok_db, err_db = self._switch_db_path(new_db_path)
            if not ok_db:
                return False, err_db

        previous_max_items = self.history.max_items
        if max_items != previous_max_items:
            self.history.set_max_items(max_items)
            if self._store is not None:
                try:
                    loaded = self._store.load_recent_slim(max_items)
                    self.history.reset(loaded)
                except Exception:
                    log.exception("更新历史条数后重新加载持久化历史失败")
                    return False, "热键已更新，但重新加载历史失败"

        self.settings = replace(
            self.settings,
            max_items=max_items,
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
            self.panel.set_items(self.history.items())
            self.panel.set_favorites(self._get_favorites())
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
            self.tray.hide()
        except Exception:
            log.debug("隐藏托盘异常", exc_info=True)
        try:
            self.qt_app.quit()
        except Exception:
            log.debug("退出应用异常", exc_info=True)
