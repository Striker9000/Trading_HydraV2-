"""
=============================================================================
SessionSelectorBot - Dynamic Momentum Ticker Selection
=============================================================================

Replaces the static momentum_bots list with screener-driven dynamic selection.
Each trading session, this bot:

1. Runs the StockScreener to score all candidates (momentum, volume, spread, volatility)
2. Selects the top N tickers that pass composite score thresholds
3. Creates ephemeral MomentumBot instances for each selected ticker
4. Manages those bots through the trading session
5. Persists selections to state for dashboard visibility

The old static momentum_bots remain in bots.yaml (disabled) so users can
still manually add tickers for trading.

Config: config/bots.yaml > session_selector section
=============================================================================
"""

from __future__ import annotations

import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime

from ..core.logging import get_logger
from ..core.config import load_bots_config
from ..core.state import get_state, set_state
from ..services.stock_screener import StockScreener, ScreeningResult


@dataclass
class SessionSelection:
    """Result of a session ticker selection run."""
    selected_tickers: List[str]
    scores: Dict[str, float]
    bot_ids: List[str]
    selection_time: str
    from_cache: bool


@dataclass
class SessionSelectorConfig:
    """Configuration for the SessionSelectorBot."""
    enabled: bool = True
    max_tickers: int = 5
    min_composite_score: float = 30.0
    budget_per_ticker_pct: float = 20.0
    trade_start: str = "06:35"
    trade_end: str = "12:55"
    manage_until: str = "12:55"
    max_trades_per_ticker: int = 2
    max_concurrent_positions: int = 2
    # Turtle settings to pass to spawned momentum bots
    turtle_enabled: bool = True
    turtle_system: str = "system_1"
    turtle_entry_lookback: int = 5
    turtle_exit_lookback: int = 3
    turtle_atr_period: int = 14
    turtle_risk_pct_per_unit: float = 1.0
    turtle_stop_loss_atr_mult: float = 1.5
    turtle_pyramid_enabled: bool = False
    turtle_max_units: int = 1
    turtle_winner_filter_enabled: bool = False
    turtle_require_trend_confirm: bool = True
    turtle_min_volume_mult: float = 2.0
    # Exit settings
    stop_loss_pct: float = 1.0
    take_profit_pct: float = 1.0
    time_stop_minutes: int = 60
    # Dynamic trailing
    dynamic_trailing_enabled: bool = True
    atr_multiplier: float = 2.5
    activation_atr_mult: float = 0.75
    min_trail_pct: float = 0.5
    max_trail_pct: float = 8.0


class SessionSelectorBot:
    """
    Dynamic momentum ticker selector.

    Uses the StockScreener to pick top tickers each session, then creates
    ephemeral momentum bot configs for the ExecutionService to run.
    """

    def __init__(self, bot_id: str = "session_selector"):
        self.bot_id = bot_id
        self._logger = get_logger()
        self._screener = StockScreener()
        self._config = self._load_config()
        self._current_selection: Optional[SessionSelection] = None
        self._last_screen_date: Optional[str] = None

        self._logger.log("session_selector_init", {
            "bot_id": bot_id,
            "enabled": self._config.enabled if self._config else False,
            "max_tickers": self._config.max_tickers if self._config else 0
        })

    def _load_config(self) -> Optional[SessionSelectorConfig]:
        """Load config from bots.yaml session_selector section."""
        try:
            bots_config = load_bots_config()
            cfg = bots_config.get("session_selector", {})
            if not cfg:
                self._logger.warn("No session_selector config found in bots.yaml")
                return None

            session = cfg.get("session", {})
            risk = cfg.get("risk", {})
            turtle = cfg.get("turtle", {})
            exits = cfg.get("exits", {})
            trailing = risk.get("dynamic_trailing", {})

            return SessionSelectorConfig(
                enabled=cfg.get("enabled", True),
                max_tickers=cfg.get("max_tickers", 5),
                min_composite_score=cfg.get("min_composite_score", 30.0),
                budget_per_ticker_pct=cfg.get("budget_per_ticker_pct", 20.0),
                trade_start=session.get("trade_start", "06:35"),
                trade_end=session.get("trade_end", "12:55"),
                manage_until=session.get("manage_until", "12:55"),
                max_trades_per_ticker=risk.get("max_trades_per_ticker", 2),
                max_concurrent_positions=risk.get("max_concurrent_positions", 2),
                turtle_enabled=turtle.get("enabled", True),
                turtle_system=turtle.get("system", "system_1"),
                turtle_entry_lookback=turtle.get("entry_lookback", 5),
                turtle_exit_lookback=turtle.get("exit_lookback", 3),
                turtle_atr_period=turtle.get("atr_period", 14),
                turtle_risk_pct_per_unit=turtle.get("risk_pct_per_unit", 1.0),
                turtle_stop_loss_atr_mult=turtle.get("stop_loss_atr_mult", 1.5),
                turtle_pyramid_enabled=turtle.get("pyramid_enabled", False),
                turtle_max_units=turtle.get("max_units", 1),
                turtle_winner_filter_enabled=turtle.get("winner_filter_enabled", False),
                turtle_require_trend_confirm=turtle.get("require_trend_confirm", True),
                turtle_min_volume_mult=turtle.get("min_volume_mult", 2.0),
                stop_loss_pct=exits.get("stop_loss_pct", 1.0),
                take_profit_pct=exits.get("take_profit_pct", 1.0),
                time_stop_minutes=exits.get("time_stop_minutes", 60),
                dynamic_trailing_enabled=trailing.get("enabled", True),
                atr_multiplier=trailing.get("atr_multiplier", 2.5),
                activation_atr_mult=trailing.get("activation_atr_mult", 0.75),
                min_trail_pct=trailing.get("min_trail_pct", 0.5),
                max_trail_pct=trailing.get("max_trail_pct", 8.0),
            )
        except Exception as e:
            self._logger.error(f"Failed to load session_selector config: {e}")
            return None

    def select_tickers(self, force_refresh: bool = False) -> SessionSelection:
        """
        Run the screener and select top tickers for this session.

        Caches per day — only re-screens if date changes or force_refresh=True.

        Returns:
            SessionSelection with selected tickers and generated bot IDs
        """
        today = datetime.utcnow().strftime("%Y-%m-%d")

        # Return cached selection if same day
        if (not force_refresh
                and self._current_selection is not None
                and self._last_screen_date == today):
            self._logger.log("session_selector_cache_hit", {
                "selected": self._current_selection.selected_tickers
            })
            return self._current_selection

        self._logger.log("session_selector_screening_start", {
            "date": today,
            "force_refresh": force_refresh
        })

        # Run the stock screener
        screen_result: ScreeningResult = self._screener.screen(force_refresh=True)

        # Filter by minimum composite score
        min_score = self._config.min_composite_score if self._config else 30.0
        max_tickers = self._config.max_tickers if self._config else 5

        qualifying = [
            s for s in screen_result.all_scores
            if s.passed_filters and s.composite_score >= min_score
        ]

        # Sort by composite score descending, take top N
        qualifying.sort(key=lambda s: s.composite_score, reverse=True)
        selected = qualifying[:max_tickers]

        selected_tickers = [s.ticker for s in selected]
        scores = {s.ticker: round(s.composite_score, 1) for s in selected}

        # Generate bot IDs for each selected ticker
        bot_ids = [f"session_{ticker}" for ticker in selected_tickers]

        selection = SessionSelection(
            selected_tickers=selected_tickers,
            scores=scores,
            bot_ids=bot_ids,
            selection_time=datetime.utcnow().isoformat(),
            from_cache=False
        )

        # Persist to state for dashboard and execution service
        set_state("session_selector.selected_tickers", selected_tickers)
        set_state("session_selector.scores", scores)
        set_state("session_selector.bot_ids", bot_ids)
        set_state("session_selector.selection_date", today)
        set_state("session_selector.selection_time", selection.selection_time)

        # Also update the screener active_stocks for UniverseGuard
        set_state("screener.active_stocks", selected_tickers)

        self._current_selection = selection
        self._last_screen_date = today

        self._logger.log("session_selector_complete", {
            "selected_tickers": selected_tickers,
            "scores": scores,
            "bot_ids": bot_ids,
            "total_candidates": len(screen_result.all_scores),
            "qualifying_count": len(qualifying)
        })

        return selection

    def get_dynamic_bot_configs(self) -> List[Dict[str, Any]]:
        """
        Generate ephemeral momentum bot configs for selected tickers.

        These configs match the momentum_bots[] format in bots.yaml so the
        ExecutionService can treat them identically to static momentum bots.

        Returns:
            List of bot config dicts compatible with momentum_bots[] format
        """
        if self._current_selection is None:
            self.select_tickers()

        if self._current_selection is None or not self._current_selection.selected_tickers:
            return []

        configs = []
        cfg = self._config or SessionSelectorConfig()

        for ticker in self._current_selection.selected_tickers:
            bot_id = f"session_{ticker}"
            bot_config = {
                "bot_id": bot_id,
                "enabled": True,
                "ticker": ticker,
                "direction": "both",
                "source": "session_selector",
                "session": {
                    "trade_start": cfg.trade_start,
                    "trade_end": cfg.trade_end,
                    "manage_until": cfg.manage_until,
                },
                "risk": {
                    "max_trades_per_day": cfg.max_trades_per_ticker,
                    "max_concurrent_positions": cfg.max_concurrent_positions,
                    "dynamic_trailing": {
                        "enabled": cfg.dynamic_trailing_enabled,
                        "atr_multiplier": cfg.atr_multiplier,
                        "activation_atr_mult": cfg.activation_atr_mult,
                        "min_trail_pct": cfg.min_trail_pct,
                        "max_trail_pct": cfg.max_trail_pct,
                    },
                },
                "turtle": {
                    "enabled": cfg.turtle_enabled,
                    "system": cfg.turtle_system,
                    "entry_lookback": cfg.turtle_entry_lookback,
                    "exit_lookback": cfg.turtle_exit_lookback,
                    "atr_period": cfg.turtle_atr_period,
                    "risk_pct_per_unit": cfg.turtle_risk_pct_per_unit,
                    "stop_loss_atr_mult": cfg.turtle_stop_loss_atr_mult,
                    "pyramid_enabled": cfg.turtle_pyramid_enabled,
                    "max_units": cfg.turtle_max_units,
                    "winner_filter_enabled": cfg.turtle_winner_filter_enabled,
                    "require_trend_confirm": cfg.turtle_require_trend_confirm,
                    "min_volume_mult": cfg.turtle_min_volume_mult,
                },
                "exits": {
                    "stop_loss_pct": cfg.stop_loss_pct,
                    "take_profit_pct": cfg.take_profit_pct,
                    "time_stop_minutes": cfg.time_stop_minutes,
                },
                "signal": {
                    "mode": "turtle",
                    "params": {},
                },
            }
            configs.append(bot_config)

        self._logger.log("session_selector_configs_generated", {
            "count": len(configs),
            "tickers": self._current_selection.selected_tickers
        })

        return configs

    def get_enabled_bot_ids(self) -> List[str]:
        """Return list of session bot IDs for the execution service."""
        if self._current_selection is None:
            self.select_tickers()
        if self._current_selection is None:
            return []
        return self._current_selection.bot_ids

    def execute(self, max_daily_loss: float = 500.0) -> Dict[str, Any]:
        """
        Main execution entry point.

        Called by the execution service. Runs screening if needed,
        then returns the selected tickers and configs.
        """
        if not self._config or not self._config.enabled:
            return {
                "success": False,
                "reason": "session_selector_disabled",
                "selected_tickers": [],
                "bot_ids": []
            }

        selection = self.select_tickers()

        return {
            "success": True,
            "selected_tickers": selection.selected_tickers,
            "scores": selection.scores,
            "bot_ids": selection.bot_ids,
            "from_cache": selection.from_cache
        }


# =============================================================================
# SINGLETON
# =============================================================================
_session_selector: Optional[SessionSelectorBot] = None


def get_session_selector(bot_id: str = "session_selector") -> SessionSelectorBot:
    """Get or create the SessionSelectorBot singleton."""
    global _session_selector
    if _session_selector is None:
        _session_selector = SessionSelectorBot(bot_id)
    return _session_selector
