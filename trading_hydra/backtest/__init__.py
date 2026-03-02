"""
Backtest Engine - Historical Testing & Auto-Optimization

This module provides backtesting capabilities for testing trading strategies
against historical data and optimizing configuration parameters.

Usage:
    from trading_hydra.backtest import BacktestEngine, run_quick_backtest, run_quick_optimization
    
    # Quick backtest
    result = run_quick_backtest(symbols=["BTC/USD"], days=30)
    print(f"P&L: ${result.total_pnl:.2f}, Win Rate: {result.win_rate*100:.1f}%")
    
    # Full optimization
    engine = BacktestEngine()
    opt_result = engine.optimize(
        symbols=["BTC/USD", "ETH/USD"],
        start_date="2025-01-01",
        end_date="2025-01-28",
        param_grid={
            "entry_lookback": [120, 240, 480],
            "stop_loss_pct": [1.0, 1.5, 2.0]
        }
    )
    print(f"Best config: {opt_result.best_config}")
"""

from .backtest_engine import (
    BacktestEngine,
    BacktestResult,
    OptimizationResult,
    Trade,
    run_quick_backtest,
    run_quick_optimization
)

from .sniper_backtest import (
    SniperBacktester,
    SniperBacktestResult,
    SniperOptimizationResult,
    SniperTrade,
    optimize_crypto,
    optimize_stocks,
    optimize_options,
    optimize_all,
)

__all__ = [
    "BacktestEngine",
    "BacktestResult", 
    "OptimizationResult",
    "Trade",
    "run_quick_backtest",
    "run_quick_optimization",
    "SniperBacktester",
    "SniperBacktestResult",
    "SniperOptimizationResult",
    "SniperTrade",
    "optimize_crypto",
    "optimize_stocks",
    "optimize_options",
    "optimize_all",
]
