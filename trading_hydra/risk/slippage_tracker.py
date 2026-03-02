"""
Slippage Tracker - Track execution quality and slippage.

Logs the difference between expected and actual fill prices
to measure execution quality under different market conditions.

Key metrics:
- Slippage in basis points
- Slippage by VIX regime
- Slippage by time of day
- Slippage by asset class
"""

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from collections import defaultdict

from ..core.logging import get_logger
from ..core.state import get_state, set_state


@dataclass
class SlippageEvent:
    """Record of execution slippage."""
    timestamp: str
    symbol: str
    side: str  # buy or sell
    expected_price: float
    fill_price: float
    slippage_bps: float  # basis points
    qty: float
    order_type: str  # market, limit
    vix_level: Optional[float] = None
    time_of_day: Optional[str] = None  # premarket, open, midday, close
    asset_class: Optional[str] = None  # equity, option, crypto
    bot_id: Optional[str] = None


class SlippageTracker:
    """
    Track execution slippage for quality analysis.
    
    Records every fill and compares to expected price, building
    a slippage model over time.
    """
    
    SLIPPAGE_FILE = Path("logs/slippage_events.jsonl")
    MAX_HISTORY = 1000
    
    # Slippage thresholds (basis points)
    ACCEPTABLE_SLIPPAGE_BPS = 10  # 0.10%
    WARNING_SLIPPAGE_BPS = 25     # 0.25%
    SEVERE_SLIPPAGE_BPS = 50      # 0.50%
    
    def __init__(self):
        self._logger = get_logger()
        self._events: List[SlippageEvent] = []
        self._stats_by_regime: Dict[str, List[float]] = defaultdict(list)
        self._stats_by_time: Dict[str, List[float]] = defaultdict(list)
        self._stats_by_asset: Dict[str, List[float]] = defaultdict(list)
        
        self._load_history()
    
    def _load_history(self) -> None:
        """Load historical slippage events."""
        try:
            if self.SLIPPAGE_FILE.exists():
                with open(self.SLIPPAGE_FILE, 'r') as f:
                    for line in f:
                        if line.strip():
                            data = json.loads(line)
                            self._events.append(SlippageEvent(**data))
                
                self._events = self._events[-self.MAX_HISTORY:]
                self._rebuild_stats()
        except Exception as e:
            self._logger.error(f"Failed to load slippage history: {e}")
    
    def _rebuild_stats(self) -> None:
        """Rebuild statistics from loaded events."""
        for event in self._events:
            if event.vix_level:
                regime = self._vix_to_regime(event.vix_level)
                self._stats_by_regime[regime].append(event.slippage_bps)
            
            if event.time_of_day:
                self._stats_by_time[event.time_of_day].append(event.slippage_bps)
            
            if event.asset_class:
                self._stats_by_asset[event.asset_class].append(event.slippage_bps)
    
    def _vix_to_regime(self, vix: float) -> str:
        """Convert VIX level to regime name."""
        if vix < 15:
            return "low_vol"
        elif vix < 20:
            return "normal"
        elif vix < 30:
            return "elevated"
        else:
            return "high_vol"
    
    def _get_time_of_day(self) -> str:
        """Get current time-of-day bucket."""
        from ..core.clock import get_market_clock
        clock = get_market_clock()
        now = clock.now_naive()
        hour = now.hour
        minute = now.minute
        
        # PST times
        if hour < 6 or (hour == 6 and minute < 30):
            return "premarket"
        elif hour < 7:
            return "open"
        elif hour < 10:
            return "morning"
        elif hour < 12:
            return "midday"
        elif hour < 13:
            return "close"
        else:
            return "afterhours"
    
    def record_fill(
        self,
        symbol: str,
        side: str,
        expected_price: float,
        fill_price: float,
        qty: float,
        order_type: str = "market",
        vix_level: Optional[float] = None,
        asset_class: Optional[str] = None,
        bot_id: Optional[str] = None
    ) -> SlippageEvent:
        """
        Record a fill and calculate slippage.
        
        Args:
            symbol: Trading symbol
            side: "buy" or "sell"
            expected_price: Price we expected to get
            fill_price: Actual fill price
            qty: Quantity filled
            order_type: "market" or "limit"
            vix_level: Current VIX level
            asset_class: "equity", "option", or "crypto"
            bot_id: Bot that placed the order
            
        Returns:
            SlippageEvent with calculated slippage
        """
        # Calculate slippage in basis points
        # For buys: positive slippage = paid more than expected (bad)
        # For sells: positive slippage = received less than expected (bad)
        if side.lower() == "buy":
            slippage_bps = ((fill_price - expected_price) / expected_price) * 10000
        else:
            slippage_bps = ((expected_price - fill_price) / expected_price) * 10000
        
        time_of_day = self._get_time_of_day()
        
        event = SlippageEvent(
            timestamp=datetime.utcnow().isoformat(),
            symbol=symbol,
            side=side.lower(),
            expected_price=expected_price,
            fill_price=fill_price,
            slippage_bps=round(slippage_bps, 2),
            qty=qty,
            order_type=order_type,
            vix_level=vix_level,
            time_of_day=time_of_day,
            asset_class=asset_class,
            bot_id=bot_id
        )
        
        self._events.append(event)
        if len(self._events) > self.MAX_HISTORY:
            self._events = self._events[-self.MAX_HISTORY:]
        
        # Update stats
        if vix_level:
            regime = self._vix_to_regime(vix_level)
            self._stats_by_regime[regime].append(slippage_bps)
        
        self._stats_by_time[time_of_day].append(slippage_bps)
        
        if asset_class:
            self._stats_by_asset[asset_class].append(slippage_bps)
        
        # Log
        log_level = "slippage_fill"
        if abs(slippage_bps) >= self.SEVERE_SLIPPAGE_BPS:
            log_level = "slippage_severe"
        elif abs(slippage_bps) >= self.WARNING_SLIPPAGE_BPS:
            log_level = "slippage_warning"
        
        self._logger.log(log_level, {
            "symbol": symbol,
            "side": side,
            "expected": expected_price,
            "fill": fill_price,
            "slippage_bps": round(slippage_bps, 2),
            "qty": qty,
            "order_type": order_type,
            "vix_regime": self._vix_to_regime(vix_level) if vix_level else None,
            "time_of_day": time_of_day,
            "asset_class": asset_class
        })
        
        # Persist
        self._persist_event(event)
        
        return event
    
    def _persist_event(self, event: SlippageEvent) -> None:
        """Append event to JSONL file."""
        try:
            self.SLIPPAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.SLIPPAGE_FILE, 'a') as f:
                f.write(json.dumps(asdict(event)) + "\n")
        except Exception as e:
            self._logger.error(f"Failed to persist slippage event: {e}")
    
    def get_slippage_stats(self) -> Dict[str, Any]:
        """Get slippage statistics summary."""
        all_slippage = [e.slippage_bps for e in self._events]
        
        def stats(values: List[float]) -> Dict[str, float]:
            if not values:
                return {"mean": 0, "median": 0, "p95": 0, "count": 0}
            sorted_vals = sorted(values)
            n = len(sorted_vals)
            return {
                "mean": round(sum(values) / n, 2),
                "median": round(sorted_vals[n // 2], 2),
                "p95": round(sorted_vals[int(n * 0.95)] if n > 20 else max(sorted_vals), 2),
                "count": n
            }
        
        return {
            "overall": stats(all_slippage),
            "by_regime": {
                regime: stats(values) 
                for regime, values in self._stats_by_regime.items()
            },
            "by_time": {
                time: stats(values)
                for time, values in self._stats_by_time.items()
            },
            "by_asset": {
                asset: stats(values)
                for asset, values in self._stats_by_asset.items()
            },
            "thresholds": {
                "acceptable_bps": self.ACCEPTABLE_SLIPPAGE_BPS,
                "warning_bps": self.WARNING_SLIPPAGE_BPS,
                "severe_bps": self.SEVERE_SLIPPAGE_BPS
            }
        }
    
    def get_slippage_haircut(self, vix_level: Optional[float] = None) -> float:
        """
        Get recommended slippage haircut for backtesting.
        
        Returns the average slippage in percentage terms to subtract
        from expected returns in backtests.
        """
        if vix_level:
            regime = self._vix_to_regime(vix_level)
            if regime in self._stats_by_regime and self._stats_by_regime[regime]:
                values = self._stats_by_regime[regime]
                avg_bps = sum(values) / len(values)
                return avg_bps / 10000  # Convert to percentage
        
        all_slippage = [e.slippage_bps for e in self._events]
        if all_slippage:
            avg_bps = sum(all_slippage) / len(all_slippage)
            return avg_bps / 10000
        
        # Default haircut if no data
        return 0.001  # 10 bps default


# Singleton
_slippage_tracker: Optional[SlippageTracker] = None


def get_slippage_tracker() -> SlippageTracker:
    """Get or create SlippageTracker singleton."""
    global _slippage_tracker
    if _slippage_tracker is None:
        _slippage_tracker = SlippageTracker()
    return _slippage_tracker
