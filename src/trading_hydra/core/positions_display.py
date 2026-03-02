"""
=============================================================================
Enhanced Position Display Module - Cockpit-Style Console Output
=============================================================================
Provides detailed position tracking with:
- Hard stop loss and two take-profit levels (TP1, TP2)
- Trailing stop that arms after TP2 hit
- R-multiple tracking
- Time-in-trade and thesis timer
- Next exit trigger preview

Usage:
    from trading_hydra.core.positions_display import PositionState, format_position_display
    
    state = PositionState(symbol="NVDA", entry_price=145.0, qty=100, side="long", ...)
    print(format_position_display(state, current_price=142.0))
=============================================================================
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Tuple
import time

from .clock import get_market_clock


@dataclass
class PositionState:
    """
    Complete state tracking for an open position with staged exits.
    
    Tracks entry, stops, take-profit levels, trailing logic, R-multiples, and timing.
    All prices are in dollars. Percentages are expressed as decimals (0.3 = 30%).
    """
    symbol: str
    entry_price: float
    qty: float
    side: str  # "long" or "short"
    opened_at: datetime
    
    unrealized_pnl: float = 0.0
    current_price: float = 0.0
    
    hard_stop_price: float = 0.0
    tp1_price: float = 0.0
    tp2_price: float = 0.0
    
    tp1_pct: float = 0.30  # Sell 30% at TP1
    tp2_pct: float = 0.40  # Sell 40% at TP2
    runner_pct: float = 0.30  # Keep 30% as runner
    
    tp1_hit: bool = False
    tp2_hit: bool = False
    
    secured_pct: float = 0.0  # Percentage of position secured (exited)
    breakeven_stop_active: bool = False  # Stop moved to breakeven after TP1
    
    trailing_active: bool = False
    trailing_mode: str = "ATR"  # "ATR" or "PCT"
    trailing_atr_period: int = 14
    trailing_atr_multiplier: float = 2.0
    trailing_pct: float = 0.05  # 5% trailing if PCT mode
    trailing_distance: float = 0.0  # Current trailing stop distance
    trailing_stop_price: float = 0.0  # Current trailing stop level
    
    r_value: float = 0.0  # Risk per share (entry - stop for longs)
    current_r_multiple: float = 0.0  # Current position in R terms
    max_planned_loss_r: float = 1.0  # Max loss in R (typically 1R)
    
    thesis_max_hold_seconds: int = 7200  # 2 hours default
    
    high_watermark: float = 0.0  # Highest price seen (for trailing)
    low_watermark: float = float('inf')  # Lowest price seen (for short trailing)

    def update_price(self, current_price: float) -> None:
        """Update current price and recalculate derived values."""
        self.current_price = current_price
        
        if self.side == "long":
            self.unrealized_pnl = (current_price - self.entry_price) * self.qty
            if current_price > self.high_watermark:
                self.high_watermark = current_price
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.qty
            if current_price < self.low_watermark:
                self.low_watermark = current_price
        
        self._calculate_r_multiple()
        self._update_trailing_stop()

    def _calculate_r_multiple(self) -> None:
        """Calculate current R-multiple based on price movement."""
        if self.r_value == 0 or self.hard_stop_price == 0:
            self.r_value = abs(self.entry_price - self.hard_stop_price)
        
        if self.r_value == 0:
            self.current_r_multiple = 0.0
            return
        
        if self.side == "long":
            self.current_r_multiple = (self.current_price - self.entry_price) / self.r_value
        else:
            self.current_r_multiple = (self.entry_price - self.current_price) / self.r_value

    def _update_trailing_stop(self) -> None:
        """Update trailing stop price if trailing is active."""
        if not self.trailing_active:
            return
        
        if self.side == "long":
            new_stop = self.high_watermark - self.trailing_distance
            if new_stop > self.trailing_stop_price:
                self.trailing_stop_price = new_stop
        else:
            new_stop = self.low_watermark + self.trailing_distance
            if new_stop < self.trailing_stop_price or self.trailing_stop_price == 0:
                self.trailing_stop_price = new_stop

    def check_tp1(self) -> Tuple[bool, str]:
        """
        Check if TP1 has been hit. Returns (hit, action_description).
        If hit: sell tp1_pct, move stop to breakeven.
        """
        if self.tp1_hit:
            return False, ""
        
        hit = False
        if self.side == "long":
            hit = self.current_price >= self.tp1_price
        else:
            hit = self.current_price <= self.tp1_price
        
        if hit:
            self.tp1_hit = True
            self.secured_pct += self.tp1_pct
            self.breakeven_stop_active = True
            return True, f"TP1 hit - sell {int(self.tp1_pct*100)}%, stop to breakeven"
        
        return False, ""

    def check_tp2(self) -> Tuple[bool, str]:
        """
        Check if TP2 has been hit. Returns (hit, action_description).
        If hit: sell tp2_pct, ARM and ACTIVATE trailing stop.
        """
        if self.tp2_hit or not self.tp1_hit:
            return False, ""
        
        hit = False
        if self.side == "long":
            hit = self.current_price >= self.tp2_price
        else:
            hit = self.current_price <= self.tp2_price
        
        if hit:
            self.tp2_hit = True
            self.secured_pct += self.tp2_pct
            self.trailing_active = True
            
            if self.side == "long":
                self.trailing_stop_price = self.high_watermark - self.trailing_distance
            else:
                self.trailing_stop_price = self.low_watermark + self.trailing_distance
            
            return True, f"TP2 hit - sell {int(self.tp2_pct*100)}%, trailing ACTIVE"
        
        return False, ""

    def check_stop_hit(self) -> Tuple[bool, str]:
        """Check if any stop (hard, breakeven, or trailing) has been hit."""
        if self.side == "long":
            if self.trailing_active and self.current_price <= self.trailing_stop_price:
                return True, f"Trailing stop hit @ ${self.trailing_stop_price:.2f}"
            
            if self.breakeven_stop_active and self.current_price <= self.entry_price:
                return True, f"Breakeven stop hit @ ${self.entry_price:.2f}"
            
            if self.current_price <= self.hard_stop_price:
                return True, f"Hard stop hit @ ${self.hard_stop_price:.2f}"
        else:
            if self.trailing_active and self.current_price >= self.trailing_stop_price:
                return True, f"Trailing stop hit @ ${self.trailing_stop_price:.2f}"
            
            if self.breakeven_stop_active and self.current_price >= self.entry_price:
                return True, f"Breakeven stop hit @ ${self.entry_price:.2f}"
            
            if self.current_price >= self.hard_stop_price:
                return True, f"Hard stop hit @ ${self.hard_stop_price:.2f}"
        
        return False, ""

    def check_thesis_timeout(self) -> Tuple[bool, str]:
        """Check if thesis time limit has been exceeded."""
        elapsed = (get_market_clock().now() - self.opened_at).total_seconds()
        if elapsed >= self.thesis_max_hold_seconds:
            return True, "Thesis timeout - max hold exceeded"
        return False, ""

    def time_in_trade_seconds(self) -> float:
        """Get time in trade in seconds."""
        return (get_market_clock().now() - self.opened_at).total_seconds()

    def get_next_exit_trigger(self) -> str:
        """
        Determine which exit condition would trigger first.
        Returns a description of the next likely exit.
        """
        triggers = []
        
        if self.side == "long":
            if not self.tp1_hit:
                dist = self.tp1_price - self.current_price
                triggers.append(("TP1", self.tp1_price, dist))
            
            if self.tp1_hit and not self.tp2_hit:
                dist = self.tp2_price - self.current_price
                triggers.append(("TP2", self.tp2_price, dist))
            
            if self.trailing_active:
                dist = self.current_price - self.trailing_stop_price
                triggers.append(("Trail", self.trailing_stop_price, -dist))
            elif self.breakeven_stop_active:
                dist = self.current_price - self.entry_price
                triggers.append(("BE", self.entry_price, -dist))
            else:
                dist = self.current_price - self.hard_stop_price
                triggers.append(("SL", self.hard_stop_price, -dist))
        else:
            if not self.tp1_hit:
                dist = self.current_price - self.tp1_price
                triggers.append(("TP1", self.tp1_price, dist))
            
            if self.tp1_hit and not self.tp2_hit:
                dist = self.current_price - self.tp2_price
                triggers.append(("TP2", self.tp2_price, dist))
            
            if self.trailing_active:
                dist = self.trailing_stop_price - self.current_price
                triggers.append(("Trail", self.trailing_stop_price, -dist))
            elif self.breakeven_stop_active:
                dist = self.entry_price - self.current_price
                triggers.append(("BE", self.entry_price, -dist))
            else:
                dist = self.hard_stop_price - self.current_price
                triggers.append(("SL", self.hard_stop_price, -dist))
        
        elapsed = self.time_in_trade_seconds()
        remaining = self.thesis_max_hold_seconds - elapsed
        triggers.append(("Time", remaining, remaining))
        
        if not triggers:
            return "None"
        
        for name, level, dist in triggers:
            if name == "Time":
                continue
            if dist < 0:
                if name in ("SL", "BE", "Trail"):
                    return f"{name} @ ${level:.2f}"
        
        closest = min(triggers, key=lambda x: abs(x[2]))
        name, level, _ = closest
        
        if name == "Time":
            mins = int(level / 60)
            return f"Time in {mins}m"
        
        return f"{name} @ ${level:.2f}"

    def get_remaining_size_pct(self) -> float:
        """Get remaining position size as percentage."""
        return max(0.0, 1.0 - self.secured_pct)


def format_time_compact(seconds: float, max_seconds: int) -> str:
    """Format time as 'Xm/Yh' or similar compact format."""
    if seconds < 0:
        seconds = 0
    
    elapsed_mins = int(seconds / 60)
    max_mins = int(max_seconds / 60)
    
    if elapsed_mins < 60:
        elapsed_str = f"{elapsed_mins}m"
    else:
        elapsed_str = f"{elapsed_mins // 60}h{elapsed_mins % 60:02d}m"
    
    if max_mins < 60:
        max_str = f"{max_mins}m"
    else:
        max_hours = max_mins // 60
        max_str = f"{max_hours}h"
    
    return f"{elapsed_str}/{max_str}"


def format_position_display(state: PositionState) -> str:
    """
    Format a position state into compact cockpit-style display.
    
    Example output:
    NVDA: UPL -$634 | R -0.71 (Max -1.25R) | T 47m/2h | Size 100% (Sec 0%)
      SL: $140.50 | TP1: $152.00 (30%) | TP2: $158.00 (40%)
      Trail: INACTIVE (arms @ TP2) mode ATR(14)x2.0
      Next Exit: SL @ $140.50
    """
    lines = []
    
    pnl_sign = "+" if state.unrealized_pnl >= 0 else ""
    r_sign = "+" if state.current_r_multiple >= 0 else ""
    
    time_str = format_time_compact(
        state.time_in_trade_seconds(),
        state.thesis_max_hold_seconds
    )
    
    remaining_pct = int(state.get_remaining_size_pct() * 100)
    secured_pct = int(state.secured_pct * 100)
    
    line1 = (
        f"{state.symbol}: UPL {pnl_sign}${state.unrealized_pnl:,.0f} | "
        f"R {r_sign}{state.current_r_multiple:.2f} (Max -{state.max_planned_loss_r:.2f}R) | "
        f"T {time_str} | Size {remaining_pct}% (Sec {secured_pct}%)"
    )
    lines.append(line1)
    
    tp1_pct = int(state.tp1_pct * 100)
    tp2_pct = int(state.tp2_pct * 100)
    
    tp1_status = " ✓" if state.tp1_hit else ""
    tp2_status = " ✓" if state.tp2_hit else ""
    
    line2 = (
        f"  SL: ${state.hard_stop_price:.2f} | "
        f"TP1: ${state.tp1_price:.2f} ({tp1_pct}%){tp1_status} | "
        f"TP2: ${state.tp2_price:.2f} ({tp2_pct}%){tp2_status}"
    )
    lines.append(line2)
    
    if state.trailing_active:
        trail_status = "ACTIVE"
        trail_price = f" @ ${state.trailing_stop_price:.2f}"
    else:
        trail_status = "INACTIVE (arms @ TP2)"
        trail_price = ""
    
    if state.trailing_mode == "ATR":
        mode_str = f"ATR({state.trailing_atr_period})x{state.trailing_atr_multiplier}"
    else:
        mode_str = f"{int(state.trailing_pct * 100)}%"
    
    line3 = f"  Trail: {trail_status}{trail_price} mode {mode_str}"
    lines.append(line3)
    
    next_exit = state.get_next_exit_trigger()
    line4 = f"  Next Exit: {next_exit}"
    lines.append(line4)
    
    return "\n".join(lines)


def format_positions_enhanced(positions: List[PositionState]) -> str:
    """
    Format multiple positions with enhanced display.
    
    Args:
        positions: List of PositionState objects
        
    Returns:
        Formatted multi-line string for all positions
    """
    if not positions:
        return "  No open positions"
    
    lines = []
    for i, pos in enumerate(positions):
        if i > 0:
            lines.append("")
        lines.append(format_position_display(pos))
    
    return "\n".join(lines)


def create_position_state_from_trade(
    symbol: str,
    entry_price: float,
    qty: float,
    side: str,
    stop_loss_pct: float = 0.02,  # 2% default stop
    tp1_r: float = 1.5,  # TP1 at 1.5R
    tp2_r: float = 2.5,  # TP2 at 2.5R
    tp1_pct: float = 0.30,
    tp2_pct: float = 0.40,
    max_hold_hours: float = 2.0,
    trailing_mode: str = "ATR",
    trailing_atr_period: int = 14,
    trailing_atr_mult: float = 2.0,
    trailing_pct: float = 0.05,
    atr_value: Optional[float] = None
) -> PositionState:
    """
    Create a PositionState from trade entry parameters.
    
    Calculates stop loss and take profit levels based on R-multiple targets.
    
    Args:
        symbol: Ticker symbol
        entry_price: Entry price
        qty: Position size (shares)
        side: "long" or "short"
        stop_loss_pct: Stop loss as percentage of entry (0.02 = 2%)
        tp1_r: Take profit 1 target in R multiples
        tp2_r: Take profit 2 target in R multiples
        tp1_pct: Percentage to sell at TP1
        tp2_pct: Percentage to sell at TP2
        max_hold_hours: Maximum time to hold position
        trailing_mode: "ATR" or "PCT"
        trailing_atr_period: ATR period for trailing calc
        trailing_atr_mult: ATR multiplier for trailing distance
        trailing_pct: Percentage for trailing if PCT mode
        atr_value: Pre-calculated ATR value (optional)
    
    Returns:
        Configured PositionState object
    """
    if side == "long":
        hard_stop = entry_price * (1 - stop_loss_pct)
        r_value = entry_price - hard_stop
        tp1_price = entry_price + (r_value * tp1_r)
        tp2_price = entry_price + (r_value * tp2_r)
    else:
        hard_stop = entry_price * (1 + stop_loss_pct)
        r_value = hard_stop - entry_price
        tp1_price = entry_price - (r_value * tp1_r)
        tp2_price = entry_price - (r_value * tp2_r)
    
    if trailing_mode == "ATR" and atr_value:
        trailing_distance = atr_value * trailing_atr_mult
    else:
        trailing_distance = entry_price * trailing_pct
    
    return PositionState(
        symbol=symbol,
        entry_price=entry_price,
        qty=qty,
        side=side,
        opened_at=get_market_clock().now(),
        current_price=entry_price,
        hard_stop_price=hard_stop,
        tp1_price=tp1_price,
        tp2_price=tp2_price,
        tp1_pct=tp1_pct,
        tp2_pct=tp2_pct,
        runner_pct=1.0 - tp1_pct - tp2_pct,
        r_value=r_value,
        max_planned_loss_r=1.0,
        thesis_max_hold_seconds=int(max_hold_hours * 3600),
        trailing_mode=trailing_mode,
        trailing_atr_period=trailing_atr_period,
        trailing_atr_multiplier=trailing_atr_mult,
        trailing_pct=trailing_pct,
        trailing_distance=trailing_distance,
        high_watermark=entry_price,
        low_watermark=entry_price
    )
