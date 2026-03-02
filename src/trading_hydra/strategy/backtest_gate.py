"""
=============================================================================
Backtest Gate - Historical Performance Threshold Enforcement
=============================================================================
Hard gate based on strategy's historical backtest performance.
If data missing, fail closed.

Thresholds from strategy YAML:
- min_win_rate_1y: Minimum 1-year win rate
- min_win_rate_3y: Minimum 3-year win rate
- min_total_wins_3y: Minimum total winning trades in 3 years
- min_total_return_1y: Minimum 1-year total return
=============================================================================
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, Optional

from ..core.logging import get_logger


@dataclass(frozen=True)
class BacktestSummary:
    """Historical backtest performance summary for a strategy + symbol."""
    win_rate_1y: float
    win_rate_3y: float
    total_wins_3y: int
    total_return_1y: float


class BacktestGate:
    """
    Hard gate based on historical performance thresholds.
    If data missing, fail closed.
    """

    def __init__(self):
        self._logger = get_logger()

    def passes(self, gate_cfg: Dict[str, Any], bt: Optional[BacktestSummary]) -> bool:
        """
        Check if backtest summary passes the gate thresholds.
        
        Args:
            gate_cfg: backtest_gate config from strategy YAML
            bt: BacktestSummary or None
            
        Returns:
            True if all thresholds met, False otherwise
        """
        if bt is None:
            self._logger.log("backtest_gate_fail", {"reason": "no_backtest_data"})
            return False

        min_wr_1y = float(gate_cfg.get("min_win_rate_1y", 0.0))
        min_wr_3y = float(gate_cfg.get("min_win_rate_3y", 0.0))
        min_wins_3y = int(gate_cfg.get("min_total_wins_3y", 0))
        min_ret_1y = float(gate_cfg.get("min_total_return_1y", 0.0))

        if bt.win_rate_1y < min_wr_1y:
            self._logger.log("backtest_gate_fail", {
                "reason": "win_rate_1y",
                "value": bt.win_rate_1y,
                "threshold": min_wr_1y
            })
            return False

        if bt.win_rate_3y < min_wr_3y:
            self._logger.log("backtest_gate_fail", {
                "reason": "win_rate_3y",
                "value": bt.win_rate_3y,
                "threshold": min_wr_3y
            })
            return False

        if bt.total_wins_3y < min_wins_3y:
            self._logger.log("backtest_gate_fail", {
                "reason": "total_wins_3y",
                "value": bt.total_wins_3y,
                "threshold": min_wins_3y
            })
            return False

        if bt.total_return_1y < min_ret_1y:
            self._logger.log("backtest_gate_fail", {
                "reason": "total_return_1y",
                "value": bt.total_return_1y,
                "threshold": min_ret_1y
            })
            return False

        return True
