"""System State Snapshot - Immutable state for logging and decision-making.

This module provides a single authoritative snapshot of the system state,
populated once per loop and consumed everywhere for "why no trades" answers.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Literal


@dataclass(frozen=True)
class SystemState:
    """Immutable snapshot of system state for a single loop iteration.
    
    This object is read-only. Nothing mutates it.
    That's intentional - it's the "truth" for this loop.
    """
    
    # Timestamp
    timestamp: datetime
    loop_id: int
    
    # Regime state
    vix: float
    regime: Literal["LOW", "NORMAL", "STRESS"]
    
    # Earnings state (per-symbol, but we track if any are active)
    earnings_active_symbols: List[str] = field(default_factory=list)
    
    # Sizing state
    equity: float = 0.0
    baseline_equity: float = 5000.0
    growth_multiplier: float = 1.0
    regime_size_multiplier: float = 1.0
    effective_size_multiplier: float = 1.0  # growth × regime
    
    # Kill-switch state
    global_freeze: bool = False
    frozen_clusters: List[str] = field(default_factory=list)
    daily_pnl: float = 0.0
    daily_pnl_r: float = 0.0  # PnL in R units
    
    # Trade tracking
    trades_today: int = 0
    max_new_trades_per_day: int = 100
    
    # Highest priority blocker (for NO_TRADES logging)
    primary_blocker: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON logging."""
        return {
            "timestamp": self.timestamp.isoformat() + "Z",
            "loop_id": self.loop_id,
            "vix": round(self.vix, 2),
            "regime": self.regime,
            "earnings_active_symbols": self.earnings_active_symbols,
            "equity": round(self.equity, 2),
            "baseline_equity": round(self.baseline_equity, 2),
            "growth_multiplier": round(self.growth_multiplier, 3),
            "regime_size_multiplier": round(self.regime_size_multiplier, 3),
            "effective_size_multiplier": round(self.effective_size_multiplier, 3),
            "global_freeze": self.global_freeze,
            "frozen_clusters": self.frozen_clusters,
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_pnl_r": round(self.daily_pnl_r, 2),
            "trades_today": self.trades_today,
            "max_new_trades_per_day": self.max_new_trades_per_day,
            "primary_blocker": self.primary_blocker
        }
    
    def get_console_banner(self) -> str:
        """Generate console banner showing current state."""
        freeze_status = "GLOBAL FREEZE" if self.global_freeze else (
            f"cluster freeze {self.frozen_clusters}" if self.frozen_clusters else "none"
        )
        earnings_status = f"ACTIVE ({len(self.earnings_active_symbols)} symbols)" if self.earnings_active_symbols else "inactive"

        # Session protection status line
        sp_line = ""
        try:
            from ..risk.session_protection import _session_protection_instance
            if _session_protection_instance is not None:
                sp_line = _session_protection_instance.get_console_line()
            else:
                sp_line = "Session Protection: initializing..."
        except Exception:
            sp_line = "Session Protection: unavailable"

        lines = [
            "=== SYSTEM STATE ===",
            f"VIX: {self.vix:.1f} → {self.regime}",
            f"Earnings Mode: {earnings_status}",
            f"Size Scaling: {self.growth_multiplier:.2f} (growth) × {self.regime_size_multiplier:.2f} (regime) = {self.effective_size_multiplier:.2f}",
            f"Kill-Switch: {freeze_status}",
            sp_line,
            f"Trades Today: {self.trades_today}/{self.max_new_trades_per_day}",
            f"Daily P&L: ${self.daily_pnl:+.2f} ({self.daily_pnl_r:+.1f}R)",
            "===================="
        ]
        return "\n".join(lines)


@dataclass(frozen=True)
class ResolvedParams:
    """Resolved parameters for a specific symbol after all modifiers applied.
    
    This is the output of ParameterResolver - the final truth for trading decisions.
    """
    
    symbol: str
    resolved_profile_name: str
    
    # Delta bands (after regime modifier)
    delta_min: float
    delta_max: float
    delta_target: float
    
    # DTE window (after regime shift)
    dte_min: int
    dte_max: int
    
    # Sizing (after regime × growth)
    max_position_pct: float
    max_total_exposure: float
    max_open_positions: int
    max_new_trades_per_day: int
    
    # Liquidity requirements
    min_open_interest: int = 500
    max_bid_ask_pct: float = 0.15
    
    # Special rules
    force_defined_risk: bool = False
    debit_only: bool = False
    disable_put_selling: bool = False
    
    # Kill-switch state for this symbol
    blocked_by_killswitch: bool = False
    killswitch_reason: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for logging."""
        return {
            "symbol": self.symbol,
            "resolved_profile": self.resolved_profile_name,
            "delta_min": round(self.delta_min, 3),
            "delta_max": round(self.delta_max, 3),
            "delta_target": round(self.delta_target, 3),
            "dte_min": self.dte_min,
            "dte_max": self.dte_max,
            "max_position_pct": round(self.max_position_pct, 4),
            "max_total_exposure": round(self.max_total_exposure, 4),
            "max_open_positions": self.max_open_positions,
            "max_new_trades_per_day": self.max_new_trades_per_day,
            "force_defined_risk": self.force_defined_risk,
            "blocked_by_killswitch": self.blocked_by_killswitch,
            "killswitch_reason": self.killswitch_reason
        }
