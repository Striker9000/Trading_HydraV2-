"""
All-Bots Backtest & Ranking Script
====================================
Runs backtests across all Trading Hydra bot strategies using real Alpaca data,
then ranks them by key performance metrics.

Bots tested:
1. MomentumBot (turtle breakout on stocks)
2. CryptoBot (turtle breakout on crypto)
3. WhipsawTrader (mean-reversion on stocks)
4. BounceBot (overnight crypto dip-buying)
5. TwentyMinuteBot (gap-and-go on stocks, equity mode)
6. TwentyMinuteBot Options (gap-and-go with options leverage)
7. OptionsBot Credit Spreads: Bull Put, Bear Call, Iron Condor
8. HailMary (cheap OTM options on momentum days)

Usage:
    python export/scripts/backtest_all_bots.py [--days 90]
"""

import os
import sys
import json
import statistics
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.trading_hydra.backtest.backtest_engine import BacktestEngine
from src.trading_hydra.backtest.options_backtest import (
    OptionsBacktester, SpreadType, OptionsBacktestResult, SpreadPosition
)


INITIAL_CAPITAL = 50000.0
BACKTEST_DAYS = 90

STOCK_SYMBOLS = ["AAPL", "NVDA", "TSLA", "AMD", "SPY"]
CRYPTO_SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD"]
BOUNCE_SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD"]
GAP_SYMBOLS = ["AAPL", "NVDA", "TSLA", "AMD", "SPY"]
OPTIONS_SYMBOLS = ["AAPL", "NVDA", "TSLA", "SPY"]


def run_momentum_backtest(engine: BacktestEngine, start: str, end: str) -> Dict[str, Any]:
    print("\n{'='*60}")
    print("1. MOMENTUMBOT (Turtle Breakout - Stocks)")
    print(f"   Symbols: {STOCK_SYMBOLS}")
    print(f"{'='*60}")

    config = {
        "entry_lookback": 20,
        "exit_lookback": 10,
        "atr_period": 14,
        "stop_loss_pct": 3.0,
        "take_profit_pct": 3.0,
        "trailing_stop_pct": 1.5,
        "trailing_activation_pct": 2.0
    }

    all_trades = []
    for symbol in STOCK_SYMBOLS:
        bars = engine.load_historical_data(symbol, start, end, "1Day")
        if bars:
            trades = engine.simulate_turtle_strategy(bars, symbol, config)
            all_trades.extend(trades)
            print(f"   {symbol}: {len(trades)} trades")
    
    metrics = engine.calculate_metrics(all_trades, asset_class="us_equity")

    print(f"   Trades: {metrics['num_trades']}, WR: {metrics['win_rate']*100:.1f}%, "
          f"PnL: ${metrics['total_pnl']:.2f}, Sharpe: {metrics['sharpe_ratio']:.2f}")

    return {
        "bot": "MomentumBot",
        "strategy": "Turtle Breakout (Stocks)",
        "asset_class": "stocks",
        **metrics
    }


def run_crypto_backtest(engine: BacktestEngine, start: str, end: str) -> Dict[str, Any]:
    print(f"\n{'='*60}")
    print("2. CRYPTOBOT (Turtle Breakout - Crypto)")
    print(f"   Symbols: {CRYPTO_SYMBOLS}")
    print(f"{'='*60}")

    config = {
        "entry_lookback": 15,
        "exit_lookback": 7,
        "atr_period": 10,
        "stop_loss_pct": 2.0,
        "take_profit_pct": 1.0,
        "trailing_stop_pct": 0.6,
        "trailing_activation_pct": 2.0
    }

    all_trades = []
    for symbol in CRYPTO_SYMBOLS:
        bars = engine.load_historical_data(symbol, start, end, "1Day")
        if bars:
            trades = engine.simulate_turtle_strategy(bars, symbol, config)
            all_trades.extend(trades)
            print(f"   {symbol}: {len(trades)} trades")

    metrics = engine.calculate_metrics(all_trades, asset_class="crypto")

    print(f"   Trades: {metrics['num_trades']}, WR: {metrics['win_rate']*100:.1f}%, "
          f"PnL: ${metrics['total_pnl']:.2f}, Sharpe: {metrics['sharpe_ratio']:.2f}")

    return {
        "bot": "CryptoBot",
        "strategy": "Turtle Breakout (Crypto)",
        "asset_class": "crypto",
        **metrics
    }


def run_whipsaw_backtest(engine: BacktestEngine, start: str, end: str) -> Dict[str, Any]:
    print(f"\n{'='*60}")
    print("3. WHIPSAW TRADER (Mean-Reversion - Stocks)")
    print(f"   Symbols: {STOCK_SYMBOLS}")
    print(f"{'='*60}")

    config = {
        "range_lookback": 15,
        "support_buffer_pct": 0.15,
        "resistance_buffer_pct": 0.15,
        "take_profit_pct": 1.5,
        "stop_loss_pct": 4.0,
        "trailing_stop_pct": 0.3,
        "trailing_activation_pct": 0.5
    }

    all_trades = []
    for symbol in STOCK_SYMBOLS:
        bars = engine.load_historical_data(symbol, start, end, "1Day")
        if bars:
            trades = engine.simulate_whipsaw_strategy(bars, symbol, config)
            all_trades.extend(trades)
            print(f"   {symbol}: {len(trades)} trades")

    metrics = engine.calculate_metrics(all_trades, asset_class="us_equity")

    print(f"   Trades: {metrics['num_trades']}, WR: {metrics['win_rate']*100:.1f}%, "
          f"PnL: ${metrics['total_pnl']:.2f}, Sharpe: {metrics['sharpe_ratio']:.2f}")

    return {
        "bot": "WhipsawTrader",
        "strategy": "Mean-Reversion (Stocks)",
        "asset_class": "stocks",
        **metrics
    }


def run_bounce_backtest(engine: BacktestEngine, start: str, end: str) -> Dict[str, Any]:
    print(f"\n{'='*60}")
    print("4. BOUNCEBOT (Overnight Crypto Dip-Buying)")
    print(f"   Symbols: {BOUNCE_SYMBOLS}")
    print(f"{'='*60}")

    config = {
        "entry_lookback": 5,
        "exit_lookback": 3,
        "atr_period": 7,
        "stop_loss_pct": 2.0,
        "take_profit_pct": 3.0,
        "trailing_stop_pct": 0.5,
        "trailing_activation_pct": 0.4
    }

    all_trades = []
    for symbol in BOUNCE_SYMBOLS:
        bars = engine.load_historical_data(symbol, start, end, "1Day")
        if not bars:
            print(f"   No data for {symbol}")
            continue

        bounce_trades = simulate_bounce_strategy(bars, symbol, config)
        all_trades.extend(bounce_trades)
        print(f"   {symbol}: {len(bounce_trades)} bounce trades")

    metrics = engine.calculate_metrics(all_trades, asset_class="crypto")

    print(f"   TOTAL Trades: {metrics['num_trades']}, WR: {metrics['win_rate']*100:.1f}%, "
          f"PnL: ${metrics['total_pnl']:.2f}, Sharpe: {metrics['sharpe_ratio']:.2f}")

    return {
        "bot": "BounceBot",
        "strategy": "Overnight Crypto Dip-Buy",
        "asset_class": "crypto",
        **metrics
    }


def simulate_bounce_strategy(bars, symbol, config):
    from src.trading_hydra.backtest.backtest_engine import Trade

    trades = []
    current_trade = None
    trailing_stop_price = 0.0

    lookback = config.get("entry_lookback", 8)
    stop_loss_pct = config.get("stop_loss_pct", 2.0) / 100
    take_profit_pct = config.get("take_profit_pct", 3.0) / 100
    trailing_stop_pct = config.get("trailing_stop_pct", 0.5) / 100
    trailing_activation_pct = config.get("trailing_activation_pct", 0.4) / 100
    drawdown_threshold = 3.0 / 100

    for idx in range(lookback + 1, len(bars)):
        bar = bars[idx]
        price = bar["close"]
        timestamp = bar["timestamp"]

        hour = None
        if hasattr(timestamp, 'hour'):
            hour = timestamp.hour
        elif isinstance(timestamp, str):
            try:
                hour = datetime.fromisoformat(timestamp.replace('Z', '+00:00')).hour
            except:
                hour = 12

        if current_trade:
            entry_price = current_trade.entry_price
            pnl_pct = (price - entry_price) / entry_price

            if pnl_pct <= -stop_loss_pct:
                current_trade.exit_time = timestamp
                current_trade.exit_price = price
                current_trade.exit_reason = "stop_loss"
                current_trade.pnl = (price - entry_price) * current_trade.quantity
                current_trade.pnl_pct = pnl_pct * 100
                trades.append(current_trade)
                current_trade = None
                trailing_stop_price = 0.0
                continue

            if pnl_pct >= take_profit_pct:
                current_trade.exit_time = timestamp
                current_trade.exit_price = price
                current_trade.exit_reason = "take_profit"
                current_trade.pnl = (price - entry_price) * current_trade.quantity
                current_trade.pnl_pct = pnl_pct * 100
                trades.append(current_trade)
                current_trade = None
                trailing_stop_price = 0.0
                continue

            if pnl_pct >= trailing_activation_pct:
                new_stop = price * (1 - trailing_stop_pct)
                trailing_stop_price = max(trailing_stop_price, new_stop)
                if price <= trailing_stop_price:
                    current_trade.exit_time = timestamp
                    current_trade.exit_price = price
                    current_trade.exit_reason = "trailing_stop"
                    current_trade.pnl = (price - entry_price) * current_trade.quantity
                    current_trade.pnl_pct = pnl_pct * 100
                    trades.append(current_trade)
                    current_trade = None
                    trailing_stop_price = 0.0
                    continue

        if not current_trade:
            is_bounce_window = True
            if hour is not None:
                is_bounce_window = (hour >= 1 and hour <= 10) or (hour >= 21)

            if is_bounce_window:
                lookback_prices = [bars[i]["close"] for i in range(max(0, idx - lookback), idx)]
                if lookback_prices:
                    recent_high = max(lookback_prices)
                    drawdown = (recent_high - price) / recent_high

                    if drawdown >= drawdown_threshold:
                        rsi = compute_rsi(bars, idx, 14)
                        if rsi is not None and rsi < 35:
                            position_size = max(500.0, INITIAL_CAPITAL * 0.03) / price
                            current_trade = Trade(
                                symbol=symbol,
                                side="buy",
                                entry_time=timestamp,
                                entry_price=price,
                                quantity=position_size
                            )
                            trailing_stop_price = 0.0

    if current_trade:
        last_bar = bars[-1]
        current_trade.exit_time = last_bar["timestamp"]
        current_trade.exit_price = last_bar["close"]
        current_trade.exit_reason = "end_of_data"
        current_trade.pnl = (last_bar["close"] - current_trade.entry_price) * current_trade.quantity
        current_trade.pnl_pct = ((last_bar["close"] - current_trade.entry_price) / current_trade.entry_price) * 100
        trades.append(current_trade)

    return trades


def compute_rsi(bars, idx, period=14):
    if idx < period + 1:
        return None
    gains = []
    losses = []
    for i in range(idx - period, idx):
        change = bars[i]["close"] - bars[i-1]["close"]
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


def run_twentymin_equity_backtest(engine: BacktestEngine, start: str, end: str) -> Dict[str, Any]:
    print(f"\n{'='*60}")
    print("5. TWENTYMINUTEBOT - Equity (Gap-and-Go Stocks)")
    print(f"   Symbols: {GAP_SYMBOLS}")
    print(f"{'='*60}")

    opts_bt = OptionsBacktester()
    all_trades = []

    config = {
        "min_gap_pct": 0.5,
        "max_gap_pct": 5.0,
        "stop_loss_pct": 1.0,
        "trailing_stop_pct": 0.8,
        "position_size_usd": 5000,
        "direction": "continuation",
        "use_options": False,
    }

    for symbol in GAP_SYMBOLS:
        bars = engine.load_historical_data(symbol, start, end, "1Day")
        if not bars or len(bars) < 5:
            continue
        result = opts_bt.run_gap_backtest(bars, symbol, config)
        all_trades.extend(result.trades)
        print(f"   {symbol}: {result.num_trades} gap trades, PnL: ${result.total_pnl:.2f}")

    metrics = compile_options_metrics(all_trades, "TwentyMinuteBot (Equity)")

    print(f"   TOTAL Trades: {metrics['num_trades']}, WR: {metrics['win_rate']*100:.1f}%, "
          f"PnL: ${metrics['total_pnl']:.2f}, Sharpe: {metrics['sharpe_ratio']:.2f}")

    return {
        "bot": "TwentyMinuteBot",
        "strategy": "Gap-and-Go (Equity)",
        "asset_class": "stocks",
        **metrics
    }


def run_twentymin_options_backtest(engine: BacktestEngine, start: str, end: str) -> Dict[str, Any]:
    print(f"\n{'='*60}")
    print("6. TWENTYMINUTEBOT - Options (Gap-and-Go w/ Options)")
    print(f"   Symbols: {GAP_SYMBOLS}")
    print(f"{'='*60}")

    opts_bt = OptionsBacktester()
    all_trades = []

    config = {
        "min_gap_pct": 0.5,
        "max_gap_pct": 5.0,
        "stop_loss_pct": 1.0,
        "trailing_stop_pct": 0.8,
        "position_size_usd": 4000,
        "direction": "continuation",
        "use_options": True,
        "options_delta": 0.50,
        "options_dte": 1,
        "iv": 0.35,
    }

    for symbol in GAP_SYMBOLS:
        bars = engine.load_historical_data(symbol, start, end, "1Day")
        if not bars or len(bars) < 5:
            continue
        result = opts_bt.run_gap_backtest(bars, symbol, config)
        all_trades.extend(result.trades)
        print(f"   {symbol}: {result.num_trades} gap trades, PnL: ${result.total_pnl:.2f}")

    metrics = compile_options_metrics(all_trades, "TwentyMinuteBot (Options)")

    print(f"   TOTAL Trades: {metrics['num_trades']}, WR: {metrics['win_rate']*100:.1f}%, "
          f"PnL: ${metrics['total_pnl']:.2f}, Sharpe: {metrics['sharpe_ratio']:.2f}")

    return {
        "bot": "TwentyMinuteBot (Options)",
        "strategy": "Gap-and-Go (Options Leverage)",
        "asset_class": "options",
        **metrics
    }


def run_hailmary_backtest(engine: BacktestEngine, start: str, end: str) -> Dict[str, Any]:
    print(f"\n{'='*60}")
    print("7. HAIL MARY (Cheap OTM Options on Momentum Days)")
    print(f"   Symbols: {OPTIONS_SYMBOLS}")
    print(f"{'='*60}")

    opts_bt = OptionsBacktester()
    all_trades = []

    config = {
        "dte": 5,
        "otm_pct": 3.0,
        "min_move_pct": 0.3,
        "profit_target_mult": 5.0,
        "time_exit_days": 1,
        "max_premium": 3.00,
        "min_premium": 0.05,
        "max_trades_per_day": 2,
        "contracts": 2,
        "iv": 0.35,
        "entry_lookback": 3,
    }

    for symbol in OPTIONS_SYMBOLS:
        bars = engine.load_historical_data(symbol, start, end, "1Day")
        if not bars or len(bars) < 5:
            continue
        result = opts_bt.run_hail_mary_backtest(bars, symbol, config)
        all_trades.extend(result.trades)
        print(f"   {symbol}: {result.num_trades} HM trades, PnL: ${result.total_pnl:.2f}")

    metrics = compile_options_metrics(all_trades, "HailMary")

    print(f"   TOTAL Trades: {metrics['num_trades']}, WR: {metrics['win_rate']*100:.1f}%, "
          f"PnL: ${metrics['total_pnl']:.2f}, Sharpe: {metrics['sharpe_ratio']:.2f}")

    return {
        "bot": "HailMary",
        "strategy": "Cheap OTM Options (Momentum)",
        "asset_class": "options",
        **metrics
    }


def run_credit_spreads_backtest(engine: BacktestEngine, start: str, end: str) -> List[Dict[str, Any]]:
    print(f"\n{'='*60}")
    print("8. OPTIONSBOT CREDIT SPREADS (Bull Put, Bear Call, Iron Condor)")
    print(f"   Symbols: {OPTIONS_SYMBOLS}")
    print(f"{'='*60}")

    opts_bt = OptionsBacktester()
    results = []

    spread_configs = {
        SpreadType.BULL_PUT_SPREAD: {
            "dte": 7,
            "delta_target": 0.30,
            "stop_loss_pct": 15.0,
            "take_profit_pct": 40.0,
            "spread_width_pct": 2.0,
            "entry_lookback": 20,
            "iv": 0.30,
        },
        SpreadType.BEAR_CALL_SPREAD: {
            "dte": 7,
            "delta_target": 0.30,
            "stop_loss_pct": 15.0,
            "take_profit_pct": 40.0,
            "spread_width_pct": 2.0,
            "entry_lookback": 20,
            "iv": 0.30,
        },
        SpreadType.IRON_CONDOR: {
            "dte": 7,
            "delta_target": 0.20,
            "stop_loss_pct": 15.0,
            "take_profit_pct": 25.0,
            "spread_width_pct": 2.0,
            "entry_lookback": 20,
            "iv": 0.30,
        },
    }

    for spread_type, config in spread_configs.items():
        all_trades = []
        for symbol in OPTIONS_SYMBOLS:
            bars = engine.load_historical_data(symbol, start, end, "1Day")
            if not bars or len(bars) < 25:
                continue
            result = opts_bt.run_backtest(bars, symbol, spread_type, config)
            all_trades.extend(result.trades)

        metrics = compile_options_metrics(all_trades, spread_type.value)
        name_map = {
            SpreadType.BULL_PUT_SPREAD: "OptionsBot (Bull Put Spread)",
            SpreadType.BEAR_CALL_SPREAD: "OptionsBot (Bear Call Spread)",
            SpreadType.IRON_CONDOR: "OptionsBot (Iron Condor)",
        }

        print(f"   {spread_type.value}: {metrics['num_trades']} trades, "
              f"WR: {metrics['win_rate']*100:.1f}%, PnL: ${metrics['total_pnl']:.2f}, "
              f"Sharpe: {metrics['sharpe_ratio']:.2f}")

        results.append({
            "bot": name_map[spread_type],
            "strategy": f"Credit Spread ({spread_type.value})",
            "asset_class": "options",
            **metrics
        })

    return results


def compile_options_metrics(trades: List[SpreadPosition], label: str) -> Dict[str, Any]:
    if not trades:
        return {
            "num_trades": 0, "win_rate": 0, "total_pnl": 0, "total_pnl_pct": 0,
            "avg_trade_pnl": 0, "avg_winner": 0, "avg_loser": 0, "profit_factor": 0,
            "sharpe_ratio": 0, "max_drawdown_pct": 0, "sortino_ratio": 0,
            "expectancy": 0, "kelly_fraction": 0, "transaction_costs": 0
        }

    pnls = [t.pnl for t in trades]
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]

    total_pnl = sum(pnls)
    win_rate = len(winners) / len(pnls) if pnls else 0
    avg_winner = statistics.mean(winners) if winners else 0
    avg_loser = statistics.mean(losers) if losers else 0

    gross_profit = sum(winners) if winners else 0
    gross_loss = abs(sum(losers)) if losers else 0
    pf = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0)

    returns = [t.pnl_pct / 100 for t in trades if t.pnl_pct != 0]
    if len(returns) > 1:
        import numpy as np
        sharpe = (np.mean(returns) / np.std(returns)) * np.sqrt(252 / 30) if np.std(returns) > 0 else 0
        downside = [r for r in returns if r < 0]
        sortino = (np.mean(returns) / np.std(downside)) * np.sqrt(252 / 30) if downside and np.std(downside) > 0 else sharpe
    else:
        sharpe = 0
        sortino = 0

    cumulative = 0
    peak = 0
    max_dd = 0
    for p in pnls:
        cumulative += p
        peak = max(peak, cumulative)
        dd = (peak - cumulative) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    expectancy = (win_rate * avg_winner) + ((1 - win_rate) * avg_loser) if avg_loser != 0 else avg_winner * win_rate
    kelly = 0
    if avg_loser != 0 and abs(avg_loser) > 0:
        kelly = win_rate - ((1 - win_rate) / (avg_winner / abs(avg_loser))) if avg_winner > 0 else 0

    return {
        "num_trades": len(pnls),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "total_pnl_pct": (total_pnl / INITIAL_CAPITAL) * 100,
        "avg_trade_pnl": statistics.mean(pnls) if pnls else 0,
        "avg_winner": avg_winner,
        "avg_loser": avg_loser,
        "profit_factor": pf,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown_pct": max_dd * 100,
        "expectancy": expectancy,
        "kelly_fraction": kelly,
        "transaction_costs": 0,
    }


def compute_composite_score(r: Dict[str, Any]) -> float:
    pf = min(r.get("profit_factor", 0), 10)
    sharpe = r.get("sharpe_ratio", 0)
    wr = r.get("win_rate", 0)
    expect = r.get("expectancy", 0)
    dd = r.get("max_drawdown_pct", 100)
    n = r.get("num_trades", 0)

    if n < 3:
        return -999

    trade_penalty = 1.0 if n >= 10 else (n / 10)

    score = (
        (sharpe * 25) +
        (pf * 15) +
        (wr * 100 * 20 / 100) +
        (min(expect, 500) * 0.02) +
        (max(0, 20 - dd) * 1.0)
    ) * trade_penalty

    return round(score, 2)


def print_rankings(all_results: List[Dict[str, Any]]):
    for r in all_results:
        r["composite_score"] = compute_composite_score(r)

    ranked = sorted(all_results, key=lambda x: x["composite_score"], reverse=True)

    print(f"\n{'#'*80}")
    print(f"#{'ALL-BOTS BACKTEST RANKINGS':^78}#")
    print(f"#{'(90-Day Historical Data, $50K Capital)':^78}#")
    print(f"{'#'*80}")

    header = f"{'Rank':<5} {'Bot':<32} {'Trades':>6} {'WR%':>6} {'PnL':>10} {'PF':>6} {'Sharpe':>7} {'MaxDD%':>7} {'Score':>7}"
    print(f"\n{header}")
    print("-" * len(header))

    for i, r in enumerate(ranked, 1):
        wr_pct = r['win_rate'] * 100
        pf = r['profit_factor']
        pf_str = f"{pf:.2f}" if pf < 100 else "INF"
        print(f"{i:<5} {r['bot']:<32} {r['num_trades']:>6} {wr_pct:>5.1f}% ${r['total_pnl']:>9.2f} {pf_str:>6} {r['sharpe_ratio']:>7.2f} {r['max_drawdown_pct']:>6.1f}% {r['composite_score']:>7.1f}")

    print(f"\n{'='*80}")
    print("DETAILED BREAKDOWN BY BOT")
    print(f"{'='*80}")

    for i, r in enumerate(ranked, 1):
        print(f"\n#{i} {r['bot']} ({r['strategy']})")
        print(f"   Asset Class:     {r.get('asset_class', 'N/A')}")
        print(f"   Trades:          {r['num_trades']}")
        print(f"   Win Rate:        {r['win_rate']*100:.1f}%")
        print(f"   Total P&L:       ${r['total_pnl']:.2f} ({r['total_pnl_pct']:.2f}%)")
        print(f"   Avg Trade P&L:   ${r['avg_trade_pnl']:.2f}")
        print(f"   Avg Winner:      ${r['avg_winner']:.2f}")
        print(f"   Avg Loser:       ${r['avg_loser']:.2f}")
        print(f"   Profit Factor:   {r['profit_factor']:.2f}")
        print(f"   Sharpe Ratio:    {r['sharpe_ratio']:.2f}")
        print(f"   Max Drawdown:    {r['max_drawdown_pct']:.1f}%")
        print(f"   Composite Score: {r['composite_score']:.1f}")

    return ranked


def main():
    import argparse
    parser = argparse.ArgumentParser(description="All-Bots Backtest & Ranking")
    parser.add_argument("--days", type=int, default=BACKTEST_DAYS, help="Backtest period in days")
    args = parser.parse_args()

    days = args.days
    end_date = datetime.now() - timedelta(days=1)
    start_date = end_date - timedelta(days=days)

    start = start_date.strftime("%Y-%m-%d")
    end = end_date.strftime("%Y-%m-%d")

    print(f"\n{'#'*80}")
    print(f"# TRADING HYDRA - ALL-BOTS BACKTEST")
    print(f"# Period: {start} to {end} ({days} days)")
    print(f"# Initial Capital: ${INITIAL_CAPITAL:,.0f}")
    print(f"{'#'*80}")

    engine = BacktestEngine(initial_capital=INITIAL_CAPITAL)

    all_results = []

    try:
        all_results.append(run_momentum_backtest(engine, start, end))
    except Exception as e:
        print(f"   ERROR: MomentumBot backtest failed: {e}")

    try:
        all_results.append(run_crypto_backtest(engine, start, end))
    except Exception as e:
        print(f"   ERROR: CryptoBot backtest failed: {e}")

    try:
        all_results.append(run_whipsaw_backtest(engine, start, end))
    except Exception as e:
        print(f"   ERROR: WhipsawTrader backtest failed: {e}")

    try:
        all_results.append(run_bounce_backtest(engine, start, end))
    except Exception as e:
        print(f"   ERROR: BounceBot backtest failed: {e}")

    try:
        all_results.append(run_twentymin_equity_backtest(engine, start, end))
    except Exception as e:
        print(f"   ERROR: TwentyMinuteBot Equity backtest failed: {e}")

    try:
        all_results.append(run_twentymin_options_backtest(engine, start, end))
    except Exception as e:
        print(f"   ERROR: TwentyMinuteBot Options backtest failed: {e}")

    try:
        all_results.append(run_hailmary_backtest(engine, start, end))
    except Exception as e:
        print(f"   ERROR: HailMary backtest failed: {e}")

    try:
        spreads = run_credit_spreads_backtest(engine, start, end)
        all_results.extend(spreads)
    except Exception as e:
        print(f"   ERROR: Credit Spreads backtest failed: {e}")

    ranked = print_rankings(all_results)

    output_path = "export/results/all_bots_backtest.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    serializable = []
    for r in ranked:
        clean = {}
        for k, v in r.items():
            if isinstance(v, float):
                if v == float('inf') or v == float('-inf') or v != v:
                    clean[k] = None
                else:
                    clean[k] = round(v, 4)
            else:
                clean[k] = v
        serializable.append(clean)

    with open(output_path, 'w') as f:
        json.dump({
            "backtest_date": datetime.now().isoformat(),
            "period": {"start": start, "end": end, "days": days},
            "initial_capital": INITIAL_CAPITAL,
            "rankings": serializable
        }, f, indent=2)

    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
