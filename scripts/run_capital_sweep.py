#!/usr/bin/env python3
"""
Capital Sweep Optimizer v3: $1K-$200K with Dynamic Everything
- Position sizing: 5%-100% (scales down at institutional tiers)
- BounceBot/HailMary allocation splits (capital-proportional)
- Dynamic ExitBot v2 per capital tier
- Fixed: HM risk scales with allocation, drawdown is dollar-based vs total capital
- Extended: 15 tiers from $1K to $200K with institutional scaling
"""

import sys, os, json, time, random, math
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_dynamic_optimizer as opt

DAYS = 30
NUM_COMBOS = 250
CAPITAL_TIERS = [1000, 2000, 3000, 5000, 7500, 10000, 15000, 20000, 25000, 30000, 50000, 75000, 100000, 150000, 200000]
HM_ALLOC_PCTS = [0, 5, 10, 15, 20, 25, 30]

EXPANDED_POSITION_SIZES = [5, 8, 10, 15, 20, 25, 30, 40, 50, 75, 100]

EXPANDED_BB_PARAMS = {
    "drawdown_threshold_pct": [2.0, 3.0, 3.5, 5.0, 7.0, 10.0],
    "rsi_oversold": [15, 20, 25, 30, 35, 40],
    "take_profit_pct": [1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0],
    "stop_loss_pct": [1, 2, 3, 5, 8],
    "position_size_pct": EXPANDED_POSITION_SIZES,
}

EXPANDED_HM_PARAMS = {
    "profit_target_mult": [3.0, 5.0, 8.0, 10.0, 15.0, 20.0],
    "dte_max": [3, 5, 7],
    "strike_otm_pct": [1.0, 2.0, 3.0, 5.0, 7.0],
    "min_stock_change_pct": [0.3, 0.5, 1.0],
    "max_risk_pct_of_alloc": [5, 10, 15, 20, 25, 30],
    "max_premium": [0.50, 1.0, 2.0, 3.0],
    "tier1_multiplier": [2.0, 3.0, 5.0],
    "tier1_sell_pct": [25, 50, 75],
    "tier2_multiplier": [5.0, 8.0, 10.0, 15.0],
    "tier2_sell_pct": [25, 50],
}

EXPANDED_EXITBOT = {
    "tp1_pct": [0.5, 1.0, 2.0, 3.0, 4.0, 6.0],
    "tp1_exit_pct": [0.25, 0.33, 0.50],
    "tp2_pct": [3.0, 5.0, 8.0, 10.0, 15.0],
    "tp2_exit_pct": [0.33, 0.50, 0.75],
    "tp3_pct": [8.0, 12.0, 15.0, 20.0, 30.0],
    "hard_stop_pct": [5.0, 8.0, 12.0, 15.0, 20.0],
    "reversal_sense_drop_pct": [1.0, 1.5, 2.0, 3.0, 5.0],
    "reversal_sense_min_gain_pct": [0.3, 0.5, 1.0, 2.0],
    "forward_proj_bars": [3, 5, 8],
    "forward_proj_exit_threshold": [-0.5, -1.0, -2.0],
    "time_decay_start_pct": [0.5, 0.7, 0.85],
    "time_decay_urgency": [0.5, 1.0, 2.0, 3.0],
    "dd_reduction_threshold_pct": [3.0, 5.0, 8.0, 10.0],
    "dd_reduction_exit_pct": [0.25, 0.50],
}

EXPANDED_SNIPER = {
    "velocity_window": [3, 5, 7, 10],
    "velocity_reversal_pct": [0.2, 0.3, 0.5, 0.8],
    "ratchet_arm_pct": [0.3, 0.5, 1.0, 2.0],
    "ratchet_base_distance_pct": [0.15, 0.25, 0.4, 0.6],
    "ratchet_tighten_per_pct": [0.02, 0.03, 0.05, 0.08],
    "ratchet_min_distance_pct": [0.05, 0.08, 0.12, 0.2],
    "exhaustion_bars": [2, 3, 4, 5],
    "exhaustion_min_profit_pct": [0.2, 0.3, 0.5, 1.0],
}

SYSTEM_PARAMS = {
    "min_position_usd": [50, 100, 200, 300, 500, 750, 1000],
}


def sample_combo(param_dict):
    return {k: random.choice(v) for k, v in param_dict.items()}


def run_bb_sim(data_cache, config, capital):
    opt.ACCOUNT_SIZE = capital
    all_trades = []
    for sym in opt.BOT_CONFIGS["bouncebot"]["symbols"]:
        bars = data_cache.get(sym, [])
        if bars:
            trades = opt.simulate_strategy("bouncebot", sym, bars, config, capital, "NORMAL")
            all_trades.extend(trades)
    return all_trades


def run_hm_sim(data_cache, config, capital):
    opt.ACCOUNT_SIZE = capital
    all_trades = []
    for sym in opt.BOT_CONFIGS["hailmary"]["symbols"]:
        bars = data_cache.get(sym, [])
        if bars:
            trades = opt.simulate_hailmary(sym, bars, config, capital, "NORMAL")
            all_trades.extend(trades)
    return all_trades


def compute_equity_curve_dd(trades, capital):
    if not trades:
        return 0.0, []
    sorted_trades = sorted(trades, key=lambda t: t.entry_time or datetime.min)
    equity = capital
    peak = capital
    max_dd_dollar = 0
    curve = []
    for t in sorted_trades:
        d = t.to_dict()
        equity += d.get("pnl", 0)
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd_dollar:
            max_dd_dollar = dd
        curve.append(equity)
    max_dd_pct = (max_dd_dollar / capital * 100) if capital > 0 else 0
    return max_dd_pct, curve


def optimize_bb_for_capital(data_cache, capital, num_combos):
    best_score = -999
    best_config = None
    best_metrics = None

    for _ in range(num_combos):
        combo = {}
        combo.update(sample_combo(EXPANDED_BB_PARAMS))
        combo.update(sample_combo(EXPANDED_EXITBOT))
        combo.update(sample_combo(EXPANDED_SNIPER))
        combo.update(sample_combo(SYSTEM_PARAMS))

        if combo["min_position_usd"] > capital * 0.5:
            combo["min_position_usd"] = max(50, capital // 10)

        trades = run_bb_sim(data_cache, combo, capital)
        metrics = opt.calculate_metrics(trades, DAYS)
        score = opt.calculate_profitability_score(metrics, DAYS)

        if score > best_score:
            best_score = score
            best_config = combo.copy()
            best_metrics = metrics

    return best_config, best_metrics, best_score


def optimize_hm_for_capital(data_cache, capital, num_combos):
    best_score = -999
    best_config = None
    best_metrics = None

    for _ in range(num_combos):
        combo = sample_combo(EXPANDED_HM_PARAMS)

        risk_pct = combo.pop("max_risk_pct_of_alloc", 15)
        combo["max_risk_per_trade_usd"] = max(10, int(capital * risk_pct / 100))

        trades = run_hm_sim(data_cache, combo, capital)
        metrics = opt.calculate_metrics(trades, DAYS)
        score = opt.calculate_profitability_score(metrics, DAYS)

        if score > best_score:
            best_score = score
            best_config = combo.copy()
            best_config["max_risk_pct_of_alloc"] = risk_pct
            best_metrics = metrics

    return best_config, best_metrics, best_score


def main():
    random.seed(42)

    print(f"\n{'='*120}")
    print(f"  CAPITAL SWEEP v2: $1K-$15K | Dynamic Sizing (5-100%) | BB+HM Allocation | ExitBot v2")
    print(f"  Fixes: HM risk scales with allocation capital, drawdown is dollar-based vs total capital")
    print(f"  Combos: {NUM_COMBOS}/bot/tier | Days: {DAYS}")
    print(f"{'='*120}")

    data_cache = opt.try_load_disk_cache(DAYS)
    if data_cache is None:
        data_cache = opt.cache_all_data(DAYS, force_refresh=False)
    print(f"Symbols cached: {len(data_cache)}")

    all_tier_results = {}

    for capital in CAPITAL_TIERS:
        print(f"\n{'─'*120}")
        print(f"  CAPITAL TIER: ${capital:,}")
        print(f"{'─'*120}")

        print(f"  Optimizing BounceBot ({NUM_COMBOS} combos)...")
        bb_cfg, bb_metrics, bb_score = optimize_bb_for_capital(data_cache, capital, NUM_COMBOS)
        bb_daily = bb_metrics["total_pnl"] / DAYS
        print(f"    BB best: ${bb_daily:.2f}/day, WR={bb_metrics['win_rate']*100:.1f}%, Sharpe={bb_metrics.get('sharpe_ratio',0):.2f}, PosSize={bb_cfg.get('position_size_pct','?')}%")

        print(f"  Finding optimal allocation split...")
        best_alloc = {"score": -999}

        for hm_pct in HM_ALLOC_PCTS:
            hm_capital = max(int(capital * hm_pct / 100), 0)
            bb_capital = capital - hm_capital

            bb_trades_alloc = run_bb_sim(data_cache, bb_cfg, bb_capital) if bb_capital >= 100 else []

            hm_trades_alloc = []
            hm_cfg_alloc = None
            if hm_capital >= 50:
                hm_cfg_alloc_best, _, _ = optimize_hm_for_capital(data_cache, hm_capital, max(50, NUM_COMBOS // 3))
                if hm_cfg_alloc_best:
                    hm_cfg_alloc = hm_cfg_alloc_best
                    hm_trades_alloc = run_hm_sim(data_cache, hm_cfg_alloc, hm_capital)

            bb_m = opt.calculate_metrics(bb_trades_alloc, DAYS)
            hm_m = opt.calculate_metrics(hm_trades_alloc, DAYS)

            bb_dd_pct, _ = compute_equity_curve_dd(bb_trades_alloc, bb_capital) if bb_capital > 0 else (0, [])
            hm_dd_pct, _ = compute_equity_curve_dd(hm_trades_alloc, hm_capital) if hm_capital > 0 else (0, [])

            bb_dd_dollar = bb_capital * bb_dd_pct / 100
            hm_dd_dollar = hm_capital * hm_dd_pct / 100
            combined_dd_dollar = bb_dd_dollar + hm_dd_dollar
            combined_dd_pct = combined_dd_dollar / capital * 100 if capital > 0 else 0

            combined_pnl = bb_m["total_pnl"] + hm_m["total_pnl"]
            combined_daily = combined_pnl / DAYS

            survival_if_hm_blows = ((bb_capital + bb_m["total_pnl"]) / capital * 100) if capital > 0 else 0

            all_trades_combined = bb_trades_alloc + hm_trades_alloc
            daily_returns = {}
            for t in all_trades_combined:
                d = t.to_dict()
                day_key = str(d.get("entry_time", ""))[:10]
                daily_returns[day_key] = daily_returns.get(day_key, 0) + d.get("pnl", 0)
            returns = list(daily_returns.values())
            if len(returns) > 1:
                avg_r = sum(returns) / len(returns)
                std_r = (sum((r - avg_r) ** 2 for r in returns) / len(returns)) ** 0.5
                sharpe = (avg_r / std_r * (252 ** 0.5)) if std_r > 0 else 0
            else:
                sharpe = 0

            daily_roi = combined_daily / capital * 100 if capital > 0 else 0
            dd_penalty = max(0, combined_dd_pct - 25) * 10
            survival_bonus = max(0, min(survival_if_hm_blows, 100) - 70) * 2
            score = daily_roi * 50 + sharpe * 5 - dd_penalty + survival_bonus

            if combined_dd_pct > 50:
                score -= (combined_dd_pct - 50) * 20
            if combined_dd_pct > 80:
                score = -abs(score)

            if score > best_alloc["score"]:
                best_alloc = {
                    "score": score,
                    "hm_pct": hm_pct,
                    "bb_pct": 100 - hm_pct,
                    "hm_capital": hm_capital,
                    "bb_capital": bb_capital,
                    "combined_daily": combined_daily,
                    "combined_dd_pct": combined_dd_pct,
                    "bb_dd_pct": bb_dd_pct,
                    "hm_dd_pct": hm_dd_pct,
                    "combined_sharpe": sharpe,
                    "survival_pct": survival_if_hm_blows,
                    "bb_daily": bb_m["total_pnl"] / DAYS,
                    "hm_daily": hm_m["total_pnl"] / DAYS,
                    "bb_trades": bb_m["num_trades"],
                    "hm_trades": hm_m["num_trades"],
                    "bb_wr": bb_m["win_rate"] * 100,
                    "hm_wr": hm_m["win_rate"] * 100,
                    "hm_cfg": hm_cfg_alloc,
                    "hm_max_risk": hm_cfg_alloc.get("max_risk_per_trade_usd", 0) if hm_cfg_alloc else 0,
                    "hm_risk_pct_of_alloc": hm_cfg_alloc.get("max_risk_pct_of_alloc", 0) if hm_cfg_alloc else 0,
                }

        ba = best_alloc
        print(f"    Optimal: {ba['bb_pct']}% BB (${ba['bb_capital']:,}) / {ba['hm_pct']}% HM (${ba['hm_capital']:,})")
        print(f"    Combined: ${ba['combined_daily']:.2f}/day, DD={ba['combined_dd_pct']:.1f}%, Sharpe={ba['combined_sharpe']:.2f}, Survival={ba['survival_pct']:.1f}%")
        if ba.get("hm_cfg"):
            print(f"    HM MaxRisk: ${ba['hm_max_risk']} ({ba['hm_risk_pct_of_alloc']}% of HM alloc)")

        all_tier_results[capital] = {
            "allocation": {
                "bb_pct": ba["bb_pct"],
                "hm_pct": ba["hm_pct"],
                "bb_capital": ba["bb_capital"],
                "hm_capital": ba["hm_capital"],
            },
            "performance": {
                "combined_daily_pnl": round(ba["combined_daily"], 2),
                "bb_daily_pnl": round(ba["bb_daily"], 2),
                "hm_daily_pnl": round(ba["hm_daily"], 2),
                "combined_dd_pct": round(ba["combined_dd_pct"], 1),
                "bb_dd_pct": round(ba.get("bb_dd_pct", 0), 1),
                "hm_dd_pct": round(ba.get("hm_dd_pct", 0), 1),
                "combined_sharpe": round(ba["combined_sharpe"], 2),
                "survival_if_hm_blows": round(ba["survival_pct"], 1),
                "bb_trades": ba["bb_trades"],
                "hm_trades": ba["hm_trades"],
                "bb_win_rate": round(ba["bb_wr"], 1),
                "hm_win_rate": round(ba["hm_wr"], 1),
                "monthly_pnl": round(ba["combined_daily"] * 30, 2),
                "monthly_roi_pct": round(ba["combined_daily"] * 30 / capital * 100, 1),
                "daily_roi_pct": round(ba["combined_daily"] / capital * 100, 2),
            },
            "bouncebot_config": bb_cfg,
            "hailmary_config": ba.get("hm_cfg", {}),
            "exitbot_v2": {k: bb_cfg.get(k) for k in EXPANDED_EXITBOT if k in bb_cfg},
            "sniper": {k: bb_cfg.get(k) for k in EXPANDED_SNIPER if k in bb_cfg},
        }

    # Print summary table
    print(f"\n{'='*150}")
    print(f"  CAPITAL SWEEP v2 SUMMARY: OPTIMAL BB+HM ALLOCATION PER TIER")
    print(f"{'='*150}")
    print(f"{'Capital':>8s} | {'BB%':>4s} {'HM%':>4s} | {'BB$':>7s} {'HM$':>7s} | {'BB$/d':>7s} {'HM$/d':>7s} {'Tot$/d':>7s} {'ROI%/d':>7s} | {'DD%':>6s} {'Sharpe':>7s} {'Surv%':>6s} | {'Mo PnL':>10s} {'MoROI%':>7s} | {'PosSize':>8s} {'HMRisk$':>8s} {'HMRisk%':>8s}")
    print(f"-"*150)

    for capital in CAPITAL_TIERS:
        r = all_tier_results[capital]
        a = r["allocation"]
        p = r["performance"]
        bb_ps = r["bouncebot_config"].get("position_size_pct", "?")
        hm_mr = r.get("hailmary_config", {}).get("max_risk_per_trade_usd", "?")
        hm_rp = r.get("hailmary_config", {}).get("max_risk_pct_of_alloc", "?")
        print(f"${capital:>7,} | {a['bb_pct']:>3d}% {a['hm_pct']:>3d}% | ${a['bb_capital']:>6,} ${a['hm_capital']:>6,} | ${p['bb_daily_pnl']:>6.2f} ${p['hm_daily_pnl']:>6.2f} ${p['combined_daily_pnl']:>6.2f} {p['daily_roi_pct']:>6.2f}% | {p['combined_dd_pct']:>5.1f}% {p['combined_sharpe']:>7.2f} {p['survival_if_hm_blows']:>5.1f}% | ${p['monthly_pnl']:>9,.2f} {p['monthly_roi_pct']:>6.1f}% | {bb_ps:>7}% ${str(hm_mr):>7} {str(hm_rp):>7}%")

    # ExitBot v2 settings per tier
    print(f"\n{'='*140}")
    print(f"  EXITBOT v2 DYNAMIC SETTINGS PER CAPITAL TIER")
    print(f"{'='*140}")
    eb_keys = ["tp1_pct", "tp2_pct", "tp3_pct", "hard_stop_pct", "forward_proj_bars", "time_decay_urgency", "dd_reduction_threshold_pct"]
    header = f"{'Capital':>8s} | " + " | ".join(f"{k:>12s}" for k in eb_keys)
    print(header)
    print("-" * 140)
    for capital in CAPITAL_TIERS:
        eb = all_tier_results[capital]["exitbot_v2"]
        vals = " | ".join(f"{str(eb.get(k, '-')):>12s}" for k in eb_keys)
        print(f"${capital:>7,} | {vals}")

    # Position sizing comparison
    print(f"\n{'='*110}")
    print(f"  POSITION SIZING & RISK PER CAPITAL TIER")
    print(f"{'='*110}")
    print(f"{'Capital':>8s} | {'BB PosSize%':>12s} {'BB $/trade':>11s} | {'HM Risk$':>9s} {'HM Risk%':>9s} {'HM$/alloc':>10s} | {'StopLoss%':>10s} {'TP%':>6s}")
    print("-" * 110)
    for capital in CAPITAL_TIERS:
        r = all_tier_results[capital]
        a = r["allocation"]
        bb_ps = r["bouncebot_config"].get("position_size_pct", 0)
        bb_dollar = a["bb_capital"] * bb_ps / 100
        hm_mr = r.get("hailmary_config", {}).get("max_risk_per_trade_usd", 0)
        hm_rp = r.get("hailmary_config", {}).get("max_risk_pct_of_alloc", 0)
        hm_vs_alloc = f"${hm_mr}/{a['hm_capital']}" if a["hm_capital"] > 0 else "N/A"
        sl = r["bouncebot_config"].get("stop_loss_pct", 0)
        tp = r["bouncebot_config"].get("take_profit_pct", 0)
        print(f"${capital:>7,} | {bb_ps:>11}% ${bb_dollar:>10,.0f} | ${hm_mr:>8} {hm_rp:>8}% {hm_vs_alloc:>10} | {sl:>9}% {tp:>5}%")

    # Save
    output = {
        "version": "v2_capital_proportional",
        "sweep_params": {
            "capital_tiers": CAPITAL_TIERS,
            "hm_alloc_range": HM_ALLOC_PCTS,
            "position_size_range": EXPANDED_POSITION_SIZES,
            "combos_per_tier": NUM_COMBOS,
            "days": DAYS,
            "hm_risk_scales_with_allocation": True,
        },
        "tiers": {}
    }
    for capital in CAPITAL_TIERS:
        output["tiers"][str(capital)] = all_tier_results[capital]

    outpath = "export/results/capital_sweep_dynamic.json"
    with open(outpath, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nSaved to {outpath}")


if __name__ == "__main__":
    main()
