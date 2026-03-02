#!/bin/bash
# Trading Hydra - Health Check Script
# Verifies app is running and optionally restarts if dead

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Detect APP_DIR: if main.py exists in SCRIPT_DIR, we're in the app root
if [ -f "$SCRIPT_DIR/main.py" ]; then
    APP_DIR="$SCRIPT_DIR"
else
    APP_DIR="$(dirname "$SCRIPT_DIR")"
fi

PID_FILE="$APP_DIR/trading-hydra.pid"
LOG_FILE="$APP_DIR/logs/healthcheck.log"
AUTO_RESTART="${1:-no}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

is_running() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

mkdir -p "$APP_DIR/logs"

if is_running; then
    PID=$(cat "$PID_FILE")
    log "HEALTHY: Trading Hydra is running (PID: $PID)"
    
    if [ -f "$APP_DIR/logs/app.jsonl" ]; then
        LAST_LOG=$(tail -1 "$APP_DIR/logs/app.jsonl" 2>/dev/null | head -c 100)
        log "Last log entry: $LAST_LOG..."
    fi
    
    exit 0
else
    log "UNHEALTHY: Trading Hydra is NOT running"
    
    if [ -f "$PID_FILE" ]; then
        log "Removing stale PID file"
        rm -f "$PID_FILE"
    fi
    
    if [ "$AUTO_RESTART" = "restart" ] || [ "$AUTO_RESTART" = "yes" ]; then
        log "Auto-restart requested. Starting Trading Hydra..."
        # Use start script from the same directory as this script
        if [ -f "$SCRIPT_DIR/start-trading.sh" ]; then
            "$SCRIPT_DIR/start-trading.sh"
        else
            "$APP_DIR/start-trading.sh"
        fi
        exit $?
    fi
    
    exit 1
fi
