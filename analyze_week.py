#!/usr/bin/env python3
"""
Weekly Trading Log Analyzer
Analyzes all trading logs for the full week across all bots.
"""

import json
import gzip
import os
import glob
from collections import defaultdict, Counter
from datetime import datetime, timezone
import re

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR = "C:/Users/admin1/Downloads/export (1)/export/logs"
ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")

# Archive files in chronological order (by file start time)
ARCHIVE_FILES = [
    # Feb 11-12 session
    "app_20260211_225523.jsonl.gz",   # 2026-02-11 21:46 → 22:55
    "app_20260212_155342.jsonl.gz",   # 2026-02-11 22:55 → 2026-02-12 15:53
    # Feb 17 session
    "app_20260226_202640.jsonl.gz",   # 2026-02-17 14:55 → 15:31
    "app_20260217_145547.jsonl.gz",   # 2026-02-12 15:53 → 2026-02-17 14:55
    # Feb 20 session
    "app_20260226_202640_50180.jsonl.gz",  # 2026-02-20 18:16 → 18:39
    "app_20260226_202639.jsonl.gz",        # 2026-02-20 18:39 → 19:31
    "app_20260226_202639_50180.jsonl.gz",  # 2026-02-20 19:31 → 19:52
    # Feb 26-27 session
    "app_20260227_154919.jsonl.gz",   # 2026-02-26 15:55 → 2026-02-27 15:49
    "app_20260227_162141.jsonl.gz",   # 2026-02-27 15:49 → 16:21
    "app_20260227_165406.jsonl.gz",   # 2026-02-27 16:21 → 16:54
    "app_20260227_172242.jsonl.gz",   # 2026-02-27 16:54 → 17:22
    "app_20260227_180011.jsonl.gz",   # 2026-02-27 17:22 → 18:00
]

# Plain log files (oldest to newest)
PLAIN_LOGS = [
    "app.jsonl.7",   # 2026-02-27 18:00 → 18:43
    "app.jsonl.6",   # 2026-02-27 18:43 → 19:16
    "app.jsonl.5",   # 2026-02-27 19:16 → 20:23
    "app.jsonl.4",   # 2026-02-27 20:23 → 21:55
    "app.jsonl.3",   # 2026-02-27 21:55 → 2026-02-28 00:27
    "app.jsonl.2",   # 2026-02-28 00:27 → 02:44
    "app.jsonl.1",   # 2026-02-28 02:44 → 04:54
    "app.jsonl",     # 2026-02-28 04:54 → 05:29
]


# ─────────────────────────────────────────────────────────────────────────────
# Log Reader
# ─────────────────────────────────────────────────────────────────────────────

def iter_events(filepath, is_gz=False):
    """Yield parsed JSON objects from a log file."""
    opener = gzip.open if is_gz else open
    try:
        with opener(filepath, 'rt', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        pass
    except Exception as e:
        print(f"  [WARN] Could not read {filepath}: {e}")


def iter_all_events():
    """Yield all events from all log files in chronological order."""
    for fname in ARCHIVE_FILES:
        fpath = os.path.join(ARCHIVE_DIR, fname)
        if os.path.exists(fpath):
            yield from iter_events(fpath, is_gz=True)
        else:
            print(f"  [WARN] Archive not found: {fpath}")

    for fname in PLAIN_LOGS:
        fpath = os.path.join(BASE_DIR, fname)
        if os.path.exists(fpath):
            yield from iter_events(fpath, is_gz=False)
        else:
            print(f"  [WARN] Plain log not found: {fpath}")


# ─────────────────────────────────────────────────────────────────────────────
# Data Collectors
# ─────────────────────────────────────────────────────────────────────────────

def parse_ts(ts_str):
    """Parse an ISO timestamp string into a datetime."""
    if not ts_str:
        return None
    try:
        # Remove trailing Z and parse
        ts_str = ts_str.rstrip('Z')
        return datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def local_date(ts_str):
    """Return the UTC date string (YYYY-MM-DD) from a timestamp."""
    dt = parse_ts(ts_str)
    if dt is None:
        return "unknown"
    return dt.strftime("%Y-%m-%d")


def local_time(ts_str):
    """Return HH:MM:SS from a timestamp."""
    dt = parse_ts(ts_str)
    if dt is None:
        return "?"
    return dt.strftime("%H:%M:%S")


def identify_bot(event, obj):
    """Determine which bot generated this event."""
    bot_id = obj.get("bot_id", "")
    if bot_id and bot_id not in ("manual",):
        if "hailmary" in bot_id.lower():
            return "hailmary"
        if "twentymin" in bot_id.lower():
            return "twentymin"
        if "options" in bot_id.lower():
            return "options"
        if "crypto" in bot_id.lower():
            return "crypto"
        if "exit" in bot_id.lower():
            return "exitbot"

    if "hailmary" in event.lower():
        return "hailmary"
    if "twentymin" in event.lower():
        return "twentymin"
    if "options" in event.lower() and "exit" not in event.lower():
        return "options"
    if "crypto" in event.lower() and "exit" not in event.lower():
        return "crypto"
    if "exit" in event.lower():
        return "exitbot"

    # Infer from symbol
    symbol = obj.get("symbol", "") or obj.get("contract", "")
    if symbol:
        # Options symbol: ends with date+C/P+strike (e.g., AAPL260302P00267500)
        if re.match(r'^[A-Z]+\d{6}[CP]\d+$', symbol):
            return "twentymin"

    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Main Analysis
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("  WEEKLY TRADING LOG ANALYSIS")
    print("  Processing all logs from 2026-02-11 through 2026-02-28")
    print("=" * 80)
    print()

    # --- Data structures ---
    # Trade exits: list of dicts per (date, bot)
    # date -> bot -> [trade dict]
    trades_by_date_bot = defaultdict(lambda: defaultdict(list))

    # Trade entries: date -> bot -> [entry dict]
    entries_by_date_bot = defaultdict(lambda: defaultdict(list))

    # Equity curve: date -> list of (ts, equity, pnl, day_start)
    equity_by_date = defaultdict(list)

    # HailMary daily activity: date -> {events}
    hailmary_by_date = defaultdict(lambda: {
        "scans": 0,
        "opportunities_found": 0,
        "top_scores": [],
        "trades_attempted": 0,
        "trades_placed": 0,
        "blocked_exitbot": 0,
        "blocked_killswitch": 0,
        "no_opportunities": 0,
    })

    # TwentyMin trade timing: date -> [time_str]
    twentymin_entry_times = defaultdict(list)

    # Crypto closed positions
    crypto_closed = []

    # All raw trade exit events
    all_exits = []
    all_entries = []

    # Tracking for exitbot start/end per day
    exitbot_day_start = {}   # date -> first equity of that day
    exitbot_day_end = {}     # date -> last equity of that day

    print("Reading all log files...")
    total_events = 0
    dates_seen = set()

    for obj in iter_all_events():
        total_events += 1
        event = obj.get("event", "")
        ts = obj.get("ts", "")
        date = local_date(ts)
        dates_seen.add(date)

        # ── EQUITY CURVE (exitbot_start / exitbot_ok) ──────────────────────
        if event == "exitbot_start":
            equity = obj.get("equity")
            day_start_eq = obj.get("day_start")
            if equity is not None:
                equity_by_date[date].append({
                    "ts": ts, "type": "start",
                    "equity": equity,
                    "day_start": day_start_eq
                })
                if date not in exitbot_day_start:
                    exitbot_day_start[date] = equity
                    if day_start_eq is not None:
                        exitbot_day_start[date] = day_start_eq

        elif event == "exitbot_ok":
            equity = obj.get("equity")
            pnl = obj.get("pnl", 0)
            if equity is not None:
                equity_by_date[date].append({
                    "ts": ts, "type": "ok",
                    "equity": equity,
                    "pnl": pnl
                })
                exitbot_day_end[date] = equity

        # ── TRADE EXITS ────────────────────────────────────────────────────
        elif event == "TRADE_EXIT":
            symbol = obj.get("symbol", "")
            pnl = obj.get("pnl", 0) or 0
            pnl_pct = obj.get("pnl_percent", 0) or 0
            reason = obj.get("reason", "")
            qty = obj.get("qty", 0) or 0
            entry_price = obj.get("entry_price", 0) or 0
            exit_price = obj.get("exit_price", 0) or 0

            # Determine bot from symbol and context
            if re.match(r'^[A-Z]+\d{6}[CP]\d+$', symbol):
                bot = "twentymin"
            else:
                bot = "unknown"

            trade = {
                "ts": ts,
                "date": date,
                "symbol": symbol,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "reason": reason,
                "qty": qty,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "bot": bot,
            }
            trades_by_date_bot[date][bot].append(trade)
            all_exits.append(trade)

        elif event == "crypto_position_closed":
            symbol = obj.get("symbol", "")
            pnl = obj.get("pnl_dollars", 0) or 0
            pnl_pct = obj.get("pnl_pct", 0) or 0
            reason = obj.get("reason", "")
            qty = obj.get("qty", 0) or 0

            trade = {
                "ts": ts,
                "date": date,
                "symbol": symbol,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "reason": reason,
                "qty": qty,
                "bot": "crypto",
            }
            trades_by_date_bot[date]["crypto"].append(trade)
            all_exits.append(trade)
            crypto_closed.append(trade)

        # ── TRADE ENTRIES ──────────────────────────────────────────────────
        elif event == "options_bracket_entry_placed":
            symbol = obj.get("symbol", "")
            entry_price = obj.get("entry_price", 0) or 0
            qty = obj.get("qty", 0) or 0
            stop = obj.get("stop_loss", 0)
            tp = obj.get("take_profit", 0)

            entry = {
                "ts": ts,
                "date": date,
                "symbol": symbol,
                "entry_price": entry_price,
                "qty": qty,
                "stop": stop,
                "tp": tp,
                "bot": "twentymin",
            }
            entries_by_date_bot[date]["twentymin"].append(entry)
            all_entries.append(entry)
            twentymin_entry_times[date].append(local_time(ts))

        elif event == "twentymin_options_entry_success":
            # May be duplicate with bracket_entry_placed - just track timing
            pass

        elif event == "validated_order_placed":
            bot_id = obj.get("bot_id", "")
            symbol = obj.get("symbol", "")
            side = obj.get("side", "")
            qty = obj.get("qty", 0)

            if "crypto" in bot_id.lower():
                entry = {
                    "ts": ts,
                    "date": date,
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "bot": "crypto",
                }
                entries_by_date_bot[date]["crypto"].append(entry)

        # ── HAILMARY ACTIVITY ──────────────────────────────────────────────
        elif event == "hailmary_scan_start":
            hailmary_by_date[date]["scans"] += 1

        elif event == "hailmary_no_opportunities":
            hailmary_by_date[date]["no_opportunities"] += 1

        elif event == "hailmary_opportunities_found":
            hm = hailmary_by_date[date]
            hm["opportunities_found"] += 1
            score = obj.get("top_score", 0)
            sym = obj.get("top_symbol", "")
            hm["top_scores"].append((score, sym))
            hm["top_3_last"] = obj.get("top_3", [])

        elif event == "hailmary_trade_failed":
            hailmary_by_date[date]["trades_attempted"] += 1
            err = obj.get("error", "")
            if "exitbot" in err.lower():
                hailmary_by_date[date]["blocked_exitbot"] += 1
            elif "killswitch" in err.lower():
                hailmary_by_date[date]["blocked_killswitch"] += 1

        elif event == "hailmary_bot_killswitch_blocked":
            hailmary_by_date[date]["blocked_killswitch"] += 1

    print(f"Total events processed: {total_events:,}")
    print(f"Dates seen: {sorted(dates_seen)}")
    print()

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 1: EQUITY CURVE
    # ─────────────────────────────────────────────────────────────────────────

    print("=" * 80)
    print("  SECTION 1: EQUITY CURVE (Start-of-Day vs End-of-Day)")
    print("=" * 80)
    print()

    all_dates_with_equity = sorted(
        d for d in equity_by_date.keys() if d != "unknown"
    )

    equity_table = []
    prev_end = None
    for date in all_dates_with_equity:
        day_data = equity_by_date[date]
        # Get day_start (from exitbot_start events that include day_start field)
        day_starts = [e["day_start"] for e in day_data if e["type"] == "start" and e.get("day_start") is not None]
        equities = [e["equity"] for e in day_data]
        ok_equities = [e["equity"] for e in day_data if e["type"] == "ok"]

        sod = day_starts[0] if day_starts else (equities[0] if equities else None)
        eod = ok_equities[-1] if ok_equities else (equities[-1] if equities else None)

        if sod is not None and eod is not None:
            day_pnl = eod - sod
            day_pnl_pct = (day_pnl / sod * 100) if sod else 0
            equity_table.append((date, sod, eod, day_pnl, day_pnl_pct))
            print(f"  {date}  SOD: ${sod:>12,.2f}  EOD: ${eod:>12,.2f}  "
                  f"Day P&L: ${day_pnl:>+9,.2f}  ({day_pnl_pct:>+.2f}%)")
        elif sod is not None:
            print(f"  {date}  SOD: ${sod:>12,.2f}  EOD: N/A (session still running or no ok events)")
        elif equities:
            print(f"  {date}  Equity range: ${min(equities):,.2f} - ${max(equities):,.2f}")

    print()
    if equity_table:
        total_start = equity_table[0][1]
        total_end = equity_table[-1][2]
        total_chg = total_end - total_start
        total_pct = (total_chg / total_start * 100) if total_start else 0
        print(f"  Week Summary: Started at ${total_start:,.2f}  ->  Ended at ${total_end:,.2f}")
        print(f"  Net Week P&L: ${total_chg:>+,.2f}  ({total_pct:>+.2f}%)")

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 2: TRADE EXITS BY DATE AND BOT
    # ─────────────────────────────────────────────────────────────────────────

    print()
    print("=" * 80)
    print("  SECTION 2: TRADE EXITS BY DATE AND BOT")
    print("=" * 80)

    all_trade_dates = sorted(trades_by_date_bot.keys())
    grand_total_pnl = 0.0
    grand_total_trades = 0

    for date in all_trade_dates:
        print(f"\n  ─── {date} ─────────────────────────────")
        bots = trades_by_date_bot[date]
        for bot in sorted(bots.keys()):
            trades = bots[bot]
            if not trades:
                continue
            pnls = [t["pnl"] for t in trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            total_pnl = sum(pnls)
            win_rate = len(wins) / len(trades) * 100 if trades else 0
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(losses) / len(losses) if losses else 0
            grand_total_pnl += total_pnl
            grand_total_trades += len(trades)

            print(f"\n    Bot: {bot.upper()}")
            print(f"      Trades:     {len(trades)}")
            print(f"      Winners:    {len(wins)}  Losers: {len(losses)}")
            print(f"      Win Rate:   {win_rate:.1f}%")
            print(f"      Total PnL:  ${total_pnl:>+,.2f}")
            print(f"      Avg Win:    ${avg_win:>+,.2f}")
            print(f"      Avg Loss:   ${avg_loss:>+,.2f}")

            if trades:
                best = max(trades, key=lambda x: x["pnl"])
                worst = min(trades, key=lambda x: x["pnl"])
                print(f"      Best:       {best['symbol']}  ${best['pnl']:>+,.2f}"
                      f"  ({best.get('pnl_pct', 0):>+.1f}%)  reason={best.get('reason', '?')}")
                print(f"      Worst:      {worst['symbol']}  ${worst['pnl']:>+,.2f}"
                      f"  ({worst.get('pnl_pct', 0):>+.1f}%)  reason={worst.get('reason', '?')}")

    print()
    print(f"  GRAND TOTAL: {grand_total_trades} trades exited, Net PnL = ${grand_total_pnl:>+,.2f}")

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 3: TRADE ENTRIES BY DATE AND BOT
    # ─────────────────────────────────────────────────────────────────────────

    print()
    print("=" * 80)
    print("  SECTION 3: TRADE ENTRIES BY DATE AND BOT")
    print("=" * 80)

    all_entry_dates = sorted(entries_by_date_bot.keys())

    for date in all_entry_dates:
        print(f"\n  ─── {date} ─────────────────────────────")
        bots = entries_by_date_bot[date]
        for bot in sorted(bots.keys()):
            entries = bots[bot]
            print(f"\n    Bot: {bot.upper()}  ({len(entries)} entries)")
            for e in entries[:10]:
                sym = e.get("symbol", "?")
                ep = e.get("entry_price", 0)
                qty = e.get("qty", 0)
                t = local_time(e["ts"])
                print(f"      {t} UTC  {sym}  qty={qty}  entry=${ep:.3f}")
            if len(entries) > 10:
                print(f"      ... and {len(entries) - 10} more")

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 4: BEST AND WORST TRADES OVERALL
    # ─────────────────────────────────────────────────────────────────────────

    print()
    print("=" * 80)
    print("  SECTION 4: BEST AND WORST TRADES (ALL BOTS, ALL DAYS)")
    print("=" * 80)
    print()

    if all_exits:
        sorted_by_pnl = sorted(all_exits, key=lambda x: x["pnl"], reverse=True)

        print("  TOP 10 BEST TRADES:")
        for i, t in enumerate(sorted_by_pnl[:10], 1):
            pct_str = f"  {t.get('pnl_pct', 0):>+.1f}%" if t.get('pnl_pct') else ""
            print(f"    {i:2}. {t['date']}  {t['bot'].upper():10}  {t['symbol']:<35}  "
                  f"${t['pnl']:>+9,.2f}{pct_str}  reason={t.get('reason', '?')}")

        print()
        print("  TOP 10 WORST TRADES:")
        for i, t in enumerate(sorted_by_pnl[-10:][::-1], 1):
            pct_str = f"  {t.get('pnl_pct', 0):>+.1f}%" if t.get('pnl_pct') else ""
            print(f"    {i:2}. {t['date']}  {t['bot'].upper():10}  {t['symbol']:<35}  "
                  f"${t['pnl']:>+9,.2f}{pct_str}  reason={t.get('reason', '?')}")
    else:
        print("  No trade exits found.")

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 5: HAILMARY DAILY ACTIVITY
    # ─────────────────────────────────────────────────────────────────────────

    print()
    print("=" * 80)
    print("  SECTION 5: HAILMARY BOT DAILY ACTIVITY")
    print("=" * 80)
    print()

    hm_dates = sorted(hailmary_by_date.keys())
    if not hm_dates:
        print("  No HailMary events found in any log.")
    else:
        for date in hm_dates:
            hm = hailmary_by_date[date]
            scans = hm["scans"]
            no_opps = hm["no_opportunities"]
            opps_found = hm["opportunities_found"]
            attempted = hm["trades_attempted"]
            blocked_exit = hm["blocked_exitbot"]
            blocked_ks = hm["blocked_killswitch"]

            print(f"  {date}:")
            print(f"    Scans run:            {scans}")
            print(f"    No-opportunity scans: {no_opps}")
            print(f"    Opportunities found:  {opps_found} scans found candidates")

            if hm["top_scores"]:
                best_score, best_sym = max(hm["top_scores"], key=lambda x: x[0])
                print(f"    Best score seen:      {best_score:.3f}  ({best_sym})")

            if attempted > 0:
                print(f"    Trades attempted:     {attempted}")
                print(f"    Blocked by ExitBot:   {blocked_exit}")
                print(f"    Blocked by Killswitch:{blocked_ks}")
            else:
                print(f"    Trades placed:        0  (no executions)")

            if "top_3_last" in hm and hm["top_3_last"]:
                print(f"    Last top-3 candidates:")
                for cand in hm["top_3_last"]:
                    print(f"      {cand.get('symbol','?'):<35}  score={cand.get('score',0):.3f}  "
                          f"mid=${cand.get('mid',0):.2f}  spread={cand.get('spread',0):.2f}")
            print()

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 6: TWENTYMIN TRADE TIMING
    # ─────────────────────────────────────────────────────────────────────────

    print()
    print("=" * 80)
    print("  SECTION 6: TWENTYMIN BOT TRADE TIMING")
    print("=" * 80)
    print()

    if not twentymin_entry_times:
        print("  No TwentyMin entries found.")
    else:
        for date in sorted(twentymin_entry_times.keys()):
            times = sorted(twentymin_entry_times[date])
            entries = entries_by_date_bot[date].get("twentymin", [])
            print(f"  {date}:  {len(times)} entries")
            for i, (t, e) in enumerate(
                zip(times, sorted(entries, key=lambda x: x["ts"])), 1
            ):
                sym = e.get("symbol", "?")
                ep = e.get("entry_price", 0)
                qty = e.get("qty", 0)
                print(f"    {i:3}. {t} UTC  {sym}  qty={qty}  ${ep:.3f}")
            print()

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 7: PER-BOT WEEKLY SUMMARY
    # ─────────────────────────────────────────────────────────────────────────

    print()
    print("=" * 80)
    print("  SECTION 7: PER-BOT WEEKLY PERFORMANCE SUMMARY")
    print("=" * 80)
    print()

    all_bots = set()
    for date_bots in trades_by_date_bot.values():
        all_bots.update(date_bots.keys())

    for bot in sorted(all_bots):
        all_bot_trades = []
        for date in all_trade_dates:
            all_bot_trades.extend(trades_by_date_bot[date].get(bot, []))

        if not all_bot_trades:
            continue

        pnls = [t["pnl"] for t in all_bot_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        total_pnl = sum(pnls)
        win_rate = len(wins) / len(pnls) * 100 if pnls else 0

        print(f"  Bot: {bot.upper()}")
        print(f"    Total exits:     {len(pnls)}")
        print(f"    Winners:         {len(wins)}  ({win_rate:.1f}% win rate)")
        print(f"    Losers:          {len(losses)}")
        print(f"    Total PnL:       ${total_pnl:>+,.2f}")
        print(f"    Avg Win:         ${(sum(wins)/len(wins)):>+,.2f}" if wins else "    Avg Win:         N/A")
        print(f"    Avg Loss:        ${(sum(losses)/len(losses)):>+,.2f}" if losses else "    Avg Loss:        N/A")
        print(f"    Best trade:      ${max(pnls):>+,.2f}")
        print(f"    Worst trade:     ${min(pnls):>+,.2f}")
        print()

    # Entry-only bots (no exits recorded)
    for date_bots in entries_by_date_bot.values():
        for bot in date_bots.keys():
            if bot not in all_bots:
                all_bots.add(bot)

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 8: CRYPTO POSITION DETAIL
    # ─────────────────────────────────────────────────────────────────────────

    print()
    print("=" * 80)
    print("  SECTION 8: CRYPTO BOT CLOSED POSITIONS DETAIL")
    print("=" * 80)
    print()

    if not crypto_closed:
        print("  No crypto_position_closed events found.")
    else:
        for t in sorted(crypto_closed, key=lambda x: x["ts"]):
            print(f"  {t['date']}  {local_time(t['ts'])} UTC  "
                  f"{t['symbol']:<12}  qty={t['qty']:<15.6f}  "
                  f"pnl=${t['pnl']:>+9.2f}  ({t.get('pnl_pct', 0):>+.3f}%)  "
                  f"reason={t.get('reason', '?')}")

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 9: TWENTYMIN DETAILED BREAKDOWN
    # ─────────────────────────────────────────────────────────────────────────

    print()
    print("=" * 80)
    print("  SECTION 9: TWENTYMIN EXITS DETAIL")
    print("=" * 80)
    print()

    twentymin_exits = [t for t in all_exits if t["bot"] == "twentymin"]
    if not twentymin_exits:
        print("  No TwentyMin exits recorded in logs (positions may still be open or exits")
        print("  happened in sessions not yet captured with TRADE_EXIT events).")
    else:
        by_date = defaultdict(list)
        for t in twentymin_exits:
            by_date[t["date"]].append(t)
        for date in sorted(by_date.keys()):
            trades = sorted(by_date[date], key=lambda x: x["ts"])
            pnls = [t["pnl"] for t in trades]
            print(f"  {date}: {len(trades)} exits, net PnL=${sum(pnls):>+,.2f}")
            for t in trades:
                print(f"    {local_time(t['ts'])} UTC  {t['symbol']:<35}  "
                      f"${t['pnl']:>+9,.2f}  ({t.get('pnl_pct', 0):>+.1f}%)  "
                      f"{t.get('reason', '?')}")

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 10: WHAT WAS HAPPENING ON EACH TRADING DAY
    # ─────────────────────────────────────────────────────────────────────────

    print()
    print("=" * 80)
    print("  SECTION 10: NARRATIVE - WHAT HAPPENED EACH TRADING DAY")
    print("=" * 80)
    print()

    # Build a set of all meaningful dates
    meaningful_dates = set()
    meaningful_dates.update(all_trade_dates)
    meaningful_dates.update(all_entry_dates)
    meaningful_dates.update(hm_dates)
    meaningful_dates.update(twentymin_entry_times.keys())
    for date in equity_by_date.keys():
        meaningful_dates.add(date)
    meaningful_dates.discard("unknown")

    for date in sorted(meaningful_dates):
        print(f"  ═══ {date} ═══════════════════════════════════════════")

        # Equity
        sod = exitbot_day_start.get(date)
        eod = exitbot_day_end.get(date)
        if sod and eod:
            chg = eod - sod
            pct = chg / sod * 100 if sod else 0
            print(f"    Equity: ${sod:,.2f} → ${eod:,.2f}  ({chg:>+,.2f} / {pct:>+.2f}%)")
        elif sod:
            print(f"    Equity SOD: ${sod:,.2f}  (no EOD recorded)")
        else:
            print(f"    Equity: No exitbot data for this date")

        # Entries
        entries = entries_by_date_bot.get(date, {})
        total_entries = sum(len(v) for v in entries.values())
        if total_entries:
            print(f"    Entries: {total_entries} total")
            for bot, elist in sorted(entries.items()):
                print(f"      {bot.upper()}: {len(elist)} entries")

        # Exits
        exits = trades_by_date_bot.get(date, {})
        total_exits = sum(len(v) for v in exits.values())
        if total_exits:
            all_day_pnl = sum(t["pnl"] for bots in exits.values() for t in bots)
            print(f"    Exits: {total_exits} total, net PnL=${all_day_pnl:>+,.2f}")
            for bot, elist in sorted(exits.items()):
                bot_pnl = sum(t["pnl"] for t in elist)
                print(f"      {bot.upper()}: {len(elist)} exits, PnL=${bot_pnl:>+,.2f}")

        # HailMary
        if date in hailmary_by_date:
            hm = hailmary_by_date[date]
            scans = hm["scans"]
            opps = hm["opportunities_found"]
            attempted = hm["trades_attempted"]
            if scans == 0:
                print(f"    HailMary: No scans (market closed or bot not running)")
            elif attempted > 0:
                print(f"    HailMary: {scans} scans, {opps} found candidates, "
                      f"{attempted} attempted (all blocked)")
            elif opps > 0:
                print(f"    HailMary: {scans} scans, {opps} found candidates, no trades placed")
            else:
                print(f"    HailMary: {scans} scans, no opportunities found")
        else:
            print(f"    HailMary: No HailMary activity recorded")

        print()

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 11: OPTIONS BOT ACTIVITY
    # ─────────────────────────────────────────────────────────────────────────

    print()
    print("=" * 80)
    print("  SECTION 11: OPTIONS BOT (enhanced_options_bot) ACTIVITY SUMMARY")
    print("=" * 80)
    print()

    # Gather options bot completion events from archives
    options_completions = []

    for fname in ARCHIVE_FILES:
        fpath = os.path.join(ARCHIVE_DIR, fname)
        if not os.path.exists(fpath):
            continue
        with gzip.open(fpath, 'rt', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        obj = json.loads(line)
                        if obj.get("event") == "enhanced_options_bot_execution_complete":
                            options_completions.append(obj)
                    except:
                        pass

    for fname in PLAIN_LOGS:
        fpath = os.path.join(BASE_DIR, fname)
        if not os.path.exists(fpath):
            continue
        with open(fpath, 'rt', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        obj = json.loads(line)
                        if obj.get("event") == "enhanced_options_bot_execution_complete":
                            options_completions.append(obj)
                    except:
                        pass

    # Aggregate by date
    options_by_date = defaultdict(list)
    for obj in options_completions:
        options_by_date[local_date(obj["ts"])].append(obj)

    for date in sorted(options_by_date.keys()):
        completions = options_by_date[date]
        total_traded = sum(c.get("trades_made", 0) or 0 for c in completions)
        total_scanned = sum(c.get("candidates_scanned", 0) or 0 for c in completions)
        runs = len(completions)
        print(f"  {date}: {runs} runs, {total_traded} trades placed, {total_scanned} candidates scanned")

    if not options_by_date:
        print("  No enhanced_options_bot completion events found.")

    print()
    print("=" * 80)
    print("  ANALYSIS COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
