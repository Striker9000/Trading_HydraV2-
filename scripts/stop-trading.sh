#!/bin/bash
# Trading Hydra - Stop Script
# Graceful shutdown with timeout fallback

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Detect APP_DIR: if main.py exists in SCRIPT_DIR, we're in the app root
if [ -f "$SCRIPT_DIR/main.py" ]; then
    APP_DIR="$SCRIPT_DIR"
else
    APP_DIR="$(dirname "$SCRIPT_DIR")"
fi

PID_FILE="$APP_DIR/trading-hydra.pid"
LOG_FILE="$APP_DIR/logs/startup.log"
TIMEOUT=30

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

mkdir -p "$APP_DIR/logs"

log "========== Trading Hydra Shutdown =========="
log "APP_DIR: $APP_DIR"

if [ ! -f "$PID_FILE" ]; then
    log "No PID file found. Trading Hydra may not be running."
    # Only kill orphans matching our specific app directory
    pkill -f "$APP_DIR/main.py" 2>/dev/null && log "Killed orphan processes for $APP_DIR"
    exit 0
fi

PID=$(cat "$PID_FILE")

if [ -z "$PID" ]; then
    log "PID file is empty. Removing."
    rm -f "$PID_FILE"
    exit 0
fi

if ! kill -0 "$PID" 2>/dev/null; then
    log "Process $PID not running. Cleaning up PID file."
    rm -f "$PID_FILE"
    exit 0
fi

log "Sending SIGTERM to Trading Hydra (PID: $PID)..."
kill -TERM "$PID"

WAITED=0
while kill -0 "$PID" 2>/dev/null && [ $WAITED -lt $TIMEOUT ]; do
    sleep 1
    WAITED=$((WAITED + 1))
    if [ $((WAITED % 5)) -eq 0 ]; then
        log "Waiting for graceful shutdown... ($WAITED/$TIMEOUT seconds)"
    fi
done

if kill -0 "$PID" 2>/dev/null; then
    log "Process did not stop gracefully. Sending SIGKILL..."
    kill -9 "$PID"
    sleep 1
fi

rm -f "$PID_FILE"
log "Trading Hydra stopped."
exit 0
