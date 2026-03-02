## Bot Readiness Brief ... Trading_Hydra (Operator Copy)

### Mission
Run a **fail-closed**, stateful multi-asset trading system (stocks, options, crypto) that continuously scans, takes only high-confidence setups, and halts on any safety violation... with **ExitBot** acting as the primary risk governor.

---

## 1) What it trades

### Asset classes
- **Stocks** (RTH session rules apply)
- **Options** (derived from underlying signals... extra liquidity/spread risk)
- **Crypto** (24/7 market... higher noise, higher slippage)

### Strategy families inside the system
- **Trend / Breakout (Turtle-like)** ... Donchian breakouts + ATR sizing + trailing exits + optional pyramiding
- **Open/early-session momentum** ... "TwentyMinute" style opening behavior
- **Risk-first management** ... ExitBot governs stops, trailing stops, daily loss cap, staleness, and global halts

Operator reality check... this is *multiple* strategies. It can work... but you must enforce budget and regime gates so they don't fight each other.

---

## 2) When it trades

### Stocks
- **Trade window:** Regular Trading Hours (RTH) only
- **Day mode:** should flatten by close unless explicitly configured otherwise
- **Avoid first minutes:** configured to avoid the immediate open volatility spike

### Options
- **Trade window:** RTH only
- **Day mode:** should avoid late-day liquidity cliffs unless the bot is specifically designed for it (0DTE behavior)

### Crypto
- **Trade window:** always on
- **Day mode:** define your "day session" operationally (example: 6:00 AM–6:00 PM PT) or you'll end up trading every micro-move overnight

---

## 3) Risk rules (what the system MUST obey)

### Per-trade risk budget
- **Day:** ~0.25% equity per trade (recommended default)
- **Swing:** ~0.50% equity per trade
- **Long:** ~1.0% equity per trade

### Portfolio risk budget
- **Day:** cap total open risk ~1.0% equity
- **Swing:** cap total open risk ~3.0%
- **Long:** cap total open risk ~5.0%

### Hard safety constraints
- **Daily max loss kill-switch:** if hit... halt trading immediately
- **API / broker issues:** if data is stale or broker is unhealthy... halt trading immediately
- **Stale price guard:** do not trade if quote age exceeds threshold
- **No silent degradation:** on error, default is "stop trading"

Operator note... these are the right principles. The only question is whether execution enforces them consistently at order time.

---

## 4) Entry logic (high-level)

### Breakout entries (Turtle mode)
- Long: price breaks above Donchian high (N bars)
- Short: price breaks below Donchian low (N bars)
- Optional trend filter: only take longs above trend MA, shorts below (recommended for day)

### Momentum entries (TwentyMinute mode)
- Early session confirmation logic around opening behavior (gap/impulse/confirmation bars)
- Requires strict throttles to prevent churn

---

## 5) Exit logic

### Primary exit systems
- **Trailing exit:** Donchian exit channel (shorter lookback than entry)
- **Stop loss:** ATR-based initial stop (2N-ish behavior)
- **Time stop:** exit if no progress after X bars/days/weeks (reduces capital stagnation)
- **Global halts:** ExitBot can flatten and halt the system

### Options-specific exits
- Use underlying-based stop logic plus premium-based hard stops
- Partial take profits are allowed if configured (recommended for options only)

---

## 6) Conditions required before the bot is allowed to place an order
This is the "greenlight checklist." If any item fails... the bot should not trade.

### A) Data integrity
- Fresh quotes within staleness threshold
- Valid ATR calculation window available
- No missing bars / broken candle feed
- No "cached quote is considered fresh" loophole

### B) Broker and account health
- API authenticated and stable
- Account buying power and constraints validated
- No pending critical errors in last loop(s)

### C) Liquidity constraints (must be enforced at order time)
Stocks:
- Spread below threshold
- Volume/ADV high enough for size

Options:
- Open interest above threshold
- Volume above threshold
- Spread % below threshold
- Avoid contracts with thin markets

Crypto:
- Spread below threshold
- Slippage estimate acceptable
- Exchange status stable

### D) Risk constraints
- Per-trade risk within budget
- Total open risk within budget
- Correlation / concentration limits not breached
- Trades/day not exceeded

---

## 7) System kill-switch behavior (how it should behave under threat)

### Triggers that should halt trading
- Daily max loss reached
- API failure / broker failure
- Stale market data
- Anomaly halt from ExitBot
- Unexpected exceptions in critical modules (execution, risk, market data)

### What happens on halt
- New entries stop immediately
- Existing positions are either:
  - Managed by ExitBot only, or
  - Flattened immediately (depending on severity)
- Global halt flag remains set until manual clearing (recommended)

---

## 8) Operational red flags (what you watch like a hawk)
These are "stop and investigate" alarms.

- OptionsBot max trades/day set too high (currently dangerously permissive)
- Quote cache TTL longer than max staleness guard (inconsistent safety)
- Rapid loop interval with slow data refresh (churn risk)
- Any hardcoded limits in code overriding config
- Repeated "almost traded" logs without fresh data (decision thrashing)
- Spread/liquidity gates not applied at execution time

---

## 9) Go/No-Go checklist (before going live)

### No-Go if any item is true
- You cannot reproduce results with a basic replay/backtest harness
- You cannot run tests cleanly on a fresh environment
- Options execution does not enforce spread limits at order time
- Quote freshness logic is inconsistent
- Max trades/day for options remains high
- No clear merged-config printout at startup

### Go when all are true
- Tests pass cleanly
- Locked dependencies (or pinned)
- Decision Records exist per symbol per loop
- Safety gates enforced at execution time
- Small-account mode works as expected
- Paper trading shows stable behavior across at least 2–4 weeks

---

## 10) Recommended immediate config corrections (minimum viable safety)
- Options max trades/day: **10**
- Quote TTL: **≤ max staleness** (example: 10–15 seconds)
- Add execution-time spread gate for all assets
- Add trade cooldowns after entry/exit
- Add a merged-config dump at startup ("this is the truth config")
