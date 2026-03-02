#!/usr/bin/env python3
"""
ExitBot + ProfitSniper Per-Bot Parameter Sweep Optimizer
==========================================================
Sweeps exit parameters (stop loss, take profit, trailing stop, ProfitSniper)
independently for each bot type using real market data.
Optimized for MAX PROFIT (profits over wins).

Usage:
    python export/scripts/sweep_exitbot.py --days 600
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
MAX_COMBOS = 300

BOT_CONFIGS = {
    "stock_exits": {
        "symbols": ["AAPL", "NVDA", "TSLA", "AMD", "SPY", "QQQ", "META", "MSFT", "AMZN", "GOOGL"],
        "asset_class": "stock",
        "notional": 5000,
        "param_grid": {
            "stop_loss_pct": [3.0, 5.0, 8.0, 10.0, 12.0, 15.0],
            "take_profit_pct": [5.0, 8.0, 12.0, 15.0, 20.0, 25.0],
            "trailing_stop_pct": [2.0, 3.0, 3.5, 5.0, 7.0],
            "trailing_activation_pct": [2.0, 3.0, 5.0, 7.0],
            "catastrophic_stop_pct": [8.0, 10.0, 12.0, 15.0],
        },
    },
    "options_exits": {
        "symbols": ["AAPL", "NVDA", "TSLA", "AMD", "SPY", "QQQ"],
        "asset_class": "option",
        "notional": 3000,
        "leverage": 5.0,
        "param_grid": {
            "stop_loss_pct": [10.0, 15.0, 20.0, 25.0, 30.0],
            "take_profit_pct": [20.0, 30.0, 40.0, 50.0, 60.0, 80.0],
            "trailing_stop_pct": [4.0, 6.0, 8.0, 10.0, 12.0],
            "trailing_activation_pct": [5.0, 8.0, 10.0, 15.0],
            "catastrophic_stop_pct": [20.0, 25.0, 30.0, 40.0],
        },
    },
    "crypto_exits": {
        "symbols": ["BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "DOGE/USD", "XRP/USD"],
        "asset_class": "crypto",
        "notional": 3000,
        "param_grid": {
            "stop_loss_pct": [3.0, 5.0, 6.0, 8.0, 10.0],
            "take_profit_pct": [5.0, 8.0, 12.0, 15.0, 20.0, 25.0],
            "trailing_stop_pct": [2.0, 3.0, 4.0, 5.0, 7.0],
            "trailing_activation_pct": [3.0, 5.0, 7.0, 10.0],
            "catastrophic_stop_pct": [8.0, 10.0, 12.0, 15.0],
        },
    },
    "bounce_exits": {
        "symbols": ["BTC/USD", "ETH/USD", "SOL/USD"],
        "asset_class": "crypto",
        "notional": 2500,
        "param_grid": {
            "stop_loss_pct": [2.0, 3.0, 4.0, 5.0, 8.0],
            "take_profit_pct": [3.0, 4.0, 5.0, 8.0, 10.0, 15.0],
            "trailing_stop_pct": [1.0, 2.0, 3.0, 4.0, 5.0],
            "trailing_activation_pct": [2.0, 3.0, 4.0, 5.0],
            "catastrophic_stop_pct": [5.0, 8.0, 10.0, 12.0],
        },
    },
    "twentymin_exits": {
        "symbols": ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMD"],
        "asset_class": "stock",
        "notional": 3000,
        "param_grid": {
            "stop_loss_pct": [1.0, 2.0, 3.0, 5.0, 7.0],
            "take_profit_pct": [1.0, 2.0, 3.0, 5.0, 8.0],
            "trailing_stop_pct": [0.5, 0.8, 1.0, 2.0, 3.0],
            "trailing_activation_pct": [0.5, 1.0, 2.0, 3.0],
            "catastrophic_stop_pct": [5.0, 7.0, 10.0],
        },
    },
    "hailmary_exits": {
        "symbols": ["AAPL", "NVDA", "TSLA", "AMD", "SPY", "QQQ", "META"],
        "asset_class": "option",
        "notional": 1000,
        "leverage": 15.0,
        "param_grid": {
            "stop_loss_pct": [50.0, 75.0, 90.0, 100.0],
            "take_profit_pct": [200.0, 300.0, 500.0, 1000.0, 1500.0],
            "trailing_stop_pct": [15.0, 25.0, 35.0, 50.0],
            "trailing_activation_pct": [50.0, 100.0, 200.0, 300.0],
            "catastrophic_stop_pct": [90.0, 100.0],
        },
    },
    "profitsniper_stocks": {
        "symbols": ["AAPL", "NVDA", "TSLA", "AMD", "SPY", "QQQ", "META", "MSFT"],
        "asset_class": "stock",
        "notional": 5000,
        "param_grid": {
            "ratchet_arm_pct": [0.3, 0.5, 0.75, 1.0, 1.5],
            "ratchet_base_distance_pct": [0.15, 0.25, 0.35, 0.50],
            "ratchet_tighten_per_pct": [0.02, 0.03, 0.05, 0.08],
            "ratchet_min_distance_pct": [0.05, 0.08, 0.12, 0.15],
            "velocity_reversal_pct": [0.2, 0.3, 0.5, 0.7],
            "partial_exit_pct": [30, 40, 50, 60, 75],
        },
    },
    "profitsniper_crypto": {
        "symbols": ["BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD"],
        "asset_class": "crypto",
        "notional": 3000,
        "param_grid": {
            "ratchet_arm_pct": [0.5, 1.0, 1.5, 2.0],
            "ratchet_base_distance_pct": [0.25, 0.4, 0.6, 0.8],
            "ratchet_tighten_per_pct": [0.01, 0.02, 0.03, 0.05],
            "ratchet_min_distance_pct": [0.08, 0.12, 0.15, 0.20],
            "velocity_reversal_pct": [0.3, 0.4, 0.6, 0.8],
            "partial_exit_pct": [30, 40, 50, 60],
        },
    },
    "profitsniper_options": {
        "symbols": ["AAPL", "NVDA", "TSLA", "AMD", "SPY", "QQQ"],
        "asset_class": "option",
        "notional": 3000,
        "leverage": 5.0,
        "param_grid": {
            "ratchet_arm_pct": [2.0, 3.0, 4.0, 5.0],
            "ratchet_base_distance_pct": [1.0, 1.5, 2.0, 3.0],
            "ratchet_tighten_per_pct": [0.10, 0.15, 0.20, 0.25],
            "ratchet_min_distance_pct": [0.3, 0.5, 0.8, 1.0],
            "velocity_reversal_pct": [0.5, 1.0, 1.5, 2.0],
            "partial_exit_pct": [30, 40, 50, 60],
        },
    },
}


def simulate_exits(bars_by_symbol: Dict[str, List[Dict]], config: Dict, bot_cfg: Dict) -> List[Dict]:
    sl_pct = config.get("stop_loss_pct", config.get("ratchet_arm_pct", 5.0)) / 100
    tp_pct = config.get("take_profit_pct", 15.0) / 100
    trail_pct = config.get("trailing_stop_pct", config.get("ratchet_base_distance_pct", 3.0)) / 100
    trail_activation = config.get("trailing_activation_pct", config.get("ratchet_arm_pct", 3.0)) / 100
    catastrophic = config.get("catastrophic_stop_pct", 15.0) / 100
    notional = bot_cfg["notional"]
    leverage = bot_cfg.get("leverage", 1.0)

    is_profitsniper = "ratchet_arm_pct" in config
    partial_exit_pct = config.get("partial_exit_pct", 50) / 100
    ratchet_tighten = config.get("ratchet_tighten_per_pct", 0.03) / 100
    ratchet_min = config.get("ratchet_min_distance_pct", 0.08) / 100
    velocity_reversal = config.get("velocity_reversal_pct", 0.3) / 100

    trades = []

    for symbol, bars in bars_by_symbol.items():
        if len(bars) < 25:
            continue

        for i in range(20, len(bars) - 5):
            bar = bars[i]
            price = bar["close"]
            prev = bars[i - 1]["close"]
            if prev <= 0 or price <= 0:
                continue

            daily_ret = (price - prev) / prev
            if abs(daily_ret) < 0.003:
                continue

            entry_price = price
            direction = 1 if daily_ret > 0 else -1
            high_water = entry_price
            partial_taken = False
            current_trail = trail_pct

            max_look = min(10, len(bars) - i - 1)
            exit_price = None
            exit_reason = None

            for j in range(1, max_look + 1):
                future = bars[i + j]
                f_high = future["high"]
                f_low = future["low"]
                f_close = future["close"]

                if direction == 1:
                    high_water = max(high_water, f_high)
                    profit_pct = (f_close - entry_price) / entry_price * leverage
                    drawdown = (f_low - entry_price) / entry_price * leverage
                else:
                    high_water = min(high_water, f_low)
                    profit_pct = (entry_price - f_close) / entry_price * leverage
                    drawdown = (entry_price - f_high) / entry_price * leverage

                if drawdown <= -catastrophic:
                    exit_price = entry_price * (1 - direction * catastrophic / leverage)
                    exit_reason = "catastrophic_stop"
                    break

                if drawdown <= -sl_pct:
                    exit_price = entry_price * (1 - direction * sl_pct / leverage)
                    exit_reason = "stop_loss"
                    break

                if is_profitsniper:
                    if profit_pct >= trail_activation and not partial_taken:
                        partial_pnl = profit_pct * notional * partial_exit_pct
                        trades.append({"symbol": symbol, "entry_price": entry_price,
                                       "exit_price": f_close, "pnl": partial_pnl,
                                       "pnl_pct": profit_pct * 100, "exit_reason": "profitsniper_partial"})
                        partial_taken = True
                        notional_remaining = notional * (1 - partial_exit_pct)
                    else:
                        notional_remaining = notional

                    if profit_pct > trail_activation:
                        excess = profit_pct - trail_activation
                        current_trail = max(ratchet_min, trail_pct - excess * ratchet_tighten * 100)

                    if profit_pct > trail_activation:
                        trail_level = profit_pct - current_trail
                        if j > 1:
                            prev_f = bars[i + j - 1]
                            velocity = abs(f_close - prev_f["close"]) / prev_f["close"]
                            if velocity > velocity_reversal and f_close < prev_f["close"] and direction == 1:
                                exit_price = f_close
                                exit_reason = "velocity_reversal"
                                trade_pnl = ((f_close - entry_price) / entry_price * leverage) * notional_remaining
                                trades.append({"symbol": symbol, "entry_price": entry_price,
                                               "exit_price": f_close, "pnl": trade_pnl,
                                               "pnl_pct": profit_pct * 100, "exit_reason": exit_reason})
                                exit_price = "handled"
                                break
                else:
                    if profit_pct >= tp_pct:
                        exit_price = f_close
                        exit_reason = "take_profit"
                        break

                    if profit_pct >= trail_activation:
                        hw_pct = (high_water - entry_price) / entry_price * leverage if direction == 1 else (entry_price - high_water) / entry_price * leverage
                        drawback = hw_pct - profit_pct
                        if drawback >= trail_pct:
                            exit_price = f_close
                            exit_reason = "trailing_stop"
                            break

            if exit_price == "handled":
                continue

            if exit_price is None:
                last = bars[min(i + max_look, len(bars) - 1)]
                exit_price = last["close"]
                exit_reason = "time_exit"

            pnl_pct = direction * (exit_price - entry_price) / entry_price * leverage
            trade_pnl = pnl_pct * notional
            trades.append({"symbol": symbol, "entry_price": entry_price, "exit_price": exit_price,
                           "pnl": trade_pnl, "pnl_pct": pnl_pct * 100, "exit_reason": exit_reason})

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


def sweep_bot(bot_name: str, bot_cfg: Dict, bars_cache: Dict, days: int) -> Dict:
    print(f"\n{'='*70}")
    print(f"  SWEEPING: {bot_name}")
    print(f"{'='*70}")

    symbols = bot_cfg["symbols"]
    bars_by_symbol = {s: bars_cache[s] for s in symbols if s in bars_cache}

    if not bars_by_symbol:
        print(f"  No data for {bot_name}, skipping.")
        return {"bot_name": bot_name, "error": "no_data"}

    param_grid = bot_cfg["param_grid"]
    keys = list(param_grid.keys())
    all_combos = list(itertools.product(*param_grid.values()))

    if len(all_combos) > MAX_COMBOS:
        random.seed(42)
        sampled = random.sample(all_combos, MAX_COMBOS)
    else:
        sampled = all_combos

    results = []
    for idx, combo in enumerate(sampled):
        config = dict(zip(keys, combo))
        try:
            trades = simulate_exits(bars_by_symbol, config, bot_cfg)
            metrics = calculate_metrics(trades)
            results.append({"params": config, "metrics": metrics})
        except:
            continue
        if (idx + 1) % 50 == 0:
            best = max(results, key=lambda x: x["metrics"]["composite_score"])
            print(f"  [{idx+1}/{len(sampled)}] Best PnL: ${best['metrics']['total_pnl']:.2f}")

    if not results:
        return {"bot_name": bot_name, "error": "no_results"}

    results.sort(key=lambda x: x["metrics"]["composite_score"], reverse=True)
    top = results[0]

    print(f"\n  BEST {bot_name}:")
    print(f"    PnL: ${top['metrics']['total_pnl']:.2f} ({top['metrics']['total_pnl_pct']:.1f}%)")
    print(f"    WR: {top['metrics']['win_rate']*100:.1f}% | PF: {top['metrics']['profit_factor']:.2f}")
    print(f"    Sharpe: {top['metrics']['sharpe_ratio']:.2f} | MaxDD: {top['metrics']['max_drawdown_pct']:.1f}%")
    for k, v in top["params"].items():
        print(f"    {k}: {v}")

    return {
        "bot_name": bot_name,
        "best_params": top["params"],
        "best_metrics": top["metrics"],
        "top_3": [{"rank": i+1, "params": r["params"], "metrics": r["metrics"]} for i, r in enumerate(results[:3])],
        "total_tested": len(results),
    }


def main():
    parser = argparse.ArgumentParser(description="ExitBot + ProfitSniper Per-Bot Sweep")
    parser.add_argument("--days", type=int, default=600, help="Days to backtest")
    args = parser.parse_args()

    print("=" * 80)
    print("EXITBOT + PROFITSNIPER PER-BOT SWEEP (MAX PROFIT)")
    print(f"Period: {args.days} days | Capital: ${INITIAL_CAPITAL:,.0f}")
    print("=" * 80)

    engine = BacktestEngine(initial_capital=INITIAL_CAPITAL)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=args.days)

    all_symbols = set()
    for cfg in BOT_CONFIGS.values():
        all_symbols.update(cfg["symbols"])

    print(f"\nLoading data for {len(all_symbols)} symbols...")
    bars_cache = {}
    for symbol in sorted(all_symbols):
        try:
            bars = engine.load_historical_data(symbol, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"), "1Day")
            if bars:
                bars_cache[symbol] = bars
                print(f"  {symbol}: {len(bars)} bars")
        except Exception as e:
            print(f"  {symbol}: Error - {e}")

    all_results = {}
    for bot_name, bot_cfg in BOT_CONFIGS.items():
        result = sweep_bot(bot_name, bot_cfg, bars_cache, args.days)
        all_results[bot_name] = result

    output = {
        "sweep_date": datetime.now().isoformat(),
        "period_days": args.days,
        "initial_capital": INITIAL_CAPITAL,
        "scoring": "MAX_PROFIT",
        "results": all_results,
    }

    os.makedirs("export/results", exist_ok=True)
    with open("export/results/sweep_exitbot.json", "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n\n{'='*90}")
    print(f"{'EXITBOT + PROFITSNIPER SWEEP SUMMARY':^90}")
    print(f"{'='*90}")
    header = f"{'Bot':<25} {'PnL':>12} {'PnL%':>8} {'WR%':>7} {'PF':>6} {'Sharpe':>7} {'MaxDD%':>7}"
    print(f"\n{header}")
    print("-" * len(header))
    for name, result in all_results.items():
        if "error" in result:
            print(f"{name:<25} {'ERROR':>12}")
            continue
        m = result["best_metrics"]
        pf_str = f"{m['profit_factor']:.2f}" if m['profit_factor'] < 100 else "INF"
        print(f"{name:<25} ${m['total_pnl']:>10.2f} {m['total_pnl_pct']:>7.1f}% "
              f"{m['win_rate']*100:>6.1f}% {pf_str:>6} {m['sharpe_ratio']:>7.2f} {m['max_drawdown_pct']:>6.1f}%")

    print(f"\nResults saved to export/results/sweep_exitbot.json")
    print("Done.")


if __name__ == "__main__":
    main()
