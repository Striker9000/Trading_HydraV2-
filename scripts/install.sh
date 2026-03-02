#!/usr/bin/env bash
#
# Trading Hydra - One-Command Installer
#
# Usage:
#   unzip Trading_Hydra_Fullbot.zip
#   cd trading-hydra
#   sudo bash scripts/install.sh
#
# This script:
#   1. Installs OS dependencies
#   2. Creates hydra user/group
#   3. Sets up directories
#   4. Creates Python venv and installs deps
#   5. Installs systemd service
#   6. Starts the bot
#
set -euo pipefail

APP_DIR="/opt/trading-hydra"
STATE_DIR="/var/lib/trading-hydra"
LOG_DIR="/var/log/trading-hydra"
USER="hydra"
GROUP="hydra"

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Check root
if [[ $EUID -ne 0 ]]; then
   log_error "This script must be run as root (use sudo)"
   exit 1
fi

echo ""
echo "=========================================="
echo "  Trading Hydra Fullbot Installer"
echo "=========================================="
echo ""

echo "[1/9] Installing OS dependencies..."
apt-get update -y
apt-get install -y python3 python3-venv python3-pip rsync ca-certificates curl sqlite3 jq

echo "[2/9] Creating user/group..."
getent group "$GROUP" >/dev/null || groupadd --system "$GROUP"
id "$USER" >/dev/null 2>&1 || useradd --system --home "$STATE_DIR" --shell /usr/sbin/nologin -g "$GROUP" "$USER"
log_info "User '$USER' ready"

echo "[3/9] Creating directories..."
mkdir -p "$APP_DIR" "$STATE_DIR" "$LOG_DIR"
chown -R "$USER:$GROUP" "$STATE_DIR" "$LOG_DIR"
log_info "Directories created"

echo "[4/9] Copying application files..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(dirname "$SCRIPT_DIR")"

rsync -a --delete \
  --exclude=".git" \
  --exclude="__pycache__" \
  --exclude="*.pyc" \
  --exclude=".venv" \
  --exclude=".env" \
  "$SOURCE_DIR"/ "$APP_DIR"/

chown -R "$USER:$GROUP" "$APP_DIR"
log_info "Application files copied to $APP_DIR"

echo "[5/9] Creating virtual environment..."
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip wheel --quiet
log_info "Virtual environment created"

echo "[6/9] Installing Python dependencies..."
if [ -f "$APP_DIR/requirements.txt" ]; then
  log_info "Found requirements.txt"
  "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet
elif [ -f "$APP_DIR/pyproject.toml" ]; then
  log_info "Found pyproject.toml"
  "$APP_DIR/.venv/bin/pip" install "$APP_DIR" --quiet
else
  log_error "Missing requirements.txt and pyproject.toml"
  exit 1
fi
log_info "Python dependencies installed"

echo "[7/9] Setting up configuration..."
# Create .env from example if not exists
if [ ! -f "$APP_DIR/.env" ]; then
  if [ -f "$APP_DIR/.env.example" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    chown "$USER:$GROUP" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    log_warn "Created $APP_DIR/.env from example - EDIT THIS FILE WITH YOUR CREDENTIALS"
  else
    log_warn "No .env.example found - create $APP_DIR/.env manually"
  fi
else
  log_info "Using existing $APP_DIR/.env"
fi

# Verify config exists
if [ ! -f "$APP_DIR/config/fullbot.yaml" ] && [ ! -f "$APP_DIR/config/settings.yaml" ]; then
  log_warn "No config file found - bot may use defaults"
fi

echo "[8/9] Installing systemd service..."
if [ -f "$APP_DIR/systemd/hydra-fullbot.service" ]; then
  cp "$APP_DIR/systemd/hydra-fullbot.service" /etc/systemd/system/hydra-fullbot.service
  systemctl daemon-reload
  systemctl enable hydra-fullbot
  log_info "Systemd service installed and enabled"
else
  log_error "Missing systemd/hydra-fullbot.service"
  exit 1
fi

echo "[9/9] Starting service..."
systemctl restart hydra-fullbot

# Wait a moment for startup
sleep 2

if systemctl is-active --quiet hydra-fullbot; then
  log_info "Service started successfully"
else
  log_warn "Service may have failed to start - check logs"
fi

systemctl status hydra-fullbot --no-pager || true

echo ""
echo "=========================================="
echo "  Installation Complete!"
echo "=========================================="
echo ""
echo "  Service:  hydra-fullbot"
echo "  Status:   systemctl status hydra-fullbot"
echo "  Logs:     journalctl -u hydra-fullbot -f"
echo "            tail -f $LOG_DIR/fullbot.log"
echo ""
echo "  Config:   $APP_DIR/.env"
echo "            $APP_DIR/config/"
echo ""
echo "  IMPORTANT: Edit $APP_DIR/.env with your Alpaca credentials!"
echo ""
