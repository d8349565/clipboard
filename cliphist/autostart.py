from __future__ import annotations

import os
import sys
from pathlib import Path

_STARTUP_LINK_NAME = "ClipHist.lnk"


def startup_shortcut_path() -> str:
    startup_dir = os.path.join(
        os.environ.get("APPDATA") or "",
        "Microsoft",
        "Windows",
        "Start Menu",
        "Programs",
        "Startup",
    )
    return os.path.join(startup_dir, _STARTUP_LINK_NAME)


def is_autostart_enabled() -> bool:
    return os.path.isfile(startup_shortcut_path())


def set_autostart_enabled(enabled: bool) -> None:
    link_path = startup_shortcut_path()
    if enabled:
        os.makedirs(os.path.dirname(link_path), exist_ok=True)
        _create_shortcut(link_path)
        return
    if os.path.exists(link_path):
        os.remove(link_path)


def _create_shortcut(link_path: str) -> None:
    # Import COM lazily. Importing win32com at application startup may rebuild
    # its generated cache even when autostart settings are never changed.
    from win32com.client import Dispatch

    shell = Dispatch("WScript.Shell")
    shortcut = shell.CreateShortcut(link_path)

    target_path, arguments, working_directory = _launch_command()
    shortcut.TargetPath = target_path
    shortcut.Arguments = arguments
    shortcut.WorkingDirectory = working_directory

    icon_path = Path(working_directory) / "assets" / "icon.ico"
    if icon_path.is_file():
        shortcut.IconLocation = f"{icon_path},0"

    shortcut.Save()


def _launch_command() -> tuple[str, str, str]:
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        return str(exe_path), "", str(exe_path.parent)

    python_executable = Path(sys.executable).resolve()
    pythonw_executable = python_executable.with_name("pythonw.exe")
    launcher = pythonw_executable if pythonw_executable.is_file() else python_executable

    project_root = Path(__file__).resolve().parent.parent
    run_py = project_root / "run.py"
    return str(launcher), f'"{run_py}"', str(project_root)
