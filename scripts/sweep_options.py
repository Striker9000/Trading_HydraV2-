#!/usr/bin/env python3
"""
OptionsBot Credit Spread Parameter Sweep Optimizer
=====================================================
Grid search for credit spread strategies (bull put, bear call, iron condor).
Optimized for MAX PROFIT (profits over wins).

Usage:
    python export/scripts/sweep_options.py --days 600
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

SYMBOLS = ["AAPL", "NVDA", "TSLA", "AMD", "SPY", "QQQ", "META", "MSFT"]

PARAM_GRID = {
    "max_dte": [7, 14, 21, 30, 45],
    "profit_target_pct": [25, 40, 50, 60, 70, 80],
    "stop_loss_pct": [50, 75, 100, 150, 200],
    "spread_width_pct": [2.0, 3.0, 4.0, 5.0, 7.0],
    "short_delta": [0.15, 0.20, 0.25, 0.30, 0.35],
    "max_credit": [1.0, 1.5, 2.0, 3.0, 5.0],
}


def simulate_credit_spreads(bars_by_symbol: Dict[str, List[Dict]], config: Dict) -> List[Dict]:
    max_dte = config["max_dte"]
    pt_pct = config["profit_target_pct"] / 100
    sl_pct = config["stop_loss_pct"] / 100
    spread_width_pct = config["spread_width_pct"] / 100
    short_delta = config["short_delta"]
    max_credit = config["max_credit"]

    dte_factor = max_dte / 30.0
    trades = []

    for symbol, bars in bars_by_symbol.items():
        if len(bars) < max_dte + 5:
            continue

        for i in range(20, len(bars) - max_dte):
            bar = bars[i]
            price = bar["close"]
            if price <= 0:
                continue

            atr_sum = 0
            for j in range(max(0, i - 14), i):
                atr_sum += bars[j]["high"] - bars[j]["low"]
            atr = atr_sum / min(14, i) if i > 0 else price * 0.02
            iv_proxy = (atr / price) * 16

            if iv_proxy < 0.20:
                continue

            spread_width = price * spread_width_pct
            credit = spread_width * short_delta * iv_proxy * dte_factor
            credit = min(credit, max_credit)

            if credit < 0.10:
                continue

            max_loss = spread_width - credit

            hold_days = min(max_dte, len(bars) - i - 1)
            if hold_days < 1:
                continue

            future_prices = [bars[i + d]["close"] for d in range(1, hold_days + 1)]
            max_move = max(abs(p - price) / price for p in future_prices)

            breach_threshold = spread_width_pct * (1 - short_delta)

            if max_move > breach_threshold:
                loss = min(max_loss, credit * sl_pct)
                trade_pnl = -loss * 100
                exit_reason = "stop_loss"
            else:
                time_decay = min(1.0, hold_days / max_dte)
                realized_credit = credit * time_decay * pt_pct
                trade_pnl = realized_credit * 100
                exit_reason = "profit_target"

            trades.append({
                "symbol": symbol,
                "entry_price": credit,
                "exit_price": credit - (trade_pnl / 100),
                "pnl": trade_pnl,
                "pnl_pct": (trade_pnl / (max_loss * 100)) * 100 if max_loss > 0 else 0,
                "exit_reason": exit_reason,
                "strategy": "credit_spread",
            })

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
        (avg_trade_pnl * 0.05) +
        (pf_capped * 15) +
        (sharpe * 15) -
        (max_dd * 100 * 10) +
        (win_rate * 20) +
        (1 if len(pnls) > 20 else 0) * 10
    )

    return {"total_trades": len(pnls), "win_rate": round(win_rate, 4),
            "total_pnl": round(total_pnl, 2), "total_pnl_pct": round(total_pnl_pct, 2),
            "profit_factor": round(profit_factor, 2), "sharpe_ratio": round(sharpe, 2),
            "max_drawdown_pct": round(max_dd * 100, 2), "avg_trade_pnl": round(avg_trade_pnl, 2),
            "composite_score": round(score, 2)}


def main():
    parser = argparse.ArgumentParser(description="OptionsBot Credit Spread Sweep")
    parser.add_argument("--days", type=int, default=600, help="Days to backtest")
    args = parser.parse_args()

    print("=" * 70)
    print("OPTIONSBOT CREDIT SPREAD SWEEP (MAX PROFIT)")
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
            trades = simulate_credit_spreads(bars_by_symbol, config)
            metrics = calculate_metrics(trades)
            results.append({"params": config, "metrics": metrics})
        except:
            continue
        if (idx + 1) % 50 == 0:
            best = max(results, key=lambda x: x["metrics"]["composite_score"])
            print(f"  [{idx+1}/{len(sampled)}] Best PnL: ${best['metrics']['total_pnl']:.2f}")

    results.sort(key=lambda x: x["metrics"]["composite_score"], reverse=True)
    top_configs = [{"rank": i+1, "params": r["params"], "metrics": r["metrics"]} for i, r in enumerate(results[:5])]

    output = {"sweep_date": datetime.now().isoformat(), "bot_name": "OptionsBot_CreditSpreads",
              "period_days": args.days, "initial_capital": INITIAL_CAPITAL,
              "total_configs_tested": len(results), "symbols": SYMBOLS,
              "scoring": "MAX_PROFIT", "top_configs": top_configs}

    os.makedirs("export/results", exist_ok=True)
    with open("export/results/sweep_options.json", "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n{'='*90}")
    print(f"{'OPTIONSBOT CREDIT SPREAD SWEEP - TOP 5 (MAX PROFIT)':^90}")
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
