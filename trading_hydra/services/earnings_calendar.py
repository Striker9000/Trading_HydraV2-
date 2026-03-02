"""
Earnings Calendar Service
=========================
Fetches upcoming earnings dates using yfinance to enforce
trading blackout periods around earnings announcements.

This helps avoid holding positions through earnings volatility.
Uses yfinance - no API key required.

PERFORMANCE OPTIMIZATION:
- Persistent JSON file cache at cache/earnings_cache.json
- Cache expires once per trading day (not time-based)
- Prefetch all tickers at startup for instant lookups
- Reduces ~100 sequential API calls to 0 on subsequent loops

Author: Trading Hydra
"""
import time
import json
import os
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass

import yfinance as yf

from ..core.logging import get_logger
from ..core.state import get_state, set_state
from ..core.clock import get_market_clock


# ==============================================================================
# PERSISTENT CACHE FILE PATH
# ==============================================================================
CACHE_FILE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "cache", "earnings_cache.json"
)

# ==============================================================================
# ETF SYMBOLS (no earnings data available - skip these to avoid 404 errors)
# ==============================================================================
ETF_SYMBOLS = {
    # Major Index ETFs
    "SPY", "QQQ", "IWM", "DIA", "IVV", "VOO", "VTI", "VXUS", "QQQM", "SPYM",
    # Growth / Value ETFs
    "VUG", "VTV", "IWF", "IWD", "SCHG", "SCHV", "MGK", "RPV", "VOOG", "VOOV",
    # Sector ETFs
    "XLF", "XLE", "XLK", "XLV", "XLI", "XLP", "XLU", "XLB", "XLY", "XLRE",
    "VGT", "VHT", "VFH", "VIS", "VDE", "VNQ", "VNQI",
    # Dividend ETFs
    "SCHD", "VYM", "DGRO", "HDV", "DVY", "SDY", "SPYD", "VIG", "NOBL",
    # Bond ETFs
    "TLT", "IEF", "SHY", "BND", "AGG", "LQD", "HYG", "JNK",
    # Commodity/Currency ETFs
    "GLD", "SLV", "USO", "UNG", "FXE", "UUP", "UGA", "DBA", "DBB",
    # Volatility ETFs
    "VXX", "UVXY", "SVXY", "VIXY",
    # Leveraged ETFs
    "TQQQ", "SQQQ", "SPXL", "SPXS", "UPRO", "SDS", "QLD", "QID",
    # International ETFs
    "EFA", "EEM", "VEA", "VWO", "IEFA", "IEMG",
    # Thematic / Industry ETFs
    "GDX", "GDXJ", "KRE", "XAR", "PPA", "ITA", "XBI", "IBB", "ARKK", "ARKG",
    "SMH", "SOXX", "HACK", "BOTZ", "LIT", "TAN", "ICLN",
}


# ==============================================================================
# DATA CLASSES
# ==============================================================================

@dataclass
class EarningsInfo:
    """
    Earnings information for a single ticker.
    
    Attributes:
        ticker: Stock symbol
        report_date: Date of earnings report (YYYY-MM-DD)
        days_until: Days until the earnings report
        is_in_blackout: True if within blackout window
        fiscal_quarter: e.g., "Q4 2024" (if available)
    """
    ticker: str
    report_date: Optional[str]
    days_until: Optional[int]
    is_in_blackout: bool
    fiscal_quarter: Optional[str] = None


# ==============================================================================
# EARNINGS CALENDAR CLASS
# ==============================================================================

class EarningsCalendar:
    """
    Fetches and caches earnings dates using yfinance.
    
    No API key required - uses Yahoo Finance data.
    
    PERFORMANCE OPTIMIZATION:
    - Uses persistent JSON file cache (survives restarts)
    - Cache refreshes once per trading day
    - Prefetch method loads all tickers at startup from cache
    - Reduces startup time from ~100 seconds to <1 second
    """
    
    # Cache duration in seconds (6 hours - but also checks trading day)
    CACHE_DURATION_SECONDS = 6 * 60 * 60
    
    def __init__(self):
        """Initialize earnings calendar service."""
        self._logger = get_logger()
        self._cache: Dict[str, EarningsInfo] = {}
        self._cache_times: Dict[str, float] = {}
        self._file_cache_loaded = False
        self._file_cache_date: Optional[str] = None
        
        # Load persistent cache on init
        self._load_file_cache()
    
    def _get_cache_file_path(self) -> str:
        """Get the path to the persistent cache file."""
        return CACHE_FILE_PATH
    
    def _load_file_cache(self) -> bool:
        """
        Load earnings data from persistent JSON cache file.
        
        Returns:
            True if cache was loaded and is valid for today
        """
        try:
            cache_path = self._get_cache_file_path()
            if not os.path.exists(cache_path):
                self._logger.log("earnings_cache_file_not_found", {"path": cache_path})
                return False
            
            with open(cache_path, 'r') as f:
                cache_data = json.load(f)
            
            # Check if cache is from today
            cache_date = cache_data.get("cache_date")
            today = datetime.now().strftime("%Y-%m-%d")
            
            if cache_date != today:
                self._logger.log("earnings_cache_stale", {
                    "cache_date": cache_date,
                    "today": today
                })
                return False
            
            # Load tickers from cache
            tickers_data = cache_data.get("tickers", {})
            loaded_count = 0
            
            for ticker, data in tickers_data.items():
                # Recalculate days_until based on today's date
                report_date = data.get("report_date")
                days_until = None
                
                if report_date:
                    try:
                        report_dt = datetime.strptime(report_date, "%Y-%m-%d").date()
                        today_dt = datetime.now().date()
                        days_until = (report_dt - today_dt).days
                    except ValueError:
                        pass
                
                earnings_info = EarningsInfo(
                    ticker=ticker,
                    report_date=report_date,
                    days_until=days_until,
                    is_in_blackout=False,
                    fiscal_quarter=data.get("fiscal_quarter")
                )
                
                self._cache[ticker] = earnings_info
                self._cache_times[ticker] = time.time()
                loaded_count += 1
            
            self._file_cache_loaded = True
            self._file_cache_date = cache_date
            
            self._logger.log("earnings_cache_loaded", {
                "tickers_loaded": loaded_count,
                "cache_date": cache_date
            })
            
            return True
            
        except Exception as e:
            self._logger.warn(f"Failed to load earnings cache: {e}")
            return False
    
    def _save_file_cache(self) -> bool:
        """
        Save current earnings data to persistent JSON cache file.
        
        Returns:
            True if saved successfully
        """
        try:
            cache_path = self._get_cache_file_path()
            
            # Ensure cache directory exists
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            
            # Build cache data
            tickers_data = {}
            for ticker, info in self._cache.items():
                tickers_data[ticker] = {
                    "report_date": info.report_date,
                    "days_until": info.days_until,
                    "fiscal_quarter": info.fiscal_quarter
                }
            
            cache_data = {
                "cache_date": datetime.now().strftime("%Y-%m-%d"),
                "cache_time": datetime.now().isoformat(),
                "ticker_count": len(tickers_data),
                "tickers": tickers_data
            }
            
            with open(cache_path, 'w') as f:
                json.dump(cache_data, f, indent=2)
            
            self._logger.log("earnings_cache_saved", {
                "tickers_saved": len(tickers_data),
                "path": cache_path
            })
            
            return True
            
        except Exception as e:
            self._logger.error(f"Failed to save earnings cache: {e}")
            return False
    
    def prefetch_all(self, tickers: List[str], force_refresh: bool = False) -> int:
        """
        Prefetch earnings data for all tickers at once.
        
        This is called at startup to load all earnings data efficiently.
        Uses cached data if available and fresh, otherwise fetches from yfinance.
        
        Args:
            tickers: List of stock symbols to prefetch
            force_refresh: If True, ignore cache and fetch fresh data
            
        Returns:
            Number of tickers that needed fresh fetches (0 = all from cache)
        """
        today = datetime.now().strftime("%Y-%m-%d")
        
        # Check if cache is valid for today
        if not force_refresh and self._file_cache_loaded and self._file_cache_date == today:
            # All tickers already in cache
            cached_count = sum(1 for t in tickers if t in self._cache)
            missing = [t for t in tickers if t not in self._cache]
            
            if not missing:
                self._logger.log("earnings_prefetch_all_cached", {
                    "total": len(tickers),
                    "cached": cached_count
                })
                return 0
            
            # Only fetch missing tickers
            tickers_to_fetch = missing
        else:
            tickers_to_fetch = tickers
        
        # Fetch earnings data for missing tickers
        self._logger.log("earnings_prefetch_start", {
            "total_tickers": len(tickers),
            "to_fetch": len(tickers_to_fetch)
        })
        
        fetch_count = 0
        start_time = time.time()
        
        for i, ticker in enumerate(tickers_to_fetch):
            try:
                # Fetch from yfinance (this is the slow part)
                self._fetch_earnings(ticker)
                fetch_count += 1
                
                # Log progress every 20 tickers
                if (i + 1) % 20 == 0:
                    elapsed = time.time() - start_time
                    self._logger.log("earnings_prefetch_progress", {
                        "completed": i + 1,
                        "total": len(tickers_to_fetch),
                        "elapsed_seconds": round(elapsed, 1)
                    })
                    
            except Exception as e:
                self._logger.warn(f"Prefetch failed for {ticker}: {e}")
        
        # Save to persistent cache
        self._save_file_cache()
        
        elapsed = time.time() - start_time
        self._logger.log("earnings_prefetch_complete", {
            "fetched": fetch_count,
            "elapsed_seconds": round(elapsed, 1),
            "avg_per_ticker": round(elapsed / max(1, fetch_count), 2)
        })
        
        return fetch_count
    
    def is_cache_valid_for_today(self) -> bool:
        """Check if the file cache is valid for today's trading session."""
        today = datetime.now().strftime("%Y-%m-%d")
        return self._file_cache_loaded and self._file_cache_date == today
    
    def is_in_blackout(self, ticker: str, blackout_days: int = 3) -> bool:
        """
        Check if a ticker is within the earnings blackout window.
        
        Args:
            ticker: Stock symbol to check
            blackout_days: Number of days before/after earnings to avoid
            
        Returns:
            True if earnings are within blackout_days, False otherwise
        """
        earnings_info = self.get_earnings_info(ticker)
        
        if earnings_info is None:
            # No earnings data available - assume NOT in blackout
            # This is a safe default that allows trading
            return False
        
        if earnings_info.days_until is None:
            return False
        
        # Check if within blackout window (before OR after earnings)
        # days_until can be negative if earnings just passed
        return abs(earnings_info.days_until) <= blackout_days
    
    def get_earnings_info(self, ticker: str) -> Optional[EarningsInfo]:
        """
        Get earnings information for a ticker.
        
        Uses cached data if available and fresh, otherwise fetches from yfinance.
        
        Args:
            ticker: Stock symbol
            
        Returns:
            EarningsInfo object or None if not available
        """
        # Check in-memory cache first
        if ticker in self._cache:
            cache_time = self._cache_times.get(ticker, 0)
            if time.time() - cache_time < self.CACHE_DURATION_SECONDS:
                return self._cache[ticker]
        
        # Check persistent state (survives restarts)
        state_key = f"earnings.{ticker}"
        cached_data = get_state(state_key)
        
        if cached_data:
            cached_time = cached_data.get("fetch_time", 0)
            if time.time() - cached_time < self.CACHE_DURATION_SECONDS:
                earnings_info = EarningsInfo(
                    ticker=ticker,
                    report_date=cached_data.get("report_date"),
                    days_until=cached_data.get("days_until"),
                    is_in_blackout=cached_data.get("is_in_blackout", False),
                    fiscal_quarter=cached_data.get("fiscal_quarter")
                )
                # Populate in-memory cache
                self._cache[ticker] = earnings_info
                self._cache_times[ticker] = cached_time
                return earnings_info
        
        # Fetch fresh data from yfinance
        return self._fetch_earnings(ticker)
    
    def _fetch_earnings(self, ticker: str) -> Optional[EarningsInfo]:
        """
        Fetch earnings data from yfinance.
        
        Args:
            ticker: Stock symbol
            
        Returns:
            EarningsInfo object or None on error
        """
        # Skip ETFs - they don't have earnings and cause 404 errors
        if ticker.upper() in ETF_SYMBOLS:
            return EarningsInfo(
                ticker=ticker,
                report_date=None,
                days_until=None,
                is_in_blackout=False,
                fiscal_quarter=None
            )
        
        try:
            # Create yfinance Ticker object
            stock = yf.Ticker(ticker)
            
            # Get calendar data (includes earnings date)
            calendar = stock.calendar
            
            # calendar can be a dict or DataFrame depending on yfinance version
            earnings_date = None
            
            if calendar is not None:
                if isinstance(calendar, dict):
                    # Dict format: {'Earnings Date': [datetime, ...], ...}
                    earnings_dates = calendar.get('Earnings Date', [])
                    if earnings_dates and len(earnings_dates) > 0:
                        earnings_date = earnings_dates[0]
                else:
                    # DataFrame format - try to extract earnings date
                    try:
                        if 'Earnings Date' in calendar.columns:
                            earnings_date = calendar['Earnings Date'].iloc[0]
                        elif len(calendar) > 0:
                            # Try first row, first column
                            earnings_date = calendar.iloc[0, 0]
                    except Exception:
                        pass
            
            # If calendar didn't work, try earnings_dates property
            if earnings_date is None:
                try:
                    earnings_dates_df = stock.earnings_dates
                    if earnings_dates_df is not None and len(earnings_dates_df) > 0:
                        # Get the next upcoming earnings (first row is usually next)
                        today = get_market_clock().now()
                        for idx in earnings_dates_df.index:
                            if hasattr(idx, 'to_pydatetime'):
                                dt = idx.to_pydatetime()
                            else:
                                dt = idx
                            if dt >= today:
                                earnings_date = dt
                                break
                except Exception:
                    pass
            
            # Process the earnings date
            if earnings_date is None:
                earnings_info = self._create_no_earnings_info(ticker)
            else:
                # Convert to date if needed
                if hasattr(earnings_date, 'date'):
                    report_date = earnings_date.date()
                elif hasattr(earnings_date, 'to_pydatetime'):
                    report_date = earnings_date.to_pydatetime().date()
                else:
                    report_date = earnings_date
                
                today = get_market_clock().now().date()
                days_until = (report_date - today).days
                report_date_str = report_date.strftime("%Y-%m-%d")
                
                earnings_info = EarningsInfo(
                    ticker=ticker,
                    report_date=report_date_str,
                    days_until=days_until,
                    is_in_blackout=False,  # Will be calculated by caller
                    fiscal_quarter=None
                )
                
                self._logger.log("earnings_fetched", {
                    "ticker": ticker,
                    "report_date": report_date_str,
                    "days_until": days_until
                })
            
            # Cache the result in memory
            self._cache[ticker] = earnings_info
            self._cache_times[ticker] = time.time()
            
            # Persist to state database
            set_state(f"earnings.{ticker}", {
                "report_date": earnings_info.report_date,
                "days_until": earnings_info.days_until,
                "is_in_blackout": earnings_info.is_in_blackout,
                "fiscal_quarter": earnings_info.fiscal_quarter,
                "fetch_time": time.time()
            })
            
            return earnings_info
            
        except Exception as e:
            self._logger.warn(f"Failed to fetch earnings for {ticker}: {e}")
            return None
    
    def _create_no_earnings_info(self, ticker: str) -> EarningsInfo:
        """Create EarningsInfo for when no upcoming earnings are found."""
        return EarningsInfo(
            ticker=ticker,
            report_date=None,
            days_until=None,
            is_in_blackout=False,
            fiscal_quarter=None
        )
    
    def batch_check_blackout(self, tickers: List[str], blackout_days: int = 3) -> Dict[str, bool]:
        """
        Check multiple tickers for earnings blackout.
        
        Args:
            tickers: List of stock symbols
            blackout_days: Days before/after earnings to avoid
            
        Returns:
            Dictionary mapping ticker to blackout status
        """
        results = {}
        
        for ticker in tickers:
            try:
                results[ticker] = self.is_in_blackout(ticker, blackout_days)
            except Exception as e:
                self._logger.error(f"Blackout check failed for {ticker}: {e}")
                results[ticker] = False  # Safe default: allow trading
        
        return results
    
    def get_next_earnings_date(self, ticker: str) -> Optional[str]:
        """
        Get the next earnings date for a ticker.
        
        Args:
            ticker: Stock symbol
            
        Returns:
            Earnings date string (YYYY-MM-DD) or None
        """
        info = self.get_earnings_info(ticker)
        return info.report_date if info else None


# ==============================================================================
# IV-AWARE EARNINGS WINDOW FUNCTIONS
# ==============================================================================

def earnings_window_days(iv_rank: float) -> int:
    """
    Calculate dynamic earnings window based on IV Rank.
    
    Higher IV rank = market pricing event earlier = larger window.
    
    Formula: window = clamp(round(5 + (IVR/100) * 16), 5, 21)
    
    Examples:
        IVR 10 -> 7 days
        IVR 50 -> 13 days  
        IVR 90 -> 19 days
    
    Args:
        iv_rank: IV rank as 0-100 value
        
    Returns:
        Number of days for earnings window (5-21)
    """
    iv_rank = max(0, min(100, iv_rank))
    raw_window = round(5 + (iv_rank / 100.0) * 16)
    return max(5, min(21, raw_window))


def is_in_earnings_window(
    ticker: str, 
    today: datetime,
    iv_rank: float = 50.0
) -> bool:
    """
    Check if a ticker is in the earnings window using IV-aware sizing.
    
    If IV rank is missing, defaults to 50 (conservative 13-day window).
    
    Args:
        ticker: Stock symbol
        today: Current date
        iv_rank: IV rank as 0-100 (default 50 if unknown)
        
    Returns:
        True if within earnings window, False otherwise
    """
    calendar = get_earnings_calendar()
    info = calendar.get_earnings_info(ticker)
    
    if info is None or info.report_date is None:
        return False
    
    try:
        earnings_date = datetime.strptime(info.report_date, "%Y-%m-%d").date()
        today_date = today.date() if hasattr(today, 'date') else today
        days = (earnings_date - today_date).days
    except (ValueError, TypeError):
        return False
    
    window = earnings_window_days(iv_rank)
    
    # Window extends from -1 (post-earnings crush day) to +window days before
    return -1 <= days <= window


def get_earnings_window_info(ticker: str, iv_rank: float = 50.0) -> dict:
    """
    Get detailed earnings window information for a ticker.
    
    Args:
        ticker: Stock symbol
        iv_rank: IV rank for window calculation
        
    Returns:
        Dictionary with earnings details and window info
    """
    from ..core.clock import get_market_clock
    
    calendar = get_earnings_calendar()
    info = calendar.get_earnings_info(ticker)
    now = get_market_clock().now()
    
    if info is None or info.report_date is None:
        return {
            "ticker": ticker,
            "has_earnings": False,
            "in_window": False,
            "window_days": earnings_window_days(iv_rank),
            "days_until": None,
            "report_date": None
        }
    
    in_window = is_in_earnings_window(ticker, now, iv_rank)
    window = earnings_window_days(iv_rank)
    
    return {
        "ticker": ticker,
        "has_earnings": True,
        "in_window": in_window,
        "window_days": window,
        "days_until": info.days_until,
        "report_date": info.report_date,
        "iv_rank_used": iv_rank
    }


# ==============================================================================
# SINGLETON ACCESS
# ==============================================================================

_earnings_calendar: Optional[EarningsCalendar] = None


def get_earnings_calendar() -> EarningsCalendar:
    """
    Get the singleton EarningsCalendar instance.
    
    Returns:
        The global EarningsCalendar instance
    """
    global _earnings_calendar
    if _earnings_calendar is None:
        _earnings_calendar = EarningsCalendar()
    return _earnings_calendar
