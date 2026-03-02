"""
=============================================================================
Human-Readable Console Output Formatter
=============================================================================
Formats trading loop data into a dashboard-style display that a human trader
can follow and potentially replicate manually. Shows market conditions,
signals, reasoning, and decisions in plain English.

Supports in-place updates using ANSI escape codes for a clean, non-scrolling
dashboard experience.
=============================================================================
"""

import sys
from datetime import datetime
from typing import Dict, Any, List, Optional, TYPE_CHECKING
from dataclasses import dataclass, field

from .clock import get_market_clock

# ANSI escape codes for cursor control
ANSI_CLEAR_SCREEN = "\033[2J"
ANSI_CURSOR_HOME = "\033[H"
ANSI_CURSOR_UP = "\033[{}A"
ANSI_CLEAR_LINE = "\033[2K"
ANSI_HIDE_CURSOR = "\033[?25l"
ANSI_SHOW_CURSOR = "\033[?25h"

if TYPE_CHECKING:
    from trading_hydra.core.positions_display import PositionState


@dataclass
class TickerSignal:
    """Represents a trading signal for a single ticker."""
    symbol: str
    price: float
    signal: str  # "BUY", "SELL", "SHORT", "HOLD"
    reason: str  # Plain English explanation
    asset_type: str = "stock"  # "stock", "crypto", "option"
    pnl: Optional[float] = None  # Current P&L if position exists


@dataclass  
class PositionInfo:
    """Represents an open position."""
    symbol: str
    qty: float
    side: str  # "long" or "short"
    entry_price: float
    current_price: float
    pnl: float
    pnl_percent: float
    trailing_stop_active: bool = False


@dataclass
class ExitInfo:
    """Represents a trade exit."""
    symbol: str
    side: str  # "long" or "short"
    qty: float
    entry_price: float
    exit_price: float
    pnl: float
    pnl_percent: float
    reason: str  # "trailing_stop", "take_profit", "stop_loss", "time_exit", "manual"
    bot_id: str = ""
    timestamp: str = ""


@dataclass
class OrderEvent:
    """Represents an order placed event."""
    bot_id: str
    symbol: str
    side: str  # "LONG" or "SHORT"
    qty: float
    notional: float
    entry_price: float
    stop_price: Optional[float] = None
    risk_amount: Optional[float] = None
    risk_pct: Optional[float] = None
    gates: Dict[str, str] = field(default_factory=dict)  # gate_name: status
    decision_id: str = ""


@dataclass
class BlockedSignalEvent:
    """Represents a blocked signal event."""
    bot_id: str
    symbol: str
    signal_type: str  # "CALL", "PUT", "LONG", "SHORT"
    signal_reason: str
    blocked_by: str  # gate name
    block_reason: str
    decision_id: str = ""


@dataclass
class HaltEvent:
    """Represents a halt event."""
    trigger: str  # "stale_data", "api_failure", "daily_loss", etc.
    details: str
    action: str  # "NEW_ENTRIES_PAUSED", "FULL_HALT", etc.


@dataclass
class LoopDisplayData:
    """All data needed to render human-readable console output."""
    # Account info
    equity: float = 0.0
    cash: float = 0.0
    day_start_equity: float = 0.0
    daily_pnl: float = 0.0
    daily_pnl_percent: float = 0.0
    risk_budget: float = 0.0
    risk_used_pct: float = 0.0
    max_risk_pct: float = 3.0
    
    # Market regime
    vix: float = 0.0
    volatility_regime: str = "unknown"
    sentiment: str = "neutral"
    position_size_mult: float = 1.0
    halt_new_entries: bool = False
    
    # Positions
    positions: List[PositionInfo] = field(default_factory=list)
    trailing_stops_active: int = 0
    
    # Bot status
    bots_enabled: List[str] = field(default_factory=list)
    bots_outside_hours: List[str] = field(default_factory=list)
    
    # Signals from this loop
    signals: List[TickerSignal] = field(default_factory=list)
    
    # Loop result
    trades_executed: int = 0
    errors: List[str] = field(default_factory=list)
    loop_number: int = 0
    next_scan_seconds: int = 5
    open_orders: int = 0
    data_age_seconds: float = 0.0
    
    # Halted status
    is_halted: bool = False
    halt_reason: str = ""
    
    # Account mode: micro, small, or standard
    account_mode: str = "standard"
    account_mode_description: str = ""
    
    # Enhanced position display (optional)
    enhanced_positions: List[Any] = field(default_factory=list)  # List[PositionState]
    
    # Recent exits (last few closed positions)
    recent_exits: List[ExitInfo] = field(default_factory=list)
    
    # EVENT MODE: Events that occurred this loop
    order_events: List[OrderEvent] = field(default_factory=list)
    blocked_signals: List[BlockedSignalEvent] = field(default_factory=list)
    halt_events: List[HaltEvent] = field(default_factory=list)


class ConsoleFormatter:
    """
    Formats trading loop data into human-readable console output.
    
    Designed to be readable by a human trader who could follow along
    and replicate the decisions manually if needed.
    
    Supports in-place updates for a clean, non-scrolling dashboard.
    """
    
    # Box drawing characters for clean borders
    HEAVY_H = "═"
    LIGHT_H = "─"
    HEAVY_V = "║"
    LIGHT_V = "│"
    
    def __init__(self, width: int = 72, quiet: bool = False, inplace: bool = False, event_mode: bool = False):
        """
        Initialize the formatter.
        
        Args:
            width: Console width for formatting
            quiet: If True, only show minimal summary line
            inplace: If True, overwrite previous output each loop
            event_mode: If True, show heartbeat + event blocks only (recommended for production)
        """
        self.width = width
        self.quiet = quiet
        self.inplace = inplace
        self.event_mode = event_mode
        self._last_line_count = 0
        self._first_render = True
    
    def format_loop(self, data: LoopDisplayData) -> str:
        """
        Format a complete loop's data into human-readable output.
        
        Args:
            data: All data from the trading loop
            
        Returns:
            Formatted string ready for console output
        """
        if self.quiet:
            return self._format_quiet(data)
        
        if self.event_mode:
            return self._format_event_mode(data)
        
        lines = []
        
        # Header with timestamp
        lines.append(self._format_header(data))
        lines.append("")
        
        # Account summary line
        lines.append(self._format_account_summary(data))
        lines.append(self._format_positions_summary(data))
        lines.append(self._heavy_line())
        lines.append("")
        
        # Market regime section
        lines.append(self._format_market_regime(data))
        lines.append("")
        
        # Enhanced positions section (if available)
        if data.enhanced_positions:
            lines.append(self._format_enhanced_positions(data.enhanced_positions))
            lines.append("")
        
        # Signals section - what each ticker is doing
        if data.signals:
            for sig in data.signals:
                lines.append(self._format_signal(sig))
        else:
            lines.append("  No active signals this loop")
        lines.append("")
        
        # Recent exits section - show closed positions
        if data.recent_exits:
            lines.append(self._format_recent_exits(data.recent_exits))
            lines.append("")
        
        # Bot status for those outside hours
        if data.bots_outside_hours:
            for bot in data.bots_outside_hours:
                lines.append(f"  \u23f0 {bot}: Outside trading hours")
        
        # Errors if any
        if data.errors:
            lines.append("")
            for err in data.errors:
                lines.append(f"  \u26a0 ERROR: {err}")
        
        # Halt warning
        if data.is_halted:
            lines.append("")
            lines.append(f"  \U0001f6d1 TRADING HALTED: {data.halt_reason}")
        
        lines.append("")
        
        # Footer summary
        lines.append(self._format_footer(data))
        lines.append(self._light_line())
        
        output = "\n".join(lines)
        
        # Handle in-place update mode
        if self.inplace:
            output = self._prepare_inplace_output(output)
        
        return output
    
    def _format_event_mode(self, data: LoopDisplayData) -> str:
        """
        Format EVENT mode output: compact heartbeat + event blocks only.
        
        This is the recommended production mode. Shows:
        - One-line heartbeat every loop
        - Positions summary (always shown if positions exist)
        - Event blocks only when something happens (order, block, halt, exit)
        """
        lines = []
        
        # Always show heartbeat line
        lines.append(self._format_heartbeat(data))
        
        # Always show positions if any exist
        if data.positions:
            lines.append(self._format_positions_compact(data.positions))
        
        # Event blocks only when something happens
        for order in data.order_events:
            lines.append("")
            lines.append(self._format_order_event(order))
        
        for blocked in data.blocked_signals:
            lines.append("")
            lines.append(self._format_blocked_signal_event(blocked))
        
        for halt in data.halt_events:
            lines.append("")
            lines.append(self._format_halt_event(halt))
        
        for exit_info in data.recent_exits:
            lines.append("")
            lines.append(self._format_exit_event(exit_info))
        
        # Errors as event blocks
        for err in data.errors:
            lines.append("")
            lines.append(self._format_error_event(err))
        
        output = "\n".join(lines)
        
        # Handle in-place update mode for event mode too
        if self.inplace:
            output = self._prepare_inplace_output(output)
        
        return output
    
    def _format_heartbeat(self, data: LoopDisplayData) -> str:
        """
        Format compact one-line heartbeat.
        
        Format:
        HH:MM:SS  L#128 ✓  EQ $25,140  PnL +$84 (+0.33%)  Risk 0.6/3.0%  Pos 2  Ord 0  Regime NORMAL(VIX 18.0)  Data 2s  Next 5s
        """
        now = get_market_clock().now()
        time_str = now.strftime("%H:%M:%S")
        
        status = "\u2713" if not data.errors and not data.is_halted else "\u2717"
        if data.is_halted:
            status = "\U0001f6d1"
        
        pnl_sign = "+" if data.daily_pnl >= 0 else ""
        pnl_pct_sign = "+" if data.daily_pnl_percent >= 0 else ""
        
        regime_str = f"{data.volatility_regime.upper()}(VIX {data.vix:.1f})" if data.vix > 0 else data.volatility_regime.upper()
        
        return (
            f"{time_str}  L#{data.loop_number} {status}  "
            f"EQ ${data.equity:,.0f}  "
            f"PnL {pnl_sign}${data.daily_pnl:,.0f} ({pnl_pct_sign}{data.daily_pnl_percent:.2f}%)  "
            f"Risk {data.risk_used_pct:.1f}/{data.max_risk_pct:.1f}%  "
            f"Pos {len(data.positions)}  "
            f"Ord {data.open_orders}  "
            f"Regime {regime_str}  "
            f"Data {data.data_age_seconds:.0f}s  "
            f"Next {data.next_scan_seconds}s"
        )
    
    def _format_positions_compact(self, positions: List[PositionInfo]) -> str:
        """
        Format positions as compact lines below heartbeat.
        
        Format per position:
          📈 AAPL LONG 10 @ $150.00 → $152.50 | +$25 (+1.67%) | Trail: ON
        """
        lines = []
        for p in positions:
            side_icon = "\U0001f4c8" if p.side == "long" else "\U0001f4c9"
            pnl_sign = "+" if p.pnl >= 0 else ""
            pct_sign = "+" if p.pnl_percent >= 0 else ""
            trail_str = "Trail: ON" if p.trailing_stop_active else "Trail: OFF"
            
            lines.append(
                f"  {side_icon} {p.symbol} {p.side.upper()} {p.qty:.2f} @ ${p.entry_price:,.2f} → ${p.current_price:,.2f} | "
                f"{pnl_sign}${p.pnl:,.2f} ({pct_sign}{p.pnl_percent:.2f}%) | {trail_str}"
            )
        
        return "\n".join(lines)

    def _format_order_event(self, order: OrderEvent) -> str:
        """Format an order placed event block."""
        gates_str = " | ".join(f"{k} {v}" for k, v in order.gates.items()) if order.gates else "ALL OK"
        
        lines = [
            "--- ORDER PLACED ---",
            f"Bot: {order.bot_id} | {order.symbol} | {order.side}",
            f"Qty: {order.qty:.4f} | Notional: ${order.notional:,.2f} | Entry: {order.entry_price:,.2f}",
        ]
        
        if order.stop_price and order.risk_amount:
            lines.append(f"Stop: {order.stop_price:,.2f} | Risk: ${order.risk_amount:,.2f} ({order.risk_pct:.2f}%)")
        
        lines.append(f"Gates: {gates_str}")
        
        if order.decision_id:
            lines.append(f"DecisionID: {order.decision_id}")
        
        return "\n".join(lines)
    
    def _format_blocked_signal_event(self, blocked: BlockedSignalEvent) -> str:
        """Format a blocked signal event block."""
        lines = [
            "--- SIGNAL BLOCKED ---",
            f"Bot: {blocked.bot_id} | {blocked.symbol} | {blocked.signal_type}",
            f"Signal: {blocked.signal_reason}",
            f"Blocked by: {blocked.blocked_by}",
            f"Reason: {blocked.block_reason}",
            "Action: NO_TRADE",
        ]
        
        if blocked.decision_id:
            lines.append(f"DecisionID: {blocked.decision_id}")
        
        return "\n".join(lines)
    
    def _format_halt_event(self, halt: HaltEvent) -> str:
        """Format a halt event block."""
        return "\n".join([
            "--- FAIL-CLOSED HALT ---",
            f"Trigger: {halt.trigger}",
            f"Details: {halt.details}",
            f"Action: {halt.action}",
        ])
    
    def _format_exit_event(self, exit_info: ExitInfo) -> str:
        """Format an exit/close event block."""
        pnl_sign = "+" if exit_info.pnl >= 0 else ""
        pnl_emoji = "\U0001f4b0" if exit_info.pnl >= 0 else "\U0001f534"
        
        return "\n".join([
            "--- POSITION CLOSED ---",
            f"Bot: {exit_info.bot_id} | {exit_info.symbol} | {exit_info.side.upper()}",
            f"Entry: {exit_info.entry_price:,.2f} | Exit: {exit_info.exit_price:,.2f}",
            f"P&L: {pnl_emoji} {pnl_sign}${exit_info.pnl:,.2f} ({pnl_sign}{exit_info.pnl_percent:.2f}%)",
            f"Reason: {exit_info.reason}",
        ])
    
    def _format_error_event(self, error: str) -> str:
        """Format an error event block."""
        return "\n".join([
            "--- ERROR ---",
            f"\u26a0 {error}",
        ])
    
    def _prepare_inplace_output(self, output: str) -> str:
        """
        Prepare output for in-place update.
        
        Uses os.system('clear') for reliable terminal clearing,
        then returns the output to be printed.
        """
        import os
        # Use system clear command - more reliable than ANSI codes
        os.system('clear')
        return output
    
    def reset_inplace(self):
        """Reset in-place state (call when restarting or on first run)."""
        self._first_render = True
        self._last_line_count = 0
    
    def _format_quiet(self, data: LoopDisplayData) -> str:
        """Format minimal one-line summary."""
        status = "\u2713" if not data.errors else "\u2717"
        pnl_sign = "+" if data.daily_pnl >= 0 else ""
        
        pos_str = ", ".join(p.symbol for p in data.positions[:3])
        if len(data.positions) > 3:
            pos_str += f" +{len(data.positions) - 3}"
        
        trades = f"{data.trades_executed} trades" if data.trades_executed else "no trades"
        
        return (
            f"[{get_market_clock().now().strftime('%H:%M:%S')}] "
            f"Loop #{data.loop_number} {status} | "
            f"${data.equity:,.0f} | "
            f"P&L: {pnl_sign}${data.daily_pnl:,.0f} | "
            f"Pos: {pos_str or 'none'} | "
            f"{trades}"
        )
    
    def _heavy_line(self) -> str:
        """Return a heavy horizontal line."""
        return self.HEAVY_H * self.width
    
    def _light_line(self) -> str:
        """Return a light horizontal line."""
        return self.LIGHT_H * self.width
    
    def _format_header(self, data: LoopDisplayData) -> str:
        """Format the header with timestamp and loop number."""
        now = get_market_clock().now()
        date_str = now.strftime("%A %b %d, %Y")
        time_str = now.strftime("%I:%M:%S %p EST")
        
        # Account mode indicator - always show mode for visibility
        mode_emoji = {"micro": "\U0001f525", "small": "\U0001f4c8", "standard": "\U0001f3e6"}.get(data.account_mode, "\U0001f3e6")
        mode_label = data.account_mode.upper()
        
        header = f"  TRADING HYDRA | {mode_emoji} {mode_label} | {time_str} | {date_str}"
        
        lines = [
            self._heavy_line(),
            header,
            self._heavy_line()
        ]
        return "\n".join(lines)
    
    def _format_account_summary(self, data: LoopDisplayData) -> str:
        """Format the account summary line."""
        pnl_sign = "+" if data.daily_pnl >= 0 else ""
        pnl_pct_sign = "+" if data.daily_pnl_percent >= 0 else ""
        
        return (
            f"  Account: ${data.equity:,.2f} | "
            f"Daily P&L: {pnl_sign}${data.daily_pnl:,.2f} ({pnl_pct_sign}{data.daily_pnl_percent:.2f}%) | "
            f"Risk Budget: ${data.risk_budget:,.2f}"
        )
    
    def _format_positions_summary(self, data: LoopDisplayData) -> str:
        """Format positions summary line."""
        if not data.positions:
            return "  Positions: None | Trailing Stops: 0 active"
        
        pos_parts = []
        for p in data.positions[:4]:  # Show max 4 positions
            pnl_sign = "+" if p.pnl >= 0 else ""
            stop_marker = "*" if p.trailing_stop_active else ""
            pos_parts.append(f"{p.symbol}{stop_marker} ({pnl_sign}${p.pnl:,.0f})")
        
        if len(data.positions) > 4:
            pos_parts.append(f"+{len(data.positions) - 4} more")
        
        return (
            f"  Positions: {' '.join(pos_parts)} | "
            f"Trailing Stops: {data.trailing_stops_active} active"
        )
    
    def _format_market_regime(self, data: LoopDisplayData) -> str:
        """Format market regime section."""
        regime_emoji = {
            "very_low": "\U0001f7e2",    # green circle
            "low": "\U0001f7e2",          # green circle  
            "normal": "\U0001f7e1",       # yellow circle
            "elevated": "\U0001f7e0",     # orange circle
            "high": "\U0001f534",         # red circle
            "extreme": "\U0001f6a8",      # alarm
        }.get(data.volatility_regime, "\u2753")
        
        sizing = f"{int(data.position_size_mult * 100)}%"
        
        halt_note = ""
        if data.halt_new_entries:
            halt_note = " | \u26a0 NEW ENTRIES PAUSED"
        
        return (
            f"  {regime_emoji} Market Regime: {data.volatility_regime.upper()} | "
            f"VIX: {data.vix:.1f} | "
            f"Sentiment: {data.sentiment.title()} | "
            f"Position Sizing: {sizing}{halt_note}"
        )
    
    def _format_signal(self, sig: TickerSignal) -> str:
        """Format a single ticker signal with reasoning."""
        emoji = {
            "BUY": "\U0001f7e2",      # green
            "SELL": "\U0001f534",     # red
            "SHORT": "\U0001f535",    # blue
            "HOLD": "\u26aa",         # white
            "EXIT": "\U0001f7e0",     # orange
        }.get(sig.signal.upper(), "\U0001f50d")
        
        asset_label = {
            "stock": "",
            "crypto": " (crypto)",
            "option": " (option)",
        }.get(sig.asset_type, "")
        
        price_str = f"${sig.price:,.2f}" if sig.price < 1000 else f"${sig.price:,.0f}"
        
        pnl_str = ""
        if sig.pnl is not None:
            pnl_sign = "+" if sig.pnl >= 0 else ""
            pnl_str = f" | P&L: {pnl_sign}${sig.pnl:,.2f}"
        
        return (
            f"  {emoji} {sig.symbol}{asset_label}: {price_str} | "
            f"Signal: {sig.signal.upper()}{pnl_str}\n"
            f"     \u2192 {sig.reason}"
        )
    
    def _format_enhanced_positions(self, positions: List[Any]) -> str:
        """
        Format enhanced position display with TP levels, trailing stops, R-multiples.
        
        Uses the positions_display module for cockpit-style output.
        """
        try:
            from trading_hydra.core.positions_display import format_positions_enhanced
            return format_positions_enhanced(positions)
        except ImportError:
            return "  [Enhanced position display not available]"
        except Exception as e:
            return f"  [Position display error: {e}]"
    
    def _format_recent_exits(self, exits: List[ExitInfo]) -> str:
        """Format recent trade exits section."""
        lines = ["  " + self.LIGHT_H * 40, "  RECENT EXITS:"]
        
        for exit in exits[:5]:  # Show last 5 exits max
            pnl_sign = "+" if exit.pnl >= 0 else ""
            pnl_color = "\033[92m" if exit.pnl >= 0 else "\033[91m"  # Green/Red
            reset = "\033[0m"
            
            reason_emoji = {
                "trailing_stop": "\U0001f6d1",
                "take_profit": "\U0001f4b0",
                "stop_loss": "\u274c",
                "time_exit": "\u23f0",
                "manual": "\u270b",
            }.get(exit.reason, "\u2192")
            
            reason_label = {
                "trailing_stop": "Trailing Stop",
                "take_profit": "Take Profit",
                "stop_loss": "Stop Loss",
                "time_exit": "Time Exit",
                "manual": "Manual Close",
            }.get(exit.reason, exit.reason)
            
            lines.append(
                f"  {reason_emoji} {exit.symbol} {exit.side.upper()}: "
                f"${exit.entry_price:.2f} \u2192 ${exit.exit_price:.2f} | "
                f"{pnl_color}P&L: {pnl_sign}${exit.pnl:.2f} ({pnl_sign}{exit.pnl_percent:.1f}%){reset} | "
                f"{reason_label}"
            )
            if exit.bot_id:
                lines.append(f"       Bot: {exit.bot_id} | {exit.timestamp}")
        
        return "\n".join(lines)
    
    def _format_footer(self, data: LoopDisplayData) -> str:
        """Format the footer summary line."""
        status = "\u2713" if not data.errors else "\u2717"
        
        bots_str = f"{len(data.bots_enabled)} bots"
        trades_str = f"{data.trades_executed} trades" if data.trades_executed else "no trades"
        
        return (
            f"  {status} Loop #{data.loop_number} complete | "
            f"{bots_str} checked | "
            f"{trades_str} | "
            f"Next scan in {data.next_scan_seconds}s"
        )


# Global formatter instance
_formatter: Optional[ConsoleFormatter] = None


def get_console_formatter(quiet: bool = False, inplace: bool = False, event_mode: bool = False) -> ConsoleFormatter:
    """
    Get or create the global console formatter.
    
    Args:
        quiet: If True, formatter uses minimal one-line output
        inplace: If True, formatter overwrites previous output each loop
        event_mode: If True, show heartbeat + event blocks only (recommended for production)
        
    Note: Always updates modes to ensure deterministic behavior
    between runs with different modes.
    
    Console modes (mutually exclusive, priority order):
    1. quiet: One-line summary only
    2. event_mode: Heartbeat + event blocks (recommended)
    3. default: Full dashboard every loop
    """
    global _formatter
    if _formatter is None:
        _formatter = ConsoleFormatter(quiet=quiet, inplace=inplace, event_mode=event_mode)
    else:
        _formatter.quiet = quiet
        _formatter.inplace = inplace
        _formatter.event_mode = event_mode
        if inplace:
            _formatter.reset_inplace()
    return _formatter


def set_quiet_mode(quiet: bool) -> None:
    """Set quiet mode for minimal output."""
    global _formatter
    if _formatter:
        _formatter.quiet = quiet
        if quiet:
            _formatter.event_mode = False
    else:
        _formatter = ConsoleFormatter(quiet=quiet)


def set_event_mode(event_mode: bool) -> None:
    """
    Set EVENT mode for production-friendly output.
    
    EVENT mode shows:
    - Compact heartbeat line every loop
    - Event blocks only when something changes (order, block, halt, exit)
    
    This is the recommended mode for production operation.
    """
    global _formatter
    if _formatter:
        _formatter.event_mode = event_mode
        if event_mode:
            _formatter.quiet = False
    else:
        _formatter = ConsoleFormatter(event_mode=event_mode)


def set_inplace_mode(inplace: bool) -> None:
    """Set in-place update mode for non-scrolling dashboard."""
    global _formatter
    if _formatter:
        _formatter.inplace = inplace
        if inplace:
            _formatter.reset_inplace()
    else:
        _formatter = ConsoleFormatter(inplace=inplace)


@dataclass
class GapData:
    """Represents a gap analysis result for display."""
    symbol: str
    gap_pct: float
    direction: str  # "UP" or "DOWN"
    volume_ratio: float
    prev_close: float = 0.0
    open_price: float = 0.0
    vwap: float = 0.0
    rsi: float = 0.0
    pattern_detected: bool = False
    pattern_name: str = ""
    ml_score: float = 0.0


def format_premarket_gap_display(
    gaps: List[GapData],
    scan_time: str,
    next_scan_seconds: int = 300,
    trading_starts_at: str = "06:30 PST"
) -> str:
    """
    Format a premarket gap analysis display showing ranked gaps.
    
    This display updates during premarket (06:00-06:30 PST) to show
    which stocks have the best gap setups for the day.
    
    Args:
        gaps: List of GapData objects sorted by priority
        scan_time: Current scan time string
        next_scan_seconds: Seconds until next scan
        trading_starts_at: When trading session begins
        
    Returns:
        Formatted string for console display
    """
    clock = get_market_clock()
    now = clock.now()
    
    lines = []
    
    lines.append("═" * 70)
    lines.append("  📊 PREMARKET GAP SCANNER  │  Trading Starts: " + trading_starts_at)
    lines.append("═" * 70)
    lines.append(f"  Scan Time: {scan_time}  │  Next Update: {next_scan_seconds}s  │  Gaps Found: {len(gaps)}")
    lines.append("─" * 70)
    
    if not gaps:
        lines.append("  No significant gaps detected yet. Waiting for premarket data...")
        lines.append("─" * 70)
        return "\n".join(lines)
    
    header = f"  {'Rank':4s} {'Symbol':8s} {'Dir':5s} {'Gap %':>8s} {'Vol':>6s} {'RSI':>6s} {'Pattern':15s} {'ML':>5s}"
    lines.append(header)
    lines.append("  " + "─" * 64)
    
    for i, gap in enumerate(gaps[:15], 1):
        direction = "▲ UP" if gap.direction == "UP" else "▼ DN"
        vol_str = f"{gap.volume_ratio:.1f}x" if gap.volume_ratio > 0 else "-"
        rsi_str = f"{gap.rsi:.0f}" if gap.rsi > 0 else "-"
        pattern_str = gap.pattern_name[:14] if gap.pattern_detected else "-"
        ml_str = f"{gap.ml_score:.0f}" if gap.ml_score > 0 else "-"
        
        rank_emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f" {i}."
        
        line = f"  {rank_emoji:4s} {gap.symbol:8s} {direction:5s} {gap.gap_pct:+7.2f}% {vol_str:>6s} {rsi_str:>6s} {pattern_str:15s} {ml_str:>5s}"
        lines.append(line)
    
    lines.append("─" * 70)
    lines.append("  Legend: Vol=Volume Ratio vs Avg | RSI=7-period RSI | ML=Trade Score")
    lines.append("═" * 70)
    
    return "\n".join(lines)


def print_premarket_gap_display(
    gaps: List[GapData],
    scan_time: str,
    next_scan_seconds: int = 300,
    trading_starts_at: str = "06:30 PST",
    clear_screen: bool = True
) -> None:
    """
    Print the premarket gap display to console.
    
    Args:
        gaps: List of GapData objects sorted by priority
        scan_time: Current scan time string
        next_scan_seconds: Seconds until next scan
        trading_starts_at: When trading session begins
        clear_screen: Whether to clear screen before printing
    """
    if clear_screen:
        print(ANSI_CURSOR_HOME + ANSI_CLEAR_SCREEN, end="")
    
    display = format_premarket_gap_display(gaps, scan_time, next_scan_seconds, trading_starts_at)
    print(display)
