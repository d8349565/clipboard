@echo off
setlocal

set "APP_DIR=%~dp0"
set "RUN_PY=%APP_DIR%run.py"
set "PYW=%APP_DIR%.venv\Scripts\pythonw.exe"
set "PYW_ARGS="

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

pushd "%APP_DIR%" >nul || (
  echo [ERROR] Cannot use project directory: "%APP_DIR%"
  exit /b 1
)
start "" "%PYW%" %PYW_ARGS% "%RUN_PY%"
popd
exit /b 0
