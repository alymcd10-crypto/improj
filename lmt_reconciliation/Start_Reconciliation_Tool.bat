@echo off
REM LMT CRM Reconciliation Tool - Windows launcher. Double-click to start.
cd /d "%~dp0"
set PY=
where py >nul 2>nul && set PY=py -3
if "%PY%"=="" where python >nul 2>nul && set PY=python
if "%PY%"=="" (
  echo Python 3 is not installed. Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^), then double-click this file again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo First run: setting up. This takes a few minutes and needs internet once.
  %PY% -m venv .venv || goto :fail
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
)
echo Starting. Your browser will open at http://localhost:8501  (close this window to stop the tool)
".venv\Scripts\python.exe" launch.py
exit /b 0
:fail
echo Setup failed. Copy the messages above and send them to whoever supports this tool.
pause
exit /b 1
