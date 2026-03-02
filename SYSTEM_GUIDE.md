# Trading Hydra — Comprehensive System Guide

**Version:** 1.0
**Date:** February 11, 2026
**Codebase:** ~166 Python modules, ~45,000 lines of code
**Audit Status:** 9-team parallel audit complete, all CRITICAL/HIGH issues resolved

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Architecture Overview](#2-system-architecture-overview)
3. [The Trading Loop (Orchestrator)](#3-the-trading-loop-orchestrator)
4. [Trading Bots](#4-trading-bots)
5. [Exit Intelligence](#5-exit-intelligence)
6. [HydraSensors](#6-hydrasensors)
7. [Risk Management System](#7-risk-management-system)
8. [ML Systems](#8-ml-systems)
9. [Market Intelligence](#9-market-intelligence)
10. [State & Persistence](#10-state--persistence)
11. [Configuration System](#11-configuration-system)
12. [Safety Controls](#12-safety-controls)
13. [Scripts Reference](#13-scripts-reference)
14. [Known Issues & Future Work](#14-known-issues--future-work)

---

# 1. Executive Summary

## What Is Trading Hydra?

Trading Hydra is an **autonomous multi-bot trading system** built entirely in Python, designed to execute automated trades via the Alpaca brokerage API. The system orchestrates six specialized trading bots, each targeting different market conditions and asset classes, unified by a centralized risk management layer and institutional-grade exit intelligence.

## Target Performance

| Metric | Value |
|--------|-------|
| Target daily return | ~$500/day |
| Account size | $47,000 |
| Daily return percentage | ~1.06% |
| Risk per trade (base) | 2% of equity (~$948) |
| Max daily loss | 5% of equity (~$2,350) |
| Daily budget allocation | 10% of equity (~$4,700) |
| Cash reserve | 10% of equity (~$4,700) |

## Design Principles

- **Paper trading by default** — Live trading requires explicit configuration change
- **Config-driven** — All parameters defined in YAML files, no hardcoded constants
- **Pure Python** — No TypeScript, Node.js, or external runtime dependencies
- **SQLite persistence** — Durable state via `trading_state.db` with WAL mode
- **JSONL logging** — Structured event logging to `app.jsonl` with automatic rotation
- **Fail-closed safety** — Any error in risk evaluation blocks the trade (never allows on error)
- **Graceful degradation** — Individual bot failures don't crash the system
- **Audit-grade traceability** — Every exit decision, trade lifecycle event, and risk evaluation is recorded

## Technology Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Broker API | Alpaca (paper + live) |
| Database | SQLite 3 (WAL mode, thread-local connections) |
| Logging | JSONL (structured), Python `logging` (console) |
| Configuration | YAML (PyYAML) |
| Market Data | Alpaca API, yfinance (fallback) |
| ML Framework | LightGBM (trade scoring) |
| AI/NLP | OpenAI API (sentiment analysis) |
| Web Dashboard | Flask |
| Process Control | SIGINT/SIGTERM handlers, PID files |

---

# 2. System Architecture Overview

## High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        TRADING HYDRA                                │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    ORCHESTRATOR (main loop)                  │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────┐ │   │
│  │  │Initialize│→│HaltCheck │→│Portfolio │→│   Execution    │ │   │
│  │  │          │ │(lightweight)│ Bot      │ │                │ │   │
│  │  └──────────┘ └──────────┘ └──────────┘ │ MomentumBot    │ │   │
│  │       │                                  │ WhipsawTrader  │ │   │
│  │       │         ┌──────────┐             │ HailMary       │ │   │
│  │       └────────→│ Finalize │←────────────│                │ │   │
│  │                 └──────────┘             └────────────────┘ │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────────────────── DEDICATED THREADS ──────────────────────┐   │
│  │                                                              │   │
│  │  ┌─────────┐  ┌──────────┐  ┌───────────┐  ┌────────────┐  │   │
│  │  │ ExitBot │  │CryptoBot │  │TwentyMin  │  │ BounceBot  │  │   │
│  │  │  (2s)   │  │  (5s)    │  │Bot (5s)   │  │   (5s)     │  │   │
│  │  └─────────┘  └──────────┘  └───────────┘  └────────────┘  │   │
│  │                                                              │   │
│  │  ┌───────────┐  ┌──────────────┐                            │   │
│  │  │OptionsBot│  │OptionsBot    │                             │   │
│  │  │   (5s)   │  │ 0DTE (5s)    │                             │   │
│  │  └───────────┘  └──────────────┘                            │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────── SHARED SERVICES ────────┐  ┌──── RISK LAYER ────────┐   │
│  │ AlpacaClient                    │  │ PolicyGate              │   │
│  │ MarketRegimeService             │  │ RiskOrchestratorInteg.  │   │
│  │ HydraSensors                    │  │ DynamicBudgetManager    │   │
│  │ NewsIntelligenceService         │  │ InstitutionalPosSizer   │   │
│  │ SentimentScorerService          │  │ CircuitBreakerRegistry  │   │
│  │ SmartMoneyService               │  │ CorrelationGuard        │   │
│  │ MacroIntelService               │  │ KillSwitchService       │   │
│  │ PremarketIntelligenceService    │  │ HaltManager             │   │
│  │ AccountAnalyticsService         │  │ PnLDistributionMonitor  │   │
│  └─────────────────────────────────┘  └─────────────────────────┘   │
│                                                                     │
│  ┌──── PERSISTENCE ────┐  ┌───── ML LAYER ──────┐                  │
│  │ SQLite (state.db)   │  │ MLSignalService      │                  │
│  │ SQLite (metrics.db) │  │ RiskAdjustmentEngine │                  │
│  │ JSONL (app.jsonl)   │  │ BotAllocationModel   │                  │
│  │ JSON (runtime/)     │  │ RegimeSizer          │                  │
│  │ YAML (config/)      │  │ DrawdownPredictor    │                  │
│  └─────────────────────┘  └──────────────────────┘                  │
└─────────────────────────────────────────────────────────────────────┘
```

## Modular, Config-Driven Architecture

Every behavioral parameter in Trading Hydra is defined in YAML configuration files. There are no magic numbers in the trading logic — all thresholds, intervals, limits, and tuning parameters are externalized to config. This enables:

- **Hot-reloading** of parameters without code changes
- **Account mode switching** (standard/small/micro) based on equity
- **A/B testing** of strategies via config variants
- **Audit trail** of what parameters were active during any given trading session

## Threading Model

Trading Hydra uses a **hybrid threading model**:

| Thread | Interval | Purpose |
|--------|----------|---------|
| Main Loop (Orchestrator) | 15s (configurable) | Runs the 5-step trading cycle |
| ExitBot | 2s | Position monitoring, stop enforcement, exit decisions |
| CryptoBot | 5s | 24/7 crypto signal generation and execution |
| TwentyMinuteBot | 5s | Opening range pattern trading |
| BounceBot | 5s | Mean-reversion on oversold conditions |
| OptionsBot | 5s | Options strategy execution |
| OptionsBot0DTE | 5s | Zero-days-to-expiry options |
| HydraSensors | Background | Continuous market data and indicator updates |
| WAL Checkpoint | 300s | SQLite WAL file maintenance |

All dedicated bot threads are managed by `DedicatedThreadManager` in `services/dedicated_threads.py`. Each thread:
- Has its own SQLite connection (thread-local, WAL mode)
- Respects its configured active window (from `bots.yaml`)
- Sleeps when outside its trading session
- Handles exceptions gracefully without crashing other threads

## Fail-Closed Safety Philosophy

The system follows a **fail-closed** design philosophy throughout:

- **Risk evaluation errors** → Trade blocked (not allowed)
- **API connection failures** → Trading halted after threshold
- **Auth failures (401/403)** → Immediate system halt
- **Config validation errors** → Hard fail on HIGH severity
- **Data staleness** → Signals rejected
- **Missing market data** → Regime defaults to conservative
- **ML model errors** → Fallback to rule-based scoring (conservative)
- **Circuit breaker open** → Service calls blocked

This is the opposite of "fail-open" where errors allow trades through. After audit finding C-6, the entire risk layer was verified to block trades on any error condition.

## Graceful Shutdown

Trading Hydra registers signal handlers for clean termination:

```python
signal.signal(signal.SIGTERM, graceful_shutdown_handler)
signal.signal(signal.SIGINT, graceful_shutdown_handler)
atexit.register(graceful_shutdown_handler)
```

Shutdown sequence:
1. Stop periodic WAL checkpoint thread
2. Final WAL checkpoint (flush all pending writes)
3. Close ALL registered SQLite connections across all threads
4. Exit process

This ensures no data loss in `trading_state.db` even on unexpected termination.

## Configuration Hierarchy

Config files are loaded in a strict precedence order:

```
settings.yaml          ← Global risk limits, budget, ML toggles (BASE)
    ↓
bots.yaml              ← Per-bot parameters (windows, thresholds, sizing)
    ↓
account_modes.yaml     ← 3-tier mode parameters (standard/small/micro)
    ↓
ticker_universe.yaml   ← Ticker tiers and trading universe
    ↓
watchlists.yaml        ← Named watchlists and tags
    ↓
sensors.yaml           ← Sensor intervals and TTLs
```

**Critical safety invariant:** Mode configs (e.g., `small_account.yaml`) only merge into the bots config namespace, NOT into `settings.yaml`. This means safety settings in `settings.yaml` are **architecturally isolated** and cannot be overridden by any mode file. This was verified during the February 2026 audit.

---

# 3. The Trading Loop (Orchestrator)

## Overview

The orchestrator (`orchestrator.py`) implements the main trading loop as a continuous 5-step cycle. Each cycle is called a "run" and is identified by a unique `run_id`. The loop runs indefinitely with a configurable interval (default 15 seconds during market hours, 60 seconds outside).

## Loop Flow Diagram

```
                    ┌─────────────────┐
                    │   LOOP START    │
                    │  (every 15s)    │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  STEP 1:        │
                    │  INITIALIZE     │
                    │                 │
                    │ • Fetch account │
                    │ • Check equity  │
                    │ • Detect mode   │
                    │ • Generate ID   │
                    │ • Start sensors │
                    │ • Config doctor │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  STEP 2:        │
                    │  HALT CHECK     │
                    │  (lightweight)  │──── If halted ────→ SKIP TO FINALIZE
                    │                 │
                    │ • HaltManager   │
                    │ • Daily P&L     │
                    └────────┬────────┘
                             │ (not halted)
                    ┌────────▼────────┐
                    │  STEP 3:        │
                    │  PORTFOLIO BOT  │
                    │                 │
                    │ • Budget alloc  │
                    │ • Bot enable    │
                    │ • Risk budget   │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  STEP 4:        │
                    │  EXECUTION      │
                    │                 │
                    │ • Market regime │
                    │ • ML sizing     │
                    │ • Premarket     │
                    │ • Screening     │
                    │ • Bot execution │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  STEP 5:        │
                    │  FINALIZE       │
                    │                 │
                    │ • Loop summary  │
                    │ • Error collect │
                    │ • Dashboard     │
                    │ • Metrics save  │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  SLEEP interval │
                    │  (adaptive)     │
                    └────────┬────────┘
                             │
                             └──────→ LOOP START
```

## Step 1: Initialize (`_step_initialize`)

The initialization step runs at the beginning of every loop cycle:

1. **Account Fetch**: Calls `AlpacaClient.get_account()` to retrieve current account state including equity, buying power, cash, and day trade count.

2. **Equity Check**: Validates that equity is positive and account is not restricted. Records `day_start_equity` on the first run of each trading day for P&L calculations.

3. **Account Mode Detection**: Determines the appropriate trading mode based on current equity:
   - **Standard** ($47,000+): Full budget allocation, all bots enabled
   - **Small** ($10,000–$47,000): Reduced position sizes, conservative parameters
   - **Micro** (<$10,000): Minimal position sizes, limited bot set

4. **Run ID Generation**: Creates a unique identifier for this loop cycle, used for log correlation and audit trail.

5. **HydraSensors Startup**: Ensures the background sensor layer is running and providing fresh market data, indicators, and regime signals.

6. **Config Doctor**: Validates configuration consistency at startup. Checks for:
   - Key naming mismatches between config files
   - Value sanity (e.g., risk limits within reasonable bounds)
   - Required fields present
   - **HARD FAIL** on HIGH severity conflicts (system will not proceed)

```python
def _step_initialize(self) -> Dict[str, Any]:
    account = self.alpaca_client.get_account()
    equity = float(account.equity)
    
    if not self._day_start_equity:
        self._day_start_equity = float(account.last_equity or equity)
    
    mode = self._detect_account_mode(equity)
    run_id = f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
    
    self._ensure_sensors_running()
    self._run_config_doctor()
    
    return {
        "equity": equity,
        "mode": mode,
        "run_id": run_id,
        "account": account
    }
```

## Step 2: Halt Check (`_step_halt_check`)

This is a **lightweight** step in the main loop. The actual heavy position monitoring runs in ExitBot's dedicated 2s thread.

The main loop only checks:
- **HaltManager status**: Is a global trading halt active?
- **Halt expiry**: Has the halt cooloff period expired?
- **Config override**: Is `trading.global_halt` set to `true` in settings.yaml?
- **Daily P&L limits**: Has the daily loss exceeded `global_max_daily_loss_pct` (5%)?

If the system is halted, execution skips directly to the Finalize step. ExitBot continues running independently in its thread to manage existing positions even during a halt.

```python
def _step_halt_check(self, context: Dict) -> bool:
    halt_manager = get_halt_manager()
    
    if halt_manager.is_halted():
        status = halt_manager.get_status()
        self.logger.log("halt_active", {
            "reason": status.reason,
            "expires_at": status.expires_at
        })
        return True  # halted
    
    daily_pnl_pct = self._calculate_daily_pnl_pct(context)
    max_loss = self.settings.get("risk", {}).get("global_max_daily_loss_pct", 5.0)
    
    if daily_pnl_pct <= -max_loss:
        halt_manager.set_halt(
            f"DAILY_LOSS_LIMIT: {daily_pnl_pct:.2f}% exceeds {max_loss}%",
            cooloff_minutes=60
        )
        return True
    
    return False  # not halted
```

## Step 3: PortfolioBot (`_step_portfolio`)

PortfolioBot performs budget allocation and bot management:

1. **Budget Allocation**: Uses `DynamicBudgetManager` to compute the day's trading budget based on current equity, drawdown state, and performance history.

2. **Bot Enable/Disable**: Based on the current market regime (from `MarketRegimeService`), certain bots may be enabled or disabled. For example:
   - In a high-volatility regime, momentum trading may be reduced
   - In a risk-off environment, aggressive strategies are disabled

3. **Risk Budget Computation**: Distributes the daily budget across active bots proportionally based on their historical performance and current market conditions.

## Step 4: Execution (`_step_execute`)

The execution step is the core of the trading loop:

1. **Market Regime Fetch**: Retrieves live market indicators:
   - **VIX** (CBOE Volatility Index) — Overall market fear gauge
   - **VVIX** (VIX of VIX) — Volatility of volatility
   - **TNX** (10-Year Treasury Yield) — Interest rate environment
   - **DXY** (Dollar Index) — USD strength
   - **MOVE** (Bond Volatility) — Fixed income stress

2. **ML Regime Sizing**: The `RegimeSizer` model adjusts position sizes (0.0–1.5x multiplier) based on the regime indicators above.

3. **Account Analytics**: `AccountAnalyticsService` runs the ML model ensemble to produce portfolio intelligence including bot allocation recommendations and drawdown probability.

4. **Premarket Intelligence**: During 6:00–6:30 AM PST, `PremarketIntelligenceService` performs multi-factor symbol ranking:
   - Gap analysis (overnight price gaps)
   - IV (implied volatility) assessment
   - Volume surge detection
   - Event flag checking (earnings, FDA, etc.)
   - Universe ranking and bot-specific eligibility scoring

5. **Ticker Screening**: `UniverseGuard` and session selectors filter the tradeable universe based on:
   - Liquidity requirements
   - Volatility thresholds
   - Earnings blackout windows
   - Sector exposure limits

6. **News Catalyst**: `NewsCatalystService` checks for breaking news that could affect positions or create trading opportunities.

7. **Bot Execution**: Each enabled bot in the main loop is called to generate signals and execute trades:
   - **MomentumBot** — Trend-following on screened stocks
   - **WhipsawTrader** — Mean-reversion in range-bound markets
   - **HailMary** — Aggressive options plays (via OptionsBot)

   Note: CryptoBot, TwentyMinBot, BounceBot, OptionsBot, and OptionsBot0DTE run in their own dedicated threads and are NOT called from the main loop execution step.

## Step 5: Finalize (`_step_finalize`)

The finalization step wraps up each loop cycle:

1. **Loop Summary**: Logs the complete run results including trades executed, signals generated, and errors encountered.

2. **Error Collection**: Aggregates any errors from all steps and services. Persistent errors may trigger escalation.

3. **Display Data Assembly**: Prepares the data payload for the console dashboard, including:
   - Current positions and P&L
   - Active bot status
   - Market regime indicators
   - Recent trade history
   - System health metrics

4. **Metrics Recording**: Saves daily performance snapshots to `metrics.db` with idempotency checks (one snapshot per day).

5. **Adaptive Sleep**: The loop interval adjusts based on market conditions:
   - During market hours: default interval (15 seconds)
   - Outside market hours: slower polling (60 seconds) to reduce API calls
   - Crypto still runs 24/7 via its dedicated thread

---

# 4. Trading Bots

Trading Hydra runs **six specialized bots**, each designed for different market conditions, asset classes, and timeframes. Three bots run in the main orchestrator loop; three run in dedicated background threads.

## Bot Summary

| Bot | Asset Class | Thread | Interval | Active Window |
|-----|------------|--------|----------|---------------|
| MomentumBot | Equities | Main loop | 15s | Market hours (06:30–13:00 PST) |
| CryptoBot | Crypto | Dedicated | 5s | 24/7 |
| WhipsawTrader | Equities | Main loop | 15s | Market hours |
| BounceBot | Equities | Dedicated | 5s | From bots.yaml (overnight session) |
| TwentyMinuteBot | Equities | Dedicated | 5s | 05:30–07:00 PST (warmup + trade) |
| HailMary/OptionsBot | Options | Dedicated | 5s | Market hours |

---

## 4.1 MomentumBot

**File:** `bots/momentum_bot.py`
**Purpose:** Trend-following strategy on screened stocks

### Strategy

MomentumBot identifies stocks exhibiting strong directional momentum and rides the trend with trailing stops. It is the primary equities bot during normal market hours.

### Signal Generation

1. **SMA Crossover**: Short-period SMA crosses above long-period SMA (bullish) or below (bearish)
2. **RSI Confirmation**: RSI must be in the momentum zone (not overbought >70 or oversold <30 for entry)
3. **Volume Confirmation**: Current volume must exceed average volume by a configurable multiplier
4. **All three conditions must align** for a valid entry signal

### Active Window

- Market hours: 06:30–13:00 PST (configurable via `bots.yaml`)
- Runs within the main orchestrator loop (not a dedicated thread)

### Trade Limits

- `max_trades_per_day`: 6 (configurable)
- Enforced via `_reserve_trade_slot()` using `atomic_increment()` on shared state
- Uses `BEGIN IMMEDIATE` SQLite transaction for true database-level atomicity across threads

```python
def _reserve_trade_slot(self) -> bool:
    day_key = datetime.utcnow().strftime("%Y%m%d")
    key = f"momentum:trades:{day_key}"
    success, count = atomic_increment(key, max_value=self.max_trades_per_day)
    if not success:
        self.logger.log("trade_limit_reached", {
            "current": count,
            "max": self.max_trades_per_day
        })
    return success
```

### ML Entry Gate

- Break-even threshold: 0.50 (trade only when ML model predicts >50% chance of profitability)
- Falls back to rule-based scoring when ML model has insufficient training data

### Position Sizing

- Uses `InstitutionalPositionSizer` for Kelly criterion–based sizing
- Base risk: 2% of equity (~$948 on $47k)
- Adjusted by VIX regime multiplier and correlation exposure

### Key Config Parameters (from `bots.yaml`)

```yaml
momentum:
  enabled: true
  max_trades_per_day: 6
  sma_short_period: 10
  sma_long_period: 20
  rsi_period: 14
  rsi_overbought: 70
  rsi_oversold: 30
  volume_multiplier: 1.5
  ml_entry_threshold: 0.50
  session:
    window_start: "06:30"
    window_end: "13:00"
```

### Post-Order Validation

After placing an order, MomentumBot verifies the response confirms submission before recording the trade. Failed orders are not counted toward the daily trade limit (audit fix H-5).

---

## 4.2 CryptoBot

**File:** `bots/crypto_bot.py`
**Purpose:** 24/7 cryptocurrency trading across a 22-pair universe

### Strategy

CryptoBot trades cryptocurrency pairs continuously, using technical indicators for entry/exit signals. It runs in a dedicated thread at 5-second intervals because crypto markets never close.

### Trading Universe (22 pairs)

The crypto universe includes major pairs (BTC/USD, ETH/USD) and altcoins. Each pair has specific configuration for quote staleness tolerance based on its liquidity profile.

### Signal Generation

1. **RSI Analysis**: Oversold (<30) for buy signals, overbought (>70) for sell signals (Wilder's EMA smoothing)
2. **MACD Crossover**: Signal line crossover for trend confirmation
3. **Volume Analysis**: Volume spike detection above moving average

### Critical Design Decisions

- **No Short Signals**: Alpaca does not support short-selling crypto. CryptoBot returns `"hold"` instead of `"short"` for bearish conditions (audit fix H-2). Previously, short signals consumed the one-trade-per-cycle slot, blocking subsequent buy signals.

- **Quote Staleness Tolerance**: `MAX_QUOTE_AGE_SECONDS` is set to 300 seconds for the general threshold. Most altcoins (low-volume pairs) have quote update intervals of 60–120 seconds. The original 30-second threshold rejected 80%+ of signals as "stale data" (audit fix C-4).

- **Cooldown Isolation**: The 180-second post-trade cooldown only applies to new entry signals, NOT to exit/stop-loss monitoring on existing positions (audit fix H-3).

### Concurrency

- Max concurrent positions: 8
- Each position tracked independently with its own stop-loss and take-profit levels

### Signal Warmup

- Minimum 5 data points required before generating signals (reduced from 20 — audit fix H-4)
- Adaptive SMA period: `min(configured_period, available_data_length)`
- Warmup completes in ~25 seconds at 5-second intervals

### Key Config Parameters

```yaml
cryptobot:
  enabled: true
  max_concurrent_positions: 8
  max_quote_age_seconds: 300
  cooldown_seconds: 180
  signal:
    rsi_period: 14
    rsi_overbought: 70
    rsi_oversold: 30
    macd_fast: 12
    macd_slow: 26
    macd_signal: 9
    entry_lookback: 20
    exit_lookback: 10
  retry:
    max_retries: 5
    backoff_base: 1.2  # Gentle backoff (was 2.0, audit fix H-16)
```

### Thread Configuration

```python
# Dedicated thread at 5-second intervals
{
    "name": "CryptoBot",
    "interval_seconds": 5,
    "config_key": "cryptobot",  # Must match bots.yaml (audit fix C-1)
    "active_24_7": True
}
```

---

## 4.3 WhipsawTrader

**File:** `strategy/whipsaw_trader.py`
**Purpose:** Detect and profit from range-bound (whipsaw) markets

### Strategy

WhipsawTrader recognizes when a market transitions from trending to range-bound behavior and switches its approach from trend-following to mean-reversion. This is the system's adaptive strategy for choppy markets.

### Whipsaw Detection

The trader identifies range-bound conditions through three signals:

1. **Consecutive Stop-Outs**: Multiple stop-losses hit in the same direction indicate the market is reversing too fast for trend-following
2. **ATR Compression**: Declining Average True Range suggests the market is consolidating into a range
3. **Failed Breakouts**: Price breaks out of a range but immediately reverses back, indicating false breakouts

### State Machine

WhipsawTrader operates in three states:

```
TRENDING ──→ WHIPSAW ──→ BREAKOUT_PENDING ──→ TRENDING
    ↑            │                                │
    └────────────┘                                │
    (range breaks)          (confirmed breakout)──┘
```

| State | Behavior |
|-------|----------|
| `TRENDING` | Normal trend-following (defers to MomentumBot) |
| `WHIPSAW` | Mean-reversion mode — buy at support, sell at resistance |
| `BREAKOUT_PENDING` | Range breakout detected, waiting for confirmation before switching back to trending |

### Mean-Reversion Execution

In WHIPSAW state:
- **Buy Signal**: Price touches lower range boundary (support)
- **Sell Signal**: Price touches upper range boundary (resistance)
- Range boundaries calculated from recent highs/lows over `lookback` period

### Risk Parameters

| Parameter | Value |
|-----------|-------|
| Take Profit | +1.0% |
| Stop Loss | -5.0% |
| Lookback | 40 bars |
| ATR compression threshold | Configurable via `bots.yaml` |

### Timezone Handling

All timestamp comparisons use `datetime.utcnow()` instead of `datetime.now()` (audit fix D-5). This prevents timezone-related state corruption.

---

## 4.4 BounceBot

**File:** `bots/bounce_bot.py`
**Purpose:** Mean-reversion on oversold conditions

### Strategy

BounceBot looks for stocks that have experienced sharp declines and are likely to bounce. It uses oversold technical indicators to identify entry points and rides the recovery.

### Active Window

- Configured via `bots.yaml` session parameters (not hardcoded — audit fix H-14)
- Originally targeted overnight sessions (01:00–05:30 PST) but configurable
- Reads `bouncebot.session.window_start` and `window_end` from config

### Thread Configuration

- Runs in a **dedicated thread** at 5-second intervals
- Thread manager reads correct config keys from `bots.yaml` (audit fix C-1: `bouncebot` not `bounce_bot`)

### Signal Generation

- RSI oversold conditions (RSI < 30)
- Price at or below support levels
- Volume confirmation of selling exhaustion

---

## 4.5 TwentyMinuteBot

**File:** `bots/twenty_minute_bot.py`
**Purpose:** Pattern trading in the first 20–25 minutes of market open

### Strategy

TwentyMinuteBot exploits the high-volatility period immediately after the market open. It uses pre-staged bracket orders for rapid execution and focuses on gap plays, opening range breakouts, and momentum ignition patterns.

### Two-Phase Execution

#### Phase 1: Warmup (05:30–06:25 PST)

During the warmup phase, the bot performs data gathering only — NO trades are executed:

- Gap scanning (overnight price gaps)
- Pre-market volume analysis
- Order book depth assessment
- Candidate ranking

The warmup period is config-driven via `trade_execution_start` in `bots.yaml`.

#### Phase 2: Active Trading (06:25–06:50+ PST)

Once the trade execution window opens:

- Pre-staged bracket orders are submitted
- Each entry has predefined stop-loss and take-profit levels
- Reactive trading with quick exits if the pattern fails

### Pre-Staged Bracket Orders

TwentyMinuteBot uses bracket orders (OCO — one-cancels-other) for every entry:

```python
# Each trade has three legs:
# 1. Entry order (market or limit)
# 2. Take-profit limit order
# 3. Stop-loss stop order (not limit — audit fix H-6)
```

**Critical fix (H-6):** Stop-loss orders previously used `order_type="limit"` with `limit_price=stop_price`. Limit orders fill immediately if the market price is above the limit, meaning "stop-losses" were executing as instant sells on entry. Fixed to use `order_type="stop"` with `stop_price` parameter.

### Position Sizing

- Uses dynamic budget allocation from `DynamicBudgetManager`
- Hardcoded $100 cap removed (audit fix H-7) — now uses configured sizing
- Supports institutional position sizing when enabled

### Trade Limits

- Enforces `max_trades_per_day` via `_reserve_trade_slot()` (same atomic mechanism as MomentumBot — audit fix C-3)

### Key Config Parameters

```yaml
twentymin:
  enabled: true
  trade_execution_start: "06:25"
  warmup_start: "05:30"
  max_trades_per_day: 4
  gap_threshold_pct: 1.5
  session:
    window_start: "05:30"
    window_end: "07:00"
```

---

## 4.6 HailMary (OptionsBot Strategy)

**File:** Part of `bots/options_bot.py`
**Purpose:** Aggressive options plays on high-conviction setups

### Strategy

HailMary is an options strategy within the OptionsBot framework that takes aggressive positions when multiple high-conviction signals align. It targets outsized returns on a small portion of the portfolio.

### IV Percentile Entry Gate

HailMary uses an IV (Implied Volatility) percentile entry gate:
- Only enters when IV percentile is favorable (not buying expensive options)
- IV rank calculated against historical IV distribution
- Prevents entering when options are overpriced relative to recent history

### Variants

| Variant | Thread | Description |
|---------|--------|-------------|
| OptionsBot | Dedicated (5s) | Standard options strategy execution |
| OptionsBot0DTE | Dedicated (5s) | Zero-days-to-expiry options — higher risk, same-day expiration plays |

### Thread Configuration

Both OptionsBot variants run in dedicated threads with correct config paths (audit fix H-15):

```python
# Config reads from correct nested paths:
# optionsbot.session.trade_start (not optionsbot.trade_window_start)
```

---

# 5. Exit Intelligence

Exit intelligence is the most critical subsystem in Trading Hydra. The system's philosophy is: **entries are important, but exits determine profitability**. Two components work together: ExitBot v2 Elite (the primary exit engine) and ProfitSniper (profit-priority exit layer).

## 5.1 ExitBot v2 Elite

**File:** `services/exitbot.py`, `services/exit_decision.py`
**Thread:** Dedicated 2-second interval (fastest thread in the system)

### Why ExitBot Is the Most Important Bot

ExitBot monitors **ALL positions** — both automated (placed by trading bots) and manual (placed through Alpaca's UI or other tools). It is the last line of defense against catastrophic losses and the primary mechanism for capturing profits.

ExitBot runs in its own dedicated thread at 2-second intervals, making it the fastest-cycling component in the system. It is NOT called from the main orchestrator loop — the main loop only performs a lightweight halt check via HaltManager.

### Position Monitoring Pipeline

Every 2 seconds, ExitBot:

1. Fetches all open positions from Alpaca
2. For each position, creates a `PositionSnapshot` (forensic state capture)
3. Evaluates exit conditions in authority order
4. Records the decision in the audit trail
5. Executes any exit orders

### Exit Authority Hierarchy

ExitBot uses an **authority-based exit hierarchy** where higher-authority exit reasons override lower ones:

```
AUTHORITY LEVEL 1 (Highest): CATASTROPHIC
  └─ Catastrophic stop (12-35% loss depending on config)
  └─ max_loss_override (daily loss limit breach)

AUTHORITY LEVEL 2: V2_INTELLIGENCE
  └─ TradeHealthScorer assessment (score 0-100)
  └─ ExitDecisionEngine recommendation
  └─ TradeMemoryEngine pattern matching

AUTHORITY LEVEL 3: TRAILING_STOP
  └─ ATR-scaled trailing stop
  └─ ML-adjusted stop width
  └─ VIX-influenced stop distance
  └─ Volume tightening trigger

AUTHORITY LEVEL 4: TAKE_PROFIT
  └─ TP1 (33% partial exit)
  └─ TP2 (50% partial exit)
  └─ TP3 (100% full exit)

AUTHORITY LEVEL 5 (Lowest): TIME_EXIT
  └─ Maximum hold duration exceeded
  └─ End-of-day position cleanup
```

A higher-authority exit reason always takes precedence. For example, if a position triggers both a take-profit and a catastrophic stop in the same cycle, the catastrophic stop executes because it has higher authority.

### Hard Stop-Loss Enforcement

Hard stops are the absolute floor — no position is allowed to lose beyond this level:

- **ATR-Scaled**: Stop distance adjusts based on the stock's Average True Range (wider stops for volatile stocks)
- **ML-Adjusted**: Machine learning model can tighten or widen stops based on historical patterns for that symbol
- **VIX-Influenced**: Higher VIX = wider stops (to avoid being stopped out by normal volatility)

### Tiered Take-Profit (TP1/TP2/TP3)

Take-profit uses a tiered system with partial exits:

| Tier | Trigger | Action | Remaining Position |
|------|---------|--------|--------------------|
| TP1 | First threshold | Exit 33% of position | 67% remaining |
| TP2 | Second threshold | Exit 50% of remaining | 33% remaining |
| TP3 | Third threshold | Exit 100% (full close) | 0% |

**ATR-Adaptive TP Thresholds**: TP levels scale with per-symbol volatility. A stock with ATR of 5% will have wider TP levels than one with ATR of 1%.

### Parabolic Runner Mode

After TP2 is hit, ExitBot enters "parabolic runner" mode:
- Trailing stop is widened significantly
- Position is allowed to "run" if momentum continues
- Captures outsized gains from strong trends
- Only exits on trailing stop hit or end-of-day

### Reversal Sense Stops

Catches positions that:
1. Went up significantly (but not enough to arm the trailing stop)
2. Started reversing back toward entry
3. Would end up as a loss if not exited

Reversal sense monitors the "unrealized P&L direction change" and exits when a position that was profitable starts heading back to breakeven.

### V2 Intelligence Layer

The V2 intelligence layer adds machine learning–based exit decisions:

#### TradeMemoryEngine
- Maintains a **31-day rolling window** of all completed trades
- Stores `ExitFingerprint` objects with trade outcome details
- Per-key TTL check on retrieval with automatic eviction (audit fix D-3)
- Queries: "How did similar trades in the past perform?"

#### TradeHealthScorer
- Scores each open position on a 0–100 scale
- Factors: unrealized P&L, time in trade, MFE/MAE ratio, regime alignment, stop distance
- Scores below threshold trigger V2 exit recommendation

#### ExitDecisionEngine
- Combines TradeHealthScorer output with TradeMemoryEngine patterns
- Produces `ExitDecisionRecord` for audit trail
- **Safety guard**: Requires 30+ historical trades before making binding decisions (prevents V2 from acting on insufficient data)

### EntryIntent Lifecycle

ExitBot tracks the complete lifecycle of every trade:

```
Intent ──→ Fill ──→ Position ──→ Exit
  │          │         │           │
  │          │         │           └─ exit_trades table
  │          │         └─ PositionSnapshot (every 2s)
  │          └─ order_ids table
  └─ EntryIntent record
```

### PositionSnapshot Forensic Logging

Every 2 seconds, each position generates a snapshot containing:
- Current price, entry price, unrealized P&L
- MFE (Maximum Favorable Excursion) — highest profit reached
- MAE (Maximum Adverse Excursion) — deepest drawdown reached
- Current stop levels, trailing stop armed status
- Market regime at snapshot time
- Health score

### ExitDecisionRecord Audit Trail

Every exit decision (including "HOLD" decisions) is recorded:

```python
@dataclass
class ExitDecisionRecord:
    ts: str                  # Timestamp
    run_id: str              # Loop cycle ID
    position_key: str        # Unique position identifier
    action: str              # HOLD, EXIT, PARTIAL_EXIT
    health_score: int        # 0-100
    confidence: float        # Decision confidence
    reason: str              # Exit reason code
    current_price: float     # Price at decision time
    unrealized_pnl_pct: float
    mfe_pct: float
    mae_pct: float
    trailing_stop_pct: float
    hard_stop_pct: float
    time_in_trade_sec: float
    regime: str              # Current market regime
    vwap_posture: str        # BULLISH/BEARISH/NEUTRAL
    triggers_json: str       # Serialized trigger details
```

### Complete Exit Reason Taxonomy

| Exit Reason | Authority | Description |
|-------------|-----------|-------------|
| `catastrophic_stop` | CATASTROPHIC | Position lost 12-35% — emergency exit |
| `max_loss_override` | CATASTROPHIC | Daily loss limit breached |
| `v2_intelligence` | V2_INTELLIGENCE | ML-based exit recommendation |
| `trailing_stop` | TRAILING_STOP | Trailing stop hit after arming |
| `reversal_sense` | TRAILING_STOP | Position reversing from profitable |
| `take_profit` | TAKE_PROFIT | Generic take-profit trigger |
| `tp1` | TAKE_PROFIT | First tier (33% partial) |
| `tp2` | TAKE_PROFIT | Second tier (50% partial) |
| `tp3` | TAKE_PROFIT | Third tier (100% close) |
| `stop_loss` | TRAILING_STOP | Standard stop-loss hit |
| `hard_stop` | TRAILING_STOP | Hard floor stop hit |
| `breakeven_exit` | TAKE_PROFIT | Exit at breakeven (no loss) |
| `time_exit` | TIME_EXIT | Maximum hold duration exceeded |

### Database Tables

ExitBot uses three dedicated SQLite tables:

**`exit_trades`** — One row per completed position lifecycle:
```sql
CREATE TABLE exit_trades (
    position_key TEXT PRIMARY KEY,
    bot_id TEXT, symbol TEXT, asset_class TEXT, side TEXT,
    entry_ts TEXT, entry_price REAL,
    exit_ts TEXT, exit_price REAL, exit_reason TEXT,
    qty REAL, realized_pnl_usd REAL, realized_pnl_pct REAL,
    mfe_pct REAL, mae_pct REAL,
    regime_at_entry TEXT, regime_at_exit TEXT,
    health_score_at_exit INTEGER, hold_duration_sec REAL,
    created_at TEXT, updated_at TEXT
);
```

**`exit_decisions`** — Every decision made, for forensic analysis:
```sql
CREATE TABLE exit_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, run_id TEXT, position_key TEXT,
    action TEXT, health_score INTEGER, confidence REAL, reason TEXT,
    current_price REAL, unrealized_pnl_pct REAL,
    mfe_pct REAL, mae_pct REAL,
    trailing_stop_pct REAL, hard_stop_pct REAL,
    time_in_trade_sec REAL,
    regime TEXT, vwap_posture TEXT, triggers_json TEXT
);
```

**`exit_options_context`** — Greeks and IV snapshots for options:
```sql
CREATE TABLE exit_options_context (
    position_key TEXT PRIMARY KEY,
    underlying TEXT, expiry TEXT, strike REAL, right TEXT,
    iv_entry REAL, iv_exit REAL,
    delta_entry REAL, delta_exit REAL,
    gamma_entry REAL, gamma_exit REAL,
    theta_entry REAL, theta_exit REAL,
    vega_entry REAL, vega_exit REAL,
    dte_at_entry INTEGER, dte_at_exit INTEGER
);
```

---

## 5.2 ProfitSniper

**File:** `services/profit_sniper.py`
**Purpose:** Profit-priority exit intelligence that prevents profit evaporation

### The Problem ProfitSniper Solves

Without ProfitSniper, a position might:
1. Enter at $100
2. Rise to $105 (5% profit)
3. Trailing stop not yet armed (needs larger move)
4. Price reverses to $98 (2% loss)
5. Stop-loss finally triggers at $95 (5% loss)

The position had a 5% peak profit that evaporated into a 5% loss. ProfitSniper catches these scenarios.

### Execution Priority

**ProfitSniper is called BEFORE standard trailing stop checks.** It has priority over the standard exit pipeline. If ProfitSniper decides to exit, the trailing stop evaluation is skipped.

### Three Capabilities

#### 1. Profit Velocity Detection

Tracks the rate of profit change over time:
- Calculates profit delta per interval
- When velocity reverses (profit was growing, now shrinking), triggers alert
- Configurable sensitivity threshold

```
Profit Over Time:
  ▲
  │       ╱╲  ← Velocity reversal detected here
  │      ╱  ╲
  │     ╱    ╲
  │    ╱      ╲
  │   ╱        ╲
  │──╱──────────╲──────→ Time
  │               ╲
  │                ╲  ← Without ProfitSniper, exits here (loss)
```

#### 2. Peak Profit Ratchet

The ratchet mechanism:
- **Arms** when profit reaches a configurable threshold
- **Only moves up** (for long positions) — never ratchets down
- **Tightens as profit grows** — the stop gets closer to current price as profit increases
- Creates a "profit floor" that rises with the position

| Asset Class | Arm Threshold | Ratchet Behavior |
|-------------|---------------|------------------|
| Equities (default) | 0.5% profit | Standard tightening |
| Options | 3.0% profit | Wider arm (options are volatile) |
| Crypto | 0.3% profit | Tighter arm (24/7 monitoring) |

#### 3. Momentum Exhaustion

Detects fading momentum through bar analysis:
- Monitors consecutive bars for weakening strength
- 3 consecutive weakening bars triggers exit signal
- "Weakening" = smaller range, lower volume, or bearish close direction

### Partial Exit Mechanism

ProfitSniper uses a two-stage exit:
1. **First trigger**: Exit 50% of position (lock in profit, keep upside exposure)
2. **Second trigger**: Exit remaining 100%

This balances profit protection with upside capture.

### State Persistence

ProfitSniper state is persisted in SQLite:
- Peak profit levels survive process restarts
- Ratchet positions are durable
- No state loss on unexpected shutdown

---

# 6. HydraSensors

**File:** `sensors/manager.py`, `sensors/indicators.py`, `sensors/breadth.py`
**Purpose:** Non-blocking, fail-open background sensor layer for continuous market intelligence

## Design Philosophy

HydraSensors is designed to be **fail-open** (unlike the risk layer which is fail-closed). If a sensor fails to update, the system continues operating with the last known good data rather than halting. This is because sensors provide supplementary intelligence — they inform decisions but don't make them.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  SensorsManager                      │
│              (Background Thread)                     │
│                                                      │
│  ┌────────────────┐  ┌──────────────────┐           │
│  │WatchlistManager│  │ MarketDataCache  │           │
│  │                │  │                  │           │
│  │• Ticker lists  │  │• Quotes (30s)    │           │
│  │• Tags          │  │• Bars 1m (60s)   │           │
│  │• Named lists   │  │• Bars 5m (300s)  │           │
│  │• Universe YAML │  │• Bars 1d (3600s) │           │
│  └────────────────┘  └──────────────────┘           │
│                                                      │
│  ┌────────────────────┐  ┌──────────────────────┐   │
│  │IndicatorCalculator │  │ BreadthCalculator    │   │
│  │                    │  │                      │   │
│  │• SMA              │  │• RSP vs SPY          │   │
│  │• RSI (Wilder's)   │  │  (breadth health)    │   │
│  │• ATR              │  │• SMH vs SPY          │   │
│  │• Returns          │  │  (tech leadership)   │   │
│  └────────────────────┘  └──────────────────────┘   │
│                                                      │
│  ┌────────────────────┐                              │
│  │  RegimeDetector    │                              │
│  │                    │                              │
│  │• risk_on           │                              │
│  │• risk_off          │                              │
│  │• neutral           │                              │
│  │• unknown           │                              │
│  └────────────────────┘                              │
└─────────────────────────────────────────────────────┘
```

## Components

### WatchlistManager

- Loads ticker universe from `ticker_universe.yaml`
- Supports named watchlists from `watchlists.yaml`
- Tags: categorize tickers by sector, market cap, strategy eligibility
- Provides filtered views: "give me all large-cap tech stocks eligible for momentum trading"

### MarketDataCache

TTL-based cache for market data to minimize API calls:

| Data Type | TTL | Source |
|-----------|-----|--------|
| Quotes | 30 seconds | Alpaca Market Data API |
| 1-minute bars | 60 seconds | Alpaca Market Data API |
| 5-minute bars | 300 seconds (5 min) | Alpaca Market Data API |
| Daily bars | 3600 seconds (1 hour) | Alpaca Market Data API |

Cache behavior:
- Returns cached data if within TTL
- Fetches fresh data if TTL expired
- Falls back to stale cached data (with `cache_fallback` flag) if fetch fails
- Thread-safe access via locks

### IndicatorCalculator

Technical indicator computation engine:

- **SMA (Simple Moving Average)**: Standard moving average calculation over configurable periods
- **RSI (Relative Strength Index)**: Uses **Wilder's EMA smoothing** (audit fix D-2):
  ```
  First period: SMA of gains/losses
  Subsequent: avg = prev_avg * (period-1) / period + current / period
  ```
  All 6 RSI implementations across the codebase were updated to use Wilder's smoothing.
- **ATR (Average True Range)**: Volatility measurement using true range over configurable period
- **Returns**: Simple and logarithmic return calculations

### BreadthCalculator

Market breadth assessment using ETF relative performance:

| Comparison | What It Measures |
|------------|-----------------|
| RSP vs SPY | **Breadth Health** — Equal-weight vs cap-weight S&P 500. When RSP outperforms, broad market participation is healthy. When SPY leads, gains are concentrated in mega-caps. |
| SMH vs SPY | **Tech Leadership** — Semiconductor ETF vs S&P 500. Tech leading = risk appetite. Tech lagging = defensive rotation. |

### RegimeDetector

Combines breadth, volatility, and momentum signals into a market regime classification:

| Regime | Meaning | Trading Behavior |
|--------|---------|-----------------|
| `risk_on` | Broad participation, low vol, positive momentum | Full allocation, aggressive entries |
| `risk_off` | Narrow leadership, high vol, negative momentum | Reduced allocation, tighter stops |
| `neutral` | Mixed signals | Default allocation |
| `unknown` | Insufficient data | Conservative defaults |

### SensorsManager

The SensorsManager runs in a background thread and continuously updates all sensor components. It writes output to `runtime/` directory for consumption by other components.

**Path resolution:** Uses `os.path.dirname()` chain to resolve to absolute `export/runtime/` regardless of current working directory (audit fix D-6).

---

# 7. Risk Management System

## Core Philosophy

The risk management system follows a **fail-closed** philosophy: **any error in risk evaluation blocks the trade**. This is the opposite of "fail-open" where errors allow trades through. After audit finding C-6, the entire risk layer was verified to block trades on any error condition.

```
┌──────────────────────────────────────────────────────┐
│                    RISK LAYER                         │
│                                                       │
│  Every order must pass through this pipeline:         │
│                                                       │
│  Order → PolicyGate → RiskOrchestrator → Execution    │
│              │              │                          │
│              │              ├─ DynamicBudgetManager    │
│              │              ├─ CorrelationGuard        │
│              │              ├─ VolOfVolMonitor         │
│              │              ├─ NewsRiskGate            │
│              │              ├─ PnLDistributionMonitor  │
│              │              ├─ MacroIntelService       │
│              │              └─ SmartMoneyService       │
│              │                                         │
│              ├─ HaltManager check                      │
│              ├─ UniverseGuard                          │
│              ├─ LiquidityFilter                        │
│              ├─ SlippageBudget                         │
│              ├─ GreekLimits (options)                  │
│              └─ MLGovernance                           │
│                                                       │
│  ANY failure at ANY gate = TRADE BLOCKED              │
└──────────────────────────────────────────────────────┘
```

---

## 7.1 PolicyGate

**File:** `risk/policy_gate.py`
**Purpose:** Mandatory checkpoint for ALL orders before execution

PolicyGate is the single entry point for all trade execution. No order can reach Alpaca without passing through PolicyGate's gate sequence:

### Gate Sequence

```
1. Halt Check
   └─ Is the system in a global trading halt?
   └─ BLOCK if halted

2. Universe Guard
   └─ Is this ticker in the approved trading universe?
   └─ Is it on the restricted/blacklist?
   └─ BLOCK if not in universe

3. Risk Orchestrator
   └─ Full risk evaluation (see RiskOrchestratorIntegration)
   └─ BLOCK if SKIP_ENTRY or HALT_TRADING
   └─ REDUCE_SIZE if recommended

4. Liquidity Filter
   └─ Is there sufficient volume and bid/ask spread?
   └─ BLOCK if illiquid

5. Slippage Budget
   └─ Would expected slippage exceed the configured budget?
   └─ BLOCK if slippage too high

6. Greek Limits (options only)
   └─ Are portfolio Greeks within acceptable bounds?
   └─ Delta, gamma, theta, vega exposure checks
   └─ BLOCK if any Greek limit exceeded

7. ML Governance
   └─ Does the ML model approve this trade?
   └─ Break-even probability check
   └─ BLOCK if below ML threshold
```

### Equity Fetching

PolicyGate fetches account equity directly from Alpaca to pass to `DynamicBudgetManager`. This was a critical fix — previously, equity was never passed, causing the budget to fall back to $100 (the hardcoded minimum fallback).

---

## 7.2 RiskOrchestratorIntegration

**File:** `risk/risk_orchestrator.py`
**Purpose:** Central hub connecting all risk evaluation services

### Connected Services

| Service | What It Evaluates |
|---------|-------------------|
| `DynamicBudgetManager` | Is there remaining budget for this trade? |
| `CorrelationGuard` | Would this trade increase portfolio correlation beyond limits? |
| `VolOfVolMonitor` | Is volatility-of-volatility (VVIX) in a dangerous zone? |
| `NewsRiskGate` | Are there adverse news events for this symbol? |
| `PnLDistributionMonitor` | Is the P&L distribution showing concerning patterns? |
| `MacroIntelService` | Is the macro environment (Fed, tariffs) favorable? |
| `SmartMoneyService` | What are institutions and Congress members doing? |

### Evaluation Flow

```
Entry request arrives
    │
    ▼
Budget check ──→ Insufficient? ──→ SKIP_ENTRY
    │
    ▼ (sufficient)
Correlation check ──→ Too correlated? ──→ REDUCE_SIZE
    │
    ▼ (acceptable)
VIX regime check ──→ Crisis level? ──→ HALT_TRADING
    │
    ▼ (normal/elevated)
News sentiment ──→ Adverse news? ──→ SKIP_ENTRY
    │
    ▼ (neutral/positive)
Macro regime ──→ STRESS regime? ──→ REDUCE_SIZE or SKIP_ENTRY
    │
    ▼ (NORMAL/CAUTION)
PnL distribution ──→ Concerning pattern? ──→ REDUCE_SIZE
    │
    ▼ (acceptable)
ALLOW (trade can proceed)
```

### Risk Actions

| Action | Meaning |
|--------|---------|
| `ALLOW` | Trade approved at requested size |
| `REDUCE_SIZE` | Trade approved but at reduced size |
| `SKIP_ENTRY` | Trade rejected — do not enter |
| `FORCE_EXIT` | Existing position should be closed |
| `HALT_TRADING` | System-wide trading halt triggered |

### Fail-Closed Behavior

```python
try:
    result = self._evaluate_all_gates(entry_request)
    return result
except Exception as e:
    self.logger.error(f"Risk evaluation error: {e}")
    return RiskAction.BLOCK  # BLOCK, not ALLOW (audit fix C-6)
```

---

## 7.3 DynamicBudgetManager

**File:** `risk/dynamic_budget.py`
**Purpose:** Scale trading budgets with equity and reduce during drawdowns

### BudgetAllocation

The budget manager produces a `BudgetAllocation` object containing:

| Field | Description |
|-------|-------------|
| `daily_budget_usd` | Total USD available for trading today |
| `max_position_usd` | Maximum size for any single position |
| `equity_multiplier` | Adjustment based on current equity vs target |
| `drawdown_multiplier` | Reduction factor during drawdowns (0.0–1.0) |
| `performance_multiplier` | Adjustment based on recent trading performance |

### Budget Scaling

- **Normal conditions**: `daily_budget = equity × daily_budget_pct` (10% = ~$4,700 on $47k)
- **Drawdown 5%+**: Budget reduced via `dd_threshold_reduce` multiplier
- **Drawdown 15%+**: Trading halted via `dd_threshold_halt`

### Fallback Budget

When the primary budget calculation fails:
```python
fallback = min(5000, equity * 0.05)  # The SAFER of $5k or 5% equity
```

**Critical fix (C-7):** Previously used `max()` which gave $23,500 as the "safe" fallback on a $47k account.

### Key Config Parameters

```yaml
risk:
  daily_budget_pct: 10
  budget_ceiling_usd: 25000
  budget_floor_usd: 500
  max_position_pct: 8
  dd_threshold_reduce: 5    # Reduce at 5% drawdown
  dd_threshold_halt: 15     # Halt at 15% drawdown
  cash_reserve_pct: 10      # Always keep 10% in cash
```

---

## 7.4 InstitutionalPositionSizer

**File:** `risk/position_sizer.py`
**Purpose:** Multi-factor position sizing using institutional methods

### Sizing Methodology

Position size is calculated using a heuristic ensemble of institutional techniques:

1. **Equity NAV%**: Base allocation as percentage of Net Asset Value
2. **ATR-Adjusted**: Size inversely proportional to volatility (volatile stocks get smaller positions)
3. **Kelly Criterion (Fractional)**: Optimal sizing based on historical win rate and payoff ratio, using fractional Kelly (typically 25-50% of full Kelly) for safety
4. **VIX Regime Adjustment**: Position size scaled by current VIX level
5. **Correlation Exposure**: Reduced sizing when portfolio is already correlated

### Key Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| `BASE_RISK_PCT` | 2% | Base risk per trade (~$948 on $47k) |
| `MIN_NOTIONAL` | $200 | Minimum position size (avoid dust positions) |
| `MAX_POSITION_PCT` | 8% | Maximum single position as % of equity |
| `POSITION_FLOOR` | `max($500, equity × 0.01)` | Dynamic floor that scales with account |

### Sizing Flow

```
Base Size = equity × BASE_RISK_PCT
    │
    ▼
ATR Adjustment ──→ High ATR? Reduce size
    │
    ▼
Kelly Criterion ──→ Low edge? Reduce size
    │
    ▼
VIX Regime ──→ High VIX? Reduce size
    │
    ▼
Correlation ──→ High correlation? Reduce size
    │
    ▼
Clamp to [MIN_NOTIONAL, equity × MAX_POSITION_PCT]
    │
    ▼
Final Position Size
```

---

## 7.5 CircuitBreakerRegistry

**File:** `risk/circuit_breaker.py`
**Purpose:** Per-service circuit breakers to prevent cascading failures

### Circuit Breaker Pattern

Each external service has its own circuit breaker that tracks failure rates and temporarily blocks calls to failing services:

```
CLOSED ──(failures exceed threshold)──→ OPEN
   ↑                                      │
   │                                      │ (cooloff expires)
   │                                      ▼
   └──(probe succeeds)── HALF_OPEN ←─────┘
```

| State | Behavior |
|-------|----------|
| `CLOSED` | Normal operation — calls go through |
| `OPEN` | Service blocked — calls immediately fail without being attempted |
| `HALF_OPEN` | Probe mode — one call allowed through to test if service recovered |

### Registered Services

| Service | Description |
|---------|-------------|
| `alpaca_market_data` | Alpaca market data API |
| `alpaca_trading` | Alpaca order execution API |
| `openai_sentiment` | OpenAI sentiment analysis |
| `openai_analysis` | OpenAI market analysis |
| `yfinance` | Yahoo Finance data fetching |
| `news_api` | News aggregation API |
| `reddit_api` | Reddit sentiment API |
| `youtube_api` | YouTube sentiment API |

### Failure Thresholds

Each circuit breaker has configurable:
- **Failure threshold**: Number of consecutive failures to trip the breaker (typically 5)
- **Cooloff period**: How long the breaker stays open before allowing probe (typically 60 seconds)
- **Probe limit**: Number of successful probes needed to close the breaker (typically 1)

---

## 7.6 Other Risk Modules

### CorrelationManager
- Tracks correlation between all open positions
- Prevents opening new positions that would create excessive portfolio correlation
- Uses rolling correlation windows

### CrossBotMonitor
- Ensures different bots don't take conflicting positions on the same symbol
- Prevents MomentumBot (long) and WhipsawTrader (short) from fighting each other

### EdgeDecayMonitor
- Tracks whether trading strategies are losing their edge over time
- Monitors win rate, average P&L, and Sharpe ratio trends
- Alerts when strategies may need retuning

### SlippageTracker
- Records expected vs actual execution prices
- Builds a slippage model per symbol and order type
- Used by SlippageBudget gate to reject trades with excessive expected slippage

### GreekRiskMonitor
- Options-specific risk monitoring
- Tracks portfolio-level delta, gamma, theta, and vega exposure
- Enforces Greek limits configured in `settings.yaml`

### KillSwitchService (StrategyKillSwitch)
- Per-strategy kill switches that can disable individual bots
- Can be triggered programmatically (by risk system) or manually (via dashboard)
- Persisted in state store — survives restarts

---

# 8. ML Systems

Trading Hydra uses **5 machine learning models** orchestrated by the `AccountAnalyticsService`. All models follow a "graceful degradation" pattern: if a model fails or has insufficient training data, the system falls back to rule-based alternatives.

## Architecture

```
┌─────────────────────────────────────────────┐
│          AccountAnalyticsService             │
│          (Orchestrator for ML)               │
│                                              │
│  ┌──────────────┐  ┌─────────────────────┐  │
│  │MLSignalService│  │RiskAdjustmentEngine │  │
│  │  (LightGBM)  │  │ (equity curve)      │  │
│  └──────────────┘  └─────────────────────┘  │
│                                              │
│  ┌────────────────┐  ┌──────────────────┐   │
│  │BotAllocation   │  │  RegimeSizer     │   │
│  │Model           │  │  (VIX/VVIX/TNX)  │   │
│  └────────────────┘  └──────────────────┘   │
│                                              │
│  ┌─────────────────┐                         │
│  │DrawdownPredictor│                         │
│  └─────────────────┘                         │
│                                              │
│  Support Services:                           │
│  ┌──────────────────┐  ┌─────────────────┐  │
│  │TradeOutcomeTracker│  │ DriftDetector   │  │
│  └──────────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────┘
```

---

## 8.1 MLSignalService

**File:** `ml/signal_service.py`
**Purpose:** Pre-trade scoring using LightGBM

### How It Works

Before any trade is executed, MLSignalService scores the trade on its probability of profitability:

1. **Feature Extraction**: Gathers ~50 features including:
   - Technical indicators (RSI, SMA, ATR, volume ratios)
   - Market regime (VIX level, breadth, momentum)
   - Symbol-specific history (past trades on this symbol)
   - Time-of-day, day-of-week patterns
   - Sector exposure

2. **Model Prediction**: LightGBM classifier predicts probability of the trade being profitable (0.0–1.0)

3. **Decision**: Trade is allowed if prediction exceeds the break-even threshold

### Break-Even Thresholds

| Bot | ML Threshold | Meaning |
|-----|-------------|---------|
| MomentumBot | 0.50 | Only take trades with >50% predicted win rate |
| OptionsBot | 0.50 | Same threshold for options |
| CryptoBot | 0.40 | Lower threshold (crypto has higher base win rate) |

### Fallback Behavior

When the ML model has insufficient training data (fewer than 100 historical trades), it falls back to a **rule-based scoring system** that uses simpler heuristics:
- RSI momentum score
- Volume confirmation score
- Regime alignment score
- Combined into a pseudo-probability

---

## 8.2 RiskAdjustmentEngine

**Purpose:** Dynamic risk multiplier based on recent trading performance

### Risk Multiplier Range

| Multiplier | Meaning |
|------------|---------|
| 0.25 | Maximum risk reduction (very poor recent performance) |
| 1.0 | No adjustment (normal performance) |
| 1.5 | Maximum risk increase (excellent recent performance) |

### Factors

- **Equity Curve**: Trending up = higher multiplier, trending down = lower
- **Drawdown Depth**: Deeper drawdown = lower multiplier
- **Win Rate**: Rolling win rate above/below historical average
- **Sharpe Ratio**: Risk-adjusted return trending

---

## 8.3 BotAllocationModel

**Purpose:** Predict optimal bot for current market conditions

Analyzes current market state and recommends which bot category should receive the most allocation:

| Condition | Recommended Bot |
|-----------|----------------|
| Strong trend, low vol | Momentum |
| Range-bound, compressed vol | Whipsaw |
| Crypto trending | Crypto |
| High IV, catalyst | Options |

The recommendation influences `PortfolioBot`'s budget allocation across bots.

---

## 8.4 RegimeSizer

**Purpose:** Position size multiplier based on macro regime indicators

### Input Features

| Indicator | Weight | Effect |
|-----------|--------|--------|
| VIX | High | High VIX → smaller positions |
| VVIX | Medium | High VVIX → unstable regime, reduce |
| TNX (10Y Yield) | Medium | Rapid yield changes → reduce |
| DXY (Dollar Index) | Low | Strong dollar shifts → adjust |
| MOVE (Bond Vol) | Medium | High bond vol → reduce |

### Output

Position size multiplier: 0.0 (no trading) to 1.5 (aggressive sizing)

**VIX now fetches LIVE** from yfinance using correct symbols (`^VIX`, `^VVIX`, `^TNX`). Previously hardcoded to 18.0 (audit fix C-2).

---

## 8.5 DrawdownPredictor

**Purpose:** Forecast drawdown probability and reduce sizing preemptively

### How It Works

- Analyzes current equity curve, recent trade outcomes, and market regime
- Produces a probability estimate (0–100%) of experiencing a significant drawdown
- When probability exceeds 50%, position sizes are automatically reduced
- At very high probabilities (>80%), may recommend halting new entries

---

## 8.6 Support Services

### TradeOutcomeTracker

Logs the complete lifecycle of every trade for ML model retraining:

```
Entry Signal → Order Placement → Fill → Position Monitoring → Exit → Outcome
     │              │              │            │               │        │
     └──────────────┴──────────────┴────────────┴───────────────┴────────┘
                              All logged for model training
```

Each trade record includes:
- Entry features at the time of signal
- Market conditions during the trade
- Exit reason and P&L
- MFE/MAE (maximum favorable/adverse excursion)

### DriftDetector

Monitors ML models for performance drift:
- Tracks prediction accuracy over rolling windows
- Alerts when model accuracy drops below threshold
- Can trigger model retraining pipeline

---

# 9. Market Intelligence

Trading Hydra's market intelligence system provides AI-powered analysis from multiple data sources to inform trading decisions.

## Architecture

```
┌────────────────────────────────────────────────────────┐
│                 MARKET INTELLIGENCE                      │
│                                                          │
│  ┌──────────────────┐    ┌───────────────────────────┐  │
│  │NewsIntelligence   │    │PremarketIntelligence      │  │
│  │Service            │    │Service                    │  │
│  │                   │    │                           │  │
│  │• Alpaca News API  │    │• 6:00-6:30 AM PST        │  │
│  │• Yahoo Finance    │    │• Gap analysis             │  │
│  │• Per-symbol cache │    │• IV assessment            │  │
│  └──────────────────┘    │• Volume surges            │  │
│                           │• Universe ranking         │  │
│  ┌──────────────────┐    └───────────────────────────┘  │
│  │SentimentScorer    │                                   │
│  │Service            │    ┌───────────────────────────┐  │
│  │                   │    │MacroIntelService           │  │
│  │• OpenAI scoring   │    │                           │  │
│  │• -1.0 to +1.0     │    │• Fed/FOMC scoring         │  │
│  │• Event flags      │    │• Tariff monitoring        │  │
│  └──────────────────┘    │• Geopolitical stress      │  │
│                           │• Regime modifiers         │  │
│  ┌──────────────────┐    └───────────────────────────┘  │
│  │SmartMoneyService  │                                   │
│  │                   │                                   │
│  │• Congress trades  │                                   │
│  │• 13F holdings     │                                   │
│  │• Conviction score │                                   │
│  └──────────────────┘                                   │
└────────────────────────────────────────────────────────┘
```

---

## 9.1 NewsIntelligenceService

**File:** `services/news_intelligence.py`
**Purpose:** Aggregate and analyze news from multiple sources

### Data Sources

| Source | Data | Update Frequency |
|--------|------|-----------------|
| Alpaca News API | Real-time news feed | Per-request |
| Yahoo Finance | Financial news articles | Per-request |

### Caching

- Per-symbol news cache with configurable TTL
- Prevents redundant API calls for the same symbol within the cache window
- Cache key: `news:{symbol}:{date}`

### Output

Provides structured news data for each symbol:
- Headline text
- Source and publication time
- Relevance score
- Category (earnings, M&A, regulatory, etc.)

---

## 9.2 SentimentScorerService

**File:** `services/sentiment_scorer.py`
**Purpose:** AI-powered sentiment scoring using OpenAI

### How It Works

1. Receives news headlines and articles for a symbol
2. Sends to OpenAI API for sentiment analysis
3. Returns a sentiment score from **-1.0 (very bearish) to +1.0 (very bullish)**

### Event Flags

The sentiment scorer also detects and flags specific events:

| Flag | Description | Impact |
|------|-------------|--------|
| `earnings` | Upcoming or recent earnings report | May trigger earnings blackout |
| `lawsuit` | Legal action against the company | Generally bearish |
| `FDA` | FDA approval/rejection news | High impact for biotech |
| `merger` | M&A activity | Can move stock significantly |
| `dividend` | Dividend announcement/change | Moderate impact |

### Circuit Breaker Protection

OpenAI calls are protected by the `openai_sentiment` circuit breaker. If the API is down, sentiment scoring gracefully degrades to neutral (0.0) rather than blocking trades.

---

## 9.3 SmartMoneyService

**File:** `services/smart_money.py`
**Purpose:** Track institutional and Congressional trading activity

### Data Sources

#### Congress Trades
- Monitors publicly disclosed Congressional stock trades
- Members are required to disclose trades within 45 days
- Historically, Congressional trades have shown above-market returns

#### 13F Institutional Holdings
- Quarterly filings from institutional investors (>$100M AUM)
- Shows what hedge funds, mutual funds, and pension funds own
- Changes in holdings indicate institutional sentiment

### Scoring

| Score | Description |
|-------|-------------|
| **Conviction Score** | How strongly institutions are positioned (based on position size relative to fund) |
| **Convergence Score** | How many institutional actors agree (multiple funds buying = high convergence) |

### Integration with Risk

SmartMoneyService feeds into `RiskOrchestratorIntegration`:
- Strong institutional buying = positive signal (may increase position size)
- Institutional selling = negative signal (may reduce or skip entry)
- Congressional trades provide directional bias

---

## 9.4 MacroIntelService

**File:** `services/macro_intel.py`
**Purpose:** Monitor macroeconomic conditions and provide regime modifiers

### Monitoring Areas

#### Fed/FOMC Analysis
- Tracks Federal Reserve communications for hawkish/dovish signals
- FOMC meeting dates and rate decision expectations
- Dot plot changes and forward guidance

#### Tariff Monitoring
- Trade policy changes and tariff announcements
- Country-specific trade tensions
- Impact on specific sectors (e.g., tech, manufacturing)

#### Geopolitical Stress
- Major geopolitical events (conflicts, elections, sanctions)
- Impact on market risk premium
- Safe-haven flows (gold, treasuries, USD)

### Regime Modifiers

MacroIntelService outputs one of three regime modifiers:

| Regime | Description | Trading Impact |
|--------|-------------|----------------|
| `NORMAL` | Standard macro environment | No adjustment |
| `CAUTION` | Elevated uncertainty (upcoming FOMC, trade tensions) | Reduced position sizes |
| `STRESS` | Active macro stress (surprise rate hike, trade war escalation) | Significantly reduced or halted trading |

---

## 9.5 PremarketIntelligenceService

**File:** `services/premarket_intelligence.py`
**Purpose:** Multi-factor pre-market analysis for symbol ranking

### Active Window

- **6:00–6:30 AM PST** (configurable via `market_hours.pre_market_intel_start/end`)
- Runs BEFORE market open to prepare the day's trading universe

### Analysis Factors

| Factor | Weight | Description |
|--------|--------|-------------|
| Gap Analysis | High | Overnight price gaps (gap up/down magnitude) |
| IV Assessment | Medium | Current implied volatility vs historical |
| Volume Surges | High | Pre-market volume relative to average |
| Event Flags | High | Earnings, FDA, ex-dividend dates |
| News Catalyst | Medium | Breaking news sentiment |

### Output

1. **Universe Ranking**: All symbols ranked by composite score
2. **Bot-Specific Eligibility**: Which bots can trade which symbols
3. **Hard Liquidity Gates**: Symbols below minimum volume/spread thresholds are excluded regardless of score

---

# 10. State & Persistence

Trading Hydra uses multiple persistence layers, each optimized for its specific use case.

## Persistence Architecture

```
┌─────────────────────────────────────────────────────┐
│                 PERSISTENCE LAYERS                    │
│                                                       │
│  ┌────────────────────────────────────────────────┐  │
│  │ SQLite: trading_state.db                       │  │
│  │ • WAL mode (concurrent reads + single writer)  │  │
│  │ • Thread-local connections                     │  │
│  │ • Periodic WAL checkpoint (every 5 minutes)    │  │
│  │ • Graceful shutdown with final checkpoint      │  │
│  │                                                │  │
│  │ Tables:                                        │  │
│  │  • state (key-value store)                     │  │
│  │  • order_ids (idempotency tracking)            │  │
│  │  • exit_trades (completed trade lifecycle)     │  │
│  │  • exit_decisions (forensic decision log)      │  │
│  │  • exit_options_context (options Greeks)        │  │
│  └────────────────────────────────────────────────┘  │
│                                                       │
│  ┌────────────────────────────────────────────────┐  │
│  │ SQLite: metrics.db                              │  │
│  │ • Daily performance snapshots                  │  │
│  │ • Regime snapshots with enum→string conversion │  │
│  │ • Wired to orchestrator finalize step (D-1)    │  │
│  └────────────────────────────────────────────────┘  │
│                                                       │
│  ┌────────────────────────────────────────────────┐  │
│  │ JSONL: logs/app.jsonl                          │  │
│  │ • Structured event logging                     │  │
│  │ • Automatic size-based rotation (10MB)         │  │
│  │ • Retention: 7 rotated files                   │  │
│  │ • Quiet mode for dashboard display             │  │
│  └────────────────────────────────────────────────┘  │
│                                                       │
│  ┌────────────────────────────────────────────────┐  │
│  │ Runtime Files (JSON)                           │  │
│  │ • runtime/regime_state.json                    │  │
│  │ • runtime/pending_trades.json                  │  │
│  │ • state/*.json (persisted component states)    │  │
│  └────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

---

## 10.1 SQLite: trading_state.db

### Core State Store

The primary persistence layer is a key-value store in SQLite:

```sql
CREATE TABLE state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,      -- JSON-serialized value
    updated_at TEXT NOT NULL   -- ISO timestamp
);
```

**Key patterns:**
- `cooldown:{bot_id}:{symbol}` — Last trade timestamp for cooldown enforcement
- `momentum:trades:{YYYYMMDD}` — Daily trade counter (atomic increment)
- `GLOBAL_TRADING_HALT` — Global halt flag
- `halt.reason`, `halt.halted_at`, `halt.expires_at` — Halt details
- `health.*` — API health tracking
- `partial_exit:{position_key}` — Partial exit state (audit fix D-4)
- `exitbot:first_seen:{symbol}` — Position first-seen timestamps

### Thread Safety

SQLite connections are **thread-local** to prevent cursor corruption:

```python
_thread_local = threading.local()

def _get_connection():
    if not hasattr(_thread_local, 'conn') or _thread_local.conn is None:
        conn = sqlite3.connect(_db_path, timeout=60.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=60000")
        conn.execute("PRAGMA synchronous=NORMAL")
        _thread_local.conn = conn
    return _thread_local.conn
```

Each thread gets its own connection. WAL (Write-Ahead Logging) mode allows multiple connections to read simultaneously while one writes.

### Atomic Operations

The `atomic_increment()` function provides true database-level atomicity:

```python
def atomic_increment(key, max_value=None):
    conn = _get_connection()
    conn.execute("BEGIN IMMEDIATE")  # Acquires write lock immediately
    # ... check current value, increment if under max ...
    conn.execute("COMMIT")
    return (success, new_value)
```

`BEGIN IMMEDIATE` acquires the write lock at the start of the transaction, preventing any race conditions between threads.

### WAL Checkpoint

WAL files can grow indefinitely if not checkpointed. Trading Hydra runs periodic checkpoints:

- **Every 5 minutes**: Background thread calls `PRAGMA wal_checkpoint(TRUNCATE)`
- **On shutdown**: Final checkpoint flushes all pending writes
- **On startup**: Integrity check verifies database health

### Database Corruption Recovery

If the database is found to be corrupted at startup:
1. Creates a backup of the corrupted file in `state/corrupt_backups/`
2. Removes the corrupted database and WAL/SHM files
3. Creates a fresh database on next access
4. Logs the recovery event

### Order Idempotency

Every order gets a deterministic `client_order_id`:

```python
client_order_id = f"{bot_id}:{symbol}:{YYYYMMDD}:{signal_id}"
```

Before placing any order, the system checks `order_ids` table to prevent duplicate submissions. This prevents double-ordering if a loop cycle restarts.

### Connection Registry

All thread-local connections are registered in a global registry:

```python
_connection_registry: Dict[int, sqlite3.Connection] = {}
```

On shutdown, `close_all_connections()` iterates through the registry and closes every connection, ensuring WAL is fully flushed.

---

## 10.2 SQLite: metrics.db

### Daily Performance Snapshots

The `MetricsRepository` saves daily performance data:

- **daily_metrics**: One row per day with P&L, trade count, win rate, Sharpe
- **regime_snapshots**: Market regime state at each snapshot

**Wiring:** `MetricsRepository.save_daily_metrics()` and `save_regime_snapshot()` are called from `orchestrator._step_finalize()` with a daily idempotency check (audit fix D-1).

**Enum conversion:** `VolatilityRegime` enum values are extracted using `getattr()` chain with `.value` to produce SQLite-compatible strings.

---

## 10.3 JSONL Logging: logs/app.jsonl

### Structure

Each line is a JSON object:

```json
{"ts": "2026-02-11T14:30:00.123Z", "event": "trade_executed", "bot": "momentum", "symbol": "AAPL", "side": "buy", "qty": 10, "price": 185.50}
{"ts": "2026-02-11T14:30:02.456Z", "event": "exit_decision", "position_key": "AAPL_long", "action": "HOLD", "health_score": 78}
```

### Log Rotation

| Parameter | Value |
|-----------|-------|
| Max file size | 10 MB |
| Rotation check interval | Every 100 log entries |
| Max rotated files | 7 (1 week of daily rotation) |
| Rotation naming | `app.jsonl.1`, `app.jsonl.2`, ... |

### Console Output Modes

| Mode | Behavior |
|------|----------|
| Verbose (default) | All events printed to console |
| Quiet | Only errors, halts, and failures printed |
| Suppress All | No console output (for in-place dashboard mode) |

All modes always write to the JSONL file.

---

## 10.4 Runtime Files

### regime_state.json
- Current market regime classification
- VIX, VVIX, TNX values
- Regime change history

### pending_trades.json
- Trades currently being processed
- Used for recovery after unexpected restart

### state/ Directory
- Component-specific persisted state files
- ProfitSniper ratchet positions
- PartialExitManager partial exit tracking (audit fix D-4)
- ExitBot position first-seen timestamps

---

# 11. Configuration System

## Configuration Files

Trading Hydra's behavior is entirely driven by YAML configuration files. There are no hardcoded trading parameters in the codebase.

### 11.1 settings.yaml — Global Settings

The master configuration file containing system-wide parameters:

```yaml
system:
  timezone: "America/Los_Angeles"
  loop_interval_seconds: 15

trading:
  paper_trading: true
  global_halt: false
  fail_closed: true

risk:
  global_max_daily_loss_pct: 5.0    # SAFETY: Max 5% daily loss
  daily_budget_pct: 10.0            # SAFETY: Max 10% budget per day
  cash_reserve_pct: 10.0            # SAFETY: Always keep 10% cash
  max_position_pct: 8.0             # Max single position
  dd_threshold_reduce: 5.0          # Reduce size at 5% drawdown
  dd_threshold_halt: 15.0           # Halt at 15% drawdown
  budget_ceiling_usd: 25000
  budget_floor_usd: 500

market_hours:
  market_open: "06:30"              # PST
  market_close: "13:00"             # PST
  pre_market_start: "01:00"
  after_hours_end: "17:00"
  pre_market_intel_start: "06:00"
  pre_market_intel_end: "06:30"

health:
  max_api_failures_in_window: 5
  max_connection_failures_in_window: 3

caching:
  market_closed_poll_seconds: 60

ml:
  signal_service_enabled: true
  risk_adjustment_enabled: true
  regime_sizer_enabled: true
  drawdown_predictor_enabled: true
  bot_allocation_enabled: true
  min_training_trades: 100
```

### 11.2 bots.yaml — Per-Bot Parameters

Each bot has its own configuration section:

```yaml
momentum:
  enabled: true
  max_trades_per_day: 6
  ml_entry_threshold: 0.50
  sma_short: 10
  sma_long: 20
  rsi_period: 14
  session:
    window_start: "06:30"
    window_end: "13:00"

cryptobot:                         # Note: "cryptobot" not "crypto_bot" (audit fix C-1)
  enabled: true
  max_concurrent_positions: 8
  max_quote_age_seconds: 300
  cooldown_seconds: 180
  signal:
    rsi_period: 14
    macd_fast: 12
    macd_slow: 26

bouncebot:                         # Note: "bouncebot" not "bounce_bot"
  enabled: true
  session:
    window_start: "01:00"
    window_end: "05:30"

twentymin:
  enabled: true
  trade_execution_start: "06:25"
  warmup_start: "05:30"
  max_trades_per_day: 4

optionsbot:                        # Note: "optionsbot" not "options_bot"
  enabled: true
  session:
    trade_start: "06:30"           # Note: nested path (audit fix H-15)
    trade_end: "13:00"

optionsbot_0dte:
  enabled: true
```

**Critical naming convention:** Bot config keys use concatenated names (`cryptobot`, `bouncebot`, `optionsbot`) NOT underscored names (`crypto_bot`, `bounce_bot`). This mismatch was audit finding C-1.

### 11.3 account_modes.yaml — Account Mode Parameters

Three-tier account mode configuration:

```yaml
standard:                          # $47,000+
  daily_budget_pct: 10.0
  max_position_pct: 8.0
  all_bots_enabled: true
  risk_multiplier: 1.0

small:                             # $10,000-$47,000
  daily_budget_pct: 8.0
  max_position_pct: 6.0
  disabled_bots: ["optionsbot_0dte"]
  risk_multiplier: 0.7

micro:                             # <$10,000
  daily_budget_pct: 5.0
  max_position_pct: 4.0
  disabled_bots: ["optionsbot", "optionsbot_0dte", "hailmary"]
  risk_multiplier: 0.4
```

**Safety invariant:** Mode configs only merge into the bots config namespace, NOT into `settings.yaml`. Safety settings cannot be overridden by mode files.

### 11.4 ticker_universe.yaml — Trading Universe

Defines the complete set of tradeable symbols organized by tier:

```yaml
tiers:
  tier1:                           # Highest liquidity, tightest spreads
    - AAPL
    - MSFT
    - GOOGL
    - AMZN
    # ...

  tier2:                           # Good liquidity
    - CRM
    - SHOP
    # ...

  tier3:                           # Lower liquidity, wider spreads
    # ...

crypto:
  - BTC/USD
  - ETH/USD
  - SOL/USD
  # ... (22 pairs total)

etf_skip_earnings:                 # ETFs to skip for earnings calendar
  - SPYM
  - SCHG
  - IWF
  # ... (16 total, audit fix D-7)
```

### 11.5 watchlists.yaml — Named Watchlists

```yaml
watchlists:
  tech_leaders:
    symbols: [AAPL, MSFT, GOOGL, AMZN, META, NVDA]
    tags: [tech, mega_cap]

  momentum_candidates:
    symbols: [TSLA, AMD, COIN]
    tags: [high_beta, momentum]

  dividend_plays:
    symbols: [KO, JNJ, PG]
    tags: [dividend, defensive]
```

### 11.6 sensors.yaml — Sensor Configuration

```yaml
intervals:
  quote_refresh: 30                # Seconds between quote updates
  bar_1m_refresh: 60
  bar_5m_refresh: 300
  bar_1d_refresh: 3600
  breadth_refresh: 120
  regime_refresh: 300

ttls:
  quote: 30
  bar_1m: 60
  bar_5m: 300
  bar_1d: 3600
```

---

## 11.7 Config Doctor

The Config Doctor runs at startup and validates configuration consistency:

### Validation Checks

1. **Key Naming**: Verifies that keys referenced in code match keys in config files
   - Checks `cryptobot`, `bouncebot`, `optionsbot` (not underscored variants — audit fix H-9)

2. **Value Sanity**: Ensures values are within reasonable bounds
   - `global_max_daily_loss_pct` ≤ 10%
   - `daily_budget_pct` ≤ 20%
   - `cash_reserve_pct` ≥ 5%

3. **Required Fields**: Checks that all required config fields are present

4. **Cross-File Consistency**: Verifies that references between config files are valid
   - Ticker symbols in watchlists exist in universe
   - Bot names in modes match bot config keys

### Severity Handling

| Severity | Behavior |
|----------|----------|
| HIGH | **HARD FAIL** — System will not start |
| MEDIUM | Warning logged, system continues |
| LOW | Info logged, system continues |

---

# 12. Safety Controls

## 12.1 HaltManager

**File:** `core/halt.py`
**Purpose:** Global trading halt on critical failures

### Halt Sources

| Source | Trigger | Cooloff |
|--------|---------|---------|
| Daily Loss Limit | P&L exceeds `global_max_daily_loss_pct` (5%) | 60 minutes |
| Auth Failure | 401/403 from Alpaca API | Manual clear required |
| API Failures | Consecutive failures exceed threshold | 60 minutes |
| Connection Failures | Network/timeout failures exceed threshold | 60 minutes |
| Config Override | `trading.global_halt: true` in settings.yaml | Until config changed |
| Manual | Via dashboard or state store | Configurable |

### Halt State

Halt state is stored in SQLite as three state keys:
- `GLOBAL_TRADING_HALT` — Boolean flag (single source of truth)
- `halt.reason` — Human-readable reason string
- `halt.halted_at` — ISO timestamp when halt was activated
- `halt.expires_at` — ISO timestamp when halt auto-clears

### Auto-Clear

Halts with a cooloff period auto-clear after expiry:
```python
def clear_if_expired(self):
    expires = datetime.fromisoformat(expires_str)
    if datetime.now() > expires:
        self.clear_halt()
        return True
```

### Halt Does NOT Stop ExitBot

When a halt is active:
- ✅ ExitBot continues running (manages existing positions)
- ✅ Stop-losses continue to execute
- ✅ Take-profits continue to execute
- ❌ No NEW positions can be opened
- ❌ No new signals are generated

---

## 12.2 Fail-Closed Architecture

The fail-closed principle is enforced throughout the system:

```
ERRORS IN:                    RESULT:
─────────────────────────────────────────
Risk evaluation          →    Trade BLOCKED
Budget calculation       →    Fallback to MIN (not MAX)
ML model prediction      →    Conservative default
Market data fetch        →    Signal rejected (stale)
Config validation        →    System won't start (HIGH)
API call                 →    Circuit breaker incremented
Position sizing          →    Minimum position size
Greek calculation        →    Trade blocked (options)
```

### What Changed in the Audit

Before the audit, several components failed OPEN:
- Risk orchestrator returned `ALLOW` on exceptions → Now returns `BLOCK` (C-6)
- Budget fallback used `max()` → Now uses `min()` (C-7)
- Safety limits at testing extremes → Restored to production values (C-5)
- `fail_closed` config was `false` → Changed to `true` (C-5)

---

## 12.3 ExitBot Kill-Switch

ExitBot has built-in kill-switch functionality:
- Can be triggered by the risk layer to force-close all positions
- Activated when daily loss limit is breached catastrophically
- Logs a WARNING at startup when kill conditions are disabled (audit fix H-13)

---

## 12.4 StrategyKillSwitch

Per-strategy kill switches that can disable individual bots:
- Persisted in state store (survives restarts)
- Can be triggered by:
  - Edge decay detection (strategy losing its edge)
  - Manual operator action (via dashboard)
  - Cross-bot conflict detection
  - Excessive losses on a specific strategy

---

## 12.5 Account Modes

Three-tier account mode system based on current equity:

| Mode | Equity Range | Characteristics |
|------|-------------|-----------------|
| **Standard** | $47,000+ | Full allocation, all bots enabled, 1.0x risk multiplier |
| **Small** | $10,000–$47,000 | Reduced positions, some bots disabled (0DTE), 0.7x risk |
| **Micro** | <$10,000 | Minimal positions, options disabled, 0.4x risk |

Mode detection runs at the start of every loop cycle. Mode switches are logged for audit trail.

---

## 12.6 Production Safety Limits

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `global_max_daily_loss_pct` | 5% | Max daily loss before halt (~$2,350) |
| `daily_budget_pct` | 10% | Max budget per day (~$4,700) |
| `cash_reserve_pct` | 10% | Always keep in cash (~$4,700) |
| `max_position_pct` | 8% | Max single position (~$3,760) |
| `dd_threshold_reduce` | 5% | Reduce sizing at 5% drawdown |
| `dd_threshold_halt` | 15% | Halt trading at 15% drawdown |
| `fail_closed` | true | Block trades on any risk error |
| `max_spread_pct` | 15% | Max bid-ask spread for entry |
| `catastrophic_stop` | 12–35% | Absolute loss floor per position |

---

# 13. Scripts Reference

## Operational Scripts

### run.sh
Main entry point for manual trading system startup:
- Virtual environment detection and activation
- Environment variable loading from `.env`
- Argument parsing: `--paper` (default), `--live`, `--dry-run`
- Launches the Python orchestrator

### start-trading.sh
Production startup script:
- PID file locking (prevents double-start)
- Background mode support
- Log file redirection
- Calls `run.sh` with production parameters

### stop-trading.sh
Graceful shutdown:
- Reads PID file
- Sends SIGTERM for graceful shutdown
- Timeout fallback to SIGKILL if process doesn't exit

### healthcheck.sh
Production health monitoring:
- Checks if process is running (PID file)
- Verifies API connectivity
- Checks data freshness
- Returns exit code for monitoring systems

## Diagnostic Scripts

### diagnose_signals.py
Signal generation diagnostics:
- Runs signal generation for all bots without executing trades
- Shows which signals would be generated and why
- Useful for debugging "why isn't the bot trading?"

### diagnose_api.py
API connection diagnostics:
- Tests Alpaca API connectivity
- Verifies authentication credentials
- Checks market data availability
- Tests order placement (paper mode)

### bypass_order_test.py
ExitBot testing utility:
- Bypasses safety gates for controlled testing
- Places test orders to verify ExitBot monitoring
- Useful for testing stop-loss and take-profit mechanics

## Backtesting Scripts

### run_*_backtest.py
Historical backtesting for individual strategies:
- `run_sniper_backtest.py` — ProfitSniper parameter optimization
- `run_hailmary_backtest.py` — HailMary options strategy
- `run_options_backtest.py` — General options strategies
- `run_twentymin_backtest.py` — TwentyMinuteBot opening range
- `run_fullstack_backtest.py` — Full system backtest (all bots)
- `run_tournament_backtest.py` — Strategy tournament (head-to-head)

### optimize_*.py
Parameter optimization scripts:
- Grid search over parameter space
- Uses `BacktestEngine` for historical simulation
- Outputs optimal parameters for config files

### run_dynamic_optimizer.py
Enhanced optimizer with profitability metrics:
- Optimizes for risk-adjusted returns (Sharpe ratio)
- Considers drawdown characteristics
- Enhanced with profit factor and win rate metrics

## Testing Scripts

### test_all_bots.py
End-to-end bot testing:
- Initializes each bot with test configuration
- Runs signal generation with mock market data
- Verifies order placement logic
- Reports pass/fail for each bot

## ML Training Scripts

Located in `scripts/ml/`:
- Model training pipelines for all 5 ML models
- Feature extraction and dataset preparation
- Cross-validation and hyperparameter tuning
- Model serialization and deployment

---

# 14. Known Issues & Future Work

## Resolved Issues (Phase 2 of Audit)

All 7 previously deferred issues from the February 2026 audit have been resolved:

| ID | Issue | Resolution |
|----|-------|------------|
| D-1 | `metrics.db` tables empty | Wired `save_daily_metrics()` and `save_regime_snapshot()` into orchestrator finalize step |
| D-2 | RSI uses SMA instead of Wilder's EMA | Converted all 6 RSI implementations to Wilder's smoothing |
| D-3 | `TradeMemoryEngine` cache TTL not checked | Per-key TTL with automatic eviction on retrieval |
| D-4 | `PartialExitManager` state not persisted | State saved/restored via `get_state`/`set_state` |
| D-5 | `WhipsawTrader` uses `datetime.now()` | All 5 instances changed to `datetime.utcnow()` |
| D-6 | Sensor output uses relative `./runtime` path | Changed to absolute path resolution |
| D-7 | ETF symbols trigger 404 on earnings calendar | Added 16 missing ETF symbols to skip set |

## Remaining Known Issues

### TradeMemoryEngine Cache TTL
- **Status:** Implemented but untested in production
- **Risk:** Low — TTL eviction works in unit tests but production behavior unverified
- **Impact:** Memory could grow if TTL eviction has edge cases
- **Next step:** Monitor memory usage in production over 1-week window

### PartialExitManager State Edge Cases
- **Status:** State persistence implemented (D-4)
- **Risk:** Low — Edge case where partial exit state could be stale after extended downtime
- **Impact:** A position partially exited before restart might not resume tracking correctly
- **Next step:** Add state age validation on restore

### Metrics.db Recording Completeness
- **Status:** Partially fixed (D-1)
- **Risk:** Low — Daily snapshots record correctly but intra-day granularity is limited
- **Impact:** Some analytics require more frequent snapshots
- **Next step:** Add hourly snapshot option via config

## Future Work

### Short-Term (Next Sprint)

1. **Integration Tests for Config Keys** — Automated test that loads `bots.yaml` and verifies every key referenced in `dedicated_threads.py` exists
2. **Startup Self-Test** — On boot, place and immediately cancel a test order to verify API connectivity, permissions, and account status
3. **Config Schema Validation** — Pydantic or Cerberus schema for `settings.yaml` and `bots.yaml` to catch type errors at load time

### Medium-Term (Next Quarter)

4. **Real-Time P&L Dashboard** — WebSocket-based live P&L display instead of polling
5. **Multi-Account Support** — Run multiple Alpaca accounts from a single instance
6. **Alerting Integration** — Slack/Discord/email alerts for halt events and significant trades
7. **Backtest-to-Live Pipeline** — Automated pipeline from backtest optimization to live config deployment

### Long-Term (Roadmap)

8. **Strategy Marketplace** — Pluggable strategy architecture for community-contributed bots
9. **Distributed Architecture** — Separate data, signal, and execution services for horizontal scaling
10. **Options Greeks Engine** — Native Greeks calculation instead of relying on broker data
11. **Advanced ML** — Transformer-based models for sequence prediction, reinforcement learning for exit timing

---

# Appendix A: Data Flow Diagram

```
                         EXTERNAL DATA SOURCES
                    ┌─────────────────────────────────┐
                    │ Alpaca API    yfinance   OpenAI  │
                    │ (quotes,      (VIX,      (NLP,   │
                    │  orders,      earnings)  sent.)  │
                    │  account)                        │
                    └──────┬──────────┬────────┬───────┘
                           │          │        │
                    ┌──────▼──────────▼────────▼───────┐
                    │        MARKET INTELLIGENCE         │
                    │ NewsIntel │ Sentiment │ SmartMoney │
                    │ PreMarket │ MacroIntel│            │
                    └──────────────────┬───────────────┘
                                       │
                    ┌──────────────────▼───────────────┐
                    │          HYDRA SENSORS            │
                    │ Watchlist │ Cache │ Indicators    │
                    │ Breadth   │ Regime│               │
                    └──────────────────┬───────────────┘
                                       │
                    ┌──────────────────▼───────────────┐
                    │          ML SYSTEMS               │
                    │ Signal │ Risk │ Regime │ Drawdown │
                    └──────────────────┬───────────────┘
                                       │
         ┌─────────────────────────────▼──────────────────────────┐
         │                      ORCHESTRATOR                       │
         │  Initialize → HaltCheck → Portfolio → Execute → Finalize│
         └────────────────────────────┬────────────────────────────┘
                                      │
                    ┌─────────────────▼────────────────┐
                    │         TRADING BOTS              │
                    │ Momentum│Crypto│Whipsaw│Bounce    │
                    │ TwentyMin│OptionsBot│HailMary    │
                    └─────────────────┬────────────────┘
                                      │
                    ┌─────────────────▼────────────────┐
                    │          RISK LAYER               │
                    │ PolicyGate → RiskOrchestrator     │
                    │ Budget → Sizing → CircuitBreaker  │
                    └─────────────────┬────────────────┘
                                      │
                    ┌─────────────────▼────────────────┐
                    │          ALPACA API               │
                    │ Order Placement & Management      │
                    └─────────────────┬────────────────┘
                                      │
                    ┌─────────────────▼────────────────┐
                    │          EXIT INTELLIGENCE        │
                    │ ExitBot v2 │ ProfitSniper         │
                    │ (monitors ALL positions)          │
                    └─────────────────┬────────────────┘
                                      │
                    ┌─────────────────▼────────────────┐
                    │          PERSISTENCE              │
                    │ SQLite │ JSONL │ Metrics │ State  │
                    └──────────────────────────────────┘
```

---

# Appendix B: Key File Reference

## Core Files

| File | Purpose |
|------|---------|
| `orchestrator.py` | Main trading loop (5-step cycle) |
| `core/config.py` | Configuration loading, validation, Config Doctor |
| `core/state.py` | SQLite state store, atomic operations, WAL management |
| `core/halt.py` | HaltManager for global trading halt |
| `core/clock.py` | MarketClock for timezone-aware time handling |
| `core/health.py` | HealthMonitor for API and data freshness tracking |
| `core/logging.py` | JSONL structured logger with rotation |
| `core/staleness.py` | Data staleness detection with session-aware TTLs |

## Trading Bots

| File | Purpose |
|------|---------|
| `bots/momentum_bot.py` | Trend-following on screened stocks |
| `bots/crypto_bot.py` | 24/7 crypto trading (22 pairs) |
| `bots/bounce_bot.py` | Mean-reversion on oversold conditions |
| `bots/twenty_minute_bot.py` | Opening range pattern trading |
| `bots/options_bot.py` | Options strategy execution (including HailMary) |
| `strategy/whipsaw_trader.py` | Range-bound market detection and trading |

## Services

| File | Purpose |
|------|---------|
| `services/exitbot.py` | ExitBot v2 Elite exit intelligence |
| `services/exit_decision.py` | Exit decision engine and records |
| `services/profit_sniper.py` | Profit-priority exit layer |
| `services/partial_exit.py` | Partial exit management |
| `services/trade_memory.py` | TradeMemoryEngine (31-day rolling) |
| `services/forward_projection.py` | Forward projection engine |
| `services/alpaca_client.py` | Alpaca API client wrapper |
| `services/market_regime.py` | VIX/VVIX/TNX regime detection |
| `services/dedicated_threads.py` | Bot thread management |
| `services/news_intelligence.py` | News aggregation service |
| `services/sentiment_scorer.py` | OpenAI sentiment analysis |
| `services/smart_money.py` | Congress trades + 13F tracking |
| `services/macro_intel.py` | Fed/tariff/geopolitical analysis |
| `services/premarket_intelligence.py` | Pre-market universe ranking |
| `services/alerts.py` | Alert notification service |
| `services/earnings_calendar.py` | Earnings date tracking |

## Risk Management

| File | Purpose |
|------|---------|
| `risk/policy_gate.py` | Mandatory pre-trade checkpoint |
| `risk/risk_orchestrator.py` | Central risk evaluation hub |
| `risk/dynamic_budget.py` | Budget scaling and drawdown management |
| `risk/position_sizer.py` | Institutional position sizing |
| `risk/circuit_breaker.py` | Per-service circuit breakers |
| `risk/correlation.py` | Portfolio correlation monitoring |
| `risk/pnl_monitor.py` | P&L distribution analysis |
| `risk/greek_monitor.py` | Options Greek exposure tracking |

## ML

| File | Purpose |
|------|---------|
| `ml/signal_service.py` | LightGBM trade scoring |
| `ml/risk_adjustment.py` | Dynamic risk multiplier |
| `ml/bot_allocation.py` | Optimal bot prediction |
| `ml/regime_sizer.py` | Macro regime position sizing |
| `ml/drawdown_predictor.py` | Drawdown probability forecasting |
| `ml/feature_extractor.py` | Feature engineering for ML models |
| `ml/drift_detector.py` | Model performance drift monitoring |

## Sensors

| File | Purpose |
|------|---------|
| `sensors/manager.py` | SensorsManager background thread |
| `sensors/indicators.py` | Technical indicator calculations |
| `sensors/breadth.py` | Market breadth analysis |
| `sensors/watchlist.py` | Watchlist management |
| `sensors/cache.py` | Market data caching |

## Configuration

| File | Purpose |
|------|---------|
| `config/settings.yaml` | Global settings and safety limits |
| `config/bots.yaml` | Per-bot parameters |
| `config/modes/account_modes.yaml` | Account mode tiers |
| `config/ticker_universe.yaml` | Trading universe |
| `config/watchlists.yaml` | Named watchlists |
| `config/sensors.yaml` | Sensor configuration |

---

# Appendix C: Glossary

| Term | Definition |
|------|------------|
| **ATR** | Average True Range — volatility measurement over N bars |
| **Authority** | Exit priority level in ExitBot's decision hierarchy |
| **Bracket Order** | Three-legged order: entry + take-profit + stop-loss (OCO) |
| **Circuit Breaker** | Pattern that prevents calls to failing services |
| **Config Doctor** | Startup validation of configuration consistency |
| **DTE** | Days to Expiration (options) |
| **Fail-Closed** | Design where errors block actions (conservative default) |
| **Fail-Open** | Design where errors allow actions (used only for sensors) |
| **IV** | Implied Volatility — market's expectation of future volatility |
| **Kelly Criterion** | Optimal bet sizing formula based on edge and odds |
| **MAE** | Maximum Adverse Excursion — worst drawdown during a trade |
| **MFE** | Maximum Favorable Excursion — best profit during a trade |
| **NAV** | Net Asset Value — total account equity |
| **OCO** | One-Cancels-Other — linked orders where filling one cancels the other |
| **PST** | Pacific Standard Time (America/Los_Angeles) |
| **Regime** | Market condition classification (risk_on, risk_off, neutral) |
| **RSI** | Relative Strength Index — momentum oscillator (0-100) |
| **Run ID** | Unique identifier for each orchestrator loop cycle |
| **SMA** | Simple Moving Average |
| **TP** | Take Profit — price level to exit with profit |
| **VWAP** | Volume-Weighted Average Price |
| **WAL** | Write-Ahead Logging — SQLite journaling mode for concurrent access |
| **Whipsaw** | Rapid reversals in a range-bound market |
| **Wilder's EMA** | Exponential moving average variant used in RSI calculation |

---

# Appendix D: Audit Summary

The system underwent a comprehensive **9-team parallel audit** on February 11, 2026:

| Severity | Found | Fixed | Deferred |
|----------|-------|-------|----------|
| CRITICAL | 8 | 8 | 0 |
| HIGH | 17 | 17 | 0 |
| MEDIUM | ~40 | ~20 | ~20 |
| LOW | ~40 | 0 | ~40 |
| **Total** | **~105** | **~45** | **~60** |

**All CRITICAL and HIGH issues were resolved.** Key fixes:
- C-1: Config key naming mismatches (4 bot threads misconfigured)
- C-2: VIX hardcoded to 18.0 (market regime always "normal")
- C-3: Daily trade limits never enforced (unlimited trades)
- C-4: Crypto quote staleness too aggressive (80%+ signals rejected)
- C-5: Safety limits at testing extremes (100% daily loss allowed)
- C-6: Risk system failed OPEN (errors allowed trades)
- C-7: Fallback budget used max() instead of min()
- C-8: AlpacaAccount missing key fields (P&L always $0)

All 7 Phase 2 deferred items (D-1 through D-7) have also been resolved.

For the complete audit report, see `AUDIT_2026-02-11.md`.

---

*End of System Guide. Last updated: February 11, 2026.*
