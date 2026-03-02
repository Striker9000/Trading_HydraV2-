
"""Synthetic trailing stop implementation for all asset classes"""
import json
import time
from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

from ..core.logging import get_logger
from ..core.state import get_state, set_state


@dataclass
class ReversalSenseConfig:
    """Configuration for reversal-aware trailing stop enhancement"""
    enabled: bool = True
    # Momentum reversal detection
    momentum_lookback: int = 5          # Bars to calculate momentum
    momentum_reversal_threshold: float = 0.5  # Min momentum change % to trigger
    # Volume spike detection
    volume_spike_mult: float = 2.0      # Volume > 2x avg = spike
    volume_lookback: int = 20           # Bars for avg volume calc
    # Price action reversal
    reversal_candle_body_pct: float = 70.0  # Strong reversal candle (body > 70% of range)
    # Tightening behavior
    reversal_tighten_ratio: float = 0.4   # Tighten by 40% on reversal signal
    strong_reversal_tighten_ratio: float = 0.6  # Tighten by 60% on strong reversal
    cooldown_minutes: int = 15          # Don't re-tighten within 15 min
    max_tightenings: int = 3            # Max tightenings before hitting stop


@dataclass
class DynamicTrailingConfig:
    """Configuration for ATR-based dynamic trailing stops.
    
    Instead of a fixed percentage trail, the stop width adapts to each
    symbol's volatility via ATR. Generous by default to let trades develop.
    
    Profit-tiered tightening:
      Phase 1 (profit < tier1_atr_mult × ATR):  full generous width
      Phase 2 (profit >= tier1, < tier2):        width * tier1_tighten_factor
      Phase 3 (profit >= tier2):                 width * tier2_tighten_factor
    """
    enabled: bool = True
    atr_multiplier: float = 2.5        # Trail width = atr_multiplier × ATR (generous default)
    activation_atr_mult: float = 0.75  # Arm trailing stop after profit >= 0.75 × ATR
    min_trail_pct: float = 0.5         # Floor: never trail tighter than 0.5%
    max_trail_pct: float = 15.0        # Ceiling: never trail wider than 15%
    tier1_atr_mult: float = 2.0        # Profit tier 1 threshold (2x ATR profit)
    tier1_tighten_factor: float = 0.75 # At tier 1, tighten trail to 75% of base width
    tier2_atr_mult: float = 4.0        # Profit tier 2 threshold (4x ATR profit)
    tier2_tighten_factor: float = 0.50 # At tier 2, tighten trail to 50% of base width


# Per-asset-class default ATR multipliers (generous to let trades develop)
DYNAMIC_TRAIL_DEFAULTS = {
    "us_equity": {"atr_multiplier": 2.5, "activation_atr_mult": 0.75, "min_trail_pct": 0.5, "max_trail_pct": 12.0},
    "option":    {"atr_multiplier": 3.5, "activation_atr_mult": 1.0,  "min_trail_pct": 1.0, "max_trail_pct": 20.0},
    "crypto":    {"atr_multiplier": 3.0, "activation_atr_mult": 0.5,  "min_trail_pct": 0.3, "max_trail_pct": 10.0},
}


@dataclass
class TrailingStopConfig:
    enabled: bool = True
    mode: str = "percent"  # "percent" or "price"
    value: float = 1.0     # 1.0 = 1% if percent, $1.00 if price
    activation_profit_pct: float = 0.3  # Start trailing after 0.3% profit
    update_only_if_improves: bool = True
    epsilon_pct: float = 0.02  # 0.02% buffer to prevent noise triggers
    exit_order_type: str = "market"  # "market", "limit", "stop_limit"
    limit_slippage_pct: float = 0.2  # For limit orders
    # Reversal-sense enhancement (optional)
    reversal_sense: Optional[Dict[str, Any]] = None
    # Dynamic trailing (optional) - stores the dynamic config used to create this
    dynamic_source: Optional[Dict[str, Any]] = None


@dataclass
class TrailingStopState:
    side: str  # "long" or "short"
    entry_price: float
    armed: bool = False
    high_water: float = 0.0  # For longs
    low_water: float = 999999.0  # For shorts
    stop_price: float = 0.0
    last_price: float = 0.0
    last_update_ts: str = ""
    config: Optional[Dict[str, Any]] = None
    # Reversal-sense tracking
    reversal_tighten_count: int = 0
    last_reversal_tighten_ts: str = ""
    reversal_signals_detected: int = 0


class TrailingStopManager:
    """Manages synthetic trailing stops with SQLite persistence"""
    
    def __init__(self):
        self._logger = get_logger()
    
    def init_for_position(self, bot_id: str, position_id: str, symbol: str, 
                         asset_class: str, entry_price: float, side: str, 
                         config: TrailingStopConfig) -> TrailingStopState:
        """Initialize trailing stop state for a new position"""
        
        state = TrailingStopState(
            side=side,
            entry_price=entry_price,
            armed=False,
            high_water=entry_price if side == "long" else 0.0,
            low_water=entry_price if side == "short" else 999999.0,
            stop_price=0.0,
            last_price=entry_price,
            last_update_ts=datetime.utcnow().isoformat() + "Z",
            config=asdict(config)
        )
        
        # Store in SQLite
        state_key = self._get_state_key(bot_id, asset_class, symbol, position_id)
        set_state(state_key, asdict(state))
        
        self._logger.log("trailing_stop_init", {
            "bot_id": bot_id,
            "symbol": symbol,
            "asset_class": asset_class,
            "position_id": position_id,
            "side": side,
            "entry_price": entry_price,
            "config": asdict(config)
        })
        
        return state
    
    def load_state(self, bot_id: str, position_id: str, symbol: str, 
                  asset_class: str) -> Optional[TrailingStopState]:
        """Load trailing stop state from SQLite"""
        
        state_key = self._get_state_key(bot_id, asset_class, symbol, position_id)
        state_data = get_state(state_key)
        
        if state_data:
            return TrailingStopState(**state_data)
        return None
    
    def update_state(self, bot_id: str, position_id: str, symbol: str, 
                    asset_class: str, current_price: float, 
                    state: TrailingStopState) -> TrailingStopState:
        """Update trailing stop state with current price"""
        
        # Handle None config gracefully - use defaults if missing
        config_dict = state.config if state.config is not None else {}
        config = TrailingStopConfig(**config_dict)
        
        # Update price tracking
        state.last_price = current_price
        state.last_update_ts = datetime.utcnow().isoformat() + "Z"
        
        # Check activation (profit threshold)
        if not state.armed:
            if state.side == "long":
                profit_pct = ((current_price - state.entry_price) / state.entry_price) * 100
            else:  # short
                profit_pct = ((state.entry_price - current_price) / state.entry_price) * 100
            
            # Small tolerance for floating point comparison (0.001% = 1 basis point)
            if profit_pct >= config.activation_profit_pct - 0.001:
                state.armed = True
                self._logger.log("trailing_stop_armed", {
                    "bot_id": bot_id,
                    "symbol": symbol,
                    "position_id": position_id,
                    "profit_pct": round(profit_pct, 3),
                    "activation_threshold": config.activation_profit_pct
                })
        
        # Update water marks and stop price
        if state.side == "long":
            # Long position: track high water, stop trails below
            old_high_water = state.high_water
            state.high_water = max(state.high_water, current_price)
            
            # Calculate new stop price
            if config.mode == "percent":
                new_stop = state.high_water * (1 - config.value / 100)
            else:  # price
                new_stop = state.high_water - config.value
            
            # Apply update-only-if-improves rule (raise stop, never lower)
            if config.update_only_if_improves and state.armed:
                state.stop_price = max(state.stop_price, new_stop)
            else:
                state.stop_price = new_stop
            
            # Log if high water moved significantly
            if state.high_water > old_high_water + 0.01:  # $0.01 threshold
                self._logger.log("trailing_stop_update", {
                    "bot_id": bot_id,
                    "symbol": symbol,
                    "side": "long",
                    "high_water": round(state.high_water, 4),
                    "stop_price": round(state.stop_price, 4),
                    "current_price": round(current_price, 4)
                })
        
        else:  # short position
            # Short position: track low water, stop trails above
            old_low_water = state.low_water
            state.low_water = min(state.low_water, current_price)
            
            # Calculate new stop price
            if config.mode == "percent":
                new_stop = state.low_water * (1 + config.value / 100)
            else:  # price
                new_stop = state.low_water + config.value
            
            # Apply update-only-if-improves rule (lower stop, never raise)
            # For shorts, lower stop is better (tighter protection)
            # Handle uninitialized stop_price (0.0) - must set to new_stop first
            if config.update_only_if_improves and state.armed and state.stop_price > 0:
                state.stop_price = min(state.stop_price, new_stop)
            else:
                state.stop_price = new_stop
            
            # Log if low water moved significantly
            if state.low_water < old_low_water - 0.01:  # $0.01 threshold
                self._logger.log("trailing_stop_update", {
                    "bot_id": bot_id,
                    "symbol": symbol,
                    "side": "short",
                    "low_water": round(state.low_water, 4),
                    "stop_price": round(state.stop_price, 4),
                    "current_price": round(current_price, 4)
                })
        
        # Persist updated state
        state_key = self._get_state_key(bot_id, asset_class, symbol, position_id)
        set_state(state_key, asdict(state))
        
        return state
    
    def should_exit(self, state: TrailingStopState, current_price: float) -> bool:
        """Check if trailing stop should trigger exit"""
        
        if not state.armed or state.stop_price == 0.0:
            return False
        
        # Handle None config gracefully - use defaults if missing
        config_dict = state.config if state.config is not None else {}
        config = TrailingStopConfig(**config_dict)
        epsilon = config.epsilon_pct / 100.0
        
        if state.side == "long":
            # Long trigger: current price <= stop price (with buffer)
            trigger_price = state.stop_price * (1 - epsilon)
            triggered = current_price <= trigger_price
        else:  # short
            # Short trigger: current price >= stop price (with buffer)
            trigger_price = state.stop_price * (1 + epsilon)
            triggered = current_price >= trigger_price
        
        if triggered:
            self._logger.log("trailing_stop_triggered", {
                "side": state.side,
                "current_price": round(current_price, 4),
                "stop_price": round(state.stop_price, 4),
                "trigger_price": round(trigger_price, 4),
                "epsilon_pct": config.epsilon_pct
            })
        
        return triggered
    
    def has_exit_lock(self, bot_id: str, position_id: str, symbol: str, 
                     asset_class: str) -> bool:
        """Check if position already has an active exit order"""
        
        lock_key = self._get_exit_lock_key(bot_id, asset_class, symbol, position_id)
        lock_data = get_state(lock_key)
        
        if lock_data and lock_data.get("active", False):
            # Check if lock is stale (older than 5 minutes)
            lock_ts = lock_data.get("created_ts", "")
            try:
                lock_time = datetime.fromisoformat(lock_ts.replace("Z", "+00:00"))
                age_seconds = (datetime.utcnow().replace(tzinfo=None) - lock_time.replace(tzinfo=None)).total_seconds()
                
                if age_seconds > 300:  # 5 minutes
                    self._clear_exit_lock(bot_id, asset_class, symbol, position_id)
                    return False

                return True
            except (ValueError, AttributeError, TypeError):
                # Malformed timestamp: treat lock as stale and clear it
                self._clear_exit_lock(bot_id, asset_class, symbol, position_id)
                return False
        
        return False
    
    def set_exit_lock(self, bot_id: str, position_id: str, symbol: str, 
                     asset_class: str, client_order_id: str) -> None:
        """Set exit lock to prevent duplicate exit orders"""
        
        lock_key = self._get_exit_lock_key(bot_id, asset_class, symbol, position_id)
        lock_data = {
            "active": True,
            "client_order_id": client_order_id,
            "created_ts": datetime.utcnow().isoformat() + "Z"
        }
        set_state(lock_key, lock_data)
    
    def clear_exit_lock(self, bot_id: str, position_id: str, symbol: str, 
                       asset_class: str) -> None:
        """Clear exit lock when position is closed"""
        self._clear_exit_lock(bot_id, asset_class, symbol, position_id)
    
    def _clear_exit_lock(self, bot_id: str, asset_class: str, symbol: str, 
                        position_id: str) -> None:
        """Internal method to clear exit lock"""
        lock_key = self._get_exit_lock_key(bot_id, asset_class, symbol, position_id)
        lock_data = get_state(lock_key)
        if lock_data:
            lock_data["active"] = False
            set_state(lock_key, lock_data)
    
    def persist_state(self, bot_id: str, position_id: str, symbol: str,
                      asset_class: str, state: TrailingStopState) -> None:
        """
        Persist trailing stop state WITHOUT recalculating stop price.
        
        Use this when you need to manually adjust the stop price (e.g., after
        hitting take-profit targets) without the update_state recalculation.
        
        Args:
            bot_id: Bot identifier
            position_id: Position identifier
            symbol: Asset symbol
            asset_class: Asset class
            state: TrailingStopState to persist as-is
        """
        state_key = self._get_state_key(bot_id, asset_class, symbol, position_id)
        set_state(state_key, asdict(state))
        
        self._logger.log("trailing_stop_persisted", {
            "bot_id": bot_id,
            "symbol": symbol,
            "position_id": position_id,
            "stop_price": round(state.stop_price, 4) if state.stop_price else 0,
            "armed": state.armed
        })
    
    def remove_state(self, bot_id: str, position_id: str, symbol: str, 
                    asset_class: str) -> None:
        """Remove trailing stop state when position is closed"""
        
        from ..core.state import delete_state
        
        state_key = self._get_state_key(bot_id, asset_class, symbol, position_id)
        delete_state(state_key)
        
        self._logger.log("trailing_stop_removed", {
            "bot_id": bot_id,
            "symbol": symbol,
            "position_id": position_id
        })
    
    def apply_tightening(self, bot_id: str, position_id: str, symbol: str,
                         asset_class: str, state: TrailingStopState,
                         tighten_ratio: float = 0.5) -> TrailingStopState:
        """
        Apply regime-based tightening to trailing stop
        
        When market regime signals danger (high VVIX, rate shock, etc.),
        this method reduces the trailing stop buffer by tighten_ratio.
        
        Args:
            bot_id: Bot identifier
            position_id: Position identifier
            symbol: Asset symbol
            asset_class: Asset class (us_equity, crypto, etc.)
            state: Current TrailingStopState (already updated with latest prices)
            tighten_ratio: How much to tighten (0.5 = 50% tighter buffer)
            
        Returns:
            Updated TrailingStopState with tightened stop_price (persisted)
        """
        # Only tighten if armed and stop_price is set
        if not state.armed or state.stop_price == 0.0:
            return state
        
        old_stop = state.stop_price
        
        if state.side == "long":
            # For longs: move stop_price UP (closer to high_water)
            # buffer = high_water - stop_price
            # tightened_buffer = buffer * (1 - tighten_ratio)
            # new_stop = high_water - tightened_buffer
            buffer = state.high_water - state.stop_price
            if buffer > 0:
                tightened_buffer = buffer * (1 - tighten_ratio)
                new_stop = state.high_water - tightened_buffer
                # Only tighten (raise stop), never loosen
                state.stop_price = max(state.stop_price, new_stop)
        else:  # short
            # For shorts: move stop_price DOWN (closer to low_water)
            buffer = state.stop_price - state.low_water
            if buffer > 0:
                tightened_buffer = buffer * (1 - tighten_ratio)
                new_stop = state.low_water + tightened_buffer
                # Only tighten (lower stop), never loosen
                state.stop_price = min(state.stop_price, new_stop)
        
        # Only persist if stop actually changed
        if state.stop_price != old_stop:
            state_key = self._get_state_key(bot_id, asset_class, symbol, position_id)
            set_state(state_key, asdict(state))
            
            self._logger.log("trailing_stop_tightened", {
                "bot_id": bot_id,
                "symbol": symbol,
                "side": state.side,
                "old_stop": round(old_stop, 4),
                "new_stop": round(state.stop_price, 4),
                "high_water": round(state.high_water, 4) if state.side == "long" else None,
                "low_water": round(state.low_water, 4) if state.side == "short" else None,
                "tighten_ratio": tighten_ratio,
                "reason": "regime_warning"
            })
        
        return state
    
    def detect_reversal(self, symbol: str, side: str, 
                        bars: list, current_price: float,
                        config: ReversalSenseConfig) -> Tuple[bool, str, float]:
        """
        Detect potential price reversals using multiple signals.
        
        Reversal signals for LONG positions:
        - Momentum slowing (price gains decelerating)
        - Volume spike with price reversal
        - Strong bearish reversal candle
        
        Args:
            symbol: Trading symbol
            side: Position side ("long" or "short")
            bars: Recent price bars with OHLCV data
            current_price: Current market price
            config: ReversalSenseConfig
            
        Returns:
            Tuple of (is_reversal, signal_type, strength)
            - is_reversal: True if reversal detected
            - signal_type: "momentum", "volume_spike", "reversal_candle", "strong_reversal"
            - strength: 0.0-1.0 signal strength (higher = stronger reversal signal)
        """
        if not bars or len(bars) < config.momentum_lookback:
            return False, "", 0.0
        
        signals = []
        
        try:
            # Extract OHLCV data
            closes = [b.get("c", b.get("close", 0)) for b in bars]
            highs = [b.get("h", b.get("high", 0)) for b in bars]
            lows = [b.get("l", b.get("low", 0)) for b in bars]
            opens = [b.get("o", b.get("open", 0)) for b in bars]
            volumes = [b.get("v", b.get("volume", 0)) for b in bars]
            
            # =================================================================
            # SIGNAL 1: Momentum Reversal
            # =================================================================
            # Calculate rate of change over lookback
            if len(closes) >= config.momentum_lookback + 2:
                # Recent momentum (last N bars)
                recent_momentum = (closes[-1] - closes[-config.momentum_lookback]) / closes[-config.momentum_lookback] * 100
                # Prior momentum (N bars before that)
                prior_momentum = (closes[-config.momentum_lookback] - closes[-config.momentum_lookback*2]) / closes[-config.momentum_lookback*2] * 100 if len(closes) >= config.momentum_lookback * 2 else 0
                
                if side == "long":
                    # For longs: look for momentum deceleration (positive to less positive or negative)
                    if prior_momentum > 0 and recent_momentum < prior_momentum - config.momentum_reversal_threshold:
                        strength = min(1.0, abs(prior_momentum - recent_momentum) / 2.0)
                        signals.append(("momentum", strength))
                else:  # short
                    # For shorts: look for momentum acceleration (negative to less negative or positive)
                    if prior_momentum < 0 and recent_momentum > prior_momentum + config.momentum_reversal_threshold:
                        strength = min(1.0, abs(recent_momentum - prior_momentum) / 2.0)
                        signals.append(("momentum", strength))
            
            # =================================================================
            # SIGNAL 2: Volume Spike with Adverse Move
            # =================================================================
            if len(volumes) >= config.volume_lookback:
                avg_volume = sum(volumes[-config.volume_lookback:]) / config.volume_lookback
                current_volume = volumes[-1] if volumes else 0
                
                if avg_volume > 0 and current_volume > avg_volume * config.volume_spike_mult:
                    # Check if volume spike is on adverse move
                    last_close = closes[-1] if closes else current_price
                    prev_close = closes[-2] if len(closes) >= 2 else last_close
                    
                    if side == "long" and last_close < prev_close:
                        # Volume spike on down move = bearish for longs
                        strength = min(1.0, (current_volume / avg_volume) / 4.0)
                        signals.append(("volume_spike", strength))
                    elif side == "short" and last_close > prev_close:
                        # Volume spike on up move = bearish for shorts
                        strength = min(1.0, (current_volume / avg_volume) / 4.0)
                        signals.append(("volume_spike", strength))
            
            # =================================================================
            # SIGNAL 3: Reversal Candle Pattern
            # =================================================================
            if len(bars) >= 1:
                last_bar = bars[-1]
                o = last_bar.get("o", last_bar.get("open", 0))
                h = last_bar.get("h", last_bar.get("high", 0))
                l = last_bar.get("l", last_bar.get("low", 0))
                c = last_bar.get("c", last_bar.get("close", 0))
                
                candle_range = h - l if h > l else 0.0001
                body = abs(c - o)
                body_pct = (body / candle_range) * 100
                
                if body_pct >= config.reversal_candle_body_pct:
                    if side == "long" and c < o:
                        # Strong bearish candle (close < open with large body)
                        strength = min(1.0, body_pct / 100.0)
                        signals.append(("reversal_candle", strength))
                    elif side == "short" and c > o:
                        # Strong bullish candle (close > open with large body)
                        strength = min(1.0, body_pct / 100.0)
                        signals.append(("reversal_candle", strength))
            
            # =================================================================
            # Aggregate Signals
            # =================================================================
            if not signals:
                return False, "", 0.0
            
            # Sort by strength, use strongest signal
            signals.sort(key=lambda x: x[1], reverse=True)
            best_signal, best_strength = signals[0]
            
            # Check for strong reversal (multiple signals or very high strength)
            if len(signals) >= 2 or best_strength >= 0.7:
                return True, "strong_reversal", min(1.0, best_strength + 0.2)
            
            return True, best_signal, best_strength
            
        except Exception as e:
            self._logger.error(f"Reversal detection failed for {symbol}: {e}")
            return False, "", 0.0
    
    def apply_reversal_tightening(self, bot_id: str, position_id: str, symbol: str,
                                   asset_class: str, state: TrailingStopState,
                                   bars: list, current_price: float) -> Tuple[TrailingStopState, bool]:
        """
        Check for reversals and apply dynamic stop tightening.
        
        This is the main entry point for reversal-sense trailing stop.
        Call this during each position monitoring loop.
        
        Args:
            bot_id: Bot identifier
            position_id: Position identifier
            symbol: Trading symbol
            asset_class: Asset class
            state: Current TrailingStopState
            bars: Recent OHLCV bars
            current_price: Current market price
            
        Returns:
            Tuple of (updated_state, was_tightened)
        """
        # Check if reversal-sense is enabled
        config_dict = state.config if state.config else {}
        rs_cfg_dict = config_dict.get("reversal_sense", {})
        
        if not rs_cfg_dict or not rs_cfg_dict.get("enabled", False):
            return state, False
        
        # Build config from dict
        rs_config = ReversalSenseConfig(
            enabled=rs_cfg_dict.get("enabled", True),
            momentum_lookback=rs_cfg_dict.get("momentum_lookback", 5),
            momentum_reversal_threshold=rs_cfg_dict.get("momentum_reversal_threshold", 0.5),
            volume_spike_mult=rs_cfg_dict.get("volume_spike_mult", 2.0),
            volume_lookback=rs_cfg_dict.get("volume_lookback", 20),
            reversal_candle_body_pct=rs_cfg_dict.get("reversal_candle_body_pct", 70.0),
            reversal_tighten_ratio=rs_cfg_dict.get("reversal_tighten_ratio", 0.4),
            strong_reversal_tighten_ratio=rs_cfg_dict.get("strong_reversal_tighten_ratio", 0.6),
            cooldown_minutes=rs_cfg_dict.get("cooldown_minutes", 15),
            max_tightenings=rs_cfg_dict.get("max_tightenings", 3)
        )
        
        # Check cooldown
        if state.last_reversal_tighten_ts:
            try:
                last_tighten = datetime.fromisoformat(state.last_reversal_tighten_ts.replace("Z", "+00:00"))
                now = datetime.utcnow().replace(tzinfo=None)
                minutes_since = (now - last_tighten.replace(tzinfo=None)).total_seconds() / 60
                
                if minutes_since < rs_config.cooldown_minutes:
                    return state, False
            except (ValueError, AttributeError, TypeError):
                pass  # Malformed tighten timestamp; skip cooldown check
        
        # Check max tightenings
        if state.reversal_tighten_count >= rs_config.max_tightenings:
            return state, False
        
        # Detect reversal
        is_reversal, signal_type, strength = self.detect_reversal(
            symbol, state.side, bars, current_price, rs_config
        )
        
        if not is_reversal:
            return state, False
        
        # Apply tightening based on signal strength
        if signal_type == "strong_reversal":
            tighten_ratio = rs_config.strong_reversal_tighten_ratio
        else:
            tighten_ratio = rs_config.reversal_tighten_ratio * strength
        
        old_stop = state.stop_price
        
        # Apply the tightening
        if state.side == "long":
            buffer = state.high_water - state.stop_price
            if buffer > 0:
                tightened_buffer = buffer * (1 - tighten_ratio)
                new_stop = state.high_water - tightened_buffer
                state.stop_price = max(state.stop_price, new_stop)
        else:  # short
            buffer = state.stop_price - state.low_water
            if buffer > 0:
                tightened_buffer = buffer * (1 - tighten_ratio)
                new_stop = state.low_water + tightened_buffer
                state.stop_price = min(state.stop_price, new_stop)
        
        was_tightened = state.stop_price != old_stop
        
        if was_tightened:
            # Update reversal tracking
            state.reversal_tighten_count += 1
            state.reversal_signals_detected += 1
            state.last_reversal_tighten_ts = datetime.utcnow().isoformat() + "Z"
            
            # Persist state
            state_key = self._get_state_key(bot_id, asset_class, symbol, position_id)
            set_state(state_key, asdict(state))
            
            self._logger.log("reversal_sense_tightened", {
                "bot_id": bot_id,
                "symbol": symbol,
                "side": state.side,
                "signal_type": signal_type,
                "signal_strength": round(strength, 2),
                "tighten_ratio": round(tighten_ratio, 2),
                "old_stop": round(old_stop, 4),
                "new_stop": round(state.stop_price, 4),
                "tighten_count": state.reversal_tighten_count,
                "current_price": round(current_price, 4)
            })
        
        return state, was_tightened
    
    def compute_dynamic_trailing(self, symbol: str, entry_price: float,
                                  asset_class: str, atr_value: Optional[float],
                                  dynamic_cfg: Optional[DynamicTrailingConfig] = None,
                                  bot_override: Optional[Dict[str, Any]] = None) -> TrailingStopConfig:
        """
        Compute a volatility-adaptive trailing stop config using ATR.
        
        The trail width = atr_multiplier × ATR, converted to a percentage of entry price.
        This is generous by default so trades have room to develop. The trail auto-tightens
        as profit grows through configurable tiers.
        
        Args:
            symbol: Trading symbol
            entry_price: Position entry price
            asset_class: Asset class (us_equity, option, crypto)
            atr_value: ATR(14) value from sensors. If None, falls back to static config.
            dynamic_cfg: Optional DynamicTrailingConfig override
            bot_override: Optional bot-level overrides (atr_multiplier, min_trail_pct, etc.)
            
        Returns:
            TrailingStopConfig with volatility-adjusted values
        """
        # Get asset-class defaults (normalize us_option -> option)
        normalized_ac = "option" if asset_class == "us_option" else asset_class
        defaults = DYNAMIC_TRAIL_DEFAULTS.get(normalized_ac, DYNAMIC_TRAIL_DEFAULTS["us_equity"])
        
        # Build dynamic config from: bot_override > dynamic_cfg > asset defaults
        if dynamic_cfg is None:
            dynamic_cfg = DynamicTrailingConfig()
        
        atr_mult = dynamic_cfg.atr_multiplier
        activation_atr_mult = dynamic_cfg.activation_atr_mult
        min_trail = dynamic_cfg.min_trail_pct
        max_trail = dynamic_cfg.max_trail_pct
        
        # Apply bot-level overrides if provided
        if bot_override:
            atr_mult = bot_override.get("atr_multiplier", atr_mult)
            activation_atr_mult = bot_override.get("activation_atr_mult", activation_atr_mult)
            min_trail = bot_override.get("min_trail_pct", min_trail)
            max_trail = bot_override.get("max_trail_pct", max_trail)
        
        # Apply asset-class defaults for anything not explicitly set
        if not bot_override and dynamic_cfg.atr_multiplier == 2.5:
            atr_mult = defaults.get("atr_multiplier", atr_mult)
            activation_atr_mult = defaults.get("activation_atr_mult", activation_atr_mult)
            min_trail = defaults.get("min_trail_pct", min_trail)
            max_trail = defaults.get("max_trail_pct", max_trail)
        
        # If no ATR data available, fall back to generous static defaults
        if atr_value is None or atr_value <= 0 or entry_price <= 0:
            fallback_pct = max(min_trail, defaults.get("atr_multiplier", 2.5))
            self._logger.log("dynamic_trailing_fallback", {
                "symbol": symbol,
                "asset_class": asset_class,
                "reason": "no_atr_data",
                "fallback_trail_pct": fallback_pct
            })
            return TrailingStopConfig(
                enabled=True,
                mode="percent",
                value=fallback_pct,
                activation_profit_pct=max(0.5, fallback_pct * 0.3),
                update_only_if_improves=True,
                epsilon_pct=0.02,
                exit_order_type="market",
                dynamic_source={"mode": "fallback", "reason": "no_atr"}
            )
        
        # Compute trail width as percentage of entry price
        trail_dollars = atr_mult * atr_value
        trail_pct = (trail_dollars / entry_price) * 100
        
        # Clamp to min/max bounds
        trail_pct = max(min_trail, min(max_trail, trail_pct))
        
        # Compute activation threshold as percentage of entry price
        activation_dollars = activation_atr_mult * atr_value
        activation_pct = (activation_dollars / entry_price) * 100
        activation_pct = max(0.2, activation_pct)
        
        self._logger.log("dynamic_trailing_computed", {
            "symbol": symbol,
            "asset_class": asset_class,
            "entry_price": round(entry_price, 2),
            "atr_14": round(atr_value, 4),
            "atr_multiplier": atr_mult,
            "raw_trail_pct": round((atr_mult * atr_value / entry_price) * 100, 2),
            "clamped_trail_pct": round(trail_pct, 2),
            "activation_pct": round(activation_pct, 2),
            "tier1_tighten_at": f"{dynamic_cfg.tier1_atr_mult}x ATR profit",
            "tier2_tighten_at": f"{dynamic_cfg.tier2_atr_mult}x ATR profit"
        })
        
        return TrailingStopConfig(
            enabled=True,
            mode="percent",
            value=round(trail_pct, 2),
            activation_profit_pct=round(activation_pct, 2),
            update_only_if_improves=True,
            epsilon_pct=0.02,
            exit_order_type="market",
            dynamic_source={
                "mode": "dynamic_atr",
                "atr_value": round(atr_value, 4),
                "atr_multiplier": atr_mult,
                "entry_price": round(entry_price, 2),
                "tier1_atr_mult": dynamic_cfg.tier1_atr_mult,
                "tier1_tighten_factor": dynamic_cfg.tier1_tighten_factor,
                "tier2_atr_mult": dynamic_cfg.tier2_atr_mult,
                "tier2_tighten_factor": dynamic_cfg.tier2_tighten_factor,
            }
        )
    
    def apply_profit_tier_tightening(self, state: TrailingStopState,
                                      current_price: float) -> TrailingStopState:
        """
        Apply profit-tiered tightening to a dynamic trailing stop.
        
        As profit grows through ATR-based tiers, the trail width automatically
        tightens to lock in gains — but always stays generous enough to let
        the trade continue developing.
        
        Should be called during update_state when dynamic_source is present.
        
        Args:
            state: Current trailing stop state
            current_price: Current market price
            
        Returns:
            Updated state with tightened trail if applicable
        """
        config_dict = state.config if state.config else {}
        dynamic_source = config_dict.get("dynamic_source", {})
        
        if not dynamic_source or dynamic_source.get("mode") != "dynamic_atr":
            return state
        
        atr_value = dynamic_source.get("atr_value", 0)
        if atr_value <= 0:
            return state
        
        # Calculate current profit in ATR units
        if state.side == "long":
            profit_dollars = current_price - state.entry_price
        else:
            profit_dollars = state.entry_price - current_price
        
        profit_atr_units = profit_dollars / atr_value if atr_value > 0 else 0
        
        if profit_atr_units <= 0:
            return state
        
        # Determine which tier we're in
        tier1_mult = dynamic_source.get("tier1_atr_mult", 2.0)
        tier1_factor = dynamic_source.get("tier1_tighten_factor", 0.75)
        tier2_mult = dynamic_source.get("tier2_atr_mult", 4.0)
        tier2_factor = dynamic_source.get("tier2_tighten_factor", 0.50)
        
        base_value = config_dict.get("value", 1.0)
        original_atr_mult = dynamic_source.get("atr_multiplier", 2.5)
        entry_price = dynamic_source.get("entry_price", state.entry_price)
        
        # Compute the original base trail width (before any tightening)
        base_trail_pct = (original_atr_mult * atr_value / entry_price) * 100 if entry_price > 0 else base_value
        
        if profit_atr_units >= tier2_mult:
            new_trail_pct = base_trail_pct * tier2_factor
            tier_name = "tier2"
        elif profit_atr_units >= tier1_mult:
            new_trail_pct = base_trail_pct * tier1_factor
            tier_name = "tier1"
        else:
            return state
        
        # Apply floor
        min_trail = dynamic_source.get("min_trail_pct", 0.5)
        new_trail_pct = max(min_trail if min_trail else 0.5, new_trail_pct)
        
        # Only tighten (reduce trail width), never widen
        if new_trail_pct < base_value:
            config_dict["value"] = round(new_trail_pct, 2)
            state.config = config_dict
            
            # Recalculate stop price with tighter trail
            if state.side == "long":
                new_stop = state.high_water * (1 - new_trail_pct / 100)
                state.stop_price = max(state.stop_price, new_stop)
            else:
                new_stop = state.low_water * (1 + new_trail_pct / 100)
                state.stop_price = min(state.stop_price, new_stop)
            
            self._logger.log("dynamic_trailing_tier_tighten", {
                "side": state.side,
                "tier": tier_name,
                "profit_atr_units": round(profit_atr_units, 2),
                "base_trail_pct": round(base_trail_pct, 2),
                "new_trail_pct": round(new_trail_pct, 2),
                "new_stop_price": round(state.stop_price, 4),
                "current_price": round(current_price, 4)
            })
        
        return state
    
    def _get_state_key(self, bot_id: str, asset_class: str, symbol: str, 
                      position_id: str) -> str:
        """Generate state key for trailing stop data"""
        return f"ts:{bot_id}:{asset_class}:{symbol}:{position_id}"
    
    def _get_exit_lock_key(self, bot_id: str, asset_class: str, symbol: str, 
                          position_id: str) -> str:
        """Generate exit lock key"""
        return f"exit_lock:{bot_id}:{asset_class}:{symbol}:{position_id}"


# Global instance
_trailing_stop_manager: Optional[TrailingStopManager] = None


def get_trailing_stop_manager() -> TrailingStopManager:
    """Get global trailing stop manager instance"""
    global _trailing_stop_manager
    if _trailing_stop_manager is None:
        _trailing_stop_manager = TrailingStopManager()
    return _trailing_stop_manager
