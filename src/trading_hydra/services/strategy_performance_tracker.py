"""
Strategy Performance Tracker
============================

Tracks per-ticker strategy performance to learn which strategies work best
for each underlying. Provides intelligent strategy recommendations based on
historical win rates, P&L, and Sharpe ratios.

Key Features:
- Per-ticker, per-strategy performance metrics
- Rolling performance windows (recent trades weighted more)
- Automatic best-strategy recommendations
- Persisted in SQLite state database
"""

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict

from ..core.state import get_state, set_state, get_db_connection
from ..core.logging import get_logger


@dataclass
class StrategyMetrics:
    """Performance metrics for a single ticker-strategy combination."""
    ticker: str
    strategy: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    max_win: float = 0.0
    max_loss: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    last_trade_date: Optional[str] = None
    recent_trades: int = 0  # Trades in last 30 days
    recent_win_rate: float = 0.0  # Win rate in last 30 days
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'StrategyMetrics':
        return cls(**data)


@dataclass
class TradeRecord:
    """Individual trade record for tracking."""
    ticker: str
    strategy: str
    entry_date: str
    exit_date: Optional[str]
    pnl: float
    pnl_pct: float
    is_winner: bool
    hold_time_minutes: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TradeRecord':
        return cls(**data)


class StrategyPerformanceTracker:
    """
    Tracks and learns which strategies work best for each ticker.
    
    Uses SQLite state for persistence across restarts.
    Provides intelligent best-strategy recommendations.
    """
    
    # Minimum trades before considering a strategy "proven" for a ticker
    MIN_TRADES_FOR_RECOMMENDATION = 3
    
    # Recent window for weighting performance (days)
    RECENT_WINDOW_DAYS = 30
    
    # Minimum win rate to recommend a strategy
    MIN_WIN_RATE = 0.45
    
    # Score weights for strategy ranking
    WEIGHT_WIN_RATE = 0.35
    WEIGHT_PROFIT_FACTOR = 0.25
    WEIGHT_RECENT_WIN_RATE = 0.25
    WEIGHT_SHARPE = 0.15
    
    def __init__(self):
        self._logger = get_logger()
        self._metrics_cache: Dict[str, Dict[str, StrategyMetrics]] = {}
        self._trades_cache: List[TradeRecord] = []
        self._load_from_state()
    
    def _load_from_state(self) -> None:
        """Load cached metrics from SQLite state."""
        try:
            # Load metrics
            metrics_data = get_state("strategy_tracker.metrics")
            if metrics_data:
                for ticker, strategies in metrics_data.items():
                    self._metrics_cache[ticker] = {}
                    for strategy, data in strategies.items():
                        self._metrics_cache[ticker][strategy] = StrategyMetrics.from_dict(data)
            
            # Load recent trades
            trades_data = get_state("strategy_tracker.trades")
            if trades_data:
                self._trades_cache = [TradeRecord.from_dict(t) for t in trades_data[-1000:]]
            
            self._logger.log("strategy_tracker_loaded", {
                "tickers": len(self._metrics_cache),
                "trades": len(self._trades_cache)
            })
            
        except Exception as e:
            self._logger.error(f"Failed to load strategy tracker state: {e}")
            self._metrics_cache = {}
            self._trades_cache = []
    
    def _save_to_state(self) -> None:
        """Persist metrics to SQLite state."""
        try:
            # Save metrics
            metrics_data = {}
            for ticker, strategies in self._metrics_cache.items():
                metrics_data[ticker] = {}
                for strategy, metrics in strategies.items():
                    metrics_data[ticker][strategy] = metrics.to_dict()
            
            set_state("strategy_tracker.metrics", metrics_data)
            
            # Save trades (keep last 1000)
            trades_data = [t.to_dict() for t in self._trades_cache[-1000:]]
            set_state("strategy_tracker.trades", trades_data)
            
        except Exception as e:
            self._logger.error(f"Failed to save strategy tracker state: {e}")
    
    def record_trade(
        self,
        ticker: str,
        strategy: str,
        pnl: float,
        pnl_pct: float,
        entry_date: Optional[str] = None,
        exit_date: Optional[str] = None,
        hold_time_minutes: int = 0
    ) -> None:
        """
        Record a completed trade for performance tracking.
        
        Args:
            ticker: Underlying symbol
            strategy: Strategy used (e.g., 'iron_condor', 'bull_put_spread')
            pnl: Dollar P&L
            pnl_pct: Percentage P&L
            entry_date: Trade entry date (ISO format)
            exit_date: Trade exit date (ISO format)
            hold_time_minutes: How long position was held
        """
        now = datetime.now().isoformat()
        is_winner = pnl > 0
        
        # Create trade record
        trade = TradeRecord(
            ticker=ticker,
            strategy=strategy,
            entry_date=entry_date or now,
            exit_date=exit_date or now,
            pnl=pnl,
            pnl_pct=pnl_pct,
            is_winner=is_winner,
            hold_time_minutes=hold_time_minutes
        )
        self._trades_cache.append(trade)
        
        # Update metrics
        self._update_metrics(ticker, strategy, trade)
        
        # Persist
        self._save_to_state()
        
        self._logger.log("strategy_trade_recorded", {
            "ticker": ticker,
            "strategy": strategy,
            "pnl": pnl,
            "is_winner": is_winner
        })
    
    def _update_metrics(self, ticker: str, strategy: str, trade: TradeRecord) -> None:
        """Update metrics for a ticker-strategy combination."""
        if ticker not in self._metrics_cache:
            self._metrics_cache[ticker] = {}
        
        if strategy not in self._metrics_cache[ticker]:
            self._metrics_cache[ticker][strategy] = StrategyMetrics(
                ticker=ticker,
                strategy=strategy
            )
        
        metrics = self._metrics_cache[ticker][strategy]
        
        # Update counts
        metrics.total_trades += 1
        if trade.is_winner:
            metrics.winning_trades += 1
        else:
            metrics.losing_trades += 1
        
        # Update P&L
        metrics.total_pnl += trade.pnl
        metrics.total_pnl_pct += trade.pnl_pct
        
        # Update max win/loss
        if trade.pnl > metrics.max_win:
            metrics.max_win = trade.pnl
        if trade.pnl < metrics.max_loss:
            metrics.max_loss = trade.pnl
        
        # Recalculate averages
        if metrics.winning_trades > 0:
            wins = [t.pnl for t in self._trades_cache 
                   if t.ticker == ticker and t.strategy == strategy and t.is_winner]
            metrics.avg_win = sum(wins) / len(wins) if wins else 0
        
        if metrics.losing_trades > 0:
            losses = [t.pnl for t in self._trades_cache 
                     if t.ticker == ticker and t.strategy == strategy and not t.is_winner]
            metrics.avg_loss = sum(losses) / len(losses) if losses else 0
        
        # Recalculate win rate
        if metrics.total_trades > 0:
            metrics.win_rate = metrics.winning_trades / metrics.total_trades
        
        # Recalculate profit factor
        gross_profit = sum(t.pnl for t in self._trades_cache 
                          if t.ticker == ticker and t.strategy == strategy and t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self._trades_cache 
                            if t.ticker == ticker and t.strategy == strategy and t.pnl < 0))
        metrics.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        # Calculate recent performance
        cutoff = (datetime.now() - timedelta(days=self.RECENT_WINDOW_DAYS)).isoformat()
        recent_trades = [t for t in self._trades_cache 
                        if t.ticker == ticker and t.strategy == strategy 
                        and t.exit_date and t.exit_date >= cutoff]
        
        metrics.recent_trades = len(recent_trades)
        if recent_trades:
            recent_wins = sum(1 for t in recent_trades if t.is_winner)
            metrics.recent_win_rate = recent_wins / len(recent_trades)
        
        # Calculate Sharpe ratio (simplified)
        pnls = [t.pnl_pct for t in self._trades_cache 
               if t.ticker == ticker and t.strategy == strategy]
        if len(pnls) > 1:
            import statistics
            mean_return = statistics.mean(pnls)
            std_return = statistics.stdev(pnls)
            metrics.sharpe_ratio = mean_return / std_return if std_return > 0 else 0
        
        metrics.last_trade_date = trade.exit_date
    
    def get_best_strategy(self, ticker: str) -> Optional[str]:
        """
        Get the best-performing strategy for a ticker.
        
        Returns None if insufficient data to recommend.
        
        Args:
            ticker: Underlying symbol
            
        Returns:
            Strategy name (e.g., 'iron_condor') or None
        """
        if ticker not in self._metrics_cache:
            return None
        
        candidates = []
        
        for strategy, metrics in self._metrics_cache[ticker].items():
            # Need minimum trades to be considered
            if metrics.total_trades < self.MIN_TRADES_FOR_RECOMMENDATION:
                continue
            
            # Need minimum win rate
            if metrics.win_rate < self.MIN_WIN_RATE:
                continue
            
            # Calculate composite score
            score = self._calculate_strategy_score(metrics)
            candidates.append((strategy, score, metrics))
        
        if not candidates:
            return None
        
        # Sort by score descending
        candidates.sort(key=lambda x: x[1], reverse=True)
        
        best_strategy = candidates[0][0]
        best_score = candidates[0][1]
        best_metrics = candidates[0][2]
        
        self._logger.log("strategy_best_selected", {
            "ticker": ticker,
            "best_strategy": best_strategy,
            "score": round(best_score, 3),
            "win_rate": round(best_metrics.win_rate, 2),
            "total_trades": best_metrics.total_trades,
            "profit_factor": round(best_metrics.profit_factor, 2)
        })
        
        return best_strategy
    
    def _calculate_strategy_score(self, metrics: StrategyMetrics) -> float:
        """
        Calculate composite score for strategy ranking.
        
        Higher is better.
        """
        # Normalize profit factor (cap at 5 for scoring)
        pf_normalized = min(metrics.profit_factor, 5.0) / 5.0
        
        # Normalize Sharpe (cap at 3 for scoring)
        sharpe_normalized = min(max(metrics.sharpe_ratio, 0), 3.0) / 3.0
        
        score = (
            self.WEIGHT_WIN_RATE * metrics.win_rate +
            self.WEIGHT_PROFIT_FACTOR * pf_normalized +
            self.WEIGHT_RECENT_WIN_RATE * metrics.recent_win_rate +
            self.WEIGHT_SHARPE * sharpe_normalized
        )
        
        # Bonus for high trade count (more confidence)
        if metrics.total_trades >= 10:
            score *= 1.1
        elif metrics.total_trades >= 5:
            score *= 1.05
        
        return score
    
    def get_strategy_rankings(self, ticker: str) -> List[Tuple[str, float, StrategyMetrics]]:
        """
        Get all strategies ranked by performance for a ticker.
        
        Returns:
            List of (strategy_name, score, metrics) tuples, sorted by score
        """
        if ticker not in self._metrics_cache:
            return []
        
        rankings = []
        for strategy, metrics in self._metrics_cache[ticker].items():
            score = self._calculate_strategy_score(metrics)
            rankings.append((strategy, score, metrics))
        
        rankings.sort(key=lambda x: x[1], reverse=True)
        return rankings
    
    def get_ticker_metrics(self, ticker: str) -> Dict[str, StrategyMetrics]:
        """Get all strategy metrics for a ticker."""
        return self._metrics_cache.get(ticker, {})
    
    def get_all_metrics(self) -> Dict[str, Dict[str, StrategyMetrics]]:
        """Get all tracked metrics."""
        return self._metrics_cache
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all tracked performance."""
        total_trades = sum(
            m.total_trades 
            for strategies in self._metrics_cache.values() 
            for m in strategies.values()
        )
        
        tickers_with_recommendations = sum(
            1 for ticker in self._metrics_cache
            if self.get_best_strategy(ticker) is not None
        )
        
        # Find overall best strategies
        strategy_wins = defaultdict(int)
        strategy_trades = defaultdict(int)
        
        for strategies in self._metrics_cache.values():
            for strategy, metrics in strategies.items():
                strategy_wins[strategy] += metrics.winning_trades
                strategy_trades[strategy] += metrics.total_trades
        
        strategy_overall_rates = {
            s: strategy_wins[s] / strategy_trades[s] if strategy_trades[s] > 0 else 0
            for s in strategy_trades
        }
        
        best_overall = max(strategy_overall_rates.items(), key=lambda x: x[1]) if strategy_overall_rates else (None, 0)
        
        return {
            "total_tickers_tracked": len(self._metrics_cache),
            "tickers_with_recommendations": tickers_with_recommendations,
            "total_trades_recorded": total_trades,
            "strategy_overall_win_rates": strategy_overall_rates,
            "best_overall_strategy": best_overall[0],
            "best_overall_win_rate": round(best_overall[1], 3) if best_overall[1] else 0
        }


# Singleton instance
_tracker_instance: Optional[StrategyPerformanceTracker] = None


def get_strategy_tracker() -> StrategyPerformanceTracker:
    """Get or create the singleton StrategyPerformanceTracker instance."""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = StrategyPerformanceTracker()
    return _tracker_instance
