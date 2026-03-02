"""
TwentyMinute Pre-Stager Service
================================

Stages bracket orders directly with Alpaca brokerage BEFORE market open
to eliminate execution latency. Orders are placed at 6:00-6:30 AM PST
and automatically execute when price triggers are hit.

Key Features:
- Scans watchlist for gap setups during premarket (6:00 AM)
- Calculates entry, stop, and target prices based on gap analysis
- Submits bracket orders (equities) via Alpaca's OTO/OTOCO system
- For options: Uses limit orders with separate stop management
- Orders live in the brokerage ready to trigger instantly

Workflow:
1. 6:00 AM: scan_and_stage() runs - identifies gaps, calculates levels
2. 6:00-6:30 AM: Bracket orders submitted to Alpaca
3. 6:30 AM (market open): Orders execute instantly when triggers hit
4. 9:30 AM: Cleanup any unfilled staged orders
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple
from zoneinfo import ZoneInfo
from uuid import uuid4

from ..core.state import get_state, set_state, delete_state
from ..core.logging import get_logger
from ..core.config import load_bots_config
from .alpaca_client import get_alpaca_client, AlpacaClient

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        MarketOrderRequest, LimitOrderRequest, StopOrderRequest, 
        StopLimitOrderRequest, TakeProfitRequest, StopLossRequest
    )
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
    ALPACA_SDK_AVAILABLE = True
except ImportError:
    ALPACA_SDK_AVAILABLE = False
    TradingClient = None
    MarketOrderRequest = None
    LimitOrderRequest = None
    StopOrderRequest = None
    StopLimitOrderRequest = None
    TakeProfitRequest = None
    StopLossRequest = None
    OrderSide = None
    TimeInForce = None
    OrderClass = None


class StagedOrderType(Enum):
    """Type of staged order."""
    BRACKET_EQUITY = "bracket_equity"      # Full bracket order for stocks
    LIMIT_ENTRY = "limit_entry"            # Limit entry (for options/fallback)
    STOP_LIMIT_ENTRY = "stop_limit_entry"  # Stop-limit entry trigger


class StagedOrderStatus(Enum):
    """Status of a staged order."""
    PENDING = "pending"           # Calculated, not yet submitted
    SUBMITTED = "submitted"       # Submitted to Alpaca
    TRIGGERED = "triggered"       # Entry triggered, position open
    FILLED = "filled"             # Fully filled
    CANCELLED = "cancelled"       # Cancelled (manually or expired)
    EXPIRED = "expired"           # Entry window passed
    REJECTED = "rejected"         # Rejected by broker


class GapDirection(Enum):
    """Direction of overnight gap."""
    GAP_UP = "gap_up"
    GAP_DOWN = "gap_down"
    NO_GAP = "no_gap"


@dataclass
class StagedBracketOrder:
    """A bracket order staged with the brokerage."""
    id: str
    symbol: str
    
    # Gap analysis
    prev_close: float
    gap_pct: float
    gap_direction: GapDirection
    premarket_price: float
    
    # Order levels
    entry_price: float            # Entry trigger price
    stop_price: float             # Stop loss price
    target_price: float           # Take profit price
    
    # Order details
    side: str                     # "buy" or "sell"
    qty: int                      # Number of shares
    order_type: StagedOrderType
    
    # Timing
    staged_at: datetime
    entry_window_end: datetime
    
    # Status tracking
    status: StagedOrderStatus = StagedOrderStatus.PENDING
    alpaca_order_id: Optional[str] = None
    alpaca_parent_id: Optional[str] = None
    
    # Execution tracking
    fill_price: Optional[float] = None
    filled_at: Optional[datetime] = None
    
    # Reasoning
    reasoning: str = ""
    confidence: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "prev_close": self.prev_close,
            "gap_pct": self.gap_pct,
            "gap_direction": self.gap_direction.value,
            "premarket_price": self.premarket_price,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "side": self.side,
            "qty": self.qty,
            "order_type": self.order_type.value,
            "staged_at": self.staged_at.isoformat(),
            "entry_window_end": self.entry_window_end.isoformat(),
            "status": self.status.value,
            "alpaca_order_id": self.alpaca_order_id,
            "alpaca_parent_id": self.alpaca_parent_id,
            "fill_price": self.fill_price,
            "filled_at": self.filled_at.isoformat() if self.filled_at else None,
            "reasoning": self.reasoning,
            "confidence": self.confidence
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StagedBracketOrder":
        return cls(
            id=data["id"],
            symbol=data["symbol"],
            prev_close=data["prev_close"],
            gap_pct=data["gap_pct"],
            gap_direction=GapDirection(data["gap_direction"]),
            premarket_price=data["premarket_price"],
            entry_price=data["entry_price"],
            stop_price=data["stop_price"],
            target_price=data["target_price"],
            side=data["side"],
            qty=data["qty"],
            order_type=StagedOrderType(data["order_type"]),
            staged_at=datetime.fromisoformat(data["staged_at"]),
            entry_window_end=datetime.fromisoformat(data["entry_window_end"]),
            status=StagedOrderStatus(data["status"]),
            alpaca_order_id=data.get("alpaca_order_id"),
            alpaca_parent_id=data.get("alpaca_parent_id"),
            fill_price=data.get("fill_price"),
            filled_at=datetime.fromisoformat(data["filled_at"]) if data.get("filled_at") else None,
            reasoning=data.get("reasoning", ""),
            confidence=data.get("confidence", 0.0)
        )


@dataclass
class PreStagerConfig:
    """Configuration for the TwentyMinute pre-stager."""
    enabled: bool = True
    
    # Timing (PST) - Prestage orders 2-15 minutes before market open
    scan_start_time: str = "06:00"         # When to scan for gaps
    staging_window_start: str = "06:15"    # Start submitting orders (15 mins before open)
    staging_window_end: str = "06:28"      # Stop submitting orders (2 mins before open)
    entry_window_end: str = "07:00"        # Cancel unfilled entries after
    cleanup_time: str = "09:30"            # Clean up all staged orders
    
    # Gap thresholds
    min_gap_pct: float = 0.5           # Minimum gap to consider (%)
    max_gap_pct: float = 5.0           # Maximum gap (avoid earnings/news)
    ideal_gap_pct: float = 1.0         # Ideal gap size
    
    # Entry calculation
    entry_buffer_pct: float = 0.10     # Buffer beyond premarket price
    
    # Risk parameters
    stop_loss_pct: float = 0.35        # Stop loss from entry (HIGH-WIN-RATE)
    take_profit_pct: float = 0.50      # Take profit from entry
    
    # Position sizing
    max_position_usd: float = 2000.0   # Maximum position size
    min_position_usd: float = 200.0    # Minimum position size
    risk_per_trade_pct: float = 1.0    # Risk per trade as % of account
    
    # Limits
    max_staged_orders: int = 3         # Max concurrent staged orders
    max_orders_per_symbol: int = 1     # Max orders per symbol
    
    # Watchlist (from TwentyMinute config)
    watchlist: List[str] = field(default_factory=lambda: [
        "SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "META"
    ])


class TwentyMinutePreStager:
    """
    Pre-stages bracket orders for TwentyMinute bot.
    
    Submits orders to Alpaca brokerage BEFORE market open so they
    execute instantly when price triggers are hit, eliminating
    the typical 50-200ms execution latency.
    
    Usage:
        prestager = TwentyMinutePreStager()
        
        # At 6:00 AM PST
        staged_orders = prestager.scan_and_stage()
        
        # Check status
        status = prestager.get_staged_orders()
        
        # At 9:30 AM PST (cleanup)
        prestager.cleanup_expired_orders()
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._logger = get_logger()
        self._alpaca = get_alpaca_client()
        self._config = self._load_config()
        self._staged_orders: Dict[str, StagedBracketOrder] = {}
        self._pst = ZoneInfo("America/Los_Angeles")
        
        # Load persisted staged orders
        self._load_staged_orders()
        self._initialized = True
        
        self._logger.log("twentymin_prestager_init", {
            "watchlist_count": len(self._config.watchlist),
            "staged_count": len(self._staged_orders),
            "enabled": self._config.enabled
        })
    
    def _load_config(self) -> PreStagerConfig:
        """Load configuration from bots.yaml."""
        config = PreStagerConfig()
        
        try:
            bots_config = load_bots_config()
            tm_config = bots_config.get("twentyminute_bot", {})
            
            # Load watchlist from TwentyMinute config
            config.watchlist = tm_config.get("tickers", config.watchlist)
            
            # Load pre-staging specific config if present
            prestage_config = tm_config.get("prestaging", {})
            if prestage_config:
                config.enabled = prestage_config.get("enabled", config.enabled)
                config.scan_start_time = prestage_config.get("scan_start_time", config.scan_start_time)
                config.staging_window_start = prestage_config.get("staging_window_start", config.staging_window_start)
                config.staging_window_end = prestage_config.get("staging_window_end", config.staging_window_end)
                config.entry_window_end = prestage_config.get("entry_window_end", config.entry_window_end)
                config.min_gap_pct = prestage_config.get("min_gap_pct", config.min_gap_pct)
                config.max_gap_pct = prestage_config.get("max_gap_pct", config.max_gap_pct)
                config.stop_loss_pct = prestage_config.get("stop_loss_pct", config.stop_loss_pct)
                config.take_profit_pct = prestage_config.get("take_profit_pct", config.take_profit_pct)
                config.max_staged_orders = prestage_config.get("max_staged_orders", config.max_staged_orders)
                config.max_position_usd = prestage_config.get("max_position_usd", config.max_position_usd)
            
            # Also check exits config for stop/target
            exits_config = tm_config.get("exits", {})
            if exits_config:
                config.stop_loss_pct = exits_config.get("stop_loss_pct", config.stop_loss_pct)
                config.take_profit_pct = exits_config.get("take_profit_pct", config.take_profit_pct)
        
        except Exception as e:
            self._logger.error(f"Failed to load prestager config: {e}")
        
        return config
    
    def _load_staged_orders(self):
        """Load staged orders from persistent state."""
        try:
            data = get_state("twentymin_staged_orders")
            if data:
                for order_id, order_data in data.items():
                    self._staged_orders[order_id] = StagedBracketOrder.from_dict(order_data)
                
                self._logger.log("twentymin_prestager_loaded", {
                    "orders_loaded": len(self._staged_orders)
                })
        except Exception as e:
            self._logger.error(f"Failed to load staged orders: {e}")
    
    def _save_staged_orders(self):
        """Persist staged orders to state."""
        try:
            data = {
                order_id: order.to_dict()
                for order_id, order in self._staged_orders.items()
            }
            set_state("twentymin_staged_orders", data)
        except Exception as e:
            self._logger.error(f"Failed to save staged orders: {e}")
    
    def scan_and_stage(self) -> List[StagedBracketOrder]:
        """
        Main entry point: Scan for gaps and stage bracket orders.
        
        Staging window: 2-15 minutes before market open (6:15-6:28 AM PST).
        Orders are submitted to Alpaca during this window.
        
        Returns:
            List of newly staged orders
        """
        if not self._config.enabled:
            self._logger.log("twentymin_prestager_disabled", {})
            return []
        
        if not ALPACA_SDK_AVAILABLE:
            self._logger.error("Alpaca SDK not available for prestaging")
            return []
        
        now_pst = datetime.now(self._pst)
        new_orders = []
        
        # Parse staging window times
        staging_start_parts = self._config.staging_window_start.split(":")
        staging_end_parts = self._config.staging_window_end.split(":")
        
        staging_start = now_pst.replace(
            hour=int(staging_start_parts[0]),
            minute=int(staging_start_parts[1]),
            second=0, microsecond=0
        )
        staging_end = now_pst.replace(
            hour=int(staging_end_parts[0]),
            minute=int(staging_end_parts[1]),
            second=0, microsecond=0
        )
        
        # Check if we're within the staging window (2-15 mins before market open)
        if now_pst < staging_start:
            self._logger.log("twentymin_prestager_too_early", {
                "time_pst": now_pst.strftime("%H:%M:%S"),
                "staging_window_start": self._config.staging_window_start,
                "minutes_until_window": (staging_start - now_pst).total_seconds() / 60
            })
            return []
        
        if now_pst > staging_end:
            self._logger.log("twentymin_prestager_window_closed", {
                "time_pst": now_pst.strftime("%H:%M:%S"),
                "staging_window_end": self._config.staging_window_end,
                "reason": "Orders must be staged 2-15 mins before market open"
            })
            return []
        
        self._logger.log("twentymin_prestager_scan_start", {
            "time_pst": now_pst.strftime("%H:%M:%S"),
            "staging_window": f"{self._config.staging_window_start}-{self._config.staging_window_end}",
            "mins_until_window_close": (staging_end - now_pst).total_seconds() / 60,
            "watchlist_count": len(self._config.watchlist),
            "existing_orders": len(self._staged_orders)
        })
        
        # Clean up expired orders from previous session
        self._expire_old_orders()
        
        # Check if we have room for more orders
        active_orders = [o for o in self._staged_orders.values() 
                        if o.status in (StagedOrderStatus.PENDING, StagedOrderStatus.SUBMITTED)]
        
        if len(active_orders) >= self._config.max_staged_orders:
            self._logger.log("twentymin_prestager_at_limit", {
                "active_orders": len(active_orders),
                "max_orders": self._config.max_staged_orders
            })
            return []
        
        # Scan each symbol for gap setups
        for symbol in self._config.watchlist:
            try:
                # Check if we already have an order for this symbol
                existing = [o for o in self._staged_orders.values() 
                           if o.symbol == symbol and o.status in (StagedOrderStatus.PENDING, StagedOrderStatus.SUBMITTED)]
                if len(existing) >= self._config.max_orders_per_symbol:
                    continue
                
                # Analyze gap and create order if valid
                order = self._analyze_and_create_order(symbol, now_pst)
                if order:
                    self._staged_orders[order.id] = order
                    new_orders.append(order)
                    
                    # Submit to Alpaca immediately
                    self._submit_order_to_alpaca(order)
                    
                    self._logger.log("twentymin_prestager_order_staged", {
                        "id": order.id,
                        "symbol": order.symbol,
                        "side": order.side,
                        "entry_price": order.entry_price,
                        "stop_price": order.stop_price,
                        "target_price": order.target_price,
                        "gap_pct": order.gap_pct,
                        "qty": order.qty,
                        "status": order.status.value,
                        "alpaca_order_id": order.alpaca_order_id
                    })
                    
                    # Check limit
                    if len([o for o in self._staged_orders.values() 
                           if o.status in (StagedOrderStatus.PENDING, StagedOrderStatus.SUBMITTED)]) >= self._config.max_staged_orders:
                        break
                        
            except Exception as e:
                self._logger.error(f"Failed to analyze {symbol}: {e}")
        
        self._save_staged_orders()
        
        self._logger.log("twentymin_prestager_scan_complete", {
            "new_orders": len(new_orders),
            "total_staged": len(self._staged_orders)
        })
        
        return new_orders
    
    def _analyze_and_create_order(self, symbol: str, now: datetime) -> Optional[StagedBracketOrder]:
        """
        Analyze a symbol for gap setup and create bracket order if valid.
        """
        try:
            # Get current quote (premarket)
            quote = self._alpaca.get_latest_quote(symbol)
            if not quote or quote.get("mid", 0) <= 0:
                return None
            
            premarket_price = quote.get("mid") or ((quote.get("bid", 0) + quote.get("ask", 0)) / 2)
            if premarket_price <= 0:
                return None
            
            # Get previous close
            bars = self._alpaca.get_stock_bars(symbol, "1Day", limit=2)
            if not bars or len(bars) < 2:
                return None
            
            # Handle both dict and Bar object formats
            bar_obj = bars[-2]
            if hasattr(bar_obj, 'close'):
                prev_close = float(bar_obj.close)
            elif isinstance(bar_obj, dict):
                prev_close = bar_obj.get("close", 0)
            else:
                prev_close = 0
            if prev_close <= 0:
                return None
            
            # Calculate gap
            gap_pct = ((premarket_price - prev_close) / prev_close) * 100
            
            # Check gap is within valid range
            if abs(gap_pct) < self._config.min_gap_pct:
                return None
            if abs(gap_pct) > self._config.max_gap_pct:
                self._logger.log("twentymin_prestager_gap_too_large", {
                    "symbol": symbol,
                    "gap_pct": gap_pct,
                    "max_gap_pct": self._config.max_gap_pct
                })
                return None
            
            # Determine gap direction and trade side
            if gap_pct > 0:
                gap_direction = GapDirection.GAP_UP
                side = "buy"  # Trade with the gap (gap continuation)
                # Entry above premarket high
                entry_price = round(premarket_price * (1 + self._config.entry_buffer_pct / 100), 2)
                stop_price = round(entry_price * (1 - self._config.stop_loss_pct / 100), 2)
                target_price = round(entry_price * (1 + self._config.take_profit_pct / 100), 2)
            else:
                gap_direction = GapDirection.GAP_DOWN
                side = "sell"  # Short gap down continuation (if allowed)
                # For now, only support long trades
                side = "buy"  # Actually trade the gap fade (bounce)
                entry_price = round(premarket_price * (1 - self._config.entry_buffer_pct / 100), 2)
                stop_price = round(entry_price * (1 - self._config.stop_loss_pct / 100), 2)
                target_price = round(entry_price * (1 + self._config.take_profit_pct / 100), 2)
            
            # Calculate position size
            account = self._alpaca.get_account()
            risk_budget = account.equity * (self._config.risk_per_trade_pct / 100)
            
            # Position size based on stop distance
            stop_distance_pct = abs(entry_price - stop_price) / entry_price * 100
            if stop_distance_pct <= 0:
                return None
            
            position_value = min(
                risk_budget / (stop_distance_pct / 100),
                self._config.max_position_usd
            )
            position_value = max(position_value, self._config.min_position_usd)
            
            qty = int(position_value / entry_price)
            if qty < 1:
                return None
            
            # Calculate entry window end
            entry_window_end = now.replace(
                hour=int(self._config.entry_window_end.split(":")[0]),
                minute=int(self._config.entry_window_end.split(":")[1]),
                second=0, microsecond=0
            )
            
            # Create order
            order_id = f"tm_{symbol}_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
            
            # Calculate confidence based on gap size
            confidence = min(0.95, 0.6 + abs(gap_pct) * 0.1)
            
            order = StagedBracketOrder(
                id=order_id,
                symbol=symbol,
                prev_close=prev_close,
                gap_pct=round(gap_pct, 2),
                gap_direction=gap_direction,
                premarket_price=round(premarket_price, 2),
                entry_price=entry_price,
                stop_price=stop_price,
                target_price=target_price,
                side=side,
                qty=qty,
                order_type=StagedOrderType.BRACKET_EQUITY,
                staged_at=now,
                entry_window_end=entry_window_end,
                status=StagedOrderStatus.PENDING,
                reasoning=f"Gap {gap_direction.value} {abs(gap_pct):.1f}% - entry at ${entry_price:.2f}, stop ${stop_price:.2f}, target ${target_price:.2f}",
                confidence=confidence
            )
            
            return order
            
        except Exception as e:
            self._logger.error(f"Error analyzing {symbol}: {e}")
            return None
    
    def _submit_order_to_alpaca(self, order: StagedBracketOrder) -> bool:
        """
        Submit a bracket order to Alpaca.
        
        Uses OTOCO (bracket) order type with stop-limit protection.
        """
        if not ALPACA_SDK_AVAILABLE:
            self._logger.error("Alpaca SDK not available")
            order.status = StagedOrderStatus.REJECTED
            return False
        
        try:
            trading_client = self._alpaca._trading_client
            if not trading_client:
                self._logger.error("Trading client not initialized")
                order.status = StagedOrderStatus.REJECTED
                return False
            
            # Create bracket order with stop-limit entry
            # Entry: Stop-limit order (triggers when price hits entry_price)
            # Take profit: Limit order at target_price
            # Stop loss: Stop-limit order at stop_price
            
            order_side = OrderSide.BUY if order.side == "buy" else OrderSide.SELL
            
            # For gap-up continuation: use stop-limit buy order
            # Entry triggers when price rises to entry_price
            if order.gap_direction == GapDirection.GAP_UP:
                # Stop-limit buy: stop at entry_price, limit slightly higher
                order_request = StopLimitOrderRequest(
                    symbol=order.symbol,
                    qty=order.qty,
                    side=order_side,
                    time_in_force=TimeInForce.GTC,
                    stop_price=order.entry_price,
                    limit_price=round(order.entry_price * 1.002, 2),  # 0.2% buffer
                    order_class=OrderClass.BRACKET,
                    take_profit=TakeProfitRequest(limit_price=order.target_price),
                    stop_loss=StopLossRequest(
                        stop_price=order.stop_price,
                        limit_price=round(order.stop_price * 0.998, 2)  # 0.2% buffer
                    ),
                    client_order_id=order.id
                )
            else:
                # Gap-down fade: limit buy order at entry_price
                order_request = LimitOrderRequest(
                    symbol=order.symbol,
                    qty=order.qty,
                    side=order_side,
                    time_in_force=TimeInForce.GTC,
                    limit_price=order.entry_price,
                    order_class=OrderClass.BRACKET,
                    take_profit=TakeProfitRequest(limit_price=order.target_price),
                    stop_loss=StopLossRequest(
                        stop_price=order.stop_price,
                        limit_price=round(order.stop_price * 0.998, 2)
                    ),
                    client_order_id=order.id
                )
            
            # Submit to Alpaca
            alpaca_order = trading_client.submit_order(order_request)
            
            # Update our order with Alpaca response
            order.alpaca_order_id = str(alpaca_order.id)
            order.status = StagedOrderStatus.SUBMITTED
            
            self._logger.log("twentymin_prestager_order_submitted", {
                "id": order.id,
                "symbol": order.symbol,
                "alpaca_order_id": order.alpaca_order_id,
                "status": str(alpaca_order.status),
                "entry_price": order.entry_price,
                "qty": order.qty
            })
            
            return True
            
        except Exception as e:
            self._logger.error(f"Failed to submit bracket order for {order.symbol}: {e}")
            order.status = StagedOrderStatus.REJECTED
            order.reasoning += f" | REJECTED: {str(e)}"
            return False
    
    def _expire_old_orders(self):
        """Expire orders from previous sessions."""
        now = datetime.now(self._pst)
        today = now.date()
        
        expired_ids = []
        for order_id, order in self._staged_orders.items():
            # Expire orders from previous days
            if order.staged_at.date() < today:
                order.status = StagedOrderStatus.EXPIRED
                expired_ids.append(order_id)
            # Expire orders past their entry window
            elif order.status == StagedOrderStatus.SUBMITTED and now > order.entry_window_end:
                order.status = StagedOrderStatus.EXPIRED
                expired_ids.append(order_id)
                # Cancel in Alpaca
                if order.alpaca_order_id:
                    try:
                        self._alpaca._trading_client.cancel_order_by_id(order.alpaca_order_id)
                    except Exception:
                        pass
        
        if expired_ids:
            self._logger.log("twentymin_prestager_orders_expired", {
                "expired_count": len(expired_ids),
                "ids": expired_ids
            })
    
    def cleanup_expired_orders(self):
        """
        Clean up all unfilled staged orders.
        Call at end of trading session (9:30 AM PST).
        """
        now = datetime.now(self._pst)
        cancelled_count = 0
        
        for order_id, order in self._staged_orders.items():
            if order.status == StagedOrderStatus.SUBMITTED:
                order.status = StagedOrderStatus.EXPIRED
                cancelled_count += 1
                
                # Cancel in Alpaca
                if order.alpaca_order_id:
                    try:
                        self._alpaca._trading_client.cancel_order_by_id(order.alpaca_order_id)
                        self._logger.log("twentymin_prestager_order_cancelled", {
                            "id": order.id,
                            "symbol": order.symbol,
                            "alpaca_order_id": order.alpaca_order_id
                        })
                    except Exception as e:
                        self._logger.error(f"Failed to cancel order {order.alpaca_order_id}: {e}")
        
        self._save_staged_orders()
        
        self._logger.log("twentymin_prestager_cleanup_complete", {
            "cancelled_count": cancelled_count,
            "total_orders": len(self._staged_orders)
        })
    
    def get_staged_orders(self, status: Optional[StagedOrderStatus] = None) -> List[StagedBracketOrder]:
        """Get all staged orders, optionally filtered by status."""
        if status:
            return [o for o in self._staged_orders.values() if o.status == status]
        return list(self._staged_orders.values())
    
    def sync_order_status(self):
        """
        Sync order status with Alpaca.
        Call periodically to check for fills and updates.
        """
        if not self._alpaca._trading_client:
            return
        
        for order_id, order in self._staged_orders.items():
            if order.status != StagedOrderStatus.SUBMITTED:
                continue
            
            if not order.alpaca_order_id:
                continue
            
            try:
                alpaca_order = self._alpaca._trading_client.get_order_by_id(order.alpaca_order_id)
                
                status_str = str(alpaca_order.status).lower()
                
                if status_str == "filled":
                    order.status = StagedOrderStatus.FILLED
                    order.fill_price = float(alpaca_order.filled_avg_price) if alpaca_order.filled_avg_price else None
                    order.filled_at = datetime.now(self._pst)
                    
                    self._logger.log("twentymin_prestager_order_filled", {
                        "id": order.id,
                        "symbol": order.symbol,
                        "fill_price": order.fill_price,
                        "qty": order.qty
                    })
                
                elif status_str in ("canceled", "cancelled", "expired"):
                    order.status = StagedOrderStatus.CANCELLED
                
                elif status_str == "rejected":
                    order.status = StagedOrderStatus.REJECTED
                
            except Exception as e:
                self._logger.error(f"Failed to sync order {order.alpaca_order_id}: {e}")
        
        self._save_staged_orders()
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel a specific staged order."""
        if order_id not in self._staged_orders:
            return False
        
        order = self._staged_orders[order_id]
        
        if order.alpaca_order_id:
            try:
                self._alpaca._trading_client.cancel_order_by_id(order.alpaca_order_id)
            except Exception as e:
                self._logger.error(f"Failed to cancel order in Alpaca: {e}")
        
        order.status = StagedOrderStatus.CANCELLED
        self._save_staged_orders()
        
        self._logger.log("twentymin_prestager_order_cancelled", {
            "id": order_id,
            "symbol": order.symbol
        })
        
        return True
    
    def is_symbol_staged(self, symbol: str) -> bool:
        """Check if a symbol already has an active staged order."""
        active_statuses = (StagedOrderStatus.PENDING, StagedOrderStatus.SUBMITTED)
        return any(
            o.symbol == symbol and o.status in active_statuses
            for o in self._staged_orders.values()
        )


# Singleton accessor
_prestager_instance: Optional[TwentyMinutePreStager] = None


def get_twentymin_prestager() -> TwentyMinutePreStager:
    """Get the TwentyMinute pre-stager singleton."""
    global _prestager_instance
    if _prestager_instance is None:
        _prestager_instance = TwentyMinutePreStager()
    return _prestager_instance
