"""
PreMarketIntelligenceService - Heuristic pre-market analysis pipeline v1.

NOTE: Previously labeled "BlackRock-style" - renamed for honesty.
These are heuristics that should be validated through forward testing.

Runs 6:00-6:30 AM PST to gather overnight intelligence before trading session:
- Gap analysis (overnight price movements)
- IV monitoring (implied volatility levels and percentiles)
- Volume surge detection (pre-market activity)
- Earnings/news event flags
- Dynamic universe screening and opportunity ranking

Results are cached in shared state for both TwentyMinuteBot and OptionsBot.
"""
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import Enum
import math

from ..core.logging import get_logger
from ..core.config import load_settings, load_bots_config
from ..core.state import get_state, set_state

# ETF symbols - skip fundamentals/earnings lookups (no quoteSummary data available)
ETF_SYMBOLS = {
    "SPY", "QQQ", "IWM", "DIA", "IVV", "VOO", "VTI", "VXUS",
    "XLF", "XLE", "XLK", "XLV", "XLI", "XLP", "XLU", "XLB", "XLY", "XLRE",
    "TLT", "IEF", "SHY", "BND", "AGG", "LQD", "HYG", "JNK",
    "GLD", "SLV", "USO", "UNG", "FXE", "UUP",
    "VXX", "UVXY", "SVXY", "VIXY",
    "TQQQ", "SQQQ", "SPXL", "SPXS", "UPRO", "SDS", "QLD", "QID",
    "EFA", "EEM", "VEA", "VWO", "IEFA", "IEMG",
}


class EventType(Enum):
    """Types of overnight events that affect trading."""
    EARNINGS = "earnings"
    DIVIDEND = "dividend"
    SPLIT = "split"
    NEWS = "news"
    FDA = "fda"
    MACRO = "macro"
    NONE = "none"


@dataclass
class GapAnalysis:
    """
    Overnight gap analysis for a single ticker.
    
    Gap-ATR ratio is the key signal for TwentyMinuteBot:
    - gap_atr < 0.3: Dead money, skip
    - gap_atr 0.3-1.5: Sweet spot for gap plays
    - gap_atr > 1.5: Too volatile, reduced size or skip
    """
    ticker: str
    prev_close: float
    current_price: float
    gap_pct: float
    atr_14: float  # 14-period ATR in dollars
    atr_pct: float  # ATR as % of price
    gap_atr: float  # |gap_pct| / atr_pct - the key ratio
    direction: str  # "up" or "down"
    is_significant: bool
    is_sweet_spot: bool  # gap_atr in [0.3, 1.5] range
    premarket_volume: int
    premarket_relvol: float  # premarket vol / avg daily vol (proxy)
    volume_surge_pct: float  # vs 20-day average
    timestamp: datetime = field(default_factory=datetime.now)
    
    @property
    def gap_atr_category(self) -> str:
        """Categorize gap-ATR ratio for decision making."""
        if self.gap_atr < 0.3:
            return "dead"  # Not worth trading
        elif self.gap_atr <= 1.5:
            return "sweet_spot"  # Ideal for gap plays
        elif self.gap_atr <= 2.5:
            return "elevated"  # Reduce size
        else:
            return "extreme"  # Skip or hedge


@dataclass
class IVAnalysis:
    """
    Implied volatility analysis for options trading.
    
    IVP (IV Percentile) drives strategy selection:
    - IVP > 60: Premium selling (iron condors, credit spreads)
    - IVP < 30: Premium buying (long calls/puts, straddles)
    - IVP 30-60: Neutral, directional plays okay
    
    Fallback: If IVP unavailable, use IV rank or HV/IV ratio as proxy.
    """
    ticker: str
    current_iv: float
    iv_percentile: float  # 0-100, where we are vs last 52 weeks
    iv_rank: float  # (current - 52w low) / (52w high - 52w low)
    iv_term_skew: float  # front month vs back month IV difference
    put_call_skew: float  # put IV vs call IV at same delta
    hv_iv_spread: float  # realized vol vs implied vol
    is_elevated: bool  # IV > 60th percentile
    is_depressed: bool  # IV < 30th percentile
    ivp_source: str = "direct"  # "direct", "iv_rank_proxy", "hv_ratio_proxy"
    ivp_confidence: float = 1.0  # 0-1, lower for fallback methods
    timestamp: datetime = field(default_factory=datetime.now)
    
    @property
    def strategy_bias(self) -> str:
        """Determine strategy bias based on IVP."""
        if self.is_elevated:
            return "sell_premium"
        elif self.is_depressed:
            return "buy_premium"
        else:
            return "neutral"


@dataclass
class OptionsLiquidity:
    """
    Options liquidity scoring - HARD GATE before ranking.
    
    No hedge fund fantasy survives bad fills.
    These are hard requirements, not soft preferences.
    """
    ticker: str
    underlying_spread_pct: float  # Underlying bid-ask spread as %
    option_oi: int  # Open interest
    option_volume: int  # Daily volume
    option_spread_pct: float  # Options bid-ask spread as %
    liquidity_score: float  # 0-100 composite
    
    # Hard gates - fail any = drop ticker
    passes_spread_gate: bool  # underlying spread < 0.1%
    passes_oi_gate: bool  # OI > 500
    passes_volume_gate: bool  # volume > 100
    passes_option_spread_gate: bool  # option spread < 5%
    
    @property
    def passes_all_gates(self) -> bool:
        """All hard gates must pass."""
        return (self.passes_spread_gate and 
                self.passes_oi_gate and 
                self.passes_volume_gate and
                self.passes_option_spread_gate)
    
    @property
    def rejection_reason(self) -> Optional[str]:
        """Return first failing gate, or None if all pass."""
        if not self.passes_spread_gate:
            return f"underlying_spread_too_wide:{self.underlying_spread_pct:.2f}%"
        if not self.passes_oi_gate:
            return f"oi_too_low:{self.option_oi}"
        if not self.passes_volume_gate:
            return f"volume_too_low:{self.option_volume}"
        if not self.passes_option_spread_gate:
            return f"option_spread_too_wide:{self.option_spread_pct:.2f}%"
        return None


@dataclass
class CatalystRisk:
    """
    Catalyst/event risk assessment.
    
    Earnings and major news require special handling:
    - Within 3 days of earnings: reduce size or skip
    - News flag active: extra caution on directional
    """
    ticker: str
    earnings_flag: bool
    days_to_earnings: Optional[int]  # None if no upcoming earnings
    news_flag: bool
    fda_flag: bool  # For biotech
    macro_flag: bool  # Fed day, etc.
    
    @property
    def catalyst_level(self) -> str:
        """Categorize catalyst risk."""
        if self.fda_flag or (self.earnings_flag and self.days_to_earnings and self.days_to_earnings <= 1):
            return "extreme"  # Skip or straddle only
        elif self.earnings_flag and self.days_to_earnings and self.days_to_earnings <= 3:
            return "high"  # Reduce size 50%
        elif self.earnings_flag or self.news_flag:
            return "moderate"  # Reduce size 25%
        else:
            return "low"  # Normal trading


@dataclass
class TickerIntelligence:
    """
    Complete pre-market intelligence for a single ticker.
    
    Bot-specific consumption:
    - TwentyMinuteBot: gap_atr in sweet spot, tight underlying spread
    - OptionsBot: high options liquidity, IVP fits strategy, catalyst handled
    """
    ticker: str
    gap: Optional[GapAnalysis]
    iv: Optional[IVAnalysis]
    liquidity: Optional[OptionsLiquidity]
    catalyst: Optional[CatalystRisk]
    events: List[EventType]
    opportunity_score: float  # 0-100 composite ranking
    liquidity_score: float  # 0-100 based on volume/OI/spreads (legacy)
    recommended_strategies: List[str]
    risk_flags: List[str]
    
    # Hard gate results - fail = drop before ranking
    passes_liquidity_gates: bool = True
    gate_rejection_reason: Optional[str] = None
    
    # Bot-specific eligibility
    eligible_for_twentymin: bool = False
    eligible_for_options: bool = False
    twentymin_score: float = 0.0  # 0-100, gap-specific scoring
    options_score: float = 0.0  # 0-100, IV/liquidity-specific scoring
    
    timestamp: datetime = field(default_factory=datetime.now)
    
    @property
    def is_tradeable(self) -> bool:
        """Ticker passes all hard gates and is eligible for at least one bot."""
        return self.passes_liquidity_gates and (self.eligible_for_twentymin or self.eligible_for_options)


@dataclass
class PreMarketCache:
    """Cached pre-market intelligence for the trading session."""
    session_date: str
    analysis_start: datetime
    analysis_end: Optional[datetime]
    tickers: Dict[str, TickerIntelligence]
    ranked_opportunities: List[str]  # Tickers sorted by opportunity score
    market_regime: str  # from regime detection
    regime_multiplier: float
    is_complete: bool = False


class PreMarketIntelligenceService:
    """
    Institutional-grade pre-market intelligence gathering.
    
    Runs during pre-market intel window (configured in settings.yaml)
    to analyze overnight developments and prepare intelligence for the trading session.
    """
    
    # Opportunity scoring weights (sum to 100)
    WEIGHT_GAP = 40
    WEIGHT_IV = 25
    WEIGHT_VOLUME = 20
    WEIGHT_EVENT = 15
    
    def __init__(self):
        from ..core.clock import get_market_clock
        
        self._logger = get_logger()
        self._cache: Optional[PreMarketCache] = None
        self._alpaca = None
        self._settings = None
        self._universe_config = None
        self._clock = get_market_clock()
        
        self._logger.log("premarket_intel_init", {"status": "initialized"})
    
    def _get_alpaca(self):
        """Lazy load Alpaca client."""
        if self._alpaca is None:
            from .alpaca_client import AlpacaClient
            self._alpaca = AlpacaClient()
        return self._alpaca
    
    def _load_universe_config(self) -> Dict[str, Any]:
        """Load ticker universe configuration."""
        if self._universe_config is None:
            try:
                import yaml
                with open("config/ticker_universe.yaml", "r") as f:
                    self._universe_config = yaml.safe_load(f)
            except Exception as e:
                self._logger.warn(f"Failed to load ticker_universe.yaml: {e}")
                # Default universe
                self._universe_config = {
                    "tiers": {
                        "core_indices": ["SPY", "QQQ", "IWM"],
                        "large_cap_tech": ["AAPL", "MSFT", "NVDA", "AMD", "META", "GOOGL", "AMZN", "TSLA"],
                        "liquid_etfs": ["XLF", "XLE", "XLK", "GLD", "SLV", "TLT"]
                    },
                    "screening": {
                        "min_adv_millions": 50,
                        "min_option_oi": 500,
                        "min_option_volume": 1000,
                        "max_spread_dollars": 0.15
                    },
                    "limits": {
                        "max_universe_size": 20,
                        "max_per_sector": 3
                    }
                }
        return self._universe_config
    
    def is_pre_market_window(self) -> bool:
        """Check if current time is within pre-market analysis window."""
        return self._clock.is_pre_market_intel_window()
    
    def get_cached_intelligence(self) -> Optional[PreMarketCache]:
        """Get cached pre-market intelligence for today's session."""
        today = self._clock.now().strftime("%Y-%m-%d")
        
        # Check in-memory cache first
        if self._cache and self._cache.session_date == today:
            return self._cache
        
        # Try to load from state
        cached_data = get_state("premarket_intelligence")
        if cached_data and cached_data.get("session_date") == today:
            self._cache = self._deserialize_cache(cached_data)
            return self._cache
        
        return None
    
    def get_ticker_intelligence(self, ticker: str) -> Optional[TickerIntelligence]:
        """Get pre-market intelligence for a specific ticker."""
        cache = self.get_cached_intelligence()
        if cache and ticker in cache.tickers:
            return cache.tickers[ticker]
        return None
    
    def get_ranked_opportunities(self, top_n: int = 5) -> List[TickerIntelligence]:
        """Get top N ranked opportunities from pre-market analysis."""
        cache = self.get_cached_intelligence()
        if not cache:
            return []
        
        result = []
        for ticker in cache.ranked_opportunities[:top_n]:
            if ticker in cache.tickers:
                result.append(cache.tickers[ticker])
        return result
    
    def run_analysis(self) -> PreMarketCache:
        """
        Run full pre-market analysis pipeline.
        
        This is the main entry point - gathers all intelligence and caches it.
        """
        today = self._clock.now().strftime("%Y-%m-%d")
        start_time = self._clock.now()
        
        self._logger.log("premarket_analysis_start", {
            "session_date": today,
            "time": start_time.strftime("%H:%M:%S")
        })
        
        # Initialize cache
        self._cache = PreMarketCache(
            session_date=today,
            analysis_start=start_time,
            analysis_end=None,
            tickers={},
            ranked_opportunities=[],
            market_regime="normal",
            regime_multiplier=1.0
        )
        
        try:
            # Step 1: Get market regime
            regime_data = self._get_market_regime()
            self._cache.market_regime = regime_data.get("regime", "normal")
            self._cache.regime_multiplier = regime_data.get("multiplier", 1.0)
            
            # Step 2: Build analysis universe
            universe = self._build_universe()
            
            self._logger.log("premarket_universe_built", {
                "size": len(universe),
                "tickers": universe
            })
            
            # DEFENSIVE: Warn if universe is empty (config issue)
            if len(universe) == 0:
                self._logger.log("premarket_universe_warning", {
                    "size": 0,
                    "warning": "EMPTY_UNIVERSE - check ticker_universe.yaml tiers section"
                })
            
            # Step 3: Analyze each ticker
            for ticker in universe:
                try:
                    intel = self._analyze_ticker(ticker)
                    if intel:
                        self._cache.tickers[ticker] = intel
                except Exception as e:
                    self._logger.warn(f"Failed to analyze {ticker}: {e}")
            
            # Step 4: Rank opportunities
            self._cache.ranked_opportunities = self._rank_opportunities()
            
            # Step 5: Mark complete and persist
            self._cache.analysis_end = self._clock.now()
            self._cache.is_complete = True
            
            self._persist_cache()
            
            self._logger.log("premarket_analysis_complete", {
                "tickers_analyzed": len(self._cache.tickers),
                "top_opportunities": self._cache.ranked_opportunities[:5],
                "duration_seconds": (self._cache.analysis_end - start_time).total_seconds()
            })
            
        except Exception as e:
            self._logger.error(f"Pre-market analysis failed: {e}")
            self._cache.is_complete = False
        
        return self._cache
    
    def _build_universe(self) -> List[str]:
        """Build the analysis universe from configured tiers."""
        config = self._load_universe_config()
        tiers = config.get("tiers", {})
        limits = config.get("limits", {})
        max_size = limits.get("max_universe_size", 20)
        
        # Collect all tickers from tiers (prioritize core indices)
        universe = []
        
        # Core indices first (always included)
        core = tiers.get("core_indices", [])
        universe.extend(core)
        
        # Large cap tech
        tech = tiers.get("large_cap_tech", [])
        universe.extend(tech)
        
        # Liquid ETFs
        etfs = tiers.get("liquid_etfs", [])
        universe.extend(etfs)
        
        # Remove duplicates, limit size
        seen = set()
        unique = []
        for t in universe:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        
        return unique[:max_size]
    
    def _analyze_ticker(self, ticker: str) -> Optional[TickerIntelligence]:
        """
        Analyze a single ticker for pre-market intelligence.
        
        Performs comprehensive analysis with HARD GATES:
        1. Liquidity gates (spread, OI, volume) - fail = drop
        2. Gap-ATR analysis for TwentyMinuteBot eligibility
        3. IVP analysis for OptionsBot strategy selection
        4. Catalyst risk assessment
        5. Bot-specific scoring
        """
        alpaca = self._get_alpaca()
        
        # Step 1: HARD GATE - Liquidity analysis
        liquidity = self._analyze_liquidity(ticker, alpaca)
        passes_liquidity_gates = True
        gate_rejection_reason = None
        
        if liquidity:
            passes_liquidity_gates = liquidity.passes_all_gates
            gate_rejection_reason = liquidity.rejection_reason
        else:
            # Can't assess liquidity = fail closed
            passes_liquidity_gates = False
            gate_rejection_reason = "liquidity_analysis_failed"
        
        # Log gate failures
        if not passes_liquidity_gates:
            self._logger.log("ticker_gate_failed", {
                "ticker": ticker,
                "reason": gate_rejection_reason
            })
        
        # Step 2: Gap analysis
        gap = self._analyze_gap(ticker, alpaca)
        
        # Step 3: IV analysis
        iv = self._analyze_iv(ticker, alpaca)
        
        # Step 4: Catalyst analysis
        catalyst = self._analyze_catalyst(ticker)
        
        # Step 5: Event detection (for backward compatibility)
        events = self._detect_events(ticker)
        
        # Step 6: Calculate scores
        opportunity_score = self._calculate_opportunity_score(gap, iv, events)
        legacy_liquidity_score = liquidity.liquidity_score if liquidity else 0
        
        # Step 7: Determine recommended strategies
        strategies = self._recommend_strategies(gap, iv, events)
        
        # Step 8: Identify risk flags
        risk_flags = self._identify_risks(gap, iv, events)
        
        # Step 9: Bot-specific eligibility and scoring
        eligible_for_twentymin = False
        eligible_for_options = False
        twentymin_score = 0.0
        options_score = 0.0
        
        if passes_liquidity_gates:
            # TwentyMinuteBot eligibility: gap_atr in sweet spot + tight spread
            if gap and gap.is_sweet_spot:
                eligible_for_twentymin = True
                # Score based on gap_atr quality and volume
                gap_atr_score = 50 if 0.5 <= gap.gap_atr <= 1.2 else 30  # Optimal range
                volume_score = min(30, gap.premarket_relvol * 100)
                catalyst_penalty = 20 if (catalyst and catalyst.catalyst_level in ["high", "extreme"]) else 0
                twentymin_score = max(0, gap_atr_score + volume_score - catalyst_penalty)
            
            # OptionsBot eligibility: liquidity + IVP valid
            if liquidity and liquidity.liquidity_score > 50:
                eligible_for_options = True
                # Score based on IV opportunity and liquidity
                iv_score = 0
                if iv:
                    if iv.is_elevated:
                        iv_score = 40  # Premium selling opportunity
                    elif iv.is_depressed:
                        iv_score = 35  # Premium buying opportunity
                    else:
                        iv_score = 20  # Neutral
                    # Adjust for confidence
                    iv_score *= iv.ivp_confidence
                
                options_score = liquidity.liquidity_score * 0.5 + iv_score
        
        return TickerIntelligence(
            ticker=ticker,
            gap=gap,
            iv=iv,
            liquidity=liquidity,
            catalyst=catalyst,
            events=events,
            opportunity_score=opportunity_score,
            liquidity_score=legacy_liquidity_score,
            recommended_strategies=strategies,
            risk_flags=risk_flags,
            passes_liquidity_gates=passes_liquidity_gates,
            gate_rejection_reason=gate_rejection_reason,
            eligible_for_twentymin=eligible_for_twentymin,
            eligible_for_options=eligible_for_options,
            twentymin_score=twentymin_score,
            options_score=options_score
        )
    
    def _analyze_gap(self, ticker: str, alpaca) -> Optional[GapAnalysis]:
        """
        Analyze overnight gap for a ticker.
        
        Key output: gap_atr ratio which determines TwentyMinuteBot eligibility.
        """
        try:
            # Get previous close and current price
            bars = alpaca.get_bars(ticker, timeframe="1Day", limit=15)  # Need 14 for ATR
            if not bars or len(bars) < 2:
                return None
            
            prev_close = float(bars[-2].close) if hasattr(bars[-2], 'close') else float(bars[-2].c if hasattr(bars[-2], 'c') else bars[-2]["c"])
            
            # Get current/pre-market price
            quote = alpaca.get_latest_quote(ticker)
            if not quote:
                return None
            
            current_price = float(quote.ask_price if hasattr(quote, 'ask_price') else quote.get("ask_price", prev_close))
            if current_price <= 0:
                current_price = prev_close
            
            gap_pct = ((current_price - prev_close) / prev_close) * 100
            
            # Calculate 14-period ATR
            atr_14 = self._calculate_atr(ticker, alpaca, period=14)
            atr_pct = (atr_14 / prev_close * 100) if prev_close > 0 else 2.0
            
            # Gap-ATR ratio: the key metric for TwentyMinuteBot
            # Sweet spot is 0.3 to 1.5 - enough movement without being insane
            gap_atr = abs(gap_pct) / atr_pct if atr_pct > 0 else 0
            is_sweet_spot = 0.3 <= gap_atr <= 1.5
            
            # Get pre-market volume
            pre_volume = self._get_premarket_volume(ticker, alpaca)
            avg_volume = self._get_avg_volume(ticker, alpaca)
            volume_surge = ((pre_volume / avg_volume) - 1) * 100 if avg_volume > 0 else 0
            
            # Premarket relative volume (proxy)
            # If avg daily volume is 1M and we see 50k premarket, relvol = 0.05
            premarket_relvol = pre_volume / avg_volume if avg_volume > 0 else 0
            
            return GapAnalysis(
                ticker=ticker,
                prev_close=prev_close,
                current_price=current_price,
                gap_pct=gap_pct,
                atr_14=atr_14,
                atr_pct=atr_pct,
                gap_atr=gap_atr,
                direction="up" if gap_pct > 0 else "down",
                is_significant=abs(gap_pct) >= 0.5,
                is_sweet_spot=is_sweet_spot,
                premarket_volume=pre_volume,
                premarket_relvol=premarket_relvol,
                volume_surge_pct=volume_surge
            )
            
        except Exception as e:
            self._logger.warn(f"Gap analysis failed for {ticker}: {e}")
            return None
    
    def _analyze_iv(self, ticker: str, alpaca) -> Optional[IVAnalysis]:
        """
        Analyze implied volatility for options trading.
        
        FALLBACK STRATEGY when true IVP unavailable:
        1. Try options chain data (if available)
        2. Fall back to IV rank (current-low)/(high-low)
        3. Fall back to HV/IV ratio proxy
        """
        try:
            # For now, estimate IV from recent price action
            bars = alpaca.get_bars(ticker, timeframe="1Day", limit=60)  # Need more for better HV estimate
            if not bars or len(bars) < 20:
                return None
            
            # Calculate historical volatility (HV)
            returns = []
            for i in range(1, len(bars)):
                prev_c = float(bars[i-1].close) if hasattr(bars[i-1], 'close') else float(bars[i-1].c if hasattr(bars[i-1], 'c') else bars[i-1]["c"])
                curr_c = float(bars[i].close) if hasattr(bars[i], 'close') else float(bars[i].c if hasattr(bars[i], 'c') else bars[i]["c"])
                if prev_c > 0:
                    returns.append(math.log(curr_c / prev_c))
            
            if not returns:
                return None
            
            import statistics
            hv_daily = statistics.stdev(returns) if len(returns) > 1 else 0.02
            hv_annual = hv_daily * math.sqrt(252) * 100  # Annualized %
            
            # Estimate IV (typically ~1.1-1.3x of HV during normal times)
            # This is a fallback - real IV would come from options chain
            estimated_iv = hv_annual * 1.15
            
            # Determine IVP source and confidence
            ivp_source = "hv_ratio_proxy"  # Default fallback
            ivp_confidence = 0.6  # Lower confidence for proxy
            
            # Try to get better estimate using HV/IV ratio
            # If HV is higher than typical, IV likely elevated
            # HV > 35% annualized suggests elevated IV regime
            if hv_annual > 35:
                iv_percentile = min(85, 50 + (hv_annual - 25) * 1.5)
            elif hv_annual > 25:
                iv_percentile = 50 + (hv_annual - 25) * 1.0
            elif hv_annual > 15:
                iv_percentile = 30 + (hv_annual - 15) * 2.0
            else:
                iv_percentile = min(30, hv_annual * 2)
            
            iv_percentile = min(100, max(0, iv_percentile))
            
            # IV rank as additional proxy
            # Assume typical range is 15-50 for most stocks
            iv_rank = min(100, max(0, (estimated_iv - 15) / (50 - 15) * 100))
            
            # If IV rank gives significantly different signal, average them
            if abs(iv_rank - iv_percentile) > 20:
                iv_percentile = (iv_percentile + iv_rank) / 2
                ivp_source = "blended_proxy"
                ivp_confidence = 0.5
            
            return IVAnalysis(
                ticker=ticker,
                current_iv=estimated_iv,
                iv_percentile=iv_percentile,
                iv_rank=iv_rank,
                iv_term_skew=0,  # Would need options chain data
                put_call_skew=0,  # Would need options chain data
                hv_iv_spread=estimated_iv - hv_annual,
                is_elevated=iv_percentile > 60,
                is_depressed=iv_percentile < 30,
                ivp_source=ivp_source,
                ivp_confidence=ivp_confidence
            )
            
        except Exception as e:
            self._logger.warn(f"IV analysis failed for {ticker}: {e}")
            return None
    
    def _analyze_liquidity(self, ticker: str, alpaca) -> Optional[OptionsLiquidity]:
        """
        Analyze options liquidity with HARD GATES.
        
        No hedge fund fantasy survives bad fills.
        These gates are non-negotiable - fail any = drop ticker.
        """
        try:
            # Get underlying quote for spread calculation
            quote = alpaca.get_latest_quote(ticker)
            if not quote:
                return None
            
            bid = float(quote.bid_price if hasattr(quote, 'bid_price') else quote.get("bid_price", 0))
            ask = float(quote.ask_price if hasattr(quote, 'ask_price') else quote.get("ask_price", 0))
            mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
            
            underlying_spread_pct = ((ask - bid) / mid * 100) if mid > 0 else 100
            
            # For options liquidity, we'd ideally check ATM options
            # Using conservative estimates based on underlying liquidity
            # Real implementation would query options chain
            
            # Estimate option OI/volume from underlying trading characteristics
            # Highly liquid underlyings tend to have better option liquidity
            bars = alpaca.get_bars(ticker, timeframe="1Day", limit=5)
            avg_volume = 0
            if bars:
                volumes = [float(b.volume if hasattr(b, 'volume') else (b.v if hasattr(b, 'v') else b.get("v", 0))) for b in bars]
                avg_volume = sum(volumes) / len(volumes) if volumes else 0
            
            # Proxy estimates based on underlying volume
            # SPY/QQQ have ~1M+ option volume, small stocks have <100
            if avg_volume > 50_000_000:  # Mega liquid (SPY, QQQ)
                option_oi = 500_000
                option_volume = 100_000
                option_spread_pct = 0.5
            elif avg_volume > 10_000_000:  # Very liquid (AAPL, MSFT)
                option_oi = 100_000
                option_volume = 20_000
                option_spread_pct = 1.0
            elif avg_volume > 1_000_000:  # Liquid
                option_oi = 10_000
                option_volume = 2_000
                option_spread_pct = 2.0
            elif avg_volume > 500_000:  # Moderate
                option_oi = 2_000
                option_volume = 500
                option_spread_pct = 3.0
            else:  # Illiquid
                option_oi = 200
                option_volume = 50
                option_spread_pct = 8.0
            
            # Hard gates - these are non-negotiable
            passes_spread_gate = underlying_spread_pct < 0.1  # < 0.1% spread
            passes_oi_gate = option_oi > 500
            passes_volume_gate = option_volume > 100
            passes_option_spread_gate = option_spread_pct < 5.0
            
            # Composite liquidity score
            spread_score = max(0, (0.1 - underlying_spread_pct) / 0.1 * 25)
            oi_score = min(25, math.log10(max(1, option_oi)) * 5)
            vol_score = min(25, math.log10(max(1, option_volume)) * 6)
            opt_spread_score = max(0, (5 - option_spread_pct) / 5 * 25)
            liquidity_score = spread_score + oi_score + vol_score + opt_spread_score
            
            return OptionsLiquidity(
                ticker=ticker,
                underlying_spread_pct=underlying_spread_pct,
                option_oi=option_oi,
                option_volume=option_volume,
                option_spread_pct=option_spread_pct,
                liquidity_score=liquidity_score,
                passes_spread_gate=passes_spread_gate,
                passes_oi_gate=passes_oi_gate,
                passes_volume_gate=passes_volume_gate,
                passes_option_spread_gate=passes_option_spread_gate
            )
            
        except Exception as e:
            self._logger.warn(f"Liquidity analysis failed for {ticker}: {e}")
            return None
    
    def _analyze_catalyst(self, ticker: str) -> Optional[CatalystRisk]:
        """
        Analyze catalyst/event risk.
        
        Uses yfinance for earnings calendar and other events.
        Protected by timeout to prevent blocking the pre-market run.
        """
        # Skip ETFs - they don't have fundamentals/earnings data
        if ticker.upper() in ETF_SYMBOLS:
            return CatalystRisk(
                ticker=ticker,
                earnings_flag=False,
                days_to_earnings=None,
                news_flag=False,
                fda_flag=False,
                macro_flag=False
            )
        
        import concurrent.futures
        
        def fetch_catalyst_data():
            """Isolated function for timeout protection."""
            import yfinance as yf
            stock = yf.Ticker(ticker)
            return stock.calendar, stock.info
        
        try:
            # Timeout protection: 3 seconds max for yfinance call
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(fetch_catalyst_data)
                try:
                    calendar, info = future.result(timeout=3.0)
                except concurrent.futures.TimeoutError:
                    self._logger.warn(f"Catalyst analysis timed out for {ticker}")
                    return CatalystRisk(
                        ticker=ticker,
                        earnings_flag=False,
                        days_to_earnings=None,
                        news_flag=False,
                        fda_flag=False,
                        macro_flag=False
                    )
            
            earnings_flag = False
            days_to_earnings = None
            news_flag = False
            fda_flag = False
            macro_flag = False
            
            # Check for upcoming earnings
            if calendar is not None and not calendar.empty if hasattr(calendar, 'empty') else calendar:
                try:
                    if hasattr(calendar, 'get'):
                        earnings_date = calendar.get('Earnings Date')
                    else:
                        earnings_date = calendar.loc['Earnings Date'] if 'Earnings Date' in calendar.index else None
                    
                    if earnings_date is not None:
                        from datetime import date
                        today = date.today()
                        
                        # Handle different formats
                        if hasattr(earnings_date, 'date'):
                            ed = earnings_date.date()
                        elif isinstance(earnings_date, str):
                            from datetime import datetime
                            ed = datetime.strptime(earnings_date[:10], "%Y-%m-%d").date()
                        else:
                            ed = None
                        
                        if ed:
                            days_to_earnings = (ed - today).days
                            if 0 <= days_to_earnings <= 14:  # Within 2 weeks
                                earnings_flag = True
                except Exception:
                    pass
            
            # Check for FDA risk (biotech/pharma)
            # info was fetched in the timeout-protected call above
            try:
                if info:
                    sector = info.get('sector', '')
                    industry = info.get('industry', '')
                    if 'Biotech' in industry or 'Pharma' in industry:
                        # Simplified: flag biotech/pharma as having potential FDA risk
                        fda_flag = False  # Would need to check actual FDA calendar
            except Exception:
                pass
            
            return CatalystRisk(
                ticker=ticker,
                earnings_flag=earnings_flag,
                days_to_earnings=days_to_earnings,
                news_flag=news_flag,
                fda_flag=fda_flag,
                macro_flag=macro_flag
            )
            
        except Exception as e:
            self._logger.warn(f"Catalyst analysis failed for {ticker}: {e}")
            return CatalystRisk(
                ticker=ticker,
                earnings_flag=False,
                days_to_earnings=None,
                news_flag=False,
                fda_flag=False,
                macro_flag=False
            )
    
    def _detect_events(self, ticker: str) -> List[EventType]:
        """Detect overnight events (earnings, news, etc.)."""
        events = []
        
        try:
            # Check earnings calendar (using yfinance)
            import yfinance as yf
            stock = yf.Ticker(ticker)
            
            # Check for earnings within next 7 days
            try:
                calendar = stock.calendar
                # Calendar can be dict or DataFrame depending on yfinance version
                if calendar is not None:
                    if hasattr(calendar, 'empty'):
                        has_events = not calendar.empty
                    elif isinstance(calendar, dict):
                        has_events = len(calendar) > 0
                    else:
                        has_events = bool(calendar)
                    
                    if has_events:
                        events.append(EventType.EARNINGS)
            except:
                pass
            
            # Check for dividends
            try:
                divs = stock.dividends
                if len(divs) > 0:
                    last_div_idx = divs.index[-1]
                    # Handle different index types
                    if hasattr(last_div_idx, 'to_pydatetime'):
                        last_div_date = last_div_idx.to_pydatetime()
                    else:
                        import pandas as pd
                        last_div_date = pd.Timestamp(last_div_idx).to_pydatetime()
                    
                    if (self._clock.now().replace(tzinfo=None) - last_div_date).days < 7:
                        events.append(EventType.DIVIDEND)
            except:
                pass
            
        except Exception as e:
            self._logger.warn(f"Event detection failed for {ticker}: {e}")
        
        if not events:
            events.append(EventType.NONE)
        
        return events
    
    def _calculate_opportunity_score(
        self,
        gap: Optional[GapAnalysis],
        iv: Optional[IVAnalysis],
        events: List[EventType]
    ) -> float:
        """Calculate composite opportunity score (0-100)."""
        score = 0.0
        
        # Gap component (40%)
        if gap:
            # Higher score for larger gaps (up to 2%)
            gap_score = min(100, abs(gap.gap_pct) * 50)
            # Bonus for volume surge
            if gap.volume_surge_pct > 50:
                gap_score = min(100, gap_score * 1.2)
            score += gap_score * (self.WEIGHT_GAP / 100)
        
        # IV component (25%)
        if iv:
            # Elevated IV = opportunity for selling premium
            # Depressed IV = opportunity for buying premium
            if iv.is_elevated:
                iv_score = 80 + (iv.iv_percentile - 60) * 0.5
            elif iv.is_depressed:
                iv_score = 70 + (30 - iv.iv_percentile) * 0.5
            else:
                iv_score = 40  # Neutral IV
            score += iv_score * (self.WEIGHT_IV / 100)
        
        # Volume component (20%)
        if gap and gap.volume_surge_pct > 0:
            volume_score = min(100, 50 + gap.volume_surge_pct * 0.5)
            score += volume_score * (self.WEIGHT_VOLUME / 100)
        
        # Event component (15%)
        event_score = 0
        if EventType.EARNINGS in events:
            event_score = 90  # High opportunity around earnings
        elif EventType.NEWS in events:
            event_score = 70
        elif EventType.FDA in events:
            event_score = 85
        else:
            event_score = 30  # No event = moderate baseline
        score += event_score * (self.WEIGHT_EVENT / 100)
        
        return min(100, max(0, score))
    
    def _calculate_liquidity_score(self, ticker: str, alpaca) -> float:
        """Calculate liquidity score based on volume, spread, and OI."""
        try:
            # Get recent volume
            bars = alpaca.get_bars(ticker, timeframe="1Day", limit=20)
            if not bars:
                return 50  # Default
            
            volumes = [float(b.v) if hasattr(b, 'v') else float(b["v"]) for b in bars]
            avg_volume = sum(volumes) / len(volumes) if volumes else 0
            
            # Score based on average daily volume
            if avg_volume > 10_000_000:
                vol_score = 100
            elif avg_volume > 5_000_000:
                vol_score = 80
            elif avg_volume > 1_000_000:
                vol_score = 60
            else:
                vol_score = 40
            
            # Get current spread
            quote = alpaca.get_latest_quote(ticker)
            if quote:
                bid = float(quote.bid_price if hasattr(quote, 'bid_price') else quote.get("bid_price", 0))
                ask = float(quote.ask_price if hasattr(quote, 'ask_price') else quote.get("ask_price", 0))
                mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 1
                spread_pct = ((ask - bid) / mid) * 100 if mid > 0 else 1
                
                if spread_pct < 0.02:
                    spread_score = 100
                elif spread_pct < 0.05:
                    spread_score = 80
                elif spread_pct < 0.1:
                    spread_score = 60
                else:
                    spread_score = 40
            else:
                spread_score = 50
            
            return (vol_score * 0.6) + (spread_score * 0.4)
            
        except Exception as e:
            self._logger.warn(f"Liquidity score failed for {ticker}: {e}")
            return 50
    
    def _recommend_strategies(
        self,
        gap: Optional[GapAnalysis],
        iv: Optional[IVAnalysis],
        events: List[EventType]
    ) -> List[str]:
        """Recommend options strategies based on analysis."""
        strategies = []
        
        # High IV = sell premium
        if iv and iv.is_elevated:
            strategies.append("iron_condor")
            strategies.append("credit_spread")
            if gap and gap.is_significant:
                # Directional + high IV
                if gap.direction == "up":
                    strategies.append("bear_call_spread")
                else:
                    strategies.append("bull_put_spread")
        
        # Low IV = buy premium
        elif iv and iv.is_depressed:
            if EventType.EARNINGS in events:
                strategies.append("straddle")
                strategies.append("strangle")
            elif gap and gap.is_significant:
                if gap.direction == "up":
                    strategies.append("long_call")
                else:
                    strategies.append("long_put")
        
        # Significant gap with moderate IV
        elif gap and gap.is_significant:
            if gap.direction == "up":
                strategies.append("long_call")
                strategies.append("bull_call_spread")
            else:
                strategies.append("long_put")
                strategies.append("bear_put_spread")
        
        # Default: directional plays based on gap
        if not strategies:
            strategies.append("long_call")
            strategies.append("long_put")
        
        return strategies
    
    def _identify_risks(
        self,
        gap: Optional[GapAnalysis],
        iv: Optional[IVAnalysis],
        events: List[EventType]
    ) -> List[str]:
        """Identify risk flags for the ticker."""
        risks = []
        
        # Earnings risk
        if EventType.EARNINGS in events:
            risks.append("earnings_event")
        
        # High volatility
        if iv and iv.current_iv > 50:
            risks.append("high_volatility")
        
        # Large gap (potential for reversal)
        if gap and abs(gap.gap_pct) > 3:
            risks.append("extreme_gap")
        
        # Low volume
        if gap and gap.premarket_volume < 10000:
            risks.append("low_premarket_volume")
        
        return risks
    
    def _rank_opportunities(self) -> List[str]:
        """Rank all analyzed tickers by opportunity score."""
        if not self._cache:
            return []
        
        # Sort by opportunity score descending
        ranked = sorted(
            self._cache.tickers.items(),
            key=lambda x: x[1].opportunity_score,
            reverse=True
        )
        
        return [ticker for ticker, _ in ranked]
    
    def select_universe(self) -> List[str]:
        """
        Select the top N symbols for today's trading universe.
        
        This is the BINDING step that locks the universe via UniverseGuard.
        Only symbols in the selected list can be traded.
        
        Returns:
            List of selected ticker symbols
        """
        from ..risk.universe_guard import get_universe_guard
        from ..core.config import get_run_id
        
        settings = load_settings()
        premarket_config = settings.get("premarket", {})
        top_n = premarket_config.get("top_n", 5)
        min_score = premarket_config.get("min_score", 0.35)
        
        if not self._cache or not self._cache.ranked_opportunities:
            self._logger.warn("select_universe called with no ranked opportunities")
            return []
        
        # Filter by minimum score and take top N
        selected = []
        ranked_with_scores = []
        
        for ticker in self._cache.ranked_opportunities:
            intel = self._cache.tickers.get(ticker)
            if intel:
                # Normalize score to 0-1 range (assuming score is 0-100)
                normalized_score = intel.opportunity_score / 100.0
                ranked_with_scores.append({
                    "ticker": ticker,
                    "score": intel.opportunity_score,
                    "normalized_score": normalized_score,
                    "gap_pct": intel.gap.gap_pct if intel.gap else 0,
                    "iv_pct": intel.iv.iv_percentile if intel.iv else 0,
                    "eligible_twentymin": intel.eligible_for_twentymin,
                    "eligible_options": intel.eligible_for_options
                })
                
                if normalized_score >= min_score and len(selected) < top_n:
                    selected.append(ticker)
        
        # Bind to UniverseGuard
        run_id = get_run_id()
        guard = get_universe_guard()
        guard.set_selected_symbols(selected, run_id)
        
        # Write JSON output file
        self._write_selection_json(selected, ranked_with_scores, run_id)
        
        self._logger.log("premarket_selection_complete", {
            "selected": selected,
            "selected_count": len(selected),
            "top_n_config": top_n,
            "min_score_config": min_score,
            "total_ranked": len(self._cache.ranked_opportunities),
            "run_id": run_id
        })
        
        return selected
    
    def _write_selection_json(
        self, 
        selected: List[str], 
        ranked: List[Dict[str, Any]], 
        run_id: str
    ) -> None:
        """Write premarket selection to JSON file for audit trail."""
        import json
        import os
        
        settings = load_settings()
        premarket_config = settings.get("premarket", {})
        output_file = premarket_config.get("output_file", "logs/premarket_selection.json")
        
        # Ensure logs directory exists
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        
        data = {
            "generated_at": self._clock.now().isoformat(),
            "run_id": run_id,
            "session_date": self._cache.session_date if self._cache else None,
            "universe_size": len(self._cache.tickers) if self._cache else 0,
            "market_regime": self._cache.market_regime if self._cache else "unknown",
            "config": {
                "top_n": premarket_config.get("top_n", 5),
                "min_score": premarket_config.get("min_score", 0.35),
                "weights": premarket_config.get("weights", {})
            },
            "selected": selected,
            "ranked": ranked[:20],  # Top 20 for audit
            "warnings": []
        }
        
        # Add warnings
        if not selected:
            data["warnings"].append("EMPTY_SELECTION: No symbols passed min_score threshold")
        if len(selected) < premarket_config.get("top_n", 5):
            data["warnings"].append(f"PARTIAL_SELECTION: Only {len(selected)} of {premarket_config.get('top_n', 5)} selected")
        
        try:
            with open(output_file, "w") as f:
                json.dump(data, f, indent=2)
            self._logger.log("premarket_selection_json_written", {"path": output_file})
        except Exception as e:
            self._logger.error(f"Failed to write premarket selection JSON: {e}")
    
    def _get_market_regime(self) -> Dict[str, Any]:
        """Get current market regime from existing service."""
        try:
            from .market_regime import get_current_regime
            regime = get_current_regime()
            return {
                "regime": regime.regime if hasattr(regime, 'regime') else "normal",
                "multiplier": regime.position_size_multiplier if hasattr(regime, 'position_size_multiplier') else 1.0
            }
        except Exception as e:
            self._logger.warn(f"Failed to get market regime: {e}")
            return {"regime": "normal", "multiplier": 1.0}
    
    def _calculate_atr(self, ticker: str, alpaca, period: int = 14) -> float:
        """Calculate Average True Range."""
        try:
            bars = alpaca.get_bars(ticker, timeframe="1Day", limit=period + 1)
            if not bars or len(bars) < period:
                return 0
            
            trs = []
            for i in range(1, len(bars)):
                h = float(bars[i].high) if hasattr(bars[i], 'high') else float(bars[i].h if hasattr(bars[i], 'h') else bars[i]["h"])
                l = float(bars[i].low) if hasattr(bars[i], 'low') else float(bars[i].l if hasattr(bars[i], 'l') else bars[i]["l"])
                prev_c = float(bars[i-1].close) if hasattr(bars[i-1], 'close') else float(bars[i-1].c if hasattr(bars[i-1], 'c') else bars[i-1]["c"])
                
                tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
                trs.append(tr)
            
            return sum(trs) / len(trs) if trs else 0
            
        except Exception:
            return 0
    
    def _get_premarket_volume(self, ticker: str, alpaca) -> int:
        """Get pre-market volume."""
        try:
            # Try to get today's pre-market bars
            bars = alpaca.get_bars(ticker, timeframe="1Min", limit=60)
            if bars:
                return sum(int(b.volume) if hasattr(b, 'volume') else int(b.v if hasattr(b, 'v') else b["v"]) for b in bars)
        except:
            pass
        return 0
    
    def _get_avg_volume(self, ticker: str, alpaca, days: int = 20) -> float:
        """Get average daily volume."""
        try:
            bars = alpaca.get_bars(ticker, timeframe="1Day", limit=days)
            if bars:
                volumes = [float(b.volume) if hasattr(b, 'volume') else float(b.v if hasattr(b, 'v') else b["v"]) for b in bars]
                return sum(volumes) / len(volumes) if volumes else 0
        except:
            pass
        return 0
    
    def _persist_cache(self):
        """Persist cache to state storage."""
        if not self._cache:
            return
        
        data = self._serialize_cache(self._cache)
        set_state("premarket_intelligence", data)
    
    def _serialize_cache(self, cache: PreMarketCache) -> Dict[str, Any]:
        """Serialize cache for storage."""
        return {
            "session_date": cache.session_date,
            "analysis_start": cache.analysis_start.isoformat() if cache.analysis_start else None,
            "analysis_end": cache.analysis_end.isoformat() if cache.analysis_end else None,
            "tickers": {
                ticker: {
                    "opportunity_score": intel.opportunity_score,
                    "liquidity_score": intel.liquidity_score,
                    "strategies": intel.recommended_strategies,
                    "risk_flags": intel.risk_flags,
                    "gap_pct": intel.gap.gap_pct if intel.gap else None,
                    "iv_percentile": intel.iv.iv_percentile if intel.iv else None,
                    "events": [e.value for e in intel.events]
                }
                for ticker, intel in cache.tickers.items()
            },
            "ranked_opportunities": cache.ranked_opportunities,
            "market_regime": cache.market_regime,
            "regime_multiplier": cache.regime_multiplier,
            "is_complete": cache.is_complete
        }
    
    def _deserialize_cache(self, data: Dict[str, Any]) -> PreMarketCache:
        """Deserialize cache from storage."""
        # Simplified deserialization - full intelligence objects aren't restored
        # but ranked list and basic data are available
        return PreMarketCache(
            session_date=data.get("session_date", ""),
            analysis_start=datetime.fromisoformat(data["analysis_start"]) if data.get("analysis_start") else self._clock.now(),
            analysis_end=datetime.fromisoformat(data["analysis_end"]) if data.get("analysis_end") else None,
            tickers={},  # Would need full deserialization for this
            ranked_opportunities=data.get("ranked_opportunities", []),
            market_regime=data.get("market_regime", "normal"),
            regime_multiplier=data.get("regime_multiplier", 1.0),
            is_complete=data.get("is_complete", False)
        )
