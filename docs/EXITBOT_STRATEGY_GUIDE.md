# ExitBot v2 Elite — Complete Strategy Guide

## Institutional-Grade Exit Intelligence for Automated Trading

---

**Version**: 2.0 Elite
**System**: Trading Hydra
**Lines of Code**: 5,004
**Last Updated**: February 2026

---

## Table of Contents

| Module | Title | Page |
|--------|-------|------|
| 1 | [What Is ExitBot?](#module-1-what-is-exitbot) | Overview & Philosophy |
| 2 | [Architecture Deep Dive](#module-2-architecture-deep-dive) | Components & Data Flow |
| 3 | [The Monitoring Loop](#module-3-the-monitoring-loop) | How Positions Are Tracked |
| 4 | [Trailing Stops](#module-4-trailing-stops) | Dynamic Stop Management |
| 5 | [Take-Profit Tiers](#module-5-take-profit-tiers) | TP1 / TP2 / TP3 Scaling |
| 6 | [Parabolic Runner Mode](#module-6-parabolic-runner-mode) | Capturing Extended Moves |
| 7 | [Hard Stops & Reversal Sense](#module-7-hard-stops--reversal-sense) | Safety Net Exits |
| 8 | [ProfitSniper Integration](#module-8-profitsniper-integration) | Velocity-Based Exit Intelligence |
| 9 | [SessionProtection](#module-9-sessionprotection) | HWM Giveback Caps & Profit Locks |
| 10 | [News-Based Exits](#module-10-news-based-exits) | Sentiment-Driven Exit Triggers |
| 11 | [Pre-Staged Exit Orders](#module-11-pre-staged-exit-orders) | Broker-Side Protection |
| 12 | [Spread Protection & Order Execution](#module-12-spread-protection--order-execution) | Smart Order Routing |
| 13 | [Telemetry, Forensics & Kill-Switches](#module-13-telemetry-forensics--kill-switches) | Decision Logging & Safety |
| A | [Configuration Reference](#appendix-a-configuration-reference) | Full YAML Config Guide |
| B | [Exit Decision Priority](#appendix-b-exit-decision-priority) | Authority Hierarchy |
| C | [Troubleshooting](#appendix-c-troubleshooting) | Common Issues & Fixes |
| D | [Quick Reference Cheat Sheet](#appendix-d-quick-reference-cheat-sheet) | One-Page Summary |

---

# Module 1: What Is ExitBot?

## The Problem With Manual Exits

Most traders spend 90% of their energy finding entries. The real edge, however, lives in *how you exit*. Consider these failure modes:

- **Giving back profits**: Stock runs +8%, you hold, it reverses to +1%. You captured 1% instead of 8%.
- **Panic selling**: Stock dips -2%, you sell. It rebounds +15% the next hour.
- **Leaving runners on the table**: You hit your target and sell 100%. Stock continues another 20%.
- **System crash risk**: Your bot dies mid-trade. No stop-loss is active. Price gaps against you.

ExitBot solves all of these problems simultaneously.

## What ExitBot Does

ExitBot is a **dedicated exit-only bot** that runs in its own thread, continuously monitoring every open position in the Trading Hydra system. It does NOT enter trades — it only exits them.

**Core Capabilities:**
- Trailing stops that ratchet up as price moves in your favor
- Three-tier take-profit system (TP1/TP2/TP3) with partial exits
- Parabolic runner mode that lets winners extend beyond TP3
- Hard stop-loss as an absolute safety net
- Reversal-sense stops that detect momentum shifts
- ATR-based adaptive thresholds that auto-adjust to volatility
- ProfitSniper integration for velocity-based exit detection
- SessionProtection with high-water-mark giveback caps
- News sentiment exit triggers
- Pre-staged broker-side OCO orders for crash protection
- Spread protection to avoid bad fills on wide-spread assets
- Regime-based stop tightening (bear markets = tighter stops)

## The ExitBot Philosophy

```
"Let winners run. Cut losers short. Protect capital at all costs."
```

ExitBot implements this through a **layered defense model**:

```
Layer 1: Pre-Staged Orders     → Broker-side SL/TP (crash protection)
Layer 2: Hard Stop-Loss        → Absolute loss limit (safety net)
Layer 3: Trailing Stop         → Dynamic stop that follows price up
Layer 4: Take-Profit Tiers     → Partial exits at profit targets
Layer 5: Parabolic Runner      → Let extended moves ride
Layer 6: ProfitSniper          → Velocity-based momentum detection
Layer 7: Reversal Sense        → Detect momentum reversals
Layer 8: News Exits            → Sentiment-driven emergency exits
Layer 9: SessionProtection     → Daily P&L caps and profit locks
```

Each layer operates independently. If one fails, the others still protect you.

## Why a Separate Bot?

ExitBot runs in its **own dedicated thread**, independent of entry bots. This design choice is critical:

1. **Speed**: Entry bots may be sleeping or processing. ExitBot never sleeps during market hours.
2. **Isolation**: A crash in MomentumBot doesn't affect exit logic.
3. **Universality**: ExitBot monitors ALL positions from ALL bots — MomentumBot, TwentyMinuteBot, HailMary, CryptoBot, etc.
4. **Crash Recovery**: Pre-staged broker-side orders provide protection even if ExitBot itself goes down.

---

# Module 2: Architecture Deep Dive

## Class Structure

```
ExitBot (5,004 lines)
├── __init__()                    → Initialize all subsystems
├── start() / stop()              → Thread lifecycle
├── _run_loop()                   → Main monitoring loop (8-sec interval)
├── _monitor_positions()          → Per-position exit logic
│
├── Trailing Stop System
│   ├── TrailingStopManager       → State persistence layer
│   ├── TrailingStopState         → Per-position stop state
│   ├── _update_trailing_stop()   → Ratchet stop on new highs
│   └── _execute_trailing_stop_exit()
│
├── Take-Profit System
│   ├── TakeProfitConfig          → TP1/TP2/TP3 configuration
│   ├── _get_take_profit_config() → Asset-class-specific TP levels
│   ├── _check_and_execute_take_profit()
│   ├── _execute_take_profit_exit()
│   ├── _adjust_stop_to_breakeven() → Post-TP1 stop adjustment
│   └── _adjust_stop_to_tp1_level() → Post-TP2 stop adjustment
│
├── Adaptive Thresholds
│   ├── AdaptiveThresholdConfig   → ATR-based adjustment params
│   └── _apply_adaptive_thresholds()
│
├── Parabolic Runner Mode
│   └── _widen_trailing_stop_for_runner()
│
├── Hard Stop System
│   ├── HardStopConfig            → Absolute loss limits
│   ├── _check_hard_stop_loss()
│   └── _execute_hard_stop_exit()
│
├── Reversal Sense
│   ├── ReversalSenseConfig       → Momentum detection params
│   ├── _check_reversal_sense()
│   └── _execute_reversal_sense_exit()
│
├── ProfitSniper Integration
│   └── (External: ProfitSniper class)
│
├── SessionProtection Integration
│   └── (External: SessionProtection class)
│
├── News Exit System
│   ├── _check_news_exit()        → Sentiment analysis
│   └── _execute_news_exit()
│
├── Pre-Staged Orders (Broker-Side)
│   ├── stage_exit_orders()       → Place OCO on Alpaca
│   ├── update_staged_stop()      → Trail broker-side stop
│   ├── cancel_staged_orders()    → Cancel before manual exit
│   ├── check_staged_order_status()
│   ├── reconcile_staged_orders() → Startup reconciliation
│   └── cancel_staged_orders_for_symbol() → Wash-trade prevention
│
├── Spread Protection
│   └── _close_with_spread_protection()
│
├── V2 Intelligence (Elite)
│   ├── TradeMemoryEngine         → Historical trade pattern memory
│   ├── TradeHealthScorer         → Position health scoring
│   ├── ExitDecisionEngine        → Multi-factor exit decisions
│   └── ForwardProjectionEngine   → Forward P&L projection
│
└── State Persistence
    ├── _save_known_positions()
    ├── _load_recent_exits() / _save_recent_exits()
    ├── _save_tp_tiers_state()
    └── _save_staged_orders()
```

## Data Types

### PositionInfo
Every position is normalized into a `PositionInfo` dataclass:

| Field | Type | Description |
|-------|------|-------------|
| `symbol` | str | Ticker symbol (e.g., "AAPL", "BTC/USD", "AAPL250117C00200000") |
| `side` | str | "long" or "short" |
| `qty` | float | Position quantity |
| `entry_price` | float | Average entry price |
| `current_price` | float | Current market price |
| `unrealized_pnl` | float | Unrealized P&L in dollars |
| `asset_class` | str | "us_equity", "crypto", "option" (also accepts "us_option") |
| `bot_id` | str | Which bot opened this position |
| `position_id` | str | Unique position identifier |

### TrailingStopState
Per-position trailing stop tracking:

| Field | Type | Description |
|-------|------|-------------|
| `high_water` | float | Highest price since entry (longs) |
| `low_water` | float | Lowest price since entry (shorts) |
| `stop_price` | float | Current trailing stop price |
| `armed` | bool | Whether trailing stop is active |
| `arm_pct` | float | Profit % needed to activate |
| `trail_pct` | float | Trailing distance percentage |

### ExitRecord
Logged for every exit:

| Field | Type | Description |
|-------|------|-------------|
| `symbol` | str | What was exited |
| `pnl` | float | Profit/loss in dollars |
| `pnl_percent` | float | Profit/loss percentage |
| `reason` | str | Why exited (trailing_stop, hard_stop, tp1, tp2, tp3, reversal_sense, news_sentiment) |
| `bot_id` | str | Which bot's position |
| `timestamp` | str | When exit occurred |

## Thread Model

```
Main Thread (Orchestrator)
├── Step 1: Initialize
├── Step 2: HaltCheck
├── Step 3: PortfolioBot (entries)
├── Step 4: Execution
└── Step 5: Finalize

ExitBot Thread (Independent)  ← Runs continuously
└── Loop every 8 seconds:
    ├── Fetch all positions from Alpaca
    ├── For each position:
    │   ├── Check ProfitSniper
    │   ├── Check SessionProtection
    │   ├── Check news exits
    │   ├── Check hard stop
    │   ├── Update trailing stop
    │   ├── Check reversal sense
    │   ├── Check take-profit tiers
    │   └── Check staged order status
    └── Log summary
```

---

# Module 3: The Monitoring Loop

## How It Works

ExitBot's heart is `_run_loop()`, which executes every **8 seconds** during market hours. Here's what happens each iteration:

### Step 1: Fetch All Positions
```python
positions = self._alpaca.get_all_positions()
```
ExitBot pulls every open position from Alpaca, regardless of which bot opened it. This is critical — if MomentumBot opens a position and then crashes, ExitBot still monitors and protects it.

### Step 2: Detect New Positions
```python
current_ids = {p.position_id for p in positions}
new_positions = current_ids - self._known_positions
```
New positions get:
- Trailing stop state initialized
- Pre-staged OCO orders placed on Alpaca (equities only)
- First-seen timestamp recorded

### Step 3: Detect Closed Positions
```python
closed_positions = self._known_positions - current_ids
```
Positions that disappeared from Alpaca get cleaned up:
- Trailing stop state removed
- Staged orders cancelled
- TP tier tracking cleared

### Step 4: Per-Position Analysis
For each open position, ExitBot runs through its exit decision tree (see Appendix B for the full priority hierarchy).

### Step 5: Summary Logging
Each loop iteration logs:
- Total positions monitored
- Any exits triggered
- Trailing stop states
- ProfitSniper signals
- SessionProtection status

## Delegate Mode

Some bots (notably TwentyMinuteBot) set `delegate_exits_to_exitbot: false`. This means ExitBot will **not** override their exit logic. TwentyMinuteBot's "no stops" strategy requires this — ExitBot's trailing stops would interfere with the gap-and-go strategy.

When `delegate_exits_to_exitbot` is false, ExitBot still:
- Monitors the position (for logging/dashboard)
- Enforces hard stops (absolute safety net)
- Runs SessionProtection
- Provides staged order crash protection

But it will NOT:
- Apply trailing stops
- Execute take-profit exits
- Use reversal sense
- Override the bot's own exit timing

## Error Isolation

Every position is processed inside its own `try/except` block:

```python
for position in positions:
    try:
        self._process_position(position, ...)
    except Exception as e:
        self._logger.error(f"Position processing error: {e}")
        continue  # Don't let one bad position kill monitoring for ALL positions
```

This was a critical bug fix (Feb 2026) — previously, one position with bad data (e.g., missing entry price) would crash the entire monitoring loop, leaving ALL positions unprotected.

---

# Module 4: Trailing Stops

## How Trailing Stops Work

A trailing stop follows price upward (for longs) but never moves down. It creates a dynamic floor that locks in gains as a position moves in your favor.

### Visual Example (Long Position)

```
Price
  │
  │         ★ High Water Mark ($112)
  │        / \
  │       /   \
  │      /     \  ← Price pulls back
  │     /       \
  │    /    ─────── Trailing Stop ($108.64) = HWM × (1 - 3%)
  │   /
  │  / ← Price rising, stop ratchets up
  │ /
  │/ Entry ($100)
  └────────────────────────── Time

  If price drops below $108.64 → EXIT
  If price makes new high ($115) → Stop moves to $111.55
```

### The Arm-Then-Trail Pattern

Trailing stops don't activate immediately. They follow a two-phase pattern:

**Phase 1: Arming**
- Position must reach a minimum profit (e.g., +3%) before the trailing stop activates
- Until armed, only the hard stop provides protection
- This prevents premature exits on normal entry noise

**Phase 2: Trailing**
- Once armed, the stop follows price at a fixed percentage distance
- For a 3% trail on a long position at $112 high: stop = $112 × 0.97 = $108.64
- Stop only moves UP (longs) or DOWN (shorts) — never against you

### Configuration by Asset Class

Trailing stop defaults are loaded from `bots.yaml` config and can vary by bot and asset class. These are typical production values — actual values depend on your config and any per-bot overrides:

| Asset Class | Typical Arm % | Typical Trail % | Notes |
|-------------|--------------|-----------------|-------|
| us_equity | 3.0% | 3.0% | Sweep-validated for stocks; overridable per-bot |
| option / us_option | 3.0% | 2.0% | REVERTED from tight 0.5%/0.15% — bid-ask noise killed winners |
| crypto | 1.0% | 1.5% | Tighter due to 24/7 trading |

These can be overridden by ProfitSniper (velocity-based), regime tightening (bear/bull), and per-bot config sections.

### ProfitSniper Override

When ProfitSniper is enabled, it provides its own arm/trail percentages based on velocity detection. These override the default trailing stop config:

```python
sniper_config = self._profit_sniper.get_config(position)
if sniper_config:
    arm_pct = sniper_config['arm_pct']
    trail_pct = sniper_config['pullback_trail_pct']
```

### Regime-Based Tightening

In bear market regimes, trailing stops automatically tighten:

```yaml
regime_tightening:
  bear: 0.7    # 30% tighter in bear markets
  neutral: 1.0 # No change
  bull: 1.2    # 20% wider in bull markets
```

A 3% trailing stop becomes 2.1% in bear markets and 3.6% in bull markets.

## Trailing Stop State Persistence

Trailing stop states are persisted to SQLite via the `TrailingStopManager`. This means:
- Bot restarts don't lose trailing stop positions
- High-water marks survive process crashes
- Armed status is preserved across restarts

---

# Module 5: Take-Profit Tiers

## The Three-Tier System

Instead of a single take-profit target, ExitBot uses three tiers with partial exits:

```
                                    TP3: 8% → Exit 100% remaining
                                   /
                              TP2: 4% → Exit 50% remaining
                             /
                        TP1: 2% → Exit 33% of position
                       /
Entry ────────────────/──────────────────── Time
```

### Why Partial Exits?

Single take-profit targets force an all-or-nothing decision. Three tiers solve this:

| Tier | Target | Action | Stop Adjustment |
|------|--------|--------|-----------------|
| TP1 | 2% | Sell 33% of position | Move stop to **breakeven** |
| TP2 | 4% | Sell 50% of remaining | Move stop to **TP1 level** |
| TP3 | 8% | Sell 100% of remaining | Position fully closed |

### Worked Example

**Entry**: 300 shares of AAPL at $200.00

| Event | Shares | Action | P&L Locked |
|-------|--------|--------|------------|
| TP1 hit ($204) | 300 → 200 | Sell 100 shares | +$400 realized |
| Stop → breakeven | 200 remaining | Stop at $200 | Can't lose on rest |
| TP2 hit ($208) | 200 → 100 | Sell 100 shares | +$800 realized |
| Stop → TP1 ($204) | 100 remaining | Stop at $204 | Locked +$1,200 min |
| TP3 hit ($216) | 100 → 0 | Sell 100 shares | +$1,600 realized |
| **Total** | | | **$2,800** |

Compare to selling all at TP1: $1,200 (57% less).
Compare to holding all for TP3: $4,800 if hit, but $0 if it reverses at +3%.

The tiered approach captures **guaranteed partial profits** while keeping upside exposure.

### Asset-Class-Specific Targets

| Asset Class | TP1 | TP2 | TP3 | Notes |
|-------------|-----|-----|-----|-------|
| us_equity (default) | 2% | 4% | 8% | Standard stock targets |
| us_equity (momentum) | 3% | 6% | 10% | 30/60/100% of bot's take_profit_pct |
| option / us_option | 20% | 35% | 50% | 40/70/100% of bot's take_profit_pct (leverage) |
| crypto | 4% | 8% | 15% | Higher due to volatility (with parabolic runner) |

### Adaptive ATR-Based Thresholds

The tiered targets aren't static — they auto-adjust based on each stock's current volatility using ATR (Average True Range):

```
Formula: TP_level = base_TP + (ATR% × multiplier)
Clamped to: [min_bound, max_bound]
```

**Example**: NVDA with 3.5% ATR

| Tier | Base | + ATR Adjustment | = Adaptive | Clamped |
|------|------|-------------------|------------|---------|
| TP1 | 2% | + (3.5% × 0.5) = 1.75% | 3.75% | 3.75% (max 4%) |
| TP2 | 4% | + (3.5% × 1.0) = 3.50% | 7.50% | 7.50% (max 8%) |
| TP3 | 8% | + (3.5% × 2.0) = 7.00% | 15.00% | 15.00% (max 15%) |

**Example**: T (AT&T) with 0.8% ATR

| Tier | Base | + ATR Adjustment | = Adaptive | Clamped |
|------|------|-------------------|------------|---------|
| TP1 | 2% | + (0.8% × 0.5) = 0.4% | 2.40% | 2.40% (min 1.5%) |
| TP2 | 4% | + (0.8% × 1.0) = 0.8% | 4.80% | 4.80% (min 3%) |
| TP3 | 8% | + (0.8% × 2.0) = 1.6% | 9.60% | 9.60% (min 6%) |

High-volatility stocks get wider targets to avoid premature exits. Low-volatility stocks get tighter targets to avoid holding too long.

### Configuration

```yaml
take_profit:
  enabled: true
  parabolic_runner:
    enabled: true
    widen_trailing_pct: 50.0
    adaptive:
      enabled: true
      atr_period: 14
      base_atr_mult_tp1: 0.5
      base_atr_mult_tp2: 1.0
      base_atr_mult_tp3: 2.0
      min_tp1_pct: 1.5
      max_tp1_pct: 4.0
      min_tp2_pct: 3.0
      max_tp2_pct: 8.0
      min_tp3_pct: 6.0
      max_tp3_pct: 15.0
```

---

# Module 6: Parabolic Runner Mode

## The Problem: Exiting Too Early on Big Moves

Standard TP3 exits 100% of the position at the third target. But some trades turn into parabolic runners — stocks that gap up 20%, 30%, or more in a single session. Exiting at TP3 (8%) means leaving enormous profits on the table.

## The Solution: Skip TP3, Ride the Trailing Stop

When parabolic runner mode is enabled:

1. TP1 triggers normally → sell 33%, move stop to breakeven
2. TP2 triggers normally → sell 50% of remaining, move stop to TP1
3. **TP3 is SKIPPED** → trailing stop (now at TP1 level) rides the move

```
Price
  │
  │                          ★ Parabolic peak ($130)
  │                         / \
  │                        /   ← Trailing stop finally triggers at $126
  │                       /     (captured +26% instead of +8%)
  │                      /
  │               TP3 at $108 ← SKIPPED (would have exited here)
  │              /
  │         TP2 at $104
  │        /
  │   TP1 at $102
  │  /
  │/ Entry ($100)
  └────────────────────────────── Time
```

## Trailing Stop Widening

When runner mode activates after TP2, the trailing stop distance is **widened** to give the parabolic move more room:

```python
widen_factor = 1.0 + (widen_trailing_pct / 100.0)  # e.g., 1.5 for 50% widening
new_distance = current_distance * widen_factor
```

**Example**: 3% trailing stop with 50% widening = 4.5% trailing stop

This wider stop prevents the trailing stop from triggering on normal intraday pullbacks during a large move.

## One-Time Widening

The trailing stop is only widened ONCE per position (tracked via state key `runner_widened_{position_id}`). This prevents the stop from widening infinitely on repeated loop iterations.

## When Runner Mode Activates

Runner mode only activates when ALL of these are true:
- `parabolic_runner.enabled: true` in config
- TP2 has been hit (tier 2 is in the `_tp_tiers_hit` set)
- TP3 has NOT been hit yet
- Position is still open with remaining shares

---

# Module 7: Hard Stops & Reversal Sense

## Hard Stop-Loss

The hard stop is ExitBot's **absolute safety net**. It triggers when a position's loss exceeds a fixed percentage from entry, regardless of any other exit logic.

### How It Works

```python
if pnl_pct <= -config.stop_loss_pct:
    # HARD STOP TRIGGERED — exit 100% immediately
```

There is no arming, no trailing, no partial exits. Hard stop = full position exit at market price.

### Why Hard Stops Are Separate from Trailing Stops

| Feature | Trailing Stop | Hard Stop |
|---------|--------------|-----------|
| Requires arming? | Yes (must reach arm_pct first) | No — always active |
| Moves with price? | Yes — ratchets up | No — fixed from entry |
| Partial exits? | Via take-profit tiers | No — full exit only |
| Can be overridden? | Via delegate mode | Never |

The hard stop catches cases where:
- The trailing stop hasn't armed yet (price drops immediately after entry)
- The trailing stop state is corrupted or missing
- The position gaps down past the trailing stop level

### Default Configuration

```yaml
hard_stop:
  enabled: true
  stop_loss_pct: 10.0  # Exit if position drops 10% from entry
```

### Exit Lock Mechanism

Before executing a hard stop exit, ExitBot checks for an **exit lock**. This prevents duplicate orders when multiple exit triggers fire simultaneously:

```python
if self._trailing_stop_mgr.has_exit_lock(bot_id, position_id, symbol, asset_class):
    return False  # Another exit is already pending
```

If no lock exists, ExitBot:
1. Sets exit lock (prevents duplicates)
2. Cancels any staged broker-side orders
3. Executes market sell with spread protection
4. Records exit and cleans up state

## Reversal Sense

Reversal sense detects momentum reversals by monitoring the drop from a position's high-water mark.

### How It Works

For a long position:
```
Drop from HWM = (high_water - current_price) / high_water × 100
If drop > reversal_threshold → EXIT
```

### Visual Example

```
Price
  │
  │    ★ HWM ($115) ← High water mark tracked by trailing stop
  │   / \
  │  /   \
  │ /     \  ← Dropping from HWM
  │/       \
  │     ────── Reversal Sense triggers at $111.85 (2.75% drop from HWM)
  │         \
  │          \ ← Would have dropped further without reversal sense
  │
  │ Entry ($100)
  └────────────────────────── Time
```

### Difference from Trailing Stop

| Feature | Trailing Stop | Reversal Sense |
|---------|--------------|----------------|
| Base | Fixed trail % from HWM | Configurable drop threshold |
| When active | After arming | After arming (separate config) |
| Sensitivity | Based on trail_pct | Based on reversal config |
| Purpose | Standard exit | Detect momentum shift |

Reversal sense is more nuanced — it can use different thresholds and logic compared to the simple trailing percentage.

---

# Module 8: ProfitSniper Integration

## What Is ProfitSniper?

ProfitSniper is a **velocity-based exit intelligence system** that detects when a profitable position's momentum is fading. While trailing stops are purely price-based, ProfitSniper analyzes the *rate of change* to predict reversals before they happen.

## How It Integrates with ExitBot

ProfitSniper operates as an **overlay** on ExitBot's trailing stop system. When enabled, it can:

1. **Override trailing stop parameters**: ProfitSniper provides its own arm/trail percentages based on momentum velocity
2. **Detect pullback reversals**: Identifies when upward momentum is decelerating
3. **Provide tighter exits**: In fast-moving markets, ProfitSniper tightens stops faster than standard trailing

## Key Parameters

| Parameter | Stock Default | Options (Reverted) | Description |
|-----------|--------------|-------------------|-------------|
| `arm_pct` | 1.5% | 3.0% | Profit % to activate ProfitSniper |
| `pullback_trail_pct` | 0.5% | 2.0% | Trailing distance after velocity reversal |
| `velocity_reversal` | 0.3 | 1.0 | Velocity threshold for reversal detection |

### The Options Revert (Feb 13, 2026)

Originally, ProfitSniper was configured with tight options parameters (0.5% arm, 0.15% pullback). This was killing winners on bid-ask noise — options have wide spreads and the tight config was triggering exits on normal bid-ask bouncing, not actual reversals.

**Reverted to sweep-validated settings**: 3% arm, 2% pullback, 1.0 velocity reversal. Stock ProfitSniper stays tight (sweep-validated for equities).

## Velocity Detection

ProfitSniper calculates momentum velocity as:

```
velocity = (current_price - price_N_bars_ago) / (N × time_interval)
```

When velocity drops below the `velocity_reversal` threshold after the position has been armed, ProfitSniper signals a tighter trailing stop. This catches the moment when a move starts losing steam — before the price actually reverses.

---

# Module 9: SessionProtection

## What Is SessionProtection?

SessionProtection is a **daily P&L management system** that prevents giving back a winning day's profits. It implements three key mechanisms:

1. **HWM Giveback Cap**: Limits how much of a day's peak profit you can give back
2. **Profit Locks**: Locks in a minimum floor once profits exceed a threshold
3. **Trailing Tighten**: Progressively tightens all trailing stops as daily P&L grows

## High-Water Mark (HWM) Giveback Cap

The HWM tracks the highest realized P&L for the current trading session. The giveback cap prevents losing more than X% of that peak.

### Example

```
Session P&L Timeline:
  +$2,000 ← HWM reaches $2,000
  +$1,800 ← Gave back $200 (10% of HWM)
  +$1,500 ← Gave back $500 (25% of HWM)
  +$1,200 ← Gave back $800 (40% of HWM) → GIVEBACK CAP HIT
           ← All remaining positions get force-closed
```

With a 40% giveback cap and $2,000 HWM:
- Minimum allowed P&L = $2,000 × (1 - 0.40) = $1,200
- If session P&L drops to $1,200, all positions are force-closed

## Profit Locks

Once daily profits exceed certain thresholds, a **floor** is locked in:

```yaml
profit_locks:
  - threshold: 1000   # When session P&L reaches $1,000
    lock_pct: 0.60     # Lock in 60% ($600 floor)
  - threshold: 2000   # When session P&L reaches $2,000
    lock_pct: 0.70     # Lock in 70% ($1,400 floor)
  - threshold: 5000   # When session P&L reaches $5,000
    lock_pct: 0.80     # Lock in 80% ($4,000 floor)
```

If session P&L ever drops below the locked floor, all positions are closed and new entries are blocked for the rest of the day.

## Trailing Tighten

As daily profits grow, SessionProtection progressively tightens all trailing stops:

```yaml
trailing_tighten:
  enabled: true
  tighten_at_pnl: 1000     # Start tightening at +$1,000 session P&L
  tighten_factor: 0.75      # Multiply all trail_pct by 0.75
  max_tighten: 0.50          # Never tighten below 50% of original
```

A 3% trailing stop at +$2,000 session P&L might tighten to 2.25% (3% × 0.75).

## Integration with ExitBot

SessionProtection hooks into ExitBot at two points:

1. **Before position processing**: Check if daily giveback cap or profit lock floor has been breached → force-close all positions
2. **After each exit**: Record the trade P&L into SessionProtection → update HWM, check locks

```python
try:
    if self._session_protection is not None:
        self._session_protection.record_trade_pnl(pnl, position.symbol, reason)
        sp_status = self._session_protection.get_session_status()
except Exception as sp_err:
    self._logger.error(f"SessionProtection record failed (fail-open): {sp_err}")
```

Note the `fail-open` design: if SessionProtection crashes, ExitBot continues operating. Protection failure should never prevent exits from executing.

---

# Module 10: News-Based Exits

## Overview

ExitBot can trigger exits based on negative news sentiment for held positions. This is an **additional** exit trigger — it does not replace trailing stops or hard stops.

## The 4-Gate System

News exits use a fail-closed gate system. All four gates must pass before an exit triggers:

```
Gate 1: News exits enabled in config?     → No → SKIP
Gate 2: News available for this symbol?    → No → SKIP
Gate 3: Cache fresh (not stale)?           → No → SKIP (fail-closed)
Gate 4: Confidence above threshold?        → No → SKIP (fail-closed)
        ↓ All gates passed
Gate 5: Sentiment below threshold?         → Check severity level
```

### Gate Details

| Gate | Parameter | Default | Purpose |
|------|-----------|---------|---------|
| 1 | `news.enabled` | false | Master switch |
| 2 | News availability | — | Can't trade on no data |
| 3 | Cache freshness | TTL-based | Stale data is dangerous |
| 4 | `min_confidence` | 0.60 | Low-confidence sentiment is noise |

### Sentiment Thresholds

| Level | Threshold | Action |
|-------|-----------|--------|
| Negative | ≤ -0.70 | Exit if position is profitable |
| Severe | ≤ -0.85 | Exit even if position is losing |

### Logic Flow

```
if sentiment ≤ severe_threshold (-0.85):
    → EXIT regardless of P&L (something very bad happened)

elif sentiment ≤ negative_threshold (-0.70):
    if position is profitable:
        → EXIT (lock in gains before news impact)
    elif loss_exit_on_severe allowed:
        → EXIT (cut losses on negative news)
    else:
        → HOLD (don't realize loss on moderate negative)
```

### Context-Aware Staleness

Cache freshness uses context-aware TTLs:
- **During market hours**: Tighter TTL (news must be very fresh)
- **Pre-market/after-hours**: Looser TTL (news updates less frequently)
- **Weekends/holidays**: Widest TTL

This prevents exits on stale news that may have already been priced in.

---

# Module 11: Pre-Staged Exit Orders

## The Crash Protection Problem

What happens if ExitBot's process dies? Or the server reboots? Or the network drops? Without broker-side protection, positions would be completely unprotected until ExitBot restarts.

## The Solution: Broker-Side OCO Orders

When ExitBot detects a new position, it immediately places **OCO (One-Cancels-Other)** exit orders on Alpaca:

```
Position: 100 shares AAPL at $200
├── Stop-Loss Order: Sell 100 @ $180 (10% below entry)
└── Take-Profit Order: Sell 100 @ $200.20 (0.1% above entry)

These orders live on Alpaca's servers, NOT on our system.
If our system dies, Alpaca still enforces these exits.
```

### Why 0.1% Take-Profit?

The staged TP is intentionally tiny (0.1%) because ExitBot manages the real take-profit logic. The staged TP serves as a **liquidate-winners** mode — ensuring even small gains get captured if the system is down.

### The Staged Order Lifecycle

```
1. New position detected
   ├── Place OCO on Alpaca (stop + TP)
   └── Track in _staged_orders dict

2. ExitBot running normally
   ├── Update staged stop as trailing stop moves up
   └── Staged TP stays at 0.1% (ExitBot handles real TP)

3. ExitBot decides to exit (trailing stop, TP tier, etc.)
   ├── Cancel staged orders first
   └── Then execute the exit

4. System crash (ExitBot down)
   ├── Alpaca's OCO orders still active
   └── Stop-loss protects against large losses
   └── TP captures any small gains
```

### Limitations

Not all positions can have staged orders:

| Condition | Can Stage? | Order Type | Fallback |
|-----------|-----------|------------|----------|
| Whole-share equity (us_equity) | Yes | OCO (stop + TP) | Standalone stop if OCO fails |
| Fractional shares (us_equity) | No | in_memory_only | ExitBot monitors in-memory; hard stops still active |
| Options (option/us_option) | No | in_memory_only | ExitBot monitors in-memory; hard stops still active |
| Crypto | Depends | OCO or standalone | May need standalone stop; in-memory if rejected |

**Why no options staging?** Alpaca doesn't support OCO or standalone stop orders for options on accounts not eligible for uncovered options trading. The code detects options by matching the OCC symbol format (e.g., `AAPL250117C00200000`). Options are protected by ExitBot's in-memory monitoring and hard stops instead.

**Why no fractional staging?** Alpaca rejects OCO/stop orders for fractional quantities (detected with float tolerance `abs(qty - round(qty)) > 1e-6`). Only whole-share positions can use broker-side staging. Fractional positions rely on ExitBot's in-memory trailing stops and hard stops.

### OCO Fallback

If OCO placement fails, ExitBot falls back to a standalone stop-loss order:

```
OCO attempt → Failed (Alpaca rejected)
  → Fallback: Place standalone stop order
    → Success: Track as "standalone_stop" type
    → Fail: Log error, rely on in-memory monitoring
```

### Staged Stop Updates

As the trailing stop ratchets up, ExitBot updates the broker-side stop to match:

```python
def update_staged_stop(position_id, new_stop_price):
    # Only update if price changed meaningfully (>$0.01)
    if abs(new_stop_price - old_stop) < 0.01:
        return  # No change needed

    # Replace the order on Alpaca with new stop price
    result = alpaca.replace_order(order_id, stop_price=new_stop_price)
```

### Reconciliation on Startup

When ExitBot starts (or restarts after a crash), it reconciles staged orders:

```python
def reconcile_staged_orders():
    for position_id, staged in staged_orders.items():
        order = alpaca.get_order(staged['stop_order_id'])
        if order.status in ('filled', 'cancelled', 'expired'):
            # Stale — position was exited while we were down
            clean_up(position_id)
        else:
            # Still active — keep tracking
            pass
```

### Wash-Trade Prevention

When another bot needs to sell a symbol that ExitBot has staged orders for, the staged orders must be cancelled first. Otherwise, Alpaca rejects the new sell order (error 40310000: opposite-side orders exist).

```python
def cancel_staged_orders_for_symbol(symbol, reason="wash_trade_prevention"):
    # Find all staged orders matching this symbol
    # Cancel them on Alpaca
    # Remove from tracking
```

This is called by other bots before they submit sell orders for symbols that ExitBot is monitoring.

---

# Module 12: Spread Protection & Order Execution

## The Spread Problem

Options and some stocks have wide bid-ask spreads. A market order fills at the **worst available price**, which can be significantly different from the displayed price.

**Example**: Option with $2.00 bid / $2.50 ask
- Market sell order fills at $2.00 (the bid)
- You lose $0.50 per contract versus the mid-price ($2.25)
- On 10 contracts, that's $500 lost to spread

## Spread Protection

ExitBot's `_close_with_spread_protection()` handles this by:

1. **Checking the current spread** before placing orders
2. **Using limit orders** instead of market orders when spreads are wide
3. **Setting the limit at the mid-point** between bid and ask
4. **Falling back to market orders** if the limit order doesn't fill within a timeout

### How It Works

```
Step 1: Get current quote (bid/ask)
Step 2: Calculate spread percentage
         spread_pct = (ask - bid) / mid_price × 100

Step 3: If spread_pct > threshold (e.g., 2%):
         → Use limit order at mid-price
         → Wait for fill (with timeout)
         → If not filled, cancel and use market order

Step 4: If spread_pct ≤ threshold:
         → Use market order directly (spread is tight enough)
```

## Order Execution Details

### Sell Orders

All sell exits use the same pattern:
```python
exit_side = "sell" if position.side == "long" else "buy"
result = self._alpaca.place_market_order(
    symbol=position.symbol,
    side=exit_side,
    qty=abs(position.qty)
)
```

### Options-Specific Rules

Options orders have special constraints (discovered via production bugs):
- **TIF must be DAY**: Alpaca rejects GTC (Good-Til-Cancelled) for options
- **No notional orders**: Must specify qty, not dollar amount
- **No fractional contracts**: Round to whole numbers

### Equity-Specific Rules

Equities also have constraints:
- **Sell orders use qty, not notional**: Alpaca rejects notional amounts for sells
- **Fractional shares round to whole for GTC**: If TIF is GTC, qty must be whole shares
- **Minimum 1 share**: Even if calculated qty is 0.3 shares, round up to 1

These constraints were discovered through production bugs (Feb 2026) that caused 186+ errors before being fixed.

---

# Module 13: Telemetry, Forensics & Kill-Switches

## Telemetry Data Types

ExitBot records detailed telemetry for every exit decision, enabling post-trade forensics and strategy optimization. Three core data structures capture the full decision lifecycle:

### EntryIntent

When ExitBot registers a new position, it creates an `EntryIntent` record:

| Field | Type | Description |
|-------|------|-------------|
| `symbol` | str | Ticker symbol |
| `side` | str | "long" or "short" |
| `qty` | float | Position quantity |
| `entry_price` | float | Entry fill price |
| `bot_id` | str | Which bot opened the position |
| `asset_class` | str | "us_equity", "crypto", or "option" |
| `options` | dict | Strike, expiry, right (for options) |
| `strategy_id` | str | Strategy system ID (if tagged) |
| `client_order_id` | str | Alpaca client order ID for linking |

EntryIntents are persisted to state and survive restarts. They link entry orders to exit decisions for full trade lifecycle tracking.

### PositionSnapshot

During each monitoring loop, ExitBot captures a `PositionSnapshot` for active positions:

| Field | Type | Description |
|-------|------|-------------|
| `symbol` | str | Ticker symbol |
| `current_price` | float | Current market price |
| `unrealized_pnl` | float | Current unrealized P&L |
| `trailing_stop_state` | dict | Current trailing stop level, HWM, armed status |
| `tp_tiers_hit` | set | Which TP tiers have been triggered |
| `time_in_position` | float | Seconds since entry |

Snapshots feed into the V2 Intelligence engine (TradeHealthScorer, ExitDecisionEngine) for multi-factor exit analysis.

### ExitDecisionRecord

Every exit decision (whether executed or not) is logged as an `ExitDecisionRecord`:

| Field | Type | Description |
|-------|------|-------------|
| `symbol` | str | Ticker symbol |
| `decision` | str | "HOLD", "TIGHTEN", "SCALE", or "FULL_EXIT" |
| `reason` | str | Why this decision was made |
| `confidence` | float | Decision confidence (0-1) |
| `factors` | dict | All factors that contributed to the decision |
| `timestamp` | str | When the decision was made |

These records enable:
- **Post-trade analysis**: Why did ExitBot exit at that moment?
- **Strategy optimization**: Which exit reasons produce the best outcomes?
- **Debugging**: What was ExitBot "thinking" when it made a bad exit?

## Strategy Kill-Switch

ExitBot integrates with the `StrategyKillSwitch` system for per-strategy drawdown tracking. After each exit, it records the P&L against the originating strategy:

```python
strategy_id = self._resolve_strategy_id(position)
if strategy_id:
    self._strategy_kill_switch.record_exit(strategy_id, pnl, strategy_cfg)
```

### Strategy ID Resolution

ExitBot resolves the strategy ID through three lookup methods (in priority order):

1. **Position state key**: `position.{position_id}.strategy_id`
2. **Symbol state key**: `position.{symbol}.strategy_id` (for options by contract)
3. **Bot ID prefix**: If `bot_id` starts with `strategy:`, extract the strategy name

If no strategy ID is found (manual trades or bots not using the strategy system), kill-switch tracking is skipped silently.

### Kill-Switch Behavior

When a strategy's cumulative losses exceed its configured drawdown limit, the kill-switch disables that strategy from opening new positions. ExitBot continues monitoring and exiting existing positions — the kill-switch only blocks new entries.

## V2 Intelligence Components

The V2 Elite upgrade adds three intelligence layers that can be enabled/disabled independently:

| Component | Purpose | Output |
|-----------|---------|--------|
| `TradeMemoryEngine` | Remembers historical trade patterns for each symbol | Pattern-based exit timing |
| `TradeHealthScorer` | Scores position health based on multiple factors | Health score 0-100 |
| `ExitDecisionEngine` | Multi-factor exit decisions combining all signals | HOLD / TIGHTEN / SCALE / FULL_EXIT |

These components gate exits with their own logic but are **advisory** — hard stops, trailing stops, and SessionProtection always take precedence. If V2 Intelligence is disabled or errors, ExitBot falls back to standard exit logic (fail-open).

---

# Appendix A: Configuration Reference

## Full ExitBot Configuration (bots.yaml)

```yaml
exitbot:
  enabled: true
  loop_interval_seconds: 8       # How often to check positions
  delegate_exits_to_exitbot: true # Whether entry bots delegate exits

  # Trailing Stop Configuration
  trailing_stop:
    enabled: true
    arm_pct: 3.0                  # Profit % to activate trailing stop
    trail_pct: 3.0                # Trail distance percentage
    regime_tightening:
      bear: 0.7                   # 30% tighter in bear markets
      neutral: 1.0
      bull: 1.2                   # 20% wider in bull markets

  # Hard Stop Configuration
  hard_stop:
    enabled: true
    stop_loss_pct: 10.0           # Absolute loss limit

  # Reversal Sense Configuration
  reversal_sense:
    enabled: true
    drop_threshold_pct: 2.75      # HWM drop to trigger exit

  # Take-Profit Configuration
  take_profit:
    enabled: true
    tp1_pct: 2.0                  # First target
    tp1_exit_pct: 0.33            # Exit 33% at TP1
    tp2_pct: 4.0                  # Second target
    tp2_exit_pct: 0.50            # Exit 50% of remaining at TP2
    tp3_pct: 8.0                  # Third target
    tp3_exit_pct: 1.0             # Exit 100% remaining at TP3
    move_stop_after_tp1: breakeven
    move_stop_after_tp2: tp1

    # Parabolic Runner Mode
    parabolic_runner:
      enabled: true
      widen_trailing_pct: 50.0    # Widen trail by 50% after TP2

      # ATR-Based Adaptive Thresholds
      adaptive:
        enabled: true
        atr_period: 14
        base_atr_mult_tp1: 0.5
        base_atr_mult_tp2: 1.0
        base_atr_mult_tp3: 2.0
        min_tp1_pct: 1.5
        max_tp1_pct: 4.0
        min_tp2_pct: 3.0
        max_tp2_pct: 8.0
        min_tp3_pct: 6.0
        max_tp3_pct: 15.0

  # ProfitSniper Configuration
  profit_sniper:
    enabled: true
    arm_pct: 1.5                  # For stocks
    pullback_trail_pct: 0.5       # For stocks
    velocity_reversal: 0.3        # For stocks
    options:                      # Separate options config
      arm_pct: 3.0                # REVERTED from 0.5%
      pullback_trail_pct: 2.0     # REVERTED from 0.15%
      velocity_reversal: 1.0      # REVERTED (wider for options)

  # SessionProtection Configuration
  session_protection:
    enabled: true
    hwm_giveback_cap_pct: 40.0    # Max % of HWM to give back
    profit_locks:
      - threshold: 1000
        lock_pct: 0.60
      - threshold: 2000
        lock_pct: 0.70
      - threshold: 5000
        lock_pct: 0.80
    trailing_tighten:
      enabled: true
      tighten_at_pnl: 1000
      tighten_factor: 0.75
      max_tighten: 0.50

  # News Exit Configuration
  intelligence:
    news:
      enabled: false              # Disabled by default
      exits:
        enabled: false
        min_confidence: 0.60
        negative_threshold: -0.70
        severe_threshold: -0.85
        profit_exit_requires_profit: true
        loss_exit_on_severe: true

  # Staged Orders Configuration
  staged_orders:
    enabled: true
    stop_pct: 0.10                # 10% stop-loss on broker side
    tp_pct: 0.001                 # 0.1% TP (liquidate-winners mode)

  # Spread Protection
  spread_protection:
    enabled: true
    max_spread_pct: 2.0           # Use limit orders above 2% spread
    limit_timeout_sec: 10         # Wait 10s for limit fill
```

## Per-Bot Overrides

Individual bots can override ExitBot's defaults:

```yaml
# CryptoBot: Higher TP targets due to volatility
cryptobot:
  parabolic_runner:
    enabled: true
    tp1_pct: 4.0
    tp2_pct: 8.0
    tp3_pct: 15.0
    widen_trailing_pct: 70.0      # More room for crypto runners

# TwentyMinuteBot: No ExitBot trailing stops
twentyminutebot:
  delegate_exits_to_exitbot: false  # ExitBot won't override
  # TwentyMin manages its own exits (gap-and-go, no stops)

# OptionsBot: Wider targets due to leverage
optionsbot:
  exits:
    take_profit_pct: 50.0         # Base TP (TP1=20%, TP2=35%, TP3=50%)
```

---

# Appendix B: Exit Decision Priority

## Authority Hierarchy

When multiple exit conditions trigger simultaneously, ExitBot uses this priority order:

```
Priority 1 (HIGHEST): SessionProtection force-close
    → Daily P&L limit breached → close ALL positions
    
Priority 2: Hard stop-loss
    → Position lost > hard_stop_pct from entry → full exit

Priority 3: News sentiment exit (severe)
    → Severe negative sentiment → exit regardless of P&L

Priority 4: ProfitSniper velocity exit
    → Momentum velocity reversal detected → exit

Priority 5: Trailing stop triggered
    → Price dropped below trailing stop level → full exit

Priority 6: Take-profit tier hit
    → TP1/TP2/TP3 targets reached → partial/full exit

Priority 7: Reversal sense
    → Momentum reversal from HWM → exit

Priority 8: News sentiment exit (moderate)
    → Moderate negative + profitable → exit to lock gains

Priority 9 (LOWEST): Time-based exit
    → Position held too long → exit
```

## Delegate Mode Exceptions

When `delegate_exits_to_exitbot: false` is set for a bot, ExitBot skips most exit logic for that bot's positions. However, these safety mechanisms **always apply regardless of delegate mode**:

| Feature | Applies in Delegate=false? | Reason |
|---------|---------------------------|--------|
| Hard stop-loss | Yes | Absolute safety net |
| SessionProtection | Yes | Daily P&L limits |
| Position monitoring/logging | Yes | Dashboard visibility |
| Staged order crash protection | Yes | Broker-side safety |
| Trailing stops | No | Bot manages own exits |
| Take-profit tiers | No | Bot manages own exits |
| Reversal sense | No | Bot manages own exits |
| News exits | No | Bot manages own exits |
| ProfitSniper | No | Bot manages own exits |

## Decision Flow Per Position

```
┌─────────────────────────────┐
│ Fetch position from Alpaca  │
└──────────┬──────────────────┘
           │
           ▼
┌──────────────────────────────┐
│ Is delegate_exits = false?   │──Yes──→ Skip trailing/TP/reversal
└──────────┬───────────────────┘         (hard stop + session STILL APPLY)
           │ No
           ▼
┌──────────────────────────────┐
│ SessionProtection breach?    │──Yes──→ Force-close ALL positions
└──────────┬───────────────────┘
           │ No
           ▼
┌──────────────────────────────┐
│ Hard stop triggered?         │──Yes──→ Full exit + cleanup
│ (pnl_pct ≤ -stop_loss_pct) │
└──────────┬───────────────────┘
           │ No
           ▼
┌──────────────────────────────┐
│ News exit warranted?         │──Yes──→ Full exit + cleanup
│ (severe negative sentiment)  │
└──────────┬───────────────────┘
           │ No
           ▼
┌──────────────────────────────┐
│ ProfitSniper signal?         │──Yes──→ Tighten trailing stop
└──────────┬───────────────────┘         (may trigger immediate exit)
           │ No
           ▼
┌──────────────────────────────┐
│ Update trailing stop         │
│ (ratchet HWM, check trigger) │──Triggered──→ Full exit + cleanup
└──────────┬───────────────────┘
           │ Not triggered
           ▼
┌──────────────────────────────┐
│ Check take-profit tiers      │
│ TP1 → partial exit (33%)     │
│ TP2 → partial exit (50%)     │
│ TP3 → full exit (or runner)  │──Exit──→ Record + adjust stops
└──────────┬───────────────────┘
           │ No tier hit
           ▼
┌──────────────────────────────┐
│ Check reversal sense         │──Yes──→ Full exit + cleanup
└──────────┬───────────────────┘
           │ No
           ▼
┌──────────────────────────────┐
│ Update staged order stops    │
│ (sync broker-side with       │
│  trailing stop level)        │
└──────────────────────────────┘
```

---

# Appendix C: Troubleshooting

## Common Issues

### Issue: "Trailing stop never arms"
**Cause**: Position never reaches `arm_pct` profit before reversing.
**Fix**: Lower `arm_pct` or ensure entries are at good levels. Check if ProfitSniper is overriding with a different `arm_pct`.

### Issue: "Premature exits on options"
**Cause**: ProfitSniper or trailing stops too tight for options' wide bid-ask spreads.
**Fix**: Use the reverted options config (3% arm, 2% pullback, 1.0 velocity). Check `profit_sniper.options` config section. This was a major production issue in Feb 2026.

### Issue: "Duplicate exit orders"
**Cause**: Multiple exit triggers firing on the same position in the same loop.
**Fix**: ExitBot uses exit locks (`has_exit_lock()`) to prevent this. If you see duplicate orders, check if exit locks are being properly set/cleared.

### Issue: "Staged orders rejected by Alpaca"
**Cause**: Fractional shares, options, or GTC TIF for options.
**Fix**: ExitBot automatically handles this — fractional and options positions get `in_memory_only` staging. Check logs for `staged_orders_skipped_*` events.

### Issue: "Position not monitored by ExitBot"
**Cause**: `delegate_exits_to_exitbot: false` for that bot, or position appeared between monitoring loops.
**Fix**: Check the bot's config. ExitBot still monitors these positions for hard stops and SessionProtection, just not trailing stops/TP.

### Issue: "Order error 40310000 (wash trade)"
**Cause**: Another bot trying to sell a symbol that has ExitBot staged orders.
**Fix**: Call `cancel_staged_orders_for_symbol(symbol)` before placing the sell order. MomentumBot and other entry bots should call this automatically.

### Issue: "ExitBot crashed, positions unprotected"
**Recovery**: Pre-staged broker-side OCO orders should still be active on Alpaca. When ExitBot restarts, `reconcile_staged_orders()` syncs state. Check Alpaca dashboard for active orders.

### Issue: "Position monitoring error for one position kills all monitoring"
**Fixed**: Feb 2026 bug fix — each position is now wrapped in its own `try/except`. One bad position can't crash monitoring for others.

### Issue: "Notional sell order rejected"
**Fixed**: Feb 2026 bug fix — `place_market_order` now converts notional→qty for sells (min 1 share) and rounds fractional qty to whole shares for GTC TIF.

## Key Log Events to Monitor

| Event | Meaning | Action |
|-------|---------|--------|
| `trailing_stop_exit_triggered` | Position hit trailing stop | Review — was it premature? |
| `hard_stop_triggered` | Position hit absolute loss limit | Review entry quality |
| `take_profit_tier_hit` | TP1/TP2/TP3 reached | Working as designed |
| `parabolic_runner_active` | Runner mode skipping TP3 | Monitor — big move potential |
| `news_exit_triggered` | Negative sentiment exit | Check news quality |
| `staged_stop_updated` | Broker-side stop ratcheted up | Trailing stop working |
| `session_protection_exit_recorded` | Session P&L tracked | Monitor giveback levels |
| `runner_trailing_stop_widened` | Runner mode widened trail | Big move in progress |
| `adaptive_thresholds_applied` | ATR adjusted TP levels | Volatility adaptation |
| `TRADE_EXIT` | Any exit completed | Master exit log |

---

# Appendix D: Quick Reference Cheat Sheet

## ExitBot at a Glance

```
┌─────────────────────────────────────────────────────┐
│                  EXITBOT v2 ELITE                    │
│           Institutional Exit Intelligence            │
├─────────────────────────────────────────────────────┤
│                                                      │
│  TRAILING STOP         TAKE-PROFIT TIERS             │
│  ├─ Arm: 3%            ├─ TP1: 2% → sell 33%        │
│  ├─ Trail: 3%          ├─ TP2: 4% → sell 50%        │
│  └─ Regime-adjusted    └─ TP3: 8% → sell 100%       │
│                            (or parabolic runner)     │
│                                                      │
│  HARD STOP             REVERSAL SENSE                │
│  └─ -10% from entry    └─ 2.75% drop from HWM       │
│                                                      │
│  PROFITSNIPER          SESSION PROTECTION             │
│  ├─ Stocks: 1.5%/0.5%  ├─ HWM giveback: 40%         │
│  └─ Options: 3%/2%     ├─ Profit locks: $1K/$2K/$5K │
│                         └─ Trail tighten: 0.75×      │
│                                                      │
│  STAGED ORDERS         NEWS EXITS                    │
│  ├─ OCO on Alpaca      ├─ Negative: ≤ -0.70         │
│  ├─ 10% stop           ├─ Severe: ≤ -0.85           │
│  └─ 0.1% TP (liq.)    └─ Confidence: ≥ 0.60        │
│                                                      │
│  LOOP: Every 8 seconds during market hours           │
│  THREAD: Independent from entry bots                 │
│  STATE: Persisted to SQLite (crash-resilient)        │
│  SPREAD: Limit orders when spread > 2%               │
│                                                      │
├─────────────────────────────────────────────────────┤
│  EXIT PRIORITY (highest to lowest):                  │
│  1. SessionProtection  5. Trailing Stop              │
│  2. Hard Stop          6. Take-Profit Tier           │
│  3. News (severe)      7. Reversal Sense             │
│  4. ProfitSniper       8. News (moderate)            │
└─────────────────────────────────────────────────────┘
```

## Key Formulas

```
Trailing Stop (long): stop = HWM × (1 - trail_pct/100)
Trailing Stop (short): stop = LWM × (1 + trail_pct/100)

Adaptive TP: new_TP = base_TP + (ATR% × multiplier)
             clamped to [min_bound, max_bound]

Runner Widen: new_distance = current_distance × (1 + widen_pct/100)

Hard Stop: EXIT when pnl_pct ≤ -stop_loss_pct

Regime Tighten: effective_trail = trail_pct × regime_factor
                bear=0.7, neutral=1.0, bull=1.2

Session Giveback: floor = HWM × (1 - giveback_cap_pct/100)
                  EXIT ALL when session_pnl < floor
```

## Critical Production Lessons

1. **Options need wide ProfitSniper config** — bid-ask noise kills tight configs
2. **Sell orders must use qty, not notional** — Alpaca rejects notional for sells
3. **Options require DAY TIF** — Alpaca rejects GTC for options
4. **Fractional shares need DAY TIF** — or round to whole shares for GTC
5. **Per-position try/except is mandatory** — one bad position can't kill all monitoring
6. **Cancel staged orders before manual exits** — prevent wash-trade rejections
7. **Exit locks prevent duplicate orders** — always check `has_exit_lock()` first
8. **Fail-open for non-critical systems** — SessionProtection crash shouldn't prevent exits

---

*ExitBot v2 Elite — Trading Hydra*
*"The best trade is the one you exit correctly."*
