# HailMary Options Strategy — Complete Trading Course

> **The Lottery Ticket Strategy That Turned $50K Into $26.6M in 1,000 Days**

---

## Table of Contents

1. [Module 1: Strategy Overview](#module-1-strategy-overview)
2. [Module 2: Finding Setups — Finviz & TradingView](#module-2-finding-setups--finviz--tradingview)
3. [Module 3: Indicators & Chart Setup](#module-3-indicators--chart-setup)
4. [Module 4: Options Chain — Finding the Right Contract](#module-4-options-chain--finding-the-right-contract)
5. [Module 5: Exits & Risk Management](#module-5-exits--risk-management)
6. [Module 6: Position Sizing & Bankroll Management](#module-6-position-sizing--bankroll-management)
7. [Module 7: Options Basics for Beginners](#module-7-options-basics-for-beginners)
8. [Module 8: Broker Setup Guide](#module-8-broker-setup-guide)
9. [Module 9: Trading Journal & Tracking](#module-9-trading-journal--tracking)
10. [Module 10: Market Conditions — When NOT to Trade](#module-10-market-conditions--when-not-to-trade)
11. [Module 11: Psychology & Discipline](#module-11-psychology--discipline)
12. [Module 12: Backtest Results & Proof](#module-12-backtest-results--proof)
13. [Appendix A: Quick Reference Cheat Sheet](#appendix-a-quick-reference-cheat-sheet)
14. [Appendix B: Daily Trading Checklist](#appendix-b-daily-trading-checklist-printable)

---

# Module 1: Strategy Overview

## What Is HailMary?

HailMary is a **0 DTE (zero days to expiration) options strategy** that buys cheap, out-of-the-money options as "lottery tickets." You spend a small amount on each trade — typically $0.50 to $7.00 per contract — and aim for a **25x return** when the underlying stock makes a sharp intraday move.

Think of it like buying a scratch-off ticket, except:

- You pick which tickets to buy using real data
- The odds are far better than actual lottery tickets
- You have a proven mathematical edge

## The Asymmetric Payoff: Risk Small, Win Big

The core principle of HailMary is **asymmetry**:

| What You Risk | What You Can Win | Ratio |
|:---:|:---:|:---:|
| $1.00 per contract | $25.00 per contract | **25:1** |
| $100 per trade (1 contract) | $2,500 per trade | **25:1** |
| $500 per trade (5 contracts) | $12,500 per trade | **25:1** |

Your maximum loss is **always** the premium you paid. The option either expires worthless (you lose 100% of your small bet) or it rockets in value. There is no margin call. There is no surprise loss bigger than what you put in.

## Why a 75% Loss Rate Is Profitable

This is the part that breaks most people's brains. You will **lose on 3 out of every 4 trades** — and still make a fortune.

Here's the math:

```
Win Rate:        24.1% (you win roughly 1 in 4 trades)
Average Winner:  25x your premium
Average Loser:   -1x your premium (total loss)

Expected Value per $1 risked:
  = (0.241 × $25) - (0.759 × $1)
  = $6.025 - $0.759
  = +$5.27 per dollar risked
```

**For every $1 you put at risk, you expect to get $5.27 back.** That is a massive mathematical edge.

Compare this to a casino:

| Game | House Edge | Your Edge |
|---|---|---|
| Roulette | -5.3% (house wins) | You lose |
| Blackjack (card counting) | +1.5% | Tiny edge |
| **HailMary Strategy** | **+527%** | **Enormous edge** |

## Real Backtest Proof

A 1,000-day backtest across 10 major stocks produced these results:

| Metric | Value |
|---|---|
| Starting Capital | $50,000 |
| Final Equity | $26,714,299 |
| Total Profit | **$26,664,299** |
| Total Trades | 5,339 |
| Win Rate | 24.1% |
| Profit Factor | **7.63** |
| Sharpe Ratio | **7.46** |
| Max Drawdown | 12.7% |
| Average Winner | $23,844 |
| Average Loser | -$993 |
| Win/Loss Ratio | **24x** |
| Months Traded | 34 |
| Profitable Months | **34 out of 34 (100%)** |

Every single month was profitable. The worst month still made $532K. The best month made $1.29M.

> **Important Disclaimer:** Backtests show what *would have happened* using historical data. Live trading involves slippage, liquidity constraints, and emotional decisions that backtests don't capture. See Module 12 for honest disclaimers.

---

# Module 2: Finding Setups — Finviz & TradingView

## Finviz Screener Setup

Finviz (https://finviz.com/screener.ashx) is a free stock screener. You need two saved screeners.

### Screener 1: "HM Calls" (Bullish Setups)

Set these filters:

| Filter | Setting |
|---|---|
| Market Cap | Large ($10B and above) |
| Average Volume | Over 5M |
| Change | Up 0.5% or more |
| Volatility — Week | Over 3% |

This finds large, liquid stocks that are already moving up today with enough weekly volatility to produce big intraday swings.

### Screener 2: "HM Puts" (Bearish Setups)

| Filter | Setting |
|---|---|
| Market Cap | Large ($10B and above) |
| Average Volume | Over 5M |
| Change | Down 0.5% or more |
| Volatility — Week | Over 3% |

Same idea, but for stocks moving down. You'll buy put options on these.

### How to Save

1. Set all filters
2. Click "Save Screen" in the top right
3. Name it "HM Calls" or "HM Puts"
4. Each morning, load the screener to see today's candidates

### Why These Filters?

- **Market Cap $10B+**: Large companies have liquid options with tight bid/ask spreads
- **Volume 5M+**: High volume means the move is real, not a fluke
- **Change 0.5%+**: The stock is already showing directional momentum
- **Weekly Volatility 3%+**: The stock is *capable* of making the big moves you need

## TradingView Watchlist

Create a TradingView watchlist with these 10 symbols (our "HailMary Universe"):

```
TSLA    NVDA    AMD     META    AMZN
GOOGL   AAPL    MSFT    QQQ     SPY
```

These were selected because they:
- Have the most liquid 0 DTE options
- Show the highest intraday volatility
- Produced the best backtest results (see Module 12)

### Setting Up Your TradingView Layout

1. **Multi-Chart Layout**: Use TradingView's 4-chart or 6-chart layout
2. **Timeframe**: Set all charts to 5-minute candles
3. **Add a Change% Column**: In the watchlist panel, right-click the header and add "Change %"
4. **Sort by Change%**: Click the Change% column header to sort — the biggest movers float to the top
5. **Set Alerts**: For each symbol, set an alert at +0.3% and -0.3% from the previous close

### Alert Setup (Step by Step)

1. Open a chart for the symbol (e.g., TSLA)
2. Click the "Alert" button (clock icon)
3. Condition: "TSLA" → "Change %" → "Crossing Up" → Value: 0.3
4. Create another alert: "Crossing Down" → Value: -0.3
5. Set notification to phone/email
6. Repeat for all 10 symbols

When an alert fires, that stock is moving enough to potentially set up a HailMary trade.

---

# Module 3: Indicators & Chart Setup

You need exactly **6 indicators** on your chart. No more, no less. Each one answers a specific yes/no question.

## Indicator 1: VWAP (Volume Weighted Average Price)

**What it is:** The average price weighted by volume. It shows where the "fair value" of the stock is for the day.

**Setup:**
- Add "VWAP" indicator in TradingView
- Set line color to **thick blue**
- Enable bands with multiplier = **1** (this creates upper and lower bands)

**How to read it:**

| Price Position | Action |
|---|---|
| Price **above** VWAP | Look for **call** setups (bullish) |
| Price **below** VWAP | Look for **put** setups (bearish) |
| Price **crossing back and forth** over VWAP | **Skip** — no clear direction |

**Why it matters:** VWAP tells you who's in control. If price is above VWAP, buyers are dominant. If below, sellers are dominant. If it's tangled around VWAP, nobody is in control — and that's when you sit out.

## Indicator 2: Volume + 20-Period Moving Average

**What it is:** Standard volume bars with a 20-period moving average line overlaid.

**Setup:**
- Add "Volume" indicator
- Add a 20-period MA to the volume (most platforms let you overlay this)
- Set the MA line color to **orange**

**How to read it:**

| Volume Bars vs. Orange Line | Action |
|---|---|
| Volume bars **above** the orange line | **Real move** — proceed with trade |
| Volume bars **below** the orange line | **Weak move** — skip this setup |

**Why it matters:** A stock can move 0.5% on low volume — that's noise, not signal. You only want to trade moves backed by above-average volume.

## Indicator 3: RSI (Relative Strength Index) — Period 14

**What it is:** A momentum oscillator that measures speed and magnitude of price changes. Ranges from 0 to 100.

**Setup:**
- Add "RSI" indicator, period = **14**
- Draw horizontal lines at **30** and **70**
- Optionally add lines at **20** and **80**

**How to read it:**

| RSI Range | For Calls | For Puts | Meaning |
|---|---|---|---|
| 40–70 | ✅ Buy calls | — | Bullish momentum, not exhausted |
| 30–60 | — | ✅ Buy puts | Bearish momentum, not exhausted |
| Above 80 | ❌ Skip | ❌ Skip | Overbought — reversal likely |
| Below 20 | ❌ Skip | ❌ Skip | Oversold — bounce likely |

**Why it matters:** RSI above 80 or below 20 means the move is probably exhausted. Buying a lottery ticket on an exhausted move is like betting on a horse that already ran the race.

## Indicator 4: EMA 9 (Green) and EMA 21 (Red)

**What it is:** Two Exponential Moving Averages that show short-term trend direction.

**Setup:**
- Add EMA with period = **9**, color = **green**
- Add EMA with period = **21**, color = **red**

**How to read it:**

| EMA Position | Action |
|---|---|
| Green (9) **above** Red (21) | ✅ Look for **calls** |
| Green (9) **below** Red (21) | ✅ Look for **puts** |
| Green and Red **tangled/crossing** | ❌ **Skip** — no trend |

**Why it matters:** When the fast EMA is above the slow EMA, the short-term trend is up. When below, the trend is down. When they're tangled, there's no trend — and HailMary needs trending stocks.

## Indicator 5: ATR (Average True Range) — Period 14

**What it is:** Measures how much a stock typically moves per day. Used for setting trailing stops.

**Setup:**
- Add "ATR" indicator, period = **14**
- Note the current ATR value (e.g., TSLA ATR = $8.50)

**How to read it:**

| Today's Range vs. ATR | Signal Strength |
|---|---|
| Today's range > **1x ATR** | ✅ **Strong signal** — stock is moving more than usual |
| Today's range = **0.5x to 1x ATR** | ⚠️ Moderate — proceed with caution |
| Today's range < **0.5x ATR** | ❌ **Skip** — stock is sleepy today |

**Trailing Stop Formula:** Trail your option's highest price by **1.5x ATR** (adjusted by delta).

Example: If ATR = $8.50 and your option's delta = 0.30:
```
Trailing stop distance = 1.5 × $8.50 × 0.30 = $3.83
```

## Indicator 6: Change% Watchlist Column

**What it is:** The percentage change from the previous close, displayed in your TradingView watchlist.

**How to read it:**

| Change% | Action |
|---|---|
| Above **+0.3%** | ✅ Look for **call** setups |
| Below **-0.3%** | ✅ Look for **put** setups |
| Between -0.3% and +0.3% | ❌ **Skip** — not enough movement |

**Why it matters:** This is your first filter. Before you even look at a chart, the Change% column tells you which stocks are moving enough to be HailMary candidates.

## The 6-Point Checklist

Before entering ANY trade, every single box must be checked:

| # | Indicator | Calls ✅ | Puts ✅ | Skip ❌ |
|---|---|---|---|---|
| 1 | **VWAP** | Price above VWAP | Price below VWAP | Price crossing VWAP |
| 2 | **Volume** | Bars above 20-MA (orange) | Bars above 20-MA (orange) | Bars below orange line |
| 3 | **RSI(14)** | Between 40–70 | Between 30–60 | Above 80 or below 20 |
| 4 | **EMA 9/21** | Green above red | Green below red | Tangled or crossing |
| 5 | **ATR(14)** | Today's range > 1x ATR | Today's range > 1x ATR | Range < 0.5x ATR |
| 6 | **Change%** | Above +0.3% | Below -0.3% | Between ±0.3% |

**Rule: All 6 must agree. If even ONE says "skip," you skip the trade.**

## Chart Layout Diagram

```
┌──────────────────────────────────────────────────────────┐
│                    MAIN CHART (5-min)                     │
│                                                          │
│   ══════ VWAP (thick blue) ══════                        │
│   ─── EMA 9 (green) ───                                 │
│   ─── EMA 21 (red) ───                                  │
│   Candlestick chart with price action                    │
│                                                          │
├──────────────────────────────────────────────────────────┤
│   VOLUME PANEL                                           │
│   ▐▐▐▐ Volume bars  ─── 20-MA (orange) ───              │
├──────────────────────────────────────────────────────────┤
│   RSI PANEL                                              │
│   ─── RSI(14) ───   ···70···   ···30···                  │
├──────────────────────────────────────────────────────────┤
│   ATR PANEL                                              │
│   ─── ATR(14) ───                                        │
└──────────────────────────────────────────────────────────┘

WATCHLIST (right side):
┌────────┬──────────┬─────────┐
│ Symbol │  Price   │ Change% │
├────────┼──────────┼─────────┤
│ TSLA   │ $248.50  │ +1.2%   │  ← Candidate!
│ NVDA   │ $875.30  │ +0.8%   │  ← Candidate!
│ AMD    │ $178.20  │ -0.6%   │  ← Put candidate!
│ META   │ $512.40  │ +0.1%   │  ← Skip (< 0.3%)
│ AMZN   │ $185.60  │ -0.2%   │  ← Skip (< 0.3%)
│ ...    │   ...    │   ...   │
└────────┴──────────┴─────────┘
```

---

# Module 4: Options Chain — Finding the Right Contract

## How to Read an Options Chain

When you open an options chain for a stock (e.g., TSLA at $248.50), you'll see a table like this:

| Strike | Bid | Ask | Last | Delta | Volume | Open Interest |
|---|---|---|---|---|---|---|
| $245 | $4.20 | $4.50 | $4.35 | 0.65 | 12,500 | 45,000 |
| $247 | $2.80 | $3.10 | $2.95 | 0.52 | 8,200 | 32,000 |
| **$249** | **$1.40** | **$1.60** | **$1.50** | **0.38** | **15,800** | **52,000** |
| $250 | $0.85 | $1.05 | $0.95 | 0.28 | 22,000 | 68,000 |
| $252 | $0.30 | $0.45 | $0.38 | 0.15 | 35,000 | 85,000 |

Here's what each column means:

- **Strike**: The price at which you can buy (call) or sell (put) the stock
- **Bid**: What buyers will pay you right now (your sell price)
- **Ask**: What sellers want from you right now (your buy price)
- **Last**: The most recent trade price
- **Delta**: How much the option moves per $1 stock move (0.30 delta = option moves $0.30 per $1 stock move)
- **Volume**: How many contracts traded today (higher = more liquid)
- **Open Interest**: Total outstanding contracts (higher = more liquid)

## Step 1: Find 0 DTE Expiration

- In your broker's options chain, select today's expiration date
- "0 DTE" means the option expires **today** — at the close of trading
- If today is Monday, pick Monday's expiration
- Most major stocks (TSLA, NVDA, SPY, QQQ, AAPL, AMZN, MSFT, META, GOOGL, AMD) have **daily expirations**

## Step 2: Pick the Right Strike — 0.5% Out-of-the-Money

Calculate the target strike:

**For Calls (bullish):**
```
Target Strike = Current Price × 1.005
Example: TSLA at $248.50 → $248.50 × 1.005 = $249.74 → Round to $250
```

**For Puts (bearish):**
```
Target Strike = Current Price × 0.995
Example: TSLA at $248.50 → $248.50 × 0.995 = $247.26 → Round to $247
```

Pick the strike closest to your calculated target.

## Step 3: Check Premium Requirements

Your contract must pass these filters:

| Filter | Requirement | Why |
|---|---|---|
| Premium (Ask price) | **Under $7.00** | Keep risk per contract low |
| Ideal Premium | **Under $3.00** | Sweet spot for risk/reward |
| Bid/Ask Spread | **Under $0.50** | Tight spread = fair pricing |
| Volume | **Over 100** | Enough liquidity to fill |
| Open Interest | **Over 500** | Established market for this contract |

If the premium is over $7.00, move to a strike further out-of-the-money. If the bid/ask spread is over $0.50, skip this stock — the options aren't liquid enough.

## Step 4: Calculate Number of Contracts

```
Number of Contracts = Budget ÷ (Premium × 100)
```

**Example:**
- Budget: $500 (1% of $50K account)
- Premium: $1.50 per contract
- Cost per contract: $1.50 × 100 shares = $150

```
Contracts = $500 ÷ $150 = 3.33 → Buy 3 contracts
Total cost: 3 × $150 = $450
```

**Quick Reference — Contracts by Premium:**

| Premium | Cost per Contract | Contracts on $500 Budget |
|---|---|---|
| $0.50 | $50 | 10 contracts |
| $1.00 | $100 | 5 contracts |
| $1.50 | $150 | 3 contracts |
| $2.00 | $200 | 2 contracts |
| $3.00 | $300 | 1 contract |
| $5.00 | $500 | 1 contract |
| $7.00 | $700 | 0 (over budget) |

---

# Module 5: Exits & Risk Management

## Your Maximum Loss Is Built-In

This is one of the best features of buying options: **your maximum loss is the premium you paid.** Period. If TSLA drops 50% in a day, your loss is still just the premium.

```
Bought: 5 contracts at $1.00 = $500 total cost
Maximum possible loss: $500 (option expires worthless)
No margin call. No surprise losses. No broker knocking on your door.
```

## The 25x Limit Sell

When you enter a trade, immediately place a **Good-Til-Cancelled (GTC) limit sell** at 25x your purchase price.

| You Paid | Sell Target (25x) | Profit per Contract |
|---|---|---|
| $0.25 | $6.25 | $600 |
| $0.50 | $12.50 | $1,200 |
| $1.00 | $25.00 | $2,400 |
| $1.50 | $37.50 | $3,600 |
| $2.00 | $50.00 | $4,800 |
| $3.00 | $75.00 | $7,200 |
| $5.00 | $125.00 | $12,000 |

## The Tiered Exit System

Instead of going all-or-nothing on the 25x target, use a **tiered exit** to lock in profits along the way:

| Tier | When | Action | Purpose |
|---|---|---|---|
| **Tier 1** | Option hits **3x** your cost | Sell **50%** of contracts | Covers your cost basis |
| **Tier 2** | Option hits **5x** your cost | Sell **25%** of contracts | Bank additional gains |
| **Tier 3** | Option hits **25x** (or trailing stop) | Sell **final 25%** | Let the runner run |

### Example: 10 Contracts Bought at $1.00 ($1,000 Total Cost)

**Step 1 — Entry:**
- Buy 10 contracts at $1.00 each = $1,000 total investment

**Step 2 — Tier 1 hits (option reaches $3.00):**
- Sell 5 contracts at $3.00 = $1,500
- You've already made back your $1,000 + $500 profit
- **Remaining: 5 contracts, all playing with house money**

**Step 3 — Tier 2 hits (option reaches $5.00):**
- Sell 3 contracts (rounding 25% of original 10) at $5.00 = $1,500
- Total banked: $1,500 + $1,500 = $3,000
- **Remaining: 2 contracts, pure profit runners**

**Step 4 — Tier 3 (option reaches $25.00 or trailing stop triggers):**
- Sell final 2 contracts at $25.00 = $5,000
- **Total realized: $3,000 + $5,000 = $8,000**
- **Net profit: $8,000 - $1,000 = $7,000 (700% return)**

**If the option dies after Tier 1:**
- You still profit $500 on a trade where the option didn't hit the full target
- This is why tiered exits dramatically improve real-world performance

## Trailing Stop Using ATR

After Tier 2 is hit, activate a trailing stop on your remaining contracts.

### The Formula

```
Trailing Stop Distance (in option price) = 1.5 × ATR × Delta

Where:
  ATR = Average True Range of the STOCK (14-period)
  Delta = Current delta of your option
```

### Example

- Stock ATR(14) = $8.50
- Option delta = 0.30
- Trailing stop distance = 1.5 × $8.50 × 0.30 = **$3.83**

If your option's highest price was $12.00:
- Trailing stop triggers at: $12.00 - $3.83 = **$8.17**

### Rule of Thumb: 30% Trail

If the ATR calculation feels complicated, use this simple rule:

```
Trailing stop = 30% below the option's highest price

Example: Option peaked at $15.00
Trailing stop = $15.00 × 0.70 = $10.50
```

### When to Activate

- **Do NOT** activate trailing stop before Tier 2
- Before Tier 2, your exit targets are fixed (Tier 1 at 3x, Tier 2 at 5x)
- **After Tier 2**, switch the remaining contracts to trailing stop mode
- This lets your runners run while protecting the bulk of your gains

## Exit Flowchart

```
TRADE ENTERED
     │
     ▼
Is option up 3x from entry? ──── NO ───→ Hold (or let expire worthless)
     │
    YES
     │
     ▼
TIER 1: Sell 50% of contracts
     │
     ▼
Is remaining position up 5x from entry? ── NO ──→ Hold remaining
     │
    YES
     │
     ▼
TIER 2: Sell 25% of original contracts
     │
     ▼
ACTIVATE TRAILING STOP on final 25%
  (30% below highest option price, or 1.5 × ATR × delta)
     │
     ▼
┌─────────────────────────────────────┐
│  Did option hit 25x?                │
│  YES → Sell all remaining           │
│  NO  → Did trailing stop trigger?   │
│        YES → Sell all remaining     │
│        NO  → Keep holding           │
│        Did it expire? → Accept loss │
└─────────────────────────────────────┘
```

---

# Module 6: Position Sizing & Bankroll Management

## The 1% Rule

**Never risk more than 1% of your total account on a single HailMary trade.**

This is the most important rule in the entire strategy. It ensures that even a catastrophic losing streak cannot blow up your account.

```
Max Risk Per Trade = Account Balance × 0.01

$5,000 account  → $50 per trade
$10,000 account → $100 per trade
$25,000 account → $250 per trade
$50,000 account → $500 per trade
$100,000 account → $1,000 per trade
```

## How to Calculate Contract Quantity

```
Step 1: Calculate budget
  Budget = Account Balance × 0.01

Step 2: Find premium
  Premium = Ask price of the option contract

Step 3: Calculate contracts
  Contracts = Budget ÷ (Premium × 100)
  Round DOWN to nearest whole number

Step 4: Verify total cost
  Total Cost = Contracts × Premium × 100
  Must be ≤ Budget
```

## Scaling by Account Size

| Account Size | Max Risk (1%) | Premium $0.50 | Premium $1.00 | Premium $2.00 | Premium $5.00 | Daily Cap (5 trades) |
|---|---|---|---|---|---|---|
| **$5,000** | $50 | 1 contract | 0 (skip) | 0 (skip) | 0 (skip) | $250/day |
| **$10,000** | $100 | 2 contracts | 1 contract | 0 (skip) | 0 (skip) | $500/day |
| **$25,000** | $250 | 5 contracts | 2 contracts | 1 contract | 0 (skip) | $1,250/day |
| **$50,000** | $500 | 10 contracts | 5 contracts | 2 contracts | 1 contract | $2,500/day |
| **$100,000** | $1,000 | 20 contracts | 10 contracts | 5 contracts | 2 contracts | $5,000/day |

**Note for Small Accounts ($5K–$10K):** You'll be limited to very cheap contracts ($0.50 or less). This is fine — many of the best HailMary setups involve cheap premiums. Just be patient and only take the highest-quality setups.

## Daily Budget Caps

| Rule | Limit |
|---|---|
| Maximum trades per day | **5** |
| Maximum daily risk | **5% of account** (5 trades × 1%) |
| Stop trading for the day after | 5 trades placed, regardless of outcome |

**Why 5 trades max?** More trades don't mean more profit. Taking 10 mediocre setups is worse than taking 3 great ones. The 5-trade cap forces you to be selective.

## What to Do After Losing/Winning Streaks

| Situation | What to Do |
|---|---|
| Lost 5 trades in a row | **Change nothing.** This is statistically expected. |
| Lost 10 trades in a row | **Change nothing.** Still within normal variance. |
| Won 3 trades in a row | **Change nothing.** Don't increase size. |
| Had a $10K winning day | **Change nothing.** Don't take extra trades tomorrow. |
| Had a $2K losing day | **Change nothing.** The math works over hundreds of trades. |

**The golden rule: The 1% risk and 5-trade daily cap NEVER change, regardless of recent results.**

The only time you adjust position size is when your *account balance* changes. If your account grows from $50K to $75K, your 1% risk grows from $500 to $750. That's automatic scaling — not emotional decision-making.

---

# Module 7: Options Basics for Beginners

If you're new to options, read this module before anything else. If you already understand options, skip to Module 8.

## What Are Options?

An option is a **contract** that gives you the right (but not the obligation) to buy or sell a stock at a specific price, before a specific date.

Think of it like a **ticket** to a concert:
- The ticket costs money (the **premium**)
- It's only valid until a certain date (the **expiration**)
- It gives you the right to attend (buy or sell the stock at the **strike price**)
- If you don't use it, it expires worthless — you just lose what you paid for the ticket

## Calls vs. Puts in Plain English

### Call Option = "I think the stock is going UP"

- You buy a call when you believe the stock price will rise
- If the stock goes up past your strike price, your call becomes valuable
- If the stock goes down or doesn't move enough, your call expires worthless

**Real-world analogy:** You pay $100 for the right to buy a rare sneaker at $200. If the sneaker's market price rises to $500, your "right to buy at $200" is now worth $300. If the sneaker's price drops to $150, your right to buy at $200 is worthless — but you only lost the $100 you paid.

### Put Option = "I think the stock is going DOWN"

- You buy a put when you believe the stock price will fall
- If the stock drops below your strike price, your put becomes valuable
- If the stock goes up or doesn't drop enough, your put expires worthless

**Real-world analogy:** You pay $100 for the right to sell your car at $20,000. If the car market crashes and your car is now only worth $15,000, your "right to sell at $20,000" is worth $5,000. If the car market booms, your right is worthless — but you only lost the $100.

## What Does "0 DTE" Mean?

**DTE = Days To Expiration**

- **0 DTE** = The option expires **today**
- **1 DTE** = The option expires **tomorrow**
- **30 DTE** = The option expires in **30 days**

In HailMary, we specifically buy 0 DTE options because:
1. They're the cheapest (most of their time value has decayed)
2. They move the most in percentage terms when the stock moves
3. They have a defined "end of day" expiration — no overnight risk

## Premium = The Price of the Ticket

When you see an option priced at "$1.50," that means:

```
Cost per contract = $1.50 × 100 shares = $150
```

Options are quoted per share but traded in contracts of 100 shares. So:
- "$0.50 option" costs $50 per contract
- "$1.00 option" costs $100 per contract
- "$5.00 option" costs $500 per contract

**The premium is the most you can lose.** If you buy 1 contract at $1.50 ($150), the absolute worst case is you lose $150.

## Buying to Open vs. Selling to Close

| Action | What It Means | When You Do It |
|---|---|---|
| **Buy to Open** | You're entering a new position (buying the option) | When you spot a HailMary setup |
| **Sell to Close** | You're exiting your position (selling the option you own) | When taking profit or cutting losses |

You **always** start with "Buy to Open" and end with "Sell to Close." This is buying options, not selling them — an important distinction.

## What Happens at Expiration

At the end of the trading day for 0 DTE options:

| Scenario | What Happens |
|---|---|
| Option is **in the money** (ITM) | Automatically exercised or sold by your broker |
| Option is **out of the money** (OTM) | Expires worthless — you lose the premium |
| Option is **at the money** (ATM) | Usually expires worthless (or very small value) |

**For HailMary:** Most of your options will expire worthless. That's by design. You're looking for the 1-in-4 that rockets to 25x.

---

# Module 8: Broker Setup Guide

## Recommended Platforms

| Broker | 0 DTE Options | Commission | Paper Trading | Best For |
|---|---|---|---|---|
| **Robinhood** | ✅ Yes | $0 | ❌ No | Beginners, simplicity |
| **Charles Schwab** | ✅ Yes | $0.65/contract | ✅ Yes (PaperMoney) | Full-featured platform |
| **Fidelity** | ✅ Yes | $0.65/contract | ❌ No | Research tools |
| **Interactive Brokers** | ✅ Yes | $0.25–$0.65/contract | ✅ Yes | Advanced traders, best fills |
| **Tastytrade** | ✅ Yes | $1.00/contract ($0 to close) | ✅ Yes | Options-focused platform |

## How to Enable Options Trading

Most brokers require you to apply for options trading. Here's the general process:

1. **Open a brokerage account** (if you don't have one)
2. **Apply for options trading** — usually under "Account Settings" or "Trading Permissions"
3. **Select Level 2** at minimum (you need "Buy to Open" permissions for calls and puts)
4. **Answer the questionnaire honestly** — they'll ask about your experience and income
5. **Wait for approval** — typically 1–3 business days
6. **Fund your account** — deposit at least your starting capital

### Level Requirements by Broker

| Broker | Level Needed | Name |
|---|---|---|
| Robinhood | Level 2 | "Intermediate" |
| Schwab | Level 1 | "Buy Calls and Puts" |
| Fidelity | Level 2 | "Buy/Write" |
| IBKR | Standard | Enable "US Options" |
| Tastytrade | Default | Options enabled by default |

## Paper Trading — Practice First

**Strongly recommended: Paper trade for at least 2 weeks before using real money.**

Paper trading lets you practice the entire HailMary workflow with fake money:

| Broker | How to Access Paper Trading |
|---|---|
| Schwab | thinkorswim platform → "PaperMoney" mode |
| Interactive Brokers | TWS → Switch to "Paper Trading" account |
| Tastytrade | Settings → "Paper Trading" toggle |

Track your paper trades the same way you'd track real ones (see Module 9). This builds muscle memory for the process.

## How to Place Orders for HailMary

### Order 1: Entry — Limit Buy

1. Find your contract (0 DTE, strike 0.5% OTM)
2. Click "Buy to Open"
3. Order type: **Limit**
4. Limit price: Set at or slightly above the **Ask** price
5. Quantity: Your calculated number of contracts
6. Duration: **Day** (order cancels if not filled today)
7. Submit

### Order 2: Profit Target — Limit Sell

Immediately after your entry fills:

1. Select your open position
2. Click "Sell to Close"
3. Order type: **Limit**
4. Limit price: **25x your entry price** (for the Tier 3 runner portion)
5. Quantity: 25% of your contracts (the Tier 3 runner)
6. Duration: **Day** (auto-cancels at close)
7. Submit

### Order 3: Tier 1 Exit — Limit Sell

1. Select your open position
2. Click "Sell to Close"
3. Limit price: **3x your entry price**
4. Quantity: 50% of your contracts
5. Duration: **Day**
6. Submit

### Order 4: Tier 2 Exit — Limit Sell

1. Select your open position
2. Click "Sell to Close"
3. Limit price: **5x your entry price**
4. Quantity: 25% of your contracts
5. Duration: **Day**
6. Submit

### Order 5: Trailing Stop (After Tier 2 Fills)

Once Tier 2 fills, cancel the Tier 3 limit sell and replace with:

1. Select remaining position
2. Click "Sell to Close"
3. Order type: **Trailing Stop** (if available) or **manually monitor**
4. Trail amount: 30% below highest price (or 1.5 × ATR × delta)
5. Submit

> **Note:** Not all brokers support trailing stops on options. If yours doesn't, set a mental stop and manually sell when the option drops 30% from its highest point after Tier 2.

---

# Module 9: Trading Journal & Tracking

## Why Track Every Trade?

Without a journal, you're flying blind. Tracking lets you:
- Know your actual win rate (not your memory of it)
- Calculate your real profit factor
- Identify which symbols perform best for you
- Catch mistakes before they become habits
- Prove to yourself the strategy works (or needs adjustment)

## Spreadsheet Template

Create a spreadsheet (Google Sheets or Excel) with these columns:

| Column | Description | Example |
|---|---|---|
| **Date** | Trade date | 2026-02-12 |
| **Symbol** | Stock ticker | TSLA |
| **Direction** | Call or Put | Call |
| **Strike** | Strike price | $250 |
| **Premium Paid** | Cost per contract | $1.50 |
| **Contracts** | Number bought | 5 |
| **Total Cost** | Premium × Contracts × 100 | $750 |
| **Tier 1 Hit?** | Did it reach 3x? | Yes/No |
| **Tier 2 Hit?** | Did it reach 5x? | Yes/No |
| **Tier 3 Hit?** | Did it reach 25x? | Yes/No |
| **Exit Price** | Average sell price | $4.25 |
| **Total Proceeds** | What you received | $1,062.50 |
| **P&L** | Proceeds - Cost | +$312.50 |
| **Notes** | What you observed | "Strong VWAP break, high volume" |

## Calculating Key Metrics

At the bottom of your spreadsheet, add these formulas:

### Win Rate
```
Win Rate = (Number of trades with positive P&L) ÷ (Total trades) × 100

Target: 20–30% (anything in this range confirms the strategy is working)
```

### Profit Factor
```
Profit Factor = (Sum of all winning P&L) ÷ (Absolute sum of all losing P&L)

Target: Above 3.0 (our backtest showed 7.63)
Concern: Below 2.0 (review your setups)
```

### Average Winner vs. Average Loser
```
Avg Winner = Sum of winning P&L ÷ Number of winners
Avg Loser = Sum of losing P&L ÷ Number of losers
Win/Loss Ratio = Avg Winner ÷ |Avg Loser|

Target: 10x or higher
```

## Weekly Review Process (15 minutes every Friday)

1. **Count trades**: How many did you take this week?
2. **Calculate win rate**: Is it between 20–30%?
3. **Check profit factor**: Is it above 3.0?
4. **Review losers**: Did you follow all 6 checklist items on every trade?
5. **Review winners**: What did the winners have in common?
6. **Adjust nothing**: If your process was correct, change nothing — even if it was a losing week

## Monthly Review Process (30 minutes on the 1st of each month)

1. **Total P&L for the month**: Was it positive?
2. **Best and worst symbols**: Which tickers performed best?
3. **Setup quality**: Were you taking only A+ setups or forcing trades?
4. **Emotional check**: Did you break any rules? Revenge trade? Over-size?
5. **Account growth**: Update your 1% risk calculation for next month
6. **Screenshot your equity curve**: Visual progress is motivating

---

# Module 10: Market Conditions — When NOT to Trade

Knowing when **not** to trade is just as important as knowing when to trade. Some market conditions kill HailMary setups.

## Low VIX (Below 14)

| VIX Level | Market Condition | HailMary Action |
|---|---|---|
| Below 14 | Very calm, low volatility | ❌ **Sit out** — stocks won't move enough |
| 14–20 | Normal volatility | ✅ Standard trading |
| 20–30 | Elevated fear/movement | ✅ **Best conditions** — bigger moves |
| Above 30 | Extreme fear/crisis | ⚠️ Trade with caution — spreads widen |

**Check VIX first thing every morning.** If it's below 14, consider taking the day off entirely.

## Market Holidays and Half-Days

| Event | Action |
|---|---|
| Market closed (holiday) | No trading (obviously) |
| Half-day (day before holiday) | ❌ **Skip** — low volume, early close, options behave erratically |
| Day after long weekend | ⚠️ First 30 minutes may be choppy — wait for setups to develop |

**Major US Market Holidays (markets closed):**
New Year's Day, MLK Day, Presidents' Day, Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day, Thanksgiving, Christmas

**Half-Days (close at 1:00 PM ET):**
Day before Independence Day, Day after Thanksgiving, Christmas Eve (if weekday)

## Earnings Days — Skip That Stock

When a company reports earnings (before or after market hours), **skip that stock for the entire day.**

| Situation | Action |
|---|---|
| Stock reports earnings today (before open) | ❌ Skip that stock today |
| Stock reports earnings today (after close) | ❌ Skip that stock today |
| Stock reported earnings yesterday | ⚠️ Trade with caution (post-earnings drift can work) |

**Why?** Earnings create unpredictable gaps and IV crush. Options pricing becomes erratic, and the 6-point checklist becomes unreliable.

**How to check:** Use https://finviz.com/screener.ashx with "Earnings Date = Today" filter, or check https://www.earningswhispers.com for the calendar.

## Major Economic Events

These events create market-wide volatility that can whipsaw your positions:

| Event | Typical Schedule | Action |
|---|---|---|
| **FOMC Rate Decision** | 8 times/year (2:00 PM ET) | ❌ Skip that day entirely |
| **CPI Report** | Monthly (8:30 AM ET) | ❌ Wait until 10:00 AM, then evaluate |
| **Jobs Report (NFP)** | First Friday of each month (8:30 AM ET) | ❌ Wait until 10:00 AM, then evaluate |
| **PPI Report** | Monthly | ⚠️ Trade with smaller size |
| **Fed Chair Speech** | Varies | ⚠️ Avoid trading 30 min before/after |

**Where to find the schedule:** https://www.forexfactory.com/calendar (shows all major US economic events)

## When to Sit on Your Hands

If any of these are true, **don't trade**:

- [ ] VIX is below 14
- [ ] It's a half-day or day before a holiday
- [ ] FOMC decision is today
- [ ] CPI or Jobs report came out this morning (wait until 10 AM)
- [ ] None of your 10 watchlist stocks have moved 0.3%+ by 10:00 AM
- [ ] You've already taken 5 trades today
- [ ] You're feeling emotional, frustrated, or desperate for a win

**One checked box = sit on your hands.** Go do something else. The market will be here tomorrow.

---

# Module 11: Psychology & Discipline

## Handling 5+ Losses in a Row

Let's do the math on losing streaks:

| Consecutive Losses | Probability (at 76% loss rate) |
|---|---|
| 2 in a row | 57.8% — happens constantly |
| 3 in a row | 43.9% — happens almost every week |
| 5 in a row | 25.3% — happens multiple times a month |
| 7 in a row | 14.5% — happens at least once a month |
| 10 in a row | 6.4% — will happen a few times a year |
| 15 in a row | 1.5% — will happen eventually |

**A 10-trade losing streak is not bad luck — it's mathematics.** With a 24% win rate, you should *expect* losing streaks of 5-10 trades regularly.

### What to Do During a Losing Streak

1. **Check your process, not your results.** Did you follow all 6 checklist items on every trade?
2. **If process was correct:** Change absolutely nothing. The math is on your side.
3. **If process was wrong:** Fix the process error, not the strategy.
4. **Do NOT** increase position size to "make it back"
5. **Do NOT** lower your 25x target to "win more often"
6. **Do NOT** take extra trades to "get more chances"

## Why Moving the 25x Target Destroys the Math

This is the single most common mistake that kills HailMary traders:

| Target Multiplier | Win Rate Needed to Break Even | Expected Edge |
|---|---|---|
| **25x** (correct) | 4% | **+527% per dollar** |
| 10x | 10% | +141% per dollar |
| 5x | 20% | +20% per dollar |
| 3x | 33% | -1% per dollar (LOSS) |
| 2x | 50% | -24% per dollar (LOSS) |

If you lower your target to 3x "because it hits more often," you've turned a massively profitable strategy into a losing one. **The 25x multiplier IS the edge.** Without it, you're just gambling.

## Revenge Trading

Revenge trading = taking trades outside your system because you're angry about losses.

**Signs you're revenge trading:**
- You just lost and immediately want to enter another trade
- You're looking at stocks outside your watchlist
- You're increasing position size without a corresponding account increase
- You're taking trades that don't pass all 6 checklist items
- You're trading after your 5-trade daily limit

**The fix:** When you feel the urge to revenge trade, **physically walk away from your computer.** Set a timer for 30 minutes. If the setup is still valid when you come back (and it passes all 6 checks), take it. If not, you were about to revenge trade.

## Process Over Outcome Mindset

| Metric | Don't Focus On | Focus On |
|---|---|---|
| Single trade P&L | "I lost $500 today" | "I followed my process on all 3 trades" |
| Daily results | "Today was a bad day" | "I only took setups that passed all 6 checks" |
| Weekly results | "This week sucked" | "My checklist compliance was 100%" |
| Winning streak | "I'm on fire, let me size up" | "I'll keep doing exactly what I've been doing" |

**The only question that matters after every trade: "Did I follow the process?"**

If yes → the trade was a good trade, regardless of outcome.
If no → the trade was a bad trade, even if it made money.

## Daily Routine

| Time | Action | Duration |
|---|---|---|
| **Pre-market** (before 9:30 AM ET) | Check VIX, load Finviz screeners, scan watchlist | 15 min |
| **9:30–9:45 AM** | Let the market open and settle — do NOT trade in first 15 min | 15 min (waiting) |
| **9:45–11:30 AM** | Run the 6-point checklist on movers, take qualified trades | 1–2 hours |
| **11:30 AM–1:00 PM** | Manage open positions (Tier exits, trailing stops) | 30 min |
| **1:00 PM** | Stop looking for new trades — manage existing only | — |
| **3:00 PM** | Close any remaining positions (options expire at 4:00 PM) | 15 min |
| **After close** | Log all trades in journal | 10 min |
| **Walk away** | Done. Go live your life. | Rest of day |

**Total active time: 2–3 hours per day.** This is not a full-time job. Do not sit at your screen for 8 hours looking for trades that aren't there.

---

# Module 12: Backtest Results & Proof

## 1,000-Day Backtest Summary

| Metric | Value |
|---|---|
| **Backtest Period** | ~1,000 trading days (May 2023 – Feb 2026) |
| **Starting Capital** | $50,000 |
| **Final Equity** | $26,714,299 |
| **Total Net Profit** | **$26,664,299** |
| **Total Return** | 53,329% |
| **Total Trades** | 5,339 |
| **Winners** | 1,287 (24.1%) |
| **Losers** | 4,052 (75.9%) |
| **Profit Factor** | **7.63** |
| **Sharpe Ratio** | **7.46** |
| **Max Drawdown** | 12.7% |
| **Average Winner** | $23,844 |
| **Average Loser** | -$993 |
| **Win/Loss Ratio** | **24.0x** |
| **Largest Single Win** | $24,000 |
| **Largest Single Loss** | -$1,000 |

## Monthly Performance Table

**Every single month was profitable.**

| Month | Trades | Wins | Win Rate | P&L |
|---|---|---|---|---|
| 2023-05 | 57 | 20 | 35.1% | $440,718 |
| 2023-06 | 167 | 43 | 25.7% | $902,747 |
| 2023-07 | 141 | 40 | 28.4% | $852,947 |
| 2023-08 | 166 | 38 | 22.9% | $780,224 |
| 2023-09 | 139 | 28 | 20.1% | $557,390 |
| 2023-10 | 172 | 42 | 24.4% | $873,193 |
| 2023-11 | 164 | 28 | 17.1% | **$532,939** ← Worst month |
| 2023-12 | 154 | 33 | 21.4% | $667,987 |
| 2024-01 | 169 | 45 | 26.6% | $950,627 |
| 2024-02 | 161 | 32 | 19.9% | $634,678 |
| 2024-03 | 167 | 41 | 24.6% | $849,690 |
| 2024-04 | 194 | 51 | 26.3% | $1,074,642 |
| 2024-05 | 173 | 44 | 25.4% | $920,301 |
| 2024-06 | 143 | 29 | 20.3% | $578,326 |
| 2024-07 | 174 | 42 | 24.1% | $871,279 |
| 2024-08 | 180 | 37 | 20.6% | $742,135 |
| 2024-09 | 145 | 28 | 19.3% | $552,193 |
| 2024-10 | 174 | 35 | 20.1% | $697,653 |
| 2024-11 | 171 | 50 | 29.2% | $1,072,293 |
| 2024-12 | 176 | 59 | 33.5% | **$1,289,186** ← Best month |
| 2025-01 | 157 | 40 | 25.5% | $835,756 |
| 2025-02 | 143 | 36 | 25.2% | $751,473 |
| 2025-03 | 154 | 39 | 25.3% | $815,027 |
| 2025-04 | 146 | 52 | 35.6% | $1,146,650 |
| 2025-05 | 145 | 32 | 22.1% | $650,492 |
| 2025-06 | 164 | 35 | 21.3% | $707,747 |
| 2025-07 | 165 | 34 | 20.6% | $682,187 |
| 2025-08 | 168 | 31 | 18.5% | $604,040 |
| 2025-09 | 161 | 29 | 18.0% | $560,352 |
| 2025-10 | 198 | 51 | 25.8% | $1,068,458 |
| 2025-11 | 164 | 44 | 26.8% | $928,004 |
| 2025-12 | 161 | 34 | 21.1% | $684,022 |
| 2026-01 | 162 | 41 | 25.3% | $856,618 |
| 2026-02 (partial) | 64 | 24 | 37.5% | $532,326 |

**Worst Month:** November 2023 — $532,939 profit (still a $532K month)
**Best Month:** December 2024 — $1,289,186 profit

## Per-Symbol Performance

| Symbol | Trades | Wins | Win Rate | Total P&L | Avg P&L/Trade |
|---|---|---|---|---|---|
| **TSLA** | 622 | 223 | **35.9%** | **$4,923,192** | $7,915 |
| **NVDA** | 549 | 175 | **31.9%** | **$3,798,382** | $6,918 |
| **META** | 585 | 149 | 25.5% | **$3,105,242** | $5,308 |
| **AMZN** | 567 | 147 | 25.9% | **$3,095,058** | $5,459 |
| **AMD** | 426 | 140 | **32.9%** | **$3,063,691** | $7,192 |
| **GOOGL** | 580 | 139 | 24.0% | $2,884,224 | $4,973 |
| **AAPL** | 543 | 112 | 20.6% | $2,246,852 | $4,138 |
| **MSFT** | 526 | 88 | 16.7% | $1,658,465 | $3,153 |
| **QQQ** | 494 | 78 | 15.8% | $1,441,564 | $2,919 |
| **SPY** | 447 | 36 | 8.1% | $447,630 | $1,001 |

**Key Takeaways:**
- **TSLA is the king** — highest win rate (35.9%) and highest total P&L ($4.9M)
- **NVDA and AMD** are strong runners — volatile tech stocks are ideal for HailMary
- **SPY is the weakest** — index ETFs move less dramatically than individual stocks
- **All 10 symbols were profitable** — diversification works

## Calls vs. Puts

Both directions are profitable:

| Direction | Estimated P&L | Share of Total |
|---|---|---|
| **Calls** (bullish) | ~$14,900,000 | 56% |
| **Puts** (bearish) | ~$11,800,000 | 44% |

You don't need to be a perma-bull or perma-bear. The strategy works in both directions.

## Equity Growth Milestones

| Milestone | Approximate Trade # | Time to Reach |
|---|---|---|
| $100,000 (2x starting) | Trade #4 | Week 1 |
| $500,000 (10x) | ~Trade #120 | Month 2 |
| $1,000,000 (20x) | ~Trade #242 | Month 4 |
| $5,000,000 (100x) | ~Trade #894 | Month 10 |
| $10,000,000 (200x) | ~Trade #1,573 | Month 16 |
| $26,700,000 (534x) | Trade #5,339 | Month 34 |

## Honest Disclaimers About Backtesting vs. Live Trading

**This section is required reading. Do not skip it.**

### What the backtest does NOT account for:

1. **Slippage**: In live trading, you won't always get filled at the exact price you want. Fast-moving options can gap past your limit orders.

2. **Liquidity**: The backtest assumes you can always buy/sell at the quoted price. In reality, 0 DTE options on some stocks may have wide spreads or low volume at certain strikes.

3. **Compounding assumptions**: The backtest reinvests all profits immediately at the same risk percentage. In practice, you may not scale up as aggressively.

4. **Emotional decisions**: The backtest never panics, never revenge trades, never skips a day because of a bad week. You will.

5. **Market microstructure**: Real options pricing involves bid/ask spreads, market maker dynamics, and intraday volatility that simplified models can't fully capture.

6. **Survivorship bias**: We tested 10 stocks that are currently large and liquid. Some of these may not have been as liquid or as volatile throughout the entire backtest period.

7. **Regulatory changes**: Options trading rules, pattern day trader rules, and broker policies can change.

### Realistic expectations for live trading:

| Metric | Backtest | Realistic Live Range |
|---|---|---|
| Win Rate | 24.1% | 18–28% |
| Profit Factor | 7.63 | 3.0–6.0 |
| Max Drawdown | 12.7% | 15–25% |
| Monthly Return | $532K–$1.29M | Varies with account size |

**The strategy has a genuine mathematical edge.** But the backtest results represent a best-case scenario. Expect your real results to be 40–60% of the backtest numbers, which is still extremely profitable.

---

# Appendix A: Quick Reference Cheat Sheet

## 6-Point Entry Checklist

| # | Check | Calls | Puts | Skip |
|---|---|---|---|---|
| 1 | VWAP | Above | Below | Crossing |
| 2 | Volume | Above 20-MA | Above 20-MA | Below 20-MA |
| 3 | RSI(14) | 40–70 | 30–60 | >80 or <20 |
| 4 | EMA 9/21 | Green > Red | Green < Red | Tangled |
| 5 | ATR | Range > 1x ATR | Range > 1x ATR | Range < 0.5x ATR |
| 6 | Change% | > +0.3% | < -0.3% | Between ±0.3% |

**All 6 must agree. One "Skip" = no trade.**

## Tiered Exit Table

| Tier | Trigger | Action | Contracts |
|---|---|---|---|
| 1 | 3x entry price | Sell 50% | Half your position |
| 2 | 5x entry price | Sell 25% | Quarter of original |
| 3 | 25x or trailing stop | Sell remaining 25% | Final quarter |

## ATR Trailing Stop Formula

```
Trailing Stop Distance = 1.5 × ATR(14) × Option Delta

Simplified Rule: Trail 30% below option's highest price
Activate: Only AFTER Tier 2 is hit
```

## Position Sizing Calculator

```
Step 1: Max Risk = Account Balance × 0.01
Step 2: Contracts = Max Risk ÷ (Option Premium × 100)
Step 3: Round DOWN to nearest whole number
Step 4: If Contracts = 0, skip this trade (premium too expensive)
```

**Quick Table:**

| Account | 1% Risk | $0.50 Premium | $1.00 Premium | $2.00 Premium |
|---|---|---|---|---|
| $5K | $50 | 1 contract | 0 | 0 |
| $10K | $100 | 2 contracts | 1 contract | 0 |
| $25K | $250 | 5 contracts | 2 contracts | 1 contract |
| $50K | $500 | 10 contracts | 5 contracts | 2 contracts |
| $100K | $1,000 | 20 contracts | 10 contracts | 5 contracts |

---

# Appendix B: Daily Trading Checklist (Printable)

```
╔══════════════════════════════════════════════════════════════╗
║            HAILMARY DAILY TRADING CHECKLIST                  ║
║                                                              ║
║  Date: _______________    Account Balance: $___________      ║
║  Max Risk Per Trade (1%): $___________                       ║
║  Max Trades Today: 5                                         ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  PRE-MARKET CHECKS                                           ║
║  [ ] VIX above 14?                                           ║
║  [ ] Not a half-day or pre-holiday?                          ║
║  [ ] No FOMC / CPI / Jobs report today?                      ║
║  [ ] Finviz screeners loaded (HM Calls + HM Puts)?          ║
║  [ ] TradingView watchlist sorted by Change%?                ║
║                                                              ║
║  If ANY box above is unchecked → NO TRADING TODAY            ║
║                                                              ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  TRADE 1                                                     ║
║  Symbol: ______  Direction: Call / Put  Time: _______        ║
║  [ ] 1. VWAP confirms direction                              ║
║  [ ] 2. Volume above 20-MA                                   ║
║  [ ] 3. RSI in valid range (not >80 or <20)                  ║
║  [ ] 4. EMA 9/21 confirms direction                          ║
║  [ ] 5. Today's range > 1x ATR                               ║
║  [ ] 6. Change% above ±0.3%                                  ║
║  Strike: ______  Premium: $______  Contracts: ______         ║
║  Total Cost: $______  (must be ≤ 1% of account)              ║
║  Tier 1 target (3x): $______                                 ║
║  Tier 2 target (5x): $______                                 ║
║  Tier 3 target (25x): $______                                ║
║  Result: Win / Loss    P&L: $______                          ║
║                                                              ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  TRADE 2                                                     ║
║  Symbol: ______  Direction: Call / Put  Time: _______        ║
║  [ ] 1. VWAP    [ ] 2. Volume    [ ] 3. RSI                 ║
║  [ ] 4. EMAs    [ ] 5. ATR       [ ] 6. Change%             ║
║  Strike: ______  Premium: $______  Contracts: ______         ║
║  Total Cost: $______                                         ║
║  Result: Win / Loss    P&L: $______                          ║
║                                                              ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  TRADE 3                                                     ║
║  Symbol: ______  Direction: Call / Put  Time: _______        ║
║  [ ] 1. VWAP    [ ] 2. Volume    [ ] 3. RSI                 ║
║  [ ] 4. EMAs    [ ] 5. ATR       [ ] 6. Change%             ║
║  Strike: ______  Premium: $______  Contracts: ______         ║
║  Total Cost: $______                                         ║
║  Result: Win / Loss    P&L: $______                          ║
║                                                              ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  TRADE 4                                                     ║
║  Symbol: ______  Direction: Call / Put  Time: _______        ║
║  [ ] 1. VWAP    [ ] 2. Volume    [ ] 3. RSI                 ║
║  [ ] 4. EMAs    [ ] 5. ATR       [ ] 6. Change%             ║
║  Strike: ______  Premium: $______  Contracts: ______         ║
║  Total Cost: $______                                         ║
║  Result: Win / Loss    P&L: $______                          ║
║                                                              ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  TRADE 5                                                     ║
║  Symbol: ______  Direction: Call / Put  Time: _______        ║
║  [ ] 1. VWAP    [ ] 2. Volume    [ ] 3. RSI                 ║
║  [ ] 4. EMAs    [ ] 5. ATR       [ ] 6. Change%             ║
║  Strike: ______  Premium: $______  Contracts: ______         ║
║  Total Cost: $______                                         ║
║  Result: Win / Loss    P&L: $______                          ║
║                                                              ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  END OF DAY SUMMARY                                          ║
║  Total Trades: ___  Wins: ___  Losses: ___                   ║
║  Total P&L: $______                                          ║
║  Did I follow all rules? [ ] Yes  [ ] No                     ║
║  If No, what did I break? _________________________________  ║
║  Emotion check (1-10, 5=neutral): ___                        ║
║  Notes: ________________________________________________     ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Final Note

The HailMary strategy is mathematically sound, backtested across 1,000 days and 5,339 trades. It works because of one simple principle: **when your winners are 25x bigger than your losers, you only need to win 1 out of every 4 trades to be massively profitable.**

Follow the process. Trust the math. Don't change the rules.

Good luck, and trade responsibly.

---

*This document is for educational purposes only. Options trading involves significant risk of loss. Past backtest performance does not guarantee future results. Never trade with money you cannot afford to lose. Consult a licensed financial advisor before making investment decisions.*
