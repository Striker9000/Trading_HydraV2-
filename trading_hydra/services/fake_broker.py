
"""Fake broker implementation for testing without live API calls"""
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
import time

from ..core.logging import get_logger


@dataclass
class FakePosition:
    symbol: str
    qty: float
    market_value: float
    unrealized_pl: float
    cost_basis: float
    side: str  # "long" or "short"
    entry_price: float
    id: str = ""


@dataclass
class FakeOrder:
    id: str
    symbol: str
    side: str
    qty: Optional[float] = None
    notional: Optional[float] = None
    status: str = "filled"
    filled_qty: str = "0"
    filled_avg_price: str = "0"
    created_at: str = ""
    client_order_id: Optional[str] = None


@dataclass
class FakeAccount:
    equity: float
    cash: float
    buying_power: float
    status: str = "ACTIVE"


class FakeBroker:
    """Deterministic fake broker for testing"""
    
    def __init__(self, initial_equity: float = 10000.0):
        self._logger = get_logger()
        self.account = FakeAccount(
            equity=initial_equity,
            cash=initial_equity * 0.5,
            buying_power=initial_equity * 2.0
        )
        
        # Controllable price feeds
        self.prices: Dict[str, float] = {
            "BTC/USD": 50000.0,
            "ETH/USD": 3000.0,
            "AAPL": 150.0,
            "TSLA": 200.0,
            "SPY": 400.0,
            "QQQ": 300.0,
            "IWM": 200.0
        }
        
        # Order and position tracking
        self.submitted_orders: List[FakeOrder] = []
        self.positions: Dict[str, FakePosition] = {}
        self.order_counter = 1000
        
        # Control flags
        self.api_failure_mode = False
        self.api_failure_message = "Fake API failure"
        
        self._logger.log("fake_broker_initialized", {
            "initial_equity": initial_equity,
            "prices": self.prices
        })
    
    def set_price(self, symbol: str, price: float) -> None:
        """Set price for a symbol"""
        self.prices[symbol] = price
        self._update_position_values()
        self._logger.log("fake_price_update", {"symbol": symbol, "price": price})
    
    def set_api_failure(self, enabled: bool, message: str = "API Error") -> None:
        """Enable/disable API failure simulation"""
        self.api_failure_mode = enabled
        self.api_failure_message = message
        self._logger.log("fake_api_failure_mode", {
            "enabled": enabled,
            "message": message
        })
    
    def has_credentials(self) -> bool:
        """Always return True for fake broker"""
        return True
    
    def get_account(self) -> FakeAccount:
        """Return fake account data"""
        if self.api_failure_mode:
            raise RuntimeError(self.api_failure_message)
        
        self._logger.log("fake_get_account", {
            "equity": self.account.equity,
            "cash": self.account.cash
        })
        return self.account
    
    def get_positions(self) -> List[FakePosition]:
        """Return current fake positions"""
        if self.api_failure_mode:
            raise RuntimeError(self.api_failure_message)
        
        positions_list = list(self.positions.values())
        self._logger.log("fake_get_positions", {
            "count": len(positions_list),
            "symbols": [p.symbol for p in positions_list]
        })
        return positions_list
    
    def get_latest_quote(self, symbol: str, asset_class: str = "stock") -> Dict[str, float]:
        """Return fake quote data"""
        if self.api_failure_mode:
            raise RuntimeError(self.api_failure_message)
        
        if symbol not in self.prices:
            raise ValueError(f"No price data for {symbol}")
        
        price = self.prices[symbol]
        spread = price * 0.001  # 0.1% spread
        
        quote = {
            "bid": price - spread/2,
            "ask": price + spread/2,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        
        self._logger.log("fake_get_quote", {
            "symbol": symbol,
            "asset_class": asset_class,
            "bid": quote["bid"],
            "ask": quote["ask"]
        })
        return quote
    
    def place_market_order(self, symbol: str, side: str, qty: Optional[float] = None,
                          notional: Optional[float] = None, 
                          client_order_id: Optional[str] = None) -> Dict[str, Any]:
        """Place fake market order"""
        if self.api_failure_mode:
            raise RuntimeError(self.api_failure_message)
        
        if not qty and not notional:
            raise ValueError("Must specify qty or notional")
        
        # Generate order ID
        order_id = f"fake_order_{self.order_counter}"
        self.order_counter += 1
        
        # Get current price
        if symbol not in self.prices:
            raise ValueError(f"No price for {symbol}")
        current_price = self.prices[symbol]
        
        # Calculate quantities
        if notional and not qty:
            qty = notional / current_price
        elif qty and not notional:
            notional = qty * current_price
        
        # Create order record
        order = FakeOrder(
            id=order_id,
            symbol=symbol,
            side=side,
            qty=qty,
            notional=notional,
            status="filled",
            filled_qty=str(qty),
            filled_avg_price=str(current_price),
            created_at=datetime.utcnow().isoformat() + "Z",
            client_order_id=client_order_id
        )
        
        self.submitted_orders.append(order)
        
        # Update positions
        self._update_position_from_order(order, current_price)
        
        order_dict = {
            "id": order.id,
            "symbol": order.symbol,
            "side": order.side,
            "qty": str(order.qty) if order.qty else None,
            "notional": str(order.notional) if order.notional else None,
            "status": order.status,
            "filled_qty": order.filled_qty,
            "filled_avg_price": order.filled_avg_price,
            "created_at": order.created_at,
            "client_order_id": order.client_order_id
        }
        
        self._logger.log("fake_order_placed", order_dict)
        return order_dict
    
    def place_limit_order(self, symbol: str, side: str, qty: float, limit_price: float,
                         client_order_id: Optional[str] = None, 
                         time_in_force: str = "gtc") -> Dict[str, Any]:
        """Place fake limit order (immediately filled at limit price)"""
        if self.api_failure_mode:
            raise RuntimeError(self.api_failure_message)
        
        # For simplicity, fake broker fills limit orders immediately
        order_id = f"fake_limit_{self.order_counter}"
        self.order_counter += 1
        
        order = FakeOrder(
            id=order_id,
            symbol=symbol,
            side=side,
            qty=qty,
            status="filled",
            filled_qty=str(qty),
            filled_avg_price=str(limit_price),
            created_at=datetime.utcnow().isoformat() + "Z",
            client_order_id=client_order_id
        )
        
        self.submitted_orders.append(order)
        self._update_position_from_order(order, limit_price)
        
        order_dict = {
            "id": order.id,
            "symbol": order.symbol,
            "side": order.side,
            "qty": str(order.qty),
            "limit_price": str(limit_price),
            "status": order.status,
            "filled_qty": order.filled_qty,
            "filled_avg_price": order.filled_avg_price,
            "created_at": order.created_at,
            "client_order_id": order.client_order_id
        }
        
        self._logger.log("fake_limit_order_placed", order_dict)
        return order_dict
    
    def cancel_all_orders(self) -> int:
        """Fake cancel all orders"""
        self._logger.log("fake_cancel_all_orders", {"count": 0})
        return 0
    
    def close_all_positions(self) -> int:
        """Fake close all positions"""
        count = len(self.positions)
        self.positions.clear()
        self._logger.log("fake_close_all_positions", {"count": count})
        return count
    
    def flatten(self) -> Dict[str, Any]:
        """Fake flatten account"""
        self.cancel_all_orders()
        self.close_all_positions()
        self._logger.log("fake_flatten_complete", {})
        return {"success": True}
    
    def get_order_history(self) -> List[FakeOrder]:
        """Get all submitted orders for testing"""
        return self.submitted_orders.copy()
    
    def reset(self) -> None:
        """Reset broker state for clean testing"""
        self.submitted_orders.clear()
        self.positions.clear()
        self.order_counter = 1000
        self.api_failure_mode = False
        self._logger.log("fake_broker_reset", {})
    
    def _update_position_from_order(self, order: FakeOrder, fill_price: float) -> None:
        """Update position from filled order"""
        symbol = order.symbol
        qty = order.qty
        side_multiplier = 1 if order.side == "buy" else -1
        
        if symbol in self.positions:
            # Update existing position
            pos = self.positions[symbol]
            old_qty = pos.qty
            new_qty = old_qty + (qty * side_multiplier)
            
            if new_qty == 0:
                # Position closed
                del self.positions[symbol]
            else:
                # Update position
                pos.qty = new_qty
                pos.side = "long" if new_qty > 0 else "short"
                self._update_position_values()
        else:
            # New position
            position_id = f"pos_{symbol}_{int(time.time())}"
            self.positions[symbol] = FakePosition(
                symbol=symbol,
                qty=qty * side_multiplier,
                market_value=qty * fill_price * side_multiplier,
                unrealized_pl=0.0,
                cost_basis=qty * fill_price,
                side="long" if side_multiplier > 0 else "short",
                entry_price=fill_price,
                id=position_id
            )
    
    def _update_position_values(self) -> None:
        """Update all position market values based on current prices"""
        for pos in self.positions.values():
            if pos.symbol in self.prices:
                current_price = self.prices[pos.symbol]
                pos.market_value = abs(pos.qty) * current_price
                
                # Calculate P&L
                if pos.side == "long":
                    pos.unrealized_pl = pos.qty * (current_price - pos.entry_price)
                else:  # short
                    pos.unrealized_pl = pos.qty * (pos.entry_price - current_price)


def get_fake_broker(initial_equity: float = 10000.0) -> FakeBroker:
    """Get a fake broker instance for testing"""
    return FakeBroker(initial_equity)
