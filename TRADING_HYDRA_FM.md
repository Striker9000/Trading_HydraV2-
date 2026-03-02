# Trading Hydra Field Manual (FM)

## CLASSIFICATION: OPERATOR REFERENCE
**Version**: 1.0  
**Last Updated**: January 2026  
**System**: Trading Hydra Autonomous Trading Platform

---

# PART I: SYSTEM FUNDAMENTALS

## Chapter 1: System Overview

### 1.1 Mission Statement

Trading Hydra is an autonomous Python-based trading system designed for institutional-grade systematic trend-following across stocks, options, and cryptocurrency markets. The system operates with fail-closed safety patterns, comprehensive audit trails, and deterministic rule-based execution.

### 1.2 Design Philosophy

| Principle | Implementation |
|-----------|----------------|
| **Fail-Closed Safety** | System halts and protects capital on any error |
| **Config-Driven** | All parameters in YAML, no code changes for tuning |
| **Deterministic** | Same inputs produce same outputs (no randomness) |
| **Auditable** | Every decision logged with full context |
| **Modular** | Each component independently testable |

### 1.3 System Components

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         TRADING HYDRA ARCHITECTURE                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                  │
│  │   Alpaca    │◄───│  Orchestrator│───►│   SQLite    │                  │
│  │     API     │    │   (Brain)    │    │   State     │                  │
│  └─────────────┘    └──────┬───────┘    └─────────────┘                  │
│                            │                                              │
│         ┌──────────────────┼──────────────────┐                          │
│         │                  │                  │                          │
│         ▼                  ▼                  ▼                          │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                  │
│  │   ExitBot   │    │ PortfolioBot│    │  Execution  │                  │
│  │  (Safety)   │    │  (Budget)   │    │   Bots      │                  │
│  └─────────────┘    └─────────────┘    └──────┬──────┘                  │
│                                               │                          │
│                     ┌─────────────────────────┼─────────────────────┐    │
│                     │              │          │          │          │    │
│                     ▼              ▼          ▼          ▼          ▼    │
│               ┌─────────┐   ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────┐│
│               │Momentum │   │ Options │ │  0DTE   │ │ 20Min   │ │Crypto││
│               │   Bot   │   │   Bot   │ │   Bot   │ │  Bot    │ │ Bot  ││
│               └─────────┘   └─────────┘ └─────────┘ └─────────┘ └──────┘│
│                                  │                                       │
│                                  ▼                                       │
│                          ┌─────────────┐                                 │
│                          │  Strategy   │                                 │
│                          │   System    │                                 │
│                          └─────────────┘                                 │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.4 Operational Modes

| Mode | ALPACA_PAPER Setting | Description |
|------|---------------------|-------------|
| **Paper Trading** | `true` | Simulated trading, no real money |
| **Live Trading** | `false` | Real money, requires `allow_live: true` |

---

# PART II: THE TRADING LOOP

## Chapter 2: Orchestration

### 2.1 The 5-Step Loop

The Orchestrator (`src/trading_hydra/orchestrator.py`) executes a continuous 5-step loop every 5 seconds (configurable):

```
LOOP START (every 5 seconds)
│
├─► STEP 1: INITIALIZE
│   ├── Load YAML configurations
│   ├── Connect to Alpaca API
│   ├── Fetch account equity and positions
│   ├── Determine account mode (micro/small/standard)
│   └── Verify health monitors
│
├─► STEP 2: EXITBOT
│   ├── Scan all open positions
│   ├── Apply trailing stops
│   ├── Check stop-loss/take-profit/time stops
│   ├── Check session end times
│   ├── Calculate daily P&L
│   ├── IF daily loss > limit: HALT
│   └── IF kill conditions met: HALT
│
├─► STEP 3: PORTFOLIOBOT
│   ├── Calculate total daily risk budget
│   ├── Apply cash reserve (30%)
│   ├── Allocate to each bot by percentage
│   └── Output: max_daily_loss per bot
│
├─► STEP 4: SCREENING & EXECUTION
│   ├── Pre-market intelligence (if in window)
│   ├── FOR each enabled bot:
│   │   ├── Check session window
│   │   ├── Screen universe
│   │   ├── Generate signals
│   │   ├── Apply ML scoring gate
│   │   ├── Apply risk checks
│   │   ├── Place orders
│   │   └── Tag positions in state
│   └── Log execution results
│
├─► STEP 5: FINALIZE
│   ├── Log loop summary
│   ├── Update performance metrics
│   ├── Persist state to SQLite
│   └── Sleep until next interval
│
└─► LOOP END → REPEAT
```

### 2.2 Loop Timing

| Setting | Location | Default | Description |
|---------|----------|---------|-------------|
| `loop_interval_seconds` | `config/settings.yaml` | 5 | Time between loop iterations |
| Market closed interval | Code default | 60 | Slower polling when markets closed |

### 2.3 Account Modes

The system adapts position sizing based on account equity:

| Mode | Equity Range | Risk Multiplier |
|------|-------------|-----------------|
| **Micro** | < $1,000 | 0.5x |
| **Small** | $1,000 - $25,000 | 1.0x |
| **Standard** | > $25,000 | 1.0x |

---

## Chapter 3: ExitBot - The Safety Guardian

### 3.1 Purpose

ExitBot is the first line of defense. It runs every loop iteration before any new trades are considered, ensuring existing positions are properly managed.

### 3.2 Exit Types

| Exit Type | Trigger Condition | Priority |
|-----------|-------------------|----------|
| **Trailing Stop** | Price drops X% from high watermark | 1 (Highest) |
| **Stop-Loss** | Position loss exceeds threshold | 2 |
| **Take-Profit** | Position gain exceeds threshold | 3 |
| **Time Stop** | Position held longer than max duration | 4 |
| **Session End** | Current time past `manage_until` | 5 |
| **Daily P&L** | Total daily loss exceeds limit | 6 |

### 3.3 Trailing Stop Mechanics

```
TRAILING STOP ALGORITHM:

1. IF position_profit_pct >= activation_profit_pct:
   │   (e.g., 0.5% profit activates trailing stop)
   │
   ├── Track HIGH WATERMARK (highest price since entry)
   │
   ├── Calculate TRAIL LEVEL:
   │   trail_price = high_watermark * (1 - trailing_pct)
   │   (e.g., if high = $100, trailing = 1%, trail = $99)
   │
   ├── IF current_price <= trail_price:
   │   └── EXIT POSITION (trail stop triggered)
   │
   └── ELSE:
       └── Update high_watermark if price higher

2. IF position_profit_pct < activation_profit_pct:
   └── Static stop-loss applies instead
```

### 3.4 Kill Conditions

| Condition | Setting | Default | Action |
|-----------|---------|---------|--------|
| API Failure | `api_failure_halt` | true | Halt on repeated API errors |
| Data Staleness | `data_stale_halt` | true | Halt if prices > 15 seconds old |
| Max Daily Loss | `max_daily_loss_halt` | true | Halt if daily loss exceeded |
| Anomaly Detected | `anomaly_halt` | true | Halt on unusual account behavior |

### 3.5 Cooloff Period

After a halt, the system enters a cooloff period before resuming:

| Setting | Value | Duration |
|---------|-------|----------|
| `cooloff_minutes` | 390 | 6.5 hours (one trading day) |

---

## Chapter 4: PortfolioBot - Budget Allocation

### 4.1 Risk Budget Calculation

```
DAILY RISK BUDGET = Account_Equity × global_max_daily_loss_pct

Example:
  Account Equity: $10,000
  Max Daily Loss: 2%
  Daily Risk Budget: $200
```

### 4.2 Bucket Allocation

| Bot | Default % | Of $200 Budget |
|-----|-----------|----------------|
| Momentum | 20% | $40 |
| Options | 40% | $80 |
| Crypto | 25% | $50 |
| TwentyMin | 15% | $30 |

### 4.3 Guardrails

| Guardrail | Value | Purpose |
|-----------|-------|---------|
| `per_bot_min_pct_of_daily_risk` | 5% | Minimum allocation per bot |
| `per_bot_max_pct_of_daily_risk` | 25% | Maximum allocation per bot |
| `cash_reserve_pct` | 30% | Always keep 30% in cash |

---

# PART III: TRADING BOTS

## Chapter 5: MomentumBot - Turtle Traders Strategy

### 5.1 Strategy Overview

MomentumBot implements the famous Turtle Traders breakout strategy developed by Richard Dennis in the 1980s. The strategy is purely mechanical and trend-following.

### 5.2 Entry Signals

```
SYSTEM 1 (20-Day Breakout):

LONG ENTRY:
  IF current_price > 20_day_high:
    AND NOT winner_filter_triggered:
      → ENTER LONG

SHORT ENTRY:
  IF current_price < 20_day_low:
    AND NOT winner_filter_triggered:
      → ENTER SHORT

Winner Filter:
  IF previous_trade_was_profitable:
    → SKIP this signal (prevents chasing)
```

### 5.3 Position Sizing (Unit Calculation)

```
TURTLE UNIT SIZE:

N = 20-day ATR (Average True Range)

Dollar Volatility = N × Point Value
                  = N × 1 (for stocks)

Account Risk Per Unit = Account × risk_pct_per_unit
                      = $10,000 × 1% = $100

Unit Size = Account_Risk / (N × Point_Value)
          = $100 / ($2.50 ATR)
          = 40 shares per unit
```

### 5.4 Pyramiding

```
ADDING TO WINNERS:

IF position_has_profit >= 0.5 × N (half ATR):
  AND current_units < max_units (4):
    → ADD another unit at current price

Pyramid Levels:
  Unit 1: Entry price
  Unit 2: Entry + 0.5N
  Unit 3: Entry + 1.0N
  Unit 4: Entry + 1.5N (maximum)
```

### 5.5 Exit Rules

| Exit Type | Rule |
|-----------|------|
| **10-Day Exit** | Price breaks 10-day low (for longs) or high (for shorts) |
| **2N Stop** | Price moves 2 × ATR against position |
| **Trailing Stop** | Configured trailing stop percentage |

### 5.6 Configuration

```yaml
momentum_bots:
  - bot_id: mom_AAPL
    enabled: true
    ticker: AAPL
    direction: both  # "long_only", "short_only", or "both"
    session:
      trade_start: "06:35"  # PST
      trade_end: "12:55"    # PST
    turtle:
      enabled: true
      system: "system_1"
      entry_lookback: 20
      exit_lookback: 10
      atr_period: 20
      risk_pct_per_unit: 1.0
      stop_loss_atr_mult: 2.0
      pyramid_enabled: true
      pyramid_trigger_atr: 0.5
      max_units: 4
      winner_filter_enabled: true
```

---

## Chapter 6: OptionsBot - Buy-Side Options Trading

### 6.1 Strategy Modes

OptionsBot supports two operational modes:

| Mode | Setting | Description |
|------|---------|-------------|
| **Legacy** | `use_strategy_system: false` | IV-aware strategy selection |
| **Strategy System** | `use_strategy_system: true` | PDF rules-based trading |

### 6.2 Strategy System Pipeline

When `use_strategy_system: true`, each strategy candidate passes through 5 gates:

```
STRATEGY SYSTEM PIPELINE:

Symbol + Strategy
       │
       ▼
┌─────────────────────────────────────────┐
│  GATE 1: KILL-SWITCH                    │
│  • Check per-strategy drawdown          │
│  • Default: -$500 over 5 trades         │
│  • IF triggered: SKIP (cooloff period)  │
└─────────────────────────────────────────┘
       │ PASS
       ▼
┌─────────────────────────────────────────┐
│  GATE 2: EARNINGS FILTER                │
│  • Policy: NEVER, ONLY, PRE, POST       │
│  • Check earnings date for symbol       │
│  • IF policy violated: SKIP             │
└─────────────────────────────────────────┘
       │ PASS
       ▼
┌─────────────────────────────────────────┐
│  GATE 3: SIGNAL RULES                   │
│  • Evaluate YAML signal rules           │
│  • price_above_ema_20: true/false       │
│  • rsi_above: 50 (threshold)            │
│  • volume_above_avg: true               │
│  • IF any rule fails: SKIP              │
└─────────────────────────────────────────┘
       │ PASS
       ▼
┌─────────────────────────────────────────┐
│  GATE 4: BACKTEST GATE                  │
│  • Check historical performance         │
│  • min_win_rate: 0.52 (52%)             │
│  • min_return: 0.005 (0.5%)             │
│  • IF below thresholds: SKIP            │
└─────────────────────────────────────────┘
       │ PASS
       ▼
┌─────────────────────────────────────────┐
│  GATE 5: CONTRACT SELECTOR              │
│  • Fetch options chain                  │
│  • Filter by delta: 0.30-0.60           │
│  • Filter by DTE: 7-45 days             │
│  • Filter by volume/OI minimums         │
│  • Select best matching contract        │
└─────────────────────────────────────────┘
       │ PASS
       ▼
   TRADE SIGNAL GENERATED
       │
       ▼
   RISK CHECKS (position size, spread gate)
       │
       ▼
   ORDER PLACED
```

### 6.3 Strategy YAML Structure

```yaml
# config/strategies/bullish_bursts.yaml

strategy_id: bullish_bursts
name: "Bullish Bursts"
description: "Momentum-based bullish options strategy"
enabled: true
direction: "call"  # "call" or "put"

# Earnings Policy
earnings_policy: "NEVER"  # NEVER, ONLY, PRE, POST

# Signal Rules (all must pass)
signal_rules:
  price_above_ema_20: true
  rsi_above: 50
  volume_above_avg: true

# Backtest Requirements
backtest_gate:
  min_win_rate: 0.52
  min_return: 0.005

# Contract Selection
contract:
  delta_min: 0.30
  delta_max: 0.60
  dte_min: 7
  dte_max: 45

# Exit Parameters
exits:
  stop_loss_pct: 0.50
  take_profit_pct: 0.30
  max_contracts: 5

# Kill-Switch (per-strategy)
kill_switch:
  max_drawdown_usd: 500
  window_trades: 5
  cooloff_minutes: 60
```

### 6.4 Strategy Inheritance

Strategies can inherit from a base strategy using the `extends` field:

```yaml
# config/strategies/bullish_bursts_no_earnings.yaml

extends: bullish_bursts
strategy_id: bullish_bursts_no_earnings
name: "Bullish Bursts (Avoid Earnings)"
earnings_policy: "NEVER"  # Override parent
```

**Strategy Files** (10 total in `config/strategies/`):
- `bullish_bursts.yaml` - Base bullish momentum
- `bullish_bursts_no_earnings.yaml` - Avoids earnings
- `bullish_bursts_only_earnings.yaml` - Only during earnings
- `bullish_bursts_pre_earnings.yaml` - Before earnings
- `bullish_bursts_post_earnings.yaml` - After earnings
- `bearish_bursts.yaml` - Base bearish momentum
- `bearish_bursts_no_earnings.yaml` - Avoids earnings
- `bearish_bursts_only_earnings.yaml` - Only during earnings
- `bearish_bursts_pre_earnings.yaml` - Before earnings
- `bearish_bursts_post_earnings.yaml` - After earnings

### 6.5 Earnings Policies

| Policy | Behavior |
|--------|----------|
| **NEVER** | Never trade within 7 days of earnings |
| **ONLY** | Only trade within 7 days of earnings |
| **PRE** | Only trade in 7 days before earnings |
| **POST** | Only trade in 7 days after earnings |

### 6.6 Kill-Switch Mechanics

```
PER-STRATEGY KILL-SWITCH:

Track rolling window of last N trades (default: 5)

Drawdown = SUM(PnL of last N trades)

IF Drawdown < -max_drawdown_usd (-$500 default):
  → FREEZE this strategy
  → Start cooloff timer (60 minutes default)
  → Log kill-switch activation

After cooloff:
  → Reset trade window
  → Strategy can trade again
```

### 6.7 Legacy Mode Configuration

```yaml
optionsbot:
  enabled: true
  use_strategy_system: false  # Use legacy mode
  tickers: [AAPL, AMD, MSFT, NVDA, TSLA]
  session:
    trade_start: "06:40"
    trade_end: "12:30"
  strategies:
    long_call:
      enabled: true
      max_cost: 3.00      # Max $3 per contract
      min_delta: 0.30
      max_delta: 0.60
      profit_target: 0.30  # 30% gain
    long_put:
      enabled: true
      max_cost: 3.00
      min_delta: 0.30
      max_delta: 0.60
      profit_target: 0.30
    straddle:
      enabled: true
      max_cost: 5.00
      profit_target: 0.25
  chain_rules:
    dte_min: 7
    dte_max: 45
    delta_min: 0.30
    delta_max: 0.60
    min_volume: 50
    min_open_interest: 100
```

---

## Chapter 7: TwentyMinuteBot - Opening Window Trading

### 7.1 Strategy Origin

Based on Jeremy Russell's "20-Minute Trader" video rules for trading the first 20 minutes after market open.

### 7.2 Session Windows

| Phase | Time (PST) | Time (EST) | Activity |
|-------|------------|------------|----------|
| Pre-Session | 06:00-06:30 | 09:00-09:30 | Analyze overnight gaps |
| Trading | 06:30-07:50 | 09:30-10:50 | Execute trades |
| Flatten | 07:50-08:00 | 10:50-11:00 | Close remaining positions |

### 7.3 Gap Analysis

```
GAP CALCULATION:

Gap % = (Today_Open - Yesterday_Close) / Yesterday_Close × 100

IF Gap % > min_gap_pct (0.3%):
  → Candidate for trading

Gap Types:
  • Gap Up: Today opens above yesterday's close
  • Gap Down: Today opens below yesterday's close
```

### 7.4 Pattern Recognition

| Pattern | Description | Signal |
|---------|-------------|--------|
| **Gap Reversal** | Gap fills back toward previous close | Fade the gap |
| **Gap Continuation** | Gap extends in same direction | Ride momentum |
| **First Bar Breakout** | Price breaks first 5-min bar high/low | Breakout trade |

### 7.5 Options Execution (Video Rules)

```
FIRST-BAR MOVE COMPUTATION:

first_bar_range = First_5min_High - First_5min_Low
expected_move = first_bar_range × k_first_bar (0.75)

BRACKET BANDS (4-8%):
  stop_loss = entry_price × (1 - stop_pct)
  take_profit = entry_price × (1 + tp_pct)

  stop_pct clamped to: 0.04 - 0.08 (4-8%)
  tp_pct clamped to: 0.04 - 0.08 (4-8%)

DELTA PREFERENCE:
  prefer_delta_min: 0.45
  prefer_delta_max: 0.60
```

### 7.6 Configuration

```yaml
twentyminute_bot:
  enabled: true
  bot_id: "twentymin_core"
  tickers: [SPY, QQQ, AAPL, MSFT, NVDA, TSLA...]
  session:
    trade_start: "06:30"
    trade_end: "07:50"
  gap:
    min_gap_pct: 0.3
  pattern:
    min_first_bar_range_pct: 0.15
    confirmation_bars: 2
  execution:
    use_options: true
    options:
      prefer_delta_min: 0.45
      prefer_delta_max: 0.60
      bracket_bands:
        stop_pct_min: 0.04
        stop_pct_max: 0.08
        tp_pct_min: 0.04
        tp_pct_max: 0.08
      risk:
        daily_budget_usd: 200
        max_contracts: 10
        stop_after_losses: 2
  exits:
    max_hold_minutes: 15
    stop_loss_pct: 0.5
    take_profit_pct: 0.5
```

---

## Chapter 8: CryptoBot - 24/7 Cryptocurrency Trading

### 8.1 Market Characteristics

| Feature | Stock/Options | Crypto |
|---------|---------------|--------|
| Hours | 6:30-13:00 PST | 24/7 |
| Bars | Daily | Hourly |
| Lookback | 20 days | 480 hours (20 days) |
| Settlement | T+2 | Instant |

### 8.2 Dynamic Universe Selection

```
UNIVERSE SELECTION PIPELINE:

Step 1: Fetch all available crypto pairs
        └── Filter: /USD pairs only (exclude /BTC, /ETH pairs)
        └── Filter: Exclude stablecoins (USDT, USDC, DAI)

Step 2: Apply minimum thresholds
        └── min_volume_24h_usd: 0 (paper trading)
        └── max_spread_pct: 10.0
        └── min_price_usd: 0.0001

Step 3: Rank by preference
        └── prefer_high_volume: weight volume
        └── prefer_high_volatility: weight volatility

Step 4: ML Re-ranking (if enabled)
        └── Evaluate top 10 candidates with ML model
        └── Select best 3 for trading

Output: Final trading universe (3 coins)
```

### 8.3 Turtle Strategy Adaptation

```
HOURLY TURTLE SYSTEM:

Entry Lookback: 480 periods (20 days × 24 hours)
Exit Lookback: 240 periods (10 days × 24 hours)
ATR Period: 480 periods

Same Turtle rules, but on hourly bars:
  • Long: Price > 480-hour high
  • Short: Price < 480-hour low
  • Exit: 240-hour channel exit or 2N ATR stop
```

### 8.4 Configuration

```yaml
cryptobot:
  enabled: true
  bot_id: "crypto_core"
  pairs: ["BTC/USD", "ETH/USD"]  # Fallback if universe disabled
  universe:
    enabled: true
    ml_rerank_enabled: true
    ml_rerank_candidates: 10
    ml_rerank_select: 3
    max_spread_pct: 10.0
    usd_pairs_only: true
    exclude_stablecoins: true
  session:
    trade_start: "00:00"
    trade_end: "23:59"
  execution:
    order_type: limit
    tif: gtc
    use_notional: true
    default_notional_usd: 50
    min_notional_usd: 10
  turtle:
    enabled: true
    entry_lookback: 480
    exit_lookback: 240
    atr_period: 480
    risk_pct_per_unit: 1.0
    stop_loss_atr_mult: 2.0
    pyramid_enabled: true
    max_units: 4
```

---

# PART IV: RISK MANAGEMENT

## Chapter 9: ML Trade Scoring

### 9.1 Purpose

The ML Signal Service predicts trade profitability before execution, filtering out low-probability trades.

### 9.2 Model Architecture

| Component | Implementation |
|-----------|----------------|
| Algorithm | GradientBoostingClassifier |
| Target | Binary (profitable/unprofitable) |
| Features | 23+ technical indicators |
| Training Data | Historical trade outcomes |

### 9.3 Feature Set

| Category | Features |
|----------|----------|
| **Price Action** | VWAP position, EMA spread, price vs SMA |
| **Momentum** | RSI(7), RSI(14), MACD, momentum |
| **Volume** | Volume ratio, OBV, relative volume |
| **Volatility** | ATR, Bollinger width, historical vol |
| **Market** | VIX, sector performance, market direction |

### 9.4 Threshold Configuration

```yaml
ml:
  enabled: true
  min_probability: 0.58        # Global default
  
  # Bot-specific thresholds
  options_threshold: 0.55      # Lower for IV edge
  crypto_threshold: 0.55       # Lower for 24/7 opportunities
  momentum_threshold: 0.60     # Stricter for stocks
  
  # Adaptive thresholds
  adaptive:
    enabled: true
    low_vix_bonus: 0.03        # Lower by 3% when VIX < 15
    high_vix_penalty: 0.05     # Raise by 5% when VIX > 30
```

### 9.5 Scoring Flow

```
TRADE CANDIDATE
      │
      ▼
┌─────────────────────────────┐
│  Extract 23+ Features       │
│  from market data           │
└─────────────────────────────┘
      │
      ▼
┌─────────────────────────────┐
│  Run through ML Model       │
│  Output: probability 0-1    │
└─────────────────────────────┘
      │
      ▼
┌─────────────────────────────┐
│  Compare to threshold       │
│  (bot-specific + adaptive)  │
└─────────────────────────────┘
      │
      ├── probability >= threshold → PROCEED
      │
      └── probability < threshold → REJECT
```

---

## Chapter 10: Institutional Position Sizing

### 10.1 Kelly Criterion

```
KELLY FORMULA:

f* = (bp - q) / b

Where:
  f* = Fraction of capital to bet
  b  = Win/loss ratio
  p  = Probability of winning
  q  = Probability of losing (1 - p)

FRACTIONAL KELLY:

position_size = f* × kelly_fraction × account_equity

kelly_fraction = 0.25 (use 25% of Kelly recommendation)
```

### 10.2 Risk Per Trade

```yaml
institutional_sizing:
  enabled: true
  base_risk_pct: 0.5           # 0.5% of NAV per trade
  max_single_position_pct: 3.0 # Max 3% in any single position
  kelly_fraction: 0.25         # Use 25% of Kelly
  min_notional: 15.0           # Minimum $15 trade size
```

### 10.3 Correlation Management

```
CORRELATION CHECKS:

Before each trade:
1. Calculate correlation with existing positions
2. IF pairwise_correlation > 0.7: BLOCK trade
3. Calculate sector exposure
4. IF sector_exposure > 20%: BLOCK trade
5. IF single_asset > 10%: BLOCK trade
```

```yaml
correlation_management:
  enabled: true
  max_pairwise_correlation: 0.7
  max_sector_exposure_pct: 20.0
  max_single_asset_pct: 10.0
```

---

## Chapter 11: Market Regime Detection

### 11.1 Volatility Indicators

| Indicator | Source | Interpretation |
|-----------|--------|----------------|
| **VIX** | CBOE | Stock market fear gauge |
| **VVIX** | CBOE | Volatility of VIX |
| **TNX** | CME | 10-year Treasury yield |
| **DXY** | ICE | US Dollar strength |
| **MOVE** | BofA | Bond market volatility |

### 11.2 Regime Classification

| Regime | VIX Level | System Behavior |
|--------|-----------|-----------------|
| **LOW** | < 15 | Normal trading, lower thresholds |
| **NORMAL** | 15-30 | Standard parameters |
| **STRESS** | > 30 | Reduced sizing, higher thresholds |

### 11.3 Regime Adjustments

```
STRESS REGIME ADJUSTMENTS:

• Position sizes: Reduced by 50%
• ML thresholds: Increased by 5%
• Max trades per day: Reduced
• Growth scaling: Capped at 1.0x
```

---

## Chapter 12: Account-Level ML Analytics

### 12.1 Five-Model System

| Model | Purpose | Output |
|-------|---------|--------|
| **RiskAdjustmentEngine** | Dynamic risk scaling | 0.25x - 1.5x multiplier |
| **BotAllocationModel** | Optimal bot allocation | % weights per bot |
| **RegimeSizer** | Regime-based sizing | Position size multiplier |
| **DrawdownPredictor** | Forecast drawdown | Probability 0-1 |
| **AnomalyDetector** | Unusual behavior | Alert flag |

### 12.2 Configuration

```yaml
account_analytics:
  enabled: true
  
  risk_adjustment:
    enabled: true
    min_multiplier: 0.25     # Defensive minimum
    max_multiplier: 1.5      # Aggressive maximum
    override: true           # Apply to bot budgets
  
  bot_allocation:
    enabled: true
    use_ml_weights: true     # Use predicted weights
  
  regime_sizing:
    enabled: true
    override_rule_based: true
  
  drawdown_prediction:
    enabled: true
    halt_threshold: 0.8      # Halt if >80% probability
    reduce_threshold: 0.5    # Reduce if >50% probability
  
  anomaly_detection:
    enabled: true
    halt_on_anomaly: false   # Alert only, don't halt
    sensitivity: 0.1
```

---

# PART V: OPERATIONS

## Chapter 13: Daily Operations

### 13.1 Pre-Market Checklist (6:00 AM PST)

| Task | Action | Location |
|------|--------|----------|
| Check system status | Verify no halts | Dashboard |
| Review overnight crypto P&L | CryptoBot trades 24/7 | Dashboard |
| Verify account equity | Check for deposits/withdrawals | Dashboard |
| Check VIX level | Regime awareness | Dashboard |
| Review configuration | Ensure settings correct | `config/*.yaml` |

### 13.2 Market Hours Monitoring

| Time (PST) | Event | Action |
|------------|-------|--------|
| 06:00-06:30 | Pre-market intel | System gathers gap/IV data |
| 06:30 | Market open | TwentyMinuteBot activates |
| 06:35 | MomentumBot start | Trading begins |
| 06:40 | OptionsBot start | Options trading begins |
| 07:50 | TwentyMinuteBot end | Flattens positions |
| 12:30 | OptionsBot end | Stops new trades |
| 12:55 | MomentumBot end | Stops new trades |
| 13:00 | Market close | All positions managed |

### 13.3 End-of-Day Checklist

| Task | Action |
|------|--------|
| Review daily P&L | Check total profit/loss |
| Check open positions | Verify all managed correctly |
| Review trade log | Check for unusual patterns |
| Verify no halt conditions | System ready for next day |

### 13.4 Weekly Maintenance

| Day | Task |
|-----|------|
| **Monday** | Review previous week's performance |
| **Wednesday** | Check ML model accuracy |
| **Friday** | Backup state database |
| **Weekend** | Review configuration for optimizations |

---

## Chapter 14: Troubleshooting

### 14.1 Common Issues

| Symptom | Cause | Solution |
|---------|-------|----------|
| No trades executing | ML threshold too high | Lower `min_probability` |
| System halted | Daily loss exceeded | Wait for cooloff or clear halt |
| API errors | Alpaca connectivity | Check credentials, Alpaca status |
| Slow performance | Rate limiting | Increase `loop_interval_seconds` |
| Strategy not firing | Kill-switch triggered | Wait for cooloff |

### 14.2 Log Analysis

```bash
# View live logs
tail -f logs/app.jsonl | jq .

# Search for errors
grep -i "error" logs/app.jsonl | jq .

# Find halt events
grep "halt" logs/app.jsonl | jq .

# Review trade decisions
tail -f logs/decision_records.jsonl | jq .
```

### 14.3 State Recovery

```bash
# Check state database
sqlite3 state/trading_state.db ".tables"

# View recent state
sqlite3 state/trading_state.db "SELECT * FROM kv_store LIMIT 10;"

# Fresh start (reset all state)
python3 main.py --fresh-start
```

---

## Chapter 15: Emergency Procedures

### 15.1 Emergency Halt

**Immediate Stop - All Trading:**

```yaml
# config/settings.yaml
trading:
  global_halt: true  # Set this to stop all new trades
```

Or via dashboard: Click "Emergency Halt" button.

### 15.2 Position Flatten

To close all positions immediately:

1. Use dashboard "Flatten All" button, or
2. Log into Alpaca directly and close positions

### 15.3 Recovery Steps

1. Identify cause of emergency (check logs)
2. Verify all positions are flat or managed
3. Wait for cooloff period (390 minutes)
4. Review and adjust configuration if needed
5. Set `global_halt: false` to resume
6. Monitor first trading session closely

---

# PART VI: APPENDICES

## Appendix A: Configuration Quick Reference

### A.1 Critical Settings

| Setting | File | Path | Default |
|---------|------|------|---------|
| Loop interval | settings.yaml | `runner.loop_interval_seconds` | 5 |
| Max daily loss | settings.yaml | `risk.global_max_daily_loss_pct` | 2.0 |
| ML threshold | settings.yaml | `ml.min_probability` | 0.58 |
| Paper trading | Environment | `ALPACA_PAPER` | true |
| Global halt | settings.yaml | `trading.global_halt` | false |
| Strategy system | bots.yaml | `optionsbot.use_strategy_system` | true |

### A.2 Bot Enable/Disable

| Bot | File | Path |
|-----|------|------|
| MomentumBot | bots.yaml | `momentum_bots[].enabled` |
| OptionsBot | bots.yaml | `optionsbot.enabled` |
| 0DTE Bot | bots.yaml | `optionsbot_0dte.enabled` |
| TwentyMinuteBot | bots.yaml | `twentyminute_bot.enabled` |
| CryptoBot | bots.yaml | `cryptobot.enabled` |
| ExitBot | bots.yaml | `exitbot.enabled` |

---

## Appendix B: File Structure

```
trading-hydra/
├── main.py                      # Entry point
├── config/
│   ├── settings.yaml            # System settings
│   ├── bots.yaml                # Bot configurations
│   └── strategies/              # Strategy system YAMLs
│       ├── bullish_bursts.yaml
│       ├── bearish_bursts.yaml
│       └── ... (10 files)
├── src/trading_hydra/
│   ├── orchestrator.py          # Main loop
│   ├── bots/
│   │   ├── momentum_bot.py
│   │   ├── options_bot.py
│   │   ├── twentyminute_bot.py
│   │   └── crypto_bot.py
│   ├── services/
│   │   ├── exitbot.py
│   │   ├── portfoliobot.py
│   │   └── ...
│   ├── strategy/
│   │   ├── registry.py
│   │   ├── validator.py
│   │   ├── backtest_gate.py
│   │   ├── options_selector.py
│   │   ├── earnings_filter.py
│   │   ├── kill_switch.py
│   │   └── runner.py
│   ├── ml/
│   │   ├── signal_service.py
│   │   └── ...
│   ├── risk/
│   │   ├── position_sizer.py
│   │   └── correlation_manager.py
│   └── dashboard/
│       └── app.py
├── state/
│   ├── trading_state.db         # SQLite state
│   └── metrics.db               # Performance metrics
├── logs/
│   ├── app.jsonl                # Application logs
│   └── decision_records.jsonl   # Audit trail
├── models/                      # Trained ML models
└── docs/
    ├── TRADING_HYDRA_SOP.md
    └── TRADING_HYDRA_FM.md
```

---

## Appendix C: Glossary

| Term | Definition |
|------|------------|
| **ATR** | Average True Range - volatility measure |
| **DTE** | Days To Expiration - time until option expires |
| **Delta** | Option's sensitivity to underlying price |
| **IV** | Implied Volatility - expected future volatility |
| **Kelly Criterion** | Optimal bet sizing formula |
| **NAV** | Net Asset Value - total account equity |
| **OI** | Open Interest - outstanding option contracts |
| **PST** | Pacific Standard Time |
| **VWAP** | Volume Weighted Average Price |
| **N** | Turtle term for ATR |

---

## Appendix D: Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ALPACA_KEY` | Yes | Alpaca API key |
| `ALPACA_SECRET` | Yes | Alpaca API secret |
| `ALPACA_PAPER` | Yes | "true" for paper, "false" for live |

---

## Appendix E: Command Reference

```bash
# Start system
python3 main.py

# Fresh start (reset state)
python3 main.py --fresh-start

# View logs
tail -f logs/app.jsonl | jq .

# Check state
sqlite3 state/trading_state.db ".tables"

# Retrain ML
python scripts/ml/train_model.py

# Run tests
python -m pytest tests/ -v
```

---

**END OF FIELD MANUAL**

*This document is classified OPERATOR REFERENCE and should be treated as the authoritative technical reference for Trading Hydra operations.*
