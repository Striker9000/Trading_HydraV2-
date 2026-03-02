"""
Regime detection for HydraSensors.

Determines market regime (risk_on / risk_off / neutral) using:
- Breadth sensors (RSP vs SPY, SMH vs SPY)
- Volatility (VIX level)
- Momentum (SPY trend)
- Leadership (sector rotation signals)

The regime is:
- EXPLAINABLE (not ML black box)
- FAIL-CLOSED (defaults to neutral when data missing)
- CONSERVATIVE (better to be neutral than wrong)
"""

from typing import Dict, Optional
from datetime import datetime
from dataclasses import dataclass

from .state import RegimeState, RegimeData, BreadthReading
from ..core.logging import get_logger


@dataclass
class RegimeConfig:
    """Configuration for regime detection."""
    # Breadth thresholds
    rsp_spy_bearish_threshold: float = -0.02  # RSP lagging SPY by 2%
    smh_spy_bearish_threshold: float = -0.03  # SMH lagging SPY by 3%
    
    # Volatility thresholds
    vix_elevated: float = 25.0
    vix_extreme: float = 35.0
    
    # Momentum thresholds
    spy_sma20_bearish: float = -0.02  # SPY 2% below SMA20
    spy_sma50_bearish: float = -0.03  # SPY 3% below SMA50
    
    # Scoring weights
    weight_breadth: float = 0.35
    weight_volatility: float = 0.30
    weight_momentum: float = 0.20
    weight_leadership: float = 0.15


class RegimeDetector:
    """
    Detects market regime based on multiple factors.
    
    Regime states:
    - RISK_ON: Favorable for aggressive trading
    - RISK_OFF: Defensive posture recommended
    - NEUTRAL: Mixed signals, use caution
    - UNKNOWN: Insufficient data
    
    Design principles:
    - Fail-closed: Missing data -> UNKNOWN
    - Explainable: Each component score visible
    - Conservative: Default to NEUTRAL over wrong calls
    """
    
    def __init__(self, config: RegimeConfig = None):
        self.logger = get_logger()
        self.config = config or RegimeConfig()
    
    def calculate_breadth_score(
        self,
        breadth_readings: Dict[str, BreadthReading],
    ) -> float:
        """
        Calculate breadth component score.
        
        Returns:
            Score from -1.0 (bearish) to 1.0 (bullish)
        """
        if not breadth_readings:
            return 0.0
        
        bullish_count = 0
        bearish_count = 0
        total = 0
        
        for reading in breadth_readings.values():
            if reading.spread_5d is not None:
                total += 1
                
                # RSP vs SPY is most important
                if reading.name == "RSP_vs_SPY":
                    if reading.spread_5d > 0:
                        bullish_count += 2
                    elif reading.spread_5d < self.config.rsp_spy_bearish_threshold:
                        bearish_count += 2
                else:
                    if reading.bullish is True:
                        bullish_count += 1
                    elif reading.bullish is False:
                        bearish_count += 1
        
        if total == 0:
            return 0.0
        
        # Normalize to -1 to 1
        net = bullish_count - bearish_count
        max_score = total * 2  # Max if all readings are bullish (RSP counts double)
        
        return net / max(1, max_score)
    
    def calculate_volatility_score(
        self,
        vix: Optional[float] = None,
    ) -> float:
        """
        Calculate volatility component score.
        
        Returns:
            Score from -1.0 (extreme fear) to 1.0 (low vol, bullish)
        """
        if vix is None:
            return 0.0
        
        if vix < 15:
            return 1.0  # Very low vol - bullish
        elif vix < 20:
            return 0.5
        elif vix < self.config.vix_elevated:
            return 0.0  # Neutral
        elif vix < self.config.vix_extreme:
            return -0.5  # Elevated fear
        else:
            return -1.0  # Extreme fear
    
    def calculate_momentum_score(
        self,
        spy_price_vs_sma20: Optional[float] = None,
        spy_price_vs_sma50: Optional[float] = None,
    ) -> float:
        """
        Calculate momentum component score.
        
        Returns:
            Score from -1.0 (bearish trend) to 1.0 (bullish trend)
        """
        if spy_price_vs_sma20 is None and spy_price_vs_sma50 is None:
            return 0.0
        
        score = 0.0
        count = 0
        
        # SMA20 check
        if spy_price_vs_sma20 is not None:
            count += 1
            if spy_price_vs_sma20 > 0.02:
                score += 1.0
            elif spy_price_vs_sma20 > 0:
                score += 0.5
            elif spy_price_vs_sma20 > self.config.spy_sma20_bearish:
                score += 0
            else:
                score -= 0.5
        
        # SMA50 check
        if spy_price_vs_sma50 is not None:
            count += 1
            if spy_price_vs_sma50 > 0.02:
                score += 1.0
            elif spy_price_vs_sma50 > 0:
                score += 0.5
            elif spy_price_vs_sma50 > self.config.spy_sma50_bearish:
                score += 0
            else:
                score -= 1.0  # Below SMA50 is more bearish
        
        return score / max(1, count)
    
    def calculate_leadership_score(
        self,
        smh_vs_spy_spread: Optional[float] = None,
    ) -> float:
        """
        Calculate leadership component score (tech/semis leading).
        
        Returns:
            Score from -1.0 (no leadership) to 1.0 (strong leadership)
        """
        if smh_vs_spy_spread is None:
            return 0.0
        
        if smh_vs_spy_spread > 0.02:
            return 1.0  # Semis strongly leading
        elif smh_vs_spy_spread > 0:
            return 0.5
        elif smh_vs_spy_spread > self.config.smh_spy_bearish_threshold:
            return 0.0  # Neutral
        else:
            return -0.5  # Semis lagging badly
    
    def detect_regime(
        self,
        breadth_readings: Dict[str, BreadthReading] = None,
        vix: Optional[float] = None,
        spy_price_vs_sma20: Optional[float] = None,
        spy_price_vs_sma50: Optional[float] = None,
        smh_vs_spy_spread: Optional[float] = None,
    ) -> RegimeData:
        """
        Detect current market regime.
        
        Args:
            breadth_readings: Current breadth sensor readings
            vix: Current VIX level
            spy_price_vs_sma20: SPY % above/below SMA20
            spy_price_vs_sma50: SPY % above/below SMA50
            smh_vs_spy_spread: SMH vs SPY 5-day spread
        
        Returns:
            RegimeData with state, confidence, and component scores
        """
        # Calculate component scores
        breadth_score = self.calculate_breadth_score(breadth_readings or {})
        volatility_score = self.calculate_volatility_score(vix)
        momentum_score = self.calculate_momentum_score(spy_price_vs_sma20, spy_price_vs_sma50)
        leadership_score = self.calculate_leadership_score(smh_vs_spy_spread)
        
        # Weighted composite score
        composite = (
            breadth_score * self.config.weight_breadth +
            volatility_score * self.config.weight_volatility +
            momentum_score * self.config.weight_momentum +
            leadership_score * self.config.weight_leadership
        )
        
        # Determine regime state
        if composite > 0.3:
            state = RegimeState.RISK_ON
            risk_on = True
        elif composite < -0.3:
            state = RegimeState.RISK_OFF
            risk_on = False
        else:
            state = RegimeState.NEUTRAL
            risk_on = None
        
        # Check if we have enough data for confidence
        data_coverage = 0
        if breadth_readings:
            data_coverage += 0.3
        if vix is not None:
            data_coverage += 0.25
        if spy_price_vs_sma20 is not None or spy_price_vs_sma50 is not None:
            data_coverage += 0.25
        if smh_vs_spy_spread is not None:
            data_coverage += 0.2
        
        # If not enough data, mark as unknown
        if data_coverage < 0.5:
            state = RegimeState.UNKNOWN
            risk_on = None
        
        # Confidence based on data coverage and signal strength
        confidence = data_coverage * min(1.0, abs(composite) * 2)
        
        # Build notes
        notes_parts = []
        if breadth_score > 0.3:
            notes_parts.append("breadth bullish")
        elif breadth_score < -0.3:
            notes_parts.append("breadth bearish")
        
        if vix is not None:
            if vix > self.config.vix_elevated:
                notes_parts.append(f"VIX elevated ({vix:.1f})")
        
        if momentum_score < -0.3:
            notes_parts.append("SPY below key SMAs")
        
        notes = "; ".join(notes_parts) if notes_parts else "Mixed signals"
        
        return RegimeData(
            state=state,
            confidence=confidence,
            risk_on=risk_on,
            breadth_score=breadth_score,
            volatility_score=volatility_score,
            momentum_score=momentum_score,
            leadership_score=leadership_score,
            last_update=datetime.now(),
            notes=notes,
        )
