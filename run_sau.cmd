@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\sau.exe" (
  echo [ERROR] sau not installed. Please check .venv in this directory.
  exit /b 1
)
".venv\Scripts\sau.exe" %*