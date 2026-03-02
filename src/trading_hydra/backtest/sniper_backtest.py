"""
=============================================================================
ProfitSniper Backtester - Grid Search Optimizer for Exit Settings
=============================================================================

Simulates ProfitSniper ratchet/velocity/exhaustion logic against historical
minute-bar data to find optimal exit settings per asset class and ticker.

Features:
1. Pull 30-60 days of historical 1-minute bars from Alpaca
2. Simulate synthetic positions (buy at random momentum entries)
3. Run ProfitSniper evaluation on each bar
4. Grid search across: ratchet_arm_pct, ratchet_base_distance_pct,
   ratchet_tighten_per_pct, ratchet_min_distance_pct, velocity_reversal_pct
5. Measure: captured profit vs peak, premature exit rate, total P&L
6. Output optimal settings per asset class / per ticker
7. Optionally write optimized settings back to bots.yaml

Usage:
    python -m trading_hydra.backtest.sniper_backtest --symbols BTC/USD,SOL/USD --days 30
    python -m trading_hydra.backtest.sniper_backtest --asset-class crypto --days 60 --apply
=============================================================================
"""

from __future__ import annotations

import time
import json
import yaml
import os
import statistics
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from itertools import product

from ..core.logging import get_logger
from ..risk.profit_sniper import ProfitSniperConfig, ProfitSniper, SniperState, SniperDecision

logger = get_logger()


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class SniperTrade:
    """A simulated trade for ProfitSniper backtesting."""
    symbol: str
    entry_time: str
    entry_price: float
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    peak_price: float = 0.0
    peak_profit_pct: float = 0.0
    exit_profit_pct: float = 0.0
    captured_vs_peak: float = 0.0  # exit_profit / peak_profit (1.0 = perfect capture)
    was_premature: bool = False    # exited before reaching a reasonable profit
    bars_held: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SniperBacktestResult:
    """Results from a single ProfitSniper backtest run."""
    config: Dict[str, Any]
    symbol: str
    asset_class: str
    trades: List[SniperTrade]

    # Aggregated metrics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl_pct: float = 0.0
    avg_pnl_pct: float = 0.0
    avg_captured_vs_peak: float = 0.0  # How well did sniper capture peak profit?
    premature_exit_rate: float = 0.0   # % of trades exited before 0.1% profit
    avg_peak_profit_pct: float = 0.0
    avg_giveback_pct: float = 0.0      # peak_profit - exit_profit average
    max_drawdown_trade_pct: float = 0.0
    avg_bars_held: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "config": self.config,
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate, 3),
            "total_pnl_pct": round(self.total_pnl_pct, 3),
            "avg_pnl_pct": round(self.avg_pnl_pct, 3),
            "avg_captured_vs_peak": round(self.avg_captured_vs_peak, 3),
            "premature_exit_rate": round(self.premature_exit_rate, 3),
            "avg_peak_profit_pct": round(self.avg_peak_profit_pct, 3),
            "avg_giveback_pct": round(self.avg_giveback_pct, 3),
            "max_drawdown_trade_pct": round(self.max_drawdown_trade_pct, 3),
            "avg_bars_held": round(self.avg_bars_held, 1),
        }


@dataclass
class SniperOptimizationResult:
    """Results from a full grid search optimization."""
    asset_class: str
    symbols_tested: List[str]
    best_config: Dict[str, Any]
    best_metrics: Dict[str, float]
    all_results: List[Dict[str, Any]]
    improvement_vs_default_pct: float
    recommended_config: ProfitSniperConfig

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_class": self.asset_class,
            "symbols_tested": self.symbols_tested,
            "best_config": self.best_config,
            "best_metrics": self.best_metrics,
            "num_combinations_tested": len(self.all_results),
            "improvement_vs_default_pct": round(self.improvement_vs_default_pct, 1),
            "top_5_configs": sorted(
                self.all_results,
                key=lambda x: x.get("score", 0),
                reverse=True
            )[:5]
        }


# =============================================================================
# SNIPER BACKTESTER
# =============================================================================

class SniperBacktester:
    """
    Backtests ProfitSniper settings against historical minute-bar data.

    Simulates positions by detecting momentum entries (3 consecutive bars up),
    then evaluates ProfitSniper decisions on every subsequent bar until exit.
    """

    def __init__(self):
        self.alpaca_client = None
        self._data_cache: Dict[str, List[Dict]] = {}
        self._init_alpaca()

    def _init_alpaca(self):
        """Initialize Alpaca client for historical data."""
        try:
            from ..services.alpaca_client import get_alpaca_client
            self.alpaca_client = get_alpaca_client()
            logger.log("sniper_backtest_init", {"status": "alpaca_connected"})
        except Exception as e:
            logger.error(f"Alpaca client init failed for sniper backtest: {e}")

    def _detect_asset_class(self, symbol: str) -> str:
        """Detect whether a symbol is crypto, stock, or option."""
        if "/" in symbol or symbol.endswith("USD"):
            return "crypto"
        if len(symbol) > 10 and any(c.isdigit() for c in symbol):
            return "options"
        return "stocks"

    def _fetch_minute_bars(self, symbol: str, days: int = 30) -> List[Dict]:
        """
        Fetch historical 1-minute bars from Alpaca.

        Uses the AlpacaClient's get_stock_bars(symbol, timeframe, limit)
        and get_crypto_bars(symbol, timeframe, limit, start) methods.

        Returns list of dicts with: timestamp, open, high, low, close, volume
        """
        cache_key = f"{symbol}_{days}"
        if cache_key in self._data_cache:
            return self._data_cache[cache_key]

        if not self.alpaca_client:
            logger.error("No Alpaca client available for historical data")
            return []

        try:
            asset_class = self._detect_asset_class(symbol)
            is_crypto = asset_class == "crypto"

            if is_crypto:
                # Crypto: 24/7, ~1440 bars/day, use start param for date range
                requested = days * 1440
                limit = min(requested, 10000)
                if limit < requested:
                    logger.warn(
                        f"Data truncated for {symbol}: requested {requested} bars "
                        f"({days} days), capped at {limit}. Use fewer days for full coverage."
                    )
                start_ts = (datetime.utcnow() - timedelta(days=days)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                bars = self.alpaca_client.get_crypto_bars(
                    symbol, timeframe="1Min", limit=limit, start=start_ts
                )
            else:
                # Stocks: ~390 bars/day (market hours only)
                requested = days * 390
                limit = min(requested, 10000)
                if limit < requested:
                    logger.warn(
                        f"Data truncated for {symbol}: requested {requested} bars "
                        f"({days} days), capped at {limit}. Use fewer days for full coverage."
                    )
                bars = self.alpaca_client.get_stock_bars(
                    symbol, timeframe="1Min", limit=limit
                )

            if not bars:
                logger.warn(f"No bars returned for {symbol}")
                return []

            # Convert Alpaca Bar objects to standard dict format
            # Alpaca SDK bars have attributes: .open, .high, .low, .close, .volume, .timestamp
            bar_list = []
            for bar in bars:
                try:
                    bar_dict = {
                        "timestamp": str(getattr(bar, "timestamp", "")),
                        "open": float(getattr(bar, "open", 0)),
                        "high": float(getattr(bar, "high", 0)),
                        "low": float(getattr(bar, "low", 0)),
                        "close": float(getattr(bar, "close", 0)),
                        "volume": float(getattr(bar, "volume", 0)),
                    }
                    if bar_dict["close"] > 0:
                        bar_list.append(bar_dict)
                except (AttributeError, TypeError):
                    # Fallback: try dict-style access
                    bar_dict = {
                        "timestamp": str(bar.get("timestamp", bar.get("t", ""))),
                        "open": float(bar.get("open", bar.get("o", 0))),
                        "high": float(bar.get("high", bar.get("h", 0))),
                        "low": float(bar.get("low", bar.get("l", 0))),
                        "close": float(bar.get("close", bar.get("c", 0))),
                        "volume": float(bar.get("volume", bar.get("v", 0))),
                    }
                    if bar_dict["close"] > 0:
                        bar_list.append(bar_dict)

            self._data_cache[cache_key] = bar_list
            logger.log("sniper_backtest_data_loaded", {
                "symbol": symbol,
                "bars": len(bar_list),
                "days": days,
                "asset_class": asset_class
            })
            return bar_list

        except Exception as e:
            logger.error(f"Failed to fetch bars for {symbol}: {e}")
            return []

    def _generate_synthetic_entries(self, bars: List[Dict],
                                     min_gap_bars: int = 30) -> List[int]:
        """
        Generate synthetic entry points by detecting 3 consecutive bars up.

        Returns list of bar indices where positions are entered.
        Enforces minimum gap between entries to avoid overlapping trades.
        """
        entries = []
        last_entry = -min_gap_bars

        for i in range(3, len(bars)):
            if i - last_entry < min_gap_bars:
                continue
            # 3 consecutive up bars
            if (bars[i - 2]["close"] > bars[i - 3]["close"] and
                    bars[i - 1]["close"] > bars[i - 2]["close"] and
                    bars[i]["close"] > bars[i - 1]["close"]):
                entries.append(i)
                last_entry = i

        return entries

    def run_single_backtest(
        self,
        symbol: str,
        config: ProfitSniperConfig,
        days: int = 30,
        max_hold_bars: int = 120
    ) -> SniperBacktestResult:
        """
        Run a single ProfitSniper backtest with the given config.

        Simulates positions by entering on momentum and applying ProfitSniper
        on every subsequent bar until exit or max hold time.

        Args:
            symbol: Ticker symbol
            config: ProfitSniper configuration to test
            days: Days of historical data
            max_hold_bars: Maximum bars to hold (default 2 hours for 1min bars)

        Returns:
            SniperBacktestResult with performance metrics
        """
        asset_class = self._detect_asset_class(symbol)
        bars = self._fetch_minute_bars(symbol, days)

        if len(bars) < 100:
            return SniperBacktestResult(
                config=self._config_to_dict(config),
                symbol=symbol,
                asset_class=asset_class,
                trades=[]
            )

        entries = self._generate_synthetic_entries(bars)
        trades: List[SniperTrade] = []

        for entry_idx in entries:
            entry_price = bars[entry_idx]["close"]
            entry_time = bars[entry_idx]["timestamp"]

            # Create fresh sniper state for this trade
            position_key = f"bt_{symbol}_{entry_idx}"
            state = SniperState(
                position_key=position_key,
                entry_price=entry_price,
                side="long",
                peak_price=entry_price
            )

            trade = SniperTrade(
                symbol=symbol,
                entry_time=entry_time,
                entry_price=entry_price,
            )

            # Replay bars through ProfitSniper
            for bar_offset in range(1, min(max_hold_bars, len(bars) - entry_idx)):
                bar_idx = entry_idx + bar_offset
                current_price = bars[bar_idx]["close"]
                current_time = bars[bar_idx]["timestamp"]

                # Calculate current profit
                profit_pct = ((current_price - entry_price) / entry_price) * 100

                # Track peak
                if current_price > trade.peak_price:
                    trade.peak_price = current_price
                    trade.peak_profit_pct = profit_pct

                # Run ProfitSniper evaluation (inline to avoid SQLite state)
                decision = self._evaluate_sniper(state, entry_price, current_price, config)

                if decision.should_exit:
                    trade.exit_time = current_time
                    trade.exit_price = current_price
                    trade.exit_profit_pct = profit_pct
                    trade.exit_reason = decision.reason
                    trade.bars_held = bar_offset
                    break
            else:
                # Max hold reached — force exit
                last_bar = bars[min(entry_idx + max_hold_bars - 1, len(bars) - 1)]
                trade.exit_time = last_bar["timestamp"]
                trade.exit_price = last_bar["close"]
                trade.exit_profit_pct = ((last_bar["close"] - entry_price) / entry_price) * 100
                trade.exit_reason = "max_hold_time"
                trade.bars_held = max_hold_bars

            # Calculate capture metrics
            if trade.peak_profit_pct > 0:
                trade.captured_vs_peak = trade.exit_profit_pct / trade.peak_profit_pct
            else:
                trade.captured_vs_peak = 1.0

            trade.was_premature = (
                trade.exit_profit_pct < 0.1 and
                trade.peak_profit_pct > 0.3 and
                trade.bars_held < 10
            )

            trades.append(trade)

        # Calculate aggregated metrics
        result = self._aggregate_trades(trades, config, symbol, asset_class)
        return result

    def _evaluate_sniper(
        self,
        state: SniperState,
        entry_price: float,
        current_price: float,
        config: ProfitSniperConfig
    ) -> SniperDecision:
        """
        Inline ProfitSniper evaluation without SQLite persistence.
        Mirrors the logic in ProfitSniper.evaluate() but operates on local state.

        NOTE: This is a deliberate duplicate of ProfitSniper decision logic to avoid
        SQLite state dependencies during batch backtesting. If ProfitSniper.evaluate()
        is modified, this method MUST be updated in parallel to prevent drift.
        Key thresholds replicated: ratchet arming, distance tightening, velocity
        reversal detection, and exhaustion bar counting.
        """
        if not config.enabled:
            return SniperDecision()

        # Calculate current profit %
        profit_pct = ((current_price - entry_price) / entry_price) * 100

        # Update peak tracking
        if profit_pct > state.peak_profit_pct:
            state.peak_profit_pct = profit_pct
            state.peak_price = current_price
            state.peak_timestamp = datetime.utcnow().isoformat() + "Z"

        # Update profit samples
        state.profit_samples.append(profit_pct)
        max_samples = config.velocity_window * 2
        if len(state.profit_samples) > max_samples:
            state.profit_samples = state.profit_samples[-max_samples:]

        # CHECK 1: RATCHET
        if not state.ratchet_armed and profit_pct >= config.ratchet_arm_pct:
            state.ratchet_armed = True
            distance_pct = config.ratchet_base_distance_pct
            state.ratchet_price = state.peak_price * (1 - distance_pct / 100)

        if state.ratchet_armed:
            excess_profit = max(0, profit_pct - config.ratchet_arm_pct)
            tightening = excess_profit * config.ratchet_tighten_per_pct
            distance_pct = max(
                config.ratchet_min_distance_pct,
                config.ratchet_base_distance_pct - tightening
            )
            new_ratchet = state.peak_price * (1 - distance_pct / 100)
            if new_ratchet > state.ratchet_price:
                state.ratchet_price = new_ratchet

            if current_price <= state.ratchet_price:
                exit_pct = config.partial_exit_pct if state.sniper_triggered_count == 0 else 100.0
                giveback = state.peak_profit_pct - profit_pct
                state.sniper_triggered_count += 1
                return SniperDecision(
                    should_exit=True,
                    exit_pct=exit_pct,
                    reason=f"ratchet_breach_peak_{state.peak_profit_pct:.1f}pct",
                    confidence=min(1.0, state.peak_profit_pct / 2.0),
                    peak_profit_pct=state.peak_profit_pct,
                    current_profit_pct=profit_pct,
                    details={"giveback_pct": round(giveback, 3)}
                )

        # CHECK 2: VELOCITY REVERSAL
        samples = state.profit_samples
        window = config.velocity_window
        if len(samples) >= window:
            recent = samples[-window:]
            velocity = (recent[-1] - recent[0]) / window

            if velocity > state.peak_velocity:
                state.peak_velocity = velocity

            if state.peak_velocity > 0.1 and velocity < 0:
                reversal_magnitude = abs(velocity) / state.peak_velocity
                if (reversal_magnitude >= config.velocity_reversal_pct and
                        profit_pct > config.ratchet_arm_pct * 0.5):
                    exit_pct = config.partial_exit_pct if state.sniper_triggered_count == 0 else 100.0
                    state.sniper_triggered_count += 1
                    return SniperDecision(
                        should_exit=True,
                        exit_pct=exit_pct,
                        reason=f"velocity_reversal_{reversal_magnitude:.0%}",
                        peak_profit_pct=state.peak_profit_pct,
                        current_profit_pct=profit_pct,
                        velocity=velocity
                    )

        # CHECK 3: EXHAUSTION
        if len(state.profit_samples) >= 2:
            current_gain = state.profit_samples[-1] - state.profit_samples[-2]
            if current_gain < state.last_bar_gain and current_gain < 0:
                state.consecutive_weaker_bars += 1
            else:
                state.consecutive_weaker_bars = 0
            state.last_bar_gain = current_gain

            if (state.consecutive_weaker_bars >= config.exhaustion_bars and
                    profit_pct >= config.exhaustion_min_profit_pct):
                exit_pct = config.partial_exit_pct if state.sniper_triggered_count == 0 else 100.0
                state.sniper_triggered_count += 1
                return SniperDecision(
                    should_exit=True,
                    exit_pct=exit_pct,
                    reason=f"exhaustion_{state.consecutive_weaker_bars}_bars",
                    peak_profit_pct=state.peak_profit_pct,
                    current_profit_pct=profit_pct
                )

        return SniperDecision(
            peak_profit_pct=state.peak_profit_pct,
            current_profit_pct=profit_pct
        )

    def _aggregate_trades(
        self,
        trades: List[SniperTrade],
        config: ProfitSniperConfig,
        symbol: str,
        asset_class: str
    ) -> SniperBacktestResult:
        """Compute aggregated metrics from trade list."""
        if not trades:
            return SniperBacktestResult(
                config=self._config_to_dict(config),
                symbol=symbol,
                asset_class=asset_class,
                trades=trades
            )

        winners = [t for t in trades if t.exit_profit_pct > 0]
        losers = [t for t in trades if t.exit_profit_pct <= 0]
        premature = [t for t in trades if t.was_premature]

        pnl_list = [t.exit_profit_pct for t in trades]
        capture_list = [t.captured_vs_peak for t in trades if t.peak_profit_pct > 0]
        peak_list = [t.peak_profit_pct for t in trades]
        giveback_list = [t.peak_profit_pct - t.exit_profit_pct for t in trades]
        bars_list = [t.bars_held for t in trades]

        return SniperBacktestResult(
            config=self._config_to_dict(config),
            symbol=symbol,
            asset_class=asset_class,
            trades=trades,
            total_trades=len(trades),
            winning_trades=len(winners),
            losing_trades=len(losers),
            win_rate=len(winners) / len(trades) if trades else 0,
            total_pnl_pct=sum(pnl_list),
            avg_pnl_pct=statistics.mean(pnl_list) if pnl_list else 0,
            avg_captured_vs_peak=statistics.mean(capture_list) if capture_list else 0,
            premature_exit_rate=len(premature) / len(trades) if trades else 0,
            avg_peak_profit_pct=statistics.mean(peak_list) if peak_list else 0,
            avg_giveback_pct=statistics.mean(giveback_list) if giveback_list else 0,
            max_drawdown_trade_pct=min(pnl_list) if pnl_list else 0,
            avg_bars_held=statistics.mean(bars_list) if bars_list else 0,
        )

    def optimize(
        self,
        symbols: List[str],
        days: int = 30,
        param_grid: Optional[Dict[str, List[Any]]] = None,
        optimize_for: str = "score",
        max_hold_bars: int = 120
    ) -> SniperOptimizationResult:
        """
        Grid search optimization for ProfitSniper settings.

        Tests all combinations in param_grid across the given symbols.
        The "score" metric is a composite: (total_pnl * 0.4 + capture_rate * 0.3
        + win_rate * 0.2 - premature_rate * 0.1).

        Args:
            symbols: List of ticker symbols to test
            days: Days of historical data
            param_grid: Dict of param name -> list of values to test
            optimize_for: Metric to optimize
            max_hold_bars: Maximum bars to hold per trade

        Returns:
            SniperOptimizationResult with best config and all results
        """
        asset_class = self._detect_asset_class(symbols[0]) if symbols else "stocks"

        # Default param grid
        if param_grid is None:
            if asset_class == "crypto":
                param_grid = {
                    "ratchet_arm_pct": [0.2, 0.3, 0.5, 0.8, 1.0],
                    "ratchet_base_distance_pct": [0.1, 0.15, 0.25, 0.4, 0.6],
                    "ratchet_tighten_per_pct": [0.01, 0.02, 0.03, 0.05],
                    "ratchet_min_distance_pct": [0.03, 0.05, 0.08, 0.12],
                    "velocity_reversal_pct": [0.15, 0.2, 0.3, 0.5],
                }
            elif asset_class == "options":
                param_grid = {
                    "ratchet_arm_pct": [2.0, 3.0, 5.0, 7.0],
                    "ratchet_base_distance_pct": [1.0, 2.0, 3.0, 5.0],
                    "ratchet_tighten_per_pct": [0.1, 0.15, 0.2, 0.3],
                    "ratchet_min_distance_pct": [0.3, 0.5, 1.0, 1.5],
                    "velocity_reversal_pct": [0.5, 1.0, 1.5, 2.0],
                }
            else:  # stocks
                param_grid = {
                    "ratchet_arm_pct": [0.3, 0.5, 0.75, 1.0, 1.5],
                    "ratchet_base_distance_pct": [0.15, 0.25, 0.4, 0.6],
                    "ratchet_tighten_per_pct": [0.02, 0.03, 0.05, 0.08],
                    "ratchet_min_distance_pct": [0.05, 0.08, 0.12, 0.2],
                    "velocity_reversal_pct": [0.2, 0.3, 0.5, 0.7],
                }

        # Generate all combinations
        param_names = list(param_grid.keys())
        param_values = list(param_grid.values())
        combinations = list(product(*param_values))

        logger.log("sniper_optimization_start", {
            "asset_class": asset_class,
            "symbols": symbols,
            "combinations": len(combinations),
            "params": param_names
        })

        # Get default config for this asset class
        if asset_class == "crypto":
            default_config = ProfitSniperConfig.for_crypto()
        elif asset_class == "options":
            default_config = ProfitSniperConfig.for_options()
        else:
            default_config = ProfitSniperConfig.for_stocks()

        # Run default config first
        default_results = []
        for symbol in symbols:
            result = self.run_single_backtest(symbol, default_config, days, max_hold_bars)
            default_results.append(result)

        default_score = self._compute_composite_score(default_results)

        # Test each combination
        all_results = []
        best_score = -999
        best_config_dict = {}
        best_metrics = {}

        for combo_idx, combo in enumerate(combinations):
            config_dict = self._config_to_dict(default_config)
            for i, param in enumerate(param_names):
                config_dict[param] = combo[i]

            # Create ProfitSniperConfig from dict
            test_config = ProfitSniperConfig(**{
                k: v for k, v in config_dict.items()
                if k in ProfitSniperConfig.__dataclass_fields__
            })

            # Run backtest across all symbols
            results = []
            for symbol in symbols:
                result = self.run_single_backtest(symbol, test_config, days, max_hold_bars)
                results.append(result)

            score = self._compute_composite_score(results)

            combo_result = {
                "config": config_dict,
                "score": round(score, 4),
                "total_trades": sum(r.total_trades for r in results),
                "win_rate": round(sum(r.win_rate for r in results) / len(results), 3) if results else 0,
                "total_pnl_pct": round(sum(r.total_pnl_pct for r in results), 3),
                "avg_captured_vs_peak": round(
                    sum(r.avg_captured_vs_peak for r in results) / len(results), 3
                ) if results else 0,
                "premature_exit_rate": round(
                    sum(r.premature_exit_rate for r in results) / len(results), 3
                ) if results else 0,
                "avg_giveback_pct": round(
                    sum(r.avg_giveback_pct for r in results) / len(results), 3
                ) if results else 0,
            }
            all_results.append(combo_result)

            if score > best_score:
                best_score = score
                best_config_dict = config_dict
                best_metrics = {
                    "score": score,
                    "total_trades": combo_result["total_trades"],
                    "win_rate": combo_result["win_rate"],
                    "total_pnl_pct": combo_result["total_pnl_pct"],
                    "avg_captured_vs_peak": combo_result["avg_captured_vs_peak"],
                    "premature_exit_rate": combo_result["premature_exit_rate"],
                    "avg_giveback_pct": combo_result["avg_giveback_pct"],
                }

            # Progress logging every 50 combos
            if (combo_idx + 1) % 50 == 0:
                logger.log("sniper_optimization_progress", {
                    "tested": combo_idx + 1,
                    "total": len(combinations),
                    "best_score_so_far": round(best_score, 4)
                })

        # Calculate improvement
        improvement = 0.0
        if default_score != 0:
            improvement = ((best_score - default_score) / abs(default_score)) * 100

        # Build recommended config
        recommended = ProfitSniperConfig(**{
            k: v for k, v in best_config_dict.items()
            if k in ProfitSniperConfig.__dataclass_fields__
        })

        logger.log("sniper_optimization_complete", {
            "asset_class": asset_class,
            "best_score": round(best_score, 4),
            "default_score": round(default_score, 4),
            "improvement_pct": round(improvement, 1),
            "best_config": best_config_dict,
            "combinations_tested": len(combinations)
        })

        return SniperOptimizationResult(
            asset_class=asset_class,
            symbols_tested=symbols,
            best_config=best_config_dict,
            best_metrics=best_metrics,
            all_results=all_results,
            improvement_vs_default_pct=improvement,
            recommended_config=recommended
        )

    def _compute_composite_score(self, results: List[SniperBacktestResult]) -> float:
        """
        Compute composite optimization score from multiple backtest results.

        Score = (total_pnl_weight * total_pnl) + (capture_weight * capture_rate)
              + (win_weight * win_rate) - (premature_penalty * premature_rate)
              - (giveback_penalty * giveback)
        """
        if not results:
            return 0.0

        n = len(results)
        avg_pnl = sum(r.total_pnl_pct for r in results) / n
        avg_capture = sum(r.avg_captured_vs_peak for r in results) / n
        avg_win_rate = sum(r.win_rate for r in results) / n
        avg_premature = sum(r.premature_exit_rate for r in results) / n
        avg_giveback = sum(r.avg_giveback_pct for r in results) / n
        total_trades = sum(r.total_trades for r in results)

        # Penalize configs that produce too few trades
        trade_penalty = 0.0
        if total_trades < 10:
            trade_penalty = 2.0

        score = (
            avg_pnl * 0.35 +
            avg_capture * 0.25 * 10 +
            avg_win_rate * 0.20 * 10 -
            avg_premature * 0.10 * 10 -
            avg_giveback * 0.10 -
            trade_penalty
        )

        return score

    def _config_to_dict(self, config: ProfitSniperConfig) -> Dict[str, Any]:
        """Convert ProfitSniperConfig to dict."""
        return {
            "enabled": config.enabled,
            "velocity_window": config.velocity_window,
            "velocity_reversal_pct": config.velocity_reversal_pct,
            "ratchet_arm_pct": config.ratchet_arm_pct,
            "ratchet_base_distance_pct": config.ratchet_base_distance_pct,
            "ratchet_tighten_per_pct": config.ratchet_tighten_per_pct,
            "ratchet_min_distance_pct": config.ratchet_min_distance_pct,
            "exhaustion_bars": config.exhaustion_bars,
            "exhaustion_min_profit_pct": config.exhaustion_min_profit_pct,
            "partial_exit_pct": config.partial_exit_pct,
            "full_exit_on_second": config.full_exit_on_second,
        }

    def apply_to_yaml(self, opt_result: SniperOptimizationResult,
                       config_path: str = "config/bots.yaml") -> bool:
        """
        Write optimized settings back to bots.yaml profit_sniper section.

        Only updates the asset class section that was optimized.
        """
        try:
            with open(config_path, "r") as f:
                full_config = yaml.safe_load(f)

            if "profit_sniper" not in full_config:
                full_config["profit_sniper"] = {}

            # Extract only the sniper-relevant keys
            sniper_keys = [
                "enabled", "velocity_window", "velocity_reversal_pct",
                "ratchet_arm_pct", "ratchet_base_distance_pct",
                "ratchet_tighten_per_pct", "ratchet_min_distance_pct",
                "exhaustion_bars", "exhaustion_min_profit_pct",
                "partial_exit_pct", "full_exit_on_second"
            ]

            optimized_section = {
                k: opt_result.best_config[k]
                for k in sniper_keys
                if k in opt_result.best_config
            }

            full_config["profit_sniper"][opt_result.asset_class] = optimized_section

            with open(config_path, "w") as f:
                yaml.dump(full_config, f, default_flow_style=False, sort_keys=False)

            logger.log("sniper_optimization_applied", {
                "asset_class": opt_result.asset_class,
                "config_path": config_path,
                "settings": optimized_section
            })

            return True

        except Exception as e:
            logger.error(f"Failed to write optimized settings to YAML: {e}")
            return False

    def export_results(self, opt_result: SniperOptimizationResult,
                        filepath: str = "logs/sniper_optimization.json") -> None:
        """Export optimization results to JSON for analysis."""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(opt_result.to_dict(), f, indent=2, default=str)
        logger.log("sniper_results_exported", {"filepath": filepath})


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def optimize_crypto(days: int = 30, apply: bool = False) -> SniperOptimizationResult:
    """Quick-run crypto ProfitSniper optimization."""
    bt = SniperBacktester()
    symbols = ["BTC/USD", "SOL/USD", "AVAX/USD", "LTC/USD", "UNI/USD"]
    result = bt.optimize(symbols, days=days)
    bt.export_results(result, "logs/sniper_opt_crypto.json")
    if apply:
        bt.apply_to_yaml(result)
    return result


def optimize_stocks(days: int = 30, apply: bool = False) -> SniperOptimizationResult:
    """Quick-run stocks ProfitSniper optimization."""
    bt = SniperBacktester()
    symbols = ["AAPL", "TSLA", "NVDA", "AMD", "PLTR"]
    result = bt.optimize(symbols, days=days)
    bt.export_results(result, "logs/sniper_opt_stocks.json")
    if apply:
        bt.apply_to_yaml(result)
    return result


def optimize_options(days: int = 30, apply: bool = False) -> SniperOptimizationResult:
    """Quick-run options ProfitSniper optimization."""
    bt = SniperBacktester()
    symbols = ["SPY", "QQQ"]
    result = bt.optimize(symbols, days=days)
    bt.export_results(result, "logs/sniper_opt_options.json")
    if apply:
        bt.apply_to_yaml(result)
    return result


def optimize_all(days: int = 30, apply: bool = False) -> Dict[str, SniperOptimizationResult]:
    """Run optimization for all asset classes."""
    results = {}
    for name, func in [("crypto", optimize_crypto), ("stocks", optimize_stocks), ("options", optimize_options)]:
        try:
            results[name] = func(days=days, apply=apply)
        except Exception as e:
            logger.error(f"Optimization failed for {name}: {e}")
    return results
