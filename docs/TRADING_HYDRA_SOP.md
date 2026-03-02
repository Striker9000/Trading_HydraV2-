# Trading Hydra - Standard Operating Procedure (SOP)

## Table of Contents
1. [System Overview](#system-overview)
2. [Component Guide](#component-guide)
3. [Configuration Reference](#configuration-reference)
4. [What You Can Change (and Why)](#what-you-can-change-and-why)
5. [Safety Controls](#safety-controls)
6. [Daily Operations](#daily-operations)

---

## System Overview

Trading Hydra is an automated trading system that runs 4 specialized trading bots:
- **CryptoBot**: 24/7 cryptocurrency trading (long and short positions)
- **MomentumBot**: Stock momentum trading during market hours
- **OptionsBot**: Options trading (long calls, puts, straddles)
- **TwentyMinuteBot**: Opening-window pattern trading (first 20 minutes after market open)

### How to Start the System
```bash
python main.py
```

This starts:
1. The Flask web dashboard at http://localhost:5000
2. The 5-step trading loop running every 5 seconds

### Fresh Start (New Account)
```bash
python main.py --fresh-start
```
Use this when transferring to a new user or resetting the system.

---

## Component Guide

### 1. Orchestrator (`src/trading_hydra/orchestrator.py`)
**Purpose**: The brain of the system. Runs the 5-step trading loop.

| Step | Name | What It Does |
|------|------|--------------|
| 1 | Initialize | Connect to Alpaca, get account equity |
| 2 | ExitBot | Monitor positions, apply trailing stops, check P&L limits |
| 3 | PortfolioBot | Allocate daily risk budget to each bot |
| 4 | Execution | Run enabled bots to find and execute trades |
| 5 | Finalize | Log results, persist metrics |

### 2. ExitBot (`bots.yaml` → exitbot section)
**Purpose**: Safety guardian - monitors all positions and enforces risk limits.

**What It Does**:
- Applies trailing stops to lock in profits
- Monitors daily P&L limits
- Halts trading if API failures occur
- Manages both automated and manual positions

### 3. PortfolioBot (`bots.yaml` → portfoliobot section)
**Purpose**: Budget allocator - divides daily risk across bots.

**How It Works**:
- Takes global daily risk limit (e.g., 2% of equity)
- Splits it: 20% Momentum, 40% Options, 25% Crypto, 15% TwentyMinute
- Ensures no single bot can risk more than its allocation

### 4. CryptoBot (`bots.yaml` → cryptobot section)
**Purpose**: 24/7 cryptocurrency trading.

**Key Features**:
- Dynamic coin selection (screens 60+ coins, picks best 3)
- Supports long AND short positions
- Uses ML scoring to filter trades
- Trailing stops with 1.5% activation

### 5. MomentumBot (`bots.yaml` → momentum_bots section)
**Purpose**: Trades individual stocks on momentum signals.

**Key Features**:
- Currently configured for AAPL (TSLA disabled)
- Session-based trading (6:35 AM - 9:30 AM PST)
- Supports long, short, or both directions
- Trailing stops with 0.8% activation

### 6. OptionsBot (`bots.yaml` → optionsbot section)
**Purpose**: Options trading on AAPL, AMD, MSFT, NVDA, TSLA, PLTR, BLK.

**Key Features**:
- BUY-SIDE ONLY (long calls, long puts, straddles)
- Credit spreads disabled (require more margin)
- Session: 6:40 AM - 12:30 PM PST
- 30% profit target, 50% stop loss

**Strategy System** (PDF Rules-Based Trading):
When `use_strategy_system: true` in config:
- Loads deterministic strategies from `config/strategies/*.yaml`
- 10 strategies: Bullish Bursts, Bearish Bursts, and earnings-aware variants
- Each strategy passes through 5 enforcement gates:

| Gate | What It Checks |
|------|----------------|
| **Kill-Switch** | Per-strategy drawdown limit (default -$500 in 5 trades) |
| **Earnings Filter** | NEVER/ONLY/PRE/POST policies for earnings blackout |
| **Signal Rules** | Price vs EMA, RSI thresholds, volume conditions |
| **Backtest Gate** | Minimum 52% win rate and 0.5% return from backtest |
| **Contract Selector** | Delta 0.30-0.60, DTE 7-45, volume/OI requirements |

**Strategy Files** (`config/strategies/`):
- `bullish_bursts.yaml` - Base bullish momentum strategy
- `bullish_bursts_no_earnings.yaml` - Avoids earnings periods
- `bullish_bursts_only_earnings.yaml` - Only during earnings
- `bullish_bursts_pre_earnings.yaml` - Before earnings
- `bullish_bursts_post_earnings.yaml` - After earnings
- `bearish_bursts.yaml` - Base bearish momentum strategy
- `bearish_bursts_no_earnings.yaml` - Avoids earnings periods
- `bearish_bursts_only_earnings.yaml` - Only during earnings
- `bearish_bursts_pre_earnings.yaml` - Before earnings
- `bearish_bursts_post_earnings.yaml` - After earnings

### 7. TwentyMinuteBot (`bots.yaml` → twentyminute_bot section)
**Purpose**: Trades the first 20 minutes after market open based on Jeremy Russell's 20-Minute Trader strategy.

**Key Features**:
- **20-Minute Window**: Only trades from 6:30-6:50 AM PST (9:30-9:50 AM EST)
- **Gap Analysis**: Identifies overnight gaps and trades their resolution
- **Pattern Recognition**: Detects gap reversals, gap continuations, and first-bar breakouts
- **Quick Exits**: Max 15-minute hold time with tight 0.5% stop loss
- **ML Scoring**: Uses ML to filter low-probability patterns

**Patterns Detected**:
| Pattern | Description | Signal |
|---------|-------------|--------|
| Gap Reversal | Gap fills back toward previous close | Fade the gap |
| Gap Continuation | Gap extends in same direction | Ride momentum |
| First Bar Breakout | Price breaks first 5-min bar high/low | Breakout trade |

**When It Runs**:
- Pre-session (6:00-6:30 AM): Analyzes overnight gaps
- In-session (6:30-6:50 AM): Executes pattern trades
- Post-session (6:50-7:00 AM): Flattens remaining positions

### 8. ML Signal Service (`src/trading_hydra/ml/`)
**Purpose**: Predicts trade profitability before execution.

**Components**:
- `signal_service.py`: Scores each trade candidate
- `feature_extractor.py`: Computes 23+ technical indicators
- `performance_analytics.py`: Tracks Sharpe, win rate, profit factor

### 9. Risk Controls (`src/trading_hydra/risk/`)
**Purpose**: Institutional-grade position sizing and correlation management.

**Components**:
- `position_sizer.py`: Kelly criterion sizing (0.5% base risk per trade)
- `correlation_manager.py`: Blocks trades when correlation too high

### 9. Web Dashboard (`src/trading_hydra/dashboard/`)
**Purpose**: Visual monitoring and control interface.

**Features**:
- Real-time equity, P&L, success rates
- Manual trading (buy/sell buttons)
- Bot enable/disable toggles
- Configuration editor
- Logs viewer

---

## Configuration Reference

### File: `config/settings.yaml`

| Section | Setting | Default | What It Controls |
|---------|---------|---------|------------------|
| **runner** | loop_interval_seconds | 5 | How often the trading loop runs |
| **safety** | fail_closed | true | System halts safely on errors |
| **risk** | global_max_daily_loss_pct | 2.0 | Maximum daily loss before halt |
| **trading** | global_halt | false | Emergency stop switch |
| **trading** | allow_live | true | Enable real money trading |
| **ml** | enabled | true | Use ML trade scoring |
| **ml** | min_probability | 0.61 | Minimum ML score to trade |
| **institutional_sizing** | base_risk_pct | 0.5 | Risk per trade (% of NAV) |
| **institutional_sizing** | kelly_fraction | 0.25 | Fraction of Kelly to use |
| **correlation_management** | max_pairwise_correlation | 0.7 | Block if correlation > 70% |
| **correlation_management** | max_sector_exposure_pct | 20.0 | Max per-sector exposure |

### File: `config/bots.yaml`

| Bot | Key Setting | What It Controls |
|-----|-------------|------------------|
| **exitbot** | cooloff_minutes | 390 | Time before re-enabling after halt |
| **exitbot** | default_trailing_stop.value | 1.0 | Trailing stop % for manual trades |
| **portfoliobot** | cash_reserve_pct | 30 | % of account to keep as cash |
| **portfoliobot** | crypto_bucket_pct | 25 | % of daily risk for crypto |
| **cryptobot** | universe.ml_rerank_select | 3 | Final # of coins to trade |
| **cryptobot** | risk.max_trades_per_day | 5 | Max crypto trades per day |
| **momentum_bots** | enabled | true/false | Enable/disable each stock |
| **optionsbot** | strategies.long_call.enabled | true | Enable long call strategy |

---

## What You Can Change (and Why)

### SAFE TO CHANGE (Low Risk)

| Setting | Location | Why Change It |
|---------|----------|---------------|
| `loop_interval_seconds` | settings.yaml | Slower = less API calls, faster = more responsive |
| `log_path` | settings.yaml | Different log location |
| Trailing stop values | bots.yaml | Tighter = lock profits faster, looser = ride trends |
| `take_profit_pct` | bots.yaml | When to exit winning trades |
| `stop_loss_pct` | bots.yaml | Maximum acceptable loss per trade |
| Bot `enabled` flags | bots.yaml | Turn bots on/off |
| Trading session times | bots.yaml | When each bot can trade |
| `min_probability` | settings.yaml | Higher = fewer but higher-quality trades |

### MODERATE RISK (Understand Before Changing)

| Setting | Location | Impact |
|---------|----------|--------|
| `global_max_daily_loss_pct` | settings.yaml | Higher = more risk, could lose more in a day |
| `base_risk_pct` | settings.yaml | Position sizes scale with this |
| `max_trades_per_day` | bots.yaml | More trades = more commissions, potentially more profit |
| `max_concurrent_positions` | bots.yaml | Higher = more capital deployed at once |
| Bucket allocations | portfoliobot | Shifts capital between bots |
| `kelly_fraction` | settings.yaml | Higher = more aggressive sizing (max 0.5 recommended) |

### HIGH RISK (Expert Only)

| Setting | Location | Warning |
|---------|----------|---------|
| `allow_live` | settings.yaml | Setting to true uses REAL MONEY |
| `fail_closed` | settings.yaml | Never set to false in production |
| `global_halt` | settings.yaml | Only set true for emergency |
| `max_pairwise_correlation` | settings.yaml | Lower = stricter, may block valid trades |
| Credit spread strategies | optionsbot | Require margin; disabled by default |

### DO NOT CHANGE (System Critical)

| Setting | Why |
|---------|-----|
| `state_db_path` | Breaking this loses all state |
| API credentials | System won't connect |
| `cooloff_minutes` | Safety mechanism timing |
| Kill condition flags | Safety mechanisms |

---

## Safety Controls

### Automatic Halts
The system will automatically halt trading when:

1. **Daily P&L Limit Hit**: Loss exceeds `global_max_daily_loss_pct`
2. **API Failures**: Too many Alpaca API errors in a row
3. **Data Staleness**: Price data is too old (>15 seconds)
4. **ML Anomaly Detection**: Unusual account behavior detected
5. **Drawdown Prediction**: >80% probability of significant drawdown

### Manual Controls

| Control | How to Use |
|---------|------------|
| **Emergency Halt** | Set `trading.global_halt: true` in settings.yaml |
| **Disable Bot** | Set `enabled: false` for specific bot in bots.yaml |
| **Pause All Trading** | Use dashboard toggle or halt via ExitBot |

### Fail-Closed Design
If anything goes wrong, the system:
- Stops opening NEW positions
- Continues monitoring EXISTING positions
- Applies trailing stops to protect gains
- Waits for cooloff period before resuming

---

## Daily Operations

### Morning Checklist
1. Check dashboard: http://localhost:5000
2. Review overnight P&L (crypto trades 24/7)
3. Verify account equity and buying power
4. Check for any halt conditions

### Monitoring
| What to Watch | Where | Action If |
|---------------|-------|-----------|
| Daily P&L | Dashboard | If down >1.5%, monitor closely |
| Open positions | Dashboard | Check trailing stops are active |
| Errors | Logs tab | Investigate any repeated errors |
| Win rate | Performance section | If <50%, review ML scores |

### Weekly Maintenance
1. Review `logs/app.jsonl` for patterns
2. Check ML model performance
3. Retrain models if win rate drops: `python scripts/ml/train_model.py`
4. Review and prune old state: check `state/trading_state.db` size

### Troubleshooting

| Issue | Check | Solution |
|-------|-------|----------|
| No trades executing | `ml.min_probability` | Lower threshold if too restrictive |
| System halted | Dashboard halt indicator | Check cooloff timer, logs for cause |
| API errors | Logs | Verify Alpaca credentials, check Alpaca status |
| Slow performance | `loop_interval_seconds` | Increase interval if API rate limited |

---

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `ALPACA_KEY` | Yes | Alpaca API key |
| `ALPACA_SECRET` | Yes | Alpaca API secret |
| `ALPACA_PAPER` | Yes | Set to "true" for paper trading |

---

## File Locations

### Configuration Files

| Path | Contents |
|------|----------|
| `config/settings.yaml` | System settings (risk, ML, market hours) |
| `config/bots.yaml` | Bot configurations (all 7 bots) |
| `config/strategies/*.yaml` | Strategy system definitions (10 files) |

### State & Data

| Path | Contents |
|------|----------|
| `state/trading_state.db` | SQLite state database |
| `state/metrics.db` | Performance metrics |
| `logs/app.jsonl` | Application logs (JSON format) |
| `logs/decision_records.jsonl` | Audit trail for all trade decisions |
| `models/` | Trained ML models |

### Source Code by Bot

| Bot | Config Section | Source File |
|-----|---------------|-------------|
| MomentumBot | `momentum_bots` | `src/trading_hydra/bots/momentum_bot.py` |
| OptionsBot | `optionsbot` | `src/trading_hydra/bots/options_bot.py` |
| OptionsBot 0DTE | `optionsbot_0dte` | `src/trading_hydra/bots/options_bot.py` |
| TwentyMinuteBot | `twentyminute_bot` | `src/trading_hydra/bots/twentyminute_bot.py` |
| CryptoBot | `cryptobot` | `src/trading_hydra/bots/crypto_bot.py` |
| ExitBot | `exitbot` | `src/trading_hydra/services/exitbot.py` |
| PortfolioBot | `portfoliobot` | `src/trading_hydra/services/portfoliobot.py` |

### Strategy System Files

| File | Purpose |
|------|---------|
| `src/trading_hydra/strategy/registry.py` | Loads and validates strategy YAML configs |
| `src/trading_hydra/strategy/validator.py` | Evaluates signal rules (price vs EMA, RSI) |
| `src/trading_hydra/strategy/backtest_gate.py` | Enforces historical performance thresholds |
| `src/trading_hydra/strategy/options_selector.py` | Selects contracts by delta/DTE |
| `src/trading_hydra/strategy/earnings_filter.py` | Earnings blackout enforcement |
| `src/trading_hydra/strategy/kill_switch.py` | Per-strategy drawdown circuit breaker |
| `src/trading_hydra/strategy/runner.py` | Orchestrates all 5 gates |

---

## Quick Reference Card

### Start System
```bash
python main.py
```

### Stop System
`Ctrl+C` or set `global_halt: true`

### Emergency Stop
1. Set `config/settings.yaml` → `trading.global_halt: true`
2. System will stop opening new positions immediately

### Resume After Halt
1. Wait for cooloff period (390 minutes by default)
2. Or manually clear halt via dashboard
3. Set `global_halt: false` if manually set

### Check Logs
```bash
tail -f logs/app.jsonl | jq .
```

### Retrain ML
```bash
python scripts/ml/train_model.py
```
