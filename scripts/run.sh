#!/usr/bin/env bash
#
# Trading Hydra - Manual Run Script
#
# Usage:
#   bash scripts/run.sh              # Run fullbot mode
#   bash scripts/run.sh --paper      # Force paper trading
#   bash scripts/run.sh --dry-run    # Dry run (no orders)
#
# This is for development/testing. In production, use:
#   systemctl start hydra-fullbot
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Check for venv
if [ -d "$PROJECT_DIR/.venv" ]; then
  PYTHON="$PROJECT_DIR/.venv/bin/python"
elif [ -d "/opt/trading-hydra/.venv" ]; then
  PYTHON="/opt/trading-hydra/.venv/bin/python"
  PROJECT_DIR="/opt/trading-hydra"
else
  PYTHON="python3"
fi

# Load .env if exists
if [ -f "$PROJECT_DIR/.env" ]; then
  set -a
  source "$PROJECT_DIR/.env"
  set +a
fi

# Default to fullbot mode
export BOT_ROLE="${BOT_ROLE:-fullbot}"

# Default to paper trading for safety
export ALPACA_PAPER="${ALPACA_PAPER:-true}"

# Parse args
for arg in "$@"; do
  case $arg in
    --paper)
      export ALPACA_PAPER=true
      echo "[INFO] Forcing paper trading mode"
      ;;
    --live)
      export ALPACA_PAPER=false
      echo "[WARN] Live trading mode enabled!"
      ;;
    --dry-run)
      export DRY_RUN=true
      echo "[INFO] Dry run mode - no orders will be placed"
      ;;
  esac
done

echo ""
echo "=========================================="
echo "  Trading Hydra - Manual Run"
echo "=========================================="
echo "  Mode:   $BOT_ROLE"
echo "  Paper:  $ALPACA_PAPER"
echo "  Python: $PYTHON"
echo "=========================================="
echo ""

cd "$PROJECT_DIR"
exec "$PYTHON" main.py "$@"
