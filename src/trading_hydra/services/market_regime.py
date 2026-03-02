"""
=============================================================================
MARKET REGIME SERVICE - Indicator-Based Regime Detection
=============================================================================

Fetches and analyzes market indicators (VIX, VVIX, TNX, DXY, MOVE) to detect
the current market regime. This information is used by trading bots to:
1. Select optimal strategies (Iron Condor vs Straddle vs directional)
2. Adjust position sizes based on volatility environment
3. Provide early warning signals to tighten stops or pause new entries

Indicators Used:
- VIX: CBOE Volatility Index - primary risk-on/risk-off signal
- VVIX: Volatility of VIX - early warning before VIX spikes
- TNX: 10-Year Treasury Yield - rate shock detector
- DXY: US Dollar Index - strong dollar pressures equities
- MOVE: Bond volatility index - often leads equity volatility

Market Regimes:
- RISK_ON: Low volatility, favorable for credit spreads (Iron Condors)
- RISK_OFF: High volatility, favor defensive or long volatility plays
- EXTREME_FEAR: Crisis mode, reduce position sizes or halt new entries
- NEUTRAL: Normal conditions, use directional strategies
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import json

from ..core.logging import get_logger
from ..core.state import get_state, set_state
from ..core.clock import get_market_clock


# =============================================================================
# ENUMS - Regime Classifications
# =============================================================================

class VolatilityRegime(Enum):
    """Volatility environment classification based on VIX levels"""
    VERY_LOW = "very_low"       # VIX < 12: Complacency, good for premium selling
    LOW = "low"                 # VIX 12-15: Calm, favor Iron Condors
    NORMAL = "normal"           # VIX 15-20: Standard environment
    ELEVATED = "elevated"       # VIX 20-25: Caution, reduce position sizes
    HIGH = "high"               # VIX 25-35: Fear, favor Straddles or reduce exposure
    EXTREME = "extreme"         # VIX > 35: Crisis, halt new entries or go cash


class MarketSentiment(Enum):
    """Overall market sentiment derived from multiple indicators"""
    RISK_ON = "risk_on"         # Favorable for credit spreads, full position sizing
    NEUTRAL = "neutral"         # Normal conditions, standard position sizing
    RISK_OFF = "risk_off"       # Defensive, reduced position sizing
    EXTREME_FEAR = "extreme_fear"  # Crisis mode, minimal or no new positions


class RateEnvironment(Enum):
    """Rate environment based on TNX (10-Year yield)"""
    FALLING = "falling"         # Yields dropping, supportive for equities
    STABLE = "stable"           # Yields stable, neutral
    RISING = "rising"           # Yields rising, pressure on growth stocks
    SPIKING = "spiking"         # Rapid yield rise, risk-off for equities


class DollarEnvironment(Enum):
    """Dollar strength environment based on DXY"""
    WEAK = "weak"               # Weak dollar, supportive for risk assets
    NEUTRAL = "neutral"         # Dollar stable
    STRONG = "strong"           # Strong dollar, headwind for equities
    SURGING = "surging"         # Dollar surge, risk-off signal


# =============================================================================
# DATA CLASSES - Regime Analysis Results
# =============================================================================

@dataclass
class MarketRegimeAnalysis:
    """Complete market regime analysis from all indicators"""
    timestamp: datetime
    
    # Raw indicator values
    vix: float                    # Current VIX level
    vvix: float                   # Current VVIX level (if available)
    tnx: float                    # 10-Year yield (if available)
    dxy: float                    # Dollar index (if available)
    move: float                   # MOVE index (if available)
    
    # Derived regimes
    volatility_regime: VolatilityRegime
    sentiment: MarketSentiment
    rate_environment: RateEnvironment
    dollar_environment: DollarEnvironment
    
    # Trading signals
    position_size_multiplier: float   # 0.0-1.0, reduce in high vol
    favor_straddles: bool             # True when expecting big moves
    favor_iron_condors: bool          # True in low/stable volatility
    halt_new_entries: bool            # True in extreme conditions
    tighten_stops: bool               # True when VVIX warns of spike
    
    # Warning flags
    vvix_warning: bool                # VVIX rising while VIX flat
    rate_shock_warning: bool          # Rapid yield increase
    dollar_surge_warning: bool        # Dollar strength hurting risk
    
    # Errors
    errors: List[str]


# =============================================================================
# MARKET REGIME SERVICE
# =============================================================================

class MarketRegimeService:
    """
    Service for detecting market regime using VIX, VVIX, TNX, DXY, MOVE.
    
    Caches results to minimize API calls - only refreshes every N minutes.
    Falls back to reasonable defaults if any indicator unavailable.
    """
    
    # VIX thresholds for volatility regime classification
    VIX_VERY_LOW = 12.0
    VIX_LOW = 15.0
    VIX_NORMAL = 20.0
    VIX_ELEVATED = 25.0
    VIX_HIGH = 35.0
    
    # VVIX threshold for early warning (VVIX typically 80-120)
    VVIX_WARNING_THRESHOLD = 110.0
    
    # TNX thresholds (10-year yield in percent)
    TNX_STABLE_LOW = 3.5
    TNX_STABLE_HIGH = 4.5
    TNX_SPIKE_THRESHOLD = 0.15  # Daily change threshold for "spiking"
    
    # DXY thresholds (Dollar Index typically 90-110)
    DXY_WEAK = 95.0
    DXY_NEUTRAL_LOW = 100.0
    DXY_NEUTRAL_HIGH = 105.0
    DXY_SURGE_THRESHOLD = 1.0  # Daily change threshold
    
    # Cache duration in minutes
    CACHE_DURATION_MINUTES = 5
    
    def __init__(self):
        self._logger = get_logger()
        self._alpaca = None  # Lazy load to avoid circular imports
        self._last_analysis: Optional[MarketRegimeAnalysis] = None
        self._last_fetch_time: Optional[datetime] = None
    
    def _get_alpaca(self):
        """Lazy load Alpaca client to avoid circular imports"""
        if self._alpaca is None:
            from .alpaca_client import get_alpaca_client
            self._alpaca = get_alpaca_client()
        return self._alpaca
    
    def get_regime(self, force_refresh: bool = False) -> MarketRegimeAnalysis:
        """
        Get current market regime analysis.
        
        Uses cached data if available and not stale.
        
        Args:
            force_refresh: Force a new fetch even if cache is valid
            
        Returns:
            MarketRegimeAnalysis with current regime and trading signals
        """
        now = get_market_clock().now()
        
        # Check cache validity
        if not force_refresh and self._last_analysis and self._last_fetch_time:
            cache_age = (now - self._last_fetch_time).total_seconds() / 60
            if cache_age < self.CACHE_DURATION_MINUTES:
                self._logger.log("market_regime_cached", {
                    "cache_age_minutes": round(cache_age, 1)
                })
                return self._last_analysis
        
        # Fetch fresh data
        self._logger.log("market_regime_fetch_start", {})
        analysis = self._analyze_regime()
        
        # Update cache
        self._last_analysis = analysis
        self._last_fetch_time = now
        
        # Log results
        self._logger.log("market_regime_result", {
            "vix": analysis.vix,
            "volatility_regime": analysis.volatility_regime.value,
            "sentiment": analysis.sentiment.value,
            "position_size_multiplier": analysis.position_size_multiplier,
            "halt_new_entries": analysis.halt_new_entries,
            "favor_straddles": analysis.favor_straddles,
            "favor_iron_condors": analysis.favor_iron_condors
        })
        
        return analysis
    
    def _analyze_regime(self) -> MarketRegimeAnalysis:
        """
        Fetch indicators and analyze market regime.
        
        Returns:
            Complete MarketRegimeAnalysis
        """
        errors = []
        
        vix = self._fetch_indicator("VIX", errors) or 18.0
        vvix = self._fetch_indicator("VVIX", errors) or 85.0
        tnx = self._fetch_indicator("TNX", errors) or 4.2
        dxy = self._fetch_indicator("DXY", errors)
        move = self._fetch_indicator("MOVE", errors)
        
        if self._logger:
            self._logger.log("market_regime_fetched", {
                "vix": vix,
                "vvix": vvix,
                "tnx": tnx,
                "vix_is_live": vix != 18.0,
                "vvix_is_live": vvix != 85.0,
                "tnx_is_live": tnx != 4.2,
                "dxy_fetched": dxy > 0,
                "move_fetched": move > 0
            })
        
        # Classify volatility regime from VIX
        volatility_regime = self._classify_volatility(vix)
        
        # Classify rate environment from TNX
        rate_environment = self._classify_rates(tnx)
        
        # Classify dollar environment from DXY
        dollar_environment = self._classify_dollar(dxy)
        
        # Detect warnings
        vvix_warning = self._detect_vvix_warning(vix, vvix)
        rate_shock_warning = self._detect_rate_shock(tnx)
        dollar_surge_warning = self._detect_dollar_surge(dxy)
        
        # Calculate overall sentiment
        sentiment = self._calculate_sentiment(
            volatility_regime, vvix_warning, rate_shock_warning, dollar_surge_warning
        )
        
        # Calculate position size multiplier (reduce in high vol)
        position_size_multiplier = self._calculate_position_multiplier(
            volatility_regime, sentiment
        )
        
        # Determine strategy preferences
        favor_straddles = volatility_regime in [
            VolatilityRegime.HIGH, VolatilityRegime.EXTREME
        ] or vvix_warning
        
        favor_iron_condors = volatility_regime in [
            VolatilityRegime.VERY_LOW, VolatilityRegime.LOW
        ] and not vvix_warning
        
        # Halt new entries in extreme conditions
        halt_new_entries = (
            volatility_regime == VolatilityRegime.EXTREME or
            sentiment == MarketSentiment.EXTREME_FEAR
        )
        
        # Tighten stops when warnings present
        tighten_stops = vvix_warning or rate_shock_warning or dollar_surge_warning
        
        return MarketRegimeAnalysis(
            timestamp=get_market_clock().now(),
            vix=vix,
            vvix=vvix,
            tnx=tnx,
            dxy=dxy,
            move=move,
            volatility_regime=volatility_regime,
            sentiment=sentiment,
            rate_environment=rate_environment,
            dollar_environment=dollar_environment,
            position_size_multiplier=position_size_multiplier,
            favor_straddles=favor_straddles,
            favor_iron_condors=favor_iron_condors,
            halt_new_entries=halt_new_entries,
            tighten_stops=tighten_stops,
            vvix_warning=vvix_warning,
            rate_shock_warning=rate_shock_warning,
            dollar_surge_warning=dollar_surge_warning,
            errors=errors
        )
    
    def _fetch_indicator(self, symbol: str, errors: List[str]) -> float:
        """
        Fetch a single indicator value from Alpaca, with yfinance fallback.
        
        Special handling for indicators that may not be directly tradeable.
        Some indicators like VVIX and MOVE may require alternative data sources.
        Falls back to yfinance for VIX/VVIX/TNX when Alpaca fails.
        
        Args:
            symbol: Indicator symbol (VIX, VVIX, TNX, DXY, MOVE)
            errors: List to append any errors
            
        Returns:
            Current value or 0 if unavailable
        """
        # Try Alpaca first, then yfinance fallback for VIX/VVIX/TNX
        price = self._fetch_from_alpaca(symbol, errors)
        if price > 0:
            return price
        
        # yfinance fallback for key volatility indicators
        price = self._fetch_from_yfinance(symbol)
        if price > 0:
            set_state(f"indicators.{symbol}.last", str(price))
            set_state(f"indicators.{symbol}.timestamp", get_market_clock().now().isoformat())
            return price
        
        # Final fallback to cached value
        stored_key = f"indicators.{symbol}.last"
        stored_val = get_state(stored_key)
        if stored_val:
            try:
                return float(stored_val)
            except (ValueError, TypeError):
                pass
        
        return 0.0
    
    def _fetch_from_alpaca(self, symbol: str, errors: List[str]) -> float:
        """Try to fetch indicator from Alpaca."""
        try:
            alpaca = self._get_alpaca()
            
            # Only tradeable indicators - VIX/VVIX/TNX are indices (not tradeable)
            symbol_map = {
                "DXY": "UUP",  # Dollar proxy ETF
                "MOVE": None
            }
            
            mapped_symbol = symbol_map.get(symbol)
            if mapped_symbol is None:
                return 0.0
            
            quote = alpaca.get_latest_quote(mapped_symbol, asset_class="stock")
            bid = quote.get("bid", 0)
            ask = quote.get("ask", 0)
            price = (bid + ask) / 2 if bid and ask else ask or bid
            
            if price > 0:
                set_state(f"indicators.{symbol}.last", str(price))
                set_state(f"indicators.{symbol}.timestamp", get_market_clock().now().isoformat())
                return price
            
            return 0.0
            
        except Exception:
            return 0.0
    
    def _fetch_from_yfinance(self, symbol: str) -> float:
        """
        Fallback to yfinance for VIX/VVIX/TNX quotes.
        Uses cached results for 5 minutes to avoid rate limits.
        """
        try:
            import yfinance as yf
            
            yf_symbol_map = {
                "VIX": "^VIX",
                "VVIX": "^VVIX",
                "TNX": "^TNX",
                "DXY": "DX-Y.NYB",
                "MOVE": "^MOVE",
            }
            
            yf_symbol = yf_symbol_map.get(symbol)
            if not yf_symbol:
                return 0.0
            
            # Check cache to avoid excessive yfinance calls (5 min TTL)
            cache_key = f"yf_cache.{symbol}"
            cache_ts_key = f"yf_cache_ts.{symbol}"
            cached = get_state(cache_key)
            cached_ts = get_state(cache_ts_key)
            
            if cached and cached_ts:
                try:
                    from datetime import datetime, timedelta
                    ts = datetime.fromisoformat(cached_ts)
                    if datetime.now() - ts < timedelta(minutes=5):
                        return float(cached)
                except (ValueError, TypeError):
                    pass
            
            # Fetch from yfinance
            ticker = yf.Ticker(yf_symbol)
            hist = ticker.history(period="1d")
            
            if not hist.empty:
                price = float(hist["Close"].iloc[-1])
                if price > 0:
                    from datetime import datetime
                    set_state(cache_key, str(price))
                    set_state(cache_ts_key, datetime.now().isoformat())
                    # Log fallback usage (once per cache refresh, not per call)
                    if self._logger:
                        self._logger.log("indicator_yfinance_fallback", {
                            "symbol": symbol,
                            "yf_symbol": yf_symbol,
                            "value": round(price, 2),
                            "source": "yfinance"
                        })
                    return price
            
            return 0.0
            
        except ImportError:
            # yfinance not installed - silent fallback to cached value
            return 0.0
        except Exception:
            return 0.0
    
    def _classify_volatility(self, vix: float) -> VolatilityRegime:
        """
        Classify volatility regime from VIX level.
        
        Args:
            vix: Current VIX value
            
        Returns:
            VolatilityRegime classification
        """
        if vix < self.VIX_VERY_LOW:
            return VolatilityRegime.VERY_LOW
        elif vix < self.VIX_LOW:
            return VolatilityRegime.LOW
        elif vix < self.VIX_NORMAL:
            return VolatilityRegime.NORMAL
        elif vix < self.VIX_ELEVATED:
            return VolatilityRegime.ELEVATED
        elif vix < self.VIX_HIGH:
            return VolatilityRegime.HIGH
        else:
            return VolatilityRegime.EXTREME
    
    def _classify_rates(self, tnx: float) -> RateEnvironment:
        """
        Classify rate environment from 10-year yield.
        
        Args:
            tnx: Current 10-year yield
            
        Returns:
            RateEnvironment classification
        """
        if tnx == 0:
            return RateEnvironment.STABLE
        
        # Get previous value for change detection
        prev_key = "indicators.TNX.previous"
        prev_val = get_state(prev_key)
        
        try:
            prev_tnx = float(prev_val) if prev_val else tnx
        except (ValueError, TypeError):
            prev_tnx = tnx
        
        # Update previous value
        set_state(prev_key, str(tnx))
        
        change = tnx - prev_tnx
        
        if abs(change) > self.TNX_SPIKE_THRESHOLD:
            return RateEnvironment.SPIKING if change > 0 else RateEnvironment.FALLING
        elif tnx < self.TNX_STABLE_LOW:
            return RateEnvironment.FALLING
        elif tnx > self.TNX_STABLE_HIGH:
            return RateEnvironment.RISING
        else:
            return RateEnvironment.STABLE
    
    def _classify_dollar(self, dxy: float) -> DollarEnvironment:
        """
        Classify dollar environment from DXY.
        
        Args:
            dxy: Current dollar index value
            
        Returns:
            DollarEnvironment classification
        """
        if dxy == 0:
            return DollarEnvironment.NEUTRAL
        
        # Get previous value for surge detection
        prev_key = "indicators.DXY.previous"
        prev_val = get_state(prev_key)
        
        try:
            prev_dxy = float(prev_val) if prev_val else dxy
        except (ValueError, TypeError):
            prev_dxy = dxy
        
        # Update previous value
        set_state(prev_key, str(dxy))
        
        change = dxy - prev_dxy
        
        if change > self.DXY_SURGE_THRESHOLD:
            return DollarEnvironment.SURGING
        elif dxy < self.DXY_WEAK:
            return DollarEnvironment.WEAK
        elif dxy > self.DXY_NEUTRAL_HIGH:
            return DollarEnvironment.STRONG
        else:
            return DollarEnvironment.NEUTRAL
    
    def _detect_vvix_warning(self, vix: float, vvix: float) -> bool:
        """
        Detect VVIX early warning signal.
        
        VVIX rising while VIX is flat suggests trouble brewing.
        
        Args:
            vix: Current VIX level
            vvix: Current VVIX level
            
        Returns:
            True if warning condition detected
        """
        if vvix == 0:
            return False
        
        # Warning if VVIX elevated while VIX still normal
        if vvix > self.VVIX_WARNING_THRESHOLD and vix < self.VIX_ELEVATED:
            return True
        
        return False
    
    def _detect_rate_shock(self, tnx: float) -> bool:
        """
        Detect rate shock warning.
        
        Args:
            tnx: Current 10-year yield
            
        Returns:
            True if rate shock detected
        """
        if tnx == 0:
            return False
        
        prev_key = "indicators.TNX.previous"
        prev_val = get_state(prev_key)
        
        try:
            prev_tnx = float(prev_val) if prev_val else tnx
        except (ValueError, TypeError):
            return False
        
        return (tnx - prev_tnx) > self.TNX_SPIKE_THRESHOLD
    
    def _detect_dollar_surge(self, dxy: float) -> bool:
        """
        Detect dollar surge warning.
        
        Args:
            dxy: Current dollar index
            
        Returns:
            True if dollar surge detected
        """
        if dxy == 0:
            return False
        
        prev_key = "indicators.DXY.previous"
        prev_val = get_state(prev_key)
        
        try:
            prev_dxy = float(prev_val) if prev_val else dxy
        except (ValueError, TypeError):
            return False
        
        return (dxy - prev_dxy) > self.DXY_SURGE_THRESHOLD
    
    def _calculate_sentiment(
        self, 
        volatility_regime: VolatilityRegime,
        vvix_warning: bool,
        rate_shock_warning: bool,
        dollar_surge_warning: bool
    ) -> MarketSentiment:
        """
        Calculate overall market sentiment from all signals.
        
        Args:
            volatility_regime: Current vol regime
            vvix_warning: VVIX early warning flag
            rate_shock_warning: Rate shock flag
            dollar_surge_warning: Dollar surge flag
            
        Returns:
            MarketSentiment classification
        """
        # Extreme fear in crisis conditions
        if volatility_regime == VolatilityRegime.EXTREME:
            return MarketSentiment.EXTREME_FEAR
        
        # Count warning signals
        warning_count = sum([vvix_warning, rate_shock_warning, dollar_surge_warning])
        
        # Multiple warnings = risk off
        if warning_count >= 2 or volatility_regime == VolatilityRegime.HIGH:
            return MarketSentiment.RISK_OFF
        
        # Single warning or elevated vol = neutral
        if warning_count >= 1 or volatility_regime == VolatilityRegime.ELEVATED:
            return MarketSentiment.NEUTRAL
        
        # Low volatility with no warnings = risk on
        if volatility_regime in [VolatilityRegime.VERY_LOW, VolatilityRegime.LOW]:
            return MarketSentiment.RISK_ON
        
        return MarketSentiment.NEUTRAL
    
    def _calculate_position_multiplier(
        self, 
        volatility_regime: VolatilityRegime,
        sentiment: MarketSentiment
    ) -> float:
        """
        Calculate position size multiplier based on regime.
        
        Returns a multiplier from 0.0 to 1.0 to scale position sizes.
        
        Args:
            volatility_regime: Current vol regime
            sentiment: Overall sentiment
            
        Returns:
            Position size multiplier (0.0-1.0)
        """
        # Base multiplier from volatility regime
        vol_multipliers = {
            VolatilityRegime.VERY_LOW: 1.0,
            VolatilityRegime.LOW: 1.0,
            VolatilityRegime.NORMAL: 0.8,
            VolatilityRegime.ELEVATED: 0.6,
            VolatilityRegime.HIGH: 0.4,
            VolatilityRegime.EXTREME: 0.0  # No new positions
        }
        
        base = vol_multipliers.get(volatility_regime, 0.5)
        
        # Adjust for sentiment
        if sentiment == MarketSentiment.EXTREME_FEAR:
            return 0.0
        elif sentiment == MarketSentiment.RISK_OFF:
            return base * 0.5
        elif sentiment == MarketSentiment.RISK_ON:
            return min(1.0, base * 1.2)
        
        return base


# =============================================================================
# SINGLETON ACCESS
# =============================================================================

_market_regime_service: Optional[MarketRegimeService] = None


def get_market_regime_service() -> MarketRegimeService:
    """
    Get or create the singleton MarketRegimeService instance.
    
    Returns:
        MarketRegimeService singleton
    """
    global _market_regime_service
    if _market_regime_service is None:
        _market_regime_service = MarketRegimeService()
    return _market_regime_service


def get_current_regime(force_refresh: bool = False) -> MarketRegimeAnalysis:
    """
    Convenience function to get current market regime.
    
    Args:
        force_refresh: Force new data fetch
        
    Returns:
        Current MarketRegimeAnalysis
    """
    return get_market_regime_service().get_regime(force_refresh)


# =============================================================================
# SIMPLE REGIME CLASSIFICATION (for Parameter Resolver)
# =============================================================================

def classify_vix_regime(vix: float) -> str:
    """
    Classify VIX into simple LOW/NORMAL/STRESS categories.
    
    Uses thresholds from regimes.yaml:
    - LOW: VIX < 14
    - NORMAL: 14 <= VIX <= 22
    - STRESS: VIX > 22
    
    Args:
        vix: Current VIX value
        
    Returns:
        Regime name: "LOW", "NORMAL", or "STRESS"
    """
    import os
    import yaml
    
    # Try to load thresholds from config
    try:
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            "config", "regimes.yaml"
        )
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
        thresholds = config.get("vix_thresholds", {})
        low_threshold = thresholds.get("low", 14)
        stress_threshold = thresholds.get("stress", 22)
    except Exception:
        low_threshold = 14
        stress_threshold = 22
    
    if vix < low_threshold:
        return "LOW"
    elif vix > stress_threshold:
        return "STRESS"
    else:
        return "NORMAL"


def get_simple_regime_info() -> dict:
    """
    Get simplified regime info for parameter resolution.
    
    Returns dict with:
    - vix: Current VIX value
    - regime: "LOW", "NORMAL", or "STRESS"
    - modifiers: Dict with delta_multiplier, dte_shift_days, size_multiplier, max_new_trades_per_day
    
    Fail-closed: If VIX unavailable, returns STRESS regime.
    """
    import os
    import yaml
    
    # Load regimes config
    try:
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            "config", "regimes.yaml"
        )
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
    except Exception:
        config = {}
    
    # Get current regime analysis
    try:
        analysis = get_current_regime()
        vix = analysis.vix
    except Exception:
        # Fail-closed: assume STRESS if we can't get VIX
        defaults = config.get("defaults", {})
        vix = defaults.get("missing_vix_value", 25.0)
    
    # Classify regime
    if vix == 0:
        # VIX unavailable - fail closed to STRESS
        defaults = config.get("defaults", {})
        regime = defaults.get("missing_vix_regime", "STRESS")
        vix = defaults.get("missing_vix_value", 25.0)
    else:
        regime = classify_vix_regime(vix)
    
    # Get modifiers for this regime
    modifiers = config.get("modifiers", {}).get(regime, {
        "delta_multiplier": 1.0,
        "dte_shift_days": 0,
        "size_multiplier": 1.0,
        "max_new_trades_per_day": 100
    })
    
    return {
        "vix": vix,
        "regime": regime,
        "modifiers": modifiers
    }
