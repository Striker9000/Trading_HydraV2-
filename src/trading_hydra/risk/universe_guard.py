"""
UniverseGuard - Singleton pattern for enforcing premarket symbol selection.

This module ensures that only symbols selected during premarket analysis
can be traded during the session. Implements fail-closed safety pattern.

When no premarket selection is available:
- If fail_closed=True: Block ALL trades (safety mode)
- If fail_closed=False: Allow ALL symbols (permissive mode for testing)
"""

from typing import Set, Optional, List
from datetime import datetime
import threading


class UniverseGuard:
    """
    Singleton guard that enforces premarket universe selection.
    
    Only symbols that pass premarket analysis and scoring are allowed
    to trade during the session. This prevents trading in illiquid or
    unsuitable symbols.
    """
    
    _instance: Optional['UniverseGuard'] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._selected_symbols: Set[str] = set()
        self._selection_timestamp: Optional[datetime] = None
        self._selection_date: Optional[str] = None
        self._fail_closed: bool = False
        self._enabled: bool = False
        self._initialized = True
        
    def configure(self, fail_closed: bool = False, enabled: bool = False):
        """
        Configure the universe guard behavior.
        
        Args:
            fail_closed: If True, block all trades when no selection available
            enabled: If False, guard is bypassed entirely (all symbols allowed)
        """
        self._fail_closed = fail_closed
        self._enabled = enabled
        
    def set_universe(self, symbols: List[str], selection_date: Optional[str] = None):
        """
        Lock in the premarket-selected symbols for today's session.
        
        Args:
            symbols: List of symbols allowed to trade
            selection_date: Date string (YYYY-MM-DD) for the selection
        """
        with self._lock:
            self._selected_symbols = set(s.upper() for s in symbols)
            self._selection_timestamp = datetime.now()
            self._selection_date = selection_date or datetime.now().strftime("%Y-%m-%d")
            
    def clear_universe(self):
        """Clear the current universe selection (e.g., at end of day)."""
        with self._lock:
            self._selected_symbols.clear()
            self._selection_timestamp = None
            self._selection_date = None
            
    def is_symbol_allowed(self, symbol: str, bot_id: Optional[str] = None) -> bool:
        """
        Check if a symbol is allowed to trade.
        
        Args:
            symbol: The ticker symbol to check
            bot_id: Optional bot identifier for logging
            
        Returns:
            True if symbol is allowed, False otherwise
        """
        if not self._enabled:
            return True
            
        symbol_upper = symbol.upper()
        
        if not self._selected_symbols:
            if self._fail_closed:
                return False
            else:
                return True
                
        return symbol_upper in self._selected_symbols
        
    def get_allowed_symbols(self) -> List[str]:
        """Return list of currently allowed symbols."""
        return list(self._selected_symbols)
    
    def is_trading_allowed(self) -> bool:
        """
        Check if trading is allowed based on universe selection status.
        
        Returns:
            True if trading is allowed, False if blocked
        """
        if not self._enabled:
            return True
        
        if not self._selected_symbols:
            if self._fail_closed:
                return False
            else:
                return True
        
        return True
        
    def get_status(self) -> dict:
        """Return current guard status for monitoring."""
        return {
            "enabled": self._enabled,
            "fail_closed": self._fail_closed,
            "symbols_count": len(self._selected_symbols),
            "symbols": list(self._selected_symbols)[:10],
            "selection_date": self._selection_date,
            "selection_timestamp": self._selection_timestamp.isoformat() if self._selection_timestamp else None
        }


_guard_instance: Optional[UniverseGuard] = None


def get_universe_guard() -> UniverseGuard:
    """Get the singleton UniverseGuard instance."""
    global _guard_instance
    if _guard_instance is None:
        _guard_instance = UniverseGuard()
    return _guard_instance


def configure_universe_guard(fail_closed: bool = False, enabled: bool = False):
    """Configure the universe guard at startup."""
    guard = get_universe_guard()
    guard.configure(fail_closed=fail_closed, enabled=enabled)
    return guard
