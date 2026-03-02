"""Correlation Guard - Multi-Loss Detection and Auto Risk-Down.

Detects correlated losses across bots/symbols and triggers risk reduction.

Key rules:
- If 2+ losses occur within X minutes across different symbols → auto risk-down
- If losses share same sector/factor → more aggressive reduction
- Always logs WHY the halt occurred (not just that it did)

Safe defaults for live trading.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Any
from collections import deque
import math

from ..core.logging import get_logger
from ..core.state import get_state, set_state
from ..core.config import load_settings


@dataclass
class LossEvent:
    """Record of a losing trade."""
    timestamp: datetime
    symbol: str
    bot_id: str
    loss_usd: float
    loss_pct: float
    sector: Optional[str] = None
    factor_profile: Optional[str] = None  # e.g., "momentum", "mean_reversion"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "bot_id": self.bot_id,
            "loss_usd": self.loss_usd,
            "loss_pct": self.loss_pct,
            "sector": self.sector,
            "factor_profile": self.factor_profile
        }


@dataclass
class CorrelationGuardState:
    """Current state of correlation monitoring."""
    risk_level: str = "normal"  # normal, reduced, halted
    risk_multiplier: float = 1.0
    reason: Optional[str] = None
    triggered_at: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None
    recent_losses: List[LossEvent] = field(default_factory=list)
    
    def is_active(self) -> bool:
        """Check if risk reduction is active."""
        if self.cooldown_until and datetime.utcnow() > self.cooldown_until:
            return False
        return self.risk_level != "normal"


class CorrelationGuard:
    """
    Correlation-aware loss detection and risk management.
    
    Philosophy:
    - Multiple losses in short window = potential correlated exposure
    - Same sector/factor = higher correlation risk
    - Always explain WHY halt occurred
    
    Safe defaults:
    - Window: 15 minutes
    - Trigger: 2 losses
    - Reduction: 50% on trigger
    - Halt: 3+ losses in window
    """
    
    # Defaults
    DEFAULT_WINDOW_MINUTES = 15
    DEFAULT_TRIGGER_COUNT = 2
    DEFAULT_HALT_COUNT = 3
    DEFAULT_REDUCE_MULTIPLIER = 0.5
    DEFAULT_COOLDOWN_MINUTES = 60
    MAX_LOSS_HISTORY = 100
    
    def __init__(self):
        self._logger = get_logger()
        self._settings = load_settings()
        
        config = self._settings.get("correlation_guard", {})
        self._enabled = config.get("enabled", True)
        self._window_minutes = config.get("window_minutes", self.DEFAULT_WINDOW_MINUTES)
        self._trigger_count = config.get("trigger_count", self.DEFAULT_TRIGGER_COUNT)
        self._halt_count = config.get("halt_count", self.DEFAULT_HALT_COUNT)
        self._reduce_mult = config.get("reduce_multiplier", self.DEFAULT_REDUCE_MULTIPLIER)
        self._cooldown_minutes = config.get("cooldown_minutes", self.DEFAULT_COOLDOWN_MINUTES)
        
        # State
        self._state = CorrelationGuardState()
        self._loss_history: deque = deque(maxlen=self.MAX_LOSS_HISTORY)
        
        self._logger.log("correlation_guard_init", {
            "enabled": self._enabled,
            "window_minutes": self._window_minutes,
            "trigger_count": self._trigger_count,
            "halt_count": self._halt_count
        })
    
    def record_loss(
        self,
        symbol: str,
        bot_id: str,
        loss_usd: float,
        loss_pct: float,
        sector: Optional[str] = None,
        factor_profile: Optional[str] = None
    ) -> CorrelationGuardState:
        """
        Record a losing trade and evaluate correlation risk.
        
        Args:
            symbol: Trading symbol
            bot_id: Bot that took the loss
            loss_usd: Dollar loss (positive number)
            loss_pct: Percentage loss (positive number)
            sector: Optional sector classification
            factor_profile: Optional factor exposure (momentum, value, etc.)
            
        Returns:
            Updated CorrelationGuardState
        """
        if not self._enabled:
            return self._state
        
        # Create loss event
        event = LossEvent(
            timestamp=datetime.utcnow(),
            symbol=symbol,
            bot_id=bot_id,
            loss_usd=abs(loss_usd),
            loss_pct=abs(loss_pct),
            sector=sector,
            factor_profile=factor_profile
        )
        
        self._loss_history.append(event)
        
        self._logger.log("correlation_guard_loss_recorded", event.to_dict())
        
        # Evaluate correlation
        return self._evaluate()
    
    def _evaluate(self) -> CorrelationGuardState:
        """Evaluate current loss pattern for correlation risk."""
        now = datetime.utcnow()
        window_start = now - timedelta(minutes=self._window_minutes)
        
        # Get losses in window
        recent = [
            e for e in self._loss_history 
            if e.timestamp >= window_start
        ]
        
        if len(recent) < self._trigger_count:
            # Reset if cooldown expired
            if self._state.cooldown_until and now > self._state.cooldown_until:
                self._reset_state()
            return self._state
        
        # Analyze correlation
        unique_symbols = set(e.symbol for e in recent)
        unique_bots = set(e.bot_id for e in recent)
        unique_sectors = set(e.sector for e in recent if e.sector)
        unique_factors = set(e.factor_profile for e in recent if e.factor_profile)
        
        total_loss = sum(e.loss_usd for e in recent)
        
        # Determine severity
        correlation_reason = self._build_reason(
            recent, unique_symbols, unique_bots, unique_sectors, unique_factors
        )
        
        if len(recent) >= self._halt_count:
            # Full halt
            self._state = CorrelationGuardState(
                risk_level="halted",
                risk_multiplier=0.0,
                reason=correlation_reason,
                triggered_at=now,
                cooldown_until=now + timedelta(minutes=self._cooldown_minutes * 2),
                recent_losses=[e.to_dict() for e in recent]
            )
            
            self._logger.log("correlation_guard_halt", {
                "reason": correlation_reason,
                "loss_count": len(recent),
                "total_loss_usd": total_loss,
                "unique_symbols": list(unique_symbols),
                "unique_bots": list(unique_bots),
                "cooldown_minutes": self._cooldown_minutes * 2
            })
            
        elif len(recent) >= self._trigger_count:
            # Risk reduction
            self._state = CorrelationGuardState(
                risk_level="reduced",
                risk_multiplier=self._reduce_mult,
                reason=correlation_reason,
                triggered_at=now,
                cooldown_until=now + timedelta(minutes=self._cooldown_minutes),
                recent_losses=[e.to_dict() for e in recent]
            )
            
            self._logger.log("correlation_guard_reduce", {
                "reason": correlation_reason,
                "loss_count": len(recent),
                "total_loss_usd": total_loss,
                "risk_multiplier": self._reduce_mult,
                "cooldown_minutes": self._cooldown_minutes
            })
        
        # Persist state
        self._persist_state()
        
        return self._state
    
    def _build_reason(
        self,
        losses: List[LossEvent],
        symbols: Set[str],
        bots: Set[str],
        sectors: Set[str],
        factors: Set[str]
    ) -> str:
        """Build detailed reason for correlation trigger."""
        parts = []
        
        parts.append(f"{len(losses)}_losses_in_{self._window_minutes}min")
        
        if len(symbols) == 1:
            parts.append(f"same_symbol_{list(symbols)[0]}")
        else:
            parts.append(f"{len(symbols)}_different_symbols")
        
        if len(bots) == 1:
            parts.append(f"single_bot_{list(bots)[0]}")
        else:
            parts.append(f"{len(bots)}_bots_affected")
        
        if sectors and len(sectors) == 1:
            parts.append(f"same_sector_{list(sectors)[0]}")
        
        if factors and len(factors) == 1:
            parts.append(f"same_factor_{list(factors)[0]}")
        
        total_loss = sum(e.loss_usd for e in losses)
        parts.append(f"total_loss_${total_loss:.2f}")
        
        return "|".join(parts)
    
    def _reset_state(self):
        """Reset to normal state after cooldown."""
        old_state = self._state
        self._state = CorrelationGuardState()
        
        self._logger.log("correlation_guard_reset", {
            "previous_level": old_state.risk_level,
            "previous_reason": old_state.reason
        })
        
        self._persist_state()
    
    def _persist_state(self):
        """Persist state to durable storage."""
        set_state("correlation_guard.state", {
            "risk_level": self._state.risk_level,
            "risk_multiplier": self._state.risk_multiplier,
            "reason": self._state.reason,
            "triggered_at": self._state.triggered_at.isoformat() if self._state.triggered_at else None,
            "cooldown_until": self._state.cooldown_until.isoformat() if self._state.cooldown_until else None
        })
    
    def get_state(self) -> CorrelationGuardState:
        """Get current guard state."""
        # Check cooldown
        now = datetime.utcnow()
        if self._state.cooldown_until and now > self._state.cooldown_until:
            self._reset_state()
        return self._state
    
    def get_risk_multiplier(self) -> float:
        """Get current risk multiplier (1.0 = normal, 0.0 = halted)."""
        state = self.get_state()
        return state.risk_multiplier
    
    def should_allow_entry(self) -> bool:
        """Check if new entries are allowed."""
        state = self.get_state()
        return state.risk_level != "halted"
    
    def clear(self):
        """Manually clear guard state (for recovery)."""
        self._state = CorrelationGuardState()
        self._loss_history.clear()
        self._persist_state()
        self._logger.log("correlation_guard_cleared", {"manual": True})


# Singleton
_correlation_guard: Optional[CorrelationGuard] = None


def get_correlation_guard() -> CorrelationGuard:
    """Get or create CorrelationGuard singleton."""
    global _correlation_guard
    if _correlation_guard is None:
        _correlation_guard = CorrelationGuard()
    return _correlation_guard
