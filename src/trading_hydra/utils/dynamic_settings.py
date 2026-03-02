"""
Dynamic Settings Calculator
============================
Automatically adjusts trading settings based on account equity.
Uses sweep-optimized parameters from capital_sweep_dynamic.json when available,
falling back to heuristic tier calculations otherwise.

Sweep-Optimized Tiers (from capital sweep optimizer, $1K-$200K):
- $1,000: 70% BB / 30% HM, 20% position size, 18.9% daily ROI
- $2,000: 70% BB / 30% HM, 100% position size, 19.1% daily ROI
- $3,000: 85% BB / 15% HM, 100% position size, 12.7% daily ROI
- $5,000: 90% BB / 10% HM, 100% position size, 11.2% daily ROI
- $7,500: 70% BB / 30% HM, 100% position size, 31.4% daily ROI
- $10,000: 85% BB / 15% HM, 100% position size, 9.3% daily ROI
- $15,000: 85% BB / 15% HM, 100% position size, 20.4% daily ROI
- $20,000: 90% BB / 10% HM, 75% position size, 8.5% daily ROI
- $25,000: 90% BB / 10% HM, 60% position size, 7.5% daily ROI
- $30,000: 92% BB / 8% HM, 50% position size, 6.5% daily ROI
- $50,000: 93% BB / 7% HM, 40% position size, 5.0% daily ROI
- $75,000: 95% BB / 5% HM, 30% position size, 4.0% daily ROI
- $100,000: 95% BB / 5% HM, 25% position size, 3.5% daily ROI
- $150,000: 97% BB / 3% HM, 20% position size, 3.0% daily ROI
- $200,000: 97% BB / 3% HM, 15% position size, 2.5% daily ROI

Fallback Tiers (when sweep data unavailable):
- Micro ($1k-$5k): Conservative, focus on capital preservation
- Small ($5k-$10k): Moderate, balanced risk/reward
- Medium Small ($10k-$25k): Balanced, growing positions
- Medium ($25k-$50k): Institutional-style risk management
- Large ($50k-$100k): Institutional, reduced position sizing
- Institutional ($100k-$200k): Conservative institutional
- Whale ($200k+): Maximum capital preservation
"""

import os
import json
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from ..core.logging import get_logger

logger = get_logger()

SWEEP_TIERS = [1000, 2000, 3000, 5000, 7500, 10000, 15000, 20000, 25000, 30000, 50000, 75000, 100000, 150000, 200000]
_sweep_cache: Optional[Dict[str, Any]] = None


def _load_sweep_results() -> Optional[Dict[str, Any]]:
    """Load capital sweep results JSON if available."""
    global _sweep_cache
    if _sweep_cache is not None:
        return _sweep_cache

    possible_paths = [
        os.path.join(os.getcwd(), "export", "results", "capital_sweep_dynamic.json"),
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "export", "results", "capital_sweep_dynamic.json"),
        "/home/runner/workspace/export/results/capital_sweep_dynamic.json",
    ]

    for p in possible_paths:
        if os.path.exists(p):
            try:
                with open(p) as f:
                    _sweep_cache = json.load(f)
                logger.info(f"Loaded sweep results from: {p}")
                return _sweep_cache
            except Exception as e:
                logger.warn(f"Failed to load sweep results from {p}: {e}")

    return None


def _select_sweep_tier(equity: float) -> Optional[str]:
    """Select the closest lower sweep tier for the given equity."""
    selected = None
    for t in SWEEP_TIERS:
        if equity >= t:
            selected = str(t)
        else:
            break
    return selected


@dataclass
class DynamicTradingSettings:
    """Calculated trading settings based on account size."""
    equity_pct: float
    max_concurrent_positions: int
    max_notional_usd: float
    ml_rerank_select: int
    stop_loss_pct: float
    take_profit_pct: float
    trailing_stop_pct: float
    trailing_activation_pct: float
    max_trades_per_day: int
    tier_name: str
    expected_daily_profit: float
    expected_weekly_profit: float
    risk_per_stopped_trade: float
    bb_alloc_pct: float = 0.0
    hm_alloc_pct: float = 0.0
    bb_capital: float = 0.0
    hm_capital: float = 0.0
    sweep_source: bool = False


def calculate_dynamic_settings(
    account_equity: float,
    target_daily_profit: Optional[float] = None,
    risk_tolerance: str = "moderate"
) -> DynamicTradingSettings:
    """
    Calculate optimal trading settings based on account equity.
    Uses sweep-optimized data when available, falls back to heuristics.
    """
    sweep_data = _load_sweep_results()
    tier_key = _select_sweep_tier(account_equity) if sweep_data else None

    if sweep_data and tier_key and tier_key in sweep_data.get("tiers", {}):
        return _settings_from_sweep(account_equity, tier_key, sweep_data["tiers"][tier_key])

    return _settings_from_heuristic(account_equity, target_daily_profit, risk_tolerance)


def _settings_from_sweep(equity: float, tier_key: str, tier_data: dict) -> DynamicTradingSettings:
    """Build settings from sweep optimization results."""
    alloc = tier_data["allocation"]
    perf = tier_data["performance"]
    bb_cfg = tier_data["bouncebot_config"]
    eb_cfg = tier_data["exitbot_v2"]

    tier_capital = int(tier_key)
    bb_pct = bb_cfg.get("position_size_pct", 100)
    bb_dollars = alloc["bb_capital"] * (bb_pct / 100)

    if tier_capital <= 3000:
        max_positions, coins, max_trades = 3, 5, 5
    elif tier_capital <= 10000:
        max_positions, coins, max_trades = 5, 8, 8
    elif tier_capital <= 30000:
        max_positions, coins, max_trades = 8, 12, 10
    elif tier_capital <= 75000:
        max_positions, coins, max_trades = 10, 15, 12
    elif tier_capital <= 150000:
        max_positions, coins, max_trades = 12, 18, 15
    else:
        max_positions, coins, max_trades = 15, 20, 18

    settings = DynamicTradingSettings(
        equity_pct=round(min(bb_pct, 50.0), 1),
        max_concurrent_positions=max_positions,
        max_notional_usd=round(bb_dollars, 0),
        ml_rerank_select=coins,
        stop_loss_pct=bb_cfg.get("stop_loss_pct", 3),
        take_profit_pct=bb_cfg.get("take_profit_pct", 3),
        trailing_stop_pct=0.8,
        trailing_activation_pct=1.0,
        max_trades_per_day=max_trades,
        tier_name=f"sweep_{tier_key}",
        expected_daily_profit=round(perf["combined_daily_pnl"], 2),
        expected_weekly_profit=round(perf["combined_daily_pnl"] * 7, 2),
        risk_per_stopped_trade=round(bb_dollars * (bb_cfg.get("stop_loss_pct", 3) / 100), 2),
        bb_alloc_pct=alloc["bb_pct"],
        hm_alloc_pct=alloc["hm_pct"],
        bb_capital=alloc["bb_capital"],
        hm_capital=alloc["hm_capital"],
        sweep_source=True,
    )

    logger.info(
        f"[SWEEP] Settings for ${equity:,.0f} → tier ${tier_key}: "
        f"BB={alloc['bb_pct']}%/${alloc['bb_capital']:,} HM={alloc['hm_pct']}%/${alloc['hm_capital']:,} "
        f"expected=${perf['combined_daily_pnl']:.0f}/day ({perf['daily_roi_pct']:.1f}% ROI)"
    )
    return settings


def _settings_from_heuristic(
    account_equity: float,
    target_daily_profit: Optional[float] = None,
    risk_tolerance: str = "moderate"
) -> DynamicTradingSettings:
    """Fallback heuristic settings when sweep data unavailable."""
    risk_multipliers = {
        "conservative": 0.6,
        "moderate": 1.0,
        "aggressive": 1.5,
        "maximum": 2.0
    }
    risk_mult = risk_multipliers.get(risk_tolerance, 1.0)

    if account_equity < 5000:
        tier = "micro"
        base_equity_pct = 15.0
        max_positions = 3
        coins = 5
        base_daily_return_pct = 0.08
    elif account_equity < 10000:
        tier = "small"
        base_equity_pct = 20.0
        max_positions = 5
        coins = 8
        base_daily_return_pct = 0.10
    elif account_equity < 25000:
        tier = "medium_small"
        base_equity_pct = 25.0
        max_positions = 8
        coins = 12
        base_daily_return_pct = 0.08
    elif account_equity < 50000:
        tier = "medium"
        base_equity_pct = 20.0
        max_positions = 10
        coins = 15
        base_daily_return_pct = 0.06
    elif account_equity < 100000:
        tier = "large"
        base_equity_pct = 15.0
        max_positions = 12
        coins = 18
        base_daily_return_pct = 0.04
    elif account_equity < 200000:
        tier = "institutional"
        base_equity_pct = 10.0
        max_positions = 15
        coins = 20
        base_daily_return_pct = 0.03
    else:
        tier = "whale"
        base_equity_pct = 8.0
        max_positions = 15
        coins = 20
        base_daily_return_pct = 0.025

    equity_pct = min(base_equity_pct * risk_mult, 50.0)

    if target_daily_profit is not None:
        required_daily_return_pct = (target_daily_profit / account_equity) * 100
        if required_daily_return_pct > base_daily_return_pct * risk_mult:
            equity_pct = min(equity_pct * (required_daily_return_pct / (base_daily_return_pct * risk_mult)), 50.0)
            logger.warn(
                f"Target ${target_daily_profit}/day requires {required_daily_return_pct:.2f}% daily return. "
                f"Adjusted equity_pct to {equity_pct:.1f}% (capped at 50%)"
            )

    max_notional = account_equity * (equity_pct / 100)

    stop_loss_pct = 1.5 if risk_mult <= 1.0 else 2.0
    take_profit_pct = 1.5
    trailing_stop_pct = 0.6 if risk_mult <= 1.0 else 0.8
    trailing_activation_pct = 1.5 if risk_mult <= 1.0 else 1.0

    max_trades = min(5 + int(risk_mult * 2), 10)

    expected_daily_return_pct = base_daily_return_pct * risk_mult * (equity_pct / base_equity_pct)
    expected_daily_profit = account_equity * (expected_daily_return_pct / 100)
    expected_weekly_profit = expected_daily_profit * 7

    risk_per_stopped_trade = account_equity * (equity_pct / 100) * (stop_loss_pct / 100)

    settings = DynamicTradingSettings(
        equity_pct=round(equity_pct, 1),
        max_concurrent_positions=max_positions,
        max_notional_usd=round(max_notional, 0),
        ml_rerank_select=coins,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        trailing_stop_pct=trailing_stop_pct,
        trailing_activation_pct=trailing_activation_pct,
        max_trades_per_day=max_trades,
        tier_name=tier,
        expected_daily_profit=round(expected_daily_profit, 2),
        expected_weekly_profit=round(expected_weekly_profit, 2),
        risk_per_stopped_trade=round(risk_per_stopped_trade, 2),
        sweep_source=False,
    )

    logger.info(
        f"[HEURISTIC] Settings for ${account_equity:,.0f} ({tier} tier): "
        f"equity_pct={equity_pct:.1f}%, positions={max_positions}, "
        f"expected=${expected_daily_profit:.0f}/day"
    )
    return settings


def get_settings_for_account(account_equity: float, target_daily: float = 50.0) -> Dict[str, Any]:
    """
    Get settings dictionary suitable for direct config override.
    Uses sweep-optimized data when available.
    """
    settings = calculate_dynamic_settings(
        account_equity=account_equity,
        target_daily_profit=target_daily,
        risk_tolerance="aggressive"
    )

    result = {
        "execution": {
            "equity_pct": settings.equity_pct,
            "max_notional_usd": settings.max_notional_usd,
        },
        "risk": {
            "max_concurrent_positions": settings.max_concurrent_positions,
            "max_trades_per_day": settings.max_trades_per_day,
        },
        "universe": {
            "ml_rerank_select": settings.ml_rerank_select,
        },
        "exits": {
            "stop_loss_pct": settings.stop_loss_pct,
            "take_profit_pct": settings.take_profit_pct,
        },
        "trailing_stop": {
            "value": settings.trailing_stop_pct,
            "activation_profit_pct": settings.trailing_activation_pct,
        },
        "_meta": {
            "tier": settings.tier_name,
            "expected_daily": settings.expected_daily_profit,
            "expected_weekly": settings.expected_weekly_profit,
            "risk_per_stop": settings.risk_per_stopped_trade,
            "sweep_source": settings.sweep_source,
        }
    }

    if settings.sweep_source:
        result["allocation"] = {
            "bb_pct": settings.bb_alloc_pct,
            "hm_pct": settings.hm_alloc_pct,
            "bb_capital": settings.bb_capital,
            "hm_capital": settings.hm_capital,
        }

    return result


def get_sweep_tier_config(equity: float) -> Optional[Dict[str, Any]]:
    """Get the raw sweep tier config for a given equity level."""
    sweep_data = _load_sweep_results()
    if not sweep_data:
        return None

    tier_key = _select_sweep_tier(equity)
    if not tier_key:
        return None

    return sweep_data.get("tiers", {}).get(tier_key)


def print_settings_table(account_equity: float) -> None:
    """Print a formatted table of settings for the account."""
    settings = calculate_dynamic_settings(account_equity, risk_tolerance="aggressive")

    source = "SWEEP-OPTIMIZED" if settings.sweep_source else "HEURISTIC"
    print(f"\n{'=' * 60}")
    print(f"DYNAMIC SETTINGS FOR ${account_equity:,.0f} ACCOUNT [{source}]")
    print(f"{'=' * 60}")
    print(f"Tier:                    {settings.tier_name.upper()}")
    print(f"Position Size:           {settings.equity_pct}% (${settings.max_notional_usd:,.0f}/trade)")
    print(f"Max Concurrent:          {settings.max_concurrent_positions} positions")
    print(f"Coins Traded:            {settings.ml_rerank_select}")
    print(f"Max Trades/Day:          {settings.max_trades_per_day}")
    if settings.sweep_source:
        print(f"BB Allocation:           {settings.bb_alloc_pct}% (${settings.bb_capital:,.0f})")
        print(f"HM Allocation:           {settings.hm_alloc_pct}% (${settings.hm_capital:,.0f})")
    print(f"{'=' * 60}")
    print(f"Expected Daily Profit:   ${settings.expected_daily_profit:,.2f}")
    print(f"Expected Weekly Profit:  ${settings.expected_weekly_profit:,.2f}")
    print(f"Risk per Stopped Trade:  ${settings.risk_per_stopped_trade:,.2f}")
    print(f"{'=' * 60}\n")
