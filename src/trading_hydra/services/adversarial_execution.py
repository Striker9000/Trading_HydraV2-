"""Adversarial Execution Layer - Slippage, Partial Fills, Latency Simulation.

Simulates real-world execution challenges:
1. Spread widening under volatility
2. Slippage based on order size vs ADV
3. Partial fills
4. Latency/quote staleness
5. Cancel/replace failures

Every execution logs detailed metrics for post-mortem analysis.

Safe defaults for live trading (disabled by default).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from enum import Enum
import random
import math

from ..core.logging import get_logger
from ..core.config import load_settings


class ExecutionScenario(Enum):
    """Types of adversarial scenarios."""
    NORMAL = "normal"
    SPREAD_EXPLOSION = "spread_explosion"
    QUOTE_LAG = "quote_lag"
    PARTIAL_FILL = "partial_fill"
    SLIPPAGE = "slippage"
    CANCEL_FAIL = "cancel_fail"
    NEWS_SHOCK = "news_shock"


@dataclass
class ExecutionMetrics:
    """Detailed execution metrics for logging."""
    # Spread metrics
    spread_at_decision: float
    spread_at_fill: float
    spread_widening_pct: float
    
    # Slippage metrics
    expected_price: float
    actual_price: float
    slippage_cents: float
    slippage_pct: float
    fill_vs_mid: float  # How far from mid price
    
    # Fill metrics
    requested_qty: float
    filled_qty: float
    fill_rate: float
    is_partial: bool
    
    # Timing metrics
    quote_age_ms: float
    latency_ms: float
    cancel_replace_count: int
    
    # Scenario
    scenario: ExecutionScenario
    adversarial_applied: bool
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "spread_at_decision": round(self.spread_at_decision, 4),
            "spread_at_fill": round(self.spread_at_fill, 4),
            "spread_widening_pct": round(self.spread_widening_pct, 2),
            "expected_price": round(self.expected_price, 4),
            "actual_price": round(self.actual_price, 4),
            "slippage_cents": round(self.slippage_cents, 2),
            "slippage_pct": round(self.slippage_pct, 4),
            "fill_vs_mid": round(self.fill_vs_mid, 4),
            "requested_qty": self.requested_qty,
            "filled_qty": self.filled_qty,
            "fill_rate": round(self.fill_rate, 4),
            "is_partial": self.is_partial,
            "quote_age_ms": round(self.quote_age_ms, 1),
            "latency_ms": round(self.latency_ms, 1),
            "cancel_replace_count": self.cancel_replace_count,
            "scenario": self.scenario.value,
            "adversarial_applied": self.adversarial_applied
        }


@dataclass
class MarketContext:
    """Market context for execution logging."""
    gap_pct: float
    premarket_volume_ratio: float  # vs 20-day avg
    one_min_atr_at_open: float
    vix_level: float
    vix_regime: str
    is_macro_day: bool  # FOMC, CPI, etc.
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "gap_pct": round(self.gap_pct, 2),
            "premarket_volume_ratio": round(self.premarket_volume_ratio, 2),
            "one_min_atr_at_open": round(self.one_min_atr_at_open, 4),
            "vix_level": round(self.vix_level, 2),
            "vix_regime": self.vix_regime,
            "is_macro_day": self.is_macro_day
        }


@dataclass
class AdversarialConfig:
    """Configuration for adversarial scenarios."""
    # Spread widening
    spread_explosion_prob: float = 0.05
    spread_explosion_mult: Tuple[float, float] = (2.0, 5.0)
    
    # Quote lag
    quote_lag_prob: float = 0.10
    quote_lag_range_ms: Tuple[float, float] = (100, 1000)
    
    # Partial fills
    partial_fill_prob: float = 0.08
    partial_fill_range: Tuple[float, float] = (0.3, 0.9)
    
    # Slippage
    slippage_prob: float = 0.15
    slippage_range_pct: Tuple[float, float] = (0.01, 0.10)
    
    # Cancel failures
    cancel_fail_prob: float = 0.03


class AdversarialExecutionLayer:
    """
    Simulate adversarial execution conditions.
    
    Philosophy:
    - Real markets are hostile; paper trading hides this
    - Simulate what can go wrong: spreads widen, fills slip, quotes stale
    - Every trade logs detailed metrics for analysis
    
    Modes:
    - DISABLED: Production default, just log metrics
    - SIMULATION: Apply adversarial conditions (paper trading only)
    - HOSTILE: Worst-case testing (development only)
    """
    
    def __init__(self):
        self._logger = get_logger()
        self._settings = load_settings()
        
        config = self._settings.get("adversarial_execution", {})
        self._enabled = config.get("enabled", False)  # DISABLED by default
        self._mode = config.get("mode", "disabled")  # disabled, simulation, hostile
        
        # Load scenario probabilities
        self._config = AdversarialConfig(
            spread_explosion_prob=config.get("spread_explosion_prob", 0.05),
            quote_lag_prob=config.get("quote_lag_prob", 0.10),
            partial_fill_prob=config.get("partial_fill_prob", 0.08),
            slippage_prob=config.get("slippage_prob", 0.15),
            cancel_fail_prob=config.get("cancel_fail_prob", 0.03)
        )
        
        self._logger.log("adversarial_execution_init", {
            "enabled": self._enabled,
            "mode": self._mode
        })
    
    def simulate_execution(
        self,
        symbol: str,
        side: str,
        expected_price: float,
        qty: float,
        spread: float,
        market_context: Optional[MarketContext] = None,
        force_scenario: Optional[ExecutionScenario] = None
    ) -> Tuple[ExecutionMetrics, float, float]:
        """
        Simulate execution with adversarial conditions.
        
        Args:
            symbol: Trading symbol
            side: "buy" or "sell"
            expected_price: Price at time of decision
            qty: Requested quantity
            spread: Spread at time of decision
            market_context: Optional market context
            force_scenario: Force specific scenario (for testing)
            
        Returns:
            Tuple of (ExecutionMetrics, actual_price, filled_qty)
        """
        now = datetime.utcnow()
        
        # Determine scenario
        if force_scenario:
            scenario = force_scenario
        elif self._enabled and self._mode in ("simulation", "hostile"):
            scenario = self._pick_scenario()
        else:
            scenario = ExecutionScenario.NORMAL
        
        # Start with baseline
        actual_price = expected_price
        filled_qty = qty
        actual_spread = spread
        quote_age_ms = random.uniform(10, 50)  # Baseline quote age
        latency_ms = random.uniform(5, 20)     # Baseline latency
        cancel_replace_count = 0
        
        # Apply adversarial conditions
        adversarial_applied = scenario != ExecutionScenario.NORMAL
        
        if adversarial_applied:
            actual_price, filled_qty, actual_spread, quote_age_ms, latency_ms, cancel_replace_count = \
                self._apply_scenario(
                    scenario, side, expected_price, qty, spread, 
                    quote_age_ms, latency_ms
                )
        
        # Calculate metrics
        slippage_cents = (actual_price - expected_price) * 100
        if side == "sell":
            slippage_cents = -slippage_cents  # Selling below expected is slippage
        
        slippage_pct = ((actual_price - expected_price) / expected_price) * 100 if expected_price > 0 else 0
        
        mid_price = expected_price
        fill_vs_mid = ((actual_price - mid_price) / mid_price) * 100 if mid_price > 0 else 0
        
        spread_widening = ((actual_spread - spread) / spread) * 100 if spread > 0 else 0
        
        metrics = ExecutionMetrics(
            spread_at_decision=spread,
            spread_at_fill=actual_spread,
            spread_widening_pct=spread_widening,
            expected_price=expected_price,
            actual_price=actual_price,
            slippage_cents=abs(slippage_cents),
            slippage_pct=abs(slippage_pct),
            fill_vs_mid=fill_vs_mid,
            requested_qty=qty,
            filled_qty=filled_qty,
            fill_rate=filled_qty / qty if qty > 0 else 0,
            is_partial=filled_qty < qty,
            quote_age_ms=quote_age_ms,
            latency_ms=latency_ms,
            cancel_replace_count=cancel_replace_count,
            scenario=scenario,
            adversarial_applied=adversarial_applied
        )
        
        # Log detailed metrics
        self._log_execution(symbol, side, metrics, market_context)
        
        return metrics, actual_price, filled_qty
    
    def _pick_scenario(self) -> ExecutionScenario:
        """Randomly pick an adversarial scenario based on probabilities."""
        r = random.random()
        
        cumulative = 0.0
        
        cumulative += self._config.spread_explosion_prob
        if r < cumulative:
            return ExecutionScenario.SPREAD_EXPLOSION
        
        cumulative += self._config.quote_lag_prob
        if r < cumulative:
            return ExecutionScenario.QUOTE_LAG
        
        cumulative += self._config.partial_fill_prob
        if r < cumulative:
            return ExecutionScenario.PARTIAL_FILL
        
        cumulative += self._config.slippage_prob
        if r < cumulative:
            return ExecutionScenario.SLIPPAGE
        
        cumulative += self._config.cancel_fail_prob
        if r < cumulative:
            return ExecutionScenario.CANCEL_FAIL
        
        return ExecutionScenario.NORMAL
    
    def _apply_scenario(
        self,
        scenario: ExecutionScenario,
        side: str,
        price: float,
        qty: float,
        spread: float,
        quote_age: float,
        latency: float
    ) -> Tuple[float, float, float, float, float, int]:
        """Apply adversarial scenario to execution parameters."""
        
        actual_price = price
        filled_qty = qty
        actual_spread = spread
        actual_quote_age = quote_age
        actual_latency = latency
        cancel_count = 0
        
        if scenario == ExecutionScenario.SPREAD_EXPLOSION:
            mult = random.uniform(*self._config.spread_explosion_mult)
            actual_spread = spread * mult
            # Wider spread means worse fill
            slip_pct = (actual_spread - spread) / 2 / price
            if side == "buy":
                actual_price = price * (1 + slip_pct)
            else:
                actual_price = price * (1 - slip_pct)
        
        elif scenario == ExecutionScenario.QUOTE_LAG:
            actual_quote_age = random.uniform(*self._config.quote_lag_range_ms)
            # Stale quote means price moved
            move_pct = random.uniform(0.001, 0.005) * (actual_quote_age / 500)
            direction = 1 if side == "buy" else -1
            actual_price = price * (1 + direction * move_pct)
        
        elif scenario == ExecutionScenario.PARTIAL_FILL:
            fill_rate = random.uniform(*self._config.partial_fill_range)
            filled_qty = qty * fill_rate
        
        elif scenario == ExecutionScenario.SLIPPAGE:
            slip_pct = random.uniform(*self._config.slippage_range_pct) / 100
            direction = 1 if side == "buy" else -1
            actual_price = price * (1 + direction * slip_pct)
        
        elif scenario == ExecutionScenario.CANCEL_FAIL:
            cancel_count = random.randint(1, 3)
            actual_latency = latency + (cancel_count * random.uniform(100, 300))
            # Delayed fill means price moved
            move_pct = random.uniform(0.001, 0.003)
            direction = 1 if side == "buy" else -1
            actual_price = price * (1 + direction * move_pct)
        
        elif scenario == ExecutionScenario.NEWS_SHOCK:
            # Big move + spread explosion
            actual_spread = spread * random.uniform(3.0, 10.0)
            move_pct = random.uniform(0.005, 0.02)
            direction = random.choice([-1, 1])  # Could go either way
            if side == "buy":
                actual_price = price * (1 + direction * move_pct)
            else:
                actual_price = price * (1 - direction * move_pct)
        
        return actual_price, filled_qty, actual_spread, actual_quote_age, actual_latency, cancel_count
    
    def _log_execution(
        self,
        symbol: str,
        side: str,
        metrics: ExecutionMetrics,
        context: Optional[MarketContext]
    ):
        """Log detailed execution metrics."""
        log_data = {
            "symbol": symbol,
            "side": side,
            "execution_metrics": metrics.to_dict()
        }
        
        if context:
            log_data["market_context"] = context.to_dict()
        
        self._logger.log("execution_metrics", log_data)
    
    def create_market_context(
        self,
        gap_pct: float = 0.0,
        premarket_volume_ratio: float = 1.0,
        one_min_atr: float = 0.0,
        vix_level: float = 15.0,
        vix_regime: str = "normal",
        is_macro_day: bool = False
    ) -> MarketContext:
        """Create market context for execution logging."""
        return MarketContext(
            gap_pct=gap_pct,
            premarket_volume_ratio=premarket_volume_ratio,
            one_min_atr_at_open=one_min_atr,
            vix_level=vix_level,
            vix_regime=vix_regime,
            is_macro_day=is_macro_day
        )
    
    def set_mode(self, mode: str):
        """Set execution mode (disabled, simulation, hostile)."""
        valid_modes = ("disabled", "simulation", "hostile")
        if mode not in valid_modes:
            raise ValueError(f"Invalid mode: {mode}. Must be one of {valid_modes}")
        
        self._mode = mode
        self._enabled = mode != "disabled"
        
        self._logger.log("adversarial_execution_mode_changed", {"mode": mode})


# Singleton
_adversarial_layer: Optional[AdversarialExecutionLayer] = None


def get_adversarial_layer() -> AdversarialExecutionLayer:
    """Get or create AdversarialExecutionLayer singleton."""
    global _adversarial_layer
    if _adversarial_layer is None:
        _adversarial_layer = AdversarialExecutionLayer()
    return _adversarial_layer
