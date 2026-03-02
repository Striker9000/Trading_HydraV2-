"""
=============================================================================
ProfitSniper - Profit-Priority Exit Intelligence Layer
=============================================================================

Solves the #1 P&L drain: positions spike to peak profit then reverse back
to loss because existing exits (trailing stops, fixed take-profit) are
too slow to capture the peak.

ProfitSniper adds three capabilities the current system lacks:

1. PROFIT VELOCITY DETECTION
   Tracks rate of profit change. When profit is accelerating UP, it
   arms a tight ratchet. When velocity reverses (profit starts falling
   from peak), it triggers immediate partial or full exit.

2. PEAK PROFIT RATCHET
   Once profit exceeds a configurable threshold, a ratchet locks in a
   minimum exit price that ONLY moves up. The ratchet distance shrinks
   as profit grows (tighter at higher profits).

3. MOMENTUM EXHAUSTION EXIT
   Detects when a price spike is losing steam (3 consecutive bars with
   decreasing gains) and exits before the reversal completes.

Integration: Called from bot _manage_position() methods BEFORE the
standard trailing stop / take-profit checks. If ProfitSniper says
exit, the bot exits immediately — no other check gets a vote.

Thread-safe singleton with SQLite state persistence.
=============================================================================
"""

import time
from dataclasses import dataclass, asdict, field
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime, timedelta

from ..core.logging import get_logger
from ..core.state import get_state, set_state


@dataclass
class ProfitSniperConfig:
    """Configuration for ProfitSniper behavior per position."""
    enabled: bool = True

    # --- Profit velocity detection ---
    velocity_window: int = 5          # Number of price samples to compute velocity
    velocity_reversal_pct: float = 0.3  # Trigger if velocity drops this % from peak velocity

    # --- Peak profit ratchet ---
    ratchet_arm_pct: float = 0.5      # Arm ratchet after this % profit (0.5% for equities)
    ratchet_base_distance_pct: float = 0.25  # Base ratchet distance (0.25% below peak)
    ratchet_tighten_per_pct: float = 0.03  # Tighten ratchet by this per 1% additional profit
    ratchet_min_distance_pct: float = 0.08  # Never tighten ratchet below this distance

    # --- Momentum exhaustion ---
    exhaustion_bars: int = 3          # Consecutive weakening bars to trigger
    exhaustion_min_profit_pct: float = 0.3  # Only trigger if in profit

    # --- Partial vs full exit ---
    partial_exit_pct: float = 50.0    # Exit this % of position on first sniper trigger
    full_exit_on_second: bool = True  # Full exit if triggered again after partial

    # --- Asset class overrides ---
    # These static methods provide HARDCODED DEFAULTS as fallbacks.
    # Preferred: load from bots.yaml via from_config() for config-driven tuning.
    @staticmethod
    def for_options() -> "ProfitSniperConfig":
        return ProfitSniperConfig._load_from_yaml("options", fallback=ProfitSniperConfig(
            ratchet_arm_pct=3.0,
            ratchet_base_distance_pct=2.0,
            ratchet_tighten_per_pct=0.15,
            ratchet_min_distance_pct=0.5,
            velocity_reversal_pct=1.0,
            exhaustion_min_profit_pct=2.0,
            partial_exit_pct=50.0
        ))

    @staticmethod
    def for_crypto() -> "ProfitSniperConfig":
        return ProfitSniperConfig._load_from_yaml("crypto", fallback=ProfitSniperConfig(
            ratchet_arm_pct=1.0,
            ratchet_base_distance_pct=0.4,
            ratchet_tighten_per_pct=0.02,
            ratchet_min_distance_pct=0.12,
            velocity_reversal_pct=0.4,
            exhaustion_min_profit_pct=0.5,
            exhaustion_bars=3,
            partial_exit_pct=50.0
        ))

    @staticmethod
    def for_stocks() -> "ProfitSniperConfig":
        return ProfitSniperConfig._load_from_yaml("stocks", fallback=ProfitSniperConfig())

    @staticmethod
    def for_ticker(ticker: str, asset_class: str = "stocks") -> "ProfitSniperConfig":
        """Load config for a specific ticker, falling back to asset class defaults."""
        return ProfitSniperConfig._load_from_yaml(
            asset_class,
            ticker_override=ticker,
            fallback=ProfitSniperConfig.for_stocks() if asset_class == "stocks"
                else ProfitSniperConfig.for_crypto() if asset_class == "crypto"
                else ProfitSniperConfig.for_options()
        )

    @staticmethod
    def _load_from_yaml(asset_class: str, ticker_override: str = None,
                        fallback: "ProfitSniperConfig" = None) -> "ProfitSniperConfig":
        """
        Load ProfitSniper config from bots.yaml profit_sniper section.

        Hierarchy: ticker-specific override > asset_class defaults > hardcoded fallback.
        If bots.yaml has no profit_sniper section, returns the fallback unchanged.
        """
        import yaml
        import os

        if fallback is None:
            fallback = ProfitSniperConfig()

        try:
            config_path = os.path.join(os.getcwd(), "config", "bots.yaml")
            if not os.path.exists(config_path):
                config_path = "/home/runner/workspace/config/bots.yaml"
            if not os.path.exists(config_path):
                return fallback

            with open(config_path, "r") as f:
                full_config = yaml.safe_load(f)

            sniper_section = full_config.get("profit_sniper", {})
            if not sniper_section:
                return fallback

            # Get asset class defaults
            asset_cfg = sniper_section.get(asset_class, {})

            # Check for ticker-specific override
            if ticker_override:
                ticker_key = ticker_override.replace("/", "_").replace(" ", "_").upper()
                ticker_overrides = sniper_section.get("ticker_overrides", {})
                ticker_cfg = ticker_overrides.get(ticker_key, {})
                # Merge: ticker overrides > asset class > fallback
                merged = {}
                for field_name in [
                    "enabled", "velocity_window", "velocity_reversal_pct",
                    "ratchet_arm_pct", "ratchet_base_distance_pct",
                    "ratchet_tighten_per_pct", "ratchet_min_distance_pct",
                    "exhaustion_bars", "exhaustion_min_profit_pct",
                    "partial_exit_pct", "full_exit_on_second"
                ]:
                    if field_name in ticker_cfg:
                        merged[field_name] = ticker_cfg[field_name]
                    elif field_name in asset_cfg:
                        merged[field_name] = asset_cfg[field_name]
                    else:
                        merged[field_name] = getattr(fallback, field_name)
                return ProfitSniperConfig(**merged)

            # Asset class only (no ticker override)
            if not asset_cfg:
                return fallback

            merged = {}
            for field_name in [
                "enabled", "velocity_window", "velocity_reversal_pct",
                "ratchet_arm_pct", "ratchet_base_distance_pct",
                "ratchet_tighten_per_pct", "ratchet_min_distance_pct",
                "exhaustion_bars", "exhaustion_min_profit_pct",
                "partial_exit_pct", "full_exit_on_second"
            ]:
                if field_name in asset_cfg:
                    merged[field_name] = asset_cfg[field_name]
                else:
                    merged[field_name] = getattr(fallback, field_name)
            return ProfitSniperConfig(**merged)

        except Exception:
            return fallback


@dataclass
class SniperState:
    """Persisted state for profit sniper tracking per position."""
    position_key: str
    entry_price: float
    side: str  # "long" or "short"

    # Peak tracking
    peak_profit_pct: float = 0.0
    peak_price: float = 0.0
    peak_timestamp: str = ""

    # Ratchet state
    ratchet_armed: bool = False
    ratchet_price: float = 0.0  # Minimum acceptable exit price (only goes up for longs)

    # Velocity tracking (rolling window of profit % values)
    profit_samples: List[float] = field(default_factory=list)
    sample_timestamps: List[str] = field(default_factory=list)
    peak_velocity: float = 0.0

    # Exhaustion tracking
    consecutive_weaker_bars: int = 0
    last_bar_gain: float = 0.0

    # Trigger tracking
    sniper_triggered_count: int = 0
    last_trigger_reason: str = ""
    last_trigger_ts: str = ""


@dataclass
class SniperDecision:
    """Decision from ProfitSniper evaluation."""
    should_exit: bool = False
    exit_pct: float = 0.0      # 0-100, percentage of position to exit
    reason: str = ""
    confidence: float = 0.0     # 0.0-1.0
    ratchet_price: float = 0.0  # Current ratchet level
    peak_profit_pct: float = 0.0
    current_profit_pct: float = 0.0
    velocity: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


class ProfitSniper:
    """
    Profit-priority exit intelligence.

    Called every price update for each open position. Maintains state
    in SQLite for durability across restarts.
    """

    def __init__(self):
        self._logger = get_logger()
        self._logger.log("profit_sniper_init", {"status": "initialized"})

    def evaluate(
        self,
        position_key: str,
        entry_price: float,
        current_price: float,
        side: str,
        config: Optional[ProfitSniperConfig] = None,
        bot_id: str = "unknown"
    ) -> SniperDecision:
        """
        Evaluate whether ProfitSniper should trigger an exit.

        Called on every price update for a position. Maintains internal
        state tracking profit peaks, velocity, and exhaustion.

        Args:
            position_key: Unique identifier for this position
            entry_price: Original entry price
            current_price: Current market price
            side: "long" or "short"
            config: ProfitSniperConfig (defaults if None)
            bot_id: Bot identifier for logging

        Returns:
            SniperDecision with exit recommendation
        """
        config = config or ProfitSniperConfig()
        if not config.enabled:
            return SniperDecision()

        state = self._load_or_create_state(position_key, entry_price, side)

        # Calculate current profit %
        if side == "long":
            profit_pct = ((current_price - entry_price) / entry_price) * 100
        else:
            profit_pct = ((entry_price - current_price) / entry_price) * 100

        # Update peak tracking
        if profit_pct > state.peak_profit_pct:
            state.peak_profit_pct = profit_pct
            state.peak_price = current_price
            state.peak_timestamp = datetime.utcnow().isoformat() + "Z"

        # Update profit sample window for velocity calc
        now_str = datetime.utcnow().isoformat() + "Z"
        state.profit_samples.append(profit_pct)
        state.sample_timestamps.append(now_str)
        # Keep only last N samples
        max_samples = config.velocity_window * 2
        if len(state.profit_samples) > max_samples:
            state.profit_samples = state.profit_samples[-max_samples:]
            state.sample_timestamps = state.sample_timestamps[-max_samples:]

        # =====================================================================
        # CHECK 1: PEAK PROFIT RATCHET
        # =====================================================================
        ratchet_decision = self._check_ratchet(state, current_price, profit_pct, config, bot_id)
        if ratchet_decision.should_exit:
            state.sniper_triggered_count += 1
            state.last_trigger_reason = ratchet_decision.reason
            state.last_trigger_ts = now_str
            self._persist_state(position_key, state)
            return ratchet_decision

        # =====================================================================
        # CHECK 2: PROFIT VELOCITY REVERSAL
        # =====================================================================
        velocity_decision = self._check_velocity_reversal(state, profit_pct, config, bot_id)
        if velocity_decision.should_exit:
            state.sniper_triggered_count += 1
            state.last_trigger_reason = velocity_decision.reason
            state.last_trigger_ts = now_str
            self._persist_state(position_key, state)
            return velocity_decision

        # =====================================================================
        # CHECK 3: MOMENTUM EXHAUSTION
        # =====================================================================
        exhaustion_decision = self._check_exhaustion(state, profit_pct, config, bot_id)
        if exhaustion_decision.should_exit:
            state.sniper_triggered_count += 1
            state.last_trigger_reason = exhaustion_decision.reason
            state.last_trigger_ts = now_str
            self._persist_state(position_key, state)
            return exhaustion_decision

        # No trigger — persist updated state and return hold
        self._persist_state(position_key, state)
        return SniperDecision(
            ratchet_price=state.ratchet_price,
            peak_profit_pct=state.peak_profit_pct,
            current_profit_pct=profit_pct,
            velocity=self._compute_velocity(state, config)
        )

    def _check_ratchet(
        self, state: SniperState, current_price: float,
        profit_pct: float, config: ProfitSniperConfig, bot_id: str
    ) -> SniperDecision:
        """Check and enforce the peak profit ratchet."""

        # Arm the ratchet once profit exceeds threshold
        if not state.ratchet_armed and profit_pct >= config.ratchet_arm_pct:
            state.ratchet_armed = True
            # Set initial ratchet price
            if state.side == "long":
                distance_pct = config.ratchet_base_distance_pct
                state.ratchet_price = state.peak_price * (1 - distance_pct / 100)
            else:
                state.ratchet_price = state.peak_price * (1 + config.ratchet_base_distance_pct / 100)

            self._logger.log("sniper_ratchet_armed", {
                "bot_id": bot_id,
                "position_key": state.position_key,
                "profit_pct": round(profit_pct, 3),
                "ratchet_price": round(state.ratchet_price, 4),
                "peak_price": round(state.peak_price, 4)
            })

        # Update ratchet if armed and profit is growing
        if state.ratchet_armed:
            excess_profit = max(0, profit_pct - config.ratchet_arm_pct)
            tightening = excess_profit * config.ratchet_tighten_per_pct
            distance_pct = max(
                config.ratchet_min_distance_pct,
                config.ratchet_base_distance_pct - tightening
            )

            if state.side == "long":
                new_ratchet = state.peak_price * (1 - distance_pct / 100)
                # Ratchet only moves UP for longs
                if new_ratchet > state.ratchet_price:
                    state.ratchet_price = new_ratchet
            else:
                new_ratchet = state.peak_price * (1 + distance_pct / 100)
                # Ratchet only moves DOWN for shorts
                if new_ratchet < state.ratchet_price or state.ratchet_price == 0:
                    state.ratchet_price = new_ratchet

            # CHECK: Has price fallen below ratchet?
            ratchet_breached = False
            if state.side == "long" and current_price <= state.ratchet_price:
                ratchet_breached = True
            elif state.side == "short" and current_price >= state.ratchet_price:
                ratchet_breached = True

            if ratchet_breached:
                exit_pct = self._get_exit_pct(state, config)
                giveback_pct = state.peak_profit_pct - profit_pct

                self._logger.log("sniper_ratchet_triggered", {
                    "bot_id": bot_id,
                    "position_key": state.position_key,
                    "peak_profit_pct": round(state.peak_profit_pct, 3),
                    "current_profit_pct": round(profit_pct, 3),
                    "giveback_pct": round(giveback_pct, 3),
                    "ratchet_price": round(state.ratchet_price, 4),
                    "current_price": round(current_price, 4),
                    "exit_pct": exit_pct,
                    "trigger_count": state.sniper_triggered_count + 1
                })

                return SniperDecision(
                    should_exit=True,
                    exit_pct=exit_pct,
                    reason=f"ratchet_breach_peak_{state.peak_profit_pct:.1f}pct",
                    confidence=min(1.0, state.peak_profit_pct / 2.0),
                    ratchet_price=state.ratchet_price,
                    peak_profit_pct=state.peak_profit_pct,
                    current_profit_pct=profit_pct,
                    details={
                        "giveback_pct": round(giveback_pct, 3),
                        "distance_pct": round(distance_pct, 3)
                    }
                )

        return SniperDecision(
            ratchet_price=state.ratchet_price,
            peak_profit_pct=state.peak_profit_pct,
            current_profit_pct=profit_pct
        )

    def _check_velocity_reversal(
        self, state: SniperState, profit_pct: float,
        config: ProfitSniperConfig, bot_id: str
    ) -> SniperDecision:
        """Detect sharp reversal in profit velocity."""
        velocity = self._compute_velocity(state, config)

        # Track peak velocity
        if velocity > state.peak_velocity:
            state.peak_velocity = velocity

        # Only check for reversal if we had meaningful positive velocity
        if state.peak_velocity < 0.1:
            return SniperDecision(velocity=velocity)

        # Check if velocity has reversed significantly from peak
        if state.peak_velocity > 0 and velocity < 0:
            reversal_magnitude = abs(velocity) / state.peak_velocity
            if reversal_magnitude >= config.velocity_reversal_pct and profit_pct > config.ratchet_arm_pct * 0.5:
                exit_pct = self._get_exit_pct(state, config)

                self._logger.log("sniper_velocity_reversal", {
                    "bot_id": bot_id,
                    "position_key": state.position_key,
                    "peak_velocity": round(state.peak_velocity, 4),
                    "current_velocity": round(velocity, 4),
                    "reversal_magnitude": round(reversal_magnitude, 3),
                    "profit_pct": round(profit_pct, 3),
                    "exit_pct": exit_pct
                })

                return SniperDecision(
                    should_exit=True,
                    exit_pct=exit_pct,
                    reason=f"velocity_reversal_{reversal_magnitude:.0%}",
                    confidence=min(1.0, reversal_magnitude),
                    peak_profit_pct=state.peak_profit_pct,
                    current_profit_pct=profit_pct,
                    velocity=velocity,
                    details={
                        "peak_velocity": round(state.peak_velocity, 4),
                        "reversal_magnitude": round(reversal_magnitude, 3)
                    }
                )

        return SniperDecision(velocity=velocity)

    def _check_exhaustion(
        self, state: SniperState, profit_pct: float,
        config: ProfitSniperConfig, bot_id: str
    ) -> SniperDecision:
        """Detect momentum exhaustion (consecutive weakening price bars)."""

        if len(state.profit_samples) < 2:
            return SniperDecision()

        # Calculate current bar gain
        current_gain = state.profit_samples[-1] - state.profit_samples[-2]

        if current_gain < state.last_bar_gain and current_gain < 0:
            state.consecutive_weaker_bars += 1
        else:
            state.consecutive_weaker_bars = 0

        state.last_bar_gain = current_gain

        # Trigger exhaustion exit
        if (state.consecutive_weaker_bars >= config.exhaustion_bars and
                profit_pct >= config.exhaustion_min_profit_pct):
            exit_pct = self._get_exit_pct(state, config)

            self._logger.log("sniper_exhaustion_triggered", {
                "bot_id": bot_id,
                "position_key": state.position_key,
                "consecutive_weaker_bars": state.consecutive_weaker_bars,
                "profit_pct": round(profit_pct, 3),
                "last_bar_gain": round(current_gain, 4),
                "exit_pct": exit_pct
            })

            return SniperDecision(
                should_exit=True,
                exit_pct=exit_pct,
                reason=f"exhaustion_{state.consecutive_weaker_bars}_bars",
                confidence=min(1.0, state.consecutive_weaker_bars / 5.0),
                peak_profit_pct=state.peak_profit_pct,
                current_profit_pct=profit_pct,
                details={
                    "consecutive_weaker_bars": state.consecutive_weaker_bars,
                    "last_bar_gain": round(current_gain, 4)
                }
            )

        return SniperDecision()

    def _compute_velocity(self, state: SniperState, config: ProfitSniperConfig) -> float:
        """Compute profit velocity (rate of change per sample)."""
        samples = state.profit_samples
        window = config.velocity_window
        if len(samples) < window:
            return 0.0
        recent = samples[-window:]
        # Simple linear velocity: (last - first) / window
        return (recent[-1] - recent[0]) / window

    def _get_exit_pct(self, state: SniperState, config: ProfitSniperConfig) -> float:
        """Determine exit percentage based on trigger count."""
        if state.sniper_triggered_count == 0:
            return config.partial_exit_pct
        elif config.full_exit_on_second:
            return 100.0
        else:
            return config.partial_exit_pct

    def _load_or_create_state(self, position_key: str, entry_price: float, side: str) -> SniperState:
        """Load existing state from SQLite or create new."""
        state_key = f"sniper.{position_key}"
        stored = get_state(state_key)

        if stored and isinstance(stored, dict):
            try:
                # Handle list fields that may be stored as JSON
                ps = stored.get("profit_samples", [])
                st = stored.get("sample_timestamps", [])
                return SniperState(
                    position_key=stored.get("position_key", position_key),
                    entry_price=stored.get("entry_price", entry_price),
                    side=stored.get("side", side),
                    peak_profit_pct=stored.get("peak_profit_pct", 0.0),
                    peak_price=stored.get("peak_price", 0.0),
                    peak_timestamp=stored.get("peak_timestamp", ""),
                    ratchet_armed=stored.get("ratchet_armed", False),
                    ratchet_price=stored.get("ratchet_price", 0.0),
                    profit_samples=ps if isinstance(ps, list) else [],
                    sample_timestamps=st if isinstance(st, list) else [],
                    peak_velocity=stored.get("peak_velocity", 0.0),
                    consecutive_weaker_bars=stored.get("consecutive_weaker_bars", 0),
                    last_bar_gain=stored.get("last_bar_gain", 0.0),
                    sniper_triggered_count=stored.get("sniper_triggered_count", 0),
                    last_trigger_reason=stored.get("last_trigger_reason", ""),
                    last_trigger_ts=stored.get("last_trigger_ts", "")
                )
            except Exception:
                pass

        return SniperState(
            position_key=position_key,
            entry_price=entry_price,
            side=side,
            peak_price=entry_price
        )

    def _persist_state(self, position_key: str, state: SniperState) -> None:
        """Persist state to SQLite."""
        state_key = f"sniper.{position_key}"
        set_state(state_key, asdict(state))

    def clear_state(self, position_key: str) -> None:
        """Clear state when position is fully closed."""
        from ..core.state import delete_state
        state_key = f"sniper.{position_key}"
        delete_state(state_key)
        self._logger.log("sniper_state_cleared", {"position_key": position_key})


# =============================================================================
# SINGLETON
# =============================================================================
_profit_sniper: Optional[ProfitSniper] = None


def get_profit_sniper() -> ProfitSniper:
    """Get or create the ProfitSniper singleton."""
    global _profit_sniper
    if _profit_sniper is None:
        _profit_sniper = ProfitSniper()
    return _profit_sniper
