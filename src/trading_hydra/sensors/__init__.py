"""
HydraSensors - Background Market Monitoring Subsystem

A non-blocking, fail-open sensor layer that provides:
- Watchlist management with tags
- Cached market data (quotes/bars)
- Technical indicators (SMA/RSI/returns)
- Breadth sensors (RSP vs SPY, SMH vs SPY)
- Regime detection (risk_on/risk_off)

Usage:
    from trading_hydra.sensors import sensors
    
    # Start sensors (non-blocking, returns immediately)
    sensors.start()
    
    # Read signals (fast, in-memory)
    regime = sensors.get_regime()
    signals = sensors.get_signals(tag="COMPUTE_CORE")
    watchlist = sensors.get_watchlist("core_macro")
    
    # Check status
    if sensors.is_ready():
        # Full data available
    else:
        # Use defaults, sensors still warming up
"""

from .manager import SensorsManager, get_sensors_manager

# Global singleton
_sensors_instance = None

def get_sensors() -> SensorsManager:
    """Get the global sensors manager instance."""
    global _sensors_instance
    if _sensors_instance is None:
        _sensors_instance = SensorsManager()
    return _sensors_instance

# Convenience aliases for simple API
sensors = None  # Will be initialized on first get_sensors() call

def start():
    """Start the sensors background thread."""
    global sensors
    sensors = get_sensors()
    sensors.start()
    return sensors

def stop():
    """Stop the sensors background thread."""
    global sensors
    if sensors:
        sensors.stop()

def is_ready() -> bool:
    """Check if sensors have warmed up."""
    global sensors
    return sensors.is_ready() if sensors else False

def get_regime():
    """Get current market regime state."""
    global sensors
    return sensors.get_regime() if sensors else None

def get_signals(tag: str = None, limit: int = 10):
    """Get latest signals, optionally filtered by tag."""
    global sensors
    return sensors.get_signals(tag=tag, limit=limit) if sensors else []

def get_watchlist(name: str):
    """Get tickers in a named watchlist."""
    global sensors
    return sensors.get_watchlist(name) if sensors else []

def get_breadth():
    """Get current breadth sensor readings."""
    global sensors
    return sensors.get_breadth() if sensors else {}


def get_signal(ticker: str):
    """
    Get the signal for a specific ticker.
    
    Returns TickerSignal with indicators, or None if not available.
    """
    global sensors
    if not sensors:
        return None
    signals = sensors.get_signals(limit=100)
    for sig in signals:
        if sig.ticker == ticker:
            return sig
    return None


def is_risk_on() -> bool:
    """
    Quick check for risk-on regime.
    
    Returns True if regime is 'risk_on', False otherwise.
    Fail-open: Returns True if sensors not ready (allows trading).
    """
    global sensors
    if not sensors or not sensors.is_ready():
        return True  # Fail-open: assume risk-on if sensors not ready
    
    regime = sensors.get_regime()
    if not regime:
        return True  # Fail-open
    
    from .state import RegimeState
    return regime.state == RegimeState.RISK_ON


def is_risk_off() -> bool:
    """
    Quick check for risk-off regime.
    
    Returns True if regime is 'risk_off', False otherwise.
    Fail-open: Returns False if sensors not ready (doesn't block trading).
    """
    global sensors
    if not sensors or not sensors.is_ready():
        return False  # Fail-open: don't block trading if sensors not ready
    
    regime = sensors.get_regime()
    if not regime:
        return False  # Fail-open
    
    from .state import RegimeState
    return regime.state == RegimeState.RISK_OFF


__all__ = [
    'SensorsManager',
    'get_sensors',
    'start',
    'stop',
    'is_ready',
    'get_regime',
    'get_signals',
    'get_signal',
    'get_watchlist',
    'get_breadth',
    'is_risk_on',
    'is_risk_off',
]
