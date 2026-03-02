"""
Heuristic Position Sizing Module v1 - Dynamic Position Sizing.

NOTE: Previously labeled "BlackRock-style" - renamed for honesty.
This is a heuristic sizing system that needs validation through backtesting.

Implements:
- Volatility-adjusted position sizing using ATR
- Fractional Kelly criterion based on ML probability
- Regime-aware multipliers
- Account equity scaling
- Maximum exposure limits

These are heuristics that should be validated, not proven edge.
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from datetime import datetime
import math

from ..core.logging import get_logger
from ..core.config import load_settings
from ..core.state import get_state

# Default b-ratio (win/loss ratio) if no historical data available
DEFAULT_KELLY_B_RATIO = 1.5

# Cache for historical b-ratio to avoid repeated expensive calculations
_cached_b_ratio: Optional[float] = None
_b_ratio_cache_time: float = 0.0
B_RATIO_CACHE_TTL_SECONDS = 3600  # Refresh hourly


@dataclass
class PositionSizeResult:
    """Result of position sizing calculation."""
    notional: float
    qty: float
    base_notional: float
    volatility_adjustment: float
    kelly_adjustment: float
    regime_adjustment: float
    equity_adjustment: float
    correlation_adjustment: float
    final_multiplier: float
    sizing_reason: str
    risk_metrics: Dict[str, Any]


class InstitutionalPositionSizer:
    """
    Heuristic position sizing v1.
    
    NOTE: This is a heuristic system, not proven institutional-grade sizing.
    All multipliers should be validated through forward testing.
    
    Replaces fixed notional with dynamic sizing based on:
    1. Account equity (% of NAV)
    2. Asset volatility (ATR-adjusted)
    3. ML signal confidence (fractional Kelly)
    4. Market regime (VIX-based)
    5. Correlation exposure (reduce when concentrated)
    """
    
    # QUANT-OPTIMIZED: Increased for $500/day target
    # Previous values were too conservative (0.5%, $15 min, 3% max)
    BASE_RISK_PCT = 2.0           # 2% of equity per trade = ~$948 on $47k account
    MIN_NOTIONAL = 500.0          # $500 minimum position - every trade must be meaningful
    MAX_SINGLE_POSITION_PCT = 8.0 # 8% max position = ~$3,800 cap
    KELLY_FRACTION = 0.35         # 35% Kelly (was 25%) - more aggressive
    
    def __init__(self):
        self._logger = get_logger()
        self._settings = load_settings()
        
        sizing_config = self._settings.get("institutional_sizing", {})
        self._enabled = sizing_config.get("enabled", True)
        self._base_risk_pct = sizing_config.get("base_risk_pct", self.BASE_RISK_PCT)
        self._max_position_pct = sizing_config.get("max_single_position_pct", self.MAX_SINGLE_POSITION_PCT)
        self._kelly_fraction = sizing_config.get("kelly_fraction", self.KELLY_FRACTION)
        self._min_notional = sizing_config.get("min_notional", self.MIN_NOTIONAL)
        
        self._logger.log("position_sizer_init", {
            "enabled": self._enabled,
            "base_risk_pct": self._base_risk_pct,
            "max_position_pct": self._max_position_pct,
            "kelly_fraction": self._kelly_fraction
        })
    
    def calculate_position_size(
        self,
        symbol: str,
        side: str,
        current_price: float,
        equity: float,
        ml_probability: float = 0.5,
        ml_confidence: float = 0.5,
        atr: Optional[float] = None,
        volatility_pct: Optional[float] = None,
        regime_multiplier: float = 1.0,
        drawdown_multiplier: float = 1.0,
        correlation_exposure: float = 0.0,
        max_daily_loss: Optional[float] = None,
        asset_class: str = "crypto",
        vix: Optional[float] = None
    ) -> PositionSizeResult:
        """
        Calculate optimal position size using institutional methods.
        
        Args:
            symbol: Asset symbol
            side: "buy" or "sell"/"short"
            current_price: Current market price
            equity: Total account equity
            ml_probability: ML model's profit probability (0.0-1.0)
            ml_confidence: ML model's confidence in prediction
            atr: Average True Range for volatility
            volatility_pct: Alternative volatility measure (%)
            regime_multiplier: From RegimeSizer (0.0-1.5)
            drawdown_multiplier: From DrawdownPredictor (0.0-1.0)
            correlation_exposure: Current correlated exposure (0.0-1.0)
            max_daily_loss: Max daily loss budget (optional)
            asset_class: "crypto", "stock", or "option"
            
        Returns:
            PositionSizeResult with calculated notional and breakdown
        """
        if not self._enabled:
            default_notional = max(self._min_notional, max_daily_loss * 0.8) if max_daily_loss else 50.0
            return PositionSizeResult(
                notional=default_notional,
                qty=default_notional / current_price if current_price > 0 else 0,
                base_notional=default_notional,
                volatility_adjustment=1.0,
                kelly_adjustment=1.0,
                regime_adjustment=1.0,
                equity_adjustment=1.0,
                correlation_adjustment=1.0,
                final_multiplier=1.0,
                sizing_reason="institutional_sizing_disabled",
                risk_metrics={}
            )
        
        base_notional = equity * (self._base_risk_pct / 100)
        
        self._logger.log("position_size_calc_start", {
            "symbol": symbol,
            "side": side,
            "equity": equity,
            "base_notional": base_notional,
            "ml_probability": ml_probability,
            "regime_multiplier": regime_multiplier
        })
        
        vol_adjustment = self._calculate_volatility_adjustment(
            symbol, current_price, atr, volatility_pct, asset_class
        )
        
        kelly_adjustment = self._calculate_kelly_adjustment(
            ml_probability, ml_confidence, vix
        )
        
        regime_adjustment = self._calculate_regime_adjustment(
            regime_multiplier, drawdown_multiplier
        )
        
        equity_adjustment = self._calculate_equity_adjustment(equity)
        
        correlation_adjustment = self._calculate_correlation_adjustment(
            correlation_exposure
        )
        
        final_multiplier = (
            vol_adjustment *
            kelly_adjustment *
            regime_adjustment *
            equity_adjustment *
            correlation_adjustment
        )
        
        final_multiplier = max(0.1, min(2.0, final_multiplier))
        
        notional = base_notional * final_multiplier
        
        max_notional = equity * (self._max_position_pct / 100)
        if notional > max_notional:
            notional = max_notional
            sizing_reason = "capped_at_max_position_pct"
        else:
            # Dynamic floor: max(config_min, equity * 0.01) - scales with account growth
            dynamic_min = max(self._min_notional, equity * 0.01)
            if notional < dynamic_min:
                notional = dynamic_min
                sizing_reason = f"floored_at_dynamic_min_{dynamic_min:.0f}"
            else:
                sizing_reason = "optimal_kelly_volatility_adjusted"
        
        if max_daily_loss and notional > max_daily_loss:
            notional = max_daily_loss
            sizing_reason = "capped_at_daily_budget"
        
        qty = notional / current_price if current_price > 0 else 0
        
        if asset_class == "crypto":
            qty = round(qty, 8)
        elif asset_class == "stock":
            qty = max(1, int(qty))
            notional = qty * current_price
        
        result = PositionSizeResult(
            notional=round(notional, 2),
            qty=qty,
            base_notional=round(base_notional, 2),
            volatility_adjustment=round(vol_adjustment, 4),
            kelly_adjustment=round(kelly_adjustment, 4),
            regime_adjustment=round(regime_adjustment, 4),
            equity_adjustment=round(equity_adjustment, 4),
            correlation_adjustment=round(correlation_adjustment, 4),
            final_multiplier=round(final_multiplier, 4),
            sizing_reason=sizing_reason,
            risk_metrics={
                "implied_risk_pct": round(notional / equity * 100, 3) if equity > 0 else 0,
                "kelly_full": round(self._full_kelly(ml_probability, ml_confidence), 4),
                "kelly_used": round(kelly_adjustment, 4),
                "vol_normalized": round(vol_adjustment, 4),
                "regime_score": round(regime_multiplier, 3),
                "correlation_penalty": round(1 - correlation_adjustment, 3)
            }
        )
        
        self._logger.log("position_size_calc_complete", {
            "symbol": symbol,
            "notional": result.notional,
            "qty": result.qty,
            "final_multiplier": result.final_multiplier,
            "sizing_reason": result.sizing_reason,
            "adjustments": {
                "volatility": result.volatility_adjustment,
                "kelly": result.kelly_adjustment,
                "regime": result.regime_adjustment,
                "equity": result.equity_adjustment,
                "correlation": result.correlation_adjustment
            }
        })
        
        return result
    
    def _calculate_volatility_adjustment(
        self,
        symbol: str,
        current_price: float,
        atr: Optional[float],
        volatility_pct: Optional[float],
        asset_class: str
    ) -> float:
        """
        Adjust position size inversely with volatility.
        Higher volatility = smaller position (risk parity approach).
        """
        target_vol = {
            "crypto": 3.0,
            "stock": 1.5,
            "option": 5.0
        }.get(asset_class, 2.0)
        
        if atr and current_price > 0:
            actual_vol = (atr / current_price) * 100
        elif volatility_pct:
            actual_vol = volatility_pct
        else:
            actual_vol = target_vol
        
        if actual_vol <= 0:
            return 1.0
        
        vol_ratio = target_vol / actual_vol
        
        adjustment = max(0.3, min(1.5, vol_ratio))
        
        self._logger.log("volatility_adjustment", {
            "symbol": symbol,
            "target_vol": target_vol,
            "actual_vol": round(actual_vol, 3),
            "adjustment": round(adjustment, 4)
        })
        
        return adjustment
    
    def _get_historical_b_ratio(self) -> float:
        """
        Get actual win/loss ratio (b) from historical trade performance.
        
        Kelly b-ratio = avg_win / avg_loss
        This is critical for proper Kelly sizing - using hardcoded values
        leads to over/under-betting.
        
        Uses hourly cache to avoid expensive recalculation on every sizing call.
        Returns DEFAULT_KELLY_B_RATIO if insufficient historical data.
        """
        import time
        global _cached_b_ratio, _b_ratio_cache_time
        
        # Check cache validity
        now = time.time()
        if _cached_b_ratio is not None and (now - _b_ratio_cache_time) < B_RATIO_CACHE_TTL_SECONDS:
            return _cached_b_ratio
        
        try:
            # Try to get performance analytics
            from ..ml.performance_analytics import get_performance_analytics
            analytics = get_performance_analytics()
            metrics = analytics.calculate_metrics(period_days=30)  # Last 30 days
            
            if metrics and metrics.total_trades >= 10:
                # Calculate b-ratio directly from raw values to avoid precision loss
                if metrics.avg_loss > 0:
                    b_ratio = metrics.avg_win / metrics.avg_loss
                else:
                    b_ratio = DEFAULT_KELLY_B_RATIO
                    
                # Sanity check: b should be positive and reasonable
                if 0.5 <= b_ratio <= 5.0:
                    self._logger.log("kelly_b_ratio_from_history", {
                        "b_ratio": round(b_ratio, 4),
                        "total_trades": metrics.total_trades,
                        "avg_win": round(metrics.avg_win, 2),
                        "avg_loss": round(metrics.avg_loss, 2),
                        "cached_for_seconds": B_RATIO_CACHE_TTL_SECONDS
                    })
                    _cached_b_ratio = b_ratio
                    _b_ratio_cache_time = now
                    return b_ratio
            
            # Not enough data or unreasonable ratio - cache the default too
            _cached_b_ratio = DEFAULT_KELLY_B_RATIO
            _b_ratio_cache_time = now
            return DEFAULT_KELLY_B_RATIO
            
        except Exception as e:
            self._logger.warn(f"Could not get historical b-ratio: {e}, using default")
            _cached_b_ratio = DEFAULT_KELLY_B_RATIO
            _b_ratio_cache_time = now
            return DEFAULT_KELLY_B_RATIO
    
    def _calculate_kelly_adjustment(
        self,
        ml_probability: float,
        ml_confidence: float,
        vix: Optional[float] = None
    ) -> float:
        """
        Calculate Kelly criterion-based position sizing.
        
        Kelly formula: f* = (p * b - q) / b
        where p = win probability, q = loss probability, b = win/loss ratio
        
        FIXED: Now uses actual historical b-ratio instead of hardcoded value.
        IMPROVED: Adjusts Kelly fraction based on VIX regime.
        """
        p = max(0.01, min(0.99, ml_probability))
        q = 1 - p
        
        b = self._get_historical_b_ratio()
        
        full_kelly = (p * b - q) / b
        
        if full_kelly <= 0:
            return 0.5
        
        regime_adjusted_fraction = self._get_regime_kelly_fraction(vix)
        fractional_kelly = full_kelly * regime_adjusted_fraction
        
        confidence_adj = 0.5 + (ml_confidence * 0.5)
        adjusted_kelly = fractional_kelly * confidence_adj
        
        final_adjustment = 0.5 + adjusted_kelly
        
        return max(0.3, min(1.5, final_adjustment))
    
    def _get_regime_kelly_fraction(self, vix: Optional[float]) -> float:
        """
        Get Kelly fraction adjusted for volatility regime.
        
        Higher VIX = more uncertainty = smaller Kelly fraction.
        This prevents over-betting during stressed markets.
        """
        if vix is None:
            return self._kelly_fraction
        
        if vix < 15:
            multiplier = 1.2
        elif vix < 20:
            multiplier = 1.0
        elif vix < 25:
            multiplier = 0.75
        elif vix < 30:
            multiplier = 0.5
        else:
            multiplier = 0.25
        
        adjusted = self._kelly_fraction * multiplier
        return max(0.1, min(0.5, adjusted))
    
    def _full_kelly(self, p: float, confidence: float) -> float:
        """Calculate full Kelly for logging/analysis."""
        p = max(0.01, min(0.99, p))
        q = 1 - p
        b = self._get_historical_b_ratio()
        return max(0, (p * b - q) / b)
    
    def _calculate_regime_adjustment(
        self,
        regime_multiplier: float,
        drawdown_multiplier: float
    ) -> float:
        """
        Combine regime and drawdown multipliers.
        Conservative: use lower of the two when both indicate risk.
        """
        if regime_multiplier < 0.5 or drawdown_multiplier < 0.5:
            return min(regime_multiplier, drawdown_multiplier)
        
        combined = (regime_multiplier + drawdown_multiplier) / 2
        
        return max(0.1, min(1.5, combined))
    
    def _calculate_equity_adjustment(self, equity: float) -> float:
        """
        Adjust for account size - smaller accounts more conservative.
        Larger accounts can take slightly larger relative positions.
        """
        if equity < 5000:
            return 0.7
        elif equity < 10000:
            return 0.85
        elif equity < 25000:
            return 1.0
        elif equity < 50000:
            return 1.1
        elif equity < 100000:
            return 1.15
        else:
            return 1.2
    
    def _calculate_correlation_adjustment(
        self,
        correlation_exposure: float
    ) -> float:
        """
        Reduce position size when highly correlated with existing positions.
        correlation_exposure: 0.0 (uncorrelated) to 1.0 (fully correlated)
        """
        if correlation_exposure <= 0.2:
            return 1.0
        elif correlation_exposure <= 0.4:
            return 0.9
        elif correlation_exposure <= 0.6:
            return 0.75
        elif correlation_exposure <= 0.8:
            return 0.5
        else:
            return 0.25


_position_sizer: Optional[InstitutionalPositionSizer] = None


def get_position_sizer() -> InstitutionalPositionSizer:
    """Get or create singleton position sizer."""
    global _position_sizer
    if _position_sizer is None:
        _position_sizer = InstitutionalPositionSizer()
    return _position_sizer


# =============================================================================
# GROWTH MULTIPLIER (sqrt model from sizing.yaml)
# =============================================================================

def compute_growth_multiplier(equity: float, regime: str = "NORMAL") -> float:
    """
    Compute account growth multiplier using sqrt model.
    
    Formula: growth_mult = clamp((equity/baseline)**0.5, min, max)
    In STRESS regime, caps at 1.0 (don't size up during chaos).
    
    Args:
        equity: Current account equity
        regime: Current regime ("LOW", "NORMAL", "STRESS")
        
    Returns:
        Growth multiplier (0.75 to 1.50, or capped at 1.0 in STRESS)
    """
    import os
    import yaml
    
    # Load sizing config
    try:
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "config", "sizing.yaml"
        )
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
    except Exception:
        config = {}
    
    baseline = config.get("baseline_equity", 5000)
    min_mult = config.get("min_multiplier", 0.75)
    max_mult = config.get("max_multiplier", 1.50)
    stress_cap = config.get("stress_cap", 1.0)
    model = config.get("model", "sqrt")
    
    if baseline <= 0:
        baseline = equity  # Fallback: no growth effect
    if equity <= 0:
        return 1.0
    
    if model == "sqrt":
        raw = math.sqrt(equity / baseline)
    else:
        raw = equity / baseline
    
    multiplier = max(min_mult, min(max_mult, raw))
    
    # Cap at 1.0 in STRESS regime
    if regime == "STRESS":
        multiplier = min(multiplier, stress_cap)
    
    return multiplier


def get_regime_size_multiplier(regime: str) -> float:
    """
    Get size multiplier from regime config.
    
    Args:
        regime: Current regime ("LOW", "NORMAL", "STRESS")
        
    Returns:
        Size multiplier from regimes.yaml
    """
    import os
    import yaml
    
    try:
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "config", "regimes.yaml"
        )
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
    except Exception:
        config = {}
    
    modifiers = config.get("modifiers", {}).get(regime, {})
    return modifiers.get("size_multiplier", 1.0)
