# ClipHist（Windows 剪切板历史）

## 功能
- 实时监听剪切板变化并保存历史
- 支持：文本、文件列表、图片（DIB）、HTML、RTF
- 托盘常驻；热键或托盘打开面板
- 面板支持：搜索、双击写回剪切板、拖拽输出到其他应用

## 运行
```powershell
python -m pip install -r requirements.txt
python run.py
```

如果你有多个 Python（例如 conda/base、系统 Python、IDE 自带 Python），务必用同一个解释器安装依赖并运行，例如：
```powershell
D:/RUANJIAN/py/python.exe -m pip install -r requirements.txt
D:/RUANJIAN/py/python.exe run.py
```

## 热键
- 默认尝试 Ctrl+Shift+V（若冲突则尝试 Ctrl+Alt+V）；失败可用托盘打开面板
- 默认尝试 Ctrl+Shift+P（或 Ctrl+Alt+P）切换“暂停监听”

## 数据与隐私
- 默认不落盘（持久化关闭）
- 托盘可开启“启用持久化”，数据保存到 %APPDATA%\\ClipHist\\history.sqlite3
- 设置文件：%APPDATA%\\ClipHist\\config.json

## 打包（Windows）
```powershell
./build_windows.ps1
```
输出位于 `dist/ClipHist/ClipHist.exe`（由 PyInstaller 生成）。

## 测试
```powershell
python -m pip install pytest
pytest -q
```
