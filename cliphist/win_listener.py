from __future__ import annotations

import ctypes
import logging
import threading
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

import pythoncom
import win32con
import win32gui

from .capture import capture_clipboard
from .models import ClipboardItem


user32 = ctypes.WinDLL("user32", use_last_error=True)


WM_CLIPBOARDUPDATE = 0x031D
WM_APP_REGISTER_HOTKEY = win32con.WM_APP + 1
WM_APP_UNREGISTER_HOTKEY = win32con.WM_APP + 2


@dataclass(frozen=True, slots=True)
class HotkeyEvent:
    hotkey_id: int


EventCallback = Callable[[ClipboardItem | HotkeyEvent], None]


class ClipboardListener:
    def __init__(self, on_event: EventCallback) -> None:
        self._on_event = on_event
        self._thread: threading.Thread | None = None
        self._hwnd: int | None = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._timer_lock = threading.Lock()
        self._capture_timer: threading.Timer | None = None
        self._hotkey_ids: set[int] = set()

    @property
    def hwnd(self) -> int | None:
        return self._hwnd

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._ready_event.clear()
        self._thread = threading.Thread(target=self._run, name="ClipboardListener", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 2.0) -> None:
        hwnd = self._hwnd
        if hwnd:
            try:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=timeout_s)

    def wait_ready(self, timeout_s: float = 2.0) -> bool:
        return self._ready_event.wait(timeout=timeout_s)

    def register_hotkey(self, hotkey_id: int, modifiers: int, vk: int) -> bool:
        ok, _ = self.register_hotkey_with_error(hotkey_id, modifiers, vk)
        return ok

    def register_hotkey_with_error(self, hotkey_id: int, modifiers: int, vk: int) -> tuple[bool, int]:
        hwnd = self._hwnd
        if not hwnd:
            return False, 0
        lparam = (int(modifiers) & 0xFFFF) | ((int(vk) & 0xFFFF) << 16)
        err = int(user32.SendMessageW(hwnd, WM_APP_REGISTER_HOTKEY, int(hotkey_id), lparam))
        return err == 0, err

    def unregister_hotkey(self, hotkey_id: int) -> None:
        hwnd = self._hwnd
        if not hwnd:
            return
        try:
            user32.SendMessageW(hwnd, WM_APP_UNREGISTER_HOTKEY, int(hotkey_id), 0)
        except Exception:
            pass

    def _run(self) -> None:
        pythoncom.CoInitialize()
        try:
            self._create_window_and_pump()
        finally:
            pythoncom.CoUninitialize()

    def _create_window_and_pump(self) -> None:
        class_name = "ClipHistHiddenWindow"

        wc = win32gui.WNDCLASS()
        wc.lpszClassName = class_name
        wc.lpfnWndProc = self._wnd_proc
        wc.hInstance = win32gui.GetModuleHandle(None)

        try:
            atom = win32gui.RegisterClass(wc)
        except win32gui.error:
            atom = win32gui.GetClassInfo(wc.hInstance, class_name)[0]

        hwnd = win32gui.CreateWindowEx(
            0,
            atom,
            class_name,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            wc.hInstance,
            None,
        )
        self._hwnd = hwnd
        self._ready_event.set()

        if not user32.AddClipboardFormatListener(hwnd):
            raise OSError("AddClipboardFormatListener failed")

        try:
            win32gui.PumpMessages()
        finally:
            try:
                user32.RemoveClipboardFormatListener(hwnd)
            except Exception:
                log.debug("RemoveClipboardFormatListener 异常", exc_info=True)
            try:
                win32gui.DestroyWindow(hwnd)
            except Exception:
                log.debug("DestroyWindow 异常", exc_info=True)
            self._hwnd = None

    def _wnd_proc(self, hwnd: int, msg: int, wparam: int, lparam: int):
        if msg == WM_APP_REGISTER_HOTKEY:
            modifiers = int(lparam) & 0xFFFF
            vk = (int(lparam) >> 16) & 0xFFFF
            ctypes.set_last_error(0)
            ok = bool(user32.RegisterHotKey(hwnd, int(wparam), int(modifiers), int(vk)))
            if ok:
                self._hotkey_ids.add(int(wparam))
                return 0
            err = int(ctypes.get_last_error())
            return err or 1

        if msg == WM_APP_UNREGISTER_HOTKEY:
            try:
                user32.UnregisterHotKey(hwnd, int(wparam))
            except Exception:
                log.debug("UnregisterHotKey 异常", exc_info=True)
            self._hotkey_ids.discard(int(wparam))
            return 0

        if msg == WM_CLIPBOARDUPDATE:
            self._schedule_capture()
            return 0

        if msg == win32con.WM_HOTKEY:
            try:
                self._on_event(HotkeyEvent(hotkey_id=int(wparam)))
            except Exception:
                log.debug("热键事件回调异常", exc_info=True)
            return 0

        if msg == win32con.WM_CLOSE:
            try:
                win32gui.DestroyWindow(hwnd)
            except Exception:
                log.debug("WM_CLOSE DestroyWindow 异常", exc_info=True)
            return 0

        if msg == win32con.WM_DESTROY:
            self._stop_event.set()
            with self._timer_lock:
                if self._capture_timer:
                    try:
                        self._capture_timer.cancel()
                    except Exception:
                        log.debug("取消定时器异常", exc_info=True)
                    self._capture_timer = None
            for hotkey_id in list(self._hotkey_ids):
                try:
                    user32.UnregisterHotKey(hwnd, hotkey_id)
                except Exception:
                    log.debug("WM_DESTROY UnregisterHotKey 异常", exc_info=True)
            self._hotkey_ids.clear()
            try:
                user32.RemoveClipboardFormatListener(hwnd)
            except Exception:
                log.debug("WM_DESTROY RemoveClipboardFormatListener 异常", exc_info=True)
            try:
                win32gui.PostQuitMessage(0)
            except Exception:
                log.debug("PostQuitMessage 异常", exc_info=True)
            return 0

        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _schedule_capture(self) -> None:
        with self._timer_lock:
            if self._capture_timer:
                try:
                    self._capture_timer.cancel()
                except Exception:
                    pass
                self._capture_timer = None

            timer = threading.Timer(0.12, self._do_capture)
            timer.daemon = True
            self._capture_timer = timer
            timer.start()

    def _do_capture(self) -> None:
        hwnd = self._hwnd
        if not hwnd or self._stop_event.is_set():
            return
        try:
            item = capture_clipboard(hwnd=hwnd)
            if item is not None:
                self._on_event(item)
        except Exception:
            log.debug("剪贴板捕获异常", exc_info=True)
