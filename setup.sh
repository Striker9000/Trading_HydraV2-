#!/bin/bash
# Trading Hydra Setup Script for Linux/macOS
# Usage: ./setup.sh [--with-autostart]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WITH_AUTOSTART=false

for arg in "$@"; do
    case $arg in
        --with-autostart)
            WITH_AUTOSTART=true
            shift
            ;;
    esac
done

echo "==================================="
echo "  Trading Hydra Setup Script"
echo "==================================="
echo ""

# Check Python version
PYTHON_CMD=""
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo "ERROR: Python is not installed. Please install Python 3.9+ first."
    exit 1
fi

PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1-2)
echo "Found Python: $PYTHON_VERSION"

# Remove old venv if it exists and has issues
if [ -d "venv" ]; then
    if grep -q "C:\\\\" venv/pyvenv.cfg 2>/dev/null; then
        echo "Removing corrupted venv (Windows paths detected)..."
        rm -rf venv
    fi
fi

# Create virtual environment
echo ""
echo "Creating virtual environment..."
$PYTHON_CMD -m venv venv --clear

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Upgrade pip inside venv
echo "Upgrading pip..."
venv/bin/pip install --upgrade pip

# Install dependencies inside venv
echo ""
echo "Installing dependencies..."
venv/bin/pip install -r requirements.txt

# Create necessary directories
echo ""
echo "Creating directories..."
mkdir -p logs
mkdir -p state
mkdir -p state/backups
mkdir -p cache

# Make scripts executable
chmod +x scripts/*.sh 2>/dev/null || true

# Copy wrapper scripts to app root for convenience
cp scripts/start-trading.sh . 2>/dev/null || true
cp scripts/stop-trading.sh . 2>/dev/null || true
cp scripts/healthcheck.sh . 2>/dev/null || true
chmod +x start-trading.sh stop-trading.sh healthcheck.sh 2>/dev/null || true

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo ""
    echo "Creating .env file template..."
    cat > .env << 'ENVEOF'
# Alpaca API Credentials
# Get your keys from: https://app.alpaca.markets/
ALPACA_KEY=your_api_key_here
ALPACA_SECRET=your_api_secret_here
ALPACA_PAPER=true

# Optional: OpenAI for AI features (sentiment analysis, etc.)
# OPENAI_API_KEY=your_openai_key_here

# Optional: Set to false for live trading (use with caution!)
# ALPACA_PAPER=false
ENVEOF
    echo "IMPORTANT: Edit .env file with your Alpaca API credentials!"
fi

echo ""
echo "==================================="
echo "  Basic Setup Complete!"
echo "==================================="

# Auto-start configuration (Linux only)
if [ "$WITH_AUTOSTART" = true ]; then
    echo ""
    echo "==================================="
    echo "  Configuring Auto-Start..."
    echo "==================================="
    
    if [ "$(id -u)" -ne 0 ]; then
        echo "ERROR: Auto-start configuration requires root privileges."
        echo "Run: sudo ./setup.sh --with-autostart"
        exit 1
    fi
    
    if ! command -v systemctl &> /dev/null; then
        echo "ERROR: systemd not found. Auto-start requires systemd."
        exit 1
    fi
    
    # Create systemd service
    echo "Creating systemd service..."
    cat > /etc/systemd/system/trading-hydra.service << EOF
[Unit]
Description=Trading Hydra Autonomous Trading System
After=network.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=root
WorkingDirectory=$SCRIPT_DIR
ExecStartPre=/bin/sleep 10
ExecStart=$SCRIPT_DIR/venv/bin/python $SCRIPT_DIR/main.py --inplace
Restart=on-failure
RestartSec=30
Environment="PATH=$SCRIPT_DIR/venv/bin:/usr/local/bin:/usr/bin:/bin"
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    
    # Create getty override for console auto-login
    echo "Configuring console auto-login..."
    mkdir -p /etc/systemd/system/getty@tty1.service.d
    cat > /etc/systemd/system/getty@tty1.service.d/override.conf << 'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noclear %I $TERM
EOF
    
    # Add startup script to .bash_profile
    echo "Configuring console startup..."
    BASH_PROFILE="/root/.bash_profile"
    STARTUP_MARKER="# Trading Hydra Auto-Start"
    
    if ! grep -q "$STARTUP_MARKER" "$BASH_PROFILE" 2>/dev/null; then
        cat >> "$BASH_PROFILE" << EOF

$STARTUP_MARKER
if [ "\$(tty)" = "/dev/tty1" ]; then
    echo "Starting Trading Hydra in 10 seconds... (Ctrl+C to cancel)"
    sleep 10
    cd $SCRIPT_DIR
    ./start-trading.sh
    # Keep the console open with logs
    tail -f logs/app.jsonl 2>/dev/null || tail -f logs/app.log 2>/dev/null || bash
fi
EOF
        echo "Added startup script to $BASH_PROFILE"
    else
        echo "Startup script already in $BASH_PROFILE"
    fi
    
    # Add cron jobs for market hours (PST)
    echo "Configuring cron jobs for market hours..."
    CRON_MARKER="# Trading Hydra Market Hours"
    CURRENT_CRON=$(crontab -l 2>/dev/null || echo "")
    
    if ! echo "$CURRENT_CRON" | grep -q "$CRON_MARKER"; then
        (echo "$CURRENT_CRON"; cat << EOF

$CRON_MARKER
# Start Trading Hydra 30 mins before market open (6:00am PST, Mon-Fri)
0 6 * * 1-5 $SCRIPT_DIR/start-trading.sh >> $SCRIPT_DIR/logs/cron.log 2>&1

# Stop Trading Hydra after market close (1:15pm PST, Mon-Fri)
15 13 * * 1-5 $SCRIPT_DIR/stop-trading.sh >> $SCRIPT_DIR/logs/cron.log 2>&1

# Health check every 15 minutes during market hours
*/15 6-13 * * 1-5 $SCRIPT_DIR/healthcheck.sh restart >> $SCRIPT_DIR/logs/healthcheck.log 2>&1
EOF
        ) | crontab -
        echo "Added cron jobs for market hours"
    else
        echo "Cron jobs already configured"
    fi
    
    # Reload systemd
    echo "Reloading systemd..."
    systemctl daemon-reload
    systemctl enable trading-hydra
    
    echo ""
    echo "==================================="
    echo "  Auto-Start Configuration Complete!"
    echo "==================================="
    echo ""
    echo "System will now:"
    echo "  - Auto-login to console on boot"
    echo "  - Start Trading Hydra after 10 second delay"
    echo "  - Display output on physical console (tty1)"
    echo "  - Auto-restart if app crashes"
    echo "  - Start at 6:00am PST on weekdays (30 mins before market)"
    echo "  - Stop at 1:15pm PST on weekdays (45 mins after close)"
    echo "  - Health check every 15 minutes during market hours"
    echo ""
    echo "Commands:"
    echo "  ./start-trading.sh   - Manual start (won't duplicate)"
    echo "  ./stop-trading.sh    - Graceful stop"
    echo "  ./healthcheck.sh     - Check if running"
    echo ""
    echo "Logs:"
    echo "  tail -f logs/app.jsonl       - Trading logs"
    echo "  journalctl -u trading-hydra  - Service logs"
    echo ""
else
    echo ""
    echo "To run Trading Hydra:"
    echo "  1. Edit .env with your Alpaca API credentials"
    echo "  2. Activate the virtual environment:"
    echo "     source venv/bin/activate"
    echo "  3. Start the system:"
    echo "     python main.py"
    echo ""
    echo "Quick start commands:"
    echo "  python main.py --fresh-start   # Reset state for new account"
    echo "  python main.py --inplace       # Run in foreground"
    echo ""
    echo "For auto-start on boot (Linux with systemd):"
    echo "  sudo ./setup.sh --with-autostart"
    echo ""
fi

echo "Dashboard will be available at: http://localhost:5000"
echo ""
