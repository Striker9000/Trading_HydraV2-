"""
=============================================================================
Config Performance Logger - Track trade outcomes for ML training
=============================================================================

Logs every trade outcome with full config snapshot to build ML training data.

Data collected:
- Trade entry/exit details
- Full bot config at time of trade
- Market conditions (VIX, regime, etc.)
- Outcome (P&L, hold time, exit reason)

Philosophy:
- Every trade is a learning opportunity
- Rich feature set for ML optimization
- Historical data enables backtesting config changes
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional, Any
import json
import os
import threading

from ..core.logging import get_logger
from ..core.state import get_state, set_state


@dataclass
class TradeOutcome:
    """Complete record of a trade for ML training."""
    trade_id: str
    bot_id: str
    symbol: str
    asset_class: str  # equity, option, crypto
    side: str  # buy, sell
    
    entry_timestamp: str
    entry_price: float
    entry_qty: float
    entry_notional: float
    
    exit_timestamp: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    
    pnl_usd: Optional[float] = None
    pnl_pct: Optional[float] = None
    hold_minutes: Optional[float] = None
    
    config_snapshot: Dict[str, Any] = field(default_factory=dict)
    market_conditions: Dict[str, Any] = field(default_factory=dict)
    
    ml_signal_score: Optional[float] = None
    ml_model_version: Optional[str] = None
    
    slippage_pct: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ConfigPerformanceLogger:
    """
    Log trade outcomes with config snapshots for ML training.
    
    Writes to JSONL file for easy batch processing.
    Also stores recent trades in state for quick access.
    """
    
    LOG_FILE = "logs/trade_outcomes.jsonl"
    STATE_KEY = "config_perf.recent_trades"
    MAX_RECENT_TRADES = 1000
    
    def __init__(self):
        self._logger = get_logger()
        self._recent_trades: List[TradeOutcome] = []
        self._lock = threading.Lock()
        
        self._ensure_log_file()
        self._load_state()
        
        self._logger.log("config_performance_logger_init", {
            "log_file": self.LOG_FILE,
            "recent_trades_loaded": len(self._recent_trades)
        })
    
    def _ensure_log_file(self) -> None:
        """Ensure log directory and file exist."""
        try:
            os.makedirs(os.path.dirname(self.LOG_FILE), exist_ok=True)
            if not os.path.exists(self.LOG_FILE):
                with open(self.LOG_FILE, 'w') as f:
                    pass
        except Exception as e:
            self._logger.error(f"Failed to create log file: {e}")
    
    def _load_state(self) -> None:
        """Load recent trades from state."""
        try:
            saved = get_state(self.STATE_KEY, [])
            self._recent_trades = [TradeOutcome(**t) for t in saved[-self.MAX_RECENT_TRADES:]]
        except Exception as e:
            self._logger.error(f"Failed to load recent trades: {e}")
    
    def _save_state(self) -> None:
        """Save recent trades to state."""
        try:
            data = [t.to_dict() for t in self._recent_trades[-self.MAX_RECENT_TRADES:]]
            set_state(self.STATE_KEY, data)
        except Exception as e:
            self._logger.error(f"Failed to save recent trades: {e}")
    
    def log_trade_entry(
        self,
        trade_id: str,
        bot_id: str,
        symbol: str,
        asset_class: str,
        side: str,
        entry_price: float,
        entry_qty: float,
        config_snapshot: Dict[str, Any],
        market_conditions: Dict[str, Any],
        ml_signal_score: Optional[float] = None,
        ml_model_version: Optional[str] = None
    ) -> TradeOutcome:
        """
        Log a trade entry with full context.
        
        Call this when opening a new position.
        """
        trade = TradeOutcome(
            trade_id=trade_id,
            bot_id=bot_id,
            symbol=symbol,
            asset_class=asset_class,
            side=side,
            entry_timestamp=datetime.utcnow().isoformat() + "Z",
            entry_price=entry_price,
            entry_qty=entry_qty,
            entry_notional=entry_price * entry_qty,
            config_snapshot=config_snapshot,
            market_conditions=market_conditions,
            ml_signal_score=ml_signal_score,
            ml_model_version=ml_model_version
        )
        
        with self._lock:
            self._recent_trades.append(trade)
            self._save_state()
        
        self._logger.log("trade_entry_logged", {
            "trade_id": trade_id,
            "bot_id": bot_id,
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "ml_score": ml_signal_score
        })
        
        return trade
    
    def log_trade_exit(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str,
        slippage_pct: Optional[float] = None
    ) -> Optional[TradeOutcome]:
        """
        Log a trade exit and calculate P&L.
        
        Call this when closing a position.
        """
        with self._lock:
            trade = None
            for t in reversed(self._recent_trades):
                if t.trade_id == trade_id:
                    trade = t
                    break
            
            if trade is None:
                self._logger.warn(f"Trade not found for exit: {trade_id}")
                return None
            
            now = datetime.utcnow()
            trade.exit_timestamp = now.isoformat() + "Z"
            trade.exit_price = exit_price
            trade.exit_reason = exit_reason
            trade.slippage_pct = slippage_pct
            
            if trade.side == "buy":
                trade.pnl_usd = (exit_price - trade.entry_price) * trade.entry_qty
                trade.pnl_pct = ((exit_price - trade.entry_price) / trade.entry_price) * 100
            else:
                trade.pnl_usd = (trade.entry_price - exit_price) * trade.entry_qty
                trade.pnl_pct = ((trade.entry_price - exit_price) / trade.entry_price) * 100
            
            entry_time = datetime.fromisoformat(trade.entry_timestamp.replace("Z", "+00:00"))
            exit_time = now.replace(tzinfo=entry_time.tzinfo)
            trade.hold_minutes = (exit_time - entry_time).total_seconds() / 60.0
            
            self._append_to_log(trade)
            self._save_state()
        
        self._logger.log("trade_exit_logged", {
            "trade_id": trade_id,
            "bot_id": trade.bot_id,
            "symbol": trade.symbol,
            "pnl_usd": round(trade.pnl_usd, 2),
            "pnl_pct": round(trade.pnl_pct, 4),
            "hold_minutes": round(trade.hold_minutes, 1),
            "exit_reason": exit_reason
        })
        
        return trade
    
    def _append_to_log(self, trade: TradeOutcome) -> None:
        """Append completed trade to JSONL log file."""
        try:
            with open(self.LOG_FILE, 'a') as f:
                f.write(json.dumps(trade.to_dict()) + "\n")
        except Exception as e:
            self._logger.error(f"Failed to append to trade log: {e}")
    
    def get_recent_trades(self, bot_id: Optional[str] = None, limit: int = 100) -> List[TradeOutcome]:
        """Get recent trades, optionally filtered by bot."""
        trades = self._recent_trades
        if bot_id:
            trades = [t for t in trades if t.bot_id == bot_id]
        return trades[-limit:]
    
    def get_bot_statistics(self, bot_id: str) -> Dict[str, Any]:
        """Get performance statistics for a bot."""
        trades = [t for t in self._recent_trades if t.bot_id == bot_id and t.exit_timestamp]
        
        if not trades:
            return {"bot_id": bot_id, "trade_count": 0}
        
        wins = [t for t in trades if t.pnl_usd and t.pnl_usd > 0]
        losses = [t for t in trades if t.pnl_usd and t.pnl_usd < 0]
        
        total_pnl = sum(t.pnl_usd for t in trades if t.pnl_usd)
        avg_win = sum(t.pnl_usd for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.pnl_usd for t in losses) / len(losses) if losses else 0
        
        avg_hold = sum(t.hold_minutes for t in trades if t.hold_minutes) / len(trades)
        
        return {
            "bot_id": bot_id,
            "trade_count": len(trades),
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": len(wins) / len(trades) if trades else 0,
            "total_pnl_usd": round(total_pnl, 2),
            "avg_win_usd": round(avg_win, 2),
            "avg_loss_usd": round(avg_loss, 2),
            "avg_hold_minutes": round(avg_hold, 1),
            "profit_factor": abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')
        }
    
    def get_config_impact_analysis(self, config_key: str) -> Dict[str, Any]:
        """
        Analyze how a config parameter affects performance.
        
        Groups trades by config value and compares outcomes.
        """
        config_values: Dict[Any, List[TradeOutcome]] = {}
        
        for trade in self._recent_trades:
            if not trade.exit_timestamp or not trade.pnl_pct:
                continue
            
            value = self._get_nested_config(trade.config_snapshot, config_key)
            if value is None:
                continue
            
            str_value = str(value)
            if str_value not in config_values:
                config_values[str_value] = []
            config_values[str_value].append(trade)
        
        results = {}
        for value, trades in config_values.items():
            wins = [t for t in trades if t.pnl_usd and t.pnl_usd > 0]
            total_pnl = sum(t.pnl_usd for t in trades if t.pnl_usd)
            
            results[value] = {
                "trade_count": len(trades),
                "win_rate": len(wins) / len(trades) if trades else 0,
                "total_pnl_usd": round(total_pnl, 2),
                "avg_pnl_pct": round(sum(t.pnl_pct for t in trades if t.pnl_pct) / len(trades), 4)
            }
        
        return {
            "config_key": config_key,
            "value_performance": results
        }
    
    def _get_nested_config(self, config: Dict, key: str) -> Any:
        """Get nested config value using dot notation."""
        keys = key.split(".")
        value = config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return None
        return value


_config_logger: Optional[ConfigPerformanceLogger] = None


def get_config_performance_logger() -> ConfigPerformanceLogger:
    """Get or create ConfigPerformanceLogger singleton."""
    global _config_logger
    if _config_logger is None:
        _config_logger = ConfigPerformanceLogger()
    return _config_logger
