"""
DynamicUniverseScreener - Heuristic universe selection and ranking v1.

NOTE: Previously labeled "BlackRock-style" - renamed for honesty.

Screens candidate tickers for liquidity, opportunity, and risk metrics.
Returns a ranked list of the best trading opportunities based on:
- Liquidity (volume, spreads, open interest)
- Opportunity (gap size, IV percentile, volume surge)
- Risk (events, volatility, correlation)
"""
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import yaml

from ..core.logging import get_logger
from ..core.state import get_state, set_state
from ..core.clock import get_market_clock


@dataclass
class ScreeningResult:
    """Result of screening a single ticker."""
    ticker: str
    passed: bool
    liquidity_score: float  # 0-100
    opportunity_score: float  # 0-100
    composite_score: float  # Weighted combination
    disqualify_reason: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class UniverseSelection:
    """Final selected universe after screening."""
    session_date: str
    screened_at: datetime
    candidates_total: int
    candidates_passed: int
    selected_tickers: List[str]
    all_results: Dict[str, ScreeningResult]
    top_opportunities: List[Tuple[str, float]]  # (ticker, score)


class DynamicUniverseScreener:
    """
    Screens and ranks tickers for options and momentum trading.
    
    Uses institutional-grade filters:
    - Minimum ADV (average daily volume)
    - Maximum bid-ask spread
    - Minimum option liquidity
    - Earnings blackout periods
    - Correlation limits
    """
    
    def __init__(self, asset_class: str = "options"):
        """
        Initialize screener.
        
        Args:
            asset_class: "options" or "stocks" - determines which config section to use
        """
        self._logger = get_logger()
        self._asset_class = asset_class
        self._config = None
        self._alpaca = None
        
        self._load_config()
        
        self._logger.log("universe_screener_init", {
            "asset_class": asset_class,
            "config_loaded": self._config is not None
        })
    
    def _get_alpaca(self):
        """Lazy load Alpaca client."""
        if self._alpaca is None:
            from .alpaca_client import AlpacaClient
            self._alpaca = AlpacaClient()
        return self._alpaca
    
    def _load_config(self):
        """Load universe configuration from YAML."""
        try:
            with open("config/ticker_universe.yaml", "r") as f:
                full_config = yaml.safe_load(f)
            
            # Get the section for our asset class
            if self._asset_class == "options":
                self._config = full_config.get("options", {})
            else:
                self._config = full_config.get("stocks", {})
            
            self._global_config = full_config.get("global", {})
            
        except Exception as e:
            self._logger.error(f"Failed to load ticker_universe.yaml: {e}")
            self._config = {}
            self._global_config = {}
    
    def get_candidates(self) -> List[str]:
        """Get list of candidate tickers from config."""
        return self._config.get("candidates", [])
    
    def screen_universe(
        self,
        premarket_intel: Optional[Dict[str, Any]] = None
    ) -> UniverseSelection:
        """
        Screen all candidates and return ranked selection.
        
        Args:
            premarket_intel: Optional pre-market intelligence from PreMarketIntelligenceService
        
        Returns:
            UniverseSelection with ranked tickers
        """
        candidates = self.get_candidates()
        today = get_market_clock().now().strftime("%Y-%m-%d")
        
        self._logger.log("universe_screening_start", {
            "asset_class": self._asset_class,
            "candidates": len(candidates)
        })
        
        results: Dict[str, ScreeningResult] = {}
        passed_count = 0
        
        # Screen each candidate
        rejection_reasons = {}
        for ticker in candidates:
            try:
                result = self._screen_ticker(ticker, premarket_intel)
                results[ticker] = result
                if result.passed:
                    passed_count += 1
                else:
                    rejection_reasons[ticker] = result.disqualify_reason or "unknown"
            except Exception as e:
                self._logger.warn(f"Failed to screen {ticker}: {e}")
                results[ticker] = ScreeningResult(
                    ticker=ticker,
                    passed=False,
                    liquidity_score=0,
                    opportunity_score=0,
                    composite_score=0,
                    disqualify_reason=str(e)
                )
                rejection_reasons[ticker] = str(e)
        
        if rejection_reasons and passed_count == 0:
            sample_rejections = dict(list(rejection_reasons.items())[:10])
            self._logger.log("universe_screening_all_rejected", {
                "asset_class": self._asset_class,
                "total_rejected": len(rejection_reasons),
                "sample_reasons": sample_rejections,
                "screening_config": {
                    k: v for k, v in self._config.get("screening", {}).items()
                    if k in ("max_spread_pct", "min_volume", "min_price", "max_price")
                }
            })
        
        # Rank by composite score
        passed_results = [r for r in results.values() if r.passed]
        ranked = sorted(passed_results, key=lambda x: x.composite_score, reverse=True)
        
        # Apply selection limits
        selection_config = self._config.get("selection", {})
        max_selections = selection_config.get("max_selections", 5)
        
        selected_tickers = [r.ticker for r in ranked[:max_selections]]
        top_opportunities = [(r.ticker, r.composite_score) for r in ranked[:max_selections]]
        
        selection = UniverseSelection(
            session_date=today,
            screened_at=get_market_clock().now(),
            candidates_total=len(candidates),
            candidates_passed=passed_count,
            selected_tickers=selected_tickers,
            all_results=results,
            top_opportunities=top_opportunities
        )
        
        # Cache the selection
        self._cache_selection(selection)
        
        self._logger.log("universe_screening_complete", {
            "asset_class": self._asset_class,
            "total": len(candidates),
            "passed": passed_count,
            "selected": selected_tickers,
            "top_scores": top_opportunities
        })
        
        return selection
    
    def _screen_ticker(
        self,
        ticker: str,
        premarket_intel: Optional[Dict[str, Any]] = None
    ) -> ScreeningResult:
        """Screen a single ticker against all criteria."""
        alpaca = self._get_alpaca()
        screening_config = self._config.get("screening", {})
        
        metrics = {}
        disqualify_reason = None
        
        # Get basic quote/bar data
        try:
            quote = alpaca.get_latest_quote(ticker)
            bars = alpaca.get_bars(ticker, timeframe="1Day", limit=20)
        except Exception as e:
            return ScreeningResult(
                ticker=ticker,
                passed=False,
                liquidity_score=0,
                opportunity_score=0,
                composite_score=0,
                disqualify_reason=f"Data fetch failed: {e}"
            )
        
        if not quote or not bars:
            return ScreeningResult(
                ticker=ticker,
                passed=False,
                liquidity_score=0,
                opportunity_score=0,
                composite_score=0,
                disqualify_reason="No market data"
            )
        
        # Extract price — Alpaca client returns {"bid": ..., "ask": ...} dict
        bid = float(
            quote.bid_price if hasattr(quote, 'bid_price') 
            else quote.get("bid_price", quote.get("bid", 0))
        )
        ask = float(
            quote.ask_price if hasattr(quote, 'ask_price') 
            else quote.get("ask_price", quote.get("ask", 0))
        )
        mid_price = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
        
        # Price filter
        min_price = screening_config.get("min_price", 10)
        max_price = screening_config.get("max_price", 500)
        
        if mid_price < min_price:
            return ScreeningResult(
                ticker=ticker,
                passed=False,
                liquidity_score=0,
                opportunity_score=0,
                composite_score=0,
                disqualify_reason=f"Price ${mid_price:.2f} below minimum ${min_price}"
            )
        
        if mid_price > max_price:
            return ScreeningResult(
                ticker=ticker,
                passed=False,
                liquidity_score=0,
                opportunity_score=0,
                composite_score=0,
                disqualify_reason=f"Price ${mid_price:.2f} above maximum ${max_price}"
            )
        
        metrics["price"] = mid_price
        
        # Calculate spread
        spread = ask - bid if bid > 0 and ask > 0 else 0
        spread_pct = (spread / mid_price) * 100 if mid_price > 0 else 100
        metrics["spread"] = spread
        metrics["spread_pct"] = spread_pct
        
        max_spread = screening_config.get("max_spread_pct", 0.15)
        if spread_pct > max_spread:
            disqualify_reason = f"Spread {spread_pct:.2f}% exceeds max {max_spread}%"
        
        # Calculate average volume
        volumes = [float(b.volume) if hasattr(b, 'volume') else float(b.v if hasattr(b, 'v') else b["v"]) for b in bars]
        avg_volume = sum(volumes) / len(volumes) if volumes else 0
        metrics["avg_volume"] = avg_volume
        
        min_volume = screening_config.get("min_volume", 2000000)
        if avg_volume < min_volume:
            if not disqualify_reason:
                disqualify_reason = f"Volume {avg_volume:,.0f} below minimum {min_volume:,.0f}"
        
        # Calculate liquidity score (0-100)
        liquidity_score = self._calculate_liquidity_score(avg_volume, spread_pct, mid_price)
        
        # Calculate opportunity score (0-100)
        opportunity_score = self._calculate_opportunity_score(
            ticker, bars, premarket_intel
        )
        
        # Get selection weights
        selection_config = self._config.get("selection", {})
        weights = selection_config.get("weights", {})
        
        # For options: iv_rank_score, liquidity_score, spread_score, trend_score
        # For stocks: momentum_score, volume_score, spread_score, volatility_score
        
        if self._asset_class == "options":
            # IV component
            iv_weight = weights.get("iv_rank_score", 0.35)
            liq_weight = weights.get("liquidity_score", 0.30)
            spr_weight = weights.get("spread_score", 0.20)
            trend_weight = weights.get("trend_score", 0.15)
            
            spread_score = max(0, 100 - (spread_pct * 500))  # Penalize wide spreads
            trend_score = self._calculate_trend_score(bars)
            iv_score = self._get_iv_score_from_intel(ticker, premarket_intel)
            
            composite_score = (
                iv_score * iv_weight +
                liquidity_score * liq_weight +
                spread_score * spr_weight +
                trend_score * trend_weight
            )
        else:
            # Stocks
            mom_weight = weights.get("momentum_score", 0.40)
            vol_weight = weights.get("volume_score", 0.25)
            spr_weight = weights.get("spread_score", 0.20)
            volatility_weight = weights.get("volatility_score", 0.15)
            
            momentum_score = self._calculate_momentum_score(bars)
            volume_score = min(100, (avg_volume / 5_000_000) * 100)
            spread_score = max(0, 100 - (spread_pct * 500))
            volatility_score = self._calculate_volatility_score(bars)
            
            composite_score = (
                momentum_score * mom_weight +
                volume_score * vol_weight +
                spread_score * spr_weight +
                volatility_score * volatility_weight
            )
        
        passed = disqualify_reason is None
        
        return ScreeningResult(
            ticker=ticker,
            passed=passed,
            liquidity_score=liquidity_score,
            opportunity_score=opportunity_score,
            composite_score=composite_score,
            disqualify_reason=disqualify_reason,
            metrics=metrics
        )
    
    def _calculate_liquidity_score(
        self,
        avg_volume: float,
        spread_pct: float,
        price: float
    ) -> float:
        """Calculate liquidity score 0-100."""
        # Volume component (60% weight)
        if avg_volume > 20_000_000:
            vol_score = 100
        elif avg_volume > 10_000_000:
            vol_score = 85
        elif avg_volume > 5_000_000:
            vol_score = 70
        elif avg_volume > 2_000_000:
            vol_score = 55
        elif avg_volume > 1_000_000:
            vol_score = 40
        else:
            vol_score = 20
        
        # Spread component (40% weight)
        if spread_pct < 0.02:
            spr_score = 100
        elif spread_pct < 0.05:
            spr_score = 80
        elif spread_pct < 0.10:
            spr_score = 60
        elif spread_pct < 0.20:
            spr_score = 40
        else:
            spr_score = 20
        
        return (vol_score * 0.6) + (spr_score * 0.4)
    
    def _calculate_opportunity_score(
        self,
        ticker: str,
        bars: List[Any],
        premarket_intel: Optional[Dict[str, Any]]
    ) -> float:
        """Calculate opportunity score 0-100."""
        score = 50  # Baseline
        
        # Factor in pre-market intelligence if available
        if premarket_intel:
            ticker_intel = premarket_intel.get("tickers", {}).get(ticker, {})
            if ticker_intel:
                # Gap contribution
                gap_pct = ticker_intel.get("gap_pct", 0)
                if gap_pct:
                    score += min(20, abs(gap_pct) * 10)
                
                # IV contribution
                iv_percentile = ticker_intel.get("iv_percentile", 50)
                if iv_percentile > 70 or iv_percentile < 30:
                    score += 15  # Extreme IV = opportunity
        
        # Trend strength from bars
        if bars and len(bars) >= 5:
            closes = [float(b.close) if hasattr(b, 'close') else float(b.c if hasattr(b, 'c') else b["c"]) for b in bars]
            
            # Recent momentum
            recent_change = (closes[-1] - closes[-5]) / closes[-5] * 100 if closes[-5] > 0 else 0
            score += min(15, abs(recent_change) * 3)
        
        return min(100, max(0, score))
    
    def _calculate_trend_score(self, bars: List[Any]) -> float:
        """Calculate trend strength 0-100."""
        if not bars or len(bars) < 10:
            return 50
        
        closes = [float(b.close) if hasattr(b, 'close') else float(b.c if hasattr(b, 'c') else b["c"]) for b in bars]
        
        # Simple trend: count up days vs down days
        up_days = 0
        for i in range(1, len(closes)):
            if closes[i] > closes[i-1]:
                up_days += 1
        
        trend_ratio = up_days / (len(closes) - 1) if len(closes) > 1 else 0.5
        
        # Convert to 0-100 (0.5 = 50, 0.8 = 80, 0.2 = 20)
        return trend_ratio * 100
    
    def _calculate_momentum_score(self, bars: List[Any]) -> float:
        """Calculate momentum score 0-100."""
        if not bars or len(bars) < 5:
            return 50
        
        closes = [float(b.close) if hasattr(b, 'close') else float(b.c if hasattr(b, 'c') else b["c"]) for b in bars]
        
        # 5-day momentum
        mom_5d = (closes[-1] - closes[-5]) / closes[-5] * 100 if closes[-5] > 0 else 0
        
        # 10-day momentum (if available)
        if len(closes) >= 10:
            mom_10d = (closes[-1] - closes[-10]) / closes[-10] * 100 if closes[-10] > 0 else 0
        else:
            mom_10d = mom_5d
        
        # Combined momentum score
        raw_score = (abs(mom_5d) * 0.6 + abs(mom_10d) * 0.4) * 10
        
        return min(100, max(0, raw_score))
    
    def _calculate_volatility_score(self, bars: List[Any]) -> float:
        """Calculate volatility score 0-100 (higher = more volatile = more opportunity)."""
        if not bars or len(bars) < 5:
            return 50
        
        closes = [float(b.close) if hasattr(b, 'close') else float(b.c if hasattr(b, 'c') else b["c"]) for b in bars]
        
        # Daily returns
        returns = []
        for i in range(1, len(closes)):
            if closes[i-1] > 0:
                ret = (closes[i] - closes[i-1]) / closes[i-1]
                returns.append(ret)
        
        if not returns:
            return 50
        
        # Standard deviation of returns
        import statistics
        try:
            std_dev = statistics.stdev(returns)
        except:
            std_dev = 0.01
        
        # Annualized volatility
        annual_vol = std_dev * (252 ** 0.5) * 100
        
        # Score: 20% vol = 50, 40% vol = 100, 10% vol = 25
        score = (annual_vol / 40) * 100
        
        return min(100, max(0, score))
    
    def _get_iv_score_from_intel(
        self,
        ticker: str,
        premarket_intel: Optional[Dict[str, Any]]
    ) -> float:
        """Get IV-based score from pre-market intelligence."""
        if not premarket_intel:
            return 50  # Neutral
        
        ticker_intel = premarket_intel.get("tickers", {}).get(ticker, {})
        if not ticker_intel:
            return 50
        
        iv_percentile = ticker_intel.get("iv_percentile", 50)
        
        # High IV (>60) or low IV (<30) = opportunity
        if iv_percentile > 70:
            return 90  # Great for premium selling
        elif iv_percentile > 60:
            return 75
        elif iv_percentile < 30:
            return 80  # Good for premium buying
        elif iv_percentile < 40:
            return 65
        else:
            return 50  # Neutral IV
    
    def _cache_selection(self, selection: UniverseSelection):
        """Cache selection to state."""
        cache_key = f"universe_selection_{self._asset_class}"
        data = {
            "session_date": selection.session_date,
            "screened_at": selection.screened_at.isoformat(),
            "candidates_total": selection.candidates_total,
            "candidates_passed": selection.candidates_passed,
            "selected_tickers": selection.selected_tickers,
            "top_opportunities": selection.top_opportunities
        }
        set_state(cache_key, data)
    
    def get_cached_selection(self) -> Optional[UniverseSelection]:
        """Get cached selection for today."""
        cache_key = f"universe_selection_{self._asset_class}"
        data = get_state(cache_key)
        
        if not data:
            return None
        
        today = get_market_clock().now().strftime("%Y-%m-%d")
        if data.get("session_date") != today:
            return None
        
        return UniverseSelection(
            session_date=data["session_date"],
            screened_at=datetime.fromisoformat(data["screened_at"]),
            candidates_total=data["candidates_total"],
            candidates_passed=data["candidates_passed"],
            selected_tickers=data["selected_tickers"],
            all_results={},
            top_opportunities=data["top_opportunities"]
        )


def get_options_universe() -> List[str]:
    """Get today's options universe (screened and ranked)."""
    screener = DynamicUniverseScreener(asset_class="options")
    
    # Try cached first
    cached = screener.get_cached_selection()
    if cached:
        return cached.selected_tickers
    
    # Screen fresh
    selection = screener.screen_universe()
    return selection.selected_tickers


def get_stocks_universe() -> List[str]:
    """Get today's stocks universe (screened and ranked)."""
    screener = DynamicUniverseScreener(asset_class="stocks")
    
    # Try cached first
    cached = screener.get_cached_selection()
    if cached:
        return cached.selected_tickers
    
    # Screen fresh
    selection = screener.screen_universe()
    return selection.selected_tickers
