"""
Crypto Trading Bot for BTC/USD and ETH/USD - PRODUCTION LEVEL
==============================================================

This bot implements a production-ready momentum trading strategy for
cryptocurrencies. Unlike stock trading, crypto markets are open 24/7,
so this bot has no trading hour restrictions.

PRODUCTION FEATURES:
- Price freshness validation (reject stale quotes > 30 seconds)
- Enhanced signal strategy with SMA + RSI + MACD confirmation
- Pre-trade risk checks (cash, equity, daily loss budget)
- Smart order execution with bid/ask spread analysis and slippage protection
- Robust error handling with exponential backoff retry logic
- Hardened trailing stops with fallback hard stop-loss
- Clear audit trail distinguishing real vs cached data

Key Features:
- Trades BTC/USD and ETH/USD pairs
- Reads all configuration from config/bots.yaml
- Enforces max trades per day limit
- Enforces max concurrent positions limit  
- Implements trailing stops from config
- Enforces stop-loss and take-profit percentages
- Implements time-based exits (max hold duration)
- 24/7 operation (no session time restrictions)

Safety Features:
- Minimum order size enforcement ($15 to exceed Alpaca's $10 minimum)
- Cooldown between trades to prevent over-trading
- Fail-closed design - any error stops the bot safely
- All trades logged for audit trail
- Integrates with global halt manager
"""

from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from uuid import uuid4
import time
import math
import traceback

from ..core.logging import get_logger
from ..core.state import get_state, set_state, delete_state, atomic_increment
from ..core.config import load_bots_config, load_settings
from ..core.clock import get_market_clock
from ..services.alpaca_client import get_alpaca_client
from ..services.market_regime import get_current_regime, MarketSentiment, VolatilityRegime
from ..services.decision_tracker import get_decision_tracker
from ..services.exitbot import get_exitbot
from ..risk.trailing_stop import get_trailing_stop_manager, TrailingStopConfig
from ..risk.profit_sniper import get_profit_sniper, ProfitSniperConfig
from ..risk.position_sizer import get_position_sizer
from ..risk.correlation_manager import get_correlation_manager
from ..services.crypto_universe import get_crypto_universe
from ..ml.signal_service import MLSignalService
from ..ml.feature_extractor import get_feature_extractor
from ..indicators.turtle_trend import TurtleTrend, TurtleConfig, TurtleSystem, SignalType
from ..indicators.vwap_posture import (
    VWAPPostureManager, VWAPPosture, get_vwap_posture_manager, PostureDecision
)
from ..strategy.whipsaw_trader import get_whipsaw_trader
from ..risk.killswitch import get_killswitch_service
from ..utils import calculate_dynamic_settings
from ..sensors import is_risk_off, get_regime as get_sensors_regime


# =============================================================================
# PRODUCTION CONSTANTS - Loaded from config/bots.yaml > cryptobot > constants
# =============================================================================

def _get_crypto_constants():
    """Load CryptoBot constants from config with hardcoded fallbacks."""
    try:
        config = load_bots_config()
        c = config.get("cryptobot", {}).get("constants", {})
    except Exception:
        c = {}
    return c

_crypto_cfg = _get_crypto_constants()

MAX_QUOTE_AGE_SECONDS = _crypto_cfg.get("max_quote_age_seconds", 30)
TRADE_COOLDOWN_SECONDS = _crypto_cfg.get("trade_cooldown_seconds", 180)

DEFAULT_MIN_HOLD_MINUTES = 10
DEFAULT_STOPOUT_COOLDOWN_MINUTES = 60
DEFAULT_MAX_CONSECUTIVE_STOPOUTS = 3
DEFAULT_WHIPSAW_PAUSE_MINUTES = 120

MAX_SPREAD_PCT = _crypto_cfg.get("max_spread_pct", 2.0)
MIN_NOTIONAL_USD = _crypto_cfg.get("min_notional_usd", 200.0)
SLIPPAGE_BUFFER_PCT = _crypto_cfg.get("slippage_buffer_pct", 0.1)

MAX_RETRIES = _crypto_cfg.get("max_retries", 3)
RETRY_BACKOFF_BASE = 2

RSI_OVERSOLD = _crypto_cfg.get("rsi_oversold", 30)
RSI_OVERBOUGHT = _crypto_cfg.get("rsi_overbought", 70)
RSI_PERIOD = _crypto_cfg.get("rsi_period", 14)

MACD_FAST = _crypto_cfg.get("macd_fast", 12)
MACD_SLOW = _crypto_cfg.get("macd_slow", 26)
MACD_SIGNAL = _crypto_cfg.get("macd_signal", 9)


# =============================================================================
# HELPER FUNCTIONS - Symbol normalization for Alpaca API
# =============================================================================

def normalize_crypto_symbol(symbol: str) -> str:
    """
    Convert Alpaca position symbol format to quote API format.
    
    Alpaca returns positions with symbols like "BTCUSD", "ETHUSD", "AAVEUSD"
    but the quote API requires "BTC/USD", "ETH/USD", "AAVE/USD" format.
    
    Args:
        symbol: Crypto symbol in any format (e.g., "BTCUSD", "BTC/USD", "DOTUSD")
    
    Returns:
        Symbol in API format with "/" separator (e.g., "BTC/USD", "DOT/USD")
    """
    # Already in correct format
    if "/" in symbol:
        return symbol
    
    # Convert XXXUSD -> XXX/USD
    if symbol.endswith("USD"):
        base = symbol[:-3]  # Remove "USD" suffix
        return f"{base}/USD"
    
    # Return as-is if not a USD pair
    return symbol


def denormalize_crypto_symbol(symbol: str) -> str:
    """
    Convert quote API format to position symbol format.
    
    Args:
        symbol: Crypto symbol in API format (e.g., "BTC/USD")
    
    Returns:
        Symbol without "/" separator (e.g., "BTCUSD")
    """
    return symbol.replace("/", "")


# =============================================================================
# DATA CLASSES - Define structure for bot configuration and state
# =============================================================================

@dataclass
class CryptoConfig:
    """
    Configuration for the crypto trading bot.
    All values come from config/bots.yaml under cryptobot section.
    Default values provided for optional fields to ensure robustness.
    """
    bot_id: str                      # Unique identifier (e.g., "crypto_core")
    enabled: bool                    # Whether this bot is active
    pairs: List[str]                 # Crypto pairs to trade (e.g., ["BTC/USD", "ETH/USD"])
    max_trades_per_day: int          # Maximum trades allowed per day
    max_concurrent_positions: int    # Maximum positions held at once
    min_order_size: float            # Minimum order size in USD (default $15)
    default_notional: float          # Default trade size in USD
    stop_loss_pct: float             # Stop loss percentage (e.g., 0.75 = 0.75%)
    take_profit_pct: float           # Take profit percentage (e.g., 1.50 = 1.50%)
    time_stop_minutes: int           # Max hold time in minutes before forced exit
    trailing_stop_enabled: bool = False  # Whether to use trailing stops (default: disabled)
    trailing_stop_mode: str = "percent"  # "percent" or "price" for trailing stop calculation
    trailing_stop_value: float = 1.5     # Trailing stop value (percent or dollar amount)
    trailing_activation_pct: float = 0.4 # Profit % required before trailing stop activates
    trailing_update_only_if_improves: bool = True  # Only raise stop, never lower
    trailing_epsilon_pct: float = 0.05   # Buffer to prevent noise triggers (higher for crypto)
    trailing_exit_order_type: str = "market"  # Exit order type
    # Production-level settings
    hard_stop_loss_pct: float = 2.0      # Hard stop-loss fallback if trailing fails
    max_spread_pct: float = 0.5          # Max bid/ask spread allowed
    require_rsi_confirmation: bool = True  # Require RSI confirmation for signals
    require_macd_confirmation: bool = True # Require MACD confirmation for signals
    # Anti-churn protection settings
    anti_churn_enabled: bool = True
    min_hold_minutes: int = 10           # Minimum hold before soft stop-loss
    stopout_cooldown_minutes: int = 30   # Extended cooldown after stop-out
    max_consecutive_stopouts: int = 3    # Whipsaw detection threshold
    whipsaw_pause_minutes: int = 120     # Pause duration after whipsaw detection
    # Turtle Traders strategy settings (24/7 adaptation using hourly bars)
    signal_mode: str = "momentum"         # "momentum" or "turtle"
    turtle_system: str = "system_1"       # "system_1" (20-day) or "system_2" (55-day)
    turtle_entry_lookback: int = 480      # 20 days * 24 hours = 480 hourly bars
    turtle_exit_lookback: int = 240       # 10 days * 24 hours = 240 hourly bars
    turtle_atr_period: int = 480          # ATR period in hours (same as entry lookback)
    turtle_risk_pct_per_unit: float = 1.0 # Risk 1% equity per unit
    turtle_stop_loss_atr_mult: float = 2.0 # Stop-loss = 2N (2x ATR)
    turtle_pyramid_enabled: bool = True   # Enable pyramiding (adding to winners)
    turtle_pyramid_trigger_atr: float = 0.5 # Add unit every 0.5N move
    turtle_max_units: int = 4             # Maximum pyramid units
    turtle_winner_filter: bool = True     # Skip next signal after profitable trade
    # VWAP Posture settings (institutional approach)
    vwap_enabled: bool = False            # DISABLED by default for maximum trading flexibility
    vwap_hold_threshold: float = 0.15     # % distance from VWAP to consider "holding"
    vwap_fail_threshold: float = 0.25     # % break below VWAP to consider "failed"
    vwap_chop_threshold: float = 0.10     # % range around VWAP considered "chop"
    # ExitBot delegation settings
    delegate_exits_to_exitbot: bool = True  # If True, delegate all exit decisions to ExitBot v2


# =============================================================================
# CRYPTO BOT CLASS - Main trading logic
# =============================================================================

class CryptoBot:
    """
    Bitcoin and Ethereum trading bot with momentum strategies.
    
    This bot:
    1. Monitors price movements for BTC/USD and ETH/USD
    2. Detects momentum using simple moving average crossovers
    3. Enters positions when momentum is confirmed
    4. Manages positions with stop-loss, take-profit, and trailing stops
    5. Exits positions based on time limits (no session restrictions for crypto)
    
    Usage:
        bot = CryptoBot("crypto_core")
        result = bot.execute(max_daily_loss=100.0)
    """
    
    def __init__(self, bot_id: str = "crypto_core"):
        """
        Initialize the crypto trading bot.
        
        Args:
            bot_id: Unique identifier for this bot instance (default: "crypto_core")
        """
        # Store bot identifier
        self.bot_id = bot_id
        
        # Initialize services - logger for audit trail, Alpaca for trading
        self._logger = get_logger()
        self._alpaca = get_alpaca_client()
        
        # Load configuration from bots.yaml
        self._config = self._load_config()
        
        # Set pairs from config or use dynamic universe
        self.pairs = self._get_trading_pairs()
        
        # Cache for last known good quotes (used when fresh data unavailable)
        self._quote_cache: Dict[str, Dict[str, Any]] = {}
        
        # Track API call failures for health monitoring
        self._api_failures: List[float] = []
        
        # Track last trade time per symbol to prevent rapid duplicate orders
        self._last_trade_time: Dict[str, float] = {}
        
        # Anti-churn tracking: consecutive stop-outs and stopout times per symbol
        self._consecutive_stopouts: Dict[str, int] = {}     # symbol -> count
        self._last_stopout_time: Dict[str, float] = {}      # symbol -> timestamp
        self._whipsaw_pause_until: Dict[str, float] = {}    # symbol -> pause end timestamp
        
        # Initialize ML signal service for trade scoring
        self._ml_service = MLSignalService(logger=self._logger)
        settings = load_settings()
        ml_config = settings.get("ml", {})
        self._ml_enabled = ml_config.get("enabled", False)
        # Use crypto-specific threshold if available, otherwise fall back to global
        self._ml_min_probability = ml_config.get("crypto_threshold",
                                                  ml_config.get("min_probability", 0.55))
        
        # Initialize institutional risk components
        self._position_sizer = get_position_sizer()
        self._correlation_manager = get_correlation_manager()
        self._feature_extractor = get_feature_extractor()
        
        # Initialize Turtle Traders engines for systematic trend-following
        # One engine per pair since TurtleTrend is symbol-specific
        self._turtle_engines: Dict[str, TurtleTrend] = {}
        if self._config and self._config.signal_mode == "turtle":
            # Convert system string to enum
            system_enum = TurtleSystem.SYSTEM_2 if self._config.turtle_system == "system_2" else TurtleSystem.SYSTEM_1
            turtle_config = TurtleConfig(
                system=system_enum,
                entry_lookback=self._config.turtle_entry_lookback,
                exit_lookback=self._config.turtle_exit_lookback,
                atr_period=self._config.turtle_atr_period,
                risk_pct_per_unit=self._config.turtle_risk_pct_per_unit,
                stop_loss_atr_mult=self._config.turtle_stop_loss_atr_mult,
                pyramid_enabled=self._config.turtle_pyramid_enabled,
                pyramid_trigger_atr=self._config.turtle_pyramid_trigger_atr,
                max_units=self._config.turtle_max_units,
                winner_filter_enabled=self._config.turtle_winner_filter,
                asset_class="crypto",
                bar_timeframe="1Hour"
            )
            for pair in self.pairs:
                self._turtle_engines[pair] = TurtleTrend(symbol=pair, config=turtle_config)
        
        # Log initialization for debugging
        self._logger.log("crypto_bot_init", {
            "bot_id": bot_id,
            "pairs": self.pairs,
            "config_loaded": self._config is not None,
            "ml_enabled": self._ml_enabled,
            "ml_available": self._ml_service.is_available,
            "institutional_sizing": True,
            "correlation_management": True,
            "ml_features_enabled": True,
            "signal_mode": self._config.signal_mode if self._config else "momentum",
            "turtle_enabled": len(self._turtle_engines) > 0
        })
    
    def _get_trading_pairs(self) -> List[str]:
        """
        Get trading pairs either from dynamic universe or static config.
        
        If universe.enabled is true in config, uses the crypto universe service
        to dynamically select top coins based on volume, volatility, and momentum.
        Otherwise uses the static pairs list from config.
        
        Returns:
            List of crypto pair symbols (e.g., ["BTC/USD", "ETH/USD"])
        """
        try:
            bots_config = load_bots_config()
            crypto_config = bots_config.get("cryptobot", {})
            universe_config = crypto_config.get("universe", {})
            
            self._logger.log("crypto_universe_config_check", {
                "universe_enabled": universe_config.get("enabled", False),
                "max_coins": universe_config.get("max_coins", 5)
            })
            
            if universe_config.get("enabled", False):
                universe = get_crypto_universe()
                max_coins = universe_config.get("max_coins", 5)
                dynamic_pairs = universe.get_top_coins(n=max_coins)
                
                if dynamic_pairs:
                    self._logger.log("crypto_universe_selected", {
                        "pairs": dynamic_pairs,
                        "count": len(dynamic_pairs)
                    })
                    return dynamic_pairs
                else:
                    self._logger.log("crypto_universe_fallback", {
                        "reason": "No coins passed screening"
                    })
            
            return self._config.pairs if self._config else ["BTC/USD", "ETH/USD"]
            
        except Exception as e:
            self._logger.error(f"Failed to get trading pairs: {e}")
            return self._config.pairs if self._config else ["BTC/USD", "ETH/USD"]
    
    # =========================================================================
    # PRODUCTION: TECHNICAL INDICATORS - RSI and MACD calculations
    # =========================================================================
    
    def _calculate_rsi(self, prices: List[float], period: int = RSI_PERIOD) -> float:
        """
        Calculate Relative Strength Index (RSI) from price history.
        
        RSI measures momentum and identifies overbought/oversold conditions.
        - RSI < 30: Oversold (potential buy signal)
        - RSI > 70: Overbought (potential sell signal)
        
        Args:
            prices: List of closing prices (oldest to newest)
            period: RSI period (default 14)
            
        Returns:
            RSI value between 0 and 100
        """
        if len(prices) < period + 1:
            return 50.0  # Neutral if insufficient data
        
        # Calculate price changes
        deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        
        # Separate gains and losses
        gains = [max(0, d) for d in deltas]
        losses = [max(0, -d) for d in deltas]
        
        # Use only the last 'period' values
        recent_gains = gains[-period:]
        recent_losses = losses[-period:]
        
        # Calculate average gain and loss
        avg_gain = sum(recent_gains) / period
        avg_loss = sum(recent_losses) / period
        
        # Avoid division by zero
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        
        # Calculate RS and RSI
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def _calculate_ema(self, prices: List[float], period: int) -> float:
        """
        Calculate Exponential Moving Average.
        
        EMA gives more weight to recent prices, making it more responsive
        to new information than a simple moving average.
        
        Args:
            prices: List of prices (oldest to newest)
            period: EMA period
            
        Returns:
            EMA value
        """
        if len(prices) < period:
            return prices[-1] if prices else 0.0
        
        # Calculate smoothing multiplier
        multiplier = 2 / (period + 1)
        
        # Start with SMA for initial EMA
        ema = sum(prices[:period]) / period
        
        # Apply EMA formula for remaining prices
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema
        
        return ema
    
    def _calculate_macd(self, prices: List[float]) -> Dict[str, float]:
        """
        Calculate MACD (Moving Average Convergence Divergence).
        
        MACD shows the relationship between two moving averages:
        - MACD Line: Fast EMA - Slow EMA
        - Signal Line: EMA of MACD Line
        - Histogram: MACD Line - Signal Line
        
        Buy signal: MACD crosses above signal line
        Sell signal: MACD crosses below signal line
        
        Args:
            prices: List of closing prices (oldest to newest)
            
        Returns:
            Dictionary with macd, signal, and histogram values
        """
        result = {"macd": 0.0, "signal": 0.0, "histogram": 0.0}
        
        if len(prices) < MACD_SLOW + MACD_SIGNAL:
            return result  # Insufficient data
        
        # Calculate fast and slow EMAs
        ema_fast = self._calculate_ema(prices, MACD_FAST)
        ema_slow = self._calculate_ema(prices, MACD_SLOW)
        
        # MACD line
        macd_line = ema_fast - ema_slow
        
        # For signal line, we need MACD history
        # Calculate MACD values for the last MACD_SIGNAL periods
        macd_history = []
        for i in range(MACD_SIGNAL):
            idx = len(prices) - MACD_SIGNAL + i + 1
            if idx > MACD_SLOW:
                fast = self._calculate_ema(prices[:idx], MACD_FAST)
                slow = self._calculate_ema(prices[:idx], MACD_SLOW)
                macd_history.append(fast - slow)
        
        # Signal line is EMA of MACD values
        if macd_history:
            signal_line = self._calculate_ema(macd_history, MACD_SIGNAL)
        else:
            signal_line = macd_line
        
        # Histogram
        histogram = macd_line - signal_line
        
        result["macd"] = macd_line
        result["signal"] = signal_line
        result["histogram"] = histogram
        
        return result
    
    # =========================================================================
    # PRODUCTION: QUOTE VALIDATION - Ensure data freshness
    # =========================================================================
    
    def _get_validated_quote(self, pair: str) -> Tuple[Optional[Dict[str, Any]], bool]:
        """
        Get a quote with freshness validation and caching.
        
        Production-level: Validates quote timestamp, rejects stale data,
        and maintains a cache for fallback when fresh data unavailable.
        
        Args:
            pair: Crypto pair (e.g., "BTC/USD")
            
        Returns:
            Tuple of (quote_dict, is_fresh_data)
            - quote_dict: Quote with bid, ask, timestamp
            - is_fresh_data: True if fresh from API, False if from cache
        """
        quote = None
        is_fresh = False
        
        # Try to get fresh quote with retry logic
        for attempt in range(MAX_RETRIES):
            try:
                raw_quote = self._alpaca.get_latest_quote(pair, asset_class="crypto")
                
                # Validate quote structure
                if not raw_quote or "bid" not in raw_quote or "ask" not in raw_quote:
                    raise ValueError("Invalid quote structure")
                
                # Check quote freshness if timestamp available
                quote_time = raw_quote.get("timestamp")
                age_seconds = 0
                
                if quote_time:
                    # Parse timestamp and check age
                    if isinstance(quote_time, str):
                        try:
                            quote_dt = datetime.fromisoformat(quote_time.replace("Z", "+00:00"))
                            age_seconds = (datetime.now(quote_dt.tzinfo) - quote_dt).total_seconds()
                        except (ValueError, TypeError):
                            age_seconds = 0  # Can't parse, assume fresh
                    
                    if age_seconds > MAX_QUOTE_AGE_SECONDS:
                        self._logger.log("crypto_stale_quote_rejected", {
                            "pair": pair,
                            "age_seconds": age_seconds,
                            "threshold": MAX_QUOTE_AGE_SECONDS,
                            "action": "retry_or_reject"
                        })
                        # PRODUCTION: Reject stale quotes - retry instead
                        # This will trigger the retry loop to try again
                        raise ValueError(f"Quote is stale: {age_seconds:.1f}s > {MAX_QUOTE_AGE_SECONDS}s")
                    else:
                        is_fresh = True
                else:
                    is_fresh = True  # No timestamp, assume fresh
                
                # Validate bid/ask sanity
                bid = float(raw_quote.get("bid", 0))
                ask = float(raw_quote.get("ask", 0))
                
                if bid <= 0 or ask <= 0 or bid > ask:
                    raise ValueError(f"Invalid bid/ask: bid={bid}, ask={ask}")
                
                quote = {
                    "bid": bid,
                    "ask": ask,
                    "mid": (bid + ask) / 2,
                    "spread": ask - bid,
                    "spread_pct": ((ask - bid) / ((bid + ask) / 2)) * 100,
                    "timestamp": datetime.utcnow().isoformat(),
                    "data_source": "fresh_api" if is_fresh else "api_stale"
                }
                
                # Update cache with fresh data
                self._quote_cache[pair] = quote
                
                # Clear API failure tracking on success
                self._api_failures = [t for t in self._api_failures 
                                     if time.time() - t < 300]  # Keep last 5 min
                
                self._logger.log("crypto_quote_fetched", {
                    "pair": pair,
                    "mid_price": quote["mid"],
                    "spread_pct": round(quote["spread_pct"], 4),
                    "data_source": quote["data_source"],
                    "attempt": attempt + 1
                })
                
                return quote, is_fresh
                
            except Exception as e:
                # Track failure
                self._api_failures.append(time.time())
                
                self._logger.warn(f"Quote fetch attempt {attempt + 1} failed for {pair}: {e}")
                
                # Exponential backoff before retry
                if attempt < MAX_RETRIES - 1:
                    sleep_time = RETRY_BACKOFF_BASE ** attempt
                    time.sleep(sleep_time)
        
        # All retries failed - try cache BUT only if cache is recent
        if pair in self._quote_cache:
            cached = self._quote_cache[pair]
            
            # PRODUCTION: Validate cache age - reject if too old
            cache_timestamp = cached.get("timestamp")
            cache_age_seconds = MAX_QUOTE_AGE_SECONDS + 1  # Default to expired
            
            if cache_timestamp:
                try:
                    cache_dt = datetime.fromisoformat(cache_timestamp)
                    cache_age_seconds = (datetime.utcnow() - cache_dt).total_seconds()
                except (ValueError, TypeError):
                    pass  # Can't parse, treat as expired
            
            # Only use cache if within freshness threshold
            if cache_age_seconds <= MAX_QUOTE_AGE_SECONDS:
                cached["data_source"] = "cache_fallback_fresh"
                
                self._logger.log("crypto_using_cached_quote", {
                    "pair": pair,
                    "cached_price": cached["mid"],
                    "cache_age_seconds": cache_age_seconds,
                    "reason": "api_failures_exhausted_using_fresh_cache"
                })
                
                return cached, False  # False because it's not live fresh
            else:
                # Cache is stale - REJECT it for trading safety
                self._logger.log("crypto_cache_too_old", {
                    "pair": pair,
                    "cache_age_seconds": cache_age_seconds,
                    "threshold": MAX_QUOTE_AGE_SECONDS,
                    "action": "reject_stale_cache"
                })
                # Fall through to return None
        
        # No cache available OR cache is stale
        self._logger.error(f"No fresh quote available for {pair} - API failed and cache stale/missing")
        return None, False
    
    # =========================================================================
    # PRODUCTION: PRE-TRADE RISK CHECKS - Validate before trading
    # =========================================================================
    
    def _pre_trade_risk_check(self, pair: str, notional: float, 
                               max_daily_loss: float) -> Dict[str, Any]:
        """
        Perform comprehensive pre-trade risk validation.
        
        Production-level: Checks account equity, available cash,
        daily loss budget, and current exposure before allowing trade.
        
        Args:
            pair: Crypto pair to trade
            notional: Dollar amount of proposed trade
            max_daily_loss: Maximum allowed daily loss
            
        Returns:
            Dictionary with:
            - approved: Boolean if trade can proceed
            - reason: Explanation if denied
            - available_cash: Current available buying power
            - daily_pnl: Today's P&L so far
        """
        result = {
            "approved": False,
            "reason": "",
            "available_cash": 0.0,
            "daily_pnl": 0.0,
            "equity": 0.0
        }
        
        try:
            # STEP 1: Get account data
            account = self._alpaca.get_account()
            result["equity"] = float(account.equity)
            result["available_cash"] = float(account.buying_power)
            
            # STEP 2: Check if we have enough buying power
            if result["available_cash"] < notional:
                result["reason"] = f"Insufficient buying power: ${result['available_cash']:.2f} < ${notional:.2f}"
                return result
            
            # STEP 3: Check daily P&L budget
            # Calculate today's realized + unrealized P&L
            today_start_equity = get_state(f"equity.day_start.{self.bot_id}", result["equity"])
            result["daily_pnl"] = result["equity"] - today_start_equity
            
            # Check if we've already hit max loss
            if result["daily_pnl"] <= -max_daily_loss:
                result["reason"] = f"Daily loss limit reached: ${abs(result['daily_pnl']):.2f} >= ${max_daily_loss:.2f}"
                return result
            
            # STEP 4: Check remaining risk budget
            remaining_budget = max_daily_loss + result["daily_pnl"]
            if notional > remaining_budget * 2:  # Trade shouldn't risk more than 2x remaining budget
                result["reason"] = f"Trade size ${notional:.2f} exceeds risk budget ${remaining_budget:.2f}"
                return result
            
            # STEP 5: Check API health (too many recent failures)
            recent_failures = len([t for t in self._api_failures 
                                  if time.time() - t < 300])
            if recent_failures >= 5:
                result["reason"] = f"API health degraded: {recent_failures} failures in last 5 minutes"
                return result
            
            # All checks passed
            result["approved"] = True
            result["reason"] = "All pre-trade checks passed"
            
            self._logger.log("crypto_pretrade_check", {
                "pair": pair,
                "notional": notional,
                "approved": True,
                "equity": result["equity"],
                "daily_pnl": result["daily_pnl"]
            })
            
        except Exception as e:
            result["reason"] = f"Pre-trade check failed: {e}"
            self._logger.error(result["reason"])
        
        return result
    
    def _load_config(self) -> Optional[CryptoConfig]:
        """
        Load bot configuration from config/bots.yaml.
        
        Returns:
            CryptoConfig object with all settings, or None if not found
        """
        try:
            # Load the full bots configuration file
            bots_config = load_bots_config()
            
            # Get cryptobot section
            crypto_config = bots_config.get("cryptobot", {})
            
            # Return None if cryptobot not configured
            if not crypto_config:
                self._logger.warn("No cryptobot config found in bots.yaml")
                return None
            
            # Check if dynamic settings are enabled
            dynamic_config = crypto_config.get("dynamic_settings", {})
            use_dynamic = dynamic_config.get("enabled", False)
            target_daily = dynamic_config.get("target_daily_profit", 50.0)
            risk_tolerance = dynamic_config.get("risk_tolerance", "aggressive")
            
            # Extract execution settings
            execution = crypto_config.get("execution", {})
            
            # Apply dynamic settings if enabled
            if use_dynamic:
                try:
                    account = self._alpaca.get_account()
                    equity = float(account.equity)
                    dynamic = calculate_dynamic_settings(
                        account_equity=equity,
                        target_daily_profit=target_daily,
                        risk_tolerance=risk_tolerance
                    )
                    execution["equity_pct"] = dynamic.equity_pct
                    execution["max_notional_usd"] = dynamic.max_notional_usd
                    risk = crypto_config.get("risk", {})
                    risk["max_concurrent_positions"] = dynamic.max_concurrent_positions
                    # PRESERVE configured max_trades_per_day - do NOT override with dynamic value
                    # Config explicitly sets 200, dynamic settings caps at 10 which is too restrictive
                    # Only use dynamic value if config doesn't specify a higher limit
                    config_max_trades = risk.get("max_trades_per_day", 10)
                    if config_max_trades > dynamic.max_trades_per_day:
                        self._logger.log("max_trades_preserved", {
                            "config_value": config_max_trades,
                            "dynamic_value": dynamic.max_trades_per_day,
                            "using": config_max_trades
                        })
                    else:
                        risk["max_trades_per_day"] = dynamic.max_trades_per_day
                    crypto_config["risk"] = risk
                    universe = crypto_config.get("universe", {})
                    universe["ml_rerank_select"] = dynamic.ml_rerank_select
                    crypto_config["universe"] = universe
                    exits = crypto_config.get("exits", {})
                    exits["stop_loss_pct"] = dynamic.stop_loss_pct
                    exits["take_profit_pct"] = dynamic.take_profit_pct
                    crypto_config["exits"] = exits
                    self._logger.log("dynamic_settings_applied", {
                        "account_equity": equity,
                        "tier": dynamic.tier_name,
                        "equity_pct": dynamic.equity_pct,
                        "max_positions": dynamic.max_concurrent_positions,
                        "expected_daily": dynamic.expected_daily_profit,
                        "expected_weekly": dynamic.expected_weekly_profit
                    })
                except Exception as e:
                    self._logger.error(f"Failed to apply dynamic settings: {e}")
            
            # Extract risk management settings
            risk = crypto_config.get("risk", {})
            trailing = risk.get("trailing_stop", {})
            
            # Extract exit condition settings
            exits = crypto_config.get("exits", {})
            
            # Extract signal mode settings
            signal = crypto_config.get("signal", {})
            signal_mode = signal.get("mode", "momentum")
            
            # Extract Turtle strategy settings (24/7 hourly adaptation)
            turtle = crypto_config.get("turtle", {})
            
            # Extract anti-churn protection settings
            anti_churn = crypto_config.get("anti_churn", {})
            
            # Extract delegation settings (whether to delegate exits to ExitBot)
            delegation = crypto_config.get("delegation", {})
            
            # Extract VWAP posture settings - defaults to DISABLED for trading flexibility
            vwap_posture = crypto_config.get("vwap_posture", {})
            
            # Build and return the configuration object
            # All trailing stop fields have defaults in CryptoConfig dataclass
            return CryptoConfig(
                bot_id=crypto_config.get("bot_id", self.bot_id),
                enabled=crypto_config.get("enabled", False),
                pairs=crypto_config.get("pairs", ["BTC/USD", "ETH/USD"]),
                max_trades_per_day=risk.get("max_trades_per_day", 200),
                max_concurrent_positions=risk.get("max_concurrent_positions", 3),
                min_order_size=risk.get("min_order_size", 15.0),
                default_notional=execution.get("default_notional_usd", 50.0),
                stop_loss_pct=exits.get("stop_loss_pct", 0.75),
                take_profit_pct=exits.get("take_profit_pct", 1.50),
                time_stop_minutes=exits.get("time_stop_minutes", 240),
                trailing_stop_enabled=trailing.get("enabled", False),
                trailing_stop_mode=trailing.get("mode", "percent"),
                trailing_stop_value=trailing.get("value", 1.5),
                trailing_activation_pct=trailing.get("activation_profit_pct", 0.4),
                trailing_update_only_if_improves=trailing.get("update_only_if_improves", True),
                trailing_epsilon_pct=trailing.get("epsilon_pct", 0.05),
                trailing_exit_order_type=trailing.get("exit_order", {}).get("type", "market"),
                # Turtle strategy settings from YAML
                signal_mode=signal_mode,
                turtle_system=turtle.get("system", "system_1"),
                turtle_entry_lookback=turtle.get("entry_lookback", 480),
                turtle_exit_lookback=turtle.get("exit_lookback", 240),
                turtle_atr_period=turtle.get("atr_period", 480),
                turtle_risk_pct_per_unit=turtle.get("risk_pct_per_unit", 1.0),
                turtle_stop_loss_atr_mult=turtle.get("stop_loss_atr_mult", 2.0),
                turtle_pyramid_enabled=turtle.get("pyramid_enabled", True),
                turtle_pyramid_trigger_atr=turtle.get("pyramid_trigger_atr", 0.5),
                turtle_max_units=turtle.get("max_units", 4),
                turtle_winner_filter=turtle.get("winner_filter_enabled", True),
                # Anti-churn settings
                anti_churn_enabled=anti_churn.get("enabled", True),
                min_hold_minutes=anti_churn.get("min_hold_minutes", DEFAULT_MIN_HOLD_MINUTES),
                stopout_cooldown_minutes=anti_churn.get("stop_out_cooldown_minutes", DEFAULT_STOPOUT_COOLDOWN_MINUTES),
                max_consecutive_stopouts=anti_churn.get("max_consecutive_stopouts", DEFAULT_MAX_CONSECUTIVE_STOPOUTS),
                whipsaw_pause_minutes=anti_churn.get("whipsaw_pause_minutes", DEFAULT_WHIPSAW_PAUSE_MINUTES),
                hard_stop_loss_pct=exits.get("hard_stop_loss_pct", 2.0),
                require_rsi_confirmation=signal.get("require_rsi_confirmation", True),
                require_macd_confirmation=signal.get("require_macd_confirmation", True),
                delegate_exits_to_exitbot=crypto_config.get("delegate_exits_to_exitbot",
                    delegation.get("delegate_exits_to_exitbot", exits.get("delegate_to_exitbot", True))),
                # Production risk settings - load from universe config with relaxed defaults
                max_spread_pct=crypto_config.get("universe", {}).get("max_spread_pct", 
                               risk.get("max_spread_pct", MAX_SPREAD_PCT)),
                # VWAP posture settings - DISABLED by default for maximum trading flexibility
                vwap_enabled=vwap_posture.get("enabled", False),  # Default OFF - don't block trades
                vwap_hold_threshold=vwap_posture.get("hold_threshold", 0.15),
                vwap_fail_threshold=vwap_posture.get("fail_threshold", 0.25),
                vwap_chop_threshold=vwap_posture.get("chop_threshold", 0.10)
            )
            
        except Exception as e:
            # Log error but don't crash - bot will use defaults
            self._logger.error(f"Failed to load crypto config: {e}")
            return None
    
    def _is_quiet_hours(self) -> bool:
        """
        Check if we're in quiet hours (low liquidity period).
        
        During quiet hours (default 00:00-04:00 PST), we:
        - Still manage existing positions (trailing stops, exits)
        - Skip new trade entries to avoid flash wick stops
        
        Returns:
            True if in quiet hours (skip new trades), False otherwise
        """
        from ..core.clock import get_market_clock
        from ..core.config import load_bots_config
        
        try:
            clock = get_market_clock()
            now = clock.now()
            current_time = now.strftime("%H:%M")
            
            bots_config = load_bots_config()
            session = bots_config.get("cryptobot", {}).get("session", {})
            
            quiet_enabled = session.get("quiet_hours_enabled", False)
            if not quiet_enabled:
                return False
            
            quiet_start = session.get("quiet_hours_start", "00:00")
            quiet_end = session.get("quiet_hours_end", "04:00")
            
            is_quiet = quiet_start <= current_time < quiet_end
            
            if is_quiet:
                self._logger.log("crypto_quiet_hours_active", {
                    "bot_id": self.bot_id,
                    "time": current_time,
                    "quiet_start": quiet_start,
                    "quiet_end": quiet_end,
                    "action": "manage_positions_only"
                })
            
            return is_quiet
            
        except Exception as e:
            self._logger.warn(f"Quiet hours check failed: {e}")
            return False
    
    def execute(self, max_daily_loss: float) -> Dict[str, Any]:
        """
        Execute one iteration of the crypto trading strategy.
        
        This is the main entry point called by the orchestrator every loop.
        It manages existing positions first, then looks for new entry opportunities.
        
        Args:
            max_daily_loss: Maximum dollar amount this bot can lose today
                           (allocated by PortfolioBot based on account equity)
        
        Returns:
            Dictionary containing execution results:
            - trades_attempted: Number of new trades attempted
            - positions_managed: Number of existing positions checked
            - signals: Dictionary of signals generated for each pair
            - errors: List of any errors encountered
        """
        # Refresh trading pairs from universe (if enabled)
        self.pairs = self._get_trading_pairs()
        
        # Initialize results dictionary to track execution outcomes
        results = {
            "trades_attempted": 0,
            "positions_managed": 0,
            "signals": {},
            "errors": []
        }
        
        try:
            # =========================================================
            # REGIME CHECK: Crypto is highly correlated with risk sentiment
            # Strong dollar or risk-off sentiment = reduce crypto exposure
            # =========================================================
            regime_multiplier = 1.0
            halt_new_entries = False
            tighten_stops = False
            regime = None
            
            try:
                regime = get_current_regime()
                regime_multiplier = regime.position_size_multiplier
                halt_new_entries = regime.halt_new_entries
                tighten_stops = regime.tighten_stops
                
                # Crypto gets extra penalty in risk-off (it amplifies equity moves)
                if regime.sentiment == MarketSentiment.RISK_OFF:
                    regime_multiplier *= 0.5  # Extra 50% reduction
                    self._logger.log("crypto_risk_off_penalty", {
                        "original_multiplier": regime.position_size_multiplier,
                        "adjusted_multiplier": regime_multiplier
                    })
                
                self._logger.log("crypto_regime_check", {
                    "bot_id": self.bot_id,
                    "vix": regime.vix,
                    "sentiment": regime.sentiment.value,
                    "regime_multiplier": regime_multiplier,
                    "halt_new_entries": halt_new_entries,
                    "dollar_environment": regime.dollar_environment.value
                })
            except Exception as regime_err:
                self._logger.error(f"Regime check failed: {regime_err}")
            
            # Apply regime multiplier to max_daily_loss for position sizing
            adjusted_max_loss = max_daily_loss * regime_multiplier
            
            # STEP 1: Get all current positions from Alpaca
            positions = self._alpaca.get_positions()
            
            # STEP 2: Filter to only crypto positions
            # Crypto symbols end with "USD" (e.g., "BTCUSD", "ETHUSD", "LINKUSD")
            # IMPORTANT: Must detect ALL crypto positions, not just those in current pairs
            # to prevent duplicate orders when dynamic universe changes
            crypto_positions = []
            all_crypto_symbols = set()  # Track all held crypto symbols for duplicate prevention
            for p in positions:
                symbol = str(p.symbol)
                # Crypto positions have symbols like BTCUSD, ETHUSD, LINKUSD
                # They end with USD and don't have "/" in the stored symbol
                if symbol.endswith("USD") and "/" not in symbol:
                    crypto_positions.append(p)
                    all_crypto_symbols.add(symbol)  # e.g., "BTCUSD"
            
            # STEP 3: Manage existing positions (check for exit conditions)
            for position in crypto_positions:
                try:
                    # Check stop-loss, take-profit, trailing stop, time stop
                    self._manage_position(position)
                    results["positions_managed"] += 1
                except Exception as e:
                    # Log error but continue to next position
                    error_msg = f"Position management {position.symbol}: {e}"
                    results["errors"].append(error_msg)
                    self._logger.error(error_msg)
            
            # STEP 4: Check if we should look for new entries
            # Only if we have room for more positions
            max_positions = self._config.max_concurrent_positions if self._config else 3
            
            # Get open orders to prevent duplicate order placement
            # Orders can be pending for several seconds before filling
            open_orders = self._alpaca.get_open_orders()
            open_order_symbols = {o.get("symbol", "").replace("/", "") for o in open_orders}
            
            if len(crypto_positions) < max_positions:
                # Check kill switch - block ALL new entries when global freeze is active
                killswitch = get_killswitch_service()
                ks_allowed, ks_reason = killswitch.is_entry_allowed("crypto")
                if not ks_allowed:
                    self._logger.log("crypto_killswitch_blocked", {
                        "bot_id": self.bot_id,
                        "reason": ks_reason
                    })
                # Check if we're in quiet hours (low liquidity period 00:00-04:00 PST)
                # During quiet hours, skip new entries but still manage positions
                elif self._is_quiet_hours():
                    self._logger.log("crypto_skip_quiet_hours", {
                        "bot_id": self.bot_id,
                        "reason": "low_liquidity_period",
                        "action": "manage_positions_only"
                    })
                # Check if regime halts new entries (extreme fear or strong dollar)
                elif halt_new_entries:
                    self._logger.log("crypto_halt_regime", {
                        "bot_id": self.bot_id,
                        "reason": "extreme_volatility_regime"
                    })
                # Check if we've exceeded daily trade limit
                elif not self._can_trade_today():
                    self._logger.log("crypto_daily_limit_reached", {
                        "bot_id": self.bot_id
                    })
                else:
                    # Look for entry opportunities in each pair
                    for pair in self.pairs:
                        # Limit to 1 new trade per execution cycle
                        if results["trades_attempted"] >= 1:
                            break
                        
                        try:
                            # Skip if we already have a position in this pair
                            # Use all_crypto_symbols set for O(1) lookup
                            pair_clean = pair.replace("/", "")
                            has_position = pair_clean in all_crypto_symbols
                            
                            if has_position:
                                self._logger.log("crypto_skip_has_position", {
                                    "bot_id": self.bot_id,
                                    "pair": pair,
                                    "pair_clean": pair_clean,
                                    "all_crypto_symbols": list(all_crypto_symbols)
                                })
                                continue
                            
                            # Skip if we already have a pending order for this pair
                            # This prevents duplicate orders when fills are delayed
                            if pair_clean in open_order_symbols:
                                self._logger.log("crypto_skip_pending_order", {
                                    "bot_id": self.bot_id,
                                    "pair": pair,
                                    "reason": "pending_order_exists"
                                })
                                continue
                            
                            # Skip if we traded this pair recently (cooldown period)
                            # Uses extended cooldown after stop-outs to prevent revenge trading
                            current_time = time.time()
                            last_trade = self._last_trade_time.get(pair_clean, 0)
                            cooldown_seconds = self._get_stopout_cooldown(pair_clean)
                            time_since_trade = current_time - last_trade
                            if time_since_trade < cooldown_seconds:
                                self._logger.log("crypto_skip_cooldown", {
                                    "bot_id": self.bot_id,
                                    "pair": pair,
                                    "seconds_remaining": round(cooldown_seconds - time_since_trade, 1),
                                    "cooldown_seconds": cooldown_seconds,
                                    "reason": "trade_cooldown" if cooldown_seconds == TRADE_COOLDOWN_SECONDS else "stopout_cooldown"
                                })
                                continue
                            
                            # ANTI-CHURN: Skip if symbol is paused due to whipsaw detection
                            if self._is_whipsaw_paused(pair_clean):
                                continue
                            
                            # ANTI-CHURN: Skip if ExitBot recently exited this symbol
                            # Prevents buy-sell churn where ExitBot exits and CryptoBot immediately re-enters
                            exitbot_cooldown_minutes = 30
                            exitbot_recent = get_state("exitbot.recent_exits", [])
                            churn_blocked = False
                            for exit_rec in exitbot_recent:
                                exit_sym = exit_rec.get("symbol", "").replace("/", "")
                                if exit_sym == pair_clean:
                                    exit_ts = exit_rec.get("timestamp", "")
                                    try:
                                        from datetime import timezone
                                        exit_time = datetime.fromisoformat(exit_ts.replace("Z", "+00:00")) if exit_ts else None
                                        if exit_time:
                                            now_utc = datetime.now(timezone.utc)
                                            mins_since = (now_utc - exit_time).total_seconds() / 60
                                            if mins_since < exitbot_cooldown_minutes:
                                                self._logger.log("crypto_skip_exitbot_cooldown", {
                                                    "bot_id": self.bot_id,
                                                    "pair": pair,
                                                    "exit_reason": exit_rec.get("reason", "unknown"),
                                                    "minutes_since_exit": round(mins_since, 1),
                                                    "cooldown_minutes": exitbot_cooldown_minutes
                                                })
                                                churn_blocked = True
                                                break
                                    except Exception:
                                        pass
                            if churn_blocked:
                                continue
                            
                            # Generate signal using Turtle strategy or legacy momentum
                            try:
                                account = self._alpaca.get_account()
                                equity = float(account.equity)
                            except Exception:
                                equity = 100000.0
                            
                            if self._turtle_engines.get(pair) and self._config and self._config.signal_mode == "turtle":
                                signal = self._generate_turtle_signal(pair, equity)
                            else:
                                signal = self._generate_signal(pair)
                            results["signals"][pair] = signal
                            
                            # Execute trade if signal is actionable
                            # Turtle signals: "buy", "short", "pyramid"
                            # Legacy signals: "buy", "short"
                            if signal["action"] in ["buy", "short", "pyramid"]:
                                # HydraSensors regime gate - extra risk layer
                                # Reduces position size in risk-off regime (does NOT block trades)
                                # NOTE: Use LOCAL variable to avoid compounding across pairs
                                sensors_regime_multiplier = 1.0
                                trade_max_loss = adjusted_max_loss  # Local copy for this trade
                                try:
                                    if is_risk_off():
                                        sensors_regime = get_sensors_regime()
                                        sig_strength = signal.get("strength", signal.get("confidence", 0.5))
                                        
                                        self._logger.log("crypto_sensors_risk_off", {
                                            "pair": pair,
                                            "action": signal["action"],
                                            "regime_state": sensors_regime.state.value if sensors_regime else "unknown",
                                            "regime_confidence": sensors_regime.confidence if sensors_regime else 0,
                                            "signal_strength": sig_strength
                                        })
                                        
                                        # Apply 50% position size reduction in risk-off
                                        sensors_regime_multiplier = 0.5
                                        trade_max_loss = adjusted_max_loss * sensors_regime_multiplier
                                        
                                        # For Turtle signals, scale down the pre-computed qty
                                        # NOTE: Crypto uses fractional quantities - preserve precision
                                        if signal.get("qty"):
                                            original_qty = signal["qty"]
                                            reduced_qty = original_qty * sensors_regime_multiplier
                                            signal["qty"] = round(reduced_qty, 8)
                                            
                                            # Validate notional meets config minimum
                                            price = signal.get("price", signal.get("entry", 0))
                                            notional = signal["qty"] * price if price > 0 else 0
                                            min_notional = MIN_NOTIONAL_USD  # Use constant from module
                                            
                                            if notional > 0 and notional < min_notional:
                                                self._logger.log("crypto_sensors_skip_small", {
                                                    "pair": pair,
                                                    "original_qty": original_qty,
                                                    "reduced_qty": signal["qty"],
                                                    "notional": notional,
                                                    "min_notional": min_notional
                                                })
                                                continue
                                            
                                            self._logger.log("crypto_sensors_turtle_sized", {
                                                "pair": pair,
                                                "original_qty": original_qty,
                                                "reduced_qty": signal["qty"],
                                                "notional": notional,
                                                "multiplier": sensors_regime_multiplier
                                            })
                                        
                                        self._logger.log("crypto_sensors_size_reduced", {
                                            "pair": pair,
                                            "sensors_multiplier": sensors_regime_multiplier,
                                            "trade_max_loss": trade_max_loss
                                        })
                                except Exception as sensors_err:
                                    # Fail-open: sensors error doesn't block or reduce trading
                                    self._logger.error(f"Sensors regime check failed: {sensors_err}")
                                
                                # ML scoring gate - score the trade before executing
                                if self._ml_enabled:
                                    hour = get_market_clock().now().hour
                                    day_of_week = get_market_clock().now().weekday()
                                    
                                    # Extract technical features from signal indicators
                                    indicators = signal.get("indicators", {})
                                    
                                    # Compute cross-asset correlation features
                                    crypto_prices = {}
                                    for cp in self.pairs:
                                        cp_key = f"price_history.{cp.replace('/', '')}"
                                        cp_history = get_state(cp_key, [])
                                        if cp_history:
                                            crypto_prices[cp] = [p["price"] for p in cp_history]
                                    
                                    cross_features = self._feature_extractor.extract_cross_asset_features(
                                        crypto_prices, pair
                                    )
                                    
                                    ml_context = {
                                        "symbol": pair,
                                        "side": signal["action"],
                                        "signal_strength": signal.get("strength", 0.5),
                                        "hour": hour,
                                        "day_of_week": day_of_week,
                                        # Technical indicators from feature extractor
                                        "rsi": indicators.get("rsi", 50),
                                        "macd": indicators.get("macd", 0),
                                        "macd_signal": indicators.get("macd_signal", 0),
                                        "macd_histogram": indicators.get("macd_histogram", 0),
                                        "volume_ratio": indicators.get("volume_ratio", 1.0),
                                        "volume_zscore": indicators.get("volume_zscore", 0.0),
                                        "momentum_5": indicators.get("momentum_5", 0.0),
                                        "momentum_10": indicators.get("momentum_10", 0.0),
                                        "volatility": indicators.get("volatility_20", 0.0),
                                        "bb_position": indicators.get("bb_position", 0.5),
                                        "stochastic_k": indicators.get("stochastic_k", 50),
                                        "atr_14": indicators.get("atr_14", 0.0),
                                        # Market regime
                                        "vix": regime.vix if regime else 20,
                                        "volatility_regime": regime.volatility_regime.value if regime else "normal",
                                        # Cross-asset correlation from feature extractor
                                        "btc_correlation": cross_features.btc_correlation,
                                        "eth_correlation": cross_features.eth_correlation,
                                        "sector_momentum": cross_features.sector_momentum,
                                        "relative_strength": cross_features.relative_strength,
                                        # Account state
                                        "account_pnl_pct": get_state("daily.pnl_pct", 0.0),
                                        "drawdown_probability": get_state("ml.drawdown_probability", 0.0)
                                    }
                                    ml_score = self._ml_service.score_entry(ml_context)
                                    
                                    # Apply adaptive threshold based on market conditions
                                    vix = regime.vix if regime else 20
                                    adaptive_threshold = self._ml_service.get_adaptive_threshold(
                                        self._ml_min_probability, vix=vix, is_earnings_season=False
                                    )
                                    
                                    if ml_score["probability"] < adaptive_threshold:
                                        self._logger.log("crypto_ml_skip", {
                                            "pair": pair,
                                            "action": signal["action"],
                                            "ml_probability": ml_score["probability"],
                                            "threshold": adaptive_threshold,
                                            "base_threshold": self._ml_min_probability,
                                            "recommendation": ml_score["recommendation"]
                                        })
                                        continue
                                
                                # Atomically reserve trade slot BEFORE executing
                                # This prevents race conditions where multiple trades sneak through
                                if not self._reserve_trade_slot():
                                    self._logger.log("crypto_trade_slot_unavailable", {
                                        "pair": pair,
                                        "reason": "daily_limit_reached_atomically"
                                    })
                                    break
                                
                                # For Turtle signals, use qty-based execution
                                # Otherwise, use legacy dollar notional execution
                                if signal.get("qty"):
                                    trade_result = self._execute_trade_with_qty(pair, signal)
                                else:
                                    trade_result = self._execute_trade(
                                        pair, signal, trade_max_loss
                                    )
                                
                                if trade_result["success"]:
                                    results["trades_attempted"] += 1
                                    # Record cooldown timestamp to prevent rapid duplicate orders
                                    self._last_trade_time[pair_clean] = time.time()
                                else:
                                    results["errors"].append(
                                        f"{pair}: {trade_result['error']}"
                                    )
                                    
                        except Exception as e:
                            error_msg = f"Signal generation {pair}: {e}"
                            results["errors"].append(error_msg)
                            self._logger.error(error_msg)
            
            # Log completion for debugging and monitoring
            self._logger.log("crypto_bot_execution_complete", {
                "bot_id": self.bot_id,
                "results": results,
                "max_daily_loss": max_daily_loss
            })
            
        except Exception as e:
            # Catch-all for unexpected errors
            self._logger.error(f"Crypto bot execution failed: {e}")
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
    
    # =========================================================================
    # TRADING LIMIT ENFORCEMENT - Prevent over-trading
    # =========================================================================
    
    def _can_trade_today(self) -> bool:
        """
        Check if we've reached the daily trade limit.
        
        Reads the trade counter from state database and compares against
        the configured max_trades_per_day limit.
        
        Returns:
            True if we can still trade today, False if limit reached
        """
        max_trades = self._config.max_trades_per_day if self._config else 200
        today = get_market_clock().now().strftime("%Y-%m-%d")
        trade_count_key = f"trade_count.{self.bot_id}.{today}"
        current_count = get_state(trade_count_key, 0)
        return current_count < max_trades
    
    def _reserve_trade_slot(self) -> bool:
        """
        Atomically reserve a trade slot to prevent race conditions.
        
        Uses atomic_increment to check limit AND increment in one operation.
        Returns True if slot was reserved, False if at limit.
        """
        max_trades = self._config.max_trades_per_day if self._config else 200
        today = get_market_clock().now().strftime("%Y-%m-%d")
        trade_count_key = f"trade_count.{self.bot_id}.{today}"
        
        success, new_count = atomic_increment(trade_count_key, max_trades)
        
        if success:
            self._logger.log("crypto_trade_slot_reserved", {
                "bot_id": self.bot_id,
                "date": today,
                "new_count": new_count,
                "max_trades": max_trades
            })
        
        return success
    
    def _record_trade(self) -> None:
        """
        Legacy method - now a no-op since _reserve_trade_slot handles counting.
        Kept for compatibility with any code that calls it.
        """
        pass
    
    def _get_trades_today(self) -> int:
        """
        Get the number of trades made today by this bot.
        
        Returns:
            Integer count of trades made today
        """
        today = get_market_clock().now().strftime("%Y-%m-%d")
        trade_count_key = f"trade_count.{self.bot_id}.{today}"
        return get_state(trade_count_key, 0)
    
    # =========================================================================
    # SIGNAL GENERATION - Detect momentum and generate trading signals
    # =========================================================================
    
    def _generate_signal(self, pair: str) -> Dict[str, Any]:
        """
        Generate a trading signal for a crypto pair.
        
        PRODUCTION STRATEGY (SMA + RSI + MACD confirmation):
        - Track the last 50 prices in state database for indicator calculation
        - Calculate 5-period and 20-period simple moving averages
        - Calculate RSI (14-period) for momentum confirmation
        - Calculate MACD for trend confirmation
        - Buy when: SMA crossover UP + RSI not overbought + MACD bullish
        - Hold otherwise (we don't short crypto in this strategy)
        
        Args:
            pair: Crypto pair to analyze (e.g., "BTC/USD")
        
        Returns:
            Dictionary containing:
            - pair: Crypto pair
            - action: "buy" or "hold"
            - confidence: 0.0 to 1.0 confidence in the signal
            - price: Current price
            - indicators: Dictionary of all calculated indicators
            - data_source: Whether using fresh or cached data
        """
        # Initialize signal with default "hold" action
        signal = {
            "pair": pair,
            "action": "hold",
            "confidence": 0.0,
            "price": 0.0,
            "indicators": {},
            "data_source": "unknown"
        }
        
        try:
            # STEP 1: Get validated quote with freshness check
            quote, is_fresh = self._get_validated_quote(pair)
            
            if not quote:
                signal["error"] = "No quote available"
                return signal
            
            # Record data source for audit trail
            signal["data_source"] = quote.get("data_source", "unknown")
            
            # Calculate mid-price from bid/ask spread
            current_price = quote["mid"]
            signal["price"] = current_price
            signal["indicators"]["spread_pct"] = quote["spread_pct"]
            
            # STEP 2: Check spread - reject if too wide (indicates low liquidity)
            max_spread = self._config.max_spread_pct if self._config else MAX_SPREAD_PCT
            if quote["spread_pct"] > max_spread:
                self._logger.log("crypto_spread_too_wide", {
                    "pair": pair,
                    "spread_pct": quote["spread_pct"],
                    "max_allowed": max_spread
                })
                signal["indicators"]["spread_rejected"] = True
                return signal  # Hold - spread too wide
            
            # STEP 3: Load OHLCV history from state database
            price_history_key = f"price_history.{pair.replace('/', '')}"
            price_history = get_state(price_history_key, [])
            
            # STEP 4: Add current OHLCV data to history
            # For crypto quotes, use bid as low, ask as high, spread as volume proxy
            spread_pct = quote["spread_pct"]
            estimated_volume = max(100, 10000 / (spread_pct + 0.01))  # Higher volume when tighter spread
            
            price_history.append({
                "price": current_price,
                "high": quote["ask"],
                "low": quote["bid"],
                "volume": estimated_volume,
                "timestamp": datetime.utcnow().isoformat()
            })
            
            # STEP 5: Trim history to last 50 prices (need more for MACD)
            if len(price_history) > 50:
                price_history = price_history[-50:]
            
            # STEP 6: Save updated history back to state
            set_state(price_history_key, price_history)
            
            # Extract OHLCV data for indicator calculations
            prices = [p["price"] for p in price_history]
            highs = [p.get("high", p["price"]) for p in price_history]
            lows = [p.get("low", p["price"]) for p in price_history]
            volumes = [p.get("volume", 1000) for p in price_history]
            
            # STEP 6.5: Check for WhipsawTrader mean-reversion mode
            # If we're in whipsaw mode, use mean-reversion signals instead of momentum
            # IMPORTANT: When whipsaw mode is active, we MUST NOT use momentum signals
            try:
                whipsaw_trader = get_whipsaw_trader()
                pair_clean = pair.replace("/", "")
                
                if whipsaw_trader.is_whipsaw_mode(pair_clean):
                    # Calculate range from price history for mean-reversion
                    bars_for_range = [
                        {"high": h, "low": l, "close": p}
                        for h, l, p in zip(highs, lows, prices)
                    ]
                    whipsaw_trader.calculate_range(pair_clean, bars_for_range)
                    
                    # Calculate current ATR for exit condition checks
                    current_atr = 0.0
                    if len(prices) >= 14:
                        atr_sum = 0.0
                        for i in range(1, min(14, len(prices))):
                            tr = max(
                                highs[-(i)] - lows[-(i)],
                                abs(highs[-(i)] - prices[-(i+1)]),
                                abs(lows[-(i)] - prices[-(i+1)])
                            )
                            atr_sum += tr
                        current_atr = atr_sum / 14
                    
                    # Check if we should exit whipsaw mode first
                    should_exit, exit_reason = whipsaw_trader.should_exit_whipsaw_mode(
                        pair_clean, current_price, current_atr
                    )
                    
                    if should_exit:
                        self._logger.log("crypto_whipsaw_mode_exit", {
                            "pair": pair,
                            "reason": exit_reason,
                            "current_price": current_price,
                            "current_atr": round(current_atr, 4)
                        })
                        # Mode exited - fall through to momentum logic below
                    else:
                        # Still in whipsaw mode - get mean-reversion signal
                        mr_signal = whipsaw_trader.get_mean_reversion_signal(
                            symbol=pair_clean,
                            current_price=current_price,
                            current_atr=current_atr
                        )
                        
                        if mr_signal and mr_signal.get("action") == "buy":
                            signal["action"] = "buy"
                            signal["confidence"] = 0.7
                            signal["reason"] = mr_signal.get("reason", "whipsaw_mean_reversion")
                            signal["whipsaw_mode"] = True
                            signal["take_profit"] = mr_signal.get("take_profit")
                            signal["stop_loss"] = mr_signal.get("stop_loss")
                            signal["indicators"]["support"] = mr_signal.get("support")
                            signal["indicators"]["resistance"] = mr_signal.get("resistance")
                            signal["indicators"]["whipsaw_status"] = whipsaw_trader.get_status(pair_clean)
                            
                            self._logger.log("crypto_whipsaw_signal", {
                                "pair": pair,
                                "action": "buy",
                                "entry": current_price,
                                "support": mr_signal.get("support"),
                                "resistance": mr_signal.get("resistance"),
                                "take_profit_pct": (mr_signal.get("take_profit", 0) / current_price - 1) * 100 if current_price > 0 else 0
                            })
                            
                            return signal  # Use whipsaw signal instead of momentum
                        
                        # In whipsaw mode but no signal at support/resistance
                        # IMPORTANT: Return HOLD - do NOT fall through to momentum
                        signal["whipsaw_mode"] = True
                        signal["indicators"]["whipsaw_status"] = whipsaw_trader.get_status(pair_clean)
                        self._logger.log("crypto_whipsaw_hold", {
                            "pair": pair,
                            "reason": "not_at_range_boundary",
                            "current_price": current_price,
                            "support": whipsaw_trader.get_status(pair_clean).get("support"),
                            "resistance": whipsaw_trader.get_status(pair_clean).get("resistance")
                        })
                        return signal  # Return hold - skip momentum logic
            except Exception as e:
                self._logger.error(f"WhipsawTrader signal check failed: {e}")
            
            # STEP 7: Calculate SMA indicators
            sma5 = 0.0
            sma20 = 0.0
            
            if len(prices) >= 5:
                sma5 = sum(prices[-5:]) / 5
                signal["indicators"]["sma5"] = sma5
                signal["indicators"]["price_vs_sma5"] = ((current_price / sma5) - 1) * 100
            
            if len(prices) >= 20:
                sma20 = sum(prices[-20:]) / 20
                signal["indicators"]["sma20"] = sma20
                signal["indicators"]["sma5_vs_sma20"] = ((sma5 / sma20) - 1) * 100 if sma20 > 0 else 0
            
            # STEP 8: Use feature extractor for comprehensive technical analysis
            tech_features = self._feature_extractor.extract_features(
                prices=prices,
                volumes=volumes,
                highs=highs,
                lows=lows
            )
            
            # Core RSI and MACD
            rsi = tech_features.rsi
            signal["indicators"]["rsi"] = round(rsi, 2)
            signal["indicators"]["macd"] = round(tech_features.macd, 6)
            signal["indicators"]["macd_signal"] = round(tech_features.macd_signal, 6)
            signal["indicators"]["macd_histogram"] = round(tech_features.macd_histogram, 6)
            
            # Additional ML features
            signal["indicators"]["momentum_5"] = round(tech_features.momentum_5, 4)
            signal["indicators"]["momentum_10"] = round(tech_features.momentum_10, 4)
            signal["indicators"]["volatility_20"] = round(tech_features.volatility_20, 4)
            signal["indicators"]["bb_position"] = round(tech_features.bb_position, 4)
            signal["indicators"]["stochastic_k"] = round(tech_features.stochastic_k, 2)
            signal["indicators"]["stochastic_d"] = round(tech_features.stochastic_d, 2)
            signal["indicators"]["price_zscore"] = round(tech_features.price_zscore, 4)
            signal["indicators"]["volume_zscore"] = round(tech_features.volume_zscore, 4)
            signal["indicators"]["volume_ratio"] = round(tech_features.volume_ratio, 4)
            signal["indicators"]["atr_14"] = round(tech_features.atr_14, 4)
            
            # For backwards compatibility, create macd_data dict
            macd_data = {
                "macd": tech_features.macd,
                "signal": tech_features.macd_signal,
                "histogram": tech_features.macd_histogram
            }
            
            # STEP 9.5: VWAP POSTURE CHECK (INSTITUTIONAL AUTHORITY)
            # VWAP posture overrides other indicators - if NEUTRAL, no trades
            # FAIL-CLOSED: If VWAP is enabled but data unavailable, block trades
            vwap_enabled = self._config.vwap_enabled if self._config and hasattr(self._config, 'vwap_enabled') else True
            vwap_decision: Optional[PostureDecision] = None
            
            if vwap_enabled:
                # RELAXED: Reduced from 10 to 5 data points for faster startup
                if len(price_history) < 5:
                    self._logger.log("crypto_vwap_insufficient_data", {
                        "pair": pair,
                        "price_history_len": len(price_history),
                        "required": 5,
                        "action": "block_trade_fail_closed"
                    })
                    signal["indicators"]["vwap_error"] = "insufficient_data"
                    return signal  # Fail-closed: no VWAP data = no trade
            
            if vwap_enabled and len(price_history) >= 5:
                try:
                    # Convert price history to bars format for VWAP calculation
                    vwap_bars = [
                        {
                            "high": p.get("high", p["price"]),
                            "low": p.get("low", p["price"]),
                            "close": p["price"],
                            "volume": p.get("volume", 1000)
                        }
                        for p in price_history
                    ]
                    
                    # Get VWAP posture manager for this pair with config values
                    vwap_config = {
                        "hold_threshold": self._config.vwap_hold_threshold if self._config else 0.15,
                        "fail_threshold": self._config.vwap_fail_threshold if self._config else 0.25,
                        "chop_threshold": self._config.vwap_chop_threshold if self._config else 0.10,
                        "retest_proximity": 0.20
                    }
                    vwap_manager = get_vwap_posture_manager(pair, vwap_config)
                    vwap_decision = vwap_manager.evaluate(
                        bars=vwap_bars,
                        current_price=current_price,
                        intraday_bars=vwap_bars,
                        bar_index=len(price_history)
                    )
                    
                    # Add VWAP metrics to signal indicators
                    signal["indicators"]["vwap_posture"] = vwap_decision.posture.value
                    signal["indicators"]["vwap"] = vwap_decision.vwap_level.vwap if vwap_decision.vwap_level else 0
                    signal["indicators"]["vwap_distance_pct"] = round(vwap_decision.distance_from_vwap_pct, 3)
                    signal["indicators"]["vwap_sigma"] = round(vwap_decision.sigma_position, 2)
                    signal["indicators"]["is_vwap_retest"] = vwap_decision.is_vwap_retest
                    signal["indicators"]["vwap_allow_long"] = vwap_decision.allow_long
                    signal["indicators"]["vwap_allow_short"] = vwap_decision.allow_short
                    
                    if vwap_decision.gap_context:
                        signal["indicators"]["gap_state"] = vwap_decision.gap_context.state.value
                        signal["indicators"]["gap_fill_status"] = vwap_decision.gap_context.fill_status.value
                    
                    # VWAP AUTHORITY: Block trades based on posture
                    if vwap_decision.posture == VWAPPosture.NEUTRAL:
                        self._logger.log("crypto_vwap_neutral_block", {
                            "pair": pair,
                            "current_price": current_price,
                            "vwap": vwap_decision.vwap_level.vwap if vwap_decision.vwap_level else 0,
                            "distance_pct": vwap_decision.distance_from_vwap_pct,
                            "reason": vwap_decision.block_reason or "NEUTRAL posture - no directional trades"
                        })
                        # Return hold - VWAP authority says no trades
                        return signal
                    
                except Exception as e:
                    self._logger.error(f"VWAP posture check failed for {pair}: {e}")
                    # Fail-closed: if VWAP check fails, block the trade
                    signal["indicators"]["vwap_error"] = str(e)
                    return signal
            
            # STEP 10: Generate signal with multi-indicator confirmation
            if len(prices) >= 20:
                sma5 = signal["indicators"]["sma5"]
                sma20 = signal["indicators"]["sma20"]
                
                # TREND FILTER: Price must be near or above 20-period SMA
                # Allows trades when price is within 0.5% of SMA (prevents blocking at SMA touch)
                TREND_FILTER_TOLERANCE_PCT = 0.5  # Allow trades within 0.5% of SMA
                pct_from_sma = ((current_price - sma20) / sma20) * 100
                trend_filter_passed = pct_from_sma >= -TREND_FILTER_TOLERANCE_PCT
                signal["indicators"]["trend_filter"] = trend_filter_passed
                signal["indicators"]["pct_from_sma20"] = round(pct_from_sma, 3)
                
                if not trend_filter_passed:
                    self._logger.log("crypto_trend_filter_block", {
                        "pair": pair,
                        "current_price": current_price,
                        "sma20": sma20,
                        "pct_below_sma": round(-pct_from_sma, 2),
                        "tolerance_pct": TREND_FILTER_TOLERANCE_PCT,
                        "action": "hold_no_entry"
                    })
                    # Return hold signal - price is too far below trend line
                    return signal
                
                # Condition 1: Price above SMA5 and SMA5 above SMA20 (uptrend)
                sma_bullish = current_price > sma5 and sma5 > sma20
                
                # Condition 2: RSI not overbought (below 70)
                rsi_ok = rsi < RSI_OVERBOUGHT
                
                # Condition 3: MACD histogram positive (bullish momentum)
                macd_bullish = macd_data["histogram"] > 0
                
                # Condition 4: RSI not in oversold territory pulling back (wait for confirmation)
                rsi_confirmed = rsi > RSI_OVERSOLD  # Above 30
                
                # Track which conditions are met
                signal["indicators"]["sma_bullish"] = sma_bullish
                signal["indicators"]["rsi_ok"] = rsi_ok
                signal["indicators"]["macd_bullish"] = macd_bullish
                
                # Calculate base score
                conditions_met = sum([sma_bullish, rsi_ok, macd_bullish, rsi_confirmed])
                
                # Determine if signal should be generated
                # Require SMA + at least one confirmation (RSI or MACD)
                require_rsi = self._config.require_rsi_confirmation if self._config else True
                require_macd = self._config.require_macd_confirmation if self._config else True
                
                if sma_bullish:
                    confirmations_needed = 0
                    confirmations_have = 0
                    
                    if require_rsi:
                        confirmations_needed += 1
                        if rsi_ok and rsi_confirmed:
                            confirmations_have += 1
                    
                    if require_macd:
                        confirmations_needed += 1
                        if macd_bullish:
                            confirmations_have += 1
                    
                    # Generate buy signal if enough confirmations
                    if confirmations_have >= confirmations_needed or confirmations_needed == 0:
                        # VWAP AUTHORITY CHECK: Block long if posture is BEARISH
                        if vwap_decision and not vwap_decision.allow_long:
                            self._logger.log("crypto_vwap_long_blocked", {
                                "pair": pair,
                                "posture": vwap_decision.posture.value,
                                "reason": "VWAP posture BEARISH - longs blocked"
                            })
                            # Don't set buy signal - VWAP authority blocks it
                        else:
                            signal["action"] = "buy"
                            # Boost confidence if this is a VWAP retest entry
                            base_confidence = 0.4 + (conditions_met * 0.125)
                            if vwap_decision and vwap_decision.is_vwap_retest:
                                base_confidence += 0.1  # Bonus for institutional setup
                                signal["vwap_retest_entry"] = True
                            signal["confidence"] = min(0.9, base_confidence)
                
                # BEARISH CONDITIONS FOR SHORTING
                # Condition 1: Price below SMA5 and SMA5 below SMA20 (downtrend)
                sma_bearish = current_price < sma5 and sma5 < sma20
                
                # Condition 2: RSI not oversold (above 30) - room to fall
                rsi_short_ok = rsi > RSI_OVERSOLD
                
                # Condition 3: MACD histogram negative (bearish momentum)
                macd_bearish = macd_data["histogram"] < 0
                
                # Condition 4: RSI below neutral (showing weakness)
                rsi_bearish = rsi < 50
                
                # Track bearish conditions
                signal["indicators"]["sma_bearish"] = sma_bearish
                signal["indicators"]["macd_bearish"] = macd_bearish
                
                # Generate short signal if bearish conditions met
                if sma_bearish and signal["action"] == "hold":
                    short_confirmations = 0
                    if require_rsi and rsi_short_ok and rsi_bearish:
                        short_confirmations += 1
                    if require_macd and macd_bearish:
                        short_confirmations += 1
                    
                    confirmations_needed_short = (1 if require_rsi else 0) + (1 if require_macd else 0)
                    
                    if short_confirmations >= confirmations_needed_short or confirmations_needed_short == 0:
                        # VWAP AUTHORITY CHECK: Block short if posture is BULLISH
                        if vwap_decision and not vwap_decision.allow_short:
                            self._logger.log("crypto_vwap_short_blocked", {
                                "pair": pair,
                                "posture": vwap_decision.posture.value,
                                "reason": "VWAP posture BULLISH - shorts blocked"
                            })
                            # Don't set short signal - VWAP authority blocks it
                        else:
                            signal["action"] = "short"
                            bearish_conditions = sum([sma_bearish, rsi_short_ok, macd_bearish, rsi_bearish])
                            base_confidence = 0.4 + (bearish_conditions * 0.125)
                            if vwap_decision and vwap_decision.is_vwap_retest:
                                base_confidence += 0.1  # Bonus for institutional setup
                                signal["vwap_retest_entry"] = True
                            signal["confidence"] = min(0.9, base_confidence)
            
            # STEP 11: Reject if using stale data for actual trades
            if signal["action"] in ["buy", "short"] and not is_fresh:
                self._logger.log("crypto_signal_stale_data_reject", {
                    "pair": pair,
                    "would_be_action": signal["action"],
                    "data_source": signal["data_source"],
                    "reason": f"Using cached/stale data - rejecting {signal['action']} signal"
                })
                signal["action"] = "hold"
                signal["indicators"]["stale_data_rejected"] = True
            
            # Log signal generation with full details
            self._logger.log("crypto_signal_generated", {
                "pair": pair,
                "action": signal["action"],
                "confidence": signal["confidence"],
                "price": current_price,
                "data_source": signal["data_source"],
                "indicators": signal["indicators"]
            })
            
            # Update decision tracker for dashboard visibility
            try:
                tracker = get_decision_tracker()
                reason = ""
                if signal["action"] == "buy":
                    reason = f"SMA+RSI+MACD bullish, {signal['confidence']:.1%} confidence"
                elif signal["action"] == "short":
                    reason = f"SMA+RSI+MACD bearish, {signal['confidence']:.1%} confidence"
                else:
                    reason = f"No entry signal ({signal['data_source']})"
                tracker.update_signal(
                    bot_id=self.bot_id,
                    bot_type="crypto",
                    symbol=pair,
                    signal=signal["action"],
                    strength=signal["confidence"],
                    reason=reason
                )
            except Exception as track_err:
                self._logger.error(f"Decision tracker update failed: {track_err}")
            
        except Exception as e:
            # Log error but return hold signal (fail-safe)
            self._logger.error(f"Signal generation failed for {pair}: {e}")
            signal["error"] = str(e)
        
        return signal

    def _generate_turtle_signal(self, pair: str, equity: float) -> Dict[str, Any]:
        """
        Generate a Turtle Traders signal for a crypto pair using hourly bars.
        
        Turtle strategy adapted for 24/7 crypto markets:
        - Uses hourly bars instead of daily (480h = 20 days)
        - Donchian channel breakouts for entries
        - ATR-based position sizing and 2N stop-losses
        - Pyramiding on 0.5N favorable moves
        
        Args:
            pair: Crypto pair (e.g., "BTC/USD")
            equity: Current account equity for position sizing
        
        Returns:
            Signal dictionary with action, qty, stop_price, atr
        """
        signal = {
            "pair": pair,
            "action": "hold",
            "confidence": 0.0,
            "price": 0.0,
            "qty": 0,
            "stop_price": None,
            "atr": None,
            "reason": "initializing"
        }
        
        turtle_engine = self._turtle_engines.get(pair)
        if not turtle_engine:
            self._logger.warn(f"Turtle engine not initialized for {pair}")
            return signal
        
        try:
            entry_lookback = self._config.turtle_entry_lookback if self._config else 480
            atr_period = self._config.turtle_atr_period if self._config else 20
            alpaca_bars = self._alpaca.get_crypto_bars(
                symbol=pair,
                timeframe="1Hour",
                limit=max(entry_lookback, atr_period) + 10
            )
            
            if not alpaca_bars or len(alpaca_bars) < atr_period + 1:
                self._logger.log("turtle_insufficient_bars", {
                    "pair": pair,
                    "bars_received": len(alpaca_bars) if alpaca_bars else 0,
                    "required": atr_period + 1
                })
                signal["reason"] = "insufficient_data"
                return signal
            
            bars = [
                {
                    "high": float(bar.high),
                    "low": float(bar.low),
                    "close": float(bar.close),
                    "open": float(bar.open),
                    "volume": float(bar.volume) if hasattr(bar, 'volume') else 0
                }
                for bar in alpaca_bars
            ]
            current_price = bars[-1]["close"]
            signal["price"] = current_price
            
            positions = self._alpaca.get_positions()
            pair_clean = pair.replace("/", "")
            has_position = any(getattr(p, "symbol", str(p.symbol)) == pair_clean for p in positions)
            position_side = None
            position_qty = 0.0
            if has_position:
                for p in positions:
                    p_symbol = getattr(p, "symbol", str(p.symbol)) if hasattr(p, "symbol") else ""
                    if p_symbol == pair_clean:
                        qty = float(getattr(p, "qty", 0))
                        position_side = "long" if qty > 0 else "short"
                        position_qty = abs(qty)
                        break
            
            turtle_signal = turtle_engine.evaluate(
                bars=bars,
                equity=equity,
                current_price=current_price,
                has_position=has_position,
                position_side=position_side,
                position_qty=position_qty
            )
            
            signal["atr"] = turtle_signal.atr_n
            signal["indicators"] = {
                "donchian_upper": turtle_signal.donchian_upper,
                "donchian_lower": turtle_signal.donchian_lower,
                "atr": turtle_signal.atr_n
            }
            signal["indicators"].update(turtle_signal.indicators)
            
            if turtle_signal.signal_type == SignalType.LONG_ENTRY:
                signal["action"] = "buy"
                signal["qty"] = turtle_signal.position_size_shares
                signal["stop_price"] = turtle_signal.stop_price
                signal["confidence"] = turtle_signal.confidence
                signal["reason"] = turtle_signal.reason
            elif turtle_signal.signal_type == SignalType.SHORT_ENTRY:
                signal["action"] = "short"
                signal["qty"] = turtle_signal.position_size_shares
                signal["stop_price"] = turtle_signal.stop_price
                signal["confidence"] = turtle_signal.confidence
                signal["reason"] = turtle_signal.reason
            elif turtle_signal.signal_type == SignalType.PYRAMID_ADD:
                signal["action"] = "pyramid"
                signal["qty"] = turtle_signal.position_size_shares
                signal["stop_price"] = turtle_signal.stop_price
                signal["confidence"] = turtle_signal.confidence
                signal["reason"] = turtle_signal.reason
            elif turtle_signal.signal_type in [SignalType.LONG_EXIT, SignalType.SHORT_EXIT, SignalType.STOP_EXIT]:
                signal["action"] = "exit"
                signal["confidence"] = turtle_signal.confidence
                signal["reason"] = turtle_signal.reason
            else:
                signal["reason"] = turtle_signal.reason
            
            if turtle_signal.filtered_by_winner:
                signal["reason"] = "winner_filter_skip"
            
            self._logger.log("turtle_signal_generated", {
                "pair": pair,
                "action": signal["action"],
                "signal_type": turtle_signal.signal_type.value,
                "confidence": signal["confidence"],
                "price": current_price,
                "atr": signal["atr"],
                "qty": signal["qty"],
                "reason": signal["reason"]
            })
            
        except Exception as e:
            self._logger.error(f"Turtle signal generation failed for {pair}: {e}")
            signal["error"] = str(e)
        
        return signal
    
    # =========================================================================
    # TRADE EXECUTION - Enter positions with proper sizing
    # =========================================================================
    
    def _execute_trade(self, pair: str, signal: Dict[str, Any], 
                       max_daily_loss: float) -> Dict[str, Any]:
        """
        Execute a trade based on the generated signal.
        
        PRODUCTION FEATURES:
        - Pre-trade risk validation (equity, cash, daily P&L)
        - Smart order execution with slippage protection
        - Fresh quote validation before order placement
        - Comprehensive audit trail logging
        
        Args:
            pair: Crypto pair to trade (e.g., "BTC/USD")
            signal: The trading signal containing action and price
            max_daily_loss: Maximum dollar amount to risk on this trade
        
        Returns:
            Dictionary containing:
            - success: True if trade was executed
            - error: Error message if failed
            - order_id: Alpaca order ID if successful
            - execution_price: Actual fill price
        """
        result = {
            "success": False, 
            "error": None, 
            "order_id": None,
            "execution_price": None,
            "slippage": None
        }
        
        try:
            # STEP 1: Get fresh quote first for pricing
            quote, is_fresh = self._get_validated_quote(pair)
            if not quote:
                result["error"] = "Cannot get quote for order placement"
                return result
            
            current_price = quote["mid"]
            
            # STEP 2: Validate spread before any sizing
            max_spread = self._config.max_spread_pct if self._config else MAX_SPREAD_PCT
            if quote["spread_pct"] > max_spread:
                result["error"] = f"Spread too wide: {quote['spread_pct']:.3f}% > {max_spread}%"
                return result
            
            # STEP 3: Get account info for institutional sizing
            try:
                account = self._alpaca.get_account()
                equity = float(account.equity)
            except Exception as e:
                result["error"] = f"Cannot get account for sizing: {e}"
                return result
            
            # STEP 4: Get current positions for correlation check
            try:
                positions = self._alpaca.get_positions()
                position_dicts = [
                    {
                        "symbol": p.symbol,
                        "market_value": float(p.market_value),
                        "asset_class": "crypto"
                    }
                    for p in positions
                ]
            except Exception:
                position_dicts = []
            
            # STEP 5: Check correlation and sector exposure limits
            corr_check = self._correlation_manager.check_trade_correlation(
                symbol=pair,
                notional=max_daily_loss * 0.8,
                side=signal["action"],
                current_positions=position_dicts,
                equity=equity,
                asset_class="crypto"
            )
            
            if not corr_check.approved:
                result["error"] = f"Correlation blocked: {corr_check.blocking_reason}"
                self._logger.log("crypto_trade_correlation_blocked", {
                    "pair": pair,
                    "reason": corr_check.blocking_reason,
                    "correlation_exposure": corr_check.correlation_exposure,
                    "sector_exposure": corr_check.sector_exposure,
                    "recommendations": corr_check.recommendations
                })
                return result
            
            # STEP 6: Calculate institutional position size using ATR-based risk
            # Extract ATR from signal indicators (calculated in _generate_momentum_signal)
            atr_value = signal.get("indicators", {}).get("atr_14", 0)
            volatility_pct = (atr_value / current_price * 100) if current_price > 0 and atr_value > 0 else quote.get("spread_pct", 0.5) * 10
            
            regime_multiplier = get_state("ml.size_multiplier", 1.0)
            drawdown_multiplier = 1.0 - (get_state("ml.drawdown_probability", 0.0) * 0.5)
            ml_probability = signal.get("confidence", 0.5)
            
            size_result = self._position_sizer.calculate_position_size(
                symbol=pair,
                side=signal["action"],
                current_price=current_price,
                equity=equity,
                ml_probability=ml_probability,
                ml_confidence=signal.get("confidence", 0.5),
                atr=atr_value if atr_value > 0 else None,
                volatility_pct=volatility_pct,
                regime_multiplier=regime_multiplier,
                drawdown_multiplier=drawdown_multiplier,
                correlation_exposure=corr_check.correlation_exposure,
                max_daily_loss=max_daily_loss,
                asset_class="crypto"
            )
            
            dollar_amount = size_result.notional
            qty = size_result.qty
            
            self._logger.log("institutional_position_size", {
                "pair": pair,
                "notional": dollar_amount,
                "qty": qty,
                "base_notional": size_result.base_notional,
                "final_multiplier": size_result.final_multiplier,
                "sizing_reason": size_result.sizing_reason,
                "kelly_adjustment": size_result.kelly_adjustment,
                "volatility_adjustment": size_result.volatility_adjustment,
                "regime_adjustment": size_result.regime_adjustment,
                "correlation_adjustment": size_result.correlation_adjustment,
                "risk_metrics": size_result.risk_metrics
            })
            
            # STEP 7: Pre-trade risk check (PRODUCTION)
            risk_check = self._pre_trade_risk_check(pair, dollar_amount, max_daily_loss)
            if not risk_check["approved"]:
                result["error"] = risk_check["reason"]
                self._logger.log("crypto_trade_rejected", {
                    "pair": pair,
                    "reason": risk_check["reason"],
                    "notional": dollar_amount
                })
                return result
            
            # Warn if using stale data but still allow if within tolerance
            if not is_fresh:
                self._logger.warn(f"Executing with non-fresh quote for {pair}")
            
            # STEP 8: Validate quantity
            if current_price <= 0 or qty <= 0:
                result["error"] = "Invalid price or quantity for order"
                return result
            
            # STEP 9: Determine order side and calculate limit price
            side = "buy" if signal["action"] == "buy" else "sell"
            slippage_buffer = SLIPPAGE_BUFFER_PCT / 100
            
            if side == "buy":
                # For buys, set limit slightly above ask to ensure fill
                limit_price = round(quote["ask"] * (1 + slippage_buffer), 2)
            else:
                # For shorts (sells), set limit slightly below bid to ensure fill
                limit_price = round(quote["bid"] * (1 - slippage_buffer), 2)
            
            # =====================================================================
            # ExitBot v2 Integration - Generate signal identity BEFORE order
            # =====================================================================
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            signal_id = f"CB_{pair.replace('/', '')}_{side}_{ts}_{uuid4().hex[:6]}"
            client_order_id = f"crypto_bot:{pair}:{side}:{signal_id}"
            
            # Check ExitBot health before entry (fail-closed enforcement)
            exitbot = get_exitbot()
            if not exitbot.is_healthy():
                result["error"] = "ExitBot unhealthy - entry blocked"
                self._logger.warn(f"ExitBot unhealthy - blocking entry for {pair}")
                return result
            
            self._logger.log("crypto_entry_attempt", {
                "pair": pair,
                "side": side,
                "notional": dollar_amount,
                "qty": qty,
                "signal_id": signal_id,
                "client_order_id": client_order_id
            })
            
            # STEP 10: Place limit order with slippage protection
            # Use limit order instead of market for better execution
            order_response = self._alpaca.place_limit_order(
                symbol=pair,
                side=side,
                qty=qty,
                limit_price=limit_price,
                client_order_id=client_order_id
            )
            
            # STEP 11: Mark success and capture order details
            result["success"] = True
            result["order_id"] = order_response.get("id")
            result["execution_price"] = order_response.get("filled_avg_price", limit_price)
            
            # Calculate actual slippage (positive = worse for us)
            if result["execution_price"]:
                if side == "buy":
                    actual_slippage = ((result["execution_price"] - quote["mid"]) / quote["mid"]) * 100
                else:
                    actual_slippage = ((quote["mid"] - result["execution_price"]) / quote["mid"]) * 100
                result["slippage"] = round(actual_slippage, 4)
            
            # STEP 12: Log trade with full production details including institutional sizing
            self._logger.log("crypto_trade_executed", {
                "pair": pair,
                "side": side,
                "notional": dollar_amount,
                "qty": qty,
                "order_id": result["order_id"],
                "signal_confidence": signal.get("confidence", 0),
                "entry_price": current_price,
                "limit_price": limit_price,
                "execution_price": result["execution_price"],
                "slippage_pct": result["slippage"],
                "quote_spread_pct": quote["spread_pct"],
                "data_source": quote.get("data_source", "unknown"),
                "risk_check_equity": risk_check.get("equity", 0),
                "signal_id": signal_id,
                "client_order_id": client_order_id,
                "institutional_sizing": {
                    "kelly_adjustment": size_result.kelly_adjustment,
                    "volatility_adjustment": size_result.volatility_adjustment,
                    "regime_adjustment": size_result.regime_adjustment,
                    "correlation_adjustment": size_result.correlation_adjustment,
                    "final_multiplier": size_result.final_multiplier,
                    "sizing_reason": size_result.sizing_reason
                }
            })
            
            # =====================================================================
            # ExitBot v2 - Register entry intent for lifecycle tracking
            # =====================================================================
            entry_time = get_market_clock().now()
            order_id = result["order_id"]
            position_key = exitbot.register_entry_intent(
                bot_id="crypto_bot",
                symbol=pair,
                side=side,
                qty=qty,
                entry_price=current_price,
                signal_id=signal_id,
                client_order_id=client_order_id,
                alpaca_order_id=order_id,
                asset_class="crypto"
            )
            
            # STEP 13: Store trade details in state
            trade_key = f"trades.{self.bot_id}.{int(time.time())}"
            set_state(trade_key, {
                "pair": pair,
                "side": side,
                "notional": dollar_amount,
                "qty": qty,
                "timestamp": time.time(),
                "entry_time": entry_time.isoformat(),
                "order_id": order_id,
                "entry_price": current_price,
                "limit_price": limit_price,
                "execution_price": result["execution_price"],
                "signal": signal,
                "data_source": quote.get("data_source"),
                "signal_id": signal_id,
                "client_order_id": client_order_id,
                "position_key": position_key
            })
            
            # Store entry time for time-based exit tracking
            entry_key = f"entry_time.{self.bot_id}.{pair.replace('/', '')}"
            set_state(entry_key, entry_time.isoformat())
            
            self._logger.log("crypto_entry_success", {
                "pair": pair,
                "order_id": order_id,
                "side": side,
                "notional": dollar_amount,
                "signal_id": signal_id,
                "position_key": position_key
            })
            
        except Exception as e:
            result["error"] = str(e)
            self._logger.error(f"Trade execution failed for {pair}: {e}")
        
        return result

    def _execute_trade_with_qty(self, pair: str, signal: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a Turtle-style trade with pre-calculated quantity.
        
        Turtle strategy calculates position size based on ATR volatility,
        so we use share quantity instead of dollar notional.
        
        Args:
            pair: Crypto pair to trade (e.g., "BTC/USD")
            signal: Trading signal containing:
                - action: "buy", "short", or "pyramid"
                - qty: Number of units to trade
                - stop_price: 2N stop-loss level
                - atr: Current ATR for position tracking
        
        Returns:
            Dictionary with success, error, order_id
        """
        result = {"success": False, "error": None, "order_id": None}
        
        try:
            qty = signal.get("qty", 0)
            if qty <= 0:
                result["error"] = "Invalid quantity for Turtle trade"
                return result
            
            quote, is_fresh = self._get_validated_quote(pair)
            if not quote:
                result["error"] = "Cannot get quote for order placement"
                return result
            
            current_price = quote["mid"]
            max_spread = self._config.max_spread_pct if self._config else MAX_SPREAD_PCT
            if quote["spread_pct"] > max_spread:
                result["error"] = f"Spread too wide: {quote['spread_pct']:.3f}%"
                return result
            
            action = signal["action"]
            if action in ["short"]:
                order_side = "sell"
                position_side = "short"
            else:
                order_side = "buy"
                position_side = "long"
            
            slippage_buffer = SLIPPAGE_BUFFER_PCT / 100
            if order_side == "buy":
                limit_price = round(quote["ask"] * (1 + slippage_buffer), 6)
            else:
                limit_price = round(quote["bid"] * (1 - slippage_buffer), 6)
            
            # =====================================================================
            # ExitBot v2 Integration - Generate signal identity BEFORE order
            # =====================================================================
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            signal_id = f"CB_{pair.replace('/', '')}_{order_side}_{ts}_{uuid4().hex[:6]}"
            client_order_id = f"crypto_bot:{pair}:{order_side}:{signal_id}"
            
            # Check ExitBot health before entry (fail-closed enforcement)
            exitbot = get_exitbot()
            if not exitbot.is_healthy():
                result["error"] = "ExitBot unhealthy - entry blocked"
                self._logger.warn(f"ExitBot unhealthy - blocking entry for {pair}")
                return result
            
            self._logger.log("crypto_turtle_entry_attempt", {
                "pair": pair,
                "action": action,
                "order_side": order_side,
                "qty": qty,
                "signal_id": signal_id,
                "client_order_id": client_order_id
            })
            
            order_response = self._alpaca.place_limit_order(
                symbol=pair,
                side=order_side,
                qty=qty,
                limit_price=limit_price,
                client_order_id=client_order_id
            )
            
            result["success"] = True
            result["order_id"] = order_response.get("id")
            result["execution_price"] = order_response.get("filled_avg_price", limit_price)
            
            self._logger.log("turtle_crypto_trade_executed", {
                "pair": pair,
                "action": action,
                "order_side": order_side,
                "position_side": position_side,
                "qty": qty,
                "limit_price": limit_price,
                "stop_price": signal.get("stop_price"),
                "atr": signal.get("atr"),
                "order_id": result["order_id"],
                "signal_id": signal_id,
                "client_order_id": client_order_id,
                "is_pyramid": action == "pyramid"
            })
            
            # =====================================================================
            # ExitBot v2 - Register entry intent for lifecycle tracking
            # =====================================================================
            entry_time_now = get_market_clock().now()
            order_id = result["order_id"]
            position_key = exitbot.register_entry_intent(
                bot_id="crypto_bot",
                symbol=pair,
                side=order_side,
                qty=qty,
                entry_price=current_price,
                signal_id=signal_id,
                client_order_id=client_order_id,
                alpaca_order_id=order_id,
                asset_class="crypto"
            )
            
            trade_key = f"trades.{self.bot_id}.{int(time.time())}"
            entry_time_iso = entry_time_now.isoformat()
            set_state(trade_key, {
                "pair": pair,
                "action": action,
                "position_side": position_side,
                "qty": qty,
                "timestamp": time.time(),
                "entry_time": entry_time_iso,
                "order_id": order_id,
                "entry_price": current_price,
                "stop_price": signal.get("stop_price"),
                "atr": signal.get("atr"),
                "strategy": "turtle",
                "signal": signal,
                "signal_id": signal_id,
                "client_order_id": client_order_id,
                "position_key": position_key
            })
            
            entry_key = f"entry_time.{self.bot_id}.{pair.replace('/', '')}"
            set_state(entry_key, entry_time_iso)
            
            self._logger.log("crypto_turtle_entry_success", {
                "pair": pair,
                "order_id": order_id,
                "action": action,
                "qty": qty,
                "signal_id": signal_id,
                "position_key": position_key
            })
            
        except Exception as e:
            result["error"] = str(e)
            self._logger.error(f"Turtle crypto trade execution failed for {pair}: {e}")
        
        return result
    
    # =========================================================================
    # POSITION MANAGEMENT - Monitor and exit positions
    # =========================================================================
    
    def _manage_position(self, position) -> None:
        """
        Manage an existing crypto position with multiple exit conditions.
        
        PRODUCTION EXIT CONDITIONS (checked in order):
        1. Hard stop loss - ALWAYS checked first as safety fallback (2% default)
        2. Trailing stop - If enabled, updates each loop and checks trigger
        3. Stop loss - If unrealized loss exceeds stop_loss_pct
        4. Take profit - If unrealized gain exceeds take_profit_pct
        5. Time stop - If held longer than time_stop_minutes
        
        Note: Crypto has no session end (24/7 trading), so no session-based exit.
        
        Args:
            position: Alpaca position object with current market values
        """
        try:
            # STEP 1: Get current market price with validation
            symbol = str(position.symbol)
            
            # Convert Alpaca symbol (BTCUSD) to quote format (BTC/USD)
            # Uses helper function to handle all crypto pairs, not just BTC/ETH
            pair = normalize_crypto_symbol(symbol)
            
            # COOLDOWN CHECK: Skip if we recently traded this pair
            # This prevents rapid duplicate exit orders
            pair_clean = pair.replace("/", "")
            current_time = time.time()
            last_trade = self._last_trade_time.get(pair_clean, 0)
            if current_time - last_trade < TRADE_COOLDOWN_SECONDS:
                self._logger.log("crypto_skip_position_cooldown", {
                    "symbol": symbol,
                    "seconds_remaining": round(TRADE_COOLDOWN_SECONDS - (current_time - last_trade), 1),
                    "reason": "recent_trade_cooldown"
                })
                return
            
            # Use validated quote getter with retry logic (PRODUCTION)
            quote, is_fresh = self._get_validated_quote(pair)
            if not quote:
                self._logger.warn(f"No quote for {symbol} - skipping management")
                return
            
            current_price = quote["mid"]
            
            # STEP 1.5: DELEGATION CHECK - ExitBot becomes sole exit authority
            # If delegate_exits_to_exitbot is True (default), skip all self-exit logic
            # and let ExitBot v2 handle position exits
            delegate_to_exitbot = (self._config and getattr(self._config, "delegate_exits_to_exitbot", True)) or (self._config is None)
            if delegate_to_exitbot:
                unrealized_pl = float(position.unrealized_pl)
                market_value = abs(float(position.market_value))
                pnl_pct = (unrealized_pl / market_value) * 100 if market_value > 0 else 0
                
                # ProfitSniper: override ExitBot if profit is spiking and reversing
                try:
                    sniper = get_profit_sniper()
                    position_key = f"crypto_{symbol}"
                    qty = abs(float(position.qty))
                    entry_price = float(position.cost_basis) / qty if qty > 0 else current_price
                    sniper_decision = sniper.evaluate(
                        position_key=position_key,
                        entry_price=entry_price,
                        current_price=current_price,
                        side=position.side or "long",
                        config=ProfitSniperConfig.for_crypto(),
                        bot_id=self.bot_id
                    )
                    if sniper_decision.should_exit:
                        self._logger.log("crypto_sniper_override_exitbot", {
                            "symbol": symbol,
                            "reason": sniper_decision.reason,
                            "exit_pct": sniper_decision.exit_pct,
                            "peak_profit_pct": round(sniper_decision.peak_profit_pct, 3),
                            "current_profit_pct": round(sniper_decision.current_profit_pct, 3)
                        })
                        self._close_position(position, f"profit_sniper_{sniper_decision.reason}", pnl_pct)
                        return
                except Exception as e:
                    self._logger.warn(f"ProfitSniper check failed for {symbol}: {e}\n{traceback.format_exc()}")
                
                self._logger.log("crypto_position_delegated_to_exitbot", {
                    "symbol": symbol,
                    "pair": pair,
                    "side": position.side,
                    "qty": abs(float(position.qty)),
                    "current_price": current_price,
                    "unrealized_pl": unrealized_pl,
                    "pnl_pct": round(pnl_pct, 3),
                    "status": "monitoring_only",
                    "message": "Exit decisions delegated to ExitBot v2 - CryptoBot reports status only"
                })
                return
            
            # Log if using stale data for position management
            if not is_fresh:
                self._logger.log("crypto_position_stale_data", {
                    "symbol": symbol,
                    "data_source": quote.get("data_source"),
                    "price": current_price
                })
            
            # STEP 2: Calculate current P&L percentage
            market_value = abs(float(position.market_value))
            if market_value <= 0:
                return  # No position value, skip
            
            unrealized_pl = float(position.unrealized_pl)
            pnl_pct = (unrealized_pl / market_value) * 100
            
            # STEP 3: Get exit thresholds from config
            stop_loss_pct = -(self._config.stop_loss_pct if self._config else 0.75)
            take_profit_pct = self._config.take_profit_pct if self._config else 1.50
            time_stop_minutes = self._config.time_stop_minutes if self._config else 240
            hard_stop_pct = -(self._config.hard_stop_loss_pct if self._config else 2.0)
            
            # STEP 4: Initialize exit decision variables
            should_close = False
            close_reason = ""
            
            # STEP 4.1: ProfitSniper — profit-priority exit (runs BEFORE trailing stops)
            try:
                sniper = get_profit_sniper()
                position_key = f"crypto_{symbol}"
                qty = abs(float(position.qty))
                entry_price = float(position.cost_basis) / qty if qty > 0 else current_price
                sniper_decision = sniper.evaluate(
                    position_key=position_key,
                    entry_price=entry_price,
                    current_price=current_price,
                    side=position.side or "long",
                    config=ProfitSniperConfig.for_crypto(),
                    bot_id=self.bot_id
                )
                if sniper_decision.should_exit:
                    should_close = True
                    close_reason = f"profit_sniper_{sniper_decision.reason}"
                    self._logger.log("crypto_sniper_triggered", {
                        "symbol": symbol,
                        "reason": sniper_decision.reason,
                        "exit_pct": sniper_decision.exit_pct,
                        "peak_profit_pct": round(sniper_decision.peak_profit_pct, 3),
                        "current_profit_pct": round(sniper_decision.current_profit_pct, 3)
                    })
            except Exception as e:
                self._logger.warn(f"ProfitSniper check failed for {symbol}: {e}\n{traceback.format_exc()}")
            
            # STEP 4.5: ANTI-CHURN - Calculate hold time for minimum hold check
            min_hold_minutes = self._config.min_hold_minutes if self._config else DEFAULT_MIN_HOLD_MINUTES
            anti_churn_enabled = self._config.anti_churn_enabled if self._config else True
            entry_time = self._get_entry_time(pair)
            hold_duration_minutes = 0
            if entry_time:
                hold_duration_minutes = (get_market_clock().now_naive() - entry_time).total_seconds() / 60
            
            # STEP 5: HARD STOP LOSS - Always checked first (PRODUCTION SAFETY)
            # This is the fallback if trailing stops fail or other logic breaks
            if pnl_pct <= hard_stop_pct:
                should_close = True
                close_reason = "hard_stop_loss"
                self._logger.log("crypto_hard_stop_triggered", {
                    "symbol": symbol,
                    "pnl_pct": round(pnl_pct, 3),
                    "threshold": hard_stop_pct,
                    "reason": "Production safety fallback triggered"
                })
            
            # STEP 6: Check trailing stop (if enabled)
            # PRODUCTION: Always update trailing stop state each loop
            if not should_close and self._config and self._config.trailing_stop_enabled:
                trailing_exit = self._check_trailing_stop_production(
                    position, current_price, pair, pnl_pct
                )
                if trailing_exit:
                    should_close = True
                    close_reason = "trailing_stop"
            
            # STEP 7: Check stop loss (with anti-churn minimum hold time)
            if not should_close and pnl_pct <= stop_loss_pct:
                # ANTI-CHURN: Only trigger soft stop-loss if held longer than minimum
                # Guard: If entry_time is missing, allow stop-loss to prevent indefinite hold
                if anti_churn_enabled and entry_time is not None and hold_duration_minutes < min_hold_minutes:
                    self._logger.log("crypto_stopout_blocked_min_hold", {
                        "symbol": symbol,
                        "pnl_pct": round(pnl_pct, 3),
                        "hold_minutes": round(hold_duration_minutes, 1),
                        "min_hold_minutes": min_hold_minutes,
                        "reason": "minimum_hold_not_reached"
                    })
                else:
                    should_close = True
                    close_reason = "stop_loss"
                    self._logger.log("crypto_stop_loss_triggered", {
                        "symbol": symbol,
                        "pnl_pct": round(pnl_pct, 3),
                        "threshold": stop_loss_pct,
                        "hold_minutes": round(hold_duration_minutes, 1),
                        "entry_time_known": entry_time is not None
                    })
            
            # STEP 8: Check take profit
            if not should_close and pnl_pct >= take_profit_pct:
                should_close = True
                close_reason = "take_profit"
                self._logger.log("crypto_take_profit_triggered", {
                    "symbol": symbol,
                    "pnl_pct": round(pnl_pct, 3),
                    "threshold": take_profit_pct
                })
            
            # STEP 9: Check time-based exit
            if not should_close:
                entry_time = self._get_entry_time(pair)
                if entry_time:
                    hold_duration = (get_market_clock().now_naive() - entry_time).total_seconds() / 60
                    if hold_duration >= time_stop_minutes:
                        should_close = True
                        close_reason = "time_stop"
                        self._logger.log("crypto_time_stop_triggered", {
                            "symbol": symbol,
                            "hold_minutes": round(hold_duration, 1),
                            "threshold": time_stop_minutes
                        })
            
            # STEP 10: Execute exit if any condition met
            # ANTI-CHURN: Record stop-out for whipsaw detection OR clear streak on profit
            if should_close:
                if close_reason in ["stop_loss", "hard_stop_loss", "trailing_stop"]:
                    self._record_stopout(pair_clean)
                elif close_reason == "take_profit":
                    # Clear stop-out streak on profitable exit
                    self._clear_stopout_streak(pair_clean)
            
            if should_close:
                # Check for pending orders on this symbol before placing exit order
                open_orders = self._alpaca.get_open_orders()
                symbol_clean = symbol.replace("/", "")
                has_pending_order = any(
                    symbol_clean in str(o.get("symbol", "")).replace("/", "")
                    for o in open_orders
                )
                if has_pending_order:
                    self._logger.log("crypto_skip_exit_pending_order", {
                        "symbol": symbol,
                        "reason": close_reason,
                        "skip_reason": "pending_order_exists"
                    })
                    return
                
                self._close_position(position, close_reason, pnl_pct)
                
        except Exception as e:
            self._logger.error(f"Position management failed for {position.symbol}: {e}")
    
    def _check_trailing_stop_production(self, position, current_price: float, 
                                         pair: str, pnl_pct: float) -> bool:
        """
        Production-level trailing stop check with hardened logic.
        
        PRODUCTION FEATURES:
        - Always updates trailing stop state each loop (not just on init)
        - Verifies trailing manager state integrity
        - Falls back to hard stop if trailing logic fails
        - Comprehensive logging for audit trail
        
        Args:
            position: Alpaca position object
            current_price: Current market price
            pair: Crypto pair for logging
            pnl_pct: Current P&L percentage
            
        Returns:
            True if trailing stop triggered, False otherwise
        """
        # GUARD: Skip if trailing stops disabled
        if not self._config or not self._config.trailing_stop_enabled:
            return False
        
        try:
            # Generate unique position ID
            position_id = f"{position.symbol}_{position.side}_{position.qty}"
            
            # Get trailing stop manager
            trailing_manager = get_trailing_stop_manager()
            
            # Load existing trailing stop state
            trailing_state = trailing_manager.load_state(
                self.bot_id, position_id, position.symbol, "crypto"
            )
            
            # Initialize if not exists
            if not trailing_state:
                # Calculate entry price from cost basis
                qty = abs(float(position.qty))
                entry_price = float(position.cost_basis) / qty if qty > 0 else current_price
                side = "long" if float(position.qty) > 0 else "short"
                
                # Create config from bot settings
                config = TrailingStopConfig(
                    enabled=self._config.trailing_stop_enabled,
                    mode=self._config.trailing_stop_mode,
                    value=self._config.trailing_stop_value,
                    activation_profit_pct=self._config.trailing_activation_pct,
                    update_only_if_improves=self._config.trailing_update_only_if_improves,
                    epsilon_pct=self._config.trailing_epsilon_pct,
                    exit_order_type=self._config.trailing_exit_order_type
                )
                
                # Initialize trailing stop
                trailing_state = trailing_manager.init_for_position(
                    self.bot_id, position_id, position.symbol, "crypto",
                    entry_price, side, config
                )
                
                self._logger.log("crypto_trailing_init", {
                    "position_id": position_id,
                    "entry_price": entry_price,
                    "trailing_value": self._config.trailing_stop_value
                })
            
            # PRODUCTION: Always update state with current price
            # This ensures the high-water mark is always current
            trailing_state = trailing_manager.update_state(
                self.bot_id, position_id, position.symbol, "crypto",
                current_price, trailing_state
            )
            
            # Log trailing stop state for debugging
            # TrailingStopState is a dataclass, access attributes directly
            self._logger.log("crypto_trailing_update", {
                "position_id": position_id,
                "current_price": current_price,
                "pnl_pct": round(pnl_pct, 3),
                "high_water": trailing_state.high_water if trailing_state else 0,
                "stop_price": trailing_state.stop_price if trailing_state else 0,
                "armed": trailing_state.armed if trailing_state else False
            })
            
            # Check if stop should trigger
            should_exit = trailing_manager.should_exit(trailing_state, current_price)
            
            if should_exit:
                self._logger.log("crypto_trailing_stop_triggered", {
                    "position_id": position_id,
                    "current_price": current_price,
                    "stop_price": trailing_state.stop_price if trailing_state else 0,
                    "pnl_pct": round(pnl_pct, 3)
                })
            
            return should_exit
            
        except Exception as e:
            # PRODUCTION: Log error but don't crash - hard stop will catch
            self._logger.error(f"Trailing stop check failed: {e}")
            self._logger.log("crypto_trailing_fallback", {
                "position": position.symbol,
                "error": str(e),
                "message": "Hard stop loss will be used as fallback"
            })
            return False
    
    def _check_trailing_stop(self, position, current_price: float, pair: str) -> bool:
        """
        Check if trailing stop should trigger exit.
        
        Trailing stop works by tracking the highest price since entry (for longs)
        and placing a stop a certain percentage below that high.
        
        Args:
            position: Alpaca position object
            current_price: Current market price
            pair: Crypto pair for logging
            
        Returns:
            True if trailing stop triggered, False otherwise
        """
        # GUARD: Skip entirely if trailing stops are disabled
        # This prevents unnecessary processing and avoids potential errors
        if not self._config or not self._config.trailing_stop_enabled:
            return False
        
        try:
            # Generate unique position ID
            position_id = f"{position.symbol}_{position.side}_{position.qty}"
            
            # Get trailing stop manager
            trailing_manager = get_trailing_stop_manager()
            
            # Load existing trailing stop state for this position
            trailing_state = trailing_manager.load_state(
                self.bot_id, position_id, position.symbol, "crypto"
            )
            
            # Initialize if not exists
            if not trailing_state:
                # Calculate entry price from cost basis
                qty = abs(float(position.qty))
                if qty > 0:
                    entry_price = float(position.cost_basis) / qty
                else:
                    entry_price = current_price
                
                side = "long" if float(position.qty) > 0 else "short"
                
                # Create trailing stop configuration from bot config
                # Uses config values with safe defaults from CryptoConfig dataclass
                config = TrailingStopConfig(
                    enabled=self._config.trailing_stop_enabled,
                    mode=self._config.trailing_stop_mode,
                    value=self._config.trailing_stop_value,
                    activation_profit_pct=self._config.trailing_activation_pct,
                    update_only_if_improves=self._config.trailing_update_only_if_improves,
                    epsilon_pct=self._config.trailing_epsilon_pct,
                    exit_order_type=self._config.trailing_exit_order_type
                )
                
                # Initialize trailing stop for this position
                trailing_state = trailing_manager.init_for_position(
                    self.bot_id, position_id, position.symbol, "crypto",
                    entry_price, side, config
                )
            
            # Update trailing stop with current price
            trailing_state = trailing_manager.update_state(
                self.bot_id, position_id, position.symbol, "crypto",
                current_price, trailing_state
            )
            
            # Check if stop should trigger
            return trailing_manager.should_exit(trailing_state, current_price)
            
        except Exception as e:
            self._logger.error(f"Trailing stop check failed: {e}")
            return False
    
    def _record_stopout(self, symbol_clean: str) -> None:
        """
        Record a stop-out event for whipsaw detection.
        
        Called when position is closed due to stop-loss, hard stop, or trailing stop.
        Tracks consecutive stop-outs and applies whipsaw pause if threshold exceeded.
        Also notifies WhipsawTrader to potentially switch to mean-reversion mode.
        
        Args:
            symbol_clean: Symbol without "/" (e.g., "BTCUSD")
        """
        current_time = time.time()
        
        # Notify WhipsawTrader (may switch to mean-reversion mode)
        try:
            whipsaw_trader = get_whipsaw_trader()
            whipsaw_trader.record_stopout(symbol_clean)
        except Exception as e:
            self._logger.error(f"WhipsawTrader notification failed: {e}")
        
        # Increment consecutive stop-out counter
        prev_count = self._consecutive_stopouts.get(symbol_clean, 0)
        self._consecutive_stopouts[symbol_clean] = prev_count + 1
        self._last_stopout_time[symbol_clean] = current_time
        
        # Check if whipsaw threshold exceeded
        max_stopouts = self._config.max_consecutive_stopouts if self._config else DEFAULT_MAX_CONSECUTIVE_STOPOUTS
        whipsaw_pause = self._config.whipsaw_pause_minutes if self._config else DEFAULT_WHIPSAW_PAUSE_MINUTES
        
        if self._consecutive_stopouts[symbol_clean] >= max_stopouts:
            # Apply whipsaw pause
            pause_until = current_time + (whipsaw_pause * 60)
            self._whipsaw_pause_until[symbol_clean] = pause_until
            
            self._logger.log("crypto_whipsaw_detected", {
                "symbol": symbol_clean,
                "consecutive_stopouts": self._consecutive_stopouts[symbol_clean],
                "threshold": max_stopouts,
                "pause_minutes": whipsaw_pause,
                "pause_until": datetime.fromtimestamp(pause_until).isoformat()
            })
            
            # Reset counter after applying pause
            self._consecutive_stopouts[symbol_clean] = 0
        else:
            self._logger.log("crypto_stopout_recorded", {
                "symbol": symbol_clean,
                "consecutive_stopouts": self._consecutive_stopouts[symbol_clean],
                "threshold": max_stopouts
            })
    
    def _is_whipsaw_paused(self, symbol_clean: str) -> bool:
        """
        Check if a symbol is paused due to whipsaw detection.
        
        Args:
            symbol_clean: Symbol without "/" (e.g., "BTCUSD")
            
        Returns:
            True if symbol is paused (skip new entries), False otherwise
        """
        pause_until = self._whipsaw_pause_until.get(symbol_clean, 0)
        current_time = time.time()
        
        if current_time < pause_until:
            remaining_minutes = (pause_until - current_time) / 60
            self._logger.log("crypto_whipsaw_pause_active", {
                "symbol": symbol_clean,
                "remaining_minutes": round(remaining_minutes, 1),
                "pause_until": datetime.fromtimestamp(pause_until).isoformat()
            })
            return True
        
        return False
    
    def _get_stopout_cooldown(self, symbol_clean: str) -> float:
        """
        Get the cooldown time based on whether last exit was a stop-out.
        
        Returns extended cooldown after stop-outs to prevent revenge trading.
        
        Args:
            symbol_clean: Symbol without "/" (e.g., "BTCUSD")
            
        Returns:
            Cooldown time in seconds
        """
        last_stopout = self._last_stopout_time.get(symbol_clean, 0)
        last_trade = self._last_trade_time.get(symbol_clean, 0)
        
        # If last exit was a stop-out (stopout time == trade time), use extended cooldown
        if abs(last_stopout - last_trade) < 5:  # Within 5 seconds = same event
            stopout_cooldown = self._config.stopout_cooldown_minutes if self._config else DEFAULT_STOPOUT_COOLDOWN_MINUTES
            return stopout_cooldown * 60  # Convert to seconds
        
        return TRADE_COOLDOWN_SECONDS  # Normal 60-second cooldown
    
    def _clear_stopout_streak(self, symbol_clean: str) -> None:
        """Clear consecutive stop-out counter after a profitable exit."""
        if symbol_clean in self._consecutive_stopouts:
            self._consecutive_stopouts[symbol_clean] = 0
        
        # Notify WhipsawTrader of profitable exit
        try:
            whipsaw_trader = get_whipsaw_trader()
            whipsaw_trader.record_profitable_exit(symbol_clean)
        except Exception as e:
            self._logger.error(f"WhipsawTrader profitable exit notification failed: {e}")
    
    def _get_entry_time(self, pair: str) -> Optional[datetime]:
        """
        Get the entry time for a position from state database.
        
        Args:
            pair: Crypto pair (e.g., "BTC/USD")
            
        Returns:
            datetime of entry, or None if not found
        """
        try:
            # Look up entry time stored when position was opened
            entry_key = f"entry_time.{self.bot_id}.{pair.replace('/', '')}"
            entry_time_str = get_state(entry_key)
            
            if entry_time_str:
                from ..core.clock import MarketClock
                return MarketClock.parse_iso_to_naive(entry_time_str)
            
            return None
            
        except Exception as e:
            self._logger.error(f"Failed to get entry time for {pair}: {e}")
            return None
    
    def _close_position(self, position, reason: str, pnl_pct: float) -> None:
        """
        Close a crypto position with a market order.
        
        Args:
            position: Alpaca position object to close
            reason: Why we're closing (for logging)
            pnl_pct: Current P&L percentage
        """
        try:
            # Determine order side (opposite of position side)
            side = "sell" if position.side == "long" else "buy"
            qty = abs(float(position.qty))
            
            # Determine pair for state cleanup
            symbol = str(position.symbol)
            if "BTC" in symbol:
                pair = "BTC/USD"
            elif "ETH" in symbol:
                pair = "ETH/USD"
            else:
                pair = symbol
            
            # Place market order to close
            order_response = self._alpaca.place_market_order(
                symbol=pair,
                side=side,
                qty=qty
            )
            
            # Log the exit for audit trail
            self._logger.log("crypto_position_closed", {
                "symbol": symbol,
                "pair": pair,
                "side": side,
                "qty": qty,
                "reason": reason,
                "pnl_pct": round(pnl_pct, 3),
                "pnl_dollars": float(position.unrealized_pl),
                "order_id": order_response.get("id")
            })
            
            # Record cooldown timestamp to prevent rapid duplicate orders
            pair_clean = pair.replace("/", "")
            self._last_trade_time[pair_clean] = time.time()
            
            # Clean up state
            entry_key = f"entry_time.{self.bot_id}.{pair.replace('/', '')}"
            delete_state(entry_key)
            
        except Exception as e:
            self._logger.error(f"Failed to close crypto position {position.symbol}: {e}")


# =============================================================================
# SINGLETON FACTORY - Provides cached bot instances
# =============================================================================

_crypto_bot_instances: Dict[str, CryptoBot] = {}


def get_crypto_bot(bot_id: str = "crypto_core") -> CryptoBot:
    """
    Get or create a CryptoBot instance (singleton per bot_id).
    
    Args:
        bot_id: Unique identifier for this bot instance
        
    Returns:
        CryptoBot instance (cached)
    """
    global _crypto_bot_instances
    if bot_id not in _crypto_bot_instances:
        _crypto_bot_instances[bot_id] = CryptoBot(bot_id)
    return _crypto_bot_instances[bot_id]
