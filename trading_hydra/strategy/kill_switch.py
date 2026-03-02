"""
=============================================================================
Strategy Kill-Switch - Per-Strategy Drawdown Circuit Breaker
=============================================================================
Tracks rolling realized PnL per strategy and disables the strategy
when drawdown exceeds threshold. Persists in state store for restart safety.

Features:
- Per-strategy (not global) circuit breaker
- Configurable drawdown threshold and cooloff period
- Persists across restarts via SQLite state
- Rolling trade window for drawdown calculation
=============================================================================
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Any, List, Optional

from ..core.state import get_state, set_state
from ..core.logging import get_logger


@dataclass(frozen=True)
class KillStatus:
    """Status of the kill-switch for a strategy."""
    is_killed: bool
    reason: str
    until_ts: float


class StrategyKillSwitch:
    """
    Per-strategy circuit breaker based on realized PnL drawdown.
    """

    def __init__(self):
        self._logger = get_logger()

    def status(self, strategy_id: str) -> KillStatus:
        """
        Check if a strategy is currently killed.
        
        Args:
            strategy_id: Strategy identifier
            
        Returns:
            KillStatus with is_killed flag and reason
        """
        data = get_state(f"strategy_kill.{strategy_id}", {}) or {}
        until_ts = float(data.get("until_ts", 0))
        
        if time.time() < until_ts:
            return KillStatus(
                is_killed=True,
                reason=str(data.get("reason", "drawdown_exceeded")),
                until_ts=until_ts
            )
        
        return KillStatus(is_killed=False, reason="", until_ts=0.0)

    def record_exit(self, strategy_id: str, pnl: float, strategy_cfg: Dict[str, Any]) -> None:
        """
        Record a trade exit and check if kill-switch should trigger.
        
        Args:
            strategy_id: Strategy identifier
            pnl: Realized PnL of the closed trade (positive or negative)
            strategy_cfg: Strategy config with kill_switch settings
        """
        key = f"strategy_perf.{strategy_id}"
        perf = get_state(key, {}) or {}
        trades = list(perf.get("trades", []))

        trades.append({"ts": time.time(), "pnl": float(pnl)})

        ks_cfg = strategy_cfg.get("kill_switch", {})
        max_trades = int(ks_cfg.get("rolling_trades", 20))
        trades = trades[-max_trades:]

        perf["trades"] = trades
        set_state(key, perf)

        dd = self._calc_drawdown(trades)
        dd_limit = float(ks_cfg.get("max_drawdown", 500.0))

        self._logger.log("strategy_performance_update", {
            "strategy_id": strategy_id,
            "pnl": pnl,
            "rolling_drawdown": dd,
            "dd_limit": dd_limit,
            "trade_count": len(trades)
        })

        if dd > dd_limit:
            self._trigger_kill(strategy_id, dd, ks_cfg)

    def _calc_drawdown(self, trades: List[Dict[str, Any]]) -> float:
        """
        Calculate max drawdown from rolling trades.
        
        Args:
            trades: List of trade records with 'pnl' field
            
        Returns:
            Maximum drawdown (positive number representing loss)
        """
        if not trades:
            return 0.0

        pnl_values = [float(t.get("pnl", 0)) for t in trades]
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0

        for pnl in pnl_values:
            cumulative += pnl
            peak = max(peak, cumulative)
            dd = peak - cumulative
            max_dd = max(max_dd, dd)

        return max_dd

    def _trigger_kill(self, strategy_id: str, drawdown: float, ks_cfg: Dict[str, Any]) -> None:
        """
        Trigger the kill-switch for a strategy.
        
        Args:
            strategy_id: Strategy identifier
            drawdown: Current drawdown that triggered the kill
            ks_cfg: kill_switch config from strategy YAML
        """
        cooloff_minutes = int(ks_cfg.get("cooloff_minutes", 60))
        until_ts = time.time() + (cooloff_minutes * 60)

        kill_data = {
            "triggered_at": time.time(),
            "until_ts": until_ts,
            "reason": f"drawdown_exceeded: ${drawdown:.2f}",
            "drawdown": drawdown
        }

        set_state(f"strategy_kill.{strategy_id}", kill_data)

        self._logger.log("strategy_kill_triggered", {
            "strategy_id": strategy_id,
            "drawdown": drawdown,
            "cooloff_minutes": cooloff_minutes,
            "until_ts": until_ts
        })

    def clear_kill(self, strategy_id: str) -> None:
        """
        Manually clear a kill-switch (for admin/recovery use).
        
        Args:
            strategy_id: Strategy identifier to clear
        """
        set_state(f"strategy_kill.{strategy_id}", {})
        self._logger.log("strategy_kill_cleared", {"strategy_id": strategy_id})

    def get_performance(self, strategy_id: str) -> Dict[str, Any]:
        """
        Get performance summary for a strategy.
        
        Args:
            strategy_id: Strategy identifier
            
        Returns:
            Dict with trade count, total PnL, and drawdown
        """
        key = f"strategy_perf.{strategy_id}"
        perf = get_state(key, {}) or {}
        trades = list(perf.get("trades", []))

        if not trades:
            return {
                "trade_count": 0,
                "total_pnl": 0.0,
                "drawdown": 0.0,
                "avg_pnl": 0.0
            }

        pnl_values = [float(t.get("pnl", 0)) for t in trades]
        total_pnl = sum(pnl_values)
        drawdown = self._calc_drawdown(trades)

        return {
            "trade_count": len(trades),
            "total_pnl": total_pnl,
            "drawdown": drawdown,
            "avg_pnl": total_pnl / len(trades) if trades else 0.0
        }
