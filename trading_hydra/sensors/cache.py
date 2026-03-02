"""
Market data caching for HydraSensors.

Provides TTL-based caching for:
- Quotes (bid/ask/last)
- Bars (OHLCV at various timeframes)

Rate-limit aware with configurable refresh intervals.
"""

import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

from ..core.logging import get_logger


@dataclass
class CachedQuote:
    """Cached quote data with TTL."""
    ticker: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    volume: Optional[int] = None
    timestamp: Optional[datetime] = None
    cached_at: float = field(default_factory=time.time)
    
    def is_expired(self, ttl_seconds: float) -> bool:
        """Check if cache entry is expired."""
        return (time.time() - self.cached_at) > ttl_seconds


@dataclass
class CachedBar:
    """Cached bar data."""
    ticker: str
    timeframe: str  # "1m", "5m", "1d"
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    cached_at: float = field(default_factory=time.time)


@dataclass
class CachedBars:
    """Collection of cached bars for a ticker/timeframe."""
    ticker: str
    timeframe: str
    bars: List[CachedBar] = field(default_factory=list)
    cached_at: float = field(default_factory=time.time)
    
    def is_expired(self, ttl_seconds: float) -> bool:
        """Check if cache entry is expired."""
        return (time.time() - self.cached_at) > ttl_seconds


class MarketDataCache:
    """
    TTL-based cache for market data.
    
    Thread-safe through internal locking (handled by caller in ThreadSafeState).
    """
    
    def __init__(self, quote_ttl: float = 30.0, bar_ttl: Dict[str, float] = None):
        """
        Initialize cache.
        
        Args:
            quote_ttl: Quote cache TTL in seconds
            bar_ttl: Bar cache TTL by timeframe (e.g., {"1m": 120, "5m": 600})
        """
        self.logger = get_logger()
        
        self.quote_ttl = quote_ttl
        self.bar_ttl = bar_ttl or {
            "1m": 120,
            "5m": 600,
            "1d": 3600,
        }
        
        # Cache storage
        self._quotes: Dict[str, CachedQuote] = {}
        self._bars: Dict[str, CachedBars] = {}  # key = "ticker:timeframe"
        
        # Stats
        self._quote_hits = 0
        self._quote_misses = 0
        self._bar_hits = 0
        self._bar_misses = 0
    
    # --- Quote Cache ---
    
    def get_quote(self, ticker: str) -> Optional[CachedQuote]:
        """
        Get cached quote if not expired.
        
        Returns None if not cached or expired.
        """
        quote = self._quotes.get(ticker)
        
        if quote and not quote.is_expired(self.quote_ttl):
            self._quote_hits += 1
            return quote
        
        self._quote_misses += 1
        return None
    
    def set_quote(
        self,
        ticker: str,
        bid: float = None,
        ask: float = None,
        last: float = None,
        volume: int = None,
        timestamp: datetime = None,
    ) -> None:
        """Cache a quote."""
        self._quotes[ticker] = CachedQuote(
            ticker=ticker,
            bid=bid,
            ask=ask,
            last=last,
            volume=volume,
            timestamp=timestamp or datetime.now(),
            cached_at=time.time(),
        )
    
    def get_quote_age(self, ticker: str) -> Optional[float]:
        """Get age of cached quote in seconds, or None if not cached."""
        quote = self._quotes.get(ticker)
        if quote:
            return time.time() - quote.cached_at
        return None
    
    def get_all_quotes(self) -> Dict[str, CachedQuote]:
        """Get all cached quotes (including expired)."""
        return self._quotes.copy()
    
    def get_valid_quotes(self) -> Dict[str, CachedQuote]:
        """Get only non-expired quotes."""
        return {
            ticker: quote
            for ticker, quote in self._quotes.items()
            if not quote.is_expired(self.quote_ttl)
        }
    
    # --- Bar Cache ---
    
    def _bar_key(self, ticker: str, timeframe: str) -> str:
        """Generate cache key for bars."""
        return f"{ticker}:{timeframe}"
    
    def get_bars(self, ticker: str, timeframe: str) -> Optional[List[CachedBar]]:
        """
        Get cached bars if not expired.
        
        Returns None if not cached or expired.
        """
        key = self._bar_key(ticker, timeframe)
        cached = self._bars.get(key)
        
        ttl = self.bar_ttl.get(timeframe, 600)
        
        if cached and not cached.is_expired(ttl):
            self._bar_hits += 1
            return cached.bars.copy()
        
        self._bar_misses += 1
        return None
    
    def set_bars(
        self,
        ticker: str,
        timeframe: str,
        bars: List[Dict],
    ) -> None:
        """
        Cache bars for a ticker/timeframe.
        
        Args:
            ticker: Ticker symbol
            timeframe: "1m", "5m", "1d", etc.
            bars: List of bar dicts with keys: timestamp, open, high, low, close, volume
        """
        key = self._bar_key(ticker, timeframe)
        
        cached_bars = []
        for bar in bars:
            # Handle both dict-style bars and Alpaca SDK Bar objects (which use attributes)
            if hasattr(bar, 'get'):
                # Dict-style bar
                ts = bar.get("timestamp") or bar.get("t") or datetime.now()
                o = bar.get("open") or bar.get("o", 0)
                h = bar.get("high") or bar.get("h", 0)
                l = bar.get("low") or bar.get("l", 0)
                c = bar.get("close") or bar.get("c", 0)
                v = bar.get("volume") or bar.get("v", 0)
            else:
                # Alpaca SDK Bar object (uses attributes)
                ts = getattr(bar, 'timestamp', None) or getattr(bar, 't', None) or datetime.now()
                o = float(getattr(bar, 'open', 0) or getattr(bar, 'o', 0) or 0)
                h = float(getattr(bar, 'high', 0) or getattr(bar, 'h', 0) or 0)
                l = float(getattr(bar, 'low', 0) or getattr(bar, 'l', 0) or 0)
                c = float(getattr(bar, 'close', 0) or getattr(bar, 'c', 0) or 0)
                v = int(getattr(bar, 'volume', 0) or getattr(bar, 'v', 0) or 0)
            
            cached_bars.append(CachedBar(
                ticker=ticker,
                timeframe=timeframe,
                timestamp=ts,
                open=o,
                high=h,
                low=l,
                close=c,
                volume=v,
                cached_at=time.time(),
            ))
        
        self._bars[key] = CachedBars(
            ticker=ticker,
            timeframe=timeframe,
            bars=cached_bars,
            cached_at=time.time(),
        )
    
    def get_bar_age(self, ticker: str, timeframe: str) -> Optional[float]:
        """Get age of cached bars in seconds, or None if not cached."""
        key = self._bar_key(ticker, timeframe)
        cached = self._bars.get(key)
        if cached:
            return time.time() - cached.cached_at
        return None
    
    # --- Maintenance ---
    
    def clear_expired(self) -> Dict[str, int]:
        """
        Remove expired entries from cache.
        
        Returns dict with counts of removed entries.
        """
        removed_quotes = 0
        removed_bars = 0
        
        # Clear expired quotes
        expired_tickers = [
            ticker for ticker, quote in self._quotes.items()
            if quote.is_expired(self.quote_ttl)
        ]
        for ticker in expired_tickers:
            del self._quotes[ticker]
            removed_quotes += 1
        
        # Clear expired bars
        expired_keys = []
        for key, cached in self._bars.items():
            timeframe = cached.timeframe
            ttl = self.bar_ttl.get(timeframe, 600)
            if cached.is_expired(ttl):
                expired_keys.append(key)
        
        for key in expired_keys:
            del self._bars[key]
            removed_bars += 1
        
        return {
            "quotes_removed": removed_quotes,
            "bars_removed": removed_bars,
        }
    
    def clear_all(self) -> None:
        """Clear entire cache."""
        self._quotes.clear()
        self._bars.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            "quote_count": len(self._quotes),
            "bar_count": len(self._bars),
            "quote_hits": self._quote_hits,
            "quote_misses": self._quote_misses,
            "quote_hit_rate": self._quote_hits / max(1, self._quote_hits + self._quote_misses),
            "bar_hits": self._bar_hits,
            "bar_misses": self._bar_misses,
            "bar_hit_rate": self._bar_hits / max(1, self._bar_hits + self._bar_misses),
        }
