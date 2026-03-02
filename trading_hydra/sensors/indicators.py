"""
Technical indicator calculations for HydraSensors.

Calculates:
- SMA (20, 50, 200)
- RSI (14)
- ATR (14)
- Returns (1d, 5d, 20d)
- Relative Strength vs benchmark
"""

from typing import List, Optional, Dict
from dataclasses import dataclass

from ..core.logging import get_logger


@dataclass
class IndicatorResult:
    """Container for calculated indicators."""
    ticker: str
    
    # Moving averages
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    
    # Momentum
    rsi_14: Optional[float] = None
    
    # Volatility
    atr_14: Optional[float] = None
    
    # Returns
    return_1d: Optional[float] = None
    return_5d: Optional[float] = None
    return_20d: Optional[float] = None
    
    # Relative strength
    rs_vs_spy_5d: Optional[float] = None
    rs_vs_spy_20d: Optional[float] = None
    
    # Price context
    current_price: Optional[float] = None
    price_vs_sma20: Optional[float] = None  # % above/below SMA20
    price_vs_sma50: Optional[float] = None
    price_vs_sma200: Optional[float] = None


def calculate_sma(closes: List[float], period: int) -> Optional[float]:
    """
    Calculate Simple Moving Average.
    
    Args:
        closes: List of closing prices (oldest to newest)
        period: SMA period
    
    Returns:
        SMA value or None if not enough data
    """
    if len(closes) < period:
        return None
    
    return sum(closes[-period:]) / period


def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """
    Calculate Relative Strength Index.
    
    Args:
        closes: List of closing prices (oldest to newest)
        period: RSI period (default 14)
    
    Returns:
        RSI value (0-100) or None if not enough data
    """
    if len(closes) < period + 1:
        return None
    
    # Calculate price changes
    changes = []
    for i in range(1, len(closes)):
        changes.append(closes[i] - closes[i-1])
    
    # Use only the last 'period' changes
    recent_changes = changes[-(period):]
    
    gains = [c for c in recent_changes if c > 0]
    losses = [-c for c in recent_changes if c < 0]
    
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0
    
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    return rsi


def calculate_atr(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14
) -> Optional[float]:
    """
    Calculate Average True Range.
    
    Args:
        highs: List of high prices
        lows: List of low prices
        closes: List of closing prices
        period: ATR period (default 14)
    
    Returns:
        ATR value or None if not enough data
    """
    if len(closes) < period + 1 or len(highs) < period + 1 or len(lows) < period + 1:
        return None
    
    # Calculate True Range for each period
    true_ranges = []
    for i in range(1, len(closes)):
        high = highs[i]
        low = lows[i]
        prev_close = closes[i-1]
        
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        true_ranges.append(tr)
    
    # Average of last 'period' true ranges
    recent_tr = true_ranges[-(period):]
    return sum(recent_tr) / len(recent_tr)


def calculate_returns(closes: List[float], periods: List[int] = None) -> Dict[int, Optional[float]]:
    """
    Calculate returns over multiple periods.
    
    Args:
        closes: List of closing prices (oldest to newest)
        periods: List of periods (default [1, 5, 20])
    
    Returns:
        Dict mapping period -> return (as decimal, e.g., 0.05 = 5%)
    """
    if periods is None:
        periods = [1, 5, 20]
    
    results = {}
    
    for period in periods:
        if len(closes) < period + 1:
            results[period] = None
        else:
            current = closes[-1]
            past = closes[-(period + 1)]
            if past > 0:
                results[period] = (current - past) / past
            else:
                results[period] = None
    
    return results


def calculate_relative_strength(
    ticker_closes: List[float],
    benchmark_closes: List[float],
    periods: List[int] = None
) -> Dict[int, Optional[float]]:
    """
    Calculate relative strength vs benchmark.
    
    RS = ticker_return - benchmark_return
    Positive = outperforming, Negative = underperforming
    
    Args:
        ticker_closes: Ticker closing prices
        benchmark_closes: Benchmark (e.g., SPY) closing prices
        periods: Periods for calculation
    
    Returns:
        Dict mapping period -> relative strength
    """
    if periods is None:
        periods = [5, 20]
    
    ticker_returns = calculate_returns(ticker_closes, periods)
    benchmark_returns = calculate_returns(benchmark_closes, periods)
    
    results = {}
    for period in periods:
        tr = ticker_returns.get(period)
        br = benchmark_returns.get(period)
        
        if tr is not None and br is not None:
            results[period] = tr - br
        else:
            results[period] = None
    
    return results


class IndicatorCalculator:
    """
    Calculates technical indicators for a ticker.
    
    Requires bar data (OHLCV) as input.
    """
    
    def __init__(self):
        self.logger = get_logger()
    
    def calculate_all(
        self,
        ticker: str,
        bars: List[Dict],
        benchmark_bars: List[Dict] = None,
    ) -> IndicatorResult:
        """
        Calculate all indicators for a ticker.
        
        Args:
            ticker: Ticker symbol
            bars: List of bar dicts with keys: open, high, low, close, volume
            benchmark_bars: Optional benchmark bars for relative strength
        
        Returns:
            IndicatorResult with all calculated values
        """
        result = IndicatorResult(ticker=ticker)
        
        if not bars:
            return result
        
        # Extract price series
        closes = [b.get("close") or b.get("c", 0) for b in bars]
        highs = [b.get("high") or b.get("h", 0) for b in bars]
        lows = [b.get("low") or b.get("l", 0) for b in bars]
        
        # Filter out zeros/None
        closes = [c for c in closes if c and c > 0]
        highs = [h for h in highs if h and h > 0]
        lows = [l for l in lows if l and l > 0]
        
        if not closes:
            return result
        
        # Current price
        result.current_price = closes[-1]
        
        # SMAs
        result.sma_20 = calculate_sma(closes, 20)
        result.sma_50 = calculate_sma(closes, 50)
        result.sma_200 = calculate_sma(closes, 200)
        
        # Price vs SMA
        if result.sma_20 and result.sma_20 > 0:
            result.price_vs_sma20 = (result.current_price - result.sma_20) / result.sma_20
        if result.sma_50 and result.sma_50 > 0:
            result.price_vs_sma50 = (result.current_price - result.sma_50) / result.sma_50
        if result.sma_200 and result.sma_200 > 0:
            result.price_vs_sma200 = (result.current_price - result.sma_200) / result.sma_200
        
        # RSI
        result.rsi_14 = calculate_rsi(closes, 14)
        
        # ATR
        if len(highs) == len(closes) and len(lows) == len(closes):
            result.atr_14 = calculate_atr(highs, lows, closes, 14)
        
        # Returns
        returns = calculate_returns(closes, [1, 5, 20])
        result.return_1d = returns.get(1)
        result.return_5d = returns.get(5)
        result.return_20d = returns.get(20)
        
        # Relative strength
        if benchmark_bars:
            benchmark_closes = [b.get("close") or b.get("c", 0) for b in benchmark_bars]
            benchmark_closes = [c for c in benchmark_closes if c and c > 0]
            
            if benchmark_closes:
                rs = calculate_relative_strength(closes, benchmark_closes, [5, 20])
                result.rs_vs_spy_5d = rs.get(5)
                result.rs_vs_spy_20d = rs.get(20)
        
        return result
