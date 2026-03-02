#!/bin/bash
# =============================================================================
# Trading Hydra - Weekday Guard
# =============================================================================
# Only starts the trading bot on weekdays (Mon-Fri).
# Used by systemd to prevent the bot from running on weekends.
#
# Usage: Called by systemd service (not directly)
#
# Exit codes:
#   0 - Weekend (clean exit, service stays down with Restart=on-failure)
#   * - Bot exit code (passed through, non-zero triggers restart)
# =============================================================================

set -e

# Get day of week (1=Monday, 7=Sunday)
DAY=$(date +%u)

# Log file for guard decisions
LOG_DIR="/var/log/trading_hydra"
mkdir -p "$LOG_DIR" 2>/dev/null || true

# Check if weekday (1-5 = Mon-Fri)
if [ "$DAY" -ge 1 ] && [ "$DAY" -le 5 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') | Weekday detected (day $DAY) - starting bot" >> "$LOG_DIR/guard.log"
    
    # Change to bot directory
    cd "$(dirname "$0")/.." || exit 1
    
    # Activate virtual environment if it exists
    if [ -f "venv/bin/activate" ]; then
        source venv/bin/activate
    fi
    
    # Start the bot (exec replaces this shell, so signals go to Python)
    exec python3 main.py
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') | Weekend detected (day $DAY) - bot not started" >> "$LOG_DIR/guard.log"
    exit 0
fi
