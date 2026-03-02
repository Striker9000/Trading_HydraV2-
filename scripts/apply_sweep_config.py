#!/usr/bin/env python3
"""
Sweep Config Applicator - Auto-applies optimized parameters from capital sweep
===============================================================================
Reads current account equity from Alpaca, selects the closest capital tier from
the sweep results ($1K-$200K), and writes optimized parameters into config/generated/tier_override.yaml.

The config loader (core/config.py) will merge this override at load time.

Usage:
    python scripts/apply_sweep_config.py              # Auto-detect equity from Alpaca
    python scripts/apply_sweep_config.py --equity 5000 # Force specific equity
    python scripts/apply_sweep_config.py --dry-run     # Preview without writing
"""

import os
import sys
import json
import yaml
import argparse
import requests
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
EXPORT_DIR = SCRIPT_DIR.parent
WORKSPACE_DIR = EXPORT_DIR.parent
CONFIG_DIR = WORKSPACE_DIR / "config"
GENERATED_DIR = CONFIG_DIR / "generated"
SWEEP_RESULTS = EXPORT_DIR / "results" / "capital_sweep_dynamic.json"

TIER_THRESHOLDS = [1000, 2000, 3000, 5000, 7500, 10000, 15000, 20000, 25000, 30000, 50000, 75000, 100000, 150000, 200000]


def get_alpaca_equity() -> float:
    """Fetch current account equity from Alpaca API."""
    api_key = os.environ.get("ALPACA_KEY") or os.environ.get("APCA_API_KEY_ID")
    api_secret = os.environ.get("ALPACA_SECRET") or os.environ.get("APCA_API_SECRET_KEY")
    is_paper = os.environ.get("ALPACA_PAPER", "true").lower() != "false"

    if not api_key or not api_secret:
        print("[WARN] No Alpaca credentials found. Using default equity $5000.")
        return 5000.0

    base_url = "https://paper-api.alpaca.markets" if is_paper else "https://api.alpaca.markets"

    try:
        resp = requests.get(
            f"{base_url}/v2/account",
            headers={
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": api_secret,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        equity = float(data.get("equity", 5000))
        print(f"[ALPACA] Account equity: ${equity:,.2f} (paper={is_paper})")
        return equity
    except Exception as e:
        print(f"[WARN] Alpaca API error: {e}. Using default equity $5000.")
        return 5000.0


def select_tier(equity: float) -> str:
    """Select the closest lower capital tier for the given equity."""
    selected = str(TIER_THRESHOLDS[0])
    for t in TIER_THRESHOLDS:
        if equity >= t:
            selected = str(t)
        else:
            break
    return selected


def load_sweep_results() -> dict:
    """Load the capital sweep dynamic results JSON."""
    if not SWEEP_RESULTS.exists():
        print(f"[ERROR] Sweep results not found: {SWEEP_RESULTS}")
        sys.exit(1)

    with open(SWEEP_RESULTS) as f:
        return json.load(f)


def build_override_yaml(tier_key: str, tier_data: dict, equity: float) -> dict:
    """Build the override YAML structure from sweep tier data."""
    alloc = tier_data["allocation"]
    bb_cfg = tier_data["bouncebot_config"]
    hm_cfg = tier_data["hailmary_config"]
    eb_cfg = tier_data["exitbot_v2"]
    sn_cfg = tier_data["sniper"]
    perf = tier_data["performance"]

    bb_capital = alloc["bb_capital"]
    hm_capital = alloc["hm_capital"]
    total_capital = int(tier_key)

    bb_position_pct = bb_cfg.get("position_size_pct", 100)
    bb_dollars_per_trade = bb_capital * (bb_position_pct / 100)

    override = {
        "_meta": {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "equity_detected": round(equity, 2),
            "tier_selected": tier_key,
            "tier_capital": total_capital,
            "allocation_bb_pct": alloc["bb_pct"],
            "allocation_hm_pct": alloc["hm_pct"],
            "bb_capital": bb_capital,
            "hm_capital": hm_capital,
            "expected_daily_pnl": perf["combined_daily_pnl"],
            "expected_daily_roi_pct": perf["daily_roi_pct"],
            "expected_max_dd_pct": perf["combined_dd_pct"],
            "expected_sharpe": perf["combined_sharpe"],
        },

        "bouncebot": {
            "entry": {
                "drawdown_threshold_pct": bb_cfg["drawdown_threshold_pct"],
                "rsi_oversold": bb_cfg["rsi_oversold"],
            },
            "exits": {
                "stop_loss_pct": bb_cfg["stop_loss_pct"],
                "take_profit_pct": bb_cfg["take_profit_pct"],
            },
            "risk": {
                "position_size_pct": bb_position_pct,
                "min_position_usd": bb_cfg.get("min_position_usd", 50),
            },
            "profit_sniper": {
                "velocity_window": sn_cfg["velocity_window"],
                "velocity_reversal_pct": sn_cfg["velocity_reversal_pct"],
                "ratchet_arm_pct": sn_cfg["ratchet_arm_pct"],
                "ratchet_base_distance_pct": sn_cfg["ratchet_base_distance_pct"],
                "ratchet_tighten_per_pct": sn_cfg["ratchet_tighten_per_pct"],
                "ratchet_min_distance_pct": sn_cfg["ratchet_min_distance_pct"],
                "exhaustion_bars": sn_cfg["exhaustion_bars"],
                "exhaustion_min_profit_pct": sn_cfg["exhaustion_min_profit_pct"],
            },
            "exitbot": {
                "tp1_pct": eb_cfg["tp1_pct"],
                "tp1_exit_pct": eb_cfg["tp1_exit_pct"],
                "tp2_pct": eb_cfg["tp2_pct"],
                "tp2_exit_pct": eb_cfg["tp2_exit_pct"],
                "tp3_pct": eb_cfg["tp3_pct"],
                "hard_stop_pct": eb_cfg["hard_stop_pct"],
                "reversal_sense_drop_pct": eb_cfg["reversal_sense_drop_pct"],
                "reversal_sense_min_gain_pct": eb_cfg["reversal_sense_min_gain_pct"],
            },
        },

        "exitbot": {
            "enabled": True,
            "v2_intelligence_enabled": True,
            "v2_forward_projection": {
                "enabled": True,
                "bars": eb_cfg.get("forward_proj_bars", 5),
                "exit_threshold": eb_cfg.get("forward_proj_exit_threshold", -1.0),
            },
            "v2_time_decay": {
                "enabled": True,
                "start_pct": eb_cfg.get("time_decay_start_pct", 0.7),
                "urgency": eb_cfg.get("time_decay_urgency", 1.0),
            },
            "v2_dd_reduction": {
                "enabled": True,
                "threshold_pct": eb_cfg.get("dd_reduction_threshold_pct", 5.0),
                "exit_pct": eb_cfg.get("dd_reduction_exit_pct", 0.25),
            },
        },

        "profit_sniper": {
            "stocks": {
                "enabled": True,
                "velocity_window": sn_cfg["velocity_window"],
                "velocity_reversal_pct": sn_cfg["velocity_reversal_pct"],
                "ratchet_arm_pct": sn_cfg["ratchet_arm_pct"],
                "ratchet_base_distance_pct": sn_cfg["ratchet_base_distance_pct"],
                "ratchet_tighten_per_pct": sn_cfg["ratchet_tighten_per_pct"],
                "ratchet_min_distance_pct": sn_cfg["ratchet_min_distance_pct"],
                "exhaustion_bars": sn_cfg["exhaustion_bars"],
                "exhaustion_min_profit_pct": sn_cfg["exhaustion_min_profit_pct"],
            },
            "crypto": {
                "enabled": True,
                "velocity_window": sn_cfg["velocity_window"],
                "velocity_reversal_pct": sn_cfg["velocity_reversal_pct"],
                "ratchet_arm_pct": sn_cfg["ratchet_arm_pct"],
                "ratchet_base_distance_pct": sn_cfg["ratchet_base_distance_pct"],
                "ratchet_tighten_per_pct": sn_cfg["ratchet_tighten_per_pct"],
                "ratchet_min_distance_pct": sn_cfg["ratchet_min_distance_pct"],
                "exhaustion_bars": sn_cfg["exhaustion_bars"],
                "exhaustion_min_profit_pct": sn_cfg["exhaustion_min_profit_pct"],
            },
        },

        "optionsbot": {
            "hail_mary": {
                "enabled": True,
                "max_risk_per_trade_usd": hm_cfg["max_risk_per_trade_usd"],
                "max_premium": hm_cfg["max_premium"],
                "strike_otm_pct": hm_cfg["strike_otm_pct"],
                "dte_max": hm_cfg["dte_max"],
                "min_stock_change_pct": hm_cfg["min_stock_change_pct"],
                "profit_target_multiplier": hm_cfg["profit_target_mult"],
                "tier1_multiplier": hm_cfg["tier1_multiplier"],
                "tier1_sell_pct": hm_cfg["tier1_sell_pct"],
                "tier2_multiplier": hm_cfg["tier2_multiplier"],
                "tier2_sell_pct": hm_cfg["tier2_sell_pct"],
                "tiered_exits": True,
            },
        },
    }

    return override


def write_override(override: dict, dry_run: bool = False) -> str:
    """Write override YAML to config/generated/tier_override.yaml."""
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    output_path = GENERATED_DIR / "tier_override.yaml"

    yaml_header = (
        "# =============================================================================\n"
        "# AUTO-GENERATED TIER OVERRIDE - DO NOT EDIT MANUALLY\n"
        "# =============================================================================\n"
        f"# Generated: {override['_meta']['generated_at']}\n"
        f"# Account Equity: ${override['_meta']['equity_detected']:,.2f}\n"
        f"# Selected Tier: ${override['_meta']['tier_selected']}\n"
        f"# Allocation: {override['_meta']['allocation_bb_pct']}% BounceBot / "
        f"{override['_meta']['allocation_hm_pct']}% HailMary\n"
        f"# Expected: ${override['_meta']['expected_daily_pnl']:.2f}/day "
        f"({override['_meta']['expected_daily_roi_pct']:.1f}% ROI), "
        f"DD={override['_meta']['expected_max_dd_pct']:.1f}%, "
        f"Sharpe={override['_meta']['expected_sharpe']:.2f}\n"
        "# =============================================================================\n\n"
    )

    if dry_run:
        print("\n[DRY RUN] Would write to:", output_path)
        print(yaml_header)
        print(yaml.dump(override, default_flow_style=False, sort_keys=False))
        return str(output_path)

    with open(output_path, "w") as f:
        f.write(yaml_header)
        yaml.dump(override, f, default_flow_style=False, sort_keys=False)

    print(f"[OK] Tier override written to: {output_path}")
    return str(output_path)


def print_summary(tier_key: str, tier_data: dict, equity: float):
    """Print a human-readable summary of selected config."""
    alloc = tier_data["allocation"]
    perf = tier_data["performance"]
    bb_cfg = tier_data["bouncebot_config"]
    hm_cfg = tier_data["hailmary_config"]
    eb_cfg = tier_data["exitbot_v2"]

    print(f"\n{'='*70}")
    print(f"  DYNAMIC OPTIMIZATION APPLIED")
    print(f"{'='*70}")
    print(f"  Account Equity:  ${equity:>10,.2f}")
    print(f"  Selected Tier:   ${tier_key:>10}")
    print(f"  Allocation:      {alloc['bb_pct']}% BB (${alloc['bb_capital']:,}) / "
          f"{alloc['hm_pct']}% HM (${alloc['hm_capital']:,})")
    print(f"{'─'*70}")
    print(f"  Expected Daily:  ${perf['combined_daily_pnl']:>10,.2f} "
          f"({perf['daily_roi_pct']:.1f}% ROI)")
    print(f"  Expected Monthly:${perf['monthly_pnl']:>10,.2f} "
          f"({perf['monthly_roi_pct']:.1f}% ROI)")
    print(f"  Max Drawdown:    {perf['combined_dd_pct']:>10.1f}%")
    print(f"  Sharpe Ratio:    {perf['combined_sharpe']:>10.2f}")
    print(f"{'─'*70}")
    print(f"  BB Position Size:{bb_cfg['position_size_pct']:>8}%  "
          f"  SL={bb_cfg['stop_loss_pct']}%  TP={bb_cfg['take_profit_pct']}%")
    print(f"  HM Max Risk:     ${hm_cfg['max_risk_per_trade_usd']:>7}  "
          f"  OTM={hm_cfg['strike_otm_pct']}%  DTE≤{hm_cfg['dte_max']}")
    print(f"  ExitBot TP1/2/3: {eb_cfg['tp1_pct']}% / {eb_cfg['tp2_pct']}% / {eb_cfg['tp3_pct']}%  "
          f"  HardStop={eb_cfg['hard_stop_pct']}%")
    print(f"  Forward Proj:    {eb_cfg.get('forward_proj_bars', 5)} bars  "
          f"  TimeDecay={eb_cfg.get('time_decay_urgency', 1.0)}  "
          f"  DD Reduce={eb_cfg.get('dd_reduction_threshold_pct', 5.0)}%")
    print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="Apply sweep-optimized config")
    parser.add_argument("--equity", type=float, default=None, help="Override equity (skip Alpaca)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--tier", type=str, default=None, help="Force specific tier (e.g. 5000)")
    args = parser.parse_args()

    if args.equity is not None:
        equity = args.equity
        print(f"[OVERRIDE] Using equity: ${equity:,.2f}")
    else:
        equity = get_alpaca_equity()

    sweep = load_sweep_results()
    tiers = sweep.get("tiers", {})

    if args.tier:
        tier_key = args.tier
    else:
        tier_key = select_tier(equity)

    if tier_key not in tiers:
        print(f"[ERROR] Tier {tier_key} not found in sweep results. Available: {list(tiers.keys())}")
        sys.exit(1)

    tier_data = tiers[tier_key]
    print_summary(tier_key, tier_data, equity)

    override = build_override_yaml(tier_key, tier_data, equity)
    output_path = write_override(override, dry_run=args.dry_run)

    print(f"[DONE] Config applicator complete. Override: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
