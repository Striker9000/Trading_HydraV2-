"""
=============================================================================
Circuit Breaker - Graceful degradation for external services
=============================================================================

Production-grade circuit breaker pattern for all external service calls.

States:
- CLOSED: Normal operation, requests flow through
- OPEN: Failures detected, requests blocked (fast fail)
- HALF_OPEN: Testing if service recovered

Philosophy:
- Never let a failing external service cascade to trading decisions
- Fail fast when service is known to be down
- Graceful degradation with cached/fallback values
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Optional, Any, Callable
from enum import Enum
import time
import threading

from ..core.logging import get_logger
from ..core.state import get_state, set_state


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitStatus:
    """Status of a circuit breaker."""
    service_name: str
    state: CircuitState
    failure_count: int
    success_count: int
    last_failure_at: Optional[str]
    last_success_at: Optional[str]
    opened_at: Optional[str]
    half_open_at: Optional[str]
    total_failures: int = 0
    total_successes: int = 0
    total_timeouts: int = 0
    avg_response_ms: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "service_name": self.service_name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure_at": self.last_failure_at,
            "last_success_at": self.last_success_at,
            "opened_at": self.opened_at,
            "half_open_at": self.half_open_at,
            "total_failures": self.total_failures,
            "total_successes": self.total_successes,
            "total_timeouts": self.total_timeouts,
            "avg_response_ms": self.avg_response_ms
        }


class CircuitBreaker:
    """
    Circuit breaker for a single service.
    
    Tracks failures and opens circuit when threshold exceeded.
    Automatically tests recovery after cooldown period.
    """
    
    def __init__(
        self,
        service_name: str,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout_seconds: float = 10.0,
        recovery_timeout_seconds: float = 60.0
    ):
        self.service_name = service_name
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout_seconds = timeout_seconds
        self.recovery_timeout = recovery_timeout_seconds
        
        self._logger = get_logger()
        self._lock = threading.Lock()
        
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_at: Optional[datetime] = None
        self._last_success_at: Optional[datetime] = None
        self._opened_at: Optional[datetime] = None
        self._half_open_at: Optional[datetime] = None
        
        self._total_failures = 0
        self._total_successes = 0
        self._total_timeouts = 0
        self._response_times: list = []
        
        self._load_state()
    
    def _load_state(self) -> None:
        """Load persisted circuit state."""
        try:
            key = f"circuit_breaker.{self.service_name}"
            saved = get_state(key, {})
            if saved:
                self._state = CircuitState(saved.get("state", "closed"))
                self._failure_count = saved.get("failure_count", 0)
                self._success_count = saved.get("success_count", 0)
                self._total_failures = saved.get("total_failures", 0)
                self._total_successes = saved.get("total_successes", 0)
                self._total_timeouts = saved.get("total_timeouts", 0)
        except Exception:
            pass
    
    def _save_state(self) -> None:
        """Persist circuit state."""
        try:
            key = f"circuit_breaker.{self.service_name}"
            set_state(key, self.get_status().to_dict())
        except Exception:
            pass
    
    def is_available(self) -> bool:
        """Check if service is available (circuit allows requests)."""
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True
            
            if self._state == CircuitState.OPEN:
                if self._opened_at and (datetime.utcnow() - self._opened_at).total_seconds() > self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_at = datetime.utcnow()
                    self._success_count = 0
                    
                    self._logger.log("circuit_half_open", {
                        "service": self.service_name,
                        "recovery_timeout_sec": self.recovery_timeout
                    })
                    return True
                return False
            
            if self._state == CircuitState.HALF_OPEN:
                return True
            
            return True
    
    def record_success(self, response_time_ms: float = 0.0) -> None:
        """Record a successful call."""
        with self._lock:
            now = datetime.utcnow()
            self._last_success_at = now
            self._success_count += 1
            self._total_successes += 1
            
            if response_time_ms > 0:
                self._response_times.append(response_time_ms)
                if len(self._response_times) > 100:
                    self._response_times = self._response_times[-100:]
            
            if self._state == CircuitState.HALF_OPEN:
                if self._success_count >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._opened_at = None
                    self._half_open_at = None
                    
                    self._logger.log("circuit_closed", {
                        "service": self.service_name,
                        "success_count": self._success_count,
                        "reason": "recovery_confirmed"
                    })
            
            self._save_state()
    
    def record_failure(self, is_timeout: bool = False, error: str = "") -> None:
        """Record a failed call."""
        with self._lock:
            now = datetime.utcnow()
            self._last_failure_at = now
            self._failure_count += 1
            self._total_failures += 1
            
            if is_timeout:
                self._total_timeouts += 1
            
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = now
                self._success_count = 0
                
                self._logger.log("circuit_reopened", {
                    "service": self.service_name,
                    "error": error[:100],
                    "reason": "half_open_failure"
                })
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    self._opened_at = now
                    
                    self._logger.log("circuit_opened", {
                        "service": self.service_name,
                        "failure_count": self._failure_count,
                        "threshold": self.failure_threshold,
                        "error": error[:100]
                    })
            
            self._save_state()
    
    def get_status(self) -> CircuitStatus:
        """Get current circuit status."""
        avg_response = sum(self._response_times) / len(self._response_times) if self._response_times else 0.0
        
        return CircuitStatus(
            service_name=self.service_name,
            state=self._state,
            failure_count=self._failure_count,
            success_count=self._success_count,
            last_failure_at=self._last_failure_at.isoformat() + "Z" if self._last_failure_at else None,
            last_success_at=self._last_success_at.isoformat() + "Z" if self._last_success_at else None,
            opened_at=self._opened_at.isoformat() + "Z" if self._opened_at else None,
            half_open_at=self._half_open_at.isoformat() + "Z" if self._half_open_at else None,
            total_failures=self._total_failures,
            total_successes=self._total_successes,
            total_timeouts=self._total_timeouts,
            avg_response_ms=round(avg_response, 2)
        )
    
    def reset(self) -> None:
        """Manually reset the circuit breaker."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._opened_at = None
            self._half_open_at = None
            self._save_state()
            
            self._logger.log("circuit_reset", {"service": self.service_name})


class CircuitBreakerRegistry:
    """
    Registry of all circuit breakers.
    
    Provides centralized management and monitoring of all external service circuits.
    """
    
    SERVICES = {
        "alpaca_market_data": {"failure_threshold": 5, "timeout": 10.0, "recovery": 60.0},
        "alpaca_trading": {"failure_threshold": 3, "timeout": 15.0, "recovery": 30.0},
        "openai_sentiment": {"failure_threshold": 5, "timeout": 10.0, "recovery": 120.0},
        "openai_analysis": {"failure_threshold": 5, "timeout": 30.0, "recovery": 120.0},
        "yfinance": {"failure_threshold": 5, "timeout": 10.0, "recovery": 60.0},
        "news_api": {"failure_threshold": 5, "timeout": 10.0, "recovery": 120.0},
        "reddit_api": {"failure_threshold": 5, "timeout": 10.0, "recovery": 300.0},
        "youtube_api": {"failure_threshold": 5, "timeout": 10.0, "recovery": 300.0},
    }
    
    def __init__(self):
        self._logger = get_logger()
        self._circuits: Dict[str, CircuitBreaker] = {}
        
        for service, config in self.SERVICES.items():
            self._circuits[service] = CircuitBreaker(
                service_name=service,
                failure_threshold=config["failure_threshold"],
                timeout_seconds=config["timeout"],
                recovery_timeout_seconds=config["recovery"]
            )
        
        self._logger.log("circuit_breaker_registry_init", {
            "services": list(self.SERVICES.keys())
        })
    
    def get(self, service_name: str) -> CircuitBreaker:
        """Get circuit breaker for a service."""
        if service_name not in self._circuits:
            self._circuits[service_name] = CircuitBreaker(service_name)
        return self._circuits[service_name]
    
    def is_available(self, service_name: str) -> bool:
        """Check if a service is available."""
        return self.get(service_name).is_available()
    
    def record_success(self, service_name: str, response_time_ms: float = 0.0) -> None:
        """Record successful call to a service."""
        self.get(service_name).record_success(response_time_ms)
    
    def record_failure(self, service_name: str, is_timeout: bool = False, error: str = "") -> None:
        """Record failed call to a service."""
        self.get(service_name).record_failure(is_timeout, error)
    
    def get_all_status(self) -> Dict[str, CircuitStatus]:
        """Get status of all circuits."""
        return {name: cb.get_status() for name, cb in self._circuits.items()}
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get registry statistics."""
        statuses = self.get_all_status()
        
        open_circuits = [s for s in statuses.values() if s.state == CircuitState.OPEN]
        half_open = [s for s in statuses.values() if s.state == CircuitState.HALF_OPEN]
        
        return {
            "total_circuits": len(self._circuits),
            "open_circuits": len(open_circuits),
            "half_open_circuits": len(half_open),
            "closed_circuits": len(self._circuits) - len(open_circuits) - len(half_open),
            "open_services": [s.service_name for s in open_circuits],
            "half_open_services": [s.service_name for s in half_open],
            "circuits": {name: status.to_dict() for name, status in statuses.items()}
        }


_circuit_registry: Optional[CircuitBreakerRegistry] = None


def get_circuit_registry() -> CircuitBreakerRegistry:
    """Get or create circuit breaker registry singleton."""
    global _circuit_registry
    if _circuit_registry is None:
        _circuit_registry = CircuitBreakerRegistry()
    return _circuit_registry


def circuit_protected(service_name: str, fallback_value: Any = None):
    """
    Decorator to wrap external service calls with circuit breaker.
    
    Usage:
        @circuit_protected("openai_sentiment", fallback_value={"sentiment": 0.0})
        def get_sentiment(text: str) -> dict:
            return openai.analyze(text)
    """
    def decorator(func: Callable):
        def wrapper(*args, **kwargs):
            registry = get_circuit_registry()
            circuit = registry.get(service_name)
            logger = get_logger()
            
            if not circuit.is_available():
                logger.log("circuit_breaker_blocked", {
                    "service": service_name,
                    "fallback_used": True
                })
                return fallback_value
            
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                response_time_ms = (time.time() - start_time) * 1000
                circuit.record_success(response_time_ms)
                return result
            except TimeoutError:
                circuit.record_failure(is_timeout=True, error="timeout")
                return fallback_value
            except Exception as e:
                circuit.record_failure(is_timeout=False, error=str(e))
                return fallback_value
        
        return wrapper
    return decorator
