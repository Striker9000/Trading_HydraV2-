"""
Edge Decay Monitor - Track alpha half-life and strategy degradation.

Monitors performance over multiple rolling windows to detect edge decay.
Alerts when short-term performance significantly lags long-term performance.

Key metrics:
- Rolling Sharpe over 7, 30, 90 day windows
- Edge decay ratio (short-term / long-term)
- Strategy-specific decay tracking
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import math

from ..core.logging import get_logger
from ..core.state import get_state, set_state


@dataclass
class EdgeDecayStatus:
    """Status of edge decay for a strategy."""
    strategy_id: str
    sharpe_7d: float
    sharpe_30d: float
    sharpe_90d: float
    decay_ratio: float  # 30d / 90d - below 0.5 = concerning
    status: str  # healthy, warning, degraded, failed
    recommended_action: str
    last_updated: str


class EdgeDecayMonitor:
    """
    Monitor edge decay across strategies.
    
    Philosophy:
    - Edges erode as more participants discover them
    - Short-term performance lagging long-term = possible decay
    - Automatic throttling when decay is detected
    
    Thresholds:
    - decay_ratio > 0.8: Healthy
    - decay_ratio 0.5-0.8: Warning
    - decay_ratio 0.3-0.5: Degraded (reduce allocation)
    - decay_ratio < 0.3: Failed (halt strategy)
    """
    
    # Decay thresholds
    HEALTHY_RATIO = 0.8
    WARNING_RATIO = 0.5
    DEGRADED_RATIO = 0.3
    
    # Minimum Sharpe for "healthy" status
    MIN_SHARPE_HEALTHY = 0.5
    MIN_SHARPE_VIABLE = 0.0
    
    def __init__(self):
        self._logger = get_logger()
        self._strategy_metrics: Dict[str, Dict[str, List[float]]] = {}
        self._last_status: Dict[str, EdgeDecayStatus] = {}
        
        self._load_state()
    
    def _load_state(self) -> None:
        """Load persisted state."""
        try:
            saved = get_state("edge_decay.metrics", {})
            if saved:
                self._strategy_metrics = saved
        except Exception as e:
            self._logger.error(f"Failed to load edge decay state: {e}")
    
    def _save_state(self) -> None:
        """Persist state."""
        try:
            set_state("edge_decay.metrics", self._strategy_metrics)
        except Exception as e:
            self._logger.error(f"Failed to save edge decay state: {e}")
    
    def record_daily_return(
        self,
        strategy_id: str,
        daily_return_pct: float,
        date: Optional[str] = None
    ) -> None:
        """
        Record daily return for a strategy.
        
        Args:
            strategy_id: Strategy identifier (e.g., "crypto_momentum", "options_theta")
            daily_return_pct: Daily return in percentage
            date: Date string (YYYY-MM-DD), defaults to today
        """
        if strategy_id not in self._strategy_metrics:
            self._strategy_metrics[strategy_id] = {"returns": [], "dates": []}
        
        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")
        
        # Avoid duplicate entries for same date
        if date in self._strategy_metrics[strategy_id]["dates"]:
            idx = self._strategy_metrics[strategy_id]["dates"].index(date)
            self._strategy_metrics[strategy_id]["returns"][idx] = daily_return_pct
        else:
            self._strategy_metrics[strategy_id]["returns"].append(daily_return_pct)
            self._strategy_metrics[strategy_id]["dates"].append(date)
        
        # Keep last 120 days
        if len(self._strategy_metrics[strategy_id]["returns"]) > 120:
            self._strategy_metrics[strategy_id]["returns"] = \
                self._strategy_metrics[strategy_id]["returns"][-120:]
            self._strategy_metrics[strategy_id]["dates"] = \
                self._strategy_metrics[strategy_id]["dates"][-120:]
        
        self._save_state()
    
    def _calculate_sharpe(self, returns: List[float], risk_free_rate: float = 0.05) -> float:
        """Calculate annualized Sharpe ratio from daily returns."""
        if len(returns) < 5:
            return 0.0
        
        mean_return = sum(returns) / len(returns)
        daily_rf = risk_free_rate / 252
        excess_return = mean_return - daily_rf
        
        variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
        std_dev = math.sqrt(variance) if variance > 0 else 0.001
        
        daily_sharpe = excess_return / std_dev
        return daily_sharpe * math.sqrt(252)  # Annualize
    
    def evaluate_strategy(self, strategy_id: str) -> EdgeDecayStatus:
        """
        Evaluate edge decay for a strategy.
        
        Args:
            strategy_id: Strategy to evaluate
            
        Returns:
            EdgeDecayStatus with recommendations
        """
        if strategy_id not in self._strategy_metrics:
            return EdgeDecayStatus(
                strategy_id=strategy_id,
                sharpe_7d=0.0,
                sharpe_30d=0.0,
                sharpe_90d=0.0,
                decay_ratio=1.0,
                status="insufficient_data",
                recommended_action="continue_monitoring",
                last_updated=datetime.utcnow().isoformat()
            )
        
        returns = self._strategy_metrics[strategy_id]["returns"]
        
        # Calculate rolling Sharpes
        sharpe_7d = self._calculate_sharpe(returns[-7:]) if len(returns) >= 7 else 0.0
        sharpe_30d = self._calculate_sharpe(returns[-30:]) if len(returns) >= 30 else 0.0
        sharpe_90d = self._calculate_sharpe(returns[-90:]) if len(returns) >= 90 else sharpe_30d
        
        # Calculate decay ratio
        if sharpe_90d > 0.1:
            decay_ratio = sharpe_30d / sharpe_90d
        elif sharpe_90d < -0.1:
            # Both negative - check if getting worse
            decay_ratio = sharpe_90d / sharpe_30d if sharpe_30d < -0.1 else 0.0
        else:
            # 90d Sharpe near zero
            decay_ratio = 1.0 if sharpe_30d >= 0 else 0.5
        
        # Clamp ratio
        decay_ratio = max(0.0, min(2.0, decay_ratio))
        
        # Determine status and action
        if decay_ratio >= self.HEALTHY_RATIO and sharpe_30d >= self.MIN_SHARPE_HEALTHY:
            status = "healthy"
            action = "maintain_allocation"
        elif decay_ratio >= self.WARNING_RATIO:
            status = "warning"
            action = "monitor_closely"
        elif decay_ratio >= self.DEGRADED_RATIO:
            status = "degraded"
            action = "reduce_allocation_50pct"
        else:
            status = "failed"
            action = "halt_strategy"
        
        # Override if Sharpe is negative
        if sharpe_30d < self.MIN_SHARPE_VIABLE:
            status = "degraded" if status == "healthy" else status
            action = "reduce_allocation_50pct" if action == "maintain_allocation" else action
        
        result = EdgeDecayStatus(
            strategy_id=strategy_id,
            sharpe_7d=round(sharpe_7d, 3),
            sharpe_30d=round(sharpe_30d, 3),
            sharpe_90d=round(sharpe_90d, 3),
            decay_ratio=round(decay_ratio, 3),
            status=status,
            recommended_action=action,
            last_updated=datetime.utcnow().isoformat()
        )
        
        self._last_status[strategy_id] = result
        
        # Log
        self._logger.log("edge_decay_evaluation", {
            "strategy_id": strategy_id,
            "sharpe_7d": result.sharpe_7d,
            "sharpe_30d": result.sharpe_30d,
            "sharpe_90d": result.sharpe_90d,
            "decay_ratio": result.decay_ratio,
            "status": result.status,
            "action": result.recommended_action
        })
        
        return result
    
    def evaluate_all(self) -> Dict[str, EdgeDecayStatus]:
        """Evaluate all tracked strategies."""
        results = {}
        for strategy_id in self._strategy_metrics.keys():
            results[strategy_id] = self.evaluate_strategy(strategy_id)
        return results
    
    def get_allocation_multiplier(self, strategy_id: str) -> float:
        """
        Get allocation multiplier based on edge decay status.
        
        Returns:
            1.0 = full allocation
            0.5 = reduced allocation
            0.0 = halted
        """
        if strategy_id not in self._last_status:
            self.evaluate_strategy(strategy_id)
        
        status = self._last_status.get(strategy_id)
        if not status:
            return 1.0
        
        if status.status == "healthy":
            return 1.0
        elif status.status == "warning":
            return 0.75
        elif status.status == "degraded":
            return 0.5
        elif status.status == "failed":
            return 0.0
        else:
            return 1.0
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all edge decay statuses."""
        self.evaluate_all()
        
        return {
            "strategies": {
                sid: {
                    "sharpe_30d": s.sharpe_30d,
                    "decay_ratio": s.decay_ratio,
                    "status": s.status,
                    "action": s.recommended_action
                }
                for sid, s in self._last_status.items()
            },
            "thresholds": {
                "healthy": self.HEALTHY_RATIO,
                "warning": self.WARNING_RATIO,
                "degraded": self.DEGRADED_RATIO
            }
        }


# Singleton
_edge_decay_monitor: Optional[EdgeDecayMonitor] = None


def get_edge_decay_monitor() -> EdgeDecayMonitor:
    """Get or create EdgeDecayMonitor singleton."""
    global _edge_decay_monitor
    if _edge_decay_monitor is None:
        _edge_decay_monitor = EdgeDecayMonitor()
    return _edge_decay_monitor
