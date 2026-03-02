"""
Data Staleness Service - Context-aware cache TTL management.

Provides different staleness thresholds based on:
- Market hours vs after-hours
- Data type (quotes, news, regime, etc.)
- Session state (premarket intel, active trading, wind-down)

This ensures tight freshness requirements during active trading
while allowing longer cache TTLs when markets are closed.
"""

from dataclasses import dataclass
from datetime import time
from enum import Enum
from typing import Optional, Dict, Any

from .clock import MarketClock, get_market_clock


class DataType(Enum):
    """Types of data with different staleness requirements."""
    QUOTE = "quote"           # Real-time quotes - most time-sensitive
    BAR = "bar"               # OHLCV bars - slightly less sensitive
    NEWS = "news"             # News items - moderate sensitivity
    SENTIMENT = "sentiment"   # Sentiment scores - moderate
    REGIME = "regime"         # Market regime - less sensitive
    POSITION = "position"     # Position data - moderate
    ACCOUNT = "account"       # Account data - moderate
    EARNINGS = "earnings"     # Earnings calendar - low sensitivity
    MACRO = "macro"           # Macro intel - low sensitivity
    SMART_MONEY = "smart_money"  # Congress/13F - low sensitivity


class SessionPhase(Enum):
    """Current trading session phase."""
    OVERNIGHT = "overnight"       # Markets fully closed
    PREMARKET_INTEL = "premarket_intel"  # Gathering intelligence before open
    PREMARKET = "premarket"       # Extended hours before regular open
    MARKET_OPEN = "market_open"   # Regular trading hours
    AFTER_HOURS = "after_hours"   # Extended hours after close
    WEEKEND = "weekend"           # Weekend - markets closed


@dataclass
class StalenessConfig:
    """Staleness thresholds for a data type across session phases."""
    market_hours_ttl: float      # TTL during regular market hours (seconds)
    premarket_ttl: float         # TTL during premarket (seconds)
    after_hours_ttl: float       # TTL during after-hours (seconds)
    overnight_ttl: float         # TTL when markets closed (seconds)
    
    def get_ttl(self, phase: SessionPhase) -> float:
        """Get TTL for the given session phase."""
        if phase == SessionPhase.MARKET_OPEN:
            return self.market_hours_ttl
        elif phase in [SessionPhase.PREMARKET, SessionPhase.PREMARKET_INTEL]:
            return self.premarket_ttl
        elif phase == SessionPhase.AFTER_HOURS:
            return self.after_hours_ttl
        else:  # OVERNIGHT, WEEKEND
            return self.overnight_ttl


# Default staleness configurations per data type
DEFAULT_STALENESS_CONFIGS: Dict[DataType, StalenessConfig] = {
    # Quotes: Reasonable threshold accounting for loop interval (~5s)
    DataType.QUOTE: StalenessConfig(
        market_hours_ttl=30.0,     # 30 seconds during trading (allows for loop interval + API latency)
        premarket_ttl=60.0,        # 60 seconds premarket
        after_hours_ttl=120.0,     # 2 minutes after hours
        overnight_ttl=300.0        # 5 minutes overnight
    ),
    
    # Bars: Slightly relaxed vs quotes
    DataType.BAR: StalenessConfig(
        market_hours_ttl=15.0,     # 15 seconds during trading
        premarket_ttl=30.0,        # 30 seconds premarket
        after_hours_ttl=60.0,      # 1 minute after hours
        overnight_ttl=600.0        # 10 minutes overnight
    ),
    
    # News: Moderate - updates aren't instant
    DataType.NEWS: StalenessConfig(
        market_hours_ttl=60.0,     # 1 minute during trading
        premarket_ttl=60.0,        # 1 minute premarket (catalyst hunting)
        after_hours_ttl=120.0,     # 2 minutes after hours
        overnight_ttl=600.0        # 10 minutes overnight
    ),
    
    # Sentiment: Similar to news
    DataType.SENTIMENT: StalenessConfig(
        market_hours_ttl=60.0,
        premarket_ttl=60.0,
        after_hours_ttl=120.0,
        overnight_ttl=600.0
    ),
    
    # Regime: Slower moving - VIX doesn't change every second
    DataType.REGIME: StalenessConfig(
        market_hours_ttl=120.0,    # 2 minutes during trading
        premarket_ttl=180.0,       # 3 minutes premarket
        after_hours_ttl=300.0,     # 5 minutes after hours
        overnight_ttl=1800.0       # 30 minutes overnight
    ),
    
    # Position: Moderate - need to track fills
    DataType.POSITION: StalenessConfig(
        market_hours_ttl=10.0,     # 10 seconds during trading
        premarket_ttl=30.0,        # 30 seconds premarket
        after_hours_ttl=60.0,      # 1 minute after hours
        overnight_ttl=300.0        # 5 minutes overnight
    ),
    
    # Account: Moderate - equity doesn't change every second
    DataType.ACCOUNT: StalenessConfig(
        market_hours_ttl=30.0,     # 30 seconds during trading
        premarket_ttl=60.0,        # 1 minute premarket
        after_hours_ttl=120.0,     # 2 minutes after hours
        overnight_ttl=600.0        # 10 minutes overnight
    ),
    
    # Earnings: Slow - calendar doesn't change often
    DataType.EARNINGS: StalenessConfig(
        market_hours_ttl=3600.0,   # 1 hour during trading
        premarket_ttl=3600.0,      # 1 hour premarket
        after_hours_ttl=3600.0,    # 1 hour after hours
        overnight_ttl=14400.0      # 4 hours overnight
    ),
    
    # Macro intel: Slow - Fed speaks rarely
    DataType.MACRO: StalenessConfig(
        market_hours_ttl=300.0,    # 5 minutes during trading
        premarket_ttl=300.0,       # 5 minutes premarket
        after_hours_ttl=600.0,     # 10 minutes after hours
        overnight_ttl=3600.0       # 1 hour overnight
    ),
    
    # Smart money: Very slow - filings are periodic
    DataType.SMART_MONEY: StalenessConfig(
        market_hours_ttl=3600.0,   # 1 hour during trading
        premarket_ttl=3600.0,      # 1 hour premarket
        after_hours_ttl=3600.0,    # 1 hour after hours
        overnight_ttl=14400.0      # 4 hours overnight
    ),
}


class DataStalenessService:
    """
    Provides context-aware staleness thresholds.
    
    Usage:
        staleness = get_data_staleness()
        ttl = staleness.get_ttl(DataType.QUOTE)
        if cache_age > ttl:
            refresh_data()
    """
    
    def __init__(self, clock: Optional[MarketClock] = None):
        self._clock = clock or get_market_clock()
        self._configs = DEFAULT_STALENESS_CONFIGS.copy()
    
    def get_session_phase(self) -> SessionPhase:
        """Determine the current trading session phase using MarketClock configuration."""
        clock = self._clock
        
        # Weekend check
        if clock.is_weekend():
            return SessionPhase.WEEKEND
        
        # Check various session windows using MarketClock's configured times
        if clock.is_market_hours():
            return SessionPhase.MARKET_OPEN
        
        if clock.is_pre_market_intel_window():
            return SessionPhase.PREMARKET_INTEL
        
        # Use is_extended_hours to check if in pre/post market
        if clock.is_extended_hours():
            now = clock.now()
            current_time = now.time()
            market_open = clock.get_market_open()
            market_close = clock.get_market_close()
            
            # Before market open = premarket
            if current_time < market_open:
                return SessionPhase.PREMARKET
            # After market close = after hours
            elif current_time > market_close:
                return SessionPhase.AFTER_HOURS
        
        # Outside extended hours = overnight
        return SessionPhase.OVERNIGHT
    
    def get_ttl(self, data_type: DataType) -> float:
        """
        Get the appropriate TTL for a data type based on current session.
        
        Args:
            data_type: The type of data to check
            
        Returns:
            TTL in seconds
        """
        config = self._configs.get(data_type)
        if not config:
            # Default: 60 seconds market hours, 5 minutes otherwise
            phase = self.get_session_phase()
            if phase == SessionPhase.MARKET_OPEN:
                return 60.0
            return 300.0
        
        phase = self.get_session_phase()
        return config.get_ttl(phase)
    
    def is_stale(self, data_type: DataType, age_seconds: float) -> bool:
        """
        Check if data is stale based on type and current session.
        
        Args:
            data_type: The type of data
            age_seconds: Age of the data in seconds
            
        Returns:
            True if data is stale and should be refreshed
        """
        ttl = self.get_ttl(data_type)
        return age_seconds > ttl
    
    def get_staleness_info(self, data_type: DataType, age_seconds: float) -> Dict[str, Any]:
        """
        Get detailed staleness information for logging/debugging.
        
        Returns dict with:
        - is_stale: bool
        - age_seconds: float
        - ttl_seconds: float
        - session_phase: str
        - percent_of_ttl: float
        """
        phase = self.get_session_phase()
        ttl = self.get_ttl(data_type)
        
        return {
            "is_stale": age_seconds > ttl,
            "age_seconds": round(age_seconds, 1),
            "ttl_seconds": ttl,
            "session_phase": phase.value,
            "percent_of_ttl": round((age_seconds / ttl) * 100, 1) if ttl > 0 else 100.0,
            "data_type": data_type.value
        }
    
    def update_config(self, data_type: DataType, config: StalenessConfig) -> None:
        """Update staleness config for a data type."""
        self._configs[data_type] = config


# Singleton instance
_staleness_service: Optional[DataStalenessService] = None


def get_data_staleness() -> DataStalenessService:
    """Get the singleton DataStalenessService instance."""
    global _staleness_service
    if _staleness_service is None:
        _staleness_service = DataStalenessService()
    return _staleness_service


def reset_staleness_service() -> None:
    """Reset the singleton (for testing)."""
    global _staleness_service
    _staleness_service = None
