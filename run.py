import sys


def _load_app():
    try:
        from cliphist.qt_app import ClipHistApp
    except ModuleNotFoundError as e:
        missing = getattr(e, "name", "") or ""
        if missing in ("win32con", "win32clipboard", "pythoncom", "PySide6", "PIL"):
            sys.stderr.write(
                "依赖缺失：{missing}\n"
                "请使用当前解释器安装依赖：\n"
                f"  {sys.executable} -m pip install -r requirements.txt\n"
            )
        raise
    return ClipHistApp


def main() -> None:
    ClipHistApp = _load_app()
    app = ClipHistApp()
    raise SystemExit(app.run())


if __name__ == "__main__":
    main()
