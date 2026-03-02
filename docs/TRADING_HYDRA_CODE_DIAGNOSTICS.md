# Trading Hydra Enterprise Code Diagnostics & Integration Reference

## CLASSIFICATION: ENTERPRISE DEVELOPER GUIDE
**Version**: 1.0  
**Last Updated**: January 2026  
**Purpose**: Debug, diagnose, and fix code issues in Trading Hydra

---

# Table of Contents

1. [Module Dependency Map](#1-module-dependency-map)
2. [Critical Call Chains](#2-critical-call-chains)
3. [Error Pattern Catalog](#3-error-pattern-catalog)
4. [Debug Recipes](#4-debug-recipes)
5. [Integration Points Reference](#5-integration-points-reference)
6. [State Flow Diagrams](#6-state-flow-diagrams)
7. [Code Fix Examples](#7-code-fix-examples)
8. [Quick Reference Card](#8-quick-reference-card)

---

# 1. Module Dependency Map

## 1.1 Four-Tier Architecture

Trading Hydra follows a strict layered architecture. Understand these layers to trace issues effectively.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           TIER 4: ORCHESTRATION                              │
│  main.py → orchestrator.py → coordinates all lower tiers                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           TIER 3: TRADING BOTS                               │
│  momentum_bot.py │ options_bot.py │ crypto_bot.py │ twenty_minute_bot.py    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           TIER 2: CORE SERVICES                              │
│  exitbot.py │ portfolio.py │ execution.py │ alpaca_client.py                │
│  market_regime.py │ decision_tracker.py │ trailing_stop.py │ position_sizer │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           TIER 1: FOUNDATION                                 │
│  logging.py │ state.py │ config.py │ clock.py │ halt.py │ health.py         │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 1.2 Tier 1: Foundation Modules (Fix These First)

These modules have NO internal dependencies. If they fail, everything above fails.

| Module | Purpose | Singleton Function | Critical State |
|--------|---------|-------------------|----------------|
| `core/logging.py` | JSONL structured logging | `get_logger()` | None (file-based) |
| `core/state.py` | SQLite persistence | `init_state_store()` | `trading_state.db` |
| `core/config.py` | YAML configuration | `load_settings()`, `load_bots_config()` | Cached configs |
| `core/clock.py` | Timezone & market hours | `get_market_clock()` | None (stateless) |
| `core/halt.py` | Global trading halt | `get_halt_manager()` | `GLOBAL_TRADING_HALT` |
| `core/health.py` | API & data health | `get_health_monitor()` | `health.*` keys |

### Foundation Dependency Chain
```
logging.py (standalone)
    │
    ├──► state.py (uses logging)
    │       │
    │       ├──► config.py (uses logging)
    │       │       │
    │       │       └──► clock.py (uses config)
    │       │
    │       ├──► halt.py (uses state, logging)
    │       │
    │       └──► health.py (uses state, config, logging)
    │
    └──► All higher-tier modules
```

### Foundation Health Check Script
```python
def check_foundation_health():
    """Run this to verify all foundation modules work"""
    from trading_hydra.core.logging import get_logger
    from trading_hydra.core.state import init_state_store, get_state, set_state
    from trading_hydra.core.config import load_settings, load_bots_config
    from trading_hydra.core.clock import get_market_clock
    from trading_hydra.core.halt import get_halt_manager
    from trading_hydra.core.health import get_health_monitor
    
    results = {}
    
    # Test logging
    try:
        logger = get_logger()
        logger.log("health_check", {"status": "testing"})
        results["logging"] = "OK"
    except Exception as e:
        results["logging"] = f"FAIL: {e}"
    
    # Test state
    try:
        init_state_store()
        set_state("health_check_test", {"ts": "now"})
        val = get_state("health_check_test")
        results["state"] = "OK" if val else "FAIL: read returned None"
    except Exception as e:
        results["state"] = f"FAIL: {e}"
    
    # Test config
    try:
        settings = load_settings()
        bots = load_bots_config()
        results["config"] = "OK" if settings and bots else "FAIL: empty config"
    except Exception as e:
        results["config"] = f"FAIL: {e}"
    
    # Test clock
    try:
        clock = get_market_clock()
        now = clock.now()
        results["clock"] = f"OK: {now}"
    except Exception as e:
        results["clock"] = f"FAIL: {e}"
    
    # Test halt
    try:
        halt = get_halt_manager()
        status = halt.is_halted()
        results["halt"] = f"OK: halted={status}"
    except Exception as e:
        results["halt"] = f"FAIL: {e}"
    
    # Test health
    try:
        health = get_health_monitor()
        snapshot = health.get_snapshot()
        results["health"] = f"OK: {snapshot.ok}"
    except Exception as e:
        results["health"] = f"FAIL: {e}"
    
    return results
```

## 1.3 Tier 2: Core Services

All services depend on Tier 1 modules plus specific additional dependencies.

### AlpacaClient Dependencies
```
services/alpaca_client.py
├── core/logging.py      → get_logger()
├── core/health.py       → get_health_monitor()
├── core/clock.py        → get_market_clock()
├── core/config.py       → load_settings() for cache TTL
└── services/mock_data.py → Optional, development mode only
    
Environment Variables Required:
├── ALPACA_KEY           → API key (required)
├── ALPACA_SECRET        → API secret (required)
└── ALPACA_PAPER         → "true" for paper trading (default: true)
```

### ExitBot Dependencies (CRITICAL - The Safety Controller)
```
services/exitbot.py
├── core/logging.py
├── core/config.py       → load_settings(), load_bots_config()
├── core/state.py        → Trailing stop persistence
├── core/health.py       → Health monitoring
├── core/halt.py         → Halt trigger
├── core/clock.py        → Time checks
├── core/risk.py         → dollars_from_pct()
├── services/alpaca_client.py → Position & order management
├── services/market_regime.py → VIX-based adjustments
├── risk/trailing_stop.py     → Stop management
└── strategy/kill_switch.py   → Per-strategy halts
```

### Portfolio Bot Dependencies
```
services/portfolio.py
├── core/logging.py
├── core/config.py       → load_settings(), load_bots_config()
├── core/state.py        → Budget persistence
└── core/risk.py         → dollars_from_pct()
```

### Execution Service Dependencies
```
services/execution.py
├── core/logging.py
├── core/config.py       → load_bots_config()
├── core/state.py        → Cooldowns, trade counts
├── core/halt.py         → Halt check before execution
├── core/health.py       → Health check
├── risk/trailing_stop.py
├── services/decision_tracker.py
└── All 4 trading bots (lazy import)
```

## 1.4 Tier 3: Trading Bots

All bots share a common dependency pattern, plus bot-specific additions.

### Common Bot Dependencies (ALL BOTS)
```
EVERY BOT imports:
├── core/logging.py           → get_logger()
├── core/state.py             → get_state(), set_state()
├── core/config.py            → load_bots_config(), load_settings()
├── core/clock.py             → get_market_clock()
├── services/alpaca_client.py → get_alpaca_client()
├── services/market_regime.py → get_current_regime()
├── services/decision_tracker.py → get_decision_tracker()
├── risk/trailing_stop.py     → get_trailing_stop_manager()
└── ml/signal_service.py      → MLSignalService
```

### Bot-Specific Dependencies

| Bot | Additional Dependencies |
|-----|------------------------|
| **momentum_bot.py** | `indicators/turtle_trend.py` (TurtleTrend, SignalType) |
| **options_bot.py** | `strategy/runner.py`, `strategy/kill_switch.py`, `services/premarket_intelligence.py`, `services/universe_screener.py`, `indicators/indicator_engine.py`, `services/options_chain.py`, `strategy/options_selector.py` |
| **crypto_bot.py** | `risk/position_sizer.py`, `risk/correlation_manager.py`, `services/crypto_universe.py`, `ml/feature_extractor.py`, `indicators/turtle_trend.py` |
| **twenty_minute_bot.py** | `services/premarket_intelligence.py` |

## 1.5 Tier 4: Orchestration Layer

The orchestrator imports EVERYTHING and coordinates the 5-step loop.

### Orchestrator Full Dependency Tree
```
orchestrator.py
├── TIER 1 (all)
│   ├── core/logging.py
│   ├── core/state.py
│   ├── core/config.py (+ all helper functions)
│   ├── core/clock.py
│   ├── core/halt.py
│   └── core/health.py
│
├── TIER 2 (key services)
│   ├── services/alpaca_client.py
│   ├── services/exitbot.py
│   ├── services/portfolio.py
│   ├── services/execution.py
│   ├── services/stock_screener.py
│   ├── services/options_screener.py
│   ├── services/market_regime.py
│   ├── services/system_state.py
│   ├── services/parameter_resolver.py
│   └── services/earnings_calendar.py
│
├── ML LAYER
│   ├── ml/account_analytics.py
│   ├── ml/performance_analytics.py
│   ├── ml/trade_outcome_tracker.py
│   ├── ml/performance_metrics.py
│   └── ml/models/regime_sizer.py
│
└── RISK LAYER
    ├── risk/position_sizer.py
    ├── risk/correlation_manager.py
    └── risk/killswitch.py
```

## 1.6 Failure Propagation Matrix

When a module fails, here's what breaks downstream:

| Failed Module | Direct Dependents | Cascade Effect | Severity |
|--------------|-------------------|----------------|----------|
| `logging.py` | Everything | All logging stops, errors silent, debugging blind | **CRITICAL** |
| `state.py` | 40+ modules | State persistence fails, trailing stops lost, halt recovery broken | **CRITICAL** |
| `config.py` | 35+ modules | All bots use wrong params, likely crashes | **CRITICAL** |
| `clock.py` | 20+ modules | Wrong trading hours, bots may run 24/7 incorrectly | HIGH |
| `halt.py` | exitbot, execution, orchestrator | **NO SAFETY STOPS - DANGEROUS** | **CRITICAL** |
| `health.py` | exitbot, orchestrator | No halt triggers, stale data undetected | HIGH |
| `alpaca_client.py` | All bots, exitbot, orchestrator | All trading stops, no quotes, no positions | **CRITICAL** |
| `exitbot.py` | orchestrator | **TRAILING STOPS DEAD** - positions unmonitored | **CRITICAL** |
| `market_regime.py` | All bots, position_sizer | Wrong sizing, no VIX adaptation, bad strategy selection | MEDIUM |
| `position_sizer.py` | crypto_bot, momentum_bot | Fixed sizing instead of dynamic | LOW |
| `ml/signal_service.py` | All bots | Falls back to 0.5 probability, trades may pass/fail incorrectly | LOW |

---

# 2. Critical Call Chains

## 2.1 The 5-Step Trading Loop

Understanding this flow is essential for debugging any issue.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           TRADING LOOP (5 STEPS)                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────┐                                                        │
│  │  STEP 1: INIT    │  _step_initialize()                                    │
│  │  ─────────────── │  • Connect to Alpaca                                   │
│  │                  │  • Get account equity                                  │
│  │                  │  • Detect account mode (micro/small/standard)          │
│  │                  │  • Get market regime (VIX, VVIX, sentiment)            │
│  │                  │  • Run config doctor                                   │
│  └────────┬─────────┘                                                        │
│           │                                                                  │
│           ▼                                                                  │
│  ┌──────────────────┐                                                        │
│  │  STEP 2: EXITBOT │  _step_exitbot()                                       │
│  │  ─────────────── │  • Check health status                                 │
│  │                  │  • Check daily P&L limits                              │
│  │                  │  • Update all trailing stops                           │
│  │                  │  • Trigger exits if stops hit                          │
│  │                  │  • HALT if limits exceeded                             │
│  └────────┬─────────┘                                                        │
│           │                                                                  │
│           ▼                                                                  │
│  ┌──────────────────┐                                                        │
│  │  STEP 3: PORTFOLIO│  _step_portfoliobot()                                 │
│  │  ─────────────── │  • Calculate daily risk budget                         │
│  │                  │  • Allocate to buckets (momentum, options, crypto)     │
│  │                  │  • Apply guardrails (min/max per bot)                  │
│  │                  │  • Set budget state for each bot                       │
│  └────────┬─────────┘                                                        │
│           │                                                                  │
│           ▼                                                                  │
│  ┌──────────────────┐                                                        │
│  │  STEP 4: EXECUTE │  _step_execution()                                     │
│  │  ─────────────── │  • Get enabled bots list                               │
│  │                  │  • Run each bot with budget                            │
│  │                  │  • Collect signals & execute trades                    │
│  │                  │  • Record decision audit trail                         │
│  └────────┬─────────┘                                                        │
│           │                                                                  │
│           ▼                                                                  │
│  ┌──────────────────┐                                                        │
│  │  STEP 5: FINALIZE│  (end of run_loop)                                     │
│  │  ─────────────── │  • Record metrics                                      │
│  │                  │  • Update ML training data                             │
│  │                  │  • Log loop summary                                    │
│  │                  │  • Prepare for next iteration                          │
│  └──────────────────┘                                                        │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 2.2 Order Execution Flow

When a bot decides to trade, here's the complete flow:

```
Bot Signal Generated
         │
         ├──► 1. Decision Tracker records signal
         │    └── Writes to decision_records.jsonl
         │
         ├──► 2. ML Signal Service scores trade
         │    ├── Extract features from market data
         │    ├── Run through trained model
         │    ├── Return probability (0.0 - 1.0)
         │    └── Compare to threshold (default 0.55)
         │         │
         │         ├── If < threshold → REJECT (log reason)
         │         │
         │         └── If ≥ threshold → CONTINUE
         │
         ├──► 3. Position Sizer calculates quantity
         │    ├── Base: % of account equity
         │    ├── Adjust for volatility (ATR)
         │    ├── Adjust for ML confidence (Kelly)
         │    ├── Adjust for regime (VIX multiplier)
         │    ├── Adjust for correlation (reduce if concentrated)
         │    └── Return final qty and notional
         │
         ├──► 4. Pre-trade Risk Checks
         │    ├── Check daily trade count < max
         │    ├── Check concurrent positions < max
         │    ├── Check cooldown expired
         │    ├── Check budget remaining
         │    └── If ANY fail → BLOCK (record blocker)
         │
         ├──► 5. Generate Client Order ID
         │    └── Format: {bot_id}_{symbol}_{date}_{signal_id}
         │
         ├──► 6. Check Order Idempotency
         │    └── Query order_ids table for client_order_id
         │         │
         │         ├── If exists → SKIP (already submitted)
         │         │
         │         └── If not exists → CONTINUE
         │
         ├──► 7. Submit Order to Alpaca
         │    ├── Build order request
         │    ├── Call Alpaca API
         │    └── Handle response
         │         │
         │         ├── Success → Record in order_ids table
         │         │
         │         └── Failure → Log error, record API failure
         │
         └──► 8. Initialize Trailing Stop
              ├── Create TrailingStopState
              ├── Set entry price, side, config
              └── Persist to SQLite state
```

### Debug Points for Order Flow
1. **Signal generated but not executed?**
   - Check `logs/decision_records.jsonl` for signal
   - Check ML threshold: `grep "ml_score" logs/app.jsonl`
   - Check blockers: `grep "blocker\|cooldown\|max_trades" logs/app.jsonl`

2. **Order submitted but not filled?**
   - Check Alpaca dashboard for order status
   - Check `order_ids` table in SQLite
   - Check for rejected orders: `grep "order_rejected" logs/app.jsonl`

3. **Trailing stop not initialized?**
   - Check for `trailing_stop_init` event in logs
   - Verify SQLite state key exists

## 2.3 Halt Trigger Flow

The system can halt from multiple sources:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        HALT TRIGGER SOURCES                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Source 1: Daily P&L Limit (ExitBot)                                        │
│  ────────────────────────────────────                                        │
│  • Calculated: (current_equity - day_start_equity)                          │
│  • Threshold: risk.global_max_daily_loss_pct (default 1%)                   │
│  • Trigger: Loss exceeds threshold                                          │
│  • State: GLOBAL_TRADING_HALT = True, halt.reason = "DAILY_LOSS_LIMIT"      │
│                                                                              │
│  Source 2: API Authentication Failure (HealthMonitor)                       │
│  ─────────────────────────────────────────────────────                      │
│  • Trigger: 401 or 403 response from Alpaca                                 │
│  • State: health.critical_auth_failure = True                               │
│  • Effect: ExitBot triggers halt on next run                                │
│                                                                              │
│  Source 3: Data Staleness (HealthMonitor)                                   │
│  ─────────────────────────────────────────                                  │
│  • Calculated: now() - health.last_price_tick                               │
│  • Threshold: health.max_price_staleness_seconds (default 15)               │
│  • Trigger: Stale seconds > threshold                                       │
│                                                                              │
│  Source 4: API Failure Count (HealthMonitor)                                │
│  ────────────────────────────────────────────                               │
│  • Counter: health.api_failure_count                                        │
│  • Threshold: health.max_api_failures_in_window (default 5)                 │
│  • Trigger: Failures > threshold                                            │
│                                                                              │
│  Source 5: Manual Dashboard Halt (routes.py)                                │
│  ────────────────────────────────────────────                               │
│  • Endpoint: POST /api/halt                                                 │
│  • Effect: Immediate halt with custom reason                                │
│                                                                              │
│  Source 6: Config Override (settings.yaml)                                  │
│  ──────────────────────────────────────────                                 │
│  • Config: trading.global_halt = true                                       │
│  • Effect: Checked every loop, forces halt                                  │
│                                                                              │
│  Source 7: Strategy Kill-Switch (strategy/kill_switch.py)                   │
│  ──────────────────────────────────────────────────────                     │
│  • Per-strategy drawdown limit                                              │
│  • Effect: Only that strategy halts, others continue                        │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
              ┌────────────────────────────────────────┐
              │           HaltManager.set_halt()        │
              │  ────────────────────────────────────  │
              │  set_state("GLOBAL_TRADING_HALT", True)│
              │  set_state("halt.reason", reason)      │
              │  set_state("halt.halted_at", now)      │
              │  set_state("halt.expires_at", now+ttl) │
              └────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           HALT EFFECTS                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│  • ExecutionService.run() skips all new trade entries                       │
│  • ExitBot CONTINUES to run (manages existing positions)                    │
│  • Trailing stops CONTINUE to update and trigger exits                      │
│  • Dashboard shows red HALTED banner                                        │
│  • Logs emit "execution_halted" event                                       │
│  • Human intervention required (or wait for auto-expire)                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 2.4 Trailing Stop Flow

Trailing stops are initialized on entry and updated every loop:

```
PHASE 1: INITIALIZATION (on position entry)
─────────────────────────────────────────────

Bot.execute_trade()
         │
         ▼
TrailingStopManager.init_for_position()
         │
         ├── Create TrailingStopState
         │   ├── side: "long" or "short"
         │   ├── entry_price: fill price
         │   ├── armed: False (not yet activated)
         │   ├── high_water: entry_price (for longs)
         │   ├── low_water: entry_price (for shorts)
         │   ├── stop_price: 0.0 (not set until armed)
         │   └── config: TrailingStopConfig from bot config
         │
         └── Persist to SQLite
             Key: trailing_stop.{bot_id}_{asset_class}_{symbol}_{position_id}


PHASE 2: UPDATE LOOP (every trading iteration)
───────────────────────────────────────────────

ExitBot.run()
         │
         ▼
For each position:
         │
         ▼
TrailingStopManager.update(current_price)
         │
         ├── Load state from SQLite
         │
         ├── Update high_water/low_water
         │   ├── Long: high_water = max(high_water, current_price)
         │   └── Short: low_water = min(low_water, current_price)
         │
         ├── Check activation threshold
         │   ├── Long: profit_pct = (high_water - entry) / entry * 100
         │   └── If profit_pct >= activation_profit_pct → armed = True
         │
         ├── If armed, update stop_price
         │   ├── Long: stop_price = high_water * (1 - trail_pct/100)
         │   └── Short: stop_price = low_water * (1 + trail_pct/100)
         │
         └── Persist updated state


PHASE 3: EXIT CHECK (every trading iteration)
──────────────────────────────────────────────

ExitBot._check_exits()
         │
         ▼
For each position with armed trailing stop:
         │
         ├── Long: if current_price <= stop_price → TRIGGER EXIT
         │
         └── Short: if current_price >= stop_price → TRIGGER EXIT
                   │
                   ▼
         AlpacaClient.close_position(symbol)
                   │
                   ▼
         Record ExitRecord with reason="trailing_stop"
                   │
                   ▼
         Clean up trailing stop state from SQLite
```

## 2.5 Strategy System Flow (Options Bot)

The 5-gate pipeline for the PDF rules-based strategy system:

```
OptionsBot.run()
         │
         ▼
StrategyRunner.run_for_symbol(symbol)
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      5-GATE STRATEGY PIPELINE                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ GATE 1: Strategy Registry                                           │    │
│  │ ─────────────────────────                                           │    │
│  │ • Load from config/strategies/*.yaml ONLY                           │    │
│  │ • Validate required keys (id, name, family, direction, enabled...)  │    │
│  │ • Resolve 'extends' inheritance                                     │    │
│  │ • Return frozen (immutable) StrategyConfig                          │    │
│  │                                                                     │    │
│  │ FAIL CONDITIONS:                                                    │    │
│  │ • YAML file not found → KeyError                                    │    │
│  │ • Missing required keys → ValueError                                │    │
│  │ • Inheritance loop → ValueError                                     │    │
│  └───────────────────────────────────────────────────────────────┬─────┘    │
│                                                                  │          │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ GATE 2: Kill-Switch Check                                           │    │
│  │ ─────────────────────────                                           │    │
│  │ • Check state: strategy_kill.{strategy_id}                          │    │
│  │ • If until_ts > now() → Strategy is killed                          │    │
│  │ • Auto-clear if expired                                             │    │
│  │                                                                     │    │
│  │ FAIL CONDITIONS:                                                    │    │
│  │ • Rolling drawdown exceeded threshold                               │    │
│  │ • Manual kill via state                                             │    │
│  └───────────────────────────────────────────────────────────────┬─────┘    │
│                                                                  │          │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ GATE 3: Earnings Filter                                             │    │
│  │ ─────────────────────────                                           │    │
│  │ • Check earnings_policy mode from strategy YAML                     │    │
│  │ • Modes: NEVER, ONLY, PRE, POST                                     │    │
│  │ • Query earnings calendar for days_until_earnings                   │    │
│  │                                                                     │    │
│  │ MODE LOGIC:                                                         │    │
│  │ • NEVER: Block if within blackout_days of earnings (default 3)      │    │
│  │ • ONLY: Allow only within window_days of earnings                   │    │
│  │ • PRE: Allow only if earnings upcoming within window_days           │    │
│  │ • POST: Allow only if earnings passed within window_days            │    │
│  │                                                                     │    │
│  │ FAIL CONDITIONS:                                                    │    │
│  │ • Earnings date conflicts with mode                                 │    │
│  │ • Missing earnings data (fails closed for ONLY mode)                │    │
│  └───────────────────────────────────────────────────────────────┬─────┘    │
│                                                                  │          │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ GATE 4: Signal Rule Validation                                      │    │
│  │ ────────────────────────────                                        │    │
│  │ • Evaluate each rule in signal_rules array                          │    │
│  │ • ALL rules must pass for signal to generate                        │    │
│  │ • Deterministic: "No AI. No vibes. Just pass/fail with receipts."   │    │
│  │                                                                     │    │
│  │ RULE TYPES:                                                         │    │
│  │ • price_vs_ema: Compare current price to EMA                        │    │
│  │ • price_vs_sma: Compare current price to SMA                        │    │
│  │ • sma_vs_price: Compare SMA to price                                │    │
│  │ • rsi_threshold: RSI above/below threshold                          │    │
│  │                                                                     │    │
│  │ FAIL CONDITIONS:                                                    │    │
│  │ • Any single rule fails → Strategy fails for this symbol            │    │
│  │ • Indicator calculation error → Fails closed                        │    │
│  └───────────────────────────────────────────────────────────────┬─────┘    │
│                                                                  │          │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ GATE 5: Backtest Gate                                               │    │
│  │ ─────────────────────                                               │    │
│  │ • Load backtest summary for strategy + symbol                       │    │
│  │ • Compare against thresholds in backtest_gate config                │    │
│  │                                                                     │    │
│  │ THRESHOLDS:                                                         │    │
│  │ • min_win_rate_1y: Minimum 1-year win rate                          │    │
│  │ • min_win_rate_3y: Minimum 3-year win rate                          │    │
│  │ • min_total_wins_3y: Minimum total winning trades                   │    │
│  │ • min_total_return_1y: Minimum 1-year return                        │    │
│  │                                                                     │    │
│  │ FAIL CONDITIONS:                                                    │    │
│  │ • No backtest data available → Fails closed                         │    │
│  │ • Any threshold not met → Fails                                     │    │
│  └───────────────────────────────────────────────────────────────┬─────┘    │
│                                                                  │          │
└──────────────────────────────────────────────────────────────────┴──────────┘
                                    │
                                    ▼ (All gates passed)
                    ┌───────────────────────────────┐
                    │   OptionsSelector.select()    │
                    │   Select contract based on    │
                    │   DTE, delta, strike rules    │
                    └───────────────────────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │   Execute Trade               │
                    │   Place bracket order         │
                    └───────────────────────────────┘
```

---

# 3. Error Pattern Catalog

## 3.1 API Authentication Failure

### Symptoms
- `401 Unauthorized` or `403 Forbidden` in logs
- Trading halts unexpectedly with no daily P&L limit reached
- `health.critical_auth_failure = True` in state
- Dashboard shows "HALTED: CRITICAL_AUTH_FAILURE"

### Root Cause Analysis

| Cause | How to Check | Fix |
|-------|--------------|-----|
| Invalid/expired API key | Compare `ALPACA_KEY` with Alpaca dashboard | Regenerate key pair in Alpaca |
| Wrong environment | Check `ALPACA_PAPER` env var | Set to "true" for paper, "false" for live |
| Account suspended | Log into Alpaca dashboard | Contact Alpaca support |
| Rate limited | Check request frequency in logs | Add backoff, reduce polling frequency |
| Clock skew | Compare system time to NTP | Sync system clock with NTP |

### Code Locations
```
services/alpaca_client.py:62-70    # API key loading
core/health.py:39-44               # record_critical_auth_failure()
services/exitbot.py:193            # Halt trigger on auth failure
```

### Diagnostic Commands
```bash
# Check current auth state
sqlite3 state/trading_state.db "SELECT * FROM state WHERE key LIKE 'health.critical%'"

# Find auth errors in logs
grep -i "401\|403\|auth\|unauthorized" logs/app.jsonl | tail -20

# Verify environment variables
env | grep ALPACA
```

### Fix Template
```python
# After fixing API keys, clear the auth failure state:
from trading_hydra.core.health import get_health_monitor
health = get_health_monitor()
health.clear_auth_failure()

# Or via SQLite:
# sqlite3 state/trading_state.db "UPDATE state SET value='false' WHERE key='health.critical_auth_failure'"
```

---

## 3.2 State Persistence Failure

### Symptoms
- `sqlite3.OperationalError: database is locked`
- Trailing stops not updating or lost after restart
- State values returning `None` unexpectedly
- `get_state()` returns default value even after `set_state()`

### Root Cause Analysis

| Cause | How to Check | Fix |
|-------|--------------|-----|
| Database locked | Multiple connections from different processes | Use single connection pattern |
| Disk full | `df -h ./state/` | Clear old logs, increase disk |
| Corrupt database | `sqlite3 trading_state.db .integrity_check` | Restore from backup or recreate |
| Missing directory | `ls -la ./state/` | `mkdir -p ./state/` |
| Permission issue | `ls -la ./state/trading_state.db` | `chmod 664 ./state/trading_state.db` |
| Invalid JSON value | Check value before set_state | Ensure JSON-serializable |

### Code Locations
```
core/state.py:14-20     # _get_connection()
core/state.py:66-75     # set_state() implementation
core/state.py:53-63     # get_state() implementation
```

### Diagnostic Commands
```bash
# Check database integrity
sqlite3 state/trading_state.db ".integrity_check"

# Check database size
ls -lh state/trading_state.db

# Check disk space
df -h ./state/

# List all state keys
sqlite3 state/trading_state.db "SELECT key FROM state ORDER BY key"

# Check for locked database
lsof state/trading_state.db
```

### Fix Template
```python
# If database is corrupt, recreate it:
import os
os.rename("state/trading_state.db", "state/trading_state.db.corrupt")

from trading_hydra.core.state import init_state_store
init_state_store()

# Warning: This loses all persisted state!
# You may need to manually re-enter critical state values.
```

---

## 3.3 Stale Quote Data

### Symptoms
- Trades rejected with "stale data" reason
- `health.stale_seconds` exceeds threshold in health snapshot
- Crypto bot logging `MAX_QUOTE_AGE_SECONDS` violations
- No trades executing despite valid signals

### Root Cause Analysis

| Cause | How to Check | Fix |
|-------|--------------|-----|
| API rate limited | Check recent API errors | Increase cache TTL in settings.yaml |
| Network timeout | `curl` to Alpaca endpoint | Check firewall, DNS, network |
| Market closed | `clock.is_market_hours()` | Expected during non-market hours |
| Cache expired | Check `_quote_cache_ttl` | Adjust cache settings |
| Alpaca outage | status.alpaca.markets | Wait for resolution |

### Code Locations
```
services/alpaca_client.py:80-81   # Quote cache TTL
bots/crypto_bot.py:63             # MAX_QUOTE_AGE_SECONDS = 30
core/health.py:59-60              # record_price_tick()
```

### Diagnostic Commands
```bash
# Check last price tick
sqlite3 state/trading_state.db "SELECT value FROM state WHERE key='health.last_price_tick'"

# Check staleness in logs
grep -i "stale\|quote_age" logs/app.jsonl | tail -20

# Test Alpaca connectivity
curl -s -H "APCA-API-KEY-ID: $ALPACA_KEY" -H "APCA-API-SECRET-KEY: $ALPACA_SECRET" \
    https://paper-api.alpaca.markets/v2/account | jq .status
```

### Fix Template
```yaml
# In config/settings.yaml, increase cache TTL:
cache:
  account_ttl_seconds: 120   # Default 60
  quote_ttl_seconds: 300     # Default 200
```

---

## 3.4 Trailing Stop Not Triggering

### Symptoms
- Position runs past expected stop level
- `trailing_stop_exit` events not in logs
- Losses exceed configured stop-loss percentage
- `armed = False` in trailing stop state despite profit

### Root Cause Analysis

| Cause | How to Check | Fix |
|-------|--------------|-----|
| Stop not armed | Check `armed` in state | Profit must exceed `activation_profit_pct` |
| Wrong stop_price | Check `stop_price` in state | Verify calculation, check for rounding |
| ExitBot not running | Check Step 2 logs | Verify exitbot.enabled = true |
| State key mismatch | SQLite key format | Verify key matches expected pattern |
| ExitBot halted early | Check for errors in exitbot | Fix underlying issue |
| Quote stale during check | Check quote freshness | Improve quote fetching |

### Code Locations
```
risk/trailing_stop.py:44-75       # init_for_position()
risk/trailing_stop.py:77-85       # load_state()
services/exitbot.py:173-250       # Trailing stop update loop
```

### Diagnostic Commands
```bash
# Check all trailing stop states
sqlite3 state/trading_state.db "SELECT key, value FROM state WHERE key LIKE 'trailing_stop.%'"

# Check if armed
sqlite3 state/trading_state.db "SELECT key, json_extract(value, '$.armed') as armed, json_extract(value, '$.stop_price') as stop FROM state WHERE key LIKE 'trailing_stop.%'"

# Check activation threshold in config
grep -A5 "trailing" config/bots.yaml

# Check exitbot events
grep "trailing_stop\|exitbot" logs/app.jsonl | tail -30
```

### Fix Template
```python
# Force arm a trailing stop (for debugging)
from trading_hydra.core.state import get_state, set_state
import json

key = "trailing_stop.crypto_core_crypto_BTC/USD_pos123"
state = get_state(key)
if state:
    state['armed'] = True
    state['stop_price'] = state['entry_price'] * 0.98  # 2% below entry
    set_state(key, state)
```

---

## 3.5 Bot Not Executing Trades

### Symptoms
- Bot runs but `trades_attempted = 0` in loop result
- Signals generated but not executed (check decision_records.jsonl)
- No new positions despite market conditions
- "Skipped" or "blocked" in logs

### Root Cause Analysis

| Cause | How to Check | Fix |
|-------|--------------|-----|
| Global halt active | `GLOBAL_TRADING_HALT` state | Clear halt or wait for expiry |
| Outside trading hours | `trade_start`/`trade_end` in config | Wait for trading window |
| ML threshold too high | `ml.threshold` in settings | Lower threshold or retrain model |
| Max trades reached | `{bot}.daily_trades` in state | Wait for next day reset |
| Cooldown active | `cooldown_{bot}_{symbol}` state | Wait 60 seconds |
| Budget exhausted | Portfolio allocation | Check equity and allocation |
| Bot disabled | `bots.yaml` enabled flag | Set enabled: true |
| Account mode disables bot | Check mode params | Micro mode disables stocks/options |

### Code Locations
```
services/execution.py:77-80       # Halt check
services/execution.py:32-41       # Cooldown check
bots/momentum_bot.py:200-250      # Session time check
core/config.py:22-62              # Account mode parameters
```

### Diagnostic Commands
```bash
# Check halt status
sqlite3 state/trading_state.db "SELECT value FROM state WHERE key='GLOBAL_TRADING_HALT'"

# Check cooldowns
sqlite3 state/trading_state.db "SELECT key, value FROM state WHERE key LIKE 'cooldown_%'"

# Check daily trades
sqlite3 state/trading_state.db "SELECT key, value FROM state WHERE key LIKE '%daily_trades%'"

# Check bot enabled status
sqlite3 state/trading_state.db "SELECT key, value FROM state WHERE key LIKE 'bots.%.enabled'"

# Check budgets
sqlite3 state/trading_state.db "SELECT key, value FROM state WHERE key LIKE 'budgets.%'"

# Check blockers in decision records
grep "blocker" logs/decision_records.jsonl | tail -10
```

### Fix Template
```bash
# Clear cooldowns (careful - may cause rapid trading)
sqlite3 state/trading_state.db "DELETE FROM state WHERE key LIKE 'cooldown_%'"

# Reset daily trade count
sqlite3 state/trading_state.db "DELETE FROM state WHERE key LIKE '%daily_trades%'"

# Clear halt
sqlite3 state/trading_state.db "DELETE FROM state WHERE key LIKE 'halt.%' OR key='GLOBAL_TRADING_HALT'"
```

---

## 3.6 Strategy Kill-Switch Activated

### Symptoms
- Specific strategy stops trading while others continue
- `strategy_killed` event in logs
- `is_killed = True` in logs for strategy
- Strategy worked before but now blocked

### Root Cause Analysis

| Cause | How to Check | Fix |
|-------|--------------|-----|
| Drawdown exceeded | Check rolling PnL for strategy | Wait for cooloff or adjust threshold |
| Manual kill | State set manually | Clear state key |
| Cooloff not expired | Check `until_ts` in state | Wait or manually clear |
| Bug in PnL calculation | Check trade outcome records | Fix calculation logic |

### Code Locations
```
strategy/kill_switch.py:41-61     # status() check
strategy/kill_switch.py:63-100    # record_exit() trigger logic
strategy/kill_switch.py:102-130   # trigger_kill()
```

### Diagnostic Commands
```bash
# Check all killed strategies
sqlite3 state/trading_state.db "SELECT key, value FROM state WHERE key LIKE 'strategy_kill.%'"

# Check strategy performance
sqlite3 state/trading_state.db "SELECT key, value FROM state WHERE key LIKE 'strategy_perf.%'"

# Find kill events in logs
grep "strategy_killed\|strategy_kill" logs/app.jsonl | tail -20
```

### Fix Template
```bash
# Clear strategy kill-switch for specific strategy
sqlite3 state/trading_state.db "DELETE FROM state WHERE key='strategy_kill.iron_condor_base'"

# Clear strategy performance (resets drawdown tracking)
sqlite3 state/trading_state.db "DELETE FROM state WHERE key='strategy_perf.iron_condor_base'"

# Clear ALL strategy kills (use with caution)
sqlite3 state/trading_state.db "DELETE FROM state WHERE key LIKE 'strategy_kill.%'"
```

---

## 3.7 ML Model Unavailable

### Symptoms
- `MLSignalService.is_available() = False`
- All trades pass/fail without ML scoring
- Default probability (0.5) used for all trades
- `ml_model_not_found` event in logs

### Root Cause Analysis

| Cause | How to Check | Fix |
|-------|--------------|-----|
| Model file missing | `ls models/trade_classifier.pkl` | Train or copy model file |
| Pickle version mismatch | Python version vs model version | Retrain with current Python |
| Feature config missing | `ls models/feature_config.json` | Create feature config |
| Import error | Try `import lightgbm` | `pip install lightgbm` |
| Corrupt model file | Try loading in Python | Retrain model |

### Code Locations
```
ml/signal_service.py:27-28        # Model paths
ml/signal_service.py:30-42        # _load_model()
```

### Diagnostic Commands
```bash
# Check model files exist
ls -la models/

# Test model loading
python -c "from trading_hydra.ml.signal_service import MLSignalService; s = MLSignalService(); print(f'Available: {s.is_available()}')"

# Check for import errors
python -c "import lightgbm; print('LightGBM OK')"
python -c "import sklearn; print('Sklearn OK')"
```

### Fix Template
```python
# If model is missing, you can create a placeholder that falls back gracefully:
# The system is designed to continue with probability=0.5 when model unavailable

# To train a new model (if training data exists):
from trading_hydra.ml.train_classifier import train_model
train_model()
```

---

## 3.8 Options Chain Empty

### Symptoms
- OptionsBot finds no contracts to trade
- `get_options_chain()` returns empty list
- "No valid contracts" in logs
- Strategy system can't select contracts

### Root Cause Analysis

| Cause | How to Check | Fix |
|-------|--------------|-----|
| Market closed | Options only trade market hours | Wait for market open |
| Invalid ticker | Ticker doesn't have options | Check Alpaca options eligibility |
| API error | Check API response | Verify API keys, check Alpaca status |
| Filter too strict | DTE/delta ranges in strategy | Widen filter parameters |
| Alpaca options not enabled | Account permissions | Enable options in Alpaca dashboard |
| Weekend/holiday | `clock.is_weekend()` | Wait for trading day |

### Code Locations
```
services/options_chain.py         # Chain fetching
strategy/options_selector.py      # Contract filtering
bots/options_bot.py               # Chain request
```

### Diagnostic Commands
```bash
# Test options chain API directly
python -c "
from trading_hydra.services.alpaca_client import get_alpaca_client
client = get_alpaca_client()
chain = client.get_options_chain('AAPL')
print(f'Contracts found: {len(chain)}')"

# Check options bot logs
grep -i "options_chain\|no_contracts\|chain_empty" logs/app.jsonl | tail -20
```

---

# 4. Debug Recipes

## 4.1 Recipe: Diagnose Why Trading Halted

**Use when**: Trading stopped unexpectedly

**Step 1**: Check halt state
```bash
sqlite3 state/trading_state.db "SELECT key, value FROM state WHERE key LIKE 'halt.%' OR key = 'GLOBAL_TRADING_HALT'"
```

**Step 2**: Check health state
```bash
sqlite3 state/trading_state.db "SELECT key, value FROM state WHERE key LIKE 'health.%'"
```

**Step 3**: Find halt event in logs
```bash
grep -i "halt_activated\|critical_auth\|daily_loss\|health_snapshot" logs/app.jsonl | tail -20
```

**Step 4**: Check config override
```bash
grep "global_halt" config/settings.yaml
```

**Step 5**: Resolution based on halt reason

| Halt Reason | Resolution |
|-------------|------------|
| `DAILY_LOSS_LIMIT` | Wait for next trading day, or increase limit in settings.yaml |
| `CRITICAL_AUTH_FAILURE` | Fix API keys in environment, then run: `health.clear_auth_failure()` |
| `CONFIG_OVERRIDE` | Set `trading.global_halt: false` in settings.yaml |
| `MANUAL_HALT` | POST to `/api/halt` with toggle action, or clear state directly |
| `DATA_STALENESS` | Check network connectivity, Alpaca status |
| `API_FAILURE_LIMIT` | Check API key validity, reduce request frequency |

---

## 4.2 Recipe: Trace a Failed Order

**Use when**: Expected trade didn't execute

**Step 1**: Find the signal in decision records
```bash
grep "AAPL" logs/decision_records.jsonl | tail -10
```

**Step 2**: Check if signal was blocked
```bash
grep "blocker.*AAPL\|blocked.*AAPL" logs/decision_records.jsonl | tail -5
```

**Step 3**: Check ML score
```bash
grep "ml_score.*AAPL" logs/app.jsonl | tail -5
```

**Step 4**: Check order submission
```bash
grep "order_submitted.*AAPL\|order_rejected.*AAPL" logs/app.jsonl | tail -5
```

**Step 5**: Check order in database
```bash
sqlite3 state/trading_state.db "SELECT * FROM order_ids WHERE symbol='AAPL' ORDER BY submitted_at DESC LIMIT 5"
```

**Step 6**: Verify on Alpaca
```python
from trading_hydra.services.alpaca_client import get_alpaca_client
client = get_alpaca_client()
orders = client.get_orders(status="all")
for o in orders:
    if o.get('symbol') == 'AAPL':
        print(o)
```

---

## 4.3 Recipe: Debug Trailing Stop Issues

**Use when**: Trailing stop didn't trigger as expected

**Step 1**: Find trailing stop state for position
```bash
sqlite3 state/trading_state.db "SELECT key, value FROM state WHERE key LIKE 'trailing_stop.%AAPL%'"
```

**Step 2**: Parse the state JSON
```bash
sqlite3 state/trading_state.db "SELECT 
    json_extract(value, '$.armed') as armed,
    json_extract(value, '$.entry_price') as entry,
    json_extract(value, '$.stop_price') as stop,
    json_extract(value, '$.high_water') as high_water,
    json_extract(value, '$.side') as side
FROM state WHERE key LIKE 'trailing_stop.%AAPL%'"
```

**Step 3**: Check ExitBot ran
```bash
grep "exitbot_start\|trailing_stop" logs/app.jsonl | tail -20
```

**Step 4**: Check activation threshold
```bash
grep -A10 "trailing" config/bots.yaml | grep activation
```

**Step 5**: Calculate expected activation
```python
entry_price = 150.0  # from state
high_water = 152.0   # from state
activation_pct = 0.4  # from config

profit_pct = (high_water - entry_price) / entry_price * 100
print(f"Profit: {profit_pct:.2f}%, Activation threshold: {activation_pct}%")
print(f"Should be armed: {profit_pct >= activation_pct}")
```

---

## 4.4 Recipe: Fix Stale ML Model

**Use when**: ML scoring disabled or returning wrong values

**Step 1**: Check if model exists
```bash
ls -la models/trade_classifier.pkl models/feature_config.json
```

**Step 2**: Test model loading
```python
from trading_hydra.ml.signal_service import MLSignalService
svc = MLSignalService()
print(f"Model available: {svc.is_available()}")
print(f"Model path: {svc.MODEL_PATH}")
```

**Step 3**: Check feature config
```bash
cat models/feature_config.json | python -m json.tool
```

**Step 4**: Test a prediction
```python
from trading_hydra.ml.signal_service import MLSignalService
svc = MLSignalService()
if svc.is_available():
    # Test with sample features
    features = {
        'vix': 18.0,
        'rsi_14': 55.0,
        'price_vs_sma_20': 1.02,
        'atr_pct': 2.5
    }
    score = svc.score_trade(features)
    print(f"Test score: {score:.4f}")
```

**Step 5**: If needed, retrain
```bash
# Ensure you have training data in data/trades.jsonl
python -c "from trading_hydra.ml.train_classifier import train_model; train_model()"
```

---

## 4.5 Recipe: Recover from Database Corruption

**Use when**: SQLite errors, integrity check fails

**Step 1**: Backup current state
```bash
cp state/trading_state.db state/trading_state.db.backup.$(date +%Y%m%d_%H%M%S)
```

**Step 2**: Check database integrity
```bash
sqlite3 state/trading_state.db ".integrity_check"
```

**Step 3**: If corrupt, export what we can
```bash
sqlite3 state/trading_state.db ".dump" > state/state_dump.sql 2>/dev/null
```

**Step 4**: Create fresh database
```bash
mv state/trading_state.db state/trading_state.db.corrupt
python -c "from trading_hydra.core.state import init_state_store; init_state_store()"
```

**Step 5**: Re-import critical state (optional)
```bash
# Only import specific tables if dump succeeded
sqlite3 state/trading_state.db < state/state_dump.sql
```

**Step 6**: Verify recovery
```bash
sqlite3 state/trading_state.db ".tables"
sqlite3 state/trading_state.db "SELECT count(*) FROM state"
```

---

## 4.6 Recipe: Debug Strategy System Failures

**Use when**: Strategy-based trading not working

**Step 1**: Verify strategy files are loaded
```python
from trading_hydra.strategy.registry import StrategyRegistry
reg = StrategyRegistry()
reg.load_all()
print(f"Loaded: {reg.list_enabled()}")
```

**Step 2**: Check specific strategy config
```bash
cat config/strategies/iron_condor_base.yaml
```

**Step 3**: Test strategy validation
```python
from trading_hydra.strategy.registry import StrategyRegistry
from trading_hydra.strategy.validator import StrategyValidator
from trading_hydra.indicators.indicator_engine import IndicatorEngine

reg = StrategyRegistry()
reg.load_all()
strategy = reg.get("iron_condor_base")

ind = IndicatorEngine()
val = StrategyValidator(ind)
result = val.evaluate(strategy.data, "AAPL")

print(f"Strategy: {result.strategy_id}")
print(f"Symbol: {result.symbol}")
print(f"Passed: {result.passed}")
for r in result.reasons:
    print(f"  - {r.rule_id}: {'PASS' if r.passed else 'FAIL'} - {r.details}")
```

**Step 4**: Check backtest gate
```bash
grep "backtest_gate" logs/app.jsonl | tail -10
```

**Step 5**: Check earnings filter
```bash
grep "earnings_filter" logs/app.jsonl | tail -10
```

---

## 4.7 Recipe: Emergency Halt Recovery

**Use when**: Need to resume trading after understanding the issue

⚠️ **WARNING**: Only use when you understand why the halt occurred

**Step 1**: Document current state
```bash
# Save current state for analysis
sqlite3 state/trading_state.db ".dump" > state/pre_recovery_dump.sql
cp logs/app.jsonl logs/app.jsonl.pre_recovery
```

**Step 2**: Clear halt states
```bash
sqlite3 state/trading_state.db "DELETE FROM state WHERE key LIKE 'halt.%'"
sqlite3 state/trading_state.db "DELETE FROM state WHERE key = 'GLOBAL_TRADING_HALT'"
```

**Step 3**: Clear health failure states
```bash
sqlite3 state/trading_state.db "DELETE FROM state WHERE key LIKE 'health.critical%'"
sqlite3 state/trading_state.db "UPDATE state SET value='0' WHERE key='health.api_failure_count'"
sqlite3 state/trading_state.db "UPDATE state SET value='0' WHERE key='health.connection_failure_count'"
```

**Step 4**: Clear strategy kills if needed
```bash
sqlite3 state/trading_state.db "DELETE FROM state WHERE key LIKE 'strategy_kill.%'"
```

**Step 5**: Verify cleared
```bash
sqlite3 state/trading_state.db "SELECT * FROM state WHERE key LIKE 'halt.%' OR key = 'GLOBAL_TRADING_HALT' OR key LIKE 'health.critical%'"
```

**Step 6**: Restart trading loop
```bash
# The next loop iteration should proceed normally
```

---

## 4.8 Recipe: Analyze Trade Performance

**Use when**: Understanding why trades are winning or losing

**Step 1**: Get recent trades from state
```bash
sqlite3 state/trading_state.db "SELECT value FROM state WHERE key='trades'" | python -m json.tool
```

**Step 2**: Check performance metrics
```bash
sqlite3 state/trading_state.db "SELECT key, value FROM state WHERE key LIKE 'stats.%'"
```

**Step 3**: Analyze by bot
```bash
grep "trade_entry_recorded\|trade_outcome_recorded" logs/app.jsonl | tail -50
```

**Step 4**: Check ML prediction accuracy
```bash
grep "ml_score" logs/app.jsonl | python -c "
import sys, json
wins, losses = 0, 0
for line in sys.stdin:
    try:
        d = json.loads(line)
        if 'ml_score' in str(d):
            print(d)
    except: pass
"
```

---

# 5. Integration Points Reference

## 5.1 Complete State Keys Reference

All keys written to SQLite `state` table:

### Halt & Health State
| Key | Set By | Read By | Type | Purpose |
|-----|--------|---------|------|---------|
| `GLOBAL_TRADING_HALT` | HaltManager | ExitBot, ExecutionService | bool | Master halt flag |
| `halt.reason` | HaltManager | Dashboard, logs | string | Halt reason |
| `halt.halted_at` | HaltManager | Dashboard | ISO timestamp | When halt started |
| `halt.expires_at` | HaltManager | HaltManager | ISO timestamp | Auto-clear time |
| `health.api_failure_count` | HealthMonitor | ExitBot | int | API failure counter |
| `health.connection_failure_count` | HealthMonitor | ExitBot | int | Connection failures |
| `health.critical_auth_failure` | HealthMonitor | ExitBot | bool | Auth failure flag |
| `health.critical_auth_error` | HealthMonitor | Logs | string | Auth error message |
| `health.last_price_tick` | HealthMonitor | ExitBot | ISO timestamp | Last quote time |

### Trailing Stop State
| Key Pattern | Set By | Read By | Type | Purpose |
|-------------|--------|---------|------|---------|
| `trailing_stop.{bot}_{asset}_{symbol}_{id}` | TrailingStopManager | ExitBot | TrailingStopState | Stop configuration |

TrailingStopState structure:
```json
{
  "side": "long",
  "entry_price": 150.0,
  "armed": true,
  "high_water": 155.0,
  "low_water": 999999.0,
  "stop_price": 153.45,
  "last_price": 154.50,
  "last_update_ts": "2026-01-18T10:30:00Z",
  "config": { ... }
}
```

### Bot Control State
| Key Pattern | Set By | Read By | Type | Purpose |
|-------------|--------|---------|------|---------|
| `bots.{bot_id}.enabled` | PortfolioBot | ExecutionService | bool | Bot enabled |
| `bots.{bot_id}.allowed` | PortfolioBot | ExecutionService | bool | Bot has budget |
| `budgets.{bot_id}.max_daily_loss` | PortfolioBot | Bots | float | Max loss allowed |
| `budgets.{bot_id}.max_open_risk` | PortfolioBot | Bots | float | Max open risk |
| `budgets.{bot_id}.max_trades_per_day` | PortfolioBot | Bots | int | Trade limit |
| `budgets.{bot_id}.max_concurrent_positions` | PortfolioBot | Bots | int | Position limit |

### Trade Tracking State
| Key Pattern | Set By | Read By | Type | Purpose |
|-------------|--------|---------|------|---------|
| `{bot_id}.daily_trades` | Bots | Bots | int | Today's trade count |
| `{bot_id}.daily_trades.{date}` | Bots | Bots | int | Date-specific count |
| `cooldown_{bot}_{symbol}` | ExecutionService | ExecutionService | float | Cooldown timestamp |
| `trades` | ExitBot | Analytics | list | Trade history |
| `stats.wins` | ExitBot | Dashboard | int | Win count |
| `stats.losses` | ExitBot | Dashboard | int | Loss count |
| `stats.total_profit` | ExitBot | Dashboard | float | Total profit |
| `stats.total_loss` | ExitBot | Dashboard | float | Total loss |

### Strategy System State
| Key Pattern | Set By | Read By | Type | Purpose |
|-------------|--------|---------|------|---------|
| `strategy_kill.{strategy_id}` | StrategyKillSwitch | StrategyRunner | KillStatus | Per-strategy halt |
| `strategy_perf.{strategy_id}` | StrategyKillSwitch | StrategyKillSwitch | PerformanceData | Rolling PnL |
| `position.{order_id}.strategy_id` | OptionsBot | ExitBot | string | Link position to strategy |

### System State
| Key | Set By | Read By | Type | Purpose |
|-----|--------|---------|------|---------|
| `run_id` | Orchestrator | Decision Tracker | string | Process session ID |
| `loop_id` | Orchestrator | Logging | int | Loop iteration counter |
| `day_start_equity` | Orchestrator | PortfolioBot, ExitBot | float | Equity at day start |
| `screener.active_stocks` | ExecutionService | Bots | list | Selected stock tickers |
| `screener.active_options` | ExecutionService | Bots | list | Selected option tickers |

## 5.2 Event Log Reference

Key events logged to `logs/app.jsonl`:

### System Events
| Event | Module | Significance | Action Required |
|-------|--------|--------------|-----------------|
| `orchestrator_init` | orchestrator | System starting | None |
| `orchestrator_ready` | orchestrator | Init complete | None |
| `loop_start` | orchestrator | Loop iteration begins | None |
| `loop_end` | orchestrator | Loop iteration ends | None |
| `config_conflicts_found` | orchestrator | Config issues detected | Review conflicts |

### Halt Events
| Event | Module | Significance | Action Required |
|-------|--------|--------------|-----------------|
| `halt_activated` | HaltManager | Trading stopped | Investigate cause |
| `halt_cleared` | HaltManager | Trading resumed | Monitor |
| `execution_halted` | ExecutionService | Bots blocked | Check halt reason |

### Health Events
| Event | Module | Significance | Action Required |
|-------|--------|--------------|-----------------|
| `critical_auth_failure` | HealthMonitor | **API auth failed** | Fix API keys |
| `health_snapshot` | ExitBot | Health status | Check if ok=false |

### Trading Events
| Event | Module | Significance | Action Required |
|-------|--------|--------------|-----------------|
| `order_submitted` | AlpacaClient | Order sent | None |
| `order_filled` | AlpacaClient | Order executed | None |
| `order_rejected` | AlpacaClient | Order failed | Investigate |
| `trade_entry_recorded` | TradeTracker | Entry logged | None |
| `trade_outcome_recorded` | TradeTracker | Exit logged | Review PnL |

### Trailing Stop Events
| Event | Module | Significance | Action Required |
|-------|--------|--------------|-----------------|
| `trailing_stop_init` | TrailingStopManager | Stop created | None |
| `trailing_stop_armed` | TrailingStopManager | Stop active | None |
| `trailing_stop_exit` | TrailingStopManager | Exit triggered | Review trade |

### Strategy Events
| Event | Module | Significance | Action Required |
|-------|--------|--------------|-----------------|
| `strategy_registry_loaded` | StrategyRegistry | Strategies loaded | None |
| `strategy_killed` | StrategyKillSwitch | Strategy disabled | Review drawdown |
| `strategy_kill_cleared` | StrategyKillSwitch | Strategy enabled | None |
| `backtest_gate_fail` | BacktestGate | Strategy blocked | Check thresholds |

### ML Events
| Event | Module | Significance | Action Required |
|-------|--------|--------------|-----------------|
| `ml_model_loaded` | MLSignalService | Model ready | None |
| `ml_model_not_found` | MLSignalService | Model unavailable | Train or copy model |
| `ml_score_entry` | MLSignalService | Trade scored | None |

## 5.3 Config to Code Mapping

Where config values are consumed:

| Config Path | File | Line/Function | Effect |
|-------------|------|---------------|--------|
| `trading.global_halt` | core/halt.py | is_halted() | Forces halt |
| `trading.loop_interval_seconds` | main.py | main() | Sleep between loops |
| `risk.global_max_daily_loss_pct` | services/portfolio.py | run() | Budget calculation |
| `health.max_api_failures_in_window` | core/health.py | get_snapshot() | Halt threshold |
| `health.max_price_staleness_seconds` | core/health.py | get_snapshot() | Stale data threshold |
| `ml.enabled` | All bots | Various | Enable/disable ML |
| `ml.threshold` | All bots | Trade filter | Minimum score |
| `ml.adaptive.enabled` | ml/signal_service.py | get_adaptive_threshold() | Dynamic threshold |
| `cache.account_ttl_seconds` | services/alpaca_client.py | __init__ | Account cache |
| `cache.quote_ttl_seconds` | services/alpaca_client.py | __init__ | Quote cache |

---

# 6. State Flow Diagrams

## 6.1 Daily Trading Cycle

```
06:00 PST - Pre-market Intelligence
│
├── PreMarketIntelligenceService scans gaps
├── Universe screener identifies opportunities
└── State: screener.active_* populated

06:30 PST - Market Open
│
├── TwentyMinuteBot runs (06:30-06:50)
├── Other bots start session
├── State: day_start_equity set
└── Portfolio budgets allocated

06:35 PST - Full Trading Active
│
├── MomentumBot active (06:35-12:55)
├── OptionsBot active (06:40-12:30)
├── CryptoBot active (24/7)
│
├── Each loop:
│   ├── ExitBot checks positions
│   ├── Trailing stops updated
│   ├── New trades executed if conditions met
│   └── State: trailing_stop.*, cooldown_*, daily_trades updated

13:00 PST - Market Close
│
├── Stock bots stop new entries
├── ExitBot continues managing positions
├── State: Positions may remain overnight

17:00 PST - After Hours End
│
├── Options expire (if 0DTE)
├── CryptoBot continues 24/7
└── State: daily_trades preserved for reporting

Next Day 00:00 UTC - Daily Reset
│
├── Loop ID continues incrementing
├── Daily trade counts NOT auto-reset (date-keyed)
└── day_start_equity recalculated on first loop
```

## 6.2 Position Lifecycle

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        POSITION LIFECYCLE                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   ┌──────────────┐                                                       │
│   │  SIGNAL      │  Bot generates buy/sell signal                        │
│   │  GENERATED   │  → decision_tracker.record_signal()                   │
│   └──────┬───────┘                                                       │
│          │                                                               │
│          ▼                                                               │
│   ┌──────────────┐                                                       │
│   │  ML SCORED   │  ML probability assigned                              │
│   │              │  → If < threshold: REJECTED                           │
│   └──────┬───────┘                                                       │
│          │                                                               │
│          ▼                                                               │
│   ┌──────────────┐                                                       │
│   │  RISK        │  Position size calculated                             │
│   │  SIZED       │  Pre-trade checks passed                              │
│   └──────┬───────┘                                                       │
│          │                                                               │
│          ▼                                                               │
│   ┌──────────────┐                                                       │
│   │  ORDER       │  Order submitted to Alpaca                            │
│   │  SUBMITTED   │  → order_ids table updated                            │
│   │              │  → trailing_stop.* state created                      │
│   └──────┬───────┘                                                       │
│          │                                                               │
│          ▼                                                               │
│   ┌──────────────┐                                                       │
│   │  POSITION    │  Order filled, position active                        │
│   │  OPEN        │  → ExitBot monitors every loop                        │
│   │              │  → Trailing stop updates each tick                    │
│   └──────┬───────┘                                                       │
│          │                                                               │
│          ├────────────────────────────────────────┐                      │
│          │                                        │                      │
│          ▼                                        ▼                      │
│   ┌──────────────┐                         ┌──────────────┐              │
│   │  TRAILING    │  Profit > activation    │  STOP LOSS   │              │
│   │  ACTIVE      │  Stop tracks price      │  TRIGGERED   │              │
│   │  (armed)     │                         │              │              │
│   └──────┬───────┘                         └──────┬───────┘              │
│          │                                        │                      │
│          │                                        │                      │
│          ▼                                        ▼                      │
│   ┌──────────────┐                         ┌──────────────┐              │
│   │  TRAILING    │                         │  EXIT        │              │
│   │  TRIGGERED   │                         │  EXECUTED    │              │
│   └──────┬───────┘                         └──────┬───────┘              │
│          │                                        │                      │
│          └────────────────┬───────────────────────┘                      │
│                           │                                              │
│                           ▼                                              │
│                    ┌──────────────┐                                      │
│                    │  POSITION    │  trade_outcome_tracker logs          │
│                    │  CLOSED      │  PnL recorded                        │
│                    │              │  trailing_stop.* state deleted       │
│                    │              │  Strategy kill-switch updated        │
│                    └──────────────┘                                      │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

# 7. Code Fix Examples

## 7.1 Add Retry Logic to API Calls

**Problem**: API calls fail silently on timeout

**Before**:
```python
def get_quote(self, symbol: str) -> Dict:
    response = requests.get(f"{self.base_url}/quotes/{symbol}", 
                           headers=self.headers)
    return response.json()
```

**After**:
```python
def get_quote(self, symbol: str, max_retries: int = 3) -> Dict:
    """Get quote with retry logic and proper error handling."""
    for attempt in range(max_retries):
        try:
            response = requests.get(
                f"{self.base_url}/quotes/{symbol}",
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 200:
                self._health.record_price_tick()
                return response.json()
            
            elif response.status_code in (401, 403):
                self._health.record_critical_auth_failure(
                    f"Auth failed: {response.status_code}"
                )
                self._logger.error(f"Auth failure for {symbol}")
                return {}
            
            else:
                self._health.record_api_failure(
                    f"HTTP {response.status_code}"
                )
                
        except requests.Timeout:
            self._health.record_connection_failure("Timeout")
            self._logger.warn(f"Timeout getting quote for {symbol}, attempt {attempt + 1}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
                continue
                
        except requests.RequestException as e:
            self._health.record_connection_failure(str(e))
            self._logger.error(f"Request error for {symbol}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
    
    return {}
```

---

## 7.2 Add State Validation Before Trade

**Problem**: Code assumes state exists and is valid type

**Before**:
```python
def execute_trade(self, symbol: str):
    daily_trades = get_state(f"{self.bot_id}.daily_trades")
    if daily_trades >= self.max_trades:
        return
```

**After**:
```python
def execute_trade(self, symbol: str):
    """Execute trade with proper state validation."""
    # Get daily trades with safe defaults
    daily_trades = get_state(f"{self.bot_id}.daily_trades", default=0)
    
    # Handle None and invalid types
    if daily_trades is None:
        daily_trades = 0
    
    if not isinstance(daily_trades, (int, float)):
        self._logger.warn(
            f"Invalid daily_trades state: {daily_trades}, resetting",
            bot_id=self.bot_id
        )
        daily_trades = 0
        set_state(f"{self.bot_id}.daily_trades", 0)
    
    daily_trades = int(daily_trades)
    
    if daily_trades >= self.max_trades:
        self._logger.log("max_trades_reached", {
            "bot_id": self.bot_id,
            "daily_trades": daily_trades,
            "max_trades": self.max_trades,
            "action": "skipping"
        })
        return
    
    # Continue with trade execution...
```

---

## 7.3 Add Graceful Degradation for ML

**Problem**: Code crashes if ML model unavailable

**Before**:
```python
def score_trade(self, features: Dict) -> float:
    return self._model.predict_proba(features)[0][1]
```

**After**:
```python
def score_trade(self, features: Dict) -> float:
    """Score trade with graceful fallback if model unavailable."""
    # Check model availability
    if not self._is_available:
        self._logger.log("ml_fallback", {
            "reason": "model_unavailable",
            "using_default": 0.5
        })
        return 0.5  # Neutral probability
    
    try:
        # Validate features
        required = ['vix', 'rsi_14', 'price_vs_sma_20']
        missing = [f for f in required if f not in features]
        if missing:
            self._logger.warn(f"Missing ML features: {missing}")
            return 0.5
        
        # Run prediction
        proba = self._model.predict_proba(features)[0][1]
        
        # Validate output
        if not 0.0 <= proba <= 1.0:
            self._logger.warn(f"Invalid probability: {proba}")
            return 0.5
            
        return float(proba)
        
    except Exception as e:
        self._logger.error(f"ML prediction failed: {e}")
        return 0.5
```

---

## 7.4 Add Comprehensive Audit Trail

**Problem**: No audit trail for trade decisions

**Before**:
```python
if ml_score > threshold:
    self.execute_order(symbol, side, qty)
```

**After**:
```python
def _make_trade_decision(self, symbol: str, side: str, 
                         ml_score: float, qty: int) -> bool:
    """Make trade decision with full audit trail."""
    threshold = self._get_ml_threshold()
    regime = get_current_regime()
    
    # Build decision record
    decision_record = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "run_id": get_state("run_id"),
        "loop_id": get_state("loop_id"),
        "bot_id": self.bot_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "ml_score": ml_score,
        "threshold": threshold,
        "passed_ml": ml_score > threshold,
        "regime": regime.sentiment.value if regime else "unknown",
        "vix": regime.vix if regime else 0,
        "decision": "pending"
    }
    
    # Log pre-decision state
    self._decision_tracker.record_signal(decision_record)
    
    # Make decision
    if ml_score <= threshold:
        decision_record["decision"] = "rejected_ml"
        decision_record["rejection_reason"] = f"ML score {ml_score:.4f} <= threshold {threshold:.4f}"
        self._decision_tracker.update(decision_record)
        self._logger.log("trade_rejected", decision_record)
        return False
    
    # Execute trade
    try:
        order_result = self.execute_order(symbol, side, qty)
        decision_record["decision"] = "executed"
        decision_record["order_id"] = order_result.get("id")
        decision_record["fill_price"] = order_result.get("filled_avg_price")
    except Exception as e:
        decision_record["decision"] = "execution_failed"
        decision_record["error"] = str(e)
        self._logger.error(f"Trade execution failed: {e}")
        return False
    finally:
        self._decision_tracker.update(decision_record)
        self._logger.log("trade_decision", decision_record)
    
    return True
```

---

## 7.5 Add Circuit Breaker Pattern

**Problem**: Continuous failures cause cascading issues

**Before**:
```python
def fetch_data(self):
    while True:
        try:
            return self.api.get_data()
        except:
            time.sleep(1)
```

**After**:
```python
class CircuitBreaker:
    """Circuit breaker for API calls."""
    
    def __init__(self, failure_threshold: int = 5, 
                 reset_timeout: int = 60):
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.last_failure_time = 0
        self.state = "closed"  # closed, open, half-open
    
    def can_execute(self) -> bool:
        if self.state == "closed":
            return True
        
        if self.state == "open":
            if time.time() - self.last_failure_time > self.reset_timeout:
                self.state = "half-open"
                return True
            return False
        
        # half-open: allow one test request
        return True
    
    def record_success(self):
        self.failure_count = 0
        self.state = "closed"
    
    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            self.state = "open"


# Usage:
class DataFetcher:
    def __init__(self):
        self._circuit_breaker = CircuitBreaker(failure_threshold=5, reset_timeout=60)
        self._logger = get_logger()
    
    def fetch_data(self) -> Optional[Dict]:
        if not self._circuit_breaker.can_execute():
            self._logger.warn("Circuit breaker open, skipping API call")
            return None
        
        try:
            data = self.api.get_data()
            self._circuit_breaker.record_success()
            return data
            
        except Exception as e:
            self._circuit_breaker.record_failure()
            self._logger.error(f"API call failed: {e}, failures: {self._circuit_breaker.failure_count}")
            return None
```

---

# 8. Quick Reference Card

## 8.1 Critical Files to Check First

1. `logs/app.jsonl` - Main event log
2. `logs/decision_records.jsonl` - Trade decision audit
3. `state/trading_state.db` - Persistent state (SQLite)
4. `config/settings.yaml` - System configuration
5. `config/bots.yaml` - Bot configuration
6. `config/strategies/*.yaml` - Strategy definitions

## 8.2 Essential SQLite Queries

```sql
-- Check halt state
SELECT * FROM state WHERE key LIKE 'halt.%' OR key = 'GLOBAL_TRADING_HALT';

-- Check health state
SELECT * FROM state WHERE key LIKE 'health.%';

-- Check trailing stops
SELECT key, 
       json_extract(value, '$.armed') as armed,
       json_extract(value, '$.stop_price') as stop,
       json_extract(value, '$.entry_price') as entry
FROM state WHERE key LIKE 'trailing_stop.%';

-- Check cooldowns
SELECT * FROM state WHERE key LIKE 'cooldown_%';

-- Check order history
SELECT * FROM order_ids ORDER BY submitted_at DESC LIMIT 20;

-- Check strategy kills
SELECT * FROM state WHERE key LIKE 'strategy_kill.%';

-- Check budgets
SELECT * FROM state WHERE key LIKE 'budgets.%';

-- Check bot enable status
SELECT * FROM state WHERE key LIKE 'bots.%.enabled';

-- Clear halt (use with caution!)
DELETE FROM state WHERE key LIKE 'halt.%' OR key = 'GLOBAL_TRADING_HALT';
```

## 8.3 Essential Log Grep Patterns

```bash
# Find all errors
grep '"level":"error"' logs/app.jsonl | tail -20

# Find halt events
grep "halt_activated\|halt_cleared" logs/app.jsonl

# Find order events
grep "order_submitted\|order_filled\|order_rejected" logs/app.jsonl | tail -20

# Find trailing stop events
grep "trailing_stop" logs/app.jsonl | tail -20

# Find ML scoring
grep "ml_score" logs/app.jsonl | tail -20

# Find specific symbol
grep '"symbol":"AAPL"' logs/app.jsonl | tail -20

# Find blockers
grep "blocker\|blocked" logs/decision_records.jsonl | tail -20

# Find strategy kills
grep "strategy_killed" logs/app.jsonl

# Find auth failures
grep "critical_auth\|401\|403" logs/app.jsonl
```

## 8.4 Python Quick Checks

```python
# Check foundation health
from trading_hydra.core.state import init_state_store
from trading_hydra.core.halt import get_halt_manager
from trading_hydra.core.health import get_health_monitor

init_state_store()
print(f"Halted: {get_halt_manager().is_halted()}")
print(f"Health OK: {get_health_monitor().get_snapshot().ok}")

# Check ML availability
from trading_hydra.ml.signal_service import MLSignalService
svc = MLSignalService()
print(f"ML Available: {svc.is_available()}")

# Check Alpaca connection
from trading_hydra.services.alpaca_client import get_alpaca_client
client = get_alpaca_client()
account = client.get_account()
print(f"Account Status: {account.status}, Equity: ${account.equity}")

# Check strategies loaded
from trading_hydra.strategy.registry import StrategyRegistry
reg = StrategyRegistry()
reg.load_all()
print(f"Strategies: {reg.list_enabled()}")
```

## 8.5 Emergency Recovery Commands

```bash
# Full system state backup
sqlite3 state/trading_state.db ".dump" > state/backup_$(date +%Y%m%d_%H%M%S).sql
cp logs/app.jsonl logs/backup_$(date +%Y%m%d_%H%M%S).jsonl

# Clear ALL halts (use with caution!)
sqlite3 state/trading_state.db "DELETE FROM state WHERE key LIKE 'halt.%' OR key='GLOBAL_TRADING_HALT' OR key LIKE 'health.critical%' OR key LIKE 'strategy_kill.%'"

# Reset daily counters
sqlite3 state/trading_state.db "DELETE FROM state WHERE key LIKE '%daily_trades%'"
sqlite3 state/trading_state.db "DELETE FROM state WHERE key LIKE 'cooldown_%'"

# Recreate database (LOSES ALL STATE)
mv state/trading_state.db state/trading_state.db.old
python -c "from trading_hydra.core.state import init_state_store; init_state_store()"
```

---

**END OF ENTERPRISE CODE DIAGNOSTICS REFERENCE**

*Document Version: 1.0*
*Classification: ENTERPRISE DEVELOPER GUIDE*
*For: Trading Hydra Development & Operations Teams*
