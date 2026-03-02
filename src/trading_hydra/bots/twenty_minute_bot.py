"""
20-Minute Trader Bot
====================

Implements Jeremy Russell's 20-Minute Trader strategy, focusing on the first
20 minutes after market open (6:30-6:50 AM PST / 9:30-9:50 AM EST).

Key Strategy Elements:
- Opening gap analysis to identify stocks with overnight gaps
- Pattern recognition for reversal and continuation signals
- Quick entry/exit with 2-15 minute max hold time
- Micro-profit targets (0.3-0.5% gains)
- Options execution for leveraged micro-movements

The strategy exploits predictable patterns that emerge during the first
20 minutes as overnight gaps resolve and initial momentum develops.

Safety Features:
- Strict 20-minute trading window
- Auto-close all positions by session end
- Tight stop losses (0.25-0.5%)
- ML signal scoring integration
- Fail-closed design
"""

from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum
from uuid import uuid4
import time

from ..core.logging import get_logger
from ..core.state import get_state, set_state, delete_state, atomic_increment
from ..core.config import load_bots_config, load_settings
from ..core.clock import get_market_clock
from ..services.alpaca_client import get_alpaca_client, AlpacaClient
from ..services.market_regime import get_current_regime
from ..services.decision_tracker import get_decision_tracker
from ..risk.killswitch import get_killswitch_service
from ..services.bar_cache import get_cached_bars, set_cached_bars, CachedBar
from ..services.exitbot import get_exitbot
from ..services.twentymin_prestager import get_twentymin_prestager, TwentyMinutePreStager, StagedOrderStatus
from ..ml.signal_service import MLSignalService
from ..indicators.vwap_posture import (
    VWAPPostureManager, VWAPPosture, get_vwap_posture_manager, PostureDecision,
    detect_liquidity_sweep, compute_order_flow,
)
from ..indicators.fair_value_gap import FairValueGapDetector

try:
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed
    ALPACA_DATA_AVAILABLE = True
except ImportError:
    ALPACA_DATA_AVAILABLE = False
    StockBarsRequest = None  # type: ignore
    TimeFrame = None  # type: ignore
    DataFeed = None  # type: ignore


import re


def _extract_underlying(symbol: str) -> str:
    """Extract the underlying ticker from an options symbol or return as-is for equities.
    Options symbols follow OCC format: ROOT + YYMMDD + C/P + strike
    Examples: BAC260220C00053500 -> BAC, GOOGL260223P00302500 -> GOOGL
    """
    m = re.match(r'^([A-Z]{1,5})\d{6}[CP]\d+$', symbol)
    if m:
        return m.group(1)
    return symbol


class PatternType(Enum):
    """Types of patterns detected in first 20 minutes."""
    GAP_REVERSAL = "gap_reversal"       # Gap up/down that reverses
    GAP_CONTINUATION = "gap_continuation"  # Gap that continues
    FIRST_BAR_BREAKOUT = "first_bar_breakout"  # Break of first 5-min bar
    OPENING_RANGE = "opening_range"      # Break of first 10-min range
    NO_PATTERN = "no_pattern"


class SignalDirection(Enum):
    """Direction of trading signal."""
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"


@dataclass
class TwentyMinuteConfig:
    """
    Configuration for the 20-Minute Trader bot.
    All values come from config/bots.yaml under twentyminute_bot section.
    """
    enabled: bool                        # Whether this bot is active
    tickers: List[str]                   # Stocks to trade (e.g., ["SPY", "QQQ", "AAPL"])
    session_start: str                   # When window opens (e.g., "06:30" PST)
    session_end: str                     # When window closes (e.g., "06:50" PST)
    max_trades_per_day: int              # Maximum trades per day
    max_concurrent_positions: int        # Maximum positions at once
    max_hold_minutes: int                # Maximum hold time (2-15 min)
    min_gap_pct: float                   # Minimum gap size to trade (e.g., 0.3%)
    stop_loss_pct: float                 # Stop loss percentage (0.25-0.5%)
    take_profit_pct: float               # Take profit percentage (0.3-0.5%)
    use_options: bool                    # Use options for entries
    options_max_cost: float              # Max cost per options contract
    min_first_bar_range_pct: float       # Minimum first bar range to consider
    confirmation_bars: int               # Bars to wait for pattern confirmation
    max_gap_pct: float = 100.0           # Maximum gap size (set high - gaps can be real during events)
    ml_enabled: bool = True              # Bot-specific ML toggle (overrides global)
    delegate_exits_to_exitbot: bool = True  # Delegate all exits to ExitBot v2 (sole exit authority)
    vwap_posture_required: bool = False  # If False, VWAP posture doesn't block trades (gap-first strategy)
    trade_execution_start: str = "06:25" # Warmup phase before this time (data gathering only, no new trades)


@dataclass 
class GapAnalysis:
    """Analysis of overnight gap for a symbol."""
    symbol: str
    prev_close: float
    current_price: float
    gap_pct: float
    gap_direction: SignalDirection
    volume_ratio: float                  # Volume vs average
    is_significant: bool


@dataclass
class PatternSignal:
    """Pattern detection result."""
    pattern: PatternType
    direction: SignalDirection
    confidence: float                    # 0-1 confidence score
    entry_price: float
    stop_price: float
    target_price: float
    reason: str


@dataclass
class VWAPMomentumIndicators:
    """
    VWAP Momentum indicators per the 20-minute trading philosophy.
    
    Key components:
    - VWAP: Volume Weighted Average Price for directional bias
    - VWAP Posture: Sticky BULLISH/BEARISH/NEUTRAL states (institutional approach)
    - 9/20 EMA: Trend confirmation and pullback entries
    - RSI(7): Overbought/oversold filter
    - Volume: Breakout confirmation
    """
    vwap: float = 0.0                    # Current VWAP
    ema_9: float = 0.0                   # 9-period EMA
    ema_20: float = 0.0                  # 20-period EMA
    rsi_7: float = 50.0                  # 7-period RSI
    volume_spike: bool = False           # Volume > 1.5x average
    volume_ratio: float = 1.0            # Current volume / average
    price_above_vwap: bool = False       # Price > VWAP
    ema_bullish_cross: bool = False      # 9 EMA > 20 EMA (level — used for entry alignment)
    ema_bearish_cross: bool = False      # 9 EMA < 20 EMA (level — used for entry alignment)
    ema_bullish_cross_event: bool = False  # 9 EMA just crossed above 20 EMA this bar (used for exit)
    ema_bearish_cross_event: bool = False  # 9 EMA just crossed below 20 EMA this bar (used for exit)
    market_aligned: bool = False         # SPY/QQQ confirms direction
    market_direction: str = "neutral"    # SPY/QQQ trend direction
    
    # VWAP Posture Manager outputs (institutional style)
    vwap_posture: str = "neutral"        # Sticky VWAP posture state
    vwap_allow_long: bool = False        # VWAP posture allows longs
    vwap_allow_short: bool = False       # VWAP posture allows shorts
    vwap_distance_pct: float = 0.0       # Distance from VWAP as percentage
    vwap_sigma: float = 0.0              # Position in sigma units
    is_vwap_retest: bool = False         # Price retesting VWAP
    vwap_retest_quality: float = 0.0     # Quality of retest (0-1)
    
    # Gap analysis from posture manager
    gap_state: str = "no_gap"            # GAP_UP, GAP_DOWN, NO_GAP
    gap_fill_status: str = "unfilled"    # FILLED, UNFILLED, PARTIAL
    
    # VWAP sigma bands (from PostureDecision.vwap_level)
    vwap_upper_1sigma: float = 0.0
    vwap_lower_1sigma: float = 0.0
    vwap_upper_2sigma: float = 0.0
    vwap_lower_2sigma: float = 0.0

    # Volume Profile
    vwap_poc: float = 0.0                # Point of Control
    vwap_vah: float = 0.0               # Value Area High
    vwap_val: float = 0.0               # Value Area Low

    # Anchored VWAP (prior day close anchor)
    anchored_vwap: float = 0.0
    price_above_avwap: bool = False

    # Fair Value Gap proximity flags
    fvg_bullish_nearby: bool = False
    fvg_bearish_nearby: bool = False

    # Liquidity sweep detection
    liquidity_sweep_up: bool = False     # Wick above swing high + close back below
    liquidity_sweep_down: bool = False   # Wick below swing low + close back above

    # Order flow approximation
    order_flow_delta: float = 0.0        # bull_vol - bear_vol (positive = buying pressure)
    order_flow_bullish: bool = False     # True when bull volume dominates

    # Computed entry validity
    long_setup_valid: bool = False       # All conditions met for long
    short_setup_valid: bool = False      # All conditions met for short


class TwentyMinuteBot:
    """
    20-Minute Trader bot implementing opening-window pattern trading.
    
    Strategy Overview:
    1. Before market open: Identify stocks with significant overnight gaps
    2. At 6:30 AM PST: Monitor first bars for pattern formation
    3. Pattern detected: Enter with options or shares
    4. Quick exit: Close within 2-15 minutes with micro-profits
    5. By 6:50 AM PST: Flatten all remaining positions
    
    Usage:
        bot = TwentyMinuteBot()
        result = bot.execute(budget=100.0)
    """
    
    def __init__(self):
        """Initialize the 20-Minute Trader bot."""
        self.bot_id = "twentymin_core"
        
        self._logger = get_logger()
        self._alpaca = get_alpaca_client()
        self._config = self._load_config()
        
        self._ml_service = MLSignalService(logger=self._logger)
        settings = load_settings()
        ml_config = settings.get("ml", {})
        # Check for bot-specific ml_enabled override first, then fall back to global
        bot_ml_enabled = self._config.ml_enabled if hasattr(self._config, 'ml_enabled') else None
        if bot_ml_enabled is not None:
            self._ml_enabled = bot_ml_enabled
        else:
            self._ml_enabled = ml_config.get("enabled", False)
        # Use momentum-specific threshold for 20-min bot (stock trading)
        self._ml_min_probability = ml_config.get("momentum_threshold",
                                                  ml_config.get("min_probability", 0.55))
        
        self._first_bars: Dict[str, List[Dict]] = {}
        self._gap_analysis: Dict[str, GapAnalysis] = {}
        self._entry_cooldowns: Dict[str, datetime] = {}  # Track last entry time per symbol
        self._entry_cooldown_seconds: int = 300  # 5 minute cooldown between entries on same symbol
        
        # Pre-staging integration
        self._prestager: Optional[TwentyMinutePreStager] = None
        self._prestaging_ran_today: bool = False
        self._last_prestage_date: Optional[datetime] = None
        
        # Reactive trading - catch opportunities when prestaging is missed
        self._reactive_trades_today: int = 0
        self._last_reactive_trade_time: Optional[datetime] = None
        self._last_reactive_scan_time: Optional[datetime] = None
        self._last_reactive_reset_date: Optional[str] = None  # Store as ISO string for reliable comparison
        self._reactive_config = self._load_reactive_config()
        
        self._logger.log("twentymin_bot_init", {
            "bot_id": self.bot_id,
            "config_loaded": self._config is not None,
            "ml_enabled": self._ml_enabled,
            "ml_threshold": self._ml_min_probability,
            "tickers": self._config.tickers if self._config else []
        })
        
        # STARTUP SANITY CHECK - log critical config for debugging
        self._logger.log("twentymin_startup_sanity", {
            "bot_id": self.bot_id,
            "ml_enabled": self._ml_enabled,
            "ml_threshold": self._ml_min_probability,
            "session_start": self._config.session_start if self._config else "unknown",
            "session_end": self._config.session_end if self._config else "unknown",
            "ticker_count": len(self._config.tickers) if self._config else 0
        })
    
    def _get_stock_bars(self, symbol: str, timeframe: str = "1Min", limit: int = 20) -> List[Any]:
        """
        Fetch recent OHLCV bars for a stock symbol.
        Returns list of bar objects with o, h, l, c, v attributes.
        Uses bar cache first for instant startup, then falls back to API.
        Uses IEX feed for paper trading compatibility (SIP requires paid subscription).
        """
        cached = get_cached_bars(symbol, timeframe, limit)
        if cached and len(cached) >= limit:
            return cached
        
        if not ALPACA_DATA_AVAILABLE or not self._alpaca._stock_data_client:
            return cached if cached else []
        
        try:
            from alpaca.data.timeframe import TimeFrameUnit
            tf_map = {
                "1Min": TimeFrame.Minute,
                "5Min": TimeFrame(5, TimeFrameUnit.Minute),
                "1D": TimeFrame.Day,
                "1Day": TimeFrame.Day
            }
            tf = tf_map.get(timeframe, TimeFrame.Minute)
            
            if timeframe in ["1D", "1Day"]:
                from datetime import datetime, timedelta
                end = datetime.now()
                start = end - timedelta(days=limit + 5)
                
                bars_request = StockBarsRequest(
                    symbol_or_symbols=[symbol],
                    timeframe=tf,
                    start=start,
                    end=end,
                    feed=DataFeed.IEX if DataFeed else None
                )
            else:
                bars_request = StockBarsRequest(
                    symbol_or_symbols=[symbol],
                    timeframe=tf,
                    limit=limit,
                    feed=DataFeed.IEX if DataFeed else None
                )
            
            bars_data = self._alpaca._stock_data_client.get_stock_bars(bars_request)
            
            try:
                bars_list = list(bars_data[symbol])
                result = bars_list[-limit:] if len(bars_list) > limit else bars_list
                
                set_cached_bars(symbol, result, timeframe)
                
                return result
            except (KeyError, TypeError):
                return cached if cached else []
        except Exception as e:
            self._logger.error(f"Failed to get bars for {symbol}: {e}")
            return cached if cached else []
    
    # =========================================================================
    # VWAP MOMENTUM INDICATORS - Per 20-Minute Trading Philosophy
    # =========================================================================
    
    def _compute_vwap(self, bars: List) -> float:
        """
        Compute Volume Weighted Average Price from bars.
        VWAP = Cumulative(Typical Price * Volume) / Cumulative(Volume)
        """
        if not bars:
            return 0.0
        
        cumulative_tp_vol = 0.0
        cumulative_vol = 0.0
        
        for bar in bars:
            typical_price = (float(bar.high) + float(bar.low) + float(bar.close)) / 3
            volume = float(bar.volume)
            cumulative_tp_vol += typical_price * volume
            cumulative_vol += volume
        
        return cumulative_tp_vol / cumulative_vol if cumulative_vol > 0 else 0.0
    
    def _compute_ema(self, prices: List[float], period: int) -> float:
        """
        Compute Exponential Moving Average.
        EMA gives more weight to recent prices for faster response.
        """
        if len(prices) < period:
            return sum(prices) / len(prices) if prices else 0.0
        
        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema
        
        return ema
    
    def _compute_rsi(self, prices: List[float], period: int = 7) -> float:
        """
        Compute Relative Strength Index.
        RSI(7) is used for fast overbought/oversold detection.
        """
        if len(prices) < period + 1:
            return 50.0
        
        gains = []
        losses = []
        
        for i in range(1, len(prices)):
            change = prices[i] - prices[i-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))
        
        if len(gains) < period:
            return 50.0
        
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def _check_market_alignment(self, direction: SignalDirection) -> Tuple[bool, str]:
        """
        Check if SPY/QQQ confirms the trade direction.
        
        Per trading philosophy:
        - For LONG: Market (SPY/QQQ) should not be dropping
        - For SHORT: Market should not be rising
        """
        try:
            spy_bars = self._get_stock_bars("SPY", timeframe="1Min", limit=10)
            
            if not spy_bars or len(spy_bars) < 3:
                return True, "insufficient_data"
            
            closes = [float(b.close) for b in spy_bars]
            recent_change = (closes[-1] - closes[0]) / closes[0] * 100
            
            # SPY moving up = bullish market
            # SPY moving down = bearish market
            if recent_change > 0.05:
                market_direction = "bullish"
            elif recent_change < -0.05:
                market_direction = "bearish"
            else:
                market_direction = "neutral"
            
            # Check alignment
            if direction == SignalDirection.LONG:
                aligned = market_direction != "bearish"
            elif direction == SignalDirection.SHORT:
                aligned = market_direction != "bullish"
            else:
                aligned = True
            
            return aligned, market_direction
            
        except Exception as e:
            self._logger.error(f"Market alignment check failed: {e}")
            return True, "error"
    
    def _compute_momentum_indicators(self, symbol: str, bars: List) -> VWAPMomentumIndicators:
        """
        Compute all VWAP Momentum indicators for a symbol.
        
        Uses the institutional VWAP Posture Manager for sticky posture states
        that override simple price_above_vwap checks.
        
        Returns VWAPMomentumIndicators with all calculated values
        for entry/exit decision making.
        """
        indicators = VWAPMomentumIndicators()
        
        if not bars or len(bars) < 5:
            return indicators
        
        try:
            closes = [float(b.close) for b in bars]
            volumes = [float(b.volume) for b in bars]
            highs = [float(b.high) for b in bars]
            lows = [float(b.low) for b in bars]
            current_price = closes[-1]
            
            # VWAP (simple calculation for backwards compatibility)
            indicators.vwap = self._compute_vwap(bars)
            indicators.price_above_vwap = current_price > indicators.vwap
            
            # VWAP POSTURE MANAGER (Institutional approach)
            # Uses sticky posture states instead of simple price comparison
            try:
                vwap_bars = [
                    {
                        "high": h,
                        "low": l,
                        "close": c,
                        "volume": v
                    }
                    for h, l, c, v in zip(highs, lows, closes, volumes)
                ]
                
                vwap_manager = get_vwap_posture_manager(symbol)
                posture_decision = vwap_manager.evaluate(
                    bars=vwap_bars,
                    current_price=current_price,
                    intraday_bars=vwap_bars,
                    bar_index=len(bars)
                )
                
                # Populate posture fields
                indicators.vwap_posture = posture_decision.posture.value
                indicators.vwap_allow_long = posture_decision.allow_long
                indicators.vwap_allow_short = posture_decision.allow_short
                indicators.vwap_distance_pct = posture_decision.distance_from_vwap_pct
                indicators.vwap_sigma = posture_decision.sigma_position
                indicators.is_vwap_retest = posture_decision.is_vwap_retest
                indicators.vwap_retest_quality = posture_decision.retest_quality
                
                # Update VWAP value from posture manager if available
                if posture_decision.vwap_level and posture_decision.vwap_level.vwap > 0:
                    indicators.vwap = posture_decision.vwap_level.vwap
                    indicators.price_above_vwap = current_price > indicators.vwap
                    # Extract sigma bands for profit-target overrides
                    indicators.vwap_upper_1sigma = posture_decision.vwap_level.upper_1sigma
                    indicators.vwap_lower_1sigma = posture_decision.vwap_level.lower_1sigma
                    indicators.vwap_upper_2sigma = posture_decision.vwap_level.upper_2sigma
                    indicators.vwap_lower_2sigma = posture_decision.vwap_level.lower_2sigma

                # Volume Profile
                indicators.vwap_poc = posture_decision.poc
                indicators.vwap_vah = posture_decision.vah
                indicators.vwap_val = posture_decision.val

                # Anchored VWAP
                indicators.anchored_vwap = posture_decision.anchored_vwap
                indicators.price_above_avwap = posture_decision.price_above_avwap

                # Gap context from posture manager
                if posture_decision.gap_context:
                    indicators.gap_state = posture_decision.gap_context.state.value
                    indicators.gap_fill_status = posture_decision.gap_context.fill_status.value

                # Fair Value Gap proximity detection
                try:
                    _fvg = FairValueGapDetector()
                    indicators.fvg_bullish_nearby = (
                        _fvg.get_nearest_fvg(current_price, "long", vwap_bars) is not None
                    )
                    indicators.fvg_bearish_nearby = (
                        _fvg.get_nearest_fvg(current_price, "short", vwap_bars) is not None
                    )
                except Exception:
                    pass

                # Liquidity sweep detection
                try:
                    sweep = detect_liquidity_sweep(vwap_bars)
                    if sweep["sweep_detected"]:
                        indicators.liquidity_sweep_up = sweep["sweep_direction"] == "above"
                        indicators.liquidity_sweep_down = sweep["sweep_direction"] == "below"
                except Exception:
                    pass

                # Order flow approximation
                try:
                    flow = compute_order_flow(vwap_bars)
                    indicators.order_flow_delta = flow["cumulative_delta"]
                    indicators.order_flow_bullish = flow["bullish_flow"]
                except Exception:
                    pass

            except Exception as e:
                self._logger.error(f"VWAP posture check failed for {symbol}: {e}")
                # FAIL-CLOSED: If VWAP posture check fails, block all directional trades
                # This ensures we don't trade without institutional VWAP authority
                indicators.vwap_allow_long = False
                indicators.vwap_allow_short = False
                indicators.vwap_posture = "error"
                self._logger.log("twentymin_vwap_fail_closed", {
                    "symbol": symbol,
                    "error": str(e),
                    "action": "block_all_directional_trades"
                })
            
            # EMAs
            indicators.ema_9 = self._compute_ema(closes, 9)
            indicators.ema_20 = self._compute_ema(closes, 20)
            indicators.ema_bullish_cross = indicators.ema_9 > indicators.ema_20
            indicators.ema_bearish_cross = indicators.ema_9 < indicators.ema_20
            # Detect actual crossover events (previous bar EMA alignment vs current bar)
            if len(closes) >= 2:
                prev_ema_9 = self._compute_ema(closes[:-1], 9)
                prev_ema_20 = self._compute_ema(closes[:-1], 20)
                indicators.ema_bullish_cross_event = (prev_ema_9 <= prev_ema_20) and (indicators.ema_9 > indicators.ema_20)
                indicators.ema_bearish_cross_event = (prev_ema_9 >= prev_ema_20) and (indicators.ema_9 < indicators.ema_20)
            
            # RSI(7)
            indicators.rsi_7 = self._compute_rsi(closes, 7)
            
            # Volume spike detection (current > 1.5x average)
            avg_volume = sum(volumes[:-1]) / len(volumes[:-1]) if len(volumes) > 1 else volumes[-1]
            indicators.volume_ratio = volumes[-1] / avg_volume if avg_volume > 0 else 1.0
            indicators.volume_spike = indicators.volume_ratio > 1.5
            
            # Get RSI thresholds from config (relaxed defaults for gap plays)
            momentum_config = {}
            if self._config:
                bots_config = load_bots_config()
                momentum_config = bots_config.get("twentyminute_bot", {}).get("momentum", {})
            rsi_overbought = momentum_config.get("rsi_overbought", 85)
            rsi_oversold = momentum_config.get("rsi_oversold", 15)
            
            # Determine setup validity per VWAP Momentum strategy
            # Check if VWAP posture is required for entry (can be bypassed for gap-first strategy)
            vwap_required = self._config.vwap_posture_required if self._config else False
            
            if vwap_required:
                # VWAP POSTURE AUTHORITY: Use posture allow flags instead of simple price check
                # LONG: VWAP allows long, 9 EMA > 20 EMA, RSI not overbought
                indicators.long_setup_valid = (
                    indicators.vwap_allow_long and
                    indicators.ema_bullish_cross and
                    indicators.rsi_7 < rsi_overbought
                )
                
                # SHORT: VWAP allows short, 9 EMA < 20 EMA, RSI not oversold
                indicators.short_setup_valid = (
                    indicators.vwap_allow_short and
                    indicators.ema_bearish_cross and
                    indicators.rsi_7 > rsi_oversold
                )
            else:
                # GAP-FIRST STRATEGY: VWAP posture is informational only, not a gate
                # LONG: 9 EMA > 20 EMA, RSI not overbought (VWAP is logged but doesn't block)
                indicators.long_setup_valid = (
                    indicators.ema_bullish_cross and
                    indicators.rsi_7 < rsi_overbought
                )
                
                # SHORT: 9 EMA < 20 EMA, RSI not oversold (VWAP is logged but doesn't block)
                indicators.short_setup_valid = (
                    indicators.ema_bearish_cross and
                    indicators.rsi_7 > rsi_oversold
                )
            
            self._logger.log("twentymin_momentum_indicators", {
                "symbol": symbol,
                "vwap_required_for_entry": vwap_required,
                "vwap": round(indicators.vwap, 2),
                "vwap_posture": indicators.vwap_posture,
                "vwap_allow_long": indicators.vwap_allow_long,
                "vwap_allow_short": indicators.vwap_allow_short,
                "vwap_distance_pct": round(indicators.vwap_distance_pct, 3),
                "is_vwap_retest": indicators.is_vwap_retest,
                "ema_9": round(indicators.ema_9, 2),
                "ema_20": round(indicators.ema_20, 2),
                "rsi_7": round(indicators.rsi_7, 1),
                "price_above_vwap": indicators.price_above_vwap,
                "ema_bullish": indicators.ema_bullish_cross,
                "volume_spike": indicators.volume_spike,
                "volume_ratio": round(indicators.volume_ratio, 2),
                "long_valid": indicators.long_setup_valid,
                "short_valid": indicators.short_setup_valid,
                "gap_state": indicators.gap_state,
                "gap_fill_status": indicators.gap_fill_status,
                # New advanced indicators
                "vwap_upper_1sigma": round(indicators.vwap_upper_1sigma, 2),
                "vwap_lower_1sigma": round(indicators.vwap_lower_1sigma, 2),
                "poc": round(indicators.vwap_poc, 2),
                "vah": round(indicators.vwap_vah, 2),
                "val": round(indicators.vwap_val, 2),
                "anchored_vwap": round(indicators.anchored_vwap, 2),
                "price_above_avwap": indicators.price_above_avwap,
                "fvg_bullish_nearby": indicators.fvg_bullish_nearby,
                "fvg_bearish_nearby": indicators.fvg_bearish_nearby,
                "liquidity_sweep_up": indicators.liquidity_sweep_up,
                "liquidity_sweep_down": indicators.liquidity_sweep_down,
                "order_flow_delta": round(indicators.order_flow_delta, 0),
                "order_flow_bullish": indicators.order_flow_bullish,
            })
            
        except Exception as e:
            self._logger.error(f"Momentum indicator computation failed for {symbol}: {e}")
        
        return indicators
    
    def _load_config(self) -> Optional[TwentyMinuteConfig]:
        """Load configuration from config/bots.yaml."""
        try:
            bots_config = load_bots_config()
            config = bots_config.get("twentyminute_bot", {})
            
            if not config:
                self._logger.warn("No config found for twentyminute_bot, using defaults")
                return TwentyMinuteConfig(
                    enabled=False,
                    tickers=["SPY", "QQQ", "AAPL"],
                    session_start="06:30",
                    session_end="06:50",
                    max_trades_per_day=5,
                    max_concurrent_positions=3,
                    max_hold_minutes=15,
                    min_gap_pct=0.3,
                    stop_loss_pct=0.5,
                    take_profit_pct=0.5,
                    use_options=True,
                    options_max_cost=2.00,
                    min_first_bar_range_pct=0.15,
                    confirmation_bars=2,
                    ml_enabled=True,  # Default to global setting
                    delegate_exits_to_exitbot=True  # ExitBot v2 as sole exit authority
                )
            
            return TwentyMinuteConfig(
                enabled=config.get("enabled", False),
                tickers=config.get("tickers", ["SPY", "QQQ", "AAPL"]),
                session_start=config.get("session", {}).get("trade_start", "06:30"),
                session_end=config.get("session", {}).get("trade_end", "06:50"),
                max_trades_per_day=config.get("risk", {}).get("max_trades_per_day", 5),
                max_concurrent_positions=config.get("risk", {}).get("max_concurrent_positions", 3),
                max_hold_minutes=config.get("exits", {}).get("max_hold_minutes", 15),
                min_gap_pct=config.get("gap", {}).get("min_gap_pct", 0.3),
                max_gap_pct=config.get("gap", {}).get("max_gap_pct", 15.0),
                stop_loss_pct=config.get("exits", {}).get("stop_loss_pct", 0.5),
                take_profit_pct=config.get("exits", {}).get("take_profit_pct", 0.5),
                use_options=config.get("execution", {}).get("use_options", True),
                options_max_cost=config.get("execution", {}).get("options_max_cost", 2.00),
                min_first_bar_range_pct=config.get("pattern", {}).get("min_first_bar_range_pct", 0.15),
                confirmation_bars=config.get("pattern", {}).get("confirmation_bars", 2),
                ml_enabled=config.get("ml_enabled", True),  # Bot-specific ML toggle
                delegate_exits_to_exitbot=config.get("delegate_exits_to_exitbot", True),  # ExitBot v2 as sole exit authority
                vwap_posture_required=config.get("vwap_posture", {}).get("required_for_entry", False),  # If False, gaps trade regardless of VWAP
                trade_execution_start=config.get("session", {}).get("trade_execution_start", "06:25")
            )
            
        except Exception as e:
            self._logger.error(f"Failed to load twentyminute_bot config: {e}")
            return None
    
    def execute(self, budget: float, halt_new_trades: bool = False) -> Dict[str, Any]:
        """
        Execute one iteration of the 20-minute trading strategy.
        
        Args:
            budget: Dollar amount allocated for this bot
            halt_new_trades: If True, only manage positions, don't open new trades
        
        Returns:
            Dictionary with execution results
        """
        results = {
            "trades_attempted": 0,
            "positions_managed": 0,
            "patterns_detected": 0,
            "gaps_analyzed": 0,
            "errors": [],
            "outside_hours": False
        }
        
        try:
            if not self._config or not self._config.enabled:
                self._logger.log("twentymin_disabled", {"bot_id": self.bot_id})
                results["outside_hours"] = True
                return results
            
            now = get_market_clock().now()
            
            in_session = self._is_in_session()
            pre_session = self._is_pre_session()
            post_session = self._is_post_session()
            in_prestaging_window = self._is_prestaging_window()
            
            results["outside_hours"] = not (in_session or pre_session or post_session or in_prestaging_window)
            
            # =================================================================
            # PRE-STAGING: Submit bracket orders BEFORE market open
            # Run during 6:00-6:30 AM PST window
            # =================================================================
            if in_prestaging_window and not self._prestaging_ran_today:
                self._logger.log("twentymin_prestaging_window", {
                    "time": now.strftime("%H:%M:%S"),
                    "action": "running_prestaging"
                })
                prestage_results = self._run_prestaging()
                results["prestaging"] = prestage_results
                
                if prestage_results.get("orders_staged", 0) > 0:
                    self._logger.log("twentymin_prestaging_complete", {
                        "orders_staged": prestage_results["orders_staged"],
                        "message": "Bracket orders staged with Alpaca - ready for instant execution"
                    })
            
            if in_session:
                current_time = now.strftime("%H:%M")
                exec_start = getattr(self._config, 'trade_execution_start', '06:25')
                in_warmup = current_time < exec_start
                
                self._logger.log("twentymin_session_active", {
                    "bot_id": self.bot_id,
                    "time": now.strftime("%H:%M:%S"),
                    "halt_new_trades": halt_new_trades,
                    "warmup_phase": in_warmup,
                    "trade_execution_start": exec_start
                })
                
                # WARMUP PHASE: Data gathering only (05:30-06:25)
                if in_warmup:
                    self._logger.log("twentymin_warmup_phase", {
                        "time": now.strftime("%H:%M:%S"),
                        "action": "data_gathering",
                        "exec_starts_at": exec_start
                    })
                    try:
                        gap_scan_results = self.run_premarket_gap_scan(display=False)
                        results["warmup_gaps_scanned"] = len(gap_scan_results)
                        results["warmup_significant_gaps"] = len([g for g in gap_scan_results if g.get("is_significant")])
                        self._logger.log("twentymin_warmup_gap_scan", {
                            "gaps_found": len(gap_scan_results),
                            "significant": results["warmup_significant_gaps"],
                            "top_3": [{"symbol": g["symbol"], "gap_pct": round(g["gap_pct"], 2)} for g in gap_scan_results[:3]]
                        })
                    except Exception as e:
                        self._logger.warn(f"Warmup gap scan failed: {e}")
                    
                    # Still manage existing positions during warmup (only TwentyMin's own)
                    positions = self._alpaca.get_positions()
                    my_positions = [p for p in positions if (_extract_underlying(p.symbol) in self._config.tickers or p.symbol in self._config.tickers) and (get_state(f"twentymin.entry.{p.symbol}") or get_state(f"twentymin.entry.{_extract_underlying(p.symbol)}"))]
                    for position in my_positions:
                        try:
                            exit_result = self._manage_position(position)
                            results["positions_managed"] += 1
                        except Exception as e:
                            results["errors"].append(f"Manage {position.symbol}: {e}")
                    
                    return results
                
                # TRADING PHASE: Full execution (06:25+)
                # Sync prestaged order status with Alpaca
                sync_results = self._sync_prestaged_orders()
                if sync_results.get("filled", 0) > 0:
                    results["prestaged_fills"] = sync_results["filled"]
                
                positions = self._alpaca.get_positions()
                all_matching = [p for p in positions if _extract_underlying(p.symbol) in self._config.tickers or p.symbol in self._config.tickers]
                my_positions = [p for p in all_matching if get_state(f"twentymin.entry.{p.symbol}") or get_state(f"twentymin.entry.{_extract_underlying(p.symbol)}")]
                
                for position in my_positions:
                    try:
                        exit_result = self._manage_position(position)
                        results["positions_managed"] += 1
                        if exit_result.get("exited"):
                            self._logger.log("twentymin_position_exited", {
                                "symbol": position.symbol,
                                "reason": exit_result.get("reason")
                            })
                    except Exception as e:
                        results["errors"].append(f"Manage {position.symbol}: {e}")
                
                if halt_new_trades:
                    self._logger.log("twentymin_halt_active", {
                        "bot_id": self.bot_id,
                        "action": "manage_positions_only"
                    })
                    return results
                
                killswitch = get_killswitch_service()
                ks_allowed, ks_reason = killswitch.is_entry_allowed("twentyminute")
                if not ks_allowed:
                    self._logger.log("twentymin_killswitch_blocked", {
                        "bot_id": self.bot_id,
                        "reason": ks_reason
                    })
                    return results
                
                if len(my_positions) < self._config.max_concurrent_positions:
                    if self._can_trade_today():
                        # ============================================================
                        # PHASE 1: Collect ALL opportunities (gaps + patterns)
                        # ============================================================
                        all_gaps = []
                        patterns_found = []
                        trade_decisions = []
                        trade_candidates = []  # NEW: Candidates ranked by opportunity quality
                        
                        # Fetch ALL Alpaca positions once to prevent stacking on same underlying
                        all_positions = self._alpaca.get_positions()
                        all_underlying_held = set(_extract_underlying(p.symbol) for p in all_positions)
                        
                        for ticker in self._config.tickers:
                            if any(p.symbol == ticker or _extract_underlying(p.symbol) == ticker for p in my_positions):
                                continue
                            
                            # Block if ANY position (from any bot) already holds this underlying
                            if ticker in all_underlying_held:
                                self._logger.log("twentymin_already_has_position", {
                                    "ticker": ticker,
                                    "reason": "Already have Alpaca position in this underlying"
                                })
                                continue
                            
                            try:
                                gap = self._analyze_gap(ticker)
                                results["gaps_analyzed"] += 1
                                
                                if gap:
                                    all_gaps.append({
                                        "ticker": ticker,
                                        "gap_pct": round(gap.gap_pct, 3),
                                        "direction": gap.gap_direction.value,
                                        "volume_ratio": round(gap.volume_ratio, 2),
                                        "is_significant": gap.is_significant
                                    })
                                
                                if gap and gap.is_significant:
                                    # Double-check: no position in this ticker (options included)
                                    has_existing_position = ticker in all_underlying_held
                                    if has_existing_position:
                                        self._logger.log("twentymin_already_has_position", {
                                            "ticker": ticker,
                                            "reason": "Already have position in this symbol"
                                        })
                                        continue
                                    
                                    self._gap_analysis[ticker] = gap
                                    
                                    pattern = self._detect_pattern(ticker, gap)
                                    
                                    if pattern and pattern.pattern != PatternType.NO_PATTERN:
                                        results["patterns_detected"] += 1
                                        patterns_found.append({
                                            "ticker": ticker,
                                            "pattern": pattern.pattern.value,
                                            "direction": pattern.direction.value,
                                            "confidence": round(pattern.confidence, 2),
                                            "reason": pattern.reason
                                        })
                                        
                                        ml_score = 1.0
                                        if self._ml_enabled:
                                            ml_score = self._score_with_ml(ticker, pattern, gap)
                                            if ml_score < self._ml_min_probability:
                                                trade_decisions.append({
                                                    "ticker": ticker,
                                                    "action": "SKIP",
                                                    "reason": f"ML score {ml_score:.2f} < threshold {self._ml_min_probability}"
                                                })
                                                self._logger.log("twentymin_ml_gate", {
                                                    "ticker": ticker,
                                                    "pattern": pattern.pattern.value,
                                                    "ml_score": round(ml_score, 3),
                                                    "threshold": self._ml_min_probability,
                                                    "passed": False,
                                                    "action": "BLOCKED"
                                                })
                                                continue
                                        
                                        # Calculate opportunity score for ranking
                                        # Primary: gap magnitude, Secondary: volume ratio, Tertiary: pattern confidence
                                        opportunity_score = (
                                            abs(gap.gap_pct) * 100 +      # Gap magnitude (weighted heavily)
                                            gap.volume_ratio * 10 +        # Volume confirmation
                                            pattern.confidence * 5         # Pattern quality
                                        )
                                        
                                        # Log ML gate PASSED
                                        if self._ml_enabled:
                                            self._logger.log("twentymin_ml_gate", {
                                                "ticker": ticker,
                                                "pattern": pattern.pattern.value,
                                                "ml_score": round(ml_score, 3),
                                                "threshold": self._ml_min_probability,
                                                "passed": True,
                                                "action": "ALLOWED"
                                            })
                                        
                                        # Add to candidates list (don't trade yet!)
                                        trade_candidates.append({
                                            "ticker": ticker,
                                            "gap": gap,
                                            "pattern": pattern,
                                            "ml_score": ml_score,
                                            "opportunity_score": opportunity_score,
                                            "gap_pct": gap.gap_pct,
                                            "volume_ratio": gap.volume_ratio
                                        })
                                    else:
                                        trade_decisions.append({
                                            "ticker": ticker,
                                            "action": "SKIP",
                                            "reason": f"No valid pattern (gap={gap.gap_pct:.2f}%)"
                                        })
                                            
                            except Exception as e:
                                results["errors"].append(f"Pattern {ticker}: {e}")
                        
                        # ============================================================
                        # PHASE 1B: BREADTH SELLOFF DETECTION
                        # Counts how many tickers are gapping in same direction.
                        # If broad selloff/rally detected, adjusts trade bias and limits.
                        # ============================================================
                        from ..core.config import load_bots_config
                        _bots_cfg = load_bots_config()
                        _twentymin_cfg = _bots_cfg.get("twentyminute_bot", {})
                        selloff_cfg = _twentymin_cfg.get("selloff_protection", {})
                        selloff_enabled = selloff_cfg.get("enabled", True)
                        selloff_breadth_threshold = selloff_cfg.get("breadth_threshold", 0.65)
                        selloff_min_gap_count = selloff_cfg.get("min_gap_count", 10)
                        selloff_mode = selloff_cfg.get("mode", "reduce")
                        selloff_max_concurrent_mult = selloff_cfg.get("max_concurrent_mult", 0.5)
                        selloff_favor_direction = selloff_cfg.get("favor_direction", True)
                        selloff_vix_boost = selloff_cfg.get("vix_boost", True)

                        breadth_state = {"detected": False, "type": "none", "ratio": 0.0}
                        if selloff_enabled and all_gaps:
                            sig_gaps = [g for g in all_gaps if g.get("is_significant", False)]
                            if len(sig_gaps) >= selloff_min_gap_count:
                                down_count = sum(1 for g in sig_gaps if g["gap_pct"] < 0)
                                up_count = sum(1 for g in sig_gaps if g["gap_pct"] > 0)
                                total = len(sig_gaps)
                                down_ratio = down_count / total if total else 0
                                up_ratio = up_count / total if total else 0

                                avg_gap_magnitude = sum(abs(g["gap_pct"]) for g in sig_gaps) / total if total else 0

                                if down_ratio >= selloff_breadth_threshold:
                                    breadth_state = {
                                        "detected": True,
                                        "type": "selloff",
                                        "ratio": round(down_ratio, 3),
                                        "down_count": down_count,
                                        "up_count": up_count,
                                        "total_significant": total,
                                        "avg_gap_magnitude": round(avg_gap_magnitude, 2),
                                        "mode": selloff_mode,
                                    }
                                elif up_ratio >= selloff_breadth_threshold:
                                    breadth_state = {
                                        "detected": True,
                                        "type": "broad_rally",
                                        "ratio": round(up_ratio, 3),
                                        "down_count": down_count,
                                        "up_count": up_count,
                                        "total_significant": total,
                                        "avg_gap_magnitude": round(avg_gap_magnitude, 2),
                                        "mode": selloff_mode,
                                    }

                                if breadth_state["detected"]:
                                    self._logger.log("twentymin_breadth_selloff", breadth_state)
                                    print(f"\n🚨 [TwentyMinuteBot] BREADTH ALERT: {breadth_state['type'].upper()} detected!")
                                    print(f"   {down_count} down / {up_count} up out of {total} significant gaps (avg magnitude: {avg_gap_magnitude:.1f}%)")
                                    print(f"   Selloff mode: {selloff_mode}")

                                    if selloff_mode == "halt":
                                        self._logger.log("twentymin_selloff_halt", {"reason": "breadth_selloff_halt", "state": breadth_state})
                                        print(f"   ⛔ HALTING all new entries due to broad {breadth_state['type']}")
                                        trade_candidates = []
                                    elif selloff_mode == "reduce":
                                        orig_max = self._config.max_concurrent_positions - len(my_positions)
                                        reduced_max = max(1, int(orig_max * selloff_max_concurrent_mult))
                                        self._logger.log("twentymin_selloff_reduce", {
                                            "original_max_new": orig_max,
                                            "reduced_max_new": reduced_max,
                                            "multiplier": selloff_max_concurrent_mult,
                                        })
                                        print(f"   📉 Reducing max new positions from {orig_max} to {reduced_max}")
                                    elif selloff_mode == "shorts_only" and breadth_state["type"] == "selloff":
                                        before_count = len(trade_candidates)
                                        trade_candidates = [c for c in trade_candidates if c["pattern"].direction.value in ("short", "SHORT")]
                                        self._logger.log("twentymin_selloff_shorts_only", {
                                            "before": before_count,
                                            "after": len(trade_candidates),
                                        })
                                        print(f"   🔻 Shorts-only mode: filtered {before_count} → {len(trade_candidates)} candidates")
                                    elif selloff_mode == "favor_shorts" and breadth_state["type"] == "selloff":
                                        for c in trade_candidates:
                                            if c["pattern"].direction.value in ("short", "SHORT"):
                                                c["opportunity_score"] *= 1.5
                                            else:
                                                c["opportunity_score"] *= 0.5
                                        self._logger.log("twentymin_selloff_favor_shorts", {"candidates": len(trade_candidates)})
                                        print(f"   🔻 Favoring shorts: boosted short scores, reduced long scores")

                        # ============================================================
                        # PHASE 2: Rank candidates and trade BEST opportunities first
                        # ============================================================
                        if trade_candidates:
                            # Sort by opportunity score (highest first = best gaps)
                            ranked_candidates = sorted(
                                trade_candidates, 
                                key=lambda x: x["opportunity_score"], 
                                reverse=True
                            )
                            
                            # Log the ranking
                            self._logger.log("twentymin_opportunity_ranking", {
                                "total_candidates": len(ranked_candidates),
                                "ranking": [
                                    {
                                        "rank": i + 1,
                                        "ticker": c["ticker"],
                                        "gap_pct": round(c["gap_pct"], 2),
                                        "volume_ratio": round(c["volume_ratio"], 2),
                                        "opportunity_score": round(c["opportunity_score"], 1)
                                    }
                                    for i, c in enumerate(ranked_candidates)
                                ]
                            })
                            
                            print(f"\n🏆 [TwentyMinuteBot] Opportunity Ranking (best first):")
                            for i, c in enumerate(ranked_candidates[:5]):
                                print(f"   #{i+1}: {c['ticker']} - gap={c['gap_pct']:+.2f}% vol={c['volume_ratio']:.1f}x score={c['opportunity_score']:.0f}")
                            
                            max_new_trades = self._config.max_concurrent_positions - len(my_positions)
                            if breadth_state["detected"] and selloff_mode == "reduce":
                                max_new_trades = max(1, int(max_new_trades * selloff_max_concurrent_mult))
                            trades_executed = 0
                            
                            # Execute trades in ranked order (best first)
                            for candidate in ranked_candidates:
                                if trades_executed >= max_new_trades:
                                    trade_decisions.append({
                                        "ticker": candidate["ticker"],
                                        "action": "SKIP",
                                        "reason": f"Position limit reached (executed {trades_executed}/{max_new_trades})"
                                    })
                                    continue
                                
                                ticker = candidate["ticker"]
                                gap = candidate["gap"]
                                pattern = candidate["pattern"]
                                ml_score = candidate["ml_score"]
                                
                                # SAFETY CHECK: Entry cooldown to prevent rapid-fire entries
                                now = datetime.utcnow()
                                last_entry = self._entry_cooldowns.get(ticker)
                                if last_entry:
                                    seconds_since = (now - last_entry).total_seconds()
                                    if seconds_since < self._entry_cooldown_seconds:
                                        self._logger.log("twentymin_entry_cooldown_active", {
                                            "ticker": ticker,
                                            "seconds_since_last": round(seconds_since, 1),
                                            "cooldown_seconds": self._entry_cooldown_seconds,
                                            "reason": "Cooldown active - preventing rapid-fire entry"
                                        })
                                        trade_decisions.append({
                                            "ticker": ticker,
                                            "action": "SKIP",
                                            "reason": f"Cooldown active ({seconds_since:.0f}s < {self._entry_cooldown_seconds}s)"
                                        })
                                        continue
                                
                                # ====================================================
                                # QUALITY CHECKLIST GATE (Trade-Bot style)
                                # VWAP posture + RSI zone + volume confirmation
                                # Prevents low-quality entries that go underwater
                                # Config: quality_gate.mode = "strict" (default), "loose" (2/4), "fail_open" (skip gate)
                                # ====================================================
                                from trading_hydra.core.config import load_bots_config as _load_bots_cfg
                                qg_cfg = _load_bots_cfg().get("twentyminute_bot", {}).get("quality_gate", {})
                                qg_mode = qg_cfg.get("mode", "strict")
                                
                                checklist_details = {"score": 0, "max_score": 0, "passed": True, "fail_reason": "gate_disabled"}
                                if qg_mode != "fail_open":
                                    checklist_passed, checklist_details = self._run_entry_quality_checklist(
                                        ticker, pattern, gap
                                    )
                                    
                                    if not checklist_passed:
                                        self._logger.log("twentymin_quality_gate_blocked", {
                                            "ticker": ticker,
                                            "checklist": checklist_details,
                                            "reason": "Failed entry quality checklist",
                                            "mode": qg_mode
                                        })
                                        trade_decisions.append({
                                            "ticker": ticker,
                                            "action": "SKIP",
                                            "reason": f"Quality gate: {checklist_details.get('fail_reason', 'checklist failed')}"
                                        })
                                        print(f"   BLOCKED {ticker}: quality gate failed - {checklist_details.get('fail_reason', '')}")
                                        continue
                                
                                # Log trade decision BEFORE executing
                                trade_decisions.append({
                                    "ticker": ticker,
                                    "action": "TRADE",
                                    "reason": f"RANKED #{ranked_candidates.index(candidate)+1}: gap={gap.gap_pct:.2f}%, score={candidate['opportunity_score']:.0f}, quality={checklist_details.get('score',0)}/{checklist_details.get('max_score',0)}"
                                })
                                self._logger.log("twentymin_trade_decision", {
                                    "ticker": ticker,
                                    "action": "EXECUTING_TRADE",
                                    "rank": ranked_candidates.index(candidate) + 1,
                                    "opportunity_score": round(candidate["opportunity_score"], 1),
                                    "gap_pct": round(gap.gap_pct, 3),
                                    "pattern": pattern.pattern.value,
                                    "pattern_confidence": round(pattern.confidence, 2),
                                    "ml_score": round(ml_score, 3) if self._ml_enabled else "disabled",
                                    "entry_price": pattern.entry_price,
                                    "stop_price": pattern.stop_price,
                                    "target_price": pattern.target_price,
                                    "quality_checklist": checklist_details
                                })
                                
                                trade_result = self._execute_entry(
                                    ticker, pattern, gap, budget
                                )
                                
                                if trade_result.get("success"):
                                    results["trades_attempted"] += 1
                                    trades_executed += 1
                                    self._record_trade(ticker)
                                    # Update entry cooldown
                                    self._entry_cooldowns[ticker] = datetime.utcnow()
                                else:
                                    results["errors"].append(
                                        f"{ticker}: {trade_result.get('error')}"
                                    )
                        
                        # Log comprehensive gap summary
                        sorted_gaps = sorted(all_gaps, key=lambda x: abs(x["gap_pct"]), reverse=True)
                        significant_gaps = [g for g in sorted_gaps if g["is_significant"]]
                        
                        self._logger.log("twentymin_gap_summary", {
                            "total_analyzed": len(all_gaps),
                            "significant_count": len(significant_gaps),
                            "top_gaps": sorted_gaps[:10],  # Top 10 by magnitude
                            "significant_gaps": significant_gaps
                        })
                        
                        # Console output for gap summary
                        print(f"\n📊 [TwentyMinuteBot] Gap Analysis Summary:")
                        print(f"   Analyzed: {len(all_gaps)} tickers | Significant (>{self._config.min_gap_pct}%): {len(significant_gaps)}")
                        if sorted_gaps[:5]:
                            print(f"   Top 5 Gaps:")
                            for g in sorted_gaps[:5]:
                                sig = "✓" if g["is_significant"] else "✗"
                                print(f"     {sig} {g['ticker']}: {g['gap_pct']:+.2f}% ({g['direction']}) vol_ratio={g['volume_ratio']:.1f}x")
                        
                        # Log pattern summary
                        if patterns_found:
                            self._logger.log("twentymin_pattern_summary", {
                                "patterns_detected": len(patterns_found),
                                "patterns": patterns_found
                            })
                            print(f"\n🔍 [TwentyMinuteBot] Patterns Detected: {len(patterns_found)}")
                            for p in patterns_found:
                                print(f"     {p['ticker']}: {p['pattern']} ({p['direction']}) conf={p['confidence']:.0%}")
                        
                        # Log trade decisions summary
                        if trade_decisions:
                            trades_taken = [d for d in trade_decisions if d["action"] == "TRADE"]
                            trades_skipped = [d for d in trade_decisions if d["action"] == "SKIP"]
                            self._logger.log("twentymin_trade_summary", {
                                "trades_taken": len(trades_taken),
                                "trades_skipped": len(trades_skipped),
                                "decisions": trade_decisions
                            })
                            print(f"\n📈 [TwentyMinuteBot] Trade Decisions: {len(trades_taken)} taken, {len(trades_skipped)} skipped")
                            for d in trade_decisions:
                                icon = "✅" if d["action"] == "TRADE" else "⏭️"
                                print(f"     {icon} {d['ticker']}: {d['action']} - {d['reason']}")
                        elif significant_gaps:
                            self._logger.log("twentymin_trade_summary", {
                                "trades_taken": 0,
                                "trades_skipped": 0,
                                "reason": "Significant gaps found but no valid patterns detected"
                            })
                            print(f"\n📈 [TwentyMinuteBot] No trades: Significant gaps found but no valid patterns")
                        else:
                            self._logger.log("twentymin_trade_summary", {
                                "trades_taken": 0,
                                "trades_skipped": 0,
                                "reason": f"No significant gaps (need >{self._config.min_gap_pct}%)"
                            })
                            print(f"\n📈 [TwentyMinuteBot] No trades: No gaps above {self._config.min_gap_pct}% threshold")
                    else:
                        self._logger.log("twentymin_daily_limit", {"bot_id": self.bot_id})
                else:
                    # At max positions - just log and wait
                    self._logger.log("twentymin_at_max_positions", {
                        "positions": len(my_positions),
                        "max_positions": self._config.max_concurrent_positions,
                        "action": "waiting_for_exits"
                    })
                
                # REACTIVE TRADING: Scan for dip opportunities when normal gap analysis didn't trade
                # This runs after position management, regardless of position count, but respects limits
                if results["trades_attempted"] == 0 and self._should_run_reactive_scan():
                    positions = self._alpaca.get_positions()
                    my_positions = [p for p in positions if (_extract_underlying(p.symbol) in self._config.tickers or p.symbol in self._config.tickers) and (get_state(f"twentymin.entry.{p.symbol}") or get_state(f"twentymin.entry.{_extract_underlying(p.symbol)}"))]
                    
                    if len(my_positions) < self._config.max_concurrent_positions:
                        self._logger.log("twentymin_reactive_scan", {
                            "reason": "no_gap_trades_checking_dips",
                            "positions": len(my_positions),
                            "max_positions": self._config.max_concurrent_positions
                        })
                        
                        opportunities = self._scan_for_reactive_opportunities(my_positions)
                        
                        if opportunities:
                            best = opportunities[0]
                            self._logger.log("twentymin_reactive_opportunity", {
                                "ticker": best["ticker"],
                                "dip_pct": best["dip_pct"],
                                "gap_pct": best["gap_pct"],
                                "score": best["score"]
                            })
                            
                            # Try reactive trade
                            trade_result = self._execute_reactive_trade(best)
                            if trade_result.get("success"):
                                results["trades_attempted"] += 1
                                results["reactive_trade"] = best["ticker"]
            
            elif self._is_pre_session():
                self._logger.log("twentymin_pre_session", {
                    "bot_id": self.bot_id,
                    "analyzing_gaps": True
                })
                for ticker in self._config.tickers:
                    try:
                        gap = self._analyze_gap(ticker)
                        if gap:
                            self._gap_analysis[ticker] = gap
                            results["gaps_analyzed"] += 1
                    except Exception as e:
                        results["errors"].append(f"Pre-gap {ticker}: {e}")
                
                # Log gap summary after pre-session analysis
                self._log_gap_summary("pre_session")
            
            elif self._is_post_session():
                positions = self._alpaca.get_positions()
                my_positions = [p for p in positions if (_extract_underlying(p.symbol) in self._config.tickers or p.symbol in self._config.tickers) and (get_state(f"twentymin.entry.{p.symbol}") or get_state(f"twentymin.entry.{_extract_underlying(p.symbol)}"))]
                
                for position in my_positions:
                    try:
                        self._flatten_position(position, "session_end")
                        results["positions_managed"] += 1
                    except Exception as e:
                        results["errors"].append(f"Flatten {position.symbol}: {e}")
                
                # Cleanup prestaged orders at end of session
                self._cleanup_prestaged_orders()
                self._prestaging_ran_today = False  # Reset for next day
            
            # Process pending brackets (fill monitor for options)
            try:
                bracket_results = self.process_pending_brackets()
                if bracket_results.get("exits_placed", 0) > 0:
                    self._logger.log("twentymin_brackets_processed", bracket_results)
            except Exception as bracket_err:
                self._logger.warn(f"Bracket processing error: {bracket_err}")
            
            results["breadth_state"] = breadth_state if 'breadth_state' in locals() else {"detected": False}
            self._logger.log("twentymin_execution_complete", {
                "bot_id": self.bot_id,
                "results": results
            })
            
        except Exception as e:
            self._logger.error(f"TwentyMinuteBot execution failed: {e}")
            results["errors"].append(str(e))
        
        # EMIT DECISION RECORD - Per symbol/ticker per loop for audit trail
        try:
            tracker = get_decision_tracker()
            loop_id = get_state("loop_id", 0)
            tracker.log_decision_record(
                bot_id=self.bot_id,
                symbol="MULTI",
                loop_number=loop_id,
                signal_inputs={"config": "enabled" if self._config else "disabled"},
                gating_results={"trades_attempted": results.get("trades_attempted", 0)},
                budget_used=0.0,
                final_action="TRADE" if results.get("trades_attempted", 0) > 0 else "NO_TRADE",
                reason=f"Managed {results.get('positions_managed', 0)} positions, {results.get('trades_attempted', 0)} trades"
            )
        except Exception:
            pass
        
        return results
    
    def _is_in_session(self) -> bool:
        """Check if we're in the 20-minute trading window."""
        if not self._config:
            return False
            
        now = get_market_clock().now()
        current_time = now.strftime("%H:%M")
        
        return self._config.session_start <= current_time < self._config.session_end
    
    def _is_pre_session(self) -> bool:
        """Check if we're in pre-session (gap analysis and warm-up time).
        
        Pre-session starts 30 minutes before trade_start for data warm-up.
        """
        if not self._config:
            return False
            
        now = get_market_clock().now()
        current_time = now.strftime("%H:%M")
        
        # Calculate pre-session start (30 min before trade_start)
        try:
            start_hour, start_min = map(int, self._config.session_start.split(":"))
            pre_start_min = start_min - 30
            pre_start_hour = start_hour
            if pre_start_min < 0:
                pre_start_min += 60
                pre_start_hour -= 1
            pre_start = f"{pre_start_hour:02d}:{pre_start_min:02d}"
        except (ValueError, AttributeError):
            pre_start = "05:30"  # Fallback
            
        return pre_start <= current_time < self._config.session_start
    
    def _is_prestaging_window(self) -> bool:
        """Check if we're in the prestaging window (6:00-6:30 AM PST).
        
        During this window, we scan for gaps and stage bracket orders
        with Alpaca for instant execution at market open.
        """
        if not self._config:
            return False
        
        now = get_market_clock().now()
        current_time = now.strftime("%H:%M")
        
        # Prestaging window: 06:00 - 06:30 PST
        return "06:00" <= current_time < "06:30"
    
    def _run_prestaging(self) -> Dict[str, Any]:
        """
        Run prestaging logic to submit bracket orders before market open.
        
        This scans for gap setups and stages bracket orders with Alpaca
        so they execute instantly when price triggers are hit.
        
        Returns:
            Dictionary with prestaging results
        """
        results = {
            "ran": False,
            "orders_staged": 0,
            "symbols_scanned": 0,
            "errors": []
        }
        
        try:
            # Check if we already ran prestaging today
            now = get_market_clock().now()
            today = now.date()
            
            if self._last_prestage_date and self._last_prestage_date.date() == today:
                self._logger.log("twentymin_prestaging_already_ran", {
                    "date": str(today),
                    "skip_reason": "Already ran today"
                })
                return results
            
            # Initialize prestager if needed
            if self._prestager is None:
                self._prestager = get_twentymin_prestager()
            
            self._logger.log("twentymin_prestaging_start", {
                "time": now.strftime("%H:%M:%S"),
                "tickers": self._config.tickers if self._config else []
            })
            
            # Run the prestager scan
            staged_orders = self._prestager.scan_and_stage()
            
            results["ran"] = True
            results["orders_staged"] = len(staged_orders)
            results["symbols_scanned"] = len(self._config.tickers) if self._config else 0
            
            self._last_prestage_date = now
            self._prestaging_ran_today = True
            
            # Log staged orders
            for order in staged_orders:
                self._logger.log("twentymin_order_prestaged", {
                    "id": order.id,
                    "symbol": order.symbol,
                    "side": order.side,
                    "entry_price": order.entry_price,
                    "stop_price": order.stop_price,
                    "target_price": order.target_price,
                    "qty": order.qty,
                    "gap_pct": order.gap_pct,
                    "alpaca_order_id": order.alpaca_order_id,
                    "status": order.status.value
                })
            
            if staged_orders:
                print(f"\n🎯 [TwentyMinuteBot] Pre-staged {len(staged_orders)} bracket orders:")
                for order in staged_orders:
                    print(f"   {order.symbol}: Entry ${order.entry_price:.2f} | Stop ${order.stop_price:.2f} | Target ${order.target_price:.2f} | Qty {order.qty}")
            
        except Exception as e:
            self._logger.error(f"Prestaging failed: {e}")
            results["errors"].append(str(e))
        
        return results
    
    def _sync_prestaged_orders(self) -> Dict[str, Any]:
        """
        Sync status of prestaged orders with Alpaca.
        
        Call during trading session to track fills.
        """
        results = {"synced": 0, "filled": 0, "cancelled": 0}
        
        if self._prestager is None:
            return results
        
        try:
            self._prestager.sync_order_status()
            
            # Count by status
            for order in self._prestager.get_staged_orders():
                results["synced"] += 1
                if order.status == StagedOrderStatus.FILLED:
                    results["filled"] += 1
                elif order.status in (StagedOrderStatus.CANCELLED, StagedOrderStatus.EXPIRED):
                    results["cancelled"] += 1
        
        except Exception as e:
            self._logger.error(f"Failed to sync prestaged orders: {e}")
        
        return results
    
    def _cleanup_prestaged_orders(self):
        """Clean up expired/unfilled prestaged orders at end of session."""
        if self._prestager is None:
            return
        
        try:
            self._prestager.cleanup_expired_orders()
            self._logger.log("twentymin_prestaged_cleanup", {
                "action": "cleanup_complete"
            })
        except Exception as e:
            self._logger.error(f"Failed to cleanup prestaged orders: {e}")
    
    def get_staged_orders_status(self) -> List[Dict[str, Any]]:
        """Get current status of all staged orders."""
        if self._prestager is None:
            return []
        
        return [order.to_dict() for order in self._prestager.get_staged_orders()]
    
    # =========================================================================
    # REACTIVE TRADING - Catch opportunities when prestaging is missed
    # =========================================================================
    
    def _load_reactive_config(self) -> Dict[str, Any]:
        """Load reactive trading configuration from bots.yaml."""
        try:
            bots_config = load_bots_config()
            twentymin_config = bots_config.get("twentyminute_bot", {})
            reactive_cfg = twentymin_config.get("reactive_trading", {})
            
            return {
                "enabled": reactive_cfg.get("enabled", True),
                "scan_interval_minutes": reactive_cfg.get("scan_interval_minutes", 5),
                "min_gap_pct": reactive_cfg.get("min_gap_pct", 0.75),
                "max_gap_pct": reactive_cfg.get("max_gap_pct", 5.0),
                "min_dip_pct": reactive_cfg.get("min_dip_pct", 0.5),
                "max_spread_pct": reactive_cfg.get("max_spread_pct", 0.15),
                "volume_spike_threshold": reactive_cfg.get("volume_spike_threshold", 1.5),
                "require_vwap_support": reactive_cfg.get("require_vwap_support", True),
                "max_reactive_trades_per_day": reactive_cfg.get("max_reactive_trades_per_day", 3),
                "cooldown_minutes": reactive_cfg.get("cooldown_minutes", 15)
            }
        except Exception as e:
            self._logger.error(f"Failed to load reactive config: {e}")
            return {"enabled": False}
    
    def _should_run_reactive_scan(self) -> bool:
        """Check if we should run a reactive scan for opportunities."""
        if not self._reactive_config.get("enabled", False):
            return False
        
        now = get_market_clock().now()
        today_str = now.strftime("%Y-%m-%d")  # Use string for reliable comparison
        
        # Reset daily counters if it's a new day
        if self._last_reactive_reset_date is None or self._last_reactive_reset_date != today_str:
            self._reactive_trades_today = 0
            self._last_reactive_trade_time = None
            self._last_reactive_reset_date = today_str
            self._logger.log("twentymin_reactive_daily_reset", {
                "date": today_str,
                "counters_reset": True
            })
        
        # Check max trades per day
        max_trades = self._reactive_config.get("max_reactive_trades_per_day", 3)
        if self._reactive_trades_today >= max_trades:
            return False
        
        # Check cooldown from last reactive trade
        cooldown_minutes = self._reactive_config.get("cooldown_minutes", 15)
        if self._last_reactive_trade_time:
            elapsed = (now - self._last_reactive_trade_time).total_seconds() / 60
            if elapsed < cooldown_minutes:
                return False
        
        # Check scan interval
        scan_interval = self._reactive_config.get("scan_interval_minutes", 5)
        if self._last_reactive_scan_time:
            elapsed = (now - self._last_reactive_scan_time).total_seconds() / 60
            if elapsed < scan_interval:
                return False
        
        return True
    
    def _scan_for_reactive_opportunities(self, existing_positions: List[Any]) -> List[Dict[str, Any]]:
        """
        Scan for reactive trading opportunities (dip-buying, gap fades).
        
        This runs during the session when prestaging was missed or new
        opportunities emerge. Uses stricter filters than prestaging.
        
        Args:
            existing_positions: List of current positions to avoid duplicates
            
        Returns:
            List of opportunities sorted by quality
        """
        opportunities = []
        now = get_market_clock().now()
        self._last_reactive_scan_time = now
        
        existing_symbols = {p.symbol for p in existing_positions}
        min_gap = self._reactive_config.get("min_gap_pct", 0.75)
        max_gap = self._reactive_config.get("max_gap_pct", 5.0)
        min_dip = self._reactive_config.get("min_dip_pct", 0.5)
        require_vwap = self._reactive_config.get("require_vwap_support", True)
        
        self._logger.log("twentymin_reactive_scan_start", {
            "time": now.strftime("%H:%M:%S"),
            "tickers_to_scan": len(self._config.tickers) if self._config else 0,
            "existing_positions": len(existing_symbols)
        })
        
        for ticker in self._config.tickers if self._config else []:
            if ticker in existing_symbols:
                continue
            
            try:
                # Get current quote
                quote = self._alpaca.get_latest_quote(ticker)
                if not quote:
                    continue
                
                bid = quote.get("bid", 0)
                ask = quote.get("ask", 0)
                if bid <= 0 or ask <= 0:
                    continue
                
                mid_price = (bid + ask) / 2
                spread_pct = ((ask - bid) / mid_price) * 100
                max_spread = self._reactive_config.get("max_spread_pct", 0.15)
                
                if spread_pct > max_spread:
                    continue
                
                # Get today's bars to find high and gap
                bars = self._get_stock_bars(ticker, "5Min", limit=20)
                if not bars or len(bars) < 5:
                    continue
                
                # Calculate day's high - handle both Bar objects and dicts
                day_high = 0.0
                for b in bars:
                    bar_high = getattr(b, 'high', None) or getattr(b, 'h', None)
                    if bar_high is None and isinstance(b, dict):
                        bar_high = b.get('high', b.get('h', 0))
                    if bar_high and float(bar_high) > day_high:
                        day_high = float(bar_high)
                if day_high <= 0:
                    continue
                
                # Calculate dip from high
                dip_pct = ((day_high - mid_price) / day_high) * 100
                
                # Get previous close for gap - handle both Bar objects and dicts
                daily_bars = self._get_stock_bars(ticker, "1Day", limit=2)
                if not daily_bars or len(daily_bars) < 2:
                    continue
                
                prev_bar = daily_bars[-2]
                prev_close = getattr(prev_bar, 'close', None) or getattr(prev_bar, 'c', None)
                if prev_close is None and isinstance(prev_bar, dict):
                    prev_close = prev_bar.get('close', prev_bar.get('c', 0))
                prev_close = float(prev_close) if prev_close else 0
                if prev_close <= 0:
                    continue
                
                gap_pct = ((mid_price - prev_close) / prev_close) * 100
                
                # Check gap criteria
                if abs(gap_pct) < min_gap or abs(gap_pct) > max_gap:
                    continue
                
                # Check dip criteria
                if dip_pct < min_dip:
                    continue
                
                # Check VWAP support if required
                vwap_ok = True
                if require_vwap:
                    vwap = self._compute_vwap(bars)
                    if vwap > 0 and mid_price > vwap * 1.01:  # Price above VWAP by 1%+
                        vwap_ok = False
                
                if not vwap_ok:
                    continue
                
                # Calculate opportunity score
                # Higher dip = better, moderate gap = better, tight spread = better
                score = (dip_pct * 2) + (abs(gap_pct) * 0.5) - (spread_pct * 10)
                
                opportunities.append({
                    "ticker": ticker,
                    "mid_price": mid_price,
                    "gap_pct": gap_pct,
                    "dip_pct": dip_pct,
                    "spread_pct": spread_pct,
                    "day_high": day_high,
                    "prev_close": prev_close,
                    "score": score,
                    "trade_type": "reactive_dip_buy" if gap_pct > 0 else "reactive_gap_fade"
                })
                
            except Exception as e:
                self._logger.error(f"Reactive scan error for {ticker}: {e}")
                continue
        
        # Sort by score descending
        opportunities.sort(key=lambda x: x["score"], reverse=True)
        
        self._logger.log("twentymin_reactive_scan_complete", {
            "opportunities_found": len(opportunities),
            "top_3": [o["ticker"] for o in opportunities[:3]]
        })
        
        return opportunities
    
    def _execute_reactive_trade(self, opportunity: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a reactive trade based on an opportunity.
        
        Args:
            opportunity: Dict with ticker, prices, and trade type
            
        Returns:
            Dict with trade results
        """
        result = {"success": False, "ticker": opportunity["ticker"]}
        
        try:
            ticker = opportunity["ticker"]
            mid_price = opportunity["mid_price"]
            trade_type = opportunity["trade_type"]
            
            self._logger.log("twentymin_reactive_trade_attempt", {
                "ticker": ticker,
                "trade_type": trade_type,
                "gap_pct": opportunity["gap_pct"],
                "dip_pct": opportunity["dip_pct"],
                "price": mid_price
            })
            
            # Use existing entry logic
            # The gap analysis should give us entry direction
            gap = self._analyze_gap(ticker)
            if not gap:
                result["reason"] = "Gap analysis failed"
                return result
            
            self._gap_analysis[ticker] = gap
            
            # Detect pattern
            pattern = self._detect_pattern(ticker, gap)
            if not pattern or pattern.pattern == PatternType.NO_PATTERN:
                result["reason"] = "No pattern detected"
                return result
            
            # Get budget from config
            bots_config = load_bots_config()
            twentymin_config = bots_config.get("twentyminute_bot", {})
            prestage_config = twentymin_config.get("prestaging", {})
            budget = prestage_config.get("max_position_usd", 2000.0)
            
            # Execute entry using existing method
            entry_result = self._execute_entry(ticker, pattern, gap, budget)
            
            if entry_result.get("success"):
                self._reactive_trades_today += 1
                self._last_reactive_trade_time = get_market_clock().now()
                result["success"] = True
                result["order_id"] = entry_result.get("order_id")
                result["direction"] = pattern.direction.value
                
                self._logger.log("twentymin_reactive_trade_success", {
                    "ticker": ticker,
                    "trade_type": trade_type,
                    "direction": pattern.direction.value,
                    "reactive_trades_today": self._reactive_trades_today
                })
            else:
                result["reason"] = entry_result.get("error", "Entry failed")
                
        except Exception as e:
            result["reason"] = str(e)
            self._logger.error(f"Reactive trade failed for {opportunity['ticker']}: {e}")
        
        return result
    
    def _is_post_session(self) -> bool:
        """Check if session has ended (need to flatten positions)."""
        if not self._config:
            return False
            
        now = get_market_clock().now()
        current_time = now.strftime("%H:%M")
        
        # Post-session is 10 minutes after session_end for position flattening
        # e.g., if session_end is 07:50, post-session is 07:50-08:00
        try:
            session_end_hour, session_end_min = map(int, self._config.session_end.split(":"))
        except (ValueError, AttributeError):
            # Fallback if config is malformed
            return False
            
        post_end_min = session_end_min + 10
        post_end_hour = session_end_hour
        if post_end_min >= 60:
            post_end_min -= 60
            post_end_hour += 1
        post_end = f"{post_end_hour:02d}:{post_end_min:02d}"
        
        return self._config.session_end <= current_time < post_end
    
    def _log_gap_summary(self, phase: str) -> None:
        """Log a summary of all analyzed gaps, sorted by magnitude."""
        if not self._gap_analysis:
            self._logger.log("twentymin_gap_summary", {
                "phase": phase,
                "total_gaps": 0,
                "significant_count": 0,
                "message": "No gaps analyzed yet"
            })
            return
        
        gap_list = []
        for ticker, gap in self._gap_analysis.items():
            gap_list.append({
                "ticker": ticker,
                "gap_pct": round(gap.gap_pct, 3),
                "direction": gap.gap_direction.value,
                "volume_ratio": round(gap.volume_ratio, 2),
                "is_significant": gap.is_significant
            })
        
        sorted_gaps = sorted(gap_list, key=lambda x: abs(x["gap_pct"]), reverse=True)
        significant_gaps = [g for g in sorted_gaps if g["is_significant"]]
        
        self._logger.log("twentymin_gap_summary", {
            "phase": phase,
            "total_gaps": len(sorted_gaps),
            "significant_count": len(significant_gaps),
            "min_gap_threshold": self._config.min_gap_pct if self._config else 0.3,
            "top_5_gaps": sorted_gaps[:5],
            "significant_gaps": significant_gaps,
            "best_opportunity": sorted_gaps[0] if sorted_gaps else None
        })
    
    def run_premarket_gap_scan(self, display: bool = True) -> List[Dict[str, Any]]:
        """
        Run premarket gap analysis on all tickers and optionally display results.
        
        This method is designed to run during premarket hours (06:00-06:30 PST)
        to identify the best gap opportunities before the trading session begins.
        
        Args:
            display: Whether to print the gap scanner display to console
            
        Returns:
            List of gap data dictionaries sorted by absolute gap percentage
        """
        from ..core.console import GapData, format_premarket_gap_display
        from ..core.clock import get_market_clock
        
        clock = get_market_clock()
        now = clock.now()
        scan_time = now.strftime("%H:%M:%S %Z")
        
        self._logger.log("premarket_gap_scan_start", {
            "bot_id": self.bot_id,
            "time": scan_time,
            "tickers_count": len(self._config.tickers)
        })
        
        gap_results = []
        
        for ticker in self._config.tickers:
            try:
                gap = self._analyze_gap(ticker)
                if gap and abs(gap.gap_pct) > 0.1:
                    pattern = None
                    ml_score = 0.0
                    
                    if gap.is_significant:
                        pattern = self._detect_pattern(ticker, gap)
                        if pattern and self._ml_enabled:
                            ml_score = self._score_with_ml(ticker, pattern, gap)
                    
                    gap_results.append({
                        "symbol": ticker,
                        "gap_pct": gap.gap_pct,
                        "direction": "UP" if gap.gap_pct > 0 else "DOWN",
                        "volume_ratio": gap.volume_ratio,
                        "prev_close": gap.prev_close,
                        "current_price": gap.current_price,
                        "is_significant": gap.is_significant,
                        "pattern_detected": pattern is not None and pattern.pattern != PatternType.NO_PATTERN if pattern else False,
                        "pattern_name": pattern.pattern.value if pattern and pattern.pattern != PatternType.NO_PATTERN else "",
                        "ml_score": ml_score * 100,
                        "rsi": getattr(pattern, 'rsi', 0) if pattern else 0
                    })
            except Exception as e:
                self._logger.warn(f"Gap scan failed for {ticker}: {e}")
        
        gap_results.sort(key=lambda x: abs(x["gap_pct"]), reverse=True)
        
        self._logger.log("premarket_gap_scan_complete", {
            "bot_id": self.bot_id,
            "gaps_found": len(gap_results),
            "significant_gaps": len([g for g in gap_results if g["is_significant"]]),
            "top_3": [{"symbol": g["symbol"], "gap_pct": round(g["gap_pct"], 2)} for g in gap_results[:3]]
        })
        
        if display:
            gap_display_list = [
                GapData(
                    symbol=g["symbol"],
                    gap_pct=g["gap_pct"],
                    direction=g["direction"],
                    volume_ratio=g["volume_ratio"],
                    prev_close=g["prev_close"],
                    open_price=g["current_price"],
                    rsi=g.get("rsi", 0),
                    pattern_detected=g["pattern_detected"],
                    pattern_name=g["pattern_name"],
                    ml_score=g["ml_score"]
                )
                for g in gap_results
            ]
            
            display_str = format_premarket_gap_display(
                gaps=gap_display_list,
                scan_time=scan_time,
                next_scan_seconds=300,
                trading_starts_at=self._config.session_start + " PST"
            )
            print(display_str)
        
        return gap_results
    
    def _analyze_gap(self, symbol: str) -> Optional[GapAnalysis]:
        """
        Analyze overnight gap for a symbol.
        
        The gap is calculated as the difference between yesterday's close
        and today's opening price (or current pre-market price).
        """
        try:
            bars = self._get_stock_bars(symbol, timeframe="1Day", limit=2)
            
            if not bars or len(bars) < 2:
                return None
            
            prev_close = float(bars[-2].close)
            
            quote = self._alpaca.get_latest_quote(symbol, asset_class="stock")
            if quote and quote.get("ask"):
                current_price = float(quote.get("ask") or quote.get("bid") or bars[-1].open)
            else:
                current_price = float(bars[-1].open)
            
            gap_pct = ((current_price - prev_close) / prev_close) * 100
            
            if gap_pct > 0:
                gap_direction = SignalDirection.LONG
            elif gap_pct < 0:
                gap_direction = SignalDirection.SHORT
            else:
                gap_direction = SignalDirection.NEUTRAL
            
            avg_volume = sum(float(b.volume) for b in bars) / len(bars)
            current_volume = float(bars[-1].volume) if bars[-1].volume else avg_volume
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0
            
            min_gap = self._config.min_gap_pct if self._config else 0.3
            is_significant = abs(gap_pct) >= min_gap
            
            gap_analysis = GapAnalysis(
                symbol=symbol,
                prev_close=prev_close,
                current_price=current_price,
                gap_pct=gap_pct,
                gap_direction=gap_direction,
                volume_ratio=volume_ratio,
                is_significant=is_significant
            )
            
            self._logger.log("twentymin_gap_analysis", {
                "symbol": symbol,
                "gap_pct": round(gap_pct, 3),
                "direction": gap_direction.value,
                "volume_ratio": round(volume_ratio, 2),
                "is_significant": is_significant
            })
            
            return gap_analysis
            
        except Exception as e:
            self._logger.error(f"Gap analysis failed for {symbol}: {e}")
            return None
    
    def _detect_pattern(self, symbol: str, gap: GapAnalysis) -> Optional[PatternSignal]:
        """
        Detect trading patterns in the first 20 minutes.
        
        Patterns detected:
        1. Gap Reversal: Gap fills (gaps up then falls, or gaps down then rises)
        2. Gap Continuation: Gap extends (momentum follows gap direction)
        3. First Bar Breakout: Break of first 5-minute bar high/low
        4. Opening Range: Break of first 10-minute range
        
        Enhanced with VWAP Momentum filters per trading philosophy:
        - VWAP position check
        - 9/20 EMA crossover confirmation
        - RSI(7) overbought/oversold filter
        - Market alignment (SPY/QQQ direction)
        - Volume spike confirmation
        """
        try:
            bars = self._get_stock_bars(symbol, timeframe="1Min", limit=20)
            
            if not bars or len(bars) < 3:
                return PatternSignal(
                    pattern=PatternType.NO_PATTERN,
                    direction=SignalDirection.NEUTRAL,
                    confidence=0.0,
                    entry_price=0.0,
                    stop_price=0.0,
                    target_price=0.0,
                    reason="Insufficient bars"
                )
            
            # Compute VWAP Momentum indicators
            momentum_indicators = self._compute_momentum_indicators(symbol, bars)
            
            first_bar = bars[0]
            first_bar_high = float(first_bar.high)
            first_bar_low = float(first_bar.low)
            first_bar_range = first_bar_high - first_bar_low
            first_bar_range_pct = (first_bar_range / float(first_bar.open)) * 100
            
            current_bar = bars[-1]
            current_price = float(current_bar.close)
            
            min_range = self._config.min_first_bar_range_pct if self._config else 0.15
            if first_bar_range_pct < min_range:
                return PatternSignal(
                    pattern=PatternType.NO_PATTERN,
                    direction=SignalDirection.NEUTRAL,
                    confidence=0.0,
                    entry_price=current_price,
                    stop_price=0.0,
                    target_price=0.0,
                    reason=f"First bar range too small: {first_bar_range_pct:.2f}%"
                )
            
            # Try each pattern type, passing momentum indicators for validation
            pattern = self._check_gap_reversal(gap, bars, current_price)
            if pattern and pattern.pattern != PatternType.NO_PATTERN:
                # Validate with momentum indicators
                pattern = self._validate_with_momentum(pattern, momentum_indicators, symbol)
                if pattern:
                    return pattern
            
            pattern = self._check_gap_continuation(gap, bars, current_price)
            if pattern and pattern.pattern != PatternType.NO_PATTERN:
                pattern = self._validate_with_momentum(pattern, momentum_indicators, symbol)
                if pattern:
                    return pattern
            
            pattern = self._check_first_bar_breakout(
                first_bar_high, first_bar_low, bars, current_price
            )
            if pattern and pattern.pattern != PatternType.NO_PATTERN:
                pattern = self._validate_with_momentum(pattern, momentum_indicators, symbol)
                if pattern:
                    return pattern
            
            return PatternSignal(
                pattern=PatternType.NO_PATTERN,
                direction=SignalDirection.NEUTRAL,
                confidence=0.0,
                entry_price=current_price,
                stop_price=0.0,
                target_price=0.0,
                reason="No clear pattern detected"
            )
            
        except Exception as e:
            self._logger.error(f"Pattern detection failed for {symbol}: {e}")
            return None
    
    def _validate_with_momentum(
        self, pattern: PatternSignal, indicators: VWAPMomentumIndicators, symbol: str
    ) -> Optional[PatternSignal]:
        """
        Validate a pattern signal against VWAP Momentum criteria.
        
        Per 20-minute trading philosophy:
        LONG Entry:
        - Price above VWAP ✓
        - 9 EMA > 20 EMA (bullish cross) ✓
        - Volume spike on breakout ✓
        - Market (SPY/QQQ) aligned ✓
        
        SHORT Entry:
        - Price below VWAP ✓
        - 9 EMA < 20 EMA (bearish cross) ✓
        - Volume spike on breakdown ✓
        - Market aligned ✓
        """
        # Load pattern config to check if EMA cross, VWAP position, and market alignment are required
        pattern_cfg = load_bots_config().get("twentyminute_bot", {}).get("pattern", {})
        require_ema_cross = pattern_cfg.get("require_ema_cross", True)  # Default True for strict mode
        require_vwap_position = pattern_cfg.get("require_vwap_position", True)  # Default True
        require_market_alignment = pattern_cfg.get("require_market_alignment", True)  # Default True, set False for gap-first
        
        if pattern.direction == SignalDirection.LONG:
            # Check VWAP position (optional based on config)
            if require_vwap_position and not indicators.price_above_vwap:
                self._logger.log("twentymin_momentum_filter", {
                    "symbol": symbol,
                    "pattern": pattern.pattern.value,
                    "direction": "long",
                    "rejected_reason": "price_below_vwap",
                    "vwap": round(indicators.vwap, 2),
                    "price": round(pattern.entry_price, 2)
                })
                return None
            
            # Check EMA crossover (optional based on config)
            if require_ema_cross and not indicators.ema_bullish_cross:
                self._logger.log("twentymin_momentum_filter", {
                    "symbol": symbol,
                    "pattern": pattern.pattern.value,
                    "direction": "long",
                    "rejected_reason": "no_bullish_ema_cross",
                    "ema_9": round(indicators.ema_9, 2),
                    "ema_20": round(indicators.ema_20, 2)
                })
                return None
            
            # Check market alignment (optional based on config - disabled for gap-first strategy)
            if require_market_alignment:
                aligned, market_dir = self._check_market_alignment(SignalDirection.LONG)
                if not aligned:
                    self._logger.log("twentymin_momentum_filter", {
                        "symbol": symbol,
                        "pattern": pattern.pattern.value,
                        "direction": "long",
                        "rejected_reason": "market_not_aligned",
                        "market_direction": market_dir
                    })
                    return None
            
            # RSI overbought check (configurable threshold from config/bots.yaml)
            momentum_cfg = load_bots_config().get("twentyminute_bot", {}).get("momentum", {})
            rsi_overbought = momentum_cfg.get("rsi_overbought", 85)
            if indicators.rsi_7 > rsi_overbought:
                self._logger.log("twentymin_momentum_filter", {
                    "symbol": symbol,
                    "pattern": pattern.pattern.value,
                    "direction": "long",
                    "rejected_reason": "rsi_overbought",
                    "rsi": round(indicators.rsi_7, 1),
                    "threshold": rsi_overbought
                })
                return None
            
            # Passed all momentum checks - boost confidence with volume spike
            confidence_boost = 0.1 if indicators.volume_spike else 0.0
            
        elif pattern.direction == SignalDirection.SHORT:
            # Check VWAP position (optional based on config)
            if require_vwap_position and indicators.price_above_vwap:
                self._logger.log("twentymin_momentum_filter", {
                    "symbol": symbol,
                    "pattern": pattern.pattern.value,
                    "direction": "short",
                    "rejected_reason": "price_above_vwap",
                    "vwap": round(indicators.vwap, 2),
                    "price": round(pattern.entry_price, 2)
                })
                return None
            
            # Check EMA crossover (optional based on config)
            if require_ema_cross and not indicators.ema_bearish_cross:
                self._logger.log("twentymin_momentum_filter", {
                    "symbol": symbol,
                    "pattern": pattern.pattern.value,
                    "direction": "short",
                    "rejected_reason": "no_bearish_ema_cross",
                    "ema_9": round(indicators.ema_9, 2),
                    "ema_20": round(indicators.ema_20, 2)
                })
                return None
            
            # Check market alignment (optional based on config - disabled for gap-first strategy)
            if require_market_alignment:
                aligned, market_dir = self._check_market_alignment(SignalDirection.SHORT)
                if not aligned:
                    self._logger.log("twentymin_momentum_filter", {
                        "symbol": symbol,
                        "pattern": pattern.pattern.value,
                        "direction": "short",
                        "rejected_reason": "market_not_aligned",
                        "market_direction": market_dir
                    })
                    return None
            
            # RSI oversold check (configurable threshold from config/bots.yaml)
            momentum_cfg = load_bots_config().get("twentyminute_bot", {}).get("momentum", {})
            rsi_oversold = momentum_cfg.get("rsi_oversold", 15)
            if indicators.rsi_7 < rsi_oversold:
                self._logger.log("twentymin_momentum_filter", {
                    "symbol": symbol,
                    "pattern": pattern.pattern.value,
                    "direction": "short",
                    "rejected_reason": "rsi_oversold",
                    "rsi": round(indicators.rsi_7, 1),
                    "threshold": rsi_oversold
                })
                return None
            
            confidence_boost = 0.1 if indicators.volume_spike else 0.0
        else:
            return pattern  # Neutral direction passes through
        
        # Log successful momentum validation with all data points for ML
        self._logger.log("twentymin_momentum_validated", {
            "symbol": symbol,
            "pattern": pattern.pattern.value,
            "direction": pattern.direction.value,
            "entry_price": round(pattern.entry_price, 2),
            "stop_price": round(pattern.stop_price, 2),
            "target_price": round(pattern.target_price, 2),
            "confidence": round(pattern.confidence + confidence_boost, 2),
            # VWAP momentum indicators for ML retraining
            "vwap": round(indicators.vwap, 2),
            "price_vs_vwap_pct": round((pattern.entry_price - indicators.vwap) / indicators.vwap * 100, 2) if indicators.vwap > 0 else 0,
            "ema_9": round(indicators.ema_9, 2),
            "ema_20": round(indicators.ema_20, 2),
            "ema_spread_pct": round((indicators.ema_9 - indicators.ema_20) / indicators.ema_20 * 100, 2) if indicators.ema_20 > 0 else 0,
            "rsi_7": round(indicators.rsi_7, 1),
            "volume_ratio": round(indicators.volume_ratio, 2),
            "volume_spike": indicators.volume_spike,
            "market_aligned": True,
            # Risk/reward for ML
            "risk_pct": round(abs(pattern.entry_price - pattern.stop_price) / pattern.entry_price * 100, 2) if pattern.entry_price > 0 else 0,
            "reward_pct": round(abs(pattern.target_price - pattern.entry_price) / pattern.entry_price * 100, 2) if pattern.entry_price > 0 else 0
        })
        
        # Order flow gate: skip if flow opposes direction
        if pattern.direction == SignalDirection.LONG and not indicators.order_flow_bullish:
            self._logger.log("twentymin_momentum_filter", {
                "symbol": symbol,
                "pattern": pattern.pattern.value,
                "direction": "long",
                "rejected_reason": "bearish_order_flow",
                "order_flow_delta": round(indicators.order_flow_delta, 0),
            })
            return None
        if pattern.direction == SignalDirection.SHORT and indicators.order_flow_bullish:
            self._logger.log("twentymin_momentum_filter", {
                "symbol": symbol,
                "pattern": pattern.pattern.value,
                "direction": "short",
                "rejected_reason": "bullish_order_flow",
                "order_flow_delta": round(indicators.order_flow_delta, 0),
            })
            return None

        # VWAP sigma band target override: use 1σ band as target if it's a
        # closer (more achievable) level than the percentage-based target.
        target_price = pattern.target_price
        entry = pattern.entry_price
        sigma_target_applied = False
        if pattern.direction == SignalDirection.LONG:
            sigma_tgt = indicators.vwap_upper_1sigma
            if sigma_tgt > entry and 0 < (sigma_tgt - entry) < (target_price - entry):
                target_price = sigma_tgt
                sigma_target_applied = True
        elif pattern.direction == SignalDirection.SHORT:
            sigma_tgt = indicators.vwap_lower_1sigma
            if 0 < sigma_tgt < entry and 0 < (entry - sigma_tgt) < (entry - target_price):
                target_price = sigma_tgt
                sigma_target_applied = True

        reason_suffix = f"[VWAP confirmed, RSI={indicators.rsi_7:.0f}"
        if sigma_target_applied:
            reason_suffix += f", tgt=1σ@{target_price:.2f}"
        reason_suffix += "]"

        # Return pattern with boosted confidence
        return PatternSignal(
            pattern=pattern.pattern,
            direction=pattern.direction,
            confidence=min(1.0, pattern.confidence + confidence_boost),
            entry_price=pattern.entry_price,
            stop_price=pattern.stop_price,
            target_price=target_price,
            reason=f"{pattern.reason} {reason_suffix}"
        )
    
    def _check_gap_reversal(
        self, gap: GapAnalysis, bars: List, current_price: float
    ) -> Optional[PatternSignal]:
        """
        Check for gap reversal pattern.
        
        Gap reversal occurs when:
        - Gap up: Price falls back toward previous close (short signal)
        - Gap down: Price rises back toward previous close (long signal)
        """
        if not gap.is_significant:
            return None
        
        fill_pct = abs(current_price - gap.current_price) / abs(gap.current_price - gap.prev_close) * 100
        
        stop_loss = self._config.stop_loss_pct if self._config else 0.5
        take_profit = self._config.take_profit_pct if self._config else 0.5
        
        if fill_pct >= 30:
            if gap.gap_direction == SignalDirection.LONG:
                direction = SignalDirection.SHORT
                stop_price = current_price * (1 + stop_loss / 100)
                target_price = current_price * (1 - take_profit / 100)
            else:
                direction = SignalDirection.LONG
                stop_price = current_price * (1 - stop_loss / 100)
                target_price = current_price * (1 + take_profit / 100)
            
            confidence = min(0.9, 0.5 + (fill_pct / 100) * 0.4)
            
            return PatternSignal(
                pattern=PatternType.GAP_REVERSAL,
                direction=direction,
                confidence=confidence,
                entry_price=current_price,
                stop_price=stop_price,
                target_price=target_price,
                reason=f"Gap {gap.gap_pct:.2f}% reversing, {fill_pct:.1f}% filled"
            )
        
        return None
    
    def _check_gap_continuation(
        self, gap: GapAnalysis, bars: List, current_price: float
    ) -> Optional[PatternSignal]:
        """
        Check for gap continuation pattern.
        
        Gap continuation occurs when:
        - Gap up: Price continues higher (long signal)
        - Gap down: Price continues lower (short signal)
        """
        if not gap.is_significant:
            return None
        
        stop_loss = self._config.stop_loss_pct if self._config else 0.5
        take_profit = self._config.take_profit_pct if self._config else 0.5
        
        extension = (current_price - gap.current_price) / gap.current_price * 100
        
        if gap.gap_direction == SignalDirection.LONG and extension > 0.2:
            direction = SignalDirection.LONG
            stop_price = current_price * (1 - stop_loss / 100)
            target_price = current_price * (1 + take_profit / 100)
            confidence = min(0.85, 0.5 + extension * 0.2)
            
            return PatternSignal(
                pattern=PatternType.GAP_CONTINUATION,
                direction=direction,
                confidence=confidence,
                entry_price=current_price,
                stop_price=stop_price,
                target_price=target_price,
                reason=f"Gap up {gap.gap_pct:.2f}% continuing, +{extension:.2f}% extension"
            )
        
        elif gap.gap_direction == SignalDirection.SHORT and extension < -0.2:
            direction = SignalDirection.SHORT
            stop_price = current_price * (1 + stop_loss / 100)
            target_price = current_price * (1 - take_profit / 100)
            confidence = min(0.85, 0.5 + abs(extension) * 0.2)
            
            return PatternSignal(
                pattern=PatternType.GAP_CONTINUATION,
                direction=direction,
                confidence=confidence,
                entry_price=current_price,
                stop_price=stop_price,
                target_price=target_price,
                reason=f"Gap down {gap.gap_pct:.2f}% continuing, {extension:.2f}% extension"
            )
        
        return None
    
    def _check_first_bar_breakout(
        self, first_high: float, first_low: float, bars: List, current_price: float
    ) -> Optional[PatternSignal]:
        """
        Check for first bar breakout pattern.
        
        Breakout occurs when price breaks above/below the first 5-minute bar.
        """
        if len(bars) < 3:
            return None
        
        take_profit = self._config.take_profit_pct if self._config else 0.5
        
        recent_closes = [float(b.close) for b in bars[-3:]]
        
        if current_price > first_high and all(c > first_high for c in recent_closes[-2:]):
            direction = SignalDirection.LONG
            stop_price = first_low
            target_price = current_price * (1 + take_profit / 100)
            
            breakout_pct = (current_price - first_high) / first_high * 100
            confidence = min(0.8, 0.5 + breakout_pct * 0.15)
            
            return PatternSignal(
                pattern=PatternType.FIRST_BAR_BREAKOUT,
                direction=direction,
                confidence=confidence,
                entry_price=current_price,
                stop_price=stop_price,
                target_price=target_price,
                reason=f"Breakout above first bar high {first_high:.2f}"
            )
        
        elif current_price < first_low and all(c < first_low for c in recent_closes[-2:]):
            direction = SignalDirection.SHORT
            stop_price = first_high
            target_price = current_price * (1 - take_profit / 100)
            
            breakdown_pct = (first_low - current_price) / first_low * 100
            confidence = min(0.8, 0.5 + breakdown_pct * 0.15)
            
            return PatternSignal(
                pattern=PatternType.FIRST_BAR_BREAKOUT,
                direction=direction,
                confidence=confidence,
                entry_price=current_price,
                stop_price=stop_price,
                target_price=target_price,
                reason=f"Breakdown below first bar low {first_low:.2f}"
            )
        
        return None
    
    def _score_with_ml(
        self, symbol: str, pattern: PatternSignal, gap: GapAnalysis
    ) -> float:
        """Score the trade with ML signal service."""
        try:
            hour = get_market_clock().now().hour
            day_of_week = get_market_clock().now().weekday()
            
            context = {
                "symbol": symbol,
                "side": "buy" if pattern.direction == SignalDirection.LONG else "sell",
                "signal_strength": pattern.confidence,
                "hour": hour,
                "day_of_week": day_of_week,
                "gap_pct": gap.gap_pct,
                "volume_ratio": gap.volume_ratio,
                "pattern_type": pattern.pattern.value
            }
            
            result = self._ml_service.score_entry(context)
            return result.get("probability", 0.5)
            
        except Exception as e:
            self._logger.error(f"ML scoring failed for {symbol}: {e}")
            return 0.5
    
    def _run_entry_quality_checklist(
        self, symbol: str, pattern: PatternSignal, gap: GapAnalysis
    ) -> tuple:
        """
        Trade-Bot style entry quality checklist.
        Checks VWAP posture, RSI zone, volume confirmation, and EMA alignment.
        Returns (passed: bool, details: dict) where details includes individual check results.
        
        Minimum 3 out of 4 checks must pass to allow entry.
        This prevents low-quality entries that immediately go underwater.
        """
        import numpy as np
        
        checks = {}
        direction = "call" if pattern.direction == SignalDirection.LONG else "put"
        min_checks = 3
        
        try:
            bars = self._get_stock_bars(symbol, "1Min", 100)
            if not bars or len(bars) < 20:
                return (False, {"score": 0, "max_score": 4, "fail_reason": f"Insufficient bar data ({len(bars) if bars else 0} bars)", "checks": {}})
            
            closes = [float(b.close) for b in bars]
            highs = [float(b.high) for b in bars]
            lows = [float(b.low) for b in bars]
            volumes = [float(b.volume) for b in bars]
            current_price = closes[-1]
            
            # CHECK 1: VWAP POSTURE — price should be on the right side of VWAP
            try:
                typical_prices = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
                cum_tp_vol = np.cumsum([tp * v for tp, v in zip(typical_prices, volumes)])
                cum_vol = np.cumsum(volumes)
                vwap = cum_tp_vol[-1] / cum_vol[-1] if cum_vol[-1] > 0 else current_price
                
                if direction == "call":
                    vwap_passed = current_price > vwap
                else:
                    vwap_passed = current_price < vwap
                
                checks["vwap"] = {
                    "passed": vwap_passed,
                    "value": round(current_price, 2),
                    "vwap": round(vwap, 2),
                    "detail": f"Price {current_price:.2f} {'>' if direction == 'call' else '<'} VWAP {vwap:.2f}"
                }
            except Exception:
                checks["vwap"] = {"passed": False, "detail": "VWAP calc failed, FAIL_CLOSED"}
            
            # CHECK 2: RSI ZONE — avoid overbought calls and oversold puts
            try:
                period = 14
                if len(closes) >= period + 1:
                    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
                    gains = [max(d, 0) for d in deltas]
                    losses = [max(-d, 0) for d in deltas]
                    
                    avg_gain = sum(gains[-period:]) / period
                    avg_loss = sum(losses[-period:]) / period
                    
                    if avg_loss > 0:
                        rs = avg_gain / avg_loss
                        rsi = 100 - (100 / (1 + rs))
                    else:
                        rsi = 100
                    
                    if direction == "call":
                        rsi_passed = 35 <= rsi <= 72
                        rng = "[35-72]"
                    else:
                        rsi_passed = 28 <= rsi <= 65
                        rng = "[28-65]"
                    
                    checks["rsi"] = {
                        "passed": rsi_passed,
                        "value": round(rsi, 1),
                        "detail": f"RSI(14) {rsi:.1f} {'in' if rsi_passed else 'outside'} {rng}"
                    }
                else:
                    checks["rsi"] = {"passed": True, "detail": "Insufficient data for RSI"}
            except Exception:
                checks["rsi"] = {"passed": False, "detail": "RSI calc failed, FAIL_CLOSED"}
            
            # CHECK 3: VOLUME CONFIRMATION — current volume should exceed 20-bar average
            try:
                if len(volumes) >= 20:
                    vol_avg = sum(volumes[-20:]) / 20
                    vol_current = volumes[-1]
                    vol_ratio = vol_current / vol_avg if vol_avg > 0 else 0
                    vol_passed = vol_ratio >= 0.8
                    
                    checks["volume"] = {
                        "passed": vol_passed,
                        "value": round(vol_ratio, 2),
                        "detail": f"Vol ratio {vol_ratio:.2f} {'≥' if vol_passed else '<'} 0.8"
                    }
                else:
                    checks["volume"] = {"passed": True, "detail": "Insufficient volume data"}
            except Exception:
                checks["volume"] = {"passed": False, "detail": "Volume calc failed, FAIL_CLOSED"}
            
            # CHECK 4: EMA ALIGNMENT — fast EMA should confirm direction
            try:
                if len(closes) >= 21:
                    import pandas as pd
                    close_series = pd.Series(closes)
                    ema9 = close_series.ewm(span=9, adjust=False).mean().iloc[-1]
                    ema21 = close_series.ewm(span=21, adjust=False).mean().iloc[-1]
                    
                    if direction == "call":
                        ema_passed = ema9 > ema21
                    else:
                        ema_passed = ema9 < ema21
                    
                    checks["ema_cross"] = {
                        "passed": ema_passed,
                        "value": round(ema9, 2),
                        "detail": f"EMA9 {ema9:.2f} {'>' if direction == 'call' else '<'} EMA21 {ema21:.2f}"
                    }
                else:
                    checks["ema_cross"] = {"passed": True, "detail": "Insufficient data for EMA"}
            except Exception:
                checks["ema_cross"] = {"passed": False, "detail": "EMA calc failed, FAIL_CLOSED"}
            
            score = sum(1 for c in checks.values() if c.get("passed", False))
            max_score = len(checks)
            passed = score >= min_checks
            
            fail_reasons = [f"{k}: {v['detail']}" for k, v in checks.items() if not v.get("passed", False)]
            
            details = {
                "score": score,
                "max_score": max_score,
                "min_required": min_checks,
                "passed": passed,
                "checks": checks,
                "fail_reason": "; ".join(fail_reasons) if fail_reasons else "all checks passed"
            }
            
            return (passed, details)
            
        except Exception as e:
            return (True, {"score": 0, "max_score": 0, "fail_reason": f"Checklist error: {e}", "checks": {}})
    
    def _execute_entry(
        self, symbol: str, pattern: PatternSignal, gap: GapAnalysis, budget: float
    ) -> Dict[str, Any]:
        """
        Execute entry for a detected pattern.
        
        If use_options is true, uses options with video rules bracket.
        Otherwise uses shares as fallback.
        """
        # SESSION PROTECTION CHECK — block entries if target locked (with freeroll)
        freeroll_budget = None
        try:
            from ..risk.session_protection import get_session_protection
            sp = get_session_protection()
            # Use the quality checklist score from the pattern if available (0-100 scale)
            qual_score = getattr(pattern, 'quality_score', 0) or 0
            sp_block, sp_reason = sp.should_block_new_trade(quality_score=qual_score)
            if sp_block:
                if not sp.should_throttle_message(f"twentymin_block_{symbol}"):
                    self._logger.log("twentymin_session_protection_block", {"symbol": symbol, "reason": sp_reason})
                    print(f"  [20MIN] Entry blocked for {symbol}: {sp_reason}")
                return {"success": False, "error": f"Session protection: {sp_reason}"}
            elif sp_reason.startswith("FREEROLL:"):
                freeroll_budget = float(sp_reason.replace("FREEROLL:$", ""))
                print(f"  [20MIN] FREEROLL entry for {symbol}: ${freeroll_budget:.0f} house money")
        except Exception as sp_err:
            self._logger.warn(f"TwentyMinBot session protection check failed (fail-open): {sp_err}")

        # Cap budget with freeroll house money if active
        if freeroll_budget is not None:
            budget = min(budget, freeroll_budget)
            print(f"  [20MIN] Freeroll sizing: budget capped to ${budget:.0f}")

        # UNIVERSE GUARD CHECK - Block if symbol not in premarket selection
        from ..risk.universe_guard import get_universe_guard
        guard = get_universe_guard()
        if not guard.is_symbol_allowed(symbol, bot_id=self.bot_id):
            self._logger.log("twentymin_entry_blocked_universe", {
                "symbol": symbol,
                "reason": "not_in_selected_universe"
            })
            return {"success": False, "error": f"Symbol {symbol} blocked by UniverseGuard"}
        
        # Check if options execution is enabled
        bots_config = load_bots_config()
        twentymin_config = bots_config.get("twentyminute_bot", {})
        execution_config = twentymin_config.get("execution", {})
        use_options = execution_config.get("use_options", False)
        allow_equity_fallback = execution_config.get("allow_equity_fallback", False)
        fallback_delay = execution_config.get("equity_fallback_delay_seconds", 0)
        
        if use_options:
            result = self._execute_options_entry(symbol, pattern, gap, execution_config)
            
            # EQUITY FALLBACK: If options failed and fallback enabled, try equity
            if not result.get("success") and allow_equity_fallback:
                signal_age = getattr(pattern, 'age_seconds', fallback_delay)  # Default to delay if no age
                
                if signal_age >= fallback_delay:
                    self._logger.log("twentymin_equity_fallback", {
                        "symbol": symbol,
                        "options_error": result.get("error", "unknown"),
                        "signal_age_seconds": signal_age,
                        "fallback_delay": fallback_delay
                    })
                    return self._execute_stock_entry(symbol, pattern, gap, budget)
                else:
                    self._logger.log("twentymin_equity_fallback_waiting", {
                        "symbol": symbol,
                        "signal_age_seconds": signal_age,
                        "fallback_delay": fallback_delay
                    })
            final_result = result
        else:
            final_result = self._execute_stock_entry(symbol, pattern, gap, budget)

        # Mark freeroll as used if this was a freeroll entry and it succeeded
        if freeroll_budget is not None and final_result.get("success"):
            try:
                sp = get_session_protection()
                trade_id = final_result.get("order_id", f"20min_{symbol}")
                sp.mark_freeroll_used(trade_id)
                self._logger.log("twentymin_freeroll_entry", {
                    "symbol": symbol,
                    "trade_id": trade_id,
                    "freeroll_budget": freeroll_budget,
                })
            except Exception as fr_err:
                self._logger.error(f"Freeroll marking failed: {fr_err}")

        return final_result

    def _execute_options_entry(
        self, symbol: str, pattern: PatternSignal, gap: GapAnalysis, execution_config: Dict
    ) -> Dict[str, Any]:
        """
        Execute options entry using video rules bracket.
        
        Steps:
        1. Check daily loss limit (fail-closed safety)
        2. Get options chain for underlying
        3. Select liquid contract via jeremy_bracket
        4. Compute underlying move from first bar
        5. Compute option bracket (stop/TP)
        6. Size position by risk budget
        7. Place entry + OCO bracket
        """
        try:
            from ..services.jeremy_bracket import (
                compute_underlying_move,
                select_liquid_contract,
                compute_option_bracket,
                compute_contract_qty
            )
            
            # Get options config
            options_config = execution_config.get("options", {})
            risk_config = options_config.get("risk", {})
            
            # Check daily loss limit (fail-closed safety)
            today = get_market_clock().now().strftime("%Y-%m-%d")
            daily_losses = get_state(f"twentymin.options.daily_losses.{today}") or 0
            stop_after_losses = risk_config.get("stop_after_losses", 2)
            
            if daily_losses >= stop_after_losses:
                self._logger.log("twentymin_options_daily_stop", {
                    "symbol": symbol,
                    "losses_today": daily_losses,
                    "limit": stop_after_losses
                })
                return {
                    "success": False,
                    "error": f"Daily stop reached: {daily_losses} losses >= {stop_after_losses}"
                }
            
            # Get first bar data for move computation
            bars = self._first_bars.get(symbol, [])
            if not bars:
                bars = self._get_stock_bars(symbol, "1Min", 5)
            
            if not bars:
                return {"success": False, "error": "No bar data for move computation"}
            
            first_bar = bars[0]
            first_bar_high = float(first_bar.high)
            first_bar_low = float(first_bar.low)
            current_price = pattern.entry_price
            
            # Compute underlying move
            move_config = options_config.get("move_model", {})
            underlying_move = compute_underlying_move(
                first_bar_high=first_bar_high,
                first_bar_low=first_bar_low,
                current_price=current_price,
                k_first_bar=move_config.get("k_first_bar", 0.75),
                min_move_pct=move_config.get("min_move_pct", 0.0008),
                max_move_pct=move_config.get("max_move_pct", 0.0035)
            )
            
            # Get options chain
            from datetime import datetime, timedelta
            today = datetime.now()
            min_dte = today + timedelta(days=1)
            max_dte = today + timedelta(days=14)
            
            chain = self._alpaca.get_options_chain(
                underlying_symbol=symbol,
                expiration_date_gte=min_dte.strftime("%Y-%m-%d"),
                expiration_date_lte=max_dte.strftime("%Y-%m-%d")
            )
            
            if not chain:
                self._logger.log("twentymin_options_no_chain", {"symbol": symbol})
                return {"success": False, "error": f"No options chain for {symbol}"}
            
            # Select liquid contract - TIME-AWARE FILTER SELECTION
            direction = "LONG" if pattern.direction == SignalDirection.LONG else "SHORT"
            
            # Determine which filter set to use based on time from market open
            clock = get_market_clock()
            now = clock.now()
            market_open = clock.get_market_open()
            market_open_dt = now.replace(hour=market_open.hour, minute=market_open.minute, second=0, microsecond=0)
            minutes_from_open = max(0, (now - market_open_dt).total_seconds() / 60)
            
            early_session = options_config.get("early_session", {})
            normal_session = options_config.get("normal_session", {})
            early_applies = early_session.get("applies_minutes_from_open", 20)
            
            if minutes_from_open <= early_applies and early_session:
                # Use relaxed early-session filters
                filter_set = early_session
                self._logger.log("twentymin_using_early_filters", {
                    "symbol": symbol,
                    "minutes_from_open": minutes_from_open
                })
            else:
                # Use normal session filters (or legacy defaults)
                filter_set = normal_session if normal_session else options_config
            
            contract = select_liquid_contract(
                chain=chain,
                direction=direction,
                max_spread=filter_set.get("max_spread", options_config.get("max_spread", 0.50)),
                prefer_spread=options_config.get("prefer_spread", 0.30),
                min_volume=filter_set.get("min_volume", options_config.get("min_volume", 500)),
                min_open_interest=filter_set.get("min_open_interest", options_config.get("min_open_interest", 500)),
                prefer_delta_min=filter_set.get("prefer_delta_min", options_config.get("prefer_delta_min", 0.45)),
                prefer_delta_max=filter_set.get("prefer_delta_max", options_config.get("prefer_delta_max", 0.60)),
                max_spread_pct=filter_set.get("max_spread_pct", options_config.get("max_spread_pct", 15.0)),
                expected_move_pct=options_config.get("expected_move_pct", 1.5),
                logger=self._logger.log  # Pass logger for detailed contract selection tracking
            )
            
            if not contract:
                self._logger.log("twentymin_options_no_contract", {
                    "symbol": symbol,
                    "direction": direction,
                    "chain_size": len(chain)
                })
                return {"success": False, "error": "No liquid contract found"}
            
            # Compute option bracket
            bands = options_config.get("bracket_bands", {})
            bracket = compute_option_bracket(
                entry_mid=contract.mid,
                delta=contract.delta,
                underlying_move=underlying_move,
                stop_pct_min=bands.get("stop_pct_min", 0.04),
                stop_pct_max=bands.get("stop_pct_max", 0.08),
                tp_pct_min=bands.get("tp_pct_min", 0.04),
                tp_pct_max=bands.get("tp_pct_max", 0.08)
            )
            
            # Compute contract quantity
            daily_budget = risk_config.get("daily_budget_usd", 200)
            max_contracts = risk_config.get("max_contracts", 10)
            
            qty = compute_contract_qty(
                daily_budget_usd=daily_budget,
                entry_mid=contract.mid,
                stop_price=bracket.stop_price,
                max_contracts=max_contracts
            )
            
            if qty == 0:
                self._logger.log("twentymin_options_qty_zero", {
                    "symbol": symbol,
                    "contract": str(contract),
                    "entry_mid": contract.mid,
                    "stop_price": bracket.stop_price,
                    "daily_budget": daily_budget
                })
                return {"success": False, "error": "Insufficient budget for 1 contract"}
            
            # Log the trade attempt (structured line per spec)
            self._logger.log("twentymin_options_entry_attempt", {
                "symbol": symbol,
                "direction": direction,
                "contract": f"{contract.expiry}/{contract.strike}/{contract.right}",
                "delta": round(contract.delta, 3),
                "spread": round(contract.spread, 2),
                "entry_mid": round(contract.mid, 2),
                "stop": round(bracket.stop_price, 2),
                "tp": round(bracket.tp_price, 2),
                "pct": round(bracket.pct * 100, 1),
                "qty": qty,
                "daily_budget": daily_budget
            })
            
            # Check for dry-run / simulation mode
            settings = load_settings()
            is_paper = settings.get("paper_trading", True)
            dry_run = execution_config.get("dry_run", False)
            
            if dry_run:
                self._logger.log("twentymin_options_dry_run", {
                    "would_place": True,
                    "contract_symbol": contract.symbol,
                    "qty": qty,
                    "entry": contract.mid,
                    "stop": bracket.stop_price,
                    "tp": bracket.tp_price
                })
                return {
                    "success": True,
                    "dry_run": True,
                    "contract": contract.symbol,
                    "qty": qty
                }
            
            # Place bracket order
            order_result = self._alpaca.place_options_bracket_order(
                symbol=contract.symbol,
                qty=qty,
                side="buy",  # Always buying options (call for long, put for short)
                limit_price=contract.mid + 0.01,  # Mid + 1 tick
                stop_loss_price=bracket.stop_price,
                take_profit_price=bracket.tp_price,
                time_in_force="day"
            )
            
            if order_result and order_result.get("success"):
                entry_time = get_market_clock().now()
                order_id = order_result.get("order_id", "unknown")
                
                set_state(f"twentymin.entry.{symbol}", {
                    "order_id": order_id,
                    "entry_time": entry_time.isoformat(),
                    "entry_price": contract.mid,
                    "stop_price": bracket.stop_price,
                    "target_price": bracket.tp_price,
                    "pattern": pattern.pattern.value,
                    "direction": pattern.direction.value,
                    "is_option": True,
                    "contract_symbol": contract.symbol,
                    "qty": qty,
                    "bracket_pending": True  # OCO legs need placement after fill
                })
                
                self._logger.log("twentymin_options_entry_success", {
                    "symbol": symbol,
                    "contract": contract.symbol,
                    "order_id": order_id,
                    "qty": qty,
                    "entry": contract.mid,
                    "stop": bracket.stop_price,
                    "tp": bracket.tp_price
                })
                
                return {"success": True, "order_id": order_id, "contract": contract.symbol}
            else:
                error = order_result.get("error", "Order failed") if order_result else "No response"
                return {"success": False, "error": error}
                
        except Exception as e:
            self._logger.error(f"Options entry execution failed for {symbol}: {e}")
            return {"success": False, "error": str(e)}
    
    def process_pending_brackets(self) -> Dict[str, Any]:
        """
        Process all entries with bracket_pending=True.
        
        For each pending entry, check if the entry order is filled.
        If filled, place the exit orders (TP and SL).
        
        This should be called in the trading loop after entries are placed.
        
        Returns:
            Summary of processed entries
        """
        results = {"processed": 0, "exits_placed": 0, "errors": []}
        
        try:
            # Find all twentymin entries with bracket_pending
            from ..core.state import get_all_states
            
            all_states = get_all_states()
            entry_keys = [k for k in all_states.keys() if k.startswith("twentymin.entry.")]
            
            for key in entry_keys:
                try:
                    entry_data = get_state(key)
                    if not entry_data:
                        continue
                    
                    # Skip non-options or already processed
                    if not entry_data.get("is_option"):
                        continue
                    if not entry_data.get("bracket_pending"):
                        continue
                    
                    results["processed"] += 1
                    
                    order_id = entry_data.get("order_id")
                    contract_symbol = entry_data.get("contract_symbol")
                    qty = entry_data.get("qty", 1)
                    stop_price = entry_data.get("stop_price")
                    target_price = entry_data.get("target_price")
                    symbol = key.replace("twentymin.entry.", "")
                    
                    # Check if entry order is filled
                    order_status = self._alpaca.get_order_status(order_id)
                    
                    if not order_status:
                        continue
                    
                    status = order_status.get("status", "").lower()
                    
                    if status == "filled":
                        # Only process fully filled orders (not partial)
                        filled_qty = int(order_status.get("filled_qty", qty))
                        
                        # Get filled price - use order's filled_avg_price or fallback to entry_price
                        raw_filled_price = order_status.get("filled_avg_price", 0)
                        filled_price = float(raw_filled_price) if raw_filled_price else float(entry_data.get("entry_price", 0))
                        
                        self._logger.log("twentymin_options_entry_filled", {
                            "symbol": symbol,
                            "contract": contract_symbol,
                            "filled_qty": filled_qty,
                            "filled_price": filled_price,
                            "raw_filled_price": raw_filled_price
                        })
                        
                        # Always persist filled_price immediately
                        entry_data["filled_price"] = filled_price
                        entry_data["filled_qty"] = filled_qty
                        set_state(key, entry_data)
                        
                        # Check which legs already exist from previous retries
                        existing_tp = entry_data.get("tp_order_id")
                        existing_sl = entry_data.get("sl_order_id")
                        
                        # Check retry limits (max 5 retries, then escalate)
                        retry_count = entry_data.get("retry_count", 0)
                        MAX_EXIT_RETRIES = 5
                        
                        if retry_count >= MAX_EXIT_RETRIES:
                            # Max retries exceeded - FLATTEN position then HALT (fail-closed)
                            entry_data["exits_failed"] = True
                            entry_data["bracket_pending"] = False  # Stop retrying this entry
                            set_state(key, entry_data)
                            
                            # First, attempt to flatten the unprotected position
                            flatten_result = None
                            try:
                                flatten_result = self._alpaca.close_position(contract_symbol)
                                self._logger.log("twentymin_options_emergency_flatten", {
                                    "symbol": symbol,
                                    "contract": contract_symbol,
                                    "result": flatten_result
                                })
                            except Exception as flatten_err:
                                self._logger.error(f"EMERGENCY FLATTEN FAILED for {contract_symbol}: {flatten_err}")
                                entry_data["flatten_failed"] = True
                                set_state(key, entry_data)
                            
                            # Then trigger halt to prevent new trades
                            from ..core.halt import HaltManager
                            halt_manager = HaltManager()
                            halt_manager.set_halt(f"UNPROTECTED_POSITION: {symbol} exits failed after {retry_count} retries - FLATTENED", cooloff_minutes=0)
                            
                            self._logger.error(f"CRITICAL: Exit placement failed after {retry_count} retries for {symbol} - FLATTENED AND HALTING SYSTEM")
                            self._logger.log("twentymin_options_exits_max_retry", {
                                "symbol": symbol,
                                "tp_order": existing_tp,
                                "sl_order": existing_sl,
                                "retry_count": retry_count,
                                "status": "UNPROTECTED_POSITION",
                                "action": "FLATTEN_AND_HALT",
                                "flatten_result": flatten_result
                            })
                            results["errors"].append(f"{symbol}: EXIT FAILURE - max retries exceeded, FLATTENED AND HALTED")
                            results["halted"] = True
                            results["flattened"] = True
                            continue
                        
                        # Determine which legs need placement
                        need_tp = not existing_tp
                        need_sl = not existing_sl
                        
                        # Terminal/dead statuses that require replacement
                        DEAD_STATUSES = {
                            "cancelled", "expired", "rejected", "replaced",
                            "done_for_day", "stopped", "suspended", "calculated",
                            "pending_cancel", "pending_replace", "error"
                        }
                        # Active/working statuses that are ok
                        ACTIVE_STATUSES = {"new", "accepted", "pending_new", "partially_filled", "filled"}
                        
                        # Track if either exit has filled (bracket complete)
                        tp_filled = False
                        sl_filled = False
                        
                        # Verify existing orders are still live (not cancelled/expired/dead)
                        if existing_tp:
                            tp_status_data = self._alpaca.get_order_status(existing_tp)
                            if tp_status_data:
                                tp_order_status = tp_status_data.get("status", "").lower()
                                if tp_order_status == "filled":
                                    tp_filled = True  # TP hit - position closed by this exit
                                elif tp_order_status in DEAD_STATUSES or tp_order_status not in ACTIVE_STATUSES:
                                    # Order no longer live - may need to replace
                                    self._logger.warn(f"TP order {existing_tp} is {tp_order_status}")
                                    entry_data.pop("tp_order_id", None)
                                    need_tp = True
                        
                        if existing_sl:
                            sl_status_data = self._alpaca.get_order_status(existing_sl)
                            if sl_status_data:
                                sl_order_status = sl_status_data.get("status", "").lower()
                                if sl_order_status == "filled":
                                    sl_filled = True  # SL hit - position closed by this exit
                                elif sl_order_status in DEAD_STATUSES or sl_order_status not in ACTIVE_STATUSES:
                                    # Order no longer live - may need to replace
                                    self._logger.warn(f"SL order {existing_sl} is {sl_order_status}")
                                    entry_data.pop("sl_order_id", None)
                                    need_sl = True
                        
                        # If EITHER exit filled, the bracket succeeded (OCO behavior)
                        if tp_filled or sl_filled:
                            entry_data["bracket_pending"] = False
                            entry_data["exits_complete"] = True
                            entry_data["exit_reason"] = "tp_filled" if tp_filled else "sl_filled"
                            set_state(key, entry_data)
                            results["exits_placed"] += 1
                            self._logger.log("twentymin_options_bracket_complete", {
                                "symbol": symbol,
                                "tp_filled": tp_filled,
                                "sl_filled": sl_filled,
                                "exit_reason": entry_data["exit_reason"]
                            })
                            continue
                        
                        # Before requeuing exits, verify position still exists
                        try:
                            positions = self._alpaca.get_positions()
                            # AlpacaPosition is a dataclass with .symbol attribute, or dict
                            # Note: Alpaca may use different symbol formats (OCC vs full precision)
                            # We check for substring match to handle format differences
                            position_exists = False
                            
                            # Extract underlying + expiry + type from contract_symbol for matching
                            # e.g., AAPL240119C00150000 -> match AAPL240119C
                            contract_prefix = contract_symbol[:15] if len(contract_symbol) > 15 else contract_symbol
                            
                            for p in positions:
                                if isinstance(p, dict):
                                    pos_symbol = p.get('symbol', '')
                                else:
                                    # Dataclass - use attribute access
                                    pos_symbol = getattr(p, 'symbol', '') or ''
                                
                                # Match by prefix or exact match
                                if pos_symbol == contract_symbol or pos_symbol.startswith(contract_prefix) or contract_symbol.startswith(pos_symbol):
                                    position_exists = True
                                    break
                            
                            if not position_exists:
                                # Position is gone (closed externally) - mark complete
                                entry_data["bracket_pending"] = False
                                entry_data["exits_complete"] = True
                                entry_data["exit_reason"] = "position_closed_externally"
                                set_state(key, entry_data)
                                self._logger.log("twentymin_options_position_gone", {
                                    "symbol": symbol,
                                    "contract": contract_symbol,
                                    "action": "marked_complete"
                                })
                                continue
                        except Exception as pos_err:
                            self._logger.warn(f"Could not verify position for {contract_symbol}: {pos_err}")
                        
                        if not need_tp and not need_sl:
                            # Both legs exist and are live - mark complete
                            entry_data["bracket_pending"] = False
                            entry_data["exits_complete"] = True
                            set_state(key, entry_data)
                            results["exits_placed"] += 1
                            self._logger.log("twentymin_options_exits_verified_complete", {
                                "symbol": symbol,
                                "tp_order": existing_tp,
                                "sl_order": existing_sl,
                                "status": "both_orders_live"
                            })
                            continue
                        
                        # Place only the missing legs
                        new_tp_id = None
                        new_sl_id = None
                        
                        if need_tp:
                            try:
                                tp_result = self._alpaca.place_options_order(
                                    symbol=contract_symbol,
                                    qty=filled_qty,
                                    side="sell",
                                    order_type="limit",
                                    limit_price=target_price
                                )
                                # Check both 'id' and 'success' flags
                                if tp_result:
                                    if tp_result.get("id"):
                                        new_tp_id = tp_result.get("id")
                                        entry_data["tp_order_id"] = new_tp_id
                                        entry_data.pop("tp_error", None)  # Clear error
                                    elif tp_result.get("status") == "rejected" or tp_result.get("success") == False:
                                        entry_data["tp_error"] = tp_result.get("error", "Rejected without exception")
                                        self._logger.warn(f"TP order rejected for {symbol}: {entry_data['tp_error']}")
                            except Exception as tp_err:
                                entry_data["tp_error"] = str(tp_err)
                                self._logger.error(f"TP order exception for {symbol}: {tp_err}")
                        
                        if need_sl:
                            try:
                                sl_result = self._alpaca.place_options_order(
                                    symbol=contract_symbol,
                                    qty=filled_qty,
                                    side="sell",
                                    order_type="limit",
                                    limit_price=stop_price
                                )
                                # Check both 'id' and 'success' flags
                                if sl_result:
                                    if sl_result.get("id"):
                                        new_sl_id = sl_result.get("id")
                                        entry_data["sl_order_id"] = new_sl_id
                                        entry_data.pop("sl_error", None)  # Clear error
                                    elif sl_result.get("status") == "rejected" or sl_result.get("success") == False:
                                        entry_data["sl_error"] = sl_result.get("error", "Rejected without exception")
                                        self._logger.warn(f"SL order rejected for {symbol}: {entry_data['sl_error']}")
                            except Exception as sl_err:
                                entry_data["sl_error"] = str(sl_err)
                                self._logger.error(f"SL order exception for {symbol}: {sl_err}")
                        
                        # Check completion based on union of old + new IDs
                        final_tp = entry_data.get("tp_order_id")
                        final_sl = entry_data.get("sl_order_id")
                        
                        if final_tp and final_sl:
                            # Both legs now exist - fully protected
                            entry_data["bracket_pending"] = False
                            entry_data["exits_complete"] = True
                            set_state(key, entry_data)
                            
                            results["exits_placed"] += 1
                            
                            self._logger.log("twentymin_options_exits_complete", {
                                "symbol": symbol,
                                "tp_order": final_tp,
                                "sl_order": final_sl,
                                "status": "fully_protected"
                            })
                        else:
                            # Still missing at least one leg - increment retry, keep pending
                            entry_data["retry_count"] = retry_count + 1
                            entry_data["exits_partial"] = True
                            set_state(key, entry_data)
                            
                            missing = []
                            if not final_tp:
                                missing.append(f"TP({entry_data.get('tp_error', 'unknown')})")
                            if not final_sl:
                                missing.append(f"SL({entry_data.get('sl_error', 'unknown')})")
                            
                            self._logger.log("twentymin_options_exits_partial", {
                                "symbol": symbol,
                                "tp_order": final_tp,
                                "sl_order": final_sl,
                                "missing": missing,
                                "retry_count": entry_data.get("retry_count", 0),
                                "status": "partial_protection"
                            })
                            
                            results["errors"].append(f"{symbol}: Missing {'/'.join(missing)} - retry {entry_data.get('retry_count')}/{MAX_EXIT_RETRIES}")
                    
                    elif status == "partially_filled":
                        # Wait for full fill before placing exits
                        self._logger.log("twentymin_options_partial_fill", {
                            "symbol": symbol,
                            "filled_qty": order_status.get("filled_qty"),
                            "total_qty": qty,
                            "action": "waiting_for_full_fill"
                        })
                    
                    elif status in ("cancelled", "expired", "rejected"):
                        # Entry failed, clean up
                        delete_state(key)
                        self._logger.log("twentymin_options_entry_failed", {
                            "symbol": symbol,
                            "status": status
                        })
                        
                except Exception as entry_err:
                    results["errors"].append(f"{key}: {str(entry_err)}")
                    self._logger.error(f"Error processing bracket for {key}: {entry_err}")
                    
        except Exception as e:
            self._logger.error(f"Failed to process pending brackets: {e}")
            results["errors"].append(str(e))
        
        return results
    
    def _execute_stock_entry(
        self, symbol: str, pattern: PatternSignal, gap: GapAnalysis, budget: float
    ) -> Dict[str, Any]:
        """
        Execute stock entry (fallback when options disabled).
        Original stock-based entry logic.
        BLOCKED: Options-only mode — no stock entries allowed.
        """
        self._logger.log("twentymin_stock_entry_BLOCKED", {
            "symbol": symbol,
            "reason": "OPTIONS_ONLY_MODE",
            "message": "Stock entries permanently disabled — options only"
        })
        return {"success": False, "error": "OPTIONS_ONLY_MODE: Stock entries disabled"}
        try:
            settings = load_settings()
            institutional = settings.get("institutional_sizing", {})
            
            if institutional.get("enabled", False):
                base_risk_pct = institutional.get("base_risk_pct", 0.5)
                max_position_pct = institutional.get("max_single_position_pct", 3.0)
                
                account = self._alpaca.get_account()
                equity = float(account.equity)
                
                position_size = equity * (base_risk_pct / 100) * pattern.confidence
                max_size = equity * (max_position_pct / 100)
                position_size = min(position_size, max_size, budget)
            else:
                position_size = budget * 0.3
            
            min_notional = institutional.get("min_notional", 15.0)
            if position_size < min_notional:
                return {
                    "success": False,
                    "error": f"Position size {position_size:.2f} below minimum {min_notional}"
                }
            
            side = "buy" if pattern.direction == SignalDirection.LONG else "sell"
            trade_side = "long" if pattern.direction == SignalDirection.LONG else "short"
            
            # =====================================================================
            # ExitBot v2 Integration - Generate signal identity BEFORE order
            # =====================================================================
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            signal_id = f"TM_{symbol}_{trade_side}_{ts}_{uuid4().hex[:6]}"
            client_order_id = f"twentyminute_bot:{symbol}:{trade_side}:{signal_id}"
            
            # Check ExitBot health before entry (fail-closed enforcement)
            exitbot = get_exitbot()
            if not exitbot.is_healthy():
                self._logger.warn(f"ExitBot unhealthy - blocking entry for {symbol}")
                return {"success": False, "error": "ExitBot unhealthy - entry blocked"}
            
            self._logger.log("twentymin_entry_attempt", {
                "symbol": symbol,
                "side": side,
                "pattern": pattern.pattern.value,
                "confidence": pattern.confidence,
                "entry_price": pattern.entry_price,
                "position_size": position_size,
                "signal_id": signal_id,
                "client_order_id": client_order_id
            })
            
            order_result = self._alpaca.place_market_order(
                symbol=symbol,
                notional=position_size,
                side=side,
                client_order_id=client_order_id
            )
            order = order_result if order_result and order_result.get("success") else None
            
            if order:
                entry_time = get_market_clock().now()
                order_id = order_result.get("order_id", "unknown")
                
                # =====================================================================
                # ExitBot v2 - Register entry intent for lifecycle tracking
                # =====================================================================
                position_key = exitbot.register_entry_intent(
                    bot_id="twentyminute_bot",
                    symbol=symbol,
                    side=trade_side,
                    qty=position_size / pattern.entry_price if pattern.entry_price > 0 else 0,
                    entry_price=pattern.entry_price,
                    signal_id=signal_id,
                    client_order_id=client_order_id,
                    alpaca_order_id=order_id,
                    asset_class="us_equity"
                )
                
                set_state(f"twentymin.entry.{symbol}", {
                    "order_id": order_id,
                    "entry_time": entry_time.isoformat(),
                    "entry_price": pattern.entry_price,
                    "stop_price": pattern.stop_price,
                    "target_price": pattern.target_price,
                    "pattern": pattern.pattern.value,
                    "direction": pattern.direction.value,
                    "signal_id": signal_id,
                    "client_order_id": client_order_id,
                    "position_key": position_key
                })
                
                self._logger.log("twentymin_entry_success", {
                    "symbol": symbol,
                    "order_id": order_id,
                    "side": side,
                    "notional": position_size,
                    "signal_id": signal_id,
                    "position_key": position_key
                })
                
                return {"success": True, "order_id": order_id, "position_key": position_key}
            else:
                return {"success": False, "error": "Order submission returned None"}
                
        except Exception as e:
            self._logger.error(f"Entry execution failed for {symbol}: {e}")
            return {"success": False, "error": str(e)}
    
    def _manage_position(self, position) -> Dict[str, Any]:
        """
        Manage an existing position - check for exit conditions.
        
        With delegate_exits_to_exitbot enabled (default), this method reports
        position status for monitoring without executing exit orders. All exit
        decisions are delegated to ExitBot v2, making it the sole exit authority.
        
        When delegate_exits_to_exitbot is disabled, uses self-managed exit logic:
        1. Stop loss hit
        2. Take profit hit
        3. Max hold time exceeded
        4. EMA cross exit
        """
        symbol = position.symbol
        entry_data = get_state(f"twentymin.entry.{symbol}") or {}
        
        current_price = float(position.current_price)
        entry_price = float(entry_data.get("entry_price", position.avg_entry_price))
        stop_price = float(entry_data.get("stop_price", 0))
        target_price = float(entry_data.get("target_price", 0))
        direction = entry_data.get("direction", "long")
        
        pnl_pct = ((current_price - entry_price) / entry_price) * 100
        if direction == "short":
            pnl_pct = -pnl_pct
        
        entry_time_str = entry_data.get("entry_time")
        if entry_time_str:
            from ..core.clock import MarketClock
            entry_time = MarketClock.parse_iso_to_naive(entry_time_str)
            hold_minutes = (get_market_clock().now_naive() - entry_time).total_seconds() / 60
        else:
            hold_minutes = 0
        
        # CHECK: Delegate exits to ExitBot v2 (sole exit authority)
        delegate_exits = self._config.delegate_exits_to_exitbot if self._config else True
        if delegate_exits:
            self._logger.log("twentymin_delegating_to_exitbot", {
                "symbol": symbol,
                "direction": direction,
                "hold_minutes": round(hold_minutes, 1),
                "pnl_pct": round(pnl_pct, 2),
                "entry_price": round(entry_price, 2),
                "current_price": round(current_price, 2),
                "status": "monitoring_only"
            })
            return {
                "exited": False,
                "delegated_to_exitbot": True,
                "hold_minutes": hold_minutes,
                "pnl_pct": pnl_pct
            }
        
        # Self-managed exit logic (only when delegate_exits_to_exitbot is False)
        exit_reason = None
        
        if direction == "long":
            if stop_price > 0 and current_price <= stop_price:
                exit_reason = "stop_loss"
            elif target_price > 0 and current_price >= target_price:
                exit_reason = "take_profit"
        else:
            if stop_price > 0 and current_price >= stop_price:
                exit_reason = "stop_loss"
            elif target_price > 0 and current_price <= target_price:
                exit_reason = "take_profit"
        
        max_hold = self._config.max_hold_minutes if self._config else 15
        if hold_minutes >= max_hold:
            exit_reason = "time_stop"
        
        # EMA cross exit rule per trading philosophy (self-managed only)
        # If EMA crosses against position direction, exit early to protect profits
        if not exit_reason and hold_minutes >= 2:  # Only check after 2 mins to avoid whipsaw
            try:
                bars = self._get_stock_bars(symbol, timeframe="1Min", limit=20)
                if bars and len(bars) >= 10:
                    indicators = self._compute_momentum_indicators(symbol, bars)
                    
                    # LONG: Exit when 9 EMA *crosses* below 20 EMA (event, not continuous level)
                    if direction == "long" and indicators.ema_bearish_cross_event:
                        exit_reason = "ema_cross_exit"
                        self._logger.log("twentymin_ema_exit", {
                            "symbol": symbol,
                            "direction": direction,
                            "ema_9": round(indicators.ema_9, 2),
                            "ema_20": round(indicators.ema_20, 2),
                            "pnl_pct": round(pnl_pct, 2),
                            "hold_minutes": round(hold_minutes, 1)
                        })

                    # SHORT: Exit when 9 EMA *crosses* above 20 EMA (event, not continuous level)
                    elif direction == "short" and indicators.ema_bullish_cross_event:
                        exit_reason = "ema_cross_exit"
                        self._logger.log("twentymin_ema_exit", {
                            "symbol": symbol,
                            "direction": direction,
                            "ema_9": round(indicators.ema_9, 2),
                            "ema_20": round(indicators.ema_20, 2),
                            "pnl_pct": round(pnl_pct, 2),
                            "hold_minutes": round(hold_minutes, 1)
                        })
            except Exception as e:
                self._logger.error(f"EMA exit check failed for {symbol}: {e}")
        
        if exit_reason:
            self._flatten_position(position, exit_reason)
            return {"exited": True, "reason": exit_reason, "pnl_pct": pnl_pct}
        
        return {"exited": False, "hold_minutes": hold_minutes, "pnl_pct": pnl_pct}
    
    def _flatten_position(self, position, reason: str):
        """Close a position completely."""
        try:
            symbol = position.symbol
            qty = abs(float(position.qty))
            side = "sell" if float(position.qty) > 0 else "buy"
            
            # Get entry data to check if this is an options position
            entry_data = get_state(f"twentymin.entry.{symbol}") or {}
            is_option = entry_data.get("is_option", False)
            contract_symbol = entry_data.get("contract_symbol", symbol)
            
            self._logger.log("twentymin_flatten", {
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "reason": reason,
                "is_option": is_option
            })
            
            # Use appropriate order method for options vs stocks
            if is_option and contract_symbol:
                order_result = self._alpaca.place_options_order(
                    symbol=contract_symbol,
                    qty=int(entry_data.get("qty", qty)),
                    side=side,
                    order_type="market"
                )
            else:
                order_result = self._alpaca.place_market_order(
                    symbol=symbol,
                    qty=qty,
                    side=side
                )
            
            if order_result and (order_result.get("success") or order_result.get("id")):
                # Calculate P&L for options loss tracking
                if is_option:
                    # Use filled_price from entry data (more reliable than position.current_price)
                    entry_price = float(entry_data.get("filled_price") or entry_data.get("entry_price", 0))
                    option_qty = int(entry_data.get("qty", 1))
                    
                    # Get exit price from order fill if available
                    exit_price = 0
                    if order_result.get("filled_avg_price"):
                        exit_price = float(order_result.get("filled_avg_price"))
                    elif hasattr(position, 'unrealized_pl'):
                        # Calculate from unrealized P&L if available
                        unrealized_pl = float(position.unrealized_pl) if position.unrealized_pl else 0
                        if entry_price > 0 and option_qty > 0:
                            exit_price = entry_price + (unrealized_pl / (option_qty * 100))
                    
                    if exit_price > 0 and entry_price > 0:
                        pnl_usd = (exit_price - entry_price) * option_qty * 100
                        
                        # Record loss if negative P&L
                        if pnl_usd < 0:
                            self.record_options_loss(symbol, pnl_usd)
                
                delete_state(f"twentymin.entry.{symbol}")
                self._logger.log("twentymin_flatten_success", {
                    "symbol": symbol,
                    "order_id": order_result.get("order_id", order_result.get("id", "unknown")),
                    "reason": reason,
                    "is_option": is_option
                })
            
        except Exception as e:
            self._logger.error(f"Flatten failed for {position.symbol}: {e}")
    
    def _can_trade_today(self) -> bool:
        """Check if daily trade limit has been reached."""
        today = get_market_clock().now().strftime("%Y-%m-%d")
        trade_count_key = f"twentymin.trades.{today}"
        trade_count = get_state(trade_count_key) or 0
        
        max_trades = self._config.max_trades_per_day if self._config else 5
        return trade_count < max_trades
    
    def _reserve_trade_slot(self) -> bool:
        """
        Atomically reserve a trade slot to prevent race conditions.
        """
        max_trades = self._config.max_trades_per_day if self._config else 5
        today = get_market_clock().now().strftime("%Y-%m-%d")
        trade_count_key = f"twentymin.trades.{today}"
        
        success, new_count = atomic_increment(trade_count_key, max_trades)
        
        if success:
            self._logger.log("twentymin_trade_slot_reserved", {
                "date": today,
                "new_count": new_count,
                "max_trades": max_trades
            })
        
        return success
    
    def _record_trade(self, symbol: str):
        """
        Legacy method - now a no-op since _reserve_trade_slot handles counting.
        """
        pass
    
    def record_options_loss(self, symbol: str, pnl_usd: float):
        """
        Record an options trade loss for fail-closed safety.
        
        Called by exit management when an options trade exits at a loss.
        Increments the daily loss counter used by stop_after_losses gate.
        
        Args:
            symbol: The underlying symbol
            pnl_usd: The P&L in USD (negative for loss)
        """
        if pnl_usd >= 0:
            # Not a loss, nothing to record
            return
        
        today = get_market_clock().now().strftime("%Y-%m-%d")
        loss_key = f"twentymin.options.daily_losses.{today}"
        current_losses = get_state(loss_key) or 0
        new_losses = current_losses + 1
        set_state(loss_key, new_losses)
        
        # Also update the simple key for backwards compatibility
        set_state("twentymin.options.daily_losses", new_losses)
        
        self._logger.log("twentymin_options_loss_recorded", {
            "symbol": symbol,
            "pnl_usd": round(pnl_usd, 2),
            "date": today,
            "total_losses_today": new_losses
        })
    
    def reset_daily_options_losses(self):
        """
        Reset the daily options loss counter.
        
        Called at start of trading day to clear previous day's losses.
        """
        today = get_market_clock().now().strftime("%Y-%m-%d")
        set_state(f"twentymin.options.daily_losses.{today}", 0)
        set_state("twentymin.options.daily_losses", 0)
        
        self._logger.log("twentymin_options_losses_reset", {
            "date": today
        })
    
    def get_daily_options_losses(self) -> int:
        """
        Get the current count of options losses for today.
        
        Returns:
            Number of losing options trades today
        """
        today = get_market_clock().now().strftime("%Y-%m-%d")
        return get_state(f"twentymin.options.daily_losses.{today}") or 0


# =============================================================================
# SINGLETON FACTORY - Provides cached bot instance
# =============================================================================

_twenty_minute_bot_instance: Optional[TwentyMinuteBot] = None


def get_twenty_minute_bot(bot_id: str = "twentyminute_core") -> TwentyMinuteBot:
    """
    Get or create the TwentyMinuteBot singleton instance.
    
    Args:
        bot_id: Identifier (unused - TwentyMinuteBot uses hardcoded bot_id)
        
    Returns:
        TwentyMinuteBot instance (cached)
    """
    global _twenty_minute_bot_instance
    if _twenty_minute_bot_instance is None:
        _twenty_minute_bot_instance = TwentyMinuteBot()
    return _twenty_minute_bot_instance
