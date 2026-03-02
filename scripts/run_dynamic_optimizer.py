#!/usr/bin/env python3
"""
Dynamic Parameter Optimizer - Comprehensive Variable Sweep
============================================================
Sweeps parameters across ALL 5 trading bots PLUS ProfitSniper exit
intelligence PLUS ExitBot exit management, simulated under 3 market
regimes (LOW VIX, NORMAL, STRESS).

Includes auto-doubling convergence, tiered take-profit simulation,
profit velocity/ratchet/exhaustion exit logic, and per-regime + 
all-weather config selection.
"""
import os, sys, json, random, time, argparse
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.trading_hydra.core.logging import get_logger
from src.trading_hydra.backtest.backtest_engine import BacktestEngine

logger = get_logger()

ACCOUNT_SIZE = 47000.0
DAILY_TARGET = 500.0

BOT_CONFIGS = {
    "momentum": {
        "symbols": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "META"],
        "params": {
            "entry_lookback": [3, 5, 10, 15, 20],
            "exit_lookback": [3, 5, 10, 15],
            "stop_loss_pct": [2, 3, 5, 8],
            "take_profit_pct": [5, 10, 15, 20, 30],
            "position_size_pct": [6, 8, 10, 12, 15],
        }
    },
    "crypto": {
        "symbols": ["BTC/USD", "ETH/USD", "SOL/USD"],
        "params": {
            "entry_lookback": [5, 10, 15, 20, 30],
            "exit_lookback": [5, 10, 15, 20],
            "stop_loss_pct": [1, 2, 3, 5],
            "take_profit_pct": [2, 3, 5, 8, 10],
            "rsi_oversold": [20, 25, 30, 35],
            "rsi_overbought": [65, 70, 75, 80],
            "position_size_pct": [6, 8, 10, 12, 15],
        }
    },
    "whipsaw": {
        "symbols": ["SPY", "QQQ", "AAPL", "MSFT"],
        "params": {
            "lookback_bars": [20, 30, 50, 80, 100],
            "std_dev_mult": [1.5, 2.0, 2.5, 3.0],
            "support_buffer_pct": [0.1, 0.2, 0.3, 0.5],
            "take_profit_pct": [0.5, 1.0, 1.5, 2.0, 3.0],
            "stop_loss_pct": [2, 3, 5],
            "trailing_stop_pct": [0.2, 0.3, 0.5, 0.8],
            "trailing_activation_pct": [0.3, 0.5, 0.8, 1.0],
            "position_size_pct": [4, 6, 8, 10, 15],
        }
    },
    "bouncebot": {
        "symbols": ["BTC/USD", "ETH/USD", "SOL/USD"],
        "params": {
            "drawdown_threshold_pct": [2.0, 3.0, 3.5, 5.0, 7.0],
            "rsi_oversold": [15, 20, 25, 30, 35],
            "take_profit_pct": [1.0, 1.5, 2.0, 3.0, 4.0],
            "stop_loss_pct": [2, 3, 5],
            "position_size_pct": [6, 8, 10, 12, 15],
        }
    },
    "twentyminute": {
        "symbols": ["SPY", "QQQ", "AAPL", "NVDA", "TSLA"],
        "params": {
            "min_gap_pct": [0.1, 0.25, 0.5, 0.75, 1.0, 1.5],
            "stop_loss_pct": [1, 2, 3],
            "take_profit_pct": [1, 2, 3, 5],
            "max_hold_bars": [5, 10, 15, 30],
            "confirmation_bars": [1, 2, 3],
            "position_size_pct": [6, 8, 10, 12],
        }
    },
}

SYSTEM_PARAMS = {
    "min_position_usd": [300, 500, 750, 1000],
}

SNIPER_PARAMS = {
    "velocity_window": [3, 5, 7, 10],
    "velocity_reversal_pct": [0.2, 0.3, 0.5, 0.8],
    "ratchet_arm_pct": [0.3, 0.5, 1.0, 2.0],
    "ratchet_base_distance_pct": [0.15, 0.25, 0.4, 0.6],
    "ratchet_tighten_per_pct": [0.02, 0.03, 0.05, 0.08],
    "ratchet_min_distance_pct": [0.05, 0.08, 0.12, 0.2],
    "exhaustion_bars": [2, 3, 4, 5],
    "exhaustion_min_profit_pct": [0.2, 0.3, 0.5, 1.0],
}

EXITBOT_PARAMS = {
    "tp1_pct": [1.0, 2.0, 3.0, 4.0],
    "tp1_exit_pct": [0.25, 0.33, 0.50],
    "tp2_pct": [3.0, 5.0, 8.0, 10.0],
    "tp2_exit_pct": [0.33, 0.50, 0.75],
    "tp3_pct": [8.0, 15.0, 20.0, 30.0],
    "hard_stop_pct": [8.0, 12.0, 15.0, 20.0],
    "reversal_sense_drop_pct": [1.0, 1.5, 2.0, 3.0, 5.0],
    "reversal_sense_min_gain_pct": [0.3, 0.5, 1.0, 2.0],
}

REGIMES = {
    "LOW": {"size_multiplier": 1.10, "vol_adjust": -0.20},
    "NORMAL": {"size_multiplier": 1.00, "vol_adjust": 0.0},
    "STRESS": {"size_multiplier": 0.60, "vol_adjust": 0.30},
}


@dataclass
class Trade:
    symbol: str
    side: str
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    quantity: float = 1.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    partial_exits: Optional[List[Dict[str, Any]]] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.exit_time is None

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "entry_price": self.entry_price,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "quantity": self.quantity,
            "pnl": round(self.pnl, 4),
            "pnl_pct": round(self.pnl_pct, 4),
            "partial_exits": self.partial_exits or [],
        }


def parse_bar_timestamp(bar):
    raw_ts = bar.get("timestamp", bar.get("t", None))
    if isinstance(raw_ts, datetime):
        return raw_ts
    elif isinstance(raw_ts, (int, float)) and raw_ts > 0:
        return datetime.fromtimestamp(raw_ts / 1000 if raw_ts > 1e12 else raw_ts)
    return datetime.now()


def get_bar_val(bar, key_full, key_short, default=0):
    return bar.get(key_full, bar.get(key_short, default))


def _calc_rsi(bars, idx, period=14):
    if idx < period + 1:
        return None
    gains = []
    losses = []
    for k in range(idx - period, idx):
        prev_c = get_bar_val(bars[k - 1], "close", "c", 0)
        curr_c = get_bar_val(bars[k], "close", "c", 0)
        if prev_c <= 0:
            continue
        change = curr_c - prev_c
        if change >= 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    if not gains:
        return None
    avg_gain = sum(gains) / len(gains)
    avg_loss = sum(losses) / len(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def apply_regime_to_bars(bars, regime):
    if regime == "NORMAL":
        return bars
    vol_adjust = REGIMES[regime]["vol_adjust"]
    adjusted = []
    for bar in bars:
        new_bar = dict(bar)
        close = get_bar_val(bar, "close", "c", 0)
        high = get_bar_val(bar, "high", "h", close)
        low = get_bar_val(bar, "low", "l", close)
        open_p = get_bar_val(bar, "open", "o", close)
        if close <= 0:
            adjusted.append(new_bar)
            continue
        range_half = (high - low) / 2.0
        mid = (high + low) / 2.0
        new_range_half = range_half * (1 + vol_adjust)
        new_high = mid + new_range_half
        new_low = mid - new_range_half
        if new_low <= 0:
            new_low = close * 0.001
        for k in ("high", "h"):
            if k in new_bar:
                new_bar[k] = new_high
        for k in ("low", "l"):
            if k in new_bar:
                new_bar[k] = new_low
        if open_p > new_high:
            for k in ("open", "o"):
                if k in new_bar:
                    new_bar[k] = new_high
        elif open_p < new_low:
            for k in ("open", "o"):
                if k in new_bar:
                    new_bar[k] = new_low
        adjusted.append(new_bar)
    return adjusted


def simulate_strategy(bot_name, symbol, bars, config, capital, regime="NORMAL"):
    trades = []
    if len(bars) < 30:
        return trades

    stop_loss_pct = config.get("stop_loss_pct", 5.0) / 100
    take_profit_pct = config.get("take_profit_pct", 10.0) / 100
    lookback = config.get("entry_lookback", config.get("lookback_bars", 20))
    exit_lookback = config.get("exit_lookback", max(lookback // 2, 2))

    size_mult = REGIMES.get(regime, {}).get("size_multiplier", 1.0)
    pos_size_pct = config.get("position_size_pct", 8) / 100 * size_mult
    min_pos_usd = config.get("min_position_usd", 500)
    position_size = max(min_pos_usd, capital * pos_size_pct)

    max_hold = config.get("max_hold_bars", 50)
    trailing_stop_pct = config.get("trailing_stop_pct", 0.3) / 100
    trailing_activation_pct = config.get("trailing_activation_pct", 0.5) / 100
    support_buffer_pct = config.get("support_buffer_pct", 0.2) / 100
    confirmation_bars_needed = config.get("confirmation_bars", 1)
    rsi_oversold = config.get("rsi_oversold", 30)
    rsi_overbought = config.get("rsi_overbought", 70)

    sniper_velocity_window = config.get("velocity_window", 5)
    sniper_velocity_reversal = config.get("velocity_reversal_pct", 0.3) / 100
    sniper_ratchet_arm = config.get("ratchet_arm_pct", 0.5) / 100
    sniper_ratchet_base_dist = config.get("ratchet_base_distance_pct", 0.25) / 100
    sniper_ratchet_tighten = config.get("ratchet_tighten_per_pct", 0.03) / 100
    sniper_ratchet_min_dist = config.get("ratchet_min_distance_pct", 0.08) / 100
    sniper_exhaustion_bars = config.get("exhaustion_bars", 3)
    sniper_exhaustion_min_profit = config.get("exhaustion_min_profit_pct", 0.3) / 100

    eb_tp1_pct = config.get("tp1_pct", 2.0) / 100
    eb_tp1_exit = config.get("tp1_exit_pct", 0.33)
    eb_tp2_pct = config.get("tp2_pct", 5.0) / 100
    eb_tp2_exit = config.get("tp2_exit_pct", 0.50)
    eb_tp3_pct = config.get("tp3_pct", 15.0) / 100
    eb_hard_stop = config.get("hard_stop_pct", 12.0) / 100
    eb_reversal_drop = config.get("reversal_sense_drop_pct", 2.0) / 100
    eb_reversal_min_gain = config.get("reversal_sense_min_gain_pct", 0.5) / 100

    regime_bars = apply_regime_to_bars(bars, regime)

    i = max(lookback, 15)
    while i < len(regime_bars) - 10:
        bar = regime_bars[i]
        entry_price = get_bar_val(bar, "close", "c", 100)
        if entry_price <= 0:
            i += 1
            continue

        should_enter = False
        local_resistance = 0

        if bot_name == "momentum":
            highs = [get_bar_val(b, "high", "h", 0) for b in regime_bars[i - lookback:i]]
            if highs and entry_price > max(highs) * 0.99:
                should_enter = True

        elif bot_name == "crypto":
            closes = [get_bar_val(b, "close", "c", 0) for b in regime_bars[i - lookback:i]]
            if len(closes) >= 2:
                sma = sum(closes) / len(closes)
                if entry_price > sma:
                    rsi = _calc_rsi(regime_bars, i, 14)
                    if rsi is not None and rsi_oversold < rsi < rsi_overbought:
                        should_enter = True

        elif bot_name == "whipsaw":
            closes = [get_bar_val(b, "close", "c", 0) for b in regime_bars[i - lookback:i]]
            lows_lb = [get_bar_val(b, "low", "l", 0) for b in regime_bars[i - lookback:i]]
            highs_lb = [get_bar_val(b, "high", "h", 0) for b in regime_bars[i - lookback:i]]
            if closes and lows_lb and highs_lb:
                support = min(lows_lb)
                local_resistance = max(highs_lb)
                range_size = local_resistance - support
                if range_size / entry_price >= 0.005:
                    support_zone = support * (1 + support_buffer_pct)
                    mean = sum(closes) / len(closes)
                    std = (sum((c - mean) ** 2 for c in closes) / len(closes)) ** 0.5
                    if std > 0 and entry_price <= support_zone and entry_price < mean - config.get("std_dev_mult", 1.5) * std:
                        should_enter = True

        elif bot_name == "bouncebot":
            lb = min(20, i)
            recent_high = max(get_bar_val(b, "high", "h", 0) for b in regime_bars[i - lb:i])
            if recent_high > 0:
                drawdown = (recent_high - entry_price) / recent_high * 100
                if drawdown >= config.get("drawdown_threshold_pct", 2.0):
                    rsi = _calc_rsi(regime_bars, i, 14)
                    if rsi is not None and rsi <= rsi_oversold:
                        should_enter = True
                    elif rsi is None:
                        should_enter = True

        elif bot_name == "twentyminute":
            if i >= confirmation_bars_needed:
                prev_close = get_bar_val(regime_bars[i - 1], "close", "c", 0)
                if prev_close > 0:
                    gap = (entry_price - prev_close) / prev_close * 100
                    if gap >= config.get("min_gap_pct", 0.1):
                        confirmed = True
                        for cb in range(1, min(confirmation_bars_needed, i)):
                            cb_close = get_bar_val(regime_bars[i - cb], "close", "c", 0)
                            cb_open = get_bar_val(regime_bars[i - cb], "open", "o", 0)
                            if cb_close < cb_open:
                                confirmed = False
                                break
                        if confirmed:
                            should_enter = True

        if should_enter:
            qty = position_size / entry_price
            remaining_qty = qty
            stop_price = entry_price * (1 - stop_loss_pct)
            target_price = entry_price * (1 + take_profit_pct)

            exit_price = entry_price
            exit_reason = "max_hold"
            exit_bar = i
            trailing_stop_price = 0.0
            high_water = entry_price
            partial_exits = []
            total_partial_pnl = 0.0

            tp1_hit = False
            tp2_hit = False
            tp3_hit = False

            ratchet_price = 0.0
            ratchet_armed = False

            profit_history = []
            peak_velocity = 0.0
            prev_bar_gain = None
            consecutive_weak = 0

            search_limit = min(i + max_hold, len(regime_bars))
            for j in range(i + 1, search_limit):
                if remaining_qty <= 0:
                    break

                bar_j = regime_bars[j]
                low = get_bar_val(bar_j, "low", "l", 0)
                high = get_bar_val(bar_j, "high", "h", 0)
                close = get_bar_val(bar_j, "close", "c", 0)

                if high > high_water:
                    high_water = high

                current_profit_pct = (close - entry_price) / entry_price
                profit_history.append(current_profit_pct)

                current_bar_gain = (close - get_bar_val(regime_bars[j - 1], "close", "c", close)) / entry_price if entry_price > 0 else 0

                sniper_exit = False
                sniper_reason = ""

                if len(profit_history) >= sniper_velocity_window:
                    window = profit_history[-sniper_velocity_window:]
                    velocity = window[-1] - window[0]
                    if velocity > peak_velocity:
                        peak_velocity = velocity
                    if peak_velocity > 0 and (peak_velocity - velocity) >= sniper_velocity_reversal and current_profit_pct > 0:
                        sniper_exit = True
                        sniper_reason = "velocity_reversal"

                if not sniper_exit and current_profit_pct >= sniper_ratchet_arm:
                    ratchet_armed = True
                    extra_profit = current_profit_pct - sniper_ratchet_arm
                    tighten_amount = extra_profit * sniper_ratchet_tighten * 100
                    distance = max(sniper_ratchet_base_dist - tighten_amount, sniper_ratchet_min_dist)
                    new_ratchet = high_water * (1 - distance)
                    if new_ratchet > ratchet_price:
                        ratchet_price = new_ratchet
                    if ratchet_armed and ratchet_price > 0 and low <= ratchet_price:
                        sniper_exit = True
                        sniper_reason = "ratchet"

                if not sniper_exit and current_profit_pct >= sniper_exhaustion_min_profit:
                    if prev_bar_gain is not None and current_bar_gain < prev_bar_gain:
                        consecutive_weak += 1
                    else:
                        consecutive_weak = 0
                    if consecutive_weak >= sniper_exhaustion_bars:
                        sniper_exit = True
                        sniper_reason = "exhaustion"

                prev_bar_gain = current_bar_gain

                if sniper_exit:
                    exit_price = close
                    exit_reason = sniper_reason
                    exit_bar = j
                    break

                eb_exit = False
                eb_reason = ""

                if not tp1_hit and high >= entry_price * (1 + eb_tp1_pct):
                    tp1_hit = True
                    tp1_price = entry_price * (1 + eb_tp1_pct)
                    exit_qty = remaining_qty * eb_tp1_exit
                    if exit_qty > 0:
                        pnl_partial = (tp1_price - entry_price) * exit_qty
                        partial_exits.append({"qty": exit_qty, "price": tp1_price, "reason": "tp1", "pnl": pnl_partial})
                        total_partial_pnl += pnl_partial
                        remaining_qty -= exit_qty

                if not tp2_hit and remaining_qty > 0 and high >= entry_price * (1 + eb_tp2_pct):
                    tp2_hit = True
                    tp2_price = entry_price * (1 + eb_tp2_pct)
                    exit_qty = remaining_qty * eb_tp2_exit
                    if exit_qty > 0:
                        pnl_partial = (tp2_price - entry_price) * exit_qty
                        partial_exits.append({"qty": exit_qty, "price": tp2_price, "reason": "tp2", "pnl": pnl_partial})
                        total_partial_pnl += pnl_partial
                        remaining_qty -= exit_qty

                if not tp3_hit and remaining_qty > 0 and high >= entry_price * (1 + eb_tp3_pct):
                    tp3_hit = True
                    tp3_price = entry_price * (1 + eb_tp3_pct)
                    exit_qty = remaining_qty
                    pnl_partial = (tp3_price - entry_price) * exit_qty
                    partial_exits.append({"qty": exit_qty, "price": tp3_price, "reason": "tp3", "pnl": pnl_partial})
                    total_partial_pnl += pnl_partial
                    remaining_qty = 0
                    exit_price = tp3_price
                    exit_reason = "tp3"
                    exit_bar = j
                    break

                hw_gain = (high_water - entry_price) / entry_price if entry_price > 0 else 0
                if remaining_qty > 0 and hw_gain >= eb_reversal_min_gain:
                    drop_from_hw = (high_water - close) / high_water if high_water > 0 else 0
                    if drop_from_hw >= eb_reversal_drop:
                        eb_exit = True
                        eb_reason = "reversal_sense"

                if remaining_qty > 0 and not eb_exit:
                    loss_pct = (entry_price - low) / entry_price if entry_price > 0 else 0
                    if loss_pct >= eb_hard_stop:
                        eb_exit = True
                        eb_reason = "hard_stop"

                if eb_exit:
                    exit_price = close
                    exit_reason = eb_reason
                    exit_bar = j
                    break

                if remaining_qty > 0 and low <= stop_price:
                    exit_price = stop_price
                    exit_reason = "stop_loss"
                    exit_bar = j
                    break
                elif remaining_qty > 0 and high >= target_price:
                    exit_price = target_price
                    exit_reason = "take_profit"
                    exit_bar = j
                    break

                pnl_from_entry = (high_water - entry_price) / entry_price
                if remaining_qty > 0 and pnl_from_entry >= trailing_activation_pct and trailing_stop_pct > 0:
                    new_trail = high_water * (1 - trailing_stop_pct)
                    trailing_stop_price = max(trailing_stop_price, new_trail)
                    if low <= trailing_stop_price:
                        exit_price = trailing_stop_price
                        exit_reason = "trailing_stop"
                        exit_bar = j
                        break

                if remaining_qty > 0 and bot_name in ("momentum", "crypto") and j >= exit_lookback + 1:
                    exit_lows = [get_bar_val(regime_bars[k], "low", "l", 0) for k in range(j - exit_lookback, j)]
                    if exit_lows and close < min(exit_lows):
                        exit_price = close
                        exit_reason = "exit_lookback"
                        exit_bar = j
                        break

                if remaining_qty > 0 and bot_name == "whipsaw" and local_resistance > 0:
                    resistance_zone = local_resistance * (1 - support_buffer_pct)
                    if close >= resistance_zone:
                        exit_price = close
                        exit_reason = "take_profit"
                        exit_bar = j
                        break

                exit_bar = j
                exit_price = close

            final_pnl = total_partial_pnl
            if remaining_qty > 0:
                final_pnl += (exit_price - entry_price) * remaining_qty

            pnl_pct = (final_pnl / (qty * entry_price) * 100) if (qty * entry_price) > 0 else 0

            entry_time = parse_bar_timestamp(bar)
            exit_time = entry_time + timedelta(hours=max(exit_bar - i, 1))

            trade = Trade(
                symbol=symbol,
                side="buy",
                entry_time=entry_time,
                entry_price=entry_price,
                exit_time=exit_time,
                exit_price=exit_price,
                exit_reason=exit_reason,
                quantity=qty,
                pnl=final_pnl,
                pnl_pct=pnl_pct,
                partial_exits=partial_exits if partial_exits else [],
            )
            trades.append(trade)
            i = exit_bar + 5
        else:
            i += 1

    return trades


def calculate_metrics(trades, days):
    if not trades:
        return {
            "total_pnl": 0, "win_rate": 0, "profit_factor": 0,
            "sharpe_ratio": 0, "avg_daily_pnl": 0, "max_drawdown": 0,
            "num_trades": 0, "avg_pnl_per_trade": 0,
            "exit_reasons": {},
        }

    total_pnl = sum(t.pnl for t in trades)
    winners = [t for t in trades if t.pnl > 0]
    losers = [t for t in trades if t.pnl <= 0]
    win_rate = len(winners) / len(trades) if trades else 0

    gross_profit = sum(t.pnl for t in winners) if winners else 0
    gross_loss = abs(sum(t.pnl for t in losers)) if losers else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (10.0 if gross_profit > 0 else 0)

    daily_pnls = defaultdict(float)
    for t in trades:
        day_key = t.entry_time.strftime("%Y-%m-%d") if t.entry_time else "unknown"
        daily_pnls[day_key] += t.pnl

    daily_values = list(daily_pnls.values()) if daily_pnls else [0]
    avg_daily = sum(daily_values) / max(len(daily_values), 1)

    if len(daily_values) > 1:
        mean_d = sum(daily_values) / len(daily_values)
        variance = sum((d - mean_d) ** 2 for d in daily_values) / len(daily_values)
        std_d = variance ** 0.5
        sharpe = (mean_d / std_d * (252 ** 0.5)) if std_d > 0 else 0
    else:
        sharpe = 0

    cumulative = 0
    peak = 0
    max_dd = 0
    for t in sorted(trades, key=lambda x: x.entry_time if x.entry_time else datetime.now()):
        cumulative += t.pnl
        if cumulative > peak:
            peak = cumulative
        dd = (peak - cumulative) / ACCOUNT_SIZE * 100 if ACCOUNT_SIZE > 0 else 0
        if dd > max_dd:
            max_dd = dd

    exit_reasons = defaultdict(int)
    for t in trades:
        exit_reasons[t.exit_reason or "unknown"] += 1
        for pe in (t.partial_exits or []):
            exit_reasons[pe.get("reason", "partial")] += 1

    avg_pnl_per_trade = total_pnl / len(trades) if trades else 0

    return {
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 2),
        "sharpe_ratio": round(sharpe, 2),
        "avg_daily_pnl": round(avg_daily, 2),
        "max_drawdown": round(max_dd, 2),
        "num_trades": len(trades),
        "avg_pnl_per_trade": round(avg_pnl_per_trade, 2),
        "exit_reasons": dict(exit_reasons),
    }


def sample_random_combo(bot_params):
    combo = {}
    for key, values in bot_params.items():
        combo[key] = random.choice(values)
    combo["min_position_usd"] = random.choice(SYSTEM_PARAMS["min_position_usd"])
    for key, values in SNIPER_PARAMS.items():
        combo[key] = random.choice(values)
    for key, values in EXITBOT_PARAMS.items():
        combo[key] = random.choice(values)
    return combo


def try_load_disk_cache(days):
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    all_symbols = set()
    for bot_cfg in BOT_CONFIGS.values():
        for s in bot_cfg["symbols"]:
            all_symbols.add(s)

    disk_cache_path = f"/tmp/optimizer_data_cache_{days}d.json"
    if os.path.exists(disk_cache_path):
        try:
            cache_age = time.time() - os.path.getmtime(disk_cache_path)
            if cache_age < 7200:
                with open(disk_cache_path, "r") as f:
                    cached = json.load(f)
                if all(s in cached and cached[s] for s in all_symbols):
                    loaded = sum(1 for v in cached.values() if v)
                    print(f"\n[CACHE] Using disk cache ({loaded} symbols, age {cache_age:.0f}s)")
                    print(f"\n{'='*70}")
                    print(f"  DYNAMIC PARAMETER OPTIMIZER v2.0")
                    print(f"  Account: ${ACCOUNT_SIZE:,.0f} | Target: ${DAILY_TARGET}/day")
                    print(f"  Period: {days} days ({start_str} to {end_str})")
                    print(f"  Regimes: LOW, NORMAL, STRESS")
                    print(f"  Exit Layers: ProfitSniper + ExitBot + Standard")
                    print(f"{'='*70}")
                    return cached
        except Exception as e:
            print(f"[CACHE] Could not load disk cache: {e}")
    return None


def cache_all_data(engine, days):
    import signal

    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    all_symbols = set()
    for bot_cfg in BOT_CONFIGS.values():
        for s in bot_cfg["symbols"]:
            all_symbols.add(s)

    disk_cache_path = f"/tmp/optimizer_data_cache_{days}d.json"
    if os.path.exists(disk_cache_path):
        try:
            cache_age = time.time() - os.path.getmtime(disk_cache_path)
            if cache_age < 3600:
                with open(disk_cache_path, "r") as f:
                    cached = json.load(f)
                if all(s in cached and cached[s] for s in all_symbols):
                    print(f"\n[CACHE] Using disk cache ({len(cached)} symbols, age {cache_age:.0f}s)")
                    print(f"\n{'='*70}")
                    print(f"  DYNAMIC PARAMETER OPTIMIZER v2.0")
                    print(f"  Account: ${ACCOUNT_SIZE:,.0f} | Target: ${DAILY_TARGET}/day")
                    print(f"  Period: {days} days ({start_str} to {end_str})")
                    print(f"  Regimes: LOW, NORMAL, STRESS")
                    print(f"  Exit Layers: ProfitSniper + ExitBot + Standard")
                    print(f"{'='*70}")
                    return cached, days
        except Exception:
            pass

    data_cache = {}
    total = len(all_symbols)
    print(f"\n{'='*70}")
    print(f"  DYNAMIC PARAMETER OPTIMIZER v2.0")
    print(f"  Account: ${ACCOUNT_SIZE:,.0f} | Target: ${DAILY_TARGET}/day")
    print(f"  Period: {days} days ({start_str} to {end_str})")
    print(f"  Regimes: LOW, NORMAL, STRESS")
    print(f"  Exit Layers: ProfitSniper + ExitBot + Standard")
    print(f"{'='*70}")
    print(f"\n[CACHE] Loading historical data for {total} symbols...")

    def _timeout_handler(signum, frame):
        raise TimeoutError("API call timed out")

    for idx, symbol in enumerate(sorted(all_symbols), 1):
        print(f"  [{idx}/{total}] Loading {symbol}...", end=" ", flush=True)
        try:
            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(30)
            bars = engine.load_historical_data(symbol, start_str, end_str, "1Hour")
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
            data_cache[symbol] = bars
            print(f"{len(bars)} bars loaded")
        except TimeoutError:
            signal.alarm(0)
            print(f"TIMEOUT (30s)")
            data_cache[symbol] = []
        except Exception as e:
            signal.alarm(0)
            print(f"FAILED: {e}")
            data_cache[symbol] = []
        if idx < total:
            time.sleep(0.5)

    loaded = sum(1 for v in data_cache.values() if v)
    print(f"\n[CACHE] Done. {loaded}/{total} symbols loaded successfully.")

    if loaded > 0:
        cache_serializable = {}
        for sym, bars in data_cache.items():
            cache_serializable[sym] = []
            for b in bars:
                bar_copy = {}
                for k, v in b.items():
                    if isinstance(v, datetime):
                        bar_copy[k] = v.isoformat()
                    else:
                        bar_copy[k] = v
                cache_serializable[sym].append(bar_copy)
        try:
            with open(disk_cache_path, "w") as f:
                json.dump(cache_serializable, f)
            print(f"[CACHE] Saved to disk cache: {disk_cache_path}")
        except Exception as e:
            print(f"[CACHE] Warning: Could not save disk cache: {e}")

    print()
    return data_cache, days


def optimize_bot_regime(bot_name, bot_cfg, data_cache, num_combos, days, regime):
    symbols = bot_cfg["symbols"]
    params = bot_cfg["params"]
    results = []

    available_symbols = [s for s in symbols if data_cache.get(s)]
    if not available_symbols:
        return results

    start_time = time.time()

    for idx in range(num_combos):
        combo = sample_random_combo(params)
        all_trades = []

        for symbol in available_symbols:
            bars = data_cache[symbol]
            if not bars:
                continue
            trades = simulate_strategy(bot_name, symbol, bars, combo, ACCOUNT_SIZE, regime)
            all_trades.extend(trades)

        metrics = calculate_metrics(all_trades, days)
        results.append({
            "config": combo.copy(),
            "metrics": metrics,
        })

        if (idx + 1) % 100 == 0 or idx == 0:
            elapsed = time.time() - start_time
            rate = (idx + 1) / elapsed if elapsed > 0 else 0
            remaining = (num_combos - idx - 1) / rate if rate > 0 else 0
            best_pnl = max((r["metrics"]["total_pnl"] for r in results), default=0)
            print(f"    [{idx+1}/{num_combos}] "
                  f"Best PnL: ${best_pnl:,.2f} | "
                  f"Rate: {rate:.1f}/sec | "
                  f"ETA: {remaining:.0f}s")

    return results


def optimize_bot_all_regimes(bot_name, bot_cfg, data_cache, num_combos, days):
    print(f"\n{'─'*60}")
    print(f"  Optimizing: {bot_name.upper()}")
    available = [s for s in bot_cfg["symbols"] if data_cache.get(s)]
    print(f"  Symbols: {', '.join(available)}")
    print(f"  Combinations per regime: {num_combos}")
    print(f"{'─'*60}")

    regime_results = {}
    for regime in ["LOW", "NORMAL", "STRESS"]:
        print(f"\n  [REGIME: {regime}]")
        results = optimize_bot_regime(bot_name, bot_cfg, data_cache, num_combos, days, regime)
        regime_results[regime] = results
        if results:
            best = max(r["metrics"]["total_pnl"] for r in results)
            print(f"  {regime} done: {len(results)} combos, best PnL=${best:,.2f}")

    return regime_results


def select_tier_configs(all_regime_results, regime):
    tiers = {
        "tier1_profitable": {},
        "tier2_balanced": {},
        "tier3_conservative": {},
    }

    for bot_name, regime_data in all_regime_results.items():
        results = regime_data.get(regime, [])
        if not results:
            continue

        sorted_by_pnl = sorted(results, key=lambda x: x["metrics"]["total_pnl"], reverse=True)
        if sorted_by_pnl:
            tiers["tier1_profitable"][bot_name] = sorted_by_pnl[0]

        balanced = [
            r for r in results
            if r["metrics"]["win_rate"] >= 0.55
            and r["metrics"]["profit_factor"] >= 1.5
            and r["metrics"]["sharpe_ratio"] >= 3
            and r["metrics"]["total_pnl"] > 0
        ]
        if balanced:
            tiers["tier2_balanced"][bot_name] = sorted(balanced, key=lambda x: x["metrics"]["total_pnl"], reverse=True)[0]
        elif sorted_by_pnl and sorted_by_pnl[0]["metrics"]["total_pnl"] > 0:
            tiers["tier2_balanced"][bot_name] = sorted_by_pnl[0]

        conservative = [
            r for r in results
            if r["metrics"]["win_rate"] >= 0.65
            and r["metrics"]["profit_factor"] >= 2.0
            and r["metrics"]["max_drawdown"] < 10
            and r["metrics"]["sharpe_ratio"] >= 5
            and r["metrics"]["total_pnl"] > 0
        ]
        if conservative:
            tiers["tier3_conservative"][bot_name] = sorted(conservative, key=lambda x: x["metrics"]["sharpe_ratio"], reverse=True)[0]
        elif sorted_by_pnl and sorted_by_pnl[0]["metrics"]["total_pnl"] > 0:
            safe = [r for r in results if r["metrics"]["win_rate"] >= 0.50 and r["metrics"]["total_pnl"] > 0]
            if safe:
                tiers["tier3_conservative"][bot_name] = sorted(safe, key=lambda x: x["metrics"]["sharpe_ratio"], reverse=True)[0]

    return tiers


def select_allweather_tiers(all_regime_results):
    tiers = {
        "tier1_profitable": {},
        "tier2_balanced": {},
        "tier3_conservative": {},
    }

    for bot_name, regime_data in all_regime_results.items():
        combined = []
        for regime in ["LOW", "NORMAL", "STRESS"]:
            combined.extend(regime_data.get(regime, []))
        if not combined:
            continue

        sorted_by_pnl = sorted(combined, key=lambda x: x["metrics"]["total_pnl"], reverse=True)
        if sorted_by_pnl:
            tiers["tier1_profitable"][bot_name] = sorted_by_pnl[0]

        balanced = [
            r for r in combined
            if r["metrics"]["win_rate"] >= 0.55
            and r["metrics"]["profit_factor"] >= 1.5
            and r["metrics"]["sharpe_ratio"] >= 3
            and r["metrics"]["total_pnl"] > 0
        ]
        if balanced:
            tiers["tier2_balanced"][bot_name] = sorted(balanced, key=lambda x: x["metrics"]["total_pnl"], reverse=True)[0]
        elif sorted_by_pnl and sorted_by_pnl[0]["metrics"]["total_pnl"] > 0:
            tiers["tier2_balanced"][bot_name] = sorted_by_pnl[0]

        conservative = [
            r for r in combined
            if r["metrics"]["win_rate"] >= 0.65
            and r["metrics"]["profit_factor"] >= 2.0
            and r["metrics"]["max_drawdown"] < 10
            and r["metrics"]["sharpe_ratio"] >= 5
            and r["metrics"]["total_pnl"] > 0
        ]
        if conservative:
            tiers["tier3_conservative"][bot_name] = sorted(conservative, key=lambda x: x["metrics"]["sharpe_ratio"], reverse=True)[0]
        elif sorted_by_pnl and sorted_by_pnl[0]["metrics"]["total_pnl"] > 0:
            safe = [r for r in combined if r["metrics"]["win_rate"] >= 0.50 and r["metrics"]["total_pnl"] > 0]
            if safe:
                tiers["tier3_conservative"][bot_name] = sorted(safe, key=lambda x: x["metrics"]["sharpe_ratio"], reverse=True)[0]

    return tiers


def print_top_results(bot_name, regime_results, top_n=5):
    for regime in ["LOW", "NORMAL", "STRESS"]:
        results = regime_results.get(regime, [])
        if not results:
            continue
        sorted_results = sorted(results, key=lambda x: x["metrics"]["total_pnl"], reverse=True)
        print(f"\n  TOP {top_n} [{regime}] - {bot_name.upper()}:")
        print(f"  {'Rank':<5} {'PnL':>10} {'WR':>7} {'PF':>6} {'Sharpe':>7} {'MaxDD':>7} {'Trades':>7} {'AvgPnL':>9}")
        print(f"  {'─'*5} {'─'*10} {'─'*7} {'─'*6} {'─'*7} {'─'*7} {'─'*7} {'─'*9}")
        for rank, r in enumerate(sorted_results[:top_n], 1):
            m = r["metrics"]
            print(f"  {rank:<5} ${m['total_pnl']:>9,.2f} {m['win_rate']*100:>6.1f}% {m['profit_factor']:>5.2f} {m['sharpe_ratio']:>6.2f} {m['max_drawdown']:>6.1f}% {m['num_trades']:>6} ${m['avg_pnl_per_trade']:>8,.2f}")


def print_tier_recommendation(tier_name, tier_label, tier_data, days, regime_label=""):
    header = f"  {tier_label}"
    if regime_label:
        header += f" [{regime_label}]"
    print(f"\n{'*'*70}")
    print(header)
    print(f"{'*'*70}")

    if not tier_data:
        print("  No qualifying configurations found.")
        return

    total_pnl = 0
    for bot_name, result in tier_data.items():
        m = result["metrics"]
        total_pnl += m["total_pnl"]
        print(f"\n  {bot_name}:")
        print(f"    PnL=${m['total_pnl']:,.2f} | WR={m['win_rate']*100:.1f}% | PF={m['profit_factor']:.2f} | Sharpe={m['sharpe_ratio']:.2f} | MaxDD={m['max_drawdown']:.1f}% | Trades={m['num_trades']} | Avg=${m['avg_pnl_per_trade']:.2f}")

        exit_r = m.get("exit_reasons", {})
        if exit_r:
            parts = [f"{k}={v}" for k, v in sorted(exit_r.items(), key=lambda x: -x[1])]
            print(f"    Exit Reasons: {', '.join(parts)}")

        bot_params_keys = list(BOT_CONFIGS.get(bot_name, {}).get("params", {}).keys())
        sniper_keys = list(SNIPER_PARAMS.keys())
        exitbot_keys = list(EXITBOT_PARAMS.keys())
        other_keys = ["min_position_usd"]

        c = result["config"]
        bp = {k: c[k] for k in bot_params_keys if k in c}
        sp = {k: c[k] for k in sniper_keys if k in c}
        ep = {k: c[k] for k in exitbot_keys if k in c}
        op = {k: c[k] for k in other_keys if k in c}
        print(f"    Bot Params:     {bp}")
        print(f"    Sniper Params:  {sp}")
        print(f"    ExitBot Params: {ep}")
        if op:
            print(f"    System Params:  {op}")

    daily_avg = total_pnl / max(days, 1)
    monthly = daily_avg * 21
    yearly = daily_avg * 252
    print(f"\n  PROJECTIONS ({tier_label}):")
    print(f"    Total Backtest PnL: ${total_pnl:,.2f} over {days} days")
    print(f"    Avg Daily PnL:      ${daily_avg:,.2f}")
    print(f"    Monthly Projected:  ${monthly:,.2f}")
    print(f"    Yearly Projected:   ${yearly:,.2f}")
    pct_of_target = (daily_avg / DAILY_TARGET * 100) if DAILY_TARGET > 0 else 0
    print(f"    Daily Target Hit:   {pct_of_target:.1f}% of ${DAILY_TARGET}/day goal")


def get_total_projected_pnl(all_regime_results, days):
    total = 0
    for bot_name, regime_data in all_regime_results.items():
        for regime, results in regime_data.items():
            if results:
                best = max(r["metrics"]["total_pnl"] for r in results)
                total += best
    return total


def save_results(all_regime_results, regime_tiers, allweather_tiers, days, convergence_report=None):
    os.makedirs("results", exist_ok=True)

    output = {
        "generated_at": datetime.now().isoformat(),
        "account_size": ACCOUNT_SIZE,
        "daily_target": DAILY_TARGET,
        "backtest_days": days,
        "regimes_tested": list(REGIMES.keys()),
        "per_bot_results": {},
        "regime_tiers": {},
        "allweather_tiers": {},
    }

    if convergence_report:
        output["convergence_report"] = convergence_report

    for bot_name, regime_data in all_regime_results.items():
        output["per_bot_results"][bot_name] = {}
        for regime, results in regime_data.items():
            sorted_r = sorted(results, key=lambda x: x["metrics"]["total_pnl"], reverse=True)
            output["per_bot_results"][bot_name][regime] = {
                "total_combos_tested": len(results),
                "top_20": sorted_r[:20],
            }

    for regime, tiers in regime_tiers.items():
        output["regime_tiers"][regime] = {}
        for tier_name, tier_data in tiers.items():
            output["regime_tiers"][regime][tier_name] = {}
            for bot_name, result in tier_data.items():
                output["regime_tiers"][regime][tier_name][bot_name] = {
                    "config": result["config"],
                    "metrics": result["metrics"],
                }

    for tier_name, tier_data in allweather_tiers.items():
        output["allweather_tiers"][tier_name] = {}
        for bot_name, result in tier_data.items():
            output["allweather_tiers"][tier_name][bot_name] = {
                "config": result["config"],
                "metrics": result["metrics"],
            }

    output_path = "results/dynamic_optimizer_results.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n[SAVED] Full results saved to {output_path}")


def run_optimization_pass(data_cache, days, num_combos, bot_order):
    all_regime_results = {}
    for bot_idx, bot_name in enumerate(bot_order, 1):
        bot_cfg = BOT_CONFIGS[bot_name]
        print(f"\n[{bot_idx}/{len(bot_order)}] Starting {bot_name} optimization ({num_combos} combos/regime)...")
        regime_results = optimize_bot_all_regimes(bot_name, bot_cfg, data_cache, num_combos, days)
        all_regime_results[bot_name] = regime_results
        print_top_results(bot_name, regime_results)
    return all_regime_results


def save_partial_results(all_regime_results, bot_name, combos, days):
    partial_dir = "/tmp/optimizer_partial"
    os.makedirs(partial_dir, exist_ok=True)
    partial_path = f"{partial_dir}/{bot_name}_{combos}c_{days}d.json"

    serializable = {}
    for regime, bot_data in all_regime_results.items():
        serializable[regime] = {}
        for bot, results in bot_data.items():
            serializable[regime][bot] = []
            for r in results[:20]:
                entry = {
                    "pnl": r.get("pnl", 0),
                    "win_rate": r.get("win_rate", 0),
                    "profit_factor": r.get("profit_factor", 0),
                    "sharpe": r.get("sharpe", 0),
                    "max_drawdown": r.get("max_drawdown", 0),
                    "total_trades": r.get("total_trades", 0),
                    "avg_pnl": r.get("avg_pnl", 0),
                    "params": r.get("params", {}),
                    "exit_reasons": r.get("exit_reasons", {}),
                }
                serializable[regime][bot].append(entry)

    with open(partial_path, "w") as f:
        json.dump(serializable, f, indent=2)
    print(f"[PARTIAL] Saved to {partial_path}")


def combine_partial_results(days, combos):
    partial_dir = "/tmp/optimizer_partial"
    if not os.path.exists(partial_dir):
        print("[COMBINE] No partial results found. Run individual bots first.")
        return

    all_bots = ["momentum", "crypto", "whipsaw", "bouncebot", "twentyminute"]
    all_regime_results = {"LOW": {}, "NORMAL": {}, "STRESS": {}}
    found_bots = []

    for bot in all_bots:
        partial_path = f"{partial_dir}/{bot}_{combos}c_{days}d.json"
        if os.path.exists(partial_path):
            with open(partial_path, "r") as f:
                partial = json.load(f)
            for regime in ["LOW", "NORMAL", "STRESS"]:
                if regime in partial and bot in partial[regime]:
                    all_regime_results[regime][bot] = partial[regime][bot]
            found_bots.append(bot)
            print(f"[COMBINE] Loaded {bot} results")
        else:
            print(f"[COMBINE] WARNING: Missing {bot} - run with --bot {bot} first")

    if not found_bots:
        print("[COMBINE] No results to combine.")
        return

    print(f"\n[COMBINE] Combined {len(found_bots)}/{len(all_bots)} bots: {', '.join(found_bots)}")

    regime_tiers = {}
    for regime in ["LOW", "NORMAL", "STRESS"]:
        regime_tiers[regime] = select_tier_configs(all_regime_results, regime)
        print_tier_recommendation("tier1_profitable", "TIER 1: MOST PROFITABLE",
            regime_tiers[regime]["tier1_profitable"], days, regime)
        print_tier_recommendation("tier2_balanced", "TIER 2: BALANCED (WR>=55%, PF>=1.5, Sharpe>=3)",
            regime_tiers[regime]["tier2_balanced"], days, regime)
        print_tier_recommendation("tier3_conservative", "TIER 3: CONSERVATIVE (WR>=65%, PF>=2.0, MaxDD<10%, Sharpe>=5)",
            regime_tiers[regime]["tier3_conservative"], days, regime)

    allweather_tiers = select_allweather_tiers(all_regime_results)
    print(f"\n{'#'*70}")
    print(f"  ALL-WEATHER CONFIGS (Best across all regimes)")
    print(f"{'#'*70}")
    print_tier_recommendation("tier1_profitable", "ALL-WEATHER TIER 1: MOST PROFITABLE",
        allweather_tiers["tier1_profitable"], days, "ALL-WEATHER")
    print_tier_recommendation("tier2_balanced", "ALL-WEATHER TIER 2: BALANCED",
        allweather_tiers["tier2_balanced"], days, "ALL-WEATHER")
    print_tier_recommendation("tier3_conservative", "ALL-WEATHER TIER 3: CONSERVATIVE",
        allweather_tiers["tier3_conservative"], days, "ALL-WEATHER")

    save_results(all_regime_results, regime_tiers, allweather_tiers, days, None)
    print(f"\n{'='*70}")
    print(f"  DONE. Results saved to results/dynamic_optimizer_results.json")
    print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="Dynamic Parameter Optimizer v2.0")
    parser.add_argument("--combos", type=int, default=5000, help="Random combos per bot per regime (default 5000)")
    parser.add_argument("--days", type=int, default=60, help="Backtest period in days (default 60)")
    parser.add_argument("--auto-double", action="store_true", help="Enable auto-doubling convergence")
    parser.add_argument("--bot", type=str, default=None, help="Run single bot (momentum/crypto/whipsaw/bouncebot/twentyminute)")
    parser.add_argument("--combine", action="store_true", help="Combine partial results from individual bot runs")
    args = parser.parse_args()

    if args.combine:
        combine_partial_results(args.days, args.combos)
        return

    random.seed(42)

    data_cache = try_load_disk_cache(args.days)
    if data_cache:
        days = args.days
    else:
        engine = BacktestEngine(initial_capital=ACCOUNT_SIZE)
        data_cache, days = cache_all_data(engine, args.days)

    all_bots = ["momentum", "crypto", "whipsaw", "bouncebot", "twentyminute"]
    bot_order = [args.bot] if args.bot else all_bots
    total_start = time.time()

    if args.auto_double:
        print(f"\n[AUTO-DOUBLE] Convergence mode enabled. Starting at {args.combos} combos.")
        convergence_report = []
        current_combos = args.combos
        max_combos = 100000
        prev_pnl = None
        best_results = None

        while current_combos <= max_combos:
            random.seed(42)
            print(f"\n{'#'*70}")
            print(f"  AUTO-DOUBLE PASS: {current_combos} combos/bot/regime")
            print(f"{'#'*70}")

            all_regime_results = run_optimization_pass(data_cache, days, current_combos, bot_order)
            total_pnl = get_total_projected_pnl(all_regime_results, days)

            convergence_report.append({
                "combos": current_combos,
                "total_projected_pnl": round(total_pnl, 2),
            })

            print(f"\n  [CONVERGENCE] {current_combos} combos -> Total Projected PnL: ${total_pnl:,.2f}")

            if prev_pnl is not None and prev_pnl > 0:
                improvement = (total_pnl - prev_pnl) / abs(prev_pnl) * 100
                convergence_report[-1]["improvement_pct"] = round(improvement, 2)
                print(f"  [CONVERGENCE] Improvement: {improvement:.2f}%")
                if improvement < 2.0:
                    print(f"  [CONVERGENCE] Improvement < 2%. Converged at {current_combos} combos.")
                    best_results = all_regime_results
                    break
            elif prev_pnl is not None and prev_pnl <= 0 and total_pnl <= 0:
                pass

            prev_pnl = total_pnl
            best_results = all_regime_results
            current_combos = min(current_combos * 2, max_combos)
            if current_combos > max_combos:
                print(f"  [CONVERGENCE] Hit max {max_combos} combos cap.")
                break

        all_regime_results = best_results

        print(f"\n{'='*70}")
        print(f"  CONVERGENCE REPORT")
        print(f"{'='*70}")
        print(f"  {'Combos':>10} {'Total PnL':>15} {'Improvement':>12}")
        print(f"  {'─'*10} {'─'*15} {'─'*12}")
        for cr in convergence_report:
            imp_str = f"{cr.get('improvement_pct', 'N/A')}%"
            print(f"  {cr['combos']:>10} ${cr['total_projected_pnl']:>14,.2f} {imp_str:>12}")
    else:
        all_regime_results = run_optimization_pass(data_cache, days, args.combos, bot_order)
        convergence_report = None

    if args.bot:
        save_partial_results(all_regime_results, args.bot, args.combos, days)
        total_elapsed = time.time() - total_start
        print(f"\n[PARTIAL] Bot '{args.bot}' done in {total_elapsed:.1f}s")
        print(f"[PARTIAL] Run --combine to aggregate all bot results")
        return

    total_elapsed = time.time() - total_start
    total_combos = sum(
        len(results)
        for regime_data in all_regime_results.values()
        for results in regime_data.values()
    )
    print(f"\n{'='*70}")
    print(f"  OPTIMIZATION COMPLETE")
    print(f"  Total time: {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")
    print(f"  Total combos tested: {total_combos}")
    print(f"{'='*70}")

    regime_tiers = {}
    for regime in ["LOW", "NORMAL", "STRESS"]:
        regime_tiers[regime] = select_tier_configs(all_regime_results, regime)

        print_tier_recommendation(
            "tier1_profitable",
            "TIER 1: MOST PROFITABLE",
            regime_tiers[regime]["tier1_profitable"],
            days, regime
        )
        print_tier_recommendation(
            "tier2_balanced",
            "TIER 2: BALANCED (WR>=55%, PF>=1.5, Sharpe>=3)",
            regime_tiers[regime]["tier2_balanced"],
            days, regime
        )
        print_tier_recommendation(
            "tier3_conservative",
            "TIER 3: CONSERVATIVE (WR>=65%, PF>=2.0, MaxDD<10%, Sharpe>=5)",
            regime_tiers[regime]["tier3_conservative"],
            days, regime
        )

    allweather_tiers = select_allweather_tiers(all_regime_results)
    print(f"\n{'#'*70}")
    print(f"  ALL-WEATHER CONFIGS (Best across all regimes)")
    print(f"{'#'*70}")
    print_tier_recommendation(
        "tier1_profitable",
        "ALL-WEATHER TIER 1: MOST PROFITABLE",
        allweather_tiers["tier1_profitable"],
        days, "ALL-WEATHER"
    )
    print_tier_recommendation(
        "tier2_balanced",
        "ALL-WEATHER TIER 2: BALANCED",
        allweather_tiers["tier2_balanced"],
        days, "ALL-WEATHER"
    )
    print_tier_recommendation(
        "tier3_conservative",
        "ALL-WEATHER TIER 3: CONSERVATIVE",
        allweather_tiers["tier3_conservative"],
        days, "ALL-WEATHER"
    )

    save_results(all_regime_results, regime_tiers, allweather_tiers, days, convergence_report)

    print(f"\n{'='*70}")
    print(f"  DONE. Review results above or check results/dynamic_optimizer_results.json")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
