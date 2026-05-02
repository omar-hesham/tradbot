@echo off
color 0A
title TradBot Installer

echo ===================================================
echo     TradBot - AI Crypto Trading Bot Installer
echo ===================================================

:: Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not added to PATH.
    echo Please install Python 3.10 or higher from python.org and check "Add Python to PATH".
    pause
    exit /b
)

echo [OK] Python is installed.
echo.

:: Create Virtual Environment if it doesn't exist
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Creating Virtual Environment...
    python -m venv .venv
) else (
    echo [OK] Virtual Environment already exists.
)

:: Install Requirements
echo [INFO] Installing Dependencies (This might take a moment)...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt

echo.
echo ===================================================
echo     Installation Complete!
echo ===================================================
echo You can now use run.bat to start the bot.
pause
