@echo off
REM Trading Hydra Setup Script for Windows

echo ===================================
echo   Trading Hydra Setup Script
echo ===================================
echo.

REM Check Python
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Python is not installed. Please install Python 3.9+ first.
    pause
    exit /b 1
)

python --version

REM Remove old venv if it exists
if exist "venv" (
    echo Removing old virtual environment...
    rmdir /s /q venv
)

REM Create virtual environment
echo.
echo Creating virtual environment...
python -m venv venv

REM Activate and install
echo Activating virtual environment...
call venv\Scripts\activate.bat

echo Upgrading pip...
python -m pip install --upgrade pip

echo.
echo Installing dependencies...
pip install -r requirements.txt

REM Create directories
echo.
echo Creating directories...
if not exist "logs" mkdir logs
if not exist "state" mkdir state
if not exist "state\backups" mkdir state\backups
if not exist "cache" mkdir cache

REM Create .env file if it doesn't exist
if not exist ".env" (
    echo.
    echo Creating .env file template...
    (
        echo # Alpaca API Credentials
        echo # Get your keys from: https://app.alpaca.markets/
        echo ALPACA_KEY=your_api_key_here
        echo ALPACA_SECRET=your_api_secret_here
        echo ALPACA_PAPER=true
        echo.
        echo # Optional: OpenAI for AI features
        echo # OPENAI_API_KEY=your_openai_key_here
    ) > .env
    echo IMPORTANT: Edit .env file with your Alpaca API credentials!
)

echo.
echo ===================================
echo   Setup Complete!
echo ===================================
echo.
echo To run Trading Hydra:
echo   1. Edit .env with your Alpaca API credentials
echo   2. Activate the virtual environment:
echo      venv\Scripts\activate
echo   3. Start the system:
echo      python main.py
echo.
echo Quick start commands:
echo   python main.py --fresh-start   # Reset state for new account
echo   python main.py --inplace       # Run in foreground
echo.
pause
