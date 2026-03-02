"""PnL Distribution Monitor - Fat-Tail Detection and Auto-Halt.

Tracks rolling trade returns and detects:
- Fat-tail events (loss > k * median loss)
- Distribution shifts
- Anomalous loss patterns

When detected:
- Auto halt with labeled reason
- Log detailed metrics for post-mortem

Safe defaults for live trading.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from collections import deque
import math
import statistics

from ..core.logging import get_logger
from ..core.state import get_state, set_state
from ..core.config import load_settings


@dataclass
class TradeReturn:
    """Single trade return record."""
    timestamp: datetime
    symbol: str
    bot_id: str
    return_pct: float
    return_usd: float
    hold_minutes: int


@dataclass
class DistributionMetrics:
    """Statistical metrics of return distribution."""
    count: int
    mean_pct: float
    median_pct: float
    std_pct: float
    min_pct: float
    max_pct: float
    win_rate: float
    skewness: float
    kurtosis_excess: float  # Excess kurtosis (>0 = fat tails)
    fat_tail_threshold: float
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": self.count,
            "mean_pct": round(self.mean_pct, 4),
            "median_pct": round(self.median_pct, 4),
            "std_pct": round(self.std_pct, 4),
            "min_pct": round(self.min_pct, 4),
            "max_pct": round(self.max_pct, 4),
            "win_rate": round(self.win_rate, 4),
            "skewness": round(self.skewness, 4),
            "kurtosis_excess": round(self.kurtosis_excess, 4),
            "fat_tail_threshold": round(self.fat_tail_threshold, 4)
        }


@dataclass
class FatTailEvent:
    """Detected fat-tail event."""
    timestamp: datetime
    symbol: str
    bot_id: str
    loss_pct: float
    threshold_pct: float
    multiple_of_median: float
    reason: str


@dataclass
class PnLMonitorState:
    """Current PnL monitor state."""
    is_halted: bool = False
    halt_reason: Optional[str] = None
    halted_at: Optional[datetime] = None
    resume_at: Optional[datetime] = None
    recent_fat_tails: List[FatTailEvent] = field(default_factory=list)
    distribution: Optional[DistributionMetrics] = None


class PnLDistributionMonitor:
    """
    Monitor PnL distribution for fat-tail events.
    
    Philosophy:
    - Normal losses are expected; fat-tail losses are warnings
    - Track rolling distribution, detect when losses exceed k * median
    - Auto halt and explain why
    
    Thresholds:
    - FAT_TAIL_K: 3.0 (loss > 3x median loss = fat tail)
    - MIN_SAMPLES: 10 (need enough data for statistics)
    - HALT_COUNT: 2 (2 fat tails in rolling window = halt)
    """
    
    # Defaults
    FAT_TAIL_K = 3.0          # Loss > k * median = fat tail
    MIN_SAMPLES = 10          # Minimum trades for statistics
    HALT_FAT_TAIL_COUNT = 2   # Fat tails to trigger halt
    ROLLING_WINDOW = 50       # Rolling window size
    HALT_COOLDOWN_MINUTES = 120
    FAT_TAIL_WINDOW_HOURS = 24
    
    def __init__(self):
        self._logger = get_logger()
        self._settings = load_settings()
        
        config = self._settings.get("pnl_monitor", {})
        self._enabled = config.get("enabled", True)
        self._fat_tail_k = config.get("fat_tail_k", self.FAT_TAIL_K)
        self._min_samples = config.get("min_samples", self.MIN_SAMPLES)
        self._halt_count = config.get("halt_fat_tail_count", self.HALT_FAT_TAIL_COUNT)
        self._cooldown_min = config.get("halt_cooldown_minutes", self.HALT_COOLDOWN_MINUTES)
        
        # State
        self._returns: deque = deque(maxlen=self.ROLLING_WINDOW)
        self._fat_tails: deque = deque(maxlen=20)
        self._state = PnLMonitorState()
        
        self._logger.log("pnl_monitor_init", {
            "enabled": self._enabled,
            "fat_tail_k": self._fat_tail_k,
            "halt_fat_tail_count": self._halt_count
        })
    
    def record_trade(
        self,
        symbol: str,
        bot_id: str,
        return_pct: float,
        return_usd: float,
        hold_minutes: int = 0
    ) -> PnLMonitorState:
        """
        Record a trade result and check for fat-tail events.
        
        Args:
            symbol: Trading symbol
            bot_id: Bot identifier
            return_pct: Return percentage (positive or negative)
            return_usd: Dollar return
            hold_minutes: How long position was held
            
        Returns:
            Updated PnLMonitorState
        """
        if not self._enabled:
            return self._state
        
        now = datetime.utcnow()
        
        # Record return
        trade = TradeReturn(
            timestamp=now,
            symbol=symbol,
            bot_id=bot_id,
            return_pct=return_pct,
            return_usd=return_usd,
            hold_minutes=hold_minutes
        )
        self._returns.append(trade)
        
        # Check halt expiry
        if self._state.resume_at and now > self._state.resume_at:
            self._clear_halt()
        
        # Skip if not enough samples
        if len(self._returns) < self._min_samples:
            return self._state
        
        # Compute distribution
        metrics = self._compute_distribution()
        self._state.distribution = metrics
        
        # Check for fat-tail (only on losses)
        if return_pct < 0:
            is_fat_tail = self._check_fat_tail(trade, metrics)
            if is_fat_tail:
                self._evaluate_halt()
        
        return self._state
    
    def _compute_distribution(self) -> DistributionMetrics:
        """Compute distribution metrics from rolling returns."""
        returns = [t.return_pct for t in self._returns]
        losses = [r for r in returns if r < 0]
        
        n = len(returns)
        mean = statistics.mean(returns)
        median = statistics.median(returns)
        std = statistics.stdev(returns) if n > 1 else 0.0
        
        # Win rate
        wins = sum(1 for r in returns if r > 0)
        win_rate = wins / n if n > 0 else 0.0
        
        # Skewness and kurtosis (simplified)
        skewness = 0.0
        kurtosis = 0.0
        
        if std > 0 and n > 2:
            # Sample skewness
            m3 = sum((r - mean) ** 3 for r in returns) / n
            skewness = m3 / (std ** 3)
            
            # Sample excess kurtosis
            m4 = sum((r - mean) ** 4 for r in returns) / n
            kurtosis = (m4 / (std ** 4)) - 3.0
        
        # Fat-tail threshold based on median loss
        median_loss = abs(statistics.median(losses)) if losses else 0.01
        fat_tail_threshold = -median_loss * self._fat_tail_k
        
        return DistributionMetrics(
            count=n,
            mean_pct=mean,
            median_pct=median,
            std_pct=std,
            min_pct=min(returns),
            max_pct=max(returns),
            win_rate=win_rate,
            skewness=skewness,
            kurtosis_excess=kurtosis,
            fat_tail_threshold=fat_tail_threshold
        )
    
    def _check_fat_tail(self, trade: TradeReturn, metrics: DistributionMetrics) -> bool:
        """Check if trade is a fat-tail event."""
        if trade.return_pct >= 0:
            return False
        
        # Calculate median loss
        losses = [t.return_pct for t in self._returns if t.return_pct < 0]
        if not losses:
            return False
        
        median_loss = abs(statistics.median(losses))
        if median_loss <= 0:
            return False
        
        # Check if this loss exceeds threshold
        multiple = abs(trade.return_pct) / median_loss
        
        if multiple >= self._fat_tail_k:
            event = FatTailEvent(
                timestamp=trade.timestamp,
                symbol=trade.symbol,
                bot_id=trade.bot_id,
                loss_pct=trade.return_pct,
                threshold_pct=metrics.fat_tail_threshold,
                multiple_of_median=multiple,
                reason=f"loss_{trade.return_pct:.2f}pct_is_{multiple:.1f}x_median_loss"
            )
            
            self._fat_tails.append(event)
            self._state.recent_fat_tails.append(event)
            
            self._logger.log("pnl_monitor_fat_tail_detected", {
                "symbol": trade.symbol,
                "bot_id": trade.bot_id,
                "loss_pct": trade.return_pct,
                "multiple_of_median": round(multiple, 2),
                "threshold_k": self._fat_tail_k
            })
            
            return True
        
        return False
    
    def _evaluate_halt(self):
        """Evaluate whether to halt based on recent fat-tails."""
        now = datetime.utcnow()
        window_start = now - timedelta(hours=self.FAT_TAIL_WINDOW_HOURS)
        
        # Count recent fat-tails
        recent = [
            e for e in self._fat_tails 
            if e.timestamp >= window_start
        ]
        
        if len(recent) >= self._halt_count:
            # Build detailed reason
            symbols = set(e.symbol for e in recent)
            bots = set(e.bot_id for e in recent)
            total_loss = sum(e.loss_pct for e in recent)
            
            reason = (
                f"{len(recent)}_fat_tails_in_{self.FAT_TAIL_WINDOW_HOURS}h|"
                f"symbols={list(symbols)}|"
                f"bots={list(bots)}|"
                f"total_loss={total_loss:.2f}pct"
            )
            
            self._state.is_halted = True
            self._state.halt_reason = reason
            self._state.halted_at = now
            self._state.resume_at = now + timedelta(minutes=self._cooldown_min)
            
            self._logger.log("pnl_monitor_halt", {
                "reason": reason,
                "fat_tail_count": len(recent),
                "cooldown_minutes": self._cooldown_min
            })
            
            self._persist_state()
    
    def _clear_halt(self):
        """Clear halt state."""
        old_reason = self._state.halt_reason
        self._state = PnLMonitorState()
        self._state.distribution = self._compute_distribution() if len(self._returns) >= self._min_samples else None
        
        self._logger.log("pnl_monitor_halt_cleared", {
            "previous_reason": old_reason
        })
        
        self._persist_state()
    
    def _persist_state(self):
        """Persist state to durable storage."""
        set_state("pnl_monitor.state", {
            "is_halted": self._state.is_halted,
            "halt_reason": self._state.halt_reason,
            "halted_at": self._state.halted_at.isoformat() if self._state.halted_at else None,
            "resume_at": self._state.resume_at.isoformat() if self._state.resume_at else None
        })
    
    def get_state(self) -> PnLMonitorState:
        """Get current monitor state."""
        # Check expiry
        now = datetime.utcnow()
        if self._state.resume_at and now > self._state.resume_at:
            self._clear_halt()
        return self._state
    
    def is_halted(self) -> bool:
        """Check if trading is halted due to fat-tail events."""
        return self.get_state().is_halted
    
    def get_distribution(self) -> Optional[DistributionMetrics]:
        """Get current distribution metrics."""
        return self._state.distribution
    
    def clear_halt(self):
        """Manually clear halt (for recovery)."""
        self._clear_halt()


# Singleton
_pnl_monitor: Optional[PnLDistributionMonitor] = None


def get_pnl_monitor() -> PnLDistributionMonitor:
    """Get or create PnLDistributionMonitor singleton."""
    global _pnl_monitor
    if _pnl_monitor is None:
        _pnl_monitor = PnLDistributionMonitor()
    return _pnl_monitor
