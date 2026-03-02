#!/usr/bin/env python3
"""
=============================================================================
Backtest Runner - CLI for Historical Testing & Auto-Optimization
=============================================================================

Features:
  - Auto-detects ticker type (crypto/stock/ETF)
  - Routes optimized params to correct bot config
  - Stores per-symbol profiles for fine-tuned parameters

Usage:
    # Quick backtest on recent data
    python scripts/run_backtest.py --quick --symbols BTC/USD ETH/USD --days 30
    
    # Full backtest with custom dates
    python scripts/run_backtest.py --symbols BTC/USD --start 2025-01-01 --end 2025-01-28
    
    # Run optimization to find best parameters
    python scripts/run_backtest.py --optimize --symbols BTC/USD --days 60
    
    # Apply best config to bots.yaml (auto-routes to correct bot)
    python scripts/run_backtest.py --optimize --apply --symbols BTC/USD --days 60
    
    # Force apply to specific bot
    python scripts/run_backtest.py --optimize --apply --bot-target cryptobot --symbols BTC/USD --days 60
    
    # Save per-symbol profile only (not bot defaults)
    python scripts/run_backtest.py --optimize --apply --per-symbol --symbols BTC/USD --days 60
"""

import sys
import os
import argparse
import json
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.trading_hydra.backtest import (
    BacktestEngine,
    run_quick_backtest,
    run_quick_optimization
)
from src.trading_hydra.utils.ticker_classifier import (
    classify_ticker,
    classify_symbols,
    get_target_bot,
    get_param_map_for_bot,
    get_optimization_grid_for_type,
    AssetType
)


def print_backtest_summary(result):
    """Print formatted backtest results."""
    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    print(f"Strategy:      {result.strategy}")
    print(f"Symbols:       {', '.join(result.symbols)}")
    print(f"Period:        {result.start_date.strftime('%Y-%m-%d')} to {result.end_date.strftime('%Y-%m-%d')}")
    print("-" * 60)
    print(f"Total P&L:     ${result.total_pnl:,.2f} ({result.total_pnl_pct:+.1f}%)")
    print(f"Win Rate:      {result.win_rate * 100:.1f}%")
    print(f"Profit Factor: {result.profit_factor:.2f}")
    print(f"Sharpe Ratio:  {result.sharpe_ratio:.2f}")
    print(f"Max Drawdown:  {result.max_drawdown_pct:.1f}%")
    print(f"Num Trades:    {result.num_trades}")
    print(f"Avg Trade:     ${result.avg_trade_pnl:.2f}")
    print(f"Avg Winner:    ${result.avg_winner:.2f}")
    print(f"Avg Loser:     ${result.avg_loser:.2f}")
    print("-" * 60)
    expectancy = (result.win_rate * result.avg_winner) - ((1 - result.win_rate) * abs(result.avg_loser))
    daily_pnl = result.total_pnl / max(1, (result.end_date - result.start_date).days)
    print(f"Expectancy:    ${expectancy:,.2f}/trade")
    print(f"Daily PnL:     ${daily_pnl:,.2f}/day")
    print(f"Monthly Proj:  ${daily_pnl * 21:,.2f}")
    print("-" * 60)
    print(f"Config:        {json.dumps(result.config, indent=2)}")
    print("=" * 60)


def print_optimization_summary(result):
    """Print formatted optimization results."""
    print("\n" + "=" * 60)
    print("OPTIMIZATION RESULTS")
    print("=" * 60)
    print(f"Combinations Tested: {len(result.all_results)}")
    print(f"Improvement vs Default: {result.improvement_vs_default:+.1f}%")
    print("-" * 60)
    print("BEST CONFIG:")
    for key, value in result.best_config.items():
        print(f"  {key}: {value}")
    print("-" * 60)
    print("BEST METRICS:")
    for key, value in result.best_metrics.items():
        if isinstance(value, float):
            print(f"  {key}: {value:.2f}")
        else:
            print(f"  {key}: {value}")
    print("-" * 60)
    
    if result.recommended_changes:
        print("RECOMMENDED CHANGES:")
        for change in result.recommended_changes:
            print(f"  {change['parameter']}: {change['current']} -> {change['recommended']} ({change['change_pct']:+.1f}%)")
    
    print("-" * 60)
    print("TOP 5 CONFIGURATIONS:")
    top5 = sorted(result.all_results, key=lambda x: x.get("total_pnl", 0), reverse=True)[:5]
    for i, cfg in enumerate(top5, 1):
        wr = cfg.get('win_rate', 0)
        aw = cfg.get('avg_winner', 0)
        al = abs(cfg.get('avg_loser', 0))
        exp = (wr * aw) - ((1 - wr) * al)
        nt = cfg.get('num_trades', 1)
        daily = cfg.get('total_pnl', 0) / max(nt, 1)
        print(f"  {i}. PnL=${cfg.get('total_pnl', 0):,.2f} Exp=${exp:,.2f}/trade $/Day~${daily:,.2f} PF={cfg.get('profit_factor', 0):.2f} WR={wr*100:.0f}%")
    
    print("-" * 60)
    print("PROFITABILITY RANKING (expectancy × trades/day):")
    ranked = []
    for cfg in result.all_results:
        wr = cfg.get('win_rate', 0)
        aw = cfg.get('avg_winner', 0)
        al = abs(cfg.get('avg_loser', 0))
        exp = (wr * aw) - ((1 - wr) * al)
        nt = cfg.get('num_trades', 1)
        trades_per_day = nt / max(1, 30)
        profit_score = exp * trades_per_day
        ranked.append((cfg, exp, trades_per_day, profit_score))
    ranked.sort(key=lambda x: x[3], reverse=True)
    for i, (cfg, exp, tpd, ps) in enumerate(ranked[:5], 1):
        print(f"  {i}. ProfitScore={ps:,.2f} (Exp=${exp:,.2f} × {tpd:.2f} trades/day) PnL=${cfg.get('total_pnl', 0):,.2f}")
    
    print("=" * 60)


def apply_optimization_to_config(result, symbols: list, dry_run: bool = True, 
                                   bot_target: str = None, per_symbol: bool = False):
    """
    Apply optimized parameters to bots.yaml with smart routing.
    
    Args:
        result: OptimizationResult from backtest
        symbols: List of symbols that were optimized
        dry_run: If True, only show what would change
        bot_target: Force apply to specific bot (None = auto-detect)
        per_symbol: If True, only update symbol_profiles, not bot defaults
    """
    import yaml
    
    config_path = "config/bots.yaml"
    
    # Read current config
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    # Auto-detect target bot if not specified
    if bot_target is None:
        bot_target, _ = get_target_bot(symbols)
    
    # Classify the symbols
    classified = classify_symbols(symbols)
    primary_type = AssetType.CRYPTO if classified[AssetType.CRYPTO] else AssetType.STOCK
    
    print(f"\n{'DRY RUN - ' if dry_run else ''}Optimization Results for {', '.join(symbols)}")
    print(f"  Detected asset type: {primary_type.value}")
    print(f"  Target bot: {bot_target}")
    
    # Get param mapping for the target bot
    param_map = get_param_map_for_bot(bot_target)
    
    changes = []
    
    # Apply to bot defaults (unless per_symbol only)
    if not per_symbol:
        print(f"\n  BOT CONFIG CHANGES ({bot_target}):")
        for param, value in result.best_config.items():
            if param in param_map:
                path = param_map[param]
                
                # Navigate to the config location
                current = config
                for key in path[:-1]:
                    if key not in current:
                        current[key] = {}
                    current = current[key]
                
                old_value = current.get(path[-1])
                if old_value != value:
                    changes.append(("bot", param, old_value, value, path))
                    print(f"    {param}: {old_value} -> {value}")
                    if not dry_run:
                        current[path[-1]] = value
    
    # Apply to symbol_profiles
    if "symbol_profiles" not in config:
        config["symbol_profiles"] = {}
    
    print(f"\n  SYMBOL PROFILE CHANGES:")
    for symbol in symbols:
        # Convert symbol to config key (BTC/USD -> BTC_USD)
        symbol_key = symbol.replace("/", "_").upper()
        
        if symbol_key not in config["symbol_profiles"]:
            config["symbol_profiles"][symbol_key] = {}
        
        symbol_profile = config["symbol_profiles"][symbol_key]
        
        for param, value in result.best_config.items():
            old_value = symbol_profile.get(param)
            if old_value != value:
                changes.append(("symbol", f"{symbol_key}.{param}", old_value, value, None))
                print(f"    {symbol_key}.{param}: {old_value} -> {value}")
                if not dry_run:
                    symbol_profile[param] = value
        
        # Add timestamp
        if not dry_run:
            symbol_profile["last_optimized"] = datetime.now().isoformat()
    
    # Write changes
    if not dry_run and changes:
        with open(config_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        print(f"\n  Applied {len(changes)} changes to {config_path}")
    elif dry_run and changes:
        print(f"\n  Would apply {len(changes)} changes. Use --apply to apply.")
    else:
        print("\n  No changes needed - config already optimal")
    
    return changes


def main():
    parser = argparse.ArgumentParser(
        description="Backtest trading strategies and optimize parameters",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Quick 30-day backtest
    python scripts/run_backtest.py --quick --symbols BTC/USD --days 30
    
    # Full optimization (auto-detects asset type)
    python scripts/run_backtest.py --optimize --symbols BTC/USD ETH/USD --days 60
    
    # Optimize and apply best config (auto-routes to correct bot)
    python scripts/run_backtest.py --optimize --apply --symbols BTC/USD --days 60
    
    # Force apply to specific bot
    python scripts/run_backtest.py --optimize --apply --bot-target twentyminute_bot --symbols AAPL --days 60
    
    # Only update per-symbol profiles (not bot defaults)
    python scripts/run_backtest.py --optimize --apply --per-symbol --symbols BTC/USD --days 60
        """
    )
    
    parser.add_argument("--symbols", nargs="+", default=["BTC/USD"],
                        help="Symbols to backtest (e.g., BTC/USD ETH/USD AAPL SPY)")
    parser.add_argument("--start", type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, default=30,
                        help="Days of history (if --start/--end not provided)")
    parser.add_argument("--quick", action="store_true",
                        help="Run quick backtest with default config")
    parser.add_argument("--optimize", action="store_true",
                        help="Run parameter optimization")
    parser.add_argument("--apply", action="store_true",
                        help="Apply best config to bots.yaml (use with --optimize)")
    parser.add_argument("--bot-target", type=str, default=None,
                        choices=["cryptobot", "twentyminute_bot", "momentum_bot"],
                        help="Force apply to specific bot (default: auto-detect)")
    parser.add_argument("--per-symbol", action="store_true",
                        help="Only update symbol_profiles, not bot defaults")
    parser.add_argument("--capital", type=float, default=10000.0,
                        help="Initial capital for simulation")
    parser.add_argument("--output", type=str,
                        help="Export results to JSON file")
    parser.add_argument("--optimize-for", type=str, default="total_pnl",
                        choices=["sharpe_ratio", "total_pnl", "win_rate", "profit_factor", "expectancy"],
                        help="Metric to optimize for")
    parser.add_argument("--strategy", type=str, default="turtle",
                        choices=["turtle", "whipsaw"],
                        help="Trading strategy to backtest (turtle=breakout, whipsaw=mean-reversion)")
    
    args = parser.parse_args()
    
    # Determine date range
    if args.start and args.end:
        start_date = args.start
        end_date = args.end
    else:
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
    
    # Classify symbols and show detection results
    print("=" * 60)
    print("BACKTEST CONFIGURATION")
    print("=" * 60)
    print(f"Strategy: {args.strategy}")
    print(f"Period: {start_date} to {end_date}")
    print(f"Initial Capital: ${args.capital:,.2f}")
    print()
    print("Symbol Classification:")
    classified = classify_symbols(args.symbols)
    for symbol in args.symbols:
        info = classify_ticker(symbol)
        print(f"  {symbol}: {info.asset_type.value} -> {info.target_bot}")
    
    # Determine primary asset type for optimization grid
    if classified[AssetType.CRYPTO]:
        primary_type = AssetType.CRYPTO
    elif classified[AssetType.ETF]:
        primary_type = AssetType.ETF
    else:
        primary_type = AssetType.STOCK
    
    target_bot, _ = get_target_bot(args.symbols)
    if args.bot_target:
        target_bot = args.bot_target
        print(f"\n  Forced target bot: {target_bot}")
    else:
        print(f"\n  Auto-detected target bot: {target_bot}")
    
    engine = BacktestEngine(initial_capital=args.capital)
    
    if args.optimize:
        print("\n" + "=" * 60)
        print("RUNNING OPTIMIZATION")
        print("=" * 60)
        
        # Get asset-type-specific optimization grid
        param_grid = get_optimization_grid_for_type(primary_type)
        print(f"Using {primary_type.value} optimization grid:")
        for param, values in param_grid.items():
            print(f"  {param}: {values}")
        
        result = engine.optimize(
            symbols=args.symbols,
            start_date=start_date,
            end_date=end_date,
            param_grid=param_grid,
            strategy=args.strategy,
            optimize_for=args.optimize_for
        )
        
        print_optimization_summary(result)
        
        print("\n" + "=" * 60)
        print("PROFITABILITY VERDICT")
        print("=" * 60)
        best_pnl = result.best_metrics.get('total_pnl', 0)
        best_trades = result.best_metrics.get('num_trades', 1)
        best_wr = result.best_metrics.get('win_rate', 0)
        best_aw = result.best_metrics.get('avg_winner', 0)
        best_al = abs(result.best_metrics.get('avg_loser', 0))
        period_days = max(1, args.days)
        best_daily = best_pnl / period_days
        best_exp = (best_wr * best_aw) - ((1 - best_wr) * best_al)
        account_size = 47000.0
        daily_roi_pct = (best_daily / account_size) * 100
        target_daily = 500.0
        target_met = best_daily >= target_daily
        print(f"Best Daily PnL:     ${best_daily:,.2f}/day")
        print(f"Best Expectancy:    ${best_exp:,.2f}/trade")
        print(f"Monthly Projection: ${best_daily * 21:,.2f}")
        print(f"$500/day Target:    {'✅ MET' if target_met else '❌ NOT MET'} (need ${target_daily:,.0f}, got ${best_daily:,.2f})")
        print(f"Daily ROI ($47k):   {daily_roi_pct:.3f}%")
        print(f"Annual ROI (est):   {daily_roi_pct * 252:.1f}%")
        print("=" * 60)
        
        # Apply with smart routing
        apply_optimization_to_config(
            result, 
            symbols=args.symbols,
            dry_run=not args.apply,
            bot_target=args.bot_target,
            per_symbol=args.per_symbol
        )
        
        if not args.apply:
            print("\nUse --apply flag to apply these changes")
        
        if args.output:
            engine.export_optimization(result, args.output)
            print(f"\nResults exported to {args.output}")
            
    elif args.quick:
        print("\nRunning quick backtest...")
        result = run_quick_backtest(symbols=args.symbols, days=args.days)
        print_backtest_summary(result)
        
        if args.output:
            engine.export_results(result, args.output)
            
    else:
        print("\nRunning backtest...")
        result = engine.run_backtest(
            symbols=args.symbols,
            start_date=start_date,
            end_date=end_date,
            strategy=args.strategy
        )
        print_backtest_summary(result)
        
        if args.output:
            engine.export_results(result, args.output)


if __name__ == "__main__":
    main()
