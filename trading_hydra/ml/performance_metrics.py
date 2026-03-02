"""
Performance Metrics - Tracks Sharpe ratio, win rate, and other performance KPIs.

Provides daily rolling metrics for performance monitoring and risk adjustment.
"""

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict

from ..core.logging import get_logger


@dataclass
class DailyMetrics:
    """Daily performance snapshot."""
    date: str
    equity: float
    daily_pnl: float
    daily_return_pct: float
    cumulative_pnl: float
    trades_executed: int
    trades_profitable: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    max_drawdown_pct: float
    sharpe_ratio: Optional[float]


class PerformanceTracker:
    """
    Tracks and computes trading performance metrics.
    
    Computes rolling Sharpe ratio, win rates, profit factor,
    and other institutional-grade performance KPIs.
    """
    
    METRICS_FILE = Path("logs/performance_metrics.jsonl")
    ROLLING_WINDOW = 20  # 20 trading days for rolling metrics
    RISK_FREE_RATE = 0.05  # 5% annual risk-free rate
    
    def __init__(self):
        self._logger = get_logger()
        self._daily_returns: List[float] = []
        self._daily_pnl: List[float] = []
        self._peak_equity: float = 0.0
        self._metrics_history: List[DailyMetrics] = []
        self._load_history()
    
    def _load_history(self) -> None:
        """Load historical metrics from disk."""
        try:
            if self.METRICS_FILE.exists():
                with open(self.METRICS_FILE, 'r') as f:
                    for line in f:
                        if line.strip():
                            data = json.loads(line)
                            self._daily_returns.append(data.get("daily_return_pct", 0))
                            self._daily_pnl.append(data.get("daily_pnl", 0))
                            
                # Keep only recent history
                self._daily_returns = self._daily_returns[-self.ROLLING_WINDOW*2:]
                self._daily_pnl = self._daily_pnl[-self.ROLLING_WINDOW*2:]
        except Exception as e:
            self._logger.error(f"Failed to load metrics history: {e}")
    
    def record_daily_metrics(
        self,
        equity: float,
        daily_pnl: float,
        trades_executed: int,
        trades_profitable: int,
        win_amounts: List[float],
        loss_amounts: List[float]
    ) -> DailyMetrics:
        """
        Record end-of-day performance metrics.
        
        Args:
            equity: Current account equity
            daily_pnl: Day's profit/loss
            trades_executed: Number of trades today
            trades_profitable: Number of winning trades
            win_amounts: List of winning trade amounts
            loss_amounts: List of losing trade amounts (positive values)
            
        Returns:
            DailyMetrics snapshot
        """
        today = datetime.utcnow().strftime("%Y-%m-%d")
        
        # Calculate daily return %
        prev_equity = equity - daily_pnl
        daily_return_pct = (daily_pnl / prev_equity * 100) if prev_equity > 0 else 0
        
        # Track for rolling calculations
        self._daily_returns.append(daily_return_pct)
        self._daily_pnl.append(daily_pnl)
        
        # Keep window size manageable
        if len(self._daily_returns) > self.ROLLING_WINDOW * 2:
            self._daily_returns = self._daily_returns[-self.ROLLING_WINDOW*2:]
            self._daily_pnl = self._daily_pnl[-self.ROLLING_WINDOW*2:]
        
        # Win rate
        win_rate = trades_profitable / trades_executed if trades_executed > 0 else 0
        
        # Average win/loss
        avg_win = sum(win_amounts) / len(win_amounts) if win_amounts else 0
        avg_loss = sum(loss_amounts) / len(loss_amounts) if loss_amounts else 0
        
        # Profit factor
        total_wins = sum(win_amounts)
        total_losses = sum(loss_amounts)
        profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')
        
        # Max drawdown
        if equity > self._peak_equity:
            self._peak_equity = equity
        drawdown_pct = ((self._peak_equity - equity) / self._peak_equity * 100) if self._peak_equity > 0 else 0
        
        # Rolling Sharpe ratio
        sharpe = self.compute_sharpe_ratio()
        
        # Cumulative P&L
        cumulative_pnl = sum(self._daily_pnl)
        
        metrics = DailyMetrics(
            date=today,
            equity=equity,
            daily_pnl=daily_pnl,
            daily_return_pct=daily_return_pct,
            cumulative_pnl=cumulative_pnl,
            trades_executed=trades_executed,
            trades_profitable=trades_profitable,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=profit_factor if profit_factor != float('inf') else 999.0,
            max_drawdown_pct=drawdown_pct,
            sharpe_ratio=sharpe
        )
        
        self._persist_metrics(metrics)
        
        self._logger.log("daily_metrics_recorded", {
            "date": today,
            "equity": equity,
            "daily_pnl": daily_pnl,
            "daily_return_pct": round(daily_return_pct, 2),
            "win_rate": round(win_rate, 2),
            "sharpe_ratio": round(sharpe, 2) if sharpe else None,
            "max_drawdown_pct": round(drawdown_pct, 2)
        })
        
        return metrics
    
    def compute_sharpe_ratio(self, annualize: bool = True) -> Optional[float]:
        """
        Compute rolling Sharpe ratio.
        
        Sharpe = (Avg Return - Risk Free Rate) / Std Dev of Returns
        
        Args:
            annualize: Whether to annualize the ratio (252 trading days)
            
        Returns:
            Sharpe ratio, or None if insufficient data
        """
        if len(self._daily_returns) < 5:
            return None
        
        returns = self._daily_returns[-self.ROLLING_WINDOW:]
        
        avg_return = sum(returns) / len(returns)
        
        # Daily risk-free rate
        daily_rf = self.RISK_FREE_RATE / 252
        excess_return = avg_return - daily_rf
        
        # Standard deviation
        variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
        std_dev = math.sqrt(variance) if variance > 0 else 0.001
        
        sharpe = excess_return / std_dev
        
        if annualize:
            sharpe *= math.sqrt(252)
        
        return sharpe
    
    def compute_sortino_ratio(self) -> Optional[float]:
        """
        Compute Sortino ratio (downside deviation only).
        
        More appropriate for trading strategies where we only care
        about downside volatility.
        """
        if len(self._daily_returns) < 5:
            return None
        
        returns = self._daily_returns[-self.ROLLING_WINDOW:]
        avg_return = sum(returns) / len(returns)
        daily_rf = self.RISK_FREE_RATE / 252
        
        # Downside deviation (only negative returns)
        downside_returns = [r for r in returns if r < 0]
        if not downside_returns:
            return 999.0  # No downside = infinite Sortino
        
        downside_variance = sum(r ** 2 for r in downside_returns) / len(downside_returns)
        downside_dev = math.sqrt(downside_variance)
        
        sortino = (avg_return - daily_rf) / downside_dev if downside_dev > 0 else 0
        return sortino * math.sqrt(252)  # Annualize
    
    def get_current_metrics(self) -> Dict[str, Any]:
        """Get current performance metrics summary."""
        sharpe = self.compute_sharpe_ratio()
        sortino = self.compute_sortino_ratio()
        
        recent_returns = self._daily_returns[-self.ROLLING_WINDOW:]
        
        return {
            "sharpe_ratio": round(sharpe, 2) if sharpe else None,
            "sortino_ratio": round(sortino, 2) if sortino else None,
            "avg_daily_return": sum(recent_returns) / len(recent_returns) if recent_returns else 0,
            "total_pnl_20d": sum(self._daily_pnl[-20:]) if self._daily_pnl else 0,
            "max_daily_gain": max(recent_returns) if recent_returns else 0,
            "max_daily_loss": min(recent_returns) if recent_returns else 0,
            "positive_days": sum(1 for r in recent_returns if r > 0),
            "negative_days": sum(1 for r in recent_returns if r < 0),
            "trading_days_tracked": len(self._daily_returns)
        }
    
    def _persist_metrics(self, metrics: DailyMetrics) -> None:
        """Append daily metrics to JSONL file."""
        try:
            self.METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.METRICS_FILE, 'a') as f:
                f.write(json.dumps(asdict(metrics)) + "\n")
        except Exception as e:
            self._logger.error(f"Failed to persist daily metrics: {e}")


# Singleton instance
_tracker_instance: Optional[PerformanceTracker] = None


def get_performance_tracker() -> PerformanceTracker:
    """Get the singleton PerformanceTracker instance."""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = PerformanceTracker()
    return _tracker_instance
