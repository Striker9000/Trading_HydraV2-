"""
Greek Risk Limits - Portfolio-level delta and gamma exposure management.

Institutional-grade risk controls for options portfolios:
1. Delta limits: Cap net directional exposure as % of equity
2. Gamma limits: Cap acceleration risk (rate of delta change)
3. Vega limits: Cap volatility exposure
4. Theta monitoring: Track daily time decay

Delta is expressed as "shares equivalent" - a delta of 100 means exposure 
equivalent to 100 shares of the underlying. For a $43k account with SPY at $500,
a 20% delta limit would be ~$8,600 of directional exposure (~17 SPY shares equiv).

Gamma is expressed in absolute terms - it's the rate of delta change per $1 move.
High gamma means delta changes rapidly, creating "gamma scalping" opportunities
but also acceleration risk on large moves.
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple
from enum import Enum

from ..core.logging import get_logger
from ..core.config import load_bots_config
from ..core.clock import get_market_clock


class GreekLimitStatus(Enum):
    """Status of Greek limit check"""
    WITHIN_LIMITS = "within_limits"
    NEAR_LIMIT = "near_limit"       # Within 80% of limit
    AT_LIMIT = "at_limit"           # Within 95% of limit
    BREACHED = "breached"           # Over limit


@dataclass
class GreekLimits:
    """Greek risk limits configuration"""
    max_delta_pct_of_equity: float = 20.0      # Max delta as % of equity
    max_gamma_per_dollar: float = 5.0          # Max gamma per $1 underlying move
    max_vega_per_iv_point: float = 500.0       # Max vega per 1% IV change
    max_theta_daily_usd: float = 100.0         # Max daily theta decay (negative)
    delta_warning_pct: float = 80.0            # Warn at 80% of limit
    gamma_warning_pct: float = 80.0


@dataclass
class GreekExposure:
    """Current Greek exposure snapshot"""
    total_delta: float = 0.0          # Net directional exposure (shares equiv)
    total_gamma: float = 0.0          # Rate of delta change per $1
    total_theta: float = 0.0          # Daily time decay (USD)
    total_vega: float = 0.0           # IV sensitivity per 1%
    delta_dollar_exposure: float = 0.0  # Delta in dollar terms
    long_delta: float = 0.0           # Gross long delta
    short_delta: float = 0.0          # Gross short delta
    position_count: int = 0


@dataclass
class GreekLimitResult:
    """Result of a Greek limit check"""
    can_trade: bool
    delta_status: GreekLimitStatus
    gamma_status: GreekLimitStatus
    delta_utilization_pct: float
    gamma_utilization_pct: float
    reason: str = ""
    exposure: Optional[GreekExposure] = None


class GreekRiskMonitor:
    """
    Monitor and enforce portfolio-level Greek limits.
    
    Provides real-time tracking of options Greek exposure and gates
    new entries when limits are approached or breached.
    """
    
    def __init__(self):
        self._logger = get_logger()
        self._enabled = True  # Default, updated by _load_limits
        self._limits = self._load_limits()
        self._last_exposure: Optional[GreekExposure] = None
        self._last_check_time: Optional[str] = None
    
    def _load_limits(self) -> GreekLimits:
        """Load Greek limits from config"""
        try:
            config = load_bots_config()
            greek_config = config.get("greek_limits", {})
            
            # Check if enabled
            self._enabled = greek_config.get("enabled", True)
            
            return GreekLimits(
                max_delta_pct_of_equity=greek_config.get("max_delta_pct_of_equity", 20.0),
                max_gamma_per_dollar=greek_config.get("max_gamma_per_dollar", 5.0),
                max_vega_per_iv_point=greek_config.get("max_vega_per_iv_point", 500.0),
                max_theta_daily_usd=greek_config.get("max_theta_daily_usd", 100.0),
                delta_warning_pct=greek_config.get("delta_warning_pct", 80.0),
                gamma_warning_pct=greek_config.get("gamma_warning_pct", 80.0),
            )
        except Exception as e:
            self._logger.error(f"Greek limits config error, using defaults: {e}")
            self._enabled = True
            return GreekLimits()
    
    def check_limits(
        self, 
        portfolio_greeks: Dict[str, float],
        equity: float,
        underlying_price: float = 500.0  # Default SPY price for dollar conversion
    ) -> GreekLimitResult:
        """
        Check if current Greek exposure is within limits.
        
        Args:
            portfolio_greeks: Dict with total_delta, total_gamma, total_theta, total_vega
                              NOTE: total_delta should already be in dollar terms if available
                              as delta_dollar_exposure. Otherwise we convert using underlying_price.
            equity: Current account equity for percentage calculations
            underlying_price: Reference price for delta-dollar conversion (default SPY)
            
        Returns:
            GreekLimitResult with status and utilization metrics
        """
        # If disabled, always return can_trade=True
        if not self._enabled:
            return GreekLimitResult(
                can_trade=True,
                delta_status=GreekLimitStatus.WITHIN_LIMITS,
                gamma_status=GreekLimitStatus.WITHIN_LIMITS,
                delta_utilization_pct=0.0,
                gamma_utilization_pct=0.0,
                reason="Greek limits disabled"
            )
        
        if equity <= 0:
            return GreekLimitResult(
                can_trade=False,
                delta_status=GreekLimitStatus.BREACHED,
                gamma_status=GreekLimitStatus.BREACHED,
                delta_utilization_pct=100.0,
                gamma_utilization_pct=100.0,
                reason="Invalid equity value"
            )
        
        total_delta = portfolio_greeks.get("total_delta", 0.0)
        total_gamma = portfolio_greeks.get("total_gamma", 0.0)
        total_theta = portfolio_greeks.get("total_theta", 0.0)
        total_vega = portfolio_greeks.get("total_vega", 0.0)
        
        # Calculate delta in dollar terms
        # Use pre-computed delta_dollar if available (per-underlying accurate)
        # Otherwise fall back to single underlying_price conversion
        delta_dollar = portfolio_greeks.get("delta_dollar", 0.0)
        if delta_dollar == 0.0 and total_delta != 0.0:
            # Fallback: convert using single reference price
            delta_dollar = abs(total_delta) * underlying_price
        delta_pct_of_equity = (delta_dollar / equity) * 100.0
        
        # Calculate limit utilization
        max_delta_dollars = (self._limits.max_delta_pct_of_equity / 100.0) * equity
        delta_utilization = (delta_dollar / max_delta_dollars * 100.0) if max_delta_dollars > 0 else 0
        
        gamma_utilization = (abs(total_gamma) / self._limits.max_gamma_per_dollar * 100.0) if self._limits.max_gamma_per_dollar > 0 else 0
        
        # Determine status
        delta_status = self._get_status(delta_utilization)
        gamma_status = self._get_status(gamma_utilization)
        
        # Build exposure snapshot
        exposure = GreekExposure(
            total_delta=total_delta,
            total_gamma=total_gamma,
            total_theta=total_theta,
            total_vega=total_vega,
            delta_dollar_exposure=delta_dollar,
            long_delta=max(0, total_delta),
            short_delta=min(0, total_delta),
            position_count=int(portfolio_greeks.get("position_count", 0))
        )
        
        self._last_exposure = exposure
        self._last_check_time = get_market_clock().now().isoformat()
        
        # Determine if we can trade
        can_trade = (
            delta_status != GreekLimitStatus.BREACHED and
            gamma_status != GreekLimitStatus.BREACHED
        )
        
        reason = ""
        if not can_trade:
            reasons = []
            if delta_status == GreekLimitStatus.BREACHED:
                reasons.append(f"delta {delta_utilization:.0f}% of limit")
            if gamma_status == GreekLimitStatus.BREACHED:
                reasons.append(f"gamma {gamma_utilization:.0f}% of limit")
            reason = "Greek limits breached: " + ", ".join(reasons)
        
        result = GreekLimitResult(
            can_trade=can_trade,
            delta_status=delta_status,
            gamma_status=gamma_status,
            delta_utilization_pct=delta_utilization,
            gamma_utilization_pct=gamma_utilization,
            reason=reason,
            exposure=exposure
        )
        
        # Log if at or near limits
        if delta_status in (GreekLimitStatus.NEAR_LIMIT, GreekLimitStatus.AT_LIMIT, GreekLimitStatus.BREACHED):
            self._logger.log("greek_delta_warning", {
                "status": delta_status.value,
                "utilization_pct": round(delta_utilization, 1),
                "delta_dollar": round(delta_dollar, 2),
                "equity": round(equity, 2),
                "limit_pct": self._limits.max_delta_pct_of_equity
            })
        
        if gamma_status in (GreekLimitStatus.NEAR_LIMIT, GreekLimitStatus.AT_LIMIT, GreekLimitStatus.BREACHED):
            self._logger.log("greek_gamma_warning", {
                "status": gamma_status.value,
                "utilization_pct": round(gamma_utilization, 1),
                "total_gamma": round(total_gamma, 4),
                "limit": self._limits.max_gamma_per_dollar
            })
        
        return result
    
    def _get_status(self, utilization_pct: float) -> GreekLimitStatus:
        """Convert utilization percentage to status"""
        if utilization_pct >= 100.0:
            return GreekLimitStatus.BREACHED
        elif utilization_pct >= 95.0:
            return GreekLimitStatus.AT_LIMIT
        elif utilization_pct >= 80.0:
            return GreekLimitStatus.NEAR_LIMIT
        return GreekLimitStatus.WITHIN_LIMITS
    
    def can_add_position(
        self,
        portfolio_greeks: Dict[str, float],
        new_position_greeks: Dict[str, float],
        equity: float,
        underlying_price: float = 500.0
    ) -> Tuple[bool, str]:
        """
        Check if adding a new position would breach Greek limits.
        
        Args:
            portfolio_greeks: Current portfolio Greeks
            new_position_greeks: Greeks of the proposed new position
            equity: Account equity
            underlying_price: Reference price for delta conversion
            
        Returns:
            Tuple of (can_add, reason)
        """
        # Calculate pro-forma Greeks (current + new)
        pro_forma = {
            "total_delta": portfolio_greeks.get("total_delta", 0) + new_position_greeks.get("delta", 0),
            "total_gamma": portfolio_greeks.get("total_gamma", 0) + new_position_greeks.get("gamma", 0),
            "total_theta": portfolio_greeks.get("total_theta", 0) + new_position_greeks.get("theta", 0),
            "total_vega": portfolio_greeks.get("total_vega", 0) + new_position_greeks.get("vega", 0),
        }
        
        result = self.check_limits(pro_forma, equity, underlying_price)
        
        if not result.can_trade:
            self._logger.log("greek_limit_blocked_entry", {
                "reason": result.reason,
                "delta_util": round(result.delta_utilization_pct, 1),
                "gamma_util": round(result.gamma_utilization_pct, 1),
                "new_delta": new_position_greeks.get("delta", 0),
                "new_gamma": new_position_greeks.get("gamma", 0)
            })
            return False, result.reason
        
        # Warn if position would push us near limits
        if result.delta_status == GreekLimitStatus.NEAR_LIMIT or result.gamma_status == GreekLimitStatus.NEAR_LIMIT:
            self._logger.log("greek_limit_near_after_trade", {
                "delta_util": round(result.delta_utilization_pct, 1),
                "gamma_util": round(result.gamma_utilization_pct, 1)
            })
        
        return True, ""
    
    def get_exposure_summary(self) -> Dict[str, Any]:
        """Get current Greek exposure summary for logging/monitoring"""
        if not self._last_exposure:
            return {"status": "no_data"}
        
        exp = self._last_exposure
        return {
            "total_delta": round(exp.total_delta, 2),
            "total_gamma": round(exp.total_gamma, 4),
            "total_theta": round(exp.total_theta, 2),
            "total_vega": round(exp.total_vega, 2),
            "delta_dollar": round(exp.delta_dollar_exposure, 2),
            "position_count": exp.position_count,
            "last_check": self._last_check_time
        }
    
    def get_limits(self) -> Dict[str, float]:
        """Get configured limits"""
        return {
            "max_delta_pct": self._limits.max_delta_pct_of_equity,
            "max_gamma": self._limits.max_gamma_per_dollar,
            "max_vega": self._limits.max_vega_per_iv_point,
            "max_theta": self._limits.max_theta_daily_usd
        }


# Singleton instance
_greek_monitor: Optional[GreekRiskMonitor] = None


def get_greek_risk_monitor() -> GreekRiskMonitor:
    """Get or create the singleton GreekRiskMonitor instance"""
    global _greek_monitor
    if _greek_monitor is None:
        _greek_monitor = GreekRiskMonitor()
    return _greek_monitor
