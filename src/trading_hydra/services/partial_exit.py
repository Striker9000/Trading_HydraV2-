"""
PartialExitDoctrine - 25/25/50 Scale-Out Structure for ExitBot v2 Elite
=========================================================================

Implements the tiered scale-out doctrine with runner protection.

Scale-Out Structure:
- Tier 1: 25% at initial target (lock in profit)
- Tier 2: 25% at 1.5x target (more profit secured)
- Tier 3: 50% runs with trailing stop (let winner run)

Once 50% profit is captured and exited, the remainder becomes a "runner"
that runs with a trailing stop that tightens as profit increases.

Key Concepts:
- ScaleOutState: Tracks position's scaling progress
- ScaleOutDecision: Decision about whether to scale and which tier
- Runner Protection: Trailing stop tightens as profit grows
- Historical Context: Uses past trade data to guide scale triggers
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from .trade_health import HealthScore, PositionContext
from ..core.logging import get_logger


class ScaleTier(Enum):
    """Scale-out tiers in the 25/25/50 doctrine."""
    TIER_1 = "tier_1"      # First 25% at initial target
    TIER_2 = "tier_2"      # Second 25% at 1.5x target
    RUNNER = "runner"      # Remaining 50% with trailing stop


@dataclass
class ScaleOutState:
    """
    Tracks the scaling status of a position through the 25/25/50 doctrine.
    
    Answers:
    - How much has been exited?
    - Is this position a runner?
    - What's the scaling progress?
    """
    position_key: str
    original_qty: float
    remaining_qty: float
    
    # Tier 1 state (first 25%)
    tier_1_exited: bool = False
    tier_1_exit_price: Optional[float] = None
    tier_1_exit_qty: float = 0.0
    tier_1_exit_time: Optional[datetime] = None
    
    # Tier 2 state (second 25%)
    tier_2_exited: bool = False
    tier_2_exit_price: Optional[float] = None
    tier_2_exit_qty: float = 0.0
    tier_2_exit_time: Optional[datetime] = None
    
    # Runner state (remaining 50%)
    runner_active: bool = False
    runner_qty: float = 0.0
    runner_entry_price: Optional[float] = None
    runner_entry_time: Optional[datetime] = None
    runner_max_profit_pct: float = 0.0  # Tracks highest profit runner achieved
    
    # Tracking
    first_scale_time: Optional[datetime] = None
    last_scale_time: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def total_exited_qty(self) -> float:
        """Total quantity exited so far."""
        return self.tier_1_exit_qty + self.tier_2_exit_qty
    
    @property
    def total_exited_pct(self) -> float:
        """Percentage of original position exited."""
        if self.original_qty <= 0:
            return 0.0
        return (self.total_exited_qty / self.original_qty) * 100
    
    @property
    def scale_progress(self) -> str:
        """Human-readable scale progress."""
        if self.total_exited_pct == 0:
            return "no_scales"
        elif self.tier_1_exited and not self.tier_2_exited:
            return "tier_1_only"
        elif self.tier_1_exited and self.tier_2_exited:
            return "both_tiers_exited"
        else:
            return "unknown"
    
    def get_age_seconds(self) -> float:
        """Get age of this position in seconds."""
        return (datetime.utcnow() - self.created_at).total_seconds()


@dataclass
class ScaleOutDecision:
    """
    Decision about whether/how to scale out of a position.
    
    Attributes:
        should_scale: Whether to scale out
        scale_tier: Which tier (if should_scale is True)
        qty_to_exit: Quantity to exit
        exit_price_target: Suggested price for exit
        trigger_reason: What triggered the decision
        confidence: Confidence level 0.0-1.0
        mfe_ratio: MFE ratio at decision time
        time_in_trade_pct: Percentage of typical hold time elapsed
        health_score: Trade health score 0-100
        decision_time: When decision was made
    """
    should_scale: bool
    scale_tier: Optional[ScaleTier] = None
    qty_to_exit: float = 0.0
    exit_price_target: Optional[float] = None
    
    # Reasoning
    trigger_reason: str = ""  # "mfe_ratio", "time_based", "momentum_fade", "none"
    confidence: float = 0.0
    
    # Context
    mfe_ratio: Optional[float] = None
    time_in_trade_pct: Optional[float] = None
    health_score: Optional[int] = None
    
    # Timestamp
    decision_time: datetime = field(default_factory=datetime.utcnow)


class PartialExitManager:
    """
    Manages 25/25/50 scale-out decisions and runner protection.
    
    Singleton instance accessed via get_partial_exit_manager().
    """
    
    _instance = None
    
    def __new__(cls):
        """Implement singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize the manager (safe to call multiple times due to singleton)."""
        if not hasattr(self, '_initialized'):
            self._logger = get_logger()
            self._state_map: Dict[str, ScaleOutState] = {}
            self._historical_data: Dict[str, Dict[str, Any]] = {}
            self._initialized = True
            self._logger.log("partial_exit_manager_initialized", {})
    
    # =========================================================================
    # STATE MANAGEMENT
    # =========================================================================
    
    def create_scale_state(self, position_key: str, original_qty: float) -> ScaleOutState:
        """
        Create initial scale state for a new position.
        
        Args:
            position_key: Unique position identifier
            original_qty: Original position quantity
            
        Returns:
            New ScaleOutState
        """
        state = ScaleOutState(
            position_key=position_key,
            original_qty=original_qty,
            remaining_qty=original_qty
        )
        self._state_map[position_key] = state
        
        self._logger.log("scale_state_created", {
            "position_key": position_key,
            "original_qty": original_qty
        })
        
        return state
    
    def get_scale_state(self, position_key: str) -> Optional[ScaleOutState]:
        """Retrieve existing scale state."""
        return self._state_map.get(position_key)
    
    def update_scale_state(
        self,
        position_key: str,
        qty_exited: float,
        exit_price: float,
        scale_tier: ScaleTier
    ) -> ScaleOutState:
        """
        Update scale state after an exit has been executed.
        
        Args:
            position_key: Position identifier
            qty_exited: Quantity that was just exited
            exit_price: Price at which exit occurred
            scale_tier: Which tier was exited
            
        Returns:
            Updated ScaleOutState
            
        Raises:
            ValueError: If no scale state exists for position
        """
        state = self._state_map.get(position_key)
        if not state:
            raise ValueError(f"No scale state for position {position_key}")
        
        # Update remaining qty
        state.remaining_qty -= qty_exited
        state.remaining_qty = max(0.0, state.remaining_qty)
        
        # Update tier-specific tracking
        if scale_tier == ScaleTier.TIER_1:
            state.tier_1_exited = True
            state.tier_1_exit_qty = qty_exited
            state.tier_1_exit_price = exit_price
            state.tier_1_exit_time = datetime.utcnow()
            if not state.first_scale_time:
                state.first_scale_time = datetime.utcnow()
        
        elif scale_tier == ScaleTier.TIER_2:
            state.tier_2_exited = True
            state.tier_2_exit_qty = qty_exited
            state.tier_2_exit_price = exit_price
            state.tier_2_exit_time = datetime.utcnow()
        
        elif scale_tier == ScaleTier.RUNNER:
            # Remaining qty becomes the runner
            state.runner_active = True
            state.runner_qty = state.remaining_qty
            state.runner_entry_price = exit_price
            state.runner_entry_time = datetime.utcnow()
        
        state.last_scale_time = datetime.utcnow()
        
        self._logger.log("scale_state_updated", {
            "position_key": position_key,
            "qty_exited": qty_exited,
            "exit_price": exit_price,
            "scale_tier": scale_tier.value,
            "remaining_qty": state.remaining_qty,
            "scale_progress": state.scale_progress
        })
        
        return state
    
    def is_runner(self, state: ScaleOutState) -> bool:
        """
        Determine if a position has runner status.
        
        A position becomes a "runner" after both Tier 1 and Tier 2 have
        been exited, leaving the final 50% to run.
        
        Args:
            state: ScaleOutState to check
            
        Returns:
            True if position is a runner
        """
        return state.runner_active
    
    # =========================================================================
    # SCALE-OUT DECISIONS
    # =========================================================================
    
    def should_scale_out(
        self,
        position_context: PositionContext,
        health_score: HealthScore,
        scale_state: ScaleOutState,
        historical_context: Optional[Dict[str, Any]] = None
    ) -> ScaleOutDecision:
        """
        Determine if position should be scaled out and which tier.
        
        Evaluation order:
        1. Check if Tier 1 should be taken
        2. If Tier 1 taken, check if Tier 2 should be taken
        3. If both tiers taken, position runs as runner (no scale)
        
        Args:
            position_context: Current position state
            health_score: Latest trade health assessment
            scale_state: Position's scaling state
            historical_context: Historical MFE/MAE data, avg hold time, etc.
            
        Returns:
            ScaleOutDecision with recommendation
        """
        historical_context = historical_context or {}
        
        # Can't scale what's already been exited
        if scale_state.total_exited_pct >= 100:
            return ScaleOutDecision(should_scale=False)
        
        # Tier 1: 25% at initial target
        if not scale_state.tier_1_exited:
            tier1_decision = self._evaluate_tier_1(
                position_context, health_score, scale_state, historical_context
            )
            if tier1_decision.should_scale:
                return tier1_decision
        
        # Tier 2: 25% at 1.5x target
        if scale_state.tier_1_exited and not scale_state.tier_2_exited:
            tier2_decision = self._evaluate_tier_2(
                position_context, health_score, scale_state, historical_context
            )
            if tier2_decision.should_scale:
                return tier2_decision
        
        # Runner doesn't scale - it runs until stop loss
        return ScaleOutDecision(should_scale=False)
    
    def _evaluate_tier_1(
        self,
        position_context: PositionContext,
        health_score: HealthScore,
        scale_state: ScaleOutState,
        historical_context: Dict[str, Any]
    ) -> ScaleOutDecision:
        """
        Evaluate if Tier 1 (first 25% scale) should trigger.
        
        Tier 1 triggers on:
        - MFE ratio > 1.2 (current profit > 1.2x typical profit)
        - Time-based: profitable at 50% of typical hold time
        
        Args:
            position_context: Position context
            health_score: Trade health score
            scale_state: Position scale state
            historical_context: Historical data
            
        Returns:
            ScaleOutDecision
        """
        # MFE-based trigger
        avg_mfe = historical_context.get("avg_mfe_pct", None)
        if avg_mfe and avg_mfe > 0:
            mfe_ratio = position_context.mfe_pct / avg_mfe
            if mfe_ratio >= 1.2:
                qty_to_exit = self.calculate_scale_amount(
                    scale_state.remaining_qty,
                    ScaleTier.TIER_1
                )
                return ScaleOutDecision(
                    should_scale=True,
                    scale_tier=ScaleTier.TIER_1,
                    qty_to_exit=qty_to_exit,
                    exit_price_target=position_context.current_price,
                    trigger_reason="mfe_ratio",
                    confidence=min(1.0, mfe_ratio / 2.0),
                    mfe_ratio=mfe_ratio,
                    health_score=health_score.score
                )
        
        # Time-based trigger: profitable at 50% of typical hold time?
        avg_hold_minutes = historical_context.get("avg_hold_minutes", None)
        if avg_hold_minutes and avg_hold_minutes > 0:
            minutes_held = (position_context.current_time - position_context.entry_time).total_seconds() / 60
            time_pct = (minutes_held / avg_hold_minutes) * 100
            
            if time_pct >= 50 and position_context.unrealized_pnl_pct > 0:
                qty_to_exit = self.calculate_scale_amount(
                    scale_state.remaining_qty,
                    ScaleTier.TIER_1
                )
                return ScaleOutDecision(
                    should_scale=True,
                    scale_tier=ScaleTier.TIER_1,
                    qty_to_exit=qty_to_exit,
                    exit_price_target=position_context.current_price,
                    trigger_reason="time_based",
                    confidence=min(1.0, time_pct / 100.0),
                    time_in_trade_pct=time_pct,
                    health_score=health_score.score
                )
        
        return ScaleOutDecision(should_scale=False)
    
    def _evaluate_tier_2(
        self,
        position_context: PositionContext,
        health_score: HealthScore,
        scale_state: ScaleOutState,
        historical_context: Dict[str, Any]
    ) -> ScaleOutDecision:
        """
        Evaluate if Tier 2 (second 25% scale) should trigger.
        
        Tier 2 triggers on:
        - MFE ratio > 1.5 (really good trade)
        - Momentum fade but still profitable
        - 1.5x the time it took to reach Tier 1
        
        Args:
            position_context: Position context
            health_score: Trade health score
            scale_state: Position scale state
            historical_context: Historical data
            
        Returns:
            ScaleOutDecision
        """
        # MFE-based trigger: higher threshold than Tier 1
        avg_mfe = historical_context.get("avg_mfe_pct", None)
        if avg_mfe and avg_mfe > 0:
            mfe_ratio = position_context.mfe_pct / avg_mfe
            if mfe_ratio >= 1.5:
                qty_to_exit = self.calculate_scale_amount(
                    scale_state.remaining_qty,
                    ScaleTier.TIER_2
                )
                return ScaleOutDecision(
                    should_scale=True,
                    scale_tier=ScaleTier.TIER_2,
                    qty_to_exit=qty_to_exit,
                    exit_price_target=position_context.current_price,
                    trigger_reason="mfe_ratio",
                    confidence=min(1.0, mfe_ratio / 3.0),
                    mfe_ratio=mfe_ratio,
                    health_score=health_score.score
                )
        
        # Momentum fade check: momentum declining but still profitable
        if health_score.momentum_score < 50 and position_context.unrealized_pnl_pct > 1:
            qty_to_exit = self.calculate_scale_amount(
                scale_state.remaining_qty,
                ScaleTier.TIER_2
            )
            return ScaleOutDecision(
                should_scale=True,
                scale_tier=ScaleTier.TIER_2,
                qty_to_exit=qty_to_exit,
                exit_price_target=position_context.current_price,
                trigger_reason="momentum_fade",
                confidence=0.7,
                health_score=health_score.score
            )
        
        return ScaleOutDecision(should_scale=False)
    
    def calculate_scale_amount(
        self,
        remaining_qty: float,
        scale_tier: ScaleTier
    ) -> float:
        """
        Calculate quantity to exit for a given tier.
        
        Rules:
        - Tier 1: Exit 50% of remaining (which is 25% of original)
        - Tier 2: Exit 50% of remaining (which is 25% of original)
        - Runner: Exit nothing, let it run
        
        Args:
            remaining_qty: Quantity still remaining in position
            scale_tier: Which tier to calculate for
            
        Returns:
            Quantity to exit
        """
        if scale_tier == ScaleTier.TIER_1:
            # Exit 50% of what remains at Tier 1 (which is 50% of original)
            return remaining_qty * 0.5
        
        elif scale_tier == ScaleTier.TIER_2:
            # Exit 50% of what remains at Tier 2 (which is 50% of the remaining 50%)
            return remaining_qty * 0.5
        
        elif scale_tier == ScaleTier.RUNNER:
            # Runner doesn't scale, returns 0
            return 0.0
        
        return 0.0
    
    # =========================================================================
    # RUNNER MANAGEMENT
    # =========================================================================
    
    def get_runner_trailing_stop(
        self,
        runner_state: ScaleOutState,
        current_price: float,
        base_stop_loss_pct: float = 2.0
    ) -> Tuple[Optional[float], str]:
        """
        Calculate trailing stop for runner position.
        
        Runner stop tightens as profit increases:
        - Breakeven to 2% profit: 2% trailing stop
        - 2-5% profit: 1.5% trailing stop
        - 5-10% profit: 1% trailing stop
        - 10%+ profit: 0.5% trailing stop
        
        Args:
            runner_state: Runner position's scale state
            current_price: Current market price
            base_stop_loss_pct: Starting trailing stop percentage
            
        Returns:
            (stop_price, stop_type) tuple, or (None, "no_runner")
        """
        if not runner_state.runner_active:
            return None, "no_runner"
        
        if runner_state.runner_entry_price is None or runner_state.runner_entry_price <= 0:
            return None, "no_entry_price"
        
        # Calculate profit on runner
        profit_pct = ((current_price - runner_state.runner_entry_price) /
                      runner_state.runner_entry_price * 100)
        
        # Track max profit
        if profit_pct > runner_state.runner_max_profit_pct:
            runner_state.runner_max_profit_pct = profit_pct
        
        # Adjust trailing stop based on profit level
        if profit_pct < 0:
            # Loss - use base stop
            trailing_stop_pct = base_stop_loss_pct
            stop_type = "loss_protection"
        elif profit_pct < 2:
            # Small profit - tight stop
            trailing_stop_pct = 2.0
            stop_type = "small_profit"
        elif profit_pct < 5:
            # Medium profit - tighter
            trailing_stop_pct = 1.5
            stop_type = "medium_profit"
        elif profit_pct < 10:
            # Good profit - tighter still
            trailing_stop_pct = 1.0
            stop_type = "good_profit"
        else:
            # Excellent profit - let it run with tight stop
            trailing_stop_pct = 0.5
            stop_type = "excellent_profit"
        
        # Calculate actual stop price
        stop_price = current_price * (1 - (trailing_stop_pct / 100))
        
        self._logger.log("runner_trailing_stop_calculated", {
            "position_key": runner_state.position_key,
            "profit_pct": profit_pct,
            "max_profit_pct": runner_state.runner_max_profit_pct,
            "trailing_stop_pct": trailing_stop_pct,
            "stop_price": stop_price,
            "stop_type": stop_type
        })
        
        return stop_price, stop_type
    
    # =========================================================================
    # HISTORICAL CONTEXT MANAGEMENT
    # =========================================================================
    
    def register_historical_context(
        self,
        symbol: str,
        context: Dict[str, Any]
    ) -> None:
        """
        Register historical context for a symbol.
        
        Args:
            symbol: Symbol/asset to track
            context: Dict with keys like:
                - avg_mfe_pct: Average MFE percentage
                - avg_mae_pct: Average MAE percentage
                - avg_hold_minutes: Average hold time
                - success_rate: Win rate
                - typical_profit_range: (min, max) profit targets
        """
        self._historical_data[symbol] = context
        
        self._logger.log("historical_context_registered", {
            "symbol": symbol,
            "avg_mfe_pct": context.get("avg_mfe_pct"),
            "avg_hold_minutes": context.get("avg_hold_minutes")
        })
    
    def get_historical_context(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get historical context for a symbol."""
        return self._historical_data.get(symbol)
    
    def clear_position_state(self, position_key: str) -> None:
        """
        Remove state when position is closed.
        
        Args:
            position_key: Position to clear
        """
        if position_key in self._state_map:
            del self._state_map[position_key]
            self._logger.log("position_state_cleared", {
                "position_key": position_key
            })
    
    def get_all_active_positions(self) -> List[ScaleOutState]:
        """Get all active position scale states."""
        return list(self._state_map.values())


# ============================================================================
# SINGLETON ACCESSOR
# ============================================================================

_partial_exit_manager = None


def get_partial_exit_manager() -> PartialExitManager:
    """
    Get or create the singleton PartialExitManager instance.
    
    Returns:
        PartialExitManager singleton
    """
    global _partial_exit_manager
    if _partial_exit_manager is None:
        _partial_exit_manager = PartialExitManager()
    return _partial_exit_manager
