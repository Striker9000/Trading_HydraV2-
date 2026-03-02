#!/usr/bin/env python3
"""
BounceBot Parameter Sweep Optimizer
======================================
Performs grid search optimization for the BounceBot crypto dip-buying strategy.
Tests parameter combinations against real Alpaca market data and outputs
optimal configs ranked by composite score.

Usage:
    python export/scripts/sweep_bouncebot.py --days 90
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

SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD"]

PARAM_GRID = {
    "drawdown_threshold_pct": [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0],
    "take_profit_pct": [1.0, 1.5, 2.0, 3.0, 4.0, 5.0],
    "stop_loss_pct": [1.0, 1.5, 2.0, 3.0, 4.0],
    "lookback_days": [3, 5, 7, 10, 14],
    "max_trades_per_session": [2, 3, 5, 7],
    "equity_pct": [2.0, 3.0, 4.0, 5.0],
}


def compute_rsi(bars, idx, period=14):
    if idx < period + 1:
        return None
    gains = []
    losses = []
    for i in range(idx - period, idx):
        change = bars[i]["close"] - bars[i - 1]["close"]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def simulate_bouncebot(bars_by_symbol: Dict[str, List[Dict]], config: Dict) -> List[Dict]:
    drawdown_threshold = config["drawdown_threshold_pct"] / 100
    take_profit_pct = config["take_profit_pct"] / 100
    stop_loss_pct = config["stop_loss_pct"] / 100
    lookback = config["lookback_days"]
    max_trades_session = config["max_trades_per_session"]
    equity_pct = config["equity_pct"] / 100

    position_size = INITIAL_CAPITAL * equity_pct

    trades = []

    for symbol, bars in bars_by_symbol.items():
        if len(bars) < lookback + 5:
            continue

        in_trade = False
        entry_price = 0
        entry_idx = 0
        session_trades = 0
        last_session_day = ""

        for i in range(lookback + 1, len(bars)):
            bar = bars[i]
            price = bar["close"]
            day_key = str(bar.get("timestamp", ""))[:10]

            if day_key != last_session_day:
                session_trades = 0
                last_session_day = day_key

            if in_trade:
                pnl_pct_cur = (price - entry_price) / entry_price

                if pnl_pct_cur <= -stop_loss_pct:
                    trade_pnl = -stop_loss_pct * position_size
                    trades.append({
                        "symbol": symbol,
                        "entry_price": entry_price,
                        "exit_price": price,
                        "pnl": trade_pnl,
                        "pnl_pct": -stop_loss_pct * 100,
                        "exit_reason": "stop_loss",
                    })
                    in_trade = False
                    continue

                if pnl_pct_cur >= take_profit_pct:
                    trade_pnl = take_profit_pct * position_size
                    trades.append({
                        "symbol": symbol,
                        "entry_price": entry_price,
                        "exit_price": price,
                        "pnl": trade_pnl,
                        "pnl_pct": take_profit_pct * 100,
                        "exit_reason": "take_profit",
                    })
                    in_trade = False
                    continue

                hold_bars = i - entry_idx
                if hold_bars >= lookback * 2:
                    trade_pnl = pnl_pct_cur * position_size
                    trades.append({
                        "symbol": symbol,
                        "entry_price": entry_price,
                        "exit_price": price,
                        "pnl": trade_pnl,
                        "pnl_pct": pnl_pct_cur * 100,
                        "exit_reason": "max_hold",
                    })
                    in_trade = False
                    continue

            if not in_trade:
                if session_trades >= max_trades_session:
                    continue

                lookback_prices = [bars[j]["high"] for j in range(max(0, i - lookback), i)]
                if not lookback_prices:
                    continue

                recent_high = max(lookback_prices)
                if recent_high <= 0:
                    continue

                drawdown = (recent_high - price) / recent_high

                if drawdown >= drawdown_threshold:
                    rsi = compute_rsi(bars, i, 14)
                    if rsi is not None and rsi < 35:
                        entry_price = price
                        entry_idx = i
                        in_trade = True
                        session_trades += 1

        if in_trade:
            last_bar = bars[-1]
            final_pnl_pct = (last_bar["close"] - entry_price) / entry_price
            trades.append({
                "symbol": symbol,
                "entry_price": entry_price,
                "exit_price": last_bar["close"],
                "pnl": final_pnl_pct * position_size,
                "pnl_pct": final_pnl_pct * 100,
                "exit_reason": "end_of_data",
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
    parser = argparse.ArgumentParser(description="BounceBot Parameter Sweep Optimizer")
    parser.add_argument("--days", type=int, default=600, help="Number of days to backtest")
    args = parser.parse_args()

    print("=" * 70)
    print("BOUNCEBOT PARAMETER SWEEP OPTIMIZER")
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
            trades = simulate_bouncebot(bars_by_symbol, config)
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
        "bot_name": "BounceBot",
        "period_days": args.days,
        "initial_capital": INITIAL_CAPITAL,
        "total_configs_tested": len(results),
        "symbols": SYMBOLS,
        "top_configs": top_configs,
    }

    os.makedirs("export/results", exist_ok=True)
    output_path = "export/results/sweep_bouncebot.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {output_path}")

    print(f"\n{'='*90}")
    print(f"{'BOUNCEBOT SWEEP RESULTS - TOP 5 CONFIGS':^90}")
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
