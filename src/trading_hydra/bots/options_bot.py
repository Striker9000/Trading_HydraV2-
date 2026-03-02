"""
=============================================================================
OPTIONS BOT - Multi-Strategy Options Trading with Credit Spreads
=============================================================================

This bot implements multiple options trading strategies optimized for 
consistent, small profits ($50-500 per trade). Uses credit spreads which 
benefit from time decay and high implied volatility.

Core Strategies:
1. Bull Put Spread - Collect premium on bullish/neutral markets
2. Bear Call Spread - Collect premium on bearish/neutral markets
3. Iron Condor - Collect premium in range-bound/low volatility markets
4. Straddle - Profit from large moves (long volatility)

Risk Management:
- Enforces max trades per day and max concurrent positions
- Implements trailing stops for protecting profits
- Position sizing based on max loss limits
- Time-based exits before market close
- Respects trading session time windows

Configuration:
All settings loaded from config/bots.yaml under 'optionsbot' section.
Supports enable/disable, strategy toggles, and risk parameters.

Safety Features:
- Fail-closed design - any error stops the bot safely
- All trades logged for audit trail
- Integrates with global halt manager
"""

from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta, time
from dataclasses import dataclass
from uuid import uuid4
import math
import json
import time as _time
from enum import Enum

from ..core.logging import get_logger
from ..core.state import get_state, set_state, delete_state, get_keys_by_prefix
from ..core.config import load_bots_config, load_settings
from ..core.clock import get_market_clock
from ..services.alpaca_client import get_alpaca_client
from ..services.market_regime import (
    get_current_regime, MarketRegimeAnalysis, 
    VolatilityRegime, MarketSentiment
)
from ..services.decision_tracker import get_decision_tracker
from ..services.news_intelligence import get_news_intelligence
from ..services.sentiment_scorer import get_sentiment_scorer
from ..services.exitbot import get_exitbot
from ..risk.trailing_stop import get_trailing_stop_manager, TrailingStopConfig
from ..risk.profit_sniper import get_profit_sniper, ProfitSniperConfig
from ..risk.greek_limits import get_greek_risk_monitor, GreekLimitStatus
from ..ml.signal_service import MLSignalService

from ..strategy.runner import StrategyRunner
from ..strategy.kill_switch import StrategyKillSwitch
from ..risk.killswitch import get_killswitch_service
from ..risk.risk_integration import get_risk_integration, RiskAction
from ..services.strategy_performance_tracker import get_strategy_tracker
from ..indicators.vwap_posture import (
    VWAPPostureManager, VWAPPosture, get_vwap_posture_manager, PostureDecision
)


# =============================================================================
# ENUMS - Strategy and market regime types
# =============================================================================

class OptionStrategy(Enum):
    """
    Enumeration of supported options strategies.
    Each strategy has different risk/reward characteristics.
    
    DEFINED-RISK ONLY: All strategies use spreads to define max loss.
    Naked short options are NOT supported (broker restriction).
    """
    # Buy-side strategies (long options) - only requires premium, no margin
    LONG_CALL = "long_call"               # Buy call - bullish directional bet
    LONG_PUT = "long_put"                 # Buy put - bearish directional bet
    STRADDLE = "straddle"                 # Long volatility (buy ATM call + put)
    # Debit spreads (directional with defined risk) - replaces naked short options
    BULL_CALL_SPREAD = "bull_call_spread" # Buy lower call, sell higher call - bullish debit spread
    BEAR_PUT_SPREAD = "bear_put_spread"   # Buy higher put, sell lower put - bearish debit spread
    # Credit spreads (premium collection with defined risk)
    BULL_PUT_SPREAD = "bull_put_spread"   # Sell higher put, buy lower put - bullish credit spread
    BEAR_CALL_SPREAD = "bear_call_spread" # Sell lower call, buy higher call - bearish credit spread
    # Neutral strategies (range-bound / theta harvesting)
    IRON_CONDOR = "iron_condor"           # Bull put spread + bear call spread combined
    CALENDAR_SPREAD = "calendar_spread"   # Sell near-term, buy far-term (theta harvesting)
    HAIL_MARY = "hail_mary"               # Cheap near-expiration OTM options with massive upside


class MarketRegime(Enum):
    """
    Market condition classification for strategy selection.
    Different strategies perform better in different regimes.
    """
    BULLISH = "bullish"           # Upward trending market
    BEARISH = "bearish"           # Downward trending market
    NEUTRAL = "neutral"           # Sideways/range-bound market
    HIGH_VOLATILITY = "high_vol"  # Large price swings expected
    LOW_VOLATILITY = "low_vol"    # Small price movements expected


# =============================================================================
# DATA CLASSES - Configuration structure
# =============================================================================

@dataclass
class OptionsConfig:
    """
    Configuration for the options trading bot.
    All values come from config/bots.yaml under optionsbot section.
    Default values provided for optional fields to ensure robustness.
    """
    bot_id: str                      # Unique identifier (e.g., "opt_core")
    enabled: bool                    # Whether this bot is active
    tickers: List[str]               # Underlying symbols to trade (e.g., ["SPY", "QQQ"])
    trade_start: str                 # When to start trading (PST, e.g., "06:40")
    trade_end: str                   # When to stop new trades (e.g., "12:30")
    manage_until: str                # When to stop managing positions (e.g., "13:00")
    max_trades_per_day: int          # Maximum trades allowed per day
    max_concurrent_positions: int    # Maximum positions held at once
    max_position_size_usd: float     # Maximum dollar amount per trade
    profit_target_usd: float         # Target profit per trade in USD
    take_profit_pct: float           # Take profit as % of credit received
    stop_loss_pct: float             # Stop loss as % of credit (e.g., 200 = max loss)
    time_stop_minutes: int           # Max hold time in minutes before forced exit
    flatten_before_close_min: int    # Close positions N minutes before market close
    dte_min: int                     # Minimum days to expiration for options
    dte_max: int                     # Maximum days to expiration for options
    delta_min: float                 # Minimum delta for option selection
    delta_max: float                 # Maximum delta for option selection
    min_volume: int                  # Minimum volume for liquidity
    min_open_interest: int           # Minimum open interest for liquidity
    trailing_stop_enabled: bool = False  # Whether to use trailing stops (default: disabled)
    trailing_stop_mode: str = "percent"  # "percent" or "price" for trailing stop
    trailing_stop_value: float = 2.0     # Trailing stop value
    trailing_activation_pct: float = 0.15 # Profit % required before trailing stop activates
    trailing_update_only_if_improves: bool = True  # Only raise stop, never lower
    trailing_epsilon_pct: float = 0.05   # Buffer to prevent noise triggers
    trailing_exit_order_type: str = "market"  # Exit order type
    # Strategy enable flags - buy-side first (no margin required)
    long_call_enabled: bool = True
    long_put_enabled: bool = True
    straddle_enabled: bool = True
    # Debit spreads (directional plays with leverage) - offers more leverage than single-leg longs
    bull_call_spread_enabled: bool = True   # Bullish directional with leverage
    bear_put_spread_enabled: bool = True    # Bearish directional with leverage
    # Credit spreads (premium collection with defined risk)
    bull_put_spread_enabled: bool = True    # Bullish credit spread
    bear_call_spread_enabled: bool = True   # Bearish credit spread
    iron_condor_enabled: bool = True        # Neutral theta harvesting
    # Calendar spread (theta decay harvesting)
    calendar_spread_enabled: bool = True    # Sell near-term, buy far-term
    # Spread width preferences
    spread_width_strikes: int = 5           # Default spread width in strike intervals ($5 for SPY)
    calendar_dte_short: int = 7             # Near-term leg DTE for calendar spreads
    calendar_dte_long: int = 30             # Far-term leg DTE for calendar spreads
    # Strategy system controls
    dry_run: bool = True  # If True, log signals but don't execute trades (default: True for safety)
    delegate_exits_to_exitbot: bool = True  # If True, delegate all exit decisions to ExitBot v2 (default: True)
    prefer_defined_risk: bool = True        # Prefer spreads for leverage/capital efficiency (long options are already safe)
    prefer_iron_condor_first: bool = True   # Default to Iron Condor first, then consider other strategies
    # Hail Mary - cheap near-expiration options with massive upside potential
    hail_mary_enabled: bool = False          # Whether hail mary scanning is active
    hail_mary_max_trades_per_day: int = 2    # Max hail mary trades per day
    hail_mary_max_risk_usd: float = 300.0    # Max capital risked per hail mary trade
    hail_mary_min_risk_usd: float = 50.0     # Min capital to deploy (avoid dust)
    hail_mary_max_premium: float = 3.00      # Max option premium per contract
    hail_mary_min_premium: float = 0.05      # Min premium (avoid worthless junk)
    hail_mary_max_spread: float = 0.50       # Max bid/ask spread
    hail_mary_dte_min: int = 0               # Min DTE for hail mary (0 = same-day)
    hail_mary_dte_max: int = 7               # Max DTE for hail mary
    hail_mary_strike_otm_pct: float = 3.0    # OTM strike range %
    hail_mary_min_delta: float = 0.01        # Min delta for hail mary
    hail_mary_max_delta: float = 0.30        # Max delta for hail mary
    hail_mary_require_momentum: bool = True  # Require momentum alignment
    hail_mary_min_stock_change: float = 0.3  # Min stock movement % for momentum
    hail_mary_tickers: Optional[List[str]] = None  # Static fallback tickers for hail mary scans
    hail_mary_use_dynamic_universe: bool = True   # Pull tickers dynamically from screeners/premarket intel
    hail_mary_dynamic_max_tickers: int = 20       # Max tickers to scan from dynamic sources
    hail_mary_dynamic_min_score: float = 30.0     # Min premarket opportunity score to include
    hail_mary_profit_target_mult: float = 5.0     # Sell at Nx entry price (5x default)
    hail_mary_time_exit_days: int = 1             # Close N days before expiry to salvage value
    hail_mary_use_exitbot: bool = False           # Do NOT register with ExitBot (own exit logic)
    # Tiered profit taking - graduated exits instead of all-or-nothing
    hail_mary_tiered_exits: bool = True           # Enable tiered (graduated) profit taking
    hail_mary_tier1_mult: float = 3.0             # Tier 1: sell portion at 3x
    hail_mary_tier1_pct: float = 50.0             # Sell 50% at tier 1
    hail_mary_tier2_mult: float = 5.0             # Tier 2: sell portion at 5x
    hail_mary_tier2_pct: float = 25.0             # Sell 25% at tier 2
    hail_mary_runner_mult: float = 10.0           # Runner: sell remainder at 10x
    # Earnings IV crush protection
    hail_mary_block_near_earnings: bool = True    # Block entries near earnings to avoid IV crush
    hail_mary_earnings_buffer_days: int = 3       # Block entries within N days of earnings
    # VWAP posture confirmation
    hail_mary_use_vwap_posture: bool = True       # Use VWAP posture to confirm direction


@dataclass
class OptionsTradeRecord:
    """
    Record of a single options trade for tracking and analysis.
    Stored in SQLite state database for persistence across restarts.
    """
    order_id: str                    # Alpaca order ID
    strategy: str                    # Strategy name used
    ticker: str                      # Underlying symbol
    contracts: int                   # Number of contracts
    credit_received: float           # Total credit received (for credit spreads)
    max_loss: float                  # Maximum possible loss
    entry_price: float               # Underlying price at entry
    timestamp: float                 # Unix timestamp of trade
    entry_time: datetime             # Datetime of entry for time-based exits


# =============================================================================
# OPTIONS BOT CLASS - Main trading logic
# =============================================================================

class OptionsBot:
    """
    Multi-strategy options trading bot.
    
    This bot:
    1. Analyzes market conditions to select optimal strategy
    2. Constructs credit spreads (bull put, bear call, iron condor)
    3. Manages positions with trailing stops and time-based exits
    4. Respects risk limits and session windows
    
    Usage:
        bot = OptionsBot("opt_core")
        results = bot.execute(max_daily_loss=100.0)
    """
    
    def __init__(self, bot_id: str = "opt_core"):
        """
        Initialize the options bot.
        
        Args:
            bot_id: Unique identifier for this bot instance (e.g., "opt_core")
        """
        # Store bot identifier
        self.bot_id = bot_id
        
        # Initialize services - logger for audit trail, Alpaca for trading
        self._logger = get_logger()
        self._alpaca = get_alpaca_client()
        
        # Load configuration from bots.yaml
        self._config = self._load_config()
        
        # Set tickers from config or use defaults
        self.tickers = self._config.tickers if self._config else ["SPY", "QQQ", "IWM"]
        
        # Determine if this is a 0DTE bot for tighter parameters
        self._is_0dte = "0dte" in bot_id.lower()
        
        # Strategy configurations - dynamically loaded from YAML config
        # 0DTE bots use tighter constraints from their dedicated config section
        self.strategy_configs = self._build_strategy_configs()
        
        # Portfolio Greeks tracking - aggregated risk exposure
        self._portfolio_greeks = {
            "total_delta": 0.0,
            "total_gamma": 0.0,
            "total_theta": 0.0,
            "total_vega": 0.0,
            "total_rho": 0.0,
            "last_updated": None
        }
        
        # Initialize ML signal service for trade scoring
        self._ml_service = MLSignalService(logger=self._logger)
        settings = load_settings()
        ml_config = settings.get("ml", {})
        self._ml_enabled = ml_config.get("enabled", False)
        # Use options-specific threshold if available, otherwise fall back to global
        self._ml_min_probability = ml_config.get("options_threshold", 
                                                  ml_config.get("min_probability", 0.55))
        
        # Pre-market intelligence cache
        self._premarket_intel = None
        self._dynamic_universe = None
        self._session_prepared = False
        
        # Index options configuration (XSP, etc.)
        # These are cash-settled, European-style options with extended hours
        self._index_options_config = self._load_index_options_config()
        self._index_option_symbols = set(self._index_options_config.get("symbols", ["XSP"]))
        
        # Log initialization for debugging
        self._logger.log("options_bot_init", {
            "bot_id": bot_id,
            "tickers": self.tickers,
            "config_loaded": self._config is not None,
            "enabled": self._config.enabled if self._config else False,
            "is_0dte": self._is_0dte,
            "ml_enabled": self._ml_enabled,
            "ml_available": self._ml_service.is_available,
            "index_options": list(self._index_option_symbols)
        })
        
        # STARTUP SANITY CHECK - log critical config for debugging
        self._logger.log("options_bot_startup_sanity", {
            "bot_id": bot_id,
            "dry_run": self._config.dry_run if self._config else True,
            "enabled": self._config.enabled if self._config else False,
            "ml_enabled": self._ml_enabled,
            "ml_threshold": self._ml_min_probability,
            "ticker_count": len(self.tickers),
            "is_0dte": self._is_0dte,
            "warning": "DRY_RUN_ENABLED" if (self._config and self._config.dry_run) else None
        })
        
        # Load spread gate config from settings
        settings = load_settings()
        smart_exec = settings.get("smart_execution", {})
        self._max_spread_pct = smart_exec.get("max_spread_pct", 0.5)
        
        # Strategy System Integration (PDF rules-based strategies)
        # Only initialized if use_strategy_system is enabled in config
        self._use_strategy_system = self._get_use_strategy_system_flag()
        self._strategy_runner = None
        self._strategy_runner_dry_run = True
        self._strategy_max_daily_loss = 0.0
        self._strategy_kill_switch = StrategyKillSwitch()
        self._strategy_signal_cooldowns: Dict[str, float] = {}
        self._strategy_cooldown_seconds = 300
        
        if self._use_strategy_system:
            self._logger.log("options_bot_strategy_system_enabled", {
                "bot_id": bot_id,
                "message": "Strategy system integration enabled via config"
            })
    
    def _get_use_strategy_system_flag(self) -> bool:
        """Check if strategy system is enabled in bots.yaml config."""
        try:
            bots_config = load_bots_config()
            optionsbot_cfg = bots_config.get("optionsbot", {})
            return bool(optionsbot_cfg.get("use_strategy_system", False))
        except Exception:
            return False
    
    def _load_index_options_config(self) -> Dict[str, Any]:
        """
        Load index options configuration from ticker_universe.yaml.
        
        Index options (XSP, etc.) have special characteristics:
        - Cash-settled (no stock delivery risk)
        - European-style (exercise only at expiration)
        - Extended trading hours (until 4:15 PM ET)
        - Section 1256 tax treatment (60/40 long/short)
        
        Returns:
            Dictionary with index options configuration
        """
        try:
            import yaml
            import os
            
            config_path = os.path.join(
                os.path.dirname(__file__), 
                "..", "..", "..", "config", "ticker_universe.yaml"
            )
            
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    universe_config = yaml.safe_load(f)
                    options_config = universe_config.get("options", {})
                    return options_config.get("index_options", {})
            
            # Default fallback
            return {
                "symbols": ["XSP"],
                "properties": {
                    "cash_settled": True,
                    "european_style": True,
                    "extended_hours": True,
                    "section_1256": True
                }
            }
        except Exception as e:
            self._logger.log("index_options_config_load_failed", {"error": str(e)})
            return {"symbols": ["XSP"]}
    
    def is_index_option(self, symbol: str) -> bool:
        """
        Check if a symbol is an index option (cash-settled, European-style).
        
        Index options like XSP have different characteristics:
        - No early assignment risk (European-style)
        - Cash settlement at expiration (no stock delivery)
        - Extended trading hours
        - Favorable 60/40 tax treatment (Section 1256)
        
        Args:
            symbol: The underlying symbol to check (e.g., "XSP", "SPY")
            
        Returns:
            True if this is an index option, False otherwise
        """
        return symbol.upper() in self._index_option_symbols
    
    def get_index_option_properties(self, symbol: str) -> Dict[str, Any]:
        """
        Get properties for an index option symbol.
        
        Returns characteristics like settlement type, exercise style, and
        extended trading hours information.
        
        Args:
            symbol: The underlying symbol (e.g., "XSP")
            
        Returns:
            Dictionary with index option properties, or empty dict if not index option
        """
        if not self.is_index_option(symbol):
            return {}
        
        props = self._index_options_config.get("properties", {})
        symbol_specific = self._index_options_config.get(symbol.lower(), {})
        
        session = self._index_options_config.get("session", {})
        return {
            "symbol": symbol,
            "cash_settled": props.get("cash_settled", True),
            "european_style": props.get("european_style", True),
            "extended_hours": props.get("extended_hours", True),
            "section_1256": props.get("section_1256", True),
            "am_settled": props.get("am_settled", False),
            "market_close_pst": session.get("market_close_pst", "13:15"),
            "last_trading_day_close_pst": session.get("last_trading_day_close_pst", "13:00"),
            **symbol_specific
        }
    
    def _extract_underlying_from_option(self, option_symbol: str) -> Optional[str]:
        """
        Extract the underlying symbol from an OCC option symbol.
        
        OCC option symbols follow the format:
        [Underlying][YY][MM][DD][C/P][Strike Price * 1000]
        
        Examples:
        - XSP240119C00450000 -> XSP
        - SPY250117P00500000 -> SPY
        - AAPL240315C00175000 -> AAPL
        
        Args:
            option_symbol: The OCC option symbol
            
        Returns:
            The underlying symbol, or None if parsing fails
        """
        if not option_symbol or len(option_symbol) < 15:
            return None
        
        try:
            # OCC symbols have a date embedded at fixed positions from the end
            # The underlying is everything before the date (which starts with YYMMDD)
            # Find where the numeric date portion begins
            for i in range(len(option_symbol)):
                # Check if we've hit the date portion (6 digits followed by C or P)
                remaining = option_symbol[i:]
                if len(remaining) >= 7 and remaining[:6].isdigit() and remaining[6] in ('C', 'P'):
                    underlying = option_symbol[:i]
                    if underlying and underlying.isalpha():
                        return underlying.upper()
            
            # Fallback: check known index option symbols at start
            for idx_symbol in self._index_option_symbols:
                if option_symbol.upper().startswith(idx_symbol):
                    return idx_symbol
            
            return None
        except Exception:
            return None
    
    def _is_last_trading_day_for_option(self, option_symbol: str) -> bool:
        """
        Check if today is the last trading day for an option.
        
        For PM-settled options (like XSP), last trading day = expiration day itself.
        For AM-settled options, last trading day = 2 days before Saturday expiration.
        
        Args:
            option_symbol: The OCC option symbol containing expiration date
            
        Returns:
            True if today is the last trading day for this option
        """
        try:
            # Parse expiration date from OCC symbol
            # Format: [Underlying][YYMMDD][C/P][Strike]
            underlying = self._extract_underlying_from_option(option_symbol)
            
            for i in range(len(option_symbol)):
                remaining = option_symbol[i:]
                if len(remaining) >= 7 and remaining[:6].isdigit() and remaining[6] in ('C', 'P'):
                    date_str = remaining[:6]  # YYMMDD
                    exp_year = 2000 + int(date_str[:2])
                    exp_month = int(date_str[2:4])
                    exp_day = int(date_str[4:6])
                    exp_date = datetime(exp_year, exp_month, exp_day).date()
                    
                    # Get today's date in system timezone
                    clock = get_market_clock()
                    today = clock.now().date()
                    
                    # Get symbol-specific properties exclusively via dedicated accessor
                    # This ensures all symbol-specific overrides are honored
                    am_settled = False
                    if underlying and self.is_index_option(underlying):
                        props = self.get_index_option_properties(underlying)
                        # Use accessor's am_settled value (includes symbol-specific overrides)
                        am_settled = props.get("am_settled", False)
                    
                    # Determine last trading day based on settlement style
                    if am_settled:
                        # AM-settled (e.g., SPX monthly): last trading is Thursday
                        # Options expire Saturday, settle Friday morning, stop trading Thursday
                        # So last trading = 2 days before OCC expiration date (Saturday)
                        is_last_day = (exp_date - today).days == 2
                    else:
                        # PM-settled (XSP) and standard equity options:
                        # Last trading is expiration day itself
                        is_last_day = today == exp_date
                    
                    if is_last_day:
                        self._logger.log("last_trading_day_detected", {
                            "symbol": option_symbol,
                            "underlying": underlying,
                            "expiration": exp_date.isoformat(),
                            "today": today.isoformat(),
                            "am_settled": am_settled
                        })
                    
                    return is_last_day
            
            return True  # Fail-closed: assume last trading day if can't parse
        except Exception as e:
            self._logger.error(f"Error checking last trading day: {e}")
            return True  # Fail-closed: use earlier close time on errors
    
    def _check_spread_gate(self, bid: float, ask: float, symbol: str) -> Dict[str, Any]:
        """
        Check bid/ask spread before placing order - execution-time spread gate.
        
        Prevents trading in wide markets where fill quality would be poor.
        This is a SAFETY GATE - orders with wide spreads are rejected.
        
        Args:
            bid: Current bid price
            ask: Current ask price
            symbol: Option symbol for logging
            
        Returns:
            dict with 'approved' (bool) and 'spread_pct' (float)
        """
        if ask <= 0 or bid <= 0:
            self._logger.log("spread_gate_invalid_quote", {
                "symbol": symbol,
                "bid": bid,
                "ask": ask
            })
            return {"approved": False, "spread_pct": 999.0, "reason": "Invalid bid/ask quotes"}
        
        mid = (bid + ask) / 2
        spread_pct = ((ask - bid) / mid) * 100 if mid > 0 else 999.0
        
        approved = spread_pct <= self._max_spread_pct
        
        self._logger.log("options_spread_gate", {
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "spread_pct": round(spread_pct, 2),
            "max_spread_pct": self._max_spread_pct,
            "approved": approved
        })
        
        if not approved:
            return {
                "approved": False,
                "spread_pct": spread_pct,
                "reason": f"Spread {spread_pct:.2f}% exceeds max {self._max_spread_pct}%"
            }
        
        return {"approved": True, "spread_pct": spread_pct}
    
    def prepare_session(self) -> Dict[str, Any]:
        """
        Prepare for trading session by loading pre-market intelligence.
        
        This method should be called during pre-market (6:00-6:30 AM PST)
        to gather overnight intelligence and select the dynamic universe.
        
        Returns:
            Dictionary with preparation results:
            - intel_loaded: Whether pre-market intelligence was loaded
            - universe_size: Number of tickers in dynamic universe
            - top_opportunities: Best opportunities ranked by score
            - strategies_recommended: Strategies per ticker
        """
        results = {
            "intel_loaded": False,
            "universe_size": 0,
            "top_opportunities": [],
            "strategies_recommended": {},
            "regime": "normal",
            "regime_multiplier": 1.0
        }
        
        self._logger.log("options_prepare_session_start", {"bot_id": self.bot_id})
        
        try:
            # Load pre-market intelligence
            from ..services.premarket_intelligence import PreMarketIntelligenceService
            intel_service = PreMarketIntelligenceService()
            
            # Get cached intelligence (should already be populated by orchestrator)
            cached = intel_service.get_cached_intelligence()
            if cached and cached.is_complete:
                self._premarket_intel = cached
                results["intel_loaded"] = True
                results["regime"] = cached.market_regime
                results["regime_multiplier"] = cached.regime_multiplier
                
                # Get top opportunities for options
                top_opps = intel_service.get_ranked_opportunities(top_n=5)
                results["top_opportunities"] = [
                    {
                        "ticker": opp.ticker,
                        "score": opp.opportunity_score,
                        "strategies": opp.recommended_strategies,
                        "iv_elevated": opp.iv.is_elevated if opp.iv else False,
                        "iv_depressed": opp.iv.is_depressed if opp.iv else False,
                        "gap_pct": opp.gap.gap_pct if opp.gap else 0
                    }
                    for opp in top_opps
                ]
                
                self._logger.log("options_intel_loaded", {
                    "bot_id": self.bot_id,
                    "opportunities": len(top_opps),
                    "regime": cached.market_regime
                })
            else:
                self._logger.log("options_no_intel_cache", {"bot_id": self.bot_id})
            
            # Screen dynamic universe
            from ..services.universe_screener import DynamicUniverseScreener
            screener = DynamicUniverseScreener(asset_class="options")
            
            # Pass pre-market intel to screener if available
            premarket_data = None
            if self._premarket_intel:
                premarket_data = {
                    "tickers": {
                        ticker: {
                            "gap_pct": intel.gap.gap_pct if intel.gap else None,
                            "iv_percentile": intel.iv.iv_percentile if intel.iv else None
                        }
                        for ticker, intel in self._premarket_intel.tickers.items()
                    }
                }
            
            selection = screener.screen_universe(premarket_intel=premarket_data)
            self._dynamic_universe = selection.selected_tickers
            
            results["universe_size"] = len(self._dynamic_universe)
            
            # Determine recommended strategies per ticker
            for ticker in self._dynamic_universe:
                intel = self._premarket_intel.tickers.get(ticker) if self._premarket_intel else None
                if intel:
                    results["strategies_recommended"][ticker] = intel.recommended_strategies
                else:
                    # Default strategies based on general approach
                    results["strategies_recommended"][ticker] = ["long_call", "long_put"]
            
            # Override static tickers with dynamic universe if available
            if self._dynamic_universe:
                self.tickers = self._dynamic_universe
                self._logger.log("options_universe_updated", {
                    "bot_id": self.bot_id,
                    "old_tickers": self._config.tickers if self._config else [],
                    "new_tickers": self._dynamic_universe
                })
            
            self._session_prepared = True
            
            self._logger.log("options_prepare_session_complete", {
                "bot_id": self.bot_id,
                "results": results
            })
            
        except Exception as e:
            self._logger.error(f"Options prepare_session failed: {e}")
            results["error"] = str(e)
        
        return results
    
    def get_ticker_intelligence(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Get pre-market intelligence for a specific ticker.
        
        Args:
            ticker: Symbol to get intelligence for
            
        Returns:
            Dictionary with gap, IV, and strategy recommendations
        """
        if not self._premarket_intel:
            return None
        
        intel = self._premarket_intel.tickers.get(ticker)
        if not intel:
            return None
        
        return {
            "opportunity_score": intel.opportunity_score,
            "liquidity_score": intel.liquidity_score,
            "gap_pct": intel.gap.gap_pct if intel.gap else None,
            "gap_direction": intel.gap.direction if intel.gap else None,
            "iv_percentile": intel.iv.iv_percentile if intel.iv else None,
            "iv_elevated": intel.iv.is_elevated if intel.iv else False,
            "iv_depressed": intel.iv.is_depressed if intel.iv else False,
            "hv_iv_spread": intel.iv.hv_iv_spread if intel.iv else None,
            "recommended_strategies": intel.recommended_strategies,
            "risk_flags": intel.risk_flags,
            "events": [e.value for e in intel.events]
        }
    
    def should_use_premium_selling(self, ticker: str) -> bool:
        """
        Determine if we should use premium selling strategies for this ticker.
        
        Premium selling (credit spreads, iron condors) works best when:
        - IV is elevated (>60th percentile)
        - No major events expected
        - HV/IV spread is positive (IV overpriced)
        
        Returns:
            True if premium selling is recommended
        """
        intel = self.get_ticker_intelligence(ticker)
        if not intel:
            return False
        
        # Check IV conditions
        iv_elevated = intel.get("iv_elevated", False)
        hv_iv_spread = intel.get("hv_iv_spread", 0) or 0
        
        # Check for risky events
        events = intel.get("events", [])
        has_earnings = "earnings" in events
        
        # Premium selling favorable if IV elevated and no earnings
        return iv_elevated and hv_iv_spread > 0 and not has_earnings
    
    def should_use_premium_buying(self, ticker: str) -> bool:
        """
        Determine if we should use premium buying strategies for this ticker.
        
        Premium buying (long calls/puts, straddles) works best when:
        - IV is depressed (<30th percentile)
        - Large overnight gap (potential for continuation)
        - Earnings or major event expected
        
        Returns:
            True if premium buying is recommended
        """
        intel = self.get_ticker_intelligence(ticker)
        if not intel:
            return True  # Default to buying (no margin required)
        
        # Check IV conditions
        iv_depressed = intel.get("iv_depressed", False)
        
        # Check for gaps
        gap_pct = intel.get("gap_pct", 0) or 0
        significant_gap = abs(gap_pct) > 0.5
        
        # Check for events
        events = intel.get("events", [])
        has_earnings = "earnings" in events
        
        # Premium buying favorable if IV low or significant catalyst
        return iv_depressed or significant_gap or has_earnings
    
    def _build_strategy_configs(self) -> Dict[OptionStrategy, Dict[str, Any]]:
        """
        Build strategy configurations from YAML config.
        
        0DTE bots use tighter constraints loaded from optionsbot_0dte section.
        Standard bots use optionsbot section with wider risk parameters.
        
        Returns:
            Dictionary mapping OptionStrategy to its configuration
        """
        # Load strategy overrides from config
        bots_config = load_bots_config()
        config_key = "optionsbot_0dte" if self._is_0dte else "optionsbot"
        options_config = bots_config.get(config_key, {})
        strategies = options_config.get("strategies", {})
        exits = options_config.get("exits", {})
        
        # Base defaults - overridden by config values
        if self._is_0dte:
            # 0DTE: Tighter constraints for same-day expiration
            base_configs = {
                # BUY-SIDE STRATEGIES for 0DTE (no margin required)
                OptionStrategy.LONG_CALL: {
                    "max_cost": strategies.get("long_call", {}).get("max_cost", 2.00),
                    "min_delta": strategies.get("long_call", {}).get("min_delta", 0.35),
                    "max_delta": strategies.get("long_call", {}).get("max_delta", 0.55),
                    "profit_target": strategies.get("long_call", {}).get("profit_target", 0.25),
                    "stop_loss": exits.get("stop_loss_pct", 50) / 100,
                    "market_bias": [MarketRegime.BULLISH, MarketRegime.HIGH_VOLATILITY],
                    "max_dte": 0
                },
                OptionStrategy.LONG_PUT: {
                    "max_cost": strategies.get("long_put", {}).get("max_cost", 2.00),
                    "min_delta": strategies.get("long_put", {}).get("min_delta", 0.35),
                    "max_delta": strategies.get("long_put", {}).get("max_delta", 0.55),
                    "profit_target": strategies.get("long_put", {}).get("profit_target", 0.25),
                    "stop_loss": exits.get("stop_loss_pct", 50) / 100,
                    "market_bias": [MarketRegime.BEARISH, MarketRegime.HIGH_VOLATILITY],
                    "max_dte": 0
                },
                OptionStrategy.STRADDLE: {
                    "min_cost": 1.00,
                    "max_cost": strategies.get("straddle", {}).get("max_cost", 4.00),
                    "delta_range": (-0.55, -0.45),
                    "profit_target": strategies.get("straddle", {}).get("profit_target", 0.20),
                    "stop_loss": 0.40,
                    "market_bias": [MarketRegime.HIGH_VOLATILITY],
                    "max_dte": 0
                },
                # SELL-SIDE STRATEGIES (disabled by default for 0DTE)
                OptionStrategy.BULL_PUT_SPREAD: {
                    "min_credit": strategies.get("bull_put_spread", {}).get("min_credit", 0.25),
                    "max_credit": strategies.get("bull_put_spread", {}).get("max_credit", 1.50),
                    "spread_width_range": (2, 5),  # Narrower spreads for 0DTE
                    "short_delta_range": (-0.25, -0.10),
                    "long_delta_range": (-0.10, -0.03),
                    "profit_target": strategies.get("bull_put_spread", {}).get("profit_target", 0.50),
                    "stop_loss": exits.get("stop_loss_pct", 100) / 100,
                    "market_bias": [MarketRegime.BULLISH, MarketRegime.NEUTRAL],
                    "max_dte": 0  # 0DTE only
                },
                OptionStrategy.BEAR_CALL_SPREAD: {
                    "min_credit": strategies.get("bear_call_spread", {}).get("min_credit", 0.25),
                    "max_credit": strategies.get("bear_call_spread", {}).get("max_credit", 1.50),
                    "spread_width_range": (2, 5),
                    "short_delta_range": (0.10, 0.25),
                    "long_delta_range": (0.03, 0.10),
                    "profit_target": strategies.get("bear_call_spread", {}).get("profit_target", 0.50),
                    "stop_loss": exits.get("stop_loss_pct", 100) / 100,
                    "market_bias": [MarketRegime.BEARISH, MarketRegime.NEUTRAL],
                    "max_dte": 0
                },
                OptionStrategy.IRON_CONDOR: {
                    "min_credit": strategies.get("iron_condor", {}).get("min_credit", 0.50),
                    "max_credit": strategies.get("iron_condor", {}).get("max_credit", 2.00),
                    "spread_width_range": (3, 8),
                    "short_delta_range": (-0.15, 0.15),
                    "profit_target": strategies.get("iron_condor", {}).get("profit_target", 0.25),
                    "stop_loss": 1.5,
                    "market_bias": [MarketRegime.NEUTRAL, MarketRegime.LOW_VOLATILITY],
                    "max_dte": 0
                },
                # DEBIT SPREADS - Directional plays with defined risk (replaces naked shorts)
                OptionStrategy.BULL_CALL_SPREAD: {
                    "max_debit": strategies.get("bull_call_spread", {}).get("max_debit", 1.50),
                    "spread_width_range": (2, 5),
                    "long_delta_range": (0.45, 0.60),   # Buy ITM/ATM call
                    "short_delta_range": (0.25, 0.40),  # Sell OTM call
                    "profit_target": strategies.get("bull_call_spread", {}).get("profit_target", 0.50),
                    "stop_loss": 0.50,
                    "market_bias": [MarketRegime.BULLISH],
                    "max_dte": 0
                },
                OptionStrategy.BEAR_PUT_SPREAD: {
                    "max_debit": strategies.get("bear_put_spread", {}).get("max_debit", 1.50),
                    "spread_width_range": (2, 5),
                    "long_delta_range": (-0.60, -0.45),  # Buy ITM/ATM put
                    "short_delta_range": (-0.40, -0.25), # Sell OTM put
                    "profit_target": strategies.get("bear_put_spread", {}).get("profit_target", 0.50),
                    "stop_loss": 0.50,
                    "market_bias": [MarketRegime.BEARISH],
                    "max_dte": 0
                },
                OptionStrategy.CALENDAR_SPREAD: {
                    "max_debit": strategies.get("calendar_spread", {}).get("max_debit", 1.00),
                    "delta_range": (-0.55, 0.55),  # ATM options for both legs
                    "short_dte": 0,   # Sell same-day for 0DTE
                    "long_dte": 7,    # Buy weekly for protection
                    "profit_target": strategies.get("calendar_spread", {}).get("profit_target", 0.25),
                    "stop_loss": 0.50,
                    "market_bias": [MarketRegime.NEUTRAL, MarketRegime.LOW_VOLATILITY],
                    "max_dte": 0
                }
            }
        else:
            # Standard: Wider constraints for multi-day positions
            base_configs = {
                # BUY-SIDE STRATEGIES (no margin required, just pay premium)
                OptionStrategy.LONG_CALL: {
                    "max_cost": strategies.get("long_call", {}).get("max_cost", 3.00),
                    "min_delta": strategies.get("long_call", {}).get("min_delta", 0.30),
                    "max_delta": strategies.get("long_call", {}).get("max_delta", 0.60),
                    "profit_target": strategies.get("long_call", {}).get("profit_target", 0.30),
                    "stop_loss": exits.get("stop_loss_pct", 50) / 100,
                    "market_bias": [MarketRegime.BULLISH, MarketRegime.HIGH_VOLATILITY],
                    "max_dte": 45
                },
                OptionStrategy.LONG_PUT: {
                    "max_cost": strategies.get("long_put", {}).get("max_cost", 3.00),
                    "min_delta": strategies.get("long_put", {}).get("min_delta", 0.30),
                    "max_delta": strategies.get("long_put", {}).get("max_delta", 0.60),
                    "profit_target": strategies.get("long_put", {}).get("profit_target", 0.30),
                    "stop_loss": exits.get("stop_loss_pct", 50) / 100,
                    "market_bias": [MarketRegime.BEARISH, MarketRegime.HIGH_VOLATILITY],
                    "max_dte": 45
                },
                OptionStrategy.STRADDLE: {
                    "min_cost": 2.00,
                    "max_cost": strategies.get("straddle", {}).get("max_cost", 5.00),
                    "delta_range": (-0.55, -0.45),
                    "profit_target": strategies.get("straddle", {}).get("profit_target", 0.25),
                    "stop_loss": 0.50,
                    "market_bias": [MarketRegime.HIGH_VOLATILITY],
                    "max_dte": strategies.get("straddle", {}).get("max_dte", 45)
                },
                # SELL-SIDE STRATEGIES (require margin/cash securing)
                OptionStrategy.BULL_PUT_SPREAD: {
                    "min_credit": strategies.get("bull_put_spread", {}).get("min_credit", 0.50),
                    "max_credit": strategies.get("bull_put_spread", {}).get("max_credit", 3.00),
                    "spread_width_range": (3, 10),
                    "short_delta_range": (-0.30, -0.15),
                    "long_delta_range": (-0.15, -0.05),
                    "profit_target": strategies.get("bull_put_spread", {}).get("profit_target", 0.50),
                    "stop_loss": exits.get("stop_loss_pct", 200) / 100,
                    "market_bias": [MarketRegime.BULLISH, MarketRegime.NEUTRAL],
                    "max_dte": strategies.get("bull_put_spread", {}).get("max_dte", 45)
                },
                OptionStrategy.BEAR_CALL_SPREAD: {
                    "min_credit": strategies.get("bear_call_spread", {}).get("min_credit", 0.50),
                    "max_credit": strategies.get("bear_call_spread", {}).get("max_credit", 3.00),
                    "spread_width_range": (3, 10),
                    "short_delta_range": (0.15, 0.30),
                    "long_delta_range": (0.05, 0.15),
                    "profit_target": strategies.get("bear_call_spread", {}).get("profit_target", 0.50),
                    "stop_loss": exits.get("stop_loss_pct", 200) / 100,
                    "market_bias": [MarketRegime.BEARISH, MarketRegime.NEUTRAL],
                    "max_dte": strategies.get("bear_call_spread", {}).get("max_dte", 45)
                },
                OptionStrategy.IRON_CONDOR: {
                    "min_credit": strategies.get("iron_condor", {}).get("min_credit", 1.00),
                    "max_credit": strategies.get("iron_condor", {}).get("max_credit", 4.00),
                    "spread_width_range": (5, 15),
                    "short_delta_range": (-0.20, 0.20),
                    "profit_target": strategies.get("iron_condor", {}).get("profit_target", 0.25),
                    "stop_loss": 2.5,
                    "market_bias": [MarketRegime.NEUTRAL, MarketRegime.LOW_VOLATILITY],
                    "max_dte": strategies.get("iron_condor", {}).get("max_dte", 60)
                },
                # DEBIT SPREADS - Directional plays with defined risk (replaces naked shorts)
                OptionStrategy.BULL_CALL_SPREAD: {
                    "max_debit": strategies.get("bull_call_spread", {}).get("max_debit", 2.50),
                    "spread_width_range": (3, 10),
                    "long_delta_range": (0.50, 0.70),   # Buy ITM/ATM call
                    "short_delta_range": (0.30, 0.45),  # Sell OTM call
                    "profit_target": strategies.get("bull_call_spread", {}).get("profit_target", 0.60),
                    "stop_loss": 0.50,
                    "market_bias": [MarketRegime.BULLISH],
                    "max_dte": strategies.get("bull_call_spread", {}).get("max_dte", 45)
                },
                OptionStrategy.BEAR_PUT_SPREAD: {
                    "max_debit": strategies.get("bear_put_spread", {}).get("max_debit", 2.50),
                    "spread_width_range": (3, 10),
                    "long_delta_range": (-0.70, -0.50),  # Buy ITM/ATM put
                    "short_delta_range": (-0.45, -0.30), # Sell OTM put
                    "profit_target": strategies.get("bear_put_spread", {}).get("profit_target", 0.60),
                    "stop_loss": 0.50,
                    "market_bias": [MarketRegime.BEARISH],
                    "max_dte": strategies.get("bear_put_spread", {}).get("max_dte", 45)
                },
                OptionStrategy.CALENDAR_SPREAD: {
                    "max_debit": strategies.get("calendar_spread", {}).get("max_debit", 2.00),
                    "delta_range": (-0.55, 0.55),  # ATM options for both legs
                    "short_dte": strategies.get("calendar_spread", {}).get("short_dte", 7),
                    "long_dte": strategies.get("calendar_spread", {}).get("long_dte", 30),
                    "profit_target": strategies.get("calendar_spread", {}).get("profit_target", 0.30),
                    "stop_loss": 0.50,
                    "market_bias": [MarketRegime.NEUTRAL, MarketRegime.LOW_VOLATILITY],
                    "max_dte": strategies.get("calendar_spread", {}).get("max_dte", 60)
                }
            }
        
        self._logger.log("strategy_configs_built", {
            "is_0dte": self._is_0dte,
            "config_key": config_key
        })
        
        return base_configs
    
    # =========================================================================
    # PORTFOLIO GREEKS - Track aggregate risk exposure across all positions
    # =========================================================================
    
    def get_portfolio_greeks(self) -> Dict[str, float]:
        """
        Calculate aggregate Greeks across all options positions.
        
        Production-level: Sums up delta, gamma, theta, vega, rho across
        all open options positions for total portfolio risk exposure.
        
        Returns:
            Dictionary with total Greeks:
            - total_delta: Net directional exposure (shares equivalent)
            - total_gamma: Rate of delta change (acceleration risk)
            - total_theta: Daily time decay (positive = collecting, negative = paying)
            - total_vega: Volatility exposure (IV sensitivity)
            - total_rho: Interest rate sensitivity
        """
        greeks = {
            "total_delta": 0.0,
            "total_gamma": 0.0,
            "total_theta": 0.0,
            "total_vega": 0.0,
            "total_rho": 0.0,
            "position_count": 0
        }
        
        try:
            # Get all options positions
            positions = self._get_options_positions()
            
            for position in positions:
                # Get current snapshot with Greeks for this position
                snapshot = self._alpaca.get_option_snapshot(position.symbol)
                
                if snapshot:
                    qty = abs(float(position.qty))
                    sign = 1 if float(position.qty) > 0 else -1  # Long vs short
                    multiplier = 100  # Options contract multiplier
                    
                    # Get underlying price for delta-dollar calculation
                    underlying_price = snapshot.get("underlying_price", 0)
                    if not underlying_price:
                        # Extract underlying from option symbol and get price
                        underlying = self._extract_underlying_from_option(position.symbol)
                        if underlying:
                            try:
                                quote = self._alpaca.get_latest_quote(underlying, "stock")
                                underlying_price = float(quote.get("bid", 0) or quote.get("ask", 0) or 500)
                            except Exception:
                                underlying_price = 500  # Default fallback
                    
                    # Aggregate Greeks (multiply by qty and contract multiplier)
                    position_delta = snapshot.get("delta", 0) * qty * multiplier * sign
                    greeks["total_delta"] += position_delta
                    greeks["total_gamma"] += snapshot.get("gamma", 0) * qty * multiplier * sign
                    greeks["total_theta"] += snapshot.get("theta", 0) * qty * multiplier * sign
                    greeks["total_vega"] += snapshot.get("vega", 0) * qty * multiplier * sign
                    greeks["total_rho"] += snapshot.get("rho", 0) * qty * multiplier * sign
                    greeks["position_count"] += 1
                    
                    # Compute delta-dollar exposure per position
                    greeks["delta_dollar"] = greeks.get("delta_dollar", 0) + abs(position_delta) * underlying_price
            
            # Update cached portfolio Greeks
            self._portfolio_greeks = greeks
            self._portfolio_greeks["last_updated"] = get_market_clock().now().isoformat()
            
            self._logger.log("portfolio_greeks_calculated", greeks)
            
        except Exception as e:
            self._logger.warn(f"Portfolio Greeks calculation failed: {e}")
        
        return greeks
    
    def check_margin_requirements(self, proposed_trade: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check if account has sufficient margin for a proposed options trade.
        
        Production-level: Validates buying power against required margin
        before placing trades. Different strategies have different requirements.
        
        Args:
            proposed_trade: Dictionary with trade details:
                - strategy: OptionStrategy value
                - contracts: Number of contracts
                - max_loss: Maximum loss per contract
                
        Returns:
            Dictionary with:
            - approved: Boolean if trade can proceed
            - required_margin: Dollar amount required
            - available_margin: Current buying power
            - reason: Explanation if denied
        """
        result = {
            "approved": False,
            "required_margin": 0.0,
            "available_margin": 0.0,
            "reason": ""
        }
        
        try:
            # Get current account data
            account = self._alpaca.get_account()
            available_margin = account.buying_power
            result["available_margin"] = available_margin
            
            # Calculate required margin based on strategy type
            strategy = proposed_trade.get("strategy")
            contracts = proposed_trade.get("contracts", 1)
            max_loss = proposed_trade.get("max_loss", 0)
            
            # Credit spreads: margin = max loss (spread width - credit) * contracts * 100
            # Debit spreads: margin = cost of spread * contracts * 100
            required_margin = max_loss * contracts * 100
            
            # Add 10% buffer for safety
            required_margin *= 1.10
            result["required_margin"] = required_margin
            
            # Check if we have enough margin
            if available_margin >= required_margin:
                result["approved"] = True
                result["reason"] = "Sufficient margin available"
            else:
                result["reason"] = f"Insufficient margin: need ${required_margin:.2f}, have ${available_margin:.2f}"
            
            self._logger.log("margin_check", result)
            
        except Exception as e:
            result["reason"] = f"Margin check failed: {e}"
            self._logger.error(result["reason"])
        
        return result
    
    def _check_buying_power_for_debit(self, total_cost: float) -> Dict[str, Any]:
        """
        Check if account has sufficient buying power for a debit (long option) trade.
        
        For long options, we need to pay the full premium upfront.
        This is simpler than credit spread margin requirements.
        
        Args:
            total_cost: Total dollar cost of the trade (premium * contracts * 100)
            
        Returns:
            Dictionary with:
            - approved: Boolean if trade can proceed
            - required: Dollar amount required
            - available: Current buying power
            - reason: Explanation
        """
        result = {
            "approved": False,
            "required": total_cost,
            "available": 0.0,
            "reason": ""
        }
        
        try:
            # Get current account data
            account = self._alpaca.get_account()
            
            # Safely convert monetary values (may be str, Decimal, or float)
            def safe_float(val, default=0.0) -> float:
                if val is None:
                    return default
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return default
            
            # For options, use options_buying_power if available
            options_bp = getattr(account, 'options_buying_power', None)
            if options_bp is not None:
                available = safe_float(options_bp)
            else:
                # Fallback to regular buying power
                available = safe_float(account.buying_power)
            
            result["available"] = available
            
            # Add 5% buffer for slippage/fills above ask
            required_with_buffer = total_cost * 1.05
            
            # Check if we have enough buying power
            if available >= required_with_buffer:
                result["approved"] = True
                result["reason"] = f"Sufficient buying power: ${available:.2f} >= ${required_with_buffer:.2f}"
            else:
                result["reason"] = f"Need ${required_with_buffer:.2f}, have ${available:.2f}"
            
            self._logger.log("debit_buying_power_check", result)
            
        except Exception as e:
            result["reason"] = f"Buying power check failed: {e}"
            self._logger.error(result["reason"])
        
        return result
    
    def _load_config(self) -> Optional[OptionsConfig]:
        """
        Load bot configuration from config/bots.yaml.
        
        Supports loading from 'optionsbot' (standard) or 'optionsbot_0dte' (0DTE)
        sections based on the bot_id. If bot_id contains '0dte', loads 0DTE config.
        
        Returns:
            OptionsConfig object with all settings, or None if not found
        """
        try:
            # Load the full bots configuration file
            bots_config = load_bots_config()
            
            # Determine which config section to use based on bot_id
            # 0DTE bots use 'optionsbot_0dte' section, others use 'optionsbot'
            if "0dte" in self.bot_id.lower():
                config_key = "optionsbot_0dte"
            else:
                config_key = "optionsbot"
            
            # Get appropriate optionsbot section
            options_config = bots_config.get(config_key, {})
            
            # Return None if optionsbot not configured
            if not options_config:
                self._logger.warn(f"No {config_key} config found in bots.yaml")
                return None
            
            # Extract session timing settings
            session = options_config.get("session", {})
            
            # Extract risk management settings
            risk = options_config.get("risk", {})
            trailing = risk.get("trailing_stop", {})
            
            # Extract exit condition settings
            exits = options_config.get("exits", {})
            
            # Extract chain rules for option selection - MODE-BASED DTE RULES
            chain_rules = options_config.get("chain_rules", {})
            
            # Get current mode (swing or short_term) and apply mode-specific DTE rules
            default_mode = options_config.get("default_mode", "swing")
            modes = chain_rules.get("modes", {})
            current_mode_rules = modes.get(default_mode, {})
            
            # Apply mode-specific DTE if available, otherwise use legacy chain_rules
            if current_mode_rules:
                effective_dte_min = current_mode_rules.get("dte_min", chain_rules.get("dte_min", 7))
                effective_dte_max = current_mode_rules.get("dte_max", chain_rules.get("dte_max", 60))
                size_multiplier = current_mode_rules.get("size_multiplier", 1.0)
                self._logger.log("optionsbot_mode_selection", {
                    "mode": default_mode,
                    "dte_min": effective_dte_min,
                    "dte_max": effective_dte_max,
                    "size_multiplier": size_multiplier
                })
            else:
                effective_dte_min = chain_rules.get("dte_min", 7)
                effective_dte_max = chain_rules.get("dte_max", 60)
            
            # Extract strategy enable flags
            strategies = options_config.get("strategies", {})
            
            # Extract hail mary configuration
            hail_mary_cfg = options_config.get("hail_mary", {})
            
            # Build and return the configuration object
            # All trailing stop fields have defaults in OptionsConfig dataclass
            return OptionsConfig(
                bot_id=options_config.get("bot_id", self.bot_id),
                enabled=options_config.get("enabled", False),
                tickers=options_config.get("tickers", ["SPY", "QQQ"]),
                trade_start=session.get("trade_start", "06:40"),
                trade_end=session.get("trade_end", "12:30"),
                manage_until=session.get("manage_until", "13:00"),
                max_trades_per_day=risk.get("max_trades_per_day", 3),
                max_concurrent_positions=risk.get("max_concurrent_positions", 3),
                max_position_size_usd=risk.get("max_position_size_usd", 500.0),
                profit_target_usd=risk.get("profit_target_usd", 100.0),
                take_profit_pct=exits.get("take_profit_pct", 50),
                stop_loss_pct=exits.get("stop_loss_pct", 200),
                time_stop_minutes=exits.get("time_stop_minutes", 240),
                flatten_before_close_min=exits.get("flatten_before_close_minutes", 30),
                dte_min=effective_dte_min,
                dte_max=effective_dte_max,
                delta_min=chain_rules.get("delta_min", 0.10),
                delta_max=chain_rules.get("delta_max", 0.70),
                min_volume=chain_rules.get("min_volume", 50),
                min_open_interest=chain_rules.get("min_open_interest", 100),
                trailing_stop_enabled=trailing.get("enabled", False),
                trailing_stop_mode=trailing.get("mode", "percent"),
                trailing_stop_value=trailing.get("value", 2.0),
                trailing_activation_pct=trailing.get("activation_profit_pct", 0.15),
                trailing_update_only_if_improves=trailing.get("update_only_if_improves", True),
                trailing_epsilon_pct=trailing.get("epsilon_pct", 0.05),
                trailing_exit_order_type=trailing.get("exit_order", {}).get("type", "market"),
                # Buy-side strategies (no margin required) - prioritized
                long_call_enabled=strategies.get("long_call", {}).get("enabled", True),
                long_put_enabled=strategies.get("long_put", {}).get("enabled", True),
                straddle_enabled=strategies.get("straddle", {}).get("enabled", True),
                # Sell-side strategies (require margin/cash securing)
                bull_put_spread_enabled=strategies.get("bull_put_spread", {}).get("enabled", False),
                bear_call_spread_enabled=strategies.get("bear_call_spread", {}).get("enabled", False),
                iron_condor_enabled=strategies.get("iron_condor", {}).get("enabled", False),
                # Exit delegation control
                delegate_exits_to_exitbot=options_config.get("delegate_exits_to_exitbot", True),
                # CRITICAL: dry_run control - must load from config, default to True for safety
                dry_run=options_config.get("dry_run", True),
                # Strategy preference flag
                prefer_defined_risk=options_config.get("prefer_defined_risk", True),
                # Iron Condor first preference - default to Iron Condor before other strategies
                prefer_iron_condor_first=options_config.get("prefer_iron_condor_first", True),
                # Hail Mary configuration
                hail_mary_enabled=hail_mary_cfg.get("enabled", False),
                hail_mary_max_trades_per_day=hail_mary_cfg.get("max_trades_per_day", 2),
                hail_mary_max_risk_usd=hail_mary_cfg.get("max_risk_per_trade_usd", 300.0),
                hail_mary_min_risk_usd=hail_mary_cfg.get("min_risk_per_trade_usd", 50.0),
                hail_mary_max_premium=hail_mary_cfg.get("max_premium", 3.00),
                hail_mary_min_premium=hail_mary_cfg.get("min_premium", 0.05),
                hail_mary_max_spread=hail_mary_cfg.get("max_spread", 0.50),
                hail_mary_dte_min=hail_mary_cfg.get("dte_min", 0),
                hail_mary_dte_max=hail_mary_cfg.get("dte_max", 7),
                hail_mary_strike_otm_pct=hail_mary_cfg.get("strike_otm_pct", 3.0),
                hail_mary_min_delta=hail_mary_cfg.get("min_delta", 0.01),
                hail_mary_max_delta=hail_mary_cfg.get("max_delta", 0.30),
                hail_mary_require_momentum=hail_mary_cfg.get("require_momentum_alignment", True),
                hail_mary_min_stock_change=hail_mary_cfg.get("min_stock_change_pct", 0.3),
                hail_mary_tickers=hail_mary_cfg.get("tickers", None),
                hail_mary_use_dynamic_universe=hail_mary_cfg.get("use_dynamic_universe", True),
                hail_mary_dynamic_max_tickers=hail_mary_cfg.get("dynamic_max_tickers", 20),
                hail_mary_dynamic_min_score=hail_mary_cfg.get("dynamic_min_score", 30.0),
                hail_mary_profit_target_mult=hail_mary_cfg.get("profit_target_multiplier", 5.0),
                hail_mary_time_exit_days=hail_mary_cfg.get("time_exit_days_before_expiry", 1),
                hail_mary_use_exitbot=hail_mary_cfg.get("use_exitbot", False),
                hail_mary_tiered_exits=hail_mary_cfg.get("tiered_exits", True),
                hail_mary_tier1_mult=hail_mary_cfg.get("tier1_multiplier", 3.0),
                hail_mary_tier1_pct=hail_mary_cfg.get("tier1_sell_pct", 50.0),
                hail_mary_tier2_mult=hail_mary_cfg.get("tier2_multiplier", 5.0),
                hail_mary_tier2_pct=hail_mary_cfg.get("tier2_sell_pct", 25.0),
                hail_mary_runner_mult=hail_mary_cfg.get("runner_multiplier", 10.0),
                hail_mary_block_near_earnings=hail_mary_cfg.get("block_near_earnings", True),
                hail_mary_earnings_buffer_days=hail_mary_cfg.get("earnings_buffer_days", 3),
                hail_mary_use_vwap_posture=hail_mary_cfg.get("use_vwap_posture", True)
            )
            
        except Exception as e:
            # Log error but don't crash - bot will use defaults
            self._logger.error(f"Failed to load options config: {e}")
            return None
    
    def execute(self, max_daily_loss: float, halt_new_trades: bool = False) -> Dict[str, Any]:
        """
        Execute one iteration of the options trading strategy.
        
        This is the main entry point called by the orchestrator every loop.
        It manages existing positions first, then looks for new entry opportunities.
        
        Args:
            max_daily_loss: Maximum dollar amount this bot can lose today
                           (allocated by PortfolioBot based on account equity)
            halt_new_trades: If True, only manage existing positions, no new entries
        
        Returns:
            Dictionary containing execution results:
            - trades_attempted: Number of new trades attempted
            - positions_managed: Number of existing positions checked
            - strategies_analyzed: Number of strategies evaluated
            - market_analysis: Analysis results for each ticker
            - errors: List of any errors encountered
        """
        # Initialize results dictionary to track execution outcomes
        results = {
            "trades_attempted": 0,
            "positions_managed": 0,
            "strategies_analyzed": 0,
            "market_analysis": {},
            "errors": []
        }
        
        # GUARD: Skip entirely if bot is disabled in config
        if not self._config or not self._config.enabled:
            self._logger.log("options_bot_disabled", {"bot_id": self.bot_id})
            return results
        
        try:
            # STEP 1: Check if we're in trading hours
            # Options have specific trading hours we must respect
            if not self._is_trading_hours():
                self._logger.log("options_bot_outside_hours", {
                    "bot_id": self.bot_id,
                    "current_time": get_market_clock().now().strftime("%H:%M"),
                    "trade_window": f"{self._config.trade_start}-{self._config.trade_end}"
                })
                # Still manage positions even outside trading hours
                return self._manage_only_mode(results)
            
            # STEP 2: Check risk limits before proceeding
            # Verify we haven't exceeded daily trade limits
            if not self._check_risk_limits():
                self._logger.log("options_risk_limit_reached", {"bot_id": self.bot_id})
                return self._manage_only_mode(results)
            
            # STEP 3: Manage existing positions first
            # Always prioritize protecting existing capital
            positions = self._get_options_positions()
            for position in positions:
                try:
                    self._manage_position(position)
                    results["positions_managed"] += 1
                except Exception as e:
                    results["errors"].append(f"Position management {position.symbol}: {e}")
            
            # GUARD: Check global kill switch - block new entries but allow position management above
            killswitch = get_killswitch_service()
            ks_allowed, ks_reason = killswitch.is_entry_allowed("options")
            if not ks_allowed:
                self._logger.log("options_killswitch_blocked", {
                    "bot_id": self.bot_id,
                    "reason": ks_reason
                })
                return results

            # SESSION PROTECTION CHECK — block entries if target locked (with freeroll)
            try:
                from ..risk.session_protection import get_session_protection
                sp = get_session_protection()
                if sp.is_target_locked() and (not sp.is_freeroll_available()):
                    sp_block, sp_reason = sp.should_block_new_trade(quality_score=0.0)
                    if sp_block:
                        if not sp.should_throttle_message("options_block"):
                            self._logger.log("options_session_protection_block", {
                                "bot_id": self.bot_id,
                                "reason": sp_reason
                            })
                            print(f"  [OPTIONS] Entry blocked: {sp_reason}")
                        return results
            except Exception as sp_err:
                self._logger.warn(f"OptionsBot session protection check failed (fail-open): {sp_err}")

            # STEP 4: Check if we can open new positions
            # Must be under concurrent position limit and not halted
            current_positions = len(positions)
            
            # Check for pending orders to prevent duplicate order placement
            open_orders = self._alpaca.get_open_orders()
            pending_option_symbols = {o.get("symbol", "") for o in open_orders if len(o.get("symbol", "")) > 10}
            
            can_open_new = (
                current_positions < self._config.max_concurrent_positions
                and not halt_new_trades
                and self._get_trades_today() < self._config.max_trades_per_day
            )
            
            if not can_open_new:
                self._logger.log("options_no_new_trades", {
                    "current_positions": current_positions,
                    "max_concurrent": self._config.max_concurrent_positions,
                    "halt_new_trades": halt_new_trades,
                    "trades_today": self._get_trades_today()
                })
                return results
            
            # STEP 5: Use Strategy System if enabled (PDF rules-based strategies)
            # This is an OPTIONAL path - when disabled, uses existing behavior
            if self._use_strategy_system:
                strategy_results = self._execute_via_strategy_system(max_daily_loss, results)
                if strategy_results:
                    # Hail Mary runs AFTER strategy system — it operates independently
                    # with its own trade limits, tickers, and exit logic
                    try:
                        strategy_results = self._manage_hail_mary_exits(strategy_results)
                    except Exception as hm_exit_err:
                        self._logger.warn(f"Hail mary exit management error: {hm_exit_err}")
                    try:
                        strategy_results = self._execute_hail_mary(strategy_results)
                    except Exception as hm_err:
                        self._logger.warn(f"Hail mary execution error: {hm_err}")
                        strategy_results["errors"].append(f"Hail mary error: {hm_err}")
                    return strategy_results
            
            # STEP 6: Analyze market and look for opportunities (legacy path)
            # Limit to 2 new trades per execution cycle to avoid over-trading
            max_new_trades = min(2, self._config.max_trades_per_day - self._get_trades_today())
            
            # RISK INTEGRATION GATE - Check for VIX crisis or correlation halt
            try:
                risk_integration = get_risk_integration()
                risk_eval = risk_integration.evaluate_entry(
                    symbol="MARKET",
                    bot_name=self.bot_id,
                    proposed_size_usd=max_daily_loss,
                    is_bullish=True
                )
                if risk_eval and risk_eval.action in (RiskAction.SKIP_ENTRY, RiskAction.HALT_TRADING):
                    self._logger.log("options_risk_gate_blocked", {
                        "action": risk_eval.action.value,
                        "reason": risk_eval.reason,
                        "gate_details": risk_eval.gate_details
                    })
                    return results
                elif risk_eval and risk_eval.action == RiskAction.REDUCE_SIZE:
                    self._logger.log("options_risk_gate_reduce", {
                        "action": risk_eval.action.value,
                        "size_multiplier": risk_eval.size_multiplier,
                        "reason": risk_eval.reason
                    })
                    max_daily_loss = max_daily_loss * risk_eval.size_multiplier
            except Exception as ri_err:
                self._logger.error(f"Risk integration check failed: {ri_err}")
            
            # Import UniverseGuard for premarket selection enforcement
            from ..risk.universe_guard import get_universe_guard
            guard = get_universe_guard()
            
            for ticker in self.tickers:
                # Stop if we've reached our new trade limit for this cycle
                if results["trades_attempted"] >= max_new_trades:
                    break
                
                # UNIVERSE GUARD CHECK - Block if symbol not in premarket selection
                if not guard.is_symbol_allowed(ticker, bot_id=self.bot_id):
                    self._logger.log("options_entry_blocked_universe", {
                        "ticker": ticker,
                        "reason": "not_in_selected_universe"
                    })
                    continue
                
                # Skip if we already have a pending order for this ticker
                has_pending = any(ticker in sym for sym in pending_option_symbols)
                if has_pending:
                    self._logger.log("options_skip_pending_order", {
                        "bot_id": self.bot_id,
                        "ticker": ticker,
                        "reason": "pending_order_exists"
                    })
                    continue
                
                try:
                    # Analyze market conditions for this ticker
                    analysis = self._analyze_market_conditions(ticker)
                    results["market_analysis"][ticker] = analysis
                    results["strategies_analyzed"] += 1
                    
                    # Select optimal strategy based on market conditions
                    best_strategy = self._select_optimal_strategy(analysis)
                    
                    if best_strategy:
                        # Apply regime-based position size adjustment
                        # Reduces position size in high volatility environments
                        regime_multiplier = analysis.get("position_size_multiplier", 1.0)
                        adjusted_max_loss = max_daily_loss * regime_multiplier
                        
                        # Skip if regime says halt new entries (extreme fear)
                        if analysis.get("halt_new_entries", False):
                            self._logger.log("options_halt_new_entries", {
                                "ticker": ticker,
                                "reason": "extreme_volatility_regime"
                            })
                            continue
                        
                        self._logger.log("position_size_adjusted", {
                            "ticker": ticker,
                            "original_max_loss": max_daily_loss,
                            "regime_multiplier": regime_multiplier,
                            "adjusted_max_loss": adjusted_max_loss
                        })
                        
                        # Execute the selected strategy with adjusted sizing
                        trade_result = self._execute_strategy(
                            ticker, best_strategy, analysis, adjusted_max_loss
                        )
                        
                        if trade_result.get("success"):
                            results["trades_attempted"] += 1
                            self._increment_trades_today()
                            self._logger.log("options_trade_executed", {
                                "ticker": ticker,
                                "strategy": best_strategy.value,
                                "expected_profit": trade_result.get("expected_profit", 0),
                                "risk": trade_result.get("risk", 0)
                            })
                        else:
                            error_msg = trade_result.get("error", "Unknown error")
                            results["errors"].append(f"{ticker} {best_strategy.value}: {error_msg}")
                            
                except Exception as e:
                    results["errors"].append(f"Analysis failed for {ticker}: {e}")
            
            # STEP 7a: Hail Mary EXIT management - check open positions for profit target or time exit
            # Runs BEFORE scanning for new entries so we free up capital first
            try:
                results = self._manage_hail_mary_exits(results)
            except Exception as hm_exit_err:
                self._logger.warn(f"Hail mary exit management error: {hm_exit_err}")
            
            # STEP 7b: Hail Mary ENTRY scan - cheap near-term options with massive upside
            # Runs independently of regular strategies with its own trade limits
            try:
                results = self._execute_hail_mary(results)
            except Exception as hm_err:
                self._logger.warn(f"Hail mary execution error: {hm_err}")
                results["errors"].append(f"Hail mary error: {hm_err}")
            
            # Log completion summary
            self._logger.log("options_bot_cycle_complete", {
                "bot_id": self.bot_id,
                "trades_attempted": results["trades_attempted"],
                "positions_managed": results["positions_managed"],
                "strategies_analyzed": results["strategies_analyzed"],
                "error_count": len(results["errors"])
            })
            
        except Exception as e:
            # Fail-closed: Log the error and return partial results
            self._logger.error(f"Options bot execution failed: {e}")
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
    
    def _manage_only_mode(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run in manage-only mode (no new trades).
        Used outside trading hours or when risk limits reached.
        
        Args:
            results: Results dictionary to update
            
        Returns:
            Updated results dictionary
        """
        try:
            positions = self._get_options_positions()
            for position in positions:
                try:
                    self._manage_position(position)
                    results["positions_managed"] += 1
                except Exception as e:
                    results["errors"].append(f"Position management {position.symbol}: {e}")
        except Exception as e:
            results["errors"].append(f"Manage-only mode failed: {e}")
        
        return results
    
    def _execute_via_strategy_system(
        self, max_daily_loss: float, results: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Execute trading via the Strategy System (PDF rules-based strategies).
        
        This is the NEW execution path that uses StrategyRunner when
        use_strategy_system is enabled in config. The existing behavior
        is unchanged when this flag is disabled.
        
        Pipeline enforcement:
        1. Kill-switch check (per-strategy drawdown circuit breaker)
        2. Earnings filter (policy enforcement)
        3. Signal rule validation (deterministic rule checks)
        4. Backtest gate (performance threshold enforcement)
        5. Option contract selection (delta/DTE matching)
        6. Order execution with bracket orders
        
        Args:
            max_daily_loss: Maximum dollar amount this bot can lose today
            results: Results dictionary to update
            
        Returns:
            Updated results dictionary if executed, None to fall back to legacy path
        """
        try:
            # UNIVERSE GUARD CHECK - Filter tickers to only premarket-selected symbols
            from ..risk.universe_guard import get_universe_guard
            guard = get_universe_guard()
            allowed_tickers = [t for t in self.tickers if guard.is_symbol_allowed(t, bot_id=self.bot_id)]
            
            if not allowed_tickers:
                self._logger.log("strategy_system_no_allowed_tickers", {
                    "bot_id": self.bot_id,
                    "original_tickers": self.tickers,
                    "reason": "all_blocked_by_universe_guard"
                })
                return results
            
            self._logger.log("strategy_system_execute_start", {
                "bot_id": self.bot_id,
                "tickers": allowed_tickers,
                "original_tickers": len(self.tickers),
                "filtered_by_guard": len(self.tickers) - len(allowed_tickers),
                "max_daily_loss": max_daily_loss
            })
            
            dry_run = getattr(self._config, "dry_run", True) if self._config else True
            
            if self._strategy_runner is None or self._strategy_runner_dry_run != dry_run:
                from ..strategy.runner import StrategyRunner
                from ..indicators.indicator_engine import IndicatorEngine
                from ..services.options_chain import OptionsChainProvider
                
                indicator_engine = IndicatorEngine()
                chain_provider = OptionsChainProvider(self._alpaca)
                
                broker = self._create_strategy_broker(max_daily_loss) if not dry_run else None
                
                self._strategy_runner = StrategyRunner(
                    indicator_engine=indicator_engine,
                    options_chain_provider=chain_provider,
                    broker=broker,
                    dry_run=dry_run
                )
                self._strategy_runner_dry_run = dry_run
                self._strategy_max_daily_loss = max_daily_loss
            
            run_results = self._strategy_runner.run(allowed_tickers)
            
            trades_executed = 0
            for run_result in run_results:
                for signal in run_result.signals:
                    self._logger.log("strategy_system_signal", {
                        "strategy_id": signal.strategy_id,
                        "symbol": signal.symbol,
                        "direction": signal.direction,
                        "contract": signal.contract.symbol,
                        "stop_loss_pct": signal.stop_loss_pct,
                        "take_profit_pct": signal.take_profit_pct,
                        "dry_run": dry_run
                    })
                    results["strategies_analyzed"] += 1
                    
                    if not dry_run:
                        cooldown_key = f"{signal.strategy_id}_{signal.symbol}"
                        cooldown_until = self._strategy_signal_cooldowns.get(cooldown_key, 0)
                        if _time.time() < cooldown_until:
                            continue
                        trades_executed += 1
                
                for skipped in run_result.skipped:
                    self._logger.log("strategy_system_skipped", skipped)
            
            results["trades_attempted"] = len([s for r in run_results for s in r.signals])
            results["trades_executed"] = trades_executed
            
            self._logger.log("strategy_system_execute_complete", {
                "bot_id": self.bot_id,
                "total_signals": results["trades_attempted"],
                "trades_executed": trades_executed,
                "total_skipped": sum(len(r.skipped) for r in run_results),
                "dry_run": dry_run
            })
            
            return results
            
        except ImportError as e:
            self._logger.warn(f"Strategy system import error (falling back to legacy): {e}")
            return None
        except Exception as e:
            self._logger.error(f"Strategy system execution failed: {e}")
            results["errors"].append(f"Strategy system: {e}")
            return results
    
    def _create_strategy_broker(self, max_daily_loss: float) -> Any:
        """
        Create a broker adapter that implements the BrokerProvider protocol
        using the existing OptionsBot order infrastructure with risk controls.
        
        Args:
            max_daily_loss: Maximum daily loss budget for position sizing
            
        Returns:
            BrokerProvider-compatible adapter
        """
        options_bot = self
        
        class OptionsBotBroker:
            def __init__(self, bot, max_loss: float):
                self._bot = bot
                self._max_daily_loss = max_loss
            
            def place_bracket(
                self,
                contract,
                stop_loss_pct: float,
                take_profit_pct: float,
                max_contracts: int
            ) -> Dict[str, Any]:
                from ..strategy.options_selector import OptionContract
                
                cooldown_key = f"broker_{contract.symbol}"
                cooldown_until = self._bot._strategy_signal_cooldowns.get(cooldown_key, 0)
                if _time.time() < cooldown_until:
                    return {"error": "execution_failed", "reason": "cooldown_active"}
                
                signal_like = type('SignalLike', (), {
                    'strategy_id': 'broker_adapter',
                    'symbol': contract.symbol.split(' ')[0] if ' ' in contract.symbol else contract.symbol,
                    'contract': contract,
                    'stop_loss_pct': stop_loss_pct,
                    'take_profit_pct': take_profit_pct,
                    'max_contracts': max_contracts
                })()
                
                result = self._bot._execute_strategy_signal(signal_like, self._max_daily_loss)
                if not result:
                    self._bot._strategy_signal_cooldowns[cooldown_key] = _time.time() + self._bot._strategy_cooldown_seconds
                    return {"error": "execution_failed"}
                return result
        
        return OptionsBotBroker(self, max_daily_loss)
    
    def _execute_strategy_signal(self, signal, max_daily_loss: float) -> Optional[Dict[str, Any]]:
        """
        Execute a single strategy signal as an options order.
        
        Uses existing risk checks and order placement infrastructure.
        Tags the position with strategy_id for kill-switch tracking.
        
        Args:
            signal: TradeSignal from StrategyRunner
            max_daily_loss: Budget limit for the day
            
        Returns:
            Order result dict if successful, None otherwise
        """
        try:
            contract_price = (signal.contract.bid + signal.contract.ask) / 2
            position_size = min(
                signal.max_contracts,
                int(max_daily_loss / (contract_price * 100 * signal.stop_loss_pct))
            )
            
            if position_size < 1:
                cooldown_key = f"{getattr(signal, 'strategy_id', 'unknown')}_{signal.symbol}"
                self._strategy_signal_cooldowns[cooldown_key] = _time.time() + self._strategy_cooldown_seconds
                self._logger.log("strategy_signal_skip_size", {
                    "strategy_id": signal.strategy_id,
                    "symbol": signal.symbol,
                    "reason": "position_size_zero",
                    "contract_price": round(contract_price, 2),
                    "max_daily_loss": round(max_daily_loss, 2),
                    "stop_loss_pct": signal.stop_loss_pct,
                    "required_budget": round(contract_price * 100 * signal.stop_loss_pct, 2),
                    "cooldown_minutes": self._strategy_cooldown_seconds / 60,
                    "message": "Budget insufficient for 1 contract - cooldown applied"
                })
                return None
            
            spread_check = self._check_spread_gate(
                signal.contract.bid, signal.contract.ask, signal.contract.symbol
            )
            if not spread_check.get("approved", False):
                self._logger.log("strategy_signal_skip_spread", {
                    "strategy_id": signal.strategy_id,
                    "symbol": signal.symbol,
                    "reason": spread_check.get("reason", "spread_too_wide")
                })
                return None
            
            # IV PERCENTILE ENTRY GATE - Check if IV is favorable for strategy
            iv_percentile = getattr(signal, 'iv_percentile', None)
            if iv_percentile is None:
                # Try to get IV from premarket intel if available
                if hasattr(self, '_premarket_intel') and self._premarket_intel:
                    ticker_intel = self._premarket_intel.tickers.get(signal.symbol)
                    if ticker_intel and ticker_intel.iv:
                        iv_percentile = ticker_intel.iv.iv_percentile
            
            # Get strategy type - prefer strategy_type over strategy_id
            strategy_name = getattr(signal, 'strategy_type', None)
            if strategy_name is None:
                strategy_name = getattr(signal, 'strategy_id', 'unknown')
                # Extract strategy type from strategy_id (e.g., "long_call_AAPL" -> "long_call")
                if '_' in strategy_name:
                    parts = strategy_name.split('_')
                    if len(parts) >= 2 and parts[0] in ['long', 'bull', 'bear', 'iron']:
                        strategy_name = '_'.join(parts[:2])
            
            if not self._check_iv_gate(signal.symbol, strategy_name, iv_percentile):
                self._logger.log("strategy_signal_skip_iv", {
                    "strategy_id": signal.strategy_id,
                    "symbol": signal.symbol,
                    "iv_percentile": iv_percentile,
                    "reason": "iv_not_favorable"
                })
                return None
            
            # NEWS ENTRY FILTER - Check news sentiment before entry
            news_filter_result = self._check_news_entry_filter(signal)
            if news_filter_result.get("blocked", False):
                self._logger.log("strategy_signal_skip_news", {
                    "strategy_id": signal.strategy_id,
                    "symbol": signal.symbol,
                    "reason": news_filter_result.get("reason", "news_filter"),
                    "sentiment": news_filter_result.get("sentiment", 0),
                    "confidence": news_filter_result.get("confidence", 0)
                })
                if news_filter_result.get("action") == "reduce_size":
                    position_size = max(1, int(position_size * 0.5))
                else:
                    return None
            
            from ..core.state import set_state
            
            order_result = self._place_bracket_order(
                contract_symbol=signal.contract.symbol,
                qty=position_size,
                stop_loss_pct=signal.stop_loss_pct,
                take_profit_pct=signal.take_profit_pct
            )
            
            if order_result and order_result.get("order_id"):
                set_state(f"position.{order_result['order_id']}.strategy_id", signal.strategy_id)
                set_state(f"position.{signal.contract.symbol}.strategy_id", signal.strategy_id)
                set_state(f"strategy_position.{signal.strategy_id}.{signal.contract.symbol}", {
                    "order_id": order_result.get("order_id"),
                    "entry_ts": __import__("time").time()
                })
            
            return order_result
            
        except Exception as e:
            self._logger.error(f"Strategy signal execution failed: {e}", 
                             strategy_id=signal.strategy_id, symbol=signal.symbol)
            return None
    
    def _place_bracket_order(self, contract_symbol: str, qty: int, 
                             stop_loss_pct: float, take_profit_pct: float) -> Optional[Dict[str, Any]]:
        """
        Place a bracket order for an options contract.
        
        A bracket order enters a position and sets up exit orders for stop-loss
        and take-profit levels.
        
        Args:
            contract_symbol: The options contract symbol (e.g., AAPL250117C00150000)
            qty: Number of contracts to buy
            stop_loss_pct: Stop-loss percentage (e.g., 0.3 for 30% loss)
            take_profit_pct: Take-profit percentage (e.g., 0.5 for 50% gain)
            
        Returns:
            Dict with order_id and status, or None on failure
        """
        try:
            # Get current quote for the contract
            snapshot = self._alpaca.get_option_snapshot(contract_symbol)
            if not snapshot:
                self._logger.log("bracket_order_no_snapshot", {"symbol": contract_symbol})
                return None
            
            ask_price = snapshot.get("ask", 0)
            if ask_price <= 0:
                self._logger.log("bracket_order_invalid_ask", {"symbol": contract_symbol, "ask": ask_price})
                return None

            # FREEROLL: Cap qty to house money if target lock is active
            try:
                from ..risk.session_protection import get_session_protection
                sp = get_session_protection()
                if sp.is_target_locked() and sp.is_freeroll_available():
                    house_money = sp.get_house_money()
                    cost_per_contract = ask_price * 100
                    max_freeroll_qty = int(house_money / cost_per_contract) if cost_per_contract > 0 else 0
                    if max_freeroll_qty < 1:
                        self._logger.log("options_freeroll_insufficient", {
                            "symbol": contract_symbol,
                            "house_money": house_money,
                            "cost_per_contract": cost_per_contract,
                        })
                        return None
                    if qty > max_freeroll_qty:
                        self._logger.log("options_freeroll_qty_capped", {
                            "symbol": contract_symbol,
                            "original_qty": qty,
                            "capped_qty": max_freeroll_qty,
                            "house_money": house_money,
                        })
                        qty = max_freeroll_qty
                    print(f"  [OPTIONS] FREEROLL sizing: {contract_symbol} capped to {qty} contracts (${house_money:.0f} house money)")
            except Exception:
                pass
            
            # Place entry order slightly above ask for better fill
            limit_price = round(ask_price * 1.02, 2)
            
            order = self._alpaca.place_options_order(
                symbol=contract_symbol,
                qty=qty,
                side="buy",
                order_type="limit",
                limit_price=limit_price
            )
            
            order_id = order.get("id")
            if not order_id:
                self._logger.log("bracket_order_no_id", {"symbol": contract_symbol})
                return None
            
            # Store bracket levels for ExitBot to monitor
            from ..core.state import set_state
            entry_price = limit_price
            stop_price = round(entry_price * (1 - stop_loss_pct), 2)
            target_price = round(entry_price * (1 + take_profit_pct), 2)
            
            set_state(f"bracket.{contract_symbol}.entry_price", entry_price)
            set_state(f"bracket.{contract_symbol}.stop_price", stop_price)
            set_state(f"bracket.{contract_symbol}.target_price", target_price)
            set_state(f"bracket.{contract_symbol}.qty", qty)
            
            self._logger.log("bracket_order_placed", {
                "symbol": contract_symbol,
                "order_id": order_id,
                "qty": qty,
                "entry_price": entry_price,
                "stop_price": stop_price,
                "target_price": target_price,
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": take_profit_pct
            })

            # Mark freeroll as used if target lock was active
            try:
                from ..risk.session_protection import get_session_protection
                sp = get_session_protection()
                if sp.is_target_locked() and not sp._freeroll_used:
                    sp.mark_freeroll_used(order_id)
                    self._logger.log("options_freeroll_entry", {
                        "symbol": contract_symbol,
                        "order_id": order_id,
                        "qty": qty,
                    })
            except Exception:
                pass

            return {
                "order_id": order_id,
                "symbol": contract_symbol,
                "qty": qty,
                "entry_price": entry_price,
                "stop_price": stop_price,
                "target_price": target_price
            }
            
        except Exception as e:
            self._logger.error(f"Bracket order failed for {contract_symbol}: {e}")
            return None
    
    def _check_news_entry_filter(self, signal) -> Dict[str, Any]:
        """
        Check if news sentiment allows this entry
        
        Args:
            signal: TradeSignal with direction info
            
        Returns:
            Dict with 'blocked', 'action', 'reason', 'sentiment', 'confidence'
        """
        try:
            config = load_bots_config()
            intel_config = config.get("intelligence", {})
            news_config = intel_config.get("news", {})
            entry_filter = news_config.get("entry_filter", {})
            
            # Gate: Check if entry filter is enabled
            if not news_config.get("enabled", False):
                return {"blocked": False}
            if not entry_filter.get("enabled", False):
                return {"blocked": False}
            
            # Get news and sentiment
            news_intel = get_news_intelligence()
            scorer = get_sentiment_scorer()
            
            # Extract underlying symbol from option contract
            symbol = signal.symbol if hasattr(signal, 'symbol') else ""
            if not symbol:
                return {"blocked": False}
            
            news_items = news_intel.get_news_for_symbol(symbol)
            if not news_items:
                return {"blocked": False}  # No news = allow (fail-open for entries)
            
            sentiment = scorer.score_news(news_items)
            
            # Get thresholds
            bullish_min = entry_filter.get("bullish_min", 0.20)
            bearish_max = entry_filter.get("bearish_max", -0.20)
            neutral_band = entry_filter.get("neutral_band", 0.15)
            mixed_handling = entry_filter.get("mixed_handling", "skip")
            
            # Determine direction from signal
            is_bullish = hasattr(signal, 'direction') and signal.direction == 'long'
            is_bearish = hasattr(signal, 'direction') and signal.direction == 'short'
            
            # Check sentiment alignment
            score = sentiment.sentiment_score
            is_neutral = abs(score) < neutral_band
            
            blocked = False
            reason = ""
            action = "skip"
            
            if is_neutral:
                # Neutral sentiment - handle based on config
                if mixed_handling == "skip":
                    blocked = True
                    reason = "neutral_sentiment"
                    action = "skip"
                elif mixed_handling == "reduce_size":
                    blocked = True
                    reason = "neutral_sentiment_reduce"
                    action = "reduce_size"
                # else: allow
            elif is_bullish and score < bullish_min:
                # Bullish trade but sentiment not positive enough
                blocked = True
                reason = f"sentiment_too_low_for_bullish: {score:.2f} < {bullish_min}"
                action = "skip"
            elif is_bearish and score > bearish_max:
                # Bearish trade but sentiment not negative enough
                blocked = True
                reason = f"sentiment_too_high_for_bearish: {score:.2f} > {bearish_max}"
                action = "skip"
            
            self._logger.log("news_entry_filter", {
                "symbol": symbol,
                "direction": "bullish" if is_bullish else ("bearish" if is_bearish else "unknown"),
                "sentiment": round(score, 3),
                "confidence": round(sentiment.confidence, 3),
                "flags": sentiment.flags,
                "blocked": blocked,
                "reason": reason,
                "action": action
            })
            
            return {
                "blocked": blocked,
                "action": action,
                "reason": reason,
                "sentiment": score,
                "confidence": sentiment.confidence
            }
            
        except Exception as e:
            self._logger.error(f"News entry filter error: {e}")
            return {"blocked": False}  # Fail-open for entries
    
    def _is_trading_hours(self) -> bool:
        """
        Check if current time is within options trading session.
        Uses Pacific timezone from config (handles DST automatically).
        
        Returns:
            True if within trading hours, False otherwise
        """
        # GUARD: Fail-closed if no config
        if not self._config:
            return False
        
        try:
            from ..core.clock import get_market_clock
            
            # Use centralized MarketClock for timezone handling
            clock = get_market_clock()
            now_pacific = clock.now()
            current_time = now_pacific.time()
            
            # Parse configured trading times (format: "HH:MM")
            start_parts = self._config.trade_start.split(":")
            end_parts = self._config.trade_end.split(":")
            
            start_time = time(int(start_parts[0]), int(start_parts[1]))
            end_time = time(int(end_parts[0]), int(end_parts[1]))
            
            in_hours = start_time <= current_time <= end_time
            
            self._logger.log("options_trading_hours_check", {
                "now_utc": datetime.utcnow().strftime("%H:%M"),
                "now_pacific": now_pacific.strftime("%H:%M"),
                "window": f"{self._config.trade_start}-{self._config.trade_end}",
                "in_hours": in_hours
            })
            
            return in_hours
            
        except Exception as e:
            self._logger.error(f"Trading hours check failed: {e}")
            return False  # Fail-closed: assume outside hours
    
    def _is_manage_hours(self) -> bool:
        """
        Check if current time is within position management window.
        Manage window extends beyond trading hours to allow closing positions.
        Uses Pacific timezone from config (handles DST automatically).
        
        Returns:
            True if within management hours, False otherwise
        """
        # GUARD: Default to allowing management if no config
        if not self._config:
            return True
        
        try:
            from ..core.clock import get_market_clock
            
            # Use centralized MarketClock for timezone handling
            clock = get_market_clock()
            now_pacific = clock.now()
            current_time = now_pacific.time()
            
            # Parse configured manage-until time (format: "HH:MM")
            end_parts = self._config.manage_until.split(":")
            end_time = time(int(end_parts[0]), int(end_parts[1]))
            
            # We can always manage until the configured end time
            return current_time <= end_time
            
        except Exception as e:
            self._logger.error(f"Manage hours check failed: {e}")
            return True  # Default to allowing management
    
    def _check_risk_limits(self) -> bool:
        """
        Check if we're within all risk limits including Greek exposure.
        
        Returns:
            True if we can continue trading, False if limits exceeded
        """
        # GUARD: Fail-closed if no config
        if not self._config:
            return False
        
        trades_today = self._get_trades_today()
        max_trades = self._config.max_trades_per_day
        
        # Check trades today limit
        if trades_today >= max_trades:
            self._logger.log("options_risk_limit_detail", {
                "reason": "max_trades_per_day",
                "trades_today": trades_today,
                "max_trades": max_trades
            })
            return False
        
        # Check concurrent positions limit
        positions = self._get_options_positions()
        max_positions = self._config.max_concurrent_positions
        
        if len(positions) >= max_positions:
            self._logger.log("options_risk_limit_detail", {
                "reason": "max_concurrent_positions",
                "current_positions": len(positions),
                "max_positions": max_positions,
                "position_symbols": [p.symbol for p in positions]
            })
            return False
        
        # Check Greek limits (delta/gamma caps)
        greek_blocked = self._check_greek_limits()
        if greek_blocked:
            return False
        
        self._logger.log("options_risk_check_passed", {
            "trades_today": trades_today,
            "max_trades": max_trades,
            "current_positions": len(positions),
            "max_positions": max_positions
        })
        
        return True
    
    def _check_iv_gate(self, symbol: str, strategy: str, iv_percentile: Optional[float] = None) -> bool:
        """
        Check if IV percentile is in favorable range for the strategy.
        
        For BUYING options (long calls/puts): Want LOW IV (buy cheap)
        For SELLING options (shorts/spreads): Want HIGH IV (sell expensive)
        
        Args:
            symbol: The underlying symbol
            strategy: The strategy type (long_call, long_put, straddle, etc.)
            iv_percentile: Current IV percentile (0-100), None if unavailable
            
        Returns:
            True if IV is favorable for this strategy, False if should skip
        """
        # Use cached config if available, otherwise load
        if not hasattr(self, '_iv_gate_config') or self._iv_gate_config is None:
            config = load_bots_config()
            self._iv_gate_config = config.get("optionsbot", {}).get("iv_gate", {})
        
        iv_gate_config = self._iv_gate_config
        
        if not iv_gate_config.get("enabled", True):
            return True  # IV gate disabled, allow all
        
        # Handle missing IV data - log warning and allow trade (fail-open)
        if iv_percentile is None:
            self._logger.log("iv_gate_missing_data", {
                "symbol": symbol,
                "strategy": strategy,
                "action": "allowing_trade_fail_open"
            })
            return True
        
        # Default thresholds
        buy_max_iv = iv_gate_config.get("buy_max_iv_percentile", 60)
        buy_min_iv = iv_gate_config.get("buy_min_iv_percentile", 10)
        sell_min_iv = iv_gate_config.get("sell_min_iv_percentile", 50)
        straddle_min_iv = iv_gate_config.get("straddle_min_iv_percentile", 40)
        
        # Normalize strategy name for matching
        strat_lower = strategy.lower().replace("-", "_")
        
        # DEBIT strategies (pay premium): Want LOW IV to buy cheap
        # These are long options or debit spreads where we pay net premium
        is_buy_strategy = strat_lower in [
            "long_call", "long_put", "buy",
            "protective_put",
            "bull_call_spread",  # Debit spread - buy lower strike call, sell higher
            "bear_put_spread",   # Debit spread - buy higher strike put, sell lower
        ]
        
        # CREDIT strategies (receive premium): Want HIGH IV to sell expensive
        # These are short options or credit spreads where we receive net premium
        is_sell_strategy = strat_lower in [
            "covered_call", "short",
            "bull_put_spread",   # Credit spread - sell higher strike put, buy lower
            "bear_call_spread",  # Credit spread - sell lower strike call, buy higher
            "iron_condor", "iron_butterfly"
        ]
        
        # Straddles/strangles: neutral strategies needing elevated IV for premium capture
        is_straddle = strat_lower in ["straddle", "strangle"]
        
        # Check IV against thresholds
        if is_buy_strategy:
            # For buying options, want IV to be LOW (not too high, not too low)
            if iv_percentile > buy_max_iv:
                self._logger.log("iv_gate_blocked", {
                    "symbol": symbol,
                    "strategy": strategy,
                    "iv_percentile": iv_percentile,
                    "threshold": buy_max_iv,
                    "reason": "IV too high for buying - options expensive"
                })
                return False
            if iv_percentile < buy_min_iv:
                self._logger.log("iv_gate_blocked", {
                    "symbol": symbol,
                    "strategy": strategy,
                    "iv_percentile": iv_percentile,
                    "threshold": buy_min_iv,
                    "reason": "IV too low - market too quiet, low move probability"
                })
                return False
                
        elif is_sell_strategy:
            # For selling options, want IV to be HIGH (sell expensive)
            if iv_percentile < sell_min_iv:
                self._logger.log("iv_gate_blocked", {
                    "symbol": symbol,
                    "strategy": strategy,
                    "iv_percentile": iv_percentile,
                    "threshold": sell_min_iv,
                    "reason": "IV too low for selling - not enough premium"
                })
                return False
                
        elif is_straddle:
            # For straddles, need elevated IV for premium capture
            if iv_percentile < straddle_min_iv:
                self._logger.log("iv_gate_blocked", {
                    "symbol": symbol,
                    "strategy": strategy,
                    "iv_percentile": iv_percentile,
                    "threshold": straddle_min_iv,
                    "reason": "IV too low for straddle - insufficient premium"
                })
                return False
        
        # IV is in favorable range
        self._logger.log("iv_gate_passed", {
            "symbol": symbol,
            "strategy": strategy,
            "iv_percentile": iv_percentile
        })
        return True
    
    def _adjust_spread_params_for_iv(self, config: Dict[str, Any], 
                                     symbol: str) -> Dict[str, Any]:
        """
        Dynamically adjust spread parameters based on current IV environment.
        
        In HIGH IV environments:
        - Widen spreads (more premium available)
        - Move deltas further OTM (more cushion)
        
        In LOW IV environments:
        - Tighten spreads (preserve capital)
        - Move deltas closer to ATM (need more intrinsic value)
        
        Args:
            config: Original strategy config dict
            symbol: Underlying symbol for IV lookup
            
        Returns:
            Adjusted config dict with modified delta ranges and spread widths
        """
        if not config.get("adjust_for_iv", False):
            return config  # No adjustment requested
        
        adjusted = dict(config)  # Copy to avoid mutating original
        
        try:
            # Get current IV percentile for the symbol
            iv_percentile = None
            if hasattr(self, '_premarket_intel') and self._premarket_intel:
                intel = self._premarket_intel.tickers.get(symbol)
                if intel and intel.iv:
                    iv_percentile = intel.iv.iv_percentile
            
            if iv_percentile is None:
                self._logger.log("spread_iv_adjustment_skipped", {
                    "symbol": symbol,
                    "reason": "no_iv_data"
                })
                return config  # No adjustment if no IV data
            
            # Determine IV regime
            # HIGH IV (>70): Widen spreads, move further OTM
            # NORMAL IV (30-70): No adjustment
            # LOW IV (<30): Tighten spreads, move closer to ATM
            
            if iv_percentile >= 70:
                # HIGH IV: Widen spreads by 20%, move deltas 0.05 further OTM
                iv_multiplier = 1.2
                delta_shift = 0.05  # Move OTM
                regime = "high_iv"
            elif iv_percentile <= 30:
                # LOW IV: Tighten spreads by 20%, move deltas 0.05 closer to ATM
                iv_multiplier = 0.8
                delta_shift = -0.05  # Move toward ATM
                regime = "low_iv"
            else:
                # Normal IV: No adjustment
                return config
            
            # Adjust spread width range
            if "spread_width_range" in adjusted:
                orig_range = adjusted["spread_width_range"]
                adjusted["spread_width_range"] = [
                    max(1, round(orig_range[0] * iv_multiplier)),
                    round(orig_range[1] * iv_multiplier)
                ]
            
            # Adjust delta ranges (calls are positive, puts are negative)
            for key in ["long_delta_range", "short_delta_range", 
                       "put_short_delta_range", "put_long_delta_range",
                       "call_short_delta_range", "call_long_delta_range"]:
                if key in adjusted:
                    orig_range = adjusted[key]
                    is_put = "put" in key or (orig_range[0] < 0)
                    
                    if is_put:
                        # Puts: more negative = more ITM, less negative = more OTM
                        # HIGH IV: shift more negative (further OTM for puts)
                        adjusted[key] = [
                            orig_range[0] - delta_shift,
                            orig_range[1] - delta_shift
                        ]
                    else:
                        # Calls: higher = more ITM, lower = more OTM
                        # HIGH IV: shift lower (further OTM for calls)
                        adjusted[key] = [
                            max(0.01, orig_range[0] - delta_shift),
                            max(0.05, orig_range[1] - delta_shift)
                        ]
            
            self._logger.log("spread_params_adjusted_for_iv", {
                "symbol": symbol,
                "iv_percentile": iv_percentile,
                "regime": regime,
                "iv_multiplier": iv_multiplier,
                "delta_shift": delta_shift,
                "original_spread_width": config.get("spread_width_range"),
                "adjusted_spread_width": adjusted.get("spread_width_range")
            })
            
            return adjusted
            
        except Exception as e:
            self._logger.error(f"IV adjustment failed: {e}")
            return config  # Return original on error
    
    def _check_greek_limits(self) -> bool:
        """
        Check if portfolio Greek exposure is within limits.
        
        Returns:
            True if Greek limits are BREACHED (should block), False if OK
        """
        try:
            # Get current portfolio Greeks
            portfolio_greeks = self.get_portfolio_greeks()
            
            # Get account equity for percentage calculations
            alpaca = get_alpaca_client()
            account = alpaca.get_account()
            equity = float(account.equity) if account else 0
            
            if equity <= 0:
                self._logger.warn("Greek check skipped: no equity data")
                return False  # Don't block if we can't get equity
            
            # Get reference underlying price (use SPY as benchmark)
            underlying_price = 500.0  # Default SPY price
            try:
                spy_quote = alpaca.get_latest_quote("SPY", "stock")
                if spy_quote:
                    underlying_price = float(spy_quote.get("ask", 0) or spy_quote.get("bid", 0) or 500.0)
            except Exception:
                pass  # Use default
            
            # Check limits
            greek_monitor = get_greek_risk_monitor()
            result = greek_monitor.check_limits(portfolio_greeks, equity, underlying_price)
            
            # Log exposure for monitoring
            if result.exposure:
                self._logger.log("greek_exposure_check", {
                    "delta": round(result.exposure.total_delta, 2),
                    "gamma": round(result.exposure.total_gamma, 4),
                    "delta_dollar": round(result.exposure.delta_dollar_exposure, 2),
                    "delta_util_pct": round(result.delta_utilization_pct, 1),
                    "gamma_util_pct": round(result.gamma_utilization_pct, 1),
                    "delta_status": result.delta_status.value,
                    "gamma_status": result.gamma_status.value,
                    "can_trade": result.can_trade
                })
            
            if not result.can_trade:
                self._logger.log("options_risk_limit_detail", {
                    "reason": "greek_limits_breached",
                    "delta_status": result.delta_status.value,
                    "gamma_status": result.gamma_status.value,
                    "delta_util": round(result.delta_utilization_pct, 1),
                    "gamma_util": round(result.gamma_utilization_pct, 1),
                    "detail": result.reason
                })
                return True  # Blocked
            
            return False  # OK to trade
            
        except Exception as e:
            self._logger.error(f"Greek limits check error (allowing trade): {e}")
            return False  # Don't block on errors - fail-open for this check
    
    def _get_trades_today(self) -> int:
        """
        Get the number of trades executed today by this bot.
        Reads from state database.
        
        Returns:
            Number of trades today
        """
        try:
            today_str = get_market_clock().now().strftime("%Y%m%d")
            key = f"trades_today.{self.bot_id}.{today_str}"
            count = get_state(key)
            return int(count) if count else 0
        except Exception:
            return 0
    
    def _increment_trades_today(self) -> None:
        """
        Increment the trades-today counter for this bot.
        Stores in state database.
        """
        try:
            today_str = get_market_clock().now().strftime("%Y%m%d")
            key = f"trades_today.{self.bot_id}.{today_str}"
            current = self._get_trades_today()
            set_state(key, current + 1)
        except Exception as e:
            self._logger.error(f"Failed to increment trades today: {e}")
    
    def _get_options_positions(self) -> List:
        """
        Get all options positions from Alpaca.
        Filters positions to only include those for our tracked tickers.
        
        Returns:
            List of options positions
        """
        try:
            all_positions = self._alpaca.get_positions()
            
            # Filter to options positions for our tickers
            # Options symbols are longer and contain the underlying ticker
            options_positions = []
            for position in all_positions:
                symbol = position.symbol
                # Options symbols typically have format: SPY241231C00500000
                if len(symbol) > 6:
                    for ticker in self.tickers:
                        if ticker in symbol:
                            options_positions.append(position)
                            break
            
            return options_positions
            
        except Exception as e:
            self._logger.error(f"Failed to get options positions: {e}")
            return []
    
    def _analyze_market_conditions(self, ticker: str) -> Dict[str, Any]:
        """
        Analyze market conditions for a ticker to determine optimal strategy.
        
        Combines ticker-specific price data with global market regime indicators
        (VIX, VVIX, TNX, DXY, MOVE) for comprehensive analysis.
        
        Args:
            ticker: Stock symbol to analyze
            
        Returns:
            Dictionary with market analysis including trend, volatility, regime, etc.
        """
        analysis = {
            "ticker": ticker,
            "price": 0.0,
            "trend": MarketRegime.NEUTRAL,
            "volatility": MarketRegime.LOW_VOLATILITY,
            "iv_rank": 50.0,
            "support_levels": [],
            "resistance_levels": [],
            "recommended_strategies": [],
            "error": None,
            # New regime fields from VIX/VVIX/TNX/DXY/MOVE analysis
            "global_regime": None,
            "position_size_multiplier": 1.0,
            "halt_new_entries": False,
            "tighten_stops": False
        }
        
        try:
            # =========================================================
            # STEP 1: Fetch global market regime from indicators
            # =========================================================
            try:
                global_regime = get_current_regime()
                analysis["global_regime"] = global_regime
                analysis["position_size_multiplier"] = global_regime.position_size_multiplier
                analysis["halt_new_entries"] = global_regime.halt_new_entries
                analysis["tighten_stops"] = global_regime.tighten_stops
                
                # Override local volatility with VIX-based regime
                if global_regime.volatility_regime in [VolatilityRegime.HIGH, VolatilityRegime.EXTREME]:
                    analysis["volatility"] = MarketRegime.HIGH_VOLATILITY
                elif global_regime.volatility_regime in [VolatilityRegime.VERY_LOW, VolatilityRegime.LOW]:
                    analysis["volatility"] = MarketRegime.LOW_VOLATILITY
                
                self._logger.log("regime_analysis", {
                    "ticker": ticker,
                    "vix": global_regime.vix,
                    "volatility_regime": global_regime.volatility_regime.value,
                    "sentiment": global_regime.sentiment.value,
                    "position_multiplier": global_regime.position_size_multiplier,
                    "favor_straddles": global_regime.favor_straddles,
                    "favor_iron_condors": global_regime.favor_iron_condors
                })
                
            except Exception as regime_err:
                self._logger.error(f"Regime fetch failed: {regime_err}")
                analysis["global_regime"] = None
            
            # =========================================================
            # STEP 2: Get ticker-specific price data
            # =========================================================
            quote = self._alpaca.get_latest_quote(ticker, asset_class="stock")
            bid = quote.get("bid", 0)
            ask = quote.get("ask", 0)
            current_price = (bid + ask) / 2 if bid and ask else ask or bid
            analysis["price"] = current_price
            
            # Store prices in state for trend analysis using JSON (safe serialization)
            price_key = f"prices.{self.bot_id}.{ticker}"
            prices_str = get_state(price_key)
            
            # Use JSON for safe deserialization (never use eval)
            try:
                prices = json.loads(prices_str) if prices_str else []
            except (json.JSONDecodeError, TypeError):
                prices = []
            
            # Add current price and keep last 20
            prices.append(current_price)
            if len(prices) > 20:
                prices = prices[-20:]
            set_state(price_key, json.dumps(prices))
            
            # =========================================================
            # STEP 3: Calculate ticker trend from recent prices
            # =========================================================
            if len(prices) >= 5:
                recent_avg = sum(prices[-5:]) / 5
                older_avg = sum(prices[:5]) / 5 if len(prices) >= 10 else recent_avg
                
                change_pct = (recent_avg - older_avg) / older_avg * 100 if older_avg > 0 else 0
                
                if change_pct > 0.5:
                    analysis["trend"] = MarketRegime.BULLISH
                elif change_pct < -0.5:
                    analysis["trend"] = MarketRegime.BEARISH
                else:
                    analysis["trend"] = MarketRegime.NEUTRAL
                
                # Estimate local volatility from price variance (used as secondary signal)
                if len(prices) >= 10:
                    returns = [(prices[i] - prices[i-1]) / prices[i-1] * 100 
                              for i in range(1, len(prices))]
                    variance = sum(r**2 for r in returns) / len(returns)
                    
                    # Only override if no global regime or local vol is extreme
                    if variance > 2.0:  # Higher threshold since VIX is primary
                        analysis["volatility"] = MarketRegime.HIGH_VOLATILITY
            
            # =========================================================
            # STEP 4: Calculate support/resistance levels
            # =========================================================
            if current_price > 0:
                analysis["support_levels"] = [
                    round(current_price * 0.98, 2),
                    round(current_price * 0.95, 2)
                ]
                analysis["resistance_levels"] = [
                    round(current_price * 1.02, 2),
                    round(current_price * 1.05, 2)
                ]
            
            # =========================================================
            # STEP 5: Get VWAP posture for directional bias (Chris Sain methodology)
            # =========================================================
            try:
                vwap_posture = self._get_vwap_posture_for_ticker(ticker, current_price)
                analysis["vwap_posture"] = vwap_posture
                if vwap_posture:
                    self._logger.log("options_vwap_posture_analysis", {
                        "ticker": ticker,
                        "posture": vwap_posture.posture.value,
                        "allow_long": vwap_posture.allow_long,
                        "allow_short": vwap_posture.allow_short,
                        "distance_pct": round(vwap_posture.distance_from_vwap_pct, 3)
                    })
            except Exception as vwap_err:
                self._logger.error(f"VWAP posture analysis failed for {ticker}: {vwap_err}")
                analysis["vwap_posture"] = None
            
            # =========================================================
            # STEP 6: Get recommended strategies based on full analysis
            # =========================================================
            analysis["recommended_strategies"] = self._get_strategy_recommendations(analysis, ticker=ticker)
            
        except Exception as e:
            self._logger.error(f"Market analysis failed for {ticker}: {e}")
            analysis["error"] = str(e)
        
        return analysis
    
    def _get_strategy_recommendations(self, analysis: Dict[str, Any], ticker: Optional[str] = None) -> List[OptionStrategy]:
        """
        Get strategy recommendations based on market analysis, global regime, and enabled strategies.
        
        Priority order (BUY-SIDE FIRST to avoid margin issues):
        1. Check if buy-side strategies are enabled (long_call, long_put, straddle)
        2. Use trend to select directional long options
        3. Fall back to sell-side only if buy-side disabled
        
        Args:
            analysis: Market analysis dictionary with global_regime
            ticker: Symbol for ticker-specific historical strategy lookup
            
        Returns:
            List of recommended OptionStrategy values, ordered by preference
        """
        recommendations = []
        
        # GUARD: Return empty list if no config available
        if not self._config:
            return recommendations
        
        trend = analysis["trend"]
        volatility = analysis["volatility"]
        global_regime = analysis.get("global_regime")
        
        # =========================================================
        # VWAP AUTHORITY CHECK (Chris Sain Institutional Methodology)
        # =========================================================
        # VWAP posture overrides trend indicators for directional trades.
        # FAIL-CLOSED: If posture is NEUTRAL or unavailable, only non-directional strategies allowed.
        vwap_posture = analysis.get("vwap_posture")  # PostureDecision or None
        vwap_allow_long = True   # Default permissive if VWAP disabled
        vwap_allow_short = True
        vwap_is_neutral = False
        
        if vwap_posture is not None:
            vwap_allow_long = vwap_posture.allow_long
            vwap_allow_short = vwap_posture.allow_short
            vwap_is_neutral = not vwap_allow_long and not vwap_allow_short
            
            self._logger.log("options_vwap_authority", {
                "posture": vwap_posture.posture.value,
                "allow_long": vwap_allow_long,
                "allow_short": vwap_allow_short,
                "is_neutral": vwap_is_neutral,
                "distance_pct": round(vwap_posture.distance_from_vwap_pct, 3)
            })
        else:
            # FAIL-OPEN: If VWAP posture unavailable, allow ALL directions
            # Previously fail-closed which blocked all directional trades
            vwap_allow_long = True
            vwap_allow_short = True
            vwap_is_neutral = False
            self._logger.log("options_vwap_fail_open", {
                "reason": "VWAP posture unavailable",
                "action": "allow_all_directions_fail_open"
            })
        
        # =========================================================
        # STEP 1: Check global regime signals (VIX-based)
        # =========================================================
        favor_straddles = False
        favor_iron_condors = False
        
        if global_regime:
            favor_straddles = global_regime.favor_straddles
            favor_iron_condors = global_regime.favor_iron_condors
            
            # Log regime influence on strategy
            self._logger.log("strategy_regime_influence", {
                "vix": global_regime.vix,
                "favor_straddles": favor_straddles,
                "favor_iron_condors": favor_iron_condors,
                "vvix_warning": global_regime.vvix_warning
            })
        
        # =========================================================
        # STEP 1.5: BEST HISTORICAL STRATEGY FOR TICKER (if available)
        # =========================================================
        # First, check if we have historical data showing which strategy works best
        # for this specific ticker - use data-driven preference
        if ticker:
            try:
                tracker = get_strategy_tracker()
                best_historical = tracker.get_best_strategy(ticker)
                
                if best_historical:
                    # Map string to enum
                    strategy_map = {
                        "iron_condor": OptionStrategy.IRON_CONDOR,
                        "bull_put_spread": OptionStrategy.BULL_PUT_SPREAD,
                        "bear_call_spread": OptionStrategy.BEAR_CALL_SPREAD,
                        "bull_call_spread": OptionStrategy.BULL_CALL_SPREAD,
                        "bear_put_spread": OptionStrategy.BEAR_PUT_SPREAD,
                        "long_call": OptionStrategy.LONG_CALL,
                        "long_put": OptionStrategy.LONG_PUT,
                        "straddle": OptionStrategy.STRADDLE,
                        "calendar_spread": OptionStrategy.CALENDAR_SPREAD
                    }
                    
                    if best_historical in strategy_map:
                        best_strategy = strategy_map[best_historical]
                        metrics = tracker.get_ticker_metrics(ticker).get(best_historical)
                        
                        recommendations.append(best_strategy)
                        self._logger.log("strategy_best_historical_selected", {
                            "ticker": ticker,
                            "strategy": best_historical,
                            "reason": "data_driven_best_performer",
                            "win_rate": round(metrics.win_rate, 2) if metrics else 0,
                            "total_trades": metrics.total_trades if metrics else 0,
                            "profit_factor": round(metrics.profit_factor, 2) if metrics else 0
                        })
            except Exception as e:
                self._logger.log("strategy_tracker_error", {"error": str(e)})
        
        # =========================================================
        # STEP 1.6: IRON CONDOR DEFAULT (if no historical data)
        # =========================================================
        # If no historical best, and prefer_iron_condor_first is enabled,
        # default to Iron Condor - backtest shows highest win rate
        if not recommendations and self._config.prefer_iron_condor_first and self._config.iron_condor_enabled:
            recommendations.append(OptionStrategy.IRON_CONDOR)
            self._logger.log("strategy_iron_condor_default", {
                "reason": "no_historical_data_defaulting_to_iron_condor",
                "volatility": volatility.value if volatility else "unknown",
                "trend": trend.value if trend else "unknown"
            })
            # Note: We continue to add other strategies as backups
            # The selection will use Iron Condor first, others as fallbacks
        
        # =========================================================
        # STEP 2: BUY-SIDE STRATEGIES (PRIORITY - no margin required)
        # =========================================================
        # VWAP Authority: Filter directional strategies based on posture
        
        # HIGH VOLATILITY: Favor Straddles (long volatility) - non-directional, always allowed
        if favor_straddles or volatility == MarketRegime.HIGH_VOLATILITY:
            if self._config.straddle_enabled:
                recommendations.append(OptionStrategy.STRADDLE)
                self._logger.log("strategy_selected_straddle", {
                    "reason": "high_vix_or_volatility"
                })
        
        # BULLISH TREND: Prefer Bull Call Spread (defined-risk) over Long Call
        if trend == MarketRegime.BULLISH:
            if not vwap_allow_long:
                self._logger.log("strategy_vwap_blocked", {
                    "strategy": "bull_call_spread/long_call",
                    "reason": "VWAP posture blocks longs",
                    "posture": vwap_posture.posture.value if vwap_posture else "unknown"
                })
            else:
                # PREFER: Bull Call Spread (defined-risk debit spread) when prefer_defined_risk is True
                if self._config.prefer_defined_risk and self._config.bull_call_spread_enabled:
                    recommendations.append(OptionStrategy.BULL_CALL_SPREAD)
                    self._logger.log("strategy_selected_bull_call_spread", {
                        "reason": "bullish_trend_defined_risk",
                        "vwap_confirmed": vwap_allow_long
                    })
                # FALLBACK: Long Call (single leg) - only if NOT preferring defined risk
                elif not self._config.prefer_defined_risk and self._config.long_call_enabled:
                    recommendations.append(OptionStrategy.LONG_CALL)
                    self._logger.log("strategy_selected_long_call", {
                        "reason": "bullish_trend",
                        "vwap_confirmed": vwap_allow_long
                    })
                # LAST RESORT: If spread disabled but defined risk preferred, use spread anyway if enabled
                elif self._config.bull_call_spread_enabled:
                    recommendations.append(OptionStrategy.BULL_CALL_SPREAD)
                    self._logger.log("strategy_selected_bull_call_spread", {
                        "reason": "bullish_defined_risk_fallback",
                        "vwap_confirmed": vwap_allow_long
                    })
        
        # BEARISH TREND: Prefer Bear Put Spread (defined-risk) over Long Put
        elif trend == MarketRegime.BEARISH:
            if not vwap_allow_short:
                self._logger.log("strategy_vwap_blocked", {
                    "strategy": "bear_put_spread/long_put",
                    "reason": "VWAP posture blocks shorts",
                    "posture": vwap_posture.posture.value if vwap_posture else "unknown"
                })
            else:
                # PREFER: Bear Put Spread (defined-risk debit spread) when prefer_defined_risk is True
                if self._config.prefer_defined_risk and self._config.bear_put_spread_enabled:
                    recommendations.append(OptionStrategy.BEAR_PUT_SPREAD)
                    self._logger.log("strategy_selected_bear_put_spread", {
                        "reason": "bearish_trend_defined_risk",
                        "vwap_confirmed": vwap_allow_short
                    })
                # FALLBACK: Long Put (single leg) - only if NOT preferring defined risk
                elif not self._config.prefer_defined_risk and self._config.long_put_enabled:
                    recommendations.append(OptionStrategy.LONG_PUT)
                    self._logger.log("strategy_selected_long_put", {
                        "reason": "bearish_trend",
                        "vwap_confirmed": vwap_allow_short
                    })
                # LAST RESORT: If spread disabled but defined risk preferred, use spread anyway if enabled
                elif self._config.bear_put_spread_enabled:
                    recommendations.append(OptionStrategy.BEAR_PUT_SPREAD)
                    self._logger.log("strategy_selected_bear_put_spread", {
                        "reason": "bearish_defined_risk_fallback",
                        "vwap_confirmed": vwap_allow_short
                    })
        
        # NEUTRAL TREND: Calendar Spread (theta), Straddle (high vol), or Iron Condor
        else:
            if self._config.straddle_enabled and volatility == MarketRegime.HIGH_VOLATILITY:
                # Straddle is non-directional - always allowed
                if OptionStrategy.STRADDLE not in recommendations:
                    recommendations.append(OptionStrategy.STRADDLE)
            # PREFER: Calendar Spread for theta harvesting in neutral markets (defined risk)
            elif self._config.prefer_defined_risk and self._config.calendar_spread_enabled:
                recommendations.append(OptionStrategy.CALENDAR_SPREAD)
                self._logger.log("strategy_selected_calendar_spread", {
                    "reason": "neutral_trend_theta_harvest_defined_risk"
                })
            # PREFER: Iron Condor for theta harvesting (defined risk)
            elif self._config.prefer_defined_risk and self._config.iron_condor_enabled:
                recommendations.append(OptionStrategy.IRON_CONDOR)
                self._logger.log("strategy_selected_iron_condor", {
                    "reason": "neutral_trend_defined_risk"
                })
            # FALLBACK: Long Call with bullish bias - only if NOT preferring defined risk
            elif not self._config.prefer_defined_risk and self._config.long_call_enabled and vwap_allow_long:
                if OptionStrategy.LONG_CALL not in recommendations:
                    recommendations.append(OptionStrategy.LONG_CALL)
            elif vwap_is_neutral:
                # VWAP NEUTRAL: No directional trades allowed - prefer Iron Condor
                self._logger.log("strategy_vwap_neutral_fallback", {
                    "reason": "VWAP posture NEUTRAL - only non-directional strategies allowed"
                })
        
        # =========================================================
        # STEP 3: SELL-SIDE FALLBACK (only if buy-side all disabled)
        # =========================================================
        if not recommendations:
            # LOW VOLATILITY REGIME: Favor Iron Condors (premium selling) - non-directional
            if favor_iron_condors and self._config.iron_condor_enabled:
                recommendations.append(OptionStrategy.IRON_CONDOR)
                self._logger.log("strategy_selected_iron_condor", {
                    "reason": "low_vix_stable_market"
                })
            # BULLISH: Bull Put Spread - BLOCKED if VWAP doesn't allow longs
            elif trend == MarketRegime.BULLISH and self._config.bull_put_spread_enabled:
                if vwap_allow_long:
                    recommendations.append(OptionStrategy.BULL_PUT_SPREAD)
                    self._logger.log("strategy_selected_bull_put_spread", {
                        "reason": "bullish_trend",
                        "vwap_confirmed": True
                    })
                else:
                    self._logger.log("strategy_vwap_blocked", {
                        "strategy": "bull_put_spread",
                        "reason": "VWAP posture blocks longs"
                    })
            # BEARISH: Bear Call Spread - BLOCKED if VWAP doesn't allow shorts
            elif trend == MarketRegime.BEARISH and self._config.bear_call_spread_enabled:
                if vwap_allow_short:
                    recommendations.append(OptionStrategy.BEAR_CALL_SPREAD)
                    self._logger.log("strategy_selected_bear_call_spread", {
                        "reason": "bearish_trend",
                        "vwap_confirmed": True
                    })
                else:
                    self._logger.log("strategy_vwap_blocked", {
                        "strategy": "bear_call_spread",
                        "reason": "VWAP posture blocks shorts"
                    })
            # FALLBACK: Bull Put Spread (bullish bias) - only if VWAP allows
            elif self._config.bull_put_spread_enabled and vwap_allow_long:
                recommendations.append(OptionStrategy.BULL_PUT_SPREAD)
                self._logger.log("strategy_fallback", {"strategy": "bull_put_spread", "vwap_confirmed": True})
            # FINAL FALLBACK: Iron Condor if VWAP is neutral (non-directional)
            elif vwap_is_neutral and self._config.iron_condor_enabled:
                recommendations.append(OptionStrategy.IRON_CONDOR)
                self._logger.log("strategy_vwap_neutral_iron_condor", {
                    "reason": "VWAP NEUTRAL - using non-directional Iron Condor"
                })
        
        return recommendations
    
    def _get_vwap_posture_for_ticker(self, ticker: str, price: float) -> Optional[PostureDecision]:
        """
        Get VWAP posture decision for a ticker to validate directional bias.
        
        VWAP Posture provides institutional-style direction confirmation:
        - BULLISH posture = prefer bull put spreads
        - BEARISH posture = prefer bear call spreads
        - NEUTRAL posture = prefer iron condors or skip
        
        Args:
            ticker: Stock symbol
            price: Current price
            
        Returns:
            PostureDecision or None if unavailable
        """
        try:
            # Get intraday bars for VWAP calculation
            bars = self._alpaca.get_bars(ticker, days=2, timeframe="5Min")
            if not bars or len(bars) < 10:
                return None
            
            # Convert to dict format
            vwap_bars = [
                {
                    "high": float(b.high) if hasattr(b, 'high') else float(b.get("high", 0)),
                    "low": float(b.low) if hasattr(b, 'low') else float(b.get("low", 0)),
                    "close": float(b.close) if hasattr(b, 'close') else float(b.get("close", 0)),
                    "volume": float(b.volume) if hasattr(b, 'volume') else float(b.get("volume", 0))
                }
                for b in bars
            ]
            
            vwap_manager = get_vwap_posture_manager(ticker)
            posture = vwap_manager.evaluate(
                bars=vwap_bars,
                current_price=price,
                intraday_bars=vwap_bars[-50:],
                bar_index=len(vwap_bars)
            )
            
            self._logger.log("options_vwap_posture", {
                "ticker": ticker,
                "posture": posture.posture.value,
                "allow_long": posture.allow_long,
                "allow_short": posture.allow_short,
                "distance_pct": round(posture.distance_from_vwap_pct, 3),
                "is_retest": posture.is_vwap_retest
            })
            
            return posture
            
        except Exception as e:
            self._logger.error(f"VWAP posture check failed for {ticker}: {e}")
            return None
    
    def _select_optimal_strategy(self, analysis: Dict[str, Any]) -> Optional[OptionStrategy]:
        """
        Select the optimal strategy from recommendations.
        
        Args:
            analysis: Market analysis with recommended strategies
            
        Returns:
            Selected OptionStrategy or None if no suitable strategy
        """
        recommended = analysis.get("recommended_strategies", [])
        
        if not recommended:
            return None
        
        # Return the first (highest priority) recommendation
        # Could be enhanced with scoring based on expected P&L, win rate, etc.
        return recommended[0]
    
    def _execute_strategy(self, ticker: str, strategy: OptionStrategy,
                         analysis: Dict[str, Any], max_daily_loss: float) -> Dict[str, Any]:
        """
        Execute the selected options strategy.
        
        Dispatches to strategy-specific execution methods.
        
        Args:
            ticker: Underlying symbol
            strategy: Strategy to execute
            analysis: Market analysis data
            max_daily_loss: Maximum loss budget
            
        Returns:
            Trade result dictionary with success, expected_profit, risk, error
        """
        result = {"success": False, "error": None, "expected_profit": 0, "risk": 0}
        
        try:
            underlying_price = analysis.get("price", 0)
            
            if underlying_price <= 0:
                result["error"] = "Invalid underlying price"
                return result
            
            # ML scoring gate - score the trade before executing
            if self._ml_enabled:
                hour = get_market_clock().now().hour
                day_of_week = get_market_clock().now().weekday()
                vix = analysis.get("vix", 20)
                ml_context = {
                    "symbol": ticker,
                    "side": strategy.value,
                    "signal_strength": 0.5,
                    "hour": hour,
                    "day_of_week": day_of_week,
                    "vix": vix,
                    "iv_percentile": analysis.get("iv_percentile", 50)
                }
                ml_score = self._ml_service.score_entry(ml_context)
                
                # Apply adaptive threshold based on market conditions
                adaptive_threshold = self._ml_service.get_adaptive_threshold(
                    self._ml_min_probability, vix=vix, is_earnings_season=False
                )
                
                if ml_score["probability"] < adaptive_threshold:
                    self._logger.log("options_ml_skip", {
                        "ticker": ticker,
                        "strategy": strategy.value,
                        "ml_probability": ml_score["probability"],
                        "threshold": adaptive_threshold,
                        "base_threshold": self._ml_min_probability,
                        "recommendation": ml_score["recommendation"]
                    })
                    result["error"] = f"ML probability {ml_score['probability']:.2f} below threshold"
                    return result
            
            # IV Percentile Entry Gate - check if IV is in favorable range for strategy
            iv_percentile = analysis.get("iv_percentile", 50)
            if not self._check_iv_gate(ticker, strategy.value, iv_percentile):
                result["error"] = f"IV percentile {iv_percentile:.1f} not favorable for {strategy.value}"
                return result
            
            # Dispatch to strategy-specific handler
            # BUY-SIDE STRATEGIES (no margin required)
            if strategy == OptionStrategy.LONG_CALL:
                return self._execute_long_call(ticker, underlying_price, max_daily_loss)
            elif strategy == OptionStrategy.LONG_PUT:
                return self._execute_long_put(ticker, underlying_price, max_daily_loss)
            elif strategy == OptionStrategy.STRADDLE:
                return self._execute_straddle(ticker, underlying_price, max_daily_loss)
            # DEBIT SPREADS (directional plays with defined risk)
            elif strategy == OptionStrategy.BULL_CALL_SPREAD:
                return self._execute_bull_call_spread(ticker, underlying_price, max_daily_loss)
            elif strategy == OptionStrategy.BEAR_PUT_SPREAD:
                return self._execute_bear_put_spread(ticker, underlying_price, max_daily_loss)
            # CREDIT SPREADS (premium collection with defined risk)
            elif strategy == OptionStrategy.BULL_PUT_SPREAD:
                return self._execute_bull_put_spread(ticker, underlying_price, max_daily_loss)
            elif strategy == OptionStrategy.BEAR_CALL_SPREAD:
                return self._execute_bear_call_spread(ticker, underlying_price, max_daily_loss)
            # NEUTRAL STRATEGIES (theta harvesting)
            elif strategy == OptionStrategy.IRON_CONDOR:
                return self._execute_iron_condor(ticker, underlying_price, max_daily_loss)
            elif strategy == OptionStrategy.CALENDAR_SPREAD:
                return self._execute_calendar_spread(ticker, underlying_price, max_daily_loss)
            else:
                result["error"] = f"Strategy {strategy.value} not implemented"
                
        except Exception as e:
            result["error"] = str(e)
            self._logger.error(f"Strategy execution failed: {e}")
        
        return result
    
    # =========================================================================
    # HAIL MARY - Cheap near-expiration options with massive upside potential
    # =========================================================================
    
    def _get_hail_mary_trades_today(self) -> int:
        """Get the number of hail mary trades executed today."""
        today_key = f"hail_mary_trades_{get_market_clock().now().strftime('%Y%m%d')}"
        return get_state(today_key, 0)
    
    def _increment_hail_mary_trades_today(self) -> None:
        """Increment hail mary trade counter for today."""
        today_key = f"hail_mary_trades_{get_market_clock().now().strftime('%Y%m%d')}"
        current = get_state(today_key, 0)
        set_state(today_key, current + 1)
    
    def _is_near_earnings(self, ticker: str, buffer_days: int = 3) -> bool:
        """
        Check if a ticker has earnings within buffer_days.
        Used to avoid IV crush risk on options entries.
        
        IV crush destroys option value post-earnings regardless of direction.
        Even if the stock moves the right way, options can lose 30-60% of value
        overnight due to IV collapsing after the earnings event.
        
        Args:
            ticker: Stock symbol to check
            buffer_days: Days before earnings to block entries
            
        Returns:
            True if earnings are within buffer_days (should block entry)
        """
        try:
            from ..services.earnings_calendar import get_earnings_calendar
            calendar = get_earnings_calendar()
            info = calendar.get_earnings_info(ticker)
            
            if info is None or info.report_date is None:
                return False
            
            days_until = info.days_until
            if days_until is None:
                return False
            
            is_near = 0 <= days_until <= buffer_days
            
            if is_near:
                self._logger.log("earnings_proximity_detected", {
                    "ticker": ticker,
                    "earnings_date": info.report_date,
                    "days_until": days_until,
                    "buffer_days": buffer_days,
                    "action": "BLOCK_ENTRY"
                })
            
            return is_near
            
        except Exception as e:
            self._logger.warn(f"Earnings check failed for {ticker} (fail-open): {e}")
            return False
    
    def _build_hail_mary_universe(self) -> List[str]:
        """
        Build a dynamic ticker universe for Hail Mary scans by merging
        multiple intelligence sources. Scans whatever is hot TODAY rather
        than a static list.
        
        Sources (in priority order, deduped):
        1. PreMarket Intelligence ranked opportunities (highest quality)
        2. Options Screener selected underlyings (IV/liquidity validated)
        3. Dynamic Universe from session preparation (already screened)
        4. Static fallback list from config (always included as safety net)
        
        Returns:
            Deduplicated list of tickers capped at dynamic_max_tickers
        """
        seen = set()
        universe = []
        source_counts = {}
        min_score = self._config.hail_mary_dynamic_min_score
        max_tickers = self._config.hail_mary_dynamic_max_tickers
        
        def _add_tickers(tickers: List[str], source: str):
            """Add tickers to universe, deduplicating."""
            added = 0
            for t in tickers:
                t_upper = t.upper().strip()
                if t_upper and t_upper not in seen and len(universe) < max_tickers:
                    seen.add(t_upper)
                    universe.append(t_upper)
                    added += 1
            source_counts[source] = added
        
        # SOURCE 1: PreMarket Intelligence ranked opportunities
        # These are the highest-quality signals — scored by gap, IV, volume, events
        try:
            from ..services.premarket_intelligence import PreMarketIntelligenceService
            intel_service = PreMarketIntelligenceService()
            cached = intel_service.get_cached_intelligence()
            
            if cached and cached.ranked_opportunities:
                ranked_tickers = []
                for ticker in cached.ranked_opportunities:
                    intel = cached.tickers.get(ticker)
                    if intel and intel.opportunity_score >= min_score:
                        ranked_tickers.append(ticker)
                    elif not intel:
                        ranked_tickers.append(ticker)
                _add_tickers(ranked_tickers, "premarket_intel")
        except Exception as e:
            self._logger.warn(f"HM universe: premarket intel failed (continuing): {e}")
        
        # SOURCE 2: Options Screener selected underlyings
        # IV rank, liquidity, spread width validated
        try:
            from ..services.options_screener import get_options_screener
            screener = get_options_screener()
            result = screener.screen()
            if result and result.selected_underlyings:
                _add_tickers(result.selected_underlyings, "options_screener")
        except Exception as e:
            self._logger.warn(f"HM universe: options screener failed (continuing): {e}")
        
        # SOURCE 3: Dynamic Universe from session preparation
        # Already computed by _prepare_session(), stored on self
        try:
            if hasattr(self, '_dynamic_universe') and self._dynamic_universe:
                _add_tickers(self._dynamic_universe, "dynamic_universe")
        except Exception as e:
            self._logger.warn(f"HM universe: dynamic universe failed (continuing): {e}")
        
        # SOURCE 4: Static fallback list from config (safety net)
        # Always included to ensure core liquid names are scanned
        static_tickers = self._config.hail_mary_tickers or []
        if static_tickers:
            _add_tickers(static_tickers, "static_config")
        
        # If all dynamic sources failed and no static list, use bot's main tickers
        if not universe:
            _add_tickers(self.tickers, "bot_tickers_fallback")
        
        self._logger.log("hail_mary_universe_built", {
            "total_tickers": len(universe),
            "source_counts": source_counts,
            "tickers": universe,
            "max_tickers": max_tickers,
            "min_score": min_score
        })
        
        return universe
    
    def _execute_hail_mary(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute hail mary options strategy - scan for and trade cheap near-term
        options with tight spreads and massive upside potential.
        
        Hail Mary Criteria:
        - Premium: $0.05 - $3.00 per contract (cheap lottery tickets)
        - Spread: < $0.50 (tight market for good fills)
        - DTE: 0-7 days (near-term for maximum leverage)
        - Strike: Up to 3% OTM (achievable moves)
        - Momentum: Stock moving in direction of the bet
        
        Risk Management:
        - Max $300 per trade (configurable)
        - Max 2 hail mary trades per day (configurable)
        - Separate from regular OptionsBot trade limits
        - Full ExitBot integration for lifecycle tracking
        
        Args:
            results: Results dictionary to update with hail mary outcomes
            
        Returns:
            Updated results dictionary
        """
        if not self._config or not self._config.hail_mary_enabled:
            return results
        
        # RISK INTEGRATION GATE - Check for VIX crisis, correlation halt, or global halt
        # Hail mary must respect the same safety gates as regular strategies
        try:
            risk_integration = get_risk_integration()
            risk_eval = risk_integration.evaluate_entry(
                symbol="MARKET",
                bot_name=f"{self.bot_id}_hail_mary",
                proposed_size_usd=self._config.hail_mary_max_risk_usd,
                is_bullish=True
            )
            if risk_eval and risk_eval.action in (RiskAction.SKIP_ENTRY, RiskAction.HALT_TRADING):
                self._logger.log("hail_mary_risk_gate_blocked", {
                    "action": risk_eval.action.value,
                    "reason": risk_eval.reason,
                    "gate_details": risk_eval.gate_details
                })
                return results
        except Exception as ri_err:
            self._logger.warn(f"Hail mary risk integration check failed: {ri_err}")
        
        # Check hail mary daily limit (separate from regular trade limit)
        hm_today = self._get_hail_mary_trades_today()
        if hm_today >= self._config.hail_mary_max_trades_per_day:
            self._logger.log("hail_mary_daily_limit_reached", {
                "trades_today": hm_today,
                "max_per_day": self._config.hail_mary_max_trades_per_day
            })
            return results
        
        # Determine tickers to scan — dynamic universe or static fallback
        if self._config.hail_mary_use_dynamic_universe:
            hm_tickers = self._build_hail_mary_universe()
        else:
            hm_tickers = self._config.hail_mary_tickers or self.tickers
        
        self._logger.log("hail_mary_scan_start", {
            "tickers": hm_tickers,
            "universe_mode": "dynamic" if self._config.hail_mary_use_dynamic_universe else "static",
            "max_premium": self._config.hail_mary_max_premium,
            "max_spread": self._config.hail_mary_max_spread,
            "dte_range": f"{self._config.hail_mary_dte_min}-{self._config.hail_mary_dte_max}",
            "trades_today": hm_today,
            "max_trades": self._config.hail_mary_max_trades_per_day,
            "earnings_protection": self._config.hail_mary_block_near_earnings,
            "vwap_posture": self._config.hail_mary_use_vwap_posture,
            "tiered_exits": self._config.hail_mary_tiered_exits
        })
        
        # Collect all opportunities across all tickers
        all_opportunities = []
        
        for ticker in hm_tickers:
            try:
                # EARNINGS IV CRUSH PROTECTION — block entries near earnings
                if self._config.hail_mary_block_near_earnings:
                    if self._is_near_earnings(ticker, self._config.hail_mary_earnings_buffer_days):
                        self._logger.log("hail_mary_earnings_blocked", {
                            "ticker": ticker,
                            "buffer_days": self._config.hail_mary_earnings_buffer_days,
                            "reason": "IV_CRUSH_RISK: options lose value post-earnings regardless of direction"
                        })
                        continue
                
                opportunities = self._scan_hail_mary_opportunities(ticker)
                all_opportunities.extend(opportunities)
            except Exception as e:
                self._logger.warn(f"Hail mary scan failed for {ticker}: {e}")
        
        if not all_opportunities:
            self._logger.log("hail_mary_no_opportunities", {
                "tickers_scanned": len(hm_tickers)
            })
            return results
        
        # Sort by score (best first) and execute the top opportunity
        all_opportunities.sort(key=lambda x: x["score"], reverse=True)
        
        self._logger.log("hail_mary_opportunities_found", {
            "total_opportunities": len(all_opportunities),
            "top_score": all_opportunities[0]["score"],
            "top_symbol": all_opportunities[0]["symbol"],
            "top_3": [{
                "symbol": o["symbol"],
                "underlying": o["underlying"],
                "type": o["type"],
                "strike": o["strike"],
                "mid": o["mid"],
                "spread": o["spread"],
                "score": o["score"]
            } for o in all_opportunities[:3]]
        })
        
        # Execute the best opportunity
        best = all_opportunities[0]
        trade_result = self._execute_hail_mary_trade(best)
        
        if trade_result.get("success"):
            results["trades_attempted"] += 1
            self._increment_hail_mary_trades_today()
            self._increment_trades_today()
            
            self._logger.log("hail_mary_trade_executed", {
                "symbol": best["symbol"],
                "underlying": best["underlying"],
                "type": best["type"],
                "strike": best["strike"],
                "contracts": trade_result.get("contracts", 0),
                "cost_per_contract": best["mid"],
                "total_risk": trade_result.get("total_risk", 0),
                "score": best["score"],
                "stock_price": best["stock_price"],
                "stock_change_pct": best["stock_change_pct"]
            })
        else:
            self._logger.log("hail_mary_trade_failed", {
                "symbol": best["symbol"],
                "error": trade_result.get("error", "Unknown")
            })
        
        return results
    
    def _scan_hail_mary_opportunities(self, ticker: str) -> List[Dict[str, Any]]:
        """
        Scan a single ticker's options chain for hail mary opportunities.
        
        Uses the Alpaca options chain API to find cheap, near-term options
        with tight bid/ask spreads. Applies momentum alignment filter to
        only buy calls when stock is green and puts when stock is red.
        
        Args:
            ticker: Underlying symbol to scan (e.g., "SPY", "NVDA")
            
        Returns:
            List of opportunity dictionaries sorted by score, each containing:
            - symbol, underlying, type, strike, expiry, bid, ask, mid, spread
            - delta, iv, stock_price, stock_change_pct, score, cost_per_contract
        """
        opportunities = []
        
        if not self._config:
            return opportunities
        
        try:
            # STEP 1: Get current stock price and momentum
            stock_data = self._alpaca.get_latest_quote(ticker)
            if not stock_data:
                return opportunities
            
            bid_price = float(stock_data.get("bid", stock_data.get("bid_price", 0)) or 0)
            ask_price = float(stock_data.get("ask", stock_data.get("ask_price", 0)) or 0)
            stock_price = (bid_price + ask_price) / 2 if bid_price > 0 and ask_price > 0 else 0
            
            # Get recent bars for momentum calculation
            bars = self._alpaca.get_bars(ticker, timeframe="1Day", limit=3)
            if not bars or len(bars) < 2:
                return opportunities
            
            prev_close = float(getattr(bars[-2], 'close', 0) or 0)
            today_close = float(getattr(bars[-1], 'close', 0) or 0)
            
            # Use bar close if quote unavailable (market closed)
            if stock_price <= 0:
                stock_price = today_close
            
            if prev_close <= 0 or stock_price <= 0:
                return opportunities
            
            change_pct = ((today_close - prev_close) / prev_close) * 100
            
            # STEP 2: Determine direction based on momentum + VWAP posture
            is_bullish = change_pct > 0
            
            # Apply momentum filter
            if self._config.hail_mary_require_momentum:
                if abs(change_pct) < self._config.hail_mary_min_stock_change:
                    self._logger.log("hail_mary_weak_momentum", {
                        "ticker": ticker,
                        "change_pct": round(change_pct, 2),
                        "min_required": self._config.hail_mary_min_stock_change
                    })
                    return opportunities
            
            # VWAP POSTURE CONFIRMATION — use institutional VWAP to confirm direction
            if self._config.hail_mary_use_vwap_posture:
                try:
                    posture = self._get_vwap_posture_for_ticker(ticker, stock_price)
                    
                    if posture and posture.posture != VWAPPosture.NEUTRAL:
                        vwap_bullish = posture.posture == VWAPPosture.BULLISH
                        vwap_bearish = posture.posture == VWAPPosture.BEARISH
                        
                        if is_bullish and vwap_bearish:
                            self._logger.log("hail_mary_vwap_conflict", {
                                "ticker": ticker,
                                "momentum": "bullish",
                                "vwap_posture": "BEARISH",
                                "action": "blocking_call_entry"
                            })
                            is_bullish = False
                        elif not is_bullish and vwap_bullish:
                            self._logger.log("hail_mary_vwap_conflict", {
                                "ticker": ticker,
                                "momentum": "bearish",
                                "vwap_posture": "BULLISH",
                                "action": "blocking_put_entry"
                            })
                            is_bullish = True
                        else:
                            self._logger.log("hail_mary_vwap_confirmed", {
                                "ticker": ticker,
                                "momentum": "bullish" if is_bullish else "bearish",
                                "vwap_posture": posture.posture.value
                            })
                    elif posture and posture.posture == VWAPPosture.NEUTRAL:
                        self._logger.log("hail_mary_vwap_neutral", {
                            "ticker": ticker,
                            "action": "allowing_momentum_direction",
                            "reason": "VWAP neutral, falling back to price momentum"
                        })
                except Exception as vwap_err:
                    self._logger.warn(f"VWAP posture check failed for {ticker} (fail-open): {vwap_err}")
            
            # STEP 3: Calculate strike range for OTM options
            otm_range = stock_price * (self._config.hail_mary_strike_otm_pct / 100)
            
            # For calls: scan strikes above current price
            # For puts: scan strikes below current price
            # Scan BOTH directions but weight towards momentum direction
            exp_start = get_market_clock().now()
            exp_end = exp_start + timedelta(days=self._config.hail_mary_dte_max)
            
            # Scan calls (OTM = above current price)
            call_chain = self._alpaca.get_options_chain(
                underlying_symbol=ticker,
                expiration_date_gte=exp_start.strftime("%Y-%m-%d"),
                expiration_date_lte=exp_end.strftime("%Y-%m-%d"),
                strike_price_gte=round(stock_price, 0),
                strike_price_lte=round(stock_price + otm_range, 0),
                option_type="call"
            )
            
            # Scan puts (OTM = below current price)
            put_chain = self._alpaca.get_options_chain(
                underlying_symbol=ticker,
                expiration_date_gte=exp_start.strftime("%Y-%m-%d"),
                expiration_date_lte=exp_end.strftime("%Y-%m-%d"),
                strike_price_gte=round(stock_price - otm_range, 0),
                strike_price_lte=round(stock_price, 0),
                option_type="put"
            )
            
            # Combine chains
            all_contracts = []
            if call_chain:
                for c in call_chain:
                    c["_option_type"] = "call"
                    all_contracts.append(c)
            if put_chain:
                for p in put_chain:
                    p["_option_type"] = "put"
                    all_contracts.append(p)
            
            # STEP 4: Filter and score each contract
            for contract in all_contracts:
                opt_bid = float(contract.get("bid", 0) or 0)
                opt_ask = float(contract.get("ask", 0) or 0)
                
                # Skip contracts with no market
                if opt_bid <= 0 or opt_ask <= 0:
                    continue
                
                opt_mid = (opt_bid + opt_ask) / 2
                spread = opt_ask - opt_bid
                
                # Apply hail mary filters
                if opt_mid < self._config.hail_mary_min_premium:
                    continue
                if opt_mid > self._config.hail_mary_max_premium:
                    continue
                if spread > self._config.hail_mary_max_spread:
                    continue
                
                # Delta filter
                delta = abs(float(contract.get("delta", 0) or 0))
                if delta < self._config.hail_mary_min_delta:
                    continue
                if delta > self._config.hail_mary_max_delta:
                    continue
                
                option_type = contract.get("_option_type", contract.get("type", "call"))
                
                # Momentum alignment: only buy calls on green days, puts on red days
                if self._config.hail_mary_require_momentum:
                    if option_type == "call" and not is_bullish:
                        continue
                    if option_type == "put" and is_bullish:
                        continue
                
                # Score the opportunity
                # Tight spreads (most important for fill quality)
                spread_score = max(0, 1 - (spread / self._config.hail_mary_max_spread))
                # Cheaper options = more leverage
                price_score = max(0, 1 - (opt_mid / self._config.hail_mary_max_premium))
                # Higher delta = more price capture (but still capped at 0.30)
                delta_score = min(delta / self._config.hail_mary_max_delta, 1.0)
                
                total_score = (
                    spread_score * 0.40 +
                    price_score * 0.30 +
                    delta_score * 0.30
                )
                
                strike = float(contract.get("strike", 0) or 0)
                expiry = contract.get("expiry", "unknown")
                iv = float(contract.get("iv", 0) or 0)
                symbol = contract.get("symbol", "")
                
                opportunities.append({
                    "symbol": symbol,
                    "underlying": ticker,
                    "type": option_type.upper(),
                    "strike": strike,
                    "expiry": expiry,
                    "bid": opt_bid,
                    "ask": opt_ask,
                    "mid": round(opt_mid, 2),
                    "spread": round(spread, 2),
                    "delta": round(delta, 4),
                    "iv": round(iv * 100, 1) if iv < 10 else round(iv, 1),
                    "stock_price": round(stock_price, 2),
                    "stock_change_pct": round(change_pct, 2),
                    "score": round(total_score, 3),
                    "cost_per_contract": round(opt_mid * 100, 2)
                })
            
            self._logger.log("hail_mary_ticker_scan_complete", {
                "ticker": ticker,
                "stock_price": round(stock_price, 2),
                "change_pct": round(change_pct, 2),
                "contracts_scanned": len(all_contracts),
                "opportunities_found": len(opportunities)
            })
            
        except Exception as e:
            self._logger.warn(f"Hail mary scan error for {ticker}: {e}")
        
        return opportunities
    
    def _execute_hail_mary_trade(self, opportunity: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a hail mary trade for the given opportunity.
        
        Places a limit order at the ask price for immediate fill.
        Registers with ExitBot for lifecycle tracking.
        Sizes position based on max risk budget.
        
        Args:
            opportunity: Opportunity dictionary from _scan_hail_mary_opportunities
            
        Returns:
            Trade result dictionary with success, contracts, total_risk, error
        """
        result = {"success": False, "error": None, "contracts": 0, "total_risk": 0}
        
        if not self._config:
            result["error"] = "No configuration"
            return result
        
        # Skip if dry run
        if self._config.dry_run:
            self._logger.log("hail_mary_dry_run_skip", {
                "symbol": opportunity["symbol"],
                "mid": opportunity["mid"],
                "score": opportunity["score"]
            })
            result["error"] = "Dry run mode - trade not executed"
            return result
        
        try:
            symbol = opportunity["symbol"]
            ask_price = opportunity["ask"]
            mid_price = opportunity["mid"]
            ticker = opportunity["underlying"]
            
            # Position sizing: how many contracts can we buy within risk budget?
            max_risk = self._config.hail_mary_max_risk_usd
            min_risk = self._config.hail_mary_min_risk_usd
            cost_per_contract = ask_price * 100  # Each contract = 100 shares
            
            if cost_per_contract <= 0:
                result["error"] = "Zero cost per contract"
                return result
            
            contracts = int(max_risk / cost_per_contract)
            contracts = max(1, contracts)  # At least 1 contract
            
            total_risk = contracts * cost_per_contract
            
            # Check minimum risk threshold (avoid placing dust trades)
            if total_risk < min_risk and contracts == 1:
                # Single contract but below minimum - still place it if cost is reasonable
                if cost_per_contract < 5.0:  # Under $5 is too cheap even for hail mary
                    result["error"] = f"Trade too small: ${total_risk:.2f} < ${min_risk:.2f} minimum"
                    return result
            
            # Buying power check
            bp_check = self._check_buying_power_for_debit(total_risk)
            if not bp_check.get("approved", False):
                result["error"] = f"Insufficient buying power: {bp_check.get('reason')}"
                return result
            
            # Spread gate check (extra safety)
            spread_check = self._check_spread_gate(
                opportunity["bid"], opportunity["ask"], symbol
            )
            if not spread_check.get("approved", False):
                result["error"] = f"Spread gate rejected: {spread_check.get('reason')}"
                return result
            
            # ExitBot health check (fail-closed)
            exitbot = get_exitbot()
            if not exitbot.is_healthy():
                self._logger.warn(f"ExitBot unhealthy - blocking hail mary entry for {symbol}")
                result["error"] = "ExitBot unhealthy - entry blocked"
                return result
            
            # Generate signal identity for ExitBot tracking
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            signal_id = f"HM_{ticker}_hail_mary_{ts}_{uuid4().hex[:6]}"
            client_order_id = f"options_bot:{ticker}:hail_mary:{signal_id}"
            
            # Place limit order at ask for immediate fill
            limit_price = round(ask_price, 2)
            
            self._logger.log("hail_mary_order_attempt", {
                "symbol": symbol,
                "ticker": ticker,
                "strike": opportunity["strike"],
                "type": opportunity["type"],
                "expiry": opportunity["expiry"],
                "contracts": contracts,
                "limit_price": limit_price,
                "total_risk": round(total_risk, 2),
                "signal_id": signal_id,
                "score": opportunity["score"],
                "stock_price": opportunity["stock_price"],
                "stock_change_pct": opportunity["stock_change_pct"]
            })
            
            order = self._alpaca.place_options_order(
                symbol=symbol,
                qty=contracts,
                side="buy",
                order_type="limit",
                limit_price=limit_price,
                client_order_id=client_order_id,
                position_intent="buy_to_open"
            )
            
            order_id = order.get("id", "unknown")
            
            self._logger.log("hail_mary_order_placed", {
                "symbol": symbol,
                "order_id": order_id,
                "contracts": contracts,
                "limit_price": limit_price,
                "total_risk": round(total_risk, 2),
                "signal_id": signal_id
            })
            
            # Hail mary uses its own lightweight exit logic by default
            # Only register with ExitBot if explicitly configured (use_exitbot: true)
            if order_id and order_id != "unknown":
                if self._config and self._config.hail_mary_use_exitbot:
                    options_context = {
                        "underlying": ticker,
                        "expiry": opportunity.get("expiry", ""),
                        "strike": opportunity.get("strike", 0),
                        "right": opportunity.get("type", "CALL").lower(),
                        "bid": opportunity.get("bid", 0),
                        "ask": opportunity.get("ask", 0),
                        "strategy": "hail_mary"
                    }
                    
                    if opportunity.get("delta"):
                        options_context["greeks"] = {
                            "delta": opportunity.get("delta", 0)
                        }
                    
                    exitbot.register_entry_intent(
                        bot_id="options_bot",
                        symbol=symbol,
                        side="long",
                        qty=contracts,
                        entry_price=limit_price,
                        signal_id=signal_id,
                        client_order_id=client_order_id,
                        alpaca_order_id=order_id,
                        asset_class="option",
                        options=options_context
                    )
                    self._logger.log("hail_mary_exitbot_registered", {"symbol": symbol, "order_id": order_id})
                else:
                    self._logger.log("hail_mary_self_managed_exit", {
                        "symbol": symbol,
                        "order_id": order_id,
                        "profit_target_mult": self._config.hail_mary_profit_target_mult if self._config else 5.0,
                        "time_exit_days": self._config.hail_mary_time_exit_days if self._config else 1,
                        "reason": "Hail mary manages own exits: premium IS the stop, target is Nx profit"
                    })
            
            # Record trade in state for persistence
            trade_record = {
                "strategy": "hail_mary",
                "strategy_type": "debit",
                "ticker": ticker,
                "contracts": contracts,
                "symbol": symbol,
                "strike": opportunity["strike"],
                "type": opportunity["type"],
                "expiry": opportunity["expiry"],
                "entry_price": limit_price,
                "total_risk": round(total_risk, 2),
                "order_id": order_id,
                "signal_id": signal_id,
                "timestamp": get_market_clock().now().isoformat(),
                "stock_price": opportunity["stock_price"],
                "stock_change_pct": opportunity["stock_change_pct"],
                "score": opportunity["score"],
                "spread": opportunity["spread"],
                "delta": opportunity["delta"]
            }
            
            # Persist trade record
            trade_key = f"hail_mary_trade_{signal_id}"
            set_state(trade_key, json.dumps(trade_record))
            
            result["success"] = True
            result["contracts"] = contracts
            result["total_risk"] = round(total_risk, 2)
            result["order_id"] = order_id
            
        except Exception as e:
            result["error"] = f"Hail mary execution failed: {e}"
            self._logger.error(f"Hail mary trade error: {e}")
        
        return result
    
    # =========================================================================
    # HAIL MARY EXIT MANAGEMENT - Lightweight self-managed exits
    # No stop-loss (premium IS the stop), profit target at Nx, time exit before expiry
    # =========================================================================
    
    def _manage_hail_mary_exits(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Manage exits for open hail mary positions. Runs every loop.
        
        Supports TIERED PROFIT TAKING (graduated exits):
        - Tier 1: Sell 50% at 3x to lock in guaranteed profit
        - Tier 2: Sell 25% at 5x for strong gains
        - Runner: Let remaining 25% ride to 10x+ for moonshot upside
        
        Fallback (tiered_exits=False): All-or-nothing at profit_target_multiplier.
        
        Time exit: Close before expiry to salvage remaining value.
        Premium IS the stop-loss — no stop needed.
        
        Args:
            results: Results dictionary to update
            
        Returns:
            Updated results dictionary
        """
        if not self._config or not self._config.hail_mary_enabled:
            return results
        
        use_tiered = self._config.hail_mary_tiered_exits
        profit_mult = self._config.hail_mary_profit_target_mult
        time_exit_days = self._config.hail_mary_time_exit_days
        
        try:
            positions = self._alpaca.get_positions()
        except Exception as e:
            self._logger.warn(f"Hail mary exit check: failed to fetch positions: {e}")
            return results
        
        options_positions = [
            p for p in positions 
            if p.asset_class == "us_option" or (len(p.symbol) > 10 and any(c in p.symbol for c in "CP"))
        ]
        
        if not options_positions:
            return results
        
        hm_records = {}
        try:
            hm_keys = get_keys_by_prefix("hail_mary_trade_")
            for key in hm_keys:
                try:
                    raw = get_state(key)
                    if not raw:
                        continue
                    record = json.loads(raw) if isinstance(raw, str) else raw
                    if not isinstance(record, dict):
                        continue
                    record_symbol = record.get("symbol", "")
                    if record_symbol and record.get("entry_price") and not record.get("exit_price"):
                        hm_records[record_symbol.upper().strip()] = record
                except Exception as rec_err:
                    self._logger.warn(f"Hail mary exit: skipping bad record {key}: {rec_err}")
                    continue
        except Exception as e:
            self._logger.warn(f"Hail mary exit: failed to load trade records: {e}")
            return results
        
        if not hm_records:
            return results
        
        self._logger.log("hail_mary_exit_check_start", {
            "options_positions": len(options_positions),
            "hm_records": len(hm_records),
            "tiered_exits": use_tiered,
            "tiers": f"{self._config.hail_mary_tier1_mult}x/{self._config.hail_mary_tier2_mult}x/{self._config.hail_mary_runner_mult}x" if use_tiered else f"{profit_mult}x",
            "time_exit_days": time_exit_days
        })
        
        today = get_market_clock().now().date()
        
        for pos in options_positions:
            symbol = pos.symbol.upper().strip()
            
            if symbol not in hm_records:
                continue
            
            record = hm_records[symbol]
            entry_price = record.get("entry_price", 0)
            expiry_str = record.get("expiry", "")
            current_price = pos.current_price
            qty = abs(int(pos.qty))
            original_contracts = record.get("contracts", qty)
            
            if entry_price <= 0 or qty <= 0:
                continue
            
            profit_multiple = current_price / entry_price if entry_price > 0 else 0
            
            try:
                from datetime import datetime as dt_class
                expiry_date = dt_class.strptime(expiry_str, "%Y-%m-%d").date()
                days_to_expiry = (expiry_date - today).days
            except (ValueError, TypeError):
                days_to_expiry = 999
            
            # TIME EXIT — always takes priority, close all remaining contracts
            if days_to_expiry <= time_exit_days and current_price > 0.01:
                exit_reason = f"TIME_EXIT_{days_to_expiry}d_to_expiry"
                self._place_hail_mary_exit(symbol, qty, entry_price, current_price,
                                           profit_multiple, days_to_expiry, exit_reason,
                                           record, results, is_full_exit=True)
                continue
            
            if use_tiered and original_contracts >= 2:
                # TIERED PROFIT TAKING — graduated exits
                tiers_hit = record.get("tiers_hit", [])
                
                # Tier 1: sell portion at tier1_mult (e.g., 3x)
                if profit_multiple >= self._config.hail_mary_tier1_mult and "tier1" not in tiers_hit:
                    tier1_qty = max(1, int(original_contracts * (self._config.hail_mary_tier1_pct / 100.0)))
                    tier1_qty = min(tier1_qty, qty)
                    
                    if tier1_qty > 0:
                        exit_reason = f"TIER1_{self._config.hail_mary_tier1_mult}x_PROFIT"
                        self._place_hail_mary_exit(symbol, tier1_qty, entry_price, current_price,
                                                   profit_multiple, days_to_expiry, exit_reason,
                                                   record, results, is_full_exit=False)
                        tiers_hit.append("tier1")
                        record["tiers_hit"] = tiers_hit
                        trade_key = f"hail_mary_trade_{record.get('signal_id', symbol)}"
                        set_state(trade_key, json.dumps(record))
                        
                        self._logger.log("hail_mary_tier1_exit", {
                            "symbol": symbol,
                            "sold_qty": tier1_qty,
                            "remaining_qty": qty - tier1_qty,
                            "profit_multiple": round(profit_multiple, 2),
                            "tier_mult": self._config.hail_mary_tier1_mult,
                            "pct_sold": self._config.hail_mary_tier1_pct
                        })
                        continue
                
                # Tier 2: sell portion at tier2_mult (e.g., 5x)
                if profit_multiple >= self._config.hail_mary_tier2_mult and "tier2" not in tiers_hit:
                    tier2_qty = max(1, int(original_contracts * (self._config.hail_mary_tier2_pct / 100.0)))
                    tier2_qty = min(tier2_qty, qty)
                    
                    if tier2_qty > 0:
                        exit_reason = f"TIER2_{self._config.hail_mary_tier2_mult}x_PROFIT"
                        self._place_hail_mary_exit(symbol, tier2_qty, entry_price, current_price,
                                                   profit_multiple, days_to_expiry, exit_reason,
                                                   record, results, is_full_exit=False)
                        tiers_hit.append("tier2")
                        record["tiers_hit"] = tiers_hit
                        trade_key = f"hail_mary_trade_{record.get('signal_id', symbol)}"
                        set_state(trade_key, json.dumps(record))
                        
                        self._logger.log("hail_mary_tier2_exit", {
                            "symbol": symbol,
                            "sold_qty": tier2_qty,
                            "remaining_qty": qty - tier2_qty,
                            "profit_multiple": round(profit_multiple, 2),
                            "tier_mult": self._config.hail_mary_tier2_mult,
                            "pct_sold": self._config.hail_mary_tier2_pct
                        })
                        continue
                
                # Runner: sell all remaining at runner_mult (e.g., 10x)
                if profit_multiple >= self._config.hail_mary_runner_mult:
                    exit_reason = f"RUNNER_{self._config.hail_mary_runner_mult}x_MOONSHOT"
                    self._place_hail_mary_exit(symbol, qty, entry_price, current_price,
                                               profit_multiple, days_to_expiry, exit_reason,
                                               record, results, is_full_exit=True)
                    
                    self._logger.log("hail_mary_runner_exit", {
                        "symbol": symbol,
                        "sold_qty": qty,
                        "profit_multiple": round(profit_multiple, 2),
                        "runner_mult": self._config.hail_mary_runner_mult,
                        "tiers_completed": tiers_hit
                    })
                    continue
                
                # Holding runner position — log status
                tier_status = "RUNNER" if "tier2" in tiers_hit else ("POST_TIER1" if "tier1" in tiers_hit else "PRE_TIER1")
                next_target = (self._config.hail_mary_tier1_mult if "tier1" not in tiers_hit 
                              else self._config.hail_mary_tier2_mult if "tier2" not in tiers_hit
                              else self._config.hail_mary_runner_mult)
                
                self._logger.log("hail_mary_position_holding", {
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "profit_multiple": round(profit_multiple, 2),
                    "days_to_expiry": days_to_expiry,
                    "tier_status": tier_status,
                    "next_target": f"{next_target}x",
                    "qty_remaining": qty,
                    "tiers_hit": tiers_hit,
                    "status": "HOLDING"
                })
            else:
                # NON-TIERED (legacy) — all-or-nothing at profit_target_multiplier
                if profit_multiple >= profit_mult:
                    exit_reason = f"PROFIT_TARGET_{profit_mult}x"
                    self._place_hail_mary_exit(symbol, qty, entry_price, current_price,
                                               profit_multiple, days_to_expiry, exit_reason,
                                               record, results, is_full_exit=True)
                else:
                    self._logger.log("hail_mary_position_holding", {
                        "symbol": symbol,
                        "entry_price": entry_price,
                        "current_price": current_price,
                        "profit_multiple": round(profit_multiple, 2),
                        "days_to_expiry": days_to_expiry,
                        "target_mult": profit_mult,
                        "status": "HOLDING"
                    })
        
        return results
    
    def _place_hail_mary_exit(self, symbol: str, qty: int, entry_price: float,
                               current_price: float, profit_multiple: float,
                               days_to_expiry: int, exit_reason: str,
                               record: Dict[str, Any], results: Dict[str, Any],
                               is_full_exit: bool = True) -> None:
        """
        Place a hail mary exit order (sell-to-close) for specified quantity.
        Handles both partial (tiered) and full exits.
        
        Args:
            symbol: Options contract symbol
            qty: Number of contracts to sell
            entry_price: Original entry price
            current_price: Current market price
            profit_multiple: Current profit multiple (current/entry)
            days_to_expiry: Days until option expiry
            exit_reason: Reason string for the exit
            record: Trade record dict to update
            results: Results dict to update
            is_full_exit: If True, marks record as fully exited
        """
        try:
            self._logger.log("hail_mary_exit_triggered", {
                "symbol": symbol,
                "ticker": record.get("ticker", ""),
                "entry_price": entry_price,
                "current_price": current_price,
                "profit_multiple": round(profit_multiple, 2),
                "days_to_expiry": days_to_expiry,
                "qty": qty,
                "is_full_exit": is_full_exit,
                "reason": exit_reason
            })
            
            exit_order = self._alpaca.place_options_order(
                symbol=symbol,
                qty=qty,
                side="sell",
                order_type="market",
                position_intent="sell_to_close",
                client_order_id=f"hm_exit_{symbol}_{exit_reason[:20]}_{int(get_market_clock().now().timestamp())}"
            )
            
            exit_order_id = exit_order.get("id", "unknown")
            pnl_per_contract = (current_price - entry_price) * 100
            total_pnl = pnl_per_contract * qty
            
            self._logger.log("hail_mary_exit_placed", {
                "symbol": symbol,
                "exit_order_id": exit_order_id,
                "qty": qty,
                "entry_price": entry_price,
                "exit_price": current_price,
                "profit_multiple": round(profit_multiple, 2),
                "pnl_per_contract": round(pnl_per_contract, 2),
                "total_pnl": round(total_pnl, 2),
                "is_full_exit": is_full_exit,
                "reason": exit_reason
            })
            
            # Track partial exit P&L in record
            partial_exits = record.get("partial_exits", [])
            partial_exits.append({
                "qty": qty,
                "price": current_price,
                "pnl": round(total_pnl, 2),
                "multiple": round(profit_multiple, 2),
                "reason": exit_reason,
                "timestamp": get_market_clock().now().isoformat()
            })
            record["partial_exits"] = partial_exits
            
            if is_full_exit:
                record["exit_price"] = current_price
                record["exit_reason"] = exit_reason
                record["exit_timestamp"] = get_market_clock().now().isoformat()
                total_realized = sum(pe.get("pnl", 0) for pe in partial_exits)
                record["pnl"] = round(total_realized, 2)
                record["profit_multiple"] = round(profit_multiple, 2)
                record["exit_order_id"] = exit_order_id
            
            trade_key = f"hail_mary_trade_{record.get('signal_id', symbol)}"
            set_state(trade_key, json.dumps(record))
            
            results["positions_managed"] = results.get("positions_managed", 0) + 1
            
        except Exception as exit_err:
            self._logger.error(f"Hail mary exit failed for {symbol}: {exit_err}")
            results["errors"].append(f"HM exit failed {symbol}: {exit_err}")
    
    # =========================================================================
    # BUY-SIDE STRATEGY EXECUTION (no margin required, just pay premium)
    # =========================================================================
    
    def _execute_long_call(self, ticker: str, underlying_price: float,
                          max_daily_loss: float) -> Dict[str, Any]:
        """
        Execute a long call strategy (buy call option).
        
        Long Call: Buy a call option
        - Max profit = unlimited (stock can go up indefinitely)
        - Max loss = premium paid
        - Profits when stock price rises above strike + premium
        
        Args:
            ticker: Underlying symbol
            underlying_price: Current stock price
            max_daily_loss: Maximum loss budget
            
        Returns:
            Trade result dictionary
        """
        result = {"success": False, "error": None, "expected_profit": 0, "risk": 0}
        
        if not self._config:
            result["error"] = "No configuration available"
            return result
        
        try:
            config = self.strategy_configs.get(OptionStrategy.LONG_CALL, {})
            
            # Generate call options chain
            call_chain = self._generate_options_chain(ticker, underlying_price, "call")
            
            if not call_chain:
                result["error"] = "No options chain available"
                return result
            
            # Filter for suitable calls:
            # - Delta in range (not too OTM, not too ITM)
            # - Cost within budget
            max_cost = config.get("max_cost", 3.00)
            min_delta = config.get("min_delta", 0.30)
            max_delta = config.get("max_delta", 0.60)
            
            # Filter calls above current price (OTM/ATM) with appropriate delta
            suitable_calls = [
                c for c in call_chain
                if c["strike"] >= underlying_price * 0.98  # Slightly ITM to ATM/OTM
                and c["ask"] <= max_cost
                and abs(c.get("delta", 0.40)) >= min_delta
                and abs(c.get("delta", 0.40)) <= max_delta
            ]
            
            if not suitable_calls:
                # Fallback: just find cheapest call near the money
                near_money_calls = [
                    c for c in call_chain
                    if c["strike"] >= underlying_price * 0.95
                    and c["strike"] <= underlying_price * 1.10
                    and c["ask"] <= max_cost
                ]
                if near_money_calls:
                    suitable_calls = sorted(near_money_calls, key=lambda x: x["ask"])
            
            if not suitable_calls:
                result["error"] = f"No suitable calls found (max_cost=${max_cost})"
                return result
            
            # Select the best call (closest to ATM with good delta)
            selected_call = min(suitable_calls, key=lambda x: abs(x["strike"] - underlying_price))
            
            # Position sizing based on max loss budget
            call_cost = selected_call["ask"]
            max_allowed = min(max_daily_loss * 0.5, self._config.max_position_size_usd)
            contracts = max(1, int(max_allowed / (call_cost * 100)))
            
            # Cap at reasonable size
            contracts = min(contracts, 5)
            
            # Get option symbol
            call_symbol = selected_call.get("symbol")
            if not call_symbol:
                result["error"] = "Missing call symbol"
                return result
            
            # SPREAD GATE - Reject wide-market trades (Safety Priority 0)
            bid = selected_call.get("bid", 0)
            ask = selected_call.get("ask", 0)
            spread_check = self._check_spread_gate(bid, ask, call_symbol)
            if not spread_check.get("approved", False):
                result["error"] = f"Spread gate rejected: {spread_check.get('reason')}"
                self._logger.log("long_call_rejected_spread_gate", {
                    "symbol": call_symbol,
                    "ticker": ticker,
                    "spread_pct": spread_check.get("spread_pct", 999.0),
                    "reason": spread_check.get("reason")
                })
                return result
            
            # PRE-TRADE BUYING POWER CHECK for long options
            total_cost = call_cost * contracts * 100
            buying_power_check = self._check_buying_power_for_debit(total_cost)
            if not buying_power_check.get("approved", False):
                result["error"] = f"Insufficient buying power: {buying_power_check.get('reason')}"
                self._logger.log("long_call_rejected_buying_power", buying_power_check)
                return result
            
            # =====================================================================
            # ExitBot v2 Integration - Generate signal identity BEFORE order
            # =====================================================================
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            signal_id = f"OP_{ticker}_long_call_{ts}_{uuid4().hex[:6]}"
            client_order_id = f"options_bot:{ticker}:long_call:{signal_id}"
            
            # Check ExitBot health before entry (fail-closed enforcement)
            exitbot = get_exitbot()
            if not exitbot.is_healthy():
                self._logger.warn(f"ExitBot unhealthy - blocking entry for {call_symbol}")
                return {"success": False, "error": "ExitBot unhealthy - entry blocked"}
            
            self._logger.log("long_call_entry_attempt", {
                "symbol": call_symbol,
                "ticker": ticker,
                "strike": selected_call["strike"],
                "cost": call_cost,
                "contracts": contracts,
                "signal_id": signal_id,
                "client_order_id": client_order_id
            })
            
            # Place buy order
            try:
                order = self._alpaca.place_options_order(
                    symbol=call_symbol,
                    qty=contracts,
                    side="buy",
                    order_type="limit",
                    limit_price=round(call_cost * 1.02, 2)  # Slightly above ask for fill
                )
                order_id = order.get("id", "unknown")
                
                self._logger.log("long_call_placed", {
                    "symbol": call_symbol,
                    "ticker": ticker,
                    "strike": selected_call["strike"],
                    "cost": call_cost,
                    "contracts": contracts,
                    "order_id": order_id,
                    "signal_id": signal_id
                })
            except Exception as e:
                result["error"] = f"Failed to place options order: {e}"
                return result
            
            # =====================================================================
            # ExitBot v2 - Register entry intent for lifecycle tracking
            # =====================================================================
            if order_id and order_id != "unknown":
                # Prepare options context for ExitBot
                options_context = {
                    "underlying": ticker,
                    "expiry": selected_call.get("expiry", ""),
                    "strike": selected_call.get("strike", 0),
                    "right": "call",
                    "bid": selected_call.get("bid", 0),
                    "ask": selected_call.get("ask", 0)
                }
                
                # Add Greeks if available
                if "delta" in selected_call:
                    options_context["greeks"] = {
                        "delta": selected_call.get("delta", 0),
                        "gamma": selected_call.get("gamma", 0),
                        "theta": selected_call.get("theta", 0),
                        "vega": selected_call.get("vega", 0),
                        "rho": selected_call.get("rho", 0)
                    }
                
                position_key = exitbot.register_entry_intent(
                    bot_id="options_bot",
                    symbol=call_symbol,
                    side="long",
                    qty=contracts,
                    entry_price=call_cost,
                    signal_id=signal_id,
                    client_order_id=client_order_id,
                    alpaca_order_id=order_id,
                    asset_class="option",
                    options=options_context
                )
            else:
                position_key = None
            
            # Calculate trade metrics for long option (max loss = premium paid)
            total_premium = call_cost * contracts * 100
            max_loss = total_premium  # Max loss is the premium paid
            
            # Record trade with fields compatible with spread management
            trade_data = {
                "strategy": "long_call",
                "strategy_type": "debit",  # Long options are debit trades
                "ticker": ticker,
                "contracts": contracts,
                "strike": selected_call["strike"],
                "option_symbol": call_symbol,
                "cost": call_cost,
                "total_cost": total_premium,
                "premium_paid": total_premium,  # For debit trades
                "credit_received": 0.0,  # No credit for long options
                "max_loss": max_loss,  # Premium paid is max loss
                "timestamp": get_market_clock().now().timestamp(),
                "entry_time": get_market_clock().now().isoformat(),
                "order_id": order_id,
                "underlying_price": underlying_price,
                "signal_id": signal_id,
                "client_order_id": client_order_id,
                "position_key": position_key
            }
            
            trade_key = f"options_trades.{self.bot_id}.{int(trade_data['timestamp'])}"
            set_state(trade_key, json.dumps(trade_data))
            
            entry_key = f"entry_time.{self.bot_id}.{ticker}"
            set_state(entry_key, get_market_clock().now().isoformat())
            
            # Store strategy for performance tracking on exit
            strategy_key = f"options.strategy.{self.bot_id}.{ticker}"
            set_state(strategy_key, "long_call")
            
            profit_target = config.get("profit_target", 0.30)
            result.update({
                "success": True,
                "expected_profit": total_premium * profit_target,
                "risk": max_loss,
                "trade_data": trade_data
            })
            
            self._logger.log("long_call_executed", trade_data)
            
        except Exception as e:
            result["error"] = str(e)
            self._logger.error(f"Long call execution failed: {e}")
        
        return result
    
    def _execute_long_put(self, ticker: str, underlying_price: float,
                         max_daily_loss: float) -> Dict[str, Any]:
        """
        Execute a long put strategy (buy put option).
        
        Long Put: Buy a put option
        - Max profit = strike price - premium (if stock goes to $0)
        - Max loss = premium paid
        - Profits when stock price falls below strike - premium
        
        Args:
            ticker: Underlying symbol
            underlying_price: Current stock price
            max_daily_loss: Maximum loss budget
            
        Returns:
            Trade result dictionary
        """
        result = {"success": False, "error": None, "expected_profit": 0, "risk": 0}
        
        if not self._config:
            result["error"] = "No configuration available"
            return result
        
        try:
            config = self.strategy_configs.get(OptionStrategy.LONG_PUT, {})
            
            # Generate put options chain
            put_chain = self._generate_options_chain(ticker, underlying_price, "put")
            
            if not put_chain:
                result["error"] = "No options chain available"
                return result
            
            # Filter for suitable puts:
            # - Delta in range (not too OTM, not too ITM)
            # - Cost within budget
            max_cost = config.get("max_cost", 3.00)
            min_delta = config.get("min_delta", 0.30)
            max_delta = config.get("max_delta", 0.60)
            
            # Filter puts below current price (OTM/ATM) with appropriate delta
            suitable_puts = [
                p for p in put_chain
                if p["strike"] <= underlying_price * 1.02  # Slightly ITM to ATM/OTM
                and p["ask"] <= max_cost
                and abs(p.get("delta", -0.40)) >= min_delta
                and abs(p.get("delta", -0.40)) <= max_delta
            ]
            
            if not suitable_puts:
                # Fallback: just find cheapest put near the money
                near_money_puts = [
                    p for p in put_chain
                    if p["strike"] >= underlying_price * 0.90
                    and p["strike"] <= underlying_price * 1.05
                    and p["ask"] <= max_cost
                ]
                if near_money_puts:
                    suitable_puts = sorted(near_money_puts, key=lambda x: x["ask"])
            
            if not suitable_puts:
                result["error"] = f"No suitable puts found (max_cost=${max_cost})"
                return result
            
            # Select the best put (closest to ATM with good delta)
            selected_put = min(suitable_puts, key=lambda x: abs(x["strike"] - underlying_price))
            
            # Position sizing based on max loss budget
            put_cost = selected_put["ask"]
            max_allowed = min(max_daily_loss * 0.5, self._config.max_position_size_usd)
            contracts = max(1, int(max_allowed / (put_cost * 100)))
            
            # Cap at reasonable size
            contracts = min(contracts, 5)
            
            # Get option symbol
            put_symbol = selected_put.get("symbol")
            if not put_symbol:
                result["error"] = "Missing put symbol"
                return result
            
            # SPREAD GATE - Reject wide-market trades (Safety Priority 0)
            bid = selected_put.get("bid", 0)
            ask = selected_put.get("ask", 0)
            spread_check = self._check_spread_gate(bid, ask, put_symbol)
            if not spread_check.get("approved", False):
                result["error"] = f"Spread gate rejected: {spread_check.get('reason')}"
                self._logger.log("long_put_rejected_spread_gate", {
                    "symbol": put_symbol,
                    "ticker": ticker,
                    "spread_pct": spread_check.get("spread_pct", 999.0),
                    "reason": spread_check.get("reason")
                })
                return result
            
            # PRE-TRADE BUYING POWER CHECK for long options
            total_cost = put_cost * contracts * 100
            buying_power_check = self._check_buying_power_for_debit(total_cost)
            if not buying_power_check.get("approved", False):
                result["error"] = f"Insufficient buying power: {buying_power_check.get('reason')}"
                self._logger.log("long_put_rejected_buying_power", buying_power_check)
                return result
            
            # Place buy order
            try:
                order = self._alpaca.place_options_order(
                    symbol=put_symbol,
                    qty=contracts,
                    side="buy",
                    order_type="limit",
                    limit_price=round(put_cost * 1.02, 2)  # Slightly above ask for fill
                )
                order_id = order.get("id", "unknown")
                
                self._logger.log("long_put_placed", {
                    "symbol": put_symbol,
                    "ticker": ticker,
                    "strike": selected_put["strike"],
                    "cost": put_cost,
                    "contracts": contracts,
                    "order_id": order_id
                })
            except Exception as e:
                result["error"] = f"Failed to place options order: {e}"
                return result
            
            # Calculate trade metrics for long option (max loss = premium paid)
            total_premium = put_cost * contracts * 100
            max_loss = total_premium  # Max loss is the premium paid
            
            # Record trade with fields compatible with spread management
            trade_data = {
                "strategy": "long_put",
                "strategy_type": "debit",  # Long options are debit trades
                "ticker": ticker,
                "contracts": contracts,
                "strike": selected_put["strike"],
                "option_symbol": put_symbol,
                "cost": put_cost,
                "total_cost": total_premium,
                "premium_paid": total_premium,  # For debit trades
                "credit_received": 0.0,  # No credit for long options
                "max_loss": max_loss,  # Premium paid is max loss
                "timestamp": get_market_clock().now().timestamp(),
                "entry_time": get_market_clock().now().isoformat(),
                "order_id": order_id,
                "underlying_price": underlying_price
            }
            
            trade_key = f"options_trades.{self.bot_id}.{int(trade_data['timestamp'])}"
            set_state(trade_key, json.dumps(trade_data))
            
            entry_key = f"entry_time.{self.bot_id}.{ticker}"
            set_state(entry_key, get_market_clock().now().isoformat())
            
            # Store strategy for performance tracking on exit
            strategy_key = f"options.strategy.{self.bot_id}.{ticker}"
            set_state(strategy_key, "long_put")
            
            profit_target = config.get("profit_target", 0.30)
            result.update({
                "success": True,
                "expected_profit": total_premium * profit_target,
                "risk": max_loss,
                "trade_data": trade_data
            })
            
            self._logger.log("long_put_executed", trade_data)
            
        except Exception as e:
            result["error"] = str(e)
            self._logger.error(f"Long put execution failed: {e}")
        
        return result
    
    # =========================================================================
    # SELL-SIDE STRATEGY EXECUTION (require margin/cash securing)
    # =========================================================================
    
    def _execute_bull_put_spread(self, ticker: str, underlying_price: float,
                                max_daily_loss: float) -> Dict[str, Any]:
        """
        Execute a bull put spread strategy.
        
        Bull Put Spread: Sell higher-strike put, buy lower-strike put
        - Max profit = credit received
        - Max loss = spread width - credit
        - Profits when price stays above short put strike
        
        Args:
            ticker: Underlying symbol
            underlying_price: Current stock price
            max_daily_loss: Maximum loss budget
            
        Returns:
            Trade result dictionary
        """
        result = {"success": False, "error": None, "expected_profit": 0, "risk": 0}
        
        # GUARD: Fail if no config
        if not self._config:
            result["error"] = "No configuration available"
            return result
        
        try:
            # Generate simulated options chain
            # In production, would fetch real options data from broker
            options_chain = self._generate_options_chain(ticker, underlying_price, "put")
            
            if not options_chain:
                result["error"] = "No options chain available"
                return result
            
            # Find optimal spread strikes with IV-adjusted parameters
            base_config = self.strategy_configs[OptionStrategy.BULL_PUT_SPREAD]
            config = self._adjust_spread_params_for_iv(base_config, ticker)
            spread = self._find_optimal_spread(options_chain, config, underlying_price)
            
            if not spread:
                result["error"] = "No suitable spread found"
                return result
            
            short_put, long_put = spread
            
            # Calculate spread metrics
            credit = short_put["bid"] - long_put["ask"]  # Credit received
            spread_width = short_put["strike"] - long_put["strike"]  # Distance between strikes
            max_loss = spread_width - credit  # Max loss per contract
            
            # Validate positive credit
            if credit <= 0:
                result["error"] = f"Non-positive credit: {credit}"
                return result
            
            # Position sizing based on target profit and risk limits
            target_profit = self._config.profit_target_usd
            contracts = max(1, min(10, int(target_profit / (credit * 100))))
            
            # Check position size against limits
            position_cost = max_loss * contracts * 100  # In dollars
            max_allowed = min(max_daily_loss * 0.5, self._config.max_position_size_usd)
            
            if position_cost > max_allowed:
                contracts = max(1, int(max_allowed / (max_loss * 100)))
            
            # EXECUTE REAL OPTIONS ORDERS
            # Bull put spread: Sell short put (higher strike), Buy long put (lower strike)
            short_put_symbol = short_put.get("symbol")
            long_put_symbol = long_put.get("symbol")
            
            if not short_put_symbol or not long_put_symbol:
                result["error"] = "Missing option contract symbols"
                return result
            
            # SPREAD GATE - Reject wide-market trades (Safety Priority 0)
            short_spread_check = self._check_spread_gate(
                short_put.get("bid", 0), short_put.get("ask", 0), short_put_symbol
            )
            long_spread_check = self._check_spread_gate(
                long_put.get("bid", 0), long_put.get("ask", 0), long_put_symbol
            )
            if not short_spread_check.get("approved", False):
                result["error"] = f"Spread gate rejected short leg: {short_spread_check.get('reason')}"
                return result
            if not long_spread_check.get("approved", False):
                result["error"] = f"Spread gate rejected long leg: {long_spread_check.get('reason')}"
                return result
            
            order_ids = []
            
            # Leg 1: Sell the short put (collect premium) - OPENING a short position
            try:
                short_order = self._alpaca.place_options_order(
                    symbol=short_put_symbol,
                    qty=contracts,
                    side="sell",
                    order_type="limit",
                    limit_price=round(short_put["bid"] * 0.98, 2),  # Slightly below bid for fill
                    time_in_force="day",
                    position_intent="sell_to_open"  # Opening a short option position
                )
                order_ids.append(short_order.get("id", "unknown"))
                self._logger.log("bull_put_short_leg_placed", {
                    "symbol": short_put_symbol,
                    "contracts": contracts,
                    "price": short_put["bid"]
                })
            except Exception as e:
                self._logger.error(f"Failed to place short put: {e}")
                result["error"] = f"Short put order failed: {e}"
                return result
            
            # Leg 2: Buy the long put (protection) - OPENING a long position
            try:
                long_order = self._alpaca.place_options_order(
                    symbol=long_put_symbol,
                    qty=contracts,
                    side="buy",
                    order_type="limit",
                    limit_price=round(long_put["ask"] * 1.02, 2),  # Slightly above ask for fill
                    time_in_force="day",
                    position_intent="buy_to_open"  # Opening a long option position
                )
                order_ids.append(long_order.get("id", "unknown"))
                self._logger.log("bull_put_long_leg_placed", {
                    "symbol": long_put_symbol,
                    "contracts": contracts,
                    "price": long_put["ask"]
                })
            except Exception as e:
                self._cancel_orders(order_ids)  # Rollback on failure
                self._logger.error(f"Failed to place long put: {e}")
                result["error"] = f"Long put order failed: {e}"
                return result
            
            order_id = ",".join(order_ids)
            
            # Store trade details in state for tracking
            trade_data = {
                "strategy": "bull_put_spread",
                "ticker": ticker,
                "contracts": contracts,
                "short_strike": short_put["strike"],
                "long_strike": long_put["strike"],
                "short_symbol": short_put_symbol,
                "long_symbol": long_put_symbol,
                "credit_per_contract": credit,
                "total_credit": credit * contracts * 100,
                "max_loss": max_loss * contracts * 100,
                "breakeven": short_put["strike"] - credit,
                "profit_target": credit * contracts * 100 * config["profit_target"],
                "timestamp": get_market_clock().now().timestamp(),
                "entry_time": get_market_clock().now().isoformat(),
                "order_id": order_id,
                "underlying_price": underlying_price
            }
            
            trade_key = f"options_trades.{self.bot_id}.{int(trade_data['timestamp'])}"
            set_state(trade_key, json.dumps(trade_data))
            
            # Store entry time for time-based exits
            entry_key = f"entry_time.{self.bot_id}.{ticker}"
            set_state(entry_key, get_market_clock().now().isoformat())
            
            # Store strategy for performance tracking on exit
            strategy_key = f"options.strategy.{self.bot_id}.{ticker}"
            set_state(strategy_key, "bull_put_spread")
            
            result.update({
                "success": True,
                "expected_profit": trade_data["profit_target"],
                "risk": trade_data["max_loss"],
                "trade_data": trade_data
            })
            
            self._logger.log("bull_put_spread_executed", trade_data)
            
        except Exception as e:
            result["error"] = str(e)
            self._logger.error(f"Bull put spread execution failed: {e}")
        
        return result
    
    def _execute_bear_call_spread(self, ticker: str, underlying_price: float,
                                 max_daily_loss: float) -> Dict[str, Any]:
        """
        Execute a bear call spread strategy.
        
        Bear Call Spread: Sell lower-strike call, buy higher-strike call
        - Max profit = credit received
        - Max loss = spread width - credit
        - Profits when price stays below short call strike
        
        Args:
            ticker: Underlying symbol
            underlying_price: Current stock price
            max_daily_loss: Maximum loss budget
            
        Returns:
            Trade result dictionary
        """
        result = {"success": False, "error": None, "expected_profit": 0, "risk": 0}
        
        # GUARD: Fail if no config
        if not self._config:
            result["error"] = "No configuration available"
            return result
        
        try:
            options_chain = self._generate_options_chain(ticker, underlying_price, "call")
            
            if not options_chain:
                result["error"] = "No options chain available"
                return result
            
            # Apply IV-based dynamic adjustments
            base_config = self.strategy_configs[OptionStrategy.BEAR_CALL_SPREAD]
            config = self._adjust_spread_params_for_iv(base_config, ticker)
            spread = self._find_optimal_spread(options_chain, config, underlying_price)
            
            if not spread:
                result["error"] = "No suitable spread found"
                return result
            
            short_call, long_call = spread
            credit = short_call["bid"] - long_call["ask"]
            spread_width = long_call["strike"] - short_call["strike"]
            max_loss = spread_width - credit
            
            # Validate positive credit
            if credit <= 0:
                result["error"] = f"Non-positive credit: {credit}"
                return result
            
            contracts = max(1, int(self._config.profit_target_usd / (credit * 100)))
            
            # EXECUTE REAL OPTIONS ORDERS
            # Bear call spread: Sell short call (lower strike), Buy long call (higher strike)
            short_call_symbol = short_call.get("symbol")
            long_call_symbol = long_call.get("symbol")
            
            if not short_call_symbol or not long_call_symbol:
                result["error"] = "Missing option contract symbols"
                return result
            
            # SPREAD GATE - Reject wide-market trades (Safety Priority 0)
            short_spread_check = self._check_spread_gate(
                short_call.get("bid", 0), short_call.get("ask", 0), short_call_symbol
            )
            long_spread_check = self._check_spread_gate(
                long_call.get("bid", 0), long_call.get("ask", 0), long_call_symbol
            )
            if not short_spread_check.get("approved", False):
                result["error"] = f"Spread gate rejected short leg: {short_spread_check.get('reason')}"
                return result
            if not long_spread_check.get("approved", False):
                result["error"] = f"Spread gate rejected long leg: {long_spread_check.get('reason')}"
                return result
            
            order_ids = []
            
            # Leg 1: Sell the short call (collect premium) - OPENING a short position
            try:
                short_order = self._alpaca.place_options_order(
                    symbol=short_call_symbol,
                    qty=contracts,
                    side="sell",
                    order_type="limit",
                    limit_price=round(short_call["bid"] * 0.98, 2),
                    time_in_force="day",
                    position_intent="sell_to_open"  # Opening a short option position
                )
                order_ids.append(short_order.get("id", "unknown"))
                self._logger.log("bear_call_short_leg_placed", {
                    "symbol": short_call_symbol,
                    "contracts": contracts,
                    "price": short_call["bid"]
                })
            except Exception as e:
                self._logger.error(f"Failed to place short call: {e}")
                result["error"] = f"Short call order failed: {e}"
                return result
            
            # Leg 2: Buy the long call (protection) - OPENING a long position
            try:
                long_order = self._alpaca.place_options_order(
                    symbol=long_call_symbol,
                    qty=contracts,
                    side="buy",
                    order_type="limit",
                    limit_price=round(long_call["ask"] * 1.02, 2),
                    time_in_force="day",
                    position_intent="buy_to_open"  # Opening a long option position
                )
                order_ids.append(long_order.get("id", "unknown"))
                self._logger.log("bear_call_long_leg_placed", {
                    "symbol": long_call_symbol,
                    "contracts": contracts,
                    "price": long_call["ask"]
                })
            except Exception as e:
                self._cancel_orders(order_ids)  # Rollback on failure
                self._logger.error(f"Failed to place long call: {e}")
                result["error"] = f"Long call order failed: {e}"
                return result
            
            order_id = ",".join(order_ids)
            
            # Store trade details in state for tracking
            trade_data = {
                "strategy": "bear_call_spread",
                "ticker": ticker,
                "contracts": contracts,
                "short_strike": short_call["strike"],
                "long_strike": long_call["strike"],
                "short_symbol": short_call_symbol,
                "long_symbol": long_call_symbol,
                "credit_per_contract": credit,
                "total_credit": credit * contracts * 100,
                "max_loss": max_loss * contracts * 100,
                "breakeven": short_call["strike"] + credit,
                "timestamp": get_market_clock().now().timestamp(),
                "entry_time": get_market_clock().now().isoformat(),
                "order_id": order_id,
                "underlying_price": underlying_price
            }
            
            trade_key = f"options_trades.{self.bot_id}.{int(trade_data['timestamp'])}"
            set_state(trade_key, json.dumps(trade_data))
            
            entry_key = f"entry_time.{self.bot_id}.{ticker}"
            set_state(entry_key, get_market_clock().now().isoformat())
            
            # Store strategy for performance tracking on exit
            strategy_key = f"options.strategy.{self.bot_id}.{ticker}"
            set_state(strategy_key, "bear_call_spread")
            
            result.update({
                "success": True,
                "expected_profit": credit * contracts * 100 * config["profit_target"],
                "risk": max_loss * contracts * 100,
                "trade_data": trade_data
            })
            
            self._logger.log("bear_call_spread_executed", trade_data)
            
        except Exception as e:
            result["error"] = str(e)
        
        return result
    
    # =========================================================================
    # DEBIT SPREAD EXECUTION (defined-risk directional plays)
    # Replaces naked short options with defined-risk spreads
    # =========================================================================
    
    def _execute_bull_call_spread(self, ticker: str, underlying_price: float,
                                  max_daily_loss: float) -> Dict[str, Any]:
        """
        Execute a bull call spread strategy (debit spread).
        
        Bull Call Spread: Buy lower-strike call, sell higher-strike call
        - Max profit = spread width - debit paid
        - Max loss = debit paid (defined risk!)
        - Profits when price rises above long call strike
        
        Offers more leverage than single-leg long calls for directional bullish plays.
        
        Args:
            ticker: Underlying symbol
            underlying_price: Current stock price
            max_daily_loss: Maximum loss budget
            
        Returns:
            Trade result dictionary
        """
        result = {"success": False, "error": None, "expected_profit": 0, "risk": 0}
        
        if not self._config:
            result["error"] = "No configuration available"
            return result
        
        try:
            options_chain = self._generate_options_chain(ticker, underlying_price, "call")
            
            if not options_chain:
                result["error"] = "No options chain available"
                return result
            
            base_config = self.strategy_configs.get(OptionStrategy.BULL_CALL_SPREAD, {})
            
            # Apply IV-based dynamic adjustments to leg parameters
            config = self._adjust_spread_params_for_iv(base_config, ticker)
            
            # Extract dynamically adjusted parameters
            long_delta_range = tuple(config.get("long_delta_range", [0.50, 0.70]))
            short_delta_range = tuple(config.get("short_delta_range", [0.30, 0.45]))
            max_debit = config.get("max_debit", 2.50)
            spread_width_range = tuple(config.get("spread_width_range", [3, 10]))
            
            long_call = None
            short_call = None
            
            for opt in sorted(options_chain, key=lambda x: abs(x.get("delta", 0) - 0.55)):
                delta = opt.get("delta", 0)
                if long_delta_range[0] <= delta <= long_delta_range[1]:
                    long_call = opt
                    break
            
            if not long_call:
                result["error"] = "No suitable long call found in delta range"
                return result
            
            for opt in sorted(options_chain, key=lambda x: abs(x.get("delta", 0) - 0.35)):
                delta = opt.get("delta", 0)
                if short_delta_range[0] <= delta <= short_delta_range[1]:
                    if opt.get("strike", 0) > long_call.get("strike", 0):
                        spread_width = opt.get("strike", 0) - long_call.get("strike", 0)
                        if spread_width_range[0] <= spread_width <= spread_width_range[1]:
                            short_call = opt
                            break
            
            if not short_call:
                result["error"] = "No suitable short call found for spread"
                return result
            
            debit = long_call.get("ask", 0) - short_call.get("bid", 0)
            spread_width = short_call.get("strike", 0) - long_call.get("strike", 0)
            max_profit = spread_width - debit
            
            if debit <= 0 or debit > max_debit:
                result["error"] = f"Debit {debit:.2f} out of range (0, {max_debit})"
                return result
            
            contracts = max(1, int(max_daily_loss / (debit * 100)))
            
            long_call_symbol = long_call.get("symbol")
            short_call_symbol = short_call.get("symbol")
            
            if not long_call_symbol or not short_call_symbol:
                result["error"] = "Missing option contract symbols"
                return result
            
            long_spread_check = self._check_spread_gate(
                long_call.get("bid", 0), long_call.get("ask", 0), long_call_symbol
            )
            short_spread_check = self._check_spread_gate(
                short_call.get("bid", 0), short_call.get("ask", 0), short_call_symbol
            )
            if not long_spread_check.get("approved", False):
                result["error"] = f"Spread gate rejected long leg: {long_spread_check.get('reason')}"
                return result
            if not short_spread_check.get("approved", False):
                result["error"] = f"Spread gate rejected short leg: {short_spread_check.get('reason')}"
                return result
            
            order_ids = []
            
            try:
                long_order = self._alpaca.place_options_order(
                    symbol=long_call_symbol,
                    qty=contracts,
                    side="buy",
                    order_type="limit",
                    limit_price=round(long_call["ask"] * 1.02, 2),
                    time_in_force="day",
                    position_intent="buy_to_open"
                )
                order_ids.append(long_order.get("id", "unknown"))
                self._logger.log("bull_call_spread_long_leg_placed", {
                    "symbol": long_call_symbol,
                    "contracts": contracts,
                    "price": long_call["ask"]
                })
            except Exception as e:
                self._logger.error(f"Failed to place long call: {e}")
                result["error"] = f"Long call order failed: {e}"
                return result
            
            try:
                short_order = self._alpaca.place_options_order(
                    symbol=short_call_symbol,
                    qty=contracts,
                    side="sell",
                    order_type="limit",
                    limit_price=round(short_call["bid"] * 0.98, 2),
                    time_in_force="day",
                    position_intent="sell_to_open"
                )
                order_ids.append(short_order.get("id", "unknown"))
                self._logger.log("bull_call_spread_short_leg_placed", {
                    "symbol": short_call_symbol,
                    "contracts": contracts,
                    "price": short_call["bid"]
                })
            except Exception as e:
                self._cancel_orders(order_ids)
                self._logger.error(f"Failed to place short call: {e}")
                result["error"] = f"Short call order failed: {e}"
                return result
            
            order_id = ",".join(order_ids)
            
            trade_data = {
                "strategy": "bull_call_spread",
                "ticker": ticker,
                "contracts": contracts,
                "long_strike": long_call["strike"],
                "short_strike": short_call["strike"],
                "long_symbol": long_call_symbol,
                "short_symbol": short_call_symbol,
                "debit_per_contract": debit,
                "total_debit": debit * contracts * 100,
                "max_profit": max_profit * contracts * 100,
                "max_loss": debit * contracts * 100,
                "breakeven": long_call["strike"] + debit,
                "profit_target": max_profit * contracts * 100 * config.get("profit_target", 0.60),
                "timestamp": get_market_clock().now().timestamp(),
                "entry_time": get_market_clock().now().isoformat(),
                "order_id": order_id,
                "underlying_price": underlying_price
            }
            
            trade_key = f"options_trades.{self.bot_id}.{int(trade_data['timestamp'])}"
            set_state(trade_key, json.dumps(trade_data))
            
            entry_key = f"entry_time.{self.bot_id}.{ticker}"
            set_state(entry_key, get_market_clock().now().isoformat())
            
            # Store strategy for performance tracking on exit
            strategy_key = f"options.strategy.{self.bot_id}.{ticker}"
            set_state(strategy_key, "bull_call_spread")
            
            result.update({
                "success": True,
                "expected_profit": trade_data["profit_target"],
                "risk": trade_data["max_loss"],
                "trade_data": trade_data
            })
            
            self._logger.log("bull_call_spread_executed", trade_data)
            
        except Exception as e:
            result["error"] = str(e)
            self._logger.error(f"Bull call spread execution failed: {e}")
        
        return result
    
    def _execute_bear_put_spread(self, ticker: str, underlying_price: float,
                                 max_daily_loss: float) -> Dict[str, Any]:
        """
        Execute a bear put spread strategy (debit spread).
        
        Bear Put Spread: Buy higher-strike put, sell lower-strike put
        - Max profit = spread width - debit paid
        - Max loss = debit paid (defined risk!)
        - Profits when price falls below long put strike
        
        Offers more leverage than single-leg long puts for directional bearish plays.
        
        Args:
            ticker: Underlying symbol
            underlying_price: Current stock price
            max_daily_loss: Maximum loss budget
            
        Returns:
            Trade result dictionary
        """
        result = {"success": False, "error": None, "expected_profit": 0, "risk": 0}
        
        if not self._config:
            result["error"] = "No configuration available"
            return result
        
        try:
            options_chain = self._generate_options_chain(ticker, underlying_price, "put")
            
            if not options_chain:
                result["error"] = "No options chain available"
                return result
            
            base_config = self.strategy_configs.get(OptionStrategy.BEAR_PUT_SPREAD, {})
            
            # Apply IV-based dynamic adjustments to leg parameters
            config = self._adjust_spread_params_for_iv(base_config, ticker)
            
            # Extract dynamically adjusted parameters
            long_delta_range = tuple(config.get("long_delta_range", [-0.70, -0.50]))
            short_delta_range = tuple(config.get("short_delta_range", [-0.45, -0.30]))
            max_debit = config.get("max_debit", 2.50)
            spread_width_range = tuple(config.get("spread_width_range", [3, 10]))
            
            long_put = None
            short_put = None
            
            for opt in sorted(options_chain, key=lambda x: abs(x.get("delta", 0) + 0.55)):
                delta = opt.get("delta", 0)
                if long_delta_range[0] <= delta <= long_delta_range[1]:
                    long_put = opt
                    break
            
            if not long_put:
                result["error"] = "No suitable long put found in delta range"
                return result
            
            for opt in sorted(options_chain, key=lambda x: abs(x.get("delta", 0) + 0.35)):
                delta = opt.get("delta", 0)
                if short_delta_range[0] <= delta <= short_delta_range[1]:
                    if opt.get("strike", 0) < long_put.get("strike", 0):
                        spread_width = long_put.get("strike", 0) - opt.get("strike", 0)
                        if spread_width_range[0] <= spread_width <= spread_width_range[1]:
                            short_put = opt
                            break
            
            if not short_put:
                result["error"] = "No suitable short put found for spread"
                return result
            
            debit = long_put.get("ask", 0) - short_put.get("bid", 0)
            spread_width = long_put.get("strike", 0) - short_put.get("strike", 0)
            max_profit = spread_width - debit
            
            if debit <= 0 or debit > max_debit:
                result["error"] = f"Debit {debit:.2f} out of range (0, {max_debit})"
                return result
            
            contracts = max(1, int(max_daily_loss / (debit * 100)))
            
            long_put_symbol = long_put.get("symbol")
            short_put_symbol = short_put.get("symbol")
            
            if not long_put_symbol or not short_put_symbol:
                result["error"] = "Missing option contract symbols"
                return result
            
            long_spread_check = self._check_spread_gate(
                long_put.get("bid", 0), long_put.get("ask", 0), long_put_symbol
            )
            short_spread_check = self._check_spread_gate(
                short_put.get("bid", 0), short_put.get("ask", 0), short_put_symbol
            )
            if not long_spread_check.get("approved", False):
                result["error"] = f"Spread gate rejected long leg: {long_spread_check.get('reason')}"
                return result
            if not short_spread_check.get("approved", False):
                result["error"] = f"Spread gate rejected short leg: {short_spread_check.get('reason')}"
                return result
            
            order_ids = []
            
            try:
                long_order = self._alpaca.place_options_order(
                    symbol=long_put_symbol,
                    qty=contracts,
                    side="buy",
                    order_type="limit",
                    limit_price=round(long_put["ask"] * 1.02, 2),
                    time_in_force="day",
                    position_intent="buy_to_open"
                )
                order_ids.append(long_order.get("id", "unknown"))
                self._logger.log("bear_put_spread_long_leg_placed", {
                    "symbol": long_put_symbol,
                    "contracts": contracts,
                    "price": long_put["ask"]
                })
            except Exception as e:
                self._logger.error(f"Failed to place long put: {e}")
                result["error"] = f"Long put order failed: {e}"
                return result
            
            try:
                short_order = self._alpaca.place_options_order(
                    symbol=short_put_symbol,
                    qty=contracts,
                    side="sell",
                    order_type="limit",
                    limit_price=round(short_put["bid"] * 0.98, 2),
                    time_in_force="day",
                    position_intent="sell_to_open"
                )
                order_ids.append(short_order.get("id", "unknown"))
                self._logger.log("bear_put_spread_short_leg_placed", {
                    "symbol": short_put_symbol,
                    "contracts": contracts,
                    "price": short_put["bid"]
                })
            except Exception as e:
                self._cancel_orders(order_ids)
                self._logger.error(f"Failed to place short put: {e}")
                result["error"] = f"Short put order failed: {e}"
                return result
            
            order_id = ",".join(order_ids)
            
            trade_data = {
                "strategy": "bear_put_spread",
                "ticker": ticker,
                "contracts": contracts,
                "long_strike": long_put["strike"],
                "short_strike": short_put["strike"],
                "long_symbol": long_put_symbol,
                "short_symbol": short_put_symbol,
                "debit_per_contract": debit,
                "total_debit": debit * contracts * 100,
                "max_profit": max_profit * contracts * 100,
                "max_loss": debit * contracts * 100,
                "breakeven": long_put["strike"] - debit,
                "profit_target": max_profit * contracts * 100 * config.get("profit_target", 0.60),
                "timestamp": get_market_clock().now().timestamp(),
                "entry_time": get_market_clock().now().isoformat(),
                "order_id": order_id,
                "underlying_price": underlying_price
            }
            
            trade_key = f"options_trades.{self.bot_id}.{int(trade_data['timestamp'])}"
            set_state(trade_key, json.dumps(trade_data))
            
            entry_key = f"entry_time.{self.bot_id}.{ticker}"
            set_state(entry_key, get_market_clock().now().isoformat())
            
            # Store strategy for performance tracking on exit
            strategy_key = f"options.strategy.{self.bot_id}.{ticker}"
            set_state(strategy_key, "bear_put_spread")
            
            result.update({
                "success": True,
                "expected_profit": trade_data["profit_target"],
                "risk": trade_data["max_loss"],
                "trade_data": trade_data
            })
            
            self._logger.log("bear_put_spread_executed", trade_data)
            
        except Exception as e:
            result["error"] = str(e)
            self._logger.error(f"Bear put spread execution failed: {e}")
        
        return result
    
    def _execute_calendar_spread(self, ticker: str, underlying_price: float,
                                 max_daily_loss: float) -> Dict[str, Any]:
        """
        Execute a calendar spread strategy (theta harvesting).
        
        Calendar Spread: Sell near-term option, buy far-term option (same strike)
        - Profits from time decay differential (near-term decays faster)
        - Max loss = net debit paid (defined risk!)
        - Best in low volatility, range-bound markets
        
        Short leg is protected by long leg - Alpaca allows this structure.
        
        Args:
            ticker: Underlying symbol
            underlying_price: Current stock price
            max_daily_loss: Maximum loss budget
            
        Returns:
            Trade result dictionary
        """
        result = {"success": False, "error": None, "expected_profit": 0, "risk": 0}
        
        if not self._config:
            result["error"] = "No configuration available"
            return result
        
        try:
            base_config = self.strategy_configs.get(OptionStrategy.CALENDAR_SPREAD, {})
            
            # Apply IV-based dynamic adjustments
            config = self._adjust_spread_params_for_iv(base_config, ticker)
            
            # Extract dynamically adjusted parameters
            short_dte = config.get("short_dte", 7)
            long_dte = config.get("long_dte", 30)
            max_debit = config.get("max_debit", 2.00)
            delta_target = config.get("delta_target", 0.50)
            delta_tolerance = config.get("delta_tolerance", 0.10)
            delta_range = (-delta_target - delta_tolerance, delta_target + delta_tolerance)
            
            short_chain = self._generate_options_chain(ticker, underlying_price, "call", target_dte=short_dte)
            long_chain = self._generate_options_chain(ticker, underlying_price, "call", target_dte=long_dte)
            
            if not short_chain or not long_chain:
                result["error"] = "No options chain available for calendar spread"
                return result
            
            atm_strike = round(underlying_price)
            short_option = None
            long_option = None
            
            for strike_step in [0, 1, -1, 2, -2, 5, -5]:
                test_strike = atm_strike + strike_step
                
                short_option = None
                long_option = None
                
                for opt in short_chain:
                    if abs(opt.get("strike", 0) - test_strike) < 1:
                        delta = opt.get("delta", 0)
                        if delta_range[0] <= delta <= delta_range[1]:
                            short_option = opt
                            break
                
                for opt in long_chain:
                    if abs(opt.get("strike", 0) - test_strike) < 1:
                        long_option = opt
                        break
                
                if short_option and long_option:
                    break
            
            if not short_option or not long_option:
                result["error"] = "No suitable ATM options found for calendar spread"
                return result
            
            debit = long_option.get("ask", 0) - short_option.get("bid", 0)
            
            if debit <= 0:
                result["error"] = "Calendar spread has no debit (near-term more expensive than far-term)"
                return result
            
            if debit > max_debit:
                result["error"] = f"Debit {debit:.2f} exceeds max {max_debit}"
                return result
            
            contracts = max(1, int(max_daily_loss / (debit * 100)))
            
            short_symbol = short_option.get("symbol")
            long_symbol = long_option.get("symbol")
            
            if not short_symbol or not long_symbol:
                result["error"] = "Missing option contract symbols"
                return result
            
            short_spread_check = self._check_spread_gate(
                short_option.get("bid", 0), short_option.get("ask", 0), short_symbol
            )
            long_spread_check = self._check_spread_gate(
                long_option.get("bid", 0), long_option.get("ask", 0), long_symbol
            )
            if not short_spread_check.get("approved", False):
                result["error"] = f"Spread gate rejected short leg: {short_spread_check.get('reason')}"
                return result
            if not long_spread_check.get("approved", False):
                result["error"] = f"Spread gate rejected long leg: {long_spread_check.get('reason')}"
                return result
            
            order_ids = []
            
            try:
                long_order = self._alpaca.place_options_order(
                    symbol=long_symbol,
                    qty=contracts,
                    side="buy",
                    order_type="limit",
                    limit_price=round(long_option["ask"] * 1.02, 2),
                    time_in_force="day",
                    position_intent="buy_to_open"
                )
                order_ids.append(long_order.get("id", "unknown"))
                self._logger.log("calendar_spread_long_leg_placed", {
                    "symbol": long_symbol,
                    "contracts": contracts,
                    "price": long_option["ask"],
                    "dte": long_dte
                })
            except Exception as e:
                self._logger.error(f"Failed to place long calendar leg: {e}")
                result["error"] = f"Long leg order failed: {e}"
                return result
            
            try:
                short_order = self._alpaca.place_options_order(
                    symbol=short_symbol,
                    qty=contracts,
                    side="sell",
                    order_type="limit",
                    limit_price=round(short_option["bid"] * 0.98, 2),
                    time_in_force="day",
                    position_intent="sell_to_open"
                )
                order_ids.append(short_order.get("id", "unknown"))
                self._logger.log("calendar_spread_short_leg_placed", {
                    "symbol": short_symbol,
                    "contracts": contracts,
                    "price": short_option["bid"],
                    "dte": short_dte
                })
            except Exception as e:
                self._cancel_orders(order_ids)
                self._logger.error(f"Failed to place short calendar leg: {e}")
                result["error"] = f"Short leg order failed: {e}"
                return result
            
            order_id = ",".join(order_ids)
            
            estimated_theta_profit = abs(short_option.get("theta", 0)) - abs(long_option.get("theta", 0))
            
            trade_data = {
                "strategy": "calendar_spread",
                "ticker": ticker,
                "contracts": contracts,
                "strike": short_option["strike"],
                "short_symbol": short_symbol,
                "long_symbol": long_symbol,
                "short_dte": short_dte,
                "long_dte": long_dte,
                "debit_per_contract": debit,
                "total_debit": debit * contracts * 100,
                "max_loss": debit * contracts * 100,
                "estimated_daily_theta": estimated_theta_profit * contracts * 100,
                "profit_target": debit * contracts * 100 * config.get("profit_target", 0.30),
                "timestamp": get_market_clock().now().timestamp(),
                "entry_time": get_market_clock().now().isoformat(),
                "order_id": order_id,
                "underlying_price": underlying_price
            }
            
            trade_key = f"options_trades.{self.bot_id}.{int(trade_data['timestamp'])}"
            set_state(trade_key, json.dumps(trade_data))
            
            entry_key = f"entry_time.{self.bot_id}.{ticker}"
            set_state(entry_key, get_market_clock().now().isoformat())
            
            # Store strategy for performance tracking on exit
            strategy_key = f"options.strategy.{self.bot_id}.{ticker}"
            set_state(strategy_key, "calendar_spread")
            
            result.update({
                "success": True,
                "expected_profit": trade_data["profit_target"],
                "risk": trade_data["max_loss"],
                "trade_data": trade_data
            })
            
            self._logger.log("calendar_spread_executed", trade_data)
            
        except Exception as e:
            result["error"] = str(e)
            self._logger.error(f"Calendar spread execution failed: {e}")
        
        return result
    
    def _cancel_orders(self, order_ids: List[str]) -> int:
        """
        Cancel previously placed orders for rollback on failure.
        
        Returns:
            Number of orders successfully cancelled
        """
        cancelled = 0
        for order_id in order_ids:
            if order_id and order_id != "unknown":
                try:
                    success = self._alpaca.cancel_order(order_id)
                    if success:
                        cancelled += 1
                        self._logger.log("order_cancelled_rollback", {"order_id": order_id})
                    else:
                        self._logger.error(f"Failed to cancel order {order_id}: cancel returned False")
                except Exception as e:
                    self._logger.error(f"Failed to cancel order {order_id}: {e}")
        
        if cancelled < len(order_ids):
            self._logger.error(f"CRITICAL: Only cancelled {cancelled}/{len(order_ids)} orders - manual intervention may be required")
        
        return cancelled
    
    def _execute_iron_condor(self, ticker: str, underlying_price: float,
                            max_daily_loss: float) -> Dict[str, Any]:
        """
        Execute an iron condor strategy.
        
        Iron Condor: Bull put spread (below spot) + Bear call spread (above spot)
        - Put spread: short put BELOW spot, long put further OTM
        - Call spread: short call ABOVE spot, long call further OTM
        - Profits when price stays in a range
        - Max profit = total credit from both sides
        - Max loss = spread width - credit
        
        Args:
            ticker: Underlying symbol
            underlying_price: Current stock price
            max_daily_loss: Maximum loss budget
            
        Returns:
            Trade result dictionary
        """
        result = {"success": False, "error": None, "expected_profit": 0, "risk": 0}
        
        if not self._config:
            result["error"] = "No configuration available"
            return result
        
        try:
            base_config = self.strategy_configs[OptionStrategy.IRON_CONDOR]
            
            # Apply IV-based dynamic adjustments
            config = self._adjust_spread_params_for_iv(base_config, ticker)
            
            # Fetch put chain for bull put side
            put_chain = self._generate_options_chain(ticker, underlying_price, "put")
            if not put_chain:
                result["error"] = "No put options chain available"
                return result
            
            # Fetch call chain for bear call side
            call_chain = self._generate_options_chain(ticker, underlying_price, "call")
            if not call_chain:
                result["error"] = "No call options chain available"
                return result
            
            # Filter put chain: only puts BELOW current price (OTM puts)
            otm_puts = [p for p in put_chain if p["strike"] < underlying_price]
            if not otm_puts:
                result["error"] = "No OTM puts available below spot"
                return result
            
            # Filter call chain: only calls ABOVE current price (OTM calls)
            otm_calls = [c for c in call_chain if c["strike"] > underlying_price]
            if not otm_calls:
                result["error"] = "No OTM calls available above spot"
                return result
            
            # Find put spread using OTM puts - use config delta ranges (already IV-adjusted)
            put_config = dict(config)
            put_config["short_delta_range"] = tuple(config.get("put_short_delta_range", [-0.30, -0.15]))
            put_config["long_delta_range"] = tuple(config.get("put_long_delta_range", [-0.15, -0.05]))
            put_spread = self._find_optimal_spread(otm_puts, put_config, underlying_price)
            if not put_spread:
                # Fallback: use any available OTM puts sorted by strike
                sorted_puts = sorted(otm_puts, key=lambda x: x["strike"], reverse=True)
                if len(sorted_puts) >= 2:
                    put_spread = (sorted_puts[0], sorted_puts[1])  # short nearer ATM, long further OTM
                else:
                    result["error"] = "No suitable put spread found"
                    return result
            
            # Find call spread using OTM calls - use config delta ranges (already IV-adjusted)
            call_config = dict(config)
            call_config["short_delta_range"] = tuple(config.get("call_short_delta_range", [0.15, 0.30]))
            call_config["long_delta_range"] = tuple(config.get("call_long_delta_range", [0.05, 0.15]))
            call_spread = self._find_optimal_spread(otm_calls, call_config, underlying_price)
            if not call_spread:
                # Fallback: use any available OTM calls sorted by strike
                sorted_calls = sorted(otm_calls, key=lambda x: x["strike"])
                if len(sorted_calls) >= 2:
                    call_spread = (sorted_calls[0], sorted_calls[1])  # short nearer ATM, long further OTM
                else:
                    result["error"] = "No suitable call spread found"
                    return result
            
            short_put, long_put = put_spread
            short_call, long_call = call_spread
            
            # Validate proper OTM structure
            if short_put["strike"] >= underlying_price:
                result["error"] = "Short put must be below spot price"
                return result
            if short_call["strike"] <= underlying_price:
                result["error"] = "Short call must be above spot price"
                return result
            
            # Calculate total credit and risk
            put_credit = short_put["bid"] - long_put["ask"]
            call_credit = short_call["bid"] - long_call["ask"]
            total_credit = put_credit + call_credit
            
            # Validate positive credit
            if total_credit <= 0:
                result["error"] = f"Non-positive credit: {total_credit}"
                return result
            
            put_width = short_put["strike"] - long_put["strike"]
            call_width = long_call["strike"] - short_call["strike"]
            max_loss = max(put_width, call_width) - total_credit
            
            contracts = max(1, int(self._config.profit_target_usd / (total_credit * 100)))
            
            # Validate all symbols exist
            short_put_sym = short_put.get("symbol")
            long_put_sym = long_put.get("symbol")
            short_call_sym = short_call.get("symbol")
            long_call_sym = long_call.get("symbol")
            
            if not all([short_put_sym, long_put_sym, short_call_sym, long_call_sym]):
                result["error"] = "Missing option contract symbols"
                return result
            
            # SPREAD GATE - Reject wide-market trades (Safety Priority 0)
            # Check all 4 legs before placing any orders
            spread_checks = [
                (short_put, short_put_sym, "short_put"),
                (long_put, long_put_sym, "long_put"),
                (short_call, short_call_sym, "short_call"),
                (long_call, long_call_sym, "long_call"),
            ]
            for leg, sym, leg_name in spread_checks:
                check = self._check_spread_gate(leg.get("bid", 0), leg.get("ask", 0), sym)
                if not check.get("approved", False):
                    result["error"] = f"Spread gate rejected {leg_name}: {check.get('reason')}"
                    self._logger.log(f"iron_condor_{leg_name}_rejected_spread_gate", {
                        "symbol": sym,
                        "spread_pct": check.get("spread_pct", 999.0)
                    })
                    return result
            
            order_ids = []
            
            # Leg 1: Sell short put - OPENING a short position
            try:
                order = self._alpaca.place_options_order(
                    symbol=str(short_put_sym),
                    qty=contracts, side="sell", order_type="limit",
                    limit_price=round(short_put["bid"] * 0.98, 2),
                    position_intent="sell_to_open"
                )
                order_ids.append(order.get("id", "unknown"))
            except Exception as e:
                result["error"] = f"Short put failed: {e}"
                return result
            
            # Leg 2: Buy long put - OPENING a long position
            try:
                order = self._alpaca.place_options_order(
                    symbol=str(long_put_sym),
                    qty=contracts, side="buy", order_type="limit",
                    limit_price=round(long_put["ask"] * 1.02, 2),
                    position_intent="buy_to_open"
                )
                order_ids.append(order.get("id", "unknown"))
            except Exception as e:
                self._cancel_orders(order_ids)
                result["error"] = f"Long put failed: {e}"
                return result
            
            # Leg 3: Sell short call - OPENING a short position
            try:
                order = self._alpaca.place_options_order(
                    symbol=str(short_call_sym),
                    qty=contracts, side="sell", order_type="limit",
                    limit_price=round(short_call["bid"] * 0.98, 2),
                    position_intent="sell_to_open"
                )
                order_ids.append(order.get("id", "unknown"))
            except Exception as e:
                self._cancel_orders(order_ids)
                result["error"] = f"Short call failed: {e}"
                return result
            
            # Leg 4: Buy long call - OPENING a long position
            try:
                order = self._alpaca.place_options_order(
                    symbol=str(long_call_sym),
                    qty=contracts, side="buy", order_type="limit",
                    limit_price=round(long_call["ask"] * 1.02, 2),
                    position_intent="buy_to_open"
                )
                order_ids.append(order.get("id", "unknown"))
            except Exception as e:
                self._cancel_orders(order_ids)
                result["error"] = f"Long call failed: {e}"
                return result
            
            order_id = ",".join(order_ids)
            
            trade_data = {
                "strategy": "iron_condor",
                "ticker": ticker,
                "contracts": contracts,
                "short_put_strike": short_put["strike"],
                "long_put_strike": long_put["strike"],
                "short_call_strike": short_call["strike"],
                "long_call_strike": long_call["strike"],
                "put_credit": put_credit,
                "call_credit": call_credit,
                "total_credit": total_credit * contracts * 100,
                "max_loss": max_loss * contracts * 100,
                "timestamp": get_market_clock().now().timestamp(),
                "entry_time": get_market_clock().now().isoformat(),
                "order_id": order_id,
                "underlying_price": underlying_price
            }
            
            trade_key = f"options_trades.{self.bot_id}.{int(trade_data['timestamp'])}"
            set_state(trade_key, json.dumps(trade_data))
            
            entry_key = f"entry_time.{self.bot_id}.{ticker}"
            set_state(entry_key, get_market_clock().now().isoformat())
            
            # Store strategy for performance tracking on exit
            strategy_key = f"options.strategy.{self.bot_id}.{ticker}"
            set_state(strategy_key, "iron_condor")
            
            result.update({
                "success": True,
                "expected_profit": total_credit * contracts * 100 * config["profit_target"],
                "risk": max_loss * contracts * 100,
                "trade_data": trade_data
            })
            
            self._logger.log("iron_condor_executed", trade_data)
            
        except Exception as e:
            result["error"] = str(e)
            self._logger.error(f"Iron condor execution failed: {e}")
        
        return result
    
    def _execute_straddle(self, ticker: str, underlying_price: float,
                         max_daily_loss: float) -> Dict[str, Any]:
        """
        Execute a long straddle strategy.
        
        Long Straddle: Buy ATM call + Buy ATM put at same strike
        - Profits when price moves significantly in either direction
        - Max loss = total premium paid
        - Best when expecting high volatility / big move
        
        Args:
            ticker: Underlying symbol
            underlying_price: Current stock price
            max_daily_loss: Maximum loss budget
            
        Returns:
            Trade result dictionary
        """
        result = {"success": False, "error": None, "expected_profit": 0, "risk": 0}
        
        if not self._config:
            result["error"] = "No configuration available"
            return result
        
        try:
            config = self.strategy_configs[OptionStrategy.STRADDLE]
            
            # Fetch both call and put chains
            call_chain = self._generate_options_chain(ticker, underlying_price, "call")
            put_chain = self._generate_options_chain(ticker, underlying_price, "put")
            
            if not call_chain or not put_chain:
                result["error"] = "No options chain available"
                return result
            
            # Find ATM call (closest to underlying price)
            atm_call = min(call_chain, key=lambda x: abs(x["strike"] - underlying_price))
            target_strike = atm_call["strike"]
            target_expiry = atm_call.get("expiry")
            target_dte = atm_call.get("dte")
            
            # Find put with EXACT same strike AND expiry for proper straddle
            matching_puts = [p for p in put_chain 
                           if p["strike"] == target_strike 
                           and p.get("expiry") == target_expiry
                           and p.get("dte") == target_dte]
            
            if not matching_puts:
                # Strict fallback: require same strike and DTE at minimum
                same_strike_dte_puts = [p for p in put_chain 
                                       if p["strike"] == target_strike
                                       and p.get("dte") == target_dte]
                if same_strike_dte_puts:
                    atm_put = same_strike_dte_puts[0]
                else:
                    result["error"] = f"No matching put for straddle: strike={target_strike}, expiry={target_expiry}"
                    return result
            else:
                atm_put = matching_puts[0]
            
            # HARD VALIDATION: Straddle REQUIRES exact strike AND expiry match
            if atm_call["strike"] != atm_put["strike"]:
                result["error"] = f"Straddle strike mismatch: call={atm_call['strike']} put={atm_put['strike']} - aborting"
                return result
            # Require BOTH legs have expiry and they match exactly
            call_expiry = atm_call.get("expiry")
            put_expiry = atm_put.get("expiry")
            if not call_expiry or not put_expiry:
                result["error"] = f"Straddle missing expiry: call={call_expiry}, put={put_expiry} - aborting"
                return result
            if call_expiry != put_expiry:
                result["error"] = f"Straddle expiry mismatch: call={call_expiry} put={put_expiry} - aborting"
                return result
            
            # Calculate total cost and potential profit
            call_cost = atm_call["ask"]
            put_cost = atm_put["ask"]
            total_cost = call_cost + put_cost
            
            # Position sizing based on max loss budget
            max_allowed = min(max_daily_loss * 0.5, self._config.max_position_size_usd)
            contracts = max(1, int(max_allowed / (total_cost * 100)))
            
            order_ids = []
            
            # Leg 1: Buy ATM call
            call_symbol = atm_call.get("symbol")
            if not call_symbol:
                result["error"] = "Missing call symbol"
                return result
            
            # SPREAD GATE - Reject wide-market trades (Safety Priority 0)
            call_spread_check = self._check_spread_gate(
                atm_call.get("bid", 0), atm_call.get("ask", 0), call_symbol
            )
            if not call_spread_check.get("approved", False):
                result["error"] = f"Spread gate rejected call: {call_spread_check.get('reason')}"
                return result
            
            try:
                order = self._alpaca.place_options_order(
                    symbol=call_symbol,
                    qty=contracts, side="buy", order_type="limit",
                    limit_price=round(call_cost * 1.02, 2)
                )
                order_ids.append(order.get("id", "unknown"))
                self._logger.log("straddle_call_placed", {
                    "symbol": call_symbol,
                    "contracts": contracts,
                    "price": call_cost
                })
            except Exception as e:
                result["error"] = f"Call order failed: {e}"
                return result
            
            # Leg 2: Buy ATM put
            put_symbol = atm_put.get("symbol")
            if not put_symbol:
                result["error"] = "Missing put symbol"
                return result
            
            # SPREAD GATE - Reject wide-market trades (Safety Priority 0)
            put_spread_check = self._check_spread_gate(
                atm_put.get("bid", 0), atm_put.get("ask", 0), put_symbol
            )
            if not put_spread_check.get("approved", False):
                self._cancel_orders(order_ids)  # Rollback first leg
                result["error"] = f"Spread gate rejected put: {put_spread_check.get('reason')}"
                return result
            
            try:
                order = self._alpaca.place_options_order(
                    symbol=put_symbol,
                    qty=contracts, side="buy", order_type="limit",
                    limit_price=round(put_cost * 1.02, 2)
                )
                order_ids.append(order.get("id", "unknown"))
                self._logger.log("straddle_put_placed", {
                    "symbol": put_symbol,
                    "contracts": contracts,
                    "price": put_cost
                })
            except Exception as e:
                self._cancel_orders(order_ids)  # Rollback on failure
                result["error"] = f"Put order failed: {e}"
                return result
            
            order_id = ",".join(order_ids)
            
            trade_data = {
                "strategy": "straddle",
                "ticker": ticker,
                "contracts": contracts,
                "strike": atm_call["strike"],
                "call_symbol": call_symbol,
                "put_symbol": put_symbol,
                "call_cost": call_cost,
                "put_cost": put_cost,
                "total_cost": total_cost * contracts * 100,
                "timestamp": get_market_clock().now().timestamp(),
                "entry_time": get_market_clock().now().isoformat(),
                "order_id": order_id,
                "underlying_price": underlying_price
            }
            
            trade_key = f"options_trades.{self.bot_id}.{int(trade_data['timestamp'])}"
            set_state(trade_key, json.dumps(trade_data))
            
            entry_key = f"entry_time.{self.bot_id}.{ticker}"
            set_state(entry_key, get_market_clock().now().isoformat())
            
            # Store strategy for performance tracking on exit
            strategy_key = f"options.strategy.{self.bot_id}.{ticker}"
            set_state(strategy_key, "straddle")
            
            result.update({
                "success": True,
                "expected_profit": total_cost * contracts * 100 * config["profit_target"],
                "risk": total_cost * contracts * 100,
                "trade_data": trade_data
            })
            
            self._logger.log("straddle_executed", trade_data)
            
        except Exception as e:
            result["error"] = str(e)
            self._logger.error(f"Straddle execution failed: {e}")
        
        return result
    
    def _generate_options_chain(self, ticker: str, underlying_price: float,
                               option_type: str, target_dte: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get options chain - tries real Alpaca data first, falls back to simulation.
        
        Production-level: Attempts to fetch real options chain from Alpaca API
        with real Greeks (Delta, Gamma, Theta, Vega, Rho) and IV.
        Falls back to Black-Scholes simulation if real data unavailable.
        
        Args:
            ticker: Underlying symbol (e.g., "SPY", "QQQ")
            underlying_price: Current stock price
            option_type: "call" or "put"
            target_dte: Optional specific DTE to target (for calendar spreads)
            
        Returns:
            List of option contracts with strikes, prices, and complete Greeks
        """
        # STEP 1: Try to fetch real options chain from Alpaca
        try:
            # If target_dte specified, use a narrow range around that DTE
            if target_dte is not None:
                dte_min = max(0, target_dte - 3)
                dte_max = target_dte + 3
            else:
                # Calculate date range for options (7-60 days out by default, or 0DTE)
                dte_min = self._config.dte_min if self._config else 7
                dte_max = self._config.dte_max if self._config else 60
            
            # For 0DTE, use today's date for both bounds
            if dte_min == 0 and dte_max == 0:
                expiration_gte = get_market_clock().now().strftime("%Y-%m-%d")
                expiration_lte = get_market_clock().now().strftime("%Y-%m-%d")
            else:
                expiration_gte = (get_market_clock().now() + timedelta(days=dte_min)).strftime("%Y-%m-%d")
                expiration_lte = (get_market_clock().now() + timedelta(days=dte_max)).strftime("%Y-%m-%d")
            
            # Calculate strike range (15% around current price)
            strike_range = underlying_price * 0.15
            strike_gte = underlying_price - strike_range
            strike_lte = underlying_price + strike_range
            
            # Fetch real options chain from Alpaca
            real_chain = self._alpaca.get_options_chain(
                underlying_symbol=ticker,
                expiration_date_gte=expiration_gte,
                expiration_date_lte=expiration_lte,
                strike_price_gte=strike_gte,
                strike_price_lte=strike_lte,
                option_type=option_type
            )
            
            if real_chain and len(real_chain) > 0:
                self._logger.log("options_chain_real_data", {
                    "ticker": ticker,
                    "contracts": len(real_chain),
                    "option_type": option_type
                })
                return real_chain
                
        except Exception as e:
            self._logger.warn(f"Real options chain fetch failed for {ticker}, using simulation: {e}")
        
        # STEP 2: Fall back to Black-Scholes simulation if real data unavailable
        options = []
        
        # If target_dte specified, use that; otherwise use config range
        if target_dte is not None:
            expiry_days = [target_dte]
        else:
            dte_min = self._config.dte_min if self._config else 7
            dte_max = self._config.dte_max if self._config else 60
            
            # Generate options for multiple expiration dates
            # For 0DTE (dte_min=0, dte_max=0), only generate same-day options
            if dte_min == 0 and dte_max == 0:
                expiry_days = [0]  # 0DTE only
            else:
                expiry_days = [7, 14, 21, 30, 45, 60]
        
        for dte in expiry_days:
            # Skip if outside configured DTE range (unless target_dte specified)
            if target_dte is None:
                dte_min = self._config.dte_min if self._config else 7
                dte_max = self._config.dte_max if self._config else 60
                if dte < dte_min or dte > dte_max:
                    continue
            
            expiry_date = get_market_clock().now() + timedelta(days=dte)
            
            # Generate strikes around current price
            strike_range = max(10, int(underlying_price * 0.15))  # 15% range
            strike_increment = 1 if underlying_price < 200 else 5
            
            for i in range(-strike_range, strike_range + 1, strike_increment):
                strike = round(underlying_price + i, 2)
                
                # Calculate option price using full Black-Scholes with all Greeks
                option_data = self._calculate_option_price(
                    underlying_price, strike, dte, option_type
                )
                
                if option_data:
                    option_data.update({
                        "symbol": f"{ticker}{expiry_date.strftime('%y%m%d')}{'C' if option_type == 'call' else 'P'}{int(strike*1000):08d}",
                        "type": option_type,
                        "strike": strike,
                        "expiry": expiry_date.strftime("%Y-%m-%d"),
                        "dte": dte
                    })
                    options.append(option_data)
        
        self._logger.log("options_chain_simulated", {
            "ticker": ticker,
            "contracts": len(options),
            "option_type": option_type
        })
        
        return options
    
    def _calculate_option_price(self, spot: float, strike: float, dte: int,
                               option_type: str, volatility: float = 0.25) -> Optional[Dict[str, Any]]:
        """
        Calculate option price using full Black-Scholes model with all Greeks.
        
        Production-level: Calculates complete Greeks (Delta, Gamma, Theta, Vega, Rho)
        for accurate position sizing and risk management.
        
        Args:
            spot: Current underlying price
            strike: Option strike price
            dte: Days to expiration
            option_type: "call" or "put"
            volatility: Implied volatility (annualized, default 25%)
            
        Returns:
            Dictionary with bid, ask, and complete Greeks:
            - delta: Rate of change of option price vs underlying price
            - gamma: Rate of change of delta vs underlying price
            - theta: Time decay (daily)
            - vega: Sensitivity to volatility changes
            - rho: Sensitivity to interest rate changes
            - iv: Implied volatility used
            - volume: Estimated volume (based on liquidity model)
            - open_interest: Estimated open interest
        """
        try:
            # Black-Scholes parameters
            time_to_expiry = dte / 365.0
            risk_free_rate = 0.05  # 5% risk-free rate (could be config-driven)
            
            if time_to_expiry <= 0 or strike <= 0 or spot <= 0:
                return None
            
            # Calculate d1 and d2 for Black-Scholes
            sqrt_t = math.sqrt(time_to_expiry)
            d1 = (math.log(spot / strike) + 
                  (risk_free_rate + 0.5 * volatility**2) * time_to_expiry) / \
                 (volatility * sqrt_t)
            d2 = d1 - volatility * sqrt_t
            
            # Standard normal PDF and CDF
            def norm_cdf(x):
                """Cumulative distribution function for standard normal"""
                return 0.5 * (1 + math.erf(x / math.sqrt(2)))
            
            def norm_pdf(x):
                """Probability density function for standard normal"""
                return math.exp(-0.5 * x**2) / math.sqrt(2 * math.pi)
            
            # Calculate option price based on type
            discount = math.exp(-risk_free_rate * time_to_expiry)
            
            if option_type == "call":
                price = spot * norm_cdf(d1) - strike * discount * norm_cdf(d2)
                delta = norm_cdf(d1)
                rho = strike * time_to_expiry * discount * norm_cdf(d2) / 100  # Per 1% rate change
            else:  # put
                price = strike * discount * norm_cdf(-d2) - spot * norm_cdf(-d1)
                delta = -norm_cdf(-d1)
                rho = -strike * time_to_expiry * discount * norm_cdf(-d2) / 100
            
            # GAMMA: Same for calls and puts
            # Rate of change of delta with respect to underlying price
            gamma = norm_pdf(d1) / (spot * volatility * sqrt_t)
            
            # THETA: Time decay (daily, not annualized)
            # Measures how much value the option loses per day
            theta_annual = -(spot * norm_pdf(d1) * volatility) / (2 * sqrt_t)
            if option_type == "call":
                theta_annual -= risk_free_rate * strike * discount * norm_cdf(d2)
            else:
                theta_annual += risk_free_rate * strike * discount * norm_cdf(-d2)
            theta = theta_annual / 365  # Convert to daily theta
            
            # VEGA: Sensitivity to volatility (per 1% change in IV)
            vega = spot * sqrt_t * norm_pdf(d1) / 100
            
            # Add realistic bid/ask spread based on liquidity
            # Tighter spreads for ATM options, wider for OTM
            moneyness = abs(spot - strike) / spot
            base_spread = max(0.05, price * 0.02)
            liquidity_adj = 1 + moneyness * 2  # Wider spread for OTM
            spread = base_spread * liquidity_adj
            
            # Volume/OI estimation based on moneyness and DTE
            # ATM options with 30-45 DTE have highest liquidity
            atm_factor = max(0.1, 1 - moneyness * 5)
            dte_factor = 1 - abs(dte - 30) / 60  # Peak at 30 DTE
            base_volume = 500
            base_oi = 2000
            
            return {
                "bid": max(0.01, price - spread/2),
                "ask": max(0.02, price + spread/2),
                "mid_price": price,
                "delta": round(delta, 4),
                "gamma": round(gamma, 6),
                "theta": round(theta, 4),
                "vega": round(vega, 4),
                "rho": round(rho, 4),
                "iv": volatility,
                "volume": int(base_volume * atm_factor * max(0.3, dte_factor)),
                "open_interest": int(base_oi * atm_factor * max(0.3, dte_factor))
            }
            
        except Exception as e:
            self._logger.warn(f"Option pricing calculation failed: {e}")
            return None
    
    def _find_optimal_spread(self, options_chain: List[Dict], config: Dict,
                            underlying_price: float) -> Optional[Tuple[Dict, Dict]]:
        """
        Find the optimal vertical spread based on strategy configuration.
        
        Scores spreads based on credit received, spread width, and liquidity.
        
        Args:
            options_chain: List of available options
            config: Strategy configuration with constraints
            underlying_price: Current stock price
            
        Returns:
            Tuple of (short_option, long_option) or None if no suitable spread
        """
        # Filter options by DTE
        min_dte = config.get("min_dte", self._config.dte_min if self._config else 7)
        max_dte = config.get("max_dte", self._config.dte_max if self._config else 60)
        
        filtered = [opt for opt in options_chain 
                   if min_dte <= opt["dte"] <= max_dte]
        
        best_spread = None
        best_score = 0
        
        short_delta_range = config.get("short_delta_range", (-0.30, -0.15))
        long_delta_range = config.get("long_delta_range", (-0.15, -0.05))
        
        for short_opt in filtered:
            # Check if short option meets delta criteria
            if not (short_delta_range[0] <= short_opt["delta"] <= short_delta_range[1]):
                continue
            
            for long_opt in filtered:
                # Check if long option meets delta criteria
                if not (long_delta_range[0] <= long_opt["delta"] <= long_delta_range[1]):
                    continue
                
                # Same expiration required
                if short_opt["dte"] != long_opt["dte"]:
                    continue
                
                # Proper spread structure (long further OTM)
                if short_opt["type"] == "put":
                    if long_opt["strike"] >= short_opt["strike"]:
                        continue
                    spread_width = short_opt["strike"] - long_opt["strike"]
                else:  # call
                    if long_opt["strike"] <= short_opt["strike"]:
                        continue
                    spread_width = long_opt["strike"] - short_opt["strike"]
                
                # Check spread width constraints
                width_range = config.get("spread_width_range", (3, 10))
                if not (width_range[0] <= spread_width <= width_range[1]):
                    continue
                
                # Calculate credit
                credit = short_opt["bid"] - long_opt["ask"]
                
                # Check credit constraints
                min_credit = config.get("min_credit", 0.30)
                max_credit = config.get("max_credit", 3.00)
                if not (min_credit <= credit <= max_credit):
                    continue
                
                # Score the spread (higher is better)
                credit_score = credit / max_credit
                width_score = 1 - (spread_width - width_range[0]) / (width_range[1] - width_range[0])
                liquidity_score = min(short_opt["volume"], long_opt["volume"]) / 1000
                
                total_score = credit_score * 0.5 + width_score * 0.3 + liquidity_score * 0.2
                
                if total_score > best_score:
                    best_score = total_score
                    best_spread = (short_opt, long_opt)
        
        return best_spread
    
    def _manage_position(self, position) -> None:
        """
        Manage an existing options position.
        
        When delegate_exits_to_exitbot is enabled (default), this method delegates
        all exit decisions to ExitBot v2 and only reports position status.
        
        When delegating is disabled, checks exit conditions in order of priority:
        1. Trailing stop triggered
        2. Stop loss hit
        3. Take profit reached
        4. Time stop (max hold duration)
        5. Session end (flatten before close)
        
        Args:
            position: Alpaca position object
        """
        # GUARD: Need config for exit thresholds
        if not self._config:
            return
        
        # DELEGATION GUARD: If ExitBot delegation is enabled, skip all exit logic
        # and let ExitBot v2 handle all exit decisions (stop-loss, take-profit, etc.)
        delegate_to_exitbot = self._config.delegate_exits_to_exitbot
        
        if delegate_to_exitbot:
            # ExitBot v2 is sole exit authority BUT ProfitSniper overrides
            # when it detects profit spike reversal (ExitBot is too slow for peaks)
            try:
                qty = abs(float(position.qty))
                if qty == 0:
                    return
                
                current_price = float(position.market_value) / qty
                entry_price = float(position.cost_basis) / qty
                pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                
                # ProfitSniper: override ExitBot if profit is spiking and reversing
                try:
                    sniper = get_profit_sniper()
                    position_key = f"opt_{self.bot_id}_{position.symbol}"
                    sniper_decision = sniper.evaluate(
                        position_key=position_key,
                        entry_price=entry_price,
                        current_price=current_price,
                        side=position.side or "long",
                        config=ProfitSniperConfig.for_options(),
                        bot_id=self.bot_id
                    )
                    if sniper_decision.should_exit:
                        if sniper_decision.exit_pct >= 100:
                            self._close_position(position, f"profit_sniper_{sniper_decision.reason}", pnl_pct)
                        else:
                            exit_qty = max(1, int(qty * sniper_decision.exit_pct / 100))
                            self._partial_close_options(position, exit_qty, f"profit_sniper_{sniper_decision.reason}", pnl_pct)
                        self._logger.log("options_sniper_override_exitbot", {
                            "symbol": position.symbol,
                            "reason": sniper_decision.reason,
                            "exit_pct": sniper_decision.exit_pct,
                            "peak_profit_pct": round(sniper_decision.peak_profit_pct, 3),
                            "current_profit_pct": round(sniper_decision.current_profit_pct, 3),
                            "ratchet_price": round(sniper_decision.ratchet_price, 4)
                        })
                        return
                except Exception as e:
                    self._logger.warn(f"ProfitSniper check failed for {position.symbol}: {e}")
                
                self._logger.log("options_position_delegated_to_exitbot", {
                    "bot_id": self.bot_id,
                    "symbol": position.symbol,
                    "qty": qty,
                    "current_price": round(current_price, 2),
                    "entry_price": round(entry_price, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "pnl_dollars": float(position.unrealized_pl) if hasattr(position, 'unrealized_pl') else 0,
                    "status": "delegated_to_exitbot",
                    "message": "Exit decisions delegated to ExitBot v2 - skipping self-exit checks"
                })
                return
            except Exception as e:
                self._logger.error(f"Error reporting position status for ExitBot delegation: {e}")
                return
        
        try:
            # Get current position value
            qty = abs(float(position.qty))
            if qty == 0:
                return
            
            current_price = float(position.market_value) / qty
            entry_price = float(position.cost_basis) / qty
            
            # Calculate P&L percentage
            pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
            
            # Track whether we should close and why
            should_close = False
            close_reason = ""
            
            # STEP 0: ProfitSniper — profit-priority exit (runs FIRST, overrides all)
            try:
                sniper = get_profit_sniper()
                position_key = f"opt_{self.bot_id}_{position.symbol}"
                sniper_decision = sniper.evaluate(
                    position_key=position_key,
                    entry_price=entry_price,
                    current_price=current_price,
                    side=position.side or "long",
                    config=ProfitSniperConfig.for_options(),
                    bot_id=self.bot_id
                )
                if sniper_decision.should_exit:
                    if sniper_decision.exit_pct >= 100:
                        should_close = True
                        close_reason = f"profit_sniper_{sniper_decision.reason}"
                    else:
                        exit_qty = max(1, int(qty * sniper_decision.exit_pct / 100))
                        self._partial_close_options(position, exit_qty, f"profit_sniper_{sniper_decision.reason}", pnl_pct)
                        return
            except Exception as e:
                self._logger.warn(f"ProfitSniper check failed for {position.symbol}: {e}")
            
            # STEP 1: Check trailing stop (if enabled)
            if not should_close and self._check_trailing_stop(position, current_price):
                should_close = True
                close_reason = "trailing_stop"
            
            # STEP 2: Check stop loss
            if not should_close and pnl_pct <= -self._config.stop_loss_pct:
                should_close = True
                close_reason = "stop_loss"
                self._logger.log("options_stop_loss_triggered", {
                    "symbol": position.symbol,
                    "pnl_pct": pnl_pct,
                    "threshold": -self._config.stop_loss_pct
                })
            
            # STEP 3: Check take profit
            if not should_close and pnl_pct >= self._config.take_profit_pct:
                should_close = True
                close_reason = "take_profit"
                self._logger.log("options_take_profit_triggered", {
                    "symbol": position.symbol,
                    "pnl_pct": pnl_pct,
                    "threshold": self._config.take_profit_pct
                })
            
            # STEP 4: Check time stop
            if not should_close:
                entry_time = self._get_entry_time(position)
                if entry_time:
                    hold_duration = (get_market_clock().now_naive() - entry_time).total_seconds() / 60
                    if hold_duration >= self._config.time_stop_minutes:
                        should_close = True
                        close_reason = "time_stop"
                        self._logger.log("options_time_stop_triggered", {
                            "symbol": position.symbol,
                            "hold_minutes": round(hold_duration, 1),
                            "threshold": self._config.time_stop_minutes
                        })
            
            # STEP 5: Check session end (flatten before close)
            # Note: Index options (XSP) have extended hours until 4:15 PM ET = 1:15 PM PST
            if not should_close:
                clock = get_market_clock()
                now = clock.now().time()
                
                # Extract underlying symbol from option symbol (e.g., "XSP240119C00450000" -> "XSP")
                underlying = self._extract_underlying_from_option(position.symbol)
                
                # Check if this is an index option with extended hours
                if underlying and self.is_index_option(underlying):
                    # Use dedicated accessor for index option properties (PST times)
                    props = self.get_index_option_properties(underlying)
                    
                    # Check if it's last trading day (use earlier close)
                    # Last trading day for XSP is typically 3rd Friday of month
                    is_last_trading_day = self._is_last_trading_day_for_option(position.symbol)
                    
                    if is_last_trading_day:
                        # Use accessor for last trading day close (allows symbol-specific overrides)
                        close_pst = props.get("last_trading_day_close_pst", "13:00")
                        schedule_type = "last_trading_day"
                    else:
                        close_pst = props.get("market_close_pst", "13:15")
                        schedule_type = "normal"
                    
                    # Parse and validate close time with proper error handling
                    try:
                        close_time = datetime.strptime(close_pst, "%H:%M").time()
                    except ValueError:
                        self._logger.error(f"Invalid close time format: {close_pst}, using fail-safe 13:00")
                        close_time = time(13, 0)  # Fail-closed: use earlier time
                    
                    self._logger.log("index_option_extended_hours", {
                        "symbol": position.symbol,
                        "underlying": underlying,
                        "close_time_pst": close_pst,
                        "schedule_type": schedule_type,
                        "is_last_trading_day": is_last_trading_day,
                        "cash_settled": props.get("cash_settled", True),
                        "european_style": props.get("european_style", True),
                        "assignment_risk": False  # Cash-settled = no assignment risk
                    })
                else:
                    close_time = clock.get_market_close()
                
                flatten_minutes = self._config.flatten_before_close_min
                flatten_time = (datetime.combine(datetime.today(), close_time) - 
                              timedelta(minutes=flatten_minutes)).time()
                
                if now >= flatten_time:
                    should_close = True
                    close_reason = "session_end"
            
            # Execute exit if any condition met
            if should_close:
                self._close_position(position, close_reason, pnl_pct)
                
        except Exception as e:
            self._logger.error(f"Options position management failed: {e}")
    
    def _check_trailing_stop(self, position, current_price: float) -> bool:
        """
        Check if trailing stop should trigger exit.
        
        Trailing stop tracks highest price since entry and places stop
        a certain percentage below that high.
        
        Args:
            position: Alpaca position object
            current_price: Current market price
            
        Returns:
            True if trailing stop triggered, False otherwise
        """
        # GUARD: Skip entirely if trailing stops are disabled
        if not self._config or not self._config.trailing_stop_enabled:
            return False
        
        try:
            # Generate unique position ID
            position_id = f"{position.symbol}_{position.side}_{position.qty}"
            
            # Get trailing stop manager
            trailing_manager = get_trailing_stop_manager()
            
            # Load existing trailing stop state for this position
            trailing_state = trailing_manager.load_state(
                self.bot_id, position_id, position.symbol, "option"
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
                    self.bot_id, position_id, position.symbol, "option",
                    entry_price, side, config
                )
            
            # Update trailing stop with current price
            trailing_state = trailing_manager.update_state(
                self.bot_id, position_id, position.symbol, "option",
                current_price, trailing_state
            )
            
            # Check if stop should trigger
            return trailing_manager.should_exit(trailing_state, current_price)
            
        except Exception as e:
            self._logger.error(f"Trailing stop check failed: {e}")
            return False
    
    def _get_entry_time(self, position) -> Optional[datetime]:
        """
        Get the entry time for a position from state database.
        
        Args:
            position: Alpaca position object
            
        Returns:
            datetime of entry, or None if not found
        """
        try:
            # Extract ticker from options symbol
            ticker = None
            for t in self.tickers:
                if t in position.symbol:
                    ticker = t
                    break
            
            if not ticker:
                return None
            
            entry_key = f"entry_time.{self.bot_id}.{ticker}"
            entry_time_str = get_state(entry_key)
            
            if entry_time_str:
                from ..core.clock import MarketClock
                return MarketClock.parse_iso_to_naive(entry_time_str)
            
            return None
            
        except Exception as e:
            self._logger.error(f"Failed to get entry time: {e}")
            return None
    
    def _partial_close_options(self, position, exit_qty: int, reason: str, pnl_pct: float) -> None:
        """Close a partial quantity of an options position."""
        try:
            side = "sell" if position.side == "long" else "buy"
            order_response = self._alpaca.place_market_order(
                symbol=position.symbol,
                side=side,
                qty=exit_qty
            )
            pnl_dollars = float(position.unrealized_pl) if hasattr(position, 'unrealized_pl') else 0
            self._logger.log("options_partial_close", {
                "symbol": position.symbol,
                "side": side,
                "exit_qty": exit_qty,
                "total_qty": abs(float(position.qty)),
                "reason": reason,
                "pnl_pct": round(pnl_pct, 2),
                "pnl_dollars": pnl_dollars,
                "order_id": order_response.get("id"),
                "bot_id": self.bot_id
            })
        except Exception as e:
            self._logger.error(f"Failed to partial close options position {position.symbol}: {e}")

    def _close_position(self, position, reason: str, pnl_pct: float) -> None:
        """
        Close an options position with detailed logging.
        
        Args:
            position: Alpaca position object to close
            reason: Reason for closing (e.g., "stop_loss", "take_profit")
            pnl_pct: Current P&L percentage for logging
        """
        try:
            # Determine exit side (opposite of current position)
            side = "sell" if position.side == "long" else "buy"
            qty = abs(float(position.qty))
            
            # Place market order to close
            order_response = self._alpaca.place_market_order(
                symbol=position.symbol,
                side=side,
                qty=qty
            )
            
            # Clean up state entries
            for ticker in self.tickers:
                if ticker in position.symbol:
                    entry_key = f"entry_time.{self.bot_id}.{ticker}"
                    delete_state(entry_key)
                    break
            
            pnl_dollars = float(position.unrealized_pl) if hasattr(position, 'unrealized_pl') else 0
            
            self._logger.log("options_position_closed", {
                "symbol": position.symbol,
                "side": side,
                "qty": qty,
                "reason": reason,
                "pnl_pct": round(pnl_pct, 2),
                "pnl_dollars": pnl_dollars,
                "order_id": order_response.get("id"),
                "bot_id": self.bot_id
            })
            
            # Record trade for strategy performance tracking
            try:
                # Extract ticker from option symbol (e.g., "AAPL250117C00150000" -> "AAPL")
                ticker = position.symbol[:4].rstrip("0123456789") if len(position.symbol) > 4 else position.symbol
                for t in self.tickers:
                    if position.symbol.startswith(t):
                        ticker = t
                        break
                
                # Get strategy from state (stored at entry time)
                strategy_key = f"options.strategy.{self.bot_id}.{ticker}"
                strategy = get_state(strategy_key) or "unknown"
                
                # Record to tracker
                tracker = get_strategy_tracker()
                tracker.record_trade(
                    ticker=ticker,
                    strategy=strategy,
                    pnl=pnl_dollars,
                    pnl_pct=pnl_pct
                )
                
                # Clean up strategy state
                delete_state(strategy_key)
            except Exception as e:
                self._logger.log("strategy_tracker_record_error", {"error": str(e)})
            
        except Exception as e:
            self._logger.error(f"Failed to close options position {position.symbol}: {e}")


# =============================================================================
# SINGLETON FACTORY - Provides cached bot instances
# =============================================================================

_options_bot_instances: Dict[str, OptionsBot] = {}


def get_options_bot(bot_id: str = "options_core") -> OptionsBot:
    """
    Get or create an OptionsBot instance (singleton per bot_id).
    
    Args:
        bot_id: Unique identifier for this bot instance
        
    Returns:
        OptionsBot instance (cached)
    """
    global _options_bot_instances
    if bot_id not in _options_bot_instances:
        _options_bot_instances[bot_id] = OptionsBot(bot_id)
    return _options_bot_instances[bot_id]
