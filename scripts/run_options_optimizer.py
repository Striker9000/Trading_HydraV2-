#!/usr/bin/env python3
"""
Options Strategy Optimizer
==========================

Sweeps configuration parameters to find optimal settings for options strategies.
Tests combinations of:
- Greeks (delta targets for different leg types)
- IV (implied volatility regimes and adjustments)
- Liquidity (spread width, volume filters)
- Risk management (stop loss, take profit, DTE)

Usage:
    # Quick optimization with profitability focus (default)
    python scripts/run_options_optimizer.py --symbol SPY --days 90
    
    # Full sweep with all parameters
    python scripts/run_options_optimizer.py --symbol SPY --days 180 --full-sweep
    
    # Specific strategy optimization
    python scripts/run_options_optimizer.py --symbol QQQ --strategy iron_condor --days 120
    
    # Optimize for maximum profit (NEW - default)
    python scripts/run_options_optimizer.py --symbol SPY --days 90 --optimize-for profit
    
    # Optimize for balanced metrics (original behavior)
    python scripts/run_options_optimizer.py --symbol SPY --days 90 --optimize-for balanced
    
    # Optimize for risk-adjusted returns (Sharpe focus)
    python scripts/run_options_optimizer.py --symbol SPY --days 90 --optimize-for sharpe
    
    # Output best config to file
    python scripts/run_options_optimizer.py --symbol SPY --days 90 --output best_config.yaml
"""

import sys
import os
import argparse
import itertools
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Dict, List, Any, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

OPTIONS_TRANSACTION_COSTS = {
    "commission_per_contract": 1.30,
    "spread_cost_pct": 0.5,
}

from src.trading_hydra.backtest.options_backtest import (
    OptionsBacktester, SpreadType, OptionsBacktestResult
)
from src.trading_hydra.backtest import BacktestEngine


@dataclass
class OptimizationResult:
    """Result from parameter optimization."""
    strategy: str
    symbol: str
    config: Dict[str, Any]
    win_rate: float
    total_pnl: float
    profit_factor: float
    sharpe_ratio: float
    max_drawdown: float
    num_trades: int
    score: float  # Composite optimization score


# Parameter ranges for sweeping
GREEK_PARAMS = {
    # Delta targets for different strategies
    "delta_target": [0.20, 0.25, 0.30, 0.35, 0.40],
    "short_delta": [0.15, 0.20, 0.25, 0.30],  # For credit spreads
    "long_delta": [0.05, 0.10, 0.15],  # For protection legs
}

IV_PARAMS = {
    # Implied volatility settings
    "iv": [0.15, 0.20, 0.25, 0.30, 0.35, 0.40],
    "iv_adjustment_factor": [0.8, 1.0, 1.2],  # Scale for high/low IV
}

LIQUIDITY_PARAMS = {
    # Spread width and volume filters
    "spread_width_pct": [1.0, 2.0, 3.0, 5.0],
    "min_volume": [100, 500, 1000],
    "max_bid_ask_spread": [0.05, 0.10, 0.15],
}

RISK_PARAMS = {
    # Risk management
    "dte": [14, 21, 30, 45, 60],
    "stop_loss_pct": [50.0, 75.0, 100.0, 150.0],
    "take_profit_pct": [25.0, 50.0, 75.0],
}

ENTRY_PARAMS = {
    # Entry criteria
    "entry_lookback": [10, 15, 20, 30],
}


def fetch_price_data(symbol: str, days: int) -> list:
    """Fetch historical price data."""
    engine = BacktestEngine()
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    print(f"Fetching {days} days of price data for {symbol}...")
    
    bars = engine.load_historical_data(
        symbol,
        start_date.strftime('%Y-%m-%d'),
        end_date.strftime('%Y-%m-%d'),
        timeframe="1Hour"
    )
    
    if not bars:
        print(f"Error: No price data available for {symbol}")
        return []
    
    print(f"Loaded {len(bars)} bars")
    return bars


def calculate_optimization_score(result: OptionsBacktestResult, mode: str = "profit") -> float:
    """
    Calculate composite optimization score.
    
    Modes:
    - profit (default): Prioritize profitability
      - Total PnL (normalized): 40%
      - Profit factor: 25%
      - Expectancy: 20%
      - Max drawdown (inverted): 15%
    
    - balanced: Balance all metrics
      - Win rate: 30%
      - Profit factor: 25%
      - Sharpe ratio: 25%
      - Max drawdown (inverted): 20%
    
    - sharpe: Prioritize risk-adjusted returns
      - Sharpe ratio: 50%
      - Profit factor: 25%
      - Max drawdown (inverted): 25%
    """
    if result.num_trades < 3:
        return 0.0
    
    # Normalize profit factor (cap at 5)
    pf_score = min(result.profit_factor, 5.0) / 5.0 if result.profit_factor != float('inf') else 1.0
    
    # Invert drawdown (lower is better)
    dd_score = 1.0 - min(result.max_drawdown_pct / 50.0, 1.0)
    
    if mode == "profit":
        pnl_score = min(abs(result.total_pnl) / 10000.0, 1.0)
        if result.total_pnl < 0:
            pnl_score = 0.0
        
        avg_winner = result.avg_winner if result.avg_winner > 0 else 0.0
        avg_loser = result.avg_loser if result.avg_loser > 0 else 0.0
        
        if avg_winner > 0 or avg_loser > 0:
            expectancy = (result.win_rate * avg_winner) - ((1 - result.win_rate) * avg_loser)
            expectancy_score = min(max(expectancy / 1000.0, 0.0), 1.0)
        else:
            expectancy_score = 0.0
        
        score = (
            0.45 * pnl_score +
            0.20 * pf_score +
            0.25 * expectancy_score +
            0.10 * dd_score
        )
        
        if result.total_pnl > 5000:
            score *= 1.15
    
    elif mode == "sharpe":
        # Sharpe-focused scoring
        # Cap Sharpe at 3 for scoring
        sharpe_score = min(max(result.sharpe_ratio, 0), 3.0) / 3.0
        
        score = (
            0.50 * sharpe_score +
            0.25 * pf_score +
            0.25 * dd_score
        )
    
    else:  # balanced
        # Balanced scoring (original)
        win_score = result.win_rate  # 0-1
        sharpe_score = min(max(result.sharpe_ratio, 0), 3.0) / 3.0
        
        score = (
            0.30 * win_score +
            0.25 * pf_score +
            0.25 * sharpe_score +
            0.20 * dd_score
        )
    
    return score


def run_single_backtest(
    symbol: str,
    strategy: SpreadType,
    price_data: list,
    config: Dict[str, Any],
    mode: str = "profit"
) -> OptimizationResult:
    """Run a single backtest with given config."""
    backtester = OptionsBacktester()
    result = backtester.run_backtest(price_data, symbol, strategy, config)
    
    score = calculate_optimization_score(result, mode=mode)
    
    return OptimizationResult(
        strategy=strategy.value,
        symbol=symbol,
        config=config,
        win_rate=result.win_rate,
        total_pnl=result.total_pnl,
        profit_factor=result.profit_factor,
        sharpe_ratio=result.sharpe_ratio,
        max_drawdown=result.max_drawdown_pct,
        num_trades=result.num_trades,
        score=score
    )


def generate_quick_param_grid() -> List[Dict[str, Any]]:
    """Generate quick parameter grid (fewer combinations)."""
    configs = []
    
    for dte in [21, 30, 45]:
        for iv in [0.20, 0.25, 0.30]:
            for spread_width in [2.0, 3.0]:
                for stop_loss in [75.0, 100.0]:
                    for take_profit in [50.0, 75.0]:
                        configs.append({
                            "dte": dte,
                            "iv": iv,
                            "spread_width_pct": spread_width,
                            "stop_loss_pct": stop_loss,
                            "take_profit_pct": take_profit,
                            "entry_lookback": 20,
                            "delta_target": 0.30,
                        })
    
    return configs


def generate_aggressive_param_grid() -> List[Dict[str, Any]]:
    """Generate aggressive parameter grid for maximum profit (wider ranges)."""
    configs = []
    for dte in [7, 14, 21, 30, 45]:
        for iv in [0.20, 0.25, 0.30, 0.35, 0.40]:
            for spread_width in [1.0, 2.0, 3.0, 5.0]:
                for stop_loss in [50.0, 75.0, 100.0, 150.0, 200.0]:
                    for take_profit in [50.0, 75.0, 100.0, 150.0]:
                        for delta in [0.25, 0.30, 0.35, 0.40, 0.45]:
                            configs.append({
                                "dte": dte,
                                "iv": iv,
                                "spread_width_pct": spread_width,
                                "stop_loss_pct": stop_loss,
                                "take_profit_pct": take_profit,
                                "entry_lookback": 20,
                                "delta_target": delta,
                            })
    return configs


def generate_full_param_grid() -> List[Dict[str, Any]]:
    """Generate full parameter grid (comprehensive sweep)."""
    configs = []
    
    # Generate all combinations
    for dte in RISK_PARAMS["dte"]:
        for iv in IV_PARAMS["iv"]:
            for spread_width in LIQUIDITY_PARAMS["spread_width_pct"]:
                for stop_loss in RISK_PARAMS["stop_loss_pct"]:
                    for take_profit in RISK_PARAMS["take_profit_pct"]:
                        for delta in GREEK_PARAMS["delta_target"]:
                            for lookback in ENTRY_PARAMS["entry_lookback"]:
                                configs.append({
                                    "dte": dte,
                                    "iv": iv,
                                    "spread_width_pct": spread_width,
                                    "stop_loss_pct": stop_loss,
                                    "take_profit_pct": take_profit,
                                    "entry_lookback": lookback,
                                    "delta_target": delta,
                                })
    
    return configs


def generate_greek_sweep_grid() -> List[Dict[str, Any]]:
    """Generate grid focused on Greek parameters."""
    configs = []
    
    base_config = {
        "dte": 30,
        "iv": 0.25,
        "spread_width_pct": 2.0,
        "stop_loss_pct": 100.0,
        "take_profit_pct": 50.0,
        "entry_lookback": 20,
    }
    
    # Sweep delta targets
    for delta in GREEK_PARAMS["delta_target"]:
        for short_delta in GREEK_PARAMS["short_delta"]:
            for long_delta in GREEK_PARAMS["long_delta"]:
                config = base_config.copy()
                config["delta_target"] = delta
                config["short_delta"] = short_delta
                config["long_delta"] = long_delta
                configs.append(config)
    
    return configs


def generate_iv_sweep_grid() -> List[Dict[str, Any]]:
    """Generate grid focused on IV parameters."""
    configs = []
    
    base_config = {
        "dte": 30,
        "spread_width_pct": 2.0,
        "stop_loss_pct": 100.0,
        "take_profit_pct": 50.0,
        "entry_lookback": 20,
        "delta_target": 0.30,
    }
    
    # Sweep IV settings
    for iv in IV_PARAMS["iv"]:
        for iv_adj in IV_PARAMS["iv_adjustment_factor"]:
            config = base_config.copy()
            config["iv"] = iv
            config["iv_adjustment_factor"] = iv_adj
            configs.append(config)
    
    return configs


def generate_liquidity_sweep_grid() -> List[Dict[str, Any]]:
    """Generate grid focused on liquidity parameters."""
    configs = []
    
    base_config = {
        "dte": 30,
        "iv": 0.25,
        "stop_loss_pct": 100.0,
        "take_profit_pct": 50.0,
        "entry_lookback": 20,
        "delta_target": 0.30,
    }
    
    # Sweep liquidity settings
    for spread_width in LIQUIDITY_PARAMS["spread_width_pct"]:
        for min_vol in LIQUIDITY_PARAMS["min_volume"]:
            for max_spread in LIQUIDITY_PARAMS["max_bid_ask_spread"]:
                config = base_config.copy()
                config["spread_width_pct"] = spread_width
                config["min_volume"] = min_vol
                config["max_bid_ask_spread"] = max_spread
                configs.append(config)
    
    return configs


def run_optimization(
    symbol: str,
    strategy: SpreadType,
    price_data: list,
    param_grid: List[Dict[str, Any]],
    top_n: int = 10,
    mode: str = "profit"
) -> List[OptimizationResult]:
    """Run optimization across parameter grid."""
    results = []
    total = len(param_grid)
    
    print(f"\nRunning {total} parameter combinations...")
    
    for i, config in enumerate(param_grid):
        if (i + 1) % 50 == 0 or i == 0:
            print(f"  Progress: {i + 1}/{total} ({(i + 1) / total * 100:.1f}%)")
        
        result = run_single_backtest(symbol, strategy, price_data, config, mode=mode)
        results.append(result)
    
    # Sort by score descending
    results.sort(key=lambda x: x.score, reverse=True)
    
    return results[:top_n]


def print_results(results: List[OptimizationResult], strategy: str):
    """Print optimization results with profitability focus."""
    print("\n" + "=" * 80)
    print(f"TOP RESULTS FOR {strategy.upper()} — PROFITABILITY RANKED")
    print("=" * 80)
    
    for i, r in enumerate(results):
        avg_winner = r.profit_factor * r.total_pnl / r.num_trades if r.num_trades > 0 else 0.0
        avg_loser = r.total_pnl / r.num_trades if r.num_trades > 0 else 0.0
        expectancy = (r.win_rate * avg_winner) - ((1 - r.win_rate) * abs(avg_loser))
        pnl_per_trade = r.total_pnl / max(r.num_trades, 1)
        daily_estimate = r.total_pnl / max(r.num_trades, 1)
        monthly_proj = daily_estimate * 21
        
        print(f"\n#{i + 1} Score: {r.score:.3f}")
        print(f"  💰 Total P&L:   ${r.total_pnl:,.2f}")
        print(f"  📈 Expectancy:  ${expectancy:,.2f}/trade")
        print(f"  📊 $/Trade:     ${pnl_per_trade:,.2f}  |  Est $/Day: ${daily_estimate:,.2f}  |  Monthly Proj: ${monthly_proj:,.2f}")
        print(f"  Win Rate: {r.win_rate * 100:.1f}%  |  Trades: {r.num_trades}  |  Profit Factor: {r.profit_factor:.2f}")
        print(f"  Sharpe: {r.sharpe_ratio:.2f}  |  Max DD: {r.max_drawdown:.1f}%")
        print(f"  Config: DTE={r.config['dte']}, IV={r.config['iv']}, "
              f"Width={r.config['spread_width_pct']}%, Delta={r.config['delta_target']}")
        print(f"          SL={r.config['stop_loss_pct']}%, TP={r.config['take_profit_pct']}%")


def save_best_config(result: OptimizationResult, output_path: str):
    """Save best config to YAML file."""
    import yaml
    
    config = {
        "optimized_strategy": result.strategy,
        "symbol": result.symbol,
        "optimization_score": round(result.score, 3),
        "metrics": {
            "win_rate": round(result.win_rate, 3),
            "total_pnl": round(result.total_pnl, 2),
            "profit_factor": round(result.profit_factor, 2),
            "sharpe_ratio": round(result.sharpe_ratio, 2),
            "max_drawdown_pct": round(result.max_drawdown, 1),
            "num_trades": result.num_trades
        },
        "parameters": result.config
    }
    
    with open(output_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False)
    
    print(f"\nBest config saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description='Options Strategy Optimizer')
    parser.add_argument('--symbol', type=str, default='SPY', help='Underlying symbol')
    parser.add_argument('--strategy', type=str, default='iron_condor',
                        choices=['long_call', 'long_put', 'bull_call_spread', 'bear_put_spread',
                                'bull_put_spread', 'bear_call_spread', 'iron_condor', 'all'],
                        help='Strategy to optimize')
    parser.add_argument('--days', type=int, default=90, help='Days of historical data')
    parser.add_argument('--optimize-for', type=str, default='profit',
                        choices=['profit', 'balanced', 'sharpe'],
                        help='Optimization objective (profit: max profitability, balanced: balance all metrics, sharpe: risk-adjusted returns)')
    parser.add_argument('--aggressive', action='store_true', help='Use aggressive parameter grid for maximum profit')
    parser.add_argument('--full-sweep', action='store_true', help='Run comprehensive parameter sweep')
    parser.add_argument('--sweep-greeks', action='store_true', help='Focus on Greek parameters')
    parser.add_argument('--sweep-iv', action='store_true', help='Focus on IV parameters')
    parser.add_argument('--sweep-liquidity', action='store_true', help='Focus on liquidity parameters')
    parser.add_argument('--top', type=int, default=10, help='Number of top results to show')
    parser.add_argument('--output', type=str, help='Output file for best config (YAML)')
    
    args = parser.parse_args()
    
    print("=" * 80)
    print("OPTIONS STRATEGY OPTIMIZER")
    print("=" * 80)
    print(f"Symbol: {args.symbol}")
    print(f"Strategy: {args.strategy}")
    print(f"Days: {args.days}")
    print(f"Optimization Mode: {args.optimize_for.upper()}")
    
    # Fetch price data
    price_data = fetch_price_data(args.symbol, args.days)
    if not price_data:
        return
    
    # Generate parameter grid
    if args.aggressive:
        param_grid = generate_aggressive_param_grid()
        print(f"Aggressive parameter sweep ({len(param_grid)} combinations)")
    elif args.sweep_greeks:
        param_grid = generate_greek_sweep_grid()
        print(f"Sweeping Greek parameters ({len(param_grid)} combinations)")
    elif args.sweep_iv:
        param_grid = generate_iv_sweep_grid()
        print(f"Sweeping IV parameters ({len(param_grid)} combinations)")
    elif args.sweep_liquidity:
        param_grid = generate_liquidity_sweep_grid()
        print(f"Sweeping liquidity parameters ({len(param_grid)} combinations)")
    elif args.full_sweep:
        param_grid = generate_full_param_grid()
        print(f"Full parameter sweep ({len(param_grid)} combinations)")
    else:
        param_grid = generate_quick_param_grid()
        print(f"Quick optimization ({len(param_grid)} combinations)")
    
    # Map strategy string to enum
    strategy_map = {
        'long_call': SpreadType.LONG_CALL,
        'long_put': SpreadType.LONG_PUT,
        'bull_call_spread': SpreadType.BULL_CALL_SPREAD,
        'bear_put_spread': SpreadType.BEAR_PUT_SPREAD,
        'bull_put_spread': SpreadType.BULL_PUT_SPREAD,
        'bear_call_spread': SpreadType.BEAR_CALL_SPREAD,
        'iron_condor': SpreadType.IRON_CONDOR
    }
    
    if args.strategy == 'all':
        # Run for all strategies
        all_results = {}
        for strat_name, strat_enum in strategy_map.items():
            print(f"\n{'=' * 40}")
            print(f"Optimizing {strat_name.upper()}")
            print('=' * 40)
            results = run_optimization(args.symbol, strat_enum, price_data, param_grid, args.top, mode=args.optimize_for)
            all_results[strat_name] = results
            print_results(results[:3], strat_name)  # Show top 3 for each
        
        # Find overall best
        print("\n" + "=" * 80)
        print("STRATEGY COMPARISON - BEST CONFIG FOR EACH")
        print("=" * 80)
        best_per_strategy = []
        for strat, results in all_results.items():
            if results:
                best_per_strategy.append(results[0])
        
        best_per_strategy.sort(key=lambda x: x.score, reverse=True)
        
        for r in best_per_strategy:
            print(f"{r.strategy:<20} Score: {r.score:.3f}  Win: {r.win_rate*100:.0f}%  "
                  f"PF: {r.profit_factor:.1f}  P&L: ${r.total_pnl:,.0f}  Trades: {r.num_trades}")
        
        if args.output and best_per_strategy:
            save_best_config(best_per_strategy[0], args.output)
    else:
        # Run for single strategy
        strategy = strategy_map[args.strategy]
        results = run_optimization(args.symbol, strategy, price_data, param_grid, args.top, mode=args.optimize_for)
        print_results(results, args.strategy)
        
        if args.output and results:
            save_best_config(results[0], args.output)
    
    print("\n" + "=" * 80)
    print("OPTIMIZATION COMPLETE")
    print("=" * 80)


if __name__ == '__main__':
    main()
