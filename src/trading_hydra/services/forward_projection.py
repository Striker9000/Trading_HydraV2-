"""
ForwardProjectionEngine - Future Price Movement Projections for ExitBot v2 Elite
====================================================================================

This module projects future price movement to help exit decisions by:
1. Calculating expected price ranges based on ATR
2. Modeling theta decay for options positions
3. Computing continuation probability from historical patterns
4. Calculating expected value (EV) for position management

Key Concepts:
- Sigma Ranges: 1-sigma and 2-sigma bands expand with time horizon
- Theta Decay: Projects premium decay over holding period
- Continuation Probability: Based on MFE/MAE historical fingerprints
- Expected Value: Win probability weighted by win/loss sizes

This engine answers questions like:
- "Where is price likely to be in 2 hours?"
- "Will theta decay kill this options trade?"
- "What's the probability this reaches the target vs the stop?"
- "What's the expected value of holding vs exiting now?"
"""

from typing import Dict, Any, Optional, Tuple, List
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math
import statistics

from ..core.logging import get_logger
from .trade_memory import get_trade_memory


@dataclass
class PriceRangeProjection:
    """
    Projected price range for a given time horizon.
    
    Uses ATR (Average True Range) to estimate volatility-based price bands.
    """
    current_price: float
    hours_ahead: float
    
    # Expected range (conservative mid-point)
    expected_price: float
    
    # 1-sigma range (68% confidence)
    sigma1_low: float
    sigma1_high: float
    sigma1_range: float
    
    # 2-sigma range (95% confidence)
    sigma2_low: float
    sigma2_high: float
    sigma2_range: float
    
    # ATR-based inputs
    atr_value: float
    atr_hourly: float  # ATR scaled to hourly movement
    time_adjustment: float  # Volatility expansion factor for time
    
    # Metadata
    confidence: float  # How confident we are (0.0-1.0)
    notes: str


@dataclass
class ThetaDecayProjection:
    """
    Projection of theta decay impact on an options position.
    """
    position_type: str  # "long_call", "long_put", "short_call", "short_put", "straddle", etc.
    current_premium: float  # Current option premium (price)
    theta_per_day: float  # Theta decay per day (negative = decay)
    
    hours_ahead: float
    
    # Projected decay
    projected_premium: float  # Estimated premium at time horizon
    theta_cost_pct: float  # Percentage cost of theta decay
    theta_cost_dollars: float  # Absolute cost
    
    # Breakeven analysis
    breakeven_movement_pct: float  # Price movement needed to overcome theta
    breakeven_movement_dollars: float
    
    # Risk flags
    theta_dominates: bool  # True if theta cost > expected price movement
    is_critical: bool  # True if theta decay is very high
    
    # Metadata
    confidence: float
    notes: str


@dataclass
class ContinuationProbability:
    """
    Probability of trade continuation based on historical patterns.
    """
    current_gain_pct: float
    target_gain_pct: float
    stop_loss_pct: float
    hold_minutes: int
    
    # Probability estimates (0.0 to 1.0)
    prob_reach_target: float  # P(price reaches target first)
    prob_reach_stop: float    # P(price reaches stop first)
    prob_stall_current: float  # P(trade stalls around current price)
    prob_continue: float      # P(trade continues to move)
    
    # Expected outcome
    expected_pnl_pct: float  # Win * prob_reach_target + loss * prob_reach_stop
    
    # Historical context
    historical_trades_analyzed: int
    mfe_mae_matched: bool  # Did we find matching historical patterns?
    
    # Metadata
    confidence: float  # Based on sample size and pattern strength
    notes: str


@dataclass
class ExpectedValueCalculation:
    """
    Expected value analysis for position management.
    
    EV = P(win) * AvgWin$ - P(loss) * AvgLoss$
    Positive EV = keep position, Negative EV = exit position
    """
    win_probability: float  # P(win) from 0.0 to 1.0
    avg_win_pct: float  # Average win size when winning
    avg_loss_pct: float  # Average loss size when losing (as positive %)
    
    # EV calculation
    ev_pct: float  # Expected value as percentage of current position
    ev_is_positive: bool  # True if EV > 0
    
    # Alternative calculations
    win_rate_needed_for_breakeven: float  # Min P(win) needed for EV=0
    kelly_fraction: float  # Kelly criterion optimal sizing
    
    # Historical vs estimated
    using_historical_data: bool  # True if P(win), AvgWin, AvgLoss from historical
    historical_trades_analyzed: int
    
    # Metadata
    confidence: float
    notes: str


@dataclass
class ProjectionResult:
    """
    Complete projection result combining all analyses.
    
    This is the main output of ForwardProjectionEngine, used by ExitBot
    to make holistic exit decisions.
    """
    timestamp: datetime
    
    # Position info
    symbol: str
    strategy: str
    position_side: str  # "long" or "short"
    entry_price: float
    current_price: float
    current_gain_pct: float
    
    # Price range projection
    price_range: PriceRangeProjection
    
    # Synthesis: What should ExitBot do?
    recommendation: str  # "hold", "tighten_stop", "scale_out", "exit_now"
    recommendation_confidence: float
    recommendation_reason: str
    
    # Metadata
    time_horizon_minutes: int
    analysis_quality: str  # "high", "medium", "low"
    
    # Theta analysis (for options) - optional
    theta_projection: Optional[ThetaDecayProjection] = None
    
    # Continuation probability - optional
    continuation: Optional[ContinuationProbability] = None
    
    # Expected value - optional
    expected_value: Optional[ExpectedValueCalculation] = None
    
    # Raw data for audit trail
    metadata: Dict[str, Any] = field(default_factory=dict)


class ForwardProjectionEngine:
    """
    Engine for projecting future price movement and exit decisions.
    
    Combines ATR-based price projections, theta decay modeling,
    and historical probability analysis to guide ExitBot decisions.
    """
    
    def __init__(self):
        self._logger = get_logger()
        self._trade_memory = get_trade_memory()
        
        # Configuration
        self._atr_multipliers = {
            1: 1.0,    # 1 hour ahead
            4: 2.0,    # 4 hours ahead
            24: 3.2,   # 1 day ahead
        }
        
        self._logger.log("forward_projection_engine_initialized", {})
    
    # =========================================================================
    # MAIN PROJECTION METHOD
    # =========================================================================
    
    def project(
        self,
        symbol: str,
        strategy: str,
        position_side: str,
        entry_price: float,
        current_price: float,
        atr: float,
        theta_per_day: Optional[float] = None,
        current_premium: Optional[float] = None,
        target_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        time_horizon_hours: float = 4.0,
        regime: Optional[str] = None
    ) -> ProjectionResult:
        """
        Project future price movement and exit metrics.
        
        Args:
            symbol: Trading symbol
            strategy: Bot/strategy identifier
            position_side: "long" or "short"
            entry_price: Entry price
            current_price: Current price
            atr: Current ATR value
            theta_per_day: Daily theta decay (options)
            current_premium: Current option premium (options)
            target_price: Profit target
            stop_price: Stop loss
            time_horizon_hours: How far ahead to project
            regime: Market regime for context
            
        Returns:
            ProjectionResult with all analyses
        """
        self._logger.log("forward_projection_start", {
            "symbol": symbol,
            "current_price": round(current_price, 2),
            "gain_pct": round(self._calc_gain_pct(entry_price, current_price, position_side), 2),
            "time_horizon_hours": time_horizon_hours
        })
        
        current_time = datetime.utcnow()
        current_gain_pct = self._calc_gain_pct(entry_price, current_price, position_side)
        
        # 1. Project price range
        price_range = self.project_price_range(
            current_price=current_price,
            atr=atr,
            hours_ahead=time_horizon_hours,
            position_side=position_side
        )
        
        # 2. Project theta impact (if options)
        theta_projection = None
        if theta_per_day is not None and current_premium is not None:
            theta_projection = self.project_theta_impact(
                theta_per_day=theta_per_day,
                premium=current_premium,
                hours_ahead=time_horizon_hours
            )
        
        # 3. Calculate continuation probability
        continuation = None
        target_gain = stop_loss = None
        if target_price and stop_price:
            target_gain = self._calc_gain_pct(current_price, target_price, position_side)
            stop_loss = self._calc_gain_pct(current_price, stop_price, position_side)
            
            continuation = self.calculate_continuation_prob(
                symbol=symbol,
                strategy=strategy,
                current_gain_pct=current_gain_pct,
                target_gain_pct=target_gain,
                stop_loss_pct=abs(stop_loss),
                hold_minutes=int(time_horizon_hours * 60),
                regime=regime
            )
        
        # 4. Calculate expected value
        ev = None
        if continuation:
            ev = self.calculate_expected_value(
                win_prob=continuation.prob_reach_target,
                avg_win_pct=continuation.expected_pnl_pct if continuation.expected_pnl_pct > 0 
                           else target_gain or 1.0,
                avg_loss_pct=abs(stop_loss) if stop_loss else 3.0
            )
        
        # 5. Generate recommendation
        recommendation, confidence, reason = self._synthesize_recommendation(
            current_gain_pct=current_gain_pct,
            price_range=price_range,
            theta_projection=theta_projection,
            continuation=continuation,
            expected_value=ev,
            time_horizon_hours=time_horizon_hours
        )
        
        result = ProjectionResult(
            timestamp=current_time,
            symbol=symbol,
            strategy=strategy,
            position_side=position_side,
            entry_price=entry_price,
            current_price=current_price,
            current_gain_pct=current_gain_pct,
            price_range=price_range,
            theta_projection=theta_projection,
            continuation=continuation,
            expected_value=ev,
            recommendation=recommendation,
            recommendation_confidence=confidence,
            recommendation_reason=reason,
            time_horizon_minutes=int(time_horizon_hours * 60),
            analysis_quality=self._assess_quality(
                has_theta=theta_projection is not None,
                has_continuation=continuation is not None,
                has_ev=ev is not None
            ),
            metadata={
                "atr": atr,
                "regime": regime,
                "analysis_timestamp": current_time.isoformat()
            }
        )
        
        self._logger.log("forward_projection_complete", {
            "symbol": symbol,
            "recommendation": recommendation,
            "confidence": round(confidence, 2),
            "ev_pct": round(ev.ev_pct, 2) if ev else None
        })
        
        return result
    
    # =========================================================================
    # PRICE RANGE PROJECTION (ATR-based)
    # =========================================================================
    
    def project_price_range(
        self,
        current_price: float,
        atr: float,
        hours_ahead: float,
        position_side: str = "long"
    ) -> PriceRangeProjection:
        """
        Project expected price range based on ATR.
        
        Uses square-root-of-time rule to scale ATR to time horizon:
        volatility(T) = volatility(1) * sqrt(T)
        
        Args:
            current_price: Current price
            atr: Average True Range
            hours_ahead: Time horizon in hours
            position_side: "long" or "short"
            
        Returns:
            PriceRangeProjection with sigma bands
        """
        if current_price <= 0 or atr < 0:
            # Fallback for invalid inputs
            return PriceRangeProjection(
                current_price=current_price,
                hours_ahead=hours_ahead,
                expected_price=current_price,
                sigma1_low=current_price * 0.98,
                sigma1_high=current_price * 1.02,
                sigma1_range=current_price * 0.04,
                sigma2_low=current_price * 0.95,
                sigma2_high=current_price * 1.05,
                sigma2_range=current_price * 0.10,
                atr_value=atr,
                atr_hourly=atr,
                time_adjustment=1.0,
                confidence=0.1,
                notes="Invalid inputs - using conservative defaults"
            )
        
        # ATR as % of price (volatility)
        atr_pct = atr / current_price
        
        # Scale ATR to hourly (assuming ATR is daily)
        # Intraday volatility is lower than daily
        atr_hourly = atr_pct / math.sqrt(6.5)  # 6.5 hours per trading day
        
        # Time adjustment: square root of time rule
        # sqrt(4 hours / 1 hour) = 2.0
        time_adjustment = math.sqrt(max(1.0, hours_ahead))
        
        # Adjusted hourly volatility
        adjusted_volatility_pct = atr_hourly * time_adjustment
        
        # Calculate sigma ranges
        sigma1_pct = adjusted_volatility_pct * 1.0  # 1 sigma = 1x volatility
        sigma2_pct = adjusted_volatility_pct * 2.0  # 2 sigma = 2x volatility
        
        # Expected price (slight drift based on side - momentum continuation)
        drift_adjustment = 0.3 if position_side == "long" else -0.3
        expected_price = current_price * (1 + adjusted_volatility_pct * drift_adjustment)
        
        sigma1_low = current_price * (1 - sigma1_pct)
        sigma1_high = current_price * (1 + sigma1_pct)
        sigma1_range = sigma1_high - sigma1_low
        
        sigma2_low = current_price * (1 - sigma2_pct)
        sigma2_high = current_price * (1 + sigma2_pct)
        sigma2_range = sigma2_high - sigma2_low
        
        confidence = min(1.0, hours_ahead / 24.0)  # Higher confidence for shorter horizons
        
        self._logger.log("price_range_projected", {
            "hours_ahead": hours_ahead,
            "atr": round(atr, 3),
            "sigma1_low": round(sigma1_low, 2),
            "sigma1_high": round(sigma1_high, 2),
            "sigma2_range_pct": round((sigma2_pct * 100), 1)
        })
        
        return PriceRangeProjection(
            current_price=current_price,
            hours_ahead=hours_ahead,
            expected_price=expected_price,
            sigma1_low=sigma1_low,
            sigma1_high=sigma1_high,
            sigma1_range=sigma1_range,
            sigma2_low=sigma2_low,
            sigma2_high=sigma2_high,
            sigma2_range=sigma2_range,
            atr_value=atr,
            atr_hourly=atr,
            time_adjustment=time_adjustment,
            confidence=confidence,
            notes=f"ATR-based projection with {hours_ahead}h time horizon"
        )
    
    # =========================================================================
    # THETA DECAY PROJECTION
    # =========================================================================
    
    def project_theta_impact(
        self,
        theta_per_day: float,
        premium: float,
        hours_ahead: float,
        position_type: str = "long_call"
    ) -> ThetaDecayProjection:
        """
        Project theta decay impact over time horizon.
        
        Args:
            theta_per_day: Daily theta decay (negative for long positions)
            premium: Current option premium
            hours_ahead: Time horizon in hours
            position_type: "long_call", "long_put", "short_call", etc.
            
        Returns:
            ThetaDecayProjection with decay analysis
        """
        if premium <= 0:
            return ThetaDecayProjection(
                position_type=position_type,
                current_premium=premium,
                theta_per_day=theta_per_day,
                hours_ahead=hours_ahead,
                projected_premium=premium,
                theta_cost_pct=0.0,
                theta_cost_dollars=0.0,
                breakeven_movement_pct=0.0,
                breakeven_movement_dollars=0.0,
                theta_dominates=False,
                is_critical=False,
                confidence=0.0,
                notes="Invalid premium"
            )
        
        # Theta per hour (theta_per_day is daily)
        # Theta decays faster as expiration approaches (gamma acceleration)
        theta_per_hour = theta_per_day / 24.0
        
        # Gamma acceleration factor (theta accelerates near expiration)
        # Simplified: assume theta increases by 10% per day as expiration approaches
        gamma_acceleration = 1.0 + (hours_ahead / 24.0) * 0.1
        
        # Project premium decay
        theta_cost_dollars = abs(theta_per_hour * hours_ahead) * gamma_acceleration
        projected_premium = max(0.01, premium - theta_cost_dollars)  # Can't go below 0
        
        theta_cost_pct = (theta_cost_dollars / premium * 100) if premium > 0 else 0.0
        
        # Breakeven movement needed to overcome theta
        # For long options: need price movement to offset theta decay
        breakeven_movement_dollars = theta_cost_dollars
        breakeven_movement_pct = (breakeven_movement_dollars / premium * 100) if premium > 0 else 0.0
        
        # Flags
        theta_dominates = theta_cost_pct > 20.0  # Theta costs > 20% of premium
        is_critical = theta_cost_pct > 50.0  # Theta costs > 50% of premium
        
        confidence = min(1.0, hours_ahead / 24.0) * 0.8  # Theta projection is less accurate
        
        self._logger.log("theta_impact_projected", {
            "position_type": position_type,
            "premium": round(premium, 2),
            "theta_cost_pct": round(theta_cost_pct, 1),
            "breakeven_movement_pct": round(breakeven_movement_pct, 1),
            "theta_dominates": theta_dominates
        })
        
        return ThetaDecayProjection(
            position_type=position_type,
            current_premium=premium,
            theta_per_day=theta_per_day,
            hours_ahead=hours_ahead,
            projected_premium=projected_premium,
            theta_cost_pct=theta_cost_pct,
            theta_cost_dollars=theta_cost_dollars,
            breakeven_movement_pct=breakeven_movement_pct,
            breakeven_movement_dollars=breakeven_movement_dollars,
            theta_dominates=theta_dominates,
            is_critical=is_critical,
            confidence=confidence,
            notes=f"Theta decay projection for {hours_ahead}h horizon"
        )
    
    # =========================================================================
    # CONTINUATION PROBABILITY
    # =========================================================================
    
    def calculate_continuation_prob(
        self,
        symbol: str,
        strategy: str,
        current_gain_pct: float,
        target_gain_pct: float,
        stop_loss_pct: float,
        hold_minutes: int,
        regime: Optional[str] = None
    ) -> ContinuationProbability:
        """
        Calculate probability of trade reaching target vs stop.
        
        Based on historical MFE/MAE patterns from TradeMemoryEngine.
        
        Args:
            symbol: Trading symbol
            strategy: Strategy/bot identifier
            current_gain_pct: Current unrealized gain %
            target_gain_pct: Target profit level %
            stop_loss_pct: Stop loss level % (as positive value)
            hold_minutes: How long we'd hold for
            regime: Market regime for context
            
        Returns:
            ContinuationProbability with reaching probabilities
        """
        # Get historical context for this symbol/strategy
        historical_context = self._trade_memory.get_historical_context(
            symbol=symbol,
            strategy=strategy,
            regime=regime
        )
        
        # Base probabilities from historical data
        if historical_context.fingerprint:
            fp = historical_context.fingerprint
            trade_count = fp.trade_count
            
            # Win probability from historical win rate
            base_win_prob = fp.win_rate
            
            # Stall probability at current level
            stall_prob = self._trade_memory.get_stall_probability(
                symbol=symbol,
                strategy=strategy,
                current_gain_pct=current_gain_pct,
                regime=regime
            )
        else:
            # Fallback: use Kelly-style estimates
            base_win_prob = 0.55  # Slight positive edge
            stall_prob = 0.3
            trade_count = 0
        
        # Distance to target vs stop
        total_distance = target_gain_pct + stop_loss_pct
        
        if total_distance > 0:
            # Proportional reach: closer target = higher prob of reaching it first
            distance_ratio = target_gain_pct / total_distance
            
            # Adjust by base win probability
            prob_reach_target = base_win_prob * 0.7 + distance_ratio * 0.3
            prob_reach_target = min(1.0, max(0.0, prob_reach_target))
            
            prob_reach_stop = 1.0 - prob_reach_target
        else:
            prob_reach_target = 0.5
            prob_reach_stop = 0.5
        
        prob_continue = 1.0 - stall_prob
        prob_stall_current = stall_prob
        
        # Expected P&L
        # P(win) * target% - P(loss) * stop%
        expected_pnl_pct = (prob_reach_target * target_gain_pct) - (prob_reach_stop * stop_loss_pct)
        
        # Confidence based on sample size
        confidence = min(1.0, trade_count / 50) if trade_count > 0 else 0.3
        
        # Build notes
        notes = []
        if historical_context.fingerprint:
            notes.append(f"{trade_count} historical trades")
            notes.append(f"Win rate: {base_win_prob*100:.0f}%")
        else:
            notes.append("No historical data - using estimates")
        
        notes.append(f"Expected PnL: {expected_pnl_pct:+.1f}%")
        
        self._logger.log("continuation_prob_calculated", {
            "symbol": symbol,
            "current_gain_pct": round(current_gain_pct, 2),
            "prob_reach_target": round(prob_reach_target, 3),
            "prob_reach_stop": round(prob_reach_stop, 3),
            "expected_pnl_pct": round(expected_pnl_pct, 2)
        })
        
        return ContinuationProbability(
            current_gain_pct=current_gain_pct,
            target_gain_pct=target_gain_pct,
            stop_loss_pct=stop_loss_pct,
            hold_minutes=hold_minutes,
            prob_reach_target=prob_reach_target,
            prob_reach_stop=prob_reach_stop,
            prob_stall_current=prob_stall_current,
            prob_continue=prob_continue,
            expected_pnl_pct=expected_pnl_pct,
            historical_trades_analyzed=trade_count,
            mfe_mae_matched=historical_context.fingerprint is not None,
            confidence=confidence,
            notes=" | ".join(notes)
        )
    
    # =========================================================================
    # EXPECTED VALUE CALCULATION
    # =========================================================================
    
    def calculate_expected_value(
        self,
        win_prob: float,
        avg_win_pct: float,
        avg_loss_pct: float,
        position_size: float = 1.0
    ) -> ExpectedValueCalculation:
        """
        Calculate expected value of holding the position.
        
        EV = P(win) * AvgWin$ - P(loss) * AvgLoss$
        
        Args:
            win_prob: Probability of winning (0.0-1.0)
            avg_win_pct: Average win size as percentage
            avg_loss_pct: Average loss size as percentage (positive value)
            position_size: Notional position size (for Kelly calculation)
            
        Returns:
            ExpectedValueCalculation with EV and recommendations
        """
        if not (0 <= win_prob <= 1):
            win_prob = 0.5
        
        loss_prob = 1.0 - win_prob
        
        # Expected value as percentage
        ev_pct = (win_prob * avg_win_pct) - (loss_prob * avg_loss_pct)
        ev_is_positive = ev_pct > 0
        
        # Win rate needed for breakeven
        # 0 = P(win) * avg_win - (1 - P(win)) * avg_loss
        # 0 = P(win) * avg_win - avg_loss + P(win) * avg_loss
        # avg_loss = P(win) * (avg_win + avg_loss)
        # P(win) = avg_loss / (avg_win + avg_loss)
        total_range = avg_win_pct + avg_loss_pct
        win_rate_needed = avg_loss_pct / total_range if total_range > 0 else 0.5
        win_rate_needed = min(1.0, max(0.0, win_rate_needed))
        
        # Kelly criterion: f* = (P*b - q) / b
        # where P = win prob, Q = loss prob, b = win/loss ratio
        if avg_loss_pct > 0:
            win_loss_ratio = avg_win_pct / avg_loss_pct
            kelly_fraction = (win_prob * win_loss_ratio - loss_prob) / win_loss_ratio
            kelly_fraction = max(0.0, min(kelly_fraction, 1.0))  # Clamp to 0-1
        else:
            kelly_fraction = 0.5
        
        # Confidence: higher with larger probability gap from 50/50
        confidence = min(1.0, abs(win_prob - 0.5) * 2.0)
        
        # Build notes
        notes = []
        notes.append(f"EV: {ev_pct:+.2f}%")
        
        if ev_is_positive:
            notes.append("Positive EV - hold position")
        else:
            notes.append("Negative EV - consider exit")
        
        notes.append(f"Kelly: {kelly_fraction:.1%}")
        
        self._logger.log("expected_value_calculated", {
            "win_prob": round(win_prob, 3),
            "ev_pct": round(ev_pct, 2),
            "ev_is_positive": ev_is_positive,
            "kelly_fraction": round(kelly_fraction, 3)
        })
        
        return ExpectedValueCalculation(
            win_probability=win_prob,
            avg_win_pct=avg_win_pct,
            avg_loss_pct=avg_loss_pct,
            ev_pct=ev_pct,
            ev_is_positive=ev_is_positive,
            win_rate_needed_for_breakeven=win_rate_needed,
            kelly_fraction=kelly_fraction,
            using_historical_data=False,  # Will be set by caller if applicable
            historical_trades_analyzed=0,
            confidence=confidence,
            notes=" | ".join(notes)
        )
    
    # =========================================================================
    # SYNTHESIS & RECOMMENDATIONS
    # =========================================================================
    
    def _synthesize_recommendation(
        self,
        current_gain_pct: float,
        price_range: PriceRangeProjection,
        theta_projection: Optional[ThetaDecayProjection],
        continuation: Optional[ContinuationProbability],
        expected_value: Optional[ExpectedValueCalculation],
        time_horizon_hours: float
    ) -> Tuple[str, float, str]:
        """
        Synthesize all analyses into a single recommendation.
        
        Returns:
            Tuple of (action, confidence, reason)
            where action is one of: "hold", "tighten_stop", "scale_out", "exit_now"
        """
        score = 0.0  # Accumulate recommendation score
        reasons = []
        
        # 1. Check if we're in profit
        if current_gain_pct > 0:
            score += 1.0  # Slight bias toward hold when profitable
            reasons.append("In profit")
        else:
            score -= 1.0
            reasons.append("Underwater")
        
        # 2. Check theta impact
        if theta_projection and theta_projection.theta_dominates:
            score -= 1.5
            reasons.append("Theta dominates")
        
        # 3. Check continuation probability
        if continuation:
            if continuation.prob_reach_target > 0.6:
                score += 1.5
                reasons.append("High prob target")
            elif continuation.prob_reach_stop > 0.6:
                score -= 1.5
                reasons.append("High prob stop")
            
            # Expected PnL
            if continuation.expected_pnl_pct > 1.0:
                score += 1.0
                reasons.append("Positive expected PnL")
            elif continuation.expected_pnl_pct < -1.0:
                score -= 1.0
                reasons.append("Negative expected PnL")
        
        # 4. Check expected value
        if expected_value:
            if expected_value.ev_is_positive:
                score += 1.0
                reasons.append("Positive EV")
            else:
                score -= 1.0
                reasons.append("Negative EV")
        
        # 5. Price range: are we near extremes?
        price_range_pct = (price_range.sigma2_range / price_range.current_price) * 100
        if current_gain_pct > price_range.sigma1_high:
            score -= 0.5
            reasons.append("Near sigma1 high")
        if current_gain_pct < price_range.sigma2_low:
            score -= 0.5
            reasons.append("Near sigma2 low")
        
        # Map score to action
        if score > 2.0:
            action = "hold"
            confidence = min(0.95, 0.5 + score / 10.0)
        elif score > 0.5:
            action = "hold"
            confidence = min(0.85, 0.5 + score / 10.0)
        elif score > -0.5:
            action = "tighten_stop"
            confidence = 0.6
        elif score > -1.5:
            action = "scale_out"
            confidence = 0.7
        else:
            action = "exit_now"
            confidence = min(0.9, 0.6 + abs(score) / 10.0)
        
        reason = " | ".join(reasons) if reasons else "Neutral signals"
        
        return action, confidence, reason
    
    def _assess_quality(self, has_theta: bool, has_continuation: bool, has_ev: bool) -> str:
        """Assess overall analysis quality based on available data."""
        quality_score = sum([has_theta, has_continuation, has_ev])
        
        if quality_score >= 2:
            return "high"
        elif quality_score >= 1:
            return "medium"
        else:
            return "low"
    
    # =========================================================================
    # HELPER METHODS
    # =========================================================================
    
    def _calc_gain_pct(self, entry_price: float, current_price: float, side: str) -> float:
        """Calculate gain percentage based on position side."""
        if entry_price <= 0:
            return 0.0
        
        if side == "long":
            return ((current_price - entry_price) / entry_price) * 100
        else:  # short
            return ((entry_price - current_price) / entry_price) * 100


# Global singleton
_forward_projection_engine: Optional[ForwardProjectionEngine] = None


def get_forward_projection_engine() -> ForwardProjectionEngine:
    """Get or create the global ForwardProjectionEngine instance."""
    global _forward_projection_engine
    if _forward_projection_engine is None:
        _forward_projection_engine = ForwardProjectionEngine()
    return _forward_projection_engine
