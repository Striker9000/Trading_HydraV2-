"""Dynamic Budget Manager - Equity and Drawdown Scaled Budgets.

Replaces static daily budget caps with dynamic scaling based on:
1. Account equity (percentage-based, not fixed USD)
2. Drawdown state (reduce allocation during drawdowns)
3. Recent bot performance (allocate more to winning strategies)

Safe defaults for live trading.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import math

from ..core.logging import get_logger
from ..core.state import get_state, set_state
from ..core.config import load_settings


@dataclass
class BudgetAllocation:
    """Result of dynamic budget calculation."""
    daily_budget_usd: float
    max_position_usd: float
    equity_multiplier: float
    drawdown_multiplier: float
    performance_multiplier: float
    final_multiplier: float
    reason: str
    constraints: Dict[str, Any]


@dataclass
class BotPerformance:
    """Rolling performance metrics for a bot."""
    bot_id: str
    trades_30d: int
    win_rate: float
    avg_return_pct: float
    sharpe_estimate: float
    last_updated: datetime


class DynamicBudgetManager:
    """
    Dynamic budget allocation based on equity, drawdown, and performance.
    
    Philosophy:
    - Budget scales WITH equity growth/decline
    - Drawdowns trigger automatic risk reduction
    - Hot bots get more capital (within limits)
    
    Safe defaults:
    - Base allocation: 2% of equity per day
    - Drawdown scaling: linear reduction 0-15% DD → 1.0x-0.5x
    - Performance boost capped at 1.5x
    """
    
    # Safe defaults (conservative)
    DEFAULT_DAILY_BUDGET_PCT = 2.0          # 2% of equity per day
    DEFAULT_MAX_POSITION_PCT = 1.5          # 1.5% of equity per position
    DEFAULT_MIN_DAILY_BUDGET_USD = 50.0     # Floor
    DEFAULT_MAX_DAILY_BUDGET_USD = 5000.0   # Ceiling
    
    # Drawdown scaling
    DD_THRESHOLD_REDUCE = 0.05   # Start reducing at 5% DD
    DD_THRESHOLD_HALT = 0.15     # Full reduction at 15% DD
    DD_MIN_MULTIPLIER = 0.5      # Never go below 50% allocation
    
    # Performance scaling
    PERF_LOOKBACK_DAYS = 30
    PERF_MIN_TRADES = 5          # Need 5 trades to evaluate
    PERF_MAX_BOOST = 1.5         # Max 1.5x for hot bots
    PERF_SHARPE_THRESHOLD = 1.0  # Need Sharpe > 1.0 for boost
    
    def __init__(self):
        self._logger = get_logger()
        self._settings = load_settings()
        
        budget_config = self._settings.get("dynamic_budget", {})
        self._enabled = budget_config.get("enabled", True)
        self._daily_budget_pct = budget_config.get("daily_budget_pct", self.DEFAULT_DAILY_BUDGET_PCT)
        self._max_position_pct = budget_config.get("max_position_pct", self.DEFAULT_MAX_POSITION_PCT)
        self._min_daily_budget = budget_config.get("min_daily_budget_usd", self.DEFAULT_MIN_DAILY_BUDGET_USD)
        self._max_daily_budget = budget_config.get("max_daily_budget_usd", self.DEFAULT_MAX_DAILY_BUDGET_USD)
        
        # Drawdown config
        self._dd_threshold_reduce = budget_config.get("dd_threshold_reduce", self.DD_THRESHOLD_REDUCE)
        self._dd_threshold_halt = budget_config.get("dd_threshold_halt", self.DD_THRESHOLD_HALT)
        self._dd_min_mult = budget_config.get("dd_min_multiplier", self.DD_MIN_MULTIPLIER)
        
        # Performance config
        self._perf_enabled = budget_config.get("performance_scaling_enabled", False)
        self._perf_max_boost = budget_config.get("perf_max_boost", self.PERF_MAX_BOOST)
        
        self._logger.log("dynamic_budget_init", {
            "enabled": self._enabled,
            "daily_budget_pct": self._daily_budget_pct,
            "max_position_pct": self._max_position_pct,
            "dd_threshold_reduce": self._dd_threshold_reduce
        })
    
    def calculate_budget(
        self,
        bot_id: str,
        equity: float,
        high_water_mark: float,
        bot_performance: Optional[BotPerformance] = None
    ) -> BudgetAllocation:
        """
        Calculate dynamic budget for a bot.
        
        Args:
            bot_id: Identifier for the bot
            equity: Current account equity
            high_water_mark: Peak equity for drawdown calculation
            bot_performance: Optional rolling performance metrics
            
        Returns:
            BudgetAllocation with computed limits
        """
        if not self._enabled or equity <= 0:
            return self._fallback_budget(bot_id, equity, "disabled")
        
        # Step 1: Base budget from equity percentage
        base_daily = equity * (self._daily_budget_pct / 100)
        base_position = equity * (self._max_position_pct / 100)
        equity_mult = 1.0
        
        # Step 2: Drawdown adjustment
        drawdown_pct = self._calculate_drawdown(equity, high_water_mark)
        dd_mult = self._calculate_drawdown_multiplier(drawdown_pct)
        
        # Step 3: Performance adjustment (optional)
        perf_mult = 1.0
        if self._perf_enabled and bot_performance:
            perf_mult = self._calculate_performance_multiplier(bot_performance)
        
        # Step 4: Combine multipliers
        final_mult = equity_mult * dd_mult * perf_mult
        final_mult = max(0.1, min(2.0, final_mult))  # Clamp
        
        # Step 5: Apply and constrain
        daily_budget = base_daily * final_mult
        max_position = base_position * final_mult
        
        # Apply floor/ceiling
        daily_budget = max(self._min_daily_budget, min(self._max_daily_budget, daily_budget))
        max_position = max(15.0, min(daily_budget * 0.5, max_position))  # Position <= 50% of daily
        
        # Determine reason
        reason = self._determine_reason(dd_mult, perf_mult, drawdown_pct)
        
        result = BudgetAllocation(
            daily_budget_usd=round(daily_budget, 2),
            max_position_usd=round(max_position, 2),
            equity_multiplier=equity_mult,
            drawdown_multiplier=dd_mult,
            performance_multiplier=perf_mult,
            final_multiplier=final_mult,
            reason=reason,
            constraints={
                "equity": equity,
                "high_water_mark": high_water_mark,
                "drawdown_pct": round(drawdown_pct * 100, 2),
                "base_daily": round(base_daily, 2),
                "base_position": round(base_position, 2)
            }
        )
        
        self._logger.log("dynamic_budget_calculated", {
            "bot_id": bot_id,
            "daily_budget_usd": result.daily_budget_usd,
            "max_position_usd": result.max_position_usd,
            "drawdown_pct": result.constraints["drawdown_pct"],
            "dd_mult": dd_mult,
            "perf_mult": perf_mult,
            "final_mult": final_mult,
            "reason": reason
        })
        
        return result
    
    def _calculate_drawdown(self, equity: float, high_water_mark: float) -> float:
        """Calculate current drawdown percentage."""
        if high_water_mark <= 0 or equity >= high_water_mark:
            return 0.0
        return (high_water_mark - equity) / high_water_mark
    
    def _calculate_drawdown_multiplier(self, drawdown_pct: float) -> float:
        """
        Calculate budget multiplier based on drawdown.
        
        Linear interpolation:
        - 0% DD → 1.0x
        - dd_threshold_reduce (5%) → 1.0x (start reducing)
        - dd_threshold_halt (15%) → dd_min_mult (0.5x)
        """
        if drawdown_pct <= self._dd_threshold_reduce:
            return 1.0
        
        if drawdown_pct >= self._dd_threshold_halt:
            return self._dd_min_mult
        
        # Linear interpolation
        range_dd = self._dd_threshold_halt - self._dd_threshold_reduce
        range_mult = 1.0 - self._dd_min_mult
        progress = (drawdown_pct - self._dd_threshold_reduce) / range_dd
        
        return 1.0 - (progress * range_mult)
    
    def _calculate_performance_multiplier(self, perf: BotPerformance) -> float:
        """
        Calculate budget boost based on bot performance.
        
        Requirements for boost:
        - At least PERF_MIN_TRADES trades
        - Win rate > 50%
        - Positive avg return
        - Sharpe > PERF_SHARPE_THRESHOLD
        """
        if perf.trades_30d < self.PERF_MIN_TRADES:
            return 1.0
        
        if perf.win_rate <= 0.5 or perf.avg_return_pct <= 0:
            return 1.0
        
        if perf.sharpe_estimate < self.PERF_SHARPE_THRESHOLD:
            return 1.0
        
        # Scale boost by Sharpe (capped)
        sharpe_excess = perf.sharpe_estimate - self.PERF_SHARPE_THRESHOLD
        boost = 1.0 + (sharpe_excess * 0.25)  # +25% per Sharpe point above threshold
        
        return min(self._perf_max_boost, boost)
    
    def _determine_reason(self, dd_mult: float, perf_mult: float, dd_pct: float) -> str:
        """Determine human-readable reason for budget."""
        reasons = []
        
        if dd_mult < 1.0:
            reasons.append(f"drawdown_reduction_{int(dd_pct*100)}pct")
        
        if perf_mult > 1.0:
            reasons.append(f"performance_boost_{int((perf_mult-1)*100)}pct")
        elif perf_mult < 1.0:
            reasons.append("performance_penalty")
        
        if not reasons:
            return "normal_allocation"
        
        return "_".join(reasons)
    
    def _fallback_budget(self, bot_id: str, equity: float, reason: str) -> BudgetAllocation:
        """Return safe fallback budget when disabled or error."""
        daily = min(200.0, equity * 0.01) if equity > 0 else 100.0
        return BudgetAllocation(
            daily_budget_usd=daily,
            max_position_usd=daily * 0.5,
            equity_multiplier=1.0,
            drawdown_multiplier=1.0,
            performance_multiplier=1.0,
            final_multiplier=1.0,
            reason=f"fallback_{reason}",
            constraints={"equity": equity}
        )
    
    def get_high_water_mark(self) -> float:
        """Get stored high water mark from state."""
        hwm = get_state("dynamic_budget.high_water_mark")
        return float(hwm) if hwm else 0.0
    
    def update_high_water_mark(self, equity: float) -> float:
        """Update high water mark if equity is new peak."""
        current_hwm = self.get_high_water_mark()
        if equity > current_hwm:
            set_state("dynamic_budget.high_water_mark", str(equity))
            self._logger.log("hwm_updated", {
                "old_hwm": current_hwm,
                "new_hwm": equity
            })
            return equity
        return current_hwm


# Singleton
_dynamic_budget_manager: Optional[DynamicBudgetManager] = None


def get_dynamic_budget_manager() -> DynamicBudgetManager:
    """Get or create DynamicBudgetManager singleton."""
    global _dynamic_budget_manager
    if _dynamic_budget_manager is None:
        _dynamic_budget_manager = DynamicBudgetManager()
    return _dynamic_budget_manager
