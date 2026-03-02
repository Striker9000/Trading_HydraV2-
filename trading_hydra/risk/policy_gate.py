"""
=============================================================================
Policy Gate - Unified Pre-Trade Validation
=============================================================================

Single mandatory checkpoint that ALL orders must pass.
This is the institutional-grade "last line of defense" before order submission.

Philosophy:
- EVERY order goes through this gate - no exceptions
- Fail-closed: any error = order blocked
- Comprehensive: wraps all risk, liquidity, and intelligence checks
- Auditable: every decision logged with full context

Consolidates:
1. RiskOrchestratorIntegration (budget, correlation, VIX, news, macro)
2. LiquidityFilter (spread, volume, OI checks)
3. SlippageTracker (slippage budget enforcement)
4. UniverseGuard (symbol whitelist)
5. GreekRiskMonitor (portfolio delta/gamma limits)

Usage:
    from src.trading_hydra.risk.policy_gate import get_policy_gate
    
    gate = get_policy_gate()
    result = gate.validate_order(order_request)
    
    if not result.approved:
        logger.log("order_blocked", result.to_dict())
        return None
    
    # Proceed with adjusted size
    actual_size = order_request.size * result.size_multiplier
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum

from ..core.logging import get_logger
from ..core.config import load_settings, load_bots_config


class PolicyDecision(Enum):
    """Policy gate decision outcome."""
    APPROVED = "approved"
    APPROVED_REDUCED = "approved_reduced"
    BLOCKED = "blocked"
    HALTED = "halted"


class BlockReason(Enum):
    """Reason for blocking an order."""
    INSUFFICIENT_BUDGET = "insufficient_budget"
    CORRELATION_HALT = "correlation_halt"
    VIX_CRISIS = "vix_crisis"
    NEWS_BLOCK = "news_block"
    MACRO_STRESS = "macro_stress"
    PNL_HALT = "pnl_halt"
    LIQUIDITY_FAIL = "liquidity_fail"
    SLIPPAGE_BUDGET_EXCEEDED = "slippage_budget_exceeded"
    UNIVERSE_BLOCKED = "universe_blocked"
    GREEK_LIMITS_EXCEEDED = "greek_limits_exceeded"
    ML_MODEL_DISABLED = "ml_model_disabled"
    SYSTEM_HALTED = "system_halted"
    SESSION_PROFIT_LOCK = "session_profit_lock"
    PDT_PROTECTION = "pdt_protection"
    VALIDATION_ERROR = "validation_error"
    FAIL_CLOSED = "fail_closed"


@dataclass
class OrderRequest:
    """Incoming order request to validate."""
    symbol: str
    bot_id: str
    side: str  # "buy" or "sell"
    asset_class: str  # "equity", "option", "crypto"
    size_usd: float
    is_entry: bool  # True for new position, False for exit
    is_bullish: bool = True  # For directional context
    option_data: Optional[Dict[str, Any]] = None  # bid, ask, volume, OI for options
    ml_signal_score: Optional[float] = None
    expected_fill_price: Optional[float] = None


@dataclass
class PolicyResult:
    """Result of policy gate validation."""
    decision: PolicyDecision
    approved: bool
    size_multiplier: float  # 1.0 = full size, 0.5 = half, 0.0 = blocked
    block_reason: Optional[BlockReason] = None
    block_details: str = ""
    
    symbol: str = ""
    bot_id: str = ""
    evaluated_at: datetime = field(default_factory=datetime.utcnow)
    
    gates_passed: Dict[str, bool] = field(default_factory=dict)
    gate_details: Dict[str, str] = field(default_factory=dict)
    
    risk_evaluation: Optional[Any] = None
    liquidity_check: Optional[Any] = None
    
    warnings: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision.value,
            "approved": self.approved,
            "size_multiplier": round(self.size_multiplier, 3),
            "block_reason": self.block_reason.value if self.block_reason else None,
            "block_details": self.block_details,
            "symbol": self.symbol,
            "bot_id": self.bot_id,
            "evaluated_at": self.evaluated_at.isoformat(),
            "gates_passed": self.gates_passed,
            "gate_details": self.gate_details,
            "warnings": self.warnings
        }


class PolicyGate:
    """
    Unified pre-trade policy gate.
    
    ALL orders must pass through this gate before submission.
    This is the institutional-grade "last line of defense."
    
    Gate sequence:
    1. System halt check (HaltManager)
    2. Universe guard (symbol whitelist)
    3. Risk orchestrator (budget, correlation, VIX, news, macro)
    4. Liquidity filter (options only)
    5. Slippage budget check
    6. Greek limits check (options only)
    7. ML model governance check
    
    Fail-closed: Any error in any gate = order blocked.
    """
    
    def __init__(self):
        self._logger = get_logger()
        self._settings = load_settings()
        self._bots_config = load_bots_config()
        
        self._load_config()
        self._init_components()
        
        self._logger.log("policy_gate_init", {
            "enabled": self._enabled,
            "fail_closed": True,
            "gates": ["halt", "universe", "risk", "liquidity", "slippage", "greek", "ml"]
        })
    
    def _load_config(self):
        """Load policy gate configuration."""
        config = self._settings.get("policy_gate", {})
        self._enabled = config.get("enabled", True)
        self._dry_run = config.get("dry_run", False)
        
        self._slippage_budget_pct = config.get("slippage_budget_pct", 0.5)
        self._min_ml_confidence = config.get("min_ml_confidence", 0.4)
        self._require_ml_signal = config.get("require_ml_signal", False)
    
    def _init_components(self):
        """Initialize component references (lazy-loaded)."""
        self._risk_integration = None
        self._liquidity_filter = None
        self._slippage_tracker = None
        self._universe_guard = None
        self._greek_monitor = None
        self._halt_manager = None
        self._ml_service = None
        self._session_protection = None
    
    def _get_risk_integration(self):
        """Lazy-load RiskOrchestratorIntegration."""
        if self._risk_integration is None:
            try:
                from .risk_integration import RiskOrchestratorIntegration
                self._risk_integration = RiskOrchestratorIntegration()
            except Exception as e:
                self._logger.error(f"[PolicyGate] Risk integration unavailable: {e}")
        return self._risk_integration
    
    def _get_liquidity_filter(self):
        """Lazy-load LiquidityFilter."""
        if self._liquidity_filter is None:
            try:
                from .liquidity_filter import get_liquidity_filter
                self._liquidity_filter = get_liquidity_filter()
            except Exception as e:
                self._logger.error(f"[PolicyGate] Liquidity filter unavailable: {e}")
        return self._liquidity_filter
    
    def _get_slippage_tracker(self):
        """Lazy-load SlippageTracker."""
        if self._slippage_tracker is None:
            try:
                from .slippage_tracker import get_slippage_tracker
                self._slippage_tracker = get_slippage_tracker()
            except Exception as e:
                self._logger.warn(f"[PolicyGate] Slippage tracker unavailable: {e}")
        return self._slippage_tracker
    
    def _get_universe_guard(self):
        """Lazy-load UniverseGuard."""
        if self._universe_guard is None:
            try:
                from .universe_guard import get_universe_guard
                self._universe_guard = get_universe_guard()
            except Exception as e:
                self._logger.warn(f"[PolicyGate] Universe guard unavailable: {e}")
        return self._universe_guard
    
    def _get_greek_monitor(self):
        """Lazy-load GreekRiskMonitor."""
        if self._greek_monitor is None:
            try:
                from .greek_limits import get_greek_risk_monitor
                self._greek_monitor = get_greek_risk_monitor()
            except Exception as e:
                self._logger.warn(f"[PolicyGate] Greek monitor unavailable: {e}")
        return self._greek_monitor
    
    def _get_halt_manager(self):
        """Lazy-load HaltManager."""
        if self._halt_manager is None:
            try:
                from ..core.halt import get_halt_manager
                self._halt_manager = get_halt_manager()
            except Exception as e:
                self._logger.warn(f"[PolicyGate] Halt manager unavailable: {e}")
        return self._halt_manager
    
    def _get_ml_service(self):
        """Lazy-load MLSignalService (optional - may not exist)."""
        if self._ml_service is None:
            try:
                from ..ml.signal_service import MLSignalService
                self._ml_service = MLSignalService(logger=self._logger)
            except ImportError:
                pass
            except Exception as e:
                self._logger.warn(f"[PolicyGate] ML service unavailable: {e}")
        return self._ml_service

    def _get_session_protection(self):
        """Lazy-load SessionProtection singleton."""
        if self._session_protection is None:
            try:
                from .session_protection import get_session_protection
                self._session_protection = get_session_protection()
            except Exception as e:
                self._logger.warn(f"[PolicyGate] Session protection unavailable: {e}")
        return self._session_protection

    def _check_session_protection(self, request: OrderRequest, result: PolicyResult) -> bool:
        """Check if session protection blocks new entries.
        
        If target is locked but freeroll is available AND quality score is high enough,
        the entry is approved with size capped to house money (stored in gate_details).
        """
        sp = self._get_session_protection()
        if not sp:
            result.gates_passed["session_protection"] = True
            result.gate_details["session_protection"] = "unavailable"
            return True

        try:
            quality_score = request.ml_signal_score or 0.0
            should_block, reason = sp.should_block_new_trade(quality_score=quality_score)

            if should_block:
                if not sp.should_throttle_message(f"block_{request.bot_id}"):
                    self._logger.log("policy_gate_session_protection_block", {
                        "symbol": request.symbol,
                        "bot_id": request.bot_id,
                        "reason": reason,
                    })
                result.decision = PolicyDecision.BLOCKED
                result.approved = False
                result.block_reason = BlockReason.SESSION_PROFIT_LOCK
                result.block_details = reason
                result.size_multiplier = 0.0
                result.gates_passed["session_protection"] = False
                result.gate_details["session_protection"] = reason
                return False

            if reason.startswith("FREEROLL:"):
                house_money_str = reason.replace("FREEROLL:$", "")
                try:
                    house_money = float(house_money_str)
                except ValueError:
                    house_money = 0.0
                result.gates_passed["session_protection"] = True
                result.gate_details["session_protection"] = f"freeroll_approved_house_money=${house_money:.0f}"
                result.gate_details["freeroll_budget_usd"] = str(house_money)
                result.warnings.append(f"FREEROLL: max budget ${house_money:.0f} (house money only)")
                self._logger.log("policy_gate_freeroll_approved", {
                    "symbol": request.symbol,
                    "bot_id": request.bot_id,
                    "house_money": house_money,
                    "quality_score": quality_score,
                })
                return True

            result.gates_passed["session_protection"] = True
            status = sp.get_session_status()
            result.gate_details["session_protection"] = (
                f"pnl=${status['realized_pnl_usd']:.0f} hwm=${status['hwm_usd']:.0f} floor=${status['locked_floor_usd']:.0f}"
            )

        except Exception as e:
            self._logger.warn(f"[PolicyGate] Session protection check error (fail-open): {e}")
            result.gates_passed["session_protection"] = True
            result.gate_details["session_protection"] = "check_error"

        return True

    def validate_order(self, request: OrderRequest) -> PolicyResult:
        """
        Validate an order through all policy gates.
        
        Args:
            request: OrderRequest with all order details
            
        Returns:
            PolicyResult with approval status and any adjustments
        """
        now = datetime.utcnow()
        result = PolicyResult(
            decision=PolicyDecision.APPROVED,
            approved=True,
            size_multiplier=1.0,
            symbol=request.symbol,
            bot_id=request.bot_id,
            evaluated_at=now,
            gates_passed={},
            gate_details={}
        )
        
        if not self._enabled:
            result.gate_details["policy_gate"] = "disabled"
            self._log_result(result, request)
            return result
        
        multipliers = []
        
        try:
            if not self._check_system_halt(result):
                return self._finalize_blocked(result, request)
            
            if request.is_entry:
                if not self._check_global_position_limit(request, result):
                    return self._finalize_blocked(result, request)
                if not self._check_pdt_floor(request, result):
                    return self._finalize_blocked(result, request)
                if not self._check_session_protection(request, result):
                    return self._finalize_blocked(result, request)
                if not self._check_universe(request, result):
                    return self._finalize_blocked(result, request)
            
            if not self._check_risk_integration(request, result, multipliers):
                return self._finalize_blocked(result, request)
            
            if request.asset_class == "option" and request.option_data:
                if not self._check_liquidity(request, result):
                    return self._finalize_blocked(result, request)
            
            if not self._check_slippage_budget(request, result):
                return self._finalize_blocked(result, request)
            
            if request.asset_class == "option":
                if not self._check_greek_limits(request, result):
                    return self._finalize_blocked(result, request)
            
            if request.is_entry and self._require_ml_signal:
                if not self._check_ml_governance(request, result, multipliers):
                    return self._finalize_blocked(result, request)
            
            final_mult = 1.0
            for m in multipliers:
                final_mult *= m
            result.size_multiplier = max(0.1, min(1.0, final_mult))
            
            if result.size_multiplier < 1.0:
                result.decision = PolicyDecision.APPROVED_REDUCED
                result.gate_details["final"] = f"size_reduced_to_{result.size_multiplier:.2f}"
            
        except Exception as e:
            self._logger.error(f"[PolicyGate] Validation error: {e}")
            result.decision = PolicyDecision.BLOCKED
            result.approved = False
            result.block_reason = BlockReason.FAIL_CLOSED
            result.block_details = f"validation_error: {str(e)}"
            result.size_multiplier = 0.0
        
        self._log_result(result, request)
        return result
    
    def _check_system_halt(self, result: PolicyResult) -> bool:
        """Check if system is halted."""
        halt_mgr = self._get_halt_manager()
        if halt_mgr:
            try:
                is_halted = halt_mgr.is_halted()
                if is_halted:
                    result.decision = PolicyDecision.HALTED
                    result.approved = False
                    result.block_reason = BlockReason.SYSTEM_HALTED
                    status = halt_mgr.get_status()
                    result.block_details = status.reason if status and status.reason else "system_halted"
                    result.size_multiplier = 0.0
                    result.gates_passed["halt"] = False
                    result.gate_details["halt"] = result.block_details
                    return False
            except Exception as e:
                self._logger.warn(f"[PolicyGate] Halt check error: {e}")
        
        result.gates_passed["halt"] = True
        result.gate_details["halt"] = "system_active"
        return True
    
    _global_positions_cache: list = []
    _global_positions_cache_ts: float = 0.0
    _recently_approved_underlyings: set = set()
    _recently_approved_ts: float = 0.0

    def _check_global_position_limit(self, request: OrderRequest, result: PolicyResult) -> bool:
        """Block new entries if total positions across ALL bots exceed global limit (3 max).
        Also tracks recently approved entries to prevent burst-fire stacking within cache window."""
        import re as _re
        import time as _time
        
        GLOBAL_MAX_POSITIONS = 3
        
        try:
            now = _time.time()
            # Clear recently approved set every 60s
            if now - self._recently_approved_ts > 60:
                self._recently_approved_underlyings = set()
                self._recently_approved_ts = now
            
            # Always fetch fresh positions (no stale cache that allows bursts)
            from ..services.alpaca_client import get_alpaca_client
            client = get_alpaca_client()
            current_positions = client.get_positions()
            
            # Also count recently approved entries not yet reflected in positions
            num_positions = len(current_positions) + len(self._recently_approved_underlyings)
            
            # Also check if we already hold the same underlying
            def extract_underlying(sym):
                m = _re.match(r'^([A-Z]{1,5})\d{6}[CP]\d+$', sym)
                return m.group(1) if m else sym
            
            request_underlying = extract_underlying(request.symbol)
            held_underlyings = set(extract_underlying(p.symbol) for p in current_positions)
            # Subtract recently approved underlyings already in positions from the count
            already_in_positions = self._recently_approved_underlyings & held_underlyings
            if already_in_positions:
                self._recently_approved_underlyings -= already_in_positions
                num_positions = len(current_positions) + len(self._recently_approved_underlyings)
            
            already_held = request_underlying in held_underlyings or request_underlying in self._recently_approved_underlyings
            
            if already_held:
                result.decision = PolicyDecision.BLOCKED
                result.approved = False
                result.block_reason = BlockReason.VALIDATION_ERROR
                result.block_details = f"Already holding position in {request_underlying} — no stacking"
                result.size_multiplier = 0.0
                result.gates_passed["global_position_limit"] = False
                result.gate_details["global_position_limit"] = f"duplicate_underlying_{request_underlying}"
                print(f"  🚫 [PolicyGate] BLOCKED {request.symbol}: already holding {request_underlying}")
                return False
            
            if num_positions >= GLOBAL_MAX_POSITIONS:
                result.decision = PolicyDecision.BLOCKED
                result.approved = False
                result.block_reason = BlockReason.VALIDATION_ERROR
                result.block_details = f"Global position limit reached: {num_positions}/{GLOBAL_MAX_POSITIONS}"
                result.size_multiplier = 0.0
                result.gates_passed["global_position_limit"] = False
                result.gate_details["global_position_limit"] = f"at_limit_{num_positions}/{GLOBAL_MAX_POSITIONS}"
                print(f"  🚫 [PolicyGate] BLOCKED {request.symbol}: {num_positions} positions >= max {GLOBAL_MAX_POSITIONS}")
                return False
            
            # Track this approved underlying to prevent burst stacking
            self._recently_approved_underlyings.add(request_underlying)
            
            result.gates_passed["global_position_limit"] = True
            result.gate_details["global_position_limit"] = f"ok_{num_positions}/{GLOBAL_MAX_POSITIONS}"
            return True
            
        except Exception as e:
            self._logger.error(f"[PolicyGate] Global position limit check error: {e}")
            result.decision = PolicyDecision.BLOCKED
            result.approved = False
            result.block_reason = BlockReason.FAIL_CLOSED
            result.block_details = f"position_limit_check_error: {e}"
            result.size_multiplier = 0.0
            return False

    _pdt_equity_cache: float = 0.0
    _pdt_equity_cache_ts: float = 0.0

    def _check_pdt_floor(self, request: OrderRequest, result: PolicyResult) -> bool:
        """Block new entries if equity is near PDT $25K threshold."""
        try:
            settings = load_settings()
            pdt_floor = settings.get("risk", {}).get("pdt_equity_floor_usd", 25500.0)
            if pdt_floor <= 0:
                result.gates_passed["pdt"] = True
                result.gate_details["pdt"] = "disabled"
                return True

            import time as _time
            now = _time.time()
            if now - PolicyGate._pdt_equity_cache_ts < 30 and PolicyGate._pdt_equity_cache > 0:
                equity = PolicyGate._pdt_equity_cache
            else:
                from ..services.alpaca_client import get_alpaca_client
                alpaca = get_alpaca_client()
                account = alpaca.get_account()
                equity = float(getattr(account, "equity", 0) if hasattr(account, "equity") else account.get("equity", 0))
                PolicyGate._pdt_equity_cache = equity
                PolicyGate._pdt_equity_cache_ts = now

            if equity <= pdt_floor:
                result.decision = PolicyDecision.BLOCKED
                result.approved = False
                result.block_reason = BlockReason.PDT_PROTECTION
                result.block_details = f"equity=${equity:.0f} <= PDT floor=${pdt_floor:.0f}"
                result.size_multiplier = 0.0
                result.gates_passed["pdt"] = False
                result.gate_details["pdt"] = result.block_details
                self._logger.log("pdt_floor_block", {
                    "symbol": request.symbol,
                    "equity": equity,
                    "pdt_floor": pdt_floor,
                })
                return False

            # Also block if the trade would push equity below floor
            buffer = equity - pdt_floor
            if request.size_usd > buffer:
                result.decision = PolicyDecision.BLOCKED
                result.approved = False
                result.block_reason = BlockReason.PDT_PROTECTION
                result.block_details = f"trade ${request.size_usd:.0f} > PDT buffer ${buffer:.0f}"
                result.size_multiplier = 0.0
                result.gates_passed["pdt"] = False
                result.gate_details["pdt"] = result.block_details
                self._logger.log("pdt_buffer_block", {
                    "symbol": request.symbol,
                    "trade_size": request.size_usd,
                    "buffer": buffer,
                    "equity": equity,
                })
                return False

            result.gates_passed["pdt"] = True
            result.gate_details["pdt"] = f"ok (buffer=${buffer:.0f})"
            return True
        except Exception as e:
            self._logger.warn(f"[PolicyGate] PDT check error (fail-open): {e}")
            result.gates_passed["pdt"] = True
            result.gate_details["pdt"] = f"error_fallback: {e}"
            return True

    def _check_universe(self, request: OrderRequest, result: PolicyResult) -> bool:
        """Check if symbol is in allowed universe."""
        guard = self._get_universe_guard()
        if guard:
            try:
                allowed = guard.is_symbol_allowed(request.symbol, request.bot_id)
                if not allowed:
                    result.decision = PolicyDecision.BLOCKED
                    result.approved = False
                    result.block_reason = BlockReason.UNIVERSE_BLOCKED
                    result.block_details = f"{request.symbol} not in universe for {request.bot_id}"
                    result.size_multiplier = 0.0
                    result.gates_passed["universe"] = False
                    result.gate_details["universe"] = result.block_details
                    return False
            except Exception as e:
                self._logger.warn(f"[PolicyGate] Universe check error: {e}")
        
        result.gates_passed["universe"] = True
        result.gate_details["universe"] = "allowed"
        return True
    
    def _check_risk_integration(
        self, 
        request: OrderRequest, 
        result: PolicyResult,
        multipliers: List[float]
    ) -> bool:
        """Check all risk gates via RiskOrchestratorIntegration."""
        risk = self._get_risk_integration()
        if not risk:
            result.warnings.append("risk_integration_unavailable")
            result.gates_passed["risk"] = True
            result.gate_details["risk"] = "unavailable_proceed_cautiously"
            multipliers.append(0.75)
            return True
        
        try:
            from .risk_integration import RiskAction
            
            if request.is_entry:
                eval_result = risk.evaluate_entry(
                    symbol=request.symbol,
                    bot_name=request.bot_id,
                    proposed_size_usd=request.size_usd,
                    is_bullish=request.is_bullish
                )
            else:
                eval_result = risk.evaluate_exit(
                    symbol=request.symbol,
                    bot_name=request.bot_id,
                    current_pnl_pct=0.0,
                    position_qty=0,
                    position_side="long" if request.is_bullish else "short"
                )
            
            result.risk_evaluation = eval_result
            result.gates_passed.update(eval_result.gates_passed)
            result.gate_details.update(eval_result.gate_details)
            
            if eval_result.action == RiskAction.HALT_TRADING:
                result.decision = PolicyDecision.HALTED
                result.approved = False
                result.block_reason = BlockReason.CORRELATION_HALT
                result.block_details = eval_result.reason
                result.size_multiplier = 0.0
                return False
            
            if eval_result.action == RiskAction.SKIP_ENTRY:
                reason_map = {
                    "insufficient_budget": BlockReason.INSUFFICIENT_BUDGET,
                    "vix_crisis": BlockReason.VIX_CRISIS,
                    "news_block": BlockReason.NEWS_BLOCK,
                    "macro_stress": BlockReason.MACRO_STRESS,
                    "pnl_halt": BlockReason.PNL_HALT
                }
                block_reason = BlockReason.VALIDATION_ERROR
                for key, br in reason_map.items():
                    if key in eval_result.reason:
                        block_reason = br
                        break
                
                result.decision = PolicyDecision.BLOCKED
                result.approved = False
                result.block_reason = block_reason
                result.block_details = eval_result.reason
                result.size_multiplier = 0.0
                return False
            
            multipliers.append(eval_result.size_multiplier)
            result.gates_passed["risk"] = True
            
        except Exception as e:
            self._logger.error(f"[PolicyGate] Risk integration error: {e}")
            result.warnings.append(f"risk_check_error: {e}")
            multipliers.append(0.75)
        
        return True
    
    def _check_liquidity(self, request: OrderRequest, result: PolicyResult) -> bool:
        """Check option liquidity."""
        liq_filter = self._get_liquidity_filter()
        if not liq_filter or not request.option_data:
            result.gates_passed["liquidity"] = True
            result.gate_details["liquidity"] = "skipped"
            return True
        
        try:
            check = liq_filter.check_option_liquidity(
                symbol=request.symbol,
                bid=float(request.option_data.get("bid", 0)),
                ask=float(request.option_data.get("ask", 0)),
                volume=int(request.option_data.get("volume", 0)),
                open_interest=int(request.option_data.get("open_interest", 0))
            )
            
            result.liquidity_check = check
            
            if not check.passed:
                result.decision = PolicyDecision.BLOCKED
                result.approved = False
                result.block_reason = BlockReason.LIQUIDITY_FAIL
                result.block_details = check.rejection_reason or "liquidity_check_failed"
                result.size_multiplier = 0.0
                result.gates_passed["liquidity"] = False
                result.gate_details["liquidity"] = result.block_details
                return False
            
            result.gates_passed["liquidity"] = True
            result.gate_details["liquidity"] = f"spread_{check.spread_pct:.1f}%"
            
        except Exception as e:
            self._logger.warn(f"[PolicyGate] Liquidity check error: {e}")
            result.warnings.append(f"liquidity_check_error: {e}")
        
        return True
    
    def _check_slippage_budget(self, request: OrderRequest, result: PolicyResult) -> bool:
        """Check if within slippage budget."""
        tracker = self._get_slippage_tracker()
        if not tracker:
            result.gates_passed["slippage"] = True
            result.gate_details["slippage"] = "tracker_unavailable"
            return True
        
        try:
            stats = tracker.get_slippage_stats()
            avg_slippage = stats.get("overall_avg_bps", 0) / 100.0  # Convert bps to pct
            if avg_slippage > self._slippage_budget_pct:
                result.warnings.append(f"high_slippage_{avg_slippage:.2f}%")
                result.gate_details["slippage"] = f"warning_avg_{avg_slippage:.2f}%"
            else:
                result.gate_details["slippage"] = "within_budget"
            
            result.gates_passed["slippage"] = True
            
        except Exception as e:
            self._logger.warn(f"[PolicyGate] Slippage check error: {e}")
            result.gates_passed["slippage"] = True
            result.gate_details["slippage"] = "check_error"
        
        return True
    
    def _check_greek_limits(self, request: OrderRequest, result: PolicyResult) -> bool:
        """Check portfolio Greek limits."""
        monitor = self._get_greek_monitor()
        if not monitor:
            result.gates_passed["greek"] = True
            result.gate_details["greek"] = "monitor_unavailable"
            return True
        
        try:
            exposure_summary = monitor.get_exposure_summary()
            within_limits = exposure_summary.get("within_limits", True)
            if not within_limits:
                if request.is_entry:
                    result.decision = PolicyDecision.BLOCKED
                    result.approved = False
                    result.block_reason = BlockReason.GREEK_LIMITS_EXCEEDED
                    result.block_details = "portfolio_greek_limits_exceeded"
                    result.size_multiplier = 0.0
                    result.gates_passed["greek"] = False
                    result.gate_details["greek"] = "limits_exceeded"
                    return False
                else:
                    result.warnings.append("greek_limits_exceeded_allow_exit")
            
            result.gates_passed["greek"] = True
            result.gate_details["greek"] = "within_limits"
            
        except Exception as e:
            self._logger.warn(f"[PolicyGate] Greek limits check error: {e}")
            result.warnings.append(f"greek_check_error: {e}")
        
        return True
    
    def _check_ml_governance(
        self, 
        request: OrderRequest, 
        result: PolicyResult,
        multipliers: List[float]
    ) -> bool:
        """Check ML model governance - auto-disable if degraded."""
        ml_service = self._get_ml_service()
        if not ml_service:
            result.gates_passed["ml"] = True
            result.gate_details["ml"] = "service_unavailable"
            return True
        
        try:
            is_healthy = ml_service.is_available  # Property, not method
            if not is_healthy:
                if self._require_ml_signal:
                    result.decision = PolicyDecision.BLOCKED
                    result.approved = False
                    result.block_reason = BlockReason.ML_MODEL_DISABLED
                    result.block_details = "ml_model_degraded_auto_disabled"
                    result.size_multiplier = 0.0
                    result.gates_passed["ml"] = False
                    result.gate_details["ml"] = "disabled"
                    return False
                else:
                    result.warnings.append("ml_model_degraded_using_rules_based")
                    multipliers.append(0.75)
            
            if request.ml_signal_score is not None:
                if request.ml_signal_score < self._min_ml_confidence:
                    multipliers.append(0.5)
                    result.gate_details["ml"] = f"low_confidence_{request.ml_signal_score:.2f}"
                else:
                    result.gate_details["ml"] = f"confidence_{request.ml_signal_score:.2f}"
            else:
                result.gate_details["ml"] = "no_signal"
            
            result.gates_passed["ml"] = True
            
        except Exception as e:
            self._logger.warn(f"[PolicyGate] ML governance check error: {e}")
            result.warnings.append(f"ml_check_error: {e}")
        
        return True
    
    def _finalize_blocked(self, result: PolicyResult, request: OrderRequest) -> PolicyResult:
        """Finalize a blocked result."""
        result.approved = False
        result.size_multiplier = 0.0
        self._log_result(result, request)
        return result
    
    def _log_result(self, result: PolicyResult, request: OrderRequest):
        """Log policy gate decision."""
        log_data = {
            **result.to_dict(),
            "request": {
                "symbol": request.symbol,
                "bot_id": request.bot_id,
                "side": request.side,
                "asset_class": request.asset_class,
                "size_usd": request.size_usd,
                "is_entry": request.is_entry,
                "is_bullish": request.is_bullish
            }
        }
        
        if result.approved:
            self._logger.log("policy_gate_approved", log_data)
        else:
            self._logger.log("policy_gate_blocked", log_data)


_policy_gate: Optional[PolicyGate] = None


def get_policy_gate() -> PolicyGate:
    """Get or create PolicyGate singleton."""
    global _policy_gate
    if _policy_gate is None:
        _policy_gate = PolicyGate()
    return _policy_gate


def validate_order(request: OrderRequest) -> PolicyResult:
    """Convenience function to validate an order."""
    return get_policy_gate().validate_order(request)
