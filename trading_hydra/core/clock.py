"""Market clock utilities with timezone support"""
from datetime import datetime, time
from zoneinfo import ZoneInfo
from typing import Optional

from .config import load_settings


def _parse_time(time_str: str) -> time:
    """Parse HH:MM string to time object."""
    parts = time_str.split(":")
    return time(int(parts[0]), int(parts[1]))


class MarketClock:
    def __init__(self, timezone: Optional[str] = None):
        settings = load_settings()
        
        if timezone:
            self.tz = ZoneInfo(timezone)
        else:
            tz_name = settings.get("system", {}).get("timezone", "America/Los_Angeles")
            self.tz = ZoneInfo(tz_name)
        
        market_hours = settings.get("market_hours", {})
        self._market_open = _parse_time(market_hours.get("market_open", "06:30"))
        self._market_close = _parse_time(market_hours.get("market_close", "13:00"))
        self._pre_market_start = _parse_time(market_hours.get("pre_market_start", "01:00"))
        self._after_hours_end = _parse_time(market_hours.get("after_hours_end", "17:00"))
        self._pre_market_intel_start = _parse_time(market_hours.get("pre_market_intel_start", "06:00"))
        self._pre_market_intel_end = _parse_time(market_hours.get("pre_market_intel_end", "06:30"))
    
    def now(self) -> datetime:
        return datetime.now(self.tz)
    
    def get_date_string(self) -> str:
        return self.now().strftime("%Y-%m-%d")
    
    def is_weekend(self) -> bool:
        """Check if it's Saturday (5) or Sunday (6)."""
        return self.now().weekday() >= 5
    
    def is_market_hours(self) -> bool:
        now = self.now()
        if now.weekday() >= 5:
            return False
        
        current_time = now.time()
        return self._market_open <= current_time <= self._market_close
    
    def is_extended_hours(self) -> bool:
        now = self.now()
        if now.weekday() >= 5:
            return False
        
        current_time = now.time()
        return self._pre_market_start <= current_time <= self._after_hours_end
    
    def is_pre_market_intel_window(self) -> bool:
        """Check if we're in the pre-market intelligence gathering window."""
        now = self.now()
        if now.weekday() >= 5:
            return False
        
        current_time = now.time()
        return self._pre_market_intel_start <= current_time <= self._pre_market_intel_end
    
    def get_market_open(self) -> time:
        return self._market_open
    
    def get_market_close(self) -> time:
        return self._market_close
    
    def get_pre_market_intel_start(self) -> time:
        return self._pre_market_intel_start
    
    def get_pre_market_intel_end(self) -> time:
        return self._pre_market_intel_end
    
    def now_naive(self) -> datetime:
        """Get current time as naive datetime (for comparing with legacy timestamps)."""
        return self.now().replace(tzinfo=None)
    
    @staticmethod
    def parse_iso_to_naive(iso_str: str) -> datetime:
        """
        Parse ISO string to naive datetime for consistent comparisons.
        Handles both timezone-aware and naive ISO strings.
        """
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00") if "Z" in iso_str else iso_str)
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    
    def get_adjusted_loop_interval(self, default_interval: int = 5) -> int:
        """
        Get adjusted loop interval based on market conditions.
        
        During market hours: use default interval (5 seconds)
        Outside market hours: use slower polling (60 seconds) to reduce API calls
        Crypto trades 24/7, so we still need some activity even off-hours.
        
        Returns:
            Adjusted interval in seconds
        """
        settings = load_settings()
        caching = settings.get("caching", {})
        closed_interval = caching.get("market_closed_poll_seconds", 60)
        
        # If it's market hours, use normal fast polling
        if self.is_market_hours():
            return default_interval
        
        # Outside market hours: slower polling for efficiency
        # (Crypto bot still runs, but doesn't need 5-second updates)
        return closed_interval
    
    def should_skip_stock_bots(self) -> bool:
        """
        Check if stock/options bots should be skipped entirely.
        
        Stocks and options don't trade on weekends or outside market hours,
        so we can skip all their signal analysis to save API calls.
        
        Returns:
            True if stock bots should be skipped
        """
        # Skip on weekends
        if self.is_weekend():
            return True
        
        # Skip outside extended hours (options/stocks don't trade)
        if not self.is_extended_hours():
            return True
        
        return False


_market_clock: Optional[MarketClock] = None


def get_market_clock() -> MarketClock:
    global _market_clock
    if _market_clock is None:
        _market_clock = MarketClock()
    return _market_clock
