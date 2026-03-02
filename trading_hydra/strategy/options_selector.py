"""
=============================================================================
Options Selector - Delta/DTE-Based Contract Selection
=============================================================================
Selects option contracts closest to target delta within a DTE window.
Tie-breaks by tightest bid-ask spread for better fills.

Selection criteria from strategy YAML:
- structure: long_call or long_put
- target_delta: Target absolute delta (e.g., 0.40)
- delta_tolerance: Acceptable deviation (e.g., 0.08)
- dte_min/dte_max: DTE window (e.g., 12-16 days)
=============================================================================
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from ..core.logging import get_logger
from ..risk.liquidity_filter import get_liquidity_filter


@dataclass(frozen=True)
class OptionContract:
    """Represents a single option contract."""
    symbol: str           # OCC symbol or underlying
    expiry: str           # Expiration date string (YYYY-MM-DD)
    strike: float         # Strike price
    right: str            # "call" or "put"
    delta: float          # Greek delta
    bid: float            # Bid price
    ask: float            # Ask price
    volume: int = 0       # Trading volume
    open_interest: int = 0  # Open interest


class OptionsChainProvider(Protocol):
    """Protocol for options chain data provider."""
    def chain(self, symbol: str) -> List[OptionContract]: ...
    def dte(self, expiry_str: str) -> int: ...


class OptionSelector:
    """
    Selects contract closest to target delta within a DTE window.
    """

    def __init__(self, options_chain_provider: OptionsChainProvider):
        self.chain_provider = options_chain_provider
        self._logger = get_logger()
        self._liquidity_filter = get_liquidity_filter()

    def select(self, symbol: str, plan: Dict[str, Any]) -> Optional[OptionContract]:
        """
        Select the best option contract based on plan criteria.
        
        Args:
            symbol: Underlying symbol
            plan: options_plan config from strategy YAML
            
        Returns:
            Best matching OptionContract or None if none found
        """
        structure = plan.get("structure", "long_call")
        right = "call" if structure == "long_call" else "put"

        target = float(plan.get("target_delta", 0.40))
        tol = float(plan.get("delta_tolerance", 0.10))
        dte_min = int(plan.get("dte_min", 7))
        dte_max = int(plan.get("dte_max", 21))

        try:
            all_contracts = self.chain_provider.chain(symbol)
        except Exception as e:
            self._logger.log("options_chain_error", {
                "symbol": symbol,
                "error": str(e)
            })
            return None

        contracts: List[OptionContract] = []
        for c in all_contracts:
            if c.right != right:
                continue
            
            try:
                dte = self.chain_provider.dte(c.expiry)
            except Exception:
                continue
                
            if dte < dte_min or dte > dte_max:
                continue
                
            delta_diff = abs(abs(c.delta) - target)
            if delta_diff > tol:
                continue
            
            # Apply liquidity filter before adding to candidates
            liquidity_check = self._liquidity_filter.check_option_liquidity(
                symbol=c.symbol,
                bid=c.bid,
                ask=c.ask,
                volume=c.volume,
                open_interest=c.open_interest
            )
            if not liquidity_check.passed:
                self._logger.log("options_liquidity_rejected", {
                    "symbol": c.symbol,
                    "spread_pct": liquidity_check.spread_pct,
                    "dollar_volume": liquidity_check.dollar_volume,
                    "open_interest": c.open_interest,
                    "reason": liquidity_check.rejection_reason
                })
                continue
                
            contracts.append(c)

        if not contracts:
            self._logger.log("options_no_match", {
                "symbol": symbol,
                "right": right,
                "target_delta": target,
                "delta_tolerance": tol,
                "dte_range": f"{dte_min}-{dte_max}"
            })
            return None

        contracts.sort(key=lambda c: (
            abs(abs(c.delta) - target),  # Closest to target delta
            (c.ask - c.bid)              # Tightest spread as tie-breaker
        ))

        selected = contracts[0]
        
        # Calculate IV percentile from context if available
        iv_percentile = plan.get("iv_percentile")
        iv_rank = plan.get("iv_rank")
        
        self._logger.log("options_selected", {
            "symbol": symbol,
            "contract": selected.symbol,
            "expiry": selected.expiry,
            "strike": selected.strike,
            "delta": round(selected.delta, 3),
            "spread": round(selected.ask - selected.bid, 2),
            "spread_pct": round((selected.ask - selected.bid) / selected.ask * 100, 2) if selected.ask > 0 else 0,
            "bid": selected.bid,
            "ask": selected.ask,
            "volume": selected.volume,
            "open_interest": selected.open_interest,
            "iv_percentile": iv_percentile,
            "iv_rank": iv_rank,
            "candidates_considered": len(contracts)
        })

        return selected
