@echo off
setlocal enabledelayedexpansion

echo ===================================================
echo   TradBot AI - Digital Command Center Launch
echo ===================================================
echo.

:: Detect Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.10+.
    pause
    exit /b
)

:: Detect Virtual Environment
set "VENV_PATH="
if exist ".venv\Scripts\activate.bat" (
    set "VENV_PATH=.venv"
) else if exist "venv\Scripts\activate.bat" (
    set "VENV_PATH=venv"
) else (
    echo [*] Creating fresh virtual environment (.venv)...
    python -m venv .venv
    set "VENV_PATH=.venv"
)

:: Activate and Sync
echo [*] Activating Environment: !VENV_PATH!
call !VENV_PATH!\Scripts\activate.bat

echo [*] Synchronizing dependencies...
pip install -r requirements.txt --quiet

:: Launch
echo.
echo ===================================================
echo   Bot Status: READY
echo   Dashboard: http://127.0.0.1:8000/dashboard
echo ===================================================
echo.

:: Open Dashboard
start "" "http://127.0.0.1:8000/dashboard"

:: Run Server
uvicorn main:app --host 0.0.0.0 --port 8000
