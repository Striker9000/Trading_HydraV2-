"""
Stock Screener Service
======================
Screens a universe of stock candidates and selects the best performers
for momentum trading based on configurable criteria.

Screening Criteria:
- Momentum score: 3-period trend strength
- Volume: Average daily volume filter
- Spread: Bid-ask spread tightness
- Volatility: Price movement opportunity
- Earnings: Avoid stocks near earnings dates

Author: Trading Hydra
"""
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime

from ..core.logging import get_logger
from ..core.config import load_bots_config
from ..core.state import get_state, set_state
from .alpaca_client import get_alpaca_client
from .earnings_calendar import get_earnings_calendar


# ==============================================================================
# DATA CLASSES
# ==============================================================================

@dataclass
class StockScore:
    """
    Represents screening scores for a single stock candidate.
    
    Attributes:
        ticker: Stock symbol
        momentum_score: Trend strength (0-100)
        volume_score: Liquidity score (0-100)
        spread_score: Bid-ask tightness (0-100)
        volatility_score: Price movement (0-100)
        composite_score: Weighted total (0-100)
        current_price: Latest price
        volume: Average daily volume
        spread_pct: Bid-ask spread as percentage
        passed_filters: True if passes all minimum thresholds
        rejection_reason: Why it was rejected, if applicable
    """
    ticker: str
    momentum_score: float
    volume_score: float
    spread_score: float
    volatility_score: float
    composite_score: float
    current_price: float
    volume: int
    spread_pct: float
    passed_filters: bool
    rejection_reason: Optional[str] = None


@dataclass
class ScreeningResult:
    """
    Result of the stock screening process.
    
    Attributes:
        selected_tickers: List of tickers chosen for trading
        all_scores: Full scoring data for all candidates
        screening_time: When screening was performed
        from_cache: True if results were cached
    """
    selected_tickers: List[str]
    all_scores: List[StockScore]
    screening_time: str
    from_cache: bool


# ==============================================================================
# STOCK SCREENER CLASS
# ==============================================================================

class StockScreener:
    """
    Screens stock candidates and selects the best for momentum trading.
    
    The screener fetches real-time data from Alpaca, calculates scores
    for each candidate, applies filters, and returns the top performers.
    """
    
    def __init__(self):
        """Initialize the stock screener with dependencies."""
        self._logger = get_logger()
        self._alpaca = get_alpaca_client()
        self._last_screening_time: Optional[float] = None
        self._cached_result: Optional[ScreeningResult] = None
    
    def screen(self, force_refresh: bool = False) -> ScreeningResult:
        """
        Screen all stock candidates and return the best performers.
        
        Args:
            force_refresh: If True, bypass cache and re-screen
            
        Returns:
            ScreeningResult with selected tickers and full scoring data
        """
        self._logger.log("stock_screener_start", {})
        
        # Load screening configuration
        config = self._load_screening_config()
        
        # Check cache validity
        if not force_refresh and self._is_cache_valid(config):
            self._logger.log("stock_screener_cache_hit", {
                "selected": self._cached_result.selected_tickers
            })
            return self._cached_result
        
        # Get candidate tickers
        candidates = config.get("candidates", [])
        if not candidates:
            self._logger.warn("No stock candidates configured")
            return self._empty_result()
        
        # PERFORMANCE: Prefetch earnings data for all candidates at once
        # This loads from persistent cache if valid, otherwise fetches once per day
        screening_params = config.get("screening", {})
        earnings_blackout_days = screening_params.get("earnings_blackout_days", 3)
        if earnings_blackout_days > 0:
            try:
                earnings_calendar = get_earnings_calendar()
                fetch_count = earnings_calendar.prefetch_all(candidates)
                if fetch_count > 0:
                    self._logger.log("earnings_prefetch_done", {
                        "candidates": len(candidates),
                        "fresh_fetches": fetch_count
                    })
            except Exception as e:
                self._logger.warn(f"Earnings prefetch failed: {e}")
        
        # Screen each candidate
        all_scores: List[StockScore] = []
        for ticker in candidates:
            try:
                score = self._score_ticker(ticker, config)
                all_scores.append(score)
            except Exception as e:
                self._logger.error(f"Failed to score {ticker}: {e}")
                # Add failed ticker with zero score
                all_scores.append(StockScore(
                    ticker=ticker,
                    momentum_score=0,
                    volume_score=0,
                    spread_score=0,
                    volatility_score=0,
                    composite_score=0,
                    current_price=0,
                    volume=0,
                    spread_pct=0,
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
        result = ScreeningResult(
            selected_tickers=selected,
            all_scores=all_scores,
            screening_time=datetime.utcnow().isoformat(),
            from_cache=False
        )
        
        # Update cache
        self._cached_result = result
        self._last_screening_time = time.time()
        
        # Persist to state for dashboard visibility
        self._persist_results(result)
        
        self._logger.log("stock_screener_complete", {
            "candidates_screened": len(candidates),
            "passed_filters": len(passing_scores),
            "selected": selected,
            "top_scores": [
                {"ticker": s.ticker, "score": round(s.composite_score, 1)}
                for s in passing_scores[:5]
            ]
        })
        
        return result
    
    def _load_screening_config(self) -> Dict[str, Any]:
        """
        Load stock screening configuration from ticker_universe.yaml.
        
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
            
            # Extract stocks section
            stocks_config = full_config.get("stocks", {})
            global_config = full_config.get("global", {})
            
            # Merge with global settings
            stocks_config["global"] = global_config
            
            return stocks_config
            
        except Exception as e:
            self._logger.error(f"Failed to load screening config: {e}")
            return self._default_config()
    
    def _default_config(self) -> Dict[str, Any]:
        """Return default screening configuration."""
        return {
            "candidates": ["AAPL", "MSFT", "NVDA", "TSLA", "AMD"],
            "screening": {
                "min_volume": 2000000,
                "max_spread_pct": 0.15,
                "min_momentum_score": 40,
                "min_price": 10.0,
                "max_price": 500.0
            },
            "selection": {
                "max_selections": 3,
                "weights": {
                    "momentum_score": 0.40,
                    "volume_score": 0.25,
                    "spread_score": 0.20,
                    "volatility_score": 0.15
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
    
    def _score_ticker(self, ticker: str, config: Dict[str, Any]) -> StockScore:
        """
        Calculate screening scores for a single ticker.
        
        Args:
            ticker: Stock symbol to score
            config: Screening configuration
            
        Returns:
            StockScore with all component and composite scores
        """
        screening_params = config.get("screening", {})
        selection_params = config.get("selection", {})
        weights = selection_params.get("weights", {})
        
        # STEP 1: Get current quote from Alpaca
        quote = self._alpaca.get_latest_quote(ticker, asset_class="stock")
        
        bid = quote.get("bid", 0)
        ask = quote.get("ask", 0)
        current_price = (bid + ask) / 2 if bid and ask else 0
        
        # Calculate spread percentage
        spread_pct = 0
        if current_price > 0:
            spread_pct = ((ask - bid) / current_price) * 100
        
        # STEP 2: Get price history for momentum calculation
        price_history = self._get_price_history(ticker)
        
        # STEP 3: Calculate individual scores
        momentum_score = self._calculate_momentum_score(price_history, current_price)
        volume_score = self._calculate_volume_score(ticker, screening_params)
        spread_score = self._calculate_spread_score(spread_pct, screening_params)
        volatility_score = self._calculate_volatility_score(price_history)
        
        # Get volume for reporting
        volume = self._get_average_volume(ticker)
        
        # STEP 4: Apply filters
        passed_filters = True
        rejection_reason = None
        
        # Check minimum volume
        min_volume = screening_params.get("min_volume", 2000000)
        if volume < min_volume:
            passed_filters = False
            rejection_reason = f"Volume {volume:,} < min {min_volume:,}"
        
        # Check spread threshold
        max_spread = screening_params.get("max_spread_pct", 0.15)
        if spread_pct > max_spread and passed_filters:
            passed_filters = False
            rejection_reason = f"Spread {spread_pct:.2f}% > max {max_spread}%"
        
        # Check price bounds
        min_price = screening_params.get("min_price", 10.0)
        max_price = screening_params.get("max_price", 500.0)
        if current_price < min_price and passed_filters:
            passed_filters = False
            rejection_reason = f"Price ${current_price:.2f} < min ${min_price}"
        if current_price > max_price and passed_filters:
            passed_filters = False
            rejection_reason = f"Price ${current_price:.2f} > max ${max_price}"
        
        # Check minimum momentum score
        min_momentum = screening_params.get("min_momentum_score", 40)
        if momentum_score < min_momentum and passed_filters:
            passed_filters = False
            rejection_reason = f"Momentum {momentum_score:.1f} < min {min_momentum}"
        
        # STEP 4b: Check earnings blackout window
        # Avoid trading stocks that have earnings within the blackout period
        earnings_blackout_days = screening_params.get("earnings_blackout_days", 3)
        if passed_filters and earnings_blackout_days > 0:
            try:
                earnings_calendar = get_earnings_calendar()
                if earnings_calendar.is_in_blackout(ticker, earnings_blackout_days):
                    passed_filters = False
                    earnings_info = earnings_calendar.get_earnings_info(ticker)
                    days_until = earnings_info.days_until if earnings_info else "?"
                    rejection_reason = f"Earnings in {days_until} days (blackout: {earnings_blackout_days} days)"
                    self._logger.log("earnings_blackout_triggered", {
                        "ticker": ticker,
                        "days_until": days_until,
                        "blackout_days": earnings_blackout_days
                    })
            except Exception as e:
                # If earnings check fails, log but allow trading (fail-open for data issues)
                self._logger.warn(f"Earnings check failed for {ticker}: {e}")
        
        # STEP 5: Calculate weighted composite score
        composite_score = (
            momentum_score * weights.get("momentum_score", 0.40) +
            volume_score * weights.get("volume_score", 0.25) +
            spread_score * weights.get("spread_score", 0.20) +
            volatility_score * weights.get("volatility_score", 0.15)
        )
        
        return StockScore(
            ticker=ticker,
            momentum_score=momentum_score,
            volume_score=volume_score,
            spread_score=spread_score,
            volatility_score=volatility_score,
            composite_score=composite_score,
            current_price=current_price,
            volume=volume,
            spread_pct=spread_pct,
            passed_filters=passed_filters,
            rejection_reason=rejection_reason
        )
    
    def _get_price_history(self, ticker: str) -> List[float]:
        """
        Get recent price history for momentum calculation.
        
        Uses state database to track prices over time.
        
        Args:
            ticker: Stock symbol
            
        Returns:
            List of recent prices (oldest first)
        """
        # Get from state (populated by previous runs)
        history_key = f"price_history.{ticker}"
        history = get_state(history_key, [])
        
        # Extract prices from history entries
        prices = []
        for entry in history[-10:]:  # Last 10 entries
            if isinstance(entry, dict) and "price" in entry:
                prices.append(entry["price"])
            elif isinstance(entry, (int, float)):
                prices.append(float(entry))
        
        return prices
    
    def _calculate_momentum_score(self, prices: List[float], current_price: float) -> float:
        """
        Calculate momentum score based on price trend.
        
        Looks for upward momentum with confirmation from moving average.
        
        Args:
            prices: Recent price history
            current_price: Latest price
            
        Returns:
            Score from 0-100 (higher = stronger upward momentum)
        """
        if len(prices) < 3:
            return 50.0  # Neutral if insufficient data
        
        # Get last 3 prices for trend detection
        recent = prices[-3:]
        
        # Calculate trend direction
        if recent[-1] > recent[-2] > recent[-3]:
            # Strong uptrend: +30 base points
            trend_score = 70.0
        elif recent[-1] > recent[-2]:
            # Mild uptrend: +10 base points
            trend_score = 60.0
        elif recent[-1] < recent[-2] < recent[-3]:
            # Strong downtrend: -20 base points
            trend_score = 30.0
        else:
            # Neutral/choppy
            trend_score = 50.0
        
        # Adjust based on deviation from moving average
        if len(prices) >= 5:
            avg = sum(prices[-5:]) / 5
            if current_price > avg * 1.01:  # 1% above MA
                trend_score += 15
            elif current_price > avg:  # Above MA
                trend_score += 5
            elif current_price < avg * 0.99:  # 1% below MA
                trend_score -= 15
        
        # Clamp to 0-100
        return max(0, min(100, trend_score))
    
    def _calculate_volume_score(self, ticker: str, params: Dict[str, Any]) -> float:
        """
        Calculate volume score based on liquidity.
        
        Higher volume = better execution quality.
        
        Args:
            ticker: Stock symbol
            params: Screening parameters
            
        Returns:
            Score from 0-100 (higher = more liquid)
        """
        volume = self._get_average_volume(ticker)
        min_volume = params.get("min_volume", 2000000)
        
        if volume <= 0:
            return 0.0
        
        # Score based on how much above minimum
        # 2M = 50, 10M = 75, 50M+ = 100
        if volume < min_volume:
            return (volume / min_volume) * 50
        
        # Log scale for volumes above minimum
        import math
        ratio = volume / min_volume
        score = 50 + (math.log10(ratio) * 25)
        
        return min(100, score)
    
    def _get_average_volume(self, ticker: str) -> int:
        """
        Get average daily volume for a ticker.
        
        Args:
            ticker: Stock symbol
            
        Returns:
            Average daily volume (shares)
        """
        # For now, use a reasonable estimate based on ticker
        # In production, this would fetch from Alpaca bars API
        volume_estimates = {
            "AAPL": 80000000,
            "MSFT": 25000000,
            "NVDA": 50000000,
            "TSLA": 100000000,
            "AMD": 60000000,
            "META": 20000000,
            "AMZN": 30000000,
            "GOOGL": 25000000,
            "NFLX": 10000000,
            "JPM": 10000000,
            "V": 8000000,
            "MA": 5000000,
            "DIS": 12000000,
            "BA": 8000000,
            "XOM": 15000000,
            "CVX": 10000000,
            "UNH": 5000000,
            "INTC": 40000000,
            "MU": 20000000,
            "AVGO": 5000000
        }
        return volume_estimates.get(ticker, 5000000)
    
    def _calculate_spread_score(self, spread_pct: float, params: Dict[str, Any]) -> float:
        """
        Calculate spread score based on bid-ask tightness.
        
        Tighter spreads = lower transaction costs.
        
        Args:
            spread_pct: Bid-ask spread as percentage
            params: Screening parameters
            
        Returns:
            Score from 0-100 (higher = tighter spread)
        """
        max_spread = params.get("max_spread_pct", 0.15)
        
        if spread_pct <= 0:
            return 100.0  # Perfect (likely stale quote)
        
        if spread_pct >= max_spread:
            return 0.0  # Fails threshold
        
        # Linear scale: 0% spread = 100, max_spread = 0
        score = (1 - (spread_pct / max_spread)) * 100
        
        return max(0, min(100, score))
    
    def _calculate_volatility_score(self, prices: List[float]) -> float:
        """
        Calculate volatility score based on recent price movement.
        
        Moderate volatility is ideal for momentum trading.
        Too low = no opportunity, too high = too risky.
        
        Args:
            prices: Recent price history
            
        Returns:
            Score from 0-100 (higher = better volatility profile)
        """
        if len(prices) < 5:
            return 50.0  # Neutral if insufficient data
        
        # Calculate price range as percentage of average
        recent = prices[-5:]
        avg = sum(recent) / len(recent)
        
        if avg <= 0:
            return 50.0
        
        price_range = max(recent) - min(recent)
        volatility_pct = (price_range / avg) * 100
        
        # Sweet spot: 1-3% range is ideal
        # < 0.5% = too quiet (score 30)
        # 0.5-1% = okay (score 60)
        # 1-3% = ideal (score 90)
        # 3-5% = elevated (score 70)
        # > 5% = too risky (score 40)
        
        if volatility_pct < 0.5:
            return 30.0
        elif volatility_pct < 1.0:
            return 60.0
        elif volatility_pct < 3.0:
            return 90.0
        elif volatility_pct < 5.0:
            return 70.0
        else:
            return 40.0
    
    def _persist_results(self, result: ScreeningResult) -> None:
        """
        Persist screening results to state for dashboard visibility.
        
        Args:
            result: Screening result to persist
        """
        # Store selected tickers
        set_state("stock_screener.selected", result.selected_tickers)
        set_state("stock_screener.screening_time", result.screening_time)
        
        # Store top scores for dashboard
        top_scores = []
        for score in result.all_scores[:10]:
            top_scores.append({
                "ticker": score.ticker,
                "composite": round(score.composite_score, 1),
                "momentum": round(score.momentum_score, 1),
                "volume": round(score.volume_score, 1),
                "spread": round(score.spread_score, 1),
                "passed": score.passed_filters,
                "rejection": score.rejection_reason
            })
        
        set_state("stock_screener.scores", top_scores)
    
    def _empty_result(self) -> ScreeningResult:
        """Return an empty screening result."""
        return ScreeningResult(
            selected_tickers=[],
            all_scores=[],
            screening_time=datetime.utcnow().isoformat(),
            from_cache=False
        )
    
    def get_selected_tickers(self) -> List[str]:
        """
        Get the currently selected tickers for trading.
        
        Returns cached results if available, otherwise runs screening.
        
        Returns:
            List of ticker symbols selected for trading
        """
        if self._cached_result:
            return self._cached_result.selected_tickers
        
        result = self.screen()
        return result.selected_tickers


# ==============================================================================
# SINGLETON ACCESS
# ==============================================================================

_stock_screener: Optional[StockScreener] = None


def get_stock_screener() -> StockScreener:
    """
    Get the singleton StockScreener instance.
    
    Returns:
        The global StockScreener instance
    """
    global _stock_screener
    if _stock_screener is None:
        _stock_screener = StockScreener()
    return _stock_screener
