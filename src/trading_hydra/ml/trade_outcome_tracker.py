"""
Trade Outcome Tracker - Logs trade outcomes for ML model retraining.

Tracks entry conditions, exit results, and P&L to improve ML predictions.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict

from ..core.logging import get_logger


@dataclass
class TradeOutcome:
    """Complete trade record for ML training."""
    trade_id: str
    symbol: str
    bot_id: str
    side: str
    entry_time: str
    exit_time: Optional[str]
    entry_price: float
    exit_price: Optional[float]
    quantity: float
    pnl: Optional[float]
    pnl_pct: Optional[float]
    is_profitable: Optional[bool]
    hold_duration_minutes: Optional[float]
    
    # Entry conditions (features at entry time)
    entry_features: Dict[str, Any]
    
    # Market conditions at entry
    vix_at_entry: float
    hour_at_entry: int
    day_of_week: int
    
    # ML scoring at entry
    ml_probability: Optional[float]
    ml_recommendation: Optional[str]
    
    # Exit reason
    exit_reason: Optional[str]  # stop_loss, take_profit, trailing_stop, time_exit, manual


class TradeOutcomeTracker:
    """
    Tracks trade outcomes for ML model retraining.
    
    Logs complete trade lifecycle data including entry features,
    market conditions, and exit results. Data is persisted to JSONL
    for periodic model retraining.
    """
    
    OUTCOMES_FILE = Path("logs/trade_outcomes.jsonl")
    PENDING_TRADES_FILE = Path("state/pending_trades.json")
    
    def __init__(self):
        self._logger = get_logger()
        self._pending_trades: Dict[str, Dict[str, Any]] = {}
        self._load_pending_trades()
    
    def _load_pending_trades(self) -> None:
        """Load pending (open) trades from disk."""
        try:
            if self.PENDING_TRADES_FILE.exists():
                with open(self.PENDING_TRADES_FILE, 'r') as f:
                    self._pending_trades = json.load(f)
                self._logger.log("trade_tracker_loaded", {
                    "pending_count": len(self._pending_trades)
                })
        except Exception as e:
            self._logger.error(f"Failed to load pending trades: {e}")
            self._pending_trades = {}
    
    def _save_pending_trades(self) -> None:
        """Save pending trades to disk."""
        try:
            self.PENDING_TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.PENDING_TRADES_FILE, 'w') as f:
                json.dump(self._pending_trades, f, indent=2)
        except Exception as e:
            self._logger.error(f"Failed to save pending trades: {e}")
    
    def record_entry(
        self,
        trade_id: str,
        symbol: str,
        bot_id: str,
        side: str,
        entry_price: float,
        quantity: float,
        entry_features: Dict[str, Any],
        vix: float = 20.0,
        ml_probability: Optional[float] = None,
        ml_recommendation: Optional[str] = None
    ) -> None:
        """
        Record a trade entry for later outcome tracking.
        
        Args:
            trade_id: Unique identifier for the trade
            symbol: Trading symbol
            bot_id: Bot that initiated the trade
            side: Trade direction (buy, short, long_call, etc.)
            entry_price: Entry price
            quantity: Position size
            entry_features: Technical features at entry time
            vix: VIX level at entry
            ml_probability: ML model probability at entry
            ml_recommendation: ML model recommendation
        """
        now = datetime.utcnow()
        
        self._pending_trades[trade_id] = {
            "trade_id": trade_id,
            "symbol": symbol,
            "bot_id": bot_id,
            "side": side,
            "entry_time": now.isoformat(),
            "entry_price": entry_price,
            "quantity": quantity,
            "entry_features": entry_features,
            "vix_at_entry": vix,
            "hour_at_entry": now.hour,
            "day_of_week": now.weekday(),
            "ml_probability": ml_probability,
            "ml_recommendation": ml_recommendation
        }
        
        self._save_pending_trades()
        
        self._logger.log("trade_entry_recorded", {
            "trade_id": trade_id,
            "symbol": symbol,
            "bot_id": bot_id,
            "side": side,
            "entry_price": entry_price,
            "ml_probability": ml_probability
        })
    
    def record_exit(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str,
        pnl: Optional[float] = None
    ) -> Optional[TradeOutcome]:
        """
        Record a trade exit and compute outcome.
        
        Args:
            trade_id: Trade identifier from entry
            exit_price: Exit price
            exit_reason: Reason for exit (stop_loss, take_profit, etc.)
            pnl: Actual P&L if known
            
        Returns:
            Complete TradeOutcome record, or None if trade not found
        """
        if trade_id not in self._pending_trades:
            self._logger.warn(f"Trade {trade_id} not found in pending trades")
            return None
        
        entry = self._pending_trades.pop(trade_id)
        now = datetime.utcnow()
        
        # Calculate P&L if not provided
        if pnl is None:
            entry_price = entry["entry_price"]
            quantity = entry["quantity"]
            side = entry["side"]
            
            if side in ["buy", "long_call", "long_put"]:
                pnl = (exit_price - entry_price) * quantity
            else:  # short positions
                pnl = (entry_price - exit_price) * quantity
        
        entry_price = entry["entry_price"]
        pnl_pct = (pnl / (entry_price * entry["quantity"])) * 100 if entry_price > 0 else 0
        
        # Calculate hold duration
        entry_time = datetime.fromisoformat(entry["entry_time"])
        hold_duration = (now - entry_time).total_seconds() / 60  # minutes
        
        outcome = TradeOutcome(
            trade_id=trade_id,
            symbol=entry["symbol"],
            bot_id=entry["bot_id"],
            side=entry["side"],
            entry_time=entry["entry_time"],
            exit_time=now.isoformat(),
            entry_price=entry_price,
            exit_price=exit_price,
            quantity=entry["quantity"],
            pnl=pnl,
            pnl_pct=pnl_pct,
            is_profitable=pnl > 0,
            hold_duration_minutes=hold_duration,
            entry_features=entry["entry_features"],
            vix_at_entry=entry["vix_at_entry"],
            hour_at_entry=entry["hour_at_entry"],
            day_of_week=entry["day_of_week"],
            ml_probability=entry.get("ml_probability"),
            ml_recommendation=entry.get("ml_recommendation"),
            exit_reason=exit_reason
        )
        
        # Persist to JSONL
        self._persist_outcome(outcome)
        self._save_pending_trades()
        
        self._logger.log("trade_outcome_recorded", {
            "trade_id": trade_id,
            "symbol": entry["symbol"],
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "is_profitable": pnl > 0,
            "hold_duration_min": hold_duration,
            "exit_reason": exit_reason,
            "ml_probability": entry.get("ml_probability")
        })
        
        return outcome
    
    def _persist_outcome(self, outcome: TradeOutcome) -> None:
        """Append outcome to JSONL file for training."""
        try:
            self.OUTCOMES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.OUTCOMES_FILE, 'a') as f:
                f.write(json.dumps(asdict(outcome)) + "\n")
        except Exception as e:
            self._logger.error(f"Failed to persist trade outcome: {e}")
    
    def get_pending_trades(self) -> Dict[str, Dict[str, Any]]:
        """Get all pending (open) trades."""
        return self._pending_trades.copy()
    
    def get_outcomes_for_training(self, min_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Load trade outcomes for ML model training.
        
        Args:
            min_date: Only load outcomes after this date (ISO format)
            
        Returns:
            List of trade outcome dictionaries
        """
        outcomes = []
        
        if not self.OUTCOMES_FILE.exists():
            return outcomes
        
        try:
            with open(self.OUTCOMES_FILE, 'r') as f:
                for line in f:
                    if line.strip():
                        outcome = json.loads(line)
                        if min_date and outcome.get("entry_time", "") < min_date:
                            continue
                        outcomes.append(outcome)
        except Exception as e:
            self._logger.error(f"Failed to load trade outcomes: {e}")
        
        return outcomes
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get aggregate statistics from trade outcomes."""
        outcomes = self.get_outcomes_for_training()
        
        if not outcomes:
            return {"total_trades": 0}
        
        total = len(outcomes)
        profitable = sum(1 for o in outcomes if o.get("is_profitable"))
        total_pnl = sum(o.get("pnl", 0) for o in outcomes)
        
        # By bot
        by_bot = {}
        for o in outcomes:
            bot_id = o.get("bot_id", "unknown")
            if bot_id not in by_bot:
                by_bot[bot_id] = {"trades": 0, "profitable": 0, "pnl": 0}
            by_bot[bot_id]["trades"] += 1
            by_bot[bot_id]["pnl"] += o.get("pnl", 0)
            if o.get("is_profitable"):
                by_bot[bot_id]["profitable"] += 1
        
        return {
            "total_trades": total,
            "profitable_trades": profitable,
            "win_rate": profitable / total if total > 0 else 0,
            "total_pnl": total_pnl,
            "avg_pnl": total_pnl / total if total > 0 else 0,
            "by_bot": by_bot
        }


# Singleton instance
_tracker_instance: Optional[TradeOutcomeTracker] = None


def get_trade_tracker() -> TradeOutcomeTracker:
    """Get the singleton TradeOutcomeTracker instance."""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = TradeOutcomeTracker()
    return _tracker_instance
