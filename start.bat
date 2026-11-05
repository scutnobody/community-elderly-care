@echo off
cd /d "%~dp0"
python --version >nul 2>&1
if errorlevel 1 (
  echo Python was not found. Install Python 3 and requirements.txt dependencies first.
  pause
  exit /b 1
)
echo Open http://127.0.0.1:8080 after the server starts.
python server.py
pause
