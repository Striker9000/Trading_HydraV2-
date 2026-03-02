#!/bin/bash
# ============================================================================
# Trading Hydra - Optimize & Start
# ============================================================================
# Single command that:
#   1. Fetches/caches market data from Alpaca
#   2. Runs the dynamic parameter optimizer (5000+ combos, auto-doubling)
#   3. Applies the optimized config to bots.yaml / optimized_settings.yaml
#   4. Starts the trading bot with the new optimized parameters
#
# Usage:
#   ./scripts/optimize_and_start.sh                    # Full optimize + start
#   ./scripts/optimize_and_start.sh --optimize-only    # Optimize only, don't start
#   ./scripts/optimize_and_start.sh --start-only       # Skip optimize, just start
#   ./scripts/optimize_and_start.sh --combos 10000     # Custom combo count
#   ./scripts/optimize_and_start.sh --days 90          # Custom backtest period
#   ./scripts/optimize_and_start.sh --tier tier2       # Use balanced tier
#   ./scripts/optimize_and_start.sh --refresh-cache    # Force re-fetch data
#   ./scripts/optimize_and_start.sh --foreground       # Run bot in foreground
#
# Environment:
#   Requires APCA_API_KEY_ID and APCA_API_SECRET_KEY in .env or environment
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
OPTIMIZER="$SCRIPT_DIR/run_dynamic_optimizer.py"
LOG_DIR="$APP_DIR/logs"
RESULTS_DIR="$APP_DIR/results"

mkdir -p "$LOG_DIR" "$RESULTS_DIR" "$APP_DIR/data"

# ── Defaults ──────────────────────────────────────────────────────────────
COMBOS=5000
DAYS=60
TIER="tier1"
AUTO_DOUBLE="--auto-double"
OPTIMIZE=true
START=true
FOREGROUND=false
REFRESH_CACHE=""
EXTRA_ARGS=""

# ── Parse Arguments ───────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        --optimize-only)
            START=false; shift ;;
        --start-only)
            OPTIMIZE=false; shift ;;
        --combos)
            COMBOS="$2"; shift 2 ;;
        --days)
            DAYS="$2"; shift 2 ;;
        --tier)
            TIER="$2"; shift 2 ;;
        --no-auto-double)
            AUTO_DOUBLE=""; shift ;;
        --refresh-cache)
            REFRESH_CACHE="--refresh-cache"; shift ;;
        --foreground|-f)
            FOREGROUND=true; shift ;;
        *)
            EXTRA_ARGS="$EXTRA_ARGS $1"; shift ;;
    esac
done

# ── Logging ───────────────────────────────────────────────────────────────
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
OPT_LOG="$LOG_DIR/optimizer_${TIMESTAMP}.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$OPT_LOG"
}

# ── Environment ───────────────────────────────────────────────────────────
cd "$APP_DIR"

if [ -f ".env" ]; then
    log "Loading .env file..."
    set -a
    source .env
    set +a
fi

# Check for Python
PYTHON=""
if command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
else
    log "ERROR: Python not found. Install Python 3.8+ first."
    exit 1
fi

# Check for venv
if [ -d "venv" ]; then
    log "Activating virtual environment..."
    source venv/bin/activate
elif [ -d ".venv" ]; then
    log "Activating virtual environment..."
    source .venv/bin/activate
fi

# Verify tqdm is available
if ! $PYTHON -c "import tqdm" 2>/dev/null; then
    log "Installing tqdm..."
    $PYTHON -m pip install tqdm -q
fi

# ── Banner ────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║              TRADING HYDRA - OPTIMIZE & START                      ║"
echo "╠══════════════════════════════════════════════════════════════════════╣"
echo "║  Combos:      $COMBOS per bot per regime"
echo "║  Days:        $DAYS day backtest period"
echo "║  Tier:        $TIER"
echo "║  Auto-Double: $([ -n "$AUTO_DOUBLE" ] && echo 'YES' || echo 'NO')"
echo "║  Optimize:    $([ "$OPTIMIZE" = true ] && echo 'YES' || echo 'SKIP')"
echo "║  Start Bot:   $([ "$START" = true ] && echo 'YES' || echo 'SKIP')"
echo "║  Cache:       $([ -n "$REFRESH_CACHE" ] && echo 'REFRESH' || echo 'USE IF AVAILABLE')"
echo "║  Log:         $OPT_LOG"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""

# ── Step 1: Optimize ─────────────────────────────────────────────────────
if [ "$OPTIMIZE" = true ]; then
    log "═══ STEP 1: RUNNING DYNAMIC PARAMETER OPTIMIZER ═══"
    log "Bots: momentum, crypto, whipsaw, bouncebot, twentyminute, hailmary"
    log "Exit Systems: ProfitSniper + ExitBot"
    log "Regimes: LOW, NORMAL, STRESS"
    log "Combos: $COMBOS (auto-double: $([ -n "$AUTO_DOUBLE" ] && echo 'on' || echo 'off'))"
    echo ""

    OPT_CMD="$PYTHON $OPTIMIZER --combos $COMBOS --days $DAYS --tier $TIER $AUTO_DOUBLE $REFRESH_CACHE $EXTRA_ARGS"
    log "Command: $OPT_CMD"
    echo ""

    OPT_START=$(date +%s)

    if $OPT_CMD 2>&1 | tee -a "$OPT_LOG"; then
        OPT_END=$(date +%s)
        OPT_ELAPSED=$((OPT_END - OPT_START))
        OPT_MINS=$((OPT_ELAPSED / 60))
        OPT_SECS=$((OPT_ELAPSED % 60))
        log "═══ OPTIMIZATION COMPLETE (${OPT_MINS}m ${OPT_SECS}s) ═══"
        echo ""

        if [ -f "$RESULTS_DIR/dynamic_optimizer_results.json" ]; then
            log "Results saved to: $RESULTS_DIR/dynamic_optimizer_results.json"
        fi
        if [ -f "$APP_DIR/config/optimized_settings.yaml" ]; then
            log "Optimized config saved to: config/optimized_settings.yaml"
        fi
    else
        log "ERROR: Optimizer failed. Check $OPT_LOG for details."
        exit 1
    fi
else
    log "═══ SKIPPING OPTIMIZATION (--start-only) ═══"
fi

# ── Step 2: Start Bot ────────────────────────────────────────────────────
if [ "$START" = true ]; then
    echo ""
    log "═══ STEP 2: STARTING TRADING BOT ═══"

    if [ -f "$APP_DIR/config/optimized_settings.yaml" ]; then
        log "Using optimized config: config/optimized_settings.yaml"
    else
        log "WARNING: No optimized config found. Using default settings."
    fi

    if [ -f "$SCRIPT_DIR/start-trading.sh" ]; then
        if [ "$FOREGROUND" = true ]; then
            log "Starting in foreground mode..."
            exec "$SCRIPT_DIR/start-trading.sh" --foreground
        else
            log "Starting in background mode..."
            "$SCRIPT_DIR/start-trading.sh"
        fi
    elif [ -f "$APP_DIR/start-trading.sh" ]; then
        if [ "$FOREGROUND" = true ]; then
            log "Starting in foreground mode..."
            exec "$APP_DIR/start-trading.sh" --foreground
        else
            log "Starting in background mode..."
            "$APP_DIR/start-trading.sh"
        fi
    elif [ -f "$APP_DIR/main.py" ]; then
        log "Starting via main.py..."
        if [ "$FOREGROUND" = true ]; then
            exec $PYTHON "$APP_DIR/main.py" --paper
        else
            nohup $PYTHON "$APP_DIR/main.py" --paper > "$LOG_DIR/trading_${TIMESTAMP}.log" 2>&1 &
            BOT_PID=$!
            echo "$BOT_PID" > "$APP_DIR/trading-hydra.pid"
            log "Bot started (PID: $BOT_PID)"
            log "Log: $LOG_DIR/trading_${TIMESTAMP}.log"
        fi
    else
        log "ERROR: No start script or main.py found."
        exit 1
    fi
else
    log "═══ SKIPPING BOT START (--optimize-only) ═══"
fi

echo ""
log "═══ ALL DONE ═══"
echo ""
