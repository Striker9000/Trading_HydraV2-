"""
Exit Types with Authority Hierarchy - ExitBot v2 Elite
========================================================

Implements 5 exit types with strict authority hierarchy:

1. CATASTROPHIC (Authority: 100) - Emergency exits
   - Auth failure, data staleness, extreme slippage, news shock
   - Overrides ALL other exit types
   - Cannot be delayed or negotiated

2. THESIS (Authority: 80) - Trade thesis invalidated
   - VWAP breakdown for VWAP-based entries
   - Delta betrayal for options
   - Momentum loss for momentum trades
   - High authority - thesis exit means the trade is wrong

3. VOLATILITY (Authority: 70) - Vol regime changes
   - VIX spike beyond threshold
   - Vol expansion making stops unsafe
   - Regime change from trending to choppy
   - Gamma environment shift

4. PROBABILITY (Authority: 50) - Historical patterns
   - MFE/MAE patterns suggest exit now
   - Stalling beyond typical hold time
   - Low continuation probability from fingerprints

5. TIME_DECAY (Authority: 40) - Time-based exits
   - Max hold time exceeded
   - Theta decay for options
   - Session end approaching

Higher authority ALWAYS wins. Same authority = most recent signal wins.
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto


class ExitTypeAuthority(Enum):
    """
    Exit type authority levels.
    Higher number = higher authority = overrides lower.
    """
    TIME_DECAY = 40      # Lowest priority - time-based
    PROBABILITY = 50     # Historical patterns
    VOLATILITY = 70      # Vol regime changes
    THESIS = 80          # Trade thesis invalidated
    CATASTROPHIC = 100   # Emergency - highest priority


class ExitType(Enum):
    """Exit type identifiers with their authority levels."""
    # Time-based exits (Authority: 40)
    TIME_MAX_HOLD = "time_max_hold"
    TIME_THETA_DECAY = "time_theta_decay"
    TIME_SESSION_END = "time_session_end"
    TIME_STALLING = "time_stalling"
    
    # Probability-based exits (Authority: 50)
    PROB_MFE_PATTERN = "prob_mfe_pattern"
    PROB_MAE_PATTERN = "prob_mae_pattern"
    PROB_FINGERPRINT = "prob_fingerprint"
    PROB_LOW_CONTINUATION = "prob_low_continuation"
    
    # Volatility-based exits (Authority: 70)
    VOL_VIX_SPIKE = "vol_vix_spike"
    VOL_EXPANSION = "vol_expansion"
    VOL_REGIME_CHANGE = "vol_regime_change"
    VOL_GAMMA_SHIFT = "vol_gamma_shift"
    
    # Thesis-based exits (Authority: 80)
    THESIS_VWAP_BREAKDOWN = "thesis_vwap_breakdown"
    THESIS_DELTA_BETRAYAL = "thesis_delta_betrayal"
    THESIS_MOMENTUM_LOSS = "thesis_momentum_loss"
    THESIS_STOP_LOSS = "thesis_stop_loss"
    THESIS_TAKE_PROFIT = "thesis_take_profit"
    
    # Catastrophic exits (Authority: 100)
    CATA_AUTH_FAILURE = "cata_auth_failure"
    CATA_DATA_STALE = "cata_data_stale"
    CATA_EXTREME_SLIPPAGE = "cata_extreme_slippage"
    CATA_NEWS_SHOCK = "cata_news_shock"
    CATA_CIRCUIT_BREAKER = "cata_circuit_breaker"
    CATA_SYSTEM_HALT = "cata_system_halt"


# Map exit types to their authority levels
EXIT_TYPE_AUTHORITY: Dict[ExitType, ExitTypeAuthority] = {
    # Time-based (40)
    ExitType.TIME_MAX_HOLD: ExitTypeAuthority.TIME_DECAY,
    ExitType.TIME_THETA_DECAY: ExitTypeAuthority.TIME_DECAY,
    ExitType.TIME_SESSION_END: ExitTypeAuthority.TIME_DECAY,
    ExitType.TIME_STALLING: ExitTypeAuthority.TIME_DECAY,
    
    # Probability (50)
    ExitType.PROB_MFE_PATTERN: ExitTypeAuthority.PROBABILITY,
    ExitType.PROB_MAE_PATTERN: ExitTypeAuthority.PROBABILITY,
    ExitType.PROB_FINGERPRINT: ExitTypeAuthority.PROBABILITY,
    ExitType.PROB_LOW_CONTINUATION: ExitTypeAuthority.PROBABILITY,
    
    # Volatility (70)
    ExitType.VOL_VIX_SPIKE: ExitTypeAuthority.VOLATILITY,
    ExitType.VOL_EXPANSION: ExitTypeAuthority.VOLATILITY,
    ExitType.VOL_REGIME_CHANGE: ExitTypeAuthority.VOLATILITY,
    ExitType.VOL_GAMMA_SHIFT: ExitTypeAuthority.VOLATILITY,
    
    # Thesis (80)
    ExitType.THESIS_VWAP_BREAKDOWN: ExitTypeAuthority.THESIS,
    ExitType.THESIS_DELTA_BETRAYAL: ExitTypeAuthority.THESIS,
    ExitType.THESIS_MOMENTUM_LOSS: ExitTypeAuthority.THESIS,
    ExitType.THESIS_STOP_LOSS: ExitTypeAuthority.THESIS,
    ExitType.THESIS_TAKE_PROFIT: ExitTypeAuthority.THESIS,
    
    # Catastrophic (100)
    ExitType.CATA_AUTH_FAILURE: ExitTypeAuthority.CATASTROPHIC,
    ExitType.CATA_DATA_STALE: ExitTypeAuthority.CATASTROPHIC,
    ExitType.CATA_EXTREME_SLIPPAGE: ExitTypeAuthority.CATASTROPHIC,
    ExitType.CATA_NEWS_SHOCK: ExitTypeAuthority.CATASTROPHIC,
    ExitType.CATA_CIRCUIT_BREAKER: ExitTypeAuthority.CATASTROPHIC,
    ExitType.CATA_SYSTEM_HALT: ExitTypeAuthority.CATASTROPHIC,
}


@dataclass
class ExitSignal:
    """
    A signal suggesting an exit action.
    
    Multiple signals can be active. The ExitTypeResolver
    determines which one wins based on authority.
    """
    exit_type: ExitType
    confidence: float            # 0.0-1.0
    triggered_at: datetime
    
    # Signal-specific data
    trigger_value: Optional[float] = None
    threshold: Optional[float] = None
    message: str = ""
    
    # Recommended action
    recommended_action: str = "full_exit"  # "tighten", "scale_out_25", "scale_out_50", "full_exit"
    new_stop_price: Optional[float] = None
    
    @property
    def authority(self) -> int:
        """Get authority level for this signal."""
        return EXIT_TYPE_AUTHORITY.get(self.exit_type, ExitTypeAuthority.TIME_DECAY).value
    
    @property
    def is_catastrophic(self) -> bool:
        """Check if this is a catastrophic exit."""
        return self.authority >= ExitTypeAuthority.CATASTROPHIC.value


@dataclass
class ResolvedExit:
    """
    The final resolved exit decision after authority arbitration.
    """
    winning_signal: ExitSignal
    competing_signals: List[ExitSignal]
    
    # Final decision
    action: str                  # "hold", "tighten", "scale_out_25", "scale_out_50", "full_exit"
    confidence: float
    
    # Audit
    authority_level: int
    resolution_reason: str
    timestamp: datetime


class ExitTypeResolver:
    """
    Resolves competing exit signals based on authority hierarchy.
    
    Rules:
    1. Higher authority ALWAYS wins
    2. Same authority = highest confidence wins
    3. Same authority + confidence = most recent wins
    4. Catastrophic signals cannot be overridden
    """
    
    @staticmethod
    def resolve(signals: List[ExitSignal]) -> Optional[ResolvedExit]:
        """
        Resolve multiple exit signals into a single decision.
        
        Args:
            signals: List of active exit signals
            
        Returns:
            ResolvedExit with winning signal, or None if no signals
        """
        if not signals:
            return None
        
        if len(signals) == 1:
            sig = signals[0]
            return ResolvedExit(
                winning_signal=sig,
                competing_signals=[],
                action=sig.recommended_action,
                confidence=sig.confidence,
                authority_level=sig.authority,
                resolution_reason="single_signal",
                timestamp=datetime.utcnow()
            )
        
        # Sort by: authority (desc), confidence (desc), time (desc)
        sorted_signals = sorted(
            signals,
            key=lambda s: (s.authority, s.confidence, s.triggered_at.timestamp()),
            reverse=True
        )
        
        winner = sorted_signals[0]
        losers = sorted_signals[1:]
        
        # Determine resolution reason
        if winner.authority > losers[0].authority:
            reason = f"authority_override_{winner.exit_type.value}_over_{losers[0].exit_type.value}"
        elif winner.confidence > losers[0].confidence:
            reason = f"confidence_tiebreak_{winner.confidence:.2f}_over_{losers[0].confidence:.2f}"
        else:
            reason = "recency_tiebreak"
        
        return ResolvedExit(
            winning_signal=winner,
            competing_signals=losers,
            action=winner.recommended_action,
            confidence=winner.confidence,
            authority_level=winner.authority,
            resolution_reason=reason,
            timestamp=datetime.utcnow()
        )


class ExitTypeEvaluator:
    """
    Evaluates exit conditions for each type.
    
    Each method returns an ExitSignal if the condition is met,
    or None if not triggered.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config = config or {}
        from ..core.logging import get_logger
        self._logger = get_logger()
    
    # =========================================================================
    # CATASTROPHIC EXITS (Authority: 100)
    # =========================================================================
    
    def check_auth_failure(self, api_status: Dict[str, Any]) -> Optional[ExitSignal]:
        """Check if API authentication has failed."""
        if api_status.get("auth_failed", False):
            return ExitSignal(
                exit_type=ExitType.CATA_AUTH_FAILURE,
                confidence=1.0,
                triggered_at=datetime.utcnow(),
                message="API authentication failure - emergency exit",
                recommended_action="full_exit"
            )
        return None
    
    def check_data_staleness(
        self, 
        last_update: datetime, 
        staleness_threshold_seconds: int = 120
    ) -> Optional[ExitSignal]:
        """Check if market data is stale."""
        age_seconds = (datetime.utcnow() - last_update).total_seconds()
        if age_seconds > staleness_threshold_seconds:
            return ExitSignal(
                exit_type=ExitType.CATA_DATA_STALE,
                confidence=min(1.0, age_seconds / staleness_threshold_seconds / 2),
                triggered_at=datetime.utcnow(),
                trigger_value=age_seconds,
                threshold=staleness_threshold_seconds,
                message=f"Data stale for {age_seconds:.0f}s (threshold: {staleness_threshold_seconds}s)",
                recommended_action="full_exit"
            )
        return None
    
    def check_extreme_slippage(
        self, 
        expected_price: float, 
        actual_price: float,
        slippage_threshold_pct: float = 2.0
    ) -> Optional[ExitSignal]:
        """Check for extreme slippage on recent orders."""
        if expected_price <= 0:
            return None
        slippage_pct = abs(actual_price - expected_price) / expected_price * 100
        if slippage_pct > slippage_threshold_pct:
            return ExitSignal(
                exit_type=ExitType.CATA_EXTREME_SLIPPAGE,
                confidence=min(1.0, slippage_pct / slippage_threshold_pct / 2),
                triggered_at=datetime.utcnow(),
                trigger_value=slippage_pct,
                threshold=slippage_threshold_pct,
                message=f"Extreme slippage: {slippage_pct:.2f}% (threshold: {slippage_threshold_pct}%)",
                recommended_action="full_exit"
            )
        return None
    
    def check_news_shock(
        self, 
        news_impact_score: float,
        news_threshold: float = 0.8
    ) -> Optional[ExitSignal]:
        """Check for high-impact news events."""
        if news_impact_score > news_threshold:
            return ExitSignal(
                exit_type=ExitType.CATA_NEWS_SHOCK,
                confidence=news_impact_score,
                triggered_at=datetime.utcnow(),
                trigger_value=news_impact_score,
                threshold=news_threshold,
                message=f"High-impact news detected (score: {news_impact_score:.2f})",
                recommended_action="full_exit"
            )
        return None
    
    def check_circuit_breaker(self, is_halted: bool) -> Optional[ExitSignal]:
        """Check if market is halted."""
        if is_halted:
            return ExitSignal(
                exit_type=ExitType.CATA_CIRCUIT_BREAKER,
                confidence=1.0,
                triggered_at=datetime.utcnow(),
                message="Market circuit breaker triggered",
                recommended_action="full_exit"
            )
        return None
    
    # =========================================================================
    # THESIS EXITS (Authority: 80)
    # =========================================================================
    
    def check_vwap_breakdown(
        self, 
        current_price: float, 
        vwap: float,
        side: str,
        vwap_threshold_pct: float = 0.5
    ) -> Optional[ExitSignal]:
        """Check if price has broken VWAP against position direction."""
        if vwap <= 0:
            return None
        
        pct_from_vwap = (current_price - vwap) / vwap * 100
        
        # Long: breakdown = price below VWAP
        # Short: breakdown = price above VWAP
        is_breakdown = False
        if side == "long" and pct_from_vwap < -vwap_threshold_pct:
            is_breakdown = True
        elif side == "short" and pct_from_vwap > vwap_threshold_pct:
            is_breakdown = True
        
        if is_breakdown:
            return ExitSignal(
                exit_type=ExitType.THESIS_VWAP_BREAKDOWN,
                confidence=min(1.0, abs(pct_from_vwap) / vwap_threshold_pct / 2),
                triggered_at=datetime.utcnow(),
                trigger_value=pct_from_vwap,
                threshold=vwap_threshold_pct,
                message=f"VWAP breakdown: {pct_from_vwap:.2f}% from VWAP",
                recommended_action="full_exit"
            )
        return None
    
    def check_delta_betrayal(
        self, 
        current_delta: float, 
        entry_delta: float,
        delta_decay_threshold: float = 0.3
    ) -> Optional[ExitSignal]:
        """Check if option delta has decayed significantly."""
        if entry_delta is None or entry_delta == 0:
            return None
        
        delta_decay = abs(current_delta) - abs(entry_delta)
        
        # Negative decay = delta moved against us
        if delta_decay < -delta_decay_threshold:
            return ExitSignal(
                exit_type=ExitType.THESIS_DELTA_BETRAYAL,
                confidence=min(1.0, abs(delta_decay) / delta_decay_threshold),
                triggered_at=datetime.utcnow(),
                trigger_value=delta_decay,
                threshold=delta_decay_threshold,
                message=f"Delta betrayal: {delta_decay:.2f} decay",
                recommended_action="full_exit"
            )
        return None
    
    def check_momentum_loss(
        self, 
        momentum_score: float,
        momentum_threshold: float = 20
    ) -> Optional[ExitSignal]:
        """Check if momentum has died."""
        if momentum_score < momentum_threshold:
            return ExitSignal(
                exit_type=ExitType.THESIS_MOMENTUM_LOSS,
                confidence=1.0 - (momentum_score / momentum_threshold),
                triggered_at=datetime.utcnow(),
                trigger_value=momentum_score,
                threshold=momentum_threshold,
                message=f"Momentum loss: score {momentum_score:.1f} below threshold",
                recommended_action="tighten"
            )
        return None
    
    def check_stop_loss(
        self, 
        current_price: float, 
        stop_price: float,
        side: str
    ) -> Optional[ExitSignal]:
        """Check if stop-loss price is hit."""
        if stop_price is None or stop_price <= 0:
            return None
        
        is_hit = False
        if side == "long" and current_price <= stop_price:
            is_hit = True
        elif side == "short" and current_price >= stop_price:
            is_hit = True
        
        if is_hit:
            return ExitSignal(
                exit_type=ExitType.THESIS_STOP_LOSS,
                confidence=1.0,
                triggered_at=datetime.utcnow(),
                trigger_value=current_price,
                threshold=stop_price,
                message=f"Stop-loss hit at {current_price}",
                recommended_action="full_exit"
            )
        return None
    
    def check_take_profit(
        self, 
        current_price: float, 
        target_price: float,
        side: str
    ) -> Optional[ExitSignal]:
        """Check if take-profit price is hit."""
        if target_price is None or target_price <= 0:
            return None
        
        is_hit = False
        if side == "long" and current_price >= target_price:
            is_hit = True
        elif side == "short" and current_price <= target_price:
            is_hit = True
        
        if is_hit:
            return ExitSignal(
                exit_type=ExitType.THESIS_TAKE_PROFIT,
                confidence=1.0,
                triggered_at=datetime.utcnow(),
                trigger_value=current_price,
                threshold=target_price,
                message=f"Take-profit hit at {current_price}",
                recommended_action="scale_out_50"  # Scale out, don't full exit - let runner run
            )
        return None
    
    # =========================================================================
    # VOLATILITY EXITS (Authority: 70)
    # =========================================================================
    
    def check_vix_spike(
        self, 
        current_vix: float, 
        entry_vix: float,
        vix_spike_threshold_pct: float = 20
    ) -> Optional[ExitSignal]:
        """Check for VIX spike since entry."""
        if entry_vix is None or entry_vix <= 0:
            return None
        
        vix_change_pct = (current_vix - entry_vix) / entry_vix * 100
        
        if vix_change_pct > vix_spike_threshold_pct:
            return ExitSignal(
                exit_type=ExitType.VOL_VIX_SPIKE,
                confidence=min(1.0, vix_change_pct / vix_spike_threshold_pct / 2),
                triggered_at=datetime.utcnow(),
                trigger_value=vix_change_pct,
                threshold=vix_spike_threshold_pct,
                message=f"VIX spike: {vix_change_pct:.1f}% increase",
                recommended_action="tighten"
            )
        return None
    
    def check_vol_expansion(
        self, 
        current_atr_pct: float, 
        avg_atr_pct: float,
        expansion_threshold: float = 1.5
    ) -> Optional[ExitSignal]:
        """Check for volatility expansion (ATR expansion)."""
        if avg_atr_pct is None or avg_atr_pct <= 0:
            return None
        
        expansion_ratio = current_atr_pct / avg_atr_pct
        
        if expansion_ratio > expansion_threshold:
            return ExitSignal(
                exit_type=ExitType.VOL_EXPANSION,
                confidence=min(1.0, expansion_ratio / expansion_threshold / 2),
                triggered_at=datetime.utcnow(),
                trigger_value=expansion_ratio,
                threshold=expansion_threshold,
                message=f"Vol expansion: {expansion_ratio:.2f}x normal ATR",
                recommended_action="tighten"
            )
        return None
    
    def check_regime_change(
        self, 
        entry_regime: str, 
        current_regime: str
    ) -> Optional[ExitSignal]:
        """Check for market regime change."""
        if entry_regime == current_regime:
            return None
        
        # Define adverse regime changes
        adverse_changes = {
            ("trending", "choppy"): 0.8,
            ("trending", "volatile"): 0.9,
            ("low_vol", "high_vol"): 0.7,
        }
        
        change_key = (entry_regime, current_regime)
        if change_key in adverse_changes:
            return ExitSignal(
                exit_type=ExitType.VOL_REGIME_CHANGE,
                confidence=adverse_changes[change_key],
                triggered_at=datetime.utcnow(),
                message=f"Regime change: {entry_regime} -> {current_regime}",
                recommended_action="tighten"
            )
        return None
    
    # =========================================================================
    # PROBABILITY EXITS (Authority: 50)
    # =========================================================================
    
    def check_mfe_pattern(
        self, 
        current_mfe_pct: float,
        historical_avg_mfe: float,
        mfe_ratio_threshold: float = 0.8
    ) -> Optional[ExitSignal]:
        """Check if MFE has reached historical limits."""
        if historical_avg_mfe is None or historical_avg_mfe <= 0:
            return None
        
        mfe_ratio = current_mfe_pct / historical_avg_mfe
        
        if mfe_ratio > mfe_ratio_threshold:
            return ExitSignal(
                exit_type=ExitType.PROB_MFE_PATTERN,
                confidence=min(1.0, mfe_ratio),
                triggered_at=datetime.utcnow(),
                trigger_value=mfe_ratio,
                threshold=mfe_ratio_threshold,
                message=f"MFE at {mfe_ratio:.0%} of historical avg - consider scaling out",
                recommended_action="scale_out_25"
            )
        return None
    
    def check_low_continuation(
        self, 
        continuation_prob: float,
        continuation_threshold: float = 0.3
    ) -> Optional[ExitSignal]:
        """Check if continuation probability is low."""
        if continuation_prob < continuation_threshold:
            return ExitSignal(
                exit_type=ExitType.PROB_LOW_CONTINUATION,
                confidence=1.0 - continuation_prob,
                triggered_at=datetime.utcnow(),
                trigger_value=continuation_prob,
                threshold=continuation_threshold,
                message=f"Low continuation probability: {continuation_prob:.0%}",
                recommended_action="scale_out_50"
            )
        return None
    
    # =========================================================================
    # TIME DECAY EXITS (Authority: 40)
    # =========================================================================
    
    def check_max_hold(
        self, 
        hold_minutes: int,
        max_hold_minutes: int = 240
    ) -> Optional[ExitSignal]:
        """Check if max hold time exceeded."""
        if hold_minutes > max_hold_minutes:
            return ExitSignal(
                exit_type=ExitType.TIME_MAX_HOLD,
                confidence=min(1.0, hold_minutes / max_hold_minutes),
                triggered_at=datetime.utcnow(),
                trigger_value=hold_minutes,
                threshold=max_hold_minutes,
                message=f"Max hold time exceeded: {hold_minutes}m > {max_hold_minutes}m",
                recommended_action="full_exit"
            )
        return None
    
    def check_theta_decay(
        self, 
        theta: float,
        premium: float,
        theta_decay_threshold_pct: float = 5.0
    ) -> Optional[ExitSignal]:
        """Check if theta decay is eating position."""
        if theta is None or premium is None or premium <= 0:
            return None
        
        theta_as_pct = abs(theta) / premium * 100
        
        if theta_as_pct > theta_decay_threshold_pct:
            return ExitSignal(
                exit_type=ExitType.TIME_THETA_DECAY,
                confidence=min(1.0, theta_as_pct / theta_decay_threshold_pct),
                triggered_at=datetime.utcnow(),
                trigger_value=theta_as_pct,
                threshold=theta_decay_threshold_pct,
                message=f"Theta eating {theta_as_pct:.1f}% of premium daily",
                recommended_action="full_exit"
            )
        return None
    
    def check_session_end(
        self, 
        minutes_to_close: int,
        flatten_threshold_min: int = 30
    ) -> Optional[ExitSignal]:
        """Check if session end approaching."""
        if minutes_to_close <= flatten_threshold_min:
            return ExitSignal(
                exit_type=ExitType.TIME_SESSION_END,
                confidence=1.0 - (minutes_to_close / flatten_threshold_min),
                triggered_at=datetime.utcnow(),
                trigger_value=minutes_to_close,
                threshold=flatten_threshold_min,
                message=f"Session end in {minutes_to_close}m",
                recommended_action="full_exit"
            )
        return None
    
    def check_stalling(
        self, 
        hold_minutes: int,
        pnl_pct: float,
        stall_threshold_minutes: int = 60,
        stall_pnl_threshold: float = 0.2
    ) -> Optional[ExitSignal]:
        """Check if position is stalling (held long with minimal movement)."""
        if hold_minutes > stall_threshold_minutes and abs(pnl_pct) < stall_pnl_threshold:
            return ExitSignal(
                exit_type=ExitType.TIME_STALLING,
                confidence=0.6,
                triggered_at=datetime.utcnow(),
                message=f"Position stalling: {hold_minutes}m with only {pnl_pct:.2f}% P&L",
                recommended_action="full_exit"
            )
        return None


# Singleton
_evaluator: Optional[ExitTypeEvaluator] = None
_resolver: Optional[ExitTypeResolver] = None


def get_exit_type_evaluator(config: Optional[Dict[str, Any]] = None) -> ExitTypeEvaluator:
    """Get singleton ExitTypeEvaluator instance."""
    global _evaluator
    if _evaluator is None:
        _evaluator = ExitTypeEvaluator(config)
    return _evaluator


def get_exit_type_resolver() -> ExitTypeResolver:
    """Get ExitTypeResolver (stateless, but keeping pattern)."""
    global _resolver
    if _resolver is None:
        _resolver = ExitTypeResolver()
    return _resolver
