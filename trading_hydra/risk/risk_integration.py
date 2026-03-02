"""
=============================================================================
Risk Orchestrator Integration
=============================================================================

Single point of contact for all risk gates and intelligence modules.
Provides unified interface for:
- Dynamic budget management
- Correlation-based risk reduction
- VIX/volatility regime monitoring
- News sentiment gating
- P&L distribution monitoring
- Market intelligence (news, smart money, macro)
- SPY intraday selloff detection (failsafe)

Safe defaults: ALL modules disabled by default for live safety.
All modules fail-closed.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum

from ..core.logging import get_logger
from ..core.config import load_settings, load_bots_config
from ..core.state import get_state, set_state


class RiskAction(Enum):
    """Recommended action from risk evaluation"""
    ALLOW = "allow"
    REDUCE_SIZE = "reduce_size"
    SKIP_ENTRY = "skip_entry"
    FORCE_EXIT = "force_exit"
    HALT_TRADING = "halt_trading"


@dataclass
class RiskEvaluation:
    """Unified result of risk evaluation"""
    action: RiskAction
    reason: str
    size_multiplier: float
    symbol: str
    evaluated_at: datetime
    
    budget_available: float = 0.0
    correlation_ok: bool = True
    vix_regime: str = "calm"
    vix_entries_allowed: bool = True
    news_sentiment: float = 0.0
    news_confidence: float = 0.0
    macro_regime: str = "NORMAL"
    macro_multiplier: float = 1.0
    
    gates_passed: Dict[str, bool] = field(default_factory=dict)
    gate_details: Dict[str, str] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "size_multiplier": round(self.size_multiplier, 3),
            "symbol": self.symbol,
            "evaluated_at": self.evaluated_at.isoformat(),
            "budget_available": round(self.budget_available, 2),
            "correlation_ok": self.correlation_ok,
            "vix_regime": self.vix_regime,
            "vix_entries_allowed": self.vix_entries_allowed,
            "news_sentiment": round(self.news_sentiment, 3),
            "news_confidence": round(self.news_confidence, 3),
            "macro_regime": self.macro_regime,
            "macro_multiplier": self.macro_multiplier,
            "gates_passed": self.gates_passed,
            "gate_details": self.gate_details
        }


class RiskOrchestratorIntegration:
    """
    Unified risk integration for the trading orchestrator.
    
    Consolidates all risk modules into a single evaluation interface:
    1. DynamicBudgetManager - equity/drawdown-scaled budgets
    2. CorrelationGuard - multi-loss detection and halt
    3. VolOfVolMonitor - VIX regime and entries gate
    4. NewsRiskGate - sentiment-based entry/exit gating
    5. PnLDistributionMonitor - fat-tail detection
    6. MacroIntelService - Fed/WH regime modifier
    7. SmartMoneyService - universe boost scoring
    
    All modules fail-closed: if unavailable, trading continues conservatively.
    All modules disabled by default.
    """
    
    def __init__(self):
        self._logger = get_logger()
        self._settings = load_settings()
        self._bots_config = load_bots_config()
        
        self._load_config()
        self._init_modules()
        
        self._logger.log("risk_integration_init", {
            "modules_enabled": {
                "budget": self._budget_enabled,
                "correlation": self._correlation_enabled,
                "vol_of_vol": self._vov_enabled,
                "news_gate": self._news_enabled,
                "pnl_monitor": self._pnl_enabled,
                "macro_intel": self._macro_enabled,
                "smart_money": self._smart_money_enabled,
                "selloff_detector": self._selloff_enabled,
                "opposing_options": self._opposing_enabled,
                "daily_loss_cap": self._dlc_enabled
            },
            "dry_run": self._dry_run
        })
    
    def _load_config(self):
        """Load configuration for all risk modules from settings.yaml risk_integration section"""
        risk_config = self._settings.get("risk_integration", {})
        intel_config = self._bots_config.get("intelligence", {})
        
        self._master_enabled = risk_config.get("enabled", True)
        self._dry_run = risk_config.get("dry_run", False)
        self._simulation_mode = intel_config.get("debug", {}).get("simulation_mode", False)
        
        if not self._master_enabled:
            self._budget_enabled = False
            self._correlation_enabled = False
            self._vov_enabled = False
            self._news_enabled = False
            self._pnl_enabled = False
            self._macro_enabled = False
            self._smart_money_enabled = False
            return
        
        self._budget_enabled = risk_config.get("budget_manager", {}).get("enabled", False)
        self._correlation_enabled = risk_config.get("correlation_guard", {}).get("enabled", False)
        self._vov_enabled = risk_config.get("vol_of_vol", {}).get("enabled", False)
        self._news_enabled = risk_config.get("news_gate", {}).get("enabled", False)
        self._pnl_enabled = risk_config.get("pnl_monitor", {}).get("enabled", False)
        self._macro_enabled = risk_config.get("macro_intel", {}).get("enabled", False)
        self._smart_money_enabled = risk_config.get("smart_money", {}).get("enabled", False)
        
        # SPY selloff detector — always enabled as a failsafe
        selloff_config = risk_config.get("selloff_detector", {})
        self._selloff_enabled = selloff_config.get("enabled", True)
        self._selloff_reduce_threshold = selloff_config.get("reduce_threshold_pct", 1.5)
        self._selloff_block_threshold = selloff_config.get("block_threshold_pct", 3.0)
        self._selloff_cache_seconds = selloff_config.get("cache_seconds", 60)
        self._selloff_last_check = None
        self._selloff_cached_result = None
        
        # Opposing options guard — prevents uncoordinated call+put on same underlying
        opposing_config = risk_config.get("opposing_options_guard", {})
        self._opposing_enabled = opposing_config.get("enabled", True)
        self._opposing_cache_seconds = opposing_config.get("cache_seconds", 30)
        self._opposing_exempt_strategies = set(opposing_config.get("exempt_strategies", [
            "iron_condor", "straddle", "strangle"
        ]))
        self._opposing_last_check = None
        self._opposing_cached_positions = None
        
        # Daily loss cap — halts all new entries if account drops >X% intraday
        exitbot_config = self._bots_config.get("exitbot", {})
        dlc_config = exitbot_config.get("daily_loss_cap", {})
        self._dlc_enabled = dlc_config.get("enabled", True)
        self._dlc_max_loss_pct = dlc_config.get("max_loss_pct", 2.0)
        self._dlc_cache_seconds = dlc_config.get("cache_seconds", 30)
        self._dlc_last_check = None
        self._dlc_cached_result = None
    
    def _init_modules(self):
        """Initialize risk modules lazily"""
        self._budget_manager = None
        self._correlation_guard = None
        self._vol_of_vol_monitor = None
        self._news_risk_gate = None
        self._pnl_monitor = None
        self._macro_intel = None
        self._smart_money = None
        self._news_intel = None
        self._sentiment_scorer = None
    
    def _get_budget_manager(self):
        """Lazy-load DynamicBudgetManager"""
        if self._budget_manager is None:
            try:
                from .dynamic_budget import get_dynamic_budget_manager
                self._budget_manager = get_dynamic_budget_manager()
            except Exception as e:
                self._logger.warn(f"[RiskIntegration] Budget manager unavailable: {e}")
        return self._budget_manager
    
    def _get_correlation_guard(self):
        """Lazy-load CorrelationGuard"""
        if self._correlation_guard is None:
            try:
                from .correlation_guard import get_correlation_guard
                self._correlation_guard = get_correlation_guard()
            except Exception as e:
                self._logger.warn(f"[RiskIntegration] Correlation guard unavailable: {e}")
        return self._correlation_guard
    
    def _get_vol_of_vol_monitor(self):
        """Lazy-load VolOfVolMonitor"""
        if self._vol_of_vol_monitor is None:
            try:
                from .vol_of_vol_monitor import get_vol_of_vol_monitor
                self._vol_of_vol_monitor = get_vol_of_vol_monitor()
            except Exception as e:
                self._logger.warn(f"[RiskIntegration] VolOfVol monitor unavailable: {e}")
        return self._vol_of_vol_monitor
    
    def _get_news_risk_gate(self):
        """Lazy-load NewsRiskGate"""
        if self._news_risk_gate is None:
            try:
                from .news_risk_gate import get_news_risk_gate
                self._news_risk_gate = get_news_risk_gate()
            except Exception as e:
                self._logger.warn(f"[RiskIntegration] News risk gate unavailable: {e}")
        return self._news_risk_gate
    
    def _get_pnl_monitor(self):
        """Lazy-load PnLDistributionMonitor"""
        if self._pnl_monitor is None:
            try:
                from .pnl_monitor import get_pnl_monitor
                self._pnl_monitor = get_pnl_monitor()
            except Exception as e:
                self._logger.warn(f"[RiskIntegration] PnL monitor unavailable: {e}")
        return self._pnl_monitor
    
    def _get_macro_intel(self):
        """Lazy-load MacroIntelService"""
        if self._macro_intel is None:
            try:
                from ..services.macro_intel_service import get_macro_intel_service
                self._macro_intel = get_macro_intel_service()
            except Exception as e:
                self._logger.warn(f"[RiskIntegration] Macro intel unavailable: {e}")
        return self._macro_intel
    
    def _get_smart_money(self):
        """Lazy-load SmartMoneyService"""
        if self._smart_money is None:
            try:
                from ..services.smart_money_service import get_smart_money_service
                self._smart_money = get_smart_money_service()
            except Exception as e:
                self._logger.warn(f"[RiskIntegration] Smart money service unavailable: {e}")
        return self._smart_money
    
    def _get_news_intel(self):
        """Lazy-load NewsIntelligenceService"""
        if self._news_intel is None:
            try:
                from ..services.news_intelligence import get_news_intelligence
                self._news_intel = get_news_intelligence()
            except Exception as e:
                self._logger.warn(f"[RiskIntegration] News intel unavailable: {e}")
        return self._news_intel
    
    def _get_sentiment_scorer(self):
        """Lazy-load SentimentScorerService"""
        if self._sentiment_scorer is None:
            try:
                from ..services.sentiment_scorer import get_sentiment_scorer
                self._sentiment_scorer = get_sentiment_scorer()
            except Exception as e:
                self._logger.warn(f"[RiskIntegration] Sentiment scorer unavailable: {e}")
        return self._sentiment_scorer
    
    def evaluate_entry(
        self,
        symbol: str,
        bot_name: str,
        proposed_size_usd: float,
        is_bullish: bool = True,
        vix: Optional[float] = None,
        equity: Optional[float] = None
    ) -> RiskEvaluation:
        """
        Evaluate whether an entry should be allowed.
        
        Checks all enabled risk gates in order:
        1. Budget availability
        2. Correlation guard (recent losses)
        3. VIX regime (crisis blocks entries)
        4. News sentiment
        5. Macro regime
        6. PnL distribution (halt check)
        
        Args:
            symbol: Trading symbol
            bot_name: Name of the bot requesting entry
            proposed_size_usd: Proposed position size in USD
            is_bullish: True for long/call, False for short/put
            vix: Current VIX value (optional, will fetch if not provided)
            equity: Current account equity (optional)
            
        Returns:
            RiskEvaluation with action and reasoning
        """
        now = datetime.utcnow()
        result = RiskEvaluation(
            action=RiskAction.ALLOW,
            reason="all_gates_passed",
            size_multiplier=1.0,
            symbol=symbol,
            evaluated_at=now,
            gates_passed={},
            gate_details={}
        )
        
        multipliers = []
        
        try:
            if self._budget_enabled:
                budget_result = self._check_budget(bot_name, proposed_size_usd, equity)
                result.gates_passed["budget"] = budget_result[0]
                result.gate_details["budget"] = budget_result[1]
                result.budget_available = budget_result[2]
                if not budget_result[0]:
                    result.action = RiskAction.SKIP_ENTRY
                    result.reason = f"insufficient_budget: {budget_result[1]}"
                    self._log_evaluation(result, "entry")
                    return result
                multipliers.append(budget_result[3])
            
            if self._correlation_enabled:
                corr_result = self._check_correlation(symbol, bot_name)
                result.gates_passed["correlation"] = corr_result[0]
                result.gate_details["correlation"] = corr_result[1]
                result.correlation_ok = corr_result[0]
                if not corr_result[0]:
                    if corr_result[2]:
                        result.action = RiskAction.HALT_TRADING
                        result.reason = f"correlation_halt: {corr_result[1]}"
                    else:
                        result.action = RiskAction.REDUCE_SIZE
                        result.reason = f"correlation_reduce: {corr_result[1]}"
                        multipliers.append(0.5)
                    if corr_result[2]:
                        self._log_evaluation(result, "entry")
                        return result
            
            if self._vov_enabled:
                vov_result = self._check_vix_regime(vix)
                result.gates_passed["vix_regime"] = vov_result[0]
                result.gate_details["vix_regime"] = vov_result[1]
                result.vix_regime = vov_result[2]
                result.vix_entries_allowed = vov_result[0]
                if not vov_result[0]:
                    result.action = RiskAction.SKIP_ENTRY
                    result.reason = f"vix_crisis: {vov_result[1]}"
                    self._log_evaluation(result, "entry")
                    return result
                multipliers.append(vov_result[3])
            
            if self._news_enabled:
                news_result = self._check_news_sentiment(symbol, is_bullish)
                result.gates_passed["news"] = news_result[0]
                result.gate_details["news"] = news_result[1]
                result.news_sentiment = news_result[2]
                result.news_confidence = news_result[3]
                if not news_result[0]:
                    result.action = RiskAction.SKIP_ENTRY
                    result.reason = f"news_block: {news_result[1]}"
                    self._log_evaluation(result, "entry")
                    return result
                multipliers.append(news_result[4])
            
            if self._macro_enabled:
                macro_result = self._check_macro_regime()
                result.gates_passed["macro"] = macro_result[0]
                result.gate_details["macro"] = macro_result[1]
                result.macro_regime = macro_result[2]
                result.macro_multiplier = macro_result[3]
                if not macro_result[0]:
                    result.action = RiskAction.SKIP_ENTRY
                    result.reason = f"macro_stress: {macro_result[1]}"
                    self._log_evaluation(result, "entry")
                    return result
                multipliers.append(macro_result[3])
            
            if self._pnl_enabled:
                pnl_result = self._check_pnl_halt()
                result.gates_passed["pnl"] = pnl_result[0]
                result.gate_details["pnl"] = pnl_result[1]
                if not pnl_result[0]:
                    result.action = RiskAction.HALT_TRADING
                    result.reason = f"pnl_halt: {pnl_result[1]}"
                    self._log_evaluation(result, "entry")
                    return result
            
            # SPY intraday selloff failsafe — protects ALL bots
            if self._selloff_enabled:
                selloff_result = self._check_selloff()
                result.gates_passed["selloff"] = selloff_result[0]
                result.gate_details["selloff"] = selloff_result[1]
                if not selloff_result[0]:
                    result.action = RiskAction.SKIP_ENTRY
                    result.reason = f"selloff_block: {selloff_result[1]}"
                    self._log_evaluation(result, "entry")
                    return result
                multipliers.append(selloff_result[2])
            
            # Opposing options guard — block uncoordinated call+put on same underlying
            if self._opposing_enabled:
                opposing_result = self._check_opposing_options(symbol, bot_name)
                result.gates_passed["opposing_options"] = opposing_result[0]
                result.gate_details["opposing_options"] = opposing_result[1]
                if not opposing_result[0]:
                    result.action = RiskAction.SKIP_ENTRY
                    result.reason = f"opposing_block: {opposing_result[1]}"
                    self._log_evaluation(result, "entry")
                    return result
            
            # Daily loss cap — halt all entries if account down >2% intraday
            if self._dlc_enabled:
                dlc_equity = equity
                if dlc_equity is None or dlc_equity <= 0:
                    try:
                        from ..services.alpaca_client import get_alpaca_client
                        acct = get_alpaca_client().get_account()
                        dlc_equity = float(acct.equity)
                    except Exception:
                        dlc_equity = None
                if dlc_equity and dlc_equity > 0:
                    dlc_result = self._check_daily_loss_cap(dlc_equity)
                    result.gates_passed["daily_loss_cap"] = dlc_result[0]
                    result.gate_details["daily_loss_cap"] = dlc_result[1]
                    if not dlc_result[0]:
                        result.action = RiskAction.HALT_TRADING
                        result.reason = f"daily_loss_cap: {dlc_result[1]}"
                        self._log_evaluation(result, "entry")
                        return result
            
            final_mult = 1.0
            for m in multipliers:
                final_mult *= m
            result.size_multiplier = max(0.1, min(1.0, final_mult))
            
            if result.size_multiplier < 1.0:
                result.action = RiskAction.REDUCE_SIZE
                result.reason = f"size_reduced_to_{result.size_multiplier:.2f}"
            
        except Exception as e:
            self._logger.error(f"[RiskIntegration] Entry evaluation error: {e}")
            result.action = RiskAction.ALLOW
            result.reason = "fail_closed_error"
            result.size_multiplier = 0.75
        
        self._log_evaluation(result, "entry")
        return result
    
    def evaluate_exit(
        self,
        symbol: str,
        bot_name: str,
        current_pnl_pct: float,
        position_qty: float,
        position_side: str = "long"
    ) -> RiskEvaluation:
        """
        Evaluate whether an exit should be forced based on news/risk.
        
        Checks:
        1. News sentiment (severe negative → force exit)
        2. Correlation guard (cluster losses → tighten stops)
        
        Args:
            symbol: Trading symbol
            bot_name: Name of the bot
            current_pnl_pct: Current P&L percentage
            position_qty: Position quantity
            position_side: "long" or "short"
            
        Returns:
            RiskEvaluation with action and reasoning
        """
        now = datetime.utcnow()
        result = RiskEvaluation(
            action=RiskAction.ALLOW,
            reason="no_exit_trigger",
            size_multiplier=1.0,
            symbol=symbol,
            evaluated_at=now,
            gates_passed={},
            gate_details={}
        )
        
        try:
            if self._news_enabled:
                exit_result = self._check_news_exit(symbol, current_pnl_pct)
                result.gates_passed["news_exit"] = not exit_result[0]
                result.gate_details["news_exit"] = exit_result[1]
                result.news_sentiment = exit_result[2]
                result.news_confidence = exit_result[3]
                if exit_result[0]:
                    result.action = RiskAction.FORCE_EXIT
                    result.reason = f"news_exit: {exit_result[1]}"
                    self._log_evaluation(result, "exit")
                    return result
            
        except Exception as e:
            self._logger.error(f"[RiskIntegration] Exit evaluation error: {e}")
        
        self._log_evaluation(result, "exit")
        return result
    
    def record_trade_outcome(
        self,
        symbol: str,
        bot_name: str,
        return_pct: float,
        pnl_usd: float,
        is_loss: bool
    ):
        """
        Record a trade outcome for risk monitoring.
        
        Updates:
        - CorrelationGuard with loss if applicable
        - PnLDistributionMonitor with trade return
        """
        try:
            if is_loss and self._correlation_enabled:
                guard = self._get_correlation_guard()
                if guard:
                    guard.record_loss(symbol, bot_name, abs(pnl_usd), abs(return_pct))
            
            if self._pnl_enabled:
                monitor = self._get_pnl_monitor()
                if monitor:
                    monitor.record_trade(symbol, bot_name, return_pct, pnl_usd)
                    
        except Exception as e:
            self._logger.error(f"[RiskIntegration] Record trade error: {e}")
    
    def get_universe_boost(self, symbol: str) -> float:
        """
        Get smart money universe boost for a symbol.
        
        Returns:
            Multiplier >= 1.0 (1.0 = no boost)
        """
        if not self._smart_money_enabled:
            return 1.0
        
        try:
            service = self._get_smart_money()
            if service:
                return service.get_boost_for_symbol(symbol)
        except Exception as e:
            self._logger.warn(f"[RiskIntegration] Smart money boost error: {e}")
        
        return 1.0
    
    def update_vix(self, vix_value: float):
        """Update VIX for VolOfVolMonitor"""
        if self._vov_enabled:
            try:
                monitor = self._get_vol_of_vol_monitor()
                if monitor:
                    monitor.update(vix_value)
            except Exception as e:
                self._logger.warn(f"[RiskIntegration] VIX update error: {e}")
    
    def update_equity(self, equity: float, peak_equity: Optional[float] = None):
        """Update equity for DynamicBudgetManager"""
        if self._budget_enabled:
            try:
                manager = self._get_budget_manager()
                if manager:
                    manager.update_high_water_mark(equity)
            except Exception as e:
                self._logger.warn(f"[RiskIntegration] Equity update error: {e}")
    
    def _check_budget(self, bot_name: str, size_usd: float, equity: Optional[float]) -> Tuple[bool, str, float, float]:
        """Check budget availability"""
        manager = self._get_budget_manager()
        if not manager:
            return True, "budget_manager_unavailable", 999999.0, 1.0
        
        current_equity = equity or 0.0
        
        # If equity is missing or zero, try to fetch from Alpaca directly
        if current_equity <= 0:
            try:
                from ..services.alpaca_client import get_alpaca_client
                client = get_alpaca_client()
                acct = client.get_account()
                if acct and hasattr(acct, 'equity') and float(acct.equity) > 0:
                    current_equity = float(acct.equity)
                    self._logger.log("budget_equity_fetched_from_alpaca", {
                        "bot_name": bot_name,
                        "equity": round(current_equity, 2)
                    })
            except Exception as e:
                self._logger.warn(f"[BudgetSafety] Failed to fetch equity from Alpaca: {e}")
        
        # If still zero but HWM exists, use HWM as conservative estimate
        high_water_mark = manager.get_high_water_mark()
        if current_equity <= 0 and high_water_mark > 0:
            current_equity = high_water_mark * 0.90
            self._logger.warn(f"[BudgetSafety] Using 90% of HWM ({high_water_mark}) as equity estimate: {current_equity}")
        
        if high_water_mark <= 0:
            high_water_mark = current_equity
        
        budget = manager.calculate_budget(bot_name, current_equity, high_water_mark)
        self._logger.log("budget_check_detail", {
            "bot_name": bot_name,
            "equity_used": round(current_equity, 2),
            "hwm": round(high_water_mark, 2),
            "daily_budget_usd": round(budget.daily_budget_usd, 2),
            "max_position_usd": round(budget.max_position_usd, 2),
            "reason": budget.reason
        })
        available = budget.daily_budget_usd
        if available < 500 and current_equity > 5000:
            self._logger.warn(f"[BudgetSafety] Budget {available} suspiciously low for equity {current_equity}. Overriding to 50% of equity.")
            available = current_equity * 0.50
        multiplier = budget.drawdown_multiplier
        
        if size_usd > available:
            self._logger.log("risk_budget_check_failed", {
                "bot_name": bot_name,
                "size_usd": round(size_usd, 2),
                "available_usd": round(available, 2),
                "drawdown_multiplier": round(multiplier, 3)
            })
            return False, f"need_{size_usd:.0f}_have_{available:.0f}", available, multiplier
        
        self._logger.log("risk_budget_check_passed", {
            "bot_name": bot_name,
            "size_usd": round(size_usd, 2),
            "available_usd": round(available, 2),
            "drawdown_multiplier": round(multiplier, 3),
            "equity": round(current_equity, 2)
        })
        return True, f"budget_ok_{available:.0f}", available, multiplier
    
    def _check_correlation(self, symbol: str, bot_name: str) -> Tuple[bool, str, bool]:
        """Check correlation guard status. Returns (ok, reason, is_halt)"""
        guard = self._get_correlation_guard()
        if not guard:
            return True, "correlation_guard_unavailable", False
        
        state = guard.get_state()
        if state.risk_level == "halted":
            return False, state.reason or "correlation_halt", True
        
        if state.is_active():
            return False, f"reduction_{state.risk_multiplier*100:.0f}pct", False
        
        return True, "correlation_ok", False
    
    def _check_vix_regime(self, vix: Optional[float]) -> Tuple[bool, str, str, float]:
        """Check VIX regime. Returns (entries_allowed, reason, regime, multiplier)"""
        monitor = self._get_vol_of_vol_monitor()
        if not monitor:
            return True, "vov_monitor_unavailable", "unknown", 1.0
        
        if vix:
            state = monitor.update(vix)
        else:
            state = monitor.get_state()
            if state is None:
                return True, "no_vix_data", "unknown", 1.0
        
        regime = state.regime
        multiplier = state.risk_multiplier
        entries_ok = state.entries_allowed
        
        if not entries_ok:
            return False, f"vix_crisis_{regime}_blocks_entries", regime, multiplier
        
        return True, f"vix_{regime}", regime, multiplier
    
    def _check_news_sentiment(self, symbol: str, is_bullish: bool) -> Tuple[bool, str, float, float, float]:
        """Check news sentiment for entry. Returns (ok, reason, sentiment, confidence, multiplier)"""
        gate = self._get_news_risk_gate()
        news_intel = self._get_news_intel()
        scorer = self._get_sentiment_scorer()
        
        if not gate or not news_intel or not scorer:
            return True, "news_services_unavailable", 0.0, 0.0, 1.0
        
        try:
            news_items = news_intel.get_news_for_symbol(symbol)
            if not news_items:
                return True, "no_news", 0.0, 0.0, 1.0
            
            sentiment = scorer.score_news(news_items)
            cache_entry = news_intel.get_cache_status(symbol)
            cache_age = cache_entry.age_seconds() if cache_entry else None
            
            result = gate.evaluate_entry(
                symbol=symbol,
                sentiment_score=sentiment.sentiment_score,
                confidence=sentiment.confidence,
                is_bullish_trade=is_bullish,
                cache_age_seconds=cache_age
            )
            
            from .news_risk_gate import NewsAction
            if result.action == NewsAction.SKIP_ENTRY:
                return False, result.reason, sentiment.sentiment_score, sentiment.confidence, 0.0
            
            return True, result.reason, sentiment.sentiment_score, sentiment.confidence, result.size_multiplier
            
        except Exception as e:
            self._logger.warn(f"[RiskIntegration] News check error: {e}")
            return True, "news_check_error_fail_closed", 0.0, 0.0, 1.0
    
    def _check_news_exit(self, symbol: str, current_pnl_pct: float) -> Tuple[bool, str, float, float]:
        """Check if news triggers exit. Returns (should_exit, reason, sentiment, confidence)"""
        gate = self._get_news_risk_gate()
        news_intel = self._get_news_intel()
        scorer = self._get_sentiment_scorer()
        
        if not gate or not news_intel or not scorer:
            return False, "news_services_unavailable", 0.0, 0.0
        
        try:
            news_items = news_intel.get_news_for_symbol(symbol)
            if not news_items:
                return False, "no_news", 0.0, 0.0
            
            sentiment = scorer.score_news(news_items)
            cache_entry = news_intel.get_cache_status(symbol)
            cache_age = cache_entry.age_seconds() if cache_entry else None
            
            result = gate.evaluate_exit(
                symbol=symbol,
                sentiment_score=sentiment.sentiment_score,
                confidence=sentiment.confidence,
                current_pnl_pct=current_pnl_pct,
                cache_age_seconds=cache_age
            )
            
            from .news_risk_gate import NewsAction
            if result.action == NewsAction.FORCE_EXIT:
                return True, result.reason, sentiment.sentiment_score, sentiment.confidence
            
            return False, result.reason, sentiment.sentiment_score, sentiment.confidence
            
        except Exception as e:
            self._logger.warn(f"[RiskIntegration] News exit check error: {e}")
            return False, "news_check_error_fail_closed", 0.0, 0.0
    
    def _check_macro_regime(self) -> Tuple[bool, str, str, float]:
        """Check macro regime. Returns (entries_ok, reason, regime, multiplier)"""
        service = self._get_macro_intel()
        if not service:
            return True, "macro_service_unavailable", "NORMAL", 1.0
        
        try:
            intel = service.get_macro_intel()
            regime = intel.regime_modifier.value
            multiplier = service.get_size_multiplier()
            
            if regime == "STRESS":
                return False, f"macro_stress_{intel.reason_short[:50]}", regime, multiplier
            
            return True, f"macro_{regime}", regime, multiplier
            
        except Exception as e:
            self._logger.warn(f"[RiskIntegration] Macro check error: {e}")
            return True, "macro_check_error", "NORMAL", 1.0
    
    def _check_pnl_halt(self) -> Tuple[bool, str]:
        """Check if PnL monitor has triggered a halt. Returns (ok, reason)"""
        monitor = self._get_pnl_monitor()
        if not monitor:
            return True, "pnl_monitor_unavailable"
        
        if monitor.is_halted():
            return False, monitor.get_state().halt_reason or "pnl_halt_triggered"
        
        return True, "pnl_ok"
    
    def _check_selloff(self) -> Tuple[bool, str, float]:
        """
        Check for intraday SPY selloff as a failsafe market protection gate.
        Uses Alpaca quote data cached for 60s to avoid excessive API calls.
        Returns (entries_ok, reason, size_multiplier)
        """
        import time as _time
        now = _time.time()
        
        # Return cached result if fresh
        if (self._selloff_last_check is not None and 
            self._selloff_cached_result is not None and
            now - self._selloff_last_check < self._selloff_cache_seconds):
            return self._selloff_cached_result
        
        try:
            # Try getting SPY change from state (set by sensors/orchestrator)
            spy_change_pct = get_state("market.spy_intraday_change_pct", None)
            
            if spy_change_pct is None:
                # Fallback: fetch SPY quote from Alpaca
                try:
                    from ..services.alpaca_client import get_alpaca_client
                    client = get_alpaca_client()
                    quote = client.get_latest_quote("SPY")
                    if quote and hasattr(quote, 'ask_price') and hasattr(quote, 'bid_price'):
                        mid = (float(quote.ask_price) + float(quote.bid_price)) / 2
                        # Get previous close from state or use open
                        prev_close = get_state("market.spy_prev_close", None)
                        if prev_close and prev_close > 0:
                            spy_change_pct = ((mid - prev_close) / prev_close) * 100
                        else:
                            # Store this as reference and skip this cycle
                            set_state("market.spy_prev_close", mid)
                            result = (True, "selloff_initializing", 1.0)
                            self._selloff_cached_result = result
                            self._selloff_last_check = now
                            return result
                except Exception as quote_err:
                    self._logger.warn(f"[RiskIntegration] SPY quote fetch failed: {quote_err}")
                    result = (True, "selloff_data_unavailable", 1.0)
                    self._selloff_cached_result = result
                    self._selloff_last_check = now
                    return result
            
            if spy_change_pct is None:
                result = (True, "selloff_no_data", 1.0)
                self._selloff_cached_result = result
                self._selloff_last_check = now
                return result
            
            spy_drop = -spy_change_pct  # Positive when SPY is down
            
            if spy_drop >= self._selloff_block_threshold:
                # Severe selloff (>3% drop) — block new entries
                self._logger.log("selloff_block_triggered", {
                    "spy_change_pct": round(spy_change_pct, 2),
                    "threshold": self._selloff_block_threshold,
                    "action": "skip_entry"
                })
                result = (False, f"selloff_severe_SPY_{spy_change_pct:+.1f}%", 0.0)
            elif spy_drop >= self._selloff_reduce_threshold:
                # Moderate selloff (>1.5% drop) — reduce position size
                severity = min(1.0, spy_drop / self._selloff_block_threshold)
                size_mult = max(0.25, 1.0 - severity)
                self._logger.log("selloff_reduce_triggered", {
                    "spy_change_pct": round(spy_change_pct, 2),
                    "threshold": self._selloff_reduce_threshold,
                    "size_multiplier": round(size_mult, 2),
                    "action": "reduce_size"
                })
                result = (True, f"selloff_moderate_SPY_{spy_change_pct:+.1f}%", size_mult)
            else:
                result = (True, f"selloff_ok_SPY_{spy_change_pct:+.1f}%", 1.0)
            
            self._selloff_cached_result = result
            self._selloff_last_check = now
            return result
            
        except Exception as e:
            self._logger.warn(f"[RiskIntegration] Selloff check error: {e}")
            result = (True, "selloff_check_error", 1.0)
            self._selloff_cached_result = result
            self._selloff_last_check = now
            return result
    
    def _check_opposing_options(self, symbol: str, bot_name: str) -> Tuple[bool, str]:
        """
        Check if the proposed option trade conflicts with an existing position
        on the same underlying in the opposite direction.
        
        Prevents: buying a call when already holding a put (or vice versa) on the
        same underlying, unless the trade is part of a coordinated multi-leg strategy
        (iron condor, straddle, strangle).
        
        Returns (entries_ok, reason_string)
        """
        import time as _time
        from ..utils.ticker_classifier import parse_option_symbol
        
        # Only applies to option symbols — equities/crypto pass through
        proposed = parse_option_symbol(symbol)
        if proposed is None:
            return (True, "opposing_ok_not_option")
        
        proposed_underlying = proposed.underlying
        proposed_type = proposed.option_type  # "call" or "put"
        
        try:
            # Fetch positions with caching
            now = _time.time()
            if (self._opposing_last_check is not None and
                self._opposing_cached_positions is not None and
                now - self._opposing_last_check < self._opposing_cache_seconds):
                positions = self._opposing_cached_positions
            else:
                from ..services.alpaca_client import get_alpaca_client
                client = get_alpaca_client()
                positions = client.get_positions() or []
                self._opposing_cached_positions = positions
                self._opposing_last_check = now
            
            # Scan held positions for opposing options on the same underlying
            for pos in positions:
                pos_symbol = pos.symbol if hasattr(pos, 'symbol') else pos.get('symbol', '')
                held = parse_option_symbol(pos_symbol)
                if held is None:
                    continue
                
                if held.underlying != proposed_underlying:
                    continue
                
                # Same underlying — check if opposing direction
                if held.option_type != proposed_type:
                    # Found opposing option on same underlying
                    held_qty = pos.qty if hasattr(pos, 'qty') else pos.get('qty', '?')
                    self._logger.log("opposing_options_blocked", {
                        "proposed_symbol": symbol,
                        "proposed_type": proposed_type,
                        "existing_symbol": pos_symbol,
                        "existing_type": held.option_type,
                        "underlying": proposed_underlying,
                        "existing_qty": str(held_qty),
                        "action": "skip_entry"
                    })
                    return (False, f"opposing_{proposed_underlying}_already_holds_{held.option_type}")
            
            return (True, f"opposing_ok_{proposed_underlying}")
            
        except Exception as e:
            self._logger.warn(f"[RiskIntegration] Opposing options check error: {e}")
            return (True, "opposing_check_error")
    
    def _check_daily_loss_cap(self, equity: float) -> Tuple[bool, str]:
        """
        Check if the account has exceeded its daily loss cap.
        Reads day_start equity from state DB and compares to current equity.
        Returns (entries_ok, reason_string)
        """
        import time as _time
        
        now = _time.time()
        if (self._dlc_last_check is not None and
            self._dlc_cached_result is not None and
            now - self._dlc_last_check < self._dlc_cache_seconds):
            return self._dlc_cached_result
        
        try:
            from ..core.state import get_state, set_state
            
            day_start_equity = get_state("day_start_equity")
            if day_start_equity is None or float(day_start_equity) <= 0:
                if equity > 0:
                    set_state("day_start_equity", equity)
                result = (True, "dlc_initializing")
                self._dlc_cached_result = result
                self._dlc_last_check = now
                return result
            
            day_start = float(day_start_equity)
            day_pnl_pct = ((equity - day_start) / day_start) * 100.0
            
            if day_pnl_pct <= -self._dlc_max_loss_pct:
                self._logger.log("daily_loss_cap_triggered", {
                    "day_start_equity": round(day_start, 2),
                    "current_equity": round(equity, 2),
                    "day_pnl_pct": round(day_pnl_pct, 2),
                    "cap_pct": self._dlc_max_loss_pct,
                    "action": "halt_trading"
                })
                result = (False, f"daily_loss_{day_pnl_pct:+.1f}%_exceeds_{self._dlc_max_loss_pct}%_cap")
            else:
                result = (True, f"dlc_ok_{day_pnl_pct:+.1f}%")
            
            self._dlc_cached_result = result
            self._dlc_last_check = now
            return result
            
        except Exception as e:
            self._logger.warn(f"[RiskIntegration] Daily loss cap check error: {e}")
            result = (True, "dlc_check_error")
            self._dlc_cached_result = result
            self._dlc_last_check = now
            return result
    
    def _log_evaluation(self, result: RiskEvaluation, context: str):
        """Log evaluation for audit"""
        self._logger.log(f"risk_integration_{context}", result.to_dict())
    
    def get_status_summary(self) -> Dict[str, Any]:
        """Get summary of all risk module statuses"""
        summary = {
            "dry_run": self._dry_run,
            "modules": {}
        }
        
        if self._budget_enabled:
            try:
                manager = self._get_budget_manager()
                if manager:
                    summary["modules"]["budget"] = {"enabled": True, "status": "active"}
            except:
                summary["modules"]["budget"] = {"enabled": True, "status": "error"}
        
        if self._correlation_enabled:
            try:
                guard = self._get_correlation_guard()
                if guard:
                    state = guard.get_state()
                    summary["modules"]["correlation"] = {
                        "enabled": True,
                        "halted": state.risk_level == "halted",
                        "reduction_active": state.is_active()
                    }
            except Exception as e:
                self._logger.warn(f"[RiskIntegration] Correlation status check failed: {e}")
                summary["modules"]["correlation"] = {"enabled": True, "status": "error"}
        
        if self._vov_enabled:
            try:
                monitor = self._get_vol_of_vol_monitor()
                if monitor:
                    state = monitor.get_state()
                    summary["modules"]["vol_of_vol"] = {
                        "enabled": True,
                        "regime": state.regime if state else "unknown"
                    }
            except Exception as e:
                self._logger.warn(f"[RiskIntegration] VolOfVol status check failed: {e}")
                summary["modules"]["vol_of_vol"] = {"enabled": True, "status": "error"}
        
        if self._macro_enabled:
            try:
                service = self._get_macro_intel()
                if service:
                    intel = service.get_macro_intel()
                    summary["modules"]["macro"] = {
                        "enabled": True,
                        "regime": intel.regime_modifier.value
                    }
            except:
                summary["modules"]["macro"] = {"enabled": True, "status": "error"}
        
        return summary


_risk_integration: Optional[RiskOrchestratorIntegration] = None


def get_risk_integration() -> RiskOrchestratorIntegration:
    """Get or create RiskOrchestratorIntegration singleton"""
    global _risk_integration
    if _risk_integration is None:
        _risk_integration = RiskOrchestratorIntegration()
    return _risk_integration


def reset_risk_integration():
    """Reset singleton for testing"""
    global _risk_integration
    _risk_integration = None
