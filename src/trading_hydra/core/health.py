"""Health monitoring for API and data freshness"""
from datetime import datetime
from typing import Dict, Any, Optional
from dataclasses import dataclass

from .state import get_state, set_state
from .config import load_settings
from .logging import get_logger
from .staleness import get_data_staleness, DataType


@dataclass
class HealthSnapshot:
    ok: bool
    reason: str
    api_failures: int
    last_price_tick: Optional[str]
    stale_seconds: float
    critical_auth_failure: bool = False
    connection_failures: int = 0


class HealthMonitor:
    def __init__(self):
        self._settings = None
        self._logger = get_logger()
    
    @property
    def settings(self) -> Dict[str, Any]:
        if self._settings is None:
            self._settings = load_settings()
        return self._settings
    
    def record_api_failure(self, error: str = "") -> None:
        count = get_state("health.api_failure_count", 0) or 0
        set_state("health.api_failure_count", count + 1)
        set_state("health.last_api_failure", datetime.utcnow().isoformat() + "Z")
        self._logger.warn(f"API failure recorded, count: {count + 1}", error=error)
    
    def record_critical_auth_failure(self, error: str = "") -> None:
        """Record a critical auth failure (401/403) - triggers immediate halt"""
        set_state("health.critical_auth_failure", True)
        set_state("health.critical_auth_error", error)
        set_state("health.critical_auth_timestamp", datetime.utcnow().isoformat() + "Z")
        self._logger.error(f"CRITICAL AUTH FAILURE - immediate halt required: {error}")
    
    def record_connection_failure(self, error: str = "") -> None:
        """Record connection/timeout failures - may indicate broker outage"""
        count = get_state("health.connection_failure_count", 0) or 0
        set_state("health.connection_failure_count", count + 1)
        set_state("health.last_connection_failure", datetime.utcnow().isoformat() + "Z")
        self._logger.warn(f"Connection failure recorded, count: {count + 1}", error=error)
    
    def clear_auth_failure(self) -> None:
        """Clear critical auth failure state (for manual recovery)"""
        set_state("health.critical_auth_failure", False)
        set_state("health.critical_auth_error", "")
        self._logger.log("critical_auth_failure_cleared", {})
    
    def record_price_tick(self) -> None:
        set_state("health.last_price_tick", datetime.utcnow().isoformat() + "Z")
        set_state("health.api_failure_count", 0)
        set_state("health.connection_failure_count", 0)
    
    def get_snapshot(self) -> HealthSnapshot:
        health_config = self.settings.get("health", {})
        max_failures = health_config.get("max_api_failures_in_window", 5)
        max_connection_failures = health_config.get("max_connection_failures_in_window", 3)
        
        # Use context-aware staleness threshold based on market hours
        staleness_service = get_data_staleness()
        stale_threshold = staleness_service.get_ttl(DataType.QUOTE)
        
        api_failures = get_state("health.api_failure_count", 0) or 0
        connection_failures = get_state("health.connection_failure_count", 0) or 0
        critical_auth_failure = get_state("health.critical_auth_failure", False) or False
        last_tick_str = get_state("health.last_price_tick")
        
        stale_seconds = 0.0
        
        if last_tick_str:
            try:
                last_tick = datetime.fromisoformat(last_tick_str.replace("Z", "+00:00"))
                stale_seconds = (datetime.now(last_tick.tzinfo) - last_tick).total_seconds()
            except (ValueError, AttributeError, TypeError):
                pass  # Malformed timestamp; stale_seconds stays 0.0
        
        ok = True
        reason = "OK"
        
        # Critical auth failure (401/403) is IMMEDIATE halt - no threshold
        if critical_auth_failure:
            ok = False
            auth_error = get_state("health.critical_auth_error", "")
            reason = f"CRITICAL_AUTH_FAILURE: {auth_error}"
        elif api_failures >= max_failures:
            ok = False
            reason = f"API failures ({api_failures}) >= max ({max_failures})"
        elif connection_failures >= max_connection_failures:
            ok = False
            reason = f"Connection failures ({connection_failures}) >= max ({max_connection_failures})"
        elif last_tick_str and stale_seconds > stale_threshold:
            ok = False
            session_phase = staleness_service.get_session_phase().value
            reason = f"Data stale ({stale_seconds:.0f}s > {stale_threshold:.0f}s TTL, {session_phase})"
        
        return HealthSnapshot(
            ok=ok,
            reason=reason,
            api_failures=api_failures,
            last_price_tick=last_tick_str,
            stale_seconds=stale_seconds,
            critical_auth_failure=critical_auth_failure,
            connection_failures=connection_failures
        )


_health_monitor: Optional[HealthMonitor] = None


def get_health_monitor() -> HealthMonitor:
    global _health_monitor
    if _health_monitor is None:
        _health_monitor = HealthMonitor()
    return _health_monitor
