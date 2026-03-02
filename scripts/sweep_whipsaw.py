#!/usr/bin/env python3
"""
WhipsawTrader Parameter Sweep Optimizer
==========================================
Grid search for mean-reversion strategy in range-bound markets.
Optimized for MAX PROFIT (profits over wins).

Usage:
    python export/scripts/sweep_whipsaw.py --days 600
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
MAX_COMBOS = 200

SYMBOLS = ["AAPL", "NVDA", "TSLA", "AMD", "SPY", "QQQ", "META", "AMZN", "MSFT"]

PARAM_GRID = {
    "take_profit_pct": [0.5, 1.0, 1.5, 2.0, 3.0, 5.0],
    "stop_loss_pct": [1.0, 2.0, 3.0, 4.0, 5.0, 8.0],
    "std_dev_mult": [1.0, 1.5, 2.0, 2.5, 3.0],
    "range_lookback": [20, 30, 40, 60, 80],
    "max_hold_bars": [3, 5, 8, 12, 20],
    "rsi_oversold": [20, 25, 28, 30, 35],
}


def compute_rsi(bars, idx, period=14):
    if idx < period + 1:
        return None
    gains, losses = [], []
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
    return 100 - (100 / (1 + avg_gain / avg_loss))


def simulate_whipsaw(bars_by_symbol: Dict[str, List[Dict]], config: Dict) -> List[Dict]:
    tp_pct = config["take_profit_pct"] / 100
    sl_pct = config["stop_loss_pct"] / 100
    std_mult = config["std_dev_mult"]
    lookback = config["range_lookback"]
    max_hold = config["max_hold_bars"]
    rsi_threshold = config["rsi_oversold"]

    notional = INITIAL_CAPITAL * 0.05
    trades = []

    for symbol, bars in bars_by_symbol.items():
        if len(bars) < lookback + 5:
            continue

        in_trade = False
        entry_price = 0
        entry_idx = 0

        for i in range(lookback, len(bars)):
            bar = bars[i]
            price = bar["close"]
            low = bar["low"]
            high = bar["high"]

            if in_trade:
                hold_bars = i - entry_idx

                if low <= entry_price * (1 - sl_pct):
                    trade_pnl = -sl_pct * notional
                    trades.append({"symbol": symbol, "entry_price": entry_price,
                                   "exit_price": entry_price * (1 - sl_pct),
                                   "pnl": trade_pnl, "pnl_pct": -sl_pct * 100, "exit_reason": "stop_loss"})
                    in_trade = False
                    continue

                if high >= entry_price * (1 + tp_pct):
                    trade_pnl = tp_pct * notional
                    trades.append({"symbol": symbol, "entry_price": entry_price,
                                   "exit_price": entry_price * (1 + tp_pct),
                                   "pnl": trade_pnl, "pnl_pct": tp_pct * 100, "exit_reason": "take_profit"})
                    in_trade = False
                    continue

                if hold_bars >= max_hold:
                    pnl_pct = (price - entry_price) / entry_price
                    trade_pnl = pnl_pct * notional
                    trades.append({"symbol": symbol, "entry_price": entry_price, "exit_price": price,
                                   "pnl": trade_pnl, "pnl_pct": pnl_pct * 100, "exit_reason": "max_hold"})
                    in_trade = False
                    continue

            if not in_trade:
                window = [b["close"] for b in bars[i - lookback:i]]
                mean_price = statistics.mean(window)
                std_price = statistics.stdev(window) if len(window) > 1 else 0

                if std_price <= 0:
                    continue

                lower_band = mean_price - std_mult * std_price

                if price <= lower_band:
                    rsi = compute_rsi(bars, i)
                    if rsi is not None and rsi <= rsi_threshold:
                        entry_price = price
                        entry_idx = i
                        in_trade = True

        if in_trade:
            last = bars[-1]["close"]
            pnl_pct = (last - entry_price) / entry_price
            trades.append({"symbol": symbol, "entry_price": entry_price, "exit_price": last,
                           "pnl": pnl_pct * notional, "pnl_pct": pnl_pct * 100, "exit_reason": "end_of_data"})

    return trades


def calculate_metrics(trades: List[Dict]) -> Dict[str, float]:
    if not trades:
        return {"total_trades": 0, "win_rate": 0, "total_pnl": 0, "total_pnl_pct": 0,
                "profit_factor": 0, "sharpe_ratio": 0, "max_drawdown_pct": 0,
                "avg_trade_pnl": 0, "composite_score": -999}

    pnls = [t["pnl"] for t in trades]
    total_pnl = sum(pnls)
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]
    win_rate = len(winners) / len(pnls)
    gross_profit = sum(winners) if winners else 0
    gross_loss = abs(sum(losers)) if losers else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (10.0 if gross_profit > 0 else 0)

    returns = [t["pnl_pct"] / 100 for t in trades if t["pnl_pct"] != 0]
    sharpe = 0
    if len(returns) > 1:
        mean_r = statistics.mean(returns)
        std_r = statistics.stdev(returns)
        sharpe = (mean_r / std_r) * (252 ** 0.5) if std_r > 0 else 0

    cumulative = peak = max_dd = 0
    for p in pnls:
        cumulative += p
        peak = max(peak, cumulative)
        dd = (peak - cumulative) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    total_pnl_pct = (total_pnl / INITIAL_CAPITAL) * 100
    avg_trade_pnl = statistics.mean(pnls)
    pf_capped = min(profit_factor, 10)

    score = (
        (total_pnl_pct * 30) +
        (avg_trade_pnl * 0.1) +
        (pf_capped * 15) +
        (sharpe * 15) -
        (max_dd * 100 * 10) +
        (win_rate * 20) +
        (1 if len(pnls) > 15 else 0) * 10
    )

    return {"total_trades": len(pnls), "win_rate": round(win_rate, 4),
            "total_pnl": round(total_pnl, 2), "total_pnl_pct": round(total_pnl_pct, 2),
            "profit_factor": round(profit_factor, 2), "sharpe_ratio": round(sharpe, 2),
            "max_drawdown_pct": round(max_dd * 100, 2), "avg_trade_pnl": round(avg_trade_pnl, 2),
            "composite_score": round(score, 2)}


def main():
    parser = argparse.ArgumentParser(description="WhipsawTrader Sweep")
    parser.add_argument("--days", type=int, default=600, help="Days to backtest")
    args = parser.parse_args()

    print("=" * 70)
    print("WHIPSAWTRADER PARAMETER SWEEP (MAX PROFIT)")
    print(f"Period: {args.days} days | Capital: ${INITIAL_CAPITAL:,.0f}")
    print("=" * 70)

    engine = BacktestEngine(initial_capital=INITIAL_CAPITAL)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=args.days)

    bars_by_symbol = {}
    for symbol in SYMBOLS:
        try:
            bars = engine.load_historical_data(symbol, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"), "1Day")
            if bars:
                bars_by_symbol[symbol] = bars
                print(f"  {symbol}: {len(bars)} bars")
        except Exception as e:
            print(f"  {symbol}: Error - {e}")

    if not bars_by_symbol:
        print("ERROR: No data.")
        sys.exit(1)

    keys = list(PARAM_GRID.keys())
    all_combos = list(itertools.product(*PARAM_GRID.values()))
    if len(all_combos) > MAX_COMBOS:
        random.seed(42)
        sampled = random.sample(all_combos, MAX_COMBOS)
    else:
        sampled = all_combos

    results = []
    print(f"\nRunning {len(sampled)} combos...")
    for idx, combo in enumerate(sampled):
        config = dict(zip(keys, combo))
        try:
            trades = simulate_whipsaw(bars_by_symbol, config)
            metrics = calculate_metrics(trades)
            results.append({"params": config, "metrics": metrics})
        except:
            continue
        if (idx + 1) % 50 == 0:
            best = max(results, key=lambda x: x["metrics"]["composite_score"])
            print(f"  [{idx+1}/{len(sampled)}] Best PnL: ${best['metrics']['total_pnl']:.2f}")

    results.sort(key=lambda x: x["metrics"]["composite_score"], reverse=True)
    top_configs = [{"rank": i+1, "params": r["params"], "metrics": r["metrics"]} for i, r in enumerate(results[:5])]

    output = {"sweep_date": datetime.now().isoformat(), "bot_name": "WhipsawTrader",
              "period_days": args.days, "initial_capital": INITIAL_CAPITAL,
              "total_configs_tested": len(results), "symbols": SYMBOLS,
              "scoring": "MAX_PROFIT", "top_configs": top_configs}

    os.makedirs("export/results", exist_ok=True)
    with open("export/results/sweep_whipsaw.json", "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n{'='*90}")
    print(f"{'WHIPSAWTRADER SWEEP - TOP 5 (MAX PROFIT)':^90}")
    print(f"{'='*90}")
    header = f"{'Rank':<5} {'Trades':>7} {'WR%':>7} {'PnL':>12} {'PnL%':>8} {'PF':>6} {'Sharpe':>7} {'MaxDD%':>7} {'AvgPnL':>9} {'Score':>8}"
    print(f"\n{header}")
    print("-" * len(header))
    for c in top_configs:
        m = c["metrics"]
        pf_str = f"{m['profit_factor']:.2f}" if m['profit_factor'] < 100 else "INF"
        print(f"{c['rank']:<5} {m['total_trades']:>7} {m['win_rate']*100:>6.1f}% ${m['total_pnl']:>10.2f} "
              f"{m['total_pnl_pct']:>7.1f}% {pf_str:>6} {m['sharpe_ratio']:>7.2f} {m['max_drawdown_pct']:>6.1f}% "
              f"${m['avg_trade_pnl']:>7.2f} {m['composite_score']:>8.1f}")
    if top_configs:
        print(f"\nBEST CONFIG:")
        for k, v in top_configs[0]["params"].items():
            print(f"  {k:<30} = {v}")
    print("Done.")


if __name__ == "__main__":
    main()
