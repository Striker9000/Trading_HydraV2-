@echo off
title Trading Hydra $500 PAPER
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo ==========================================
echo  TRADING HYDRA - $500 PAPER ACCOUNT
echo  Sweep-Optimized Settings Applied
echo  Press Ctrl+C to stop
echo ==========================================
echo.
python -X utf8 main.py
pause
