import os
import sys
import ctypes
import logging

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
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


def _load_app():
    try:
        os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.window=false")
        from cliphist.qt_app import ClipHistApp
    except ModuleNotFoundError as e:
        missing = getattr(e, "name", "") or ""
        if missing in ("win32con", "win32clipboard", "pythoncom", "PySide6"):
            sys.stderr.write(
                f"依赖缺失：{missing}\n"
                "请使用当前解释器安装依赖：\n"
                f"  {sys.executable} -m pip install -r requirements.txt\n"
            )
        raise
    return ClipHistApp


def main() -> None:
    if not _acquire_single_instance():
        sys.stderr.write("ClipHist 已在运行，请先退出已有实例（托盘图标）后再启动。\n")
        raise SystemExit(0)

    ClipHistApp = _load_app()
    try:
        app = ClipHistApp()
        raise SystemExit(app.run())
    finally:
        _release_single_instance()


if __name__ == "__main__":
    main()
