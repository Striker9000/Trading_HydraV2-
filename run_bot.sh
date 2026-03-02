#!/bin/bash
# Trading Hydra - Resilient Runner with Watchdog
# This script runs the trading bot with auto-restart via watchdog

echo "=========================================="
echo "  Trading Hydra - Starting with Watchdog"
echo "=========================================="

# Kill any existing bot processes
pkill -f "python3 main.py" 2>/dev/null
pkill -f "python3 watchdog.py" 2>/dev/null
sleep 1

# Run the watchdog with nohup to survive terminal disconnections
# Output goes to bot.log, errors to bot_error.log
nohup python3 watchdog.py > bot.log 2>&1 &
WATCHDOG_PID=$!

echo "Watchdog started with PID: $WATCHDOG_PID"
echo "Logs: tail -f bot.log"
echo ""
echo "To stop: pkill -f 'python3 watchdog.py'"
echo "To view: tail -f bot.log"
echo ""

# Wait a moment and check if it started
sleep 3
if ps -p $WATCHDOG_PID > /dev/null 2>&1; then
    echo "✓ Watchdog is running successfully!"
    echo "✓ Auto-restart enabled - bot will restart on crash"
    echo ""
    echo "Dashboard: http://0.0.0.0:5000"
    echo ""
    echo "Showing live logs (Ctrl+C to stop viewing, bot continues running):"
    echo "=========================================="
    echo ""
    echo "Watchdog events: tail -f logs/watchdog.jsonl"
    echo ""
    tail -f bot.log
else
    echo "✗ Watchdog failed to start. Check bot_error.log"
    cat bot_error.log 2>/dev/null
fi
