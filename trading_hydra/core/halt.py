"""Trading halt management for fail-closed safety"""
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from dataclasses import dataclass

from .state import get_state, set_state, delete_state
from .logging import get_logger


@dataclass
class HaltStatus:
    active: bool
    reason: str
    halted_at: Optional[str]
    expires_at: Optional[str]


class HaltManager:
    def __init__(self):
        self._logger = get_logger()
    
    def set_halt(self, reason: str, cooloff_minutes: int = 60) -> None:
        now = datetime.utcnow()
        expires = now + timedelta(minutes=cooloff_minutes)
        
        # GLOBAL_TRADING_HALT is the single source of truth
        set_state("GLOBAL_TRADING_HALT", True)
        set_state("halt.reason", reason)
        set_state("halt.halted_at", now.isoformat() + "Z")
        set_state("halt.expires_at", expires.isoformat() + "Z")
        
        self._logger.log("halt_activated", {
            "reason": reason,
            "cooloff_minutes": cooloff_minutes,
            "expires_at": expires.isoformat() + "Z"
        })
        
        try:
            from ..services.alerts import get_alert_service
            alerts = get_alert_service()
            alerts.alert_halt(reason, 0, 0)
        except Exception:
            pass
    
    def clear_halt(self) -> None:
        delete_state("GLOBAL_TRADING_HALT")
        delete_state("halt.reason")
        delete_state("halt.halted_at")
        delete_state("halt.expires_at")
        self._logger.log("halt_cleared", {})
    
    def clear_if_expired(self) -> bool:
        expires_str = get_state("halt.expires_at")
        if not expires_str:
            return False
        
        try:
            expires = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
            if datetime.now(expires.tzinfo) > expires:
                self._logger.log("halt_expired", {"expires_at": expires_str})
                self.clear_halt()
                return True
        except:
            pass
        
        return False
    
    def is_halted(self) -> bool:
        from .config import load_settings
        
        self.clear_if_expired()
        
        # Check config override first (manual override that can force halt)
        settings = load_settings()
        config_halt = settings.get("trading", {}).get("global_halt", False)
        if config_halt:
            # Config override forces halt into state if not already set
            if not get_state("GLOBAL_TRADING_HALT", False):
                self.set_halt("CONFIG_OVERRIDE: trading.global_halt=true", cooloff_minutes=0)
            return True
        
        # Otherwise check the runtime state
        return get_state("GLOBAL_TRADING_HALT", False) or False
    
    def get_status(self) -> HaltStatus:
        return HaltStatus(
            active=get_state("GLOBAL_TRADING_HALT", False) or False,
            reason=get_state("halt.reason", "") or "",
            halted_at=get_state("halt.halted_at"),
            expires_at=get_state("halt.expires_at")
        )


_halt_manager: Optional[HaltManager] = None


def get_halt_manager() -> HaltManager:
    global _halt_manager
    if _halt_manager is None:
        _halt_manager = HaltManager()
    return _halt_manager
