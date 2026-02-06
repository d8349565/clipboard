from __future__ import annotations

import logging
import os
import sys
from dataclasses import replace

import win32con

log = logging.getLogger(__name__)

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

from .favorites import FavoritesStore
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

        self.settings = load_settings()
        self.paused = False
        self.history = ClipboardHistory(max_items=self.settings.max_items)
        self._store: SQLiteHistoryStore | None = None
        self.favorites = FavoritesStore()
        self.favorites.load()

        self.hotkey_show_hint: str | None = None
        self.hotkey_pause_hint: str | None = None
        self._hotkey_specs: dict[int, HotkeySpec] = {}

        self.tray = QSystemTrayIcon(self._default_icon(), self.qt_app)
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
        )

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
        dlg = SettingsDialog(self.settings, self._apply_hotkeys, parent=self.panel)
        dlg.exec()

    def _handle_event(self, evt: object) -> None:
        if isinstance(evt, HotkeyEvent):
            if evt.hotkey_id == HOTKEY_TOGGLE_PANEL:
                self._open_panel()
            elif evt.hotkey_id == HOTKEY_TOGGLE_PAUSE:
                self._set_paused(not self.paused)
            return

        if isinstance(evt, ClipboardItem):
            if self.paused:
                return
            added = self.history.add(evt)
            if added and self.panel.isVisible():
                self.panel.set_items(self.history.items())
                self.panel.set_favorites(self._get_favorites())
            if added and self._store is not None:
                try:
                    self._store.insert_and_trim(evt, self.history.max_items)
                except Exception:
                    log.exception("持久化写入失败")

    def _activate_item(self, item: ClipboardItem) -> None:
        set_clipboard_item(item, hwnd=self.listener.hwnd)

    def _set_paused(self, paused: bool) -> None:
        if self.paused == paused:
            return
        self.paused = paused
        self._sync_ui_state()

    def _clear_history(self) -> None:
        self.history.clear()
        if self._store is not None:
            try:
                self._store.clear()
            except Exception:
                log.exception("清空持久化历史失败")
        if self.panel.isVisible():
            self.panel.set_items(self.history.items())
            self.panel.set_favorites(self._get_favorites())

    def _get_favorites(self) -> list[tuple[str, ClipboardItem]]:
        return [(e.fav_id, e.item) for e in self.favorites.entries]

    def _toggle_favorite(self, item: ClipboardItem) -> tuple[bool, str | None]:
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

    def _enable_persistence(self, enabled: bool) -> None:
        if enabled and self._store is None:
            db_path = self.settings.db_path or default_db_path()
            try:
                os.makedirs(os.path.dirname(db_path), exist_ok=True)
                self._store = SQLiteHistoryStore(db_path)
                loaded = self._store.load_recent(self.history.max_items)
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
