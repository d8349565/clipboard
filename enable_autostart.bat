@echo off
setlocal

set "MODE=%~1"
set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
set "RUN_PY=%APP_DIR%\run.py"
set "ICON_PATH=%APP_DIR%\assets\icon.ico"
set "PYW=C:\Windows\pyw.exe"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LINK_PATH=%STARTUP%\ClipHist.lnk"

if /I "%MODE%"=="remove" goto :remove
if /I "%MODE%"=="uninstall" goto :remove

if not exist "%RUN_PY%" (
  echo [ERROR] run.py not found: "%RUN_PY%"
  exit /b 1
)

if not exist "%PYW%" (
  echo [ERROR] pyw.exe not found: "%PYW%"
  echo         Please confirm Python launcher is installed.
  exit /b 1
)

set "VBS_FILE=%TEMP%\cliphist_make_shortcut_%RANDOM%%RANDOM%.vbs"
> "%VBS_FILE%" echo Set ws = CreateObject("WScript.Shell")
>> "%VBS_FILE%" echo Set fso = CreateObject("Scripting.FileSystemObject")
>> "%VBS_FILE%" echo startup = ws.SpecialFolders("Startup")
>> "%VBS_FILE%" echo linkPath = startup ^& "\ClipHist.lnk"
>> "%VBS_FILE%" echo Set s = ws.CreateShortcut(linkPath)
>> "%VBS_FILE%" echo s.TargetPath = "C:\Windows\pyw.exe"
>> "%VBS_FILE%" echo s.Arguments = "-3 ""%RUN_PY%"""
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

start "" "%PYW%" -3 "%RUN_PY%"
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
