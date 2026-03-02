"""
Thread-safe state management for HydraSensors.

All state is protected by RLock for concurrent access from:
- Background sensor thread (writes)
- Trading bot threads (reads)
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any
from enum import Enum


class RegimeState(Enum):
    """Market regime classification."""
    UNKNOWN = "unknown"
    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    NEUTRAL = "neutral"


@dataclass
class RegimeData:
    """Current market regime state."""
    state: RegimeState = RegimeState.UNKNOWN
    confidence: float = 0.0
    risk_on: Optional[bool] = None  # True=risk_on, False=risk_off, None=unknown
    
    # Component scores (for explainability)
    breadth_score: float = 0.0
    volatility_score: float = 0.0
    momentum_score: float = 0.0
    leadership_score: float = 0.0
    
    # Metadata
    last_update: Optional[datetime] = None
    notes: str = ""
    
    def to_dict(self) -> Dict:
        return {
            "state": self.state.value,
            "confidence": self.confidence,
            "risk_on": self.risk_on,
            "breadth_score": self.breadth_score,
            "volatility_score": self.volatility_score,
            "momentum_score": self.momentum_score,
            "leadership_score": self.leadership_score,
            "last_update": self.last_update.isoformat() if self.last_update else None,
            "notes": self.notes,
        }


@dataclass
class TickerSignal:
    """Signal data for a single ticker."""
    ticker: str
    tags: List[str] = field(default_factory=list)
    priority: int = 3
    
    # Price data
    last_price: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    volume: Optional[int] = None
    
    # Indicators
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    rsi_14: Optional[float] = None
    atr_14: Optional[float] = None
    
    # Returns
    return_1d: Optional[float] = None
    return_5d: Optional[float] = None
    return_20d: Optional[float] = None
    
    # Relative strength vs benchmark
    rs_vs_spy_5d: Optional[float] = None
    rs_vs_spy_20d: Optional[float] = None
    
    # Metadata
    last_quote_ts: Optional[datetime] = None
    last_bar_ts: Optional[datetime] = None
    
    def to_dict(self) -> Dict:
        return {
            "ticker": self.ticker,
            "tags": self.tags,
            "priority": self.priority,
            "last_price": self.last_price,
            "bid": self.bid,
            "ask": self.ask,
            "volume": self.volume,
            "sma_20": self.sma_20,
            "sma_50": self.sma_50,
            "sma_200": self.sma_200,
            "rsi_14": self.rsi_14,
            "atr_14": self.atr_14,
            "return_1d": self.return_1d,
            "return_5d": self.return_5d,
            "return_20d": self.return_20d,
            "rs_vs_spy_5d": self.rs_vs_spy_5d,
            "rs_vs_spy_20d": self.rs_vs_spy_20d,
            "last_quote_ts": self.last_quote_ts.isoformat() if self.last_quote_ts else None,
            "last_bar_ts": self.last_bar_ts.isoformat() if self.last_bar_ts else None,
        }


@dataclass
class BreadthReading:
    """Breadth sensor reading (e.g., RSP vs SPY spread)."""
    name: str
    ticker: str
    benchmark: str
    
    # Spread values
    spread_1d: Optional[float] = None
    spread_5d: Optional[float] = None
    spread_20d: Optional[float] = None
    
    # Current prices
    ticker_price: Optional[float] = None
    benchmark_price: Optional[float] = None
    
    # Interpretation
    bullish: Optional[bool] = None
    description: str = ""
    
    last_update: Optional[datetime] = None
    
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "ticker": self.ticker,
            "benchmark": self.benchmark,
            "spread_1d": self.spread_1d,
            "spread_5d": self.spread_5d,
            "spread_20d": self.spread_20d,
            "ticker_price": self.ticker_price,
            "benchmark_price": self.benchmark_price,
            "bullish": self.bullish,
            "description": self.description,
            "last_update": self.last_update.isoformat() if self.last_update else None,
        }


class ThreadSafeState:
    """
    Thread-safe container for all sensor state.
    
    Uses RLock to allow recursive locking and safe concurrent access.
    Reads are fast (just acquire lock, copy data, release).
    Writes happen only from the sensor background thread.
    """
    
    def __init__(self):
        self._lock = threading.RLock()
        
        # Core state
        self._regime: RegimeData = RegimeData()
        self._signals: Dict[str, TickerSignal] = {}
        self._breadth: Dict[str, BreadthReading] = {}
        self._watchlists: Dict[str, List[str]] = {}
        self._ticker_tags: Dict[str, List[str]] = {}
        
        # Status tracking
        self._ready: bool = False
        self._last_update: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._warmup_progress: float = 0.0
    
    # --- Regime ---
    
    def set_regime(self, regime: RegimeData) -> None:
        """Update regime state (called by sensor thread)."""
        with self._lock:
            self._regime = regime
            self._last_update = datetime.now()
    
    def get_regime(self) -> RegimeData:
        """Get current regime state (thread-safe copy)."""
        with self._lock:
            return RegimeData(
                state=self._regime.state,
                confidence=self._regime.confidence,
                risk_on=self._regime.risk_on,
                breadth_score=self._regime.breadth_score,
                volatility_score=self._regime.volatility_score,
                momentum_score=self._regime.momentum_score,
                leadership_score=self._regime.leadership_score,
                last_update=self._regime.last_update,
                notes=self._regime.notes,
            )
    
    # --- Signals ---
    
    def set_signal(self, ticker: str, signal: TickerSignal) -> None:
        """Update signal for a ticker."""
        with self._lock:
            self._signals[ticker] = signal
    
    def get_signal(self, ticker: str) -> Optional[TickerSignal]:
        """Get signal for a ticker."""
        with self._lock:
            sig = self._signals.get(ticker)
            if sig:
                # Return a copy
                return TickerSignal(
                    ticker=sig.ticker,
                    tags=sig.tags.copy(),
                    priority=sig.priority,
                    last_price=sig.last_price,
                    bid=sig.bid,
                    ask=sig.ask,
                    volume=sig.volume,
                    sma_20=sig.sma_20,
                    sma_50=sig.sma_50,
                    sma_200=sig.sma_200,
                    rsi_14=sig.rsi_14,
                    atr_14=sig.atr_14,
                    return_1d=sig.return_1d,
                    return_5d=sig.return_5d,
                    return_20d=sig.return_20d,
                    rs_vs_spy_5d=sig.rs_vs_spy_5d,
                    rs_vs_spy_20d=sig.rs_vs_spy_20d,
                    last_quote_ts=sig.last_quote_ts,
                    last_bar_ts=sig.last_bar_ts,
                )
            return None
    
    def get_signals(self, tag: str = None, limit: int = None) -> List[TickerSignal]:
        """Get signals, optionally filtered by tag."""
        with self._lock:
            signals = list(self._signals.values())
            
            if tag:
                signals = [s for s in signals if tag in s.tags]
            
            # Sort by priority (lower = higher priority)
            signals.sort(key=lambda s: s.priority)
            
            if limit:
                signals = signals[:limit]
            
            # Return copies
            return [
                TickerSignal(
                    ticker=s.ticker,
                    tags=s.tags.copy(),
                    priority=s.priority,
                    last_price=s.last_price,
                    bid=s.bid,
                    ask=s.ask,
                    volume=s.volume,
                    sma_20=s.sma_20,
                    sma_50=s.sma_50,
                    sma_200=s.sma_200,
                    rsi_14=s.rsi_14,
                    atr_14=s.atr_14,
                    return_1d=s.return_1d,
                    return_5d=s.return_5d,
                    return_20d=s.return_20d,
                    rs_vs_spy_5d=s.rs_vs_spy_5d,
                    rs_vs_spy_20d=s.rs_vs_spy_20d,
                    last_quote_ts=s.last_quote_ts,
                    last_bar_ts=s.last_bar_ts,
                )
                for s in signals
            ]
    
    # --- Breadth ---
    
    def set_breadth(self, name: str, reading: BreadthReading) -> None:
        """Update breadth reading."""
        with self._lock:
            self._breadth[name] = reading
    
    def get_breadth(self) -> Dict[str, BreadthReading]:
        """Get all breadth readings."""
        with self._lock:
            return {
                k: BreadthReading(
                    name=v.name,
                    ticker=v.ticker,
                    benchmark=v.benchmark,
                    spread_1d=v.spread_1d,
                    spread_5d=v.spread_5d,
                    spread_20d=v.spread_20d,
                    ticker_price=v.ticker_price,
                    benchmark_price=v.benchmark_price,
                    bullish=v.bullish,
                    description=v.description,
                    last_update=v.last_update,
                )
                for k, v in self._breadth.items()
            }
    
    # --- Watchlists ---
    
    def set_watchlists(self, watchlists: Dict[str, List[str]]) -> None:
        """Set all watchlists."""
        with self._lock:
            self._watchlists = {k: v.copy() for k, v in watchlists.items()}
    
    def set_ticker_tags(self, ticker_tags: Dict[str, List[str]]) -> None:
        """Set ticker tag mappings."""
        with self._lock:
            self._ticker_tags = {k: v.copy() for k, v in ticker_tags.items()}
    
    def get_watchlist(self, name: str) -> List[str]:
        """Get tickers in a named watchlist."""
        with self._lock:
            return self._watchlists.get(name, []).copy()
    
    def get_tickers_by_tag(self, tag: str) -> List[str]:
        """Get all tickers with a specific tag."""
        with self._lock:
            return [
                ticker for ticker, tags in self._ticker_tags.items()
                if tag in tags
            ]
    
    def get_all_tickers(self) -> List[str]:
        """Get all known tickers."""
        with self._lock:
            return list(self._ticker_tags.keys())
    
    # --- Status ---
    
    def set_ready(self, ready: bool) -> None:
        """Set ready status."""
        with self._lock:
            self._ready = ready
    
    def is_ready(self) -> bool:
        """Check if sensors are ready."""
        with self._lock:
            return self._ready
    
    def set_warmup_progress(self, progress: float) -> None:
        """Set warmup progress (0.0 to 1.0)."""
        with self._lock:
            self._warmup_progress = progress
    
    def get_warmup_progress(self) -> float:
        """Get warmup progress."""
        with self._lock:
            return self._warmup_progress
    
    def set_error(self, error: str) -> None:
        """Record an error."""
        with self._lock:
            self._last_error = error
    
    def get_last_error(self) -> Optional[str]:
        """Get last error."""
        with self._lock:
            return self._last_error
    
    def get_last_update(self) -> Optional[datetime]:
        """Get last update timestamp."""
        with self._lock:
            return self._last_update
    
    def to_summary_dict(self) -> Dict[str, Any]:
        """Get summary of all state for debugging/export."""
        with self._lock:
            return {
                "ready": self._ready,
                "warmup_progress": self._warmup_progress,
                "last_update": self._last_update.isoformat() if self._last_update else None,
                "last_error": self._last_error,
                "regime": self._regime.to_dict(),
                "signal_count": len(self._signals),
                "breadth_count": len(self._breadth),
                "watchlist_count": len(self._watchlists),
                "ticker_count": len(self._ticker_tags),
            }
