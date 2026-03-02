import random
import time
from typing import Dict, Any, List
from datetime import datetime, time as dt_time
import math

from ..core.logging import get_logger
from ..core.config import load_settings


class MockDataService:
    """Provides realistic mock data for after-hours development"""

    def __init__(self):
        self._logger = get_logger()
        self._settings = load_settings()
        self._mock_enabled = self._settings.get("development", {}).get("enable_mock_data", False)
        self._base_prices = {
            "AAPL": 272.94,
            "META": 580.00,
            "TSLA": 420.00,
            "NVDA": 145.00,
            "BTC/USD": 88500.00,
            "ETH/USD": 2975.00
        }
        self._price_cache = {}
        self._last_update = {}

    def is_mock_enabled(self) -> bool:
        """Check if mock data is enabled"""
        return self._mock_enabled

    def should_mock_market_hours(self) -> bool:
        """Check if we should simulate market hours"""
        return self._settings.get("development", {}).get("mock_market_hours", False)

    def get_mock_quote(self, symbol: str, asset_class: str = "stock") -> Dict[str, Any]:
        """Generate realistic mock quote data"""

        if not self._mock_enabled:
            return None

        # Update price with realistic movement
        current_time = time.time()
        if symbol not in self._last_update or current_time - self._last_update[symbol] > 1.0:
            self._update_mock_price(symbol)
            self._last_update[symbol] = current_time

        price = self._price_cache.get(symbol, self._base_prices.get(symbol, 100.0))

        # Create realistic bid/ask spread
        if asset_class == "crypto":
            spread_pct = 0.001  # 0.1% spread for crypto
        else:
            spread_pct = 0.0005  # 0.05% spread for stocks

        spread = price * spread_pct
        bid = price - spread/2
        ask = price + spread/2

        # Generate volume based on asset type
        if asset_class == "crypto":
            volume = random.randint(50000, 200000)
        else:
            volume = random.randint(500000, 2000000)

        return {
            "symbol": symbol,
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "last": round(price, 2),
            "volume": volume,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

    def _update_mock_price(self, symbol: str):
        """Update mock price with realistic movement"""

        base_price = self._base_prices.get(symbol, 100.0)

        if symbol not in self._price_cache:
            # Initialize with slight random variation
            self._price_cache[symbol] = base_price * (1 + random.uniform(-0.01, 0.01))

        current_price = self._price_cache[symbol]

        # Simulate mean reversion with trending
        mean_revert_strength = 0.1
        trend_strength = 0.02
        volatility = self._get_volatility(symbol)

        # Random walk with mean reversion
        random_change = random.gauss(0, volatility)
        mean_revert = (base_price - current_price) * mean_revert_strength
        trend = random.uniform(-trend_strength, trend_strength)

        price_change = random_change + mean_revert + trend
        new_price = current_price * (1 + price_change)

        # Ensure reasonable bounds
        max_deviation = 0.1  # 10% max from base
        min_price = base_price * (1 - max_deviation)
        max_price = base_price * (1 + max_deviation)

        self._price_cache[symbol] = max(min_price, min(max_price, new_price))

    def _get_volatility(self, symbol: str) -> float:
        """Get appropriate volatility for symbol"""

        volatilities = {
            "AAPL": 0.002,     # 0.2% per update
            "META": 0.003,     # 0.3% per update
            "TSLA": 0.005,     # 0.5% per update
            "NVDA": 0.004,     # 0.4% per update
            "BTC/USD": 0.008,  # 0.8% per update
            "ETH/USD": 0.007   # 0.7% per update
        }

        return volatilities.get(symbol, 0.003)

    def get_mock_positions(self) -> List[Dict[str, Any]]:
        """Generate mock position data"""

        if not self._mock_enabled:
            return []

        # Create a realistic position
        positions = []

        # Mock META position (from logs)
        meta_qty = 115  # From logs showing 115 shares
        meta_price = self.get_mock_quote("META", "stock")["last"]
        meta_market_value = meta_qty * meta_price

        positions.append({
            "symbol": "META",
            "qty": str(meta_qty),
            "market_value": str(meta_market_value),
            "unrealized_pl": str(random.uniform(-500, 500)),  # Random P&L
            "side": "long",
            "asset_class": "us_equity"
        })

        return positions

    def get_mock_account(self, base_equity: float = 44662.70) -> Dict[str, Any]:
        """Generate mock account data"""

        if not self._mock_enabled:
            return None

        # Add small realistic fluctuation to equity
        equity_change = random.uniform(-100, 100)
        current_equity = base_equity + equity_change

        # Calculate cash based on positions
        positions = self.get_mock_positions()
        position_value = sum(float(p["market_value"]) for p in positions)
        cash = current_equity - position_value

        return {
            "equity": round(current_equity, 2),
            "cash": round(cash, 2),
            "portfolio_value": round(current_equity, 2),
            "status": "ACTIVE",
            "trading_blocked": False,
            "buying_power": round(current_equity * 2, 2)  # 2:1 margin
        }

    def simulate_market_hours(self) -> bool:
        """Simulate market hours for development"""

        if not self.should_mock_market_hours():
            return False  # Use real market hours

        # Always simulate market hours for development
        return True

    def log_mock_usage(self, operation: str, data: Dict[str, Any]):
        """Log mock data usage for debugging"""

        self._logger.log("mock_data_used", {
            "operation": operation,
            "mock_enabled": self._mock_enabled,
            **data
        })


_mock_service = None

def get_mock_data_service():
    """Get singleton mock data service"""
    global _mock_service
    if _mock_service is None:
        _mock_service = MockDataService()
    return _mock_service

def get_mock_provider():
    """Get mock data provider (alias for compatibility)"""
    return get_mock_data_service()


def should_generate_signal(symbol: str, signal_type: str) -> bool:
    """Determine if we should generate a mock signal for development"""
    service = get_mock_data_service()
    if not service.is_mock_enabled():
        return False

    # Generate signals 30% of the time during development
    import random
    return random.random() < 0.3


def get_mock_signal_action() -> str:
    """Get a random signal action for development"""
    import random
    actions = ["buy", "hold", "hold", "hold"]  # 25% buy probability
    return random.choice(actions)


def is_development_mode() -> bool:
    """Check if development mode is enabled"""
    return get_mock_data_service().is_mock_enabled()