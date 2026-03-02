"""Kill-Switch Module - Correlated loss protection.

Halts new entries on cluster or global drawdowns.
This is the "save my account from death-by-same-trade" module.
"""

import os
import yaml
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Set, Optional, List, Any

from ..core.logging import get_logger
from ..core.state import get_state, set_state


# Singleton instance
_killswitch_service: Optional["KillSwitchService"] = None


def get_killswitch_service() -> "KillSwitchService":
    """Get or create the KillSwitchService singleton."""
    global _killswitch_service
    if _killswitch_service is None:
        _killswitch_service = KillSwitchService()
    return _killswitch_service


@dataclass
class KillSwitchState:
    """Current state of the kill-switch system."""
    global_freeze: bool = False
    global_freeze_until: Optional[datetime] = None
    frozen_clusters: Set[str] = field(default_factory=set)
    cluster_freeze_until: Dict[str, datetime] = field(default_factory=dict)
    last_evaluated: Optional[datetime] = None
    
    def is_cluster_frozen(self, cluster: str) -> bool:
        """Check if a specific cluster is frozen."""
        if cluster not in self.frozen_clusters:
            return False
        # Check if freeze has expired
        if cluster in self.cluster_freeze_until:
            if datetime.utcnow() > self.cluster_freeze_until[cluster]:
                self.frozen_clusters.discard(cluster)
                del self.cluster_freeze_until[cluster]
                return False
        return True
    
    def is_global_frozen(self) -> bool:
        """Check if global freeze is active."""
        if not self.global_freeze:
            return False
        # Check if freeze has expired
        if self.global_freeze_until and datetime.utcnow() > self.global_freeze_until:
            self.global_freeze = False
            self.global_freeze_until = None
            return False
        return True


class KillSwitchService:
    """Service for managing kill-switch state and evaluation."""
    
    def __init__(self):
        self._logger = get_logger()
        self._config = self._load_config()
        self._state = KillSwitchState()
        self._logger.log("killswitch_init", {"config_loaded": True})
    
    def _load_config(self) -> Dict[str, Any]:
        """Load kill-switch configuration from YAML."""
        base = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        config_path = os.path.join(base, "config", "killswitch.yaml")
        if not os.path.exists(config_path):
            base = os.path.dirname(base)
            config_path = os.path.join(base, "config", "killswitch.yaml")
        try:
            with open(config_path, "r") as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            self._logger.error(f"killswitch.yaml not found at {config_path}, using defaults")
            return {
                "risk_unit": {"type": "equity_pct", "value": 0.005},
                "cluster_limits": {"level1_r": -1.5, "level2_r": -2.5, "cooldown_hours": 24},
                "global_limits": {"freeze_r": -3.0, "cooldown_hours": 24}
            }
    
    def compute_r(self, equity: float) -> float:
        """Compute R (risk unit) based on equity.
        
        R = equity * risk_unit.value
        Default: 0.5% of equity = 1R
        """
        if equity <= 0:
            return 0.0
        risk_unit = self._config.get("risk_unit", {})
        value = risk_unit.get("value", 0.005)
        return equity * value
    
    def pnl_to_r(self, pnl: float, equity: float) -> float:
        """Convert P&L to R units."""
        r = self.compute_r(equity)
        if r <= 0:
            return 0.0
        return pnl / r
    
    def evaluate(
        self,
        pnl_by_cluster: Dict[str, float],
        total_pnl: float,
        equity: float
    ) -> KillSwitchState:
        """Evaluate kill-switch state based on current P&L.
        
        Args:
            pnl_by_cluster: Daily P&L by cluster/profile (e.g., {"high_iv": -150.0})
            total_pnl: Total daily P&L across all clusters
            equity: Current account equity
            
        Returns:
            KillSwitchState with global_freeze and frozen_clusters
        """
        now = datetime.utcnow()
        r = self.compute_r(equity)
        
        if r <= 0:
            # Fail-closed: if we can't compute R, freeze everything
            self._logger.log("killswitch_fail_closed", {"reason": "invalid_r", "equity": equity})
            self._state.global_freeze = True
            self._state.global_freeze_until = now + timedelta(hours=24)
            return self._state
        
        cluster_limits = self._config.get("cluster_limits", {})
        global_limits = self._config.get("global_limits", {})
        cooldown_hours = cluster_limits.get("cooldown_hours", 24)
        
        # Check global freeze first
        total_pnl_r = total_pnl / r
        global_freeze_r = global_limits.get("freeze_r", -3.0)
        
        if total_pnl_r <= global_freeze_r:
            if not self._state.is_global_frozen():
                self._logger.log("killswitch_global_freeze", {
                    "total_pnl": total_pnl,
                    "total_pnl_r": round(total_pnl_r, 2),
                    "threshold_r": global_freeze_r,
                    "cooldown_hours": cooldown_hours
                })
            self._state.global_freeze = True
            self._state.global_freeze_until = now + timedelta(hours=cooldown_hours)
            self._state.last_evaluated = now
            return self._state
        
        # Check per-cluster limits
        level1_r = cluster_limits.get("level1_r", -1.5)
        level2_r = cluster_limits.get("level2_r", -2.5)
        
        for cluster, pnl in pnl_by_cluster.items():
            pnl_r = pnl / r
            
            if pnl_r <= level2_r:
                # Level 2: Hard freeze
                if not self._state.is_cluster_frozen(cluster):
                    self._logger.log("killswitch_cluster_freeze", {
                        "cluster": cluster,
                        "pnl": pnl,
                        "pnl_r": round(pnl_r, 2),
                        "threshold_r": level2_r,
                        "level": 2
                    })
                self._state.frozen_clusters.add(cluster)
                self._state.cluster_freeze_until[cluster] = now + timedelta(hours=cooldown_hours)
                
            elif pnl_r <= level1_r:
                # Level 1: Throttle (treat same as freeze for v1)
                if not self._state.is_cluster_frozen(cluster):
                    self._logger.log("killswitch_cluster_throttle", {
                        "cluster": cluster,
                        "pnl": pnl,
                        "pnl_r": round(pnl_r, 2),
                        "threshold_r": level1_r,
                        "level": 1
                    })
                self._state.frozen_clusters.add(cluster)
                self._state.cluster_freeze_until[cluster] = now + timedelta(hours=cooldown_hours)
        
        self._state.last_evaluated = now
        return self._state
    
    def is_entry_allowed(self, profile: str) -> tuple[bool, Optional[str]]:
        """Check if new entry is allowed for given profile.
        
        Returns:
            Tuple of (allowed, reason) where reason is set if not allowed
        """
        # Check global freeze first
        if self._state.is_global_frozen():
            return False, "GLOBAL_KILL_SWITCH_ACTIVE"
        
        # Check cluster freeze
        if self._state.is_cluster_frozen(profile):
            return False, f"CLUSTER_FROZEN:{profile}"
        
        return True, None
    
    def get_state(self) -> KillSwitchState:
        """Get current kill-switch state."""
        return self._state
    
    def get_frozen_clusters_list(self) -> List[str]:
        """Get list of currently frozen clusters."""
        return [c for c in self._state.frozen_clusters if self._state.is_cluster_frozen(c)]
    
    def clear_freeze(self, cluster: Optional[str] = None):
        """Manually clear a freeze (for testing or manual override).
        
        Args:
            cluster: Specific cluster to unfreeze, or None to clear global freeze
        """
        if cluster is None:
            self._state.global_freeze = False
            self._state.global_freeze_until = None
            self._logger.log("killswitch_global_cleared", {})
        else:
            self._state.frozen_clusters.discard(cluster)
            if cluster in self._state.cluster_freeze_until:
                del self._state.cluster_freeze_until[cluster]
            self._logger.log("killswitch_cluster_cleared", {"cluster": cluster})
