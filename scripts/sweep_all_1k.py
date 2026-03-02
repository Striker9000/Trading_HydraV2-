#!/usr/bin/env python3
"""
ALL BOTS $1,000/Day Unified Sweep + Ranking
================================================
Runs parameter sweeps for every entry bot at a normalized $1,000/day
budget, then ranks them by profitability (total PnL) first, win rate second.

Usage:
    python export/scripts/sweep_all_1k.py --days 600
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
DAILY_BUDGET = 1000.0
MAX_COMBOS = 500


STOCK_SYMBOLS = ["AAPL", "NVDA", "TSLA", "AMD", "SPY", "QQQ", "META", "AMZN", "MSFT", "GOOGL"]
CRYPTO_SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LINK/USD", "DOGE/USD", "LTC/USD", "XRP/USD"]

BOT_SWEEP_CONFIGS = {
    "HailMary": {
        "symbols": STOCK_SYMBOLS,
        "param_grid": {
            "max_premium": [0.50, 1.00, 2.00, 3.00, 5.00, 7.00, 10.00],
            "min_stock_change_pct": [0.3, 0.5, 1.0, 1.5, 2.0],
            "profit_target_multiplier": [3.0, 5.0, 7.0, 10.0, 15.0, 20.0, 25.0],
            "max_trades_per_day": [2, 3, 5, 7],
            "dte_max": [0, 1, 3, 5, 7],
            "strike_otm_pct": [0.5, 1.0, 2.0, 3.0, 5.0],
        },
    },
    "MomentumBot": {
        "symbols": STOCK_SYMBOLS,
        "param_grid": {
            "stop_loss_pct": [1.0, 1.5, 2.0, 3.0, 5.0],
            "take_profit_pct": [2.0, 3.0, 5.0, 8.0, 12.0, 15.0],
            "trailing_stop_pct": [1.0, 1.5, 2.0, 3.0, 5.0],
            "trailing_activation_pct": [1.0, 2.0, 3.0, 5.0],
            "max_concurrent": [3, 5, 7, 10],
        },
    },
    "CryptoBot": {
        "symbols": CRYPTO_SYMBOLS,
        "param_grid": {
            "entry_lookback": [5, 10, 15, 20, 30],
            "exit_lookback": [3, 5, 7, 10],
            "stop_loss_pct": [2.0, 3.0, 5.0, 8.0, 10.0],
            "take_profit_pct": [3.0, 5.0, 8.0, 12.0, 15.0, 20.0],
            "trailing_stop_pct": [1.0, 2.0, 3.0, 5.0],
        },
    },
    "BounceBot": {
        "symbols": ["BTC/USD", "ETH/USD", "SOL/USD"],
        "param_grid": {
            "drawdown_threshold_pct": [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0],
            "take_profit_pct": [1.0, 1.5, 2.0, 3.0, 4.0, 5.0],
            "stop_loss_pct": [1.0, 1.5, 2.0, 3.0, 4.0],
            "lookback_days": [3, 5, 7, 10, 14],
            "max_trades_per_session": [2, 3, 5, 7],
        },
    },
    "TwentyMinuteBot": {
        "symbols": ["AAPL", "NVDA", "TSLA", "AMD", "SPY", "QQQ", "META", "AMZN"],
        "param_grid": {
            "min_gap_pct": [0.25, 0.50, 0.75, 1.0, 1.5, 2.0],
            "take_profit_pct": [1.0, 2.0, 3.0, 5.0, 8.0],
            "stop_loss_pct": [1.0, 2.0, 3.0, 5.0],
            "options_leverage": [1.0, 5.0, 10.0, 20.0],
            "confirmation_bars": [1, 2, 3],
            "max_trades_per_day": [3, 5, 6, 8],
        },
    },
    "OptionsBot_CreditSpreads": {
        "symbols": ["AAPL", "NVDA", "TSLA", "AMD", "SPY", "QQQ", "META", "MSFT"],
        "param_grid": {
            "max_dte": [7, 14, 21, 30, 45],
            "profit_target_pct": [25, 40, 50, 60, 70, 80],
            "stop_loss_pct": [50, 75, 100, 150, 200],
            "spread_width_pct": [2.0, 3.0, 4.0, 5.0, 7.0],
            "short_delta": [0.15, 0.20, 0.25, 0.30, 0.35],
            "max_credit": [1.0, 1.5, 2.0, 3.0, 5.0],
        },
    },
    "WhipsawTrader": {
        "symbols": STOCK_SYMBOLS[:9],
        "param_grid": {
            "take_profit_pct": [0.5, 1.0, 1.5, 2.0, 3.0, 5.0],
            "stop_loss_pct": [1.0, 2.0, 3.0, 4.0, 5.0, 8.0],
            "std_dev_mult": [1.0, 1.5, 2.0, 2.5, 3.0],
            "range_lookback": [20, 30, 40, 60, 80],
            "max_hold_bars": [3, 5, 8, 12, 20],
            "rsi_oversold": [20, 25, 28, 30, 35],
        },
    },
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


def simulate_hailmary(bars_by_symbol, config):
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
            if abs(daily_change_pct) < min_change_pct:
                continue
            premium = cur_close * (strike_otm_pct / 100) * 0.1 * dte_factor
            if premium > max_premium or premium < 0.05:
                continue
            day_key = str(bar.get("timestamp", ""))[:10]
            trades_today = sum(1 for t in trades if str(t.get("day_key", "")) == day_key and t.get("symbol") == symbol)
            if trades_today >= max_trades_day:
                continue
            next_close = next_bar["close"]
            next_change_pct = ((next_close - cur_close) / cur_close) * 100
            direction = 1 if daily_change_pct > 0 else -1
            continuation = next_change_pct * direction
            threshold = profit_mult * premium / (cur_close * 0.01)
            if continuation > threshold:
                pnl = profit_mult * premium - premium
                exit_reason = "profit_target"
            else:
                pnl = -premium
                exit_reason = "expired_worthless"
            contracts = max(1, int(min(DAILY_BUDGET, INITIAL_CAPITAL * 0.02) / (premium * 100)))
            trade_pnl = pnl * contracts * 100
            trades.append({"symbol": symbol, "entry_price": premium, "pnl": trade_pnl,
                           "pnl_pct": (trade_pnl / (premium * contracts * 100)) * 100 if premium > 0 else 0,
                           "exit_reason": exit_reason, "day_key": day_key})
    return trades


def simulate_momentum(bars_by_symbol, config):
    sl_pct = config["stop_loss_pct"] / 100
    tp_pct = config["take_profit_pct"] / 100
    trail_pct = config["trailing_stop_pct"] / 100
    trail_activation = config["trailing_activation_pct"] / 100
    notional = DAILY_BUDGET
    trades = []
    for symbol, bars in bars_by_symbol.items():
        if len(bars) < 22:
            continue
        for i in range(21, len(bars)):
            bar = bars[i]
            prev_bar = bars[i - 1]
            price = bar["close"]
            prev_close = prev_bar["close"]
            if prev_close <= 0:
                continue
            sma20 = sum(b["close"] for b in bars[max(0, i-20):i]) / min(20, i)
            if price <= sma20:
                continue
            daily_return = (price - prev_close) / prev_close
            if daily_return < 0.005:
                continue
            vol_avg = sum(b.get("volume", 0) for b in bars[max(0, i-20):i]) / min(20, i)
            cur_vol = bar.get("volume", 0)
            if vol_avg > 0 and cur_vol < vol_avg * 1.2:
                continue
            entry_price = price
            qty = notional / entry_price
            if i + 1 < len(bars):
                next_bar = bars[i + 1]
                tp_price = entry_price * (1 + tp_pct)
                sl_price = entry_price * (1 - sl_pct)
                if next_bar["high"] >= tp_price:
                    exit_price = tp_price
                    exit_reason = "take_profit"
                elif next_bar["low"] <= sl_price:
                    exit_price = sl_price
                    exit_reason = "stop_loss"
                else:
                    profit_pct_cur = (next_bar["close"] - entry_price) / entry_price
                    if profit_pct_cur >= trail_activation:
                        trail_price = next_bar["close"] * (1 - trail_pct)
                        exit_price = max(trail_price, sl_price)
                        exit_reason = "trailing_stop"
                    else:
                        exit_price = next_bar["close"]
                        exit_reason = "eod_close"
                trade_pnl = (exit_price - entry_price) * qty
                trades.append({"symbol": symbol, "entry_price": entry_price, "exit_price": exit_price,
                               "pnl": trade_pnl, "pnl_pct": ((exit_price - entry_price) / entry_price) * 100,
                               "exit_reason": exit_reason})
    return trades


def simulate_crypto(bars_by_symbol, config):
    entry_lookback = config["entry_lookback"]
    exit_lookback = config["exit_lookback"]
    sl_pct = config["stop_loss_pct"] / 100
    tp_pct = config["take_profit_pct"] / 100
    trail_pct = config["trailing_stop_pct"] / 100
    notional = DAILY_BUDGET
    trades = []
    for symbol, bars in bars_by_symbol.items():
        if len(bars) < entry_lookback + 5:
            continue
        in_trade = False
        entry_price = 0
        entry_idx = 0
        high_water = 0
        trail_active = False
        for i in range(entry_lookback + 1, len(bars)):
            bar = bars[i]
            price = bar["close"]
            high = bar["high"]
            low = bar["low"]
            if in_trade:
                if high > high_water:
                    high_water = high
                profit_pct = (price - entry_price) / entry_price
                if low <= entry_price * (1 - sl_pct):
                    trades.append({"symbol": symbol, "entry_price": entry_price,
                                   "exit_price": entry_price * (1 - sl_pct),
                                   "pnl": -sl_pct * notional, "pnl_pct": -sl_pct * 100, "exit_reason": "stop_loss"})
                    in_trade = False
                    continue
                if high >= entry_price * (1 + tp_pct):
                    trades.append({"symbol": symbol, "entry_price": entry_price,
                                   "exit_price": entry_price * (1 + tp_pct),
                                   "pnl": tp_pct * notional, "pnl_pct": tp_pct * 100, "exit_reason": "take_profit"})
                    in_trade = False
                    continue
                if profit_pct > trail_pct * 2:
                    trail_active = True
                if trail_active:
                    trail_price = high_water * (1 - trail_pct)
                    if low <= trail_price:
                        actual_exit = trail_price
                        trade_pnl = ((actual_exit - entry_price) / entry_price) * notional
                        trades.append({"symbol": symbol, "entry_price": entry_price, "exit_price": actual_exit,
                                       "pnl": trade_pnl, "pnl_pct": ((actual_exit - entry_price) / entry_price) * 100,
                                       "exit_reason": "trailing_stop"})
                        in_trade = False
                        continue
                exit_low = min(b["low"] for b in bars[max(0, i - exit_lookback):i + 1])
                if low <= exit_low and i - entry_idx > exit_lookback:
                    trade_pnl = profit_pct * notional
                    trades.append({"symbol": symbol, "entry_price": entry_price, "exit_price": price,
                                   "pnl": trade_pnl, "pnl_pct": profit_pct * 100, "exit_reason": "turtle_exit"})
                    in_trade = False
                    continue
            if not in_trade:
                entry_high = max(b["high"] for b in bars[max(0, i - entry_lookback):i])
                if high >= entry_high:
                    rsi = compute_rsi(bars, i)
                    if rsi is None or rsi < 75:
                        entry_price = price
                        entry_idx = i
                        high_water = high
                        trail_active = False
                        in_trade = True
        if in_trade:
            last_price = bars[-1]["close"]
            pnl_pct = (last_price - entry_price) / entry_price
            trades.append({"symbol": symbol, "entry_price": entry_price, "exit_price": last_price,
                           "pnl": pnl_pct * notional, "pnl_pct": pnl_pct * 100, "exit_reason": "end_of_data"})
    return trades


def simulate_bouncebot(bars_by_symbol, config):
    drawdown_threshold = config["drawdown_threshold_pct"] / 100
    take_profit_pct = config["take_profit_pct"] / 100
    stop_loss_pct = config["stop_loss_pct"] / 100
    lookback = config["lookback_days"]
    max_trades_session = config["max_trades_per_session"]
    position_size = DAILY_BUDGET
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
                    trades.append({"symbol": symbol, "entry_price": entry_price, "exit_price": price,
                                   "pnl": -stop_loss_pct * position_size, "pnl_pct": -stop_loss_pct * 100,
                                   "exit_reason": "stop_loss"})
                    in_trade = False
                    continue
                if pnl_pct_cur >= take_profit_pct:
                    trades.append({"symbol": symbol, "entry_price": entry_price, "exit_price": price,
                                   "pnl": take_profit_pct * position_size, "pnl_pct": take_profit_pct * 100,
                                   "exit_reason": "take_profit"})
                    in_trade = False
                    continue
                hold_bars = i - entry_idx
                if hold_bars >= lookback * 2:
                    trades.append({"symbol": symbol, "entry_price": entry_price, "exit_price": price,
                                   "pnl": pnl_pct_cur * position_size, "pnl_pct": pnl_pct_cur * 100,
                                   "exit_reason": "max_hold"})
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
            trades.append({"symbol": symbol, "entry_price": entry_price, "exit_price": last_bar["close"],
                           "pnl": final_pnl_pct * position_size, "pnl_pct": final_pnl_pct * 100,
                           "exit_reason": "end_of_data"})
    return trades


def simulate_twentyminute(bars_by_symbol, config):
    min_gap_pct = config["min_gap_pct"]
    take_profit_pct = config["take_profit_pct"] / 100
    stop_loss_pct = config["stop_loss_pct"] / 100
    leverage = config["options_leverage"]
    confirmation_bars = config["confirmation_bars"]
    max_trades_day = config["max_trades_per_day"]
    position_size = DAILY_BUDGET
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
            trade_pnl = leveraged_pnl_pct * position_size
            trades.append({"symbol": symbol, "entry_price": entry_price, "exit_price": exit_price,
                           "pnl": trade_pnl, "pnl_pct": leveraged_pnl_pct * 100, "exit_reason": exit_reason})
    return trades


def simulate_credit_spreads(bars_by_symbol, config):
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
            contracts = max(1, int(DAILY_BUDGET / (max_loss * 100))) if max_loss > 0 else 1
            trade_pnl = trade_pnl * min(contracts, 5)
            trades.append({"symbol": symbol, "entry_price": credit, "pnl": trade_pnl,
                           "pnl_pct": (trade_pnl / (max_loss * 100 * contracts)) * 100 if max_loss > 0 and contracts > 0 else 0,
                           "exit_reason": exit_reason})
    return trades


def simulate_whipsaw(bars_by_symbol, config):
    tp_pct = config["take_profit_pct"] / 100
    sl_pct = config["stop_loss_pct"] / 100
    std_mult = config["std_dev_mult"]
    lookback = config["range_lookback"]
    max_hold = config["max_hold_bars"]
    rsi_threshold = config["rsi_oversold"]
    notional = DAILY_BUDGET
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
                    trades.append({"symbol": symbol, "entry_price": entry_price,
                                   "exit_price": entry_price * (1 - sl_pct),
                                   "pnl": -sl_pct * notional, "pnl_pct": -sl_pct * 100, "exit_reason": "stop_loss"})
                    in_trade = False
                    continue
                if high >= entry_price * (1 + tp_pct):
                    trades.append({"symbol": symbol, "entry_price": entry_price,
                                   "exit_price": entry_price * (1 + tp_pct),
                                   "pnl": tp_pct * notional, "pnl_pct": tp_pct * 100, "exit_reason": "take_profit"})
                    in_trade = False
                    continue
                if hold_bars >= max_hold:
                    pnl_pct = (price - entry_price) / entry_price
                    trades.append({"symbol": symbol, "entry_price": entry_price, "exit_price": price,
                                   "pnl": pnl_pct * notional, "pnl_pct": pnl_pct * 100, "exit_reason": "max_hold"})
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


SIMULATORS = {
    "HailMary": simulate_hailmary,
    "MomentumBot": simulate_momentum,
    "CryptoBot": simulate_crypto,
    "BounceBot": simulate_bouncebot,
    "TwentyMinuteBot": simulate_twentyminute,
    "OptionsBot_CreditSpreads": simulate_credit_spreads,
    "WhipsawTrader": simulate_whipsaw,
}


def calculate_metrics(trades):
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
    returns = [t["pnl_pct"] / 100 for t in trades if t.get("pnl_pct", 0) != 0]
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
        (1 if len(pnls) > 15 else 0) * 10
    )
    return {"total_trades": len(pnls), "win_rate": round(win_rate, 4),
            "total_pnl": round(total_pnl, 2), "total_pnl_pct": round(total_pnl_pct, 2),
            "profit_factor": round(profit_factor, 2), "sharpe_ratio": round(sharpe, 2),
            "max_drawdown_pct": round(max_dd * 100, 2), "avg_trade_pnl": round(avg_trade_pnl, 2),
            "composite_score": round(score, 2)}


def sweep_bot(bot_name, bars_by_symbol, param_grid):
    keys = list(param_grid.keys())
    all_combos = list(itertools.product(*param_grid.values()))
    if len(all_combos) > MAX_COMBOS:
        random.seed(42)
        sampled = random.sample(all_combos, MAX_COMBOS)
    else:
        sampled = all_combos

    simulator = SIMULATORS[bot_name]
    results = []
    print(f"\n  Running {len(sampled)} combos for {bot_name}...")

    for idx, combo in enumerate(sampled):
        config = dict(zip(keys, combo))
        try:
            trades = simulator(bars_by_symbol, config)
            metrics = calculate_metrics(trades)
            results.append({"params": config, "metrics": metrics})
        except Exception as e:
            continue
        if (idx + 1) % 100 == 0:
            best = max(results, key=lambda x: x["metrics"]["total_pnl"])
            print(f"    [{idx+1}/{len(sampled)}] Best PnL so far: ${best['metrics']['total_pnl']:,.2f}")

    if not results:
        return None

    results.sort(key=lambda x: (-x["metrics"]["total_pnl"], -x["metrics"]["win_rate"]))
    best = results[0]
    top3 = [{"rank": i+1, "params": r["params"], "metrics": r["metrics"]} for i, r in enumerate(results[:3])]

    m = best["metrics"]
    pf_str = f"{m['profit_factor']:.2f}" if m['profit_factor'] < 100 else "INF"
    print(f"  BEST {bot_name}: PnL=${m['total_pnl']:,.2f} | WR={m['win_rate']*100:.1f}% | "
          f"PF={pf_str} | Trades={m['total_trades']} | Sharpe={m['sharpe_ratio']:.2f}")

    return {
        "bot_name": bot_name,
        "best_params": best["params"],
        "best_metrics": best["metrics"],
        "top_3": top3,
        "total_tested": len(results),
    }


def main():
    parser = argparse.ArgumentParser(description="All Bots $1K/Day Unified Sweep + Ranking")
    parser.add_argument("--days", type=int, default=600, help="Days to backtest")
    args = parser.parse_args()

    print("=" * 100)
    print(f"{'ALL BOTS $1,000/DAY UNIFIED SWEEP + RANKING':^100}")
    print(f"{'='*100}")
    print(f"  Period: {args.days} days | Capital: ${INITIAL_CAPITAL:,.0f} | Daily Budget: ${DAILY_BUDGET:,.0f}/bot")
    print(f"  Bots: {', '.join(BOT_SWEEP_CONFIGS.keys())}")
    print(f"{'='*100}")

    engine = BacktestEngine(initial_capital=INITIAL_CAPITAL)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=args.days)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    all_symbols = set()
    for cfg in BOT_SWEEP_CONFIGS.values():
        all_symbols.update(cfg["symbols"])

    print(f"\nLoading historical data for {len(all_symbols)} symbols ({start_str} to {end_str})...")
    bars_cache = {}
    for symbol in sorted(all_symbols):
        try:
            bars = engine.load_historical_data(symbol, start_str, end_str, "1Day")
            if bars:
                bars_cache[symbol] = bars
                print(f"  {symbol}: {len(bars)} bars")
            else:
                print(f"  {symbol}: No data")
        except Exception as e:
            print(f"  {symbol}: Error - {e}")

    if not bars_cache:
        print("FATAL: No data loaded. Check Alpaca credentials.")
        sys.exit(1)

    bot_results = []
    for bot_name, bot_cfg in BOT_SWEEP_CONFIGS.items():
        symbols = bot_cfg["symbols"]
        bot_bars = {s: bars_cache[s] for s in symbols if s in bars_cache}
        if not bot_bars:
            print(f"\n  SKIP {bot_name}: No data for symbols {symbols}")
            continue

        result = sweep_bot(bot_name, bot_bars, bot_cfg["param_grid"])
        if result:
            bot_results.append(result)

    bot_results.sort(key=lambda x: (-x["best_metrics"]["total_pnl"], -x["best_metrics"]["win_rate"]))

    print(f"\n\n{'='*110}")
    print(f"{'BOT RANKING — $1,000/DAY BUDGET — SORTED BY PROFITABILITY, THEN WIN RATE':^110}")
    print(f"{'='*110}")
    header = (f"{'Rank':<5} {'Bot':<25} {'Total PnL':>13} {'PnL%':>8} {'WR%':>7} {'PF':>7} "
              f"{'Sharpe':>7} {'MaxDD%':>7} {'Trades':>7} {'AvgPnL':>10} {'Score':>8}")
    print(f"\n{header}")
    print("-" * len(header))

    for rank, result in enumerate(bot_results, 1):
        m = result["best_metrics"]
        pf_str = f"{m['profit_factor']:.2f}" if m['profit_factor'] < 100 else "INF"
        print(f"{rank:<5} {result['bot_name']:<25} ${m['total_pnl']:>11,.2f} {m['total_pnl_pct']:>7.1f}% "
              f"{m['win_rate']*100:>6.1f}% {pf_str:>7} {m['sharpe_ratio']:>7.2f} {m['max_drawdown_pct']:>6.1f}% "
              f"{m['total_trades']:>7} ${m['avg_trade_pnl']:>8,.2f} {m['composite_score']:>8.1f}")

    print(f"\n{'='*110}")
    print(f"{'BEST CONFIG PER BOT':^110}")
    print(f"{'='*110}")
    for rank, result in enumerate(bot_results, 1):
        print(f"\n  #{rank} {result['bot_name']} (tested {result['total_tested']} configs)")
        print(f"  {'—'*50}")
        for k, v in result["best_params"].items():
            print(f"    {k:<30} = {v}")
        m = result["best_metrics"]
        print(f"    {'—'*40}")
        print(f"    PnL: ${m['total_pnl']:,.2f} ({m['total_pnl_pct']:.1f}%) | WR: {m['win_rate']*100:.1f}% | "
              f"PF: {m['profit_factor']:.2f} | Sharpe: {m['sharpe_ratio']:.2f}")

    output = {
        "sweep_date": datetime.now().isoformat(),
        "period_days": args.days,
        "initial_capital": INITIAL_CAPITAL,
        "daily_budget": DAILY_BUDGET,
        "ranking_criteria": "profitability_first_winrate_second",
        "bot_ranking": [
            {
                "rank": rank,
                "bot_name": r["bot_name"],
                "best_params": r["best_params"],
                "best_metrics": r["best_metrics"],
                "top_3": r["top_3"],
                "configs_tested": r["total_tested"],
            }
            for rank, r in enumerate(bot_results, 1)
        ],
    }

    os.makedirs("export/results", exist_ok=True)
    output_path = "export/results/sweep_all_1k.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n\nResults saved to {output_path}")
    print(f"{'='*110}")
    print("SWEEP COMPLETE.")


if __name__ == "__main__":
    main()
