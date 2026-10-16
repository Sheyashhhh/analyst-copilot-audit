@echo off
REM One-click setup for Windows. Double-click or run from Command Prompt / PowerShell.
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.10+ from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause & exit /b 1
)
if not exist .venv (python -m venv .venv)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || (echo Install failed & pause & exit /b 1)
python -m copilot_audit setup || (pause & exit /b 1)
python -m copilot_audit doctor
echo.
echo Done. To use the project later: open a terminal here and run   .venv\Scripts\activate
pause
