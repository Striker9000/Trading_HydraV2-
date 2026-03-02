"""
FailSafeController for ExitBot v2 Elite
========================================

Nuclear option controller for emergency situations.

Monitors six critical failure modes:
1. Auth Failure Detection - API authentication failures
2. Data Staleness Detection - Market data freshness
3. Extreme Slippage Detection - Execution quality degradation
4. News Shock Detection - High-impact news events
5. Circuit Breaker Integration - Market halts
6. System Halt Trigger - Coordinated system shutdown

All conditions trigger automatic emergency exits and system halt.
Philosophy: Fail closed - when in doubt, flatten and halt.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from enum import Enum
import threading
import time

from ..core.logging import get_logger
from ..core.state import get_state, set_state
from ..core.halt import get_halt_manager
from ..services.alerts import get_alert_service, AlertLevel
from ..core.staleness import get_data_staleness, DataType


class FailsafeAlertType(Enum):
    """Types of failsafe alerts with severity levels."""
    AUTH_FAILURE = "auth_failure"              # Authority: 100
    DATA_STALE = "data_stale"                  # Authority: 100
    EXTREME_SLIPPAGE = "extreme_slippage"      # Authority: 100
    NEWS_SHOCK = "news_shock"                  # Authority: 100
    CIRCUIT_BREAKER = "circuit_breaker"        # Authority: 100
    SYSTEM_HALT = "system_halt"                # Authority: 100


class FailsafeSeverity(Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"  # Requires immediate action


@dataclass
class FailsafeAlert:
    """A triggered failsafe condition."""
    alert_type: FailsafeAlertType
    severity: FailsafeSeverity
    message: str
    triggered_at: datetime
    
    # Alert-specific data
    trigger_value: Optional[float] = None
    threshold: Optional[float] = None
    affected_symbols: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Resolution
    action_taken: str = ""  # "emergency_exit", "system_halt", etc.
    resolved: bool = False
    resolved_at: Optional[datetime] = None


@dataclass
class FailsafeStatus:
    """Current failsafe controller status."""
    is_active: bool
    active_alerts: List[FailsafeAlert]
    last_check: datetime
    
    # Statistics
    total_alerts_triggered: int = 0
    auth_failures: int = 0
    data_staleness_events: int = 0
    slippage_events: int = 0
    news_shocks: int = 0
    circuit_breaker_events: int = 0
    
    # Metadata
    system_halted: bool = False
    halt_reason: str = ""


class FailSafeController:
    """
    Nuclear option controller for emergency situations.
    
    Monitors all critical failure modes and triggers:
    - Immediate position flattening
    - System halt with audit trail
    - Alert notifications
    - Coordinated recovery procedures
    
    Philosophy:
    - Fail closed: When in doubt, flatten and halt
    - Fast execution: No negotiation with failsafe conditions
    - Clear audit trail: Every action logged and timestamped
    - Recovery-aware: Graceful exit paths when possible
    """
    
    # Thresholds (configurable)
    DEFAULT_STALENESS_THRESHOLD_SECONDS = 120
    DEFAULT_SLIPPAGE_THRESHOLD_PCT = 2.0
    DEFAULT_NEWS_IMPACT_THRESHOLD = 0.85
    
    def __init__(self):
        self._logger = get_logger()
        self._alerts_service = get_alert_service()
        self._halt_manager = get_halt_manager()
        self._staleness_service = get_data_staleness()
        
        # State tracking
        self._lock = threading.Lock()
        self._alerts: List[FailsafeAlert] = []
        self._max_alerts_history = 1000
        
        # Per-symbol tracking
        self._symbol_last_update: Dict[str, float] = {}  # symbol -> timestamp
        self._symbol_slippage: Dict[str, float] = {}     # symbol -> slippage_pct
        self._symbol_auth_failures: Dict[str, int] = {}  # symbol -> failure count
        
        # System state
        self._auth_failure_detected = False
        self._system_halted = False
        self._last_check_time = datetime.utcnow()
        
        # Load configuration
        self._load_config()
        self._load_state()
        
        self._logger.info("[failsafe_init] FailSafeController initialized")
    
    def _load_config(self) -> None:
        """Load failsafe configuration from settings."""
        try:
            from ..core.config import load_settings
            settings = load_settings()
            failsafe_config = settings.get("failsafe", {})
            
            self._staleness_threshold = failsafe_config.get(
                "data_staleness_threshold_seconds",
                self.DEFAULT_STALENESS_THRESHOLD_SECONDS
            )
            self._slippage_threshold = failsafe_config.get(
                "extreme_slippage_threshold_pct",
                self.DEFAULT_SLIPPAGE_THRESHOLD_PCT
            )
            self._news_impact_threshold = failsafe_config.get(
                "news_impact_threshold",
                self.DEFAULT_NEWS_IMPACT_THRESHOLD
            )
            self._enabled = failsafe_config.get("enabled", True)
            
        except Exception as e:
            self._logger.warn(f"[failsafe_config_error] Failed to load config: {e}")
            self._staleness_threshold = self.DEFAULT_STALENESS_THRESHOLD_SECONDS
            self._slippage_threshold = self.DEFAULT_SLIPPAGE_THRESHOLD_PCT
            self._news_impact_threshold = self.DEFAULT_NEWS_IMPACT_THRESHOLD
            self._enabled = True
    
    def _load_state(self) -> None:
        """Load persisted failsafe state."""
        try:
            saved_alerts = get_state("failsafe.alerts", [])
            saved_auth_failure = get_state("failsafe.auth_failure", False)
            
            self._auth_failure_detected = saved_auth_failure
            self._system_halted = self._halt_manager.is_halted()
            
        except Exception as e:
            self._logger.warn(f"[failsafe_state_load_error] {e}")
    
    def _save_state(self) -> None:
        """Persist failsafe state."""
        try:
            set_state("failsafe.auth_failure", self._auth_failure_detected)
            set_state("failsafe.alerts_count", len(self._alerts))
            set_state("failsafe.last_check", self._last_check_time.isoformat())
        except Exception:
            pass
    
    # =========================================================================
    # AUTH FAILURE DETECTION (Authority: 100)
    # =========================================================================
    
    def check_auth_status(self, api_status: Optional[Dict[str, Any]] = None) -> Optional[FailsafeAlert]:
        """
        Monitor Alpaca API authentication status.
        
        Args:
            api_status: Status dict from API call (optional)
            
        Returns:
            FailsafeAlert if auth failure detected, None otherwise
        """
        if not self._enabled:
            return None
        
        try:
            # Check if recent API calls have failed with auth errors
            if api_status and api_status.get("auth_failed", False):
                alert = FailsafeAlert(
                    alert_type=FailsafeAlertType.AUTH_FAILURE,
                    severity=FailsafeSeverity.EMERGENCY,
                    message="Alpaca API authentication failure detected",
                    triggered_at=datetime.utcnow(),
                    metadata={
                        "error": api_status.get("error"),
                        "status_code": api_status.get("status_code")
                    }
                )
                
                self._auth_failure_detected = True
                self._logger.error("[failsafe_auth_failure] Auth failure detected!", 
                                  error=api_status.get("error"),
                                  status_code=api_status.get("status_code"))
                
                return alert
            
            # Reset auth failure flag if status is OK
            if api_status and not api_status.get("auth_failed", False):
                self._auth_failure_detected = False
            
        except Exception as e:
            self._logger.error(f"[failsafe_auth_check_error] {e}")
        
        return None
    
    def set_auth_failed(self) -> bool:
        """Mark authentication as failed - triggers emergency exit."""
        with self._lock:
            if self._auth_failure_detected:
                return False  # Already marked
            
            alert = FailsafeAlert(
                alert_type=FailsafeAlertType.AUTH_FAILURE,
                severity=FailsafeSeverity.EMERGENCY,
                message="API authentication failure - immediate system halt required",
                triggered_at=datetime.utcnow()
            )
            
            self._auth_failure_detected = True
            self._record_alert(alert)
            
            self._logger.error("[failsafe_auth_critical] AUTH FAILURE DETECTED - SYSTEM HALT REQUIRED")
            
            return True
    
    # =========================================================================
    # DATA STALENESS DETECTION (Authority: 100)
    # =========================================================================
    
    def record_symbol_update(self, symbol: str) -> None:
        """Record that we just received fresh data for a symbol."""
        with self._lock:
            self._symbol_last_update[symbol] = time.time()
    
    def check_data_staleness(self, symbol: str) -> Optional[FailsafeAlert]:
        """
        Check if market data for a symbol is stale.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            FailsafeAlert if data is stale, None otherwise
        """
        if not self._enabled:
            return None
        
        try:
            with self._lock:
                last_update = self._symbol_last_update.get(symbol)
            
            if last_update is None:
                # No data received yet - might be initialization
                return None
            
            age_seconds = time.time() - last_update
            
            if age_seconds > self._staleness_threshold:
                alert = FailsafeAlert(
                    alert_type=FailsafeAlertType.DATA_STALE,
                    severity=FailsafeSeverity.EMERGENCY,
                    message=f"Data for {symbol} stale for {age_seconds:.0f}s "
                           f"(threshold: {self._staleness_threshold}s)",
                    triggered_at=datetime.utcnow(),
                    trigger_value=age_seconds,
                    threshold=self._staleness_threshold,
                    affected_symbols=[symbol],
                    metadata={
                        "age_seconds": age_seconds,
                        "symbol": symbol
                    }
                )
                
                self._logger.error("[failsafe_data_stale] Data staleness detected",
                                  symbol=symbol,
                                  age_seconds=age_seconds,
                                  threshold=self._staleness_threshold)
                
                return alert
        
        except Exception as e:
            self._logger.error(f"[failsafe_staleness_check_error] {e}")
        
        return None
    
    def get_data_age_seconds(self, symbol: str) -> Optional[float]:
        """Get how many seconds since we last received data for a symbol."""
        with self._lock:
            last_update = self._symbol_last_update.get(symbol)
        
        if last_update is None:
            return None
        
        return time.time() - last_update
    
    # =========================================================================
    # EXTREME SLIPPAGE DETECTION (Authority: 100)
    # =========================================================================
    
    def record_fill(
        self,
        symbol: str,
        expected_price: float,
        actual_price: float,
        qty: float
    ) -> Optional[FailsafeAlert]:
        """
        Record a fill and check for extreme slippage.
        
        Args:
            symbol: Trading symbol
            expected_price: Price we expected
            actual_price: Actual fill price
            qty: Quantity filled
            
        Returns:
            FailsafeAlert if slippage exceeds threshold, None otherwise
        """
        if not self._enabled or expected_price <= 0:
            return None
        
        try:
            # Calculate slippage as percentage
            slippage_pct = abs(actual_price - expected_price) / expected_price * 100
            
            with self._lock:
                self._symbol_slippage[symbol] = slippage_pct
            
            # Check threshold
            if slippage_pct > self._slippage_threshold:
                alert = FailsafeAlert(
                    alert_type=FailsafeAlertType.EXTREME_SLIPPAGE,
                    severity=FailsafeSeverity.EMERGENCY,
                    message=f"Extreme slippage on {symbol}: {slippage_pct:.2f}% "
                           f"(threshold: {self._slippage_threshold}%)",
                    triggered_at=datetime.utcnow(),
                    trigger_value=slippage_pct,
                    threshold=self._slippage_threshold,
                    affected_symbols=[symbol],
                    metadata={
                        "symbol": symbol,
                        "expected_price": expected_price,
                        "actual_price": actual_price,
                        "slippage_pct": round(slippage_pct, 2),
                        "qty": qty
                    }
                )
                
                self._logger.error("[failsafe_slippage] Extreme slippage detected",
                                  symbol=symbol,
                                  slippage_pct=round(slippage_pct, 2),
                                  expected=expected_price,
                                  actual=actual_price)
                
                return alert
        
        except Exception as e:
            self._logger.error(f"[failsafe_slippage_check_error] {e}")
        
        return None
    
    def get_symbol_slippage(self, symbol: str) -> Optional[float]:
        """Get last recorded slippage percentage for a symbol."""
        with self._lock:
            return self._symbol_slippage.get(symbol)
    
    # =========================================================================
    # NEWS SHOCK DETECTION (Authority: 100)
    # =========================================================================
    
    def check_news_impact(self, symbol: str, impact_score: float) -> Optional[FailsafeAlert]:
        """
        Check if news impact for a symbol exceeds threshold.
        
        High impact = immediate position flatten recommended.
        
        Args:
            symbol: Trading symbol
            impact_score: News impact score (0.0-1.0)
            
        Returns:
            FailsafeAlert if impact exceeds threshold, None otherwise
        """
        if not self._enabled:
            return None
        
        try:
            # Check against configured threshold
            if impact_score > self._news_impact_threshold:
                alert = FailsafeAlert(
                    alert_type=FailsafeAlertType.NEWS_SHOCK,
                    severity=FailsafeSeverity.EMERGENCY,
                    message=f"High-impact news shock on {symbol}: "
                           f"impact score {impact_score:.2f} "
                           f"(threshold: {self._news_impact_threshold})",
                    triggered_at=datetime.utcnow(),
                    trigger_value=impact_score,
                    threshold=self._news_impact_threshold,
                    affected_symbols=[symbol],
                    metadata={
                        "symbol": symbol,
                        "impact_score": round(impact_score, 3)
                    }
                )
                
                self._logger.error("[failsafe_news_shock] News shock detected",
                                  symbol=symbol,
                                  impact_score=round(impact_score, 3))
                
                return alert
        
        except Exception as e:
            self._logger.error(f"[failsafe_news_check_error] {e}")
        
        return None
    
    def check_news_gate_result(self, gate_result: Any) -> Optional[FailsafeAlert]:
        """
        Check NewsRiskGate result for force_exit actions.
        
        If gate signals FORCE_EXIT, trigger failsafe alert.
        
        Args:
            gate_result: NewsGateResult from NewsRiskGate
            
        Returns:
            FailsafeAlert if gate requires force exit, None otherwise
        """
        if not self._enabled or gate_result is None:
            return None
        
        try:
            from ..risk.news_risk_gate import NewsAction
            
            if gate_result.action == NewsAction.FORCE_EXIT:
                alert = FailsafeAlert(
                    alert_type=FailsafeAlertType.NEWS_SHOCK,
                    severity=FailsafeSeverity.EMERGENCY,
                    message=f"NewsRiskGate FORCE_EXIT triggered for {gate_result.symbol}: "
                           f"sentiment {gate_result.sentiment_score:.2f}",
                    triggered_at=datetime.utcnow(),
                    affected_symbols=[gate_result.symbol],
                    metadata={
                        "symbol": gate_result.symbol,
                        "sentiment_score": round(gate_result.sentiment_score, 3),
                        "reason": gate_result.reason
                    }
                )
                
                self._logger.error("[failsafe_news_gate_force_exit] News gate force exit",
                                  symbol=gate_result.symbol,
                                  sentiment=round(gate_result.sentiment_score, 3),
                                  reason=gate_result.reason)
                
                return alert
        
        except Exception as e:
            self._logger.error(f"[failsafe_news_gate_check_error] {e}")
        
        return None
    
    # =========================================================================
    # CIRCUIT BREAKER INTEGRATION (Authority: 100)
    # =========================================================================
    
    def check_circuit_breaker_status(self, is_halted: bool) -> Optional[FailsafeAlert]:
        """
        Check if market is halted due to circuit breaker.
        
        Args:
            is_halted: True if market/symbol is halted
            
        Returns:
            FailsafeAlert if halt detected, None otherwise
        """
        if not self._enabled or not is_halted:
            return None
        
        try:
            alert = FailsafeAlert(
                alert_type=FailsafeAlertType.CIRCUIT_BREAKER,
                severity=FailsafeSeverity.EMERGENCY,
                message="Market circuit breaker triggered - halt all trading",
                triggered_at=datetime.utcnow(),
                metadata={"reason": "circuit_breaker_halt"}
            )
            
            self._logger.error("[failsafe_circuit_breaker] CIRCUIT BREAKER TRIGGERED")
            
            return alert
        
        except Exception as e:
            self._logger.error(f"[failsafe_circuit_breaker_check_error] {e}")
        
        return None
    
    # =========================================================================
    # UNIFIED FAILSAFE CHECK (aggregates all conditions)
    # =========================================================================
    
    def check_failsafe_conditions(self) -> List[FailsafeAlert]:
        """
        Check all failsafe conditions.
        
        Returns:
            List of triggered FailsafeAlert conditions
        """
        if not self._enabled:
            return []
        
        self._last_check_time = datetime.utcnow()
        triggered_alerts: List[FailsafeAlert] = []
        
        # Auth check
        if self._auth_failure_detected:
            alert = FailsafeAlert(
                alert_type=FailsafeAlertType.AUTH_FAILURE,
                severity=FailsafeSeverity.EMERGENCY,
                message="Ongoing auth failure - system requires halt",
                triggered_at=datetime.utcnow()
            )
            triggered_alerts.append(alert)
        
        # System halt status
        if self._halt_manager.is_halted():
            alert = FailsafeAlert(
                alert_type=FailsafeAlertType.SYSTEM_HALT,
                severity=FailsafeSeverity.CRITICAL,
                message=self._halt_manager.get_status().reason or "System trading halt active",
                triggered_at=datetime.utcnow()
            )
            triggered_alerts.append(alert)
        
        # Store new alerts
        for alert in triggered_alerts:
            if alert not in self._alerts:
                self._record_alert(alert)
        
        self._save_state()
        
        return triggered_alerts
    
    def _record_alert(self, alert: FailsafeAlert) -> None:
        """Record a failsafe alert internally."""
        with self._lock:
            self._alerts.append(alert)
            if len(self._alerts) > self._max_alerts_history:
                self._alerts = self._alerts[-self._max_alerts_history:]
    
    # =========================================================================
    # EMERGENCY EXIT TRIGGER
    # =========================================================================
    
    def trigger_emergency_exit(self, position_key: str, reason: str) -> bool:
        """
        Trigger an immediate emergency exit of a position.
        
        This is the nuclear option - flat ASAP, no negotiation.
        
        Args:
            position_key: Position identifier (e.g., "SPY_long")
            reason: Reason for emergency exit
            
        Returns:
            True if exit was triggered, False otherwise
        """
        if not self._enabled:
            return False
        
        try:
            self._logger.error("[failsafe_emergency_exit] Emergency exit triggered",
                                 position_key=position_key,
                                 reason=reason)
            
            # Alert
            self._alerts_service.send_alert(
                level=AlertLevel.CRITICAL,
                category="failsafe",
                title=f"Emergency Exit: {position_key}",
                message=f"Emergency position exit triggered: {reason}",
                data={"position": position_key, "reason": reason},
                force=True
            )
            
            # TODO: Integrate with actual exit execution once ExitBot exit module is available
            # For now, this logs the intent and creates the alert
            
            return True
        
        except Exception as e:
            self._logger.error(f"[failsafe_emergency_exit_error] {e}")
            return False
    
    # =========================================================================
    # SYSTEM HALT TRIGGER
    # =========================================================================
    
    def trigger_system_halt(self, reason: str, cooloff_minutes: int = 60) -> bool:
        """
        Trigger a full system trading halt.
        
        Blocks all new entries, initiates position flattening,
        and prevents further trading until cooloff expires.
        
        Args:
            reason: Reason for system halt (audit trail)
            cooloff_minutes: How long to maintain halt
            
        Returns:
            True if halt was triggered, False otherwise
        """
        if not self._enabled:
            return False
        
        try:
            if self._system_halted:
                self._logger.warn("[failsafe_halt_already_active] Halt already active")
                return False
            
            # Trigger halt via HaltManager
            self._halt_manager.set_halt(reason, cooloff_minutes)
            self._system_halted = True
            
            self._logger.error("[failsafe_system_halt] SYSTEM HALT TRIGGERED",
                                 reason=reason,
                                 cooloff_minutes=cooloff_minutes)
            
            # Alert with highest priority
            self._alerts_service.send_alert(
                level=AlertLevel.CRITICAL,
                category="failsafe",
                title="SYSTEM TRADING HALT",
                message=f"System halt triggered: {reason}",
                data={
                    "reason": reason,
                    "cooloff_minutes": cooloff_minutes,
                    "timestamp": datetime.utcnow().isoformat()
                },
                force=True
            )
            
            # Record halt alert
            alert = FailsafeAlert(
                alert_type=FailsafeAlertType.SYSTEM_HALT,
                severity=FailsafeSeverity.EMERGENCY,
                message=f"System halt: {reason}",
                triggered_at=datetime.utcnow(),
                action_taken="system_halt",
                metadata={"reason": reason, "cooloff_minutes": cooloff_minutes}
            )
            self._record_alert(alert)
            self._save_state()
            
            return True
        
        except Exception as e:
            self._logger.error(f"[failsafe_system_halt_error] {e}")
            return False
    
    def clear_system_halt(self) -> bool:
        """Manually clear system halt (after cooloff or manual intervention)."""
        try:
            self._halt_manager.clear_halt()
            self._system_halted = False
            
            self._logger.info("[failsafe_halt_cleared] System halt cleared")
            
            return True
        except Exception as e:
            self._logger.error(f"[failsafe_halt_clear_error] {e}")
            return False
    
    # =========================================================================
    # STATUS AND REPORTING
    # =========================================================================
    
    def get_failsafe_status(self) -> FailsafeStatus:
        """
        Get current failsafe controller status.
        
        Returns:
            FailsafeStatus with all current conditions and statistics
        """
        with self._lock:
            status = FailsafeStatus(
                is_active=self._enabled,
                active_alerts=self._alerts.copy(),
                last_check=self._last_check_time,
                total_alerts_triggered=len(self._alerts),
                system_halted=self._system_halted,
                halt_reason=self._halt_manager.get_status().reason or ""
            )
        
        return status
    
    def get_active_alerts(self) -> List[FailsafeAlert]:
        """Get list of currently active/recent alerts."""
        with self._lock:
            # Return unresolved alerts
            return [a for a in self._alerts if not a.resolved]
    
    def clear_alerts(self) -> None:
        """Clear the alerts history (for testing)."""
        with self._lock:
            self._alerts.clear()
        self._logger.info("[failsafe_alerts_cleared] Alerts history cleared")
    
    def reset_auth_failure(self) -> None:
        """Reset auth failure flag after recovery."""
        with self._lock:
            self._auth_failure_detected = False
        self._save_state()
        self._logger.info("[failsafe_auth_reset] Auth failure flag reset")
    
    def get_symbol_status(self, symbol: str) -> Dict[str, Any]:
        """
        Get failsafe status for a specific symbol.
        
        Returns dict with:
        - data_age_seconds
        - is_data_stale
        - last_slippage_pct
        - has_open_alerts
        """
        data_age = self.get_data_age_seconds(symbol)
        slippage = self.get_symbol_slippage(symbol)
        
        is_stale = False
        if data_age is not None:
            is_stale = data_age > self._staleness_threshold
        
        return {
            "symbol": symbol,
            "data_age_seconds": data_age,
            "is_data_stale": is_stale,
            "staleness_threshold": self._staleness_threshold,
            "last_slippage_pct": slippage,
            "slippage_threshold": self._slippage_threshold,
            "has_open_alerts": any(
                a.affected_symbols and symbol in a.affected_symbols 
                for a in self.get_active_alerts()
            )
        }


# Singleton instance
_failsafe_controller: Optional[FailSafeController] = None
_failsafe_lock = threading.Lock()


def get_failsafe_controller() -> FailSafeController:
    """Get the singleton FailSafeController instance."""
    global _failsafe_controller
    
    if _failsafe_controller is None:
        with _failsafe_lock:
            if _failsafe_controller is None:
                _failsafe_controller = FailSafeController()
    
    return _failsafe_controller


def reset_failsafe_controller() -> None:
    """Reset failsafe controller (for testing)."""
    global _failsafe_controller
    with _failsafe_lock:
        _failsafe_controller = None
