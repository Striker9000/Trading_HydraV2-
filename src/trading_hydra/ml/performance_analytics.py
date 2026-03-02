"""
Performance Analytics - Performance Tracking v1.

NOTE: Targets are aspirational, not validated. Use for monitoring.

Tracks and computes:
- Sharpe Ratio (target > 1.5)
- Win Rate (target > 55%)
- Profit Factor (target > 1.5)
- Maximum Drawdown (target < 10% monthly)
- Sortino Ratio
- Calmar Ratio
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from collections import defaultdict
import math

from ..core.logging import get_logger
from ..core.state import get_state, set_state


@dataclass
class PerformanceMetrics:
    """Complete performance metrics snapshot."""
    timestamp: str
    period_days: int
    
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    
    total_return_pct: float
    annualized_return_pct: float
    
    win_rate: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    win_loss_ratio: float
    
    max_drawdown_pct: float
    current_drawdown_pct: float
    avg_drawdown_pct: float
    max_drawdown_duration_days: int
    
    total_trades: int
    winning_trades: int
    losing_trades: int
    
    daily_volatility: float
    monthly_volatility: float
    
    best_day_pct: float
    worst_day_pct: float
    avg_daily_pnl_pct: float
    
    meets_sharpe_target: bool
    meets_win_rate_target: bool
    meets_profit_factor_target: bool
    meets_drawdown_target: bool
    overall_score: float


@dataclass  
class TradeRecord:
    """Individual trade record for analysis."""
    trade_id: str
    symbol: str
    side: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    qty: float
    pnl: float
    pnl_pct: float
    hold_time_minutes: float
    bot_id: str
    asset_class: str


class PerformanceAnalytics:
    """
    Institutional performance analytics engine.
    
    Computes all key performance metrics for trading evaluation.
    Target metrics:
    - Sharpe Ratio > 1.5
    - Win Rate > 55%
    - Profit Factor > 1.5
    - Max Drawdown < 10% monthly
    """
    
    # Realistic targets for multi-strategy crypto/equity trading system
    # These are achievable with good risk management, not aspirational
    SHARPE_TARGET = 1.0          # Net after friction, 1.5+ is excellent
    SHARPE_EXCELLENT = 1.5       # Excellent performance threshold
    SHARPE_SUSPICIOUS = 2.5      # Investigate if consistently above this
    
    WIN_RATE_TARGET = 0.45       # 45% win rate is fine with good R:R
    WIN_RATE_EXCELLENT = 0.55    # Excellent win rate
    WIN_RATE_SUSPICIOUS = 0.70   # Investigate for overfitting
    
    PROFIT_FACTOR_TARGET = 1.3   # Minimum viable
    PROFIT_FACTOR_EXCELLENT = 1.8
    
    MAX_DRAWDOWN_TARGET = 15.0   # Monthly max drawdown target
    MAX_DRAWDOWN_REVIEW = 20.0   # Trigger system review
    MAX_DRAWDOWN_HALT = 25.0     # Auto-halt threshold
    
    RISK_FREE_RATE = 0.05
    TRADING_DAYS_PER_YEAR = 252
    
    def __init__(self):
        self._logger = get_logger()
        self._trade_history: List[TradeRecord] = []
        self._daily_pnl: Dict[str, float] = {}
        self._equity_curve: List[Tuple[str, float]] = []
        
        self._load_history()
    
    def _load_history(self) -> None:
        """Load historical data from state."""
        try:
            saved_trades = get_state("performance.trade_history", [])
            if saved_trades:
                self._trade_history = [
                    TradeRecord(**t) if isinstance(t, dict) else t 
                    for t in saved_trades
                ]
            
            saved_pnl = get_state("performance.daily_pnl", {})
            if saved_pnl:
                self._daily_pnl = saved_pnl
            
            saved_equity = get_state("performance.equity_curve", [])
            if saved_equity:
                self._equity_curve = [(e[0], e[1]) for e in saved_equity]
                
        except Exception as e:
            self._logger.error(f"Failed to load performance history: {e}")
    
    def _save_history(self) -> None:
        """Save historical data to state."""
        try:
            trade_dicts = [asdict(t) for t in self._trade_history[-1000:]]
            set_state("performance.trade_history", trade_dicts)
            
            recent_pnl = dict(sorted(self._daily_pnl.items())[-365:])
            set_state("performance.daily_pnl", recent_pnl)
            
            set_state("performance.equity_curve", self._equity_curve[-365:])
            
        except Exception as e:
            self._logger.error(f"Failed to save performance history: {e}")
    
    def record_trade(self, trade: TradeRecord) -> None:
        """Record a completed trade."""
        self._trade_history.append(trade)
        
        date_key = trade.exit_time[:10] if trade.exit_time else datetime.utcnow().strftime("%Y-%m-%d")
        if date_key not in self._daily_pnl:
            self._daily_pnl[date_key] = 0.0
        self._daily_pnl[date_key] += trade.pnl
        
        self._save_history()
        
        self._logger.log("trade_recorded", {
            "trade_id": trade.trade_id,
            "symbol": trade.symbol,
            "pnl": trade.pnl,
            "pnl_pct": trade.pnl_pct
        })
    
    def record_daily_equity(self, equity: float, date: Optional[str] = None) -> None:
        """Record end-of-day equity."""
        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")
        
        if self._equity_curve and self._equity_curve[-1][0] == date:
            self._equity_curve[-1] = (date, equity)
        else:
            self._equity_curve.append((date, equity))
        
        self._save_history()
    
    def calculate_metrics(self, period_days: int = 30) -> PerformanceMetrics:
        """
        Calculate comprehensive performance metrics.
        
        Args:
            period_days: Number of days to analyze
            
        Returns:
            PerformanceMetrics with all calculated values
        """
        now = datetime.utcnow()
        cutoff = now - timedelta(days=period_days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        
        period_trades = [
            t for t in self._trade_history
            if t.exit_time and t.exit_time[:10] >= cutoff_str
        ]
        
        period_pnl = {
            k: v for k, v in self._daily_pnl.items()
            if k >= cutoff_str
        }
        
        period_equity = [
            (d, e) for d, e in self._equity_curve
            if d >= cutoff_str
        ]
        
        total_trades = len(period_trades)
        winning_trades = sum(1 for t in period_trades if t.pnl > 0)
        losing_trades = sum(1 for t in period_trades if t.pnl <= 0)
        
        win_rate = winning_trades / total_trades if total_trades > 0 else 0.5
        
        total_wins = sum(t.pnl for t in period_trades if t.pnl > 0)
        total_losses = abs(sum(t.pnl for t in period_trades if t.pnl < 0))
        profit_factor = total_wins / total_losses if total_losses > 0 else 1.0
        
        avg_win = total_wins / winning_trades if winning_trades > 0 else 0
        avg_loss = total_losses / losing_trades if losing_trades > 0 else 0
        win_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 1.0
        
        daily_returns = list(period_pnl.values()) if period_pnl else [0]
        
        if period_equity and len(period_equity) >= 2:
            start_equity = period_equity[0][1]
            end_equity = period_equity[-1][1]
            total_return_pct = ((end_equity - start_equity) / start_equity * 100) if start_equity > 0 else 0
        else:
            total_return_pct = sum(daily_returns) / 100 if daily_returns else 0
        
        annualized_return_pct = total_return_pct * (365 / period_days) if period_days > 0 else 0
        
        avg_daily_pnl = sum(daily_returns) / len(daily_returns) if daily_returns else 0
        daily_volatility = self._calculate_std(daily_returns)
        monthly_volatility = daily_volatility * math.sqrt(21)
        
        sharpe_ratio = self._calculate_sharpe(daily_returns)
        sortino_ratio = self._calculate_sortino(daily_returns)
        
        max_dd, current_dd, avg_dd, max_dd_duration = self._calculate_drawdown_metrics(period_equity)
        
        calmar_ratio = abs(annualized_return_pct / max_dd) if max_dd > 0 else 0
        
        best_day = max(daily_returns) if daily_returns else 0
        worst_day = min(daily_returns) if daily_returns else 0
        
        start_equity = period_equity[0][1] if period_equity else 10000
        best_day_pct = best_day / start_equity * 100 if start_equity > 0 else 0
        worst_day_pct = worst_day / start_equity * 100 if start_equity > 0 else 0
        avg_daily_pnl_pct = avg_daily_pnl / start_equity * 100 if start_equity > 0 else 0
        
        meets_sharpe = sharpe_ratio >= self.SHARPE_TARGET
        meets_win_rate = win_rate >= self.WIN_RATE_TARGET
        meets_pf = profit_factor >= self.PROFIT_FACTOR_TARGET
        meets_dd = max_dd <= self.MAX_DRAWDOWN_TARGET
        
        targets_met = sum([meets_sharpe, meets_win_rate, meets_pf, meets_dd])
        overall_score = (targets_met / 4) * 100
        
        metrics = PerformanceMetrics(
            timestamp=now.isoformat(),
            period_days=period_days,
            sharpe_ratio=round(sharpe_ratio, 3),
            sortino_ratio=round(sortino_ratio, 3),
            calmar_ratio=round(calmar_ratio, 3),
            total_return_pct=round(total_return_pct, 2),
            annualized_return_pct=round(annualized_return_pct, 2),
            win_rate=round(win_rate, 3),
            profit_factor=round(profit_factor, 3),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            win_loss_ratio=round(win_loss_ratio, 3),
            max_drawdown_pct=round(max_dd, 2),
            current_drawdown_pct=round(current_dd, 2),
            avg_drawdown_pct=round(avg_dd, 2),
            max_drawdown_duration_days=max_dd_duration,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            daily_volatility=round(daily_volatility, 2),
            monthly_volatility=round(monthly_volatility, 2),
            best_day_pct=round(best_day_pct, 3),
            worst_day_pct=round(worst_day_pct, 3),
            avg_daily_pnl_pct=round(avg_daily_pnl_pct, 4),
            meets_sharpe_target=meets_sharpe,
            meets_win_rate_target=meets_win_rate,
            meets_profit_factor_target=meets_pf,
            meets_drawdown_target=meets_dd,
            overall_score=round(overall_score, 1)
        )
        
        self._logger.log("performance_metrics_calculated", {
            "period_days": period_days,
            "sharpe": metrics.sharpe_ratio,
            "win_rate": metrics.win_rate,
            "profit_factor": metrics.profit_factor,
            "max_dd": metrics.max_drawdown_pct,
            "overall_score": metrics.overall_score
        })
        
        return metrics
    
    def _calculate_std(self, returns: List[float]) -> float:
        """Calculate standard deviation of returns."""
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        return math.sqrt(variance) if variance > 0 else 0.0
    
    def _calculate_sharpe(self, daily_returns: List[float]) -> float:
        """
        Calculate Sharpe Ratio.
        Sharpe = (Return - Risk Free Rate) / Volatility
        """
        if len(daily_returns) < 2:
            return 0.0
        
        mean_return = sum(daily_returns) / len(daily_returns)
        std_return = self._calculate_std(daily_returns)
        
        if std_return == 0:
            return 0.0
        
        daily_rf = self.RISK_FREE_RATE / self.TRADING_DAYS_PER_YEAR
        
        daily_sharpe = (mean_return - daily_rf) / std_return
        
        annualized_sharpe = daily_sharpe * math.sqrt(self.TRADING_DAYS_PER_YEAR)
        
        return annualized_sharpe
    
    def _calculate_sortino(self, daily_returns: List[float]) -> float:
        """
        Calculate Sortino Ratio.
        Sortino = (Return - Risk Free Rate) / Downside Volatility
        """
        if len(daily_returns) < 2:
            return 0.0
        
        mean_return = sum(daily_returns) / len(daily_returns)
        
        downside_returns = [min(0, r - mean_return) for r in daily_returns]
        downside_variance = sum(r ** 2 for r in downside_returns) / (len(downside_returns) - 1)
        downside_std = math.sqrt(downside_variance) if downside_variance > 0 else 0.0
        
        if downside_std == 0:
            return 0.0
        
        daily_rf = self.RISK_FREE_RATE / self.TRADING_DAYS_PER_YEAR
        
        daily_sortino = (mean_return - daily_rf) / downside_std
        annualized_sortino = daily_sortino * math.sqrt(self.TRADING_DAYS_PER_YEAR)
        
        return annualized_sortino
    
    def _calculate_drawdown_metrics(
        self,
        equity_curve: List[Tuple[str, float]]
    ) -> Tuple[float, float, float, int]:
        """
        Calculate drawdown metrics.
        
        Returns:
            (max_drawdown_pct, current_drawdown_pct, avg_drawdown_pct, max_dd_duration_days)
        """
        if len(equity_curve) < 2:
            return 0.0, 0.0, 0.0, 0
        
        equities = [e[1] for e in equity_curve]
        
        peak = equities[0]
        max_dd = 0.0
        current_dd = 0.0
        drawdowns = []
        
        dd_start = None
        max_dd_duration = 0
        current_duration = 0
        
        for i, eq in enumerate(equities):
            if eq > peak:
                peak = eq
                if dd_start is not None:
                    max_dd_duration = max(max_dd_duration, current_duration)
                dd_start = None
                current_duration = 0
            else:
                dd = (peak - eq) / peak * 100 if peak > 0 else 0
                drawdowns.append(dd)
                max_dd = max(max_dd, dd)
                
                if dd_start is None:
                    dd_start = i
                current_duration = i - dd_start + 1
        
        peak = max(equities)
        current_eq = equities[-1]
        current_dd = (peak - current_eq) / peak * 100 if peak > 0 else 0
        
        avg_dd = sum(drawdowns) / len(drawdowns) if drawdowns else 0
        
        return max_dd, current_dd, avg_dd, max_dd_duration
    
    def get_bot_performance(
        self,
        bot_id: str,
        period_days: int = 30
    ) -> Dict[str, Any]:
        """Get performance metrics for a specific bot."""
        cutoff = (datetime.utcnow() - timedelta(days=period_days)).strftime("%Y-%m-%d")
        
        bot_trades = [
            t for t in self._trade_history
            if t.bot_id == bot_id and t.exit_time and t.exit_time[:10] >= cutoff
        ]
        
        total = len(bot_trades)
        wins = sum(1 for t in bot_trades if t.pnl > 0)
        
        total_pnl = sum(t.pnl for t in bot_trades)
        total_wins = sum(t.pnl for t in bot_trades if t.pnl > 0)
        total_losses = abs(sum(t.pnl for t in bot_trades if t.pnl < 0))
        
        return {
            "bot_id": bot_id,
            "period_days": period_days,
            "total_trades": total,
            "winning_trades": wins,
            "win_rate": wins / total if total > 0 else 0,
            "total_pnl": round(total_pnl, 2),
            "profit_factor": round(total_wins / total_losses, 3) if total_losses > 0 else 1.0,
            "avg_trade_pnl": round(total_pnl / total, 2) if total > 0 else 0
        }
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """Get summary for dashboard display."""
        metrics_30d = self.calculate_metrics(30)
        metrics_7d = self.calculate_metrics(7)
        
        return {
            "30_day": {
                "sharpe": metrics_30d.sharpe_ratio,
                "win_rate": metrics_30d.win_rate,
                "profit_factor": metrics_30d.profit_factor,
                "max_drawdown": metrics_30d.max_drawdown_pct,
                "total_return": metrics_30d.total_return_pct,
                "trades": metrics_30d.total_trades,
                "score": metrics_30d.overall_score
            },
            "7_day": {
                "sharpe": metrics_7d.sharpe_ratio,
                "win_rate": metrics_7d.win_rate,
                "profit_factor": metrics_7d.profit_factor,
                "max_drawdown": metrics_7d.max_drawdown_pct,
                "total_return": metrics_7d.total_return_pct,
                "trades": metrics_7d.total_trades,
                "score": metrics_7d.overall_score
            },
            "targets": {
                "sharpe": self.SHARPE_TARGET,
                "win_rate": self.WIN_RATE_TARGET,
                "profit_factor": self.PROFIT_FACTOR_TARGET,
                "max_drawdown": self.MAX_DRAWDOWN_TARGET
            }
        }


_performance_analytics: Optional[PerformanceAnalytics] = None


def get_performance_analytics() -> PerformanceAnalytics:
    """Get or create singleton performance analytics."""
    global _performance_analytics
    if _performance_analytics is None:
        _performance_analytics = PerformanceAnalytics()
    return _performance_analytics
