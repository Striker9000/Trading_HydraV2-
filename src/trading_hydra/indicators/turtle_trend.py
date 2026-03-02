"""
Turtle Traders Strategy Implementation
======================================

Implements the original Turtle Traders strategy (Richard Dennis + William Eckhardt, 1980s):
- Donchian Channel breakouts for entries
- ATR-based position sizing (N) so each trade risks ~1% equity
- Pyramiding: add units every 0.5N move in favor (up to 4 units)
- 2N protective stop-loss
- Channel-based exits (10/20-day opposite breakout)
- Winner filter: skip next signal after a winning trade

Two systems supported:
- System 1: 20-day breakout entry, 10-day exit (faster, more signals)
- System 2: 55-day breakout entry, 20-day exit (slower, bigger trends)

References:
- "Way of the Turtle" by Curtis Faith
- Original Turtle Trading Rules (public domain)
"""

from typing import Dict, Any, Optional, List, Tuple, NamedTuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import math

from ..core.logging import get_logger
from ..core.state import get_state, set_state, delete_state
from ..core.clock import get_market_clock


class TurtleSystem(Enum):
    SYSTEM_1 = "system_1"  # 20-day entry, 10-day exit
    SYSTEM_2 = "system_2"  # 55-day entry, 20-day exit


class SignalType(Enum):
    LONG_ENTRY = "long_entry"
    SHORT_ENTRY = "short_entry"
    LONG_EXIT = "long_exit"
    SHORT_EXIT = "short_exit"
    PYRAMID_ADD = "pyramid_add"
    STOP_EXIT = "stop_exit"
    NO_SIGNAL = "no_signal"


@dataclass
class DonchianChannel:
    """
    Donchian Channel values for a given lookback period.
    
    The channel tracks the highest high and lowest low over N periods.
    Breakouts above the upper band or below the lower band generate signals.
    """
    upper: float      # Highest high over lookback period
    lower: float      # Lowest low over lookback period
    middle: float     # Midpoint of channel
    lookback: int     # Number of periods used
    timestamp: datetime


@dataclass
class TurtleConfig:
    """
    Configuration for Turtle Traders strategy.
    
    Adapts original Turtle rules to different asset classes:
    - Stocks: Daily bars, standard lookbacks
    - Crypto: Hourly bars with equivalent lookbacks (24x multiplier)
    """
    system: TurtleSystem = TurtleSystem.SYSTEM_1
    
    entry_lookback: int = 20       # Days/periods for entry breakout (20 or 55)
    exit_lookback: int = 10        # Days/periods for exit breakout (10 or 20)
    atr_period: int = 20           # ATR calculation period
    
    risk_pct_per_unit: float = 1.0     # Equity % risked per unit (original Turtles: 1%)
    stop_loss_atr_mult: float = 2.0    # Stop-loss = 2N (2x ATR)
    
    pyramid_enabled: bool = True       # Add to winners
    pyramid_trigger_atr: float = 0.5   # Add unit every 0.5N move in favor
    max_units: int = 4                 # Maximum units per position
    
    winner_filter_enabled: bool = True # Skip signal after winner
    
    asset_class: str = "stock"         # "stock" or "crypto"
    bar_timeframe: str = "1Day"        # Bar aggregation for lookbacks


@dataclass
class TurtleSignal:
    """
    Output signal from Turtle Traders strategy.
    
    Contains all information needed to execute the trade:
    - Signal type and direction
    - Position sizing based on ATR(N)
    - Stop-loss level (2N from entry)
    - Pyramid level if adding to winner
    """
    signal_type: SignalType
    symbol: str
    system: TurtleSystem
    
    entry_price: float = 0.0
    stop_price: float = 0.0
    
    atr_n: float = 0.0                 # Current ATR (N) value
    position_size_dollars: float = 0.0 # Dollar amount for this unit
    position_size_shares: float = 0.0  # Share count for this unit
    
    current_units: int = 0             # Units already held
    target_units: int = 0              # Units after this signal
    
    donchian_upper: float = 0.0
    donchian_lower: float = 0.0
    
    confidence: float = 0.0
    reason: str = ""
    
    filtered_by_winner: bool = False   # True if signal was skipped due to filter
    
    indicators: Dict[str, Any] = field(default_factory=dict)


class TurtleTrend:
    """
    Turtle Traders trend-following strategy engine.
    
    This class computes Donchian Channels, ATR(N), and generates
    Turtle-style trading signals for entries, exits, and pyramiding.
    
    Usage:
        turtle = TurtleTrend("AAPL", config)
        signal = turtle.evaluate(bars, equity, current_position)
        if signal.signal_type == SignalType.LONG_ENTRY:
            # Execute entry with signal.position_size_dollars
    """
    
    def __init__(self, symbol: str, config: Optional[TurtleConfig] = None):
        self.symbol = symbol
        self.config = config or TurtleConfig()
        self._logger = get_logger()
        
        self._state_prefix = f"turtle.{symbol}"
        
        self._logger.log("turtle_init", {
            "symbol": symbol,
            "system": self.config.system.value,
            "entry_lookback": self.config.entry_lookback,
            "exit_lookback": self.config.exit_lookback,
            "atr_period": self.config.atr_period,
            "risk_pct": self.config.risk_pct_per_unit,
            "max_units": self.config.max_units
        })
    
    def compute_donchian(self, bars: List[Dict], lookback: int) -> Optional[DonchianChannel]:
        """
        Compute Donchian Channel from price bars.
        
        Donchian Channel = highest high and lowest low over N periods.
        This is the core breakout detection mechanism.
        
        Args:
            bars: List of OHLCV bars (must have 'high', 'low', 'close')
            lookback: Number of periods for channel calculation
        
        Returns:
            DonchianChannel with upper, lower, and middle values
        """
        if not bars or len(bars) < lookback:
            self._logger.log("turtle_donchian_insufficient", {
                "symbol": self.symbol,
                "bars": len(bars) if bars else 0,
                "required": lookback
            })
            return None
        
        channel_bars = bars[-lookback:]
        
        highs = [float(b.get("high", b.get("h", 0))) for b in channel_bars]
        lows = [float(b.get("low", b.get("l", 0))) for b in channel_bars]
        
        if not highs or not lows or max(highs) == 0:
            return None
        
        upper = max(highs)
        lower = min(lows)
        middle = (upper + lower) / 2
        
        return DonchianChannel(
            upper=upper,
            lower=lower,
            middle=middle,
            lookback=lookback,
            timestamp=get_market_clock().now()
        )
    
    def compute_atr(self, bars: List[Dict], period: int = 20) -> float:
        """
        Compute Average True Range (ATR) - the "N" in Turtle parlance.
        
        True Range = max of:
        1. High - Low (current bar range)
        2. |High - Previous Close|
        3. |Low - Previous Close|
        
        ATR = EMA of True Range over N periods
        
        This volatility measure is used for:
        - Position sizing (1N move = 1% account risk)
        - Stop-loss placement (2N from entry)
        - Pyramiding triggers (add every 0.5N)
        
        Args:
            bars: List of OHLCV bars
            period: ATR period (default 20)
        
        Returns:
            ATR value (float)
        """
        if not bars or len(bars) < period + 1:
            self._logger.log("turtle_atr_insufficient", {
                "symbol": self.symbol,
                "bars": len(bars) if bars else 0,
                "required": period + 1
            })
            return 0.0
        
        true_ranges = []
        
        for i in range(1, len(bars)):
            high = float(bars[i].get("high", bars[i].get("h", 0)))
            low = float(bars[i].get("low", bars[i].get("l", 0)))
            prev_close = float(bars[i-1].get("close", bars[i-1].get("c", 0)))
            
            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            
            true_range = max(tr1, tr2, tr3)
            true_ranges.append(true_range)
        
        if len(true_ranges) < period:
            return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0
        
        recent_tr = true_ranges[-period:]
        atr = sum(recent_tr) / len(recent_tr)
        
        return atr
    
    def compute_position_size(
        self, 
        atr_n: float, 
        equity: float, 
        current_price: float
    ) -> Tuple[float, float]:
        """
        Compute Turtle-style position size based on volatility.
        
        The genius of Turtle sizing: every position risks the same dollar amount.
        If ATR is high (volatile), trade smaller. If ATR is low, trade larger.
        
        Formula:
        Dollar Volatility = ATR * Point Value (1 for stocks)
        Unit Size (shares) = (Equity * Risk%) / (ATR * Stop Multiplier)
        
        Args:
            atr_n: Current ATR (N) value
            equity: Total account equity
            current_price: Current asset price
        
        Returns:
            Tuple of (dollar_size, share_count)
        """
        if atr_n <= 0 or equity <= 0 or current_price <= 0:
            return 0.0, 0.0
        
        risk_pct = self.config.risk_pct_per_unit / 100.0
        stop_mult = self.config.stop_loss_atr_mult
        dollar_risk_per_unit = equity * risk_pct
        
        shares = dollar_risk_per_unit / (atr_n * stop_mult)
        
        if self.config.asset_class == "crypto":
            shares = round(shares, 6)
        else:
            shares = int(shares)
        
        dollar_size = shares * current_price
        
        self._logger.log("turtle_position_size", {
            "symbol": self.symbol,
            "equity": equity,
            "atr_n": round(atr_n, 4),
            "risk_pct": self.config.risk_pct_per_unit,
            "shares": shares,
            "dollar_size": round(dollar_size, 2)
        })
        
        return dollar_size, shares
    
    def _get_last_trade_result(self) -> Optional[str]:
        """Get result of last trade for winner filter."""
        key = f"{self._state_prefix}.last_trade_result"
        return get_state(key)
    
    def _set_last_trade_result(self, result: str):
        """Store trade result for winner filter."""
        key = f"{self._state_prefix}.last_trade_result"
        set_state(key, result)
    
    def _get_current_units(self) -> int:
        """Get current number of units in position."""
        key = f"{self._state_prefix}.current_units"
        return get_state(key, 0)
    
    def _set_current_units(self, units: int):
        """Store current unit count."""
        key = f"{self._state_prefix}.current_units"
        set_state(key, units)
    
    def _get_entry_prices(self) -> List[float]:
        """Get list of entry prices for each unit (for pyramiding tracking)."""
        key = f"{self._state_prefix}.entry_prices"
        return get_state(key, [])
    
    def _add_entry_price(self, price: float):
        """Add entry price for new unit."""
        prices = self._get_entry_prices()
        prices.append(price)
        key = f"{self._state_prefix}.entry_prices"
        set_state(key, prices)
    
    def _clear_position_state(self):
        """Clear all position-related state after exit."""
        delete_state(f"{self._state_prefix}.current_units")
        delete_state(f"{self._state_prefix}.entry_prices")
        delete_state(f"{self._state_prefix}.position_side")
        delete_state(f"{self._state_prefix}.initial_stop")
    
    def _get_position_side(self) -> Optional[str]:
        """Get current position side ('long' or 'short')."""
        key = f"{self._state_prefix}.position_side"
        return get_state(key)
    
    def _set_position_side(self, side: str):
        """Store position side."""
        key = f"{self._state_prefix}.position_side"
        set_state(key, side)
    
    def _get_initial_stop(self) -> Optional[float]:
        """Get initial stop price."""
        key = f"{self._state_prefix}.initial_stop"
        return get_state(key)
    
    def _set_initial_stop(self, price: float):
        """Store initial stop price."""
        key = f"{self._state_prefix}.initial_stop"
        set_state(key, price)
    
    def evaluate(
        self,
        bars: List[Dict],
        equity: float,
        current_price: float,
        has_position: bool = False,
        position_side: Optional[str] = None,
        position_qty: float = 0.0
    ) -> TurtleSignal:
        """
        Evaluate Turtle Traders signals for a symbol.
        
        This is the main entry point. Call each bar/period to get:
        - Entry signals (breakout above/below Donchian channel)
        - Exit signals (opposite channel break or stop hit)
        - Pyramid signals (add to winner every 0.5N)
        
        Args:
            bars: Historical OHLCV bars (newest last)
            equity: Current account equity for sizing
            current_price: Current market price
            has_position: Whether we currently hold a position
            position_side: "long" or "short" if holding
            position_qty: Current position quantity
        
        Returns:
            TurtleSignal with action to take
        """
        entry_lookback = self.config.entry_lookback
        exit_lookback = self.config.exit_lookback
        atr_period = self.config.atr_period
        
        min_bars = max(entry_lookback, exit_lookback, atr_period) + 1
        if len(bars) < min_bars:
            return TurtleSignal(
                signal_type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                system=self.config.system,
                reason=f"Insufficient bars: {len(bars)} < {min_bars}"
            )
        
        entry_channel = self.compute_donchian(bars, entry_lookback)
        exit_channel = self.compute_donchian(bars, exit_lookback)
        atr_n = self.compute_atr(bars, atr_period)
        
        if not entry_channel or not exit_channel or atr_n <= 0:
            return TurtleSignal(
                signal_type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                system=self.config.system,
                reason="Could not compute channels or ATR"
            )
        
        dollar_size, share_size = self.compute_position_size(atr_n, equity, current_price)
        
        current_units = self._get_current_units()
        
        base_signal = TurtleSignal(
            signal_type=SignalType.NO_SIGNAL,
            symbol=self.symbol,
            system=self.config.system,
            entry_price=current_price,
            atr_n=atr_n,
            position_size_dollars=dollar_size,
            position_size_shares=share_size,
            current_units=current_units,
            donchian_upper=entry_channel.upper,
            donchian_lower=entry_channel.lower,
            indicators={
                "entry_channel_upper": entry_channel.upper,
                "entry_channel_lower": entry_channel.lower,
                "exit_channel_upper": exit_channel.upper if exit_channel else 0,
                "exit_channel_lower": exit_channel.lower if exit_channel else 0,
                "atr_n": atr_n,
                "atr_pct": (atr_n / current_price * 100) if current_price > 0 else 0
            }
        )
        
        if has_position and position_side:
            return self._evaluate_position_management(
                base_signal, exit_channel, atr_n, current_price, 
                position_side, position_qty
            )
        
        return self._evaluate_entry(base_signal, entry_channel, atr_n, current_price)
    
    def _evaluate_entry(
        self,
        base_signal: TurtleSignal,
        entry_channel: DonchianChannel,
        atr_n: float,
        current_price: float
    ) -> TurtleSignal:
        """
        Evaluate entry signals based on Donchian breakout.
        
        Long entry: Price breaks above N-day high
        Short entry: Price breaks below N-day low
        
        Includes winner filter check if enabled.
        """
        if self.config.winner_filter_enabled:
            last_result = self._get_last_trade_result()
            if last_result == "winner":
                self._logger.log("turtle_winner_filter", {
                    "symbol": self.symbol,
                    "action": "skip_signal",
                    "reason": "Last trade was winner, skipping this breakout"
                })
                self._set_last_trade_result("filtered")
                base_signal.filtered_by_winner = True
                base_signal.reason = "Winner filter: skipping after profitable trade"
                return base_signal
        
        if current_price > entry_channel.upper:
            stop_price = current_price - (self.config.stop_loss_atr_mult * atr_n)
            
            base_signal.signal_type = SignalType.LONG_ENTRY
            base_signal.stop_price = stop_price
            base_signal.target_units = 1
            base_signal.confidence = min(0.8, (current_price - entry_channel.upper) / entry_channel.upper * 100)
            base_signal.reason = f"Breakout above {self.config.entry_lookback}-day high ({entry_channel.upper:.2f})"
            
            self._set_current_units(1)
            self._add_entry_price(current_price)
            self._set_position_side("long")
            self._set_initial_stop(stop_price)
            
            self._logger.log("turtle_long_entry", {
                "symbol": self.symbol,
                "price": current_price,
                "channel_upper": entry_channel.upper,
                "atr_n": atr_n,
                "stop_price": stop_price,
                "position_size": base_signal.position_size_dollars
            })
            
            return base_signal
        
        if current_price < entry_channel.lower:
            stop_price = current_price + (self.config.stop_loss_atr_mult * atr_n)
            
            base_signal.signal_type = SignalType.SHORT_ENTRY
            base_signal.stop_price = stop_price
            base_signal.target_units = 1
            base_signal.confidence = min(0.8, (entry_channel.lower - current_price) / entry_channel.lower * 100)
            base_signal.reason = f"Breakout below {self.config.entry_lookback}-day low ({entry_channel.lower:.2f})"
            
            self._set_current_units(1)
            self._add_entry_price(current_price)
            self._set_position_side("short")
            self._set_initial_stop(stop_price)
            
            self._logger.log("turtle_short_entry", {
                "symbol": self.symbol,
                "price": current_price,
                "channel_lower": entry_channel.lower,
                "atr_n": atr_n,
                "stop_price": stop_price,
                "position_size": base_signal.position_size_dollars
            })
            
            return base_signal
        
        base_signal.reason = f"No breakout: price {current_price:.2f} within channel [{entry_channel.lower:.2f}, {entry_channel.upper:.2f}]"
        return base_signal
    
    def _evaluate_position_management(
        self,
        base_signal: TurtleSignal,
        exit_channel: DonchianChannel,
        atr_n: float,
        current_price: float,
        position_side: str,
        position_qty: float
    ) -> TurtleSignal:
        """
        Evaluate exit and pyramiding signals for existing position.
        
        Exit triggers:
        1. Price breaks opposite channel (10/20-day)
        2. Price hits 2N stop-loss
        
        Pyramid trigger:
        - Price moves 0.5N in favor, add another unit (up to max_units)
        """
        initial_stop = self._get_initial_stop() or 0
        entry_prices = self._get_entry_prices()
        current_units = self._get_current_units()
        
        avg_entry = sum(entry_prices) / len(entry_prices) if entry_prices else current_price
        
        if position_side == "long":
            if current_price < exit_channel.lower:
                pnl = (current_price - avg_entry) / avg_entry * 100
                result = "winner" if pnl > 0 else "loser"
                self._set_last_trade_result(result)
                self._clear_position_state()
                
                base_signal.signal_type = SignalType.LONG_EXIT
                base_signal.reason = f"Exit: price broke below {self.config.exit_lookback}-day low ({exit_channel.lower:.2f})"
                
                self._logger.log("turtle_long_exit_channel", {
                    "symbol": self.symbol,
                    "price": current_price,
                    "exit_channel_lower": exit_channel.lower,
                    "pnl_pct": pnl,
                    "result": result
                })
                
                return base_signal
            
            if initial_stop > 0 and current_price <= initial_stop:
                self._set_last_trade_result("loser")
                self._clear_position_state()
                
                base_signal.signal_type = SignalType.STOP_EXIT
                base_signal.reason = f"Stop hit at {initial_stop:.2f} (2N from entry)"
                
                self._logger.log("turtle_stop_exit", {
                    "symbol": self.symbol,
                    "price": current_price,
                    "stop_price": initial_stop,
                    "side": "long"
                })
                
                return base_signal
            
            if self.config.pyramid_enabled and current_units < self.config.max_units:
                last_entry = entry_prices[-1] if entry_prices else avg_entry
                pyramid_trigger = last_entry + (self.config.pyramid_trigger_atr * atr_n)
                
                if current_price >= pyramid_trigger:
                    new_stop = current_price - (self.config.stop_loss_atr_mult * atr_n)
                    
                    base_signal.signal_type = SignalType.PYRAMID_ADD
                    base_signal.target_units = current_units + 1
                    base_signal.stop_price = new_stop
                    base_signal.reason = f"Pyramid: adding unit {current_units + 1}, price moved 0.5N above last entry"
                    
                    self._set_current_units(current_units + 1)
                    self._add_entry_price(current_price)
                    self._set_initial_stop(new_stop)
                    
                    self._logger.log("turtle_pyramid_add", {
                        "symbol": self.symbol,
                        "price": current_price,
                        "new_units": current_units + 1,
                        "last_entry": last_entry,
                        "trigger": pyramid_trigger
                    })
                    
                    return base_signal
        
        elif position_side == "short":
            if current_price > exit_channel.upper:
                pnl = (avg_entry - current_price) / avg_entry * 100
                result = "winner" if pnl > 0 else "loser"
                self._set_last_trade_result(result)
                self._clear_position_state()
                
                base_signal.signal_type = SignalType.SHORT_EXIT
                base_signal.reason = f"Exit: price broke above {self.config.exit_lookback}-day high ({exit_channel.upper:.2f})"
                
                self._logger.log("turtle_short_exit_channel", {
                    "symbol": self.symbol,
                    "price": current_price,
                    "exit_channel_upper": exit_channel.upper,
                    "pnl_pct": pnl,
                    "result": result
                })
                
                return base_signal
            
            if initial_stop > 0 and current_price >= initial_stop:
                self._set_last_trade_result("loser")
                self._clear_position_state()
                
                base_signal.signal_type = SignalType.STOP_EXIT
                base_signal.reason = f"Stop hit at {initial_stop:.2f} (2N from entry)"
                
                self._logger.log("turtle_stop_exit", {
                    "symbol": self.symbol,
                    "price": current_price,
                    "stop_price": initial_stop,
                    "side": "short"
                })
                
                return base_signal
            
            if self.config.pyramid_enabled and current_units < self.config.max_units:
                last_entry = entry_prices[-1] if entry_prices else avg_entry
                pyramid_trigger = last_entry - (self.config.pyramid_trigger_atr * atr_n)
                
                if current_price <= pyramid_trigger:
                    new_stop = current_price + (self.config.stop_loss_atr_mult * atr_n)
                    
                    base_signal.signal_type = SignalType.PYRAMID_ADD
                    base_signal.target_units = current_units + 1
                    base_signal.stop_price = new_stop
                    base_signal.reason = f"Pyramid: adding unit {current_units + 1}, price moved 0.5N below last entry"
                    
                    self._set_current_units(current_units + 1)
                    self._add_entry_price(current_price)
                    self._set_initial_stop(new_stop)
                    
                    self._logger.log("turtle_pyramid_add", {
                        "symbol": self.symbol,
                        "price": current_price,
                        "new_units": current_units + 1,
                        "last_entry": last_entry,
                        "trigger": pyramid_trigger
                    })
                    
                    return base_signal
        
        base_signal.reason = "Holding position, no exit or pyramid trigger"
        return base_signal
    
    def record_trade_result(self, is_winner: bool):
        """
        Record trade result for winner filter.
        
        Call this after closing a position to properly track
        win/loss sequence for the filter rule.
        """
        result = "winner" if is_winner else "loser"
        self._set_last_trade_result(result)
        
        self._logger.log("turtle_trade_result_recorded", {
            "symbol": self.symbol,
            "result": result,
            "filter_enabled": self.config.winner_filter_enabled
        })
    
    def reset_state(self):
        """Reset all state for this symbol (use when starting fresh)."""
        self._clear_position_state()
        delete_state(f"{self._state_prefix}.last_trade_result")
        
        self._logger.log("turtle_state_reset", {"symbol": self.symbol})


_turtle_instances: Dict[str, TurtleTrend] = {}


def get_turtle_trend(symbol: str, config: Optional[TurtleConfig] = None) -> TurtleTrend:
    """
    Get or create TurtleTrend instance for a symbol.
    
    Singleton pattern ensures consistent state across calls.
    """
    if symbol not in _turtle_instances:
        _turtle_instances[symbol] = TurtleTrend(symbol, config)
    return _turtle_instances[symbol]
