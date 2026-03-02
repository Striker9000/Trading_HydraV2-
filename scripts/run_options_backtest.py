#!/usr/bin/env python3
"""
Options Strategy Backtester CLI
===============================

Backtest options spread strategies using underlying price data.
Simulates P&L via Black-Scholes pricing and delta exposure.

Usage:
    # Backtest bull put spreads on SPY
    python scripts/run_options_backtest.py --symbol SPY --strategy bull_put_spread --days 90
    
    # Backtest iron condors on QQQ with custom DTE
    python scripts/run_options_backtest.py --symbol QQQ --strategy iron_condor --days 180 --dte 45
    
    # Compare all strategies
    python scripts/run_options_backtest.py --symbol SPY --strategy all --days 120

Strategies:
    long_call, long_put, bull_call_spread, bear_put_spread,
    bull_put_spread, bear_call_spread, iron_condor, all
"""

import sys
import os
import argparse
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.trading_hydra.backtest.options_backtest import (
    OptionsBacktester, SpreadType, OptionsBacktestResult
)
from src.trading_hydra.backtest import BacktestEngine


def fetch_price_data(symbol: str, days: int) -> list:
    """Fetch historical price data using BacktestEngine."""
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


def print_result(result: OptionsBacktestResult):
    """Print formatted backtest results."""
    print("\n" + "=" * 60)
    print(f"STRATEGY: {result.strategy.upper()}")
    print("=" * 60)
    print(f"Symbol:        {result.symbols[0]}")
    print(f"Period:        {result.start_date.strftime('%Y-%m-%d') if hasattr(result.start_date, 'strftime') else result.start_date} to {result.end_date.strftime('%Y-%m-%d') if hasattr(result.end_date, 'strftime') else result.end_date}")
    print("-" * 60)
    print(f"Total Trades:  {result.num_trades}")
    print(f"Win Rate:      {result.win_rate * 100:.1f}%")
    print(f"Total P&L:     ${result.total_pnl:,.2f} ({result.total_pnl_pct:+.1f}%)")
    print(f"Avg Trade:     ${result.avg_trade_pnl:,.2f}")
    print(f"Avg Winner:    ${result.avg_winner:,.2f}")
    print(f"Avg Loser:     ${result.avg_loser:,.2f}")
    print(f"Profit Factor: {result.profit_factor:.2f}")
    print(f"Max Drawdown:  {result.max_drawdown_pct:.1f}%")
    print(f"Sharpe Ratio:  {result.sharpe_ratio:.2f}")
    print("-" * 60)
    
    if result.trades:
        print("\nRecent Trades:")
        for trade in result.trades[-5:]:
            entry = trade.entry_time.strftime('%m/%d') if hasattr(trade.entry_time, 'strftime') else str(trade.entry_time)[:10]
            print(f"  {entry}: {trade.spread_type.value} @ ${trade.entry_price:.2f} -> P&L: ${trade.pnl:.2f} ({trade.exit_reason})")


def run_single_strategy(
    symbol: str,
    strategy: SpreadType,
    price_data: list,
    config: dict
) -> OptionsBacktestResult:
    """Run backtest for a single strategy."""
    backtester = OptionsBacktester()
    return backtester.run_backtest(price_data, symbol, strategy, config)


def main():
    parser = argparse.ArgumentParser(description='Options Strategy Backtester')
    parser.add_argument('--symbol', type=str, default='SPY', help='Underlying symbol')
    parser.add_argument('--strategy', type=str, default='bull_put_spread',
                        choices=['long_call', 'long_put', 'bull_call_spread', 'bear_put_spread',
                                'bull_put_spread', 'bear_call_spread', 'iron_condor', 'all'],
                        help='Strategy to backtest')
    parser.add_argument('--days', type=int, default=90, help='Days of historical data')
    parser.add_argument('--dte', type=int, default=30, help='Days to expiration')
    parser.add_argument('--iv', type=float, default=0.25, help='Implied volatility estimate')
    parser.add_argument('--spread-width', type=float, default=2.0, help='Spread width as % of price')
    parser.add_argument('--stop-loss', type=float, default=100.0, help='Stop loss % of max loss')
    parser.add_argument('--take-profit', type=float, default=50.0, help='Take profit % of max profit')
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("OPTIONS STRATEGY BACKTESTER")
    print("=" * 60)
    
    price_data = fetch_price_data(args.symbol, args.days)
    if not price_data:
        return
    
    config = {
        'dte': args.dte,
        'iv': args.iv,
        'spread_width_pct': args.spread_width,
        'stop_loss_pct': args.stop_loss,
        'take_profit_pct': args.take_profit,
        'entry_lookback': 20
    }
    
    if args.strategy == 'all':
        strategies = [
            SpreadType.LONG_CALL,
            SpreadType.LONG_PUT,
            SpreadType.BULL_CALL_SPREAD,
            SpreadType.BEAR_PUT_SPREAD,
            SpreadType.BULL_PUT_SPREAD,
            SpreadType.BEAR_CALL_SPREAD,
            SpreadType.IRON_CONDOR
        ]
        
        results = []
        for strat in strategies:
            print(f"\nRunning {strat.value}...")
            result = run_single_strategy(args.symbol, strat, price_data, config)
            results.append(result)
            print_result(result)
        
        print("\n" + "=" * 60)
        print("STRATEGY COMPARISON")
        print("=" * 60)
        print(f"{'Strategy':<20} {'Trades':<8} {'Win%':<8} {'P&L':<12} {'Sharpe':<8}")
        print("-" * 60)
        
        for r in sorted(results, key=lambda x: x.total_pnl, reverse=True):
            print(f"{r.strategy:<20} {r.num_trades:<8} {r.win_rate*100:<7.1f}% ${r.total_pnl:<10,.0f} {r.sharpe_ratio:<8.2f}")
        
    else:
        strategy_map = {
            'long_call': SpreadType.LONG_CALL,
            'long_put': SpreadType.LONG_PUT,
            'bull_call_spread': SpreadType.BULL_CALL_SPREAD,
            'bear_put_spread': SpreadType.BEAR_PUT_SPREAD,
            'bull_put_spread': SpreadType.BULL_PUT_SPREAD,
            'bear_call_spread': SpreadType.BEAR_CALL_SPREAD,
            'iron_condor': SpreadType.IRON_CONDOR
        }
        
        strategy = strategy_map[args.strategy]
        result = run_single_strategy(args.symbol, strategy, price_data, config)
        print_result(result)
    
    print("\n" + "=" * 60)
    print("BACKTEST COMPLETE")
    print("=" * 60)
    print("\nNote: This is an approximation using Black-Scholes pricing.")
    print("Real options have path-dependent Greeks, IV changes, and liquidity issues.")


if __name__ == '__main__':
    main()
