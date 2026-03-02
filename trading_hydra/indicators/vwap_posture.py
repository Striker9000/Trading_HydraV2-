"""
=============================================================================
VWAP Posture Manager - Institutional-Style VWAP Trading Framework
=============================================================================
Implements Chris Sain's institutional VWAP approach:
- Sticky posture states (BULLISH/BEARISH/NEUTRAL) that don't flip on every tick
- VWAP as the primary decision driver, not just an indicator
- Gap context integration with fill tracking
- 200 MA as trend validation layer
- Deviation bands for target zones
- Re-entry logic on VWAP retests with cooldown

Core Philosophy:
- Ride trends, respect VWAP
- Buy pullbacks TO VWAP, not breakouts
- Stand down when structure breaks
- VWAP posture overrides all other indicators

References:
- Chris Sain's institutional VWAP methodology
- Volume-weighted price anchoring for entries/exits
=============================================================================
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import math

from ..core.logging import get_logger
from ..core.state import get_state, set_state, delete_state
from ..core.clock import get_market_clock


class VWAPPosture(Enum):
    """
    VWAP Posture States - Sticky states that drive trading decisions.
    
    BULLISH: Price pulled back to VWAP and HELD - looking for longs
    BEARISH: Price broke and FAILED at VWAP - looking for shorts
    NEUTRAL: Price chopping around VWAP - NO TRADES
    """
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class GapState(Enum):
    """Gap classification and fill status."""
    GAP_UP = "gap_up"
    GAP_DOWN = "gap_down"
    NO_GAP = "no_gap"


class GapFillStatus(Enum):
    """Whether the gap has been filled."""
    UNFILLED = "unfilled"
    FILLED = "filled"
    PARTIAL = "partial"


@dataclass
class VWAPLevel:
    """
    Current VWAP and deviation bands.
    
    Bands provide target zones:
    - +1σ / -1σ: First profit targets / first support
    - +2σ / -2σ: Extended targets / strong support
    """
    vwap: float                 # Current VWAP value
    upper_1sigma: float = 0.0   # VWAP + 1 standard deviation
    lower_1sigma: float = 0.0   # VWAP - 1 standard deviation
    upper_2sigma: float = 0.0   # VWAP + 2 standard deviations
    lower_2sigma: float = 0.0   # VWAP - 2 standard deviations
    timestamp: datetime = field(default_factory=lambda: get_market_clock().now())
    
    def distance_from_vwap(self, price: float) -> float:
        """Get distance from VWAP as percentage."""
        if self.vwap <= 0:
            return 0.0
        return ((price - self.vwap) / self.vwap) * 100
    
    def sigma_position(self, price: float) -> float:
        """
        Get position relative to VWAP in sigma units.
        +1.0 = at upper 1σ, -1.0 = at lower 1σ, etc.
        """
        if self.upper_1sigma <= self.vwap:
            return 0.0
        one_sigma = self.upper_1sigma - self.vwap
        if one_sigma <= 0:
            return 0.0
        return (price - self.vwap) / one_sigma


@dataclass
class GapContext:
    """
    Daily gap analysis with fill tracking.
    
    Calculated once per day at market open (or on first bar for crypto).
    Tracks whether the gap has been filled during the session.
    """
    state: GapState = GapState.NO_GAP
    fill_status: GapFillStatus = GapFillStatus.UNFILLED
    gap_pct: float = 0.0
    prev_close: float = 0.0
    open_price: float = 0.0
    gap_high: float = 0.0       # High of gap zone (for gap up)
    gap_low: float = 0.0        # Low of gap zone (for gap down)
    date: str = ""              # Date string for caching
    
    def check_fill(self, current_price: float) -> bool:
        """
        Check if gap has been filled by current price action.
        
        Gap is filled when price trades back to previous close.
        Returns True if fill status changed.
        """
        if self.state == GapState.NO_GAP:
            return False
        
        old_status = self.fill_status
        
        if self.state == GapState.GAP_UP:
            if current_price <= self.prev_close:
                self.fill_status = GapFillStatus.FILLED
            elif current_price < self.open_price:
                self.fill_status = GapFillStatus.PARTIAL
        elif self.state == GapState.GAP_DOWN:
            if current_price >= self.prev_close:
                self.fill_status = GapFillStatus.FILLED
            elif current_price > self.open_price:
                self.fill_status = GapFillStatus.PARTIAL
        
        return self.fill_status != old_status


@dataclass
class PostureDecision:
    """
    Output from VWAP Posture Manager for each evaluation.
    
    Contains everything needed to make a trading decision:
    - Current posture and whether trades are allowed
    - Gap context and fill status
    - Distance metrics for logging
    - Reason for any blocks
    """
    posture: VWAPPosture
    allow_long: bool = False
    allow_short: bool = False
    
    vwap_level: Optional[VWAPLevel] = None
    gap_context: Optional[GapContext] = None
    ma_200: float = 0.0
    price_above_200ma: bool = False
    
    distance_from_vwap_pct: float = 0.0
    sigma_position: float = 0.0
    
    is_vwap_retest: bool = False
    retest_quality: float = 0.0  # 0-1 score of retest quality
    
    block_reason: str = ""
    posture_reason: str = ""
    
    # Re-entry tracking
    can_reenter: bool = False
    reentry_cooldown_remaining: int = 0

    # Volume profile
    poc: float = 0.0
    vah: float = 0.0
    val: float = 0.0

    # Anchored VWAP (prior day close anchor)
    anchored_vwap: float = 0.0
    price_above_avwap: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            "posture": self.posture.value,
            "allow_long": self.allow_long,
            "allow_short": self.allow_short,
            "vwap": self.vwap_level.vwap if self.vwap_level else 0,
            "distance_from_vwap_pct": round(self.distance_from_vwap_pct, 3),
            "sigma_position": round(self.sigma_position, 2),
            "gap_state": self.gap_context.state.value if self.gap_context else "none",
            "gap_fill_status": self.gap_context.fill_status.value if self.gap_context else "none",
            "ma_200": round(self.ma_200, 2),
            "price_above_200ma": self.price_above_200ma,
            "is_vwap_retest": self.is_vwap_retest,
            "retest_quality": round(self.retest_quality, 2),
            "block_reason": self.block_reason,
            "posture_reason": self.posture_reason,
            "can_reenter": self.can_reenter,
            "poc": round(self.poc, 2),
            "vah": round(self.vah, 2),
            "val": round(self.val, 2),
            "anchored_vwap": round(self.anchored_vwap, 2),
            "price_above_avwap": self.price_above_avwap,
        }


class VWAPPostureManager:
    """
    Institutional-style VWAP Posture Manager.
    
    Evaluates price action relative to VWAP and outputs trading decisions.
    Posture is "sticky" - doesn't flip on every tick, only on significant
    price action that invalidates the current posture.
    
    Usage:
        manager = VWAPPostureManager("AAPL")
        decision = manager.evaluate(bars, current_price)
        
        if decision.allow_long and decision.is_vwap_retest:
            # High-quality long entry
        elif not decision.allow_long:
            # BLOCKED: decision.block_reason explains why
    """
    
    # Posture transition thresholds
    VWAP_HOLD_THRESHOLD = 0.15      # % distance from VWAP to consider "holding"
    VWAP_FAIL_THRESHOLD = 0.25      # % break below VWAP to consider "failed"
    CHOP_ZONE_THRESHOLD = 0.10      # % range around VWAP considered "chop"
    
    # Gap thresholds
    MIN_GAP_PCT = 0.3               # Minimum gap to be significant
    LARGE_GAP_PCT = 1.0             # Large gap that may fade
    
    # Retest parameters
    RETEST_PROXIMITY_PCT = 0.20     # How close to VWAP for valid retest
    RETEST_COOLDOWN_BARS = 5        # Bars between re-entries
    
    def __init__(self, symbol: str, config: Optional[Dict[str, Any]] = None):
        """
        Initialize VWAP Posture Manager for a symbol.
        
        Args:
            symbol: Trading symbol
            config: Optional configuration overrides
        """
        self.symbol = symbol
        self.config = config or {}
        self._logger = get_logger()
        
        self._state_prefix = f"vwap_posture.{symbol}"
        
        # Configuration
        self.hold_threshold = self.config.get("hold_threshold", self.VWAP_HOLD_THRESHOLD)
        self.fail_threshold = self.config.get("fail_threshold", self.VWAP_FAIL_THRESHOLD)
        self.chop_threshold = self.config.get("chop_threshold", self.CHOP_ZONE_THRESHOLD)
        self.min_gap_pct = self.config.get("min_gap_pct", self.MIN_GAP_PCT)
        self.retest_proximity = self.config.get("retest_proximity", self.RETEST_PROXIMITY_PCT)
        self.retest_cooldown = self.config.get("retest_cooldown", self.RETEST_COOLDOWN_BARS)

        # Daily bars cache for 200MA computation (keyed by date string)
        self._daily_bars_cache: Dict[str, Any] = {}

        self._logger.log("vwap_posture_init", {
            "symbol": symbol,
            "hold_threshold": self.hold_threshold,
            "fail_threshold": self.fail_threshold,
            "chop_threshold": self.chop_threshold
        })
    
    # =========================================================================
    # STATE MANAGEMENT
    # =========================================================================
    
    def _get_current_posture(self) -> VWAPPosture:
        """Get current sticky posture from state."""
        key = f"{self._state_prefix}.posture"
        value = get_state(key, "neutral")
        return VWAPPosture(value)
    
    def _set_posture(self, posture: VWAPPosture, reason: str):
        """Set new posture with logging."""
        old_posture = self._get_current_posture()
        if old_posture != posture:
            self._logger.log("vwap_posture_change", {
                "symbol": self.symbol,
                "old_posture": old_posture.value,
                "new_posture": posture.value,
                "reason": reason
            })
        key = f"{self._state_prefix}.posture"
        set_state(key, posture.value)
    
    def _get_gap_context(self) -> Optional[GapContext]:
        """Get cached gap context for today."""
        key = f"{self._state_prefix}.gap_context"
        data = get_state(key)
        if data:
            return GapContext(
                state=GapState(data.get("state", "no_gap")),
                fill_status=GapFillStatus(data.get("fill_status", "unfilled")),
                gap_pct=data.get("gap_pct", 0),
                prev_close=data.get("prev_close", 0),
                open_price=data.get("open_price", 0),
                gap_high=data.get("gap_high", 0),
                gap_low=data.get("gap_low", 0),
                date=data.get("date", "")
            )
        return None
    
    def _set_gap_context(self, gap: GapContext):
        """Cache gap context."""
        key = f"{self._state_prefix}.gap_context"
        set_state(key, {
            "state": gap.state.value,
            "fill_status": gap.fill_status.value,
            "gap_pct": gap.gap_pct,
            "prev_close": gap.prev_close,
            "open_price": gap.open_price,
            "gap_high": gap.gap_high,
            "gap_low": gap.gap_low,
            "date": gap.date
        })
    
    def _get_last_retest_bar(self) -> int:
        """Get bar index of last retest entry."""
        key = f"{self._state_prefix}.last_retest_bar"
        return get_state(key, -999)
    
    def _set_last_retest_bar(self, bar_idx: int):
        """Record bar of retest entry."""
        key = f"{self._state_prefix}.last_retest_bar"
        set_state(key, bar_idx)
    
    def _get_reentry_count(self) -> int:
        """Get number of re-entries today."""
        key = f"{self._state_prefix}.reentry_count"
        return get_state(key, 0)
    
    def _increment_reentry_count(self):
        """Increment re-entry count."""
        key = f"{self._state_prefix}.reentry_count"
        count = get_state(key, 0)
        set_state(key, count + 1)
    
    def reset_daily_state(self):
        """Reset daily state (call at start of trading day)."""
        delete_state(f"{self._state_prefix}.gap_context")
        delete_state(f"{self._state_prefix}.reentry_count")
        delete_state(f"{self._state_prefix}.last_retest_bar")
        self._set_posture(VWAPPosture.NEUTRAL, "Daily reset")
    
    # =========================================================================
    # VWAP CALCULATION
    # =========================================================================
    
    def compute_vwap_level(self, bars: List[Dict]) -> Optional[VWAPLevel]:
        """
        Compute VWAP and deviation bands from intraday bars.
        
        VWAP = Σ(Typical Price × Volume) / Σ(Volume)
        Typical Price = (High + Low + Close) / 3
        
        Standard deviation bands calculated from price deviations weighted by volume.
        
        Args:
            bars: Intraday OHLCV bars (should be same-day or rolling window)
        
        Returns:
            VWAPLevel with VWAP and sigma bands
        """
        if not bars or len(bars) < 1:
            return None
        
        cumulative_tp_vol = 0.0
        cumulative_vol = 0.0
        price_deviations = []
        volumes = []
        
        for bar in bars:
            high = float(bar.get("high", bar.get("h", 0)))
            low = float(bar.get("low", bar.get("l", 0)))
            close = float(bar.get("close", bar.get("c", 0)))
            volume = float(bar.get("volume", bar.get("v", 0)))
            
            if volume <= 0 or close <= 0:
                continue
            
            typical_price = (high + low + close) / 3
            cumulative_tp_vol += typical_price * volume
            cumulative_vol += volume
            volumes.append(volume)
        
        if cumulative_vol <= 0:
            return None
        
        vwap = cumulative_tp_vol / cumulative_vol
        
        # Calculate volume-weighted standard deviation
        for i, bar in enumerate(bars):
            if i >= len(volumes):
                break
            close = float(bar.get("close", bar.get("c", 0)))
            if close > 0 and volumes[i] > 0:
                deviation = close - vwap
                price_deviations.append((deviation, volumes[i]))
        
        if price_deviations:
            # Volume-weighted variance
            weighted_sq_sum = sum(dev**2 * vol for dev, vol in price_deviations)
            total_weight = sum(vol for _, vol in price_deviations)
            variance = weighted_sq_sum / total_weight if total_weight > 0 else 0
            std_dev = math.sqrt(variance)
        else:
            std_dev = vwap * 0.01  # Default 1% if no data
        
        return VWAPLevel(
            vwap=vwap,
            upper_1sigma=vwap + std_dev,
            lower_1sigma=vwap - std_dev,
            upper_2sigma=vwap + (2 * std_dev),
            lower_2sigma=vwap - (2 * std_dev),
            timestamp=get_market_clock().now()
        )
    
    def _get_daily_bars(self) -> List[Dict]:
        """
        Fetch and cache daily bars for 200MA computation.
        Refreshed once per trading day; falls back gracefully to empty list.
        """
        today = get_market_clock().now().strftime("%Y-%m-%d")
        cache = self._daily_bars_cache.get(self.symbol)
        if cache and cache.get("date") == today and cache.get("bars"):
            return cache["bars"]

        try:
            from ..services.alpaca_client import get_alpaca_client
            alpaca = get_alpaca_client()
            raw_bars = alpaca.get_stock_bars(self.symbol, "1Day", limit=210)
            bars = []
            for bar in raw_bars:
                if hasattr(bar, "close"):
                    bars.append({"close": float(bar.close)})
                elif isinstance(bar, dict):
                    bars.append({"close": float(bar.get("close", bar.get("c", 0)))})
            if bars:
                self._daily_bars_cache[self.symbol] = {"bars": bars, "date": today}
            return bars
        except Exception as e:
            self._logger.warn(f"Failed to fetch daily bars for {self.symbol} 200MA: {e}")
            return []

    def compute_200_ma(self, bars: List[Dict], period: int = 200) -> float:
        """
        Compute 200-period moving average.
        
        Used as trend validation layer, NOT an entry trigger.
        If price respects 200 MA during pullbacks, trend bias remains intact.
        
        Args:
            bars: Historical bars (at least 200 periods)
            period: MA period (default 200)
        
        Returns:
            200 MA value
        """
        if not bars or len(bars) < period:
            return 0.0
        
        closes = []
        for bar in bars[-period:]:
            close = float(bar.get("close", bar.get("c", 0)))
            if close > 0:
                closes.append(close)
        
        if len(closes) < period:
            return 0.0
        
        return sum(closes) / len(closes)
    
    # =========================================================================
    # GAP DETECTION
    # =========================================================================
    
    def analyze_gap(self, bars: List[Dict], current_date: Optional[str] = None) -> GapContext:
        """
        Analyze overnight gap between sessions.
        
        Compares prior day close to current day open.
        Caches result for the day to avoid recalculation.
        
        Args:
            bars: Historical bars including prior day
            current_date: Date string (YYYY-MM-DD) for caching
        
        Returns:
            GapContext with gap classification and levels
        """
        if not current_date:
            current_date = get_market_clock().now().strftime("%Y-%m-%d")
        
        # Check cache
        cached = self._get_gap_context()
        if cached and cached.date == current_date:
            return cached
        
        if not bars or len(bars) < 2:
            gap = GapContext(date=current_date)
            self._set_gap_context(gap)
            return gap
        
        # Find prior day close and current day open
        # Assuming bars are chronological
        prev_close = float(bars[-2].get("close", bars[-2].get("c", 0)))
        open_price = float(bars[-1].get("open", bars[-1].get("o", 0)))
        
        if prev_close <= 0 or open_price <= 0:
            gap = GapContext(date=current_date)
            self._set_gap_context(gap)
            return gap
        
        gap_pct = ((open_price - prev_close) / prev_close) * 100
        
        if abs(gap_pct) < self.min_gap_pct:
            gap = GapContext(
                state=GapState.NO_GAP,
                gap_pct=gap_pct,
                prev_close=prev_close,
                open_price=open_price,
                date=current_date
            )
        elif gap_pct > 0:
            gap = GapContext(
                state=GapState.GAP_UP,
                fill_status=GapFillStatus.UNFILLED,
                gap_pct=gap_pct,
                prev_close=prev_close,
                open_price=open_price,
                gap_high=open_price,
                gap_low=prev_close,
                date=current_date
            )
        else:
            gap = GapContext(
                state=GapState.GAP_DOWN,
                fill_status=GapFillStatus.UNFILLED,
                gap_pct=gap_pct,
                prev_close=prev_close,
                open_price=open_price,
                gap_high=prev_close,
                gap_low=open_price,
                date=current_date
            )
        
        self._set_gap_context(gap)
        
        self._logger.log("vwap_gap_analyzed", {
            "symbol": self.symbol,
            "date": current_date,
            "state": gap.state.value,
            "gap_pct": round(gap_pct, 2),
            "prev_close": prev_close,
            "open_price": open_price
        })
        
        return gap
    
    # =========================================================================
    # POSTURE EVALUATION
    # =========================================================================
    
    def _evaluate_posture(
        self,
        current_price: float,
        vwap_level: VWAPLevel,
        prior_bars: List[Dict]
    ) -> Tuple[VWAPPosture, str]:
        """
        Evaluate and potentially transition VWAP posture.
        
        Posture Rules:
        - BULLISH: Price pulled back TO VWAP and held (bounced)
        - BEARISH: Price broke BELOW VWAP and failed to reclaim
        - NEUTRAL: Price chopping around VWAP with no clear direction
        
        Posture is STICKY - only changes on significant price action.
        
        Returns:
            Tuple of (new_posture, reason_for_transition)
        """
        current_posture = self._get_current_posture()
        vwap = vwap_level.vwap
        distance_pct = abs(vwap_level.distance_from_vwap(current_price))
        
        # Get recent price action to determine if VWAP was tested
        recent_lows = []
        recent_highs = []
        for bar in prior_bars[-5:]:
            low = float(bar.get("low", bar.get("l", 0)))
            high = float(bar.get("high", bar.get("h", 0)))
            if low > 0:
                recent_lows.append(low)
            if high > 0:
                recent_highs.append(high)
        
        # Check for VWAP test and hold (bullish)
        tested_vwap_from_above = any(
            low <= vwap * (1 + self.hold_threshold / 100) 
            for low in recent_lows
        )
        
        # Check for VWAP break and fail (bearish)
        broke_below_vwap = any(
            low < vwap * (1 - self.fail_threshold / 100)
            for low in recent_lows
        )
        
        # Current price position
        above_vwap = current_price > vwap
        in_chop_zone = distance_pct < self.chop_threshold
        
        # Posture transition logic
        if current_posture == VWAPPosture.NEUTRAL:
            # Look for breakout from neutral
            if tested_vwap_from_above and above_vwap and not in_chop_zone:
                return VWAPPosture.BULLISH, "Price tested VWAP and held, now above - BULLISH"
            elif broke_below_vwap and not above_vwap:
                return VWAPPosture.BEARISH, "Price broke below VWAP - BEARISH"
            else:
                return VWAPPosture.NEUTRAL, "Still in chop zone"
        
        elif current_posture == VWAPPosture.BULLISH:
            # Stay bullish unless VWAP fails
            if broke_below_vwap and not above_vwap:
                return VWAPPosture.BEARISH, "VWAP failed - transition to BEARISH"
            elif in_chop_zone:
                return VWAPPosture.NEUTRAL, "Price returned to chop zone"
            else:
                return VWAPPosture.BULLISH, "BULLISH posture maintained"
        
        elif current_posture == VWAPPosture.BEARISH:
            # Stay bearish unless price reclaims VWAP
            if above_vwap and not in_chop_zone:
                reclaimed_vwap = current_price > vwap * (1 + self.hold_threshold / 100)
                if reclaimed_vwap:
                    return VWAPPosture.BULLISH, "Price reclaimed VWAP - transition to BULLISH"
            if in_chop_zone:
                return VWAPPosture.NEUTRAL, "Price returned to chop zone"
            return VWAPPosture.BEARISH, "BEARISH posture maintained"
        
        return VWAPPosture.NEUTRAL, "Default neutral"
    
    def _check_vwap_retest(
        self,
        current_price: float,
        vwap_level: VWAPLevel,
        posture: VWAPPosture,
        prior_bars: List[Dict]
    ) -> Tuple[bool, float]:
        """
        Check if current price action represents a valid VWAP retest.
        
        A valid retest for LONG:
        - Price pulls back TO VWAP (within proximity threshold)
        - Price shows rejection (bounce) at VWAP
        - Posture is BULLISH
        
        Returns:
            Tuple of (is_retest, quality_score)
        """
        if posture == VWAPPosture.NEUTRAL:
            return False, 0.0
        
        vwap = vwap_level.vwap
        distance_pct = abs(vwap_level.distance_from_vwap(current_price))
        
        # Must be near VWAP
        if distance_pct > self.retest_proximity:
            return False, 0.0
        
        # Check for rejection candle pattern
        if len(prior_bars) < 2:
            return False, 0.0
        
        last_bar = prior_bars[-1]
        prev_bar = prior_bars[-2]
        
        last_close = float(last_bar.get("close", last_bar.get("c", 0)))
        last_low = float(last_bar.get("low", last_bar.get("l", 0)))
        last_high = float(last_bar.get("high", last_bar.get("h", 0)))
        prev_close = float(prev_bar.get("close", prev_bar.get("c", 0)))
        
        if posture == VWAPPosture.BULLISH:
            # Look for bullish rejection at VWAP
            wick_down = last_close - last_low
            body = abs(last_close - prev_close)
            
            if wick_down > body and last_low <= vwap * 1.002:
                # Lower wick touched VWAP and rejected
                quality = min(1.0, wick_down / (body + 0.01))
                return True, quality
        
        elif posture == VWAPPosture.BEARISH:
            # Look for bearish rejection at VWAP
            wick_up = last_high - last_close
            body = abs(last_close - prev_close)
            
            if wick_up > body and last_high >= vwap * 0.998:
                # Upper wick touched VWAP and rejected
                quality = min(1.0, wick_up / (body + 0.01))
                return True, quality
        
        return False, 0.0
    
    # =========================================================================
    # VOLUME PROFILE
    # =========================================================================

    def compute_volume_profile(self, bars: List[Dict], bins: int = 20) -> Dict[str, float]:
        """
        Compute intraday Volume Profile: POC, VAH, VAL.

        Bins prices by range weighted by volume.
        POC = price bucket with highest volume.
        VAH/VAL = 70% value area bracket expanding from POC outward.

        Returns:
            Dict with "poc", "vah", "val" (all floats, 0.0 on failure)
        """
        empty = {"poc": 0.0, "vah": 0.0, "val": 0.0}
        if not bars or len(bars) < 3:
            return empty

        prices = []
        volumes = []
        for bar in bars:
            high = float(bar.get("high", bar.get("h", 0)))
            low = float(bar.get("low", bar.get("l", 0)))
            volume = float(bar.get("volume", bar.get("v", 0)))
            if high > 0 and low > 0 and volume > 0:
                prices.append((high + low) / 2)
                volumes.append(volume)

        if not prices:
            return empty

        min_price = min(prices) - 0.001
        max_price = max(prices) + 0.001
        bin_size = (max_price - min_price) / bins
        if bin_size <= 0:
            return empty

        bin_volumes = [0.0] * bins
        for price, volume in zip(prices, volumes):
            idx = int((price - min_price) / bin_size)
            idx = max(0, min(idx, bins - 1))
            bin_volumes[idx] += volume

        poc_idx = bin_volumes.index(max(bin_volumes))
        poc = min_price + (poc_idx + 0.5) * bin_size

        total_vol = sum(bin_volumes)
        target_vol = total_vol * 0.70
        accumulated = bin_volumes[poc_idx]
        lower = poc_idx
        upper = poc_idx

        while accumulated < target_vol:
            can_go_lower = lower > 0
            can_go_upper = upper < bins - 1
            if not can_go_lower and not can_go_upper:
                break
            if can_go_lower and can_go_upper:
                if bin_volumes[lower - 1] >= bin_volumes[upper + 1]:
                    lower -= 1
                    accumulated += bin_volumes[lower]
                else:
                    upper += 1
                    accumulated += bin_volumes[upper]
            elif can_go_lower:
                lower -= 1
                accumulated += bin_volumes[lower]
            else:
                upper += 1
                accumulated += bin_volumes[upper]

        vah = min_price + (upper + 1) * bin_size
        val = min_price + lower * bin_size

        return {"poc": poc, "vah": vah, "val": val}

    # =========================================================================
    # ANCHORED VWAP
    # =========================================================================

    def compute_anchored_vwap(self, intraday_bars: List[Dict], anchor_price: float) -> float:
        """
        Compute VWAP anchored to a prior price level (e.g. prior day close).

        Seeds the accumulation with the anchor price (weight=1) so the
        resulting AVWAP represents trend conviction measured from that level.

        Args:
            intraday_bars: Same-day OHLCV bars
            anchor_price: Anchor level (prior day close)

        Returns:
            Anchored VWAP float, or 0.0 on failure
        """
        if not intraday_bars or anchor_price <= 0:
            return 0.0

        cum_tp_vol = anchor_price   # Seed with anchor price at weight 1
        cum_vol = 1.0

        for bar in intraday_bars:
            high = float(bar.get("high", bar.get("h", 0)))
            low = float(bar.get("low", bar.get("l", 0)))
            close = float(bar.get("close", bar.get("c", 0)))
            volume = float(bar.get("volume", bar.get("v", 0)))
            if close > 0 and volume > 0:
                tp = (high + low + close) / 3
                cum_tp_vol += tp * volume
                cum_vol += volume

        return cum_tp_vol / cum_vol if cum_vol > 0 else 0.0

    # =========================================================================
    # MAIN EVALUATION
    # =========================================================================
    
    def evaluate(
        self,
        bars: List[Dict],
        current_price: float,
        intraday_bars: Optional[List[Dict]] = None,
        bar_index: int = 0
    ) -> PostureDecision:
        """
        Evaluate VWAP posture and return trading decision.
        
        This is the main entry point. Returns a PostureDecision that tells
        the bot whether longs/shorts are allowed and why.
        
        VWAP Authority: This decision OVERRIDES other indicators.
        If posture is NEUTRAL or data is missing, NO TRADES.
        
        Args:
            bars: Historical bars for gap analysis and 200 MA
            current_price: Current market price
            intraday_bars: Same-day bars for VWAP calculation (optional, uses bars if not provided)
            bar_index: Current bar index for cooldown tracking
        
        Returns:
            PostureDecision with allow_long, allow_short, and reasons
        """
        decision = PostureDecision(posture=VWAPPosture.NEUTRAL)
        
        # Fail-closed: insufficient data = no trades
        if not bars or len(bars) < 5:
            decision.block_reason = "Insufficient bar data - FAIL CLOSED"
            self._logger.log("vwap_decision_blocked", {
                "symbol": self.symbol,
                "reason": decision.block_reason
            })
            return decision
        
        # Calculate VWAP
        vwap_bars = intraday_bars if intraday_bars else bars[-50:]  # Use last 50 bars if no intraday
        vwap_level = self.compute_vwap_level(vwap_bars)
        
        if not vwap_level or vwap_level.vwap <= 0:
            decision.block_reason = "Could not compute VWAP - FAIL CLOSED"
            self._logger.log("vwap_decision_blocked", {
                "symbol": self.symbol,
                "reason": decision.block_reason
            })
            return decision
        
        decision.vwap_level = vwap_level
        decision.distance_from_vwap_pct = vwap_level.distance_from_vwap(current_price)
        decision.sigma_position = vwap_level.sigma_position(current_price)
        
        # Calculate 200 MA using daily bars (intraday bars are never 200+ periods)
        daily_bars = self._get_daily_bars()
        decision.ma_200 = self.compute_200_ma(daily_bars, 200)
        decision.price_above_200ma = current_price > decision.ma_200 if decision.ma_200 > 0 else True

        # Volume Profile (POC / VAH / VAL) from intraday bars
        try:
            profile = self.compute_volume_profile(vwap_bars)
            decision.poc = profile["poc"]
            decision.vah = profile["vah"]
            decision.val = profile["val"]
        except Exception:
            pass

        # Anchored VWAP (prior day close as anchor)
        try:
            if daily_bars:
                anchor_price = float(daily_bars[-1].get("close", daily_bars[-1].get("c", 0)))
                if anchor_price > 0:
                    avwap = self.compute_anchored_vwap(vwap_bars, anchor_price)
                    decision.anchored_vwap = avwap
                    decision.price_above_avwap = current_price > avwap if avwap > 0 else False
        except Exception:
            pass

        # Analyze gap
        gap_context = self.analyze_gap(bars)
        gap_context.check_fill(current_price)  # Update fill status
        decision.gap_context = gap_context
        self._set_gap_context(gap_context)  # Update cache with fill status
        
        # Evaluate posture
        posture, posture_reason = self._evaluate_posture(current_price, vwap_level, bars)
        self._set_posture(posture, posture_reason)
        decision.posture = posture
        decision.posture_reason = posture_reason
        
        # Check for VWAP retest
        is_retest, retest_quality = self._check_vwap_retest(current_price, vwap_level, posture, bars)
        decision.is_vwap_retest = is_retest
        decision.retest_quality = retest_quality
        
        # Check re-entry cooldown
        last_retest_bar = self._get_last_retest_bar()
        bars_since_retest = bar_index - last_retest_bar
        if bars_since_retest < self.retest_cooldown:
            decision.can_reenter = False
            decision.reentry_cooldown_remaining = self.retest_cooldown - bars_since_retest
        else:
            decision.can_reenter = True
            decision.reentry_cooldown_remaining = 0
        
        # DETERMINE TRADE PERMISSIONS (VWAP AUTHORITY)
        if posture == VWAPPosture.NEUTRAL:
            decision.allow_long = False
            decision.allow_short = False
            decision.block_reason = "NEUTRAL posture - no directional trades allowed"
        
        elif posture == VWAPPosture.BULLISH:
            decision.allow_long = True
            decision.allow_short = False
            if not decision.allow_long:
                decision.block_reason = "BULLISH posture - shorts blocked"
        
        elif posture == VWAPPosture.BEARISH:
            decision.allow_long = False
            decision.allow_short = True
            if not decision.allow_short:
                decision.block_reason = "BEARISH posture - longs blocked"
        
        # Additional 200 MA filter (contextual, not blocking)
        if decision.allow_long and not decision.price_above_200ma:
            # Log warning but don't block - 200 MA is support validation, not trigger
            self._logger.log("vwap_200ma_warning", {
                "symbol": self.symbol,
                "price": current_price,
                "ma_200": decision.ma_200,
                "note": "Price below 200 MA - trend caution"
            })
        
        # Log decision
        self._logger.log("vwap_decision", {
            "symbol": self.symbol,
            **decision.to_dict()
        })
        
        return decision
    
    def record_reentry(self, bar_index: int):
        """Record a re-entry to start cooldown timer."""
        self._set_last_retest_bar(bar_index)
        self._increment_reentry_count()


# =============================================================================
# MODULE-LEVEL HELPERS
# =============================================================================

_posture_managers: Dict[str, VWAPPostureManager] = {}


def get_vwap_posture_manager(symbol: str, config: Optional[Dict[str, Any]] = None) -> VWAPPostureManager:
    """
    Get or create VWAP Posture Manager for a symbol.
    
    Caches managers to maintain posture state across calls.
    
    Args:
        symbol: Trading symbol
        config: Optional configuration
    
    Returns:
        VWAPPostureManager instance
    """
    if symbol not in _posture_managers:
        _posture_managers[symbol] = VWAPPostureManager(symbol, config)
    return _posture_managers[symbol]


def reset_all_posture_managers():
    """Reset all posture managers (call at start of trading day)."""
    for manager in _posture_managers.values():
        manager.reset_daily_state()


# =============================================================================
# LIQUIDITY SWEEP DETECTION
# =============================================================================

def detect_liquidity_sweep(bars: List[Dict], lookback: int = 10) -> Dict[str, Any]:
    """
    Detect if a recent candle swept a prior swing high/low and reversed.

    A liquidity sweep (stop hunt) occurs when price wicks beyond a swing
    level and closes back inside — a high-confidence reversal signal.

    Args:
        bars: Intraday OHLCV bar dicts
        lookback: How many prior bars define the swing high/low (default 10)

    Returns:
        Dict: sweep_detected (bool), sweep_direction ("above"/"below"/None),
              swept_level (float)
    """
    no_sweep: Dict[str, Any] = {"sweep_detected": False, "sweep_direction": None, "swept_level": 0.0}

    if not bars or len(bars) < lookback + 2:
        return no_sweep

    # Lookback window excludes last 2 bars (trigger candle + one buffer)
    lookback_bars = bars[-(lookback + 2):-2]
    last_bar = bars[-1]

    if not lookback_bars:
        return no_sweep

    prior_highs = [float(b.get("high", b.get("h", 0))) for b in lookback_bars]
    prior_lows = [float(b.get("low", b.get("l", 0))) for b in lookback_bars]
    prior_highs = [h for h in prior_highs if h > 0]
    prior_lows = [l for l in prior_lows if l > 0]

    if not prior_highs or not prior_lows:
        return no_sweep

    prior_swing_high = max(prior_highs)
    prior_swing_low = min(prior_lows)

    last_high = float(last_bar.get("high", last_bar.get("h", 0)))
    last_low = float(last_bar.get("low", last_bar.get("l", 0)))
    last_close = float(last_bar.get("close", last_bar.get("c", 0)))

    # Bearish sweep: wick above prior swing high, closed back below it
    if prior_swing_high > 0 and last_high > prior_swing_high and last_close < prior_swing_high:
        return {"sweep_detected": True, "sweep_direction": "above", "swept_level": prior_swing_high}

    # Bullish sweep: wick below prior swing low, closed back above it
    if prior_swing_low > 0 and last_low < prior_swing_low and last_close > prior_swing_low:
        return {"sweep_detected": True, "sweep_direction": "below", "swept_level": prior_swing_low}

    return no_sweep


# =============================================================================
# ORDER FLOW APPROXIMATION
# =============================================================================

def compute_order_flow(bars: List[Dict]) -> Dict[str, Any]:
    """
    Approximate order flow using bar close vs open as bull/bear volume proxy.

    If close >= open the bar is treated as bullish volume; otherwise bearish.
    Cumulative delta = bull_volume - bear_volume.

    Args:
        bars: OHLCV bar dicts

    Returns:
        Dict: cumulative_delta (float), delta_ratio (float 0-1),
              bullish_flow (bool)
    """
    if not bars:
        return {"cumulative_delta": 0.0, "delta_ratio": 0.5, "bullish_flow": True}

    bull_delta = 0.0
    bear_delta = 0.0

    for bar in bars:
        open_ = float(bar.get("open", bar.get("o", 0)))
        close = float(bar.get("close", bar.get("c", 0)))
        volume = float(bar.get("volume", bar.get("v", 0)))

        if close > 0 and volume > 0:
            if close >= open_:
                bull_delta += volume
            else:
                bear_delta += volume

    total = bull_delta + bear_delta
    delta_ratio = bull_delta / total if total > 0 else 0.5
    cumulative_delta = bull_delta - bear_delta

    return {
        "cumulative_delta": cumulative_delta,
        "delta_ratio": round(delta_ratio, 3),
        "bullish_flow": delta_ratio >= 0.5,
    }
