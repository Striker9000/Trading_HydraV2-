#!/usr/bin/env bash
#
# Trading Hydra - Uninstaller
#
# Usage:
#   sudo bash scripts/uninstall.sh
#
# Options:
#   --keep-data    Keep state and log directories
#   --keep-user    Keep hydra user/group
#
set -euo pipefail

APP_DIR="/opt/trading-hydra"
STATE_DIR="/var/lib/trading-hydra"
LOG_DIR="/var/log/trading-hydra"
USER="hydra"
GROUP="hydra"

KEEP_DATA=false
KEEP_USER=false

# Parse args
for arg in "$@"; do
  case $arg in
    --keep-data) KEEP_DATA=true ;;
    --keep-user) KEEP_USER=true ;;
  esac
done

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

# Check root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}[ERROR]${NC} This script must be run as root (use sudo)"
   exit 1
fi

echo ""
echo "=========================================="
echo "  Trading Hydra Uninstaller"
echo "=========================================="
echo ""

echo "[1/4] Stopping service..."
if systemctl is-active --quiet hydra-fullbot 2>/dev/null; then
  systemctl stop hydra-fullbot
  log_info "Service stopped"
else
  log_info "Service not running"
fi

echo "[2/4] Removing systemd service..."
if [ -f /etc/systemd/system/hydra-fullbot.service ]; then
  systemctl disable hydra-fullbot 2>/dev/null || true
  rm -f /etc/systemd/system/hydra-fullbot.service
  systemctl daemon-reload
  log_info "Systemd service removed"
else
  log_info "Systemd service not found"
fi

echo "[3/4] Removing application files..."
if [ -d "$APP_DIR" ]; then
  rm -rf "$APP_DIR"
  log_info "Application directory removed: $APP_DIR"
else
  log_info "Application directory not found"
fi

if [ "$KEEP_DATA" = false ]; then
  if [ -d "$STATE_DIR" ]; then
    rm -rf "$STATE_DIR"
    log_info "State directory removed: $STATE_DIR"
  fi
  if [ -d "$LOG_DIR" ]; then
    rm -rf "$LOG_DIR"
    log_info "Log directory removed: $LOG_DIR"
  fi
else
  log_warn "Keeping data directories (--keep-data)"
fi

echo "[4/4] Removing user/group..."
if [ "$KEEP_USER" = false ]; then
  if id "$USER" >/dev/null 2>&1; then
    userdel "$USER" 2>/dev/null || true
    log_info "User '$USER' removed"
  fi
  if getent group "$GROUP" >/dev/null 2>&1; then
    groupdel "$GROUP" 2>/dev/null || true
    log_info "Group '$GROUP' removed"
  fi
else
  log_warn "Keeping user/group (--keep-user)"
fi

echo ""
echo "=========================================="
echo "  Uninstall Complete!"
echo "=========================================="
echo ""
