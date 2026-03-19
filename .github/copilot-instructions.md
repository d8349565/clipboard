# Copilot Instructions for ClipHist

## Project Overview

- Windows clipboard history tool, PySide6 + pywin32
- Single-instance, tray UI, persistent history, favorites, hotkey fallback

## Build & Run

- Install dependencies: `python -m pip install -r requirements.txt`
- Start: `python run.py`
- Main entry: run.py → cliphist.qt_app.ClipHistApp

## Key Conventions

- All features in cliphist/ directory
- Config, favorites, history stored in %APPDATA%/ClipHist
- Hotkeys auto-fallback, customizable in settings
- UI: tray icon, main panel, settings dialog
- Exception handling: log to stderr, try/except everywhere

## Agent Guidance

- Prefer PySide6 for UI, pywin32 for clipboard/hotkey
- Do not duplicate single-instance logic
- Always check for dependency errors and show user-friendly message
- Persist history only if enabled
- Use pytest for tests, place in root or cliphist/ as needed

## Example Prompts

- “添加新的剪切板类型支持”
- “优化持久化数据库性能”
- “实现自定义热键设置面板”
- “增加批量导出历史功能”
