"""
Breadth sensor calculations for HydraSensors.

Monitors market breadth through spread relationships:
- RSP vs SPY (equal weight vs cap weight = breadth health)
- SMH vs SPY (semiconductors vs market = tech leadership)
- Sector ETFs vs SPY (rotation signals)
"""

from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass

from .state import BreadthReading
from .indicators import calculate_returns
from ..core.logging import get_logger


@dataclass
class BreadthConfig:
    """Configuration for a breadth pair."""
    name: str
    ticker: str
    benchmark: str
    description: str = ""
    bullish_threshold: float = 0.0  # Spread above this = bullish
    bearish_threshold: float = -0.02  # Spread below this = bearish


class BreadthCalculator:
    """
    Calculates breadth sensors based on spread relationships.
    
    Breadth sensors help determine:
    - Market health (narrow vs broad participation)
    - Sector leadership rotation
    - Risk appetite signals
    """
    
    def __init__(self, pairs: List[BreadthConfig] = None):
        self.logger = get_logger()
        
        # Default breadth pairs
        if pairs is None:
            pairs = [
                BreadthConfig(
                    name="RSP_vs_SPY",
                    ticker="RSP",
                    benchmark="SPY",
                    description="Equal weight vs cap weight - breadth health",
                    bullish_threshold=0.0,
                    bearish_threshold=-0.02,
                ),
                BreadthConfig(
                    name="SMH_vs_SPY",
                    ticker="SMH",
                    benchmark="SPY",
                    description="Semis vs market - tech leadership",
                    bullish_threshold=0.0,
                    bearish_threshold=-0.03,
                ),
                BreadthConfig(
                    name="XBI_vs_SPY",
                    ticker="XBI",
                    benchmark="SPY",
                    description="Biotech vs market - risk appetite",
                    bullish_threshold=0.0,
                    bearish_threshold=-0.03,
                ),
            ]
        
        self.pairs = {p.name: p for p in pairs}
    
    def calculate_spread(
        self,
        ticker_bars: List[Dict],
        benchmark_bars: List[Dict],
        periods: List[int] = None,
    ) -> Dict[int, Optional[float]]:
        """
        Calculate return spread between ticker and benchmark.
        
        Spread = ticker_return - benchmark_return
        Positive = ticker outperforming
        Negative = ticker underperforming
        
        Args:
            ticker_bars: Ticker OHLCV bars
            benchmark_bars: Benchmark OHLCV bars
            periods: Return periods (default [1, 5, 20])
        
        Returns:
            Dict mapping period -> spread
        """
        if periods is None:
            periods = [1, 5, 20]
        
        # Extract closes
        ticker_closes = [b.get("close") or b.get("c", 0) for b in ticker_bars]
        benchmark_closes = [b.get("close") or b.get("c", 0) for b in benchmark_bars]
        
        ticker_closes = [c for c in ticker_closes if c and c > 0]
        benchmark_closes = [c for c in benchmark_closes if c and c > 0]
        
        if not ticker_closes or not benchmark_closes:
            return {p: None for p in periods}
        
        # Calculate returns
        ticker_returns = calculate_returns(ticker_closes, periods)
        benchmark_returns = calculate_returns(benchmark_closes, periods)
        
        # Calculate spread
        spreads = {}
        for period in periods:
            tr = ticker_returns.get(period)
            br = benchmark_returns.get(period)
            
            if tr is not None and br is not None:
                spreads[period] = tr - br
            else:
                spreads[period] = None
        
        return spreads
    
    def calculate_reading(
        self,
        pair_name: str,
        ticker_bars: List[Dict],
        benchmark_bars: List[Dict],
    ) -> BreadthReading:
        """
        Calculate a breadth reading for a configured pair.
        
        Args:
            pair_name: Name of the breadth pair (e.g., "RSP_vs_SPY")
            ticker_bars: Ticker bar data
            benchmark_bars: Benchmark bar data
        
        Returns:
            BreadthReading with spread values and interpretation
        """
        config = self.pairs.get(pair_name)
        if not config:
            self.logger.error(f"Unknown breadth pair: {pair_name}")
            return BreadthReading(
                name=pair_name,
                ticker="",
                benchmark="",
                description="Unknown pair",
            )
        
        # Calculate spreads
        spreads = self.calculate_spread(ticker_bars, benchmark_bars, [1, 5, 20])
        
        # Get current prices
        ticker_price = None
        benchmark_price = None
        
        if ticker_bars:
            ticker_price = ticker_bars[-1].get("close") or ticker_bars[-1].get("c")
        if benchmark_bars:
            benchmark_price = benchmark_bars[-1].get("close") or benchmark_bars[-1].get("c")
        
        # Determine bullish/bearish
        spread_5d = spreads.get(5)
        bullish = None
        if spread_5d is not None:
            if spread_5d > config.bullish_threshold:
                bullish = True
            elif spread_5d < config.bearish_threshold:
                bullish = False
        
        return BreadthReading(
            name=config.name,
            ticker=config.ticker,
            benchmark=config.benchmark,
            spread_1d=spreads.get(1),
            spread_5d=spreads.get(5),
            spread_20d=spreads.get(20),
            ticker_price=ticker_price,
            benchmark_price=benchmark_price,
            bullish=bullish,
            description=config.description,
            last_update=datetime.now(),
        )
    
    def get_pairs(self) -> List[str]:
        """Get list of configured pair names."""
        return list(self.pairs.keys())
    
    def get_tickers_needed(self) -> List[str]:
        """Get list of all tickers needed for breadth calculation."""
        tickers = set()
        for config in self.pairs.values():
            tickers.add(config.ticker)
            tickers.add(config.benchmark)
        return list(tickers)
    
    def interpret_breadth(self, readings: Dict[str, BreadthReading]) -> Dict[str, any]:
        """
        Interpret overall market breadth from readings.
        
        Returns:
            Dict with overall assessment
        """
        bullish_count = 0
        bearish_count = 0
        neutral_count = 0
        
        for reading in readings.values():
            if reading.bullish is True:
                bullish_count += 1
            elif reading.bullish is False:
                bearish_count += 1
            else:
                neutral_count += 1
        
        total = bullish_count + bearish_count + neutral_count
        
        if total == 0:
            overall = "unknown"
        elif bullish_count > bearish_count:
            overall = "bullish"
        elif bearish_count > bullish_count:
            overall = "bearish"
        else:
            overall = "neutral"
        
        return {
            "overall": overall,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "neutral_count": neutral_count,
            "total_pairs": total,
            "bullish_pct": bullish_count / max(1, total),
        }
