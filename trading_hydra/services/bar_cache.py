"""
Bar Cache Service - Stores historical bar data in SQLite for instant startup.

Purpose:
- Cache bar data during premarket so bots can start instantly after restart
- Avoid API rate limits and network latency during critical first 20 minutes
- Support incremental updates during trading session

Usage:
1. Call warm_cache() during premarket (before 06:30 PST) to prefetch all tickers
2. Call get_cached_bars() to retrieve bars instantly without API calls
3. Cache auto-expires after market close to ensure fresh data next day
"""
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..core.logging import get_logger
from ..core.state import _get_connection, init_state_store


@dataclass
class CachedBar:
    """Cached bar data structure"""
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: Optional[float] = None


_cache_lock = threading.Lock()
_initialized = False


def _ensure_cache_table() -> None:
    """Create the bar cache table if it doesn't exist"""
    global _initialized
    if _initialized:
        return
        
    with _cache_lock:
        if _initialized:
            return
            
        conn = _get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bar_cache (
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                cache_date TEXT NOT NULL,
                bars_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (symbol, timeframe, cache_date)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_bar_cache_date 
            ON bar_cache(cache_date)
        """)
        conn.commit()
        _initialized = True


def get_cached_bars(
    symbol: str, 
    timeframe: str = "1Min",
    limit: int = 20,
    cache_date: Optional[str] = None
) -> Optional[List[CachedBar]]:
    """
    Get cached bar data for a symbol.
    
    Args:
        symbol: Ticker symbol
        timeframe: Bar timeframe (1Min, 5Min, 1Day)
        limit: Number of most recent bars to return
        cache_date: Date key (defaults to today)
        
    Returns:
        List of CachedBar objects or None if not cached
    """
    _ensure_cache_table()
    
    if cache_date is None:
        cache_date = datetime.utcnow().strftime("%Y-%m-%d")
    
    try:
        conn = _get_connection()
        cursor = conn.execute("""
            SELECT bars_json FROM bar_cache 
            WHERE symbol = ? AND timeframe = ? AND cache_date = ?
        """, (symbol, timeframe, cache_date))
        
        row = cursor.fetchone()
        if row:
            bars_data = json.loads(row["bars_json"])
            bars = [CachedBar(**b) for b in bars_data]
            return bars[-limit:] if len(bars) > limit else bars
        return None
        
    except Exception as e:
        get_logger().error(f"Bar cache get error: {e}", symbol=symbol)
        return None


def set_cached_bars(
    symbol: str,
    bars: List[Any],
    timeframe: str = "1Min",
    cache_date: Optional[str] = None
) -> bool:
    """
    Cache bar data for a symbol.
    
    Args:
        symbol: Ticker symbol
        bars: List of bar objects (Alpaca bars or CachedBar)
        timeframe: Bar timeframe
        cache_date: Date key (defaults to today)
        
    Returns:
        True if cached successfully
    """
    _ensure_cache_table()
    
    if cache_date is None:
        cache_date = datetime.utcnow().strftime("%Y-%m-%d")
    
    try:
        bars_data = []
        for b in bars:
            if isinstance(b, CachedBar):
                bars_data.append(asdict(b))
            elif hasattr(b, 'open'):
                bars_data.append({
                    "timestamp": str(getattr(b, 'timestamp', '')),
                    "open": float(b.open),
                    "high": float(b.high),
                    "low": float(b.low),
                    "close": float(b.close),
                    "volume": float(b.volume),
                    "vwap": float(b.vwap) if hasattr(b, 'vwap') and b.vwap else None
                })
            else:
                bars_data.append({
                    "timestamp": str(b.get("timestamp", "")),
                    "open": float(b.get("open", b.get("o", 0))),
                    "high": float(b.get("high", b.get("h", 0))),
                    "low": float(b.get("low", b.get("l", 0))),
                    "close": float(b.get("close", b.get("c", 0))),
                    "volume": float(b.get("volume", b.get("v", 0))),
                    "vwap": float(b.get("vwap")) if b.get("vwap") else None
                })
        
        with _cache_lock:
            conn = _get_connection()
            now = datetime.utcnow().isoformat() + "Z"
            conn.execute("""
                INSERT OR REPLACE INTO bar_cache 
                (symbol, timeframe, cache_date, bars_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
            """, (symbol, timeframe, cache_date, json.dumps(bars_data), now))
            conn.commit()
        return True
        
    except Exception as e:
        get_logger().error(f"Bar cache set error: {e}", symbol=symbol)
        return False


def clear_old_cache(days_to_keep: int = 3) -> int:
    """
    Clear cached bars older than specified days.
    
    Args:
        days_to_keep: Number of days of cache to keep
        
    Returns:
        Number of rows deleted
    """
    _ensure_cache_table()
    
    try:
        cutoff = (datetime.utcnow() - timedelta(days=days_to_keep)).strftime("%Y-%m-%d")
        with _cache_lock:
            conn = _get_connection()
            cursor = conn.execute("""
                DELETE FROM bar_cache WHERE cache_date < ?
            """, (cutoff,))
            conn.commit()
            return cursor.rowcount
        
    except Exception as e:
        get_logger().error(f"Bar cache clear error: {e}")
        return 0


def get_cache_stats() -> Dict[str, Any]:
    """Get cache statistics"""
    _ensure_cache_table()
    
    try:
        conn = _get_connection()
        cursor = conn.execute("""
            SELECT 
                cache_date,
                COUNT(DISTINCT symbol) as symbols,
                COUNT(*) as entries
            FROM bar_cache 
            GROUP BY cache_date
            ORDER BY cache_date DESC
            LIMIT 7
        """)
        
        stats: Dict[str, Any] = {
            "by_date": [dict(row) for row in cursor.fetchall()]
        }
        
        cursor = conn.execute("SELECT COUNT(DISTINCT symbol) as total FROM bar_cache")
        row = cursor.fetchone()
        stats["total_symbols"] = int(row["total"]) if row and row["total"] else 0
        
        return stats
        
    except Exception as e:
        get_logger().error(f"Bar cache stats error: {e}")
        return {"error": str(e)}


class BarCacheWarmer:
    """
    Warms the bar cache by prefetching historical data for all configured tickers.
    Run during premarket to ensure instant startup.
    """
    
    def __init__(self, alpaca_client: Any = None, logger: Any = None):
        self._alpaca = alpaca_client
        self._logger = logger or get_logger()
        self._warm_cache_date: Optional[str] = None
    
    def warm_cache(
        self,
        tickers: List[str],
        timeframes: List[str] = ["1Min", "1Day"],
        bar_limit: int = 50,
        max_workers: int = 5
    ) -> Dict[str, Any]:
        """
        Warm the cache for all tickers in parallel.
        
        Args:
            tickers: List of ticker symbols
            timeframes: List of timeframes to cache
            bar_limit: Number of bars to cache per ticker
            max_workers: Number of parallel fetch threads
            
        Returns:
            Dictionary with warm cache results
        """
        if self._alpaca is None:
            from .alpaca_client import get_alpaca_client
            self._alpaca = get_alpaca_client()
        
        cache_date = datetime.utcnow().strftime("%Y-%m-%d")
        self._warm_cache_date = cache_date
        
        self._logger.log("bar_cache_warm_start", {
            "tickers": len(tickers),
            "timeframes": timeframes,
            "bar_limit": bar_limit,
            "cache_date": cache_date
        })
        
        results = {
            "cached": 0,
            "failed": 0,
            "skipped": 0,
            "errors": []
        }
        
        tasks = []
        for ticker in tickers:
            for timeframe in timeframes:
                tasks.append((ticker, timeframe, bar_limit, cache_date))
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._fetch_and_cache, *task): task 
                for task in tasks
            }
            
            for future in as_completed(futures):
                task = futures[future]
                try:
                    success = future.result()
                    if success:
                        results["cached"] += 1
                    else:
                        results["failed"] += 1
                except Exception as e:
                    results["failed"] += 1
                    results["errors"].append(f"{task[0]}:{task[1]}: {e}")
        
        self._logger.log("bar_cache_warm_complete", {
            "cached": results["cached"],
            "failed": results["failed"],
            "total_tasks": len(tasks)
        })
        
        clear_old_cache(days_to_keep=3)
        
        return results
    
    def _fetch_and_cache(
        self, 
        ticker: str, 
        timeframe: str, 
        limit: int,
        cache_date: str
    ) -> bool:
        """Fetch bars and cache them"""
        try:
            existing = get_cached_bars(ticker, timeframe, limit, cache_date)
            if existing and len(existing) >= limit:
                return True
            
            bars = self._alpaca.get_stock_bars(
                symbol=ticker,
                timeframe=timeframe,
                limit=limit
            )
            
            if bars:
                return set_cached_bars(ticker, bars, timeframe, cache_date)
            return False
            
        except Exception as e:
            self._logger.error(f"Cache warm error {ticker}: {e}")
            return False
    
    def is_cache_warm(self, tickers: List[str], timeframe: str = "1Min") -> bool:
        """
        Check if cache is warm for all tickers.
        
        Args:
            tickers: List of tickers to check
            timeframe: Timeframe to check
            
        Returns:
            True if all tickers have cached data
        """
        cache_date = datetime.utcnow().strftime("%Y-%m-%d")
        
        for ticker in tickers:
            cached = get_cached_bars(ticker, timeframe, 1, cache_date)
            if not cached:
                return False
        return True


_bar_cache_warmer: Optional[BarCacheWarmer] = None


def get_bar_cache_warmer() -> BarCacheWarmer:
    """Get or create the bar cache warmer singleton"""
    global _bar_cache_warmer
    if _bar_cache_warmer is None:
        _bar_cache_warmer = BarCacheWarmer()
    return _bar_cache_warmer


class BarCacheAccessor:
    """
    Simple accessor class for bar cache operations.
    Provides object-oriented interface to the module-level functions.
    """
    
    def get_cached_bars(self, symbol: str, limit: int = 100) -> Optional[List[CachedBar]]:
        """Get cached bars for a symbol"""
        return get_cached_bars(symbol, limit=limit)
    
    def set_cached_bars(self, symbol: str, bars: List[Any], timeframe: str = "1Min") -> bool:
        """Set cached bars for a symbol"""
        return set_cached_bars(symbol, bars, timeframe)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        return get_cache_stats()


_bar_cache_accessor: Optional[BarCacheAccessor] = None


def get_bar_cache() -> BarCacheAccessor:
    """Get or create the bar cache accessor singleton"""
    global _bar_cache_accessor
    if _bar_cache_accessor is None:
        _bar_cache_accessor = BarCacheAccessor()
    return _bar_cache_accessor
