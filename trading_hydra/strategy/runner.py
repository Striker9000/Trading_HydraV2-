"""
=============================================================================
Strategy Runner - Single Enforcement Point for Strategy Execution
=============================================================================
The "one throat to choke" module that enforces the complete strategy
execution pipeline:

1. Registry load (only real YAML files)
2. Per-strategy kill-switch check
3. Earnings policy filter
4. Deterministic signal rule validation
5. Backtest gate enforcement
6. Option contract selection
7. Trade execution with logging

Bots scan symbols. StrategyRunner decides trades.
=============================================================================
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from ..core.logging import get_logger
from ..core.state import get_state, set_state
from .registry import StrategyRegistry, StrategyConfig
from .validator import StrategyValidator, StrategyDecision, IndicatorEngine
from .backtest_gate import BacktestGate, BacktestSummary
from .options_selector import OptionSelector, OptionContract, OptionsChainProvider
from .earnings_filter import EarningsFilter
from .kill_switch import StrategyKillSwitch


@dataclass(frozen=True)
class TradeSignal:
    """Signal to execute a trade."""
    strategy_id: str
    symbol: str
    direction: str      # "bullish" or "bearish"
    contract: OptionContract
    stop_loss_pct: float
    take_profit_pct: float
    max_contracts: int


@dataclass(frozen=True)
class RunResult:
    """Result of running strategies for a symbol."""
    symbol: str
    signals: List[TradeSignal]
    skipped: List[Dict[str, Any]]


class BacktesterProvider(Protocol):
    """Protocol for backtest data provider."""
    def get_summary(self, strategy_id: str, symbol: str) -> Optional[BacktestSummary]: ...


class BrokerProvider(Protocol):
    """Protocol for order execution."""
    def place_bracket(
        self,
        contract: OptionContract,
        stop_loss_pct: float,
        take_profit_pct: float,
        max_contracts: int
    ) -> Dict[str, Any]: ...


class StrategyRunner:
    """
    Single enforcement point for strategy execution.
    All strategy trades must go through this runner.
    """

    def __init__(
        self,
        indicator_engine: IndicatorEngine,
        options_chain_provider: OptionsChainProvider,
        backtester: Optional[BacktesterProvider] = None,
        broker: Optional[BrokerProvider] = None,
        strategies_dir: Optional[str] = None,
        dry_run: bool = True
    ):
        """
        Initialize the strategy runner.
        
        Args:
            indicator_engine: Provider for indicator calculations
            options_chain_provider: Provider for options chain data
            backtester: Provider for backtest summaries (optional)
            broker: Provider for order execution (optional)
            strategies_dir: Path to strategies directory
            dry_run: If True, log signals but don't execute (default: True)
        """
        self._logger = get_logger()
        self._dry_run = dry_run
        
        self._registry = StrategyRegistry(strategies_dir)
        self._validator = StrategyValidator(indicator_engine)
        self._backtest_gate = BacktestGate()
        self._option_selector = OptionSelector(options_chain_provider)
        self._earnings_filter = EarningsFilter()
        self._kill_switch = StrategyKillSwitch()
        self._backtester = backtester
        self._broker = broker
        
        self._registry.load_all()

    def run(self, symbols: List[str]) -> List[RunResult]:
        """
        Run all enabled strategies against a list of symbols.
        
        Args:
            symbols: List of ticker symbols to evaluate
            
        Returns:
            List of RunResult, one per symbol
        """
        results = []
        strategies = self._registry.enabled_strategies()
        
        if not strategies:
            self._logger.log("strategy_runner_no_strategies", {
                "message": "No enabled strategies found"
            })
            return results

        self._logger.log("strategy_runner_start", {
            "symbol_count": len(symbols),
            "strategy_count": len(strategies)
        })

        for symbol in symbols:
            result = self._run_symbol(symbol, strategies)
            results.append(result)

        self._logger.log("strategy_runner_complete", {
            "total_signals": sum(len(r.signals) for r in results),
            "total_skipped": sum(len(r.skipped) for r in results)
        })

        return results

    def _run_symbol(self, symbol: str, strategies: List[StrategyConfig]) -> RunResult:
        """Run all strategies for a single symbol."""
        signals: List[TradeSignal] = []
        skipped: List[Dict[str, Any]] = []

        for strat in strategies:
            cfg = strat.data
            strategy_id = cfg["id"]

            kill_status = self._kill_switch.status(strategy_id)
            if kill_status.is_killed:
                skipped.append({
                    "strategy_id": strategy_id,
                    "symbol": symbol,
                    "reason": "kill_switch",
                    "details": kill_status.reason
                })
                continue

            if not self._earnings_filter.allows(symbol, cfg):
                skipped.append({
                    "strategy_id": strategy_id,
                    "symbol": symbol,
                    "reason": "earnings_policy"
                })
                continue

            decision = self._validator.evaluate(cfg, symbol)
            if not decision.passed:
                skipped.append({
                    "strategy_id": strategy_id,
                    "symbol": symbol,
                    "reason": "signal_rules_failed",
                    "details": [r.details for r in decision.reasons if not r.passed]
                })
                continue

            if self._backtester:
                bt_summary = self._backtester.get_summary(strategy_id, symbol)
                if not self._backtest_gate.passes(cfg.get("backtest_gate", {}), bt_summary):
                    skipped.append({
                        "strategy_id": strategy_id,
                        "symbol": symbol,
                        "reason": "backtest_gate_failed"
                    })
                    continue

            contract = self._option_selector.select(symbol, cfg.get("options_plan", {}))
            if not contract:
                skipped.append({
                    "strategy_id": strategy_id,
                    "symbol": symbol,
                    "reason": "no_matching_contract"
                })
                continue

            risk_plan = cfg.get("risk_plan", {})
            signal = TradeSignal(
                strategy_id=strategy_id,
                symbol=symbol,
                direction=cfg["direction"],
                contract=contract,
                stop_loss_pct=float(risk_plan.get("stop_loss_pct", 0.60)),
                take_profit_pct=float(risk_plan.get("take_profit_pct", 0.40)),
                max_contracts=int(risk_plan.get("max_contracts", 1))
            )

            self._logger.log("strategy_signal_generated", {
                "strategy_id": strategy_id,
                "symbol": symbol,
                "direction": cfg["direction"],
                "contract_symbol": contract.symbol,
                "contract_delta": contract.delta,
                "stop_loss_pct": signal.stop_loss_pct,
                "take_profit_pct": signal.take_profit_pct
            })

            if not self._dry_run and self._broker:
                try:
                    order_result = self._broker.place_bracket(
                        contract=contract,
                        stop_loss_pct=signal.stop_loss_pct,
                        take_profit_pct=signal.take_profit_pct,
                        max_contracts=signal.max_contracts
                    )
                    self._logger.log("strategy_order_placed", {
                        "strategy_id": strategy_id,
                        "symbol": symbol,
                        "order": order_result
                    })
                except Exception as e:
                    self._logger.error(f"Strategy order failed: {e}", 
                                       strategy_id=strategy_id, symbol=symbol)

            signals.append(signal)

        return RunResult(symbol=symbol, signals=signals, skipped=skipped)

    def record_exit(self, strategy_id: str, pnl: float) -> None:
        """
        Record a trade exit for kill-switch tracking.
        
        Args:
            strategy_id: Strategy that generated the trade
            pnl: Realized PnL of the closed position
        """
        try:
            cfg = self._registry.get(strategy_id)
            self._kill_switch.record_exit(strategy_id, pnl, cfg.data)
        except KeyError:
            self._logger.warn(f"Unknown strategy_id for exit: {strategy_id}")

    @property
    def registry(self) -> StrategyRegistry:
        """Access the strategy registry."""
        return self._registry

    @property
    def kill_switch(self) -> StrategyKillSwitch:
        """Access the kill-switch for manual operations."""
        return self._kill_switch
