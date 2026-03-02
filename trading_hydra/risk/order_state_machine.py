"""
=============================================================================
Order State Machine - Deterministic Order Lifecycle Tracking
=============================================================================

Tracks every order from submission to terminal state with reconciliation.

Order lifecycle:
  PENDING → SUBMITTED → ACKED → PARTIAL_FILL → FILLED/CANCELLED/REJECTED

Special states:
  UNKNOWN - Order status cannot be determined (requires investigation)
  EXPIRED - Order expired before fill

Philosophy:
- Every order must reach a terminal state
- Unknown states trigger investigation, not silent failure
- Idempotent: retries don't create duplicate orders
- Durable: state persisted to SQLite

Usage:
    from src.trading_hydra.risk.order_state_machine import get_order_state_machine
    
    osm = get_order_state_machine()
    
    # Before submitting
    order_id = osm.create_order(request)
    
    # After broker submission
    osm.mark_submitted(order_id, broker_order_id)
    
    # On broker ack
    osm.mark_acknowledged(order_id)
    
    # On fill
    osm.mark_filled(order_id, fill_price, fill_qty)
    
    # Reconciliation loop
    osm.reconcile_pending_orders(broker_client)
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from enum import Enum
import json

from ..core.logging import get_logger
from ..core.state import get_state, set_state


class OrderState(Enum):
    """Order lifecycle states."""
    PENDING = "pending"           # Created, not yet submitted
    SUBMITTED = "submitted"       # Sent to broker
    ACKNOWLEDGED = "acknowledged" # Broker confirmed receipt
    PARTIAL_FILL = "partial_fill" # Partially filled
    FILLED = "filled"             # Fully filled (terminal)
    CANCELLED = "cancelled"       # Cancelled (terminal)
    REJECTED = "rejected"         # Broker rejected (terminal)
    EXPIRED = "expired"           # Time expired (terminal)
    UNKNOWN = "unknown"           # Cannot determine status (needs investigation)


@dataclass
class OrderRecord:
    """Complete order record for audit trail."""
    order_id: str                  # Internal order ID
    symbol: str
    bot_id: str
    side: str                      # "buy" or "sell"
    asset_class: str               # "equity", "option", "crypto"
    qty: float
    order_type: str                # "market", "limit", "stop_limit"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    
    state: OrderState = OrderState.PENDING
    broker_order_id: Optional[str] = None
    
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    submitted_at: Optional[str] = None
    acknowledged_at: Optional[str] = None
    filled_at: Optional[str] = None
    terminal_at: Optional[str] = None
    
    fill_price: Optional[float] = None
    filled_qty: float = 0.0
    remaining_qty: float = 0.0
    
    expected_price: Optional[float] = None  # For slippage tracking
    
    rejection_reason: Optional[str] = None
    cancel_reason: Optional[str] = None
    
    retry_count: int = 0
    max_retries: int = 3
    last_retry_at: Optional[str] = None
    
    reconcile_attempts: int = 0
    last_reconcile_at: Optional[str] = None
    
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def is_terminal(self) -> bool:
        """Check if order is in a terminal state."""
        return self.state in [
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.REJECTED,
            OrderState.EXPIRED
        ]
    
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OrderRecord':
        if "state" in data and isinstance(data["state"], str):
            data["state"] = OrderState(data["state"])
        return cls(**data)


class OrderStateMachine:
    """
    Deterministic order lifecycle tracking.
    
    Ensures every order reaches a terminal state with full audit trail.
    Handles retries, reconciliation, and unknown state investigation.
    """
    
    STATE_KEY_PREFIX = "osm.order."
    PENDING_ORDERS_KEY = "osm.pending_orders"
    
    MAX_PENDING_AGE_MINUTES = 30
    MAX_UNKNOWN_AGE_MINUTES = 60
    
    def __init__(self):
        self._logger = get_logger()
        self._logger.log("order_state_machine_init", {
            "max_pending_age_min": self.MAX_PENDING_AGE_MINUTES,
            "max_unknown_age_min": self.MAX_UNKNOWN_AGE_MINUTES
        })
    
    def _get_order_key(self, order_id: str) -> str:
        """Get state key for an order."""
        return f"{self.STATE_KEY_PREFIX}{order_id}"
    
    def _save_order(self, order: OrderRecord) -> None:
        """Persist order to state store."""
        key = self._get_order_key(order.order_id)
        set_state(key, order.to_dict())
    
    def _load_order(self, order_id: str) -> Optional[OrderRecord]:
        """Load order from state store."""
        key = self._get_order_key(order_id)
        data = get_state(key)
        if data:
            return OrderRecord.from_dict(data)
        return None
    
    def _add_to_pending(self, order_id: str) -> None:
        """Add order to pending list."""
        pending = get_state(self.PENDING_ORDERS_KEY) or []
        if order_id not in pending:
            pending.append(order_id)
            set_state(self.PENDING_ORDERS_KEY, pending)
    
    def _remove_from_pending(self, order_id: str) -> None:
        """Remove order from pending list."""
        pending = get_state(self.PENDING_ORDERS_KEY) or []
        if order_id in pending:
            pending.remove(order_id)
            set_state(self.PENDING_ORDERS_KEY, pending)
    
    def _get_pending_order_ids(self) -> List[str]:
        """Get all pending order IDs."""
        return get_state(self.PENDING_ORDERS_KEY) or []
    
    def create_order(
        self,
        symbol: str,
        bot_id: str,
        side: str,
        asset_class: str,
        qty: float,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        expected_price: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        signal_id: Optional[str] = None
    ) -> str:
        """
        Create a new order record in PENDING state.
        
        Returns:
            order_id: Unique order identifier
        """
        from ..core.state import generate_client_order_id
        import uuid
        
        effective_signal_id = signal_id or f"osm_{uuid.uuid4().hex[:8]}"
        order_id = generate_client_order_id(bot_id, symbol, effective_signal_id)
        
        order = OrderRecord(
            order_id=order_id,
            symbol=symbol,
            bot_id=bot_id,
            side=side,
            asset_class=asset_class,
            qty=qty,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            expected_price=expected_price,
            remaining_qty=qty,
            state=OrderState.PENDING,
            metadata=metadata or {}
        )
        
        self._save_order(order)
        self._add_to_pending(order_id)
        
        self._logger.log("osm_order_created", {
            "order_id": order_id,
            "symbol": symbol,
            "bot_id": bot_id,
            "side": side,
            "qty": qty,
            "order_type": order_type,
            "state": OrderState.PENDING.value
        })
        
        return order_id
    
    def mark_submitted(self, order_id: str, broker_order_id: str) -> bool:
        """
        Mark order as submitted to broker.
        
        Args:
            order_id: Internal order ID
            broker_order_id: Broker's order ID
            
        Returns:
            True if state transition successful
        """
        order = self._load_order(order_id)
        if not order:
            self._logger.error(f"[OSM] Order not found: {order_id}")
            return False
        
        if order.state != OrderState.PENDING:
            self._logger.warn(f"[OSM] Invalid transition: {order.state.value} → submitted")
            return False
        
        order.state = OrderState.SUBMITTED
        order.broker_order_id = broker_order_id
        order.submitted_at = datetime.utcnow().isoformat() + "Z"
        
        self._save_order(order)
        
        self._logger.log("osm_order_submitted", {
            "order_id": order_id,
            "broker_order_id": broker_order_id,
            "symbol": order.symbol
        })
        
        return True
    
    def mark_acknowledged(self, order_id: str) -> bool:
        """Mark order as acknowledged by broker."""
        order = self._load_order(order_id)
        if not order:
            self._logger.error(f"[OSM] Order not found: {order_id}")
            return False
        
        if order.state not in [OrderState.SUBMITTED, OrderState.PENDING]:
            self._logger.warn(f"[OSM] Invalid transition: {order.state.value} → acknowledged")
            return False
        
        order.state = OrderState.ACKNOWLEDGED
        order.acknowledged_at = datetime.utcnow().isoformat() + "Z"
        
        self._save_order(order)
        
        self._logger.log("osm_order_acknowledged", {
            "order_id": order_id,
            "broker_order_id": order.broker_order_id,
            "symbol": order.symbol
        })
        
        return True
    
    def mark_partial_fill(
        self, 
        order_id: str, 
        fill_price: float, 
        fill_qty: float
    ) -> bool:
        """Record a partial fill."""
        order = self._load_order(order_id)
        if not order:
            self._logger.error(f"[OSM] Order not found: {order_id}")
            return False
        
        if order.state not in [OrderState.SUBMITTED, OrderState.ACKNOWLEDGED, OrderState.PARTIAL_FILL]:
            self._logger.warn(f"[OSM] Invalid transition: {order.state.value} → partial_fill")
            return False
        
        order.state = OrderState.PARTIAL_FILL
        order.filled_qty += fill_qty
        order.remaining_qty = order.qty - order.filled_qty
        
        if order.fill_price is None:
            order.fill_price = fill_price
        else:
            total_filled = order.filled_qty
            prev_filled = total_filled - fill_qty
            order.fill_price = (
                (order.fill_price * prev_filled + fill_price * fill_qty) / total_filled
            )
        
        self._save_order(order)
        
        self._logger.log("osm_order_partial_fill", {
            "order_id": order_id,
            "fill_price": fill_price,
            "fill_qty": fill_qty,
            "total_filled": order.filled_qty,
            "remaining": order.remaining_qty,
            "symbol": order.symbol
        })
        
        return True
    
    def mark_filled(
        self, 
        order_id: str, 
        fill_price: Optional[float] = None, 
        fill_qty: Optional[float] = None
    ) -> bool:
        """Mark order as fully filled (terminal state)."""
        order = self._load_order(order_id)
        if not order:
            self._logger.error(f"[OSM] Order not found: {order_id}")
            return False
        
        now = datetime.utcnow().isoformat() + "Z"
        
        order.state = OrderState.FILLED
        order.filled_at = now
        order.terminal_at = now
        
        if fill_qty is not None:
            order.filled_qty = fill_qty
        else:
            order.filled_qty = order.qty
        
        order.remaining_qty = 0.0
        
        if fill_price is not None:
            order.fill_price = fill_price
        
        self._save_order(order)
        self._remove_from_pending(order_id)
        
        self._logger.log("osm_order_filled", {
            "order_id": order_id,
            "broker_order_id": order.broker_order_id,
            "symbol": order.symbol,
            "fill_price": order.fill_price,
            "fill_qty": order.filled_qty,
            "expected_price": order.expected_price,
            "slippage_pct": self._calc_slippage_pct(order)
        })
        
        return True
    
    def mark_cancelled(self, order_id: str, reason: str = "") -> bool:
        """Mark order as cancelled (terminal state)."""
        order = self._load_order(order_id)
        if not order:
            self._logger.error(f"[OSM] Order not found: {order_id}")
            return False
        
        now = datetime.utcnow().isoformat() + "Z"
        
        order.state = OrderState.CANCELLED
        order.terminal_at = now
        order.cancel_reason = reason
        
        self._save_order(order)
        self._remove_from_pending(order_id)
        
        self._logger.log("osm_order_cancelled", {
            "order_id": order_id,
            "broker_order_id": order.broker_order_id,
            "symbol": order.symbol,
            "reason": reason,
            "filled_qty": order.filled_qty
        })
        
        return True
    
    def mark_rejected(self, order_id: str, reason: str = "") -> bool:
        """Mark order as rejected (terminal state)."""
        order = self._load_order(order_id)
        if not order:
            self._logger.error(f"[OSM] Order not found: {order_id}")
            return False
        
        now = datetime.utcnow().isoformat() + "Z"
        
        order.state = OrderState.REJECTED
        order.terminal_at = now
        order.rejection_reason = reason
        
        self._save_order(order)
        self._remove_from_pending(order_id)
        
        self._logger.log("osm_order_rejected", {
            "order_id": order_id,
            "broker_order_id": order.broker_order_id,
            "symbol": order.symbol,
            "reason": reason
        })
        
        return True
    
    def mark_unknown(self, order_id: str, reason: str = "") -> bool:
        """Mark order as unknown state (requires investigation)."""
        order = self._load_order(order_id)
        if not order:
            self._logger.error(f"[OSM] Order not found: {order_id}")
            return False
        
        order.state = OrderState.UNKNOWN
        order.reconcile_attempts += 1
        order.last_reconcile_at = datetime.utcnow().isoformat() + "Z"
        
        self._save_order(order)
        
        self._logger.log("osm_order_unknown", {
            "order_id": order_id,
            "broker_order_id": order.broker_order_id,
            "symbol": order.symbol,
            "reason": reason,
            "reconcile_attempts": order.reconcile_attempts
        })
        
        return True
    
    def _calc_slippage_pct(self, order: OrderRecord) -> Optional[float]:
        """Calculate slippage percentage."""
        if order.expected_price and order.fill_price and order.expected_price > 0:
            diff = order.fill_price - order.expected_price
            if order.side == "sell":
                diff = -diff
            return round((diff / order.expected_price) * 100, 4)
        return None
    
    def get_order(self, order_id: str) -> Optional[OrderRecord]:
        """Get order by ID."""
        return self._load_order(order_id)
    
    def get_pending_orders(self) -> List[OrderRecord]:
        """Get all non-terminal orders."""
        pending_ids = self._get_pending_order_ids()
        orders = []
        for order_id in pending_ids:
            order = self._load_order(order_id)
            if order and not order.is_terminal():
                orders.append(order)
        return orders
    
    def reconcile_pending_orders(self, broker_client: Any) -> Dict[str, Any]:
        """
        Reconcile pending orders with broker state.
        
        Queries broker for current order status and updates internal state.
        Handles unknown states and stale orders.
        
        Args:
            broker_client: Alpaca client with get_order() method
            
        Returns:
            Reconciliation summary
        """
        pending = self.get_pending_orders()
        
        results = {
            "checked": 0,
            "filled": 0,
            "cancelled": 0,
            "rejected": 0,
            "unknown": 0,
            "still_pending": 0,
            "errors": []
        }
        
        for order in pending:
            results["checked"] += 1
            
            try:
                if not order.broker_order_id:
                    age_minutes = self._get_order_age_minutes(order)
                    if age_minutes > self.MAX_PENDING_AGE_MINUTES:
                        self.mark_cancelled(order.order_id, "stale_never_submitted")
                        results["cancelled"] += 1
                    else:
                        results["still_pending"] += 1
                    continue
                
                broker_status = broker_client.get_order(order.broker_order_id)
                
                if broker_status is None:
                    self.mark_unknown(order.order_id, "broker_order_not_found")
                    results["unknown"] += 1
                    continue
                
                status = broker_status.get("status", "").lower()
                
                if status == "filled":
                    fill_price = float(broker_status.get("filled_avg_price", 0))
                    fill_qty = float(broker_status.get("filled_qty", order.qty))
                    self.mark_filled(order.order_id, fill_price, fill_qty)
                    results["filled"] += 1
                    
                elif status in ["cancelled", "canceled"]:
                    self.mark_cancelled(order.order_id, "broker_cancelled")
                    results["cancelled"] += 1
                    
                elif status == "rejected":
                    reason = broker_status.get("reject_reason", "unknown")
                    self.mark_rejected(order.order_id, reason)
                    results["rejected"] += 1
                    
                elif status == "expired":
                    order_obj = self._load_order(order.order_id)
                    if order_obj:
                        order_obj.state = OrderState.EXPIRED
                        order_obj.terminal_at = datetime.utcnow().isoformat() + "Z"
                        self._save_order(order_obj)
                        self._remove_from_pending(order.order_id)
                    results["cancelled"] += 1
                    
                elif status in ["new", "accepted", "pending_new", "partially_filled"]:
                    if status == "partially_filled":
                        fill_price = float(broker_status.get("filled_avg_price", 0))
                        fill_qty = float(broker_status.get("filled_qty", 0))
                        self.mark_partial_fill(order.order_id, fill_price, fill_qty)
                    else:
                        self.mark_acknowledged(order.order_id)
                    results["still_pending"] += 1
                    
                else:
                    self.mark_unknown(order.order_id, f"unexpected_status_{status}")
                    results["unknown"] += 1
                    
            except Exception as e:
                results["errors"].append(f"{order.order_id}: {str(e)}")
                self._logger.error(f"[OSM] Reconcile error for {order.order_id}: {e}")
        
        self._logger.log("osm_reconciliation_complete", results)
        return results
    
    def _get_order_age_minutes(self, order: OrderRecord) -> float:
        """Get order age in minutes."""
        try:
            created = datetime.fromisoformat(order.created_at.replace("Z", "+00:00"))
            now = datetime.utcnow().replace(tzinfo=created.tzinfo)
            return (now - created).total_seconds() / 60.0
        except Exception:
            return 0.0
    
    def cleanup_stale_unknown(self) -> int:
        """
        Cleanup orders stuck in UNKNOWN state too long.
        
        Returns:
            Number of orders cleaned up
        """
        pending_ids = self._get_pending_order_ids()
        cleaned = 0
        
        for order_id in pending_ids:
            order = self._load_order(order_id)
            if order and order.state == OrderState.UNKNOWN:
                age = self._get_order_age_minutes(order)
                if age > self.MAX_UNKNOWN_AGE_MINUTES:
                    self.mark_cancelled(order_id, "stale_unknown_timeout")
                    cleaned += 1
        
        if cleaned > 0:
            self._logger.log("osm_stale_cleanup", {"cleaned": cleaned})
        
        return cleaned
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get order state machine statistics."""
        pending_ids = self._get_pending_order_ids()
        
        state_counts = {s.value: 0 for s in OrderState}
        total_pending = 0
        
        for order_id in pending_ids:
            order = self._load_order(order_id)
            if order:
                state_counts[order.state.value] += 1
                if not order.is_terminal():
                    total_pending += 1
        
        return {
            "pending_count": total_pending,
            "state_distribution": state_counts,
            "pending_order_ids": pending_ids[:20]  # First 20 for debugging
        }


_order_state_machine: Optional[OrderStateMachine] = None


def get_order_state_machine() -> OrderStateMachine:
    """Get or create OrderStateMachine singleton."""
    global _order_state_machine
    if _order_state_machine is None:
        _order_state_machine = OrderStateMachine()
    return _order_state_machine
