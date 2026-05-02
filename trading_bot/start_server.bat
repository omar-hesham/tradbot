@echo off
title TradBot Server
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found. Please run install.bat first!
    pause
    exit /b
)

echo Starting TradBot...
.venv\Scripts\python.exe main.py
pause