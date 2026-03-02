"""
TradeHealthScorer - Live Position Health Assessment for ExitBot v2
===================================================================

Every open trade gets a live health score (0-100) that updates every bar.
This is the "pulse" of each position - answering the question:
"Is this trade alive and healthy, or is it dying?"

Score Ranges:
- 80-100: Let it breathe - trade is healthy
- 60-80:  Tighten slowly - early warning signs
- 40-60:  Prepare partial exit - trade is struggling
- <40:    Exit on next favorable liquidity - trade is failing

Inputs:
- Price vs VWAP (directional alignment)
- Price vs entry thesis level
- Delta behavior (for options - is delta helping or betraying?)
- Theta burn vs price progress
- Volume confirmation or divergence
- Time decay vs distance to target

No emotions. Just scores.
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..core.logging import get_logger


class HealthPriority(Enum):
    """Priority level for exit consideration based on health score."""
    LET_BREATHE = "let_breathe"       # 80-100: healthy
    TIGHTEN_SLOWLY = "tighten_slowly" # 60-80: early warning
    PREPARE_EXIT = "prepare_exit"     # 40-60: struggling
    EXIT_NOW = "exit_now"             # <40: failing


@dataclass
class HealthScore:
    """
    Health assessment for a single position.
    """
    position_key: str
    score: int                       # 0-100 overall score
    priority: HealthPriority
    
    # Component scores (0-100 each, weighted into overall)
    price_vs_vwap_score: int         # Is price on right side of VWAP?
    thesis_alignment_score: int      # Is trade thesis still valid?
    momentum_score: int              # Is momentum with or against us?
    volume_score: int                # Is volume confirming?
    time_score: int                  # How long have we held?
    pnl_score: int                   # Current P&L health
    
    # Options-specific (None for equities)
    delta_score: Optional[int]       # Is delta helping?
    theta_score: Optional[int]       # Theta burn vs progress
    
    # Flags
    thesis_alive: bool
    momentum_divergence: bool        # Volume/price divergence
    stalling: bool                   # Trade is flat/stalling
    
    # Context
    timestamp: str
    notes: str


@dataclass
class PositionContext:
    """
    Context data for health scoring a position.
    """
    position_key: str
    symbol: str
    side: str                        # "long" or "short"
    asset_class: str                 # "us_equity", "crypto", "option"
    
    # Prices
    entry_price: float
    current_price: float
    vwap: Optional[float]
    
    # P&L
    unrealized_pnl_pct: float
    mfe_pct: float                   # Max favorable excursion
    mae_pct: float                   # Max adverse excursion
    
    # Thesis
    stop_price: Optional[float]
    target_price: Optional[float]
    
    # Market context
    volume: Optional[int]
    avg_volume: Optional[int]
    atr_pct: Optional[float]
    
    # Timing
    entry_time: datetime
    current_time: datetime
    max_hold_minutes: Optional[int]
    
    # Options-specific
    delta: Optional[float]
    delta_at_entry: Optional[float]
    theta: Optional[float]
    theta_at_entry: Optional[float]
    iv: Optional[float]
    iv_at_entry: Optional[float]
    dte: Optional[int]


class TradeHealthScorer:
    """
    Live health scorer for open positions.
    
    Provides a 0-100 health score for each position based on
    multiple factors. Higher scores = healthier trades.
    """
    
    # Weights for component scores (must sum to 1.0)
    WEIGHTS = {
        "price_vs_vwap": 0.15,
        "thesis_alignment": 0.20,
        "momentum": 0.15,
        "volume": 0.10,
        "time": 0.15,
        "pnl": 0.25
    }
    
    # Options-specific weights (replaces some base weights)
    OPTIONS_WEIGHTS = {
        "price_vs_vwap": 0.10,
        "thesis_alignment": 0.15,
        "momentum": 0.10,
        "volume": 0.05,
        "time": 0.10,
        "pnl": 0.20,
        "delta": 0.15,
        "theta": 0.15
    }
    
    def __init__(self):
        self._logger = get_logger()
        self._logger.log("trade_health_scorer_initialized", {})
    
    def score_position(self, ctx: PositionContext) -> HealthScore:
        """
        Calculate health score for a position.
        
        Args:
            ctx: Position context with all relevant data
            
        Returns:
            HealthScore with overall and component scores
        """
        is_option = ctx.asset_class == "option"
        
        # Calculate component scores
        vwap_score = self._score_price_vs_vwap(ctx)
        thesis_score = self._score_thesis_alignment(ctx)
        momentum_score = self._score_momentum(ctx)
        volume_score = self._score_volume(ctx)
        time_score = self._score_time(ctx)
        pnl_score = self._score_pnl(ctx)
        
        # Options-specific scores
        delta_score = None
        theta_score = None
        if is_option:
            delta_score = self._score_delta(ctx)
            theta_score = self._score_theta(ctx)
        
        # Calculate weighted overall score
        if is_option and delta_score is not None and theta_score is not None:
            weights = self.OPTIONS_WEIGHTS
            overall = (
                vwap_score * weights["price_vs_vwap"] +
                thesis_score * weights["thesis_alignment"] +
                momentum_score * weights["momentum"] +
                volume_score * weights["volume"] +
                time_score * weights["time"] +
                pnl_score * weights["pnl"] +
                delta_score * weights["delta"] +
                theta_score * weights["theta"]
            )
        else:
            weights = self.WEIGHTS
            overall = (
                vwap_score * weights["price_vs_vwap"] +
                thesis_score * weights["thesis_alignment"] +
                momentum_score * weights["momentum"] +
                volume_score * weights["volume"] +
                time_score * weights["time"] +
                pnl_score * weights["pnl"]
            )
        
        score = int(round(overall))
        
        # Determine priority
        if score >= 80:
            priority = HealthPriority.LET_BREATHE
        elif score >= 60:
            priority = HealthPriority.TIGHTEN_SLOWLY
        elif score >= 40:
            priority = HealthPriority.PREPARE_EXIT
        else:
            priority = HealthPriority.EXIT_NOW
        
        # Detect flags
        thesis_alive = thesis_score >= 50
        momentum_divergence = self._detect_momentum_divergence(ctx)
        stalling = self._detect_stalling(ctx)
        
        # Generate notes
        notes = self._generate_notes(
            score, thesis_alive, momentum_divergence, stalling,
            vwap_score, pnl_score, is_option
        )
        
        health = HealthScore(
            position_key=ctx.position_key,
            score=score,
            priority=priority,
            price_vs_vwap_score=vwap_score,
            thesis_alignment_score=thesis_score,
            momentum_score=momentum_score,
            volume_score=volume_score,
            time_score=time_score,
            pnl_score=pnl_score,
            delta_score=delta_score,
            theta_score=theta_score,
            thesis_alive=thesis_alive,
            momentum_divergence=momentum_divergence,
            stalling=stalling,
            timestamp=datetime.utcnow().isoformat() + "Z",
            notes=notes
        )
        
        self._logger.log("trade_health_scored", {
            "position_key": ctx.position_key,
            "symbol": ctx.symbol,
            "score": score,
            "priority": priority.value,
            "thesis_alive": thesis_alive
        })
        
        return health
    
    def _score_price_vs_vwap(self, ctx: PositionContext) -> int:
        """Score price position relative to VWAP (0-100)."""
        if ctx.vwap is None or ctx.vwap <= 0:
            return 50  # No VWAP data - neutral
        
        price = ctx.current_price
        vwap = ctx.vwap
        
        # Calculate distance from VWAP as percentage
        distance_pct = ((price - vwap) / vwap) * 100
        
        if ctx.side == "long":
            # Longs want price above VWAP
            if distance_pct > 1.0:
                return 100
            elif distance_pct > 0.5:
                return 85
            elif distance_pct > 0:
                return 70
            elif distance_pct > -0.5:
                return 50
            elif distance_pct > -1.0:
                return 35
            else:
                return 20
        else:
            # Shorts want price below VWAP
            if distance_pct < -1.0:
                return 100
            elif distance_pct < -0.5:
                return 85
            elif distance_pct < 0:
                return 70
            elif distance_pct < 0.5:
                return 50
            elif distance_pct < 1.0:
                return 35
            else:
                return 20
    
    def _score_thesis_alignment(self, ctx: PositionContext) -> int:
        """Score whether trade thesis is still valid (0-100)."""
        # If we have explicit stop/target, check against those
        if ctx.stop_price and ctx.stop_price > 0:
            if ctx.side == "long":
                # Long thesis invalid if below stop
                if ctx.current_price <= ctx.stop_price:
                    return 0
                elif ctx.target_price and ctx.current_price >= ctx.target_price:
                    return 100
                else:
                    # Score based on position between stop and target
                    if ctx.target_price and ctx.target_price > ctx.stop_price:
                        range_size = ctx.target_price - ctx.stop_price
                        progress = ctx.current_price - ctx.stop_price
                        return int(min(100, max(0, (progress / range_size) * 100)))
            else:
                # Short thesis
                if ctx.current_price >= ctx.stop_price:
                    return 0
                elif ctx.target_price and ctx.current_price <= ctx.target_price:
                    return 100
                else:
                    if ctx.target_price and ctx.target_price < ctx.stop_price:
                        range_size = ctx.stop_price - ctx.target_price
                        progress = ctx.stop_price - ctx.current_price
                        return int(min(100, max(0, (progress / range_size) * 100)))
        
        # Fallback: use P&L as proxy for thesis
        pnl = ctx.unrealized_pnl_pct
        if pnl > 3:
            return 100
        elif pnl > 1:
            return 80
        elif pnl > 0:
            return 60
        elif pnl > -1:
            return 40
        elif pnl > -3:
            return 25
        else:
            return 10
    
    def _score_momentum(self, ctx: PositionContext) -> int:
        """Score momentum alignment (0-100)."""
        # Compare current P&L to MFE - are we gaining or losing momentum?
        mfe = ctx.mfe_pct
        current = ctx.unrealized_pnl_pct
        
        if mfe <= 0:
            # No positive excursion yet
            if current >= 0:
                return 60  # Flat but not losing
            elif current > -1:
                return 40
            else:
                return 20
        
        # How much of MFE have we retained?
        retained = current / mfe if mfe > 0 else 0
        
        if retained >= 0.9:
            return 100  # Still at/near highs
        elif retained >= 0.7:
            return 80
        elif retained >= 0.5:
            return 60
        elif retained >= 0.3:
            return 40
        else:
            return 20
    
    def _score_volume(self, ctx: PositionContext) -> int:
        """Score volume confirmation (0-100)."""
        if ctx.volume is None or ctx.avg_volume is None or ctx.avg_volume <= 0:
            return 50  # No volume data - neutral
        
        relative_volume = ctx.volume / ctx.avg_volume
        
        # Higher volume = more conviction in the move
        if relative_volume >= 2.0:
            return 100 if ctx.unrealized_pnl_pct > 0 else 30
        elif relative_volume >= 1.5:
            return 85 if ctx.unrealized_pnl_pct > 0 else 40
        elif relative_volume >= 1.0:
            return 70
        elif relative_volume >= 0.5:
            return 50
        else:
            return 35  # Low volume is concerning
    
    def _score_time(self, ctx: PositionContext) -> int:
        """Score time in trade vs max hold (0-100)."""
        if ctx.max_hold_minutes is None:
            # No time limit - give moderate score based on age
            minutes_held = (ctx.current_time - ctx.entry_time).total_seconds() / 60
            if minutes_held < 5:
                return 100  # Fresh trade
            elif minutes_held < 15:
                return 85
            elif minutes_held < 30:
                return 70
            elif minutes_held < 60:
                return 55
            else:
                return 40  # Getting stale
        
        # Calculate time pressure
        minutes_held = (ctx.current_time - ctx.entry_time).total_seconds() / 60
        time_used_pct = (minutes_held / ctx.max_hold_minutes) * 100
        
        if time_used_pct < 25:
            return 100
        elif time_used_pct < 50:
            return 80
        elif time_used_pct < 75:
            return 60
        elif time_used_pct < 90:
            return 40
        else:
            return 20  # Almost out of time
    
    def _score_pnl(self, ctx: PositionContext) -> int:
        """Score current P&L status (0-100)."""
        pnl = ctx.unrealized_pnl_pct
        
        if pnl >= 5:
            return 100
        elif pnl >= 3:
            return 90
        elif pnl >= 2:
            return 80
        elif pnl >= 1:
            return 70
        elif pnl >= 0.5:
            return 60
        elif pnl >= 0:
            return 50
        elif pnl >= -0.5:
            return 40
        elif pnl >= -1:
            return 30
        elif pnl >= -2:
            return 20
        else:
            return 10
    
    def _score_delta(self, ctx: PositionContext) -> int:
        """Score delta behavior for options (0-100)."""
        if ctx.delta is None or ctx.delta_at_entry is None:
            return 50  # No delta data
        
        delta = ctx.delta
        entry_delta = ctx.delta_at_entry
        
        # For long calls/puts, we want delta to increase (more ITM)
        # Delta betrayal = delta decreasing when we expected increase
        delta_change = abs(delta) - abs(entry_delta)
        
        if ctx.side == "long":
            # Long options want delta to increase (deeper ITM)
            if delta_change > 0.1:
                return 100
            elif delta_change > 0.05:
                return 80
            elif delta_change > 0:
                return 65
            elif delta_change > -0.05:
                return 50
            elif delta_change > -0.1:
                return 35
            else:
                return 20  # Delta betrayal
        else:
            # Short options - opposite
            if delta_change < -0.1:
                return 100
            elif delta_change < -0.05:
                return 80
            elif delta_change < 0:
                return 65
            elif delta_change < 0.05:
                return 50
            else:
                return 30
    
    def _score_theta(self, ctx: PositionContext) -> int:
        """Score theta burn efficiency for options (0-100)."""
        if ctx.theta is None or ctx.dte is None:
            return 50  # No theta data
        
        theta = ctx.theta
        pnl = ctx.unrealized_pnl_pct
        
        # For long options, theta is negative (time decay hurts us)
        # We want P&L to outpace theta decay
        if ctx.side == "long":
            # Long options: theta is working against us
            if pnl > 0 and abs(theta) < pnl:
                return 90  # Winning faster than decay
            elif pnl > 0:
                return 70  # Winning but decay is a factor
            elif pnl > abs(theta) * -1:
                return 50  # Losing but not faster than decay
            else:
                return 30  # Theta crushing us
        else:
            # Short options: theta works for us
            if theta > 0 and pnl > 0:
                return 100  # Theta helping and winning
            elif theta > 0:
                return 70  # Theta helping
            else:
                return 40
    
    def _detect_momentum_divergence(self, ctx: PositionContext) -> bool:
        """Detect if volume and price are diverging."""
        if ctx.volume is None or ctx.avg_volume is None:
            return False
        
        relative_volume = ctx.volume / ctx.avg_volume if ctx.avg_volume > 0 else 1
        
        # Bearish divergence: Price up but volume declining
        if ctx.unrealized_pnl_pct > 1 and relative_volume < 0.7:
            return True
        
        # Bullish divergence for shorts: Price down but volume declining
        if ctx.unrealized_pnl_pct < -1 and relative_volume < 0.7 and ctx.side == "short":
            return True
        
        return False
    
    def _detect_stalling(self, ctx: PositionContext) -> bool:
        """Detect if trade is stalling (not making progress)."""
        mfe = ctx.mfe_pct
        current = ctx.unrealized_pnl_pct
        
        # Stalling if we reached a significant MFE but have given back most of it
        if mfe > 1 and current < mfe * 0.3:
            return True
        
        # Stalling if trade is old but hasn't moved much
        minutes_held = (ctx.current_time - ctx.entry_time).total_seconds() / 60
        if minutes_held > 10 and abs(current) < 0.3:
            return True
        
        return False
    
    def _generate_notes(
        self,
        score: int,
        thesis_alive: bool,
        momentum_divergence: bool,
        stalling: bool,
        vwap_score: int,
        pnl_score: int,
        is_option: bool
    ) -> str:
        """Generate human-readable notes for the health assessment."""
        notes = []
        
        if score >= 80:
            notes.append("Trade is healthy")
        elif score >= 60:
            notes.append("Early warning signs")
        elif score >= 40:
            notes.append("Trade is struggling")
        else:
            notes.append("Trade is failing")
        
        if not thesis_alive:
            notes.append("Thesis invalidated")
        
        if momentum_divergence:
            notes.append("Volume/price divergence")
        
        if stalling:
            notes.append("Trade is stalling")
        
        if vwap_score < 40:
            notes.append("Wrong side of VWAP")
        
        if pnl_score < 30:
            notes.append("Deep in red")
        
        return " | ".join(notes)


# Global singleton
_health_scorer: Optional[TradeHealthScorer] = None


def get_trade_health_scorer() -> TradeHealthScorer:
    """Get or create the global TradeHealthScorer instance."""
    global _health_scorer
    if _health_scorer is None:
        _health_scorer = TradeHealthScorer()
    return _health_scorer
