@echo off
REM Trading Hydra Run Script for Windows

REM Activate virtual environment
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

REM Run the trading system
echo Starting Trading Hydra...
python main.py
