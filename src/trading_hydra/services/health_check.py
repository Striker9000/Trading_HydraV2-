"""
=============================================================================
Health Check Service - Production readiness monitoring
=============================================================================

Provides comprehensive health checks for all system components:
1. Database connectivity
2. External API status (Alpaca, OpenAI)
3. Circuit breaker states
4. ML model governance status
5. Memory and resource usage

Philosophy:
- Proactive monitoring prevents failures
- Clear status reporting for operations
- Metrics export for alerting integration
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any
import time
import os
import psutil

from ..core.logging import get_logger
from ..core.state import get_state


@dataclass
class ComponentHealth:
    """Health status of a single component."""
    name: str
    status: str  # healthy, degraded, unhealthy, unknown
    latency_ms: float
    details: str
    last_check: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "details": self.details,
            "last_check": self.last_check
        }


@dataclass
class SystemHealth:
    """Overall system health status."""
    status: str  # healthy, degraded, unhealthy
    components: List[ComponentHealth]
    uptime_seconds: float
    memory_usage_pct: float
    cpu_usage_pct: float
    timestamp: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "components": [c.to_dict() for c in self.components],
            "uptime_seconds": self.uptime_seconds,
            "memory_usage_pct": self.memory_usage_pct,
            "cpu_usage_pct": self.cpu_usage_pct,
            "timestamp": self.timestamp
        }


class HealthCheckService:
    """
    Comprehensive health monitoring for the trading system.
    
    Checks all critical components and provides aggregated status.
    """
    
    START_TIME = time.time()
    
    UNHEALTHY_THRESHOLD = 0.5  # > 50% unhealthy components
    DEGRADED_THRESHOLD = 0.2   # > 20% degraded components
    
    def __init__(self):
        self._logger = get_logger()
        self._last_check: Optional[SystemHealth] = None
        
        self._logger.log("health_check_init", {
            "start_time": self.START_TIME
        })
    
    def check_all(self) -> SystemHealth:
        """Run all health checks and return system status."""
        components = []
        
        components.append(self._check_database())
        components.append(self._check_alpaca())
        components.append(self._check_openai())
        components.append(self._check_circuit_breakers())
        components.append(self._check_ml_governance())
        components.append(self._check_state())
        
        unhealthy = sum(1 for c in components if c.status == "unhealthy")
        degraded = sum(1 for c in components if c.status == "degraded")
        total = len(components)
        
        if unhealthy / total > self.UNHEALTHY_THRESHOLD:
            status = "unhealthy"
        elif (degraded + unhealthy) / total > self.DEGRADED_THRESHOLD:
            status = "degraded"
        else:
            status = "healthy"
        
        uptime = time.time() - self.START_TIME
        
        try:
            memory_pct = psutil.virtual_memory().percent
            cpu_pct = psutil.cpu_percent(interval=0.1)
        except Exception:
            memory_pct = 0.0
            cpu_pct = 0.0
        
        health = SystemHealth(
            status=status,
            components=components,
            uptime_seconds=uptime,
            memory_usage_pct=memory_pct,
            cpu_usage_pct=cpu_pct
        )
        
        self._last_check = health
        
        self._logger.log("health_check_complete", {
            "status": status,
            "healthy": total - unhealthy - degraded,
            "degraded": degraded,
            "unhealthy": unhealthy,
            "uptime_hours": round(uptime / 3600, 2)
        })
        
        return health
    
    def _check_database(self) -> ComponentHealth:
        """Check SQLite database health."""
        start = time.time()
        try:
            from ..core.state import _get_db_connection
            conn = _get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            latency = (time.time() - start) * 1000
            
            return ComponentHealth(
                name="database",
                status="healthy",
                latency_ms=latency,
                details="SQLite connection OK"
            )
        except Exception as e:
            return ComponentHealth(
                name="database",
                status="unhealthy",
                latency_ms=0,
                details=f"Connection failed: {str(e)[:100]}"
            )
    
    def _check_alpaca(self) -> ComponentHealth:
        """Check Alpaca API health."""
        start = time.time()
        try:
            from ..core.alpaca_client import get_alpaca_client
            client = get_alpaca_client()
            account = client.get_account()
            latency = (time.time() - start) * 1000
            
            status = "healthy" if account else "degraded"
            
            return ComponentHealth(
                name="alpaca_api",
                status=status,
                latency_ms=latency,
                details=f"Account status: {getattr(account, 'status', 'unknown')}"
            )
        except Exception as e:
            return ComponentHealth(
                name="alpaca_api",
                status="unhealthy",
                latency_ms=0,
                details=f"API error: {str(e)[:100]}"
            )
    
    def _check_openai(self) -> ComponentHealth:
        """Check OpenAI API health."""
        start = time.time()
        try:
            from openai import OpenAI
            
            base_url = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
            api_key = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
            
            if not base_url or not api_key:
                return ComponentHealth(
                    name="openai_api",
                    status="degraded",
                    latency_ms=0,
                    details="Credentials not configured"
                )
            
            client = OpenAI(base_url=base_url, api_key=api_key)
            
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                timeout=5
            )
            
            latency = (time.time() - start) * 1000
            
            return ComponentHealth(
                name="openai_api",
                status="healthy",
                latency_ms=latency,
                details="API responding"
            )
            
        except Exception as e:
            return ComponentHealth(
                name="openai_api",
                status="degraded",
                latency_ms=0,
                details=f"API error: {str(e)[:100]}"
            )
    
    def _check_circuit_breakers(self) -> ComponentHealth:
        """Check circuit breaker status."""
        try:
            from ..risk.circuit_breaker import get_circuit_registry
            registry = get_circuit_registry()
            stats = registry.get_statistics()
            
            open_count = stats.get("open_circuits", 0)
            total = stats.get("total_circuits", 1)
            
            if open_count > total / 2:
                status = "unhealthy"
            elif open_count > 0:
                status = "degraded"
            else:
                status = "healthy"
            
            return ComponentHealth(
                name="circuit_breakers",
                status=status,
                latency_ms=0,
                details=f"{open_count}/{total} circuits open"
            )
            
        except Exception as e:
            return ComponentHealth(
                name="circuit_breakers",
                status="unknown",
                latency_ms=0,
                details=f"Check failed: {str(e)[:100]}"
            )
    
    def _check_ml_governance(self) -> ComponentHealth:
        """Check ML governance status."""
        try:
            from ..risk.ml_governance import get_ml_governance
            gov = get_ml_governance()
            stats = gov.get_statistics()
            
            disabled = stats.get("disabled", 0)
            quarantine = stats.get("quarantine", 0)
            total = stats.get("total_models", 1) or 1
            
            if (disabled + quarantine) > total / 2:
                status = "degraded"
            else:
                status = "healthy"
            
            return ComponentHealth(
                name="ml_governance",
                status=status,
                latency_ms=0,
                details=f"Active: {stats.get('active', 0)}, Disabled: {disabled}"
            )
            
        except Exception as e:
            return ComponentHealth(
                name="ml_governance",
                status="unknown",
                latency_ms=0,
                details=f"Check failed: {str(e)[:100]}"
            )
    
    def _check_state(self) -> ComponentHealth:
        """Check state persistence health."""
        start = time.time()
        try:
            test_key = "_health_check_test"
            from ..core.state import get_state, set_state
            
            set_state(test_key, {"ts": time.time()})
            result = get_state(test_key, {})
            
            latency = (time.time() - start) * 1000
            
            if result and "ts" in result:
                return ComponentHealth(
                    name="state_persistence",
                    status="healthy",
                    latency_ms=latency,
                    details="Read/write OK"
                )
            else:
                return ComponentHealth(
                    name="state_persistence",
                    status="degraded",
                    latency_ms=latency,
                    details="Read returned unexpected data"
                )
                
        except Exception as e:
            return ComponentHealth(
                name="state_persistence",
                status="unhealthy",
                latency_ms=0,
                details=f"Check failed: {str(e)[:100]}"
            )
    
    def get_metrics_export(self) -> Dict[str, Any]:
        """Export metrics in Prometheus-compatible format."""
        if not self._last_check:
            self.check_all()
        
        health = self._last_check
        
        metrics = {
            "trading_hydra_up": 1 if health.status == "healthy" else 0,
            "trading_hydra_uptime_seconds": health.uptime_seconds,
            "trading_hydra_memory_usage_percent": health.memory_usage_pct,
            "trading_hydra_cpu_usage_percent": health.cpu_usage_pct,
            "trading_hydra_components_healthy": sum(1 for c in health.components if c.status == "healthy"),
            "trading_hydra_components_degraded": sum(1 for c in health.components if c.status == "degraded"),
            "trading_hydra_components_unhealthy": sum(1 for c in health.components if c.status == "unhealthy")
        }
        
        for component in health.components:
            name = component.name.replace("-", "_")
            metrics[f"trading_hydra_{name}_latency_ms"] = component.latency_ms
            metrics[f"trading_hydra_{name}_healthy"] = 1 if component.status == "healthy" else 0
        
        return metrics
    
    def get_last_check(self) -> Optional[SystemHealth]:
        """Get the last health check result."""
        return self._last_check


_health_service: Optional[HealthCheckService] = None


def get_health_service() -> HealthCheckService:
    """Get or create HealthCheckService singleton."""
    global _health_service
    if _health_service is None:
        _health_service = HealthCheckService()
    return _health_service
