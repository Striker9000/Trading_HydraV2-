"""Trading loop orchestrator with 5-step execution and fail-closed safety"""
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime

from .core.logging import get_logger
from .core.config import (
    load_settings, load_bots_config, auto_detect_account_mode, is_small_account_mode,
    SMALL_ACCOUNT_THRESHOLD, MICRO_ACCOUNT_THRESHOLD, get_account_mode, get_account_mode_params,
    dump_effective_config, run_config_doctor, get_run_id
)
from .core.state import (
    init_state_store, get_state, set_state, delete_state, get_all_state
)
from .core.clock import get_market_clock
from .core.health import get_health_monitor
from .core.halt import get_halt_manager
from .core.console import LoopDisplayData, TickerSignal, PositionInfo, OrderEvent, BlockedSignalEvent, HaltEvent
from .core.positions_display import PositionState, create_position_state_from_trade
from .services.alpaca_client import get_alpaca_client
from .services.exitbot import get_exitbot
from .services.portfolio import get_portfoliobot
from .services.execution import get_execution_service
from .services.stock_screener import get_stock_screener
from .services.options_screener import get_options_screener
from .services.market_regime import get_current_regime
from .ml.account_analytics import get_account_analytics_service, AccountAnalytics
from .ml.performance_analytics import get_performance_analytics
from .ml.models.regime_sizer import RegimeSizer
from .ml.trade_outcome_tracker import get_trade_tracker
from .ml.performance_metrics import get_performance_tracker
from .risk.position_sizer import get_position_sizer, compute_growth_multiplier, get_regime_size_multiplier
from .risk.correlation_manager import get_correlation_manager
from .risk.killswitch import get_killswitch_service
from .risk.risk_integration import get_risk_integration, RiskAction
from .risk.cross_bot_monitor import get_cross_bot_monitor
from .risk.edge_decay_monitor import get_edge_decay_monitor
from .risk.slippage_tracker import get_slippage_tracker
from .ml.drift_detector import get_drift_detector
from .services.system_state import SystemState
from .services.parameter_resolver import get_parameter_resolver
from .services.market_regime import classify_vix_regime, get_simple_regime_info
from .services.earnings_calendar import is_in_earnings_window, earnings_window_days


@dataclass
class LoopResult:
    success: bool
    status: str
    summary: str
    timestamp: str
    display_data: Optional[LoopDisplayData] = None


class TradingOrchestrator:
    def __init__(self):
        self._logger = get_logger()
        self._clock = get_market_clock()
        self._health = get_health_monitor()
        self._halt = get_halt_manager()
        self._alpaca = get_alpaca_client()
        self._exitbot = get_exitbot()
        self._portfoliobot = get_portfoliobot()
        self._execution = get_execution_service()
        self._stock_screener = get_stock_screener()
        self._options_screener = get_options_screener()
        self._account_analytics = None
        self._performance_analytics = None
        self._position_sizer = None
        self._correlation_manager = None
        self._regime_sizer = None
        self._trade_tracker = None
        self._performance_tracker = None
        self._risk_integration = None
        self._cross_bot_monitor = None
        self._edge_decay_monitor = None
        self._slippage_tracker = None
        self._drift_detector = None
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return

        self._logger.log("orchestrator_init", {})
        init_state_store()
        
        # Get unique run_id for this process session (Decision Record tracking)
        run_id = get_run_id()
        set_state("run_id", run_id)
        set_state("loop_id", 0)
        self._logger.log("run_id_generated", {"run_id": run_id})
        
        settings = load_settings()
        analytics_enabled = settings.get("account_analytics", {}).get("enabled", True)
        self._account_analytics = get_account_analytics_service(enabled=analytics_enabled)
        
        self._performance_analytics = get_performance_analytics()
        self._position_sizer = get_position_sizer()
        self._correlation_manager = get_correlation_manager()
        self._regime_sizer = RegimeSizer()
        self._trade_tracker = get_trade_tracker()
        self._performance_tracker = get_performance_tracker()
        self._risk_integration = get_risk_integration()
        
        # Initialize new limitation-fix modules
        self._cross_bot_monitor = get_cross_bot_monitor()
        self._edge_decay_monitor = get_edge_decay_monitor()
        self._slippage_tracker = get_slippage_tracker()
        self._drift_detector = get_drift_detector()
        
        set_state("institutional.position_sizer_enabled", True)
        set_state("institutional.correlation_manager_enabled", True)
        set_state("institutional.regime_throttling_enabled", True)
        set_state("institutional.risk_integration_enabled", True)
        
        self._initialized = True
        
        # Dump effective config at startup (the "cheat code" for debugging)
        try:
            config_path = dump_effective_config(print_summary=True)
            self._logger.log("effective_config_dumped", {"path": config_path})
            
            # Run config doctor to check for conflicts (HARD FAIL on HIGH severity)
            conflicts = run_config_doctor(print_output=True, hard_fail=True)
            if conflicts:
                self._logger.log("config_conflicts_found", {"count": len(conflicts), "conflicts": conflicts})
        except Exception as e:
            self._logger.error(f"Failed to dump effective config: {e}")
        
        risk_status = self._risk_integration.get_status_summary() if self._risk_integration else {}
        self._logger.log("orchestrator_ready", {
            "run_id": run_id,
            "account_analytics_enabled": analytics_enabled,
            "institutional_sizing_enabled": True,
            "correlation_management_enabled": True,
            "regime_throttling_enabled": True,
            "risk_integration_enabled": True,
            "risk_modules": risk_status.get("modules", {})
        })

    def run_loop(self) -> LoopResult:
        self.initialize()

        # Increment loop_id for Decision Record tracking
        loop_id = get_state("loop_id", 0) + 1
        set_state("loop_id", loop_id)

        timestamp = datetime.utcnow().isoformat() + "Z"
        self._logger.log("loop_start", {"timestamp": timestamp, "loop_id": loop_id})

        # Initialize display data for human-readable output
        display_data = LoopDisplayData()
        display_data.loop_number = loop_id
        
        equity = 0.0
        day_start_equity = 0.0
        enabled_bots = []
        errors = []
        regime = None

        init_ok, equity, day_start_equity, init_error = self._step_initialize()
        if not init_ok:
            errors.append(init_error)
        else:
            # Populate display data with account info and persist equity for risk gates
            display_data.equity = equity
            set_state("account.equity", equity)
            display_data.day_start_equity = day_start_equity
            display_data.daily_pnl = equity - day_start_equity
            display_data.daily_pnl_percent = ((equity - day_start_equity) / day_start_equity * 100) if day_start_equity > 0 else 0.0
            
            # Set account mode for display
            display_data.account_mode = get_account_mode()
            mode_params = get_account_mode_params()
            display_data.account_mode_description = mode_params.get("description", "")
            
            # Get cash from account
            try:
                account = self._alpaca.get_account()
                display_data.cash = account.cash
            except Exception:
                pass  # Non-critical display field; stale value is acceptable

        should_continue = True
        halt_reason = ""

        if init_ok:
            # Check halt status directly from HaltManager (ExitBot runs in dedicated 2s thread)
            halt_check = self._check_halt_and_daily_limits(equity, day_start_equity)
            should_continue = halt_check["should_continue"]
            halt_reason = halt_check.get("halt_reason", "")
            if not should_continue:
                errors.append(halt_reason)
            
            # Populate position/display data independently of ExitBot
            display_data.trailing_stops_active = self._count_active_trailing_stops()
            display_data.positions = self._get_positions_for_display()
            display_data.enhanced_positions = self._get_enhanced_positions()
            
            # Get recent exits from ExitBot's cache (doesn't trigger a full scan)
            display_data.recent_exits = self._convert_exits_for_display(
                self._exitbot._recent_exits[:5] if hasattr(self._exitbot, '_recent_exits') else []
            )
        else:
            should_continue = False
            halt_reason = f"Init failed: {init_error}"
            self._logger.log("loop_init_failed", {"error": init_error})

        if should_continue:
            portfolio_result = self._step_portfoliobot(equity)
            if portfolio_result.budgets_set:
                enabled_bots = portfolio_result.enabled_bots
                display_data.bots_enabled = enabled_bots
                display_data.risk_budget = portfolio_result.daily_risk
            else:
                errors.append(portfolio_result.error or "Budgets not set")

        # Fetch and log market regime (VIX, VVIX, TNX, DXY, MOVE indicators)
        if should_continue:
            try:
                regime = get_current_regime()
                self._logger.log("market_regime", {
                    "vix": regime.vix,
                    "vvix": regime.vvix,
                    "tnx": regime.tnx,
                    "dxy": regime.dxy,
                    "volatility_regime": regime.volatility_regime.value,
                    "sentiment": regime.sentiment.value,
                    "position_size_multiplier": regime.position_size_multiplier,
                    "halt_new_entries": regime.halt_new_entries,
                    "favor_straddles": regime.favor_straddles,
                    "favor_iron_condors": regime.favor_iron_condors,
                    "tighten_stops": regime.tighten_stops
                })
                
                # Populate regime info for display
                display_data.vix = regime.vix
                display_data.volatility_regime = regime.volatility_regime.value
                display_data.sentiment = regime.sentiment.value
                display_data.position_size_mult = regime.position_size_multiplier
                display_data.halt_new_entries = regime.halt_new_entries
                
                # Store VIX in state DB so risk gates can read it
                set_state("regime.vix", regime.vix)
                set_state("regime.volatility_regime", regime.volatility_regime.value)
                
                # Feed VIX to risk integration VolOfVol monitor
                if self._risk_integration and regime.vix and regime.vix > 0:
                    try:
                        self._risk_integration.update_vix(regime.vix)
                        self._logger.log("risk_vix_updated", {
                            "vix": regime.vix,
                            "volatility_regime": regime.volatility_regime.value
                        })
                    except Exception as vix_err:
                        self._logger.warn(f"Risk VIX update failed: {vix_err}")
                
                # REGIME-AWARE THROTTLING: Use ML to compute optimal size multiplier
                if self._regime_sizer:
                    try:
                        regime_context = {
                            "regime": {
                                "vix": regime.vix,
                                "vvix": regime.vvix,
                                "tnx": regime.tnx,
                                "dxy": regime.dxy,
                                "move": getattr(regime, 'move', 100.0),
                                "volatility_regime": regime.volatility_regime.value,
                                "sentiment": regime.sentiment.value,
                                "vvix_warning": getattr(regime, 'vvix_warning', False),
                                "rate_shock_warning": getattr(regime, 'rate_shock_warning', False),
                                "dollar_surge_warning": getattr(regime, 'dollar_surge_warning', False)
                            },
                            "regime_history": get_state("regime.history", []),
                            "returns_by_regime": get_state("regime.returns_by_regime", {
                                "low_vol": 0.5, "normal": 0.3, "high_vol": -0.2
                            })
                        }
                        
                        ml_size_result = self._regime_sizer.safe_predict(regime_context)
                        ml_size_multiplier = ml_size_result.get("size_multiplier", 1.0)
                        ml_confidence = ml_size_result.get("confidence", 0.5)
                        
                        set_state("ml.size_multiplier", ml_size_multiplier)
                        set_state("ml.size_multiplier_confidence", ml_confidence)
                        set_state("ml.regime_source", ml_size_result.get("source", "fallback"))
                        
                        self._logger.log("regime_throttle_applied", {
                            "ml_size_multiplier": ml_size_multiplier,
                            "confidence": ml_confidence,
                            "source": ml_size_result.get("source", "fallback"),
                            "vix": regime.vix,
                            "volatility_regime": regime.volatility_regime.value,
                            "regime_position_mult": regime.position_size_multiplier,
                            "halt_new_entries": regime.halt_new_entries
                        })
                        
                        display_data.position_size_mult = ml_size_multiplier
                        
                    except Exception as regime_ml_err:
                        self._logger.error(f"Regime ML sizing failed: {regime_ml_err}")
                        set_state("ml.size_multiplier", 1.0)
                
            except Exception as regime_err:
                self._logger.error(f"Market regime fetch failed: {regime_err}")
                set_state("ml.size_multiplier", 1.0)
        
        # Track SPY intraday change for selloff detector
        if should_continue:
            try:
                spy_quote = self._alpaca.get_latest_quote("SPY", "stock")
                if spy_quote and spy_quote.get("bid") and spy_quote.get("ask"):
                    spy_mid = (spy_quote["bid"] + spy_quote["ask"]) / 2
                    spy_prev = get_state("market.spy_prev_close", None)
                    if spy_prev and spy_prev > 0:
                        spy_change_pct = ((spy_mid - spy_prev) / spy_prev) * 100
                        set_state("market.spy_intraday_change_pct", round(spy_change_pct, 3))
                        if abs(spy_change_pct) >= 1.0:
                            self._logger.log("spy_intraday_alert", {
                                "spy_mid": round(spy_mid, 2),
                                "prev_close": round(spy_prev, 2),
                                "change_pct": round(spy_change_pct, 2)
                            })
                    else:
                        # First loop of the day: store current price as reference
                        set_state("market.spy_prev_close", spy_mid)
                        set_state("market.spy_intraday_change_pct", 0.0)
            except Exception as spy_err:
                self._logger.warn(f"SPY tracking failed: {spy_err}")
        
        # Build SystemState snapshot for this loop
        system_state = self._build_system_state(
            loop_id=loop_id,
            vix=regime.vix if regime else 25.0,
            equity=equity,
            day_start_equity=day_start_equity
        )
        
        # Log SystemState to JSONL
        self._log_system_state(system_state)
        
        # Print console banner
        if system_state:
            print(system_state.get_console_banner())
        
        # Print live position monitor (Trade-Bot style play-by-play)
        try:
            live_positions = self._alpaca.get_positions()
            if live_positions:
                print("--- LIVE POSITIONS ---")
                total_upl = 0.0
                for pos in live_positions:
                    sym = pos.symbol if hasattr(pos, 'symbol') else pos.get('symbol', '?')
                    entry = float(pos.avg_entry_price if hasattr(pos, 'avg_entry_price') else pos.get('avg_entry_price', 0))
                    current = float(pos.current_price if hasattr(pos, 'current_price') else pos.get('current_price', 0))
                    upl = float(pos.unrealized_pl if hasattr(pos, 'unrealized_pl') else pos.get('unrealized_pl', 0))
                    upl_pct = float(pos.unrealized_plpc if hasattr(pos, 'unrealized_plpc') else pos.get('unrealized_plpc', 0)) * 100
                    qty = pos.qty if hasattr(pos, 'qty') else pos.get('qty', '?')
                    total_upl += upl
                    tier1_target = entry * 3.0
                    status = "WIN" if upl > 0 else "LOSS"
                    print(f"  {sym[:25]:25s} x{str(qty):4s} ${entry:.2f}→${current:.2f} P&L:${upl:+.0f} ({upl_pct:+.1f}%) [{status}] T1@${tier1_target:.2f}")
                print(f"  Total UPL: ${total_upl:+.2f} | {len(live_positions)} positions")
                print("----------------------")
        except Exception as e:
            pass
        
        # Run account-level ML analytics
        account_analytics = None
        if should_continue and self._account_analytics and self._account_analytics.is_enabled:
            try:
                account = self._alpaca.get_account()
                positions = self._alpaca.get_positions()
                
                bot_stats = {
                    "crypto_bot": {"trades_today": 0, "pnl_today": 0},
                    "momentum_bot": {"trades_today": 0, "pnl_today": 0},
                    "options_bot": {"trades_today": 0, "pnl_today": 0}
                }
                
                account_analytics = self._account_analytics.analyze(
                    account_info={
                        "equity": float(account.equity),
                        "cash": float(account.cash),
                        "buying_power": float(account.buying_power)
                    },
                    positions=[{"symbol": p.symbol, "qty": float(p.qty)} for p in positions],
                    bot_stats=bot_stats
                )
                
                self._logger.log("account_analytics", {
                    "risk_multiplier": account_analytics.risk_multiplier,
                    "size_multiplier": account_analytics.position_size_multiplier,
                    "drawdown_prob": account_analytics.drawdown_probability,
                    "is_anomaly": account_analytics.is_anomaly,
                    "health_score": account_analytics.overall_health_score,
                    "should_halt": account_analytics.should_halt_trading
                })
                
                if account_analytics.should_halt_trading:
                    should_continue = False
                    halt_reason = f"ML halt: {', '.join(account_analytics.halt_reasons)}"
                    self._logger.log("ml_trading_halt", {
                        "reasons": account_analytics.halt_reasons
                    })
                else:
                    set_state("ml.risk_multiplier", account_analytics.risk_multiplier)
                    set_state("ml.size_multiplier", account_analytics.position_size_multiplier)
                    set_state("ml.bot_allocations", account_analytics.bot_allocations)
                    set_state("ml.drawdown_probability", account_analytics.drawdown_probability)
                    set_state("ml.health_score", account_analytics.overall_health_score)
                    
                    settings = load_settings()
                    analytics_config = settings.get("account_analytics", {})
                    
                    if analytics_config.get("risk_adjustment", {}).get("enabled", True):
                        self._apply_ml_risk_multiplier(account_analytics.risk_multiplier)
                    
                    if analytics_config.get("regime_sizing", {}).get("enabled", True):
                        set_state("ml.position_size_override", account_analytics.position_size_multiplier)
                    
                    dd_config = analytics_config.get("drawdown_prediction", {})
                    if dd_config.get("enabled", True):
                        reduce_threshold = dd_config.get("reduce_threshold", 0.5)
                        if account_analytics.drawdown_probability > reduce_threshold:
                            current_mult = get_state("ml.size_multiplier", 1.0)
                            reduced_mult = current_mult * 0.7
                            set_state("ml.size_multiplier", reduced_mult)
                            self._logger.log("ml_size_reduced_dd_risk", {
                                "drawdown_prob": account_analytics.drawdown_probability,
                                "new_multiplier": reduced_mult
                            })
                    
            except Exception as analytics_err:
                self._logger.error(f"Account analytics failed: {analytics_err}")
        
        # Run pre-market intelligence gathering (6:00-6:30 AM PST)
        premarket_intel = None
        if should_continue:
            premarket_intel = self._step_premarket_intelligence()
        
        # Run ticker screening to select best candidates
        selected_stocks = []
        selected_options = []
        if should_continue:
            selected_stocks, selected_options = self._step_screening(premarket_intel)

        # Run SessionSelectorBot to dynamically inject screener-selected tickers
        if should_continue:
            try:
                from .bots.session_selector_bot import get_session_selector
                session_bot = get_session_selector()
                session_result = session_bot.execute()
                if session_result.get("success") and session_result.get("bot_ids"):
                    session_bot_ids = session_result["bot_ids"]
                    # Inject session bot IDs into enabled_bots (alongside static bots)
                    for sid in session_bot_ids:
                        if sid not in enabled_bots:
                            enabled_bots.append(sid)
                            # Set budget state for each session bot
                            session_cfg = load_bots_config().get("session_selector", {})
                            budget_pct = session_cfg.get("budget_per_ticker_pct", 20.0)
                            session_budget = (equity * budget_pct / 100.0) if equity > 0 else 500.0
                            set_state(f"bots.{sid}", {"enabled": True, "allowed": True})
                            set_state(f"budgets.{sid}", {"max_daily_loss": session_budget})
                    self._logger.log("session_selector_injected", {
                        "session_bot_ids": session_bot_ids,
                        "selected_tickers": session_result.get("selected_tickers", []),
                        "scores": session_result.get("scores", {}),
                        "total_enabled_bots": len(enabled_bots)
                    })
            except Exception as ss_err:
                self._logger.error(f"SessionSelectorBot failed (non-fatal): {ss_err}")

        bots_run = []
        trades_executed = 0
        signals = []
        bots_outside_hours = []
        
        if should_continue and enabled_bots:
            exec_result = self._step_execution(
                enabled_bots, equity, selected_stocks, selected_options
            )
            bots_run = exec_result.bots_run
            errors.extend(exec_result.errors)
            
            # Extract signals and trades for display
            trades_executed = getattr(exec_result, 'trades_attempted', 0)
            signals = getattr(exec_result, 'signals', [])
            bots_outside_hours = getattr(exec_result, 'bots_outside_hours', [])
        
        # Populate execution results for display
        display_data.trades_executed = trades_executed
        display_data.signals = signals
        display_data.bots_outside_hours = bots_outside_hours
        display_data.errors = errors
        display_data.is_halted = self._halt.is_halted()
        display_data.halt_reason = halt_reason
        
        # EVENT MODE: Populate event fields for compact console output
        # Order events from execution
        display_data.order_events = getattr(exec_result, 'order_events', []) if should_continue and enabled_bots else []
        # Blocked signals from execution
        display_data.blocked_signals = getattr(exec_result, 'blocked_signals', []) if should_continue and enabled_bots else []
        
        # Halt events (when a halt was triggered this loop)
        if not should_continue and halt_reason:
            halt_event = HaltEvent(
                trigger=self._classify_halt_trigger(halt_reason),
                details=halt_reason,
                action="NEW_ENTRIES_PAUSED" if self._halt.is_halted() else "INIT_FAILED"
            )
            display_data.halt_events = [halt_event]
        
        # Additional data for heartbeat display
        try:
            orders = self._alpaca.list_orders(status="open")
            display_data.open_orders = len(orders) if orders else 0
        except Exception:
            display_data.open_orders = 0  # Non-critical display field
        
        # Calculate risk used
        settings = load_settings()
        max_daily_loss_pct = settings.get('risk', {}).get('global_max_daily_loss_pct', 2.0)
        display_data.max_risk_pct = max_daily_loss_pct
        if day_start_equity > 0 and equity > 0:
            current_loss_pct = abs(min(0, equity - day_start_equity) / day_start_equity * 100)
            display_data.risk_used_pct = current_loss_pct
        
        # Data age from state (last quote fetch time)
        last_quote_time = get_state("cache.last_quote_time", None)
        if last_quote_time:
            try:
                import datetime as dt_module
                last_dt = dt_module.datetime.fromisoformat(last_quote_time.replace("Z", "+00:00"))
                now = dt_module.datetime.utcnow().replace(tzinfo=last_dt.tzinfo)
                display_data.data_age_seconds = (now - last_dt).total_seconds()
            except (ValueError, AttributeError, TypeError):
                display_data.data_age_seconds = 0.0
        else:
            display_data.data_age_seconds = 0.0

        result = self._step_finalize(bots_run, errors, halt_reason)
        result.display_data = display_data

        self._logger.log("loop_end", {
            "success": result.success,
            "status": result.status,
            "bots_run": len(bots_run),
            "errors": len(errors)
        })

        return result
    
    def _get_positions_for_display(self) -> List[PositionInfo]:
        """Fetch current positions and format for display."""
        positions = []
        try:
            alpaca_positions = self._alpaca.get_positions()
            for p in alpaca_positions:
                qty = float(p.qty)
                side = "long" if qty > 0 else "short"
                entry_price = float(p.avg_entry_price)
                current_price = float(p.current_price)
                pnl = float(p.unrealized_pl)
                cost_basis = abs(qty) * entry_price
                pnl_percent = (pnl / cost_basis * 100) if cost_basis > 0 else 0.0
                
                # Check if trailing stop is active for this position
                trailing_active = get_state(f"trailing.{p.symbol}.active", False)
                
                positions.append(PositionInfo(
                    symbol=p.symbol,
                    qty=abs(qty),
                    side=side,
                    entry_price=entry_price,
                    current_price=current_price,
                    pnl=pnl,
                    pnl_percent=pnl_percent,
                    trailing_stop_active=trailing_active
                ))
        except Exception as e:
            self._logger.error(f"Failed to get positions for display: {e}")
        return positions

    def _get_enhanced_positions(self) -> List[PositionState]:
        """
        Fetch positions and create PositionState objects for enhanced display.
        
        Retrieves stop loss levels, TP targets, and other trade parameters from
        stored state to build complete PositionState objects for cockpit display.
        """
        enhanced = []
        try:
            alpaca_positions = self._alpaca.get_positions()
            settings = load_settings()
            
            for p in alpaca_positions:
                symbol = p.symbol
                qty = abs(float(p.qty))
                side = "long" if float(p.qty) > 0 else "short"
                entry_price = float(p.avg_entry_price)
                current_price = float(p.current_price)
                
                stop_loss_pct = get_state(f"position.{symbol}.stop_pct", 0.02)
                tp1_r = get_state(f"position.{symbol}.tp1_r", 1.5)
                tp2_r = get_state(f"position.{symbol}.tp2_r", 2.5)
                max_hold_hours = get_state(f"position.{symbol}.max_hold_hours", 2.0)
                
                opened_at_ts = get_state(f"position.{symbol}.opened_at", None)
                
                pos_state = create_position_state_from_trade(
                    symbol=symbol,
                    entry_price=entry_price,
                    qty=qty,
                    side=side,
                    stop_loss_pct=stop_loss_pct,
                    tp1_r=tp1_r,
                    tp2_r=tp2_r,
                    max_hold_hours=max_hold_hours
                )
                
                if opened_at_ts:
                    from datetime import datetime
                    try:
                        pos_state.opened_at = datetime.fromisoformat(opened_at_ts)
                    except (ValueError, AttributeError):
                        pass  # Malformed timestamp; opened_at remains unset
                
                tp1_hit = get_state(f"position.{symbol}.tp1_hit", False)
                tp2_hit = get_state(f"position.{symbol}.tp2_hit", False)
                secured_pct = get_state(f"position.{symbol}.secured_pct", 0.0)
                trailing_active = get_state(f"trailing.{symbol}.active", False)
                
                pos_state.tp1_hit = tp1_hit
                pos_state.tp2_hit = tp2_hit
                pos_state.secured_pct = secured_pct
                pos_state.breakeven_stop_active = tp1_hit
                
                high_wm = get_state(f"trailing.{symbol}.high_watermark", entry_price)
                low_wm = get_state(f"trailing.{symbol}.low_watermark", entry_price)
                trail_mode = get_state(f"trailing.{symbol}.mode", "ATR")
                trail_atr_period = get_state(f"trailing.{symbol}.atr_period", 14)
                trail_atr_mult = get_state(f"trailing.{symbol}.atr_multiplier", 2.0)
                trail_pct = get_state(f"trailing.{symbol}.pct", 0.05)
                trail_distance = get_state(f"trailing.{symbol}.distance", pos_state.trailing_distance)
                trail_stop = get_state(f"trailing.{symbol}.stop_price", 0.0)
                
                pos_state.high_watermark = high_wm
                pos_state.low_watermark = low_wm
                pos_state.trailing_mode = trail_mode
                pos_state.trailing_atr_period = trail_atr_period
                pos_state.trailing_atr_multiplier = trail_atr_mult
                pos_state.trailing_pct = trail_pct
                pos_state.trailing_distance = trail_distance if trail_distance > 0 else pos_state.trailing_distance
                
                if trailing_active and trail_stop > 0:
                    pos_state.trailing_active = True
                    pos_state.trailing_stop_price = trail_stop
                elif trailing_active and pos_state.trailing_distance > 0:
                    pos_state.trailing_active = True
                    if side == "long":
                        pos_state.trailing_stop_price = high_wm - pos_state.trailing_distance
                    else:
                        pos_state.trailing_stop_price = low_wm + pos_state.trailing_distance
                else:
                    pos_state.trailing_active = False
                
                pos_state.update_price(current_price)
                
                enhanced.append(pos_state)
                
        except Exception as e:
            self._logger.error(f"Failed to get enhanced positions: {e}")
        
        return enhanced

    def _classify_halt_trigger(self, halt_reason: str) -> str:
        """Classify halt reason into a trigger type for EVENT mode display."""
        reason_lower = halt_reason.lower()
        if "stale" in reason_lower or "staleness" in reason_lower:
            return "stale_data"
        elif "api" in reason_lower or "connection" in reason_lower or "timeout" in reason_lower:
            return "api_failure"
        elif "loss" in reason_lower or "pnl" in reason_lower or "drawdown" in reason_lower:
            return "daily_loss"
        elif "ml" in reason_lower or "model" in reason_lower:
            return "ml_halt"
        elif "credential" in reason_lower or "key" in reason_lower:
            return "auth_failure"
        elif "init" in reason_lower:
            return "init_failure"
        else:
            return "manual_halt"

    def _convert_exits_for_display(self, exit_records) -> List:
        """Convert ExitRecord objects from ExitBot to ExitInfo for console display."""
        from .core.console import ExitInfo
        exits = []
        for rec in exit_records:
            try:
                exits.append(ExitInfo(
                    symbol=rec.symbol,
                    side=rec.side,
                    qty=rec.qty,
                    entry_price=rec.entry_price,
                    exit_price=rec.exit_price,
                    pnl=rec.pnl,
                    pnl_percent=rec.pnl_percent,
                    reason=rec.reason,
                    bot_id=rec.bot_id,
                    timestamp=rec.timestamp
                ))
            except Exception:
                pass
        return exits

    def _step_initialize(self):
        self._logger.log("step_1_init", {})

        # Daily cleanup: purge stale exit_lock keys (>24h old) that never got lazily cleared
        # because their positions were already closed. Runs once per UTC day.
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        if get_state("exit_lock_last_cleanup_date") != today_str:
            try:
                import json as _json
                from datetime import timezone as _tz, timedelta as _td
                cutoff = datetime.now(_tz.utc) - _td(hours=24)
                all_state = get_all_state() or {}
                lock_keys = [k for k in all_state if k.startswith("exit_lock")]
                stale = []
                for k in lock_keys:
                    try:
                        raw = all_state[k]
                        data = _json.loads(raw) if isinstance(raw, str) else (raw or {})
                        ts = datetime.fromisoformat(data.get("created_ts", "").replace("Z", "+00:00"))
                        if ts < cutoff:
                            stale.append(k)
                    except Exception:
                        stale.append(k)
                for k in stale:
                    delete_state(k)
                set_state("exit_lock_last_cleanup_date", today_str)
                if stale:
                    self._logger.log("exit_lock_daily_cleanup", {
                        "deleted": len(stale),
                        "remaining": len(lock_keys) - len(stale)
                    })
            except Exception as _cleanup_err:
                self._logger.error(f"Daily exit_lock cleanup failed (fail-open): {_cleanup_err}")

        # Validate inputs
        if not self._alpaca.has_credentials():
            error = "ALPACA_KEY and ALPACA_SECRET required"
            self._logger.log("step_1_no_credentials", {"error": error})
            self._health.record_api_failure(error)
            return False, 0.0, 0.0, error

        # Retry account fetch with exponential backoff
        max_retries = 3
        for attempt in range(max_retries):
            try:
                account = self._alpaca.get_account()
                equity = account.equity

                # Validate account data before proceeding
                if equity <= 0:
                    error = f"Invalid account equity: {equity}"
                    self._logger.error(error)
                    return False, 0.0, 0.0, error

                if account.status != "ACTIVE":
                    error = f"Account not active: {account.status}"
                    self._logger.warn(error)
                    # Continue with warning but don't fail

                # Auto-detect account mode based on equity (micro/small/standard)
                is_small = auto_detect_account_mode(equity)
                mode = get_account_mode()
                mode_params = get_account_mode_params()
                self._logger.log("account_mode_detected", {
                    "equity": equity,
                    "mode": mode,
                    "thresholds": {"micro": MICRO_ACCOUNT_THRESHOLD, "small": SMALL_ACCOUNT_THRESHOLD},
                    "daily_risk_pct": mode_params.get("daily_risk_pct", 2.0),
                    "description": mode_params.get("description", "")
                })

                break

            except Exception as e:
                error = f"Failed to fetch account (attempt {attempt + 1}): {e}"
                self._logger.error(error)

                if attempt < max_retries - 1:
                    # Wait before retry: 1s, 2s, 4s
                    import time
                    wait_time = 2 ** attempt
                    self._logger.log("retry_wait", {"seconds": wait_time})
                    time.sleep(wait_time)
                    continue
                else:
                    # Final attempt failed
                    return False, 0.0, 0.0, error

        date_string = self._clock.get_date_string()
        day_start_key = f"day_start_equity_{date_string}"

        day_start_equity = get_state(day_start_key)
        if not day_start_equity or day_start_equity < 1000:  # Reset if unreasonably low
            day_start_equity = equity
            set_state(day_start_key, day_start_equity)
            set_state("day_start_equity", day_start_equity)
            # Reset SPY tracking for the new day
            set_state("market.spy_prev_close", None)
            set_state("market.spy_intraday_change_pct", 0.0)
            # Reset daily trade metrics counters for fresh performance tracking
            set_state("daily.trades_count", 0)
            set_state("daily.wins_count", 0)
            set_state("daily.win_amounts", [])
            set_state("daily.loss_amounts", [])
            self._logger.log("step_1_day_start_reset", {
                "date": date_string,
                "day_start_equity": day_start_equity,
                "current_equity": equity,
                "reason": "new_day_or_invalid_value"
            })
        else:
            # Ensure day_start_equity is reasonable - if too low, reset it
            if day_start_equity < equity * 0.1:  # If day start is less than 10% of current equity
                self._logger.log("step_1_equity_reset", {
                    "old_day_start": day_start_equity,
                    "new_day_start": equity,
                    "reason": "day_start_too_low"
                })
                day_start_equity = equity
                set_state(day_start_key, day_start_equity)
                set_state("day_start_equity", day_start_equity)

        self._halt.clear_if_expired()
        
        self._warm_bar_cache_if_needed()

        self._logger.log("step_1_ok", {
            "equity": equity,
            "day_start_equity": day_start_equity
        })

        return True, equity, day_start_equity, ""
    
    def _warm_bar_cache_if_needed(self) -> None:
        """
        Warm the bar cache for fast startup during trading hours.
        
        Caches historical bar data for all TwentyMinuteBot tickers so
        startup after restart is nearly instant (no 3+ min data fetch).
        Only runs once per day during the first loop.
        """
        try:
            from .services.bar_cache import get_bar_cache_warmer, get_cache_stats
            from .core.clock import get_market_clock
            
            clock = get_market_clock()
            now = clock.now()
            cache_date = now.strftime("%Y-%m-%d")
            
            cache_key = f"bar_cache_warmed_{cache_date}"
            if get_state(cache_key):
                return
            
            bots_config = load_bots_config()
            twentymin_config = bots_config.get("twentyminute_bot", {})
            tickers = twentymin_config.get("tickers", [])
            
            if not tickers:
                return
            
            self._logger.log("bar_cache_warm_start", {
                "tickers": len(tickers),
                "cache_date": cache_date
            })
            
            warmer = get_bar_cache_warmer()
            results = warmer.warm_cache(
                tickers=tickers,
                timeframes=["1Min", "1Day"],
                bar_limit=50,
                max_workers=5
            )
            
            set_state(cache_key, True)
            
            self._logger.log("bar_cache_warm_complete", {
                "cached": results.get("cached", 0),
                "failed": results.get("failed", 0)
            })
            
        except Exception as e:
            self._logger.error(f"Bar cache warm error (non-fatal): {e}")

    def _check_halt_and_daily_limits(self, equity: float, day_start_equity: float) -> dict:
        """
        Lightweight halt check for main loop - ExitBot runs in dedicated 2s thread.
        Checks HaltManager status and daily P&L limits without running full position scan.
        """
        self._logger.log("step_2_halt_check", {})
        
        # Check HaltManager state (set by ExitBot's dedicated thread)
        if self._halt.is_halted():
            status = self._halt.get_status()
            return {"should_continue": False, "halt_reason": status.reason}
        
        # PDT PROTECTION: Hard equity floor at $25,500 (buffer above $25K PDT line)
        settings = load_settings()
        risk_config = settings.get("risk", {})
        pdt_floor = risk_config.get("pdt_equity_floor_usd", 25500.0)
        if pdt_floor > 0 and equity <= pdt_floor:
            reason = f"PDT_PROTECTION: equity=${equity:.2f} <= floor=${pdt_floor:.2f} — halting to protect PDT status"
            self._logger.log("halt_pdt_floor", {"equity": equity, "pdt_floor": pdt_floor})
            print(f"\n  *** PDT PROTECTION: Equity ${equity:.0f} near $25K PDT line — ALL TRADING HALTED ***\n")
            return {"should_continue": False, "halt_reason": reason}

        # Check daily P&L limit directly
        pnl = equity - day_start_equity
        max_loss_pct = risk_config.get("global_max_daily_loss_pct", 1.0)
        max_loss = day_start_equity * (max_loss_pct / 100)
        
        if pnl <= -max_loss:
            reason = f"MAX_DAILY_LOSS: pnl={pnl:.2f} <= -{max_loss:.2f}"
            self._logger.log("halt_daily_loss_check", {"pnl": pnl, "max_loss": max_loss})
            return {"should_continue": False, "halt_reason": reason}
        
        return {"should_continue": True}
    
    def _count_active_trailing_stops(self) -> int:
        """Count active trailing stops from state without running ExitBot."""
        count = 0
        try:
            from .core.state import get_state
            positions = self._alpaca.get_positions()
            for p in positions:
                if get_state(f"trailing.{p.symbol}.active", False):
                    count += 1
        except Exception:
            pass
        return count

    def _step_portfoliobot(self, equity: float):
        self._logger.log("step_3_portfoliobot", {})
        return self._portfoliobot.run(equity)

    def _step_premarket_intelligence(self) -> Optional[Dict[str, Any]]:
        """
        Run pre-market intelligence gathering (configured in settings.yaml).
        
        Analyzes overnight gaps, IV levels, volume surges, and events.
        Results are cached for the trading session and shared between
        TwentyMinuteBot and OptionsBot.
        
        Returns:
            Pre-market cache data if in window, None otherwise
        """
        from .services.premarket_intelligence import PreMarketIntelligenceService
        from .core.clock import get_market_clock
        
        clock = get_market_clock()
        
        # Check if we're in pre-market window (uses config times)
        if not clock.is_pre_market_intel_window():
            # Not in pre-market window - try to get cached data
            try:
                intel_service = PreMarketIntelligenceService()
                cached = intel_service.get_cached_intelligence()
                if cached and cached.is_complete:
                    self._logger.log("premarket_using_cache", {
                        "session_date": cached.session_date,
                        "tickers_analyzed": len(cached.tickers),
                        "top_3": cached.ranked_opportunities[:3]
                    })
                    serialized = self._serialize_premarket_cache(cached)
                    set_state("premarket.cache", serialized)  # Persist for execution service
                    return serialized
            except Exception as e:
                self._logger.warn(f"Failed to load pre-market cache: {e}")
            return None
        
        # In pre-market window - run full analysis
        self._logger.log("step_premarket_intel_start", {
            "time": clock.now().strftime("%H:%M:%S")
        })
        
        try:
            intel_service = PreMarketIntelligenceService()
            cache = intel_service.run_analysis()
            
            self._logger.log("step_premarket_intel_complete", {
                "tickers_analyzed": len(cache.tickers),
                "top_opportunities": cache.ranked_opportunities[:5],
                "market_regime": cache.market_regime,
                "regime_multiplier": cache.regime_multiplier
            })
            
            # SELECT AND LOCK the trading universe via UniverseGuard
            selected_symbols = intel_service.select_universe()
            
            self._logger.log("premarket_universe_locked", {
                "selected_symbols": selected_symbols,
                "count": len(selected_symbols)
            })
            
            # Serialize and store in state for execution service and dashboard
            serialized = self._serialize_premarket_cache(cache)
            set_state("premarket.cache", serialized)  # For execution service
            set_state("premarket.analyzed", True)
            set_state("premarket.top_opportunities", cache.ranked_opportunities[:5])
            set_state("premarket.selected_symbols", selected_symbols)  # The LOCKED list
            set_state("premarket.market_regime", cache.market_regime)
            
            return serialized
            
        except Exception as e:
            self._logger.error(f"Pre-market intelligence failed: {e}")
            # Fail-closed: check if we should halt trading
            from .risk.universe_guard import get_universe_guard
            guard = get_universe_guard()
            if not guard.is_trading_allowed():
                self._logger.log("premarket_fail_closed", {
                    "reason": "premarket_failed_no_selection"
                })
            return None
    
    def _serialize_premarket_cache(self, cache) -> Dict[str, Any]:
        """Serialize pre-market cache for passing to other components."""
        return {
            "session_date": cache.session_date,
            "tickers": {
                ticker: {
                    "opportunity_score": intel.opportunity_score,
                    "liquidity_score": intel.liquidity_score,
                    "gap_pct": intel.gap.gap_pct if intel.gap else None,
                    "iv_percentile": intel.iv.iv_percentile if intel.iv else None,
                    "strategies": intel.recommended_strategies,
                    "risk_flags": intel.risk_flags
                }
                for ticker, intel in cache.tickers.items()
            },
            "ranked_opportunities": cache.ranked_opportunities,
            "market_regime": cache.market_regime,
            "regime_multiplier": cache.regime_multiplier
        }

    def _step_screening(self, premarket_intel: Optional[Dict[str, Any]] = None):
        """
        Run ticker screening to select best candidates for trading.
        
        Screens stock universe for momentum bots and options universe
        for options trading. Uses pre-market intelligence when available
        to rank opportunities by gap/IV/volume metrics.
        
        Args:
            premarket_intel: Optional pre-market intelligence to factor in
        
        Uses fallback tickers if:
        - Screening throws an exception
        - No candidates pass the filters
        
        Returns:
            Tuple of (selected_stocks, selected_options)
        """
        self._logger.log("step_3b_screening", {"has_premarket_intel": premarket_intel is not None})
        
        # Fallback tickers from config or hardcoded defaults
        fallback_stocks = ["AAPL", "MSFT"]
        fallback_options = ["SPY", "QQQ"]
        
        selected_stocks = []
        selected_options = []
        
        # Use pre-market ranked opportunities if available
        if premarket_intel and premarket_intel.get("ranked_opportunities"):
            ranked = premarket_intel["ranked_opportunities"]
            
            # Split ranked opportunities by suitability
            # Use top ranked for options (where IV matters)
            selected_options = ranked[:5]  # Top 5 for options
            
            # Use momentum candidates from ticker data
            ticker_data = premarket_intel.get("tickers", {})
            gap_stocks = [
                ticker for ticker, data in ticker_data.items()
                if data.get("gap_pct") and abs(data.get("gap_pct", 0)) > 0.3
            ]
            selected_stocks = gap_stocks[:5] if gap_stocks else ranked[:3]
            
            self._logger.log("premarket_screening_complete", {
                "selected_options": selected_options,
                "selected_stocks": selected_stocks,
                "source": "premarket_intel"
            })
        else:
            # Fall back to legacy screeners if no pre-market intel
            try:
                # Screen stock universe using legacy screener
                stock_result = self._stock_screener.screen()
                selected_stocks = stock_result.selected_tickers
                
                if not selected_stocks:
                    self._logger.warn("Stock screening returned empty - using fallbacks")
                    selected_stocks = fallback_stocks
                    
                self._logger.log("stock_screening_complete", {
                    "selected": selected_stocks,
                    "from_cache": stock_result.from_cache,
                    "used_fallback": selected_stocks == fallback_stocks
                })
                
            except Exception as e:
                self._logger.error(f"Stock screening failed: {e}")
                selected_stocks = fallback_stocks
            
            try:
                # Screen options universe using legacy screener
                options_result = self._options_screener.screen()
                selected_options = options_result.selected_underlyings
                
                if not selected_options:
                    self._logger.warn("Options screening returned empty - using fallbacks")
                    selected_options = fallback_options
                
                self._logger.log("options_screening_complete", {
                    "selected": selected_options,
                    "from_cache": options_result.from_cache,
                    "used_fallback": selected_options == fallback_options
                })
                
            except Exception as e:
                self._logger.error(f"Options screening failed: {e}")
                selected_options = fallback_options
        
        # Store selected tickers in state for dashboard and downstream bots
        set_state("screener.selected_stocks", selected_stocks)
        set_state("screener.selected_options", selected_options)
        set_state("screener.premarket_intel_used", premarket_intel is not None)
        set_state("screener.last_run", datetime.utcnow().isoformat())
        
        return selected_stocks, selected_options

    def _step_execution(self, enabled_bots, equity: float, 
                        selected_stocks: list = None, 
                        selected_options: list = None):
        self._logger.log("step_4_execution", {
            "enabled_bots": enabled_bots,
            "selected_stocks": selected_stocks or [],
            "selected_options": selected_options or []
        })
        return self._execution.run(
            enabled_bots, equity, 
            selected_stocks=selected_stocks or [],
            selected_options=selected_options or []
        )

    def _step_finalize(self, bots_run, errors, halt_reason):
        self._logger.log("step_5_finalize", {})

        timestamp = datetime.utcnow().isoformat() + "Z"
        is_halted = self._halt.is_halted()
        has_errors = len(errors) > 0
        bots_ran = len(bots_run) > 0

        if is_halted:
            status = f"HALTED: {halt_reason}"
            success = True
        elif has_errors and not bots_ran:
            is_init_error = any(
                "credentials" in e.lower() or
                "init" in e.lower() or
                "budgets" in e.lower()
                for e in errors
            )
            if is_init_error:
                status = "FAIL_CLOSED: System failed safely without trading"
                success = True
            else:
                status = f"ERROR: {'; '.join(errors)}"
                success = False
        elif not bots_ran:
            status = "SKIPPED: No bots ran"
            success = True
        elif has_errors:
            status = f"PARTIAL: {len(bots_run)} bots with {len(errors)} errors"
            success = False
        else:
            status = f"OK: {len(bots_run)} bots ran successfully"
            success = True

        summary = f"""
Trading Loop Summary:
- Status: {status}
- Bots run: {', '.join(bots_run) if bots_run else 'None'}
- Errors: {'; '.join(errors) if errors else 'None'}
- Timestamp: {timestamp}
""".strip()

        self._logger.log("loop_complete", {
            "status": status,
            "bots_run": bots_run,
            "errors": errors,
            "success": success,
            "timestamp": timestamp
        })
        
        # Record daily equity for performance analytics and metrics persistence
        if self._performance_analytics and success:
            try:
                # Verify fresh equity data by checking account accessibility
                account = self._alpaca.get_account()
                current_equity = float(account.equity)
                
                # Idempotency: Only record once per calendar day
                today = datetime.utcnow().strftime("%Y-%m-%d")
                last_recorded_date = get_state("performance.last_equity_date", "")
                
                if today != last_recorded_date:
                    # Record daily equity for Sharpe calculation
                    self._performance_analytics.record_daily_equity(current_equity)
                    set_state("performance.last_equity_date", today)
                    
                    # Calculate and persist metrics
                    metrics = self._performance_analytics.calculate_metrics()
                    
                    self._logger.log("performance_metrics_persisted", {
                        "equity": current_equity,
                        "date": today,
                        "sharpe": metrics.sharpe_ratio,
                        "win_rate": metrics.win_rate,
                        "profit_factor": metrics.profit_factor,
                        "max_drawdown": metrics.max_drawdown_pct,
                        "meets_targets": {
                            "sharpe": metrics.meets_sharpe_target,
                            "win_rate": metrics.meets_win_rate_target,
                            "profit_factor": metrics.meets_profit_factor_target,
                            "drawdown": metrics.meets_drawdown_target
                        }
                    })
                    # NEW: Record daily metrics via PerformanceTracker for Sharpe/Sortino
                    if self._performance_tracker:
                        daily_pnl = float(account.equity) - float(account.last_equity)
                        # Get trade counts from today's state
                        trades_today = get_state("daily.trades_count", 0)
                        wins_today = get_state("daily.wins_count", 0)
                        win_amounts = get_state("daily.win_amounts", [])
                        loss_amounts = get_state("daily.loss_amounts", [])
                        
                        self._performance_tracker.record_daily_metrics(
                            equity=current_equity,
                            daily_pnl=daily_pnl,
                            trades_executed=trades_today,
                            trades_profitable=wins_today,
                            win_amounts=win_amounts if isinstance(win_amounts, list) else [],
                            loss_amounts=loss_amounts if isinstance(loss_amounts, list) else []
                        )
                else:
                    self._logger.log("performance_metrics_skipped", {
                        "reason": "already_recorded_today",
                        "date": today
                    })
            except Exception as perf_err:
                # Fail-closed: Don't persist potentially stale data
                self._logger.error(f"Performance metrics persistence failed (fail-closed): {perf_err}")

        # Run cross-bot exposure monitoring
        if self._cross_bot_monitor and success:
            try:
                positions = self._alpaca.get_positions()
                equity = float(self._alpaca.get_account().equity)
                self._cross_bot_monitor.set_equity(equity)
                
                bot_positions: Dict[str, List[Dict[str, Any]]] = {}
                for p in positions:
                    qty = float(p.qty)
                    side = "long" if qty > 0 else "short"
                    current_price = float(p.current_price)
                    market_value = abs(qty) * current_price
                    bot_id = get_state(f"position.{p.symbol}.bot_id", "unknown")
                    
                    if bot_id not in bot_positions:
                        bot_positions[bot_id] = []
                    bot_positions[bot_id].append({
                        "symbol": str(p.symbol),
                        "qty": qty,
                        "market_value": market_value,
                        "side": side
                    })
                
                for bot_id, pos_list in bot_positions.items():
                    self._cross_bot_monitor.update_bot_exposure(bot_id, pos_list)
                # Check aggregate exposure
                exposure = self._cross_bot_monitor.get_aggregate_exposure()
                if exposure.status in ["near_limit", "at_limit", "breached"]:
                    self._logger.log("cross_bot_exposure_warning", {
                        "net_delta": exposure.net_delta,
                        "net_delta_pct": exposure.net_delta_pct,
                        "status": exposure.status,
                        "action": exposure.recommended_action
                    })
            except Exception as cb_err:
                self._logger.error(f"Cross-bot monitoring failed (non-fatal): {cb_err}")

        # Run edge decay checks periodically
        if self._edge_decay_monitor and success:
            try:
                today = datetime.utcnow().strftime("%Y-%m-%d")
                last_decay_check = get_state("edge_decay.last_check_date", "")
                if today != last_decay_check:
                    strategies = ["MomentumBot", "CryptoBot", "TwentyMinuteBot", "OptionsBot"]
                    for strategy in strategies:
                        status = self._edge_decay_monitor.evaluate_strategy(strategy)
                        if status.status in ["warning", "degraded"]:
                            self._logger.log("edge_decay_alert", {
                                "strategy": strategy,
                                "status": status.status,
                                "decay_ratio": status.decay_ratio,
                                "action": status.recommended_action
                            })
                    set_state("edge_decay.last_check_date", today)
            except Exception as ed_err:
                self._logger.error(f"Edge decay check failed (non-fatal): {ed_err}")

        # Run ML drift detection periodically
        if self._drift_detector and success:
            try:
                today = datetime.utcnow().strftime("%Y-%m-%d")
                last_drift_check = get_state("ml_drift.last_check_date", "")
                if today != last_drift_check:
                    drift_status = self._drift_detector.detect_drift("ml_signal_model")
                    if drift_status.status in ["warning", "retrain_needed"]:
                        self._logger.log("ml_drift_alert", {
                            "model_id": drift_status.model_id,
                            "status": drift_status.status,
                            "drift_score": drift_status.drift_score,
                            "action": drift_status.recommended_action
                        })
                    set_state("ml_drift.last_check_date", today)
            except Exception as dr_err:
                self._logger.error(f"ML drift detection failed (non-fatal): {dr_err}")

        # Validate outputs before returning
        if not isinstance(success, bool):
            success = bool(success)
        if not isinstance(status, str):
            status = str(status)
        if not isinstance(summary, str):
            summary = str(summary)
        if not isinstance(timestamp, str):
            timestamp = str(timestamp)

        return LoopResult(
            success=success,
            status=status,
            summary=summary,
            timestamp=timestamp
        )


    def _build_system_state(
        self,
        loop_id: int,
        vix: float,
        equity: float,
        day_start_equity: float
    ) -> SystemState:
        """Build immutable SystemState snapshot for this loop.
        
        This is the single source of truth for "why no trades" answers.
        """
        import yaml
        import os
        
        # Get regime classification
        regime = classify_vix_regime(vix)
        regime_info = get_simple_regime_info()
        modifiers = regime_info.get("modifiers", {})
        
        # Compute growth multiplier
        growth_mult = compute_growth_multiplier(equity, regime)
        regime_size_mult = get_regime_size_multiplier(regime)
        effective_size = growth_mult * regime_size_mult
        
        # Get kill-switch state and evaluate current P&L
        killswitch = get_killswitch_service()
        
        # Compute P&L in R units
        daily_pnl = equity - day_start_equity
        daily_pnl_r = killswitch.pnl_to_r(daily_pnl, equity) if equity > 0 else 0.0
        
        # Evaluate kill-switch with current P&L (this actually triggers freeze if thresholds breached)
        ks_state = killswitch.evaluate(
            pnl_by_cluster={},
            total_pnl=daily_pnl,
            equity=equity
        )
        
        # Get max trades per day from regime
        max_trades = modifiers.get("max_new_trades_per_day", 2)
        
        # Get trades executed today
        trades_today = get_state("trades.today.count", 0)
        
        # Determine primary blocker
        primary_blocker = None
        if ks_state.is_global_frozen():
            primary_blocker = "GLOBAL_KILL_SWITCH"
        elif ks_state.frozen_clusters:
            primary_blocker = f"CLUSTER_FREEZE:{list(ks_state.frozen_clusters)[0]}"
        elif trades_today >= max_trades:
            primary_blocker = "MAX_TRADES_TODAY"
        elif regime == "STRESS" and vix > 30:
            primary_blocker = "EXTREME_VIX"
        
        # Get active earnings symbols (simplified - would need to track per-symbol)
        earnings_active = []  # TODO: Track per-symbol
        
        # Load sizing config for baseline equity
        try:
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "config", "sizing.yaml"
            )
            with open(config_path, "r") as f:
                sizing_config = yaml.safe_load(f)
            baseline_equity = sizing_config.get("baseline_equity", 5000)
        except Exception:
            baseline_equity = 5000
        
        return SystemState(
            timestamp=datetime.utcnow(),
            loop_id=loop_id,
            vix=vix,
            regime=regime,
            earnings_active_symbols=earnings_active,
            equity=equity,
            baseline_equity=baseline_equity,
            growth_multiplier=growth_mult,
            regime_size_multiplier=regime_size_mult,
            effective_size_multiplier=effective_size,
            global_freeze=ks_state.is_global_frozen(),
            frozen_clusters=killswitch.get_frozen_clusters_list(),
            daily_pnl=daily_pnl,
            daily_pnl_r=daily_pnl_r,
            trades_today=trades_today,
            max_new_trades_per_day=int(max_trades),
            primary_blocker=primary_blocker
        )
    
    def _log_system_state(self, state: SystemState) -> None:
        """Log SystemState to logs/system_state.jsonl."""
        import json
        import os
        
        logs_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "logs"
        )
        os.makedirs(logs_dir, exist_ok=True)
        
        log_path = os.path.join(logs_dir, "system_state.jsonl")
        
        try:
            with open(log_path, "a") as f:
                f.write(json.dumps(state.to_dict()) + "\n")
        except Exception as e:
            self._logger.error(f"Failed to log system state: {e}")

    def _apply_ml_risk_multiplier(self, risk_multiplier: float) -> None:
        """Apply ML-derived risk multiplier to bot budgets"""
        if abs(risk_multiplier - 1.0) < 0.01:
            return
        
        config = load_bots_config()
        bots_list = []
        
        for bot in config.get("momentum_bots", []):
            if bot.get("enabled"):
                bots_list.append(bot.get("bot_id"))
        
        if config.get("optionsbot", {}).get("enabled"):
            bots_list.append(config.get("optionsbot", {}).get("bot_id", "opt_core"))
        
        if config.get("cryptobot", {}).get("enabled"):
            bots_list.append(config.get("cryptobot", {}).get("bot_id", "crypto_main"))
        
        for bot_id in bots_list:
            budget_state = get_state(f"budgets.{bot_id}", {})
            current_budget = budget_state.get("max_daily_loss", 0) if isinstance(budget_state, dict) else 0
            if current_budget > 0:
                new_budget = current_budget * risk_multiplier
                budget_state["max_daily_loss"] = new_budget
                budget_state["max_open_risk"] = new_budget * 2
                set_state(f"budgets.{bot_id}", budget_state)
        
        self._logger.log("ml_risk_multiplier_applied", {
            "multiplier": risk_multiplier,
            "bots_adjusted": bots_list
        })
    
    def evaluate_entry_risk(
        self,
        symbol: str,
        bot_name: str,
        proposed_size_usd: float,
        is_bullish: bool = True
    ):
        """
        Evaluate entry through unified risk integration.
        
        All bots should call this before placing an entry order.
        Returns RiskEvaluation with action and size_multiplier.
        """
        if not self._risk_integration:
            return None
        
        try:
            vix = get_state("regime.vix", None)
            equity = get_state("account.equity", None)
            
            return self._risk_integration.evaluate_entry(
                symbol=symbol,
                bot_name=bot_name,
                proposed_size_usd=proposed_size_usd,
                is_bullish=is_bullish,
                vix=vix,
                equity=equity
            )
        except Exception as e:
            self._logger.error(f"Risk entry evaluation failed: {e}")
            return None
    
    def evaluate_exit_risk(
        self,
        symbol: str,
        bot_name: str,
        current_pnl_pct: float,
        position_qty: float,
        position_side: str = "long"
    ):
        """
        Evaluate exit through unified risk integration.
        
        ExitBot should call this to check for news-triggered exits.
        Returns RiskEvaluation with action (FORCE_EXIT if triggered).
        """
        if not self._risk_integration:
            return None
        
        try:
            return self._risk_integration.evaluate_exit(
                symbol=symbol,
                bot_name=bot_name,
                current_pnl_pct=current_pnl_pct,
                position_qty=position_qty,
                position_side=position_side
            )
        except Exception as e:
            self._logger.error(f"Risk exit evaluation failed: {e}")
            return None
    
    def record_trade_outcome(
        self,
        symbol: str,
        bot_name: str,
        return_pct: float,
        pnl_usd: float,
        is_loss: bool
    ):
        """Record trade outcome to risk integration for monitoring."""
        if self._risk_integration:
            try:
                self._risk_integration.record_trade_outcome(
                    symbol=symbol,
                    bot_name=bot_name,
                    return_pct=return_pct,
                    pnl_usd=pnl_usd,
                    is_loss=is_loss
                )
            except Exception as e:
                self._logger.error(f"Record trade outcome failed: {e}")
    
    def get_universe_boost(self, symbol: str) -> float:
        """Get smart money boost for universe scoring."""
        if self._risk_integration:
            try:
                return self._risk_integration.get_universe_boost(symbol)
            except:
                pass
        return 1.0


_orchestrator: Optional[TradingOrchestrator] = None


def get_orchestrator() -> TradingOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = TradingOrchestrator()
    return _orchestrator