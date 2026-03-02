"""
=============================================================================
ML Model Governance - Auto-disable models when edge decays
=============================================================================

Production-grade ML model lifecycle management:
1. Monitor rolling Sharpe ratios via EdgeDecayMonitor
2. Auto-disable models when performance degrades below threshold
3. Fallback to rules-based signals when ML is disabled
4. Track governance decisions with full audit trail

Philosophy:
- Models decay as markets adapt; detect and respond automatically
- Never let a degraded model continue trading (fail-safe)
- Human-in-the-loop for re-enabling (safety)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from enum import Enum

from ..core.logging import get_logger
from ..core.state import get_state, set_state
from ..core.config import load_settings


class ModelStatus(Enum):
    """Model governance status."""
    ACTIVE = "active"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    QUARANTINE = "quarantine"  # Disabled pending human review


@dataclass
class ModelHealth:
    """Health status for a single ML model."""
    model_id: str
    status: ModelStatus
    sharpe_7d: float
    sharpe_30d: float
    sharpe_90d: float
    decay_ratio: float
    last_check: str
    disabled_at: Optional[str] = None
    disable_reason: Optional[str] = None
    consecutive_degraded_days: int = 0
    auto_disable_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "status": self.status.value,
            "sharpe_7d": self.sharpe_7d,
            "sharpe_30d": self.sharpe_30d,
            "sharpe_90d": self.sharpe_90d,
            "decay_ratio": self.decay_ratio,
            "last_check": self.last_check,
            "disabled_at": self.disabled_at,
            "disable_reason": self.disable_reason,
            "consecutive_degraded_days": self.consecutive_degraded_days,
            "auto_disable_count": self.auto_disable_count
        }


class MLGovernance:
    """
    Manage ML model lifecycle based on performance metrics.
    
    Uses EdgeDecayMonitor to track rolling Sharpe and automatically
    disables models when edge decay is detected.
    """
    
    MIN_SHARPE_THRESHOLD = 0.3
    DEGRADED_SHARPE_THRESHOLD = 0.5
    DECAY_RATIO_THRESHOLD = 0.5
    CONSECUTIVE_DEGRADED_DAYS_LIMIT = 3
    
    STATE_KEY = "ml_governance.model_health"
    
    def __init__(self):
        self._logger = get_logger()
        self._model_health: Dict[str, ModelHealth] = {}
        self._edge_decay_monitor = None
        
        self._load_config()
        self._load_state()
        
        self._logger.log("ml_governance_init", {
            "min_sharpe": self.MIN_SHARPE_THRESHOLD,
            "degraded_sharpe": self.DEGRADED_SHARPE_THRESHOLD,
            "decay_ratio_threshold": self.DECAY_RATIO_THRESHOLD,
            "consecutive_days_limit": self.CONSECUTIVE_DEGRADED_DAYS_LIMIT
        })
    
    def _load_config(self) -> None:
        """Load governance config from settings."""
        try:
            settings = load_settings()
            gov_config = settings.get("ml_governance", {})
            
            self.MIN_SHARPE_THRESHOLD = gov_config.get("min_sharpe_threshold", 0.3)
            self.DEGRADED_SHARPE_THRESHOLD = gov_config.get("degraded_sharpe_threshold", 0.5)
            self.DECAY_RATIO_THRESHOLD = gov_config.get("decay_ratio_threshold", 0.5)
            self.CONSECUTIVE_DEGRADED_DAYS_LIMIT = gov_config.get("consecutive_degraded_days_limit", 3)
        except Exception as e:
            self._logger.error(f"Failed to load ML governance config: {e}")
    
    def _load_state(self) -> None:
        """Load persisted model health state."""
        try:
            saved = get_state(self.STATE_KEY, {})
            for model_id, data in saved.items():
                data["status"] = ModelStatus(data.get("status", "active"))
                self._model_health[model_id] = ModelHealth(**data)
        except Exception as e:
            self._logger.error(f"Failed to load ML governance state: {e}")
    
    def _save_state(self) -> None:
        """Persist model health state."""
        try:
            data = {k: v.to_dict() for k, v in self._model_health.items()}
            set_state(self.STATE_KEY, data)
        except Exception as e:
            self._logger.error(f"Failed to save ML governance state: {e}")
    
    def _get_edge_decay_monitor(self):
        """Lazy load EdgeDecayMonitor."""
        if self._edge_decay_monitor is None:
            try:
                from .edge_decay_monitor import EdgeDecayMonitor
                self._edge_decay_monitor = EdgeDecayMonitor()
            except Exception as e:
                self._logger.error(f"Failed to load EdgeDecayMonitor: {e}")
        return self._edge_decay_monitor
    
    def is_model_active(self, model_id: str) -> bool:
        """Check if a model is active and allowed to generate signals."""
        if model_id not in self._model_health:
            return True  # Unknown models default to active
        
        status = self._model_health[model_id].status
        return status == ModelStatus.ACTIVE
    
    def check_model_health(self, model_id: str) -> ModelHealth:
        """
        Check and update health status for a model.
        
        Uses EdgeDecayMonitor metrics to determine if model should
        be degraded or disabled.
        """
        monitor = self._get_edge_decay_monitor()
        now = datetime.utcnow().isoformat() + "Z"
        
        if monitor:
            decay_status = monitor.evaluate_strategy(model_id)
            sharpe_7d = decay_status.sharpe_7d
            sharpe_30d = decay_status.sharpe_30d
            sharpe_90d = decay_status.sharpe_90d
            decay_ratio = decay_status.decay_ratio
        else:
            sharpe_7d = sharpe_30d = sharpe_90d = 0.0
            decay_ratio = 1.0
        
        existing = self._model_health.get(model_id)
        consecutive_days = existing.consecutive_degraded_days if existing else 0
        auto_disable_count = existing.auto_disable_count if existing else 0
        current_status = existing.status if existing else ModelStatus.ACTIVE
        
        if sharpe_30d < self.MIN_SHARPE_THRESHOLD or decay_ratio < self.DECAY_RATIO_THRESHOLD:
            consecutive_days += 1
            
            if consecutive_days >= self.CONSECUTIVE_DEGRADED_DAYS_LIMIT:
                new_status = ModelStatus.DISABLED
                disable_reason = f"Sharpe={sharpe_30d:.2f}, decay_ratio={decay_ratio:.2f}"
                auto_disable_count += 1
                
                self._logger.log("ml_model_auto_disabled", {
                    "model_id": model_id,
                    "sharpe_30d": sharpe_30d,
                    "decay_ratio": decay_ratio,
                    "consecutive_days": consecutive_days,
                    "auto_disable_count": auto_disable_count
                })
            else:
                new_status = ModelStatus.DEGRADED
                disable_reason = None
        elif sharpe_30d < self.DEGRADED_SHARPE_THRESHOLD:
            new_status = ModelStatus.DEGRADED
            consecutive_days = max(0, consecutive_days)
            disable_reason = None
        else:
            new_status = ModelStatus.ACTIVE
            consecutive_days = 0
            disable_reason = None
        
        if current_status == ModelStatus.DISABLED and new_status in [ModelStatus.ACTIVE, ModelStatus.DEGRADED]:
            new_status = ModelStatus.QUARANTINE
            self._logger.log("ml_model_quarantine", {
                "model_id": model_id,
                "reason": "Auto-disabled models require human review to re-enable"
            })
        
        health = ModelHealth(
            model_id=model_id,
            status=new_status,
            sharpe_7d=sharpe_7d,
            sharpe_30d=sharpe_30d,
            sharpe_90d=sharpe_90d,
            decay_ratio=decay_ratio,
            last_check=now,
            disabled_at=now if new_status == ModelStatus.DISABLED else (existing.disabled_at if existing else None),
            disable_reason=disable_reason,
            consecutive_degraded_days=consecutive_days,
            auto_disable_count=auto_disable_count
        )
        
        self._model_health[model_id] = health
        self._save_state()
        
        return health
    
    def manually_enable(self, model_id: str, reason: str = "manual_override") -> bool:
        """
        Manually re-enable a disabled/quarantined model.
        
        Requires human decision - resets consecutive degraded days.
        """
        if model_id not in self._model_health:
            return False
        
        health = self._model_health[model_id]
        
        if health.status not in [ModelStatus.DISABLED, ModelStatus.QUARANTINE]:
            return False
        
        health.status = ModelStatus.ACTIVE
        health.consecutive_degraded_days = 0
        health.disabled_at = None
        health.disable_reason = None
        health.last_check = datetime.utcnow().isoformat() + "Z"
        
        self._save_state()
        
        self._logger.log("ml_model_manually_enabled", {
            "model_id": model_id,
            "reason": reason,
            "auto_disable_count": health.auto_disable_count
        })
        
        return True
    
    def get_fallback_signal(self, model_id: str, symbol: str, market_data: Dict[str, Any]) -> Optional[str]:
        """
        Get rules-based fallback signal when ML is disabled.
        
        Simple momentum/mean-reversion rules as backup.
        """
        if self.is_model_active(model_id):
            return None
        
        close = market_data.get("close", 0)
        sma_20 = market_data.get("sma_20", close)
        rsi = market_data.get("rsi", 50)
        
        if rsi < 30 and close < sma_20 * 0.98:
            signal = "buy"  # Oversold bounce
        elif rsi > 70 and close > sma_20 * 1.02:
            signal = "sell"  # Overbought fade
        else:
            signal = None  # No clear signal
        
        if signal:
            self._logger.log("ml_fallback_signal", {
                "model_id": model_id,
                "symbol": symbol,
                "signal": signal,
                "rsi": rsi,
                "close_vs_sma": round(close / sma_20, 4) if sma_20 > 0 else 1.0
            })
        
        return signal
    
    def get_all_model_health(self) -> Dict[str, ModelHealth]:
        """Get health status for all tracked models."""
        return self._model_health.copy()
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get governance statistics."""
        active = sum(1 for h in self._model_health.values() if h.status == ModelStatus.ACTIVE)
        degraded = sum(1 for h in self._model_health.values() if h.status == ModelStatus.DEGRADED)
        disabled = sum(1 for h in self._model_health.values() if h.status == ModelStatus.DISABLED)
        quarantine = sum(1 for h in self._model_health.values() if h.status == ModelStatus.QUARANTINE)
        
        return {
            "total_models": len(self._model_health),
            "active": active,
            "degraded": degraded,
            "disabled": disabled,
            "quarantine": quarantine,
            "models": {k: v.to_dict() for k, v in self._model_health.items()}
        }


_ml_governance: Optional[MLGovernance] = None


def get_ml_governance() -> MLGovernance:
    """Get or create MLGovernance singleton."""
    global _ml_governance
    if _ml_governance is None:
        _ml_governance = MLGovernance()
    return _ml_governance
