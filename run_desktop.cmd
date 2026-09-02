@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1"
  exit /b %errorlevel%
)

start "" ".venv\Scripts\pythonw.exe" "%~dp0desktop.py"
exit /b 0
