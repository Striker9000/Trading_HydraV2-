#!/usr/bin/env python3
"""
=============================================================================
ULTRA DYNAMIC SWEEP OPTIMIZER — 132-Parameter Trading System Optimization
=============================================================================
Tests ALL 132 sweepable parameters across the entire Trading Hydra platform
against real 5-minute intraday Alpaca data. Finds optimal configurations that
maximize profitability while minimizing drawdown.

Simulation Modes:
  - TwentyMinuteBot: Gap-and-go entries with options leverage
  - HailMary: Momentum-burst OTM option entries
  - OptionsBot: Credit spread / defined-risk entries

PARAMETER CATEGORIES (132 sweepable variables):
  ENTRY FILTERS (12):  min_gap_pct, max_gap_pct, confirmation_bars, rsi_overbought,
                       rsi_oversold, require_volume_spike, quality_gate_mode,
                       direction_lock, require_vwap, min_first_bar_range_pct,
                       require_ema_cross, require_market_alignment
  HAILMARY ENTRY (10): hm_dynamic_min_score, hm_max_premium, hm_min_premium,
                       hm_min_stock_change_pct, hm_strike_otm_pct, hm_min_delta,
                       hm_max_delta, hm_dte_max, hm_max_spread, hm_max_spread_pct
  OPTIONSBOT ENTRY (8): ob_bull_put_min_credit, ob_bull_put_max_dte, ob_bull_put_profit_target,
                        ob_bull_put_stop_loss_pct, ob_ic_min_credit, ob_ic_max_dte,
                        ob_iv_gate_buy_max, ob_iv_gate_buy_min
  POSITION SIZING (9): options_leverage, daily_budget_usd, max_contracts,
                       hm_max_risk_per_trade, ob_max_position_size, base_risk_pct,
                       kelly_fraction, daily_budget_pct, cash_reserve_pct
  HOLD TIME (5):       min_hold_minutes, stock_min_hold_minutes, max_hold_minutes,
                       time_stop_minutes, flatten_before_close_min
  STOP LOSSES (5):     catastrophic_stop, hard_stop_pct, stock_catastrophic_stop,
                       stock_stop_loss_pct, hard_stop_default
  TAKE PROFIT (6):     take_profit_pct, trailing_stop_pct, trailing_activation_pct,
                       stock_take_profit_pct, stock_trailing_stop, stock_trailing_activation
  TIERED EXITS (9):    tier1_multiplier, tier1_sell_pct, tier2_multiplier, tier3_runner_mult,
                       hm_tier1_mult, hm_tier1_sell_pct, hm_tier2_mult, hm_runner_mult,
                       hm_profit_target_mult
  DYNAMIC TRAILING (6):atr_multiplier, atr_activation_mult, atr_min_trail_pct,
                       atr_max_trail_pct, atr_tier1_tighten, atr_tier2_tighten
  PROFITSNIPER (4):    sniper_velocity_window, sniper_velocity_reversal,
                       sniper_ratchet_arm, sniper_ratchet_distance
  STOP JITTER (1):     stop_jitter_pct
  PARABOLIC (3):       parabolic_initial_trail, parabolic_min_trail, parabolic_acceleration
  TRADE LIMITS (7):    max_trades_per_day, max_concurrent, hm_max_trades,
                       ob_max_trades, ob_max_concurrent, min_entry_spacing_s,
                       stop_after_losses
  RISK MGMT (9):       daily_loss_cap_pct, pdt_equity_floor, drawdown_reduce_pct,
                       dd_threshold_halt, dd_min_multiplier, corr_trigger_count,
                       corr_halt_count, corr_reduce_mult, corr_cooldown_min
  SESSION PROTECT (4): daily_goal_usd, daily_goal_lock_pct, strong_day_usd,
                       freeroll_min_quality
  OPTIONS CHAIN (7):   prefer_delta_min, prefer_delta_max, chain_max_spread,
                       chain_max_spread_pct, chain_min_oi, chain_min_volume,
                       options_max_cost
  TIMING (4):          trade_start_minutes_after_open, trade_end_minutes_after_open,
                       ob_trade_start_min, ob_trade_end_min
  LIQUIDITY (4):       liq_max_spread_pct, liq_min_dollar_volume, liq_min_oi,
                       exit_spread_protection_pct
  NEWS/INTEL (5):      news_negative_threshold, news_severe_threshold,
                       news_bullish_min, block_near_earnings, earnings_buffer_days
  PORTFOLIO (4):       bucket_twentymin_pct, bucket_hailmary_pct, bucket_options_pct,
                       bucket_momentum_pct
  GAP PATTERN (2):     gap_fill_threshold_pct, gap_continuation_threshold_pct
  SUPPORT/RESIST (8):  sr_lookback, sr_cluster_pct, sr_min_touches, sr_min_strength,
                       sr_entry_proximity_pct, sr_block_near_resistance,
                       sr_prefer_near_support, sr_exit_at_resistance

Usage:
    python scripts/dynamic_sweep.py --days 60 --mode quick --max-combos 600
    python scripts/dynamic_sweep.py --days 120 --mode full --max-combos 1000
    python scripts/dynamic_sweep.py --days 30 --mode focused --max-combos 300
    python scripts/dynamic_sweep.py --days 60 --mode mega --max-combos 1000
    python scripts/dynamic_sweep.py --days 60 --mode ultra --max-combos 1000
    python scripts/dynamic_sweep.py --days 60 --mode freeroll --max-combos 800

Output: results/dynamic_sweep_results.json + console summary
"""

import os
import sys
import json
import time
import math
import random
import hashlib
import argparse
import itertools
import statistics
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field, asdict

INITIAL_CAPITAL = 500.0
OPTIONS_CONTRACT_MULTIPLIER = 100

SYMBOLS = [
    "SPY", "QQQ", "AMD", "NVDA", "TSLA", "AAPL", "MSFT",
    "GOOGL", "AMZN", "META", "BAC", "WFC", "JPM", "XLF",
    "DIA", "PLTR", "CRM", "BA", "SMCI", "KRE",
]

PARAM_GRID_QUICK = {
    "min_gap_pct":             [0.3, 0.5, 1.0, 1.5, 2.0, 3.0],
    "min_hold_minutes":        [3, 5, 10, 15, 20, 30],
    "catastrophic_stop":       [10, 15, 20, 30, 50],
    "hard_stop_pct":           [5, 10, 15, 20, 30],
    "direction_lock":          [True, False],
    "max_trades_per_day":      [1, 2, 3, 5],
    "confirmation_bars":       [1, 2, 3, 5],
    "options_leverage":        [5, 10, 15, 20],
    "take_profit_pct":         [50, 100, 150, 200, 300],
    "quality_gate_mode":       ["fail_open", "fail_closed"],
}

PARAM_GRID_FULL = {
    "min_gap_pct":             [0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0],
    "max_gap_pct":             [5.0, 8.0, 10.0, 15.0],
    "min_hold_minutes":        [1, 3, 5, 8, 10, 15, 20, 25, 30, 45, 60],
    "catastrophic_stop":       [5, 8, 10, 15, 20, 25, 30, 40, 50, 75],
    "hard_stop_pct":           [3, 5, 8, 10, 15, 20, 25, 30],
    "direction_lock":          [True, False],
    "max_trades_per_day":      [1, 2, 3, 4, 5, 8, 10],
    "max_concurrent":          [1, 2, 3, 5],
    "confirmation_bars":       [1, 2, 3, 4, 5],
    "options_leverage":        [3, 5, 8, 10, 15, 20, 25],
    "take_profit_pct":         [30, 50, 75, 100, 150, 200, 300, 400],
    "quality_gate_mode":       ["fail_open", "fail_closed"],
    "require_volume_spike":    [True, False],
    "rsi_overbought":          [50, 60, 70, 80, 90],
    "rsi_oversold":            [10, 15, 20, 25, 30],
    "min_entry_spacing_s":     [0, 30, 60, 120, 300],
    "trailing_stop_pct":       [0, 5, 10, 15, 20, 30],
    "trailing_activation_pct": [0, 2, 3, 5, 8, 10],
    "require_vwap":            [True, False],
    "position_size_pct":       [1.0, 1.5, 2.0, 3.0, 5.0],
    "stop_after_losses":       [2, 3, 5, 8, 99],
}

PARAM_GRID_FOCUSED = {
    "min_gap_pct":             [1.0, 1.5, 2.0, 2.5, 3.0],
    "min_hold_minutes":        [10, 15, 20, 30],
    "catastrophic_stop":       [25, 30, 40, 50],
    "hard_stop_pct":           [15, 20, 25, 30],
    "direction_lock":          [True],
    "max_trades_per_day":      [1, 2, 3],
    "confirmation_bars":       [2, 3, 5],
    "options_leverage":        [10, 15, 20],
    "take_profit_pct":         [100, 150, 200, 300],
    "quality_gate_mode":       ["fail_closed"],
}

# =============================================================================
# FREEROLL MODE — Loosened entries for "house money" sessions
# =============================================================================
# Use AFTER hitting daily target with session protection locked.
# Strategy: Wider entry gates (bigger gaps, fewer bars, less filtering)
#           + more trade attempts + proven exit mechanics from mega-sweep.
# Session protection floor keeps your profit safe regardless.
# Focuses on the ~35 params that matter most for entry aggressiveness
# while pinning exit/risk params to proven mega-sweep winners.
# =============================================================================
PARAM_GRID_FREEROLL = {
    # --- LOOSENED ENTRIES: wider gates to catch more movers ---
    "min_gap_pct":                  [0.3, 0.5, 1.0, 1.5, 2.0, 3.0],
    "max_gap_pct":                  [10.0, 15.0, 20.0, 25.0, 40.0],
    "confirmation_bars":            [1, 2, 3, 5],
    "rsi_overbought":               [70, 80, 90, 99],
    "rsi_oversold":                 [5, 10, 15, 20],
    "require_volume_spike":         [True, False],
    "quality_gate_mode":            ["fail_open", "fail_closed"],
    "direction_lock":               [True, False],
    "require_vwap":                 [True, False],
    "min_first_bar_range_pct":      [0.01, 0.03, 0.05],
    "require_ema_cross":            [False],
    "require_market_alignment":     [False],

    # --- MORE SHOTS: higher trade limits for house-money sessions ---
    "max_trades_per_day":           [5, 8, 10, 15, 20],
    "max_concurrent":               [2, 3, 5],
    "hm_max_trades":                [2, 3, 5],
    "min_entry_spacing_s":          [0, 30, 60],
    "stop_after_losses":            [3, 5, 10, 99],

    # --- POSITION SIZING: smaller per-trade since playing with house money ---
    "options_leverage":             [5, 10, 15, 20],
    "max_contracts":                [1, 2, 3],
    "daily_budget_pct":             [10, 15, 20, 25],
    "base_risk_pct":                [1.0, 1.5, 2.0, 3.0],
    "kelly_fraction":               [0.15, 0.20, 0.25, 0.35],

    # --- HOLD TIME: test both quick scalps and longer holds ---
    "min_hold_minutes":             [1, 3, 5, 10],
    "time_stop_minutes":            [30, 60, 90, 120, 180],

    # --- STOPS: keep proven stop mechanics, test wider for big-gap plays ---
    "catastrophic_stop":            [10, 20, 30, 50, 75],
    "hard_stop_pct":                [5, 10, 15, 20, 30],

    # --- TAKE PROFIT: wider targets for bigger gap opportunities ---
    "take_profit_pct":              [50, 100, 150, 200, 300, 500],
    "trailing_stop_pct":            [5, 10, 15, 20, 30],
    "trailing_activation_pct":      [0, 2, 5, 10],

    # --- TIERED EXITS: proven mechanics, slight variations ---
    "tier1_multiplier":             [2.0, 3.0, 5.0],
    "tier1_sell_pct":               [25, 33, 50],
    "tier2_multiplier":             [5.0, 8.0, 10.0],

    # --- S/R: test if S/R helps freeroll entries ---
    "sr_lookback":                  [3, 5, 7, 10],
    "sr_min_strength":              [5, 10, 15, 25],
    "sr_block_near_resistance":     [True, False],
    "sr_prefer_near_support":       [True, False],
    "sr_exit_at_resistance":        [True, False],

    # --- TIMING: extend trading window for freeroll ---
    "trade_start_minutes_after_open": [5, 10, 15],
    "trade_end_minutes_after_open":   [120, 180, 240, 330, 390],

    # --- SESSION PROTECTION: test various freeroll quality gates ---
    "freeroll_min_quality":         [50, 60, 70, 80, 90],

    # --- SELLOFF PROTECTION: how aggressive during broad selloffs ---
    "selloff_enabled":              [True, False],
    "selloff_breadth_threshold":    [0.55, 0.60, 0.65, 0.70, 0.80],
    "selloff_mode":                 ["normal", "reduce", "shorts_only", "favor_shorts"],
    "selloff_max_concurrent_mult":  [0.33, 0.50, 0.75],
}

PARAM_GRID_MEGA = {
    "min_gap_pct":             [0.3, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0],
    "max_gap_pct":             [5.0, 8.0, 10.0, 15.0],
    "min_first_bar_range_pct": [0.01, 0.03, 0.05, 0.08, 0.15],
    "confirmation_bars":       [1, 2, 3, 5, 7],
    "rsi_overbought":          [50, 60, 70, 80, 90],
    "rsi_oversold":            [10, 15, 20, 25, 30],
    "require_volume_spike":    [True, False],
    "quality_gate_mode":       ["fail_open", "fail_closed"],
    "direction_lock":          [True, False],
    "require_vwap":            [True, False],
    "min_hold_minutes":        [1, 3, 5, 10, 15, 20, 30, 45],
    "catastrophic_stop":       [5, 10, 15, 20, 30, 50, 75],
    "hard_stop_pct":           [3, 5, 10, 15, 20, 30],
    "take_profit_pct":         [30, 50, 100, 150, 200, 300, 500],
    "trailing_stop_pct":       [0, 5, 10, 15, 20, 30],
    "trailing_activation_pct": [0, 2, 5, 8, 10],
    "tier1_multiplier":        [2.0, 3.0, 5.0],
    "tier1_sell_pct":          [25, 33, 50, 75],
    "tier2_multiplier":        [5.0, 8.0, 10.0],
    "options_leverage":        [3, 5, 10, 15, 20, 25],
    "position_size_pct":       [1.0, 1.5, 2.0, 3.0, 5.0],
    "max_contracts":           [1, 3, 5, 10],
    "max_trades_per_day":      [1, 2, 3, 5, 8],
    "max_concurrent":          [1, 2, 3, 5],
    "min_entry_spacing_s":     [0, 30, 60, 120, 300],
    "stop_after_losses":       [1, 2, 3, 5, 99],
    "daily_loss_cap_pct":      [2, 3, 5, 8, 10],
    "drawdown_reduce_pct":     [3, 5, 8, 10, 15],
    # Selloff Protection
    "selloff_enabled":         [True, False],
    "selloff_breadth_threshold": [0.55, 0.65, 0.75],
    "selloff_mode":            ["normal", "reduce", "shorts_only", "halt"],
    "selloff_max_concurrent_mult": [0.33, 0.50, 0.75],
}

PARAM_GRID_ULTRA = {
    # === CATEGORY 1: ENTRY FILTERS (12 params) ===
    "min_gap_pct":                  [0.3, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0],
    "max_gap_pct":                  [5.0, 8.0, 10.0, 15.0, 20.0],
    "confirmation_bars":            [1, 2, 3, 5, 7],
    "rsi_overbought":               [50, 60, 70, 80, 90],
    "rsi_oversold":                 [10, 15, 20, 25, 30],
    "require_volume_spike":         [True, False],
    "quality_gate_mode":            ["fail_open", "fail_closed"],
    "direction_lock":               [True, False],
    "require_vwap":                 [True, False],
    "min_first_bar_range_pct":      [0.01, 0.03, 0.05, 0.08, 0.15],
    "require_ema_cross":            [True, False],
    "require_market_alignment":     [True, False],

    # === CATEGORY 1B: GAP PATTERN THRESHOLDS (2 params) ===
    "gap_fill_threshold_pct":       [15, 20, 30, 40, 50],
    "gap_continuation_threshold_pct": [0.1, 0.2, 0.3, 0.5],

    # === CATEGORY 2: HAILMARY ENTRY (10 params) ===
    "hm_dynamic_min_score":         [30, 40, 50, 55, 65, 75],
    "hm_max_premium":               [1.00, 3.00, 5.00, 10.00],
    "hm_min_premium":               [0.05, 0.10, 0.20, 0.50],
    "hm_min_stock_change_pct":      [0.1, 0.3, 0.5, 1.0, 2.0],
    "hm_strike_otm_pct":            [0.5, 1.0, 2.0, 3.0, 5.0],
    "hm_min_delta":                 [0.05, 0.10, 0.15, 0.25],
    "hm_max_delta":                 [0.20, 0.30, 0.40, 0.60],
    "hm_dte_max":                   [0, 1, 2, 3, 5, 7],
    "hm_max_spread":                [0.10, 0.20, 0.30, 0.50, 1.00],
    "hm_max_spread_pct":            [5, 10, 15, 20, 25],

    # === CATEGORY 3: OPTIONSBOT ENTRY (8 params) ===
    "ob_bull_put_min_credit":       [0.10, 0.20, 0.30, 0.50, 1.00],
    "ob_bull_put_max_dte":          [7, 14, 21, 30, 45],
    "ob_bull_put_profit_target":    [0.25, 0.40, 0.50, 0.60, 0.80],
    "ob_bull_put_stop_loss_pct":    [50, 100, 150, 200, 300],
    "ob_ic_min_credit":             [0.25, 0.50, 0.75, 1.00, 2.00],
    "ob_ic_max_dte":                [7, 14, 21, 30, 45],
    "ob_iv_gate_buy_max":           [30, 40, 50, 60, 80],
    "ob_iv_gate_buy_min":           [5, 10, 15, 20, 30],

    # === CATEGORY 4: POSITION SIZING (9 params) ===
    "options_leverage":             [3, 5, 10, 15, 20, 25],
    "daily_budget_usd":             [500, 1000, 2000, 3000, 5000],
    "max_contracts":                [1, 2, 3, 5, 10],
    "hm_max_risk_per_trade":        [100, 250, 500, 1000, 2000],
    "ob_max_position_size":         [500, 1000, 2000, 3000, 5000],
    "base_risk_pct":                [0.5, 1.0, 1.5, 2.0, 3.0, 5.0],
    "kelly_fraction":               [0.10, 0.15, 0.20, 0.25, 0.35, 0.50],
    "daily_budget_pct":             [5, 10, 15, 20, 25],
    "cash_reserve_pct":             [10, 15, 20, 30, 40],

    # === CATEGORY 5: HOLD TIME (5 params) ===
    "min_hold_minutes":             [1, 3, 5, 10, 15, 20, 30, 45],
    "stock_min_hold_minutes":       [1, 3, 5, 8, 15, 30],
    "max_hold_minutes":             [30, 60, 90, 120, 180, 390],
    "time_stop_minutes":            [30, 60, 90, 120, 180, 390],
    "flatten_before_close_min":     [5, 10, 15, 30, 60],

    # === CATEGORY 6: STOP LOSSES (5 params) ===
    "catastrophic_stop":            [5, 10, 15, 20, 30, 50, 75],
    "hard_stop_pct":                [3, 5, 10, 15, 20, 30],
    "stock_catastrophic_stop":      [3, 5, 8, 10, 15, 20],
    "stock_stop_loss_pct":          [1, 2, 3, 5, 8, 10],
    "hard_stop_default":            [5, 8, 10, 12, 15, 20, 25],

    # === CATEGORY 7: TAKE PROFIT / TRAILING (6 params) ===
    "take_profit_pct":              [30, 50, 100, 150, 200, 300, 500],
    "trailing_stop_pct":            [0, 5, 10, 15, 20, 30],
    "trailing_activation_pct":      [0, 2, 5, 8, 10],
    "stock_take_profit_pct":        [3, 5, 8, 10, 15, 20, 25],
    "stock_trailing_stop":          [0.5, 1.0, 1.5, 2.0, 3.0, 5.0],
    "stock_trailing_activation":    [0.5, 1.0, 1.5, 2.0, 3.0, 5.0],

    # === CATEGORY 8: TIERED EXITS — OPTIONS (4 params) ===
    "tier1_multiplier":             [2.0, 3.0, 4.0, 5.0],
    "tier1_sell_pct":               [20, 25, 33, 50, 75],
    "tier2_multiplier":             [5.0, 8.0, 10.0, 15.0],
    "tier3_runner_mult":            [10, 15, 25, 50],

    # === CATEGORY 8B: TIERED EXITS — HAILMARY (5 params) ===
    "hm_tier1_mult":                [2.0, 3.0, 5.0, 10.0],
    "hm_tier1_sell_pct":            [20, 25, 33, 50, 75],
    "hm_tier2_mult":                [5.0, 10.0, 15.0, 20.0],
    "hm_runner_mult":               [10, 25, 50],
    "hm_profit_target_mult":        [5, 10, 25, 50],

    # === CATEGORY 9: DYNAMIC TRAILING — ATR (6 params) ===
    "atr_multiplier":               [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0],
    "atr_activation_mult":          [0.25, 0.50, 0.75, 1.0, 1.5, 2.0],
    "atr_min_trail_pct":            [0.1, 0.25, 0.5, 1.0, 2.0],
    "atr_max_trail_pct":            [5, 8, 10, 15, 20, 25],
    "atr_tier1_tighten":            [0.50, 0.60, 0.75, 0.85, 1.0],
    "atr_tier2_tighten":            [0.25, 0.35, 0.50, 0.65, 0.75],

    # === CATEGORY 10: PROFITSNIPER — STOCKS (4 params) ===
    "sniper_velocity_window":       [3, 5, 7, 10],
    "sniper_velocity_reversal":     [0.05, 0.1, 0.15, 0.25, 0.5],
    "sniper_ratchet_arm":           [0.2, 0.3, 0.5, 1.0, 2.0],
    "sniper_ratchet_distance":      [0.05, 0.1, 0.15, 0.25, 0.5],

    # === CATEGORY 11: STOP JITTER (1 param) ===
    "stop_jitter_pct":              [0, 0.5, 1.0, 1.5, 2.0, 3.0],

    # === CATEGORY 12: PARABOLIC RUNNER (3 params) ===
    "parabolic_initial_trail":      [15, 20, 25, 30, 40, 50],
    "parabolic_min_trail":          [3, 5, 8, 10, 15],
    "parabolic_acceleration":       [0.01, 0.02, 0.04, 0.06, 0.10],

    # === CATEGORY 13: TRADE FREQUENCY (7 params) ===
    "max_trades_per_day":           [1, 2, 3, 5, 8, 10],
    "max_concurrent":               [1, 2, 3, 5],
    "hm_max_trades":                [1, 2, 3, 5],
    "ob_max_trades":                [1, 2, 3, 5, 10],
    "ob_max_concurrent":            [1, 2, 3, 5],
    "min_entry_spacing_s":          [0, 30, 60, 120, 300, 600],
    "stop_after_losses":            [1, 2, 3, 5, 10, 99],

    # === CATEGORY 14: RISK MANAGEMENT (9 params) ===
    "daily_loss_cap_pct":           [2, 3, 5, 8, 10],
    "pdt_equity_floor":             [25100, 25250, 25500, 26000],
    "drawdown_reduce_pct":          [3, 5, 8, 10, 15],
    "dd_threshold_halt":            [8, 10, 12, 15, 20],
    "dd_min_multiplier":            [0.2, 0.3, 0.4, 0.5, 0.6, 0.8],
    "corr_trigger_count":           [2, 3, 4, 5],
    "corr_halt_count":              [3, 4, 5, 6, 8],
    "corr_reduce_mult":             [0.25, 0.40, 0.50, 0.60, 0.75],
    "corr_cooldown_min":            [15, 30, 45, 60, 90, 120],

    # === CATEGORY 15: SESSION PROTECTION (4 params) ===
    "daily_goal_usd":               [100, 200, 300, 500, 750, 1000],
    "daily_goal_lock_pct":          [70, 75, 80, 85, 90, 95],
    "strong_day_usd":               [300, 500, 750, 1000, 1500],
    "freeroll_min_quality":         [70, 80, 85, 90, 95, 100],

    # === CATEGORY 16: OPTIONS CHAIN SELECTION (7 params) ===
    "prefer_delta_min":             [0.25, 0.30, 0.40, 0.45, 0.50, 0.60],
    "prefer_delta_max":             [0.40, 0.50, 0.55, 0.60, 0.70, 0.75],
    "chain_max_spread":             [0.10, 0.20, 0.30, 0.50, 1.00],
    "chain_max_spread_pct":         [5, 10, 15, 20, 25],
    "chain_min_oi":                 [10, 25, 50, 100, 250, 500],
    "chain_min_volume":             [10, 25, 50, 100, 250, 500],
    "options_max_cost":             [1.00, 5.00, 10.00, 15.00, 25.00, 50.00],

    # === CATEGORY 17: TIMING (4 params) ===
    "trade_start_minutes_after_open": [5, 10, 15, 20, 30],
    "trade_end_minutes_after_open":   [60, 90, 120, 180, 240, 330, 390],
    "ob_trade_start_min":             [10, 15, 20, 30],
    "ob_trade_end_min":               [120, 180, 240, 300, 360],

    # === CATEGORY 18: LIQUIDITY FILTERS (4 params) ===
    "liq_max_spread_pct":           [5, 10, 15, 20, 25],
    "liq_min_dollar_volume":        [50000, 100000, 200000, 500000],
    "liq_min_oi":                   [25, 50, 100, 250, 500],
    "exit_spread_protection_pct":   [5, 10, 15, 20, 30],

    # === CATEGORY 19: NEWS / INTELLIGENCE (5 params) ===
    "news_negative_threshold":      [-0.5, -0.6, -0.7, -0.8, -0.9],
    "news_severe_threshold":        [-0.7, -0.8, -0.85, -0.9, -0.95],
    "news_bullish_min":             [0.1, 0.2, 0.3, 0.4, 0.5],
    "block_near_earnings":          [True, False],
    "earnings_buffer_days":         [1, 2, 3, 5, 7],

    # === CATEGORY 20: PORTFOLIO ALLOCATION (4 params) ===
    "bucket_twentymin_pct":         [10, 20, 30, 35, 40, 50, 60],
    "bucket_hailmary_pct":          [10, 20, 30, 35, 40, 50, 60],
    "bucket_options_pct":           [5, 10, 15, 20, 30, 40],
    "bucket_momentum_pct":          [0, 5, 10, 15, 20],

    # === CATEGORY 21: SUPPORT / RESISTANCE (8 params) ===
    "sr_lookback":                  [3, 5, 7, 10, 15],
    "sr_cluster_pct":               [0.15, 0.25, 0.3, 0.5, 0.75],
    "sr_min_touches":               [1, 2, 3, 4],
    "sr_min_strength":              [5, 10, 15, 25, 40],
    "sr_entry_proximity_pct":       [0.2, 0.3, 0.5, 0.8, 1.0],
    "sr_block_near_resistance":     [True, False],
    "sr_prefer_near_support":       [True, False],
    "sr_exit_at_resistance":        [True, False],

    # === CATEGORY 22: SELLOFF PROTECTION (7 params) ===
    "selloff_enabled":              [True, False],
    "selloff_breadth_threshold":    [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80],
    "selloff_min_gap_count":        [5, 8, 10, 15, 20],
    "selloff_mode":                 ["normal", "reduce", "shorts_only", "favor_shorts", "halt"],
    "selloff_max_concurrent_mult":  [0.25, 0.33, 0.50, 0.75, 1.0],
    "selloff_favor_direction":      [True, False],
    "selloff_vix_boost":            [True, False],
}

MAX_COMBOS = 600


@dataclass
class SimTrade:
    symbol: str
    direction: str
    pattern: str
    entry_time: Any
    entry_price: float
    exit_time: Any
    exit_price: float
    exit_reason: str
    gap_pct: float
    hold_minutes: int
    pnl_pct: float
    pnl_usd: float
    max_favorable: float = 0.0
    max_adverse: float = 0.0
    volume_at_entry: float = 0.0
    bars_held: int = 0
    sim_mode: str = "twentymin"

    def to_dict(self):
        d = asdict(self)
        for k in ["entry_time", "exit_time"]:
            if d[k] is not None:
                d[k] = str(d[k])
        return d


def load_intraday_data(symbols: List[str], days: int) -> Dict[str, List[Dict]]:
    """Load 5-minute intraday bars from Alpaca with disk caching."""
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
    except ImportError:
        print("  ERROR: alpaca-py not installed. Run: pip install alpaca-py")
        return {}

    api_key = os.environ.get("APCA_API_KEY_ID", "")
    api_secret = os.environ.get("APCA_API_SECRET_KEY", "")
    if not api_key or not api_secret:
        print("  ERROR: APCA_API_KEY_ID / APCA_API_SECRET_KEY not set")
        return {}

    cache_dir = "cache/bars"
    os.makedirs(cache_dir, exist_ok=True)

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days + 5)
    cache_key = f"{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}"

    all_bars = {}
    symbols_to_fetch = []

    for sym in symbols:
        cache_file = f"{cache_dir}/{sym}_{cache_key}.json"
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r") as f:
                    bars = json.load(f)
                if bars:
                    all_bars[sym] = bars
                    continue
            except:
                pass
        symbols_to_fetch.append(sym)

    if symbols_to_fetch:
        print(f"  Fetching {len(symbols_to_fetch)} symbols from Alpaca...")
        client = StockHistoricalDataClient(api_key, api_secret)

        batch_size = 5
        for batch_start in range(0, len(symbols_to_fetch), batch_size):
            batch = symbols_to_fetch[batch_start:batch_start + batch_size]

            for attempt in range(3):
                try:
                    request = StockBarsRequest(
                        symbol_or_symbols=batch,
                        start=start_dt,
                        end=end_dt,
                        timeframe=TimeFrame.Minute,
                        limit=None,
                        feed="iex",
                    )
                    bars_data = client.get_stock_bars(request)

                    for sym in batch:
                        sym_bars = []
                        try:
                            for bar in bars_data[sym]:
                                sym_bars.append({
                                    "timestamp": bar.timestamp.isoformat(),
                                    "open": float(bar.open),
                                    "high": float(bar.high),
                                    "low": float(bar.low),
                                    "close": float(bar.close),
                                    "volume": int(bar.volume),
                                })
                        except (KeyError, TypeError):
                            pass

                        if sym_bars:
                            all_bars[sym] = sym_bars
                            cache_file = f"{cache_dir}/{sym}_{cache_key}.json"
                            with open(cache_file, "w") as f:
                                json.dump(sym_bars, f)

                    break
                except Exception as e:
                    wait = 2 ** attempt
                    print(f"  Retry {attempt+1}/3 for {batch}: {e} (waiting {wait}s)")
                    time.sleep(wait)

            if batch_start + batch_size < len(symbols_to_fetch):
                time.sleep(0.3)

    print(f"  Loaded {len(all_bars)} symbols")
    return all_bars


def group_bars_by_day(bars: List[Dict]) -> Dict[str, List[Dict]]:
    """Group bars by trading day."""
    days = {}
    for bar in bars:
        ts = bar["timestamp"]
        if isinstance(ts, str):
            day = ts[:10]
        else:
            day = ts.strftime("%Y-%m-%d")
        if day not in days:
            days[day] = []
        days[day].append(bar)
    return days


def compute_gap(prev_day_bars: List[Dict], today_bars: List[Dict]) -> Optional[float]:
    if not prev_day_bars or not today_bars:
        return None
    prev_close = prev_day_bars[-1]["close"]
    today_open = today_bars[0]["open"]
    if prev_close <= 0:
        return None
    return ((today_open - prev_close) / prev_close) * 100


def compute_rsi(bars: List[Dict], period: int = 14) -> Optional[float]:
    if not bars or len(bars) < period + 1:
        return None
    closes = [b["close"] for b in bars]
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    if len(deltas) < period:
        return None
    gains = [max(d, 0) for d in deltas[-period:]]
    losses = [max(-d, 0) for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_vwap(bars: List[Dict]) -> Optional[float]:
    if not bars:
        return None
    cum_tp_vol = 0
    cum_vol = 0
    for b in bars:
        typical_price = (b["high"] + b["low"] + b["close"]) / 3
        cum_tp_vol += typical_price * b["volume"]
        cum_vol += b["volume"]
    if cum_vol <= 0:
        return None
    return cum_tp_vol / cum_vol


def compute_ema(values: List[float], period: int) -> Optional[float]:
    if not values or len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for val in values[period:]:
        ema = (val - ema) * multiplier + ema
    return ema


def detect_sr_levels(bars: List[Dict], config: Dict) -> List[Dict]:
    """
    Detect support/resistance levels from historical bars using swing
    high/low detection with volume weighting and clustering.
    Returns list of dicts with keys: price, level_type, strength, touch_count.

    Touch definition: a bar "touches" a level if its intrabar high/low
    range overlaps the level's proximity band (price ± sr_entry_proximity_pct%).
    This captures wicks that tested the level, not just closes.

    Performance: caps input to last 300 bars for speed while retaining
    ~4 trading days of 5-min context.
    """
    lookback = int(config.get("sr_lookback", 5))
    cluster_pct = float(config.get("sr_cluster_pct", 0.3))
    min_touches = int(config.get("sr_min_touches", 2))
    min_strength = float(config.get("sr_min_strength", 10))
    touch_prox = float(config.get("sr_entry_proximity_pct", 0.3))
    recency_hl = 50

    max_bars = 300
    if len(bars) > max_bars:
        bars = bars[-max_bars:]
    n = len(bars)
    if n < lookback * 2 + 1:
        return []

    vols = [b.get("volume", 1) for b in bars]
    avg_vol = sum(vols) / n if n > 0 else 1
    if avg_vol <= 0:
        avg_vol = 1

    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]

    swing_highs = []
    swing_lows = []
    for i in range(lookback, n - lookback):
        h = highs[i]
        lo = lows[i]
        window = range(i - lookback, i + lookback + 1)
        if all(highs[j] <= h for j in window if j != i):
            swing_highs.append((i, h, vols[i]))
        if all(lows[j] >= lo for j in window if j != i):
            swing_lows.append((i, lo, vols[i]))

    levels = []
    for level_type, raw_pts in [("resistance", swing_highs), ("support", swing_lows)]:
        if not raw_pts:
            continue
        sorted_pts = sorted(raw_pts, key=lambda x: x[1])
        clusters = []
        cur_p = [sorted_pts[0][1]]
        cur_v = [sorted_pts[0][2]]
        cur_i = [sorted_pts[0][0]]
        for idx, price, vol in sorted_pts[1:]:
            centre = sum(cur_p) / len(cur_p)
            if centre > 0 and abs(price - centre) / centre * 100 <= cluster_pct:
                cur_p.append(price)
                cur_v.append(vol)
                cur_i.append(idx)
            else:
                clusters.append((cur_p[:], cur_v[:], cur_i[:]))
                cur_p = [price]
                cur_v = [vol]
                cur_i = [idx]
        clusters.append((cur_p, cur_v, cur_i))

        for cl_p, cl_v, cl_idx in clusters:
            total_vol = sum(cl_v)
            cnt = len(cl_p)
            if total_vol > 0 and cnt > 0:
                vw_price = sum(p * v for p, v in zip(cl_p, cl_v)) / total_vol
            else:
                vw_price = sum(cl_p) / cnt

            vol_wt = (sum(cl_v) / cnt) / avg_vol if avg_vol > 0 else 1.0
            last_idx = max(cl_idx)
            first_idx = min(cl_idx)

            touch_count = 0
            band = vw_price * touch_prox / 100
            for bi in range(first_idx, n):
                if lows[bi] <= vw_price + band and highs[bi] >= vw_price - band:
                    touch_count += 1

            if touch_count < min_touches:
                continue

            bars_since = n - last_idx
            recency = math.exp(-0.693 * bars_since / recency_hl)

            strength = (min(touch_count, 10) * 10 + min(vol_wt, 3.0) * 15 + cnt * 5) * recency
            if strength < min_strength:
                continue

            levels.append({
                "price": round(vw_price, 4),
                "level_type": level_type,
                "strength": round(strength, 2),
                "touch_count": touch_count,
            })

    levels.sort(key=lambda lv: lv["strength"], reverse=True)
    return levels[:20]


def sr_price_info(price: float, levels: List[Dict], proximity_pct: float = 0.3) -> Dict:
    """Check if price is near support/resistance levels."""
    supports = [lv for lv in levels if lv["level_type"] == "support" and lv["price"] < price]
    resistances = [lv for lv in levels if lv["level_type"] == "resistance" and lv["price"] > price]

    ns = min(supports, key=lambda lv: price - lv["price"]) if supports else None
    nr = min(resistances, key=lambda lv: lv["price"] - price) if resistances else None

    s_dist = ((price - ns["price"]) / price * 100) if ns else 999.0
    r_dist = ((nr["price"] - price) / price * 100) if nr else 999.0

    return {
        "near_support": s_dist <= proximity_pct,
        "near_resistance": r_dist <= proximity_pct,
        "support_distance_pct": s_dist,
        "resistance_distance_pct": r_dist,
        "support_strength": ns["strength"] if ns else 0,
        "resistance_strength": nr["strength"] if nr else 0,
        "nearest_resistance_price": nr["price"] if nr else None,
        "nearest_support_price": ns["price"] if ns else None,
    }


def compute_atr(bars: List[Dict], period: int = 14) -> Optional[float]:
    if not bars or len(bars) < period + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        h = bars[i]["high"]
        l = bars[i]["low"]
        pc = bars[i-1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def detect_pattern(gap_pct: float, bars: List[Dict], bar_idx: int, config: Dict) -> Optional[Tuple[str, str]]:
    confirmation_bars = config.get("confirmation_bars", 3)
    min_first_bar_range = config.get("min_first_bar_range_pct", 0.03)
    gap_fill_thresh = config.get("gap_fill_threshold_pct", 30)
    gap_cont_thresh = config.get("gap_continuation_threshold_pct", 0.2)

    if bar_idx < confirmation_bars + 1:
        return None

    current = bars[bar_idx]
    current_price = current["close"]
    first_bar = bars[0]

    first_bar_high = first_bar["high"]
    first_bar_low = first_bar["low"]
    first_bar_range = first_bar_high - first_bar_low
    first_bar_range_pct = (first_bar_range / first_bar["open"]) * 100 if first_bar["open"] > 0 else 0

    if first_bar_range_pct >= min_first_bar_range:
        if current_price > first_bar_high:
            confirmed = True
            for cb in range(1, min(confirmation_bars + 1, bar_idx + 1)):
                cb_bar = bars[bar_idx - cb]
                if cb_bar["close"] < cb_bar["open"]:
                    confirmed = False
                    break
            if confirmed:
                return ("first_bar_breakout", "long")

        elif current_price < first_bar_low:
            confirmed = True
            for cb in range(1, min(confirmation_bars + 1, bar_idx + 1)):
                cb_bar = bars[bar_idx - cb]
                if cb_bar["close"] > cb_bar["open"]:
                    confirmed = False
                    break
            if confirmed:
                return ("first_bar_breakout", "short")

    if abs(gap_pct) >= config.get("min_gap_pct", 0.3):
        open_price = bars[0]["open"]
        prev_day_close_approx = open_price / (1 + gap_pct / 100)
        fill_pct = abs(current_price - open_price) / abs(open_price - prev_day_close_approx) * 100 if abs(open_price - prev_day_close_approx) > 0 else 0

        if fill_pct >= gap_fill_thresh:
            if gap_pct > 0:
                return ("gap_reversal", "short")
            else:
                return ("gap_reversal", "long")

        extension = (current_price - open_price) / open_price * 100 if open_price > 0 else 0

        if gap_pct > 0 and extension > gap_cont_thresh:
            return ("gap_continuation", "long")
        elif gap_pct < 0 and extension < -gap_cont_thresh:
            return ("gap_continuation", "short")

    return None


def detect_hailmary_signal(bars: List[Dict], bar_idx: int, config: Dict) -> Optional[Tuple[str, str]]:
    """Detect HailMary momentum burst pattern with dynamic quality scoring."""
    if bar_idx < 3:
        return None

    min_change = config.get("hm_min_stock_change_pct", 0.5)
    dynamic_min_score = config.get("hm_dynamic_min_score", 30.0)
    current = bars[bar_idx]
    lookback = bars[max(0, bar_idx - 3):bar_idx]
    if not lookback:
        return None

    ref_price = lookback[0]["open"]
    if ref_price <= 0:
        return None

    move_pct = ((current["close"] - ref_price) / ref_price) * 100

    if abs(move_pct) >= min_change:
        vol_now = current["volume"]
        avg_vol = statistics.mean([b["volume"] for b in lookback]) if lookback else 1
        if avg_vol > 0 and vol_now >= avg_vol * 1.5:
            vol_ratio = vol_now / avg_vol if avg_vol > 0 else 1.0
            move_strength = abs(move_pct) / min_change
            signal_score = min(100, (vol_ratio * 15) + (move_strength * 20) + 10)
            if signal_score < dynamic_min_score:
                return None
            direction = "long" if move_pct > 0 else "short"
            return ("hailmary_momentum", direction)

    return None


def detect_optionsbot_signal(bars: List[Dict], bar_idx: int, config: Dict, rsi_val: Optional[float]) -> Optional[Tuple[str, str]]:
    """Detect OptionsBot credit spread setup (mean reversion on overextension)."""
    if bar_idx < 10:
        return None

    iv_max = config.get("ob_iv_gate_buy_max", 60)
    iv_min = config.get("ob_iv_gate_buy_min", 10)

    recent = bars[max(0, bar_idx-10):bar_idx+1]
    closes = [b["close"] for b in recent]
    if len(closes) < 5:
        return None

    mean_price = statistics.mean(closes)
    std_price = statistics.stdev(closes) if len(closes) > 1 else 0.01
    current = bars[bar_idx]["close"]
    z_score = (current - mean_price) / std_price if std_price > 0 else 0

    if rsi_val is not None:
        if rsi_val < 30 and z_score < -1.5:
            return ("credit_spread_bullish", "long")
        elif rsi_val > 70 and z_score > 1.5:
            return ("credit_spread_bearish", "short")

    return None


def simulate_day(
    symbol: str,
    day_bars: List[Dict],
    gap_pct: float,
    config: Dict,
    prev_bars_for_rsi: List[Dict],
    spy_day_bars: Optional[List[Dict]] = None,
    sr_history_bars: Optional[List[Dict]] = None,
) -> List[SimTrade]:
    """Simulate ALL bot strategies for one day with full 131-param config."""

    min_gap = config.get("min_gap_pct", 0.3)
    max_gap = config.get("max_gap_pct", 15.0)
    min_hold = config.get("min_hold_minutes", 3)
    cat_stop = config.get("catastrophic_stop", 20)
    hard_stop = config.get("hard_stop_pct", 15)
    tp_pct = config.get("take_profit_pct", 200)
    direction_lock = config.get("direction_lock", False)
    max_trades = config.get("max_trades_per_day", 3)
    quality_gate = config.get("quality_gate_mode", "fail_open")
    leverage = config.get("options_leverage", 10)
    require_vol = config.get("require_volume_spike", True)
    rsi_ob = config.get("rsi_overbought", 70)
    rsi_os = config.get("rsi_oversold", 15)
    min_spacing = config.get("min_entry_spacing_s", 0)
    max_concurrent = config.get("max_concurrent", 3)
    require_vwap = config.get("require_vwap", False)
    position_size_pct = config.get("position_size_pct", 2.0)
    max_contracts = config.get("max_contracts", 5)
    stop_after_losses = config.get("stop_after_losses", 99)
    daily_loss_cap_pct = config.get("daily_loss_cap_pct", 8)
    drawdown_reduce_pct = config.get("drawdown_reduce_pct", 5)
    require_ema = config.get("require_ema_cross", False)
    require_mkt_align = config.get("require_market_alignment", False)

    trailing_stop_pct = config.get("trailing_stop_pct", 0)
    trailing_activation_pct = config.get("trailing_activation_pct", 0)

    tier1_mult = config.get("tier1_multiplier", 3.0)
    tier1_sell = config.get("tier1_sell_pct", 50) / 100.0
    tier2_mult = config.get("tier2_multiplier", 5.0)
    tier3_mult = config.get("tier3_runner_mult", 25)

    max_hold_min = config.get("max_hold_minutes", 390)
    time_stop_min = config.get("time_stop_minutes", 390)
    flatten_before = config.get("flatten_before_close_min", 30)

    jitter_pct = config.get("stop_jitter_pct", 0)
    para_initial = config.get("parabolic_initial_trail", 30)
    para_min = config.get("parabolic_min_trail", 8)
    para_accel = config.get("parabolic_acceleration", 0.04)

    atr_mult = config.get("atr_multiplier", 2.5)
    atr_act_mult = config.get("atr_activation_mult", 0.75)
    atr_min_t = config.get("atr_min_trail_pct", 0.5)
    atr_max_t = config.get("atr_max_trail_pct", 15)
    atr_t1_tight = config.get("atr_tier1_tighten", 0.75)
    atr_t2_tight = config.get("atr_tier2_tighten", 0.50)

    sniper_vel_win = config.get("sniper_velocity_window", 5)
    sniper_vel_rev = config.get("sniper_velocity_reversal", 0.1)
    sniper_ratch_arm = config.get("sniper_ratchet_arm", 0.5)
    sniper_ratch_dist = config.get("sniper_ratchet_distance", 0.15)

    dd_halt = config.get("dd_threshold_halt", 12)
    dd_min_mult = config.get("dd_min_multiplier", 0.4)
    corr_trigger = config.get("corr_trigger_count", 3)
    corr_reduce = config.get("corr_reduce_mult", 0.5)
    corr_cooldown = config.get("corr_cooldown_min", 60)

    daily_goal = config.get("daily_goal_usd", 300)
    daily_goal_lock = config.get("daily_goal_lock_pct", 85) / 100.0
    strong_day = config.get("strong_day_usd", 500)
    freeroll_min_q = config.get("freeroll_min_quality", 90)

    trade_start_bar = max(1, config.get("trade_start_minutes_after_open", 5) // 5)
    trade_end_bar = config.get("trade_end_minutes_after_open", 390) // 5

    kelly = config.get("kelly_fraction", 0.35)
    cash_reserve = config.get("cash_reserve_pct", 20) / 100.0
    base_risk = config.get("base_risk_pct", 2.0)
    daily_budget = config.get("daily_budget_usd", 2000)

    hm_max_trades = config.get("hm_max_trades", 3)
    ob_max_trades = config.get("ob_max_trades", 3)
    ob_max_concurrent_val = config.get("ob_max_concurrent", 2)

    block_earnings = config.get("block_near_earnings", True)
    news_neg = config.get("news_negative_threshold", -0.7)
    news_bullish = config.get("news_bullish_min", 0.2)

    liq_max_spread = config.get("liq_max_spread_pct", 15)
    exit_spread_prot = config.get("exit_spread_protection_pct", 20)

    bucket_tm = config.get("bucket_twentymin_pct", 35) / 100.0
    bucket_hm = config.get("bucket_hailmary_pct", 35) / 100.0
    bucket_ob = config.get("bucket_options_pct", 20) / 100.0

    hm_max_risk = config.get("hm_max_risk_per_trade", 500)
    hm_min_change = config.get("hm_min_stock_change_pct", 0.5)
    hm_tier1_m = config.get("hm_tier1_mult", 5.0)
    hm_tier1_s = config.get("hm_tier1_sell_pct", 33) / 100.0
    hm_tier2_m = config.get("hm_tier2_mult", 10.0)

    ob_tp_target = config.get("ob_bull_put_profit_target", 0.6)
    ob_sl_pct = config.get("ob_bull_put_stop_loss_pct", 150)

    stock_cat_stop = config.get("stock_catastrophic_stop", 10)
    stock_sl = config.get("stock_stop_loss_pct", 3)
    stock_tp = config.get("stock_take_profit_pct", 15)
    stock_trail = config.get("stock_trailing_stop", 2.0)
    stock_trail_act = config.get("stock_trailing_activation", 2.0)
    stock_min_hold = config.get("stock_min_hold_minutes", 8)

    hard_stop_def = config.get("hard_stop_default", 12)

    sr_entry_prox = config.get("sr_entry_proximity_pct", 0.3)
    sr_block_resist = config.get("sr_block_near_resistance", False)
    sr_prefer_support = config.get("sr_prefer_near_support", False)
    sr_exit_resist = config.get("sr_exit_at_resistance", False)

    sr_source = sr_history_bars if sr_history_bars else prev_bars_for_rsi
    sr_levels = detect_sr_levels(sr_source, config) if sr_source else []

    available_capital = INITIAL_CAPITAL * (1 - cash_reserve)
    tm_budget = available_capital * bucket_tm
    hm_budget = available_capital * bucket_hm
    ob_budget = available_capital * bucket_ob

    if abs(gap_pct) < min_gap:
        skip_twentymin = True
    elif abs(gap_pct) > max_gap:
        skip_twentymin = True
    else:
        skip_twentymin = False

    trades = []
    open_positions = []
    day_trade_count = 0
    hm_trade_count = 0
    ob_trade_count = 0
    day_loss_count = 0
    consecutive_losses = 0
    day_pnl_usd = 0.0
    directions_traded = {}
    last_entry_ts = None
    session_locked = False
    session_floor = 0.0
    daily_spent_usd = 0.0

    position_size_usd = min(
        tm_budget * position_size_pct / 100,
        daily_budget,
        available_capital * 0.10,
    )
    hm_position_usd = min(hm_max_risk, hm_budget * 0.3)
    ob_position_usd = min(
        config.get("ob_max_position_size", 2000),
        ob_budget * 0.5,
    )

    avg_volume = statistics.mean([b["volume"] for b in day_bars[:6]]) if len(day_bars) >= 6 else 0

    warmup_bars = max(6, config.get("confirmation_bars", 3) + 2)

    atr_val = compute_atr(prev_bars_for_rsi + day_bars[:min(20, len(day_bars))])

    rsi_context_bars = prev_bars_for_rsi[-20:] if prev_bars_for_rsi else []
    ema_closes_base = [b["close"] for b in prev_bars_for_rsi]

    total_day_bars = len(day_bars)
    flatten_bar = max(0, total_day_bars - (flatten_before // 5))

    for i in range(warmup_bars, total_day_bars):
        bar = day_bars[i]
        bar_ts = datetime.fromisoformat(bar["timestamp"].replace("Z", "+00:00")) if isinstance(bar["timestamp"], str) else bar["timestamp"]

        daily_loss_cap_usd = INITIAL_CAPITAL * daily_loss_cap_pct / 100
        dd_halt_usd = INITIAL_CAPITAL * dd_halt / 100

        if day_pnl_usd <= -daily_loss_cap_usd or day_pnl_usd <= -dd_halt_usd:
            for pos in open_positions:
                hold_min = (i - pos["entry_bar_idx"]) * 5
                entry = pos["entry_price"]
                cp = bar["close"]
                lev = pos.get("leverage", leverage)
                if pos["direction"] == "long":
                    rmp = ((cp - entry) / entry) * 100
                else:
                    rmp = ((entry - cp) / entry) * 100
                lp = rmp * lev * pos.get("size_mult", 1.0)
                trade = SimTrade(symbol=symbol, direction=pos["direction"], pattern=pos["pattern"],
                    entry_time=pos["entry_time"], entry_price=entry, exit_time=bar_ts, exit_price=cp,
                    exit_reason="daily_loss_cap" if day_pnl_usd <= -daily_loss_cap_usd else "drawdown_halt",
                    gap_pct=gap_pct, hold_minutes=hold_min,
                    pnl_pct=lp, pnl_usd=lp / 100 * pos.get("pos_size_usd", position_size_usd),
                    max_favorable=pos.get("max_favorable", 0), max_adverse=pos.get("max_adverse", 0),
                    volume_at_entry=pos.get("volume_at_entry", 0), bars_held=i - pos["entry_bar_idx"],
                    sim_mode=pos.get("sim_mode", "twentymin"))
                trades.append(trade)
            open_positions.clear()
            break

        if session_locked and day_pnl_usd < session_floor:
            for pos in open_positions:
                hold_min = (i - pos["entry_bar_idx"]) * 5
                entry = pos["entry_price"]
                cp = bar["close"]
                lev = pos.get("leverage", leverage)
                if pos["direction"] == "long":
                    rmp = ((cp - entry) / entry) * 100
                else:
                    rmp = ((entry - cp) / entry) * 100
                lp = rmp * lev * pos.get("size_mult", 1.0)
                trade = SimTrade(symbol=symbol, direction=pos["direction"], pattern=pos["pattern"],
                    entry_time=pos["entry_time"], entry_price=entry, exit_time=bar_ts, exit_price=cp,
                    exit_reason="session_floor_protect",
                    gap_pct=gap_pct, hold_minutes=hold_min,
                    pnl_pct=lp, pnl_usd=lp / 100 * pos.get("pos_size_usd", position_size_usd),
                    max_favorable=pos.get("max_favorable", 0), max_adverse=pos.get("max_adverse", 0),
                    volume_at_entry=pos.get("volume_at_entry", 0), bars_held=i - pos["entry_bar_idx"],
                    sim_mode=pos.get("sim_mode", "twentymin"))
                trades.append(trade)
            open_positions.clear()
            break

        closed_positions = []
        for pos_idx, pos in enumerate(open_positions):
            bars_since_entry = i - pos["entry_bar_idx"]
            hold_min = bars_since_entry * 5

            current_price = bar["close"]
            bar_high = bar["high"]
            bar_low = bar["low"]
            entry = pos["entry_price"]
            size_mult = pos.get("size_mult", 1.0)
            lev = pos.get("leverage", leverage)
            pos_mode = pos.get("sim_mode", "twentymin")
            pos_size = pos.get("pos_size_usd", position_size_usd)
            pos_min_hold = pos.get("min_hold", min_hold)
            pos_cat_stop = pos.get("cat_stop", cat_stop)
            pos_hard_stop = pos.get("hard_stop", hard_stop)
            pos_tp = pos.get("tp_pct", tp_pct)

            if pos["direction"] == "long":
                raw_move_pct = ((current_price - entry) / entry) * 100
                intra_high_pct = ((bar_high - entry) / entry) * 100
                intra_low_pct = ((bar_low - entry) / entry) * 100
            else:
                raw_move_pct = ((entry - current_price) / entry) * 100
                intra_high_pct = ((entry - bar_low) / entry) * 100
                intra_low_pct = ((entry - bar_high) / entry) * 100

            leveraged_pnl_pct = raw_move_pct * lev
            leveraged_high = intra_high_pct * lev
            leveraged_low = intra_low_pct * lev

            pos["max_favorable"] = max(pos.get("max_favorable", 0), leveraged_high)
            pos["max_adverse"] = min(pos.get("max_adverse", 0), leveraged_low)

            jitter = 0
            if jitter_pct > 0:
                jitter = random.uniform(-jitter_pct, jitter_pct)

            trail_triggered = pos.get("trailing_triggered", False)
            pos_trail_stop = pos.get("trail_stop_pct", trailing_stop_pct)
            pos_trail_act = pos.get("trail_act_pct", trailing_activation_pct)

            if pos_trail_stop > 0 and pos_trail_act > 0:
                if pos["max_favorable"] >= pos_trail_act:
                    if pos_mode == "twentymin" and atr_val and atr_val > 0:
                        atr_trail_width = atr_mult * atr_val / entry * 100 * lev
                        atr_trail_width = max(atr_min_t, min(atr_max_t, atr_trail_width))
                        if pos["max_favorable"] >= tier1_mult * 100:
                            atr_trail_width *= atr_t1_tight
                        if pos["max_favorable"] >= tier2_mult * 100:
                            atr_trail_width *= atr_t2_tight
                        trail_level = pos["max_favorable"] - atr_trail_width
                    else:
                        if para_accel > 0 and pos["max_favorable"] >= pos_tp * 0.5:
                            bars_in_profit = max(1, bars_since_entry - pos.get("profit_start_bar", bars_since_entry))
                            para_trail = max(para_min, para_initial - (para_accel * 100 * bars_in_profit))
                            trail_level = pos["max_favorable"] - para_trail
                        else:
                            trail_level = pos["max_favorable"] - pos_trail_stop

                    if leveraged_pnl_pct <= trail_level:
                        trail_triggered = True
                        pos["trailing_triggered"] = True

                    if not pos.get("profit_start_bar") and leveraged_pnl_pct > 0:
                        pos["profit_start_bar"] = bars_since_entry

            exit_reason = None
            exit_pnl_override = None

            effective_cat = pos_cat_stop + jitter
            effective_hard = pos_hard_stop + jitter

            if leveraged_low <= -effective_cat:
                exit_reason = "catastrophic_stop"
                exit_pnl_override = -effective_cat

            elif i >= flatten_bar:
                exit_reason = "flatten_before_close"

            elif hold_min >= max_hold_min or hold_min >= time_stop_min:
                exit_reason = "time_stop"

            elif hold_min >= pos_min_hold:
                if leveraged_low <= -effective_hard:
                    exit_reason = "hard_stop"
                    exit_pnl_override = -effective_hard

                elif trail_triggered:
                    exit_reason = "trailing_stop"

                elif leveraged_high >= pos_tp:
                    exit_reason = "take_profit"
                    exit_pnl_override = pos_tp

                elif pos_mode == "twentymin":
                    if tier3_mult > 0 and leveraged_high >= tier3_mult * 100:
                        exit_reason = "tier3_runner"
                        exit_pnl_override = tier3_mult * 100
                    elif tier2_mult > 0 and leveraged_high >= tier2_mult * 100:
                        exit_reason = "tier2_exit"
                        exit_pnl_override = tier2_mult * 100
                    elif tier1_mult > 0 and leveraged_high >= tier1_mult * 100:
                        exit_reason = "tier1_exit"
                        exit_pnl_override = tier1_mult * 100
                        size_mult *= (1.0 - tier1_sell)

                elif pos_mode == "hailmary":
                    if hm_tier2_m > 0 and leveraged_high >= hm_tier2_m * 100:
                        exit_reason = "hm_tier2_exit"
                        exit_pnl_override = hm_tier2_m * 100
                    elif hm_tier1_m > 0 and leveraged_high >= hm_tier1_m * 100:
                        exit_reason = "hm_tier1_exit"
                        exit_pnl_override = hm_tier1_m * 100
                        size_mult *= (1.0 - hm_tier1_s)

                elif pos_mode == "optionsbot":
                    ob_profit = leveraged_high
                    ob_credit_pnl = raw_move_pct
                    if ob_credit_pnl >= ob_tp_target * 100:
                        exit_reason = "ob_profit_target"
                        exit_pnl_override = ob_tp_target * 100
                    elif leveraged_low <= -(ob_sl_pct):
                        exit_reason = "ob_stop_loss"
                        exit_pnl_override = -ob_sl_pct

                if exit_reason is None and pos_mode != "optionsbot":
                    if sniper_vel_win > 0 and bars_since_entry >= sniper_vel_win:
                        recent_closes = [day_bars[j]["close"] for j in range(max(0, i - sniper_vel_win), i + 1)]
                        if len(recent_closes) >= 2 and leveraged_pnl_pct >= sniper_ratch_arm:
                            velocity = (recent_closes[-1] - recent_closes[0]) / recent_closes[0] * 100 if recent_closes[0] > 0 else 0
                            if pos["direction"] == "long" and velocity < -sniper_vel_rev:
                                exit_reason = "sniper_velocity"
                            elif pos["direction"] == "short" and velocity > sniper_vel_rev:
                                exit_reason = "sniper_velocity"

                if exit_reason is None and sr_exit_resist and leveraged_pnl_pct > 0:
                    sr_r = pos.get("sr_resist_price")
                    sr_s = pos.get("sr_support_price")
                    if pos["direction"] == "long" and sr_r and bar_high >= sr_r:
                        exit_reason = "sr_resistance_exit"
                        r_pnl = ((sr_r - entry) / entry) * 100 * lev
                        exit_pnl_override = max(r_pnl, 0)
                    elif pos["direction"] == "short" and sr_s and bar_low <= sr_s:
                        exit_reason = "sr_support_exit"
                        s_pnl = ((entry - sr_s) / entry) * 100 * lev
                        exit_pnl_override = max(s_pnl, 0)

            if i == total_day_bars - 1 and exit_reason is None:
                exit_reason = "eod_close"

            if exit_reason:
                final_pnl = exit_pnl_override if exit_pnl_override is not None else leveraged_pnl_pct
                trade_pnl_usd = final_pnl / 100 * pos_size * size_mult
                trade = SimTrade(
                    symbol=symbol, direction=pos["direction"], pattern=pos["pattern"],
                    entry_time=pos["entry_time"], entry_price=entry, exit_time=bar_ts,
                    exit_price=current_price, exit_reason=exit_reason, gap_pct=gap_pct,
                    hold_minutes=hold_min, pnl_pct=final_pnl,
                    pnl_usd=trade_pnl_usd,
                    max_favorable=pos.get("max_favorable", 0), max_adverse=pos.get("max_adverse", 0),
                    volume_at_entry=pos.get("volume_at_entry", 0), bars_held=bars_since_entry,
                    sim_mode=pos_mode,
                )
                trades.append(trade)
                day_pnl_usd += trade_pnl_usd
                if trade_pnl_usd < 0:
                    day_loss_count += 1
                    consecutive_losses += 1
                else:
                    consecutive_losses = 0
                closed_positions.append(pos_idx)

        for idx in sorted(closed_positions, reverse=True):
            open_positions.pop(idx)

        if not session_locked and day_pnl_usd >= daily_goal:
            session_locked = True
            session_floor = day_pnl_usd * daily_goal_lock
        elif session_locked and day_pnl_usd >= strong_day:
            new_floor = day_pnl_usd * (daily_goal_lock + 0.05)
            session_floor = max(session_floor, new_floor)

        if i < trade_start_bar or i > trade_end_bar:
            continue
        if i >= flatten_bar:
            continue

        size_multiplier = 1.0
        drawdown_pnl_threshold = -(INITIAL_CAPITAL * drawdown_reduce_pct / 100)
        if day_pnl_usd <= drawdown_pnl_threshold:
            size_multiplier = max(dd_min_mult, 0.5)

        if consecutive_losses >= corr_trigger:
            size_multiplier *= corr_reduce

        if session_locked:
            freeroll_allowed = (day_pnl_usd - session_floor) > 0
            if not freeroll_allowed:
                continue

        tm_count = sum(1 for p in open_positions if p.get("sim_mode") == "twentymin")
        hm_count = sum(1 for p in open_positions if p.get("sim_mode") == "hailmary")
        ob_count = sum(1 for p in open_positions if p.get("sim_mode") == "optionsbot")

        rsi_val = compute_rsi(rsi_context_bars + day_bars[max(0,i-14):i+1])

        if not skip_twentymin and day_trade_count < max_trades and tm_count < max_concurrent:
            if day_loss_count < stop_after_losses:
                if min_spacing <= 0 or last_entry_ts is None:
                    can_enter_tm = True
                else:
                    try:
                        elapsed = (bar_ts - last_entry_ts).total_seconds()
                        can_enter_tm = elapsed >= min_spacing
                    except:
                        can_enter_tm = True

                if can_enter_tm:
                    pattern_result = detect_pattern(gap_pct, day_bars, i, config)
                    if pattern_result:
                        pattern_name, direction = pattern_result

                        if direction_lock and symbol in directions_traded:
                            if directions_traded[symbol] != direction:
                                pattern_result = None

                        if pattern_result:
                            already_in = any(p["symbol"] == symbol and p["direction"] == direction for p in open_positions)
                            if already_in:
                                pattern_result = None

                        if pattern_result:
                            passed_quality = True

                            if quality_gate == "fail_closed" or require_vwap:
                                if rsi_val is not None:
                                    if direction == "long" and rsi_val > rsi_ob:
                                        passed_quality = False
                                    if direction == "short" and rsi_val < rsi_os:
                                        passed_quality = False
                                elif quality_gate == "fail_closed":
                                    passed_quality = False

                                vwap = compute_vwap(day_bars[max(0,i-20):i+1])
                                if require_vwap or quality_gate == "fail_closed":
                                    if vwap is not None:
                                        if direction == "long" and bar["close"] < vwap:
                                            passed_quality = False
                                        if direction == "short" and bar["close"] > vwap:
                                            passed_quality = False
                                    elif quality_gate == "fail_closed":
                                        passed_quality = False

                                if require_vol and avg_volume > 0:
                                    if bar["volume"] < avg_volume * 1.2:
                                        if quality_gate == "fail_closed":
                                            passed_quality = False
                            elif require_vol and avg_volume > 0:
                                if bar["volume"] < avg_volume * 1.2:
                                    passed_quality = False

                            if require_ema:
                                closes_to_i = ema_closes_base + [b["close"] for b in day_bars[:i+1]]
                                cur_ema9 = compute_ema(closes_to_i[-30:], 9)
                                cur_ema21 = compute_ema(closes_to_i[-40:], 21)
                                if cur_ema9 is not None and cur_ema21 is not None:
                                    if direction == "long" and cur_ema9 <= cur_ema21:
                                        passed_quality = False
                                    elif direction == "short" and cur_ema9 >= cur_ema21:
                                        passed_quality = False

                            if require_mkt_align and spy_day_bars and len(spy_day_bars) > i:
                                spy_bar = spy_day_bars[i]
                                spy_open = spy_day_bars[0]["open"]
                                spy_move = (spy_bar["close"] - spy_open) / spy_open * 100 if spy_open > 0 else 0
                                if direction == "long" and spy_move < -0.1:
                                    passed_quality = False
                                elif direction == "short" and spy_move > 0.1:
                                    passed_quality = False

                            if passed_quality and sr_levels:
                                sr_info = sr_price_info(bar["close"], sr_levels, sr_entry_prox)
                                if sr_block_resist and direction == "long" and sr_info["near_resistance"]:
                                    passed_quality = False
                                if sr_block_resist and direction == "short" and sr_info["near_support"]:
                                    passed_quality = False
                                if sr_prefer_support and direction == "long" and not sr_info["near_support"]:
                                    if sr_info["support_distance_pct"] > sr_entry_prox * 3:
                                        passed_quality = False

                            if passed_quality:
                                actual_size = position_size_usd * size_multiplier
                                daily_spent_usd += actual_size

                                sr_resist_price = None
                                sr_support_price = None
                                if sr_levels:
                                    sr_at_entry = sr_price_info(bar["close"], sr_levels, sr_entry_prox)
                                    sr_resist_price = sr_at_entry.get("nearest_resistance_price")
                                    sr_support_price = sr_at_entry.get("nearest_support_price")

                                open_positions.append({
                                    "symbol": symbol,
                                    "direction": direction,
                                    "pattern": pattern_name,
                                    "entry_price": bar["close"],
                                    "entry_time": bar_ts,
                                    "entry_bar_idx": i,
                                    "max_favorable": 0,
                                    "max_adverse": 0,
                                    "volume_at_entry": bar["volume"],
                                    "trailing_triggered": False,
                                    "size_mult": size_multiplier,
                                    "sim_mode": "twentymin",
                                    "leverage": leverage,
                                    "pos_size_usd": actual_size,
                                    "min_hold": min_hold,
                                    "cat_stop": cat_stop,
                                    "hard_stop": hard_stop,
                                    "tp_pct": tp_pct,
                                    "trail_stop_pct": trailing_stop_pct,
                                    "trail_act_pct": trailing_activation_pct,
                                    "sr_resist_price": sr_resist_price,
                                    "sr_support_price": sr_support_price,
                                })
                                day_trade_count += 1
                                last_entry_ts = bar_ts
                                directions_traded[symbol] = direction

        if hm_trade_count < hm_max_trades and hm_count < 2:
            hm_signal = detect_hailmary_signal(day_bars, i, config)
            if hm_signal:
                hm_pattern, hm_dir = hm_signal
                already_hm = any(p["symbol"] == symbol and p.get("sim_mode") == "hailmary" for p in open_positions)
                if not already_hm:
                    hm_lev = leverage * 1.5
                    actual_hm_size = hm_position_usd * size_multiplier
                    open_positions.append({
                        "symbol": symbol,
                        "direction": hm_dir,
                        "pattern": hm_pattern,
                        "entry_price": bar["close"],
                        "entry_time": bar_ts,
                        "entry_bar_idx": i,
                        "max_favorable": 0,
                        "max_adverse": 0,
                        "volume_at_entry": bar["volume"],
                        "trailing_triggered": False,
                        "size_mult": size_multiplier,
                        "sim_mode": "hailmary",
                        "leverage": hm_lev,
                        "pos_size_usd": actual_hm_size,
                        "min_hold": 5,
                        "cat_stop": cat_stop * 1.5,
                        "hard_stop": hard_stop * 1.5,
                        "tp_pct": config.get("hm_profit_target_mult", 25) * 100,
                        "trail_stop_pct": trailing_stop_pct * 1.5 if trailing_stop_pct > 0 else 0,
                        "trail_act_pct": trailing_activation_pct,
                    })
                    hm_trade_count += 1
                    last_entry_ts = bar_ts

        if ob_trade_count < ob_max_trades and ob_count < ob_max_concurrent_val:
            ob_signal = detect_optionsbot_signal(day_bars, i, config, rsi_val)
            if ob_signal:
                ob_pattern, ob_dir = ob_signal
                already_ob = any(p["symbol"] == symbol and p.get("sim_mode") == "optionsbot" for p in open_positions)
                if not already_ob:
                    actual_ob_size = ob_position_usd * size_multiplier
                    open_positions.append({
                        "symbol": symbol,
                        "direction": ob_dir,
                        "pattern": ob_pattern,
                        "entry_price": bar["close"],
                        "entry_time": bar_ts,
                        "entry_bar_idx": i,
                        "max_favorable": 0,
                        "max_adverse": 0,
                        "volume_at_entry": bar["volume"],
                        "trailing_triggered": False,
                        "size_mult": size_multiplier,
                        "sim_mode": "optionsbot",
                        "leverage": leverage * 0.5,
                        "pos_size_usd": actual_ob_size,
                        "min_hold": 15,
                        "cat_stop": ob_sl_pct,
                        "hard_stop": ob_sl_pct * 0.8,
                        "tp_pct": ob_tp_target * 100 * 2,
                        "trail_stop_pct": 0,
                        "trail_act_pct": 0,
                    })
                    ob_trade_count += 1

    for pos in open_positions:
        last_bar = day_bars[-1]
        bar_ts = datetime.fromisoformat(last_bar["timestamp"].replace("Z", "+00:00")) if isinstance(last_bar["timestamp"], str) else last_bar["timestamp"]
        hold_min = (total_day_bars - 1 - pos["entry_bar_idx"]) * 5
        entry = pos["entry_price"]
        current_price = last_bar["close"]
        lev = pos.get("leverage", leverage)

        if pos["direction"] == "long":
            raw_move_pct = ((current_price - entry) / entry) * 100
        else:
            raw_move_pct = ((entry - current_price) / entry) * 100

        leveraged_pnl = raw_move_pct * lev

        trade = SimTrade(
            symbol=pos["symbol"], direction=pos["direction"], pattern=pos["pattern"],
            entry_time=pos["entry_time"], entry_price=entry, exit_time=bar_ts,
            exit_price=current_price, exit_reason="eod_close", gap_pct=gap_pct,
            hold_minutes=hold_min, pnl_pct=leveraged_pnl,
            pnl_usd=leveraged_pnl / 100 * pos.get("pos_size_usd", position_size_usd),
            max_favorable=pos.get("max_favorable", 0), max_adverse=pos.get("max_adverse", 0),
            volume_at_entry=pos.get("volume_at_entry", 0),
            bars_held=total_day_bars - 1 - pos["entry_bar_idx"],
            sim_mode=pos.get("sim_mode", "twentymin"),
        )
        trades.append(trade)

    return trades


def _compute_breadth_by_day(bars_by_symbol: Dict[str, List[Dict]]) -> Dict[str, Dict]:
    """Pre-compute breadth selloff state for each trading day across all symbols."""
    all_days_by_symbol = {}
    for symbol, bars in bars_by_symbol.items():
        all_days_by_symbol[symbol] = group_bars_by_day(bars)

    all_day_keys = set()
    for sym_days in all_days_by_symbol.values():
        all_day_keys.update(sym_days.keys())

    breadth_by_day = {}
    for day_key in sorted(all_day_keys):
        gaps_down = 0
        gaps_up = 0
        total_sig = 0
        mag_sum = 0.0

        for symbol, sym_days in all_days_by_symbol.items():
            sorted_sym_days = sorted(sym_days.keys())
            if day_key not in sym_days:
                continue
            day_idx = sorted_sym_days.index(day_key)
            if day_idx == 0:
                continue
            prev_key = sorted_sym_days[day_idx - 1]
            prev_bars = sym_days.get(prev_key, [])
            day_bars = sym_days[day_key]
            gap = compute_gap(prev_bars, day_bars)
            if gap is None:
                continue
            if abs(gap) >= 1.0:
                total_sig += 1
                mag_sum += abs(gap)
                if gap < 0:
                    gaps_down += 1
                else:
                    gaps_up += 1

        breadth_by_day[day_key] = {
            "down": gaps_down,
            "up": gaps_up,
            "total_significant": total_sig,
            "avg_magnitude": mag_sum / total_sig if total_sig else 0,
            "down_ratio": gaps_down / total_sig if total_sig else 0,
            "up_ratio": gaps_up / total_sig if total_sig else 0,
        }

    return breadth_by_day


def run_sweep(bars_by_symbol: Dict[str, List[Dict]], config: Dict) -> List[SimTrade]:
    """Run a full sweep simulation across all symbols and days."""
    all_trades = []

    spy_days = {}
    if "SPY" in bars_by_symbol:
        spy_days = group_bars_by_day(bars_by_symbol["SPY"])

    selloff_enabled = config.get("selloff_enabled", True)
    selloff_threshold = config.get("selloff_breadth_threshold", 0.65)
    selloff_min_count = config.get("selloff_min_gap_count", 10)
    selloff_mode = config.get("selloff_mode", "reduce")
    selloff_mult = config.get("selloff_max_concurrent_mult", 0.5)

    breadth_by_day = _compute_breadth_by_day(bars_by_symbol) if selloff_enabled else {}

    for symbol, bars in bars_by_symbol.items():
        days = group_bars_by_day(bars)
        sorted_days = sorted(days.keys())

        for day_idx, day_key in enumerate(sorted_days):
            day_bars = days[day_key]
            if len(day_bars) < 12:
                continue

            prev_day_key = sorted_days[day_idx - 1] if day_idx > 0 else None
            prev_day_bars = days.get(prev_day_key, []) if prev_day_key else []

            gap = compute_gap(prev_day_bars, day_bars)
            if gap is None:
                continue

            breadth = breadth_by_day.get(day_key, {})
            is_selloff = False
            is_rally = False
            if selloff_enabled and breadth.get("total_significant", 0) >= selloff_min_count:
                if breadth.get("down_ratio", 0) >= selloff_threshold:
                    is_selloff = True
                elif breadth.get("up_ratio", 0) >= selloff_threshold:
                    is_rally = True

            if is_selloff or is_rally:
                if selloff_mode == "halt":
                    continue
                if selloff_mode == "shorts_only" and is_selloff and gap > 0:
                    continue
                if selloff_mode == "favor_shorts" and is_selloff and gap > 0:
                    gap = gap * 0.5

            effective_config = dict(config)
            if (is_selloff or is_rally) and selloff_mode == "reduce":
                orig_max = effective_config.get("max_concurrent", 3)
                effective_config["max_concurrent"] = max(1, int(orig_max * selloff_mult))

            sr_lookback_days = 5
            sr_history_bars = []
            for prev_i in range(max(0, day_idx - sr_lookback_days), day_idx):
                pk = sorted_days[prev_i]
                sr_history_bars.extend(days.get(pk, []))

            rsi_context = prev_day_bars[-30:] if prev_day_bars else []

            spy_day = spy_days.get(day_key, None)

            day_trades = simulate_day(symbol, day_bars, gap, effective_config, rsi_context, spy_day, sr_history_bars)
            all_trades.extend(day_trades)

    return all_trades


def calculate_metrics(trades: List[SimTrade]) -> Dict[str, Any]:
    """Calculate comprehensive metrics from simulated trades."""
    if not trades:
        return {
            "total_trades": 0, "win_rate": 0, "total_pnl_usd": 0, "total_pnl_pct": 0,
            "profit_factor": 0, "sharpe": 0, "max_drawdown_pct": 0, "avg_trade_pnl": 0,
            "avg_winner_pnl": 0, "avg_loser_pnl": 0, "avg_hold_min": 0,
            "composite_score": -999,
            "twentymin_trades": 0, "hailmary_trades": 0, "optionsbot_trades": 0,
            "exit_breakdown": {},
        }

    pnls = [t.pnl_usd for t in trades]
    pnl_pcts = [t.pnl_pct for t in trades]
    total_pnl = sum(pnls)
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]
    win_rate = len(winners) / len(pnls) if pnls else 0

    gross_profit = sum(winners) if winners else 0
    gross_loss = abs(sum(losers)) if losers else 0.01
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (10.0 if gross_profit > 0 else 0)

    avg_winner = statistics.mean(winners) if winners else 0
    avg_loser = statistics.mean(losers) if losers else 0
    avg_hold = statistics.mean([t.hold_minutes for t in trades]) if trades else 0

    exit_reasons = {}
    for t in trades:
        exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1
    total = len(trades)

    tm_trades = sum(1 for t in trades if t.sim_mode == "twentymin")
    hm_trades = sum(1 for t in trades if t.sim_mode == "hailmary")
    ob_trades = sum(1 for t in trades if t.sim_mode == "optionsbot")

    if len(pnl_pcts) > 1:
        returns = [p / 100 for p in pnl_pcts]
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

    pf_capped = min(profit_factor, 10)
    total_pnl_pct = (total_pnl / INITIAL_CAPITAL) * 100

    min_trades_for_significance = 10
    trade_penalty = 0
    if total < min_trades_for_significance:
        trade_penalty = (min_trades_for_significance - total) * 30

    score = (
        (total_pnl_pct * 30) +
        (pf_capped * 15) +
        (sharpe * 10) +
        (win_rate * 25) -
        (max_dd * 100 * 10) +
        (min(total, 50) * 2) -
        trade_penalty
    )

    def er(reason):
        return round(exit_reasons.get(reason, 0) / total * 100, 1) if total else 0

    return {
        "total_trades": total,
        "win_rate": round(win_rate, 4),
        "total_pnl_usd": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "profit_factor": round(profit_factor, 2),
        "sharpe": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "avg_trade_pnl": round(statistics.mean(pnls), 2) if pnls else 0,
        "avg_winner_pnl": round(avg_winner, 2),
        "avg_loser_pnl": round(avg_loser, 2),
        "avg_hold_min": round(avg_hold, 1),
        "twentymin_trades": tm_trades,
        "hailmary_trades": hm_trades,
        "optionsbot_trades": ob_trades,
        "exit_breakdown": {k: v for k, v in sorted(exit_reasons.items(), key=lambda x: -x[1])},
        "composite_score": round(score, 2),
    }


def analyze_kill_factors(results: List[Dict]) -> Dict[str, Any]:
    """Analyze which parameters have the most impact on performance."""
    if not results:
        return {}

    analysis = {}
    all_params = results[0]["params"].keys() if results else []

    for param in all_params:
        value_scores = {}
        for r in results:
            val = str(r["params"].get(param, ""))
            score = r["metrics"]["composite_score"]
            if val not in value_scores:
                value_scores[val] = []
            value_scores[val].append(score)

        avg_scores = {v: statistics.mean(s) for v, s in value_scores.items() if s}

        if len(avg_scores) > 1:
            best_val = max(avg_scores, key=avg_scores.get)
            worst_val = min(avg_scores, key=avg_scores.get)
            impact = avg_scores[best_val] - avg_scores[worst_val]

            analysis[param] = {
                "best_value": best_val,
                "avg_score_at_best": round(avg_scores[best_val], 2),
                "worst_value": worst_val,
                "avg_score_at_worst": round(avg_scores[worst_val], 2),
                "impact_range": round(impact, 2),
                "all_values": {k: round(v, 2) for k, v in sorted(avg_scores.items(), key=lambda x: -x[1])},
            }

    analysis = dict(sorted(analysis.items(), key=lambda x: -x[1].get("impact_range", 0)))
    return analysis


# ═══════════════════════════════════════════════════════════════════════════════
# HORSE RACE VISUALIZATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

HORSE_COLORS = [
    "\033[93m",  # yellow
    "\033[96m",  # cyan
    "\033[92m",  # green
    "\033[95m",  # magenta
    "\033[94m",  # blue
    "\033[91m",  # red
]
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
WHITE = "\033[97m"
MAGENTA = "\033[95m"

HORSE_NAMES = [
    "Thunderbolt", "Midnight Run", "Gold Rush", "Iron Will",
    "Shadow Fox", "Blaze", "Stormchaser", "Lucky Strike",
    "Viper", "Maverick", "Phantom", "Rocket",
    "Avalanche", "Wildcard", "Steel Nerves", "Dark Horse",
]

PARAM_EXPLANATIONS = {
    "min_gap_pct": ("gap floor", "tighter gap filter", "wider gap acceptance", "%"),
    "max_gap_pct": ("gap ceiling", "conservative cap", "chasing bigger gaps", "%"),
    "confirmation_bars": ("confirmation", "more patience at entry", "faster trigger-pull", " bars"),
    "time_stop_minutes": ("time stop", "longer leash", "quicker time-based exit", "m"),
    "hard_stop_pct": ("hard stop", "tighter risk control", "wider breathing room", "%"),
    "take_profit_pct": ("take-profit", "greedier targets", "banking gains quicker", "%"),
    "trailing_stop_pct": ("trail stop", "tighter trail", "looser trailing stop", "%"),
    "trailing_activation_pct": ("trail activation", "earlier trail start", "waits for more profit first", "%"),
    "rsi_overbought": ("RSI ceiling", "stricter overbought filter", "lets momentum run hotter", ""),
    "rsi_oversold": ("RSI floor", "buying deeper dips", "more aggressive dip entries", ""),
    "max_trades_per_day": ("daily limit", "fewer but pickier trades", "more shots on goal", " trades"),
    "position_size_pct": ("position sizing", "smaller positions", "bigger bets per trade", "%"),
    "options_leverage": ("leverage", "less leverage", "juicing returns with leverage", "x"),
    "require_volume_spike": ("volume filter", "requires volume spike", "skipping volume check", ""),
    "require_vwap": ("VWAP filter", "requires VWAP confirmation", "skipping VWAP check", ""),
    "direction_lock": ("direction lock", "locks to one direction", "trades both directions", ""),
    "min_hold_minutes": ("min hold", "quicker exits allowed", "forces longer holds", "m"),
    "max_hold_minutes": ("max hold", "shorter max hold", "letting winners run longer", "m"),
    "daily_loss_cap_pct": ("daily loss cap", "tighter daily loss limit", "more risk tolerance", "%"),
    "stop_after_losses": ("loss streak limit", "stops after fewer losses", "more tolerance for losing streaks", ""),
    "max_concurrent": ("max positions", "fewer concurrent positions", "more simultaneous exposure", ""),
    "sr_block_near_resistance": ("resistance block", "avoids entries near resistance", "ignoring resistance", ""),
    "sr_prefer_near_support": ("support preference", "prefers entries near support", "ignoring support levels", ""),
    "sr_exit_at_resistance": ("resistance exits", "takes profit at resistance", "lets price push through", ""),
    "sr_lookback": ("S/R lookback", "shorter S/R history", "deeper S/R analysis", " days"),
    "sr_min_strength": ("S/R strength", "weaker levels accepted", "only strong levels matter", ""),
    "tier1_multiplier": ("tier-1 exit", "lower tier-1 target", "higher first exit target", "x"),
    "tier1_sell_pct": ("tier-1 size", "sells less at tier-1", "banks more at tier-1", "%"),
    "profit_sniper_target_pct": ("sniper target", "lower sniper target", "higher sniper target", "%"),
    "atr_trail_multiplier": ("ATR trail", "tighter ATR trail", "wider ATR trailing stop", "x"),
    "flatten_before_close_min": ("close flatten", "earlier flatten", "holds closer to bell", "m"),
    "hm_max_premium": ("HM premium cap", "cheaper options", "paying more for entries", "$"),
    "hm_min_stock_change_pct": ("HM momentum", "lower momentum bar", "needs bigger moves", "%"),
    "hm_dynamic_min_score": ("HM min score", "lower quality bar", "only high-conviction setups", ""),
    "ob_bull_put_min_credit": ("credit minimum", "takes smaller credits", "needs bigger premiums", "$"),
    "quality_gate_mode": ("quality gate", "strict quality filtering", "relaxed quality gate", ""),
    "daily_budget_pct": ("budget %", "smaller daily budget", "bigger daily budget", "%"),
    "kelly_fraction": ("Kelly fraction", "conservative Kelly", "aggressive Kelly sizing", ""),
    "time_based_exit_pct": ("time exit %", "exits more at time stop", "exits less at time stop", "%"),
    "catastrophic_stop": ("catastrophic stop", "tighter catastrophic stop", "looser catastrophic stop", "%"),
}

SURGE_PHRASES = [
    "SURGES into",
    "ROCKETS into",
    "BLASTS into",
    "CHARGES into",
    "POWERS into",
    "STORMS into",
    "VAULTS into",
]

LEAD_PHRASES = [
    "extends the lead!",
    "pulls further ahead!",
    "is running away with it!",
    "widens the gap!",
    "is in a league of its own!",
    "just keeps on running!",
]

STRUGGLE_PHRASES = [
    "is bleeding money",
    "can't find its stride",
    "is fading fast",
    "is in trouble",
    "stumbles badly",
    "falls behind the pack",
]

NEW_ENTRY_PHRASES = [
    "enters the race!",
    "joins the field!",
    "steps onto the track!",
    "approaches the starting gate!",
    "trots to the post!",
]

POSITION_LABELS = {1: "1st 🏆", 2: "2nd 🥈", 3: "3rd 🥉", 4: "4th", 5: "5th", 6: "6th"}


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{['th','st','nd','rd'][n % 10] if n % 10 < 4 else 'th'}"


def _bar(value: float, max_value: float, width: int = 35) -> str:
    if max_value <= 0:
        return "░" * width
    fill = max(0, min(width, int((value / max_value) * width)))
    return "█" * fill + "▓" + "░" * (width - fill - 1)


def _param_diff_explanation(winner_params: dict, loser_params: dict) -> list:
    diffs = []
    for key in PARAM_EXPLANATIONS:
        if key in winner_params and key in loser_params:
            w_val = winner_params[key]
            l_val = loser_params[key]
            if str(w_val) != str(l_val):
                name, low_desc, high_desc, unit = PARAM_EXPLANATIONS[key]
                if isinstance(w_val, bool) or isinstance(l_val, bool):
                    desc = f"{name}: {'ON' if w_val else 'OFF'} vs {'ON' if l_val else 'OFF'}"
                elif isinstance(w_val, (int, float)):
                    desc = f"{name} {w_val}{unit} vs {l_val}{unit}"
                    if w_val > l_val:
                        desc += f" — {high_desc}"
                    else:
                        desc += f" — {low_desc}"
                else:
                    desc = f"{name}: {w_val} vs {l_val}"
                diffs.append(desc)
    return diffs


def _generate_commentary(event_type: str, horse_name: str, rank: int, pnl: float,
                          params: dict, prev_rank: int = 0, leader_params: dict = None,
                          loser_params: dict = None) -> str:
    color = HORSE_COLORS[hash(horse_name) % len(HORSE_COLORS)]

    if event_type == "new_leader":
        reason_parts = []
        compare_to = loser_params or leader_params
        if compare_to:
            diffs = _param_diff_explanation(params, compare_to)
            if diffs:
                reason_parts = diffs[:2]
        phrase = random.choice(LEAD_PHRASES)
        msg = f"🎙️ \"{color}{BOLD}{horse_name}{RESET} takes the lead with {GREEN}${pnl:,.0f}{RESET} and {phrase}"
        if reason_parts:
            msg += f"\n     Why? {reason_parts[0]}"
            if len(reason_parts) > 1:
                msg += f" + {reason_parts[1]}"
        msg += "\""
        return msg

    elif event_type == "position_jump":
        phrase = random.choice(SURGE_PHRASES)
        msg = f"🎙️ \"{color}{horse_name}{RESET} {phrase} {_ordinal(rank)} with {GREEN}${pnl:,.0f}{RESET}!"
        if leader_params:
            diffs = _param_diff_explanation(params, leader_params)
            if diffs:
                msg += f"\n     Edge: {diffs[0]}"
        msg += "\""
        return msg

    elif event_type == "struggling":
        phrase = random.choice(STRUGGLE_PHRASES)
        msg = f"🎙️ \"{color}{horse_name}{RESET} {phrase} at {RED}${pnl:,.0f}{RESET}."
        if leader_params:
            diffs = _param_diff_explanation(leader_params, params)
            if diffs:
                msg += f"\n     Problem: {diffs[0]}"
        msg += "\""
        return msg

    elif event_type == "new_entry":
        phrase = random.choice(NEW_ENTRY_PHRASES)
        highlights = []
        if "min_gap_pct" in params:
            highlights.append(f"gap={params['min_gap_pct']}-{params.get('max_gap_pct','?')}%")
        if "confirmation_bars" in params:
            highlights.append(f"bars={params['confirmation_bars']}")
        if "time_stop_minutes" in params:
            highlights.append(f"time_stop={params['time_stop_minutes']}m")
        tag = f" ({', '.join(highlights[:3])})" if highlights else ""
        return f"📢 {color}{horse_name}{RESET} {phrase}{tag}"

    return ""


class HorseRace:
    def __init__(self, total_combos: int, mode: str, num_symbols: int, days: int):
        self.total_combos = total_combos
        self.mode = mode
        self.num_symbols = num_symbols
        self.days = days
        self.leaderboard = []
        self.name_map = {}
        self.prev_ranks = {}
        self.combo_count = 0
        self.start_time = time.time()
        self.last_display = 0
        self.commentary_queue = []
        if total_combos <= 20:
            self.display_interval = 1
        elif total_combos <= 100:
            self.display_interval = max(1, total_combos // 20)
        else:
            self.display_interval = max(1, total_combos // 40)
        self.displayed_header = False
        self.worst_pnl = 0
        self.best_pnl = 0
        self.milestones_hit = set()
        self.leader_streaks = {}
        self.total_tested = 0

    def _config_id(self, params: dict) -> str:
        return hashlib.md5(str(sorted(params.items())).encode()).hexdigest()[:6]

    def _get_name(self, config_id: str) -> str:
        if config_id not in self.name_map:
            idx = len(self.name_map) % len(HORSE_NAMES)
            suffix = len(self.name_map) // len(HORSE_NAMES)
            name = HORSE_NAMES[idx]
            if suffix > 0:
                name += f" {suffix + 1}"
            self.name_map[config_id] = name
        return self.name_map[config_id]

    def _print_header(self):
        print(f"\n{'━' * 70}")
        print(f"  🏇 {BOLD}SWEEP DERBY{RESET} — {self.mode.upper()} Mode")
        print(f"  Track: {self.days}-Day Backtest | Field: {self.total_combos} Configs | {self.num_symbols} Symbols")
        print(f"{'━' * 70}")
        self.displayed_header = True

    def _print_leaderboard(self):
        elapsed = time.time() - self.start_time
        rate = self.combo_count / elapsed if elapsed > 0 else 0
        remaining = (self.total_combos - self.combo_count) / rate if rate > 0 else 0
        pct = self.combo_count / self.total_combos * 100

        eta_m, eta_s = divmod(int(remaining), 60)
        el_m, el_s = divmod(int(elapsed), 60)

        track_width = 35
        progress_fill = int(pct / 100 * track_width)
        progress_bar = f"{'▓' * progress_fill}{'░' * (track_width - progress_fill)}"

        print(f"\n  🏁 Race {self.combo_count}/{self.total_combos}  [{progress_bar}] {pct:.0f}%")
        print(f"  ⏱️  Elapsed: {el_m}m {el_s:02d}s | ETA: {eta_m}m {eta_s:02d}s | Speed: {rate:.1f} configs/min")
        print()

        max_pnl = max(abs(self.best_pnl), abs(self.worst_pnl), 1)
        show_count = min(6, len(self.leaderboard))

        for i in range(show_count):
            entry = self.leaderboard[i]
            cid = entry["id"]
            name = self._get_name(cid)
            pnl = entry["metrics"]["total_pnl_usd"]
            wr = entry["metrics"]["win_rate"] * 100
            trades = entry["metrics"]["total_trades"]
            color = HORSE_COLORS[hash(name) % len(HORSE_COLORS)]
            pos_label = POSITION_LABELS.get(i + 1, f"{i+1}th")

            if pnl >= 0:
                pnl_color = GREEN
            else:
                pnl_color = RED

            bar = _bar(max(0, pnl), max_pnl, 30)

            print(f"  🐎 {color}{name:<16}{RESET} [{pnl_color}${pnl:>8,.0f}{RESET}] "
                  f"{bar}  {pos_label}")

        if len(self.leaderboard) > show_count:
            worst = self.leaderboard[-1]
            worst_name = self._get_name(worst["id"])
            worst_pnl = worst["metrics"]["total_pnl_usd"]
            worst_color = HORSE_COLORS[hash(worst_name) % len(HORSE_COLORS)]
            pnl_color = RED if worst_pnl < 0 else GREEN
            print(f"  🐴 {worst_color}{worst_name:<16}{RESET} [{pnl_color}${worst_pnl:>8,.0f}{RESET}] "
                  f"{'░' * 30}  DNF 💀")

        print()

    def update(self, config: dict, metrics: dict, idx: int):
        self.combo_count = idx + 1
        cid = self._config_id(config)
        pnl = metrics.get("total_pnl_usd", 0)
        score = metrics.get("composite_score", 0)

        if not self.displayed_header:
            self._print_header()

        self.best_pnl = max(self.best_pnl, pnl)
        self.worst_pnl = min(self.worst_pnl, pnl)

        old_leader_id = self.leaderboard[0]["id"] if self.leaderboard else None
        old_leader_params = self.leaderboard[0]["params"] if self.leaderboard else None

        existing_idx = None
        for i, e in enumerate(self.leaderboard):
            if e["id"] == cid:
                existing_idx = i
                break

        if existing_idx is not None:
            self.leaderboard[existing_idx] = {"id": cid, "params": config, "metrics": metrics, "score": score}
        else:
            self.leaderboard.append({"id": cid, "params": config, "metrics": metrics, "score": score})
            if self.combo_count <= 6 or self.combo_count % 50 == 0:
                self.commentary_queue.append(
                    _generate_commentary("new_entry", self._get_name(cid), len(self.leaderboard), pnl, config)
                )

        self.leaderboard.sort(key=lambda x: x["metrics"]["total_pnl_usd"], reverse=True)

        new_rank = next(i + 1 for i, e in enumerate(self.leaderboard) if e["id"] == cid)
        old_rank = self.prev_ranks.get(cid, new_rank)

        if new_rank == 1 and old_leader_id and cid != old_leader_id:
            loser_params = old_leader_params
            self.commentary_queue.append(
                _generate_commentary("new_leader", self._get_name(cid), 1, pnl, config,
                                      loser_params=loser_params)
            )
        elif old_rank > new_rank and new_rank <= 3 and old_rank - new_rank >= 2:
            leader_params = self.leaderboard[0]["params"] if self.leaderboard else None
            self.commentary_queue.append(
                _generate_commentary("position_jump", self._get_name(cid), new_rank, pnl, config,
                                      prev_rank=old_rank, leader_params=leader_params)
            )

        if pnl < -500 and self.combo_count > 10 and random.random() < 0.15:
            leader_params = self.leaderboard[0]["params"] if self.leaderboard else None
            self.commentary_queue.append(
                _generate_commentary("struggling", self._get_name(cid), new_rank, pnl, config,
                                      leader_params=leader_params)
            )

        if new_rank == 1:
            if old_leader_id and cid != old_leader_id:
                for k in list(self.leader_streaks.keys()):
                    if k != cid:
                        self.leader_streaks[k] = 0
            self.leader_streaks[cid] = self.leader_streaks.get(cid, 0) + 1
            if self.leader_streaks[cid] in (25, 50, 100, 200):
                name = self._get_name(cid)
                color = HORSE_COLORS[hash(name) % len(HORSE_COLORS)]
                self.commentary_queue.append(
                    f"🎙️ \"{color}{name}{RESET} has held the lead for {BOLD}{self.leader_streaks[cid]} configs straight{RESET} — "
                    f"this config is {GREEN}locked in{RESET}.\""
                )

        milestone_triggered = False
        pct = self.combo_count / self.total_combos
        for milestone, label in [(0.25, "QUARTER"), (0.50, "HALFWAY"), (0.75, "THREE-QUARTER")]:
            if pct >= milestone and milestone not in self.milestones_hit:
                self.milestones_hit.add(milestone)
                milestone_triggered = True
                leader = self.leaderboard[0] if self.leaderboard else None
                if leader:
                    l_name = self._get_name(leader["id"])
                    l_color = HORSE_COLORS[hash(l_name) % len(HORSE_COLORS)]
                    l_pnl = leader["metrics"]["total_pnl_usd"]
                    l_wr = leader["metrics"]["win_rate"] * 100
                    self.commentary_queue.append(
                        f"\n  {'🔔' * 3}  {BOLD}{label} POLE{RESET}  {'🔔' * 3}\n"
                        f"  📍 Leader: {l_color}{l_name}{RESET} — {GREEN}${l_pnl:,.0f}{RESET} "
                        f"({l_wr:.0f}% WR) after {self.combo_count} configs tested"
                    )

        self.prev_ranks[cid] = new_rank

        should_display = (
            self.combo_count <= 5 or
            self.combo_count % self.display_interval == 0 or
            (new_rank == 1 and cid != old_leader_id) or
            self.combo_count == self.total_combos or
            milestone_triggered
        )

        if should_display:
            if self.commentary_queue:
                print()
                for comment in self.commentary_queue[-3:]:
                    if comment:
                        print(f"  {comment}")
                self.commentary_queue.clear()

            self._print_leaderboard()

        self.leaderboard = self.leaderboard[:20]

    def finish(self):
        elapsed = time.time() - self.start_time
        el_m, el_s = divmod(int(elapsed), 60)

        print(f"\n{'━' * 70}")
        print(f"""
    ╔═══════════════════════════════════════════╗
    ║           🏆  RACE  COMPLETE  🏆          ║
    ╠═══════════════════════════════════════════╣
    ║                                           ║
    ║      .----.     ___                       ║
    ║     / /  \\ \\   |   |  WINNER!             ║
    ║    | |    | |  | 1 |                      ║
    ║    | |    | |  |   |                      ║
    ║   _|_|____|_|__|___|__                    ║
    ║  |________________________|               ║
    ║                                           ║
    ╚═══════════════════════════════════════════╝
""")

        if not self.leaderboard:
            print("  No valid configs finished the race.")
            return

        winner = self.leaderboard[0]
        w_name = self._get_name(winner["id"])
        w_m = winner["metrics"]
        w_p = winner["params"]
        w_color = HORSE_COLORS[hash(w_name) % len(HORSE_COLORS)]

        print(f"  🥇 {BOLD}CHAMPION: {w_color}{w_name}{RESET}")
        print(f"     Profit: {GREEN}${w_m['total_pnl_usd']:,.2f}{RESET}")
        print(f"     Win Rate: {w_m['win_rate']*100:.1f}% | Trades: {w_m['total_trades']}")
        print(f"     Profit Factor: {w_m['profit_factor']:.2f} | Sharpe: {w_m['sharpe']:.2f}")
        print(f"     Max Drawdown: {w_m['max_drawdown_pct']:.1f}%")
        print()

        key_params = ["min_gap_pct", "max_gap_pct", "confirmation_bars", "time_stop_minutes",
                       "hard_stop_pct", "take_profit_pct", "rsi_overbought", "rsi_oversold",
                       "max_trades_per_day", "trailing_stop_pct", "options_leverage", "position_size_pct"]

        print(f"  🔑 {BOLD}WINNING FORMULA:{RESET}")
        for k in key_params:
            if k in w_p:
                info = PARAM_EXPLANATIONS.get(k, (k, "", "", ""))
                name = info[0]
                unit = info[3]
                print(f"     {name:<25} {w_p[k]}{unit}")

        if len(self.leaderboard) > 1:
            runner = self.leaderboard[1]
            r_name = self._get_name(runner["id"])
            r_m = runner["metrics"]
            r_color = HORSE_COLORS[hash(r_name) % len(HORSE_COLORS)]

            print(f"\n  🥈 RUNNER-UP: {r_color}{r_name}{RESET} — ${r_m['total_pnl_usd']:,.2f} "
                  f"(WR: {r_m['win_rate']*100:.1f}%, {r_m['total_trades']} trades)")

            diffs = _param_diff_explanation(w_p, runner["params"])
            if diffs:
                print(f"     What separated them: {diffs[0]}")
                if len(diffs) > 1:
                    print(f"                          {diffs[1]}")

        if len(self.leaderboard) > 2:
            show = self.leaderboard[2]
            s_name = self._get_name(show["id"])
            s_m = show["metrics"]
            s_color = HORSE_COLORS[hash(s_name) % len(HORSE_COLORS)]
            print(f"  🥉 SHOW:      {s_color}{s_name}{RESET} — ${s_m['total_pnl_usd']:,.2f} "
                  f"(WR: {s_m['win_rate']*100:.1f}%, {s_m['total_trades']} trades)")

        print(f"\n  📊 {BOLD}RACE STATS:{RESET}")
        print(f"     Configs tested: {self.combo_count}")
        print(f"     Race time: {el_m}m {el_s:02d}s")
        rate = self.combo_count / elapsed if elapsed > 0 else 0
        print(f"     Speed: {rate:.1f} configs/min")
        profitable = sum(1 for e in self.leaderboard if e["metrics"]["total_pnl_usd"] > 0)
        print(f"     Profitable configs (top 20): {profitable}/{len(self.leaderboard)}")
        print(f"     Best PnL: {GREEN}${self.best_pnl:,.2f}{RESET}")
        print(f"     Worst PnL: {RED}${self.worst_pnl:,.2f}{RESET}")
        print(f"{'━' * 70}\n")


def main():
    parser = argparse.ArgumentParser(description="Ultra Dynamic Sweep Optimizer — 121 Parameters")
    parser.add_argument("--days", type=int, default=60, help="Days of historical data (default: 60)")
    parser.add_argument("--mode", choices=["quick", "full", "focused", "mega", "ultra", "freeroll"], default="quick",
                        help="Sweep mode: quick (10), full (21), focused (10 narrow), mega (28), ultra (131), freeroll (48 loosened)")
    parser.add_argument("--max-combos", type=int, default=MAX_COMBOS, help="Max parameter combinations to test")
    parser.add_argument("--initial-capital", type=float, default=None, help="Starting account balance in USD (default: 29000)")
    args = parser.parse_args()

    global INITIAL_CAPITAL
    if args.initial_capital is not None:
        INITIAL_CAPITAL = args.initial_capital

    if args.mode == "full":
        param_grid = PARAM_GRID_FULL
    elif args.mode == "focused":
        param_grid = PARAM_GRID_FOCUSED
    elif args.mode == "mega":
        param_grid = PARAM_GRID_MEGA
    elif args.mode == "ultra":
        param_grid = PARAM_GRID_ULTRA
    elif args.mode == "freeroll":
        param_grid = PARAM_GRID_FREEROLL
    else:
        param_grid = PARAM_GRID_QUICK

    print("=" * 90)
    print(f"{'ULTRA DYNAMIC SWEEP OPTIMIZER':^90}")
    print(f"{'121-Parameter Trading System Optimization':^90}")
    print("=" * 90)
    print(f"  Mode: {args.mode} | Days: {args.days} | Capital: ${INITIAL_CAPITAL:,.0f}")
    print(f"  Symbols: {len(SYMBOLS)} tickers")
    print(f"  Sweepable Parameters: {len(param_grid)} variables")

    grid_size = 1
    for v in param_grid.values():
        grid_size *= len(v)

    if grid_size > 10**15:
        exp = int(math.log10(grid_size))
        mantissa = grid_size / (10 ** exp)
        print(f"  Total Grid Size: {mantissa:.1f} × 10^{exp} combinations")
    else:
        print(f"  Total Grid Size: {grid_size:,.0f} combinations")

    if args.mode == "ultra":
        print(f"\n  Parameter Categories:")
        categories = {
            "Entry Filters": 14, "HailMary Entry": 9, "OptionsBot Entry": 8,
            "Position Sizing": 9, "Hold Time": 5, "Stop Losses": 5,
            "Take Profit/Trail": 6, "Tiered Exits": 9, "Dynamic Trail ATR": 6,
            "ProfitSniper": 4, "Stop Jitter": 1, "Parabolic Runner": 3,
            "Trade Limits": 7, "Risk Management": 9, "Session Protection": 4,
            "Options Chain": 7, "Timing": 4, "Liquidity": 4,
            "News/Intel": 5, "Portfolio Alloc": 4, "Support/Resist": 8,
        }
        for cat, count in categories.items():
            print(f"    {cat:<22} {count:>3} params")

    print()

    print("PHASE 1: Loading 5-minute intraday data from Alpaca...")
    bars_by_symbol = load_intraday_data(SYMBOLS, args.days)

    if not bars_by_symbol:
        print("ERROR: No data loaded. Check Alpaca credentials.")
        sys.exit(1)

    total_bars = sum(len(b) for b in bars_by_symbol.values())
    print(f"\n  Total: {len(bars_by_symbol)} symbols, {total_bars:,} bars loaded")

    keys = list(param_grid.keys())
    values = list(param_grid.values())

    total_grid = 1
    for v in values:
        total_grid *= len(v)

    max_c = args.max_combos

    if total_grid > max_c:
        if total_grid > 10**15:
            exp = int(math.log10(total_grid))
            mantissa = total_grid / (10 ** exp)
            print(f"\nPHASE 2: Grid {mantissa:.1f}×10^{exp} → random sampling {max_c} combos")
        else:
            print(f"\nPHASE 2: Grid size {total_grid:,} → random sampling {max_c} combinations")
        random.seed(42)
        sampled = []
        seen = set()
        while len(sampled) < max_c:
            combo = tuple(random.choice(v) for v in values)
            combo_key = str(combo)
            if combo_key not in seen:
                seen.add(combo_key)
                sampled.append(combo)
    else:
        print(f"\nPHASE 2: Testing all {total_grid} combinations")
        sampled = list(itertools.product(*values))

    results = []
    start_time = time.time()

    race = HorseRace(len(sampled), args.mode, len(bars_by_symbol), args.days)

    print(f"\nPHASE 3: Running {len(sampled)} sweep simulations...\n")

    for idx, combo in enumerate(sampled):
        config = dict(zip(keys, combo))

        try:
            trades = run_sweep(bars_by_symbol, config)
            metrics = calculate_metrics(trades)
            results.append({
                "params": config,
                "metrics": metrics,
                "sample_trades": [t.to_dict() for t in trades[:5]] if trades else [],
            })
            race.update(config, metrics, idx)
        except Exception as e:
            continue

    elapsed = time.time() - start_time
    race.finish()
    print(f"\n  Sweep completed in {elapsed:.1f}s ({len(results)} valid configs)")

    results.sort(key=lambda x: x["metrics"]["composite_score"], reverse=True)

    top_n = 10
    top_configs = results[:top_n]

    current_config = {}
    for k in keys:
        current_config[k] = param_grid[k][0]
    current_config.update({
        "min_gap_pct": 2.0, "min_hold_minutes": 20, "catastrophic_stop": 75,
        "hard_stop_pct": 20, "direction_lock": True, "max_trades_per_day": 8,
        "confirmation_bars": 7, "options_leverage": 20, "take_profit_pct": 100,
        "quality_gate_mode": "fail_closed", "max_concurrent": 3, "rsi_overbought": 90,
        "rsi_oversold": 15, "require_volume_spike": True, "require_vwap": True,
        "trailing_stop_pct": 15, "trailing_activation_pct": 5, "position_size_pct": 5.0,
        "daily_loss_cap_pct": 5, "stop_after_losses": 2, "max_contracts": 1,
        "drawdown_reduce_pct": 8, "tier1_multiplier": 5.0, "tier1_sell_pct": 33,
        "tier2_multiplier": 8.0,
    })
    current_config = {k: current_config.get(k, param_grid[k][0]) for k in keys}

    try:
        current_trades = run_sweep(bars_by_symbol, current_config)
        current_metrics = calculate_metrics(current_trades)
    except:
        current_metrics = {"composite_score": -999, "total_pnl_usd": 0, "win_rate": 0, "profit_factor": 0, "total_trades": 0}

    output = {
        "sweep_date": datetime.now().isoformat(),
        "mode": args.mode,
        "period_days": args.days,
        "initial_capital": INITIAL_CAPITAL,
        "symbols": SYMBOLS,
        "total_params": len(param_grid),
        "total_grid_size": str(total_grid),
        "total_configs_tested": len(results),
        "elapsed_seconds": round(elapsed, 1),
        "current_config": {
            "params": current_config,
            "metrics": current_metrics,
        },
        "top_configs": [{
            "rank": rank,
            "params": r["params"],
            "metrics": r["metrics"],
            "sample_trades": r.get("sample_trades", []),
        } for rank, r in enumerate(top_configs, 1)],
        "kill_factor_analysis": analyze_kill_factors(results),
    }

    os.makedirs("results", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    capital_tag = f"_{int(INITIAL_CAPITAL/1000)}k" if INITIAL_CAPITAL != 29000 else ""
    output_path = f"results/sweep_{args.mode}_{args.days}d_{len(results)}combos{capital_tag}_{ts}.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n{'=' * 90}")
    print(f"{'SWEEP RESULTS':^90}")
    print(f"{'=' * 90}")

    print(f"\n  CURRENT CONFIG PERFORMANCE:")
    cm = current_metrics
    print(f"    Trades: {cm.get('total_trades', 0)} | WR: {cm.get('win_rate', 0)*100:.1f}% | "
          f"PnL: ${cm.get('total_pnl_usd', 0):,.2f} | PF: {cm.get('profit_factor', 0):.2f} | "
          f"Score: {cm.get('composite_score', -999):.1f}")

    print(f"\n  TOP {top_n} OPTIMIZED CONFIGS:")
    print(f"  {'Rank':<5} {'Trades':>7} {'WR%':>7} {'PnL':>12} {'PF':>7} {'Sharpe':>7} {'MaxDD%':>7} {'Score':>9} {'TM':>4} {'HM':>4} {'OB':>4}")
    print(f"  {'-'*85}")

    for rank_idx, c in enumerate(top_configs, 1):
        m = c["metrics"]
        pf_str = f"{m['profit_factor']:.2f}" if m['profit_factor'] < 100 else "INF"
        print(f"  {rank_idx:<5} {m['total_trades']:>7} {m['win_rate']*100:>6.1f}% "
              f"${m['total_pnl_usd']:>10,.2f} {pf_str:>7} {m['sharpe']:>7.2f} "
              f"{m['max_drawdown_pct']:>6.1f}% {m['composite_score']:>9.1f} "
              f"{m.get('twentymin_trades',0):>4} {m.get('hailmary_trades',0):>4} {m.get('optionsbot_trades',0):>4}")

    if top_configs:
        print(f"\n  BEST CONFIG (#1) PARAMETER SETTINGS:")
        print(f"  {'-'*70}")
        best = top_configs[0]
        changed_count = 0
        for k, v in best["params"].items():
            current_val = current_config.get(k, "N/A")
            changed = " ← CHANGED" if str(v) != str(current_val) else ""
            if changed:
                changed_count += 1
            print(f"    {k:<40} = {str(v):<15} (current: {current_val}){changed}")

        print(f"\n  Parameters changed: {changed_count}/{len(best['params'])}")

        improvement = best["metrics"]["composite_score"] - cm.get("composite_score", -999)
        print(f"  IMPROVEMENT vs CURRENT: Score {'+' if improvement > 0 else ''}{improvement:.1f}")
        pnl_improvement = best["metrics"]["total_pnl_usd"] - cm.get("total_pnl_usd", 0)
        print(f"  PnL improvement: {'+' if pnl_improvement > 0 else ''}${pnl_improvement:,.2f}")

        if best["metrics"].get("exit_breakdown"):
            print(f"\n  EXIT BREAKDOWN (best config):")
            for reason, count in best["metrics"]["exit_breakdown"].items():
                pct = count / best["metrics"]["total_trades"] * 100 if best["metrics"]["total_trades"] > 0 else 0
                print(f"    {reason:<30} {count:>4} ({pct:>5.1f}%)")

    print(f"\n  KILL FACTOR ANALYSIS (ranked by impact — top 30):")
    kfa = output.get("kill_factor_analysis", {})
    for i, (factor, analysis) in enumerate(list(kfa.items())[:30]):
        impact = analysis.get('impact_range', 0)
        bar = '█' * min(int(impact / 50), 40)
        print(f"    #{i+1:<3} {factor:<40} best={str(analysis.get('best_value', '?')):<12} "
              f"impact={impact:>8.1f}  {bar}")

    if len(kfa) > 30:
        print(f"\n    ... and {len(kfa) - 30} more parameters (see JSON output)")

    print(f"\n  Results saved to: {output_path}")
    print(f"  Parameter master list: results/SWEEPABLE_PARAMETERS_MASTER_LIST.md")
    print(f"  {'=' * 90}")


if __name__ == "__main__":
    main()
