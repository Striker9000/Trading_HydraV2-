#!/bin/bash
# ============================================================================
# Trading Hydra - Quick Start (Lean Version)
# ============================================================================
# Lightweight startup: install deps, validate env, launch bot.
# No optimization step - just get the bot running with current config.
#
# Usage:
#   ./scripts/quick_start.sh              # Paper trading (default)
#   ./scripts/quick_start.sh --paper      # Explicit paper mode
#   ./scripts/quick_start.sh --live       # Live trading (careful!)
#   ./scripts/quick_start.sh --dry-run    # Dry run, no orders placed
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"

cd "$APP_DIR"

# ── Find Python ──────────────────────────────────────────────────────────
if [ -d "venv" ] && [ -f "venv/bin/python" ]; then
    PYTHON="venv/bin/python"
elif [ -d ".venv" ] && [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
else
    PYTHON="python"
fi

# ── Load .env ────────────────────────────────────────────────────────────
if [ -f ".env" ]; then
    set -a; source .env; set +a
fi

# ── Validate Alpaca keys ─────────────────────────────────────────────────
if [ -z "$APCA_API_KEY_ID" ] && [ -z "$ALPACA_API_KEY" ]; then
    echo "ERROR: No Alpaca API key found."
    echo "Set APCA_API_KEY_ID and APCA_API_SECRET_KEY in .env or environment."
    exit 1
fi

# ── Install deps if missing ──────────────────────────────────────────────
if ! $PYTHON -c "import flask" 2>/dev/null; then
    echo "[SETUP] Installing dependencies..."
    $PYTHON -m pip install -r requirements.txt -q
fi

# ── Default to paper trading ─────────────────────────────────────────────
export ALPACA_PAPER="${ALPACA_PAPER:-true}"

echo ""
echo "══════════════════════════════════════"
echo "  Trading Hydra - Quick Start"
echo "══════════════════════════════════════"
echo "  Paper: $ALPACA_PAPER"
echo "  Python: $PYTHON"
echo "══════════════════════════════════════"
echo ""

exec $PYTHON main.py "$@"
