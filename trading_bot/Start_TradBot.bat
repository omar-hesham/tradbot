@echo off
color 0A
title TradBot - One-Click Launcher
cd /d "%~dp0"

echo ===================================================
echo     TradBot - AI Crypto Trading Bot
echo ===================================================

:: 1. Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    color 0C
    echo [ERROR] Python is not installed or not added to PATH.
    echo Please install Python 3.10 or higher from python.org and ensure you check "Add Python to PATH" during installation.
    pause
    exit /b
)

:: 2. Setup / Install Dependencies (Only runs if not already installed)
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] First-time setup detected. This will only happen once!
    echo [INFO] Creating Virtual Environment...
    python -m venv .venv
    
    echo [INFO] Installing required AI and trading libraries. Please wait...
    .venv\Scripts\python.exe -m pip install --upgrade pip >nul
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    
    echo [OK] Setup completed successfully!
    echo ===================================================
    echo.
)

:: 3. Start the Server and Open Browser
echo [INFO] Starting TradBot Server...
echo [INFO] The dashboard will open in your default browser automatically.
echo [WARNING] Please do NOT close this black window while using the bot.
echo.

:: Open the browser asynchronously after 4 seconds (giving the server time to start)
start cmd /c "timeout /t 4 >nul && start http://127.0.0.1:8005"

:: Run the application
.venv\Scripts\python.exe main.py

pause
