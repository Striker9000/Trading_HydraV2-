"""
=============================================================================
REGIME CLASSIFIER - Context-Aware Market Regime Classification for ExitBot v2
=============================================================================

Classifies market regimes for context-aware exit decisions:

1. Trend Classification (ADX, +DI, -DI)
   - TRENDING_UP: Strong uptrend (ADX > 25, +DI > -DI)
   - TRENDING_DOWN: Strong downtrend (ADX > 25, -DI > +DI)
   - CHOPPY: No clear trend (ADX < 20)
   - TRANSITIONING: Trend changing (ADX 20-25)

2. Volatility Classification (VIX, VVIX, ATR)
   - VOL_LOW: VIX < 15 or ATR below normal
   - VOL_NORMAL: VIX 15-25, ATR normal
   - VOL_HIGH: VIX 25-35
   - VOL_EXTREME: VIX > 35 or VVIX > 120

3. Volatility Dynamic (VIX/ATR history)
   - VOL_EXPANDING: Vol increasing (VIX rising, ATR widening)
   - VOL_CONTRACTING: Vol decreasing
   - VOL_STABLE: Vol flat

4. Market Context (News/Flow/Macro)
   - NEWS_DRIVEN: High news activity affecting market
   - FLOW_DRIVEN: Price movement from order flow
   - MACRO_DRIVEN: Fed/economic data driving

5. Gamma Environment (for options)
   - GAMMA_NEGATIVE: Dealers short gamma (amplifies moves)
   - GAMMA_NEUTRAL: Balanced
   - GAMMA_POSITIVE: Dealers long gamma (dampens moves)

Usage:
    classifier = get_regime_classifier()
    regime = classifier.get_current_regime("AAPL", bars=recent_bars)
    if regime.trend == TrendClassification.TRENDING_UP:
        # Adjust exit strategy for uptrend
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import json

from ..core.logging import get_logger
from ..core.state import get_state, set_state
from ..core.clock import get_market_clock


# =============================================================================
# ENUMS - Market Regime Classifications
# =============================================================================

class TrendClassification(Enum):
    """Trend strength classification from ADX and directional indicators"""
    TRENDING_UP = "trending_up"           # Strong uptrend (ADX > 25, +DI > -DI)
    TRENDING_DOWN = "trending_down"       # Strong downtrend (ADX > 25, -DI > +DI)
    CHOPPY = "choppy"                     # No clear trend (ADX < 20)
    TRANSITIONING = "transitioning"       # Trend changing (ADX 20-25)


class VolatilityClassification(Enum):
    """Volatility regime classification"""
    VOL_LOW = "vol_low"           # VIX < 15 or ATR below normal
    VOL_NORMAL = "vol_normal"     # VIX 15-25, ATR normal
    VOL_HIGH = "vol_high"         # VIX 25-35
    VOL_EXTREME = "vol_extreme"   # VIX > 35 or VVIX > 120


class VolDynamic(Enum):
    """Volatility direction and momentum"""
    VOL_EXPANDING = "vol_expanding"       # Vol increasing (VIX rising, ATR widening)
    VOL_CONTRACTING = "vol_contracting"   # Vol decreasing
    VOL_STABLE = "vol_stable"             # Vol flat


class MarketContext(Enum):
    """What is driving the market?"""
    NEWS_DRIVEN = "news_driven"     # High news activity affecting market
    FLOW_DRIVEN = "flow_driven"     # Price movement from order flow
    MACRO_DRIVEN = "macro_driven"   # Fed/economic data driving


class GammaEnvironment(Enum):
    """Gamma environment for options traders"""
    GAMMA_NEGATIVE = "gamma_negative"     # Dealers short gamma (amplifies moves)
    GAMMA_NEUTRAL = "gamma_neutral"       # Balanced gamma
    GAMMA_POSITIVE = "gamma_positive"     # Dealers long gamma (dampens moves)


# =============================================================================
# DATA CLASSES - Regime Analysis Results
# =============================================================================

@dataclass
class TrendIndicators:
    """ADX and directional indicator values"""
    adx: float                     # ADX value (0-100, >25 = strong trend)
    plus_di: float                 # +DI (uptrend strength)
    minus_di: float                # -DI (downtrend strength)
    timestamp: datetime = field(default_factory=lambda: get_market_clock().now())


@dataclass
class VolatilityIndicators:
    """Volatility indicators for classification"""
    vix: float                     # VIX level
    vvix: float                    # Volatility of VIX
    atr: float                     # Average True Range
    atr_sma: float                 # Normal ATR level (baseline)
    timestamp: datetime = field(default_factory=lambda: get_market_clock().now())


@dataclass
class MarketRegime:
    """Complete market regime classification snapshot"""
    symbol: str
    timestamp: datetime
    
    # Classifications
    trend: TrendClassification
    volatility: VolatilityClassification
    vol_dynamic: VolDynamic
    context: MarketContext
    gamma: GammaEnvironment
    
    # Raw indicators
    trend_indicators: TrendIndicators
    vol_indicators: VolatilityIndicators
    
    # Derived signals for exit decisions
    is_strong_trend: bool          # True if ADX > 25
    is_trending_up: bool           # True if +DI > -DI and strong trend
    is_trending_down: bool         # True if -DI > +DI and strong trend
    is_choppy: bool                # True if ADX < 20
    is_vol_expanding: bool         # True if vol rising
    is_vol_high: bool              # True if in HIGH or EXTREME regime
    
    # Exit decision hints
    high_divergence: bool          # True if trend and vol don't agree
    regime_changing: bool          # True if moving between regimes
    
    # Audit
    errors: List[str] = field(default_factory=list)


@dataclass
class RegimeChange:
    """Detected change in market regime"""
    symbol: str
    timestamp: datetime
    
    old_regime: MarketRegime
    new_regime: MarketRegime
    
    # What changed
    trend_changed: bool
    vol_changed: bool
    context_changed: bool
    
    # Impact on exits
    severity: str                  # "minor", "moderate", "major"
    reason: str                    # Human-readable reason for change


# =============================================================================
# REGIME CLASSIFIER - Main Class
# =============================================================================

class RegimeClassifier:
    """
    Classifies market regimes for context-aware exit decisions.
    
    Integrates with MarketRegimeService for global market context,
    and uses technical indicators (ADX, ATR) for symbol-specific regime.
    
    Caches regime data to minimize computation.
    """
    
    # ADX thresholds
    ADX_STRONG_TREND = 25.0
    ADX_WEAK_TREND = 20.0
    ADX_MAX = 100.0
    
    # VIX thresholds (from market_regime.py)
    VIX_LOW = 15.0
    VIX_NORMAL_HIGH = 25.0
    VIX_HIGH = 35.0
    
    # VVIX threshold (early warning)
    VVIX_WARNING = 120.0
    
    # ATR SMA period for normalization
    ATR_SMA_PERIOD = 50
    
    # Cache duration in minutes
    CACHE_DURATION_MINUTES = 1
    
    def __init__(self):
        self._logger = get_logger()
        self._market_regime_service = None  # Lazy load
        self._regime_cache: Dict[str, Tuple[MarketRegime, datetime]] = {}
        self._vix_history: List[Tuple[float, datetime]] = []
        self._atr_history: Dict[str, List[Tuple[float, datetime]]] = {}
    
    def _get_market_regime_service(self):
        """Lazy load MarketRegimeService to avoid circular imports"""
        if self._market_regime_service is None:
            try:
                from .market_regime import MarketRegimeService
                self._market_regime_service = MarketRegimeService()
            except Exception as e:
                self._logger.error(f"Failed to load MarketRegimeService: {e}")
                self._market_regime_service = None
        return self._market_regime_service
    
    def get_current_regime(
        self, 
        symbol: str, 
        bars: Optional[List[Dict[str, Any]]] = None,
        vix: Optional[float] = None,
        vvix: Optional[float] = None,
        force_refresh: bool = False
    ) -> MarketRegime:
        """
        Get current market regime for a symbol.
        
        Args:
            symbol: Stock symbol
            bars: Historical OHLCV bars (required for trend/ATR calculation)
            vix: Current VIX level (optional, will fetch if not provided)
            vvix: Current VVIX level (optional)
            force_refresh: Force recalculation even if cached
            
        Returns:
            MarketRegime with complete classification
        """
        # Check cache
        if not force_refresh and symbol in self._regime_cache:
            regime, cache_time = self._regime_cache[symbol]
            age = (get_market_clock().now() - cache_time).total_seconds() / 60
            if age < self.CACHE_DURATION_MINUTES:
                self._logger.log("regime_classifier_cached", {
                    "symbol": symbol,
                    "cache_age_minutes": round(age, 1)
                })
                return regime
        
        # Analyze regime
        self._logger.log("regime_classifier_start", {"symbol": symbol})
        regime = self._analyze_regime(symbol, bars, vix, vvix)
        
        # Cache it
        self._regime_cache[symbol] = (regime, get_market_clock().now())
        
        # Log results
        self._logger.log("regime_classifier_result", {
            "symbol": symbol,
            "trend": regime.trend.value,
            "volatility": regime.volatility.value,
            "vol_dynamic": regime.vol_dynamic.value,
            "context": regime.context.value,
            "gamma": regime.gamma.value,
            "is_strong_trend": regime.is_strong_trend,
            "is_vol_expanding": regime.is_vol_expanding,
            "high_divergence": regime.high_divergence,
            "errors": regime.errors
        })
        
        return regime
    
    def detect_regime_change(
        self, 
        old_regime: MarketRegime, 
        new_regime: MarketRegime
    ) -> Optional[RegimeChange]:
        """
        Detect and characterize changes in market regime.
        
        Args:
            old_regime: Previous regime snapshot
            new_regime: Current regime snapshot
            
        Returns:
            RegimeChange if significant change detected, None otherwise
        """
        # Check if anything changed
        trend_changed = old_regime.trend != new_regime.trend
        vol_changed = old_regime.volatility != new_regime.volatility
        context_changed = old_regime.context != new_regime.context
        
        if not (trend_changed or vol_changed or context_changed):
            return None
        
        # Determine severity
        severity = "minor"
        reason_parts = []
        
        if trend_changed:
            reason_parts.append(f"trend {old_regime.trend.value} -> {new_regime.trend.value}")
            if (old_regime.is_strong_trend and not new_regime.is_strong_trend) or \
               (not old_regime.is_strong_trend and new_regime.is_strong_trend):
                severity = "major"
            else:
                severity = "moderate"
        
        if vol_changed:
            reason_parts.append(f"vol {old_regime.volatility.value} -> {new_regime.volatility.value}")
            # Vol change from LOW to HIGH is major
            if (old_regime.volatility in [VolatilityClassification.VOL_LOW] and \
                new_regime.volatility in [VolatilityClassification.VOL_HIGH, VolatilityClassification.VOL_EXTREME]) or \
               (old_regime.volatility in [VolatilityClassification.VOL_HIGH, VolatilityClassification.VOL_EXTREME] and \
                new_regime.volatility in [VolatilityClassification.VOL_LOW]):
                severity = "major"
            elif severity == "minor":
                severity = "moderate"
        
        if context_changed:
            reason_parts.append(f"context {old_regime.context.value} -> {new_regime.context.value}")
        
        return RegimeChange(
            symbol=new_regime.symbol,
            timestamp=new_regime.timestamp,
            old_regime=old_regime,
            new_regime=new_regime,
            trend_changed=trend_changed,
            vol_changed=vol_changed,
            context_changed=context_changed,
            severity=severity,
            reason=" | ".join(reason_parts)
        )
    
    # =========================================================================
    # INTERNAL METHODS - Regime Analysis
    # =========================================================================
    
    def _analyze_regime(
        self, 
        symbol: str, 
        bars: Optional[List[Dict[str, Any]]], 
        vix: Optional[float],
        vvix: Optional[float]
    ) -> MarketRegime:
        """
        Analyze complete market regime for a symbol.
        
        Args:
            symbol: Stock symbol
            bars: Historical bars
            vix: Current VIX
            vvix: Current VVIX
            
        Returns:
            MarketRegime with all classifications
        """
        errors = []
        timestamp = get_market_clock().now()
        
        # Get global market context
        market_regime_service = self._get_market_regime_service()
        global_regime = None
        if market_regime_service:
            try:
                global_regime = market_regime_service.get_regime()
            except Exception as e:
                errors.append(f"Global regime fetch failed: {e}")
        
        # Use provided VIX/VVIX or get from global regime
        if vix is None and global_regime:
            vix = global_regime.vix
        if vvix is None and global_regime:
            vvix = global_regime.vvix
        if vix is None:
            vix = 18.0  # Default normal
        if vvix is None:
            vvix = 85.0  # Default normal
        
        # Classify trend from bars
        trend_indicators = self._compute_trend_indicators(symbol, bars)
        trend = self._classify_trend(trend_indicators)
        
        # Classify volatility
        atr = self._compute_atr(symbol, bars) if bars else 20.0
        atr_sma = self._compute_atr_sma(symbol, bars) if bars else 20.0
        vol_indicators = VolatilityIndicators(
            vix=vix,
            vvix=vvix,
            atr=atr,
            atr_sma=atr_sma,
            timestamp=timestamp
        )
        volatility = self._classify_volatility(vix, vvix, atr, atr_sma)
        
        # Classify vol dynamics
        vol_dynamic = self._classify_vol_dynamic(vix, atr)
        
        # Determine market context
        context = self._determine_context(symbol, global_regime)
        
        # Determine gamma environment
        gamma = self._determine_gamma(trend, volatility)
        
        # Derived signals
        is_strong_trend = trend_indicators.adx > self.ADX_STRONG_TREND
        is_trending_up = is_strong_trend and trend_indicators.plus_di > trend_indicators.minus_di
        is_trending_down = is_strong_trend and trend_indicators.minus_di > trend_indicators.plus_di
        is_choppy = trend_indicators.adx < self.ADX_WEAK_TREND
        is_vol_expanding = vol_dynamic == VolDynamic.VOL_EXPANDING
        is_vol_high = volatility in [VolatilityClassification.VOL_HIGH, VolatilityClassification.VOL_EXTREME]
        
        # High divergence: strong trend but high vol (or choppy but vol expanding)
        high_divergence = (is_strong_trend and is_vol_high) or (is_choppy and is_vol_expanding)
        
        # Regime changing: in transition zones
        regime_changing = trend_indicators.adx >= self.ADX_WEAK_TREND and \
                         trend_indicators.adx <= self.ADX_STRONG_TREND
        
        return MarketRegime(
            symbol=symbol,
            timestamp=timestamp,
            trend=trend,
            volatility=volatility,
            vol_dynamic=vol_dynamic,
            context=context,
            gamma=gamma,
            trend_indicators=trend_indicators,
            vol_indicators=vol_indicators,
            is_strong_trend=is_strong_trend,
            is_trending_up=is_trending_up,
            is_trending_down=is_trending_down,
            is_choppy=is_choppy,
            is_vol_expanding=is_vol_expanding,
            is_vol_high=is_vol_high,
            high_divergence=high_divergence,
            regime_changing=regime_changing,
            errors=errors
        )
    
    def _compute_trend_indicators(
        self, 
        symbol: str, 
        bars: Optional[List[Dict[str, Any]]]
    ) -> TrendIndicators:
        """
        Compute ADX and directional indicators (+DI, -DI).
        
        Args:
            symbol: Stock symbol
            bars: Historical OHLCV bars
            
        Returns:
            TrendIndicators with ADX, +DI, -DI
        """
        if not bars or len(bars) < 14:
            # Not enough data - return neutral
            return TrendIndicators(
                adx=50.0,  # Neutral
                plus_di=50.0,
                minus_di=50.0,
                timestamp=get_market_clock().now()
            )
        
        adx = self._compute_adx(bars, period=14)
        plus_di, minus_di = self._compute_directional_indicators(bars, period=14)
        
        return TrendIndicators(
            adx=adx,
            plus_di=plus_di,
            minus_di=minus_di,
            timestamp=get_market_clock().now()
        )
    
    def _compute_adx(self, bars: List[Dict[str, Any]], period: int = 14) -> float:
        """
        Compute Average Directional Index (ADX).
        
        ADX measures trend strength (0-100):
        - 0-20: Weak trend or no trend
        - 20-25: Transitioning
        - 25-100: Strong trend
        
        Args:
            bars: Historical bars with high, low, close
            period: ADX period (default 14)
            
        Returns:
            ADX value (0-100)
        """
        if len(bars) < period + 1:
            return 50.0
        
        try:
            plus_di, minus_di = self._compute_directional_indicators(bars, period)
            
            # DX = 100 * |+DI - -DI| / (+DI + -DI)
            di_sum = plus_di + minus_di
            if di_sum == 0:
                return 0.0
            
            dx = 100 * abs(plus_di - minus_di) / di_sum
            
            # ADX = EMA of DX over period
            adx = self._ema([dx], period) if len(bars) >= 2 * period else dx
            
            return min(100.0, max(0.0, adx))
        except Exception as e:
            self._logger.error(f"ADX computation failed: {e}")
            return 50.0
    
    def _compute_directional_indicators(
        self, 
        bars: List[Dict[str, Any]], 
        period: int = 14
    ) -> Tuple[float, float]:
        """
        Compute +DI (Plus Directional Indicator) and -DI (Minus Directional Indicator).
        
        These measure uptrend and downtrend strength separately.
        
        Args:
            bars: Historical bars
            period: DI period (default 14)
            
        Returns:
            Tuple of (+DI, -DI) values
        """
        if len(bars) < period + 1:
            return 50.0, 50.0
        
        try:
            # Compute True Range and directional movements
            plus_moves = []
            minus_moves = []
            true_ranges = []
            
            for i in range(1, len(bars)):
                high = float(bars[i].get("high", bars[i].get("h", 0)))
                low = float(bars[i].get("low", bars[i].get("l", 0)))
                prev_high = float(bars[i-1].get("high", bars[i-1].get("h", 0)))
                prev_low = float(bars[i-1].get("low", bars[i-1].get("l", 0)))
                prev_close = float(bars[i-1].get("close", bars[i-1].get("c", 0)))
                
                # True Range
                tr1 = high - low
                tr2 = abs(high - prev_close)
                tr3 = abs(low - prev_close)
                tr = max(tr1, tr2, tr3)
                true_ranges.append(tr)
                
                # Directional movements
                up_move = high - prev_high
                down_move = prev_low - low
                
                if up_move > 0 and up_move > down_move:
                    plus_moves.append(up_move)
                else:
                    plus_moves.append(0)
                
                if down_move > 0 and down_move > up_move:
                    minus_moves.append(down_move)
                else:
                    minus_moves.append(0)
            
            # Sum over period
            recent_tr = true_ranges[-period:] if len(true_ranges) >= period else true_ranges
            recent_plus = plus_moves[-period:] if len(plus_moves) >= period else plus_moves
            recent_minus = minus_moves[-period:] if len(minus_moves) >= period else minus_moves
            
            sum_tr = sum(recent_tr) if recent_tr else 1.0
            sum_plus = sum(recent_plus)
            sum_minus = sum(recent_minus)
            
            # Calculate DI
            plus_di = 100 * (sum_plus / sum_tr) if sum_tr > 0 else 50.0
            minus_di = 100 * (sum_minus / sum_tr) if sum_tr > 0 else 50.0
            
            return min(100.0, plus_di), min(100.0, minus_di)
        except Exception as e:
            self._logger.error(f"DI computation failed: {e}")
            return 50.0, 50.0
    
    def _compute_atr(
        self, 
        symbol: str, 
        bars: Optional[List[Dict[str, Any]]], 
        period: int = 14
    ) -> float:
        """
        Compute Average True Range (ATR) - volatility measure.
        
        Args:
            symbol: Stock symbol
            bars: Historical bars
            period: ATR period (default 14)
            
        Returns:
            ATR value
        """
        if not bars or len(bars) < period + 1:
            return 20.0  # Default
        
        try:
            true_ranges = []
            for i in range(1, len(bars)):
                high = float(bars[i].get("high", bars[i].get("h", 0)))
                low = float(bars[i].get("low", bars[i].get("l", 0)))
                prev_close = float(bars[i-1].get("close", bars[i-1].get("c", 0)))
                
                tr1 = high - low
                tr2 = abs(high - prev_close)
                tr3 = abs(low - prev_close)
                tr = max(tr1, tr2, tr3)
                true_ranges.append(tr)
            
            # Average recent TR
            recent_tr = true_ranges[-period:] if len(true_ranges) >= period else true_ranges
            atr = sum(recent_tr) / len(recent_tr) if recent_tr else 20.0
            
            # Track history for dynamic classification
            self._track_atr_history(symbol, atr)
            
            return atr
        except Exception as e:
            self._logger.error(f"ATR computation failed: {e}")
            return 20.0
    
    def _compute_atr_sma(
        self, 
        symbol: str, 
        bars: Optional[List[Dict[str, Any]]]
    ) -> float:
        """
        Compute normal ATR level (SMA of ATR over longer period).
        
        This is used to determine if current ATR is elevated or depressed.
        
        Args:
            symbol: Stock symbol
            bars: Historical bars
            
        Returns:
            Normal ATR level
        """
        if not bars or len(bars) < self.ATR_SMA_PERIOD + 20:
            return 20.0  # Default
        
        try:
            atr_values = []
            for start in range(max(0, len(bars) - self.ATR_SMA_PERIOD - 20), len(bars) - 20):
                window_bars = bars[start:start + 20]
                atr = self._compute_atr(symbol, window_bars, period=14)
                atr_values.append(atr)
            
            return sum(atr_values) / len(atr_values) if atr_values else 20.0
        except Exception as e:
            self._logger.error(f"ATR SMA computation failed: {e}")
            return 20.0
    
    def _classify_trend(self, trend_indicators: TrendIndicators) -> TrendClassification:
        """
        Classify trend from ADX and directional indicators.
        
        Args:
            trend_indicators: TrendIndicators with ADX, +DI, -DI
            
        Returns:
            TrendClassification
        """
        adx = trend_indicators.adx
        plus_di = trend_indicators.plus_di
        minus_di = trend_indicators.minus_di
        
        if adx >= self.ADX_STRONG_TREND:
            # Strong trend
            if plus_di > minus_di:
                return TrendClassification.TRENDING_UP
            else:
                return TrendClassification.TRENDING_DOWN
        elif adx >= self.ADX_WEAK_TREND:
            # Transitioning
            return TrendClassification.TRANSITIONING
        else:
            # No clear trend
            return TrendClassification.CHOPPY
    
    def _classify_volatility(
        self, 
        vix: float, 
        vvix: float, 
        atr: float,
        atr_sma: float
    ) -> VolatilityClassification:
        """
        Classify volatility regime.
        
        Args:
            vix: Current VIX
            vvix: Current VVIX
            atr: Current ATR
            atr_sma: Normal ATR level
            
        Returns:
            VolatilityClassification
        """
        # VVIX extreme takes precedence
        if vvix > self.VVIX_WARNING:
            return VolatilityClassification.VOL_EXTREME
        
        # VIX-based classification
        if vix >= self.VIX_HIGH:
            return VolatilityClassification.VOL_EXTREME
        elif vix >= self.VIX_NORMAL_HIGH:
            return VolatilityClassification.VOL_HIGH
        elif vix >= self.VIX_LOW:
            return VolatilityClassification.VOL_NORMAL
        else:
            return VolatilityClassification.VOL_LOW
    
    def _classify_vol_dynamic(self, vix: float, atr: float) -> VolDynamic:
        """
        Classify volatility direction (expanding, contracting, stable).
        
        Args:
            vix: Current VIX
            atr: Current ATR
            
        Returns:
            VolDynamic classification
        """
        # Check history
        if not self._vix_history or len(self._vix_history) < 2:
            return VolDynamic.VOL_STABLE
        
        # Get previous VIX
        prev_vix = self._vix_history[-2][0]
        vix_change = vix - prev_vix
        vix_pct = (vix_change / prev_vix * 100) if prev_vix > 0 else 0
        
        # Threshold: 5% change
        DYNAMIC_THRESHOLD = 5.0
        
        if vix_pct > DYNAMIC_THRESHOLD:
            return VolDynamic.VOL_EXPANDING
        elif vix_pct < -DYNAMIC_THRESHOLD:
            return VolDynamic.VOL_CONTRACTING
        else:
            return VolDynamic.VOL_STABLE
    
    def _determine_context(
        self, 
        symbol: str, 
        global_regime: Optional[Any]
    ) -> MarketContext:
        """
        Determine what is driving the market (news, flow, or macro).
        
        Args:
            symbol: Stock symbol
            global_regime: Global market regime (if available)
            
        Returns:
            MarketContext
        """
        # Default to FLOW_DRIVEN for now
        # In a full implementation, this would check:
        # - Recent news impact on symbol or market
        # - Fed/economic calendar
        # - Order flow analysis (if available)
        
        try:
            # Check if there's recent news
            from .news_intelligence import NewsIntelligenceService
            news_intel = NewsIntelligenceService()
            news_items = news_intel.get_news_for_symbol(symbol)
            
            # If there are recent high-impact news items, consider it news-driven
            if news_items and len(news_items) > 0:
                return MarketContext.NEWS_DRIVEN
        except Exception:
            pass
        
        # Check for macro events
        try:
            from .macro_intel_service import MacroIntelService
            macro = MacroIntelService()
            macro_intel = macro.get_macro_intel()
            # If impact probability is high, consider it macro-driven
            if macro_intel and macro_intel.impact_probability > 0.7:
                return MarketContext.MACRO_DRIVEN
        except Exception:
            pass
        
        return MarketContext.FLOW_DRIVEN
    
    def _determine_gamma(
        self, 
        trend: TrendClassification, 
        volatility: VolatilityClassification
    ) -> GammaEnvironment:
        """
        Determine gamma environment for options.
        
        Simple heuristic:
        - Choppy + high vol = dealers long gamma (dampens moves)
        - Strong trend + expanding vol = dealers short gamma (amplifies)
        - Normal conditions = neutral gamma
        
        Args:
            trend: Current trend
            volatility: Current volatility regime
            
        Returns:
            GammaEnvironment
        """
        if volatility in [VolatilityClassification.VOL_EXTREME, VolatilityClassification.VOL_HIGH]:
            if trend == TrendClassification.CHOPPY:
                # Choppy + high vol = dealers long gamma (dampens moves)
                return GammaEnvironment.GAMMA_POSITIVE
            elif trend in [TrendClassification.TRENDING_UP, TrendClassification.TRENDING_DOWN]:
                # Strong trend + high vol = dealers short gamma (amplifies)
                return GammaEnvironment.GAMMA_NEGATIVE
        
        return GammaEnvironment.GAMMA_NEUTRAL
    
    def _track_atr_history(self, symbol: str, atr: float):
        """Track ATR history for volume analysis."""
        now = get_market_clock().now()
        if symbol not in self._atr_history:
            self._atr_history[symbol] = []
        
        self._atr_history[symbol].append((atr, now))
        
        # Keep last 100 entries
        if len(self._atr_history[symbol]) > 100:
            self._atr_history[symbol] = self._atr_history[symbol][-100:]
    
    def _ema(self, values: List[float], period: int) -> float:
        """Simple EMA calculation."""
        if not values:
            return 0.0
        if len(values) < period:
            return sum(values) / len(values)
        
        multiplier = 2 / (period + 1)
        ema = sum(values[:period]) / period
        
        for val in values[period:]:
            ema = (val * multiplier) + (ema * (1 - multiplier))
        
        return ema


# =============================================================================
# SINGLETON GETTER
# =============================================================================

_regime_classifier: Optional[RegimeClassifier] = None


def get_regime_classifier() -> RegimeClassifier:
    """
    Get the singleton RegimeClassifier instance.
    
    Returns:
        RegimeClassifier instance
    """
    global _regime_classifier
    if _regime_classifier is None:
        _regime_classifier = RegimeClassifier()
    return _regime_classifier
