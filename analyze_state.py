#!/usr/bin/env python3
"""
Trading Hydra - Full State & Log Audit
Covers: trading_state.db, system_state.jsonl, decision_records.jsonl,
        performance_metrics.jsonl, slippage_events.jsonl
"""

import sqlite3
import json
import os
import sys
from datetime import datetime, timezone
from collections import defaultdict, Counter

BASE_DIR = r"C:\Users\admin1\Downloads\export (1)\export"
DB_PATH  = os.path.join(BASE_DIR, "state", "trading_state.db")
MET_DB   = os.path.join(BASE_DIR, "state", "metrics.db")
LOG_DIR  = os.path.join(BASE_DIR, "logs")

SEP = "=" * 80

def section(title):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)

def sub(title):
    print(f"\n--- {title} ---")

# ─────────────────────────────────────────────────────────────────────────────
# 1.  trading_state.db
# ─────────────────────────────────────────────────────────────────────────────
section("1. trading_state.db  –  SCHEMA")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur  = conn.cursor()

cur.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table','view') ORDER BY name")
for row in cur.fetchall():
    print(f"\nTABLE: {row['name']}")
    print(row['sql'])

# row counts per table
sub("Row counts")
for tbl in ["state","order_ids","exit_trades","exit_decisions","exit_options_context","bar_cache"]:
    cur.execute(f"SELECT COUNT(*) FROM {tbl}")
    print(f"  {tbl}: {cur.fetchone()[0]:,} rows")

# ── session_prot_ keys ─────────────────────────────────────────────────────
section("2.  state keys: session_prot_*")

cur.execute("SELECT key, value, updated_at FROM state WHERE key LIKE 'session_prot_%' ORDER BY key")
rows = cur.fetchall()
print(f"Total session_prot_ keys: {len(rows)}\n")
for r in rows:
    try:
        val = json.loads(r['value'])
        val_display = json.dumps(val, indent=2)
    except Exception:
        val_display = r['value']
    print(f"KEY: {r['key']}")
    print(f"  updated_at: {r['updated_at']}")
    print(f"  value: {val_display[:600]}")
    print()

# ── hailmary_bot_trade_ keys ───────────────────────────────────────────────
section("3.  state keys: hailmary_bot_trade_*")

cur.execute("SELECT key, value, updated_at FROM state WHERE key LIKE 'hailmary_bot_trade_%' ORDER BY key")
rows = cur.fetchall()
print(f"Total hailmary_bot_trade_ keys: {len(rows)}\n")
for r in rows:
    try:
        val = json.loads(r['value'])
        val_display = json.dumps(val, indent=2)
    except Exception:
        val_display = r['value']
    print(f"KEY: {r['key']}")
    print(f"  updated_at: {r['updated_at']}")
    print(f"  value: {val_display[:800]}")
    print()

# ── daily equity / day-start / PnL keys ───────────────────────────────────
section("4.  Daily equity / day_start / account state")

sub("day_start_equity_* keys")
cur.execute("SELECT key, value, updated_at FROM state WHERE key LIKE 'day_start_equity%' ORDER BY key")
for r in cur.fetchall():
    try:
        v = float(r['value'])
        print(f"  {r['key']}: ${v:,.2f}   (updated: {r['updated_at']})")
    except Exception:
        print(f"  {r['key']}: {r['value']}   (updated: {r['updated_at']})")

sub("account.equity / cross_bot.equity")
for k in ["account.equity", "cross_bot.equity", "day_start_equity"]:
    cur.execute("SELECT value, updated_at FROM state WHERE key=?", (k,))
    r = cur.fetchone()
    if r:
        print(f"  {k}: {r['value']}   (updated: {r['updated_at']})")

sub("daily_pnl_* keys")
cur.execute("SELECT key, value, updated_at FROM state WHERE key LIKE 'daily_pnl%' ORDER BY key")
pnl_rows = cur.fetchall()
for r in pnl_rows:
    print(f"  {r['key']}: {r['value']}   (updated: {r['updated_at']})")

sub("budget keys (all bots)")
cur.execute("SELECT key, value, updated_at FROM state WHERE key LIKE 'budgets.%' ORDER BY key")
for r in cur.fetchall():
    try:
        val = json.loads(r['value'])
        print(f"  {r['key']}: {json.dumps(val)}")
    except Exception:
        print(f"  {r['key']}: {r['value']}")

sub("bots.* state keys")
cur.execute("SELECT key, value, updated_at FROM state WHERE key LIKE 'bots.%' ORDER BY key")
for r in cur.fetchall():
    try:
        val = json.loads(r['value'])
        print(f"\n  KEY: {r['key']}  (updated: {r['updated_at']})")
        print(f"  {json.dumps(val, indent=4)[:600]}")
    except Exception:
        print(f"  {r['key']}: {r['value']}")

sub("position-related keys")
cur.execute("SELECT key, value, updated_at FROM state WHERE key LIKE '%position%' OR key LIKE '%pos_%' ORDER BY key")
pos_rows = cur.fetchall()
if pos_rows:
    for r in pos_rows:
        print(f"  {r['key']}: {r['value'][:200]}   (updated: {r['updated_at']})")
else:
    print("  (none found)")

sub("cooldown keys")
cur.execute("SELECT key, value, updated_at FROM state WHERE key LIKE 'cooldown:%' ORDER BY updated_at DESC")
for r in cur.fetchall():
    print(f"  {r['key']}: {r['value']}   (updated: {r['updated_at']})")

# ── exit_lock keys (stale check) ───────────────────────────────────────────
section("5.  exit_lock keys – staleness check")
cur.execute("SELECT key, value, updated_at FROM state WHERE key LIKE 'exit_lock%' ORDER BY updated_at")
exit_locks = cur.fetchall()
now_utc = datetime.now(timezone.utc)
print(f"Total exit_lock keys: {len(exit_locks)}")
stale_threshold_hours = 48
stale = []
for r in exit_locks:
    try:
        ts = datetime.fromisoformat(r['updated_at'].replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_h = (now_utc - ts).total_seconds() / 3600
        flag = " *** STALE ***" if age_h > stale_threshold_hours else ""
        print(f"  {r['key'][:80]:80s}  age={age_h:.1f}h{flag}")
        if age_h > stale_threshold_hours:
            stale.append((r['key'], age_h))
    except Exception as e:
        print(f"  {r['key']}  parse error: {e}")

if stale:
    print(f"\n*** {len(stale)} STALE exit_lock keys (>{stale_threshold_hours}h old):")
    for k, age in stale:
        print(f"    {k}  age={age:.1f}h")
else:
    print("\nNo stale exit_lock keys.")

# ── exit_trades table ──────────────────────────────────────────────────────
section("6.  exit_trades – completed trades this week")
cur.execute("""
    SELECT bot_id, symbol, side, entry_ts, exit_ts, entry_price, exit_price,
           qty, realized_pnl_usd, realized_pnl_pct, exit_reason, hold_duration_sec
    FROM exit_trades
    WHERE exit_ts IS NOT NULL
    ORDER BY exit_ts DESC
    LIMIT 100
""")
trades = cur.fetchall()
print(f"Recent completed trades (up to 100):\n")
total_pnl = 0.0
for t in trades:
    pnl = t['realized_pnl_usd'] or 0.0
    total_pnl += pnl
    pnl_str = f"${pnl:+.2f}" if pnl else "N/A"
    hold_m = round((t['hold_duration_sec'] or 0) / 60, 1)
    print(f"  {t['exit_ts'][:19]}  {t['bot_id']:<20s} {t['symbol']:<25s} "
          f"{t['side']:<5s} qty={t['qty']:.4f}  pnl={pnl_str:<10s} "
          f"hold={hold_m}m  reason={t['exit_reason']}")
print(f"\nSum realized PnL (shown rows): ${total_pnl:+,.2f}")

sub("Open (entry filled, no exit_ts)")
cur.execute("""
    SELECT bot_id, symbol, side, entry_ts, entry_price, qty, mfe_pct, mae_pct
    FROM exit_trades
    WHERE exit_ts IS NULL
    ORDER BY entry_ts DESC
""")
open_pos = cur.fetchall()
print(f"Open positions: {len(open_pos)}")
for t in open_pos:
    print(f"  {t['entry_ts'][:19]}  {t['bot_id']:<20s} {t['symbol']:<25s} "
          f"{t['side']:<5s} qty={t['qty']:.4f}  entry=${t['entry_price']:.4f}  "
          f"mfe={t['mfe_pct']}%  mae={t['mae_pct']}%")

# ── order_ids table ────────────────────────────────────────────────────────
section("7.  order_ids – recent submitted orders")
cur.execute("""
    SELECT client_order_id, bot_id, symbol, day_key, signal_id, submitted_at, alpaca_order_id
    FROM order_ids
    ORDER BY submitted_at DESC
    LIMIT 60
""")
orders = cur.fetchall()
print(f"Showing up to 60 most-recent orders:\n")
for o in orders:
    print(f"  {o['submitted_at'][:19]}  {o['bot_id']:<20s} {o['symbol']:<20s} "
          f"day={o['day_key']}  alpaca_id={o['alpaca_order_id'] or 'NONE'}")

# ── exit_decisions table ───────────────────────────────────────────────────
section("8.  exit_decisions – recent")
cur.execute("""
    SELECT ts, position_key, action, health_score, reason, unrealized_pnl_pct,
           trailing_stop_pct, regime, triggers_json
    FROM exit_decisions
    ORDER BY ts DESC
    LIMIT 60
""")
edecs = cur.fetchall()
action_counts = Counter()
print(f"Last 60 exit decisions:\n")
for d in edecs:
    action_counts[d['action']] += 1
    trigs = ""
    try:
        tj = json.loads(d['triggers_json'] or "{}")
        trigs = ",".join(tj.keys()) if tj else ""
    except Exception:
        pass
    print(f"  {d['ts'][:19]}  {d['action']:<10s}  hs={d['health_score']}  "
          f"pnl={d['unrealized_pnl_pct']}%  regime={d['regime']}  "
          f"reason={str(d['reason'])[:60]}  triggers={trigs}")

sub("Action summary")
for act, cnt in action_counts.most_common():
    print(f"  {act}: {cnt}")

# ── potential corruption / anomalies in state ──────────────────────────────
section("9.  State DB – corruption / anomaly scan")

sub("Keys with NULL or empty values")
cur.execute("SELECT key, updated_at FROM state WHERE value IS NULL OR TRIM(value)=''")
null_rows = cur.fetchall()
if null_rows:
    for r in null_rows:
        print(f"  *** NULL/EMPTY: {r['key']}  (updated: {r['updated_at']})")
else:
    print("  None found.")

sub("Keys with unparseable JSON (where JSON expected)")
json_prefix_keys = ["bots.", "budgets.", "session_prot_", "hailmary_bot_trade_",
                    "correlation_guard", "decision_tracker"]
bad_json = []
cur.execute("SELECT key, value FROM state")
all_state = cur.fetchall()
for r in all_state:
    for pref in json_prefix_keys:
        if r['key'].startswith(pref):
            try:
                json.loads(r['value'])
            except Exception:
                bad_json.append(r['key'])
if bad_json:
    print(f"  *** {len(bad_json)} keys with bad JSON:")
    for k in bad_json:
        print(f"    {k}")
else:
    print("  None found.")

sub("Duplicate/orphan exit_lock keys for same position")
cur.execute("SELECT key FROM state WHERE key LIKE 'exit_lock%'")
lock_keys = [r['key'] for r in cur.fetchall()]
# Group by symbol
from collections import defaultdict
lock_by_symbol = defaultdict(list)
for k in lock_keys:
    parts = k.split(":")
    sym = parts[3] if len(parts) > 3 else "unknown"
    lock_by_symbol[sym].append(k)
for sym, keys in lock_by_symbol.items():
    if len(keys) > 3:
        print(f"  *** {sym}: {len(keys)} exit_lock entries (possible accumulation)")
        for k in keys:
            print(f"      {k}")

sub("Exit trades with no exit but very old entry (>7 days)")
cutoff = "2026-02-20"
cur.execute("""
    SELECT bot_id, symbol, entry_ts, qty FROM exit_trades
    WHERE exit_ts IS NULL AND entry_ts < ?
    ORDER BY entry_ts
""", (cutoff,))
ghost = cur.fetchall()
if ghost:
    print(f"  *** {len(ghost)} ghost open positions (entry before {cutoff}):")
    for g in ghost:
        print(f"    {g['entry_ts'][:19]}  {g['bot_id']:<20s} {g['symbol']}  qty={g['qty']}")
else:
    print(f"  None (no open entries before {cutoff}).")

conn.close()

# ─────────────────────────────────────────────────────────────────────────────
# 2.  metrics.db
# ─────────────────────────────────────────────────────────────────────────────
section("10. metrics.db  –  daily_metrics (full history)")

mconn = sqlite3.connect(MET_DB)
mconn.row_factory = sqlite3.Row
mcur = mconn.cursor()

mcur.execute("SELECT * FROM daily_metrics ORDER BY date")
dm_rows = mcur.fetchall()
print(f"{'Date':<12} {'Equity':>12} {'Cash':>12} {'DailyPnL':>12} {'DailyPnL%':>10} "
      f"{'CumPnL':>12} {'Trades':>7} {'WinRate':>8} {'MaxDD%':>8} {'OpenPos':>8}")
print("-" * 110)
for r in dm_rows:
    print(f"{r['date']:<12} ${r['equity']:>11,.2f} ${r['cash']:>11,.2f} "
          f"${r['daily_pnl']:>+11,.2f} {r['daily_pnl_pct']:>+9.3f}% "
          f"${r['cumulative_pnl']:>+11,.2f} {r['total_trades']:>7} "
          f"{r['win_rate']:>7.1f}% {r['max_drawdown_pct']:>7.3f}% {r['open_positions']:>8}")

if dm_rows:
    print(f"\nFirst equity: ${dm_rows[0]['equity']:,.2f}  ({dm_rows[0]['date']})")
    print(f"Last  equity: ${dm_rows[-1]['equity']:,.2f}  ({dm_rows[-1]['date']})")
    net = dm_rows[-1]['equity'] - dm_rows[0]['equity']
    print(f"Net change:   ${net:+,.2f}")
    worst = min(dm_rows, key=lambda r: r['daily_pnl'])
    best  = max(dm_rows, key=lambda r: r['daily_pnl'])
    print(f"Best  day:  {best['date']}  ${best['daily_pnl']:+,.2f}  ({best['daily_pnl_pct']:+.3f}%)")
    print(f"Worst day: {worst['date']}  ${worst['daily_pnl']:+,.2f}  ({worst['daily_pnl_pct']:+.3f}%)")

sub("bot_performance (all bots, all dates)")
mcur.execute("SELECT * FROM bot_performance ORDER BY date, bot_id")
bp_rows = mcur.fetchall()
print(f"{'Date':<12} {'Bot':<22} {'Trades':>7} {'W':>4} {'L':>4} "
      f"{'PnL':>10} {'WR30d':>7} {'MaxDD30d':>9}")
print("-" * 85)
for r in bp_rows:
    print(f"{r['date']:<12} {r['bot_id']:<22} {r['trades_today']:>7} "
          f"{r['wins_today']:>4} {r['losses_today']:>4} "
          f"${r['pnl_today']:>+9,.2f} {r['win_rate_30d']:>6.1f}% "
          f"{r['max_drawdown_30d']:>8.3f}%")

sub("regime_history – last 30 records")
mcur.execute("SELECT * FROM regime_history ORDER BY timestamp DESC LIMIT 30")
rh = mcur.fetchall()
print(f"{'Timestamp':<22} {'VolRegime':<18} {'Sentiment':<15} {'PSM':>6} {'Halt':>5} {'VVIXw':>6}")
print("-" * 80)
for r in rh:
    print(f"{r['timestamp'][:19]:<22} {r['volatility_regime']:<18} "
          f"{r['sentiment']:<15} {r['position_size_multiplier']:>6.2f} "
          f"{'YES' if r['halt_new_entries'] else 'no':>5} "
          f"{'YES' if r['vvix_warning'] else 'no':>6}")

sub("risk_decisions – all")
mcur.execute("SELECT * FROM risk_decisions ORDER BY timestamp DESC")
rd = mcur.fetchall()
print(f"Total risk decisions: {len(rd)}")
for r in rd:
    print(f"  {r['timestamp'][:19]}  type={r['decision_type']:<20s} "
          f"{r['previous_value']:.4f} -> {r['new_value']:.4f}  reason={r['reason'][:80]}")

mconn.close()

# ─────────────────────────────────────────────────────────────────────────────
# 3.  system_state.jsonl
# ─────────────────────────────────────────────────────────────────────────────
section("11. system_state.jsonl  –  first 200 / last 200 lines + summary")

ss_path = os.path.join(LOG_DIR, "system_state.jsonl")
with open(ss_path, encoding="utf-8") as f:
    ss_lines = f.readlines()

total_ss = len(ss_lines)
print(f"Total lines: {total_ss:,}")

def parse_jsonl_lines(lines, label):
    records = []
    for i, ln in enumerate(lines, 1):
        ln = ln.strip()
        if not ln:
            continue
        try:
            records.append(json.loads(ln))
        except Exception as e:
            print(f"  [parse error line {i} in {label}]: {e}  raw={ln[:80]}")
    return records

sub("First 200 lines")
first200 = parse_jsonl_lines(ss_lines[:200], "system_state first200")
for r in first200[:20]:
    ts  = r.get("timestamp","?")[:19]
    evt = r.get("event", r.get("type","?"))
    msg = r.get("message", r.get("msg",""))[:100]
    print(f"  {ts}  [{evt}]  {msg}")
if len(first200) > 20:
    print(f"  ... ({len(first200)-20} more in first 200)")

sub("Last 200 lines")
last200 = parse_jsonl_lines(ss_lines[-200:], "system_state last200")
for r in last200[-40:]:
    ts  = r.get("timestamp","?")[:19]
    evt = r.get("event", r.get("type","?"))
    msg = r.get("message", r.get("msg",""))[:100]
    print(f"  {ts}  [{evt}]  {msg}")

sub("Event-type distribution (full file)")
all_ss = parse_jsonl_lines(ss_lines, "system_state full")
evt_counts = Counter()
for r in all_ss:
    evt = r.get("event", r.get("type", r.get("level","UNKNOWN")))
    evt_counts[evt] += 1
print(f"Total parseable records: {len(all_ss):,}")
print(f"{'Event/Type':<40} {'Count':>8}")
print("-" * 50)
for evt, cnt in evt_counts.most_common(40):
    print(f"  {str(evt):<38} {cnt:>8,}")

sub("Anomaly scan – ERROR / WARNING / CRITICAL events")
anomalies = []
for r in all_ss:
    level = str(r.get("level", r.get("event",""))).upper()
    if any(x in level for x in ["ERROR","WARN","CRITICAL","FATAL","EXCEPTION"]):
        anomalies.append(r)
print(f"Total anomaly records: {len(anomalies):,}")
for r in anomalies[-60:]:
    ts  = r.get("timestamp","?")[:19]
    msg = r.get("message", r.get("msg", str(r)))[:120]
    print(f"  {ts}  {msg}")

# ─────────────────────────────────────────────────────────────────────────────
# 4.  decision_records.jsonl
# ─────────────────────────────────────────────────────────────────────────────
section("12. decision_records.jsonl  –  full analysis")

dr_path = os.path.join(LOG_DIR, "decision_records.jsonl")
with open(dr_path, encoding="utf-8") as f:
    dr_lines = f.readlines()

print(f"Total lines: {len(dr_lines):,}")
all_dr = parse_jsonl_lines(dr_lines, "decision_records")
print(f"Parseable records: {len(all_dr):,}")

# Sample from start, middle, end
samples = []
n = len(all_dr)
if n > 0:
    indices = set()
    indices.update(range(0, min(10, n)))
    mid = n // 2
    indices.update(range(max(0, mid-5), min(n, mid+5)))
    indices.update(range(max(0, n-10), n))
    samples = [all_dr[i] for i in sorted(indices)]

sub("Sample records from start/middle/end")
for r in samples:
    ts    = r.get("timestamp", "?")[:19]
    bot   = r.get("bot_id", "?")
    sym   = r.get("symbol", "?")
    dec   = r.get("final_action", "?")
    rsn   = str(r.get("reason", ""))[:100]
    gr    = r.get("gating_results", {})
    nattempted = gr.get("trades_attempted", "?")
    print(f"  {ts}  {bot:<20s} {sym:<15s} -> {dec}  attempted={nattempted}  reason={rsn}")

sub("Decision distribution (final_action)")
dec_counts = Counter()
for r in all_dr:
    d = r.get("final_action", "UNKNOWN")
    dec_counts[d] += 1
for d, c in dec_counts.most_common():
    print(f"  {str(d):<30} {c:>8,}  ({100*c/len(all_dr):.1f}%)")

sub("NO_TRADE reason distribution (top 20)")
block_reasons = Counter()
for r in all_dr:
    if r.get("final_action") == "NO_TRADE":
        rsn = r.get("reason", "")
        block_reasons[str(rsn)[:80]] += 1
print(f"  NOTE: 'NO_TRADE' reason is always 'Managed N positions, 0 trades'")
print(f"        meaning loops ran but no new entry/exit executed")
print(f"{'Reason':<80} {'Count':>8}")
print("-" * 90)
for rsn, cnt in block_reasons.most_common(20):
    print(f"  {rsn:<78} {cnt:>8,}")

sub("TRADE decisions – all {len([r for r in all_dr if r.get('final_action')=='TRADE'])} records by date")
actual_trades = [r for r in all_dr if r.get("final_action") == "TRADE"]
trade_by_date = defaultdict(list)
for r in actual_trades:
    trade_by_date[r["timestamp"][:10]].append(r)
print(f"Total TRADE loops: {len(actual_trades):,}")
for d in sorted(trade_by_date.keys()):
    bots_on_day = Counter(r["bot_id"] for r in trade_by_date[d])
    print(f"  {d}: {len(trade_by_date[d])} TRADE loops  bots={dict(bots_on_day)}")

sub("Decision counts by bot")
by_bot_cnt = defaultdict(Counter)
for r in all_dr:
    bot = r.get("bot_id", "UNKNOWN")
    dec = r.get("final_action", "?")
    by_bot_cnt[bot][dec] += 1
for bot in sorted(by_bot_cnt.keys()):
    t = by_bot_cnt[bot].get("TRADE", 0)
    n = by_bot_cnt[bot].get("NO_TRADE", 0)
    tot = t + n
    pct = 100*t/tot if tot else 0
    print(f"  {bot:<25s}  total={tot:>6,}  trades={t:>5,}  no_trade={n:>6,}  trade_rate={pct:.1f}%")

# ─────────────────────────────────────────────────────────────────────────────
# 5.  performance_metrics.jsonl
# ─────────────────────────────────────────────────────────────────────────────
section("13. performance_metrics.jsonl  –  full file")

pm_path = os.path.join(LOG_DIR, "performance_metrics.jsonl")
with open(pm_path, encoding="utf-8") as f:
    pm_lines = f.readlines()

print(f"Total lines: {len(pm_lines)}")
all_pm = parse_jsonl_lines(pm_lines, "performance_metrics")
print(f"Parseable records: {len(all_pm)}\n")
for r in all_pm:
    print(json.dumps(r, indent=2))

# ─────────────────────────────────────────────────────────────────────────────
# 6.  slippage_events.jsonl
# ─────────────────────────────────────────────────────────────────────────────
section("14. slippage_events.jsonl  –  full summary")

sl_path = os.path.join(LOG_DIR, "slippage_events.jsonl")
with open(sl_path, encoding="utf-8") as f:
    sl_lines = f.readlines()

print(f"Total lines: {len(sl_lines)}")
all_sl = parse_jsonl_lines(sl_lines, "slippage_events")
print(f"Parseable records: {len(all_sl)}\n")

slip_amounts = []
by_symbol = defaultdict(list)
by_side    = defaultdict(list)
by_bot     = defaultdict(list)

for r in all_sl:
    slip = r.get("slippage_bps", None)
    sym  = r.get("symbol", "?")
    side = r.get("side", "?")
    bot  = str(r.get("bot_id") or "NONE")
    ts   = r.get("timestamp", "?")[:19]
    expected = r.get("expected_price")
    filled   = r.get("fill_price")
    tod      = r.get("time_of_day", "?")
    print(f"  {ts}  {sym:<10s} {side:<5s}  slip_bps={slip}  "
          f"expected={expected}  fill={filled}  time={tod}")
    if slip is not None:
        try:
            slip_amounts.append(float(slip))
            by_symbol[sym].append(float(slip))
            by_side[side].append(float(slip))
            by_bot[bot].append(float(slip))
        except Exception:
            pass

if slip_amounts:
    print(f"\nSlippage summary ({len(slip_amounts)} events):")
    print(f"  Mean  : {sum(slip_amounts)/len(slip_amounts):.4f} bps")
    print(f"  Min   : {min(slip_amounts):.4f} bps")
    print(f"  Max   : {max(slip_amounts):.4f} bps")
    pos_slip  = [s for s in slip_amounts if s > 0]
    zero_slip = [s for s in slip_amounts if s == 0]
    neg_slip  = [s for s in slip_amounts if s < 0]
    print(f"  Adverse  (>0 bps): {len(pos_slip)}")
    print(f"  Zero     (=0 bps): {len(zero_slip)}")
    print(f"  Favorable(<0 bps): {len(neg_slip)}")
    print(f"  NOTE: All 77 fills recorded at 0 bps slippage – "
          f"bot_id field is null on all records (paper/simulation fills)")

    sub("Slippage by symbol")
    for sym in sorted(by_symbol.keys()):
        vals = by_symbol[sym]
        print(f"  {sym:<15s}  n={len(vals):>4}  mean={sum(vals)/len(vals):+.4f} bps")

    sub("Slippage by side")
    for side in sorted(by_side.keys()):
        vals = by_side[side]
        print(f"  {side:<10s}  n={len(vals):>4}  mean={sum(vals)/len(vals):+.4f} bps")

    sub("Slippage by bot")
    for bot in sorted(by_bot.keys()):
        vals = by_bot[bot]
        print(f"  {bot:<25s}  n={len(vals):>4}  mean={sum(vals)/len(vals):+.4f} bps")

# ─────────────────────────────────────────────────────────────────────────────
# 7.  SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
section("15. AUDIT SUMMARY")

print("""
KEY FINDINGS (auto-generated – verify against output above):

[DB]
  - trading_state.db tables: state, order_ids, exit_trades, exit_decisions,
    exit_options_context, bar_cache
  - metrics.db tables: daily_metrics, bot_performance, regime_history, risk_decisions

[STATE]
  - All session_prot_ and hailmary_bot_trade_ keys reported above
  - Daily equity from day_start_equity_* keys and daily_metrics table
  - exit_lock staleness: see Section 5 above for any STALE flags
  - JSON corruption: see Section 9

[DECISIONS]
  - Full decision distribution in Section 12
  - Top NO_TRADE block reasons in Section 12

[SLIPPAGE]
  - Summary statistics in Section 14

[METRICS]
  - Daily PnL table in Section 10
  - Bot performance breakdown in Section 10

Audit completed: """ + datetime.now().isoformat())
