"""
=============================================================================
Strategy Validator - Deterministic Rule Evaluator
=============================================================================
Evaluates signal rules from strategy YAML against indicator data.
No AI. No vibes. Just pass/fail with receipts.

Supports rule types:
- price_vs_ema: Compare price to EMA
- price_vs_sma: Compare price to SMA
- sma_vs_price: Compare SMA to price
- sma_vs_sma: Compare two SMAs (e.g., SMA20 > SMA50)
- rsi_threshold: RSI above/below threshold
=============================================================================
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Protocol


@dataclass(frozen=True)
class RuleResult:
    """Result of evaluating a single signal rule."""
    rule_id: str
    passed: bool
    details: str


@dataclass(frozen=True)
class StrategyDecision:
    """Result of evaluating all rules for a strategy + symbol."""
    strategy_id: str
    symbol: str
    passed: bool
    reasons: List[RuleResult]


class IndicatorEngine(Protocol):
    """Protocol for indicator data provider."""
    def ema(self, symbol: str, period: int, lookback_days: int = 0) -> float: ...
    def sma(self, symbol: str, period: int) -> float: ...
    def rsi(self, symbol: str, period: int) -> float: ...
    def last_close(self, symbol: str, lookback_days: int = 0) -> float: ...


class StrategyValidator:
    """
    Deterministic rule evaluator.
    No AI. No vibes. Just pass/fail with receipts.
    """

    def __init__(self, indicator_engine: IndicatorEngine):
        self.ind = indicator_engine

    def evaluate(self, strategy: Dict[str, Any], symbol: str) -> StrategyDecision:
        """
        Evaluate all signal rules for a strategy against a symbol.
        
        Args:
            strategy: Strategy config dict with signal_rules list
            symbol: Symbol to evaluate
            
        Returns:
            StrategyDecision with pass/fail and per-rule reasons
        """
        reasons: List[RuleResult] = []

        for rule in strategy.get("signal_rules", []):
            rid = rule["id"]

            try:
                passed, details = self._eval_rule(rule, symbol)
            except Exception as e:
                passed, details = False, f"rule_eval_error: {e}"

            reasons.append(RuleResult(rule_id=rid, passed=passed, details=details))

        ok = all(r.passed for r in reasons)
        return StrategyDecision(
            strategy_id=strategy["id"],
            symbol=symbol,
            passed=ok,
            reasons=reasons
        )

    def _eval_rule(self, rule: Dict[str, Any], symbol: str) -> Tuple[bool, str]:
        """Evaluate a single rule. Returns (passed, details)."""
        rtype = rule["type"]
        op = rule.get("op")

        if rtype == "price_vs_ema":
            period = int(rule["period"])
            lookback = int(rule.get("lookback_days", 0))
            price = float(self.ind.last_close(symbol, lookback_days=lookback))
            ema = float(self.ind.ema(symbol, period=period, lookback_days=lookback))
            return _cmp(price, ema, op), f"price={price:.2f} {op} ema{period}={ema:.2f} (lookback={lookback})"

        if rtype == "price_vs_sma":
            period = int(rule["period"])
            price = float(self.ind.last_close(symbol, lookback_days=0))
            sma = float(self.ind.sma(symbol, period=period))
            return _cmp(price, sma, op), f"price={price:.2f} {op} sma{period}={sma:.2f}"

        if rtype == "sma_vs_price":
            period = int(rule["period"])
            price = float(self.ind.last_close(symbol, lookback_days=0))
            sma = float(self.ind.sma(symbol, period=period))
            return _cmp(sma, price, op), f"sma{period}={sma:.2f} {op} price={price:.2f}"

        if rtype == "rsi_threshold":
            period = int(rule["period"])
            val = float(rule["value"])
            rsi = float(self.ind.rsi(symbol, period=period))
            return _cmp(rsi, val, op), f"rsi{period}={rsi:.2f} {op} {val}"

        if rtype == "sma_vs_sma":
            period_a = int(rule["period_a"])
            period_b = int(rule["period_b"])
            sma_a = float(self.ind.sma(symbol, period=period_a))
            sma_b = float(self.ind.sma(symbol, period=period_b))
            return _cmp(sma_a, sma_b, op), f"sma{period_a}={sma_a:.2f} {op} sma{period_b}={sma_b:.2f}"

        raise ValueError(f"Unknown rule type: {rtype}")


def _cmp(a: float, b: float, op: str) -> bool:
    """Compare two values with an operator."""
    if op == ">":
        return a > b
    if op == "<":
        return a < b
    if op == ">=":
        return a >= b
    if op == "<=":
        return a <= b
    if op == "==":
        return abs(a - b) < 0.0001
    raise ValueError(f"Bad operator: {op}")
