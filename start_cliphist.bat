@echo off
setlocal

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
set "RUN_PY=%APP_DIR%\run.py"
set "PYW=C:\Windows\pyw.exe"

if not exist "%RUN_PY%" (
  echo [ERROR] run.py not found: "%RUN_PY%"
  exit /b 1
)

if not exist "%PYW%" (
  for %%I in (pyw.exe) do set "PYW=%%~$PATH:I"
)

if not defined PYW (
  echo [ERROR] pyw.exe not found. Please install Python Launcher.
  exit /b 1
)

start "" "%PYW%" -3 "%RUN_PY%"
exit /b 0
