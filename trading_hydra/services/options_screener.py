"""
Options Screener Service
========================
Screens a universe of underlying candidates and selects the best performers
for options trading based on IV rank, liquidity, and spread analysis.

Screening Criteria:
- IV Rank: Premium opportunity (sweet spot 30-70)
- Option Liquidity: Volume on ATM options
- Spread Width: Bid-ask on options chains
- Trend: Underlying price direction for strategy selection

Author: Trading Hydra
"""
import time
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

from ..core.logging import get_logger
from ..core.state import get_state, set_state
from .alpaca_client import get_alpaca_client


# ==============================================================================
# DATA CLASSES
# ==============================================================================

@dataclass
class OptionsScore:
    """
    Represents screening scores for a single options underlying.
    
    Attributes:
        ticker: Underlying symbol
        iv_rank_score: Premium opportunity (0-100)
        liquidity_score: Option volume quality (0-100)
        spread_score: Option spread tightness (0-100)
        trend_score: Directional bias score (0-100)
        composite_score: Weighted total (0-100)
        current_price: Underlying price
        iv_rank: Actual IV rank value (0-100)
        option_volume: Average daily option volume
        recommended_strategy: Suggested strategy based on conditions
        passed_filters: True if passes all thresholds
        rejection_reason: Why it was rejected, if applicable
    """
    ticker: str
    iv_rank_score: float
    liquidity_score: float
    spread_score: float
    trend_score: float
    composite_score: float
    current_price: float
    iv_rank: float
    option_volume: int
    recommended_strategy: str
    passed_filters: bool
    rejection_reason: Optional[str] = None


@dataclass
class OptionsScreeningResult:
    """
    Result of the options screening process.
    
    Attributes:
        selected_underlyings: List of underlyings chosen for trading
        all_scores: Full scoring data for all candidates
        screening_time: When screening was performed
        from_cache: True if results were cached
    """
    selected_underlyings: List[str]
    all_scores: List[OptionsScore]
    screening_time: str
    from_cache: bool


# ==============================================================================
# OPTIONS SCREENER CLASS
# ==============================================================================

class OptionsScreener:
    """
    Screens options underlyings and selects the best for credit spreads.
    
    The screener analyzes IV rank, option liquidity, spread width,
    and underlying trend to identify optimal trading candidates.
    """
    
    def __init__(self):
        """Initialize the options screener with dependencies."""
        self._logger = get_logger()
        self._alpaca = get_alpaca_client()
        self._last_screening_time: Optional[float] = None
        self._cached_result: Optional[OptionsScreeningResult] = None
    
    def screen(self, force_refresh: bool = False) -> OptionsScreeningResult:
        """
        Screen all options candidates and return the best performers.
        
        Args:
            force_refresh: If True, bypass cache and re-screen
            
        Returns:
            OptionsScreeningResult with selected underlyings and scoring data
        """
        self._logger.log("options_screener_start", {})
        
        # Load screening configuration
        config = self._load_screening_config()
        
        # Check cache validity
        if not force_refresh and self._is_cache_valid(config):
            self._logger.log("options_screener_cache_hit", {
                "selected": self._cached_result.selected_underlyings
            })
            return self._cached_result
        
        # Get candidate underlyings
        candidates = config.get("candidates", [])
        if not candidates:
            self._logger.warn("No options candidates configured")
            return self._empty_result()
        
        # Screen each candidate
        all_scores: List[OptionsScore] = []
        for ticker in candidates:
            try:
                score = self._score_underlying(ticker, config)
                all_scores.append(score)
            except Exception as e:
                self._logger.error(f"Failed to score options for {ticker}: {e}")
                # Add failed ticker with zero score
                all_scores.append(OptionsScore(
                    ticker=ticker,
                    iv_rank_score=0,
                    liquidity_score=0,
                    spread_score=0,
                    trend_score=0,
                    composite_score=0,
                    current_price=0,
                    iv_rank=0,
                    option_volume=0,
                    recommended_strategy="none",
                    passed_filters=False,
                    rejection_reason=f"Screening error: {e}"
                ))
        
        # Filter candidates that passed all thresholds
        passing_scores = [s for s in all_scores if s.passed_filters]
        
        # Sort by composite score (highest first)
        passing_scores.sort(key=lambda s: s.composite_score, reverse=True)
        
        # Select top N candidates
        selection_config = config.get("selection", {})
        max_selections = selection_config.get("max_selections", 3)
        selected = [s.ticker for s in passing_scores[:max_selections]]
        
        # Build result
        result = OptionsScreeningResult(
            selected_underlyings=selected,
            all_scores=all_scores,
            screening_time=datetime.utcnow().isoformat(),
            from_cache=False
        )
        
        # Update cache
        self._cached_result = result
        self._last_screening_time = time.time()
        
        # Persist to state for dashboard visibility
        self._persist_results(result)
        
        self._logger.log("options_screener_complete", {
            "candidates_screened": len(candidates),
            "passed_filters": len(passing_scores),
            "selected": selected,
            "top_scores": [
                {
                    "ticker": s.ticker, 
                    "score": round(s.composite_score, 1),
                    "iv_rank": round(s.iv_rank, 1),
                    "strategy": s.recommended_strategy
                }
                for s in passing_scores[:5]
            ]
        })
        
        return result
    
    def _load_screening_config(self) -> Dict[str, Any]:
        """
        Load options screening configuration from ticker_universe.yaml.
        
        Returns:
            Dictionary with candidates, screening params, selection params
        """
        try:
            import yaml
            import os
            
            # Find config file
            possible_paths = [
                os.path.join(os.getcwd(), "config", "ticker_universe.yaml"),
                "/home/runner/workspace/config/ticker_universe.yaml",
            ]
            
            config_path = None
            for p in possible_paths:
                if os.path.exists(p):
                    config_path = p
                    break
            
            if not config_path:
                self._logger.warn("ticker_universe.yaml not found, using defaults")
                return self._default_config()
            
            with open(config_path, "r") as f:
                full_config = yaml.safe_load(f)
            
            # Extract options section
            options_config = full_config.get("options", {})
            global_config = full_config.get("global", {})
            
            # Merge with global settings
            options_config["global"] = global_config
            
            return options_config
            
        except Exception as e:
            self._logger.error(f"Failed to load options screening config: {e}")
            return self._default_config()
    
    def _default_config(self) -> Dict[str, Any]:
        """Return default options screening configuration."""
        return {
            "candidates": ["SPY", "QQQ", "IWM"],
            "screening": {
                "min_option_volume": 10000,
                "max_option_spread": 0.10,
                "min_iv_rank": 20,
                "max_iv_rank": 80,
                "require_weeklys": True
            },
            "selection": {
                "max_selections": 3,
                "weights": {
                    "iv_rank_score": 0.35,
                    "liquidity_score": 0.30,
                    "spread_score": 0.20,
                    "trend_score": 0.15
                }
            }
        }
    
    def _is_cache_valid(self, config: Dict[str, Any]) -> bool:
        """
        Check if cached screening results are still valid.
        
        Respects the cache_results config flag - if False, never use cache.
        
        Args:
            config: Current screening configuration
            
        Returns:
            True if cache is valid and can be used
        """
        # Check if caching is disabled in config
        global_config = config.get("global", {})
        if not global_config.get("cache_results", True):
            return False  # Caching disabled
        
        if self._cached_result is None:
            return False
        
        if self._last_screening_time is None:
            return False
        
        # Get cache interval from config
        interval_minutes = global_config.get("screening_interval_minutes", 15)
        
        # Check if cache has expired
        age_seconds = time.time() - self._last_screening_time
        max_age_seconds = interval_minutes * 60
        
        return age_seconds < max_age_seconds
    
    def _score_underlying(self, ticker: str, config: Dict[str, Any]) -> OptionsScore:
        """
        Calculate screening scores for a single options underlying.
        
        Args:
            ticker: Underlying symbol to score
            config: Screening configuration
            
        Returns:
            OptionsScore with all component and composite scores
        """
        screening_params = config.get("screening", {})
        selection_params = config.get("selection", {})
        weights = selection_params.get("weights", {})
        
        # STEP 1: Get current underlying price
        quote = self._alpaca.get_latest_quote(ticker, asset_class="stock")
        
        bid = quote.get("bid", 0)
        ask = quote.get("ask", 0)
        current_price = (bid + ask) / 2 if bid and ask else 0
        
        # STEP 2: Get IV rank (simulated for now - would use options data API)
        iv_rank = self._estimate_iv_rank(ticker)
        
        # STEP 3: Get option volume (simulated - would use options data API)
        option_volume = self._get_option_volume(ticker)
        
        # STEP 4: Calculate individual scores
        iv_rank_score = self._calculate_iv_rank_score(iv_rank, screening_params)
        liquidity_score = self._calculate_liquidity_score(option_volume, screening_params)
        spread_score = self._calculate_options_spread_score(ticker)
        trend_score, trend_direction = self._calculate_trend_score(ticker)
        
        # STEP 5: Determine recommended strategy based on conditions
        recommended_strategy = self._recommend_strategy(
            iv_rank, trend_direction, current_price
        )
        
        # STEP 6: Apply filters
        passed_filters = True
        rejection_reason = None
        
        # Check IV rank bounds
        min_iv = screening_params.get("min_iv_rank", 20)
        max_iv = screening_params.get("max_iv_rank", 80)
        
        if iv_rank < min_iv:
            passed_filters = False
            rejection_reason = f"IV rank {iv_rank:.0f} < min {min_iv}"
        elif iv_rank > max_iv:
            passed_filters = False
            rejection_reason = f"IV rank {iv_rank:.0f} > max {max_iv} (earnings/event risk)"
        
        # Check minimum option volume
        min_volume = screening_params.get("min_option_volume", 10000)
        if option_volume < min_volume and passed_filters:
            passed_filters = False
            rejection_reason = f"Option volume {option_volume:,} < min {min_volume:,}"
        
        # STEP 7: Calculate weighted composite score
        composite_score = (
            iv_rank_score * weights.get("iv_rank_score", 0.35) +
            liquidity_score * weights.get("liquidity_score", 0.30) +
            spread_score * weights.get("spread_score", 0.20) +
            trend_score * weights.get("trend_score", 0.15)
        )
        
        return OptionsScore(
            ticker=ticker,
            iv_rank_score=iv_rank_score,
            liquidity_score=liquidity_score,
            spread_score=spread_score,
            trend_score=trend_score,
            composite_score=composite_score,
            current_price=current_price,
            iv_rank=iv_rank,
            option_volume=option_volume,
            recommended_strategy=recommended_strategy,
            passed_filters=passed_filters,
            rejection_reason=rejection_reason
        )
    
    def _estimate_iv_rank(self, ticker: str) -> float:
        """
        Estimate IV rank for an underlying.
        
        IV Rank measures current IV relative to its 52-week range.
        0 = at 52-week low, 100 = at 52-week high.
        
        In production, this would use options data API.
        For now, uses reasonable estimates based on ticker.
        
        Args:
            ticker: Underlying symbol
            
        Returns:
            IV rank from 0-100
        """
        # Typical IV ranks - in production would fetch real data
        iv_estimates = {
            "SPY": 35,    # Usually moderate IV
            "QQQ": 40,    # Slightly higher than SPY
            "IWM": 45,    # Small caps more volatile
            "DIA": 30,    # Blue chips, lower IV
            "XLF": 35,    # Financials moderate
            "XLE": 50,    # Energy sector volatile
            "XLK": 38,    # Tech moderate-high
            "XLV": 28,    # Healthcare defensive
            "GLD": 25,    # Gold usually low IV
            "TLT": 30,    # Bonds moderate
            "AAPL": 32,   # Mega cap, lower IV
            "MSFT": 28,   # Very stable
            "NVDA": 55,   # AI momentum, higher IV
            "TSLA": 65,   # Famous for high IV
            "AMD": 50,    # Semiconductor volatility
            "META": 45,   # Social media volatility
            "AMZN": 35,   # E-commerce stable
            "GOOGL": 30,  # Search stable
        }
        
        # Add some variance to simulate real-time changes
        import random
        base_iv = iv_estimates.get(ticker, 40)
        variance = random.uniform(-5, 5)
        
        return max(0, min(100, base_iv + variance))
    
    def _get_option_volume(self, ticker: str) -> int:
        """
        Get average daily option volume for an underlying.
        
        In production, would fetch from options data API.
        
        Args:
            ticker: Underlying symbol
            
        Returns:
            Average daily option contract volume
        """
        volume_estimates = {
            "SPY": 5000000,   # Most liquid
            "QQQ": 2000000,   # Very liquid
            "IWM": 800000,    # Liquid
            "DIA": 100000,    # Less liquid
            "XLF": 300000,
            "XLE": 200000,
            "XLK": 250000,
            "XLV": 150000,
            "GLD": 200000,
            "TLT": 300000,
            "AAPL": 1500000,  # Most liquid single stock
            "MSFT": 500000,
            "NVDA": 1000000,
            "TSLA": 2000000,  # Very active
            "AMD": 800000,
            "META": 400000,
            "AMZN": 500000,
            "GOOGL": 300000,
        }
        return volume_estimates.get(ticker, 50000)
    
    def _calculate_iv_rank_score(self, iv_rank: float, params: Dict[str, Any]) -> float:
        """
        Calculate IV rank score for premium selling.
        
        Sweet spot for credit spreads is IV rank 30-60.
        Too low = not enough premium, too high = event risk.
        
        Args:
            iv_rank: Current IV rank (0-100)
            params: Screening parameters
            
        Returns:
            Score from 0-100
        """
        # Ideal range for credit spreads: 35-55
        if 35 <= iv_rank <= 55:
            return 100.0
        elif 25 <= iv_rank < 35:
            return 80.0
        elif 55 < iv_rank <= 65:
            return 75.0
        elif 20 <= iv_rank < 25:
            return 60.0
        elif 65 < iv_rank <= 75:
            return 50.0  # Getting risky
        elif iv_rank < 20:
            return 30.0  # Too low premium
        else:
            return 25.0  # Too high (event risk)
    
    def _calculate_liquidity_score(self, volume: int, params: Dict[str, Any]) -> float:
        """
        Calculate liquidity score based on option volume.
        
        Args:
            volume: Average daily option volume
            params: Screening parameters
            
        Returns:
            Score from 0-100
        """
        min_volume = params.get("min_option_volume", 10000)
        
        if volume <= 0:
            return 0.0
        
        if volume < min_volume:
            return (volume / min_volume) * 50
        
        # Scale for high volume
        # 10K = 50, 100K = 70, 1M = 90, 5M+ = 100
        import math
        if volume >= 5000000:
            return 100.0
        elif volume >= 1000000:
            return 90.0
        elif volume >= 100000:
            return 70 + (volume - 100000) / 900000 * 20
        else:
            return 50 + (volume - min_volume) / (100000 - min_volume) * 20
    
    def _calculate_options_spread_score(self, ticker: str) -> float:
        """
        Calculate score based on typical option spread width.
        
        In production, would analyze actual option chains.
        
        Args:
            ticker: Underlying symbol
            
        Returns:
            Score from 0-100 (tighter = higher)
        """
        # ETFs have tightest spreads, followed by mega-caps
        spread_quality = {
            "SPY": 100,   # Penny-wide
            "QQQ": 95,    # Penny-wide
            "IWM": 90,
            "DIA": 80,
            "AAPL": 90,
            "MSFT": 85,
            "NVDA": 80,
            "TSLA": 85,
            "AMD": 75,
            "META": 70,
            "AMZN": 75,
            "GOOGL": 75,
            "XLF": 70,
            "XLE": 65,
            "XLK": 70,
            "XLV": 65,
            "GLD": 75,
            "TLT": 80,
        }
        return spread_quality.get(ticker, 60)
    
    def _calculate_trend_score(self, ticker: str) -> Tuple[float, str]:
        """
        Calculate trend score and direction for the underlying.
        
        Used to select appropriate option strategy.
        
        Args:
            ticker: Underlying symbol
            
        Returns:
            Tuple of (score 0-100, direction string)
        """
        # Get price history from state
        history_key = f"price_history.{ticker}"
        history = get_state(history_key, [])
        
        if len(history) < 3:
            return 50.0, "neutral"
        
        # Extract recent prices
        prices = []
        for entry in history[-5:]:
            if isinstance(entry, dict) and "price" in entry:
                prices.append(entry["price"])
            elif isinstance(entry, (int, float)):
                prices.append(float(entry))
        
        if len(prices) < 3:
            return 50.0, "neutral"
        
        # Calculate trend
        recent = prices[-3:]
        
        if recent[-1] > recent[-2] > recent[-3]:
            return 75.0, "bullish"
        elif recent[-1] > recent[-2]:
            return 60.0, "mildly_bullish"
        elif recent[-1] < recent[-2] < recent[-3]:
            return 75.0, "bearish"
        elif recent[-1] < recent[-2]:
            return 60.0, "mildly_bearish"
        else:
            return 50.0, "neutral"
    
    def _recommend_strategy(self, iv_rank: float, trend: str, price: float) -> str:
        """
        Recommend an options strategy based on conditions.
        
        Args:
            iv_rank: Current IV rank
            trend: Underlying trend direction
            price: Current underlying price
            
        Returns:
            Recommended strategy name
        """
        # High IV favors premium selling
        if iv_rank > 50:
            if trend in ["bullish", "mildly_bullish"]:
                return "bull_put_spread"
            elif trend in ["bearish", "mildly_bearish"]:
                return "bear_call_spread"
            else:
                return "iron_condor"
        
        # Lower IV - need directional view
        if trend in ["bullish", "mildly_bullish"]:
            return "bull_put_spread"
        elif trend in ["bearish", "mildly_bearish"]:
            return "bear_call_spread"
        else:
            return "iron_condor"
    
    def _persist_results(self, result: OptionsScreeningResult) -> None:
        """
        Persist screening results to state for dashboard visibility.
        
        Args:
            result: Screening result to persist
        """
        set_state("options_screener.selected", result.selected_underlyings)
        set_state("options_screener.screening_time", result.screening_time)
        
        # Store top scores for dashboard
        top_scores = []
        for score in result.all_scores[:10]:
            top_scores.append({
                "ticker": score.ticker,
                "composite": round(score.composite_score, 1),
                "iv_rank": round(score.iv_rank, 1),
                "liquidity": round(score.liquidity_score, 1),
                "strategy": score.recommended_strategy,
                "passed": score.passed_filters,
                "rejection": score.rejection_reason
            })
        
        set_state("options_screener.scores", top_scores)
    
    def _empty_result(self) -> OptionsScreeningResult:
        """Return an empty screening result."""
        return OptionsScreeningResult(
            selected_underlyings=[],
            all_scores=[],
            screening_time=datetime.utcnow().isoformat(),
            from_cache=False
        )
    
    def get_selected_underlyings(self) -> List[str]:
        """
        Get the currently selected underlyings for trading.
        
        Returns cached results if available, otherwise runs screening.
        
        Returns:
            List of underlying symbols selected for trading
        """
        if self._cached_result:
            return self._cached_result.selected_underlyings
        
        result = self.screen()
        return result.selected_underlyings
    
    def get_strategy_for_ticker(self, ticker: str) -> str:
        """
        Get the recommended strategy for a specific ticker.
        
        Args:
            ticker: Underlying symbol
            
        Returns:
            Recommended strategy name, or "none" if not found
        """
        if self._cached_result:
            for score in self._cached_result.all_scores:
                if score.ticker == ticker:
                    return score.recommended_strategy
        
        return "iron_condor"  # Default safe strategy


# ==============================================================================
# SINGLETON ACCESS
# ==============================================================================

_options_screener: Optional[OptionsScreener] = None


def get_options_screener() -> OptionsScreener:
    """
    Get the singleton OptionsScreener instance.
    
    Returns:
        The global OptionsScreener instance
    """
    global _options_screener
    if _options_screener is None:
        _options_screener = OptionsScreener()
    return _options_screener
