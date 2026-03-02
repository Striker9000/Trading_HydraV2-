"""
Strategy System Module
======================
Deterministic strategy execution engine with:
- StrategyRegistry: Load and validate strategy configs from YAML
- StrategyValidator: Evaluate signal rules against indicators
- BacktestGate: Enforce historical performance thresholds
- OptionsSelector: Select contracts by delta/DTE criteria
- EarningsFilter: Enforce earnings blackout policies
- StrategyKillSwitch: Per-strategy drawdown circuit breaker
- StrategyRunner: Single enforcement point for strategy execution
"""

from .registry import StrategyRegistry, StrategyConfig
from .validator import StrategyValidator, StrategyDecision, RuleResult
from .backtest_gate import BacktestGate, BacktestSummary
from .options_selector import OptionSelector, OptionContract
from .earnings_filter import EarningsFilter
from .kill_switch import StrategyKillSwitch, KillStatus
from .runner import StrategyRunner

__all__ = [
    "StrategyRegistry",
    "StrategyConfig",
    "StrategyValidator",
    "StrategyDecision",
    "RuleResult",
    "BacktestGate",
    "BacktestSummary",
    "OptionSelector",
    "OptionContract",
    "EarningsFilter",
    "StrategyKillSwitch",
    "KillStatus",
    "StrategyRunner",
]
