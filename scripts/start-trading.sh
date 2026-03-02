#!/bin/bash
# Trading Hydra - Start Script with PID Locking
# Prevents duplicate instances and ensures clean startup
#
# Usage:
#   ./start-trading.sh              # Background mode (nohup, output to log file)
#   ./start-trading.sh --foreground # Interactive mode (console output, Ctrl+C to stop)
#   ./start-trading.sh -f           # Same as --foreground

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Detect APP_DIR: if main.py exists in SCRIPT_DIR, we're in the app root
# Otherwise we're in scripts/ subdirectory
if [ -f "$SCRIPT_DIR/main.py" ]; then
    APP_DIR="$SCRIPT_DIR"
else
    APP_DIR="$(dirname "$SCRIPT_DIR")"
fi

PID_FILE="$APP_DIR/trading-hydra.pid"
LOG_FILE="$APP_DIR/logs/startup.log"

# Parse arguments
FOREGROUND=false
for arg in "$@"; do
    case $arg in
        --foreground|-f)
            FOREGROUND=true
            shift
            ;;
    esac
done

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

cleanup_stale_pid() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if [ -n "$PID" ] && ! kill -0 "$PID" 2>/dev/null; then
            log "Removing stale PID file (process $PID not running)"
            rm -f "$PID_FILE"
        fi
    fi
}

mkdir -p "$APP_DIR/logs"

log "========== Trading Hydra Startup =========="
log "APP_DIR: $APP_DIR"

cleanup_stale_pid

if is_running; then
    PID=$(cat "$PID_FILE")
    log "Trading Hydra is already running (PID: $PID). Skipping start."
    exit 0
fi

cd "$APP_DIR" || exit 1

if [ ! -f "venv/bin/python" ]; then
    log "ERROR: Virtual environment not found. Run setup.sh first."
    exit 1
fi

if [ ! -f ".env" ]; then
    log "ERROR: .env file not found. Configure API credentials first."
    exit 1
fi

source venv/bin/activate

if [ "$FOREGROUND" = true ]; then
    # Foreground mode: run interactively with console output
    log "Starting Trading Hydra in FOREGROUND mode (Ctrl+C to stop)..."
    
    # Write PID file for this process
    echo "$$" > "$PID_FILE"
    log "Trading Hydra running with PID: $$"
    
    # Trap to clean up PID file on exit
    cleanup_on_exit() {
        log "Foreground mode exiting, cleaning up..."
        rm -f "$PID_FILE"
    }
    trap cleanup_on_exit EXIT
    
    # Run in foreground - console output visible, Ctrl+C works
    python main.py --inplace
    
else
    # Background mode: nohup with output to log file
    log "Starting Trading Hydra in background mode..."
    
    nohup python main.py --inplace >> "$APP_DIR/logs/app.log" 2>&1 &
    APP_PID=$!
    
    echo "$APP_PID" > "$PID_FILE"
    log "Trading Hydra started with PID: $APP_PID"
    
    sleep 2
    if is_running; then
        log "Startup confirmed - Trading Hydra is running"
        exit 0
    else
        log "ERROR: Trading Hydra failed to start"
        rm -f "$PID_FILE"
        exit 1
    fi
fi
