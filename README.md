# Trading Hydra

An autonomous Python-based trading system for institutional-grade systematic trend-following across stocks, options, and cryptocurrency markets. Features fail-closed safety patterns, comprehensive audit trails, and deterministic rule-based execution.

## Features

### Multi-Strategy Trading Bots
- **MomentumBot**: Turtle Traders breakout strategy for stocks
- **OptionsBot**: Buy-side options with PDF rules-based Strategy System (includes 0DTE mode for SPY/QQQ)
- **TwentyMinuteBot**: Opening window gap trading (first 20 minutes)
- **CryptoBot**: 24/7 cryptocurrency with dynamic universe selection

### Core Infrastructure
- **5-Step Trading Loop**: Initialize, ExitBot, PortfolioBot, Execution, Finalize
- **Fail-Closed Safety**: System halts and protects capital on any error
- **Config-Driven**: All parameters in YAML, no code changes for tuning
- **Durable State**: SQLite persistence across restarts
- **Structured Logging**: JSONL format with automatic rotation
- **Web Dashboard**: Flask-based real-time monitoring and control

### Risk Management
- **ExitBot**: Position monitoring, trailing stops, daily P&L limits
- **PortfolioBot**: Budget allocation across bots
- **ML Trade Scoring**: GradientBoostingClassifier for pre-trade filtering
- **Kelly Criterion Sizing**: Institutional-grade position sizing
- **Correlation Management**: Blocks trades when correlation too high
- **Market Regime Detection**: VIX-based volatility adaptation

### Strategy System (OptionsBot)
- **10 Deterministic Strategies**: Bullish/Bearish Bursts with earnings variants
- **5-Gate Pipeline**: Kill-Switch, Earnings Filter, Signal Rules, Backtest Gate, Contract Selector
- **Per-Strategy Drawdown Limits**: Automatic cooloff on excessive losses

## Project Structure

```
trading-hydra/
├── main.py                      # Entry point
├── config/
│   ├── settings.yaml            # System settings
│   ├── bots.yaml                # Bot configurations
│   └── strategies/              # Strategy System YAMLs (10 files)
├── src/trading_hydra/
│   ├── orchestrator.py          # Main 5-step loop
│   ├── bots/
│   │   ├── momentum_bot.py      # Stock momentum (Turtle Traders)
│   │   ├── options_bot.py       # Options + Strategy System + 0DTE mode
│   │   ├── twenty_minute_bot.py # Opening window trading
│   │   └── crypto_bot.py        # 24/7 cryptocurrency
│   ├── services/
│   │   ├── alpaca_client.py     # Alpaca API integration
│   │   ├── exitbot.py           # Safety and position management
│   │   ├── portfolio.py         # Budget allocation
│   │   ├── execution.py         # Order execution
│   │   ├── decision_tracker.py  # Audit trail
│   │   └── market_regime.py     # VIX regime detection
│   ├── strategy/                # PDF Rules-Based Trading
│   │   ├── registry.py          # Strategy loader
│   │   ├── validator.py         # Signal rule evaluation
│   │   ├── backtest_gate.py     # Historical performance check
│   │   ├── options_selector.py  # Contract selection
│   │   ├── earnings_filter.py   # Earnings blackout
│   │   ├── kill_switch.py       # Per-strategy circuit breaker
│   │   └── runner.py            # Pipeline orchestrator
│   ├── ml/
│   │   ├── signal_service.py    # ML trade scoring
│   │   └── feature_extractor.py # Technical indicators
│   ├── risk/
│   │   ├── position_sizer.py    # Kelly criterion sizing
│   │   └── correlation_manager.py
│   └── dashboard/
│       └── app.py               # Flask web interface
├── state/
│   ├── trading_state.db         # SQLite state
│   └── metrics.db               # Performance metrics
├── logs/
│   ├── app.jsonl                # Application logs
│   └── decision_records.jsonl   # Audit trail
├── models/                      # Trained ML models
└── docs/
    ├── TRADING_HYDRA_SOP.md     # Standard Operating Procedure
    └── TRADING_HYDRA_FM.md      # Field Manual (comprehensive)
```

## Quick Start

### Prerequisites
- Python 3.11+
- Alpaca Markets API credentials

### Environment Variables
```bash
export ALPACA_KEY="your_alpaca_api_key"
export ALPACA_SECRET="your_alpaca_api_secret"
export ALPACA_PAPER="true"  # Set to "false" for live trading
```

### Installation
```bash
pip install -r requirements.txt
```

### Running
```bash
# Start the trading system (full mode, default)
python3 main.py

# Fresh start (reset all state)
python3 main.py --fresh-start
```

### Multi-Container Deployment (Proxmox LXC)

For distributed deployment across multiple containers, use the `--role` flag:

```bash
# Role: all (default) - Full trading loop, same as `python3 main.py`
python3 main.py --role all

# Role: marketdata - Only market data collection/snapshot publishing
python3 main.py --role marketdata --no-dashboard

# Role: strategy - Only signal generation (NO broker orders - SAFE)
python3 main.py --role strategy --no-dashboard

# Role: execution - Only intent consumption and order placement
python3 main.py --role execution --no-dashboard

# Role: exit - Only exit/position management (stops/TPs/close logic)
python3 main.py --role exit --no-dashboard
```

**Environment Variable Overrides:**
```bash
# Using env vars instead of CLI flags
HYDRA_ROLE=strategy HYDRA_NO_DASHBOARD=1 python3 main.py

# Systemd unit file example:
# Environment="HYDRA_ROLE=marketdata"
# Environment="HYDRA_NO_DASHBOARD=1"
# ExecStart=/usr/bin/python3 /opt/trading-hydra/main.py
```

**Role Safety Rules:**
- `strategy` role CANNOT place orders (safe for separate container)
- `execution` role is the ONLY one that submits entry orders
- `exit` role can submit exit orders to close positions

**Hub MySQL Configuration (Optional):**

When deploying across multiple containers, bots communicate via a shared MySQL database:

```bash
# Set these env vars to enable hub communication
export HUB_DB_HOST=192.168.8.90
export HUB_DB_PORT=3306
export HUB_DB_NAME=hydra
export HUB_DB_USER=hydra
export HUB_DB_PASS=CHANGE_ME_NOW

# Then run any role - it will auto-connect to hub
HYDRA_ROLE=strategy HYDRA_NO_DASHBOARD=1 python3 main.py
```

Hub features:
- `marketdata` writes snapshots to `market_snapshots` table
- `strategy` writes intents to `trade_intents` table with idempotency
- `execution` leases intents with safe locking (SKIP LOCKED)
- `exit` syncs positions and enforces kill-switches via rolling PnL

### Web Dashboard
Access at http://localhost:5000 for:
- Real-time equity and P&L monitoring
- Bot enable/disable toggles
- Manual trading controls
- Configuration editor
- Performance analytics

## Configuration

### File: `config/settings.yaml`
Global system settings including:
- `runner.loop_interval_seconds`: Trading loop frequency (default: 5)
- `risk.global_max_daily_loss_pct`: Maximum daily loss before halt (default: 2.0)
- `ml.min_probability`: Minimum ML score to trade (default: 0.58)

### File: `config/bots.yaml`
Per-bot configurations including:
- Session windows (when each bot trades)
- Risk limits (max trades, position sizes)
- Strategy parameters

### File: `config/strategies/*.yaml`
Strategy System definitions (10 files):
- `bullish_bursts.yaml` / `bearish_bursts.yaml`
- Earnings variants: `*_no_earnings`, `*_only_earnings`, `*_pre_earnings`, `*_post_earnings`

## Safety Controls

### Automatic Halts
- Daily P&L limit exceeded
- API/broker failures
- Stale market data (>15 seconds)
- ML anomaly detection
- Per-strategy kill-switch activation

### Emergency Stop
```yaml
# config/settings.yaml
trading:
  global_halt: true  # Stops all new trades immediately
```

### Recovery
```bash
# View logs
tail -f logs/app.jsonl | jq .

# Check state
sqlite3 state/trading_state.db ".tables"

# Resume after halt
# Wait for cooloff period (390 minutes) or clear halt via dashboard
```

## Testing
```bash
# Run all tests
python -m pytest tests/ -v

# Run QC tests
python run_qc_tests.py

# Test Alpaca connection
python test_alpaca_connection.py
```

## Documentation

- **[SOP](docs/TRADING_HYDRA_SOP.md)**: Quick reference for operators
- **[Field Manual](docs/TRADING_HYDRA_FM.md)**: Comprehensive technical reference

## Legal Disclaimer

This software is for educational and research purposes. Trading involves substantial risk of loss. The authors are not responsible for any financial losses. Always test strategies thoroughly in paper trading before deploying capital.

---

**Built for Replit | Fail-Closed Safety | Trade Responsibly**
