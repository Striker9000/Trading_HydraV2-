#!/usr/bin/env python3
"""
HailMary Parameter Sweep Optimizer
====================================
Performs grid search optimization for the HailMary bot strategy.
Tests parameter combinations against real Alpaca market data and outputs
optimal configs ranked by composite score.

Usage:
    python export/scripts/sweep_hailmary.py --days 90
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
HAILMARY_BUDGET = 1000.0  # Up to $1000 available funds for HailMary
MAX_COMBOS = 500

SYMBOLS = ["AAPL", "NVDA", "TSLA", "AMD", "SPY", "QQQ", "META", "AMZN", "MSFT", "GOOGL"]

PARAM_GRID = {
    "max_premium": [0.50, 1.00, 2.00, 3.00, 5.00, 7.00, 10.00],
    "min_stock_change_pct": [0.3, 0.5, 1.0, 1.5, 2.0],
    "profit_target_multiplier": [3.0, 5.0, 7.0, 10.0, 15.0, 20.0, 25.0],
    "max_trades_per_day": [2, 3, 5, 7],
    "dte_max": [0, 1, 3, 5, 7],
    "strike_otm_pct": [0.5, 1.0, 2.0, 3.0, 5.0],
}


def simulate_hailmary(bars_by_symbol: Dict[str, List[Dict]], config: Dict) -> List[Dict]:
    max_premium = config["max_premium"]
    min_change_pct = config["min_stock_change_pct"]
    profit_mult = config["profit_target_multiplier"]
    max_trades_day = config["max_trades_per_day"]
    dte_max = config["dte_max"]
    strike_otm_pct = config["strike_otm_pct"]

    dte_factor = 1.0 + (dte_max - 1) * 0.15

    trades = []

    for symbol, bars in bars_by_symbol.items():
        if len(bars) < 3:
            continue

        for i in range(1, len(bars) - 1):
            prev_bar = bars[i - 1]
            bar = bars[i]
            next_bar = bars[i + 1]

            prev_close = prev_bar["close"]
            cur_close = bar["close"]
            if prev_close <= 0:
                continue

            daily_change_pct = ((cur_close - prev_close) / prev_close) * 100
            abs_change = abs(daily_change_pct)

            if abs_change < min_change_pct:
                continue

            stock_price = cur_close
            premium = stock_price * (strike_otm_pct / 100) * 0.1 * dte_factor

            if premium > max_premium or premium < 0.05:
                continue

            day_trades = 0
            day_key = str(bar.get("timestamp", ""))[:10]

            trades_today = sum(1 for t in trades if str(t.get("day_key", "")) == day_key and t.get("symbol") == symbol)
            if trades_today >= max_trades_day:
                continue

            next_close = next_bar["close"]
            next_change_pct = ((next_close - cur_close) / cur_close) * 100

            direction = 1 if daily_change_pct > 0 else -1
            continuation = next_change_pct * direction

            threshold = profit_mult * premium / (stock_price * 0.01)

            if continuation > threshold:
                pnl = profit_mult * premium - premium
                exit_reason = "profit_target"
            else:
                pnl = -premium
                exit_reason = "expired_worthless"

            contracts = max(1, int(min(HAILMARY_BUDGET, INITIAL_CAPITAL * 0.02) / (premium * 100)))
            trade_pnl = pnl * contracts * 100

            trades.append({
                "symbol": symbol,
                "entry_price": premium,
                "exit_price": premium * profit_mult if continuation > threshold else 0,
                "pnl": trade_pnl,
                "pnl_pct": (trade_pnl / (premium * contracts * 100)) * 100 if premium > 0 else 0,
                "exit_reason": exit_reason,
                "day_key": day_key,
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
    parser = argparse.ArgumentParser(description="HailMary Parameter Sweep Optimizer")
    parser.add_argument("--days", type=int, default=600, help="Number of days to backtest")
    args = parser.parse_args()

    print("=" * 70)
    print("HAILMARY PARAMETER SWEEP OPTIMIZER")
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
            trades = simulate_hailmary(bars_by_symbol, config)
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
        "bot_name": "HailMary",
        "period_days": args.days,
        "initial_capital": INITIAL_CAPITAL,
        "total_configs_tested": len(results),
        "symbols": SYMBOLS,
        "top_configs": top_configs,
    }

    os.makedirs("export/results", exist_ok=True)
    output_path = "export/results/sweep_hailmary.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {output_path}")

    print(f"\n{'='*90}")
    print(f"{'HAILMARY SWEEP RESULTS - TOP 5 CONFIGS':^90}")
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
