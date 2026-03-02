"""
Decision Tracker Service - Tracks pending trading decisions for visibility

This service collects and exposes the current decision state from each bot,
allowing the dashboard to display what trades are being considered, what
blockers exist, and how close positions are to exits.

Decision States Tracked:
- Signals: Buy/sell/hold signals from each bot
- Blockers: Why a trade isn't happening (cooldown, max trades, etc.)
- Exit Proximity: How close positions are to trailing stops/exits
- Position Status: Current P&L and distance from targets
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass, field, asdict
import json
import os
from pathlib import Path

from ..core.state import get_state, set_state
from ..core.config import load_bots_config
from ..core.logging import get_logger

DECISION_RECORDS_FILE = "logs/decision_records.jsonl"


@dataclass
class SignalState:
    """Current signal state for a bot/symbol"""
    bot_id: str
    symbol: str
    signal: str  # "buy", "sell", "hold", "wait"
    strength: float  # 0.0 to 1.0
    reason: str
    timestamp: str
    
    
@dataclass
class BlockerState:
    """Reason why a trade isn't happening"""
    bot_id: str
    blocker_type: str  # "cooldown", "max_trades", "outside_session", "halted", etc.
    description: str
    clears_at: Optional[str] = None  # When the blocker clears (if known)


@dataclass
class ExitProximity:
    """How close a position is to exiting"""
    symbol: str
    bot_id: str
    position_side: str  # "long" or "short"
    entry_price: float
    current_price: float
    unrealized_pnl_pct: float
    trailing_stop_price: Optional[float] = None
    trailing_stop_pct_away: Optional[float] = None
    take_profit_price: Optional[float] = None
    take_profit_pct_away: Optional[float] = None
    stop_loss_price: Optional[float] = None
    stop_loss_pct_away: Optional[float] = None
    time_remaining_minutes: Optional[int] = None


@dataclass 
class BotDecisionState:
    """Complete decision state for a single bot"""
    bot_id: str
    bot_type: str  # "momentum", "crypto", "options"
    enabled: bool
    signals: List[SignalState] = field(default_factory=list)
    blockers: List[BlockerState] = field(default_factory=list)
    exit_proximity: List[ExitProximity] = field(default_factory=list)
    last_updated: str = ""


class DecisionTracker:
    """
    Tracks and exposes decision states from all trading bots.
    
    This allows visibility into:
    - What signals each bot is generating
    - What blockers are preventing trades
    - How close positions are to exits
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
        self._initialized = True
        self._logger = get_logger()
        self._decision_states: Dict[str, BotDecisionState] = {}
        self._load_from_state()
        self._ensure_decision_log_file()
    
    def _ensure_decision_log_file(self):
        """Ensure the decision records JSONL file exists"""
        try:
            log_dir = Path(DECISION_RECORDS_FILE).parent
            log_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self._logger.error(f"Failed to create decision records directory: {e}")
    
    def _write_decision_record_to_file(self, record: Dict[str, Any]):
        """Append a decision record to the JSONL file for durable audit trail"""
        try:
            with open(DECISION_RECORDS_FILE, "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            self._logger.error(f"Failed to write decision record to file: {e}")
    
    def _load_from_state(self):
        """Load saved decision states from persistent storage"""
        saved = get_state("decision_tracker.states", {})
        if isinstance(saved, dict):
            for bot_id, state_dict in saved.items():
                if isinstance(state_dict, dict):
                    try:
                        signals = [SignalState(**s) for s in state_dict.get("signals", [])]
                        blockers = [BlockerState(**b) for b in state_dict.get("blockers", [])]
                        exit_proximity = [ExitProximity(**e) for e in state_dict.get("exit_proximity", [])]
                        self._decision_states[bot_id] = BotDecisionState(
                            bot_id=state_dict.get("bot_id", bot_id),
                            bot_type=state_dict.get("bot_type", "unknown"),
                            enabled=state_dict.get("enabled", False),
                            signals=signals,
                            blockers=blockers,
                            exit_proximity=exit_proximity,
                            last_updated=state_dict.get("last_updated", "")
                        )
                    except Exception as e:
                        self._logger.error(f"Failed to load decision state for {bot_id}: {e}")
    
    def _save_to_state(self):
        """Save decision states to persistent storage"""
        states_dict = {}
        for bot_id, state in self._decision_states.items():
            states_dict[bot_id] = {
                "bot_id": state.bot_id,
                "bot_type": state.bot_type,
                "enabled": state.enabled,
                "signals": [asdict(s) for s in state.signals],
                "blockers": [asdict(b) for b in state.blockers],
                "exit_proximity": [asdict(e) for e in state.exit_proximity],
                "last_updated": state.last_updated
            }
        set_state("decision_tracker.states", states_dict)
    
    def update_signal(self, bot_id: str, bot_type: str, symbol: str, 
                      signal: str, strength: float = 0.5, reason: str = ""):
        """Update signal state for a bot/symbol"""
        now = datetime.utcnow().isoformat() + "Z"
        
        if bot_id not in self._decision_states:
            self._decision_states[bot_id] = BotDecisionState(
                bot_id=bot_id,
                bot_type=bot_type,
                enabled=True,
                last_updated=now
            )
        
        state = self._decision_states[bot_id]
        state.signals = [s for s in state.signals if s.symbol != symbol]
        state.signals.append(SignalState(
            bot_id=bot_id,
            symbol=symbol,
            signal=signal,
            strength=strength,
            reason=reason,
            timestamp=now
        ))
        state.last_updated = now
        
        self._logger.log("decision_signal_update", {
            "bot_id": bot_id,
            "symbol": symbol,
            "signal": signal,
            "strength": strength,
            "reason": reason
        })
        
        self._save_to_state()
    
    def update_blocker(self, bot_id: str, bot_type: str, blocker_type: str,
                       description: str, clears_at: Optional[str] = None):
        """Update or add a blocker for a bot"""
        now = datetime.utcnow().isoformat() + "Z"
        
        if bot_id not in self._decision_states:
            self._decision_states[bot_id] = BotDecisionState(
                bot_id=bot_id,
                bot_type=bot_type,
                enabled=True,
                last_updated=now
            )
        
        state = self._decision_states[bot_id]
        state.blockers = [b for b in state.blockers if b.blocker_type != blocker_type]
        state.blockers.append(BlockerState(
            bot_id=bot_id,
            blocker_type=blocker_type,
            description=description,
            clears_at=clears_at
        ))
        state.last_updated = now
        self._save_to_state()
    
    def clear_blocker(self, bot_id: str, blocker_type: str):
        """Remove a blocker when it's resolved"""
        if bot_id in self._decision_states:
            state = self._decision_states[bot_id]
            state.blockers = [b for b in state.blockers if b.blocker_type != blocker_type]
            state.last_updated = datetime.utcnow().isoformat() + "Z"
            self._save_to_state()
    
    def clear_all_blockers(self, bot_id: str):
        """Clear all blockers for a bot"""
        if bot_id in self._decision_states:
            state = self._decision_states[bot_id]
            state.blockers = []
            state.last_updated = datetime.utcnow().isoformat() + "Z"
            self._save_to_state()
    
    def update_exit_proximity(self, bot_id: str, bot_type: str, symbol: str,
                               position_side: str, entry_price: float,
                               current_price: float, unrealized_pnl_pct: float,
                               trailing_stop_price: Optional[float] = None,
                               take_profit_price: Optional[float] = None,
                               stop_loss_price: Optional[float] = None,
                               time_remaining_minutes: Optional[int] = None):
        """Update exit proximity for a position"""
        now = datetime.utcnow().isoformat() + "Z"
        
        if bot_id not in self._decision_states:
            self._decision_states[bot_id] = BotDecisionState(
                bot_id=bot_id,
                bot_type=bot_type,
                enabled=True,
                last_updated=now
            )
        
        trailing_stop_pct_away = None
        if trailing_stop_price and current_price > 0:
            if position_side == "long":
                trailing_stop_pct_away = ((current_price - trailing_stop_price) / current_price) * 100
            else:
                trailing_stop_pct_away = ((trailing_stop_price - current_price) / current_price) * 100
        
        take_profit_pct_away = None
        if take_profit_price and current_price > 0:
            if position_side == "long":
                take_profit_pct_away = ((take_profit_price - current_price) / current_price) * 100
            else:
                take_profit_pct_away = ((current_price - take_profit_price) / current_price) * 100
        
        stop_loss_pct_away = None
        if stop_loss_price and current_price > 0:
            if position_side == "long":
                stop_loss_pct_away = ((current_price - stop_loss_price) / current_price) * 100
            else:
                stop_loss_pct_away = ((stop_loss_price - current_price) / current_price) * 100
        
        state = self._decision_states[bot_id]
        state.exit_proximity = [e for e in state.exit_proximity if e.symbol != symbol]
        state.exit_proximity.append(ExitProximity(
            symbol=symbol,
            bot_id=bot_id,
            position_side=position_side,
            entry_price=entry_price,
            current_price=current_price,
            unrealized_pnl_pct=unrealized_pnl_pct,
            trailing_stop_price=trailing_stop_price,
            trailing_stop_pct_away=trailing_stop_pct_away,
            take_profit_price=take_profit_price,
            take_profit_pct_away=take_profit_pct_away,
            stop_loss_price=stop_loss_price,
            stop_loss_pct_away=stop_loss_pct_away,
            time_remaining_minutes=time_remaining_minutes
        ))
        state.last_updated = now
        self._save_to_state()
    
    def clear_exit_proximity(self, bot_id: str, symbol: str):
        """Clear exit proximity when position is closed"""
        if bot_id in self._decision_states:
            state = self._decision_states[bot_id]
            state.exit_proximity = [e for e in state.exit_proximity if e.symbol != symbol]
            state.last_updated = datetime.utcnow().isoformat() + "Z"
            self._save_to_state()
    
    def set_bot_enabled(self, bot_id: str, bot_type: str, enabled: bool):
        """Update whether a bot is enabled"""
        now = datetime.utcnow().isoformat() + "Z"
        
        if bot_id not in self._decision_states:
            self._decision_states[bot_id] = BotDecisionState(
                bot_id=bot_id,
                bot_type=bot_type,
                enabled=enabled,
                last_updated=now
            )
        else:
            self._decision_states[bot_id].enabled = enabled
            self._decision_states[bot_id].last_updated = now
        
        self._save_to_state()
    
    def get_all_decisions(self) -> Dict[str, Any]:
        """Get all decision states for API response"""
        result = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "bots": {}
        }
        
        for bot_id, state in self._decision_states.items():
            result["bots"][bot_id] = {
                "bot_id": state.bot_id,
                "bot_type": state.bot_type,
                "enabled": state.enabled,
                "last_updated": state.last_updated,
                "signals": [asdict(s) for s in state.signals],
                "blockers": [asdict(b) for b in state.blockers],
                "exit_proximity": [asdict(e) for e in state.exit_proximity]
            }
        
        return result
    
    def get_bot_decision(self, bot_id: str) -> Optional[Dict[str, Any]]:
        """Get decision state for a specific bot"""
        if bot_id not in self._decision_states:
            return None
        
        state = self._decision_states[bot_id]
        return {
            "bot_id": state.bot_id,
            "bot_type": state.bot_type,
            "enabled": state.enabled,
            "last_updated": state.last_updated,
            "signals": [asdict(s) for s in state.signals],
            "blockers": [asdict(b) for b in state.blockers],
            "exit_proximity": [asdict(e) for e in state.exit_proximity]
        }

    def log_decision_record(
        self,
        bot_id: str,
        symbol: str,
        loop_number: int,
        signal_inputs: Dict[str, Any],
        gating_results: Dict[str, Any],
        budget_used: float,
        final_action: str,
        reason: str
    ):
        """
        Log a complete Decision Record for debugging and audit.
        
        This creates a structured record per symbol per loop that includes:
        - signal_inputs: Raw data used for signal generation
        - gating_results: Which gates passed/failed (spread, cooldown, risk, etc.)
        - budget_used: How much of allocated budget was consumed
        - final_action: What action was taken (buy, sell, hold, blocked)
        - reason: Human-readable explanation
        
        Use this for debugging: "why did we not trade?"
        """
        run_id = get_state("run_id", "run_unknown")
        loop_id = get_state("loop_id", loop_number)
        
        record = {
            "record_type": "decision_record",
            "run_id": run_id,
            "loop_id": loop_id,
            "bot_id": bot_id,
            "symbol": symbol,
            "loop_number": loop_number,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "signal_inputs": signal_inputs,
            "gating_results": gating_results,
            "budget_used": budget_used,
            "final_action": final_action,
            "reason": reason
        }
        
        self._logger.log("decision_record", record)
        self._write_decision_record_to_file(record)
        return record

    def emit_full_decision_record(
        self,
        bot: str,
        symbol: str,
        asset_class: str,
        horizon: str,
        account: Dict[str, Any],
        market_context: Dict[str, Any],
        inputs: Dict[str, Any],
        gates: Dict[str, Any],
        risk: Dict[str, Any],
        signal: Dict[str, Any],
        plan: Dict[str, Any],
        action: Dict[str, Any],
        outcome: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Emit a full-schema compliant Decision Record per symbol per loop.
        
        This follows the decision_record.schema.json specification for
        complete debugging visibility: "why did it trade / why did it not trade?"
        
        Args:
            bot: Bot name (e.g., "MomentumBot", "CryptoBot", "OptionsBot")
            symbol: Asset symbol (e.g., "AAPL", "BTC/USD")
            asset_class: "stocks", "options", or "crypto"
            horizon: "day", "swing", or "long"
            account: Account state dict with equity, buying_power, positions_count, etc.
            market_context: Session, data freshness, regime info
            inputs: Price, ATR, Donchian, trend data used for decision
            gates: Gate check results (allowed: bool, reasons: [], checks: {})
            risk: Risk budget info (risk_per_trade, risk_dollars, position_size)
            signal: Signal direction, strength, reason
            plan: Entry/stop/exit plan
            action: Action type (NO_TRADE, ENTER, ADD, EXIT, REDUCE, etc.)
            outcome: Status and message
            
        Returns:
            The full decision record dict
        """
        run_id = get_state("run_id", "run_unknown")
        loop_id = get_state("loop_id", 0)
        
        record = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "run_id": run_id,
            "loop_id": loop_id,
            "bot": bot,
            "horizon": horizon,
            "asset_class": asset_class,
            "symbol": symbol,
            "account": account,
            "market_context": market_context,
            "inputs": inputs,
            "gates": gates,
            "risk": risk,
            "signal": signal,
            "plan": plan,
            "action": action,
            "outcome": outcome
        }
        
        self._logger.log("decision_record_full", record)
        self._write_decision_record_to_file(record)
        return record


_tracker: Optional[DecisionTracker] = None

def get_decision_tracker() -> DecisionTracker:
    """Get singleton instance of DecisionTracker"""
    global _tracker
    if _tracker is None:
        _tracker = DecisionTracker()
    return _tracker
