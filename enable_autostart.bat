@echo off
setlocal

set "MODE=%~1"
set "APP_DIR=%~dp0"
set "RUN_PY=%APP_DIR%run.py"
set "ICON_PATH=%APP_DIR%assets\icon.ico"
set "PYW=%APP_DIR%.venv\Scripts\pythonw.exe"
set "PYW_ARGS="
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LINK_PATH=%STARTUP%\ClipHist.lnk"

if /I "%MODE%"=="remove" goto :remove
if /I "%MODE%"=="uninstall" goto :remove

if not exist "%RUN_PY%" (
  echo [ERROR] run.py not found: "%RUN_PY%"
  exit /b 1
)

if not exist "%PYW%" (
  set "PYW="
  for %%I in (pythonw.exe) do set "PYW=%%~$PATH:I"
)

if not defined PYW (
  for %%I in (pyw.exe) do set "PYW=%%~$PATH:I"
  if defined PYW set "PYW_ARGS=-3 "
)

if not defined PYW (
  echo [ERROR] Python windowless interpreter not found.
  echo         Install Python or create the project environment first:
  echo         py -3 -m venv "%APP_DIR%.venv"
  echo         "%APP_DIR%.venv\Scripts\python.exe" -m pip install -r "%APP_DIR%requirements.txt"
  exit /b 1
)

set "VBS_FILE=%TEMP%\cliphist_make_shortcut_%RANDOM%%RANDOM%.vbs"
> "%VBS_FILE%" echo Set ws = CreateObject("WScript.Shell")
>> "%VBS_FILE%" echo Set fso = CreateObject("Scripting.FileSystemObject")
>> "%VBS_FILE%" echo startup = ws.SpecialFolders("Startup")
>> "%VBS_FILE%" echo linkPath = startup ^& "\ClipHist.lnk"
>> "%VBS_FILE%" echo Set s = ws.CreateShortcut(linkPath)
>> "%VBS_FILE%" echo s.TargetPath = "%PYW%"
>> "%VBS_FILE%" echo s.Arguments = "%PYW_ARGS%" ^& Chr(34) ^& "%RUN_PY%" ^& Chr(34)
>> "%VBS_FILE%" echo s.WorkingDirectory = "%APP_DIR%"
>> "%VBS_FILE%" echo If fso.FileExists("%ICON_PATH%") Then s.IconLocation = "%ICON_PATH%,0"
>> "%VBS_FILE%" echo s.Save

cscript //nologo "%VBS_FILE%"
set "CS_ERR=%ERRORLEVEL%"
del /f /q "%VBS_FILE%" >nul 2>nul
if not "%CS_ERR%"=="0" (
  echo [ERROR] Failed to create startup shortcut.
  exit /b 1
)

pushd "%APP_DIR%" >nul || (
  echo [ERROR] Cannot use project directory: "%APP_DIR%"
  exit /b 1
)
start "" "%PYW%" %PYW_ARGS% "%RUN_PY%"
popd
echo [OK] Auto-start enabled: "%LINK_PATH%"
echo [OK] ClipHist started (no console window).
exit /b 0

:remove
if exist "%LINK_PATH%" (
  del /f /q "%LINK_PATH%" >nul 2>nul
  if exist "%LINK_PATH%" (
    echo [ERROR] Failed to remove startup shortcut: "%LINK_PATH%"
    exit /b 1
  )
  echo [OK] Auto-start disabled.
) else (
  echo [OK] Startup shortcut not found. Nothing to remove.
)
exit /b 0
