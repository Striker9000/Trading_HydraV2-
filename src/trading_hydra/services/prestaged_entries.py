"""
PreStagedEntry Service - Predetermined entries ready before market open.

This service runs at 6:00 AM PST to identify setups and calculate entry levels
BEFORE the move happens. Entries are "staged" and ready to execute immediately
when conditions are met at market open.

Supported Setup Types:
1. GAP_DOWN_BREAKDOWN - Puts when gap down continues lower
2. GAP_UP_BREAKOUT - Calls when gap up continues higher  
3. GAP_FADE_REVERSAL - Counter-trend when gaps fail (fade the gap)
4. REVERSAL_BOUNCE - Mean reversion when oversold/overbought reverses

Each staged entry has:
- Entry trigger level (price that activates the trade)
- Direction (calls or puts)
- Target and stop levels
- Expiration time (entry must trigger by X or cancelled)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Any
from zoneinfo import ZoneInfo

from ..core.state import get_state, set_state, delete_state
from ..core.logging import get_logger
from .alpaca_client import get_alpaca_client


class SetupType(Enum):
    GAP_DOWN_BREAKDOWN = "gap_down_breakdown"
    GAP_UP_BREAKOUT = "gap_up_breakout"
    GAP_FADE_REVERSAL = "gap_fade_reversal"
    REVERSAL_BOUNCE = "reversal_bounce"


class EntryDirection(Enum):
    CALLS = "calls"
    PUTS = "puts"


class EntryStatus(Enum):
    STAGED = "staged"
    TRIGGERED = "triggered"
    EXECUTED = "executed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass
class StagedEntry:
    """A predetermined entry ready to execute when conditions are met."""
    id: str
    symbol: str
    setup_type: SetupType
    direction: EntryDirection
    
    trigger_price: float
    trigger_condition: str
    
    prev_close: float
    gap_pct: float
    
    target_pct: float
    stop_pct: float
    
    max_option_premium: float
    preferred_dte: int
    
    staged_at: datetime
    expires_at: datetime
    
    status: EntryStatus = EntryStatus.STAGED
    triggered_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    execution_price: Optional[float] = None
    
    reasoning: str = ""
    confidence: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "setup_type": self.setup_type.value,
            "direction": self.direction.value,
            "trigger_price": self.trigger_price,
            "trigger_condition": self.trigger_condition,
            "prev_close": self.prev_close,
            "gap_pct": self.gap_pct,
            "target_pct": self.target_pct,
            "stop_pct": self.stop_pct,
            "max_option_premium": self.max_option_premium,
            "preferred_dte": self.preferred_dte,
            "staged_at": self.staged_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "status": self.status.value,
            "triggered_at": self.triggered_at.isoformat() if self.triggered_at else None,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "execution_price": self.execution_price,
            "reasoning": self.reasoning,
            "confidence": self.confidence
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StagedEntry":
        return cls(
            id=data["id"],
            symbol=data["symbol"],
            setup_type=SetupType(data["setup_type"]),
            direction=EntryDirection(data["direction"]),
            trigger_price=data["trigger_price"],
            trigger_condition=data["trigger_condition"],
            prev_close=data["prev_close"],
            gap_pct=data["gap_pct"],
            target_pct=data["target_pct"],
            stop_pct=data["stop_pct"],
            max_option_premium=data["max_option_premium"],
            preferred_dte=data["preferred_dte"],
            staged_at=datetime.fromisoformat(data["staged_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
            status=EntryStatus(data["status"]),
            triggered_at=datetime.fromisoformat(data["triggered_at"]) if data.get("triggered_at") else None,
            executed_at=datetime.fromisoformat(data["executed_at"]) if data.get("executed_at") else None,
            execution_price=data.get("execution_price"),
            reasoning=data.get("reasoning", ""),
            confidence=data.get("confidence", 0.0)
        )


@dataclass
class PreStagedConfig:
    """Configuration for pre-staged entry detection."""
    enabled: bool = True
    
    scan_start_time: str = "06:00"
    entry_window_start: str = "06:30"
    entry_window_end: str = "07:30"
    
    min_gap_pct: float = 0.5
    large_gap_pct: float = 1.5
    
    breakdown_buffer_pct: float = 0.15
    breakout_buffer_pct: float = 0.15
    
    reversal_rsi_oversold: float = 30.0
    reversal_rsi_overbought: float = 70.0
    
    gap_fade_min_gap: float = 1.0
    gap_fade_confirmation_candles: int = 2
    
    default_target_pct: float = 1.5
    default_stop_pct: float = 0.75
    
    max_staged_per_symbol: int = 2
    max_total_staged: int = 10
    
    preferred_dte_range: tuple = (3, 14)
    max_option_premium: float = 3.00
    
    watchlist: List[str] = field(default_factory=lambda: [
        "SPY", "QQQ", "IWM", "XLF", "XLE", "XLK",
        "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "META",
        "AMZN", "GOOGL", "SMCI", "AVGO", "COIN"
    ])


class PreStagedEntryService:
    """
    Service to prepare predetermined entries before market open.
    
    Runs at 6:00 AM PST to:
    1. Scan premarket for gaps and overnight moves
    2. Calculate entry trigger levels
    3. Stage entries ready to execute at market open
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._logger = get_logger()
        self._alpaca = get_alpaca_client()
        self._config = PreStagedConfig()
        self._staged_entries: Dict[str, StagedEntry] = {}
        self._pst = ZoneInfo("America/Los_Angeles")
        
        self._load_staged_entries()
        self._initialized = True
        
        self._logger.log("prestaged_service_init", {
            "watchlist_count": len(self._config.watchlist),
            "staged_count": len(self._staged_entries)
        })
    
    def _load_staged_entries(self):
        """Load staged entries from persistent state."""
        try:
            data = get_state("prestaged_entries")
            if data:
                for entry_id, entry_data in data.items():
                    self._staged_entries[entry_id] = StagedEntry.from_dict(entry_data)
        except Exception as e:
            self._logger.error(f"Failed to load staged entries: {e}")
    
    def _save_staged_entries(self):
        """Persist staged entries to state."""
        try:
            data = {
                entry_id: entry.to_dict() 
                for entry_id, entry in self._staged_entries.items()
            }
            set_state("prestaged_entries", data)
        except Exception as e:
            self._logger.error(f"Failed to save staged entries: {e}")
    
    def run_premarket_scan(self) -> List[StagedEntry]:
        """
        Run at 6:00 AM PST to identify setups and stage entries.
        
        Returns list of newly staged entries.
        """
        now_pst = datetime.now(self._pst)
        new_entries = []
        
        self._logger.log("prestaged_scan_start", {
            "time_pst": now_pst.strftime("%H:%M"),
            "watchlist": self._config.watchlist
        })
        
        self._expire_old_entries()
        
        for symbol in self._config.watchlist:
            try:
                entries = self._analyze_symbol(symbol)
                for entry in entries:
                    if len(self._staged_entries) < self._config.max_total_staged:
                        self._staged_entries[entry.id] = entry
                        new_entries.append(entry)
                        
                        self._logger.log("prestaged_entry_created", {
                            "id": entry.id,
                            "symbol": entry.symbol,
                            "setup_type": entry.setup_type.value,
                            "direction": entry.direction.value,
                            "trigger_price": entry.trigger_price,
                            "trigger_condition": entry.trigger_condition,
                            "gap_pct": entry.gap_pct,
                            "reasoning": entry.reasoning
                        })
            except Exception as e:
                self._logger.error(f"PreStaged scan error for {symbol}: {e}")
        
        self._save_staged_entries()
        
        self._logger.log("prestaged_scan_complete", {
            "new_entries": len(new_entries),
            "total_staged": len(self._staged_entries)
        })
        
        return new_entries
    
    def _analyze_symbol(self, symbol: str) -> List[StagedEntry]:
        """Analyze a symbol for potential staged entries."""
        entries = []
        now = datetime.now(self._pst)
        
        existing_count = sum(
            1 for e in self._staged_entries.values() 
            if e.symbol == symbol and e.status == EntryStatus.STAGED
        )
        if existing_count >= self._config.max_staged_per_symbol:
            return entries
        
        try:
            quote = self._alpaca.get_latest_quote(symbol)
            if not quote:
                return entries

            current_price = (quote.get("bid", 0) + quote.get("ask", 0)) / 2
            if current_price <= 0:
                return entries

            bars = self._alpaca.get_stock_bars(symbol, "1Day", limit=5)
            if not bars or len(bars) < 2:
                return entries
            
            prev_close = bars[-2].get("close", 0)
            if prev_close <= 0:
                return entries
            
            gap_pct = ((current_price - prev_close) / prev_close) * 100
            
            if abs(gap_pct) >= self._config.min_gap_pct:
                if gap_pct < -self._config.min_gap_pct:
                    entry = self._create_gap_down_entry(
                        symbol, current_price, prev_close, gap_pct, now
                    )
                    if entry:
                        entries.append(entry)
                    
                    if gap_pct < -self._config.large_gap_pct:
                        fade_entry = self._create_gap_fade_entry(
                            symbol, current_price, prev_close, gap_pct, now, "down"
                        )
                        if fade_entry:
                            entries.append(fade_entry)
                
                elif gap_pct > self._config.min_gap_pct:
                    entry = self._create_gap_up_entry(
                        symbol, current_price, prev_close, gap_pct, now
                    )
                    if entry:
                        entries.append(entry)
                    
                    if gap_pct > self._config.large_gap_pct:
                        fade_entry = self._create_gap_fade_entry(
                            symbol, current_price, prev_close, gap_pct, now, "up"
                        )
                        if fade_entry:
                            entries.append(fade_entry)
            
            reversal_entry = self._check_reversal_setup(
                symbol, current_price, prev_close, bars, now
            )
            if reversal_entry:
                entries.append(reversal_entry)
        
        except Exception as e:
            self._logger.error(f"Symbol analysis error for {symbol}: {e}")
        
        return entries
    
    def _create_gap_down_entry(
        self, symbol: str, current: float, prev_close: float, 
        gap_pct: float, now: datetime
    ) -> Optional[StagedEntry]:
        """Create a staged put entry for gap down breakdown."""
        
        trigger_price = current * (1 - self._config.breakdown_buffer_pct / 100)
        
        expires_at = now.replace(
            hour=int(self._config.entry_window_end.split(":")[0]),
            minute=int(self._config.entry_window_end.split(":")[1]),
            second=0, microsecond=0
        )
        
        entry_id = f"{symbol}_gap_down_{now.strftime('%Y%m%d_%H%M')}"
        
        return StagedEntry(
            id=entry_id,
            symbol=symbol,
            setup_type=SetupType.GAP_DOWN_BREAKDOWN,
            direction=EntryDirection.PUTS,
            trigger_price=round(trigger_price, 2),
            trigger_condition=f"price < {trigger_price:.2f}",
            prev_close=prev_close,
            gap_pct=round(gap_pct, 2),
            target_pct=self._config.default_target_pct,
            stop_pct=self._config.default_stop_pct,
            max_option_premium=self._config.max_option_premium,
            preferred_dte=self._config.preferred_dte_range[0],
            staged_at=now,
            expires_at=expires_at,
            reasoning=f"Gap down {gap_pct:.1f}%, watching for breakdown below {trigger_price:.2f}",
            confidence=min(0.9, 0.5 + abs(gap_pct) * 0.1)
        )
    
    def _create_gap_up_entry(
        self, symbol: str, current: float, prev_close: float,
        gap_pct: float, now: datetime
    ) -> Optional[StagedEntry]:
        """Create a staged call entry for gap up breakout."""
        
        trigger_price = current * (1 + self._config.breakout_buffer_pct / 100)
        
        expires_at = now.replace(
            hour=int(self._config.entry_window_end.split(":")[0]),
            minute=int(self._config.entry_window_end.split(":")[1]),
            second=0, microsecond=0
        )
        
        entry_id = f"{symbol}_gap_up_{now.strftime('%Y%m%d_%H%M')}"
        
        return StagedEntry(
            id=entry_id,
            symbol=symbol,
            setup_type=SetupType.GAP_UP_BREAKOUT,
            direction=EntryDirection.CALLS,
            trigger_price=round(trigger_price, 2),
            trigger_condition=f"price > {trigger_price:.2f}",
            prev_close=prev_close,
            gap_pct=round(gap_pct, 2),
            target_pct=self._config.default_target_pct,
            stop_pct=self._config.default_stop_pct,
            max_option_premium=self._config.max_option_premium,
            preferred_dte=self._config.preferred_dte_range[0],
            staged_at=now,
            expires_at=expires_at,
            reasoning=f"Gap up {gap_pct:.1f}%, watching for breakout above {trigger_price:.2f}",
            confidence=min(0.9, 0.5 + abs(gap_pct) * 0.1)
        )
    
    def _create_gap_fade_entry(
        self, symbol: str, current: float, prev_close: float,
        gap_pct: float, now: datetime, gap_direction: str
    ) -> Optional[StagedEntry]:
        """Create a staged entry for gap fade/reversal."""
        
        if gap_direction == "down":
            trigger_price = current * (1 + 0.3 / 100)
            direction = EntryDirection.CALLS
            condition = f"price > {trigger_price:.2f} (gap fade reversal)"
        else:
            trigger_price = current * (1 - 0.3 / 100)
            direction = EntryDirection.PUTS
            condition = f"price < {trigger_price:.2f} (gap fade reversal)"
        
        expires_at = now.replace(
            hour=int(self._config.entry_window_end.split(":")[0]),
            minute=int(self._config.entry_window_end.split(":")[1]),
            second=0, microsecond=0
        )
        
        entry_id = f"{symbol}_gap_fade_{now.strftime('%Y%m%d_%H%M')}"
        
        return StagedEntry(
            id=entry_id,
            symbol=symbol,
            setup_type=SetupType.GAP_FADE_REVERSAL,
            direction=direction,
            trigger_price=round(trigger_price, 2),
            trigger_condition=condition,
            prev_close=prev_close,
            gap_pct=round(gap_pct, 2),
            target_pct=self._config.default_target_pct * 0.75,
            stop_pct=self._config.default_stop_pct,
            max_option_premium=self._config.max_option_premium,
            preferred_dte=self._config.preferred_dte_range[0],
            staged_at=now,
            expires_at=expires_at,
            reasoning=f"Large gap {gap_direction} {abs(gap_pct):.1f}% may fade - watching for reversal",
            confidence=0.6
        )
    
    def _check_reversal_setup(
        self, symbol: str, current: float, prev_close: float,
        bars: List[Dict], now: datetime
    ) -> Optional[StagedEntry]:
        """Check for mean reversion reversal setup."""
        
        if len(bars) < 4:
            return None
        
        recent_closes = [b.get("close", 0) for b in bars[-4:]]
        if not all(recent_closes):
            return None
        
        avg_close = sum(recent_closes) / len(recent_closes)
        deviation_pct = ((current - avg_close) / avg_close) * 100
        
        if deviation_pct < -2.0:
            trigger_price = current * 1.002
            direction = EntryDirection.CALLS
            condition = f"price > {trigger_price:.2f} (oversold bounce)"
            reasoning = f"Oversold {deviation_pct:.1f}% below 4-day avg, watching for bounce"
            
        elif deviation_pct > 2.0:
            trigger_price = current * 0.998
            direction = EntryDirection.PUTS
            condition = f"price < {trigger_price:.2f} (overbought fade)"
            reasoning = f"Overbought {deviation_pct:.1f}% above 4-day avg, watching for pullback"
        else:
            return None
        
        expires_at = now.replace(
            hour=int(self._config.entry_window_end.split(":")[0]),
            minute=int(self._config.entry_window_end.split(":")[1]),
            second=0, microsecond=0
        )
        
        entry_id = f"{symbol}_reversal_{now.strftime('%Y%m%d_%H%M')}"
        
        return StagedEntry(
            id=entry_id,
            symbol=symbol,
            setup_type=SetupType.REVERSAL_BOUNCE,
            direction=direction,
            trigger_price=round(trigger_price, 2),
            trigger_condition=condition,
            prev_close=prev_close,
            gap_pct=0,
            target_pct=self._config.default_target_pct,
            stop_pct=self._config.default_stop_pct,
            max_option_premium=self._config.max_option_premium,
            preferred_dte=self._config.preferred_dte_range[0],
            staged_at=now,
            expires_at=expires_at,
            reasoning=reasoning,
            confidence=0.65
        )
    
    def check_triggers(self) -> List[StagedEntry]:
        """
        Check all staged entries for trigger conditions.
        Called every loop iteration during entry window.
        
        Returns list of entries that just triggered.
        """
        triggered = []
        now = datetime.now(self._pst)
        
        for entry_id, entry in list(self._staged_entries.items()):
            if entry.status != EntryStatus.STAGED:
                continue
            
            if now > entry.expires_at:
                entry.status = EntryStatus.EXPIRED
                self._logger.log("prestaged_entry_expired", {
                    "id": entry.id,
                    "symbol": entry.symbol,
                    "trigger_price": entry.trigger_price
                })
                continue
            
            try:
                quote = self._alpaca.get_latest_quote(entry.symbol)
                if not quote:
                    continue

                current_price = (quote.get("bid", 0) + quote.get("ask", 0)) / 2
                if current_price <= 0:
                    continue
                
                is_triggered = False
                
                if entry.setup_type == SetupType.GAP_DOWN_BREAKDOWN:
                    if current_price < entry.trigger_price:
                        is_triggered = True
                
                elif entry.setup_type == SetupType.GAP_UP_BREAKOUT:
                    if current_price > entry.trigger_price:
                        is_triggered = True
                
                elif entry.setup_type == SetupType.GAP_FADE_REVERSAL:
                    if entry.direction == EntryDirection.CALLS:
                        if current_price > entry.trigger_price:
                            is_triggered = True
                    else:
                        if current_price < entry.trigger_price:
                            is_triggered = True
                
                elif entry.setup_type == SetupType.REVERSAL_BOUNCE:
                    if entry.direction == EntryDirection.CALLS:
                        if current_price > entry.trigger_price:
                            is_triggered = True
                    else:
                        if current_price < entry.trigger_price:
                            is_triggered = True
                
                if is_triggered:
                    entry.status = EntryStatus.TRIGGERED
                    entry.triggered_at = now
                    triggered.append(entry)
                    
                    self._logger.log("prestaged_entry_triggered", {
                        "id": entry.id,
                        "symbol": entry.symbol,
                        "setup_type": entry.setup_type.value,
                        "direction": entry.direction.value,
                        "trigger_price": entry.trigger_price,
                        "current_price": current_price,
                        "gap_pct": entry.gap_pct
                    })
            
            except Exception as e:
                self._logger.error(f"Trigger check error for {entry.symbol}: {e}")
        
        if triggered:
            self._save_staged_entries()
        
        return triggered
    
    def mark_executed(self, entry_id: str, execution_price: float):
        """Mark a staged entry as executed."""
        if entry_id in self._staged_entries:
            entry = self._staged_entries[entry_id]
            entry.status = EntryStatus.EXECUTED
            entry.executed_at = datetime.now(self._pst)
            entry.execution_price = execution_price
            self._save_staged_entries()
            
            self._logger.log("prestaged_entry_executed", {
                "id": entry_id,
                "symbol": entry.symbol,
                "execution_price": execution_price
            })
    
    def cancel_entry(self, entry_id: str, reason: str = ""):
        """Cancel a staged entry."""
        if entry_id in self._staged_entries:
            entry = self._staged_entries[entry_id]
            entry.status = EntryStatus.CANCELLED
            self._save_staged_entries()
            
            self._logger.log("prestaged_entry_cancelled", {
                "id": entry_id,
                "symbol": entry.symbol,
                "reason": reason
            })
    
    def _expire_old_entries(self):
        """Clean up expired entries from previous days."""
        now = datetime.now(self._pst)
        today = now.date()
        
        for entry_id in list(self._staged_entries.keys()):
            entry = self._staged_entries[entry_id]
            if entry.staged_at.date() < today:
                if entry.status == EntryStatus.STAGED:
                    entry.status = EntryStatus.EXPIRED
                self._staged_entries.pop(entry_id, None)
        
        self._save_staged_entries()
    
    def get_staged_entries(self) -> List[StagedEntry]:
        """Get all currently staged entries."""
        return [
            e for e in self._staged_entries.values()
            if e.status == EntryStatus.STAGED
        ]
    
    def get_triggered_entries(self) -> List[StagedEntry]:
        """Get entries that have triggered but not yet executed."""
        return [
            e for e in self._staged_entries.values()
            if e.status == EntryStatus.TRIGGERED
        ]
    
    def get_entry_summary(self) -> Dict[str, Any]:
        """Get summary of all staged entries for dashboard."""
        staged = self.get_staged_entries()
        triggered = self.get_triggered_entries()
        
        return {
            "staged_count": len(staged),
            "triggered_count": len(triggered),
            "staged": [e.to_dict() for e in staged],
            "triggered": [e.to_dict() for e in triggered]
        }


def get_prestaged_service() -> PreStagedEntryService:
    """Get singleton instance of PreStagedEntryService."""
    return PreStagedEntryService()
