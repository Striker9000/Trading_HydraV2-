#!/usr/bin/env python3
"""
TwentyMinuteBot Parameter Sweep Optimizer
============================================
Performs grid search optimization for the TwentyMinuteBot gap-and-go strategy.
Tests parameter combinations against real Alpaca market data and outputs
optimal configs ranked by composite score.

Usage:
    python export/scripts/sweep_twentyminute.py --days 90
"""

import os
import sys
import json
import random
import argparse
import itertools
import statistics
from datetime import datetime, timedelta
from typing import Dict, List, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.trading_hydra.backtest.backtest_engine import BacktestEngine

INITIAL_CAPITAL = 50000.0
MAX_COMBOS = 500

SYMBOLS = ["AAPL", "NVDA", "TSLA", "AMD", "SPY", "QQQ", "META", "AMZN"]

PARAM_GRID = {
    "min_gap_pct": [0.25, 0.50, 0.75, 1.0, 1.5, 2.0],
    "take_profit_pct": [1.0, 2.0, 3.0, 5.0, 8.0],
    "stop_loss_pct": [1.0, 2.0, 3.0, 5.0],
    "options_leverage": [1.0, 5.0, 10.0, 20.0],
    "confirmation_bars": [1, 2, 3],
    "max_trades_per_day": [3, 5, 6, 8],
}


def simulate_twentyminute(bars_by_symbol: Dict[str, List[Dict]], config: Dict) -> List[Dict]:
    min_gap_pct = config["min_gap_pct"]
    take_profit_pct = config["take_profit_pct"] / 100
    stop_loss_pct = config["stop_loss_pct"] / 100
    leverage = config["options_leverage"]
    confirmation_bars = config["confirmation_bars"]
    max_trades_day = config["max_trades_per_day"]

    position_size = INITIAL_CAPITAL * 0.04

    trades = []

    for symbol, bars in bars_by_symbol.items():
        if len(bars) < confirmation_bars + 2:
            continue

        daily_trade_counts = {}

        for i in range(confirmation_bars, len(bars)):
            bar = bars[i]
            prev_bar = bars[i - 1]

            prev_close = prev_bar["close"]
            cur_open = bar["open"]
            cur_high = bar["high"]
            cur_low = bar["low"]
            cur_close = bar["close"]

            if prev_close <= 0:
                continue

            gap_pct = ((cur_open - prev_close) / prev_close) * 100

            if abs(gap_pct) < min_gap_pct:
                continue

            confirmed = True
            for cb in range(1, min(confirmation_bars, i)):
                cb_bar = bars[i - cb]
                if gap_pct > 0 and cb_bar["close"] < cb_bar["open"]:
                    confirmed = False
                    break
                elif gap_pct < 0 and cb_bar["close"] > cb_bar["open"]:
                    confirmed = False
                    break

            if not confirmed:
                continue

            day_key = str(bar.get("timestamp", ""))[:10]
            if daily_trade_counts.get(day_key, 0) >= max_trades_day:
                continue
            daily_trade_counts[day_key] = daily_trade_counts.get(day_key, 0) + 1

            entry_price = cur_open
            direction = 1 if gap_pct > 0 else -1

            tp_price = entry_price * (1 + direction * take_profit_pct)
            sl_price = entry_price * (1 - direction * stop_loss_pct)

            if direction == 1:
                if cur_high >= tp_price:
                    exit_price = tp_price
                    exit_reason = "take_profit"
                elif cur_low <= sl_price:
                    exit_price = sl_price
                    exit_reason = "stop_loss"
                else:
                    exit_price = cur_close
                    exit_reason = "eod_close"
            else:
                if cur_low <= tp_price:
                    exit_price = tp_price
                    exit_reason = "take_profit"
                elif cur_high >= sl_price:
                    exit_price = sl_price
                    exit_reason = "stop_loss"
                else:
                    exit_price = cur_close
                    exit_reason = "eod_close"

            raw_pnl_pct = direction * ((exit_price - entry_price) / entry_price)
            leveraged_pnl_pct = raw_pnl_pct * leverage

            qty = position_size / entry_price
            trade_pnl = leveraged_pnl_pct * position_size

            trades.append({
                "symbol": symbol,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl": trade_pnl,
                "pnl_pct": leveraged_pnl_pct * 100,
                "exit_reason": exit_reason,
                "gap_pct": gap_pct,
            })

    return trades


def calculate_metrics(trades: List[Dict]) -> Dict[str, float]:
    if not trades:
        return {
            "total_trades": 0, "win_rate": 0, "total_pnl": 0, "total_pnl_pct": 0,
            "profit_factor": 0, "sharpe_ratio": 0, "max_drawdown_pct": 0,
            "avg_trade_pnl": 0, "composite_score": -999,
        }

    pnls = [t["pnl"] for t in trades]
    total_pnl = sum(pnls)
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]
    win_rate = len(winners) / len(pnls)

    gross_profit = sum(winners) if winners else 0
    gross_loss = abs(sum(losers)) if losers else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (10.0 if gross_profit > 0 else 0)

    returns = [t["pnl_pct"] / 100 for t in trades if t["pnl_pct"] != 0]
    if len(returns) > 1:
        mean_r = statistics.mean(returns)
        std_r = statistics.stdev(returns)
        sharpe = (mean_r / std_r) * (252 ** 0.5) if std_r > 0 else 0
    else:
        sharpe = 0

    cumulative = 0
    peak = 0
    max_dd = 0
    for p in pnls:
        cumulative += p
        peak = max(peak, cumulative)
        dd = (peak - cumulative) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    total_pnl_pct = (total_pnl / INITIAL_CAPITAL) * 100
    total_trades = len(pnls)
    avg_trade_pnl = statistics.mean(pnls)

    pf_capped = min(profit_factor, 10)
    score = (
        (total_pnl_pct * 30) +
        (avg_trade_pnl * 0.1) +
        (pf_capped * 15) +
        (sharpe * 15) -
        (max_dd * 100 * 10) +
        (win_rate * 20) +
        (1 if total_trades > 10 else 0) * 10
    )

    return {
        "total_trades": total_trades,
        "win_rate": round(win_rate, 4),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "profit_factor": round(profit_factor, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "avg_trade_pnl": round(avg_trade_pnl, 2),
        "composite_score": round(score, 2),
    }


def main():
    parser = argparse.ArgumentParser(description="TwentyMinuteBot Parameter Sweep Optimizer")
    parser.add_argument("--days", type=int, default=600, help="Number of days to backtest")
    args = parser.parse_args()

    print("=" * 70)
    print("TWENTYMINUTEBOT PARAMETER SWEEP OPTIMIZER")
    print(f"Period: {args.days} days | Capital: ${INITIAL_CAPITAL:,.0f}")
    print(f"Symbols: {SYMBOLS}")
    print("=" * 70)

    engine = BacktestEngine(initial_capital=INITIAL_CAPITAL)

    end_date = datetime.now()
    start_date = end_date - timedelta(days=args.days)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    print(f"\nLoading historical data from {start_str} to {end_str}...")
    bars_by_symbol = {}
    for symbol in SYMBOLS:
        try:
            bars = engine.load_historical_data(symbol, start_str, end_str, "1Day")
            if bars:
                bars_by_symbol[symbol] = bars
                print(f"  {symbol}: {len(bars)} bars loaded")
            else:
                print(f"  {symbol}: No data available")
        except Exception as e:
            print(f"  {symbol}: Error loading data - {e}")

    if not bars_by_symbol:
        print("ERROR: No data loaded. Check Alpaca credentials.")
        sys.exit(1)

    keys = list(PARAM_GRID.keys())
    values = list(PARAM_GRID.values())
    all_combos = list(itertools.product(*values))
    total_grid = len(all_combos)

    if total_grid > MAX_COMBOS:
        print(f"\nGrid size: {total_grid} combos -> sampling {MAX_COMBOS}")
        random.seed(42)
        sampled = random.sample(all_combos, MAX_COMBOS)
    else:
        print(f"\nGrid size: {total_grid} combos -> testing all")
        sampled = all_combos

    results = []
    print(f"\nRunning {len(sampled)} parameter combinations...")

    for idx, combo in enumerate(sampled):
        config = dict(zip(keys, combo))

        try:
            trades = simulate_twentyminute(bars_by_symbol, config)
            metrics = calculate_metrics(trades)
            results.append({"params": config, "metrics": metrics})
        except Exception as e:
            print(f"  Config {idx+1} error: {e}")
            continue

        if (idx + 1) % 50 == 0:
            best_so_far = max(results, key=lambda x: x["metrics"]["composite_score"])
            print(f"  [{idx+1}/{len(sampled)}] Best score so far: {best_so_far['metrics']['composite_score']:.1f} "
                  f"(WR: {best_so_far['metrics']['win_rate']*100:.1f}%, PnL: ${best_so_far['metrics']['total_pnl']:.2f})")

    results.sort(key=lambda x: x["metrics"]["composite_score"], reverse=True)
    top_configs = []
    for rank, r in enumerate(results[:5], 1):
        top_configs.append({
            "rank": rank,
            "params": r["params"],
            "metrics": r["metrics"],
        })

    output = {
        "sweep_date": datetime.now().isoformat(),
        "bot_name": "TwentyMinuteBot",
        "period_days": args.days,
        "initial_capital": INITIAL_CAPITAL,
        "total_configs_tested": len(results),
        "symbols": SYMBOLS,
        "top_configs": top_configs,
    }

    os.makedirs("export/results", exist_ok=True)
    output_path = "export/results/sweep_twentyminute.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {output_path}")

    print(f"\n{'='*90}")
    print(f"{'TWENTYMINUTEBOT SWEEP RESULTS - TOP 5 CONFIGS':^90}")
    print(f"{'='*90}")

    header = f"{'Rank':<5} {'Trades':>7} {'WR%':>7} {'PnL':>11} {'PnL%':>7} {'PF':>6} {'Sharpe':>7} {'MaxDD%':>7} {'Score':>8}"
    print(f"\n{header}")
    print("-" * len(header))

    for c in top_configs:
        m = c["metrics"]
        pf_str = f"{m['profit_factor']:.2f}" if m['profit_factor'] < 100 else "INF"
        print(f"{c['rank']:<5} {m['total_trades']:>7} {m['win_rate']*100:>6.1f}% ${m['total_pnl']:>9.2f} "
              f"{m['total_pnl_pct']:>6.1f}% {pf_str:>6} {m['sharpe_ratio']:>7.2f} {m['max_drawdown_pct']:>6.1f}% "
              f"{m['composite_score']:>8.1f}")

    print(f"\n{'BEST CONFIG DETAILS':^90}")
    print("-" * 90)
    if top_configs:
        best = top_configs[0]
        for k, v in best["params"].items():
            print(f"  {k:<30} = {v}")

    print(f"\n{'='*90}")
    print("Done.")


if __name__ == "__main__":
    main()
