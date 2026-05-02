@echo off
echo ===================================================
echo   TradBot AI - Setup and Run Script
echo ===================================================
echo.

:: 1. Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in your PATH.
    echo Please install Python 3.10+ and try again.
    pause
    exit /b
)

:: 2. Create Virtual Environment if it doesn't exist
if not exist "venv\Scripts\activate.bat" (
    echo [*] Creating virtual environment...
    python -m venv venv
)

:: 3. Activate Virtual Environment
echo [*] Activating virtual environment...
call venv\Scripts\activate.bat

:: 4. Install Requirements
echo [*] Installing dependencies...
pip install -r requirements.txt

:: 5. Start the Application
echo.
echo ===================================================
echo   Starting the AI Trading Bot Server...
echo ===================================================
echo.
echo [*] The dashboard will be available at: http://127.0.0.1:8000/dashboard
echo [*] Press Ctrl+C to stop the server.
echo.

:: Automatically open the dashboard in the default browser after 3 seconds
start "" "http://127.0.0.1:8000/dashboard"

:: Run the FastAPI server using Uvicorn
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
