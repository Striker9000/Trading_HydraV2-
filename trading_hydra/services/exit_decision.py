"""
ExitDecisionEngine - Unified Exit Decision Making for ExitBot v2
=================================================================

This is the brain of ExitBot v2. It consumes signals from:
- TradeMemoryEngine (historical patterns)
- TradeHealthScorer (live position health)
- Market regime context
- Options Greeks (for options)

And outputs one of:
- HOLD: Keep position, no action needed
- TIGHTEN: Tighten stop-loss, reduce risk
- SCALE_OUT: Partial exit (25/50%)
- FULL_EXIT: Close entire position

Each decision has a confidence score (0.0-1.0) and reasoning.

No emotions. Just data-driven exits.
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from ..core.logging import get_logger
from .trade_memory import get_trade_memory, HistoricalContext
from .trade_health import (
    get_trade_health_scorer, TradeHealthScorer, HealthScore, 
    HealthPriority, PositionContext
)
from .exit_types import (
    ExitType, ExitTypeAuthority, ExitSignal, ResolvedExit,
    ExitTypeEvaluator, ExitTypeResolver,
    get_exit_type_evaluator, get_exit_type_resolver
)
from .forward_projection import get_forward_projection_engine
from .partial_exit import get_partial_exit_manager, ScaleOutDecision
from .failsafe import get_failsafe_controller, FailsafeSeverity
from .regime_classifier import get_regime_classifier


class ExitAction(Enum):
    """Exit action types with authority levels."""
    HOLD = "hold"                    # No action
    TIGHTEN = "tighten"              # Tighten stop/reduce risk
    SCALE_OUT_25 = "scale_out_25"    # Exit 25%
    SCALE_OUT_50 = "scale_out_50"    # Exit 50%
    FULL_EXIT = "full_exit"          # Exit 100%


class ExitReason(Enum):
    """Why we're exiting (for audit trail)."""
    THESIS_INVALIDATED = "thesis_invalidated"
    STOP_LOSS_HIT = "stop_loss_hit"
    TAKE_PROFIT_HIT = "take_profit_hit"
    TRAILING_STOP_HIT = "trailing_stop_hit"
    TIME_DECAY = "time_decay"
    HEALTH_CRITICAL = "health_critical"
    STALLING = "stalling"
    MOMENTUM_LOSS = "momentum_loss"
    VWAP_BREAKDOWN = "vwap_breakdown"
    DELTA_BETRAYAL = "delta_betrayal"
    THETA_CRUSH = "theta_crush"
    HISTORICAL_PATTERN = "historical_pattern"
    REGIME_CHANGE = "regime_change"
    VOLATILITY_SPIKE = "volatility_spike"
    RUNNER_PROTECTION = "runner_protection"


@dataclass
class ExitDecision:
    """
    A decision about what to do with a position.
    """
    position_key: str
    action: ExitAction
    confidence: float                # 0.0-1.0 how confident we are
    
    # Reasoning
    primary_reason: ExitReason
    secondary_reasons: List[ExitReason]
    
    # Context
    health_score: int                # 0-100 from TradeHealthScorer
    historical_confidence: float     # How confident in historical data
    
    # For TIGHTEN actions
    new_stop_price: Optional[float]
    
    # For SCALE_OUT actions
    qty_to_exit: Optional[float]
    exit_pct: Optional[float]
    
    # Execution guidance
    urgency: str                     # "immediate", "next_bar", "favorable_liquidity"
    limit_vs_market: str             # "market", "limit", "adaptive"
    
    # Audit
    timestamp: str
    reasoning: str                   # Human-readable explanation


@dataclass
class DecisionInputs:
    """
    All inputs for making an exit decision.
    """
    position_key: str
    position_context: PositionContext
    health_score: Optional[HealthScore]
    historical_context: Optional[HistoricalContext]
    
    # Live market data
    current_price: float
    bid: Optional[float]
    ask: Optional[float]
    spread_pct: Optional[float]
    
    # Current exit parameters
    current_stop: Optional[float]
    current_target: Optional[float]
    trailing_stop_armed: bool
    trailing_stop_price: Optional[float]
    
    # Market regime
    regime: Optional[str]            # "trending", "choppy", "volatile"
    vix: Optional[float]
    
    # For options
    dte: Optional[int]
    delta: Optional[float]
    theta: Optional[float]
    iv: Optional[float]


class ExitDecisionEngine:
    """
    Central decision engine for ExitBot v2.
    
    Consumes all available intelligence and outputs clear,
    actionable decisions for each position.
    
    Uses authority-based exit type hierarchy:
    - CATASTROPHIC (100) - emergency exits, overrides all
    - THESIS (80) - trade thesis invalidated
    - VOLATILITY (70) - vol regime changes
    - PROBABILITY (50) - historical patterns
    - TIME_DECAY (40) - time-based exits
    """
    
    def __init__(self):
        self._logger = get_logger()
        self._memory = get_trade_memory()
        self._health_scorer = get_trade_health_scorer()
        
        # Phase 4 integrations
        self._exit_type_evaluator = get_exit_type_evaluator()
        self._exit_type_resolver = get_exit_type_resolver()
        self._projection_engine = get_forward_projection_engine()
        self._partial_exit_mgr = get_partial_exit_manager()
        self._failsafe = get_failsafe_controller()
        self._regime_classifier = get_regime_classifier()
        
        self._logger.log("exit_decision_engine_initialized", {
            "version": "v2_elite",
            "exit_types_enabled": True,
            "projection_enabled": True,
            "partial_exits_enabled": True,
            "failsafe_enabled": True
        })
    
    def decide(self, inputs: DecisionInputs) -> ExitDecision:
        """
        Make an exit decision for a position.
        
        This is the main entry point. It evaluates all available
        intelligence using the authority-based exit type hierarchy
        and returns a clear decision.
        
        Decision flow:
        1. Check CATASTROPHIC conditions first (failsafe)
        2. Evaluate all exit types using ExitTypeEvaluator
        3. Resolve competing signals using ExitTypeResolver
        4. Check for partial exit opportunities (scale-out)
        5. Use forward projection for confirmation
        
        Args:
            inputs: All available data for the decision
            
        Returns:
            ExitDecision with action, confidence, and reasoning
        """
        # Start with health score as baseline
        health = inputs.health_score
        health_score = health.score if health else 50
        ctx = inputs.position_context
        
        # Collect exit signals using authority-based evaluator
        exit_signals: List[ExitSignal] = []
        legacy_signals: List[Tuple[ExitReason, float, str]] = []
        
        # ==== PRIORITY 0: Failsafe/Catastrophic Checks ====
        failsafe_alerts = self._failsafe.check_failsafe_conditions()
        for alert in failsafe_alerts:
            # Check for EMERGENCY or CRITICAL severity (using enum comparison)
            if alert.severity in (FailsafeSeverity.EMERGENCY, FailsafeSeverity.CRITICAL):
                exit_signals.append(ExitSignal(
                    exit_type=ExitType.CATA_SYSTEM_HALT,
                    confidence=1.0,
                    triggered_at=datetime.utcnow(),
                    message=alert.message,
                    recommended_action="full_exit"
                ))
        
        # ==== Get Current Regime from RegimeClassifier (with timeout for exit safety) ====
        # CRITICAL: Exit decisions must be fast. Skip regime if it takes >2s.
        current_regime = None
        regime_timeout_seconds = 2.0
        try:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self._regime_classifier.get_current_regime, ctx.symbol)
                try:
                    regime_result = future.result(timeout=regime_timeout_seconds)
                    if regime_result:
                        current_regime = regime_result
                        if regime_result.volatility.name == "VOL_EXTREME":
                            exit_signals.append(ExitSignal(
                                exit_type=ExitType.VOL_VIX_SPIKE,
                                confidence=0.8,
                                triggered_at=datetime.utcnow(),
                                message=f"Extreme volatility detected: {regime_result.volatility.name}",
                                recommended_action="tighten"
                            ))
                        if regime_result.vol_dynamic and regime_result.vol_dynamic.name == "VOL_EXPANDING":
                            exit_signals.append(ExitSignal(
                                exit_type=ExitType.VOL_EXPANSION,
                                confidence=0.6,
                                triggered_at=datetime.utcnow(),
                                message="Volatility expanding - tighten stops",
                                recommended_action="tighten"
                            ))
                except concurrent.futures.TimeoutError:
                    self._logger.log("regime_timeout", {"timeout_seconds": regime_timeout_seconds, "message": "Continuing without regime data"})
        except Exception as e:
            self._logger.log("regime_skip", {"error": str(e), "message": "Regime classifier unavailable"})
        
        # ==== PRIORITY 1: THESIS Checks (Authority: 80) ====
        if ctx.vwap and ctx.vwap > 0:
            vwap_signal = self._exit_type_evaluator.check_vwap_breakdown(
                current_price=ctx.current_price,
                vwap=ctx.vwap,
                side=ctx.side
            )
            if vwap_signal:
                exit_signals.append(vwap_signal)
        
        if inputs.current_stop:
            stop_signal = self._exit_type_evaluator.check_stop_loss(
                current_price=ctx.current_price,
                stop_price=inputs.current_stop,
                side=ctx.side
            )
            if stop_signal:
                exit_signals.append(stop_signal)
        
        if inputs.current_target:
            tp_signal = self._exit_type_evaluator.check_take_profit(
                current_price=ctx.current_price,
                target_price=inputs.current_target,
                side=ctx.side
            )
            if tp_signal:
                exit_signals.append(tp_signal)
        
        # ==== PRIORITY 2: VOLATILITY Checks (Authority: 70) ====
        if inputs.regime:
            regime_signal = self._exit_type_evaluator.check_regime_change(
                entry_regime="trending",  # TODO: track entry regime
                current_regime=inputs.regime
            )
            if regime_signal:
                exit_signals.append(regime_signal)
        
        # ==== PRIORITY 3: PROBABILITY Checks (Authority: 50) ====
        if inputs.historical_context and inputs.historical_context.expected_mfe_pct > 0:
            mfe_signal = self._exit_type_evaluator.check_mfe_pattern(
                current_mfe_pct=ctx.mfe_pct,
                historical_avg_mfe=inputs.historical_context.expected_mfe_pct
            )
            if mfe_signal:
                exit_signals.append(mfe_signal)
        
        # ==== PRIORITY 4: TIME_DECAY Checks (Authority: 40) ====
        if ctx.max_hold_minutes:
            minutes_held = (ctx.current_time - ctx.entry_time).total_seconds() / 60
            max_hold_signal = self._exit_type_evaluator.check_max_hold(
                hold_minutes=int(minutes_held),
                max_hold_minutes=ctx.max_hold_minutes
            )
            if max_hold_signal:
                exit_signals.append(max_hold_signal)
        
        stall_signal = self._exit_type_evaluator.check_stalling(
            hold_minutes=int((ctx.current_time - ctx.entry_time).total_seconds() / 60),
            pnl_pct=ctx.unrealized_pnl_pct
        )
        if stall_signal:
            exit_signals.append(stall_signal)
        
        # ==== Forward Projection Engine (Phase 4B) ====
        projection = None
        try:
            # Get ATR estimate (use 2% of price as fallback)
            atr_estimate = ctx.current_price * 0.02  # 2% ATR estimate
            
            projection = self._projection_engine.project(
                symbol=ctx.symbol,
                strategy="exitbot_v2",
                position_side=ctx.side,
                entry_price=ctx.entry_price,
                current_price=ctx.current_price,
                atr=atr_estimate,
                target_price=inputs.current_target,
                stop_price=inputs.current_stop,
                time_horizon_hours=1.0
            )
            
            # Use projection for probability-based signals
            if projection and projection.continuation:
                if projection.continuation.prob_reach_target < 0.3:
                    exit_signals.append(ExitSignal(
                        exit_type=ExitType.PROB_LOW_CONTINUATION,
                        confidence=1.0 - projection.continuation.prob_reach_target,
                        triggered_at=datetime.utcnow(),
                        trigger_value=projection.continuation.prob_reach_target,
                        threshold=0.3,
                        message=f"Low continuation probability: {projection.continuation.prob_reach_target:.0%}",
                        recommended_action="scale_out_50"
                    ))
            
            # Use projection recommendation
            if projection and projection.recommendation in ("exit_now", "scale_out"):
                if projection.recommendation == "exit_now":
                    exit_signals.append(ExitSignal(
                        exit_type=ExitType.PROB_FINGERPRINT,
                        confidence=0.75,
                        triggered_at=datetime.utcnow(),
                        message=f"Forward projection recommends exit: {projection.recommendation_reason}",
                        recommended_action="full_exit"
                    ))
        except Exception as e:
            self._logger.log("projection_error", {"error": str(e), "message": "Failed to run projection"})
        
        # ==== Legacy Health-Based Checks ====
        if health and health.priority == HealthPriority.EXIT_NOW:
            legacy_signals.append((
                ExitReason.HEALTH_CRITICAL,
                0.85,
                f"Health score critical: {health.score}"
            ))
        
        if health and health.stalling:
            stall_legacy = self._check_stalling(inputs)
            if stall_legacy:
                legacy_signals.append(stall_legacy)
        
        if ctx.asset_class == "option":
            greeks_signal = self._check_greeks(inputs)
            if greeks_signal:
                legacy_signals.append(greeks_signal)
        
        # ==== Resolve Signals Using Authority Hierarchy ====
        resolved = self._exit_type_resolver.resolve(exit_signals) if exit_signals else None
        
        # ==== Check for Partial Exit (Scale-Out) ====
        scale_out_decision = None
        if health:
            # Get or create scale state for position
            scale_state = self._partial_exit_mgr.get_scale_state(inputs.position_key)
            if not scale_state:
                scale_state = self._partial_exit_mgr.create_scale_state(
                    position_key=inputs.position_key,
                    original_qty=1.0  # Normalized
                )
            
            # Build historical dict for should_scale_out
            hist_dict = None
            if inputs.historical_context:
                hist_dict = {
                    "avg_mfe": inputs.historical_context.expected_mfe_pct,
                    "avg_hold_minutes": 60  # Default, can enhance later
                }
            
            scale_out_decision = self._partial_exit_mgr.should_scale_out(
                position_context=ctx,
                health_score=health,
                scale_state=scale_state,
                historical_context=hist_dict
            )
        
        # ==== Make Final Decision ====
        if resolved and resolved.winning_signal.is_catastrophic:
            # Catastrophic signals always win - immediate full exit
            return self._build_decision_from_resolved(inputs, resolved, health_score)
        
        if resolved and resolved.winning_signal.authority >= ExitTypeAuthority.THESIS.value:
            # High authority signals (thesis) take priority
            return self._build_decision_from_resolved(inputs, resolved, health_score)
        
        if scale_out_decision and scale_out_decision.should_scale:
            # Scale-out opportunity - determine action based on scale tier
            from .partial_exit import ScaleTier
            if scale_out_decision.scale_tier == ScaleTier.TIER_1:
                action = ExitAction.SCALE_OUT_25
                exit_pct = 0.25
            else:
                action = ExitAction.SCALE_OUT_50
                exit_pct = 0.25  # Tier 2 is also 25%
            
            return ExitDecision(
                position_key=inputs.position_key,
                action=action,
                confidence=scale_out_decision.confidence,
                primary_reason=ExitReason.RUNNER_PROTECTION,
                secondary_reasons=[],
                health_score=health_score,
                historical_confidence=0.7,
                new_stop_price=None,
                qty_to_exit=scale_out_decision.qty_to_exit,
                exit_pct=exit_pct,
                urgency="next_bar",
                limit_vs_market="adaptive",
                timestamp=datetime.utcnow().isoformat(),
                reasoning=scale_out_decision.trigger_reason
            )
        
        if resolved:
            # Lower authority signals
            return self._build_decision_from_resolved(inputs, resolved, health_score)
        
        # Fall back to legacy decision logic
        return self._make_decision(inputs, health_score, legacy_signals)
    
    def _build_decision_from_resolved(
        self, 
        inputs: DecisionInputs, 
        resolved: ResolvedExit,
        health_score: int
    ) -> ExitDecision:
        """Build ExitDecision from ResolvedExit."""
        sig = resolved.winning_signal
        
        # Map recommended action to ExitAction
        action_map = {
            "hold": ExitAction.HOLD,
            "tighten": ExitAction.TIGHTEN,
            "scale_out_25": ExitAction.SCALE_OUT_25,
            "scale_out_50": ExitAction.SCALE_OUT_50,
            "full_exit": ExitAction.FULL_EXIT
        }
        action = action_map.get(sig.recommended_action, ExitAction.FULL_EXIT)
        
        # Map exit type to reason
        reason_map = {
            ExitType.THESIS_VWAP_BREAKDOWN: ExitReason.VWAP_BREAKDOWN,
            ExitType.THESIS_STOP_LOSS: ExitReason.STOP_LOSS_HIT,
            ExitType.THESIS_TAKE_PROFIT: ExitReason.TAKE_PROFIT_HIT,
            ExitType.THESIS_MOMENTUM_LOSS: ExitReason.MOMENTUM_LOSS,
            ExitType.TIME_MAX_HOLD: ExitReason.TIME_DECAY,
            ExitType.TIME_STALLING: ExitReason.STALLING,
            ExitType.TIME_THETA_DECAY: ExitReason.THETA_CRUSH,
            ExitType.VOL_REGIME_CHANGE: ExitReason.REGIME_CHANGE,
            ExitType.VOL_VIX_SPIKE: ExitReason.VOLATILITY_SPIKE,
            ExitType.PROB_MFE_PATTERN: ExitReason.HISTORICAL_PATTERN,
        }
        reason = reason_map.get(sig.exit_type, ExitReason.THESIS_INVALIDATED)
        
        return ExitDecision(
            position_key=inputs.position_key,
            action=action,
            confidence=sig.confidence,
            primary_reason=reason,
            secondary_reasons=[],
            health_score=health_score,
            historical_confidence=0.7,
            new_stop_price=sig.new_stop_price,
            qty_to_exit=None,
            exit_pct=0.25 if action == ExitAction.SCALE_OUT_25 else (0.50 if action == ExitAction.SCALE_OUT_50 else None),
            urgency="immediate" if sig.is_catastrophic else "next_bar",
            limit_vs_market="market" if sig.is_catastrophic else "adaptive",
            timestamp=datetime.utcnow().isoformat(),
            reasoning=f"{sig.message} (authority: {sig.authority})"
        )
    
    def _check_thesis(self, inputs: DecisionInputs) -> Optional[Tuple[ExitReason, float, str]]:
        """Check if trade thesis is still valid."""
        ctx = inputs.position_context
        
        if inputs.current_stop and inputs.current_stop > 0:
            if ctx.side == "long" and ctx.current_price <= inputs.current_stop:
                return (
                    ExitReason.THESIS_INVALIDATED,
                    0.95,
                    "Price at/below stop level - thesis invalidated"
                )
            elif ctx.side == "short" and ctx.current_price >= inputs.current_stop:
                return (
                    ExitReason.THESIS_INVALIDATED,
                    0.95,
                    "Price at/above stop level - thesis invalidated"
                )
        
        # Check if health scorer says thesis is dead
        if inputs.health_score and not inputs.health_score.thesis_alive:
            return (
                ExitReason.THESIS_INVALIDATED,
                0.80,
                "Trade thesis no longer valid based on health assessment"
            )
        
        return None
    
    def _check_stop_loss(self, inputs: DecisionInputs) -> Optional[Tuple[ExitReason, float, str]]:
        """Check if stop loss was hit."""
        ctx = inputs.position_context
        
        # Hard stop
        if inputs.current_stop and inputs.current_stop > 0:
            if ctx.side == "long" and ctx.current_price <= inputs.current_stop:
                return (
                    ExitReason.STOP_LOSS_HIT,
                    0.99,
                    f"Price {ctx.current_price} <= stop {inputs.current_stop}"
                )
            elif ctx.side == "short" and ctx.current_price >= inputs.current_stop:
                return (
                    ExitReason.STOP_LOSS_HIT,
                    0.99,
                    f"Price {ctx.current_price} >= stop {inputs.current_stop}"
                )
        
        # Trailing stop
        if inputs.trailing_stop_armed and inputs.trailing_stop_price:
            if ctx.side == "long" and ctx.current_price <= inputs.trailing_stop_price:
                return (
                    ExitReason.TRAILING_STOP_HIT,
                    0.95,
                    f"Trailing stop hit at {inputs.trailing_stop_price}"
                )
            elif ctx.side == "short" and ctx.current_price >= inputs.trailing_stop_price:
                return (
                    ExitReason.TRAILING_STOP_HIT,
                    0.95,
                    f"Trailing stop hit at {inputs.trailing_stop_price}"
                )
        
        return None
    
    def _check_take_profit(self, inputs: DecisionInputs) -> Optional[Tuple[ExitReason, float, str]]:
        """Check if take profit was hit."""
        ctx = inputs.position_context
        
        if inputs.current_target and inputs.current_target > 0:
            if ctx.side == "long" and ctx.current_price >= inputs.current_target:
                return (
                    ExitReason.TAKE_PROFIT_HIT,
                    0.90,
                    f"Take profit target {inputs.current_target} reached"
                )
            elif ctx.side == "short" and ctx.current_price <= inputs.current_target:
                return (
                    ExitReason.TAKE_PROFIT_HIT,
                    0.90,
                    f"Take profit target {inputs.current_target} reached"
                )
        
        return None
    
    def _check_time_decay(self, inputs: DecisionInputs) -> Optional[Tuple[ExitReason, float, str]]:
        """Check time-based exit conditions."""
        ctx = inputs.position_context
        
        # Options 0DTE rule - exit before expiration
        if inputs.dte is not None and inputs.dte == 0:
            if ctx.unrealized_pnl_pct > 0:
                return (
                    ExitReason.TIME_DECAY,
                    0.85,
                    "0DTE option - exiting to capture profits before expiration"
                )
            else:
                return (
                    ExitReason.TIME_DECAY,
                    0.75,
                    "0DTE option - exiting to limit time decay losses"
                )
        
        # Max hold time
        if ctx.max_hold_minutes:
            minutes_held = (ctx.current_time - ctx.entry_time).total_seconds() / 60
            if minutes_held >= ctx.max_hold_minutes:
                return (
                    ExitReason.TIME_DECAY,
                    0.80,
                    f"Max hold time ({ctx.max_hold_minutes}min) exceeded"
                )
        
        return None
    
    def _check_stalling(self, inputs: DecisionInputs) -> Optional[Tuple[ExitReason, float, str]]:
        """Check if trade is stalling."""
        ctx = inputs.position_context
        
        # If MFE was significant and we've given back most of it
        if ctx.mfe_pct > 1.0 and ctx.unrealized_pnl_pct < ctx.mfe_pct * 0.3:
            return (
                ExitReason.STALLING,
                0.70,
                f"Gave back {((ctx.mfe_pct - ctx.unrealized_pnl_pct) / ctx.mfe_pct * 100):.0f}% of MFE"
            )
        
        # If trade hasn't moved much after 15+ minutes
        minutes_held = (ctx.current_time - ctx.entry_time).total_seconds() / 60
        if minutes_held > 15 and abs(ctx.unrealized_pnl_pct) < 0.3:
            return (
                ExitReason.STALLING,
                0.60,
                f"Trade flat for {minutes_held:.0f} minutes"
            )
        
        return None
    
    def _check_greeks(self, inputs: DecisionInputs) -> Optional[Tuple[ExitReason, float, str]]:
        """Check options-specific exit conditions."""
        ctx = inputs.position_context
        
        # Delta betrayal - delta moving against us significantly
        if ctx.delta is not None and ctx.delta_at_entry is not None:
            delta_change = abs(ctx.delta) - abs(ctx.delta_at_entry)
            
            if ctx.side == "long" and delta_change < -0.15:
                return (
                    ExitReason.DELTA_BETRAYAL,
                    0.75,
                    f"Delta dropped from {ctx.delta_at_entry:.2f} to {ctx.delta:.2f}"
                )
        
        # Theta crush - losing faster than expected
        if inputs.theta is not None and ctx.unrealized_pnl_pct < 0:
            if abs(inputs.theta) > abs(ctx.unrealized_pnl_pct) * 0.5:
                return (
                    ExitReason.THETA_CRUSH,
                    0.65,
                    f"Theta decay ({inputs.theta:.2f}) crushing position"
                )
        
        return None
    
    def _check_historical_pattern(self, inputs: DecisionInputs) -> Optional[Tuple[ExitReason, float, str]]:
        """Check if historical patterns suggest exit."""
        ctx = inputs.position_context
        hist = inputs.historical_context
        
        if not hist or hist.trade_count < 5:
            return None
        
        # If we've exceeded expected stall point
        if ctx.unrealized_pnl_pct >= hist.expected_stall_pct:
            return (
                ExitReason.HISTORICAL_PATTERN,
                hist.confidence * 0.7,
                f"At historical stall point ({hist.expected_stall_pct:.1f}%)"
            )
        
        return None
    
    def _make_decision(
        self,
        inputs: DecisionInputs,
        health_score: int,
        signals: List[Tuple[ExitReason, float, str]]
    ) -> ExitDecision:
        """
        Synthesize all signals into a final decision.
        """
        ctx = inputs.position_context
        
        # If no exit signals, HOLD
        if not signals:
            return ExitDecision(
                position_key=inputs.position_key,
                action=ExitAction.HOLD,
                confidence=0.8,
                primary_reason=ExitReason.THESIS_INVALIDATED,  # Placeholder
                secondary_reasons=[],
                health_score=health_score,
                historical_confidence=inputs.historical_context.confidence if inputs.historical_context else 0.0,
                new_stop_price=None,
                qty_to_exit=None,
                exit_pct=None,
                urgency="none",
                limit_vs_market="none",
                timestamp=datetime.utcnow().isoformat() + "Z",
                reasoning="No exit signals detected - holding position"
            )
        
        # Sort signals by confidence
        signals.sort(key=lambda x: x[1], reverse=True)
        
        primary_reason = signals[0][0]
        primary_confidence = signals[0][1]
        primary_explanation = signals[0][2]
        secondary_reasons = [s[0] for s in signals[1:3]]
        
        # Determine action based on strongest signal
        if primary_confidence >= 0.95:
            # Very high confidence - FULL EXIT
            action = ExitAction.FULL_EXIT
            urgency = "immediate"
            limit_vs_market = "market"
        elif primary_confidence >= 0.85:
            # High confidence - FULL EXIT but can wait for liquidity
            action = ExitAction.FULL_EXIT
            urgency = "next_bar"
            limit_vs_market = "adaptive"
        elif primary_confidence >= 0.70:
            # Moderate-high confidence - Scale out 50%
            action = ExitAction.SCALE_OUT_50
            urgency = "next_bar"
            limit_vs_market = "limit"
        elif primary_confidence >= 0.60:
            # Moderate confidence - Scale out 25%
            action = ExitAction.SCALE_OUT_25
            urgency = "favorable_liquidity"
            limit_vs_market = "limit"
        elif health_score < 60:
            # Low health - tighten
            action = ExitAction.TIGHTEN
            urgency = "none"
            limit_vs_market = "none"
        else:
            # Weak signals - hold but watch
            action = ExitAction.HOLD
            urgency = "none"
            limit_vs_market = "none"
        
        # Calculate new stop for TIGHTEN
        new_stop = None
        if action == ExitAction.TIGHTEN:
            new_stop = self._calculate_tighter_stop(inputs)
        
        # Calculate qty for SCALE_OUT
        qty_to_exit = None
        exit_pct = None
        if action == ExitAction.SCALE_OUT_25:
            exit_pct = 0.25
        elif action == ExitAction.SCALE_OUT_50:
            exit_pct = 0.50
        elif action == ExitAction.FULL_EXIT:
            exit_pct = 1.0
        
        # Build reasoning
        reasoning_parts = [primary_explanation]
        for s in signals[1:3]:
            reasoning_parts.append(s[2])
        reasoning = " | ".join(reasoning_parts)
        
        decision = ExitDecision(
            position_key=inputs.position_key,
            action=action,
            confidence=primary_confidence,
            primary_reason=primary_reason,
            secondary_reasons=secondary_reasons,
            health_score=health_score,
            historical_confidence=inputs.historical_context.confidence if inputs.historical_context else 0.0,
            new_stop_price=new_stop,
            qty_to_exit=qty_to_exit,
            exit_pct=exit_pct,
            urgency=urgency,
            limit_vs_market=limit_vs_market,
            timestamp=datetime.utcnow().isoformat() + "Z",
            reasoning=reasoning
        )
        
        self._logger.log("exit_decision_made", {
            "position_key": inputs.position_key,
            "action": action.value,
            "confidence": primary_confidence,
            "primary_reason": primary_reason.value,
            "health_score": health_score,
            "urgency": urgency
        })
        
        return decision
    
    def _calculate_tighter_stop(self, inputs: DecisionInputs) -> Optional[float]:
        """Calculate a tighter stop price."""
        ctx = inputs.position_context
        current = ctx.current_price
        
        # If we have ATR, use that for tighter stop
        if ctx.atr_pct:
            if ctx.side == "long":
                return current * (1 - ctx.atr_pct / 100)
            else:
                return current * (1 + ctx.atr_pct / 100)
        
        # Default: 1% from current price
        if ctx.side == "long":
            return current * 0.99
        else:
            return current * 1.01


# Global singleton
_decision_engine: Optional[ExitDecisionEngine] = None


def get_exit_decision_engine() -> ExitDecisionEngine:
    """Get or create the global ExitDecisionEngine instance."""
    global _decision_engine
    if _decision_engine is None:
        _decision_engine = ExitDecisionEngine()
    return _decision_engine
