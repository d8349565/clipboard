import os
import sys
import ctypes
import logging

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr or open(os.devnull, "w")),
    ],
)


_SINGLE_INSTANCE_MUTEX = "Local\\ClipHist.SingleInstance"
_ERROR_ALREADY_EXISTS = 183
_mutex_handle: int | None = None


def _acquire_single_instance() -> bool:
    global _mutex_handle
    if os.name != "nt":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    create_mutex.restype = ctypes.c_void_p
    handle = create_mutex(None, False, _SINGLE_INSTANCE_MUTEX)
    if not handle:
        return True
    _mutex_handle = int(ctypes.cast(handle, ctypes.c_void_p).value or 0)
    return ctypes.get_last_error() != _ERROR_ALREADY_EXISTS


def _release_single_instance() -> None:
    global _mutex_handle
    if os.name != "nt" or not _mutex_handle:
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        close_handle(ctypes.c_void_p(_mutex_handle))
    except Exception:
        pass
    _mutex_handle = None


def _msgbox(text: str, title: str = "ClipHist") -> None:
    """在 --windowed 打包模式下 sys.stderr 为 None，使用 Win32 弹窗通知用户。"""
    try:
        MB_OK = 0x0
        MB_ICONINFORMATION = 0x40
        ctypes.windll.user32.MessageBoxW(None, text, title, MB_OK | MB_ICONINFORMATION)
    except Exception:
        # 终极兜底：尝试 stderr（开发模式可用）
        if sys.stderr is not None:
            sys.stderr.write(text + "\n")


def _load_app():
    try:
        os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.window=false")
        from cliphist.qt_app import ClipHistApp
    except ModuleNotFoundError as e:
        missing = getattr(e, "name", "") or ""
        if missing in ("win32con", "win32clipboard", "pythoncom", "PySide6"):
            _msgbox(
                f"依赖缺失：{missing}\n"
                "请使用当前解释器安装依赖：\n"
                f"  {sys.executable} -m pip install -r requirements.txt",
                "ClipHist - 缺少依赖",
            )
        raise
    return ClipHistApp


def main() -> None:
    if not _acquire_single_instance():
        _msgbox("ClipHist 已在运行中。\n\n请查看系统托盘区域的 ClipHist 图标。")
        raise SystemExit(0)

    ClipHistApp = _load_app()
    try:
        app = ClipHistApp()
        raise SystemExit(app.run())
    finally:
        _release_single_instance()


if __name__ == "__main__":
    main()
