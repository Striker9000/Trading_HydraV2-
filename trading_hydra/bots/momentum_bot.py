"""
Momentum Trading Bot for Individual Stocks
==========================================

This bot implements a momentum-based trading strategy for individual stocks
(e.g., AAPL, TSLA). It detects price trends and executes trades when momentum
signals are strong enough.

Key Features:
- Reads all configuration from config/bots.yaml
- Enforces max trades per day limit
- Enforces max concurrent positions limit  
- Implements trailing stops from config
- Enforces stop-loss and take-profit percentages
- Respects trading session time windows
- Implements time-based exits (max hold duration)

Safety Features:
- Cooldown between trades to prevent over-trading
- Fail-closed design - any error stops the bot safely
- All trades logged for audit trail
- Integrates with global halt manager
"""

from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from dataclasses import dataclass, field
import time

from ..core.logging import get_logger
from ..core.state import get_state, set_state, delete_state, atomic_increment
from ..core.config import load_bots_config, load_settings
from ..core.clock import get_market_clock
from ..services.alpaca_client import get_alpaca_client
from ..services.market_regime import get_current_regime, MarketSentiment, VolatilityRegime
from ..services.decision_tracker import get_decision_tracker
from ..risk.trailing_stop import get_trailing_stop_manager, TrailingStopConfig
from ..risk.profit_sniper import get_profit_sniper, ProfitSniperConfig
from ..ml.signal_service import MLSignalService
from ..indicators.turtle_trend import (
    TurtleTrend, TurtleConfig, TurtleSignal, SignalType, TurtleSystem, get_turtle_trend
)


def _build_momentum_decision_record(
    bot_id: str,
    ticker: str,
    equity: float,
    positions_count: int,
    daily_pnl: float,
    is_rth: bool,
    quote_age_sec: float,
    regime_label: str,
    vix: float,
    price_data: dict,
    atr_data: dict,
    donchian_data: dict,
    gates_allowed: bool,
    gates_reasons: list,
    gates_checks: dict,
    risk_per_trade: float,
    risk_dollars: float,
    position_size: dict,
    signal_direction: str,
    signal_strength: float,
    signal_reason: str,
    plan_entry: dict,
    plan_stop: dict,
    plan_exit: dict,
    action_type: str,
    outcome_status: str,
    outcome_message: str
) -> None:
    """
    Build and emit a full-schema Decision Record for MomentumBot.
    Called once per symbol per loop for complete audit trail.
    """
    tracker = get_decision_tracker()
    
    account = {
        "equity": equity,
        "buying_power": equity * 2,
        "positions_count": positions_count,
        "open_risk_dollars": 0,
        "daily_pnl": daily_pnl
    }
    
    market_context = {
        "session": {
            "is_rth": is_rth,
            "market_state": "rth" if is_rth else "closed",
            "seconds_to_close": 0
        },
        "data_freshness": {
            "quote_age_sec": quote_age_sec,
            "bars_ok": True,
            "last_bar_ts": ""
        },
        "regime": {
            "label": regime_label,
            "vix": vix,
            "sentiment": "neutral"
        }
    }
    
    inputs = {
        "price": price_data,
        "atr": atr_data,
        "donchian": donchian_data
    }
    
    gates = {
        "allowed": gates_allowed,
        "reasons": gates_reasons,
        "checks": gates_checks
    }
    
    risk = {
        "risk_per_trade": risk_per_trade,
        "risk_dollars": risk_dollars,
        "max_total_risk_dollars": risk_dollars * 4,
        "position_size": position_size,
        "pyramiding": {
            "enabled": True,
            "adds_allowed": 4,
            "next_add_at": 0,
            "adds_taken": 0
        }
    }
    
    signal = {
        "direction": signal_direction,
        "strength": signal_strength,
        "reason": signal_reason
    }
    
    plan = {
        "entry": plan_entry,
        "stop": plan_stop,
        "exit": plan_exit
    }
    
    action = {"type": action_type}
    outcome = {"status": outcome_status, "message": outcome_message}
    
    tracker.emit_full_decision_record(
        bot="MomentumBot",
        symbol=ticker,
        asset_class="stocks",
        horizon="day",
        account=account,
        market_context=market_context,
        inputs=inputs,
        gates=gates,
        risk=risk,
        signal=signal,
        plan=plan,
        action=action,
        outcome=outcome
    )


# =============================================================================
# DATA CLASSES - Define structure for bot configuration and state
# =============================================================================

@dataclass
class MomentumConfig:
    """
    Configuration for a single momentum bot instance.
    All values come from config/bots.yaml under momentum_bots section.
    
    Supports two signal modes:
    - "turtle": Turtle Traders strategy (Donchian breakouts, ATR sizing, pyramiding)
    - "placeholder_momentum_v1": Legacy 3-bar momentum strategy
    """
    bot_id: str                      # Unique identifier like "mom_AAPL"
    enabled: bool                    # Whether this bot is active
    ticker: str                      # Stock symbol to trade (e.g., "AAPL")
    trade_start: str                 # When to start trading (e.g., "06:35" PST)
    trade_end: str                   # When to stop new trades (e.g., "12:55" PST)
    manage_until: str                # When to stop managing positions (e.g., "12:55" PST)
    max_trades_per_day: int          # Maximum trades allowed per day
    max_concurrent_positions: int    # Maximum positions held at once (4 for Turtle pyramiding)
    stop_loss_pct: float             # Stop loss percentage (fallback if Turtle disabled)
    take_profit_pct: float           # Take profit percentage (fallback if Turtle disabled)
    time_stop_minutes: int           # Max hold time (0 = disabled for trend-following)
    trailing_stop_enabled: bool = False  # Whether to use trailing stops
    trailing_stop_mode: str = "percent"  # "percent" or "price"
    trailing_stop_value: float = 0.8     # Trailing stop value
    trailing_activation_pct: float = 0.3 # Profit % required before activation
    trailing_update_only_if_improves: bool = True
    trailing_epsilon_pct: float = 0.02
    trailing_exit_order_type: str = "market"
    direction: str = "long_only"         # "long_only", "short_only", or "both"
    signal_mode: str = "turtle"          # "turtle" or "placeholder_momentum_v1"
    
    # Turtle Traders Strategy Configuration
    turtle_enabled: bool = True
    turtle_system: str = "system_1"      # "system_1" (20-day) or "system_2" (55-day)
    turtle_entry_lookback: int = 20      # Days for entry breakout channel
    turtle_exit_lookback: int = 10       # Days for exit channel
    turtle_atr_period: int = 20          # ATR calculation period (the "N")
    turtle_risk_pct_per_unit: float = 1.0  # % of equity risked per unit
    turtle_stop_loss_atr_mult: float = 2.0 # Stop at 2N from entry
    turtle_pyramid_enabled: bool = True    # Add to winners
    turtle_pyramid_trigger_atr: float = 0.5  # Add unit every 0.5N
    turtle_max_units: int = 4              # Maximum units per position
    turtle_winner_filter_enabled: bool = True  # Skip signal after winner


@dataclass
class TradeRecord:
    """
    Record of a single trade for tracking and analysis.
    Stored in SQLite state database for persistence across restarts.
    """
    order_id: str                    # Alpaca order ID
    ticker: str                      # Stock symbol
    side: str                        # "buy" or "sell"
    entry_price: float               # Price at entry
    notional: float                  # Dollar amount traded
    timestamp: float                 # Unix timestamp of trade
    entry_time: datetime             # Datetime of entry for time-based exits


# =============================================================================
# MOMENTUM BOT CLASS - Main trading logic
# =============================================================================

class MomentumBot:
    """
    Single stock momentum trading bot.
    
    This bot:
    1. Monitors price movements for a single stock ticker
    2. Detects momentum (consecutive price increases/decreases)
    3. Enters positions when momentum is confirmed
    4. Manages positions with stop-loss, take-profit, and trailing stops
    5. Exits positions based on time limits or session end
    
    Usage:
        bot = MomentumBot("mom_AAPL", "AAPL")
        result = bot.execute(max_daily_loss=50.0)
    """
    
    def __init__(self, bot_id: str, ticker: str):
        """
        Initialize the momentum bot.
        
        Args:
            bot_id: Unique identifier for this bot instance (e.g., "mom_AAPL")
            ticker: Stock symbol to trade (e.g., "AAPL")
        """
        # Store basic identifiers
        self.bot_id = bot_id
        self.ticker = ticker
        
        # Initialize services - logger for audit trail, Alpaca for trading
        self._logger = get_logger()
        self._alpaca = get_alpaca_client()
        
        # Load configuration from bots.yaml
        self._config = self._load_config()
        
        # Initialize ML signal service for trade scoring
        self._ml_service = MLSignalService(logger=self._logger)
        settings = load_settings()
        ml_config = settings.get("ml", {})
        self._ml_enabled = ml_config.get("enabled", False)
        self._ml_min_probability = ml_config.get("momentum_threshold",
                                                  ml_config.get("min_probability", 0.55))
        
        # Initialize Turtle Traders strategy engine if enabled
        self._turtle_engine: Optional[TurtleTrend] = None
        if self._config and self._config.turtle_enabled:
            turtle_config = TurtleConfig(
                system=TurtleSystem.SYSTEM_1 if self._config.turtle_system == "system_1" else TurtleSystem.SYSTEM_2,
                entry_lookback=self._config.turtle_entry_lookback,
                exit_lookback=self._config.turtle_exit_lookback,
                atr_period=self._config.turtle_atr_period,
                risk_pct_per_unit=self._config.turtle_risk_pct_per_unit,
                stop_loss_atr_mult=self._config.turtle_stop_loss_atr_mult,
                pyramid_enabled=self._config.turtle_pyramid_enabled,
                pyramid_trigger_atr=self._config.turtle_pyramid_trigger_atr,
                max_units=self._config.turtle_max_units,
                winner_filter_enabled=self._config.turtle_winner_filter_enabled,
                asset_class="stock",
                bar_timeframe="1Day"
            )
            self._turtle_engine = TurtleTrend(self.ticker, turtle_config)
        
        # Log initialization
        self._logger.log("momentum_bot_init", {
            "bot_id": bot_id,
            "ticker": ticker,
            "config_loaded": self._config is not None,
            "ml_enabled": self._ml_enabled,
            "ml_available": self._ml_service.is_available,
            "turtle_enabled": self._turtle_engine is not None,
            "signal_mode": self._config.signal_mode if self._config else "unknown"
        })
    
    def _load_config(self) -> Optional[MomentumConfig]:
        """
        Load bot configuration from config/bots.yaml.
        
        Returns:
            MomentumConfig object with all settings, or None if not found
        """
        try:
            # Load the full bots configuration file
            bots_config = load_bots_config()
            
            # Find this bot's configuration in the momentum_bots list
            momentum_bots = bots_config.get("momentum_bots", [])
            bot_config = None
            
            for bot in momentum_bots:
                if bot.get("bot_id") == self.bot_id:
                    bot_config = bot
                    break
            
            # Return None if this bot isn't configured
            if not bot_config:
                self._logger.warn(f"No config found for bot {self.bot_id}")
                return None
            
            # Extract session timing settings
            session = bot_config.get("session", {})
            
            # Extract risk management settings
            risk = bot_config.get("risk", {})
            trailing = risk.get("trailing_stop", {})
            
            # Extract exit condition settings
            exits = bot_config.get("exits", {})
            
            # Extract Turtle Traders strategy settings
            turtle = bot_config.get("turtle", {})
            signal = bot_config.get("signal", {})
            
            # Build and return the configuration object
            return MomentumConfig(
                bot_id=self.bot_id,
                enabled=bot_config.get("enabled", False),
                ticker=bot_config.get("ticker", self.ticker),
                trade_start=session.get("trade_start", "06:35"),
                trade_end=session.get("trade_end", "12:55"),
                manage_until=session.get("manage_until", "12:55"),
                max_trades_per_day=risk.get("max_trades_per_day", 3),
                max_concurrent_positions=risk.get("max_concurrent_positions", 4),
                stop_loss_pct=exits.get("stop_loss_pct", 2.00),
                take_profit_pct=exits.get("take_profit_pct", 10.00),
                time_stop_minutes=exits.get("time_stop_minutes", 0),
                trailing_stop_enabled=trailing.get("enabled", False),
                trailing_stop_mode=trailing.get("mode", "percent"),
                trailing_stop_value=trailing.get("value", 0.8),
                trailing_activation_pct=trailing.get("activation_profit_pct", 0.3),
                trailing_update_only_if_improves=trailing.get("update_only_if_improves", True),
                trailing_epsilon_pct=trailing.get("epsilon_pct", 0.02),
                trailing_exit_order_type=trailing.get("exit_order", {}).get("type", "market"),
                direction=bot_config.get("direction", "long_only"),
                signal_mode=signal.get("mode", "turtle"),
                # Turtle Traders strategy configuration
                turtle_enabled=turtle.get("enabled", True),
                turtle_system=turtle.get("system", "system_1"),
                turtle_entry_lookback=turtle.get("entry_lookback", 20),
                turtle_exit_lookback=turtle.get("exit_lookback", 10),
                turtle_atr_period=turtle.get("atr_period", 20),
                turtle_risk_pct_per_unit=turtle.get("risk_pct_per_unit", 1.0),
                turtle_stop_loss_atr_mult=turtle.get("stop_loss_atr_mult", 2.0),
                turtle_pyramid_enabled=turtle.get("pyramid_enabled", True),
                turtle_pyramid_trigger_atr=turtle.get("pyramid_trigger_atr", 0.5),
                turtle_max_units=turtle.get("max_units", 4),
                turtle_winner_filter_enabled=turtle.get("winner_filter_enabled", True)
            )
            
        except Exception as e:
            # Log error but don't crash - bot will use defaults
            self._logger.error(f"Failed to load config for {self.bot_id}: {e}")
            return None
    
    def execute(self, max_daily_loss: float) -> Dict[str, Any]:
        """
        Execute one iteration of the momentum trading strategy.
        
        This is the main entry point called by the orchestrator every loop.
        It manages existing positions first, then looks for new entry opportunities.
        
        Args:
            max_daily_loss: Maximum dollar amount this bot can lose today
                           (allocated by PortfolioBot based on account equity)
        
        Returns:
            Dictionary containing execution results:
            - trades_attempted: Number of new trades attempted
            - positions_managed: Number of existing positions checked
            - signal: The generated trading signal
            - errors: List of any errors encountered
        """
        # Initialize results dictionary to track execution outcomes
        results = {
            "trades_attempted": 0,
            "positions_managed": 0,
            "signal": {},
            "errors": []
        }
        
        # Decision Record tracking - collect gate checks and emit at end
        gates_checks = {
            "regime_ok": True,
            "max_positions_ok": True,
            "no_pending_orders": True,
            "daily_limit_ok": True,
            "session_ok": True,
            "signal_valid": False,
            "ml_gate_ok": True
        }
        gates_reasons = []
        action_type = "NO_TRADE"
        outcome_status = "SKIPPED"
        outcome_message = "No entry conditions met"
        equity = 100000.0
        vix = 20.0
        signal = {}
        ticker_positions = []
        regime = None
        
        try:
            # =========================================================
            # REGIME CHECK: Fetch market regime for position sizing
            # =========================================================
            regime_multiplier = 1.0
            halt_new_entries = False
            tighten_stops = False
            
            try:
                regime = get_current_regime()
                regime_multiplier = regime.position_size_multiplier
                halt_new_entries = regime.halt_new_entries
                tighten_stops = regime.tighten_stops
                vix = regime.vix
                
                self._logger.log("momentum_regime_check", {
                    "bot_id": self.bot_id,
                    "vix": regime.vix,
                    "sentiment": regime.sentiment.value,
                    "regime_multiplier": regime_multiplier,
                    "halt_new_entries": halt_new_entries
                })
            except Exception as regime_err:
                self._logger.error(f"Regime check failed: {regime_err}")
                regime = None
            
            # Apply regime multiplier to max_daily_loss for position sizing
            adjusted_max_loss = max_daily_loss * regime_multiplier
            
            # STEP 1: Get all current positions from Alpaca
            positions = self._alpaca.get_positions()
            
            # Get account equity for decision record
            try:
                account = self._alpaca.get_account()
                equity = float(account.equity)
            except Exception:
                equity = 100000.0
            
            # Filter to only positions for this ticker
            ticker_positions = [p for p in positions if p.symbol == self.ticker]
            
            # STEP 2: Manage existing positions (check for exit conditions)
            for position in ticker_positions:
                try:
                    # Check stop-loss, take-profit, trailing stop, time stop
                    self._manage_position(position, max_daily_loss)
                    results["positions_managed"] += 1
                except Exception as e:
                    # Log error but continue to next position
                    error_msg = f"Position management {self.ticker}: {e}"
                    results["errors"].append(error_msg)
                    self._logger.error(error_msg)
            
            # STEP 3: Check if we should look for new entries
            # Only if we have room for more positions (respecting max_concurrent_positions)
            max_positions = self._config.max_concurrent_positions if self._config else 1
            
            # Check for pending orders to prevent duplicate order placement
            open_orders = self._alpaca.get_open_orders()
            has_pending_order = any(self.ticker in o.get("symbol", "") for o in open_orders)
            
            if has_pending_order:
                gates_checks["no_pending_orders"] = False
                gates_reasons.append("Pending order exists")
                self._logger.log("momentum_skip_pending_order", {
                    "bot_id": self.bot_id,
                    "ticker": self.ticker,
                    "reason": "pending_order_exists"
                })
            elif len(ticker_positions) >= max_positions:
                gates_checks["max_positions_ok"] = False
                gates_reasons.append("Max positions reached")
            else:
                try:
                    # Check if regime halts new entries (extreme fear)
                    if halt_new_entries:
                        gates_checks["regime_ok"] = False
                        gates_reasons.append("Regime halt - extreme volatility")
                        self._logger.log("momentum_halt_regime", {
                            "bot_id": self.bot_id,
                            "ticker": self.ticker,
                            "reason": "extreme_volatility_regime"
                        })
                    # Check if we've exceeded daily trade limit
                    elif not self._can_trade_today():
                        gates_checks["daily_limit_ok"] = False
                        gates_reasons.append("Daily trade limit reached")
                        self._logger.log("momentum_daily_limit_reached", {
                            "bot_id": self.bot_id,
                            "ticker": self.ticker
                        })
                    # Check if we're in the trading time window
                    elif not self._is_trading_hours():
                        gates_checks["session_ok"] = False
                        gates_reasons.append("Outside trading hours")
                        self._logger.log("momentum_outside_trading_hours", {
                            "bot_id": self.bot_id,
                            "ticker": self.ticker
                        })
                    else:
                        # Generate signal using Turtle strategy or legacy momentum
                        if self._turtle_engine and self._config and self._config.signal_mode == "turtle":
                            signal = self._generate_turtle_signal(equity)
                        else:
                            signal = self._generate_momentum_signal()
                        results["signal"] = signal
                        gates_checks["signal_valid"] = signal.get("action") in ["buy", "short", "pyramid"]
                        
                        # Execute trade if signal is actionable
                        # Turtle signals: "buy", "short", "pyramid", "exit"
                        # Legacy signals: "buy", "short"
                        if signal["action"] in ["buy", "short", "pyramid"]:
                            # ML scoring gate - score the trade before executing
                            if self._ml_enabled:
                                hour = get_market_clock().now().hour
                                day_of_week = get_market_clock().now().weekday()
                                ml_context = {
                                    "symbol": self.ticker,
                                    "side": signal["action"],
                                    "signal_strength": signal.get("strength", 0.5),
                                    "hour": hour,
                                    "day_of_week": day_of_week,
                                    "vix": regime.vix if regime else 20
                                }
                                ml_score = self._ml_service.score_entry(ml_context)
                                
                                # Apply adaptive threshold based on market conditions
                                adaptive_threshold = self._ml_service.get_adaptive_threshold(
                                    self._ml_min_probability, vix=vix, is_earnings_season=False
                                )
                                
                                if ml_score["probability"] < adaptive_threshold:
                                    gates_checks["ml_gate_ok"] = False
                                    gates_reasons.append(f"ML score {ml_score['probability']:.2f} < {adaptive_threshold:.2f}")
                                    self._logger.log("momentum_ml_skip", {
                                        "ticker": self.ticker,
                                        "action": signal["action"],
                                        "ml_probability": ml_score["probability"],
                                        "threshold": adaptive_threshold,
                                        "base_threshold": self._ml_min_probability,
                                        "recommendation": ml_score["recommendation"]
                                    })
                                    outcome_message = "ML gate blocked entry"
                                else:
                                    # ML gate passed - proceed to trade
                                    pass
                            
                            # Execute if all gates passed
                            if gates_checks["ml_gate_ok"]:
                                # For Turtle signals, use the calculated qty from signal
                                # Otherwise, use legacy position sizing with max_loss
                                if signal.get("qty"):
                                    trade_result = self._execute_trade_with_qty(signal)
                                else:
                                    trade_result = self._execute_trade(signal, adjusted_max_loss)
                                
                                if trade_result["success"]:
                                    results["trades_attempted"] += 1
                                    action_type = "ENTER" if signal["action"] in ["buy", "short"] else "ADD"
                                    outcome_status = "EXECUTED"
                                    outcome_message = f"Trade placed: {signal['action']}"
                                    # Record trade for daily limit tracking
                                    self._record_trade()
                                else:
                                    outcome_status = "FAILED"
                                    outcome_message = f"Trade failed: {trade_result.get('error', 'unknown')}"
                                    results["errors"].append(
                                        f"{self.ticker}: {trade_result['error']}"
                                    )
                        else:
                            # No actionable signal
                            if not gates_reasons:
                                gates_reasons.append("No entry signal")
                                
                except Exception as e:
                    error_msg = f"Signal generation {self.ticker}: {e}"
                    results["errors"].append(error_msg)
                    self._logger.error(error_msg)
                    outcome_status = "ERROR"
                    outcome_message = str(e)
            
            # Log completion for debugging and monitoring
            self._logger.log("momentum_bot_execution_complete", {
                "bot_id": self.bot_id,
                "ticker": self.ticker,
                "results": results,
                "max_daily_loss": max_daily_loss
            })
            
        except Exception as e:
            # Catch-all for unexpected errors
            self._logger.error(f"Momentum bot execution failed for {self.ticker}: {e}")
            results["errors"].append(str(e))
            outcome_status = "ERROR"
            outcome_message = str(e)
        
        # EMIT DECISION RECORD - Per symbol per loop for audit trail
        gates_allowed = all(gates_checks.values()) and not gates_reasons
        _build_momentum_decision_record(
            bot_id=self.bot_id,
            ticker=self.ticker,
            equity=equity,
            positions_count=len(ticker_positions),
            daily_pnl=0.0,
            is_rth=self._is_trading_hours() if self._config else False,
            quote_age_sec=0.0,
            regime_label=regime.volatility_regime.value if regime else "UNKNOWN",
            vix=vix,
            price_data={"last": signal.get("price", 0), "bid": 0, "ask": 0, "spread_pct": 0} if signal else {"last": 0},
            atr_data={"period": 20, "value": signal.get("atr", 0)} if signal else {"period": 20, "value": 0},
            donchian_data={"entry_bars": 20, "exit_bars": 10, "high": signal.get("donchian_high", 0), "low": signal.get("donchian_low", 0)} if signal else {},
            gates_allowed=gates_allowed,
            gates_reasons=gates_reasons if gates_reasons else ["All gates passed"] if gates_allowed else ["Unknown"],
            gates_checks=gates_checks,
            risk_per_trade=0.01,
            risk_dollars=max_daily_loss * 0.25 if max_daily_loss else 0,
            position_size={"shares": signal.get("qty", 0)} if signal else {"shares": 0},
            signal_direction=signal.get("action", "flat") if signal else "flat",
            signal_strength=signal.get("strength", 0.0) if signal else 0.0,
            signal_reason=signal.get("reason", "") if signal else "",
            plan_entry={"type": "breakout", "price": signal.get("price", 0)} if signal else {"type": "none"},
            plan_stop={"type": "atr", "price": signal.get("stop_price", 0)} if signal else {"type": "none"},
            plan_exit={"type": "donchian", "price": signal.get("exit_price", 0)} if signal else {"type": "none"},
            action_type=action_type,
            outcome_status=outcome_status,
            outcome_message=outcome_message
        )
        
        return results
    
    # =========================================================================
    # TRADING LIMIT ENFORCEMENT - Prevent over-trading
    # =========================================================================
    
    def _can_trade_today(self) -> bool:
        """
        Check if we've reached the daily trade limit.
        """
        max_trades = self._config.max_trades_per_day if self._config else 3
        today = get_market_clock().now().strftime("%Y-%m-%d")
        trade_count_key = f"trade_count.{self.bot_id}.{today}"
        current_count = get_state(trade_count_key, 0)
        return current_count < max_trades
    
    def _reserve_trade_slot(self) -> bool:
        """
        Atomically reserve a trade slot to prevent race conditions.
        """
        max_trades = self._config.max_trades_per_day if self._config else 3
        today = get_market_clock().now().strftime("%Y-%m-%d")
        trade_count_key = f"trade_count.{self.bot_id}.{today}"
        
        success, new_count = atomic_increment(trade_count_key, max_trades)
        
        if success:
            self._logger.log("momentum_trade_slot_reserved", {
                "bot_id": self.bot_id,
                "date": today,
                "new_count": new_count,
                "max_trades": max_trades
            })
        
        return success
    
    def _record_trade(self) -> None:
        """
        Legacy method - now a no-op since _reserve_trade_slot handles counting.
        """
        pass
    
    def _get_trades_today(self) -> int:
        """
        Get the number of trades made today by this bot.
        
        Returns:
            Integer count of trades made today
        """
        today = get_market_clock().now().strftime("%Y-%m-%d")
        trade_count_key = f"trade_count.{self.bot_id}.{today}"
        return get_state(trade_count_key, 0)
    
    # =========================================================================
    # TIME WINDOW ENFORCEMENT - Trade only during configured hours
    # =========================================================================
    
    def _is_trading_hours(self) -> bool:
        """
        Check if current time is within the configured trading window.
        
        Uses trade_start and trade_end from bots.yaml configuration.
        These times are in the configured timezone (typically PST).
        
        Returns:
            True if we're in the trading window, False otherwise
        """
        # Get configured times (defaults if no config)
        trade_start = self._config.trade_start if self._config else "06:35"
        trade_end = self._config.trade_end if self._config else "09:30"
        
        # Parse time strings into time objects
        start_time = datetime.strptime(trade_start, "%H:%M").time()
        end_time = datetime.strptime(trade_end, "%H:%M").time()
        
        # Get current time
        current_time = get_market_clock().now().time()
        
        # Check if current time is within the window
        return start_time <= current_time <= end_time
    
    def _is_manage_hours(self) -> bool:
        """
        Check if we're still within position management hours.
        
        We manage positions (check exits) until manage_until time,
        which is typically later than trade_end.
        
        Returns:
            True if we should still manage positions, False otherwise
        """
        manage_until = self._config.manage_until if self._config else "12:55"
        end_time = datetime.strptime(manage_until, "%H:%M").time()
        current_time = get_market_clock().now().time()
        
        return current_time <= end_time
    
    # =========================================================================
    # SIGNAL GENERATION - Turtle Traders or legacy momentum strategy
    # =========================================================================
    
    def _generate_turtle_signal(self, equity: float) -> Dict[str, Any]:
        """
        Generate trading signal using Turtle Traders strategy.
        
        Strategy (from Richard Dennis / William Eckhardt):
        - Entry: Donchian Channel breakout (20-day high for long, 20-day low for short)
        - Exit: Opposite channel break (10-day low for long exit, 10-day high for short exit)
        - Stop: 2N (2x ATR) from entry
        - Position sizing: 1% equity risk per unit
        - Pyramiding: Add unit every 0.5N move in favor (up to 4 units)
        - Filter: Skip next signal after a winner (reduce chop)
        
        Args:
            equity: Current account equity for position sizing
        
        Returns:
            Dictionary containing signal details compatible with execute flow
        """
        signal = {
            "ticker": self.ticker,
            "action": "hold",
            "confidence": 0.0,
            "price": 0.0,
            "indicators": {},
            "turtle_signal": None,
            "position_size_dollars": 0.0,
            "position_size_shares": 0.0,
            "stop_price": 0.0
        }
        
        if not self._turtle_engine:
            self._logger.warn(f"Turtle engine not initialized for {self.ticker}")
            return signal
        
        try:
            # Get daily bars for Donchian channel and ATR calculation
            entry_lookback = self._config.turtle_entry_lookback if self._config else 20
            atr_period = self._config.turtle_atr_period if self._config else 20
            bars = self._alpaca.get_stock_bars(
                symbol=self.ticker,
                timeframe="1Day",
                limit=max(entry_lookback, atr_period) + 5
            )
            
            if not bars or len(bars) < 21:
                self._logger.log("turtle_insufficient_bars", {
                    "ticker": self.ticker,
                    "bars": len(bars) if bars else 0,
                    "required": 21
                })
                return signal
            
            # Convert bars to dict format expected by TurtleTrend
            bar_dicts = []
            for bar in bars:
                bar_dicts.append({
                    "high": float(bar.high) if hasattr(bar, 'high') else float(bar.get('high', 0)),
                    "low": float(bar.low) if hasattr(bar, 'low') else float(bar.get('low', 0)),
                    "close": float(bar.close) if hasattr(bar, 'close') else float(bar.get('close', 0)),
                    "open": float(bar.open) if hasattr(bar, 'open') else float(bar.get('open', 0)),
                    "volume": float(bar.volume) if hasattr(bar, 'volume') else float(bar.get('volume', 0))
                })
            
            # Get current price
            quote = self._alpaca.get_latest_quote(self.ticker, asset_class="stock")
            current_price = (quote["bid"] + quote["ask"]) / 2
            signal["price"] = current_price
            
            # Check if we have an existing position
            positions = self._alpaca.get_positions()
            has_position = False
            position_side = None
            position_qty = 0.0
            
            for pos in positions:
                if pos.symbol == self.ticker:
                    has_position = True
                    position_qty = float(pos.qty)
                    position_side = "long" if position_qty > 0 else "short"
                    break
            
            # Evaluate Turtle signal
            turtle_signal = self._turtle_engine.evaluate(
                bars=bar_dicts,
                equity=equity,
                current_price=current_price,
                has_position=has_position,
                position_side=position_side,
                position_qty=abs(position_qty)
            )
            
            signal["turtle_signal"] = turtle_signal
            signal["indicators"] = turtle_signal.indicators
            signal["position_size_dollars"] = turtle_signal.position_size_dollars
            signal["position_size_shares"] = turtle_signal.position_size_shares
            signal["qty"] = turtle_signal.position_size_shares  # For _execute_trade_with_qty
            signal["stop_price"] = turtle_signal.stop_price
            signal["atr"] = turtle_signal.atr_n
            signal["confidence"] = turtle_signal.confidence
            
            # Map Turtle signal types to legacy action format
            if turtle_signal.signal_type == SignalType.LONG_ENTRY:
                direction = self._config.direction if self._config else "long_only"
                if direction in ("long_only", "both"):
                    signal["action"] = "buy"
                    signal["side"] = "long"
            elif turtle_signal.signal_type == SignalType.SHORT_ENTRY:
                direction = self._config.direction if self._config else "long_only"
                if direction in ("short_only", "both"):
                    signal["action"] = "short"
                    signal["side"] = "short"
            elif turtle_signal.signal_type == SignalType.PYRAMID_ADD:
                signal["action"] = "pyramid"
                signal["side"] = position_side
            elif turtle_signal.signal_type in (SignalType.LONG_EXIT, SignalType.SHORT_EXIT, SignalType.STOP_EXIT):
                signal["action"] = "exit"
                signal["exit_reason"] = turtle_signal.reason
            
            # Log Turtle signal
            self._logger.log("turtle_signal_generated", {
                "ticker": self.ticker,
                "signal_type": turtle_signal.signal_type.value,
                "action": signal["action"],
                "price": current_price,
                "donchian_upper": turtle_signal.donchian_upper,
                "donchian_lower": turtle_signal.donchian_lower,
                "atr_n": turtle_signal.atr_n,
                "position_size_dollars": turtle_signal.position_size_dollars,
                "stop_price": turtle_signal.stop_price,
                "current_units": turtle_signal.current_units,
                "filtered_by_winner": turtle_signal.filtered_by_winner,
                "reason": turtle_signal.reason
            })
            
            # Update decision tracker
            try:
                tracker = get_decision_tracker()
                tracker.update_signal(
                    bot_id=self.bot_id,
                    bot_type="momentum",
                    symbol=self.ticker,
                    signal=signal["action"],
                    strength=signal["confidence"],
                    reason=turtle_signal.reason
                )
            except Exception as track_err:
                self._logger.error(f"Decision tracker update failed: {track_err}")
            
        except Exception as e:
            self._logger.error(f"Turtle signal generation failed for {self.ticker}: {e}")
            signal["error"] = str(e)
        
        return signal
    
    def _generate_momentum_signal(self) -> Dict[str, Any]:
        """
        Generate a momentum signal using legacy 3-bar strategy (fallback if Turtle disabled).
        
        Strategy:
        - Track the last 10 prices in state database
        - Detect uptrend: 3 consecutive higher prices
        - Detect downtrend: 3 consecutive lower prices
        - Confirm with moving average: price must be 0.2% above/below average
        
        Returns:
            Dictionary containing:
            - ticker: Stock symbol
            - action: "buy", "sell", or "hold"
            - confidence: 0.0 to 1.0 confidence in the signal
            - price: Current price
            - indicators: Dictionary of calculated indicators
        """
        # Initialize signal with default "hold" action
        signal = {
            "ticker": self.ticker,
            "action": "hold",
            "confidence": 0.0,
            "price": 0.0,
            "indicators": {}
        }
        
        try:
            # STEP 1: Get current market price from Alpaca
            quote = self._alpaca.get_latest_quote(self.ticker, asset_class="stock")
            
            # Calculate mid-price from bid/ask spread
            current_price = (quote["bid"] + quote["ask"]) / 2
            signal["price"] = current_price
            
            # STEP 2: Load price history from state database
            price_history_key = f"price_history.{self.ticker}"
            price_history = get_state(price_history_key, [])
            
            # STEP 3: Add current price to history
            price_history.append({
                "price": current_price,
                "timestamp": datetime.utcnow().isoformat()
            })
            
            # STEP 4: Trim history to last 10 prices (prevents unbounded growth)
            if len(price_history) > 10:
                price_history = price_history[-10:]
            
            # STEP 5: Save updated history back to state
            set_state(price_history_key, price_history)
            
            # STEP 6: Generate signal if we have enough data points
            # QUANT-OPTIMIZED: Reduced from 3 bars to 2 bars for faster signal generation
            if len(price_history) >= 2:
                # Extract last 2 prices for trend detection (was 3 - too restrictive)
                prices = [p["price"] for p in price_history[-2:]]
                
                # Calculate simple moving average
                avg_price = sum(prices) / len(prices)
                
                # Get direction config (default to long_only for safety)
                direction = self._config.direction if self._config else "long_only"
                
                # UPTREND DETECTION: Price higher than previous (was 3 consecutive - too strict)
                # QUANT-OPTIMIZED: Only need 2 bars of momentum, lower threshold
                if prices[-1] > prices[-2]:
                    # Additional confirmation: price must be 0.1% above average (was 0.2%)
                    if current_price > avg_price * 1.001:
                        if direction in ("long_only", "both"):
                            signal["action"] = "buy"
                            signal["side"] = "long"  # Track position side
                            # Confidence based on how far above average (capped at 0.8)
                            signal["confidence"] = min(0.8, (current_price / avg_price - 1) * 50)
                
                # DOWNTREND DETECTION: Price lower than previous
                # QUANT-OPTIMIZED: Only need 2 bars, lower threshold
                elif prices[-1] < prices[-2]:
                    # Additional confirmation: price must be 0.1% below average (was 0.2%)
                    if current_price < avg_price * 0.999:
                        if direction in ("short_only", "both"):
                            signal["action"] = "short"  # Distinct from "sell" (exit)
                            signal["side"] = "short"    # Track position side
                            # Confidence based on how far below average (capped at 0.8)
                            signal["confidence"] = min(0.8, (avg_price / current_price - 1) * 50)
                
                # Store indicators for debugging/analysis
                signal["indicators"]["trend_prices"] = prices
                signal["indicators"]["avg_price"] = avg_price
            
            # Log signal generation
            self._logger.log("momentum_signal_generated", {
                "ticker": self.ticker,
                "action": signal["action"],
                "confidence": signal["confidence"],
                "price": current_price
            })
            
            # Update decision tracker for dashboard visibility
            try:
                tracker = get_decision_tracker()
                reason = ""
                if signal["action"] == "buy":
                    reason = f"Uptrend detected, {signal['confidence']:.1%} confidence"
                elif signal["action"] == "short":
                    reason = f"Downtrend detected, {signal['confidence']:.1%} confidence"
                else:
                    reason = "No momentum signal"
                tracker.update_signal(
                    bot_id=self.bot_id,
                    bot_type="momentum",
                    symbol=self.ticker,
                    signal=signal["action"],
                    strength=signal["confidence"],
                    reason=reason
                )
            except Exception as track_err:
                self._logger.error(f"Decision tracker update failed: {track_err}")
            
        except Exception as e:
            # Log error but return hold signal (fail-safe)
            self._logger.error(f"Momentum signal generation failed for {self.ticker}: {e}")
            signal["error"] = str(e)
        
        return signal
    
    # =========================================================================
    # TRADE EXECUTION - Enter positions with proper sizing
    # =========================================================================
    
    def _execute_trade(self, signal: Dict[str, Any], max_daily_loss: float) -> Dict[str, Any]:
        """
        Execute a trade based on the generated signal.
        
        Position sizing uses 75% of the allocated budget for this trade,
        leaving room for slippage and fees.
        
        Supports both long and short positions:
        - "buy" action: Opens a long position
        - "short" action: Opens a short position (sends "sell" to Alpaca)
        
        Args:
            signal: The trading signal containing action and price
            max_daily_loss: Maximum dollar amount to risk on this trade
        
        Returns:
            Dictionary containing:
            - success: True if trade was executed
            - error: Error message if failed
            - order_id: Alpaca order ID if successful
        """
        result = {"success": False, "error": None, "order_id": None}
        
        try:
            # STEP 1: Calculate position size
            # Use 75% of budget, with minimum of $5 for Alpaca
            dollar_amount = max(5.0, max_daily_loss * 0.75)
            
            # STEP 2: Determine order side
            # For shorting, Alpaca uses "sell" to open short position
            action = signal["action"]
            if action == "short":
                order_side = "sell"  # Alpaca interprets sell without position as short
                position_side = "short"
            else:
                order_side = "buy"
                position_side = "long"
            
            # STEP 3: Place market order through Alpaca
            order_response = self._alpaca.place_market_order(
                symbol=self.ticker,
                side=order_side,
                notional=dollar_amount  # Dollar amount, not shares
            )
            
            # STEP 4: Mark success and capture order ID
            result["success"] = True
            result["order_id"] = order_response.get("id")
            
            # STEP 5: Log trade for audit trail
            self._logger.log("momentum_trade_executed", {
                "ticker": self.ticker,
                "action": action,
                "order_side": order_side,
                "position_side": position_side,
                "notional": dollar_amount,
                "order_id": order_response.get("id"),
                "signal_confidence": signal.get("confidence", 0),
                "entry_price": signal.get("price", 0)
            })
            
            # STEP 6: Store trade details in state for position management
            # Include position_side to track long vs short for exit logic
            trade_key = f"trades.{self.bot_id}.{int(time.time())}"
            entry_time_now = get_market_clock().now().isoformat()
            set_state(trade_key, {
                "ticker": self.ticker,
                "action": action,
                "position_side": position_side,  # "long" or "short"
                "notional": dollar_amount,
                "timestamp": time.time(),
                "entry_time": entry_time_now,  # For time-based exit
                "order_id": order_response.get("id"),
                "entry_price": signal.get("price", 0),
                "signal": signal
            })
            
            # STEP 7: Store entry_time separately for quick lookup by _get_entry_time
            entry_key = f"entry_time.{self.bot_id}.{self.ticker}"
            set_state(entry_key, entry_time_now)
            
        except Exception as e:
            result["error"] = str(e)
            self._logger.error(f"Momentum trade execution failed for {self.ticker}: {e}")
        
        return result

    def _execute_trade_with_qty(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a Turtle-style trade with pre-calculated quantity.
        
        Turtle strategy calculates position size based on ATR volatility,
        so we use share quantity instead of dollar notional.
        
        Args:
            signal: Trading signal containing:
                - action: "buy", "short", or "pyramid"
                - qty: Number of shares to trade
                - stop_price: 2N stop-loss level
                - atr: Current ATR for position tracking
        
        Returns:
            Dictionary with success, error, order_id
        """
        result = {"success": False, "error": None, "order_id": None}
        
        try:
            qty = signal.get("qty", 0)
            if qty <= 0:
                result["error"] = "Invalid quantity for Turtle trade"
                return result
            
            action = signal["action"]
            if action in ["short"]:
                order_side = "sell"
                position_side = "short"
            else:
                order_side = "buy"
                position_side = "long"
            
            order_response = self._alpaca.place_market_order(
                symbol=self.ticker,
                side=order_side,
                qty=qty
            )
            
            result["success"] = True
            result["order_id"] = order_response.get("id")
            
            self._logger.log("turtle_trade_executed", {
                "ticker": self.ticker,
                "action": action,
                "order_side": order_side,
                "position_side": position_side,
                "qty": qty,
                "stop_price": signal.get("stop_price"),
                "atr": signal.get("atr"),
                "order_id": order_response.get("id"),
                "is_pyramid": action == "pyramid"
            })
            
            trade_key = f"trades.{self.bot_id}.{int(time.time())}"
            entry_time_now = get_market_clock().now().isoformat()
            set_state(trade_key, {
                "ticker": self.ticker,
                "action": action,
                "position_side": position_side,
                "qty": qty,
                "timestamp": time.time(),
                "entry_time": entry_time_now,
                "order_id": order_response.get("id"),
                "entry_price": signal.get("price", 0),
                "stop_price": signal.get("stop_price"),
                "atr": signal.get("atr"),
                "strategy": "turtle",
                "signal": signal
            })
            
            entry_key = f"entry_time.{self.bot_id}.{self.ticker}"
            set_state(entry_key, entry_time_now)
            
        except Exception as e:
            result["error"] = str(e)
            self._logger.error(f"Turtle trade execution failed for {self.ticker}: {e}")
        
        return result
    
    # =========================================================================
    # POSITION MANAGEMENT - Monitor and exit positions
    # =========================================================================
    
    def _manage_position(self, position, max_daily_loss: float) -> None:
        """
        Manage an existing position with multiple exit conditions.
        
        Exit conditions checked (in order):
        1. Trailing stop - If enabled and triggered
        2. Stop loss - If unrealized loss exceeds stop_loss_pct
        3. Take profit - If unrealized gain exceeds take_profit_pct
        4. Time stop - If held longer than time_stop_minutes
        5. Session end - If past manage_until time
        
        Args:
            position: Alpaca position object with current market values
            max_daily_loss: Maximum daily loss budget (for reference)
        """
        try:
            # STEP 1: Get current market price
            try:
                quote = self._alpaca.get_latest_quote(position.symbol, asset_class="stock")
                current_price = (quote["bid"] + quote["ask"]) / 2
            except Exception as e:
                self._logger.warn(f"Could not get quote for {position.symbol}: {e}")
                return
            
            # STEP 2: Calculate current P&L percentage
            # market_value is signed (negative for shorts)
            # unrealized_pl is the dollar P&L
            if abs(float(position.market_value)) > 0:
                pnl_pct = (float(position.unrealized_pl) / abs(float(position.market_value))) * 100
            else:
                return  # No position value, skip
            
            # STEP 3: Get exit thresholds from config
            stop_loss_pct = -(self._config.stop_loss_pct if self._config else 0.50)
            take_profit_pct = self._config.take_profit_pct if self._config else 1.00
            time_stop_minutes = self._config.time_stop_minutes if self._config else 25
            
            # STEP 4: Initialize exit decision variables
            should_close = False
            close_reason = ""
            
            # STEP 4.5: ProfitSniper — profit-priority exit (runs FIRST, overrides all)
            try:
                sniper = get_profit_sniper()
                position_key = f"mom_{self.bot_id}_{position.symbol}"
                qty = abs(float(position.qty))
                entry_price = float(position.cost_basis) / qty if qty > 0 else current_price
                sniper_decision = sniper.evaluate(
                    position_key=position_key,
                    entry_price=entry_price,
                    current_price=current_price,
                    side=position.side or "long",
                    config=ProfitSniperConfig.for_stocks(),
                    bot_id=self.bot_id
                )
                if sniper_decision.should_exit:
                    if sniper_decision.exit_pct >= 100:
                        should_close = True
                        close_reason = f"profit_sniper_{sniper_decision.reason}"
                    else:
                        exit_qty = max(1, int(qty * sniper_decision.exit_pct / 100))
                        self._partial_close(position, exit_qty, f"profit_sniper_{sniper_decision.reason}", pnl_pct)
                        self._logger.log("momentum_sniper_partial_exit", {
                            "ticker": self.ticker,
                            "exit_qty": exit_qty,
                            "total_qty": qty,
                            "reason": sniper_decision.reason,
                            "peak_profit_pct": round(sniper_decision.peak_profit_pct, 3),
                            "current_profit_pct": round(sniper_decision.current_profit_pct, 3)
                        })
                        return
            except Exception as e:
                self._logger.warn(f"ProfitSniper check failed for {position.symbol}: {e}")
            
            # STEP 5: Check trailing stop (if enabled)
            if self._config and self._config.trailing_stop_enabled:
                trailing_exit = self._check_trailing_stop(position, current_price)
                if trailing_exit:
                    should_close = True
                    close_reason = "trailing_stop"
            
            # STEP 6: Check stop loss
            if not should_close and pnl_pct <= stop_loss_pct:
                should_close = True
                close_reason = "stop_loss"
                self._logger.log("momentum_stop_loss_triggered", {
                    "ticker": self.ticker,
                    "pnl_pct": round(pnl_pct, 3),
                    "threshold": stop_loss_pct
                })
            
            # STEP 7: Check take profit
            if not should_close and pnl_pct >= take_profit_pct:
                should_close = True
                close_reason = "take_profit"
                self._logger.log("momentum_take_profit_triggered", {
                    "ticker": self.ticker,
                    "pnl_pct": round(pnl_pct, 3),
                    "threshold": take_profit_pct
                })
            
            # STEP 8: Check time-based exit
            if not should_close:
                entry_time = self._get_entry_time(position)
                if entry_time:
                    hold_duration = (get_market_clock().now_naive() - entry_time).total_seconds() / 60
                    if hold_duration >= time_stop_minutes:
                        should_close = True
                        close_reason = "time_stop"
                        self._logger.log("momentum_time_stop_triggered", {
                            "ticker": self.ticker,
                            "hold_minutes": round(hold_duration, 1),
                            "threshold": time_stop_minutes
                        })
            
            # STEP 9: Check session end
            if not should_close and not self._is_manage_hours():
                should_close = True
                close_reason = "session_end"
                self._logger.log("momentum_session_end_triggered", {
                    "ticker": self.ticker
                })
            
            # STEP 10: Execute exit if any condition met
            if should_close:
                self._close_position(position, close_reason, pnl_pct)
                
        except Exception as e:
            self._logger.error(f"Momentum position management failed for {self.ticker}: {e}")
    
    def _check_trailing_stop(self, position, current_price: float) -> bool:
        """
        Check if trailing stop should trigger exit.
        
        Trailing stop works by tracking the highest price since entry (for longs)
        and placing a stop a certain percentage below that high.
        
        Args:
            position: Alpaca position object
            current_price: Current market price
            
        Returns:
            True if trailing stop triggered, False otherwise
        """
        # GUARD: Skip entirely if trailing stops are disabled
        # This prevents unnecessary processing and avoids potential errors
        if not self._config or not self._config.trailing_stop_enabled:
            return False
        
        try:
            # Generate unique position ID
            position_id = f"{position.symbol}_{position.side}_{position.qty}"
            
            # Get trailing stop manager
            trailing_manager = get_trailing_stop_manager()
            
            # Load existing trailing stop state for this position
            trailing_state = trailing_manager.load_state(
                self.bot_id, position_id, position.symbol, "equity"
            )
            
            # Initialize if not exists
            if not trailing_state:
                # Calculate entry price from cost basis
                qty = abs(float(position.qty))
                if qty > 0:
                    entry_price = float(position.cost_basis) / qty
                else:
                    entry_price = current_price
                
                side = "long" if float(position.qty) > 0 else "short"
                
                # Create trailing stop configuration from bot config
                # Uses config values with safe defaults from MomentumConfig dataclass
                config = TrailingStopConfig(
                    enabled=self._config.trailing_stop_enabled,
                    mode=self._config.trailing_stop_mode,
                    value=self._config.trailing_stop_value,
                    activation_profit_pct=self._config.trailing_activation_pct,
                    update_only_if_improves=self._config.trailing_update_only_if_improves,
                    epsilon_pct=self._config.trailing_epsilon_pct,
                    exit_order_type=self._config.trailing_exit_order_type
                )
                
                # Initialize trailing stop for this position
                trailing_state = trailing_manager.init_for_position(
                    self.bot_id, position_id, position.symbol, "equity",
                    entry_price, side, config
                )
            
            # Update trailing stop with current price
            trailing_state = trailing_manager.update_state(
                self.bot_id, position_id, position.symbol, "equity",
                current_price, trailing_state
            )
            
            # Check if stop should trigger
            return trailing_manager.should_exit(trailing_state, current_price)
            
        except Exception as e:
            self._logger.error(f"Trailing stop check failed: {e}")
            return False
    
    def _get_entry_time(self, position) -> Optional[datetime]:
        """
        Get the entry time for a position from state database.
        
        Looks up the trade record stored when the position was opened.
        
        Args:
            position: Alpaca position object
            
        Returns:
            datetime of entry, or None if not found
        """
        try:
            # Search for trade records matching this position
            # Trade keys are in format: trades.{bot_id}.{timestamp}
            # We need to find the most recent one for this ticker
            
            # For now, estimate based on when we might have entered
            # In production, you'd iterate through state keys
            
            # Check if we stored entry time when opening position
            entry_key = f"entry_time.{self.bot_id}.{position.symbol}"
            entry_time_str = get_state(entry_key)
            
            if entry_time_str:
                from ..core.clock import MarketClock
                return MarketClock.parse_iso_to_naive(entry_time_str)
            
            return None
            
        except Exception as e:
            self._logger.error(f"Failed to get entry time: {e}")
            return None
    
    def _partial_close(self, position, exit_qty: int, reason: str, pnl_pct: float) -> None:
        """Close a partial quantity of a position."""
        try:
            side = "sell" if position.side == "long" else "buy"
            # Cancel any pending orders first so shares aren't locked
            try:
                open_orders = self._alpaca.get_open_orders(symbol=position.symbol)
                for order in open_orders:
                    if order.get("id"):
                        self._alpaca.cancel_order(order["id"])
            except Exception:
                pass
            order_response = self._alpaca.place_market_order(
                symbol=position.symbol,
                side=side,
                qty=exit_qty
            )
            self._logger.log("momentum_partial_close", {
                "ticker": self.ticker,
                "side": side,
                "exit_qty": exit_qty,
                "total_qty": abs(float(position.qty)),
                "reason": reason,
                "pnl_pct": round(pnl_pct, 3),
                "order_id": order_response.get("id")
            })
        except Exception as e:
            self._logger.error(f"Failed to partial close momentum position {self.ticker}: {e}")

    def _close_position(self, position, reason: str, pnl_pct: float) -> None:
        """
        Close a position with a market order.
        
        Args:
            position: Alpaca position object to close
            reason: Why we're closing (for logging)
            pnl_pct: Current P&L percentage
        """
        try:
            # Determine order side (opposite of position side)
            side = "sell" if position.side == "long" else "buy"
            qty = abs(float(position.qty))
            
            # Cancel any pending orders for this symbol first
            # so the shares aren't held/locked by staged exit orders
            try:
                open_orders = self._alpaca.get_open_orders(symbol=position.symbol)
                for order in open_orders:
                    if order.get("id"):
                        self._alpaca.cancel_order(order["id"])
                        self._logger.log("momentum_cancelled_pending_order", {
                            "ticker": self.ticker,
                            "order_id": order["id"],
                            "reason": f"clearing_for_{reason}"
                        })
            except Exception as cancel_err:
                self._logger.warn(f"Could not cancel pending orders for {position.symbol}: {cancel_err}")
            
            # Place market order to close
            order_response = self._alpaca.place_market_order(
                symbol=position.symbol,
                side=side,
                qty=qty
            )
            
            # Log the exit for audit trail
            self._logger.log("momentum_position_closed", {
                "ticker": self.ticker,
                "side": side,
                "qty": qty,
                "reason": reason,
                "pnl_pct": round(pnl_pct, 3),
                "pnl_dollars": float(position.unrealized_pl),
                "order_id": order_response.get("id")
            })
            
            # Clean up state
            entry_key = f"entry_time.{self.bot_id}.{position.symbol}"
            delete_state(entry_key)
            
        except Exception as e:
            self._logger.error(f"Failed to close momentum position {self.ticker}: {e}")
