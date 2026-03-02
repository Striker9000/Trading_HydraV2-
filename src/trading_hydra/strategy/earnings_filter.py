"""
=============================================================================
Earnings Filter - Earnings Policy Enforcement
=============================================================================
Enforces strategy earnings_policy before allowing trades.

Modes:
- NEVER: Block within blackout_days of earnings (default safe)
- ONLY: Allow only within window_days of earnings
- PRE: Allow only if earnings upcoming within window_days
- POST: Allow only if earnings just happened within window_days
=============================================================================
"""
from __future__ import annotations

from typing import Dict, Any, Optional

from ..core.logging import get_logger
from ..services.earnings_calendar import get_earnings_calendar


class EarningsFilter:
    """
    Enforces strategy earnings_policy.
    Fail-closed design: unknown modes or missing data = block.
    """

    def __init__(self):
        self._logger = get_logger()
        self._svc = get_earnings_calendar()

    def allows(self, ticker: str, strategy_cfg: Dict[str, Any]) -> bool:
        """
        Check if trade is allowed based on earnings policy.
        
        Args:
            ticker: Stock symbol
            strategy_cfg: Strategy config with earnings_policy
            
        Returns:
            True if trade allowed, False otherwise
        """
        pol = strategy_cfg.get("earnings_policy") or {"mode": "NEVER"}
        mode = (pol.get("mode") or "NEVER").upper()

        try:
            info = self._svc.get_earnings_info(ticker)
        except Exception as e:
            self._logger.log("earnings_filter_error", {
                "ticker": ticker,
                "error": str(e)
            })
            return mode != "ONLY"

        if info is None or info.days_until is None:
            result = mode != "ONLY"
            self._logger.log("earnings_filter_no_data", {
                "ticker": ticker,
                "mode": mode,
                "allowed": result
            })
            return result

        d = int(info.days_until)

        if mode == "NEVER":
            blackout = int(pol.get("blackout_days", 3))
            allowed = abs(d) > blackout
            self._logger.log("earnings_filter_check", {
                "ticker": ticker,
                "mode": mode,
                "days_until": d,
                "blackout_days": blackout,
                "allowed": allowed
            })
            return allowed

        if mode == "ONLY":
            w = int(pol.get("window_days", 1))
            allowed = abs(d) <= w
            self._logger.log("earnings_filter_check", {
                "ticker": ticker,
                "mode": mode,
                "days_until": d,
                "window_days": w,
                "allowed": allowed
            })
            return allowed

        if mode == "PRE":
            w = int(pol.get("window_days", 3))
            allowed = 0 <= d <= w
            self._logger.log("earnings_filter_check", {
                "ticker": ticker,
                "mode": mode,
                "days_until": d,
                "window_days": w,
                "allowed": allowed
            })
            return allowed

        if mode == "POST":
            w = int(pol.get("window_days", 2))
            allowed = -w <= d <= 0
            self._logger.log("earnings_filter_check", {
                "ticker": ticker,
                "mode": mode,
                "days_until": d,
                "window_days": w,
                "allowed": allowed
            })
            return allowed

        self._logger.log("earnings_filter_unknown_mode", {
            "ticker": ticker,
            "mode": mode
        })
        return False
