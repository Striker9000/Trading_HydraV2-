# Trading Hydra - systemd Deployment Guide

Production-grade systemd service configuration with auto-restart, logging, and weekday guard.

## Prerequisites

- Linux server (Ubuntu/Debian recommended)
- Python 3.10+
- Non-root user `trader` (or adjust service file)
- Bot installed in `/opt/trading_hydra` (or adjust paths)

## 1. Create the systemd Service File

```bash
sudo nano /etc/systemd/system/trading-bot.service
```

Paste this configuration:

```ini
[Unit]
Description=Trading Hydra Bot
After=network.target
Wants=network.target

[Service]
Type=simple
User=trader
Group=trader
WorkingDirectory=/opt/trading_hydra

# Use weekday guard for market-day-only operation
ExecStart=/opt/trading_hydra/scripts/weekday_guard.sh

# Restart behavior - on-failure prevents weekend restart loops
# (weekday_guard.sh exits 0 on weekends = clean exit, no restart)
Restart=on-failure
RestartSec=10
StartLimitIntervalSec=300
StartLimitBurst=5

# Environment hardening
Environment=PYTHONUNBUFFERED=1
Environment=TZ=America/Los_Angeles

# Logging
StandardOutput=append:/var/log/trading_hydra/bot.out
StandardError=append:/var/log/trading_hydra/bot.err

# Kill behavior
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

### Why This Configuration:

- `Restart=on-failure` → Bot crashes, systemd brings it back; clean exit (weekends) stays down
- `RestartSec=10` → Avoids restart loops
- `StartLimit*` → Protects against infinite crash storms
- Separate stdout/stderr logs for debugging

## 2. Create Log Directory

```bash
sudo mkdir -p /var/log/trading_hydra
sudo chown trader:trader /var/log/trading_hydra
```

## 3. Enable and Start the Service

```bash
sudo systemctl daemon-reexec
sudo systemctl daemon-reload
sudo systemctl enable trading-bot
sudo systemctl start trading-bot
```

## 4. Verify Operation

```bash
# Check status
systemctl status trading-bot

# View logs
tail -f /var/log/trading_hydra/bot.out

# Test auto-restart (kill the process, watch it come back)
pkill -f "python3 main.py"
# Wait 10 seconds, then check status again
```

## 5. Common Commands

```bash
# Start/stop/restart
sudo systemctl start trading-bot
sudo systemctl stop trading-bot
sudo systemctl restart trading-bot

# View service logs via journald
journalctl -u trading-bot -n 50
journalctl -u trading-bot -f  # Follow live

# Check if enabled at boot
systemctl is-enabled trading-bot
```

## 6. Environment Variables

The bot reads credentials from `.env` file in the working directory. Create it:

```bash
cp /opt/trading_hydra/.env.example /opt/trading_hydra/.env
chmod 600 /opt/trading_hydra/.env
nano /opt/trading_hydra/.env
```

Required variables:
```ini
ALPACA_KEY=your_api_key
ALPACA_SECRET=your_api_secret
ALPACA_PAPER=true
```

## 7. Log Rotation

Add logrotate configuration:

```bash
sudo nano /etc/logrotate.d/trading-hydra
```

```
/var/log/trading_hydra/*.out
/var/log/trading_hydra/*.err
{
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 trader trader
    sharedscripts
    postrotate
        systemctl restart trading-bot >/dev/null 2>&1 || true
    endscript
}
```

## 8. Monitoring Recommendations

- Set up alerts for service failures
- Monitor `/var/log/trading_hydra/bot.err` for errors
- Consider adding a healthcheck endpoint
- Wire HaltManager to systemd via `systemctl stop trading-bot`

## Troubleshooting

### Service won't start
```bash
journalctl -u trading-bot -n 100 --no-pager
```

### Permission denied
```bash
sudo chown -R trader:trader /opt/trading_hydra
sudo chmod +x /opt/trading_hydra/scripts/weekday_guard.sh
```

### 401 Unauthorized from Alpaca
- Check that ALPACA_KEY/ALPACA_SECRET match your ALPACA_PAPER setting
- Paper keys only work with paper endpoint
- Live keys only work with live endpoint
