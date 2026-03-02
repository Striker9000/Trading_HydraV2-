"""
ExitBot service - Central position monitor and safety controller

This is the MOST IMPORTANT bot in the system. It provides:
1. Kill-switch functionality (halt trading on critical failures)
2. Position monitoring for ALL entries (manual + automated)
3. Trailing-stop management for every position
4. Automatic exit execution when stops are triggered
5. HARD STOP-LOSS ENFORCEMENT - exit if position drops X% from entry
6. TIERED TAKE-PROFIT TARGETS (TP1, TP2, TP3) with partial exits
7. DYNAMIC STOPS - ATR-scaled, ML-confidence adjusted, VIX-regime influenced

ExitBot runs every loop iteration and:
- Detects new positions from Alpaca (manual or automated)
- Registers trailing stops for positions that don't have one
- Updates trailing stops for all tracked positions
- Triggers exits when trailing stops are hit
- Enforces hard stop-losses regardless of trailing stop activation
- Executes tiered take-profits with partial position exits
- Monitors health and daily P&L limits
"""

from typing import Dict, Any, Optional, List, Set
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4
import time
import json

from ..core.logging import get_logger
from ..core.config import load_settings, load_bots_config
from ..core.state import get_state, set_state, delete_state, get_db_connection
from ..core.health import get_health_monitor
from ..core.halt import get_halt_manager
from ..core.risk import dollars_from_pct
from ..core.clock import get_market_clock
from ..core.staleness import get_data_staleness, DataType
from ..risk.trailing_stop import (
    TrailingStopManager, TrailingStopConfig, TrailingStopState,
    DynamicTrailingConfig, DYNAMIC_TRAIL_DEFAULTS,
    get_trailing_stop_manager
)
from .alpaca_client import get_alpaca_client
from .market_regime import get_current_regime
from .news_intelligence import get_news_intelligence
from .sentiment_scorer import get_sentiment_scorer, SentimentResult

# ExitBot v2 Intelligence Modules
from .trade_memory import get_trade_memory, TradeMemoryEngine, HistoricalContext
from .trade_health import (
    get_trade_health_scorer, TradeHealthScorer, HealthScore, 
    HealthPriority, PositionContext
)
from .exit_decision import (
    get_exit_decision_engine, ExitDecisionEngine, 
    ExitAction as ExitActionV2, ExitDecision, DecisionInputs
)

from ..strategy.kill_switch import StrategyKillSwitch
from ..risk.risk_integration import get_risk_integration
from ..risk.greek_limits import get_greek_risk_monitor
from ..risk.profit_sniper import get_profit_sniper, ProfitSniperConfig
from ..risk.session_protection import (
    get_session_protection, SessionProtectionConfig, SessionProtection
)


@dataclass
class PositionInfo:
    """Information about a tracked position"""
    symbol: str
    qty: float
    side: str  # "long" or "short"
    entry_price: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    asset_class: str  # "us_equity", "crypto", "option"
    position_id: str  # Unique identifier
    first_seen_ts: float  # Unix timestamp when first detected
    bot_id: str  # Which bot owns this, or "manual" if user-created


@dataclass
class ExitRecord:
    """Record of a trade exit"""
    symbol: str
    side: str
    qty: float
    entry_price: float
    exit_price: float
    pnl: float
    pnl_percent: float
    reason: str  # "trailing_stop", "take_profit", "stop_loss", "time_exit", "position_closed", "hard_stop", "tp1", "tp2", "tp3", "liquidate_winner", "breakeven_exit", "max_loss_override", "catastrophic_stop"
    bot_id: str = ""
    timestamp: str = ""


@dataclass
class TakeProfitTier:
    """Configuration for a single take-profit tier"""
    level: int                      # 1, 2, or 3
    target_pct: float               # Target profit percentage (e.g., 2.0 for 2%)
    exit_pct: float                 # Percentage of remaining position to exit (e.g., 0.33 for 33%)
    move_stop_to_entry: bool        # After hitting this TP, move stop to breakeven
    move_stop_to_prev_tp: bool      # After hitting this TP, move stop to previous TP level


@dataclass
class HardStopConfig:
    """Hard stop-loss configuration - exits regardless of trailing stop status"""
    enabled: bool = True
    stop_loss_pct: float = 5.0      # Default 5% hard stop from entry
    use_atr_scaling: bool = True    # Scale by ATR if available
    atr_multiplier: float = 2.0     # ATR multiplier for stop distance
    ml_confidence_adjust: bool = True  # Widen/tighten based on ML confidence
    vix_regime_adjust: bool = True  # Tighten in high VIX regimes


@dataclass
class ReversalSenseStopConfig:
    """
    Reversal-sense stop - exits if price drops X% from high water mark
    
    This catches positions that went up but came back down, even if they
    never hit the trailing stop activation threshold. Key difference from
    trailing stop: this triggers regardless of arming status.
    
    Example: Position goes up 1.5% (below 2% activation) then drops back.
    Trailing stop won't trigger because it was never armed.
    Reversal-sense WILL trigger if drop from high water exceeds threshold.
    """
    enabled: bool = True
    drop_from_high_pct: float = 1.5  # Exit if price drops 1.5% from high water
    min_high_water_gain_pct: float = 0.5  # Only trigger if HWM was at least 0.5% above entry
    apply_to_crypto: bool = True
    apply_to_stocks: bool = True
    apply_to_options: bool = False  # Options have different dynamics


@dataclass
class AdaptiveThresholdConfig:
    """ATR-based adaptive threshold configuration for parabolic runner"""
    enabled: bool = True
    atr_period: int = 14                # ATR lookback period
    base_atr_mult_tp1: float = 0.5      # TP1 = base + (ATR% * this multiplier)
    base_atr_mult_tp2: float = 1.0      # TP2 = base + (ATR% * this multiplier)
    base_atr_mult_tp3: float = 2.0      # TP3 = base + (ATR% * this multiplier)
    min_tp1_pct: float = 1.5            # Floor for TP1
    max_tp1_pct: float = 4.0            # Ceiling for TP1
    min_tp2_pct: float = 3.0            # Floor for TP2
    max_tp2_pct: float = 8.0            # Ceiling for TP2
    min_tp3_pct: float = 6.0            # Floor for TP3
    max_tp3_pct: float = 15.0           # Ceiling for TP3


@dataclass
class TakeProfitConfig:
    """Tiered take-profit configuration with per-bot and adaptive support"""
    enabled: bool = True
    tp1_pct: float = 2.0            # First target at 2% profit
    tp1_exit_pct: float = 0.33      # Exit 33% of position at TP1
    tp2_pct: float = 4.0            # Second target at 4% profit
    tp2_exit_pct: float = 0.50      # Exit 50% of remaining at TP2
    tp3_pct: float = 8.0            # Third target at 8% profit
    tp3_exit_pct: float = 1.0       # Exit 100% of remaining at TP3
    move_stop_after_tp1: str = "breakeven"  # "breakeven" or "none"
    move_stop_after_tp2: str = "tp1"        # "tp1" level or "none"
    use_atr_scaling: bool = True    # Scale TP levels by ATR
    atr_multiplier: float = 1.5     # ATR multiplier for TP distances
    # Parabolic Runner Mode - after TP2, disable TP3 and let trailing stop ride
    parabolic_runner_enabled: bool = True   # Enable runner mode after TP2
    runner_widen_trailing_pct: float = 50.0 # Widen trailing stop by 50% for more room
    # ATR-based adaptive thresholds - auto-adjust based on volatility
    adaptive: AdaptiveThresholdConfig = field(default_factory=AdaptiveThresholdConfig)


@dataclass
class ExitBotResult:
    """Result from ExitBot run"""
    should_continue: bool
    is_halted: bool
    halt_reason: str
    equity: float
    pnl: float
    positions_monitored: int = 0
    trailing_stops_active: int = 0
    exits_triggered: int = 0
    recent_exits: List[ExitRecord] = field(default_factory=list)


# =============================================================================
# ExitBot v2 - Elite Exit Intelligence Data Structures
# =============================================================================

class ExitAction:
    """Exit decision actions - what ExitBot commands"""
    HOLD = "HOLD"              # Let trade breathe
    TIGHTEN = "TIGHTEN"        # Tighten trailing stop
    SCALE_OUT = "SCALE_OUT"    # Partial exit (25/25/50 doctrine)
    FULL_EXIT = "FULL_EXIT"    # Complete position close


class ExitType:
    """Exit type classification - WHY we exited"""
    THESIS = "thesis"              # Entry reason no longer valid
    TIME_DECAY = "time_decay"      # Flat + theta bleeding
    PROBABILITY = "probability"    # Upside exhausted vs downside
    VOLATILITY = "volatility"      # IV crush detected
    CATASTROPHIC = "catastrophic"  # Hard stop, no debate
    TRAILING_STOP = "trailing_stop"
    TAKE_PROFIT = "take_profit"
    REVERSAL_SENSE = "reversal_sense"
    MANUAL = "manual"


@dataclass
class EntryIntent:
    """
    Entry intent registration - announced by bots when order is submitted.
    
    This creates the lifecycle anchor before the position appears in Alpaca.
    ExitBot uses this to correlate intent -> fill -> position -> exit.
    """
    bot_id: str
    symbol: str
    side: str                      # "long" or "short"
    qty: float
    entry_price: float             # Expected/limit price
    entry_ts: str                  # ISO timestamp
    signal_id: str                 # Unique signal identifier
    client_order_id: str           # Deterministic order ID
    alpaca_order_id: Optional[str] = None
    asset_class: str = "us_equity" # "us_equity", "crypto", "option"
    position_key: str = ""         # Generated by ExitBot
    regime: Optional[str] = None   # Market regime at entry
    options: Optional[Dict] = None # Options-specific context


@dataclass
class PositionSnapshot:
    """
    Point-in-time snapshot of a position - what ExitBot "saw".
    
    Logged every evaluation cycle for replay and forensics.
    """
    ts: str                        # ISO timestamp
    run_id: str                    # ExitBot loop iteration ID
    position_key: str
    bot_id: str
    symbol: str
    asset_class: str
    side: str
    qty: float
    
    # Entry info
    entry_ts: str
    entry_price: float
    signal_id: Optional[str]
    client_order_id: Optional[str]
    
    # Current mark
    current_price: float
    bid: Optional[float]
    ask: Optional[float]
    spread: Optional[float]
    
    # P&L tracking
    unrealized_pnl_usd: float
    unrealized_pnl_pct: float
    mfe_pct: float                 # Max favorable excursion
    mae_pct: float                 # Max adverse excursion
    
    # Market context
    session: str                   # "RTH", "PRE", "POST", "CRYPTO"
    minutes_since_open: Optional[float]
    regime: Optional[str]
    vix_level: Optional[float]
    data_age_sec: float
    
    # Features for intelligence
    vwap_posture: Optional[str]    # "ABOVE_VWAP", "BELOW_VWAP", "AT_VWAP"
    atr_pct: Optional[float]
    
    # Options-specific (null for equity/crypto)
    options: Optional[Dict] = None


@dataclass
class ExitDecisionRecord:
    """
    Exit decision record - why ExitBot chose HOLD/TIGHTEN/SCALE/EXIT.
    
    This is the "judge's ruling" - logged for every decision made.
    """
    ts: str
    run_id: str
    position_key: str
    
    # Decision
    action: str                    # ExitAction value
    confidence: float              # 0.0 to 1.0
    priority: str                  # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    health_score: int              # 0-100
    thesis_alive: bool             # Is entry thesis still valid?
    
    # Triggers that fired
    triggers: List[Dict]           # [{"type": "TP1_HIT", "value": 2.0, "units": "pct"}]
    
    # Risk state
    hard_stop_pct: float
    trailing_stop_pct: Optional[float]
    time_stop_sec: Optional[float]
    
    # Notes
    reason: str


@dataclass 
class ExitActionRecord:
    """
    Exit action record - what order ExitBot actually sent.
    
    Ties decision to execution for blame assignment if fills go sideways.
    """
    ts: str
    run_id: str
    position_key: str
    
    # Order details
    action: str                    # "BUY" or "SELL"
    qty: float
    order_type: str                # "market", "limit"
    limit_price: Optional[float]
    time_in_force: str
    
    # Context
    exit_type: str                 # ExitType value
    signal_id: str                 # Exit signal identifier


@dataclass
class ExitOutcomeRecord:
    """
    Exit outcome record - what actually happened.
    
    The truth record - fills, slippage, and realized P&L.
    """
    ts: str
    run_id: str
    position_key: str
    
    # Fill info
    filled_qty: float
    avg_fill_price: float
    slippage_usd: float
    alpaca_order_id: Optional[str]
    
    # Realized
    realized_pnl_usd: float
    realized_pnl_pct: float
    
    # Result
    result: str                    # "FILLED", "PARTIAL", "REJECTED", "CANCELLED"


class ExitBot:
    """
    Central position monitor and safety controller
    
    Monitors ALL positions (manual + automated) and manages:
    - Health checks and kill conditions
    - Daily P&L limits
    - Trailing stops for every position
    - Automatic exit execution
    - HARD STOP-LOSS enforcement (exit if position drops X% from entry)
    - TIERED TAKE-PROFIT targets (TP1, TP2, TP3 with partial exits)
    - DYNAMIC adjustments (ATR, ML confidence, VIX regime)
    """
    
    def __init__(self):
        # Initialize core dependencies
        self._logger = get_logger()
        self._health = get_health_monitor()
        self._halt = get_halt_manager()
        self._alpaca = get_alpaca_client()
        self._trailing_stop_mgr = get_trailing_stop_manager()
        
        # Strategy system kill-switch for per-strategy drawdown tracking
        self._strategy_kill_switch = StrategyKillSwitch()
        
        self._profit_sniper = None
        self._session_protection: Optional[SessionProtection] = None
        
        # Track known positions to detect new entries
        self._known_positions: Set[str] = set()
        self._position_first_seen: Dict[str, float] = {}
        self._load_position_first_seen()
        
        # Track recent exits for display (cleared each run, persisted in state)
        self._recent_exits: List[ExitRecord] = []
        
        # Track which take-profit tiers have been hit per position
        # Key: position_id, Value: set of tiers hit (1, 2, 3)
        self._tp_tiers_hit: Dict[str, Set[int]] = {}
        
        # =====================================================================
        # ExitBot v2 - Elite Exit Intelligence State
        # =====================================================================
        
        # Run counter for generating unique run_ids within session
        self._run_counter: int = 0
        
        # Pending entry intents - orders submitted but not yet filled
        # Key: client_order_id, Value: EntryIntent
        self._pending_intents: Dict[str, EntryIntent] = {}
        
        # Active positions with full context - Key: position_key
        self._active_positions: Dict[str, Dict[str, Any]] = {}
        
        # MFE/MAE tracking per position - Key: position_key
        # Value: {"mfe_pct": float, "mae_pct": float, "high_water": float, "low_water": float}
        self._excursion_tracker: Dict[str, Dict[str, float]] = {}
        
        # Health status - fail-closed safety
        self._is_healthy: bool = True
        self._last_health_check: float = 0.0
        
        # Load known positions from state on startup
        known_list = get_state("exitbot.known_positions", [])
        if isinstance(known_list, list):
            self._known_positions = set(known_list)
        
        # Load TP tiers hit from state
        tp_state = get_state("exitbot.tp_tiers_hit", {})
        if isinstance(tp_state, dict):
            self._tp_tiers_hit = {k: set(v) for k, v in tp_state.items()}
        
        # Load excursion tracker from state
        excursion_state = get_state("exitbot.excursion_tracker", {})
        if isinstance(excursion_state, dict):
            self._excursion_tracker = excursion_state
        
        # Load pending intents from state (survives restarts)
        intents_state = get_state("exitbot.pending_intents", {})
        if isinstance(intents_state, dict):
            for coid, intent_data in intents_state.items():
                self._pending_intents[coid] = EntryIntent(**intent_data)
        
        # =====================================================================
        # PRE-STAGED EXIT ORDERS - Broker-side SL/TP protection
        # Tracks OCO order IDs so we can modify/cancel them
        # Key: position_id, Value: {stop_order_id, tp_order_id, parent_order_id, 
        #   stop_price, tp_price, last_update_ts, symbol, qty, side}
        # =====================================================================
        self._staged_orders: Dict[str, Dict[str, Any]] = {}
        
        # Load staged orders from state (survives restarts)
        staged_state = get_state("exitbot.staged_orders", {})
        if isinstance(staged_state, dict):
            self._staged_orders = staged_state
        
        # Load recent exits from state (last 10)
        self._load_recent_exits()
        
        self._close_cooldown: Dict[str, float] = {}
        self._close_cooldown_seconds = 60
        
        self._logger.log("exitbot_v2_initialized", {
            "known_positions": len(self._known_positions),
            "pending_intents": len(self._pending_intents),
            "excursion_trackers": len(self._excursion_tracker),
            "staged_orders": len(self._staged_orders)
        })
    
    def run(self, equity: float, day_start_equity: float) -> ExitBotResult:
        """
        Main ExitBot loop - called every trading iteration
        
        Args:
            equity: Current account equity
            day_start_equity: Equity at start of trading day
            
        Returns:
            ExitBotResult with status and metrics
        """
        self._logger.log("exitbot_start", {
            "equity": equity,
            "day_start": day_start_equity
        })
        
        # ------------------------------------------------------------------
        # Step 1: Load configuration
        # ------------------------------------------------------------------
        try:
            config = load_bots_config()
            settings = load_settings()
        except Exception as e:
            # Config load failure is critical - halt immediately
            self._logger.error(f"ExitBot config load failed: {e}")
            return ExitBotResult(
                should_continue=False,
                is_halted=True,
                halt_reason=f"Config load failed: {e}",
                equity=equity,
                pnl=0
            )
        
        exitbot_config = config.get("exitbot", {})
        
        # ------------------------------------------------------------------
        # Step 1b: Initialize SessionProtection from settings
        # ------------------------------------------------------------------
        try:
            sp_config_dict = settings.get("session_protection", {})
            sp_config = SessionProtectionConfig.from_yaml(sp_config_dict)
            self._session_protection = get_session_protection(sp_config)
            self._session_protection.update_config(sp_config)
            tier_labels = [f"${t.threshold_usd:.0f}/{t.retain_pct}%/{t.label}" for t in sp_config.lock_tiers]
            self._logger.log("exitbot_session_protection_loaded", {
                "enabled": sp_config.enabled,
                "tighten_target": sp_config.tighten_target_usd,
                "lock_tiers": tier_labels,
                "freeroll_enabled": sp_config.freeroll_enabled,
                "freeroll_min_score": sp_config.freeroll_min_quality_score,
                "freeroll_min_house_money": sp_config.freeroll_min_house_money_usd,
                "spam_throttle_min": sp_config.spam_throttle_minutes,
            })
        except Exception as sp_err:
            self._logger.error(f"SessionProtection init failed (fail-open): {sp_err}")
        
        # ------------------------------------------------------------------
        # Step 2: Check if ExitBot is disabled
        # ------------------------------------------------------------------
        if not exitbot_config.get("enabled", True):
            self._logger.log("exitbot_disabled", {})
            pnl = equity - day_start_equity
            return ExitBotResult(
                should_continue=True,
                is_halted=False,
                halt_reason="",
                equity=equity,
                pnl=pnl
            )
        
        # ------------------------------------------------------------------
        # Step 3: Check if already halted
        # ------------------------------------------------------------------
        if self._halt.is_halted():
            status = self._halt.get_status()
            self._logger.log("exitbot_already_halted", {"reason": status.reason})
            return ExitBotResult(
                should_continue=False,
                is_halted=True,
                halt_reason=status.reason,
                equity=equity,
                pnl=0
            )
        
        # ------------------------------------------------------------------
        # Step 4: Check health status (API failures, etc.)
        # ------------------------------------------------------------------
        health = self._health.get_snapshot()
        kill_conditions = exitbot_config.get("kill_conditions", {})
        cooloff = exitbot_config.get("cooloff_minutes", 60)
        
        if not health.ok and kill_conditions.get("api_failure_halt", True):
            reason = f"HEALTH_FAIL: {health.reason}"
            self._halt.set_halt(reason, cooloff)
            self._logger.log("exitbot_halt", {"reason": reason})
            
            # Attempt to flatten all positions on health failure
            if self._alpaca.has_credentials():
                result = self._alpaca.flatten()
                if not result["success"]:
                    reason = f"{reason} + FLATTEN_FAILED: {result['error']}"
                    self._logger.log("exitbot_flatten_failed", {"error": result["error"]})
            else:
                self._logger.log("exitbot_flatten_skipped", {"reason": "no_credentials"})
            
            return ExitBotResult(
                should_continue=False,
                is_halted=True,
                halt_reason=reason,
                equity=equity,
                pnl=0
            )
        
        # ------------------------------------------------------------------
        # Step 5: Check daily P&L limit
        # ------------------------------------------------------------------
        pnl = equity - day_start_equity
        risk_config = settings.get("risk", {})
        max_loss_pct = risk_config.get("global_max_daily_loss_pct", 1.0)
        max_loss = dollars_from_pct(day_start_equity, max_loss_pct)
        
        if pnl <= -max_loss and kill_conditions.get("max_daily_loss_halt", True):
            reason = f"MAX_DAILY_LOSS: pnl={pnl:.2f} <= -{max_loss:.2f}"
            self._halt.set_halt(reason, cooloff)
            self._logger.log("exitbot_halt", {
                "reason": reason,
                "pnl": pnl,
                "max_loss": max_loss
            })
            
            # Flatten all positions on max loss breach
            if self._alpaca.has_credentials():
                result = self._alpaca.flatten()
                if not result["success"]:
                    reason = f"{reason} + FLATTEN_FAILED: {result['error']}"
                    self._logger.error("Flatten failed", error=result["error"])
            else:
                self._logger.error("Cannot flatten - no credentials with positions at risk")
                reason = f"{reason} + CANNOT_FLATTEN: no credentials"
            
            return ExitBotResult(
                should_continue=False,
                is_halted=True,
                halt_reason=reason,
                equity=equity,
                pnl=pnl
            )
        
        # ------------------------------------------------------------------
        # Step 6: Check market regime for tighten_stops signal
        # ------------------------------------------------------------------
        tighten_stops = False
        try:
            regime = get_current_regime()
            tighten_stops = regime.tighten_stops
            if tighten_stops:
                self._logger.log("exitbot_tighten_stops_active", {
                    "vix": regime.vix,
                    "vvix_warning": regime.vvix_warning,
                    "rate_shock_warning": regime.rate_shock_warning
                })
        except Exception as regime_err:
            self._logger.error(f"Regime check in ExitBot failed: {regime_err}")
        
        # ------------------------------------------------------------------
        # Step 7: Monitor positions and manage trailing stops
        # ------------------------------------------------------------------
        positions_monitored = 0
        trailing_stops_active = 0
        exits_triggered = 0
        
        # Only monitor positions if we have Alpaca credentials
        if self._alpaca.has_credentials():
            try:
                monitor_result = self._monitor_positions(
                    exitbot_config, config, tighten_stops=tighten_stops
                )
                positions_monitored = monitor_result.get("positions_monitored", 0)
                trailing_stops_active = monitor_result.get("trailing_stops_active", 0)
                exits_triggered = monitor_result.get("exits_triggered", 0)
            except Exception as e:
                self._logger.error(f"Position monitoring failed: {e}")
                # Continue trading even if monitoring fails - fail-open for monitoring
        
        # ------------------------------------------------------------------
        # Step 8: Log Greek exposure for monitoring
        # ------------------------------------------------------------------
        try:
            greek_monitor = get_greek_risk_monitor()
            greek_summary = greek_monitor.get_exposure_summary()
            if greek_summary.get("status") != "no_data":
                self._logger.log("exitbot_greek_exposure", greek_summary)
        except Exception as e:
            self._logger.error(f"Greek exposure log failed: {e}")
        
        # ------------------------------------------------------------------
        # Step 9: Return success result
        # ------------------------------------------------------------------
        self._logger.log("exitbot_ok", {
            "equity": equity,
            "pnl": round(pnl, 2),
            "max_loss": round(max_loss, 2),
            "positions_monitored": positions_monitored,
            "trailing_stops_active": trailing_stops_active,
            "exits_triggered": exits_triggered
        })
        
        return ExitBotResult(
            should_continue=True,
            is_halted=False,
            halt_reason="",
            equity=equity,
            pnl=pnl,
            positions_monitored=positions_monitored,
            trailing_stops_active=trailing_stops_active,
            exits_triggered=exits_triggered,
            recent_exits=self._recent_exits[:5]  # Return last 5 exits
        )
    
    # =========================================================================
    # ExitBot v2 - Elite Exit Intelligence Methods
    # =========================================================================
    
    def generate_run_id(self) -> str:
        """
        Generate unique run_id for this ExitBot loop iteration.
        
        Format: XB_{date}_{time}_{counter:04d}
        Example: XB_20260129_170112_0012
        """
        self._run_counter += 1
        now = datetime.utcnow()
        return f"XB_{now.strftime('%Y%m%d_%H%M%S')}_{self._run_counter:04d}"
    
    def build_position_key(
        self,
        account_id: str,
        symbol: str,
        side: str,
        entry_ts: str,
        asset_class: str = "us_equity",
        expiry: Optional[str] = None,
        strike: Optional[float] = None,
        right: Optional[str] = None
    ) -> str:
        """
        Build canonical position_key for lifecycle tracking.
        
        Formats:
        - Equities/Crypto: "{account}:{symbol}:{side}:{entry_ts}"
        - Options: "{account}:{underlying}:{side}:{expiry}:{strike}:{right}:{entry_ts}"
        
        Args:
            account_id: Account identifier (or "paper" for paper trading)
            symbol: Ticker symbol or crypto pair
            side: "long" or "short"
            entry_ts: ISO timestamp of entry
            asset_class: "us_equity", "crypto", or "option"
            expiry: Option expiration date (options only)
            strike: Option strike price (options only)
            right: "call" or "put" (options only)
            
        Returns:
            Canonical position_key string
        """
        if asset_class in ("option", "us_option") and expiry and strike and right:
            return f"{account_id}:{symbol}:{side}:{expiry}:{strike}:{right}:{entry_ts}"
        else:
            return f"{account_id}:{symbol}:{side}:{entry_ts}"
    
    def is_healthy(self) -> bool:
        """
        Check if ExitBot is healthy and can accept new entries.
        
        Fail-closed enforcement: If ExitBot is unhealthy, bots should NOT
        open new positions. This prevents orphaned trades that can't be managed.
        
        Returns:
            True if ExitBot is operational, False otherwise
        """
        # Check basic health
        if self._halt.is_halted():
            return False
        
        health = self._health.get_snapshot()
        if not health.ok:
            return False
        
        self._is_healthy = True
        self._last_health_check = time.time()
        return True
    
    def register_entry_intent(
        self,
        bot_id: str,
        symbol: str,
        side: str,
        qty: float,
        entry_price: float,
        signal_id: str,
        client_order_id: str,
        alpaca_order_id: Optional[str] = None,
        asset_class: str = "us_equity",
        options: Optional[Dict] = None
    ) -> str:
        """
        Register entry intent when a bot submits an order.
        
        This creates the lifecycle anchor BEFORE the position appears in Alpaca.
        ExitBot uses this to correlate: intent -> fill -> position -> exit
        
        Args:
            bot_id: Owning bot identifier
            symbol: Ticker symbol
            side: "long" or "short"
            qty: Order quantity
            entry_price: Expected/limit price
            signal_id: Unique signal identifier from bot
            client_order_id: Deterministic order ID
            alpaca_order_id: Alpaca order ID (if available)
            asset_class: "us_equity", "crypto", or "option"
            options: Options-specific context (underlying, expiry, strike, right, greeks)
            
        Returns:
            position_key for tracking
        """
        entry_ts = datetime.utcnow().isoformat() + "Z"
        
        # Get current regime for context
        regime = None
        try:
            from .market_regime import get_current_regime
            regime_data = get_current_regime()
            if regime_data and regime_data.volatility_regime:
                regime = regime_data.volatility_regime.name
        except Exception:
            pass
        
        # Build position key
        account_id = "paper" if self._alpaca.is_paper else "live"
        
        if asset_class in ("option", "us_option") and options:
            position_key = self.build_position_key(
                account_id=account_id,
                symbol=options.get("underlying", symbol),
                side=side,
                entry_ts=entry_ts,
                asset_class=asset_class,
                expiry=options.get("expiry"),
                strike=options.get("strike"),
                right=options.get("right")
            )
        else:
            position_key = self.build_position_key(
                account_id=account_id,
                symbol=symbol,
                side=side,
                entry_ts=entry_ts,
                asset_class=asset_class
            )
        
        # Create entry intent
        intent = EntryIntent(
            bot_id=bot_id,
            symbol=symbol,
            side=side,
            qty=qty,
            entry_price=entry_price,
            entry_ts=entry_ts,
            signal_id=signal_id,
            client_order_id=client_order_id,
            alpaca_order_id=alpaca_order_id,
            asset_class=asset_class,
            position_key=position_key,
            regime=regime,
            options=options
        )
        
        # Store in pending intents
        self._pending_intents[client_order_id] = intent
        self._save_pending_intents()
        
        # Emit entry intent event to JSONL
        self._emit_event("exitbot.entry_intent", {
            "position_key": position_key,
            "bot_id": bot_id,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "entry_price": entry_price,
            "signal_id": signal_id,
            "client_order_id": client_order_id,
            "alpaca_order_id": alpaca_order_id,
            "asset_class": asset_class,
            "regime": regime,
            "options": options
        })
        
        # Insert stub row in exit_trades table
        self._insert_exit_trade_stub(intent)
        
        self._logger.log("exitbot_entry_intent_registered", {
            "position_key": position_key,
            "bot_id": bot_id,
            "symbol": symbol,
            "side": side,
            "signal_id": signal_id
        })
        
        return position_key
    
    def _emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Emit structured JSONL event for audit logging.
        
        Events are logged to app.jsonl with standardized format.
        
        Args:
            event_type: Event type (e.g., "exitbot.position_snapshot")
            data: Event payload
        """
        event = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "event": event_type,
            **data
        }
        self._logger.log(event_type, event)
    
    def _emit_position_snapshot(self, snapshot: PositionSnapshot) -> None:
        """Emit position snapshot event to JSONL."""
        self._emit_event("exitbot.position_snapshot", {
            "run_id": snapshot.run_id,
            "position_key": snapshot.position_key,
            "bot_id": snapshot.bot_id,
            "symbol": snapshot.symbol,
            "asset_class": snapshot.asset_class,
            "side": snapshot.side,
            "qty": snapshot.qty,
            "entry": {
                "entry_ts": snapshot.entry_ts,
                "entry_price": snapshot.entry_price,
                "signal_id": snapshot.signal_id,
                "client_order_id": snapshot.client_order_id
            },
            "mark": {
                "price": snapshot.current_price,
                "bid": snapshot.bid,
                "ask": snapshot.ask,
                "spread": snapshot.spread
            },
            "pnl": {
                "unrealized_usd": snapshot.unrealized_pnl_usd,
                "unrealized_pct": snapshot.unrealized_pnl_pct,
                "mfe_pct": snapshot.mfe_pct,
                "mae_pct": snapshot.mae_pct
            },
            "market_context": {
                "session": snapshot.session,
                "minutes_since_open": snapshot.minutes_since_open,
                "regime": snapshot.regime,
                "vix_level": snapshot.vix_level,
                "data_age_sec": snapshot.data_age_sec
            },
            "features": {
                "vwap_posture": snapshot.vwap_posture,
                "atr_pct": snapshot.atr_pct
            },
            "options": snapshot.options
        })
    
    def _emit_decision(self, decision: ExitDecisionRecord) -> None:
        """Emit exit decision event to JSONL."""
        self._emit_event("exitbot.decision", {
            "run_id": decision.run_id,
            "position_key": decision.position_key,
            "decision": {
                "action": decision.action,
                "confidence": decision.confidence,
                "priority": decision.priority,
                "health_score": decision.health_score,
                "thesis_alive": decision.thesis_alive
            },
            "triggers": decision.triggers,
            "risk": {
                "hard_stop_pct": decision.hard_stop_pct,
                "trailing_stop_pct": decision.trailing_stop_pct,
                "time_stop_sec": decision.time_stop_sec
            },
            "notes": decision.reason
        })
        
        # Also insert into SQLite for fast querying
        self._insert_exit_decision(decision)
    
    def _emit_action(self, action: ExitActionRecord) -> None:
        """Emit exit action event to JSONL."""
        self._emit_event("exitbot.action", {
            "run_id": action.run_id,
            "position_key": action.position_key,
            "order": {
                "action": action.action,
                "qty": action.qty,
                "order_type": action.order_type,
                "limit_price": action.limit_price,
                "time_in_force": action.time_in_force
            },
            "reason": action.exit_type,
            "signal_id": action.signal_id
        })
    
    def _emit_outcome(self, outcome: ExitOutcomeRecord) -> None:
        """Emit exit outcome event to JSONL."""
        self._emit_event("exitbot.outcome", {
            "run_id": outcome.run_id,
            "position_key": outcome.position_key,
            "fill": {
                "filled_qty": outcome.filled_qty,
                "avg_fill_price": outcome.avg_fill_price,
                "slippage_usd": outcome.slippage_usd,
                "alpaca_order_id": outcome.alpaca_order_id
            },
            "realized": {
                "pnl_usd": outcome.realized_pnl_usd,
                "pnl_pct": outcome.realized_pnl_pct
            },
            "result": outcome.result
        })
        
        # Update exit_trades row with final outcome
        self._update_exit_trade_outcome(outcome)
    
    def _insert_exit_trade_stub(self, intent: EntryIntent) -> None:
        """Insert stub row into exit_trades table when entry intent is registered."""
        try:
            conn = get_db_connection()
            now = datetime.utcnow().isoformat() + "Z"
            
            conn.execute("""
                INSERT OR REPLACE INTO exit_trades (
                    position_key, bot_id, symbol, asset_class, side,
                    entry_ts, entry_price, entry_signal_id, entry_client_order_id,
                    entry_alpaca_order_id, qty, regime_at_entry, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                intent.position_key,
                intent.bot_id,
                intent.symbol,
                intent.asset_class,
                intent.side,
                intent.entry_ts,
                intent.entry_price,
                intent.signal_id,
                intent.client_order_id,
                intent.alpaca_order_id,
                intent.qty,
                intent.regime,
                now,
                now
            ))
            conn.commit()
            
            # If options, also insert into exit_options_context
            if intent.asset_class in ("option", "us_option") and intent.options:
                self._insert_options_context(intent.position_key, intent.options, "entry")
                
        except Exception as e:
            self._logger.error(f"Failed to insert exit_trade stub: {e}")
    
    def _insert_options_context(
        self, 
        position_key: str, 
        options: Dict, 
        phase: str
    ) -> None:
        """Insert or update options context for a position."""
        try:
            conn = get_db_connection()
            
            if phase == "entry":
                conn.execute("""
                    INSERT OR REPLACE INTO exit_options_context (
                        position_key, underlying, expiry, strike, right, multiplier,
                        iv_entry, iv_rank_entry, delta_entry, gamma_entry, 
                        theta_entry, vega_entry, dte_at_entry
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    position_key,
                    options.get("underlying"),
                    options.get("expiry"),
                    options.get("strike"),
                    options.get("right"),
                    options.get("multiplier", 100),
                    options.get("greeks", {}).get("iv"),
                    options.get("iv_rank"),
                    options.get("greeks", {}).get("delta"),
                    options.get("greeks", {}).get("gamma"),
                    options.get("greeks", {}).get("theta"),
                    options.get("greeks", {}).get("vega"),
                    options.get("dte")
                ))
            else:  # exit
                conn.execute("""
                    UPDATE exit_options_context SET
                        iv_exit = ?, iv_rank_exit = ?,
                        delta_exit = ?, gamma_exit = ?,
                        theta_exit = ?, vega_exit = ?,
                        dte_at_exit = ?
                    WHERE position_key = ?
                """, (
                    options.get("greeks", {}).get("iv"),
                    options.get("iv_rank"),
                    options.get("greeks", {}).get("delta"),
                    options.get("greeks", {}).get("gamma"),
                    options.get("greeks", {}).get("theta"),
                    options.get("greeks", {}).get("vega"),
                    options.get("dte"),
                    position_key
                ))
            
            conn.commit()
        except Exception as e:
            self._logger.error(f"Failed to insert/update options context: {e}")
    
    def _insert_exit_decision(self, decision: ExitDecisionRecord) -> None:
        """Insert exit decision into SQLite for forensic analysis."""
        try:
            conn = get_db_connection()
            
            conn.execute("""
                INSERT INTO exit_decisions (
                    ts, run_id, position_key, action, health_score, confidence,
                    reason, trailing_stop_pct, hard_stop_pct, triggers_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                decision.ts,
                decision.run_id,
                decision.position_key,
                decision.action,
                decision.health_score,
                decision.confidence,
                decision.reason,
                decision.trailing_stop_pct,
                decision.hard_stop_pct,
                json.dumps(decision.triggers)
            ))
            conn.commit()
        except Exception as e:
            self._logger.error(f"Failed to insert exit decision: {e}")
    
    def _update_exit_trade_outcome(self, outcome: ExitOutcomeRecord) -> None:
        """Update exit_trades row with final exit outcome."""
        try:
            conn = get_db_connection()
            now = datetime.utcnow().isoformat() + "Z"
            
            # Get excursion data
            excursion = self._excursion_tracker.get(outcome.position_key, {})
            
            conn.execute("""
                UPDATE exit_trades SET
                    exit_ts = ?,
                    exit_price = ?,
                    exit_reason = ?,
                    realized_pnl_usd = ?,
                    realized_pnl_pct = ?,
                    mfe_pct = ?,
                    mae_pct = ?,
                    updated_at = ?
                WHERE position_key = ?
            """, (
                outcome.ts,
                outcome.avg_fill_price,
                outcome.result,
                outcome.realized_pnl_usd,
                outcome.realized_pnl_pct,
                excursion.get("mfe_pct", 0.0),
                excursion.get("mae_pct", 0.0),
                now,
                outcome.position_key
            ))
            conn.commit()
            
            # Clean up excursion tracker
            if outcome.position_key in self._excursion_tracker:
                del self._excursion_tracker[outcome.position_key]
                self._save_excursion_tracker()
                
        except Exception as e:
            self._logger.error(f"Failed to update exit trade outcome: {e}")
    
    def update_excursion(
        self, 
        position_key: str, 
        current_pnl_pct: float,
        current_price: float
    ) -> Dict[str, float]:
        """
        Update MFE/MAE excursion tracking for a position.
        
        Called on every position evaluation to track max favorable
        and max adverse excursions.
        
        Args:
            position_key: Position identifier
            current_pnl_pct: Current unrealized P&L percentage
            current_price: Current price
            
        Returns:
            Dict with mfe_pct, mae_pct, high_water, low_water
        """
        if position_key not in self._excursion_tracker:
            self._excursion_tracker[position_key] = {
                "mfe_pct": max(0.0, current_pnl_pct),
                "mae_pct": min(0.0, current_pnl_pct),
                "high_water": current_price,
                "low_water": current_price
            }
        else:
            tracker = self._excursion_tracker[position_key]
            tracker["mfe_pct"] = max(tracker["mfe_pct"], current_pnl_pct)
            tracker["mae_pct"] = min(tracker["mae_pct"], current_pnl_pct)
            tracker["high_water"] = max(tracker["high_water"], current_price)
            tracker["low_water"] = min(tracker["low_water"], current_price)
        
        return self._excursion_tracker[position_key]
    
    # =========================================================================
    # V2 Intelligence Integration Methods
    # =========================================================================
    
    def _run_v2_intelligence(
        self, 
        position: PositionInfo, 
        pnl_pct: float,
        exitbot_config: Dict
    ) -> Optional[ExitDecision]:
        """
        Run v2 intelligence stack on a position.
        
        This is the core intelligence integration that:
        1. Builds PositionContext from position data
        2. Calls TradeHealthScorer for live health scoring
        3. Fetches HistoricalContext from TradeMemoryEngine
        4. Calls ExitDecisionEngine.decide() for unified decision
        
        Args:
            position: Current position info
            pnl_pct: Current P&L percentage
            exitbot_config: ExitBot configuration
            
        Returns:
            ExitDecision or None if intelligence unavailable
        """
        try:
            # Get intelligence services
            memory = get_trade_memory()
            health_scorer = get_trade_health_scorer()
            decision_engine = get_exit_decision_engine()
            
            # Get excursion data for MFE/MAE
            excursion = self._excursion_tracker.get(position.position_id, {})
            mfe_pct = excursion.get("mfe_pct", max(0.0, pnl_pct))
            mae_pct = excursion.get("mae_pct", min(0.0, pnl_pct))
            
            # Get VWAP if available - use compute_vwap_level for actual VWAP value
            vwap = None
            try:
                from ..indicators.vwap_posture import get_vwap_posture_manager
                vwap_mgr = get_vwap_posture_manager(position.symbol)
                # _get_current_posture returns VWAPPosture enum, not VWAPLevel with vwap value
                # VWAP value would need bar data; skip for now if not critical
            except Exception:
                pass
            
            # Build PositionContext
            now = datetime.utcnow()
            
            # Handle timestamp parsing safely (handles both float and ISO string with Z)
            entry_ts = now
            if position.first_seen_ts:
                if isinstance(position.first_seen_ts, float):
                    entry_ts = datetime.fromtimestamp(position.first_seen_ts)
                elif isinstance(position.first_seen_ts, str):
                    try:
                        # Handle ISO strings with Z suffix
                        ts_str = position.first_seen_ts.replace("Z", "+00:00")
                        entry_ts = datetime.fromisoformat(ts_str)
                    except Exception:
                        entry_ts = now
            
            pos_ctx = PositionContext(
                position_key=position.position_id,
                symbol=position.symbol,
                side=position.side,
                asset_class=position.asset_class,
                entry_price=position.entry_price,
                current_price=position.current_price,
                vwap=vwap,
                unrealized_pnl_pct=pnl_pct,
                mfe_pct=mfe_pct,
                mae_pct=mae_pct,
                stop_price=None,  # Get from trailing stop state if available
                target_price=None,
                volume=None,
                avg_volume=None,
                atr_pct=None,
                entry_time=entry_ts,
                current_time=now,
                max_hold_minutes=exitbot_config.get("v2_max_hold_minutes"),
                delta=None,
                delta_at_entry=None,
                theta=None,
                theta_at_entry=None,
                iv=None,
                iv_at_entry=None,
                dte=None
            )
            
            # Calculate health score
            health = health_scorer.score_position(pos_ctx)
            
            # Get historical context
            regime = None
            try:
                from .market_regime import get_current_regime
                regime_data = get_current_regime()
                if regime_data and regime_data.volatility_regime:
                    regime = regime_data.volatility_regime.name
            except Exception:
                pass
            
            historical = memory.get_historical_context(
                symbol=position.symbol,
                strategy=position.bot_id,
                regime=regime
            )
            
            # Get current stop/target from trailing stop state
            ts_state = self._trailing_stop_mgr.load_state(
                bot_id=position.bot_id,
                position_id=position.position_id,
                symbol=position.symbol,
                asset_class=position.asset_class
            )
            
            current_stop = None
            trailing_armed = False
            trailing_price = None
            
            if ts_state:
                current_stop = ts_state.stop_price
                trailing_armed = ts_state.armed
                trailing_price = ts_state.stop_price if ts_state.armed else None
            
            # Build decision inputs
            inputs = DecisionInputs(
                position_key=position.position_id,
                position_context=pos_ctx,
                health_score=health,
                historical_context=historical,
                current_price=position.current_price,
                bid=None,
                ask=None,
                spread_pct=None,
                current_stop=current_stop,
                current_target=None,
                trailing_stop_armed=trailing_armed,
                trailing_stop_price=trailing_price,
                regime=regime,
                vix=None,
                dte=None,
                delta=None,
                theta=None,
                iv=None
            )
            
            # Get decision from engine
            decision = decision_engine.decide(inputs)
            
            # Log the decision
            self._logger.log("exitbot_v2_decision", {
                "symbol": position.symbol,
                "action": decision.action.value,
                "confidence": round(decision.confidence, 2),
                "health_score": decision.health_score,
                "primary_reason": decision.primary_reason.value,
                "urgency": decision.urgency
            })
            
            return decision
            
        except Exception as e:
            self._logger.error(f"V2 intelligence error: {e}")
            return None
    
    def _execute_v2_decision(
        self, 
        position: PositionInfo, 
        decision: ExitDecision,
        pnl_pct: float
    ) -> Dict[str, Any]:
        """
        Execute a v2 intelligence decision.
        
        Maps ExitDecision actions to actual exit/stop/scale-out commands.
        
        Args:
            position: Current position info
            decision: ExitDecision from ExitDecisionEngine
            pnl_pct: Current P&L percentage
            
        Returns:
            Dict with exit_triggered, full_exit flags
        """
        result = {"exit_triggered": False, "full_exit": False}
        
        try:
            if decision.action == ExitActionV2.HOLD:
                return result  # No action needed
            
            elif decision.action == ExitActionV2.TIGHTEN:
                # Tighten stop-loss
                if decision.new_stop_price:
                    ts_state = self._trailing_stop_mgr.load_state(
                        bot_id=position.bot_id,
                        position_id=position.position_id,
                        symbol=position.symbol,
                        asset_class=position.asset_class
                    )
                    if ts_state:
                        # Only tighten, never loosen
                        if position.side == "long":
                            if decision.new_stop_price > (ts_state.stop_price or 0):
                                ts_state.stop_price = decision.new_stop_price
                                ts_state.armed = True
                                self._trailing_stop_mgr.persist_state(
                                    bot_id=position.bot_id,
                                    position_id=position.position_id,
                                    symbol=position.symbol,
                                    asset_class=position.asset_class,
                                    state=ts_state
                                )
                                self._logger.log("exitbot_v2_tighten", {
                                    "symbol": position.symbol,
                                    "new_stop": decision.new_stop_price,
                                    "reason": decision.primary_reason.value
                                })
                        else:
                            if decision.new_stop_price < (ts_state.stop_price or float('inf')):
                                ts_state.stop_price = decision.new_stop_price
                                ts_state.armed = True
                                self._trailing_stop_mgr.persist_state(
                                    bot_id=position.bot_id,
                                    position_id=position.position_id,
                                    symbol=position.symbol,
                                    asset_class=position.asset_class,
                                    state=ts_state
                                )
                                self._logger.log("exitbot_v2_tighten", {
                                    "symbol": position.symbol,
                                    "new_stop": decision.new_stop_price,
                                    "reason": decision.primary_reason.value
                                })
                return result
            
            elif decision.action in (ExitActionV2.SCALE_OUT_25, ExitActionV2.SCALE_OUT_50):
                # Partial exit with dust protection and cooldown
                exit_pct = 0.25 if decision.action == ExitActionV2.SCALE_OUT_25 else 0.50
                qty_to_exit = position.qty * exit_pct

                # --- SCALE-OUT COOLDOWN (checked first to prevent all spam) ---
                # Minimum 60 seconds between scale-outs per position_id
                if not hasattr(self, '_last_scale_out_times'):
                    self._last_scale_out_times = {}
                cooldown_key = getattr(position, 'position_id', f"{position.symbol}_{position.side}")
                last_scale_time = self._last_scale_out_times.get(cooldown_key)
                if last_scale_time and (time.time() - last_scale_time) < 60:
                    self._logger.log("exitbot_v2_scale_cooldown", {
                        "symbol": position.symbol,
                        "seconds_since_last": round(time.time() - last_scale_time, 1),
                        "cooldown_seconds": 60
                    })
                    return result

                is_crypto = getattr(position, 'asset_class', '') == "crypto" or "USD" in position.symbol
                current_price = getattr(position, 'current_price', None) or 0

                if is_crypto:
                    # --- CRYPTO DUST PROTECTION ---
                    if current_price > 0:
                        pos_value = abs(position.qty * current_price)
                        if pos_value < 1.0:
                            self._logger.log("exitbot_v2_dust_skip", {
                                "symbol": position.symbol,
                                "qty": position.qty,
                                "value": round(pos_value, 6),
                                "reason": "crypto_position_too_small_for_partial_exit"
                            })
                            return result

                        exit_notional = qty_to_exit * current_price
                        if exit_notional < 0.10:
                            self._logger.log("exitbot_v2_partial_too_small_full_exit", {
                                "symbol": position.symbol,
                                "qty_to_exit": qty_to_exit,
                                "notional": round(exit_notional, 6),
                                "action": "converting_to_full_exit"
                            })
                            qty_to_exit = position.qty
                    else:
                        self._logger.log("exitbot_v2_no_price_skip", {
                            "symbol": position.symbol,
                            "reason": "no_current_price_for_dust_check"
                        })
                        return result
                else:
                    # --- EQUITY/OPTIONS: minimum 1 share/contract ---
                    if qty_to_exit < 1.0:
                        self._logger.log("exitbot_v2_skip_sub_unit", {
                            "symbol": position.symbol,
                            "qty_to_exit": qty_to_exit,
                            "position_qty": position.qty,
                            "reason": "below_1_share_minimum"
                        })
                        return result
                
                if qty_to_exit > 0:
                    side = "sell" if position.side == "long" else "buy"
                    exit_result = self._alpaca.place_market_order(
                        symbol=position.symbol,
                        qty=qty_to_exit,
                        side=side
                    )
                    
                    if exit_result and exit_result.get("success"):
                        self._last_scale_out_times[cooldown_key] = time.time()
                        self._logger.log("exitbot_v2_scale_out", {
                            "symbol": position.symbol,
                            "exit_pct": exit_pct,
                            "qty_exited": qty_to_exit,
                            "reason": decision.primary_reason.value,
                            "order_id": exit_result.get("order_id")
                        })
                        result["exit_triggered"] = True
                
                return result
            
            elif decision.action == ExitActionV2.FULL_EXIT:
                # Check for exit lock to prevent duplicate orders
                if self._trailing_stop_mgr.has_exit_lock(
                    position.bot_id, position.position_id,
                    position.symbol, position.asset_class
                ):
                    self._logger.log("v2_exit_locked", {
                        "symbol": position.symbol,
                        "reason": "exit_already_pending"
                    })
                    return result
                
                # Full position exit
                client_order_id = f"v2_exit_{position.symbol}_{int(time.time() * 1000)}"
                
                # Set exit lock before placing order
                self._trailing_stop_mgr.set_exit_lock(
                    position.bot_id, position.position_id,
                    position.symbol, position.asset_class,
                    client_order_id
                )
                
                side = "sell" if position.side == "long" else "buy"
                exit_result = self._alpaca.place_market_order(
                    symbol=position.symbol,
                    qty=position.qty,
                    side=side,
                    client_order_id=client_order_id
                )
                
                if exit_result and exit_result.get("success"):
                    exit_price = exit_result.get("filled_avg_price", position.current_price)
                    order_id = exit_result.get("order_id", "")
                    
                    # Record exit in recent exits
                    record = ExitRecord(
                        symbol=position.symbol,
                        side=position.side,
                        qty=position.qty,
                        entry_price=position.entry_price,
                        exit_price=exit_price,
                        pnl=position.unrealized_pnl,
                        pnl_percent=pnl_pct,
                        reason=f"v2_{decision.primary_reason.value}",
                        bot_id=position.bot_id,
                        timestamp=datetime.utcnow().isoformat()
                    )
                    self._recent_exits.insert(0, record)
                    if len(self._recent_exits) > 10:
                        self._recent_exits = self._recent_exits[:10]
                    
                    # Emit v2 decision to JSONL audit trail
                    run_id = self.generate_run_id()
                    self._emit_event("exitbot.v2_decision", {
                        "run_id": run_id,
                        "position_key": position.position_id,
                        "decision": {
                            "action": decision.action.value,
                            "confidence": decision.confidence,
                            "health_score": decision.health_score,
                            "primary_reason": decision.primary_reason.value
                        },
                        "reasoning": decision.reasoning
                    })
                    
                    # Emit v2 action to JSONL
                    self._emit_event("exitbot.v2_action", {
                        "run_id": run_id,
                        "position_key": position.position_id,
                        "action": "FULL_EXIT",
                        "qty": position.qty,
                        "order_id": order_id
                    })
                    
                    # Update exit_trades in SQLite
                    try:
                        conn = get_db_connection()
                        now = datetime.utcnow().isoformat() + "Z"
                        excursion = self._excursion_tracker.get(position.position_id, {})
                        
                        conn.execute("""
                            UPDATE exit_trades SET
                                exit_ts = ?,
                                exit_price = ?,
                                exit_reason = ?,
                                realized_pnl_usd = ?,
                                realized_pnl_pct = ?,
                                mfe_pct = ?,
                                mae_pct = ?,
                                updated_at = ?
                            WHERE symbol = ? AND bot_id = ? AND exit_ts IS NULL
                        """, (
                            now,
                            exit_price,
                            f"v2_{decision.primary_reason.value}",
                            position.unrealized_pnl,
                            pnl_pct,
                            excursion.get("mfe_pct", 0.0),
                            excursion.get("mae_pct", 0.0),
                            now,
                            position.symbol,
                            position.bot_id
                        ))
                        conn.commit()
                    except Exception as e:
                        self._logger.error(f"Failed to update exit_trades for v2 exit: {e}")
                    
                    self._logger.log("exitbot_v2_full_exit", {
                        "symbol": position.symbol,
                        "qty": position.qty,
                        "pnl_pct": round(pnl_pct, 2),
                        "reason": decision.primary_reason.value,
                        "confidence": round(decision.confidence, 2),
                        "order_id": order_id
                    })
                    
                    result["exit_triggered"] = True
                    result["full_exit"] = True
                    
                    try:
                        if self._session_protection is not None:
                            self._session_protection.record_trade_pnl(
                                position.unrealized_pnl, position.symbol,
                                f"v2_{decision.primary_reason.value}"
                            )
                    except Exception as sp_err:
                        self._logger.error(f"SessionProtection record failed in v2 exit (fail-open): {sp_err}")
                
                return result
            
        except Exception as e:
            self._logger.error(f"V2 decision execution error: {e}")
        
        return result
    
    def _save_pending_intents(self) -> None:
        """Save pending intents to state for restart resilience."""
        intents_data = {}
        for coid, intent in self._pending_intents.items():
            intents_data[coid] = {
                "bot_id": intent.bot_id,
                "symbol": intent.symbol,
                "side": intent.side,
                "qty": intent.qty,
                "entry_price": intent.entry_price,
                "entry_ts": intent.entry_ts,
                "signal_id": intent.signal_id,
                "client_order_id": intent.client_order_id,
                "alpaca_order_id": intent.alpaca_order_id,
                "asset_class": intent.asset_class,
                "position_key": intent.position_key,
                "regime": intent.regime,
                "options": intent.options
            }
        set_state("exitbot.pending_intents", intents_data)
    
    def _save_excursion_tracker(self) -> None:
        """Save excursion tracker to state for restart resilience."""
        set_state("exitbot.excursion_tracker", self._excursion_tracker)
    
    def get_pending_intent(self, client_order_id: str) -> Optional[EntryIntent]:
        """Get pending intent by client_order_id."""
        return self._pending_intents.get(client_order_id)
    
    def confirm_entry_fill(
        self,
        client_order_id: str,
        alpaca_order_id: str,
        filled_qty: float,
        avg_fill_price: float
    ) -> Optional[str]:
        """
        Confirm entry fill and transition intent to active position.
        
        Called when an entry order fills. Updates the intent with actual
        fill data and moves it to active tracking.
        
        Args:
            client_order_id: Order identifier
            alpaca_order_id: Alpaca's order ID
            filled_qty: Actual filled quantity
            avg_fill_price: Actual fill price
            
        Returns:
            position_key if successful, None otherwise
        """
        intent = self._pending_intents.get(client_order_id)
        if not intent:
            self._logger.warn(f"No pending intent for client_order_id: {client_order_id}")
            return None
        
        # Update intent with fill data
        intent.alpaca_order_id = alpaca_order_id
        intent.qty = filled_qty
        intent.entry_price = avg_fill_price
        
        # Move to active positions
        self._active_positions[intent.position_key] = {
            "intent": intent,
            "filled_at": datetime.utcnow().isoformat() + "Z",
            "filled_qty": filled_qty,
            "avg_fill_price": avg_fill_price
        }
        
        # Remove from pending
        del self._pending_intents[client_order_id]
        self._save_pending_intents()
        
        # Update exit_trades with actual fill data
        try:
            conn = get_db_connection()
            now = datetime.utcnow().isoformat() + "Z"
            conn.execute("""
                UPDATE exit_trades SET
                    entry_price = ?,
                    qty = ?,
                    entry_alpaca_order_id = ?,
                    updated_at = ?
                WHERE position_key = ?
            """, (avg_fill_price, filled_qty, alpaca_order_id, now, intent.position_key))
            conn.commit()
        except Exception as e:
            self._logger.error(f"Failed to update exit_trade with fill: {e}")
        
        # Initialize excursion tracker
        self._excursion_tracker[intent.position_key] = {
            "mfe_pct": 0.0,
            "mae_pct": 0.0,
            "high_water": avg_fill_price,
            "low_water": avg_fill_price
        }
        self._save_excursion_tracker()
        
        self._logger.log("exitbot_entry_fill_confirmed", {
            "position_key": intent.position_key,
            "client_order_id": client_order_id,
            "filled_qty": filled_qty,
            "avg_fill_price": avg_fill_price
        })
        
        return intent.position_key
    
    # =========================================================================
    # End ExitBot v2 Methods
    # =========================================================================
    
    def _monitor_positions(self, exitbot_config: Dict, bots_config: Dict, 
                          tighten_stops: bool = False) -> Dict[str, int]:
        """
        Monitor all positions and manage trailing stops
        
        This is the core position monitoring logic that:
        1. Fetches all current positions from Alpaca
        2. Detects new positions (manual or automated)
        3. Registers trailing stops for new positions
        4. Updates trailing stops for existing positions
        5. Triggers exits when stops are hit
        6. Tightens stops when market regime signals danger (VVIX warning, rate shock)
        
        Args:
            exitbot_config: ExitBot configuration from bots.yaml
            bots_config: Full bots configuration for default trailing stop settings
            tighten_stops: If True, reduce trailing stop buffer by 50% (regime warning active)
            
        Returns:
            Dict with monitoring metrics
        """
        positions_monitored = 0
        trailing_stops_active = 0
        exits_triggered = 0
        pnl_summary = []  # Track P&L for each position
        
        # Get current positions from Alpaca
        try:
            positions_list = self._alpaca.get_positions()
        except Exception as e:
            self._logger.error(f"Failed to fetch positions: {e}")
            return {"positions_monitored": 0, "trailing_stops_active": 0, "exits_triggered": 0}
        
        current_positions = positions_list

        # FLOOR PROTECTION: Force-close all positions if P&L dropped below locked floor
        if self._session_protection is not None:
            try:
                should_force, floor_usd = self._session_protection.should_force_exit_to_protect_floor()
                if should_force and current_positions:
                    sp_status = self._session_protection.get_session_status()
                    current_pnl = sp_status.get("realized_pnl_usd", 0)
                    print(f"\n  *** FLOOR PROTECTION: P&L ${current_pnl:.0f} < floor ${floor_usd:.0f} — closing all positions ***\n")
                    self._logger.log("session_protection_floor_breach", {
                        "current_pnl": current_pnl,
                        "floor": floor_usd,
                        "positions_to_close": len(current_positions),
                    })
                    for pos in current_positions:
                        try:
                            sym = getattr(pos, "symbol", None) or pos.get("symbol", "?")
                            qty = abs(float(getattr(pos, "qty", None) or pos.get("qty", 0)))
                            side = "sell" if qty > 0 else "buy"
                            if qty > 0:
                                self._alpaca.place_market_order(sym, qty, side)
                                print(f"    FLOOR EXIT: {sym} x{qty}")
                        except Exception as fe:
                            self._logger.error(f"Floor protection exit failed for position: {fe}")
            except Exception as fp_err:
                self._logger.error(f"Floor protection check failed (fail-open): {fp_err}")

        # Build set of current position IDs
        current_position_ids: Set[str] = set()
        
        for pos in current_positions:
          try:
            # Parse position data from Alpaca
            position_info = self._parse_alpaca_position(pos)
            if position_info is None:
                continue
            
            # SPREAD PROTECTION: Skip short legs of option spreads
            # Short legs are managed as part of their spread unit, not independently.
            # Exiting a short leg alone breaks the spread structure and locks in losses.
            if position_info.asset_class == "us_option" and position_info.side == "short":
                spread_state = get_state(f"spread.short_leg.{position_info.symbol}")
                if spread_state:
                    try:
                        spread_data = json.loads(spread_state) if isinstance(spread_state, str) else spread_state
                        self._logger.log("exitbot_spread_short_leg_skipped", {
                            "symbol": position_info.symbol,
                            "strategy": spread_data.get("strategy", "unknown"),
                            "long_symbol": spread_data.get("long_symbol", "unknown"),
                            "ticker": spread_data.get("ticker", "unknown"),
                            "pnl_usd": round(position_info.unrealized_pnl, 2),
                            "reason": "short_leg_of_spread_protected"
                        })
                    except Exception:
                        self._logger.log("exitbot_spread_short_leg_skipped", {
                            "symbol": position_info.symbol,
                            "reason": "short_leg_of_spread_protected"
                        })
                    current_position_ids.add(position_info.position_id)
                    if position_info.position_id not in self._known_positions:
                        self._known_positions.add(position_info.position_id)
                        self._save_known_positions()
                    continue
            
            current_position_ids.add(position_info.position_id)
            positions_monitored += 1
            
            # Check if this is a new position we haven't seen before
            is_new_position = position_info.position_id not in self._known_positions
            
            if is_new_position:
                # New position detected - register trailing stop
                self._logger.log("exitbot_new_position_detected", {
                    "symbol": position_info.symbol,
                    "side": position_info.side,
                    "qty": position_info.qty,
                    "entry_price": position_info.entry_price,
                    "bot_id": position_info.bot_id,
                    "asset_class": position_info.asset_class
                })
                
                # Get trailing stop config for this position (with entry_price for dynamic ATR trailing)
                ts_config = self._get_trailing_stop_config(
                    position_info.symbol,
                    position_info.asset_class,
                    exitbot_config,
                    bots_config,
                    entry_price=position_info.entry_price
                )
                
                if ts_config.enabled:
                    # Initialize trailing stop for this position
                    self._trailing_stop_mgr.init_for_position(
                        bot_id=position_info.bot_id,
                        position_id=position_info.position_id,
                        symbol=position_info.symbol,
                        asset_class=position_info.asset_class,
                        entry_price=position_info.entry_price,
                        side=position_info.side,
                        config=ts_config
                    )
                    trailing_stops_active += 1
                
                # PRE-STAGE EXIT ORDERS on Alpaca for broker-side protection
                # Uses liquidate-winners config for TP threshold, hard stop for SL
                lw_config = exitbot_config.get("liquidate_winners_mode", {})
                if lw_config.get("enabled", False):
                    tp_pct = lw_config.get("min_profit_pct", 0.1) / 100.0  # Convert % to decimal
                    stop_pct = lw_config.get("max_loss_override_pct", 10.0) / 100.0
                else:
                    tp_pct = 0.02  # Default 2% take-profit
                    stop_pct = 0.10  # Default 10% stop-loss
                
                try:
                    self.stage_exit_orders(
                        position_id=position_info.position_id,
                        symbol=position_info.symbol,
                        qty=position_info.qty,
                        side=position_info.side,
                        entry_price=position_info.entry_price,
                        stop_pct=stop_pct,
                        tp_pct=tp_pct
                    )
                except Exception as stage_err:
                    # Staging is best-effort - don't block position tracking
                    self._logger.error(f"Failed to stage exit orders for {position_info.symbol}: {stage_err}")
                
                # Mark position as known
                self._known_positions.add(position_info.position_id)
                self._save_known_positions()
            
            else:
                # Existing position - check all exit conditions in priority order
                
                # Calculate current profit/loss percentage
                if position_info.side == "long":
                    pnl_pct = ((position_info.current_price - position_info.entry_price) / position_info.entry_price) * 100
                else:
                    pnl_pct = ((position_info.entry_price - position_info.current_price) / position_info.entry_price) * 100
                
                # Track P&L for summary logging (detect slow bleeders)
                pnl_summary.append({
                    "symbol": position_info.symbol,
                    "pnl_pct": round(pnl_pct, 2),
                    "pnl_usd": round(position_info.unrealized_pnl, 2),
                    "side": position_info.side,
                    "asset_class": position_info.asset_class,
                    "entry": round(position_info.entry_price, 4),
                    "current": round(position_info.current_price, 4)
                })
                
                # ============================================================
                # GET ASSET-CLASS-SPECIFIC EXIT PROFILE
                # This is the single source of truth for all exit settings
                # ============================================================
                exit_profile = self._get_asset_class_exit_profile(
                    position_info.asset_class, exitbot_config
                )
                min_hold_minutes = exit_profile.get("min_hold_minutes", 5)
                catastrophic_stop_pct = exit_profile.get("catastrophic_stop_pct", 30.0)
                
                if position_info.asset_class == "crypto" and position_info.bot_id == "bounce_core":
                    bounce_state = get_state(f"bounce_position_{position_info.symbol}")
                    if bounce_state:
                        min_hold_minutes = 0
                
                # Calculate hold duration in minutes
                hold_duration_sec = time.time() - position_info.first_seen_ts
                hold_duration_min = hold_duration_sec / 60.0
                
                # ============================================================
                # EXIT CHECK 0: CATASTROPHIC STOP (bypasses min_hold)
                # Immediate exit if loss exceeds catastrophic threshold
                # ============================================================
                if pnl_pct <= -catastrophic_stop_pct:
                    self._logger.log("catastrophic_stop_triggered", {
                        "symbol": position_info.symbol,
                        "asset_class": position_info.asset_class,
                        "pnl_pct": round(pnl_pct, 2),
                        "threshold": catastrophic_stop_pct,
                        "hold_min": round(hold_duration_min, 1),
                        "reason": "BYPASSING min_hold - catastrophic loss"
                    })
                    print(f"  [ExitBot] EXIT {position_info.symbol} CATASTROPHIC STOP {pnl_pct:.1f}% (threshold: -{catastrophic_stop_pct}%)")
                    self.cancel_staged_orders(position_info.position_id, "catastrophic_stop")
                    exit_result = self._close_with_spread_protection(position_info, "catastrophic_stop")
                    if exit_result:
                        self._add_exit_record(position_info, "catastrophic_stop")
                        exits_triggered += 1
                        continue
                
                # ============================================================
                # MIN_HOLD CHECK: Skip all normal exits if position too young
                # ============================================================
                if hold_duration_min < min_hold_minutes:
                    self._logger.log("min_hold_active", {
                        "symbol": position_info.symbol,
                        "asset_class": position_info.asset_class,
                        "hold_min": round(hold_duration_min, 1),
                        "min_hold_minutes": min_hold_minutes,
                        "pnl_pct": round(pnl_pct, 2),
                        "remaining_min": round(min_hold_minutes - hold_duration_min, 1)
                    })
                    # Skip ALL exit checks - let the position develop
                    trailing_stops_active += 1  # Still count as monitored
                    continue
                
                # Get exit configurations for this position (using profile overrides)
                hard_stop_config = self._get_hard_stop_config(
                    position_info.symbol, position_info.asset_class,
                    exitbot_config, bots_config, tighten_stops
                )
                # Override with asset-class-specific stop loss
                hard_stop_config.stop_loss_pct = exit_profile.get("stop_loss_pct", hard_stop_config.stop_loss_pct)
                
                tp_config = self._get_take_profit_config(
                    position_info.symbol, position_info.asset_class,
                    exitbot_config, bots_config
                )
                
                # ============================================================
                # EXIT CHECK 0.25: LIQUIDATE WINNERS / LET LOSERS RIDE MODE
                # Takes any profit immediately, holds losers until breakeven
                # ============================================================
                lw_config = exitbot_config.get("liquidate_winners_mode", {})
                if lw_config.get("enabled", False):
                    min_profit_pct = lw_config.get("min_profit_pct", 0.1)
                    breakeven_buffer = lw_config.get("breakeven_buffer_pct", 0.1)
                    max_loss_override = lw_config.get("max_loss_override_pct", 10.0)
                    
                    # WINNERS: Exit immediately if profitable
                    if pnl_pct >= min_profit_pct:
                        self._logger.log("liquidate_winner_triggered", {
                            "symbol": position_info.symbol,
                            "pnl_pct": round(pnl_pct, 2),
                            "pnl_usd": round(position_info.unrealized_pnl, 2),
                            "min_profit_pct": min_profit_pct,
                            "reason": "TAKING PROFIT - liquidate winners mode"
                        })
                        # Cancel staged orders before manual exit to avoid double-exit
                        self.cancel_staged_orders(position_info.position_id, "liquidate_winner")
                        exit_result = self._close_with_spread_protection(position_info, "liquidate_winner")
                        if exit_result:
                            self._add_exit_record(position_info, "liquidate_winner")
                            exits_triggered += 1
                            continue
                    
                    # LOSERS: Check if recovered to breakeven
                    elif pnl_pct < 0 and pnl_pct >= -breakeven_buffer:
                        self._logger.log("breakeven_exit_triggered", {
                            "symbol": position_info.symbol,
                            "pnl_pct": round(pnl_pct, 2),
                            "pnl_usd": round(position_info.unrealized_pnl, 2),
                            "buffer": breakeven_buffer,
                            "reason": "BREAKEVEN REACHED - cutting loser at recovery"
                        })
                        # Cancel staged orders before manual exit
                        self.cancel_staged_orders(position_info.position_id, "breakeven_exit")
                        exit_result = self._close_with_spread_protection(position_info, "breakeven_exit")
                        if exit_result:
                            self._add_exit_record(position_info, "breakeven_exit")
                            exits_triggered += 1
                            continue
                    
                    # LOSERS: Safety override - force exit if max loss exceeded
                    elif pnl_pct <= -max_loss_override:
                        self._logger.log("max_loss_override_triggered", {
                            "symbol": position_info.symbol,
                            "pnl_pct": round(pnl_pct, 2),
                            "pnl_usd": round(position_info.unrealized_pnl, 2),
                            "max_loss_override": max_loss_override,
                            "reason": "MAX LOSS OVERRIDE - forcing exit on underwater position"
                        })
                        # Cancel staged orders before manual exit
                        self.cancel_staged_orders(position_info.position_id, "max_loss_override")
                        exit_result = self._close_with_spread_protection(position_info, "max_loss_override")
                        if exit_result:
                            self._add_exit_record(position_info, "max_loss_override")
                            exits_triggered += 1
                            continue
                    
                    # LOSERS: Let it ride - skip other exit checks
                    elif pnl_pct < -breakeven_buffer:
                        self._logger.log("letting_loser_ride", {
                            "symbol": position_info.symbol,
                            "pnl_pct": round(pnl_pct, 2),
                            "pnl_usd": round(position_info.unrealized_pnl, 2),
                            "breakeven_target": 0.0,
                            "max_loss_override": max_loss_override,
                            "reason": "HOLDING LOSER - waiting for breakeven recovery"
                        })
                        trailing_stops_active += 1
                        continue  # Skip other exit checks - let it ride
                
                # ============================================================
                # EXIT CHECK 0.5: V2 INTELLIGENCE DECISION ENGINE (adaptive exits)
                # Uses TradeMemoryEngine, TradeHealthScorer, ExitDecisionEngine
                # ============================================================
                v2_enabled = exitbot_config.get("v2_intelligence_enabled", False)  # DISABLED by default
                if v2_enabled:
                    try:
                        v2_decision = self._run_v2_intelligence(
                            position_info, pnl_pct, exitbot_config
                        )
                        if v2_decision and v2_decision.action != ExitActionV2.HOLD:
                            # V2 intelligence recommends action
                            v2_result = self._execute_v2_decision(
                                position_info, v2_decision, pnl_pct
                            )
                            if v2_result.get("exit_triggered"):
                                exits_triggered += 1
                                if v2_result.get("full_exit"):
                                    continue  # Full position exited, move to next
                    except Exception as e:
                        self._logger.error(f"V2 intelligence failed for {position_info.symbol}: {e}")
                        # Fail-open: continue with legacy exit checks
                
                # ============================================================
                # EXIT CHECK 0.75: PROFIT SNIPER (profit-priority exit)
                # Captures peak profits before reversal via ratchet,
                # velocity reversal, and momentum exhaustion detection.
                # DISABLED FOR OPTIONS (02/17): ProfitSniper ratchet exits
                # at 5-10% profit but options tiered exits target 200-2400%.
                # Trade-Bot approach: let tiers handle options, sniper is for stocks.
                # ============================================================
                try:
                    if self._profit_sniper is None:
                        self._profit_sniper = get_profit_sniper()

                    if position_info.asset_class in ("option", "us_option"):
                        sniper_cfg = ProfitSniperConfig.for_options()
                    elif position_info.asset_class == "crypto":
                        sniper_cfg = ProfitSniperConfig.for_crypto()
                    else:
                        sniper_cfg = ProfitSniperConfig()

                    if self._session_protection is not None:
                        try:
                            should_tighten, tighten_factor = self._session_protection.get_tighten_factor()
                            if should_tighten and tighten_factor > 0:
                                original_distance = sniper_cfg.ratchet_base_distance_pct
                                sniper_cfg.ratchet_base_distance_pct *= (1.0 - tighten_factor)
                                sniper_cfg.ratchet_base_distance_pct = max(
                                    sniper_cfg.ratchet_base_distance_pct,
                                    sniper_cfg.ratchet_min_distance_pct
                                )
                                self._logger.log("session_protection_sniper_tighten", {
                                    "symbol": position_info.symbol,
                                    "original_distance": round(original_distance, 4),
                                    "tightened_distance": round(sniper_cfg.ratchet_base_distance_pct, 4),
                                    "tighten_factor": tighten_factor,
                                })
                        except Exception as sp_tighten_err:
                            self._logger.error(f"SessionProtection tighten check failed (fail-open): {sp_tighten_err}")

                    if not sniper_cfg.enabled:
                        self._logger.log("profit_sniper_skipped", {
                            "symbol": position_info.symbol,
                            "asset_class": position_info.asset_class,
                            "pnl_pct": round(pnl_pct, 2),
                            "reason": "disabled_for_asset_class"
                        })
                    elif sniper_cfg.enabled:
                        sniper_decision = self._profit_sniper.evaluate(
                            position_key=position_info.position_id,
                            entry_price=position_info.entry_price,
                            current_price=position_info.current_price,
                            side=position_info.side,
                            config=sniper_cfg,
                            bot_id=position_info.bot_id
                        )

                        self._logger.log("profit_sniper_eval", {
                            "symbol": position_info.symbol,
                            "should_exit": sniper_decision.should_exit,
                            "exit_pct": sniper_decision.exit_pct,
                            "reason": sniper_decision.reason,
                            "confidence": round(sniper_decision.confidence, 3),
                            "peak_profit_pct": round(sniper_decision.peak_profit_pct, 3),
                            "current_profit_pct": round(sniper_decision.current_profit_pct, 3),
                            "ratchet_price": round(sniper_decision.ratchet_price, 4),
                            "velocity": round(sniper_decision.velocity, 4),
                            "bot_id": position_info.bot_id,
                            "asset_class": position_info.asset_class
                        })

                        if sniper_decision.should_exit:
                            self._logger.log("profit_sniper_exit_triggered", {
                                "symbol": position_info.symbol,
                                "reason": sniper_decision.reason,
                                "exit_pct": sniper_decision.exit_pct,
                                "confidence": round(sniper_decision.confidence, 3),
                                "peak_profit_pct": round(sniper_decision.peak_profit_pct, 3),
                                "current_profit_pct": round(sniper_decision.current_profit_pct, 3),
                                "details": sniper_decision.details,
                                "pnl_usd": round(position_info.unrealized_pnl, 2),
                                "bot_id": position_info.bot_id
                            })
                            self.cancel_staged_orders(position_info.position_id, f"profit_sniper_{sniper_decision.reason}")
                            is_spread_position = (
                                position_info.asset_class == "us_option"
                                and get_state(f"spread.long_leg.{position_info.symbol}") is not None
                            )
                            if sniper_decision.exit_pct >= 100.0 or is_spread_position:
                                exit_result = self._close_with_spread_protection(position_info, f"profit_sniper_{sniper_decision.reason}")
                            else:
                                partial_qty = round(position_info.qty * (sniper_decision.exit_pct / 100.0), 6)
                                if partial_qty < 1 and position_info.asset_class != "crypto":
                                    partial_qty = max(1, int(partial_qty))
                                if partial_qty > 0:
                                    exit_result = self._alpaca.close_position(
                                        position_info.symbol, qty=partial_qty
                                    )
                                else:
                                    exit_result = None
                            if exit_result:
                                self._add_exit_record(position_info, f"profit_sniper_{sniper_decision.reason}")
                                exits_triggered += 1
                                if sniper_decision.exit_pct >= 100.0:
                                    self._profit_sniper.clear_state(position_info.position_id)
                                    continue
                except Exception as sniper_err:
                    self._logger.error(f"ProfitSniper check failed for {position_info.symbol}: {sniper_err}")

                # ============================================================
                # EXIT CHECK 1: HARD STOP-LOSS (highest priority - protect capital)
                # ============================================================
                if hard_stop_config.enabled:
                    hard_stop_triggered = self._check_hard_stop_loss(
                        position_info, pnl_pct, hard_stop_config
                    )
                    if hard_stop_triggered:
                        print(f"  [ExitBot] EXIT {position_info.symbol} HARD STOP {pnl_pct:.1f}% (limit: -{hard_stop_config.stop_loss_pct:.0f}%)")
                        exit_result = self._execute_hard_stop_exit(
                            position_info, pnl_pct, hard_stop_config
                        )
                        if exit_result:
                            exits_triggered += 1
                            continue
                
                # ============================================================
                # EXIT CHECK 2: TIERED TAKE-PROFIT (TP1, TP2, TP3)
                # ============================================================
                if tp_config.enabled and pnl_pct > 0:
                    tp_exit_result = self._check_and_execute_take_profit(
                        position_info, pnl_pct, tp_config
                    )
                    if tp_exit_result.get("exit_triggered"):
                        tier = tp_exit_result.get("tier", "?")
                        pct = tp_exit_result.get("exit_pct", 100)
                        print(f"  [ExitBot] EXIT {position_info.symbol} TIER {tier} +{pnl_pct:.1f}% (sold {pct}%)")
                        exits_triggered += 1
                        if tp_exit_result.get("full_exit"):
                            continue
                
                # ============================================================
                # EXIT CHECK 2.5: REVERSAL-SENSE STOP (drop from high water mark)
                # Catches positions that went up but came back down, even if
                # they never hit the trailing stop activation threshold
                # ============================================================
                reversal_config = self._get_reversal_sense_config(
                    position_info.symbol, position_info.asset_class,
                    exitbot_config, bots_config
                )
                
                if reversal_config.enabled:
                    # Load trailing stop state to get high_water mark
                    ts_state_for_reversal = self._trailing_stop_mgr.load_state(
                        bot_id=position_info.bot_id,
                        position_id=position_info.position_id,
                        symbol=position_info.symbol,
                        asset_class=position_info.asset_class
                    )
                    
                    if ts_state_for_reversal is not None:
                        reversal_triggered = self._check_reversal_sense_stop(
                            position_info, ts_state_for_reversal, reversal_config
                        )
                        if reversal_triggered:
                            exit_result = self._execute_reversal_sense_exit(
                                position_info, ts_state_for_reversal, reversal_config
                            )
                            if exit_result:
                                exits_triggered += 1
                                continue  # Position exited, move to next
                
                # ============================================================
                # EXIT CHECK 3: TRAILING STOP (existing logic)
                # ============================================================
                ts_state = self._trailing_stop_mgr.load_state(
                    bot_id=position_info.bot_id,
                    position_id=position_info.position_id,
                    symbol=position_info.symbol,
                    asset_class=position_info.asset_class
                )
                
                if ts_state is not None:
                    # Update trailing stop with current price
                    updated_state = self._trailing_stop_mgr.update_state(
                        bot_id=position_info.bot_id,
                        position_id=position_info.position_id,
                        symbol=position_info.symbol,
                        asset_class=position_info.asset_class,
                        current_price=position_info.current_price,
                        state=ts_state
                    )
                    
                    # Log position status with trailing stop details
                    config_dict = ts_state.config if ts_state.config else {}
                    activation_pct = config_dict.get("activation_profit_pct", 0.3)
                    
                    tiers_hit = list(self._tp_tiers_hit.get(position_info.position_id, set()))
                    self._logger.log("exitbot_position_status", {
                        "symbol": position_info.symbol,
                        "side": position_info.side,
                        "entry": round(position_info.entry_price, 4),
                        "current": round(position_info.current_price, 4),
                        "pnl_pct": round(pnl_pct, 3),
                        "pnl_usd": round(position_info.unrealized_pnl, 2),
                        "armed": updated_state.armed,
                        "activation_pct": activation_pct,
                        "stop_price": round(updated_state.stop_price, 4) if updated_state.stop_price else 0,
                        "high_water": round(updated_state.high_water, 4) if position_info.side == "long" else round(updated_state.low_water, 4),
                        "hard_stop_pct": hard_stop_config.stop_loss_pct if hard_stop_config.enabled else None,
                        "tp_tiers_hit": tiers_hit
                    })
                    
                    # PLAY-BY-PLAY console output (forensic addition 02/17)
                    status_icon = "+" if pnl_pct > 0 else "-"
                    armed_str = "ARMED" if updated_state.armed else "watch"
                    stop_str = f"stop@${updated_state.stop_price:.2f}" if updated_state.stop_price else f"hard@-{hard_stop_config.stop_loss_pct:.0f}%"
                    hw = updated_state.high_water if position_info.side == "long" else updated_state.low_water
                    tiers_str = f"T{','.join(str(t) for t in sorted(tiers_hit))}" if tiers_hit else "no-tiers"
                    print(f"  [ExitBot] {position_info.symbol[:25]:25s} {status_icon}{abs(pnl_pct):.1f}% ${position_info.unrealized_pnl:+.0f} | {armed_str} {stop_str} | hw=${hw:.2f} | {tiers_str}")
                    
                    # Apply dynamic ATR profit-tier tightening (generous → progressively tighter)
                    # Only applies when trailing stop was computed dynamically from ATR
                    if updated_state.armed:
                        updated_state = self._trailing_stop_mgr.apply_profit_tier_tightening(
                            updated_state, position_info.current_price
                        )
                    
                    # Apply regime-based tightening AFTER update_state
                    # This permanently reduces the stop buffer by 50% when regime warns of danger
                    if tighten_stops and updated_state.armed:
                        updated_state = self._trailing_stop_mgr.apply_tightening(
                            bot_id=position_info.bot_id,
                            position_id=position_info.position_id,
                            symbol=position_info.symbol,
                            asset_class=position_info.asset_class,
                            state=updated_state,
                            tighten_ratio=0.5  # 50% tighter buffer
                        )
                    
                    # Apply reversal-sense tightening if enabled
                    # This detects momentum/volume reversals and tightens stops dynamically
                    if updated_state.armed and (config_dict.get("reversal_sense") or {}).get("enabled", False):
                        try:
                            from .bar_cache import get_bar_cache
                            bar_cache = get_bar_cache()
                            bars = bar_cache.get_cached_bars(position_info.symbol, limit=25)
                            
                            if bars:
                                updated_state, was_tightened = self._trailing_stop_mgr.apply_reversal_tightening(
                                    bot_id=position_info.bot_id,
                                    position_id=position_info.position_id,
                                    symbol=position_info.symbol,
                                    asset_class=position_info.asset_class,
                                    state=updated_state,
                                    bars=bars,
                                    current_price=position_info.current_price
                                )
                        except Exception as e:
                            self._logger.error(f"Reversal-sense check failed for {position_info.symbol}: {e}")
                    
                    if updated_state.armed:
                        trailing_stops_active += 1
                    
                    # Check if trailing stop should trigger exit
                    # The tightened stop_price is now persisted and used by should_exit
                    should_exit = self._trailing_stop_mgr.should_exit(
                        updated_state, position_info.current_price
                    )
                    
                    if should_exit:
                        # Execute exit
                        exit_result = self._execute_trailing_stop_exit(
                            position_info,
                            updated_state
                        )
                        if exit_result:
                            exits_triggered += 1
                    else:
                        # No trailing stop exit - check news-based exit
                        # This is an additional trigger, uses same exit lock to prevent duplicates
                        intel_config = bots_config.get("intelligence", {})
                        dry_run = intel_config.get("dry_run", False)
                        
                        news_exit_info = self._check_news_exit(
                            position_info, intel_config, dry_run
                        )
                        
                        if news_exit_info and news_exit_info.get("should_exit", False):
                            exit_result = self._execute_news_exit(
                                position_info, news_exit_info
                            )
                            if exit_result:
                                exits_triggered += 1
          except Exception as per_position_err:
            import traceback
            sym = position_info.symbol if position_info else "unknown"
            tb = traceback.format_exc()
            self._logger.error(f"Position monitoring failed for {sym}: {per_position_err}")
            self._logger.log("position_monitoring_traceback", {
                "symbol": sym,
                "error": str(per_position_err),
                "traceback": tb[-500:]
            })
        
        # Clean up positions that no longer exist (closed positions)
        closed_positions = self._known_positions - current_position_ids
        tp_tiers_cleaned = False
        staged_cleaned = False
        for closed_id in closed_positions:
            self._logger.log("exitbot_position_closed", {"position_id": closed_id})
            self._known_positions.discard(closed_id)
            if closed_id in self._position_first_seen:
                del self._position_first_seen[closed_id]
            # Also clean up TP tiers tracking for closed positions
            if closed_id in self._tp_tiers_hit:
                del self._tp_tiers_hit[closed_id]
                tp_tiers_cleaned = True
            # Clean up staged orders for closed positions (may have been filled by Alpaca)
            if closed_id in self._staged_orders:
                del self._staged_orders[closed_id]
                staged_cleaned = True
                self._logger.log("staged_orders_cleaned_closed_position", {"position_id": closed_id})
        
        if closed_positions:
            self._save_known_positions()
            self._save_position_first_seen()
        if tp_tiers_cleaned:
            self._save_tp_tiers_state()
        if staged_cleaned:
            self._save_staged_orders()
        
        # Log position P&L summary per loop (helps detect slow bleeders)
        if pnl_summary:
            total_unrealized = sum(p["pnl_usd"] for p in pnl_summary)
            winners = [p for p in pnl_summary if p["pnl_pct"] > 0]
            losers = [p for p in pnl_summary if p["pnl_pct"] < 0]
            slow_bleeders = [p for p in pnl_summary if -5 < p["pnl_pct"] < -1]  # -1% to -5% range
            
            self._logger.log("exitbot_position_pnl_summary", {
                "positions_count": len(pnl_summary),
                "total_unrealized_usd": round(total_unrealized, 2),
                "winners_count": len(winners),
                "losers_count": len(losers),
                "slow_bleeders_count": len(slow_bleeders),
                "slow_bleeders": slow_bleeders[:5] if slow_bleeders else [],  # Top 5 slow bleeders
                "worst_position": min(pnl_summary, key=lambda x: x["pnl_pct"]) if pnl_summary else None,
                "best_position": max(pnl_summary, key=lambda x: x["pnl_pct"]) if pnl_summary else None
            })
        
        return {
            "positions_monitored": positions_monitored,
            "trailing_stops_active": trailing_stops_active,
            "exits_triggered": exits_triggered
        }
    
    def _close_with_spread_protection(self, position_info: 'PositionInfo', reason: str) -> bool:
        """
        Close a position with spread awareness.
        
        If the position is the long leg of a spread, also closes the paired
        short leg and cleans up spread state to preserve spread structure.
        
        Args:
            position_info: Position to close
            reason: Exit reason for logging
            
        Returns:
            True if position was successfully closed
        """
        cooldown_key = position_info.symbol
        if cooldown_key in self._close_cooldown:
            elapsed = time.time() - self._close_cooldown[cooldown_key]
            if elapsed < self._close_cooldown_seconds:
                self._logger.log("close_position_cooldown_skip", {
                    "symbol": position_info.symbol,
                    "reason": reason,
                    "cooldown_remaining": round(self._close_cooldown_seconds - elapsed, 1)
                })
                return False
        
        try:
            exit_result = self._alpaca.close_position(position_info.symbol)
            
            if position_info.asset_class == "crypto" and position_info.bot_id == "bounce_core":
                bounce_key = f"bounce_position_{position_info.symbol}"
                if get_state(bounce_key):
                    delete_state(bounce_key)
                    self._logger.log("exitbot_bounce_state_cleanup", {
                        "symbol": position_info.symbol,
                        "reason": reason
                    })

            if position_info.asset_class == "us_option":
                spread_state = get_state(f"spread.long_leg.{position_info.symbol}")
                if spread_state:
                    try:
                        spread_data = json.loads(spread_state) if isinstance(spread_state, str) else spread_state
                        short_symbol = spread_data.get("short_symbol", "")
                        if short_symbol:
                            try:
                                self._alpaca.close_position(short_symbol)
                                self._logger.log("exitbot_spread_short_leg_closed", {
                                    "short_symbol": short_symbol,
                                    "long_symbol": position_info.symbol,
                                    "reason": reason,
                                    "strategy": spread_data.get("strategy", "unknown"),
                                    "ticker": spread_data.get("ticker", "unknown")
                                })
                            except Exception as short_err:
                                self._logger.error(
                                    f"Failed to close paired short leg {short_symbol}: {short_err}"
                                )
                        delete_state(f"spread.long_leg.{position_info.symbol}")
                        delete_state(f"spread.short_leg.{short_symbol}")
                    except Exception as spread_err:
                        self._logger.error(f"Spread cleanup error: {spread_err}")
            
            return bool(exit_result)
        except Exception as e:
            self._logger.error(f"Failed to close position {position_info.symbol}: {e}")
            err_str = str(e).lower()
            if "insufficient qty" in err_str or "held_for_orders" in err_str or "position intent mismatch" in err_str:
                self._close_cooldown[cooldown_key] = time.time()
                self._logger.log("close_position_cooldown_set", {
                    "symbol": position_info.symbol,
                    "cooldown_seconds": self._close_cooldown_seconds,
                    "reason": "retryable_error"
                })
            return False

    def _parse_alpaca_position(self, pos: Any) -> Optional[PositionInfo]:
        """
        Parse Alpaca position data into PositionInfo
        
        Args:
            pos: AlpacaPosition dataclass from Alpaca API
            
        Returns:
            PositionInfo object or None if parsing fails
        """
        try:
            # Handle both AlpacaPosition dataclass and dict
            if hasattr(pos, 'symbol'):
                # AlpacaPosition dataclass - use the actual entry and current prices
                symbol = pos.symbol
                qty = float(pos.qty)
                market_value = float(pos.market_value)
                unrealized_pnl = float(pos.unrealized_pl)
                side = pos.side
                # Use actual entry price and current price from the API
                avg_entry_price = float(pos.avg_entry_price) if hasattr(pos, 'avg_entry_price') and pos.avg_entry_price else 0
                current_price = float(pos.current_price) if hasattr(pos, 'current_price') and pos.current_price else 0
                asset_class = pos.asset_class if hasattr(pos, 'asset_class') else "us_equity"
                
                # Fallback: calculate from market_value if API didn't provide prices
                if avg_entry_price == 0 and qty != 0:
                    avg_entry_price = abs(market_value / qty) - (unrealized_pnl / abs(qty))
                if current_price == 0 and qty != 0:
                    current_price = abs(market_value / qty)
            else:
                # Dict format (fallback)
                symbol = pos.get("symbol", "")
                qty = float(pos.get("qty", 0))
                avg_entry_price = float(pos.get("avg_entry_price", 0))
                current_price = float(pos.get("current_price", avg_entry_price))
                market_value = float(pos.get("market_value", 0))
                unrealized_pnl = float(pos.get("unrealized_pl", 0))
                side = pos.get("side", "long")
                asset_class = pos.get("asset_class", "us_equity")
            
            # Determine asset class from symbol if not provided
            if asset_class == "us_equity" and ("/" in symbol or symbol.endswith("USD")):
                asset_class = "crypto"
            
            # Determine side from quantity
            side = "long" if qty > 0 else "short"
            qty = abs(qty)
            
            # Generate unique position ID
            position_id = f"{symbol}_{side}_{avg_entry_price:.4f}"
            
            # Determine which bot owns this position (or mark as manual)
            bot_id = self._determine_bot_owner(symbol, asset_class)
            
            if position_id not in self._position_first_seen:
                self._position_first_seen[position_id] = time.time()
                self._save_position_first_seen()
                self._logger.log("position_first_seen_recorded", {
                    "symbol": symbol,
                    "position_id": position_id,
                    "ts": self._position_first_seen[position_id]
                })
            
            return PositionInfo(
                symbol=symbol,
                qty=qty,
                side=side,
                entry_price=avg_entry_price,
                current_price=current_price,
                market_value=market_value,
                unrealized_pnl=unrealized_pnl,
                asset_class=asset_class,
                position_id=position_id,
                first_seen_ts=self._position_first_seen[position_id],
                bot_id=bot_id
            )
        except Exception as e:
            self._logger.error(f"Failed to parse position: {e}", position=pos)
            return None
    
    def _determine_bot_owner(self, symbol: str, asset_class: str) -> str:
        """
        Determine which bot owns a position based on symbol and asset class
        
        If no bot claims the position, it's marked as "manual" (user-created)
        
        Args:
            symbol: Trading symbol
            asset_class: Asset class (us_equity, crypto, option)
            
        Returns:
            Bot ID or "manual"
        """
        try:
            config = load_bots_config()
            
            # Check crypto pairs
            if asset_class == "crypto":
                # Check BounceBot first (more specific overnight strategy)
                bouncebot = config.get("bouncebot", {})
                if bouncebot.get("enabled", False):
                    pairs = bouncebot.get("pairs", [])
                    for pair in pairs:
                        pair_symbol = pair.replace("/", "")
                        if symbol == pair_symbol or symbol == pair:
                            # Check if position was created during bounce window
                            # If we have bounce state for this position, assign to bouncebot
                            from ..core.state import get_state
                            bounce_state = get_state(f"bounce_position_{symbol}")
                            if bounce_state:
                                return bouncebot.get("bot_id", "bounce_core")
                
                # Fall back to cryptobot
                cryptobot = config.get("cryptobot", {})
                if cryptobot.get("enabled", False):
                    pairs = cryptobot.get("pairs", [])
                    # Convert BTC/USD to BTCUSD for matching
                    for pair in pairs:
                        pair_symbol = pair.replace("/", "")
                        if symbol == pair_symbol or symbol == pair:
                            return cryptobot.get("bot_id", "crypto_core")
            
            # Check momentum bots
            momentum_bots = config.get("momentum_bots", [])
            for bot in momentum_bots:
                if bot.get("enabled", False) and bot.get("ticker") == symbol:
                    return bot.get("bot_id", f"mom_{symbol}")
            
            # Check options bot (SPY, QQQ, IWM options)
            optionsbot = config.get("optionsbot", {})
            if asset_class in ("option", "us_option") and optionsbot.get("enabled", False):
                tickers = optionsbot.get("tickers", [])
                for ticker in tickers:
                    if symbol.startswith(ticker):
                        return optionsbot.get("bot_id", "opt_core")
            
            # Check 0DTE options bot
            optionsbot_0dte = config.get("optionsbot_0dte", {})
            if asset_class in ("option", "us_option") and optionsbot_0dte.get("enabled", False):
                tickers_0dte = optionsbot_0dte.get("tickers", [])
                for ticker in tickers_0dte:
                    if symbol.startswith(ticker):
                        return optionsbot_0dte.get("bot_id", "opt_0dte")
            
            # Check hailmary bot — check order_ids table for hailmary_bot entries
            hailmary_bot = config.get("hailmary_bot", {})
            if asset_class in ("option", "us_option") and hailmary_bot.get("enabled", False):
                try:
                    from ..core.state import get_db_connection
                    conn = get_db_connection()
                    cursor = conn.execute(
                        "SELECT bot_id FROM order_ids WHERE symbol = ? AND bot_id = 'hailmary_bot' LIMIT 1",
                        (symbol,)
                    )
                    row = cursor.fetchone()
                    if row:
                        return hailmary_bot.get("bot_id", "hm_core")
                except Exception as hm_db_err:
                    self._logger.warn(f"HailMary ownership DB lookup failed for {symbol}: {hm_db_err}")
                # Fallback: check hailmary tickers list
                hm_tickers = hailmary_bot.get("tickers", [])
                for ticker in hm_tickers:
                    if symbol.startswith(ticker):
                        return hailmary_bot.get("bot_id", "hm_core")
            
            # No bot claims this position - it's a manual trade
            return "manual"
            
        except Exception as e:
            self._logger.error(f"Failed to determine bot owner: {e}")
            return "manual"
    
    def _get_trailing_stop_config(self, symbol: str, asset_class: str,
                                   exitbot_config: Dict, bots_config: Dict,
                                   entry_price: float = 0.0) -> TrailingStopConfig:
        """
        Get trailing stop configuration for a position.
        
        Supports dynamic ATR-based trailing stops that adapt to each symbol's
        volatility. When a bot has dynamic_trailing enabled in its config,
        the trail width is computed from ATR × multiplier — generous by default
        to let trades develop, with profit-tiered tightening.
        
        Priority order:
        1. Bot-specific dynamic trailing (ATR-based, if enabled)
        2. Bot-specific static trailing config
        3. ExitBot default dynamic trailing (if enabled)
        4. ExitBot default static config
        
        Args:
            symbol: Trading symbol
            asset_class: Asset class
            exitbot_config: ExitBot configuration
            bots_config: Full bots configuration
            entry_price: Position entry price (needed for dynamic trailing)
            
        Returns:
            TrailingStopConfig for this position
        """
        # Helper: fetch ATR from sensors (fail-open: returns None if unavailable)
        def _get_atr(ticker: str) -> Optional[float]:
            try:
                from ..sensors import get_signal
                signal = get_signal(ticker)
                if signal and signal.atr_14 is not None:
                    return signal.atr_14
            except Exception:
                pass
            return None
        
        # Helper: try dynamic trailing for a given bot config section
        def _try_dynamic(bot_cfg: Dict, bot_name: str) -> Optional[TrailingStopConfig]:
            dyn_cfg = (bot_cfg.get("risk") or {}).get("dynamic_trailing") or {}
            if not dyn_cfg.get("enabled", False):
                return None
            
            # For options, use the underlying symbol for ATR lookup
            atr_ticker = symbol.split("/")[0] if "/" in symbol else symbol
            # Strip options contract notation if present (e.g. AAPL250207C00230000 -> AAPL)
            if len(atr_ticker) > 6 and any(c.isdigit() for c in atr_ticker):
                for i, c in enumerate(atr_ticker):
                    if c.isdigit():
                        atr_ticker = atr_ticker[:i]
                        break
            
            atr_value = _get_atr(atr_ticker)
            
            # Normalize us_option -> option for defaults lookup
            ac_key = "option" if asset_class == "us_option" else asset_class
            dynamic_config = DynamicTrailingConfig(
                enabled=True,
                atr_multiplier=dyn_cfg.get("atr_multiplier", DYNAMIC_TRAIL_DEFAULTS.get(ac_key, {}).get("atr_multiplier", 2.5)),
                activation_atr_mult=dyn_cfg.get("activation_atr_mult", DYNAMIC_TRAIL_DEFAULTS.get(ac_key, {}).get("activation_atr_mult", 0.75)),
                min_trail_pct=dyn_cfg.get("min_trail_pct", DYNAMIC_TRAIL_DEFAULTS.get(ac_key, {}).get("min_trail_pct", 0.5)),
                max_trail_pct=dyn_cfg.get("max_trail_pct", DYNAMIC_TRAIL_DEFAULTS.get(ac_key, {}).get("max_trail_pct", 15.0)),
                tier1_atr_mult=dyn_cfg.get("tier1_atr_mult", 2.0),
                tier1_tighten_factor=dyn_cfg.get("tier1_tighten_factor", 0.75),
                tier2_atr_mult=dyn_cfg.get("tier2_atr_mult", 4.0),
                tier2_tighten_factor=dyn_cfg.get("tier2_tighten_factor", 0.50),
            )
            
            result = self._trailing_stop_mgr.compute_dynamic_trailing(
                symbol=symbol,
                entry_price=entry_price,
                asset_class=asset_class,
                atr_value=atr_value,
                dynamic_cfg=dynamic_config,
                bot_override=dyn_cfg
            )
            
            self._logger.log("dynamic_trailing_selected", {
                "symbol": symbol,
                "asset_class": asset_class,
                "bot": bot_name,
                "atr_value": round(atr_value, 4) if atr_value else None,
                "trail_pct": result.value,
                "activation_pct": result.activation_profit_pct,
                "mode": "dynamic_atr" if atr_value else "fallback"
            })
            
            return result
        
        # Helper: build static config from a bot's trailing_stop section
        def _build_static(ts_cfg: Dict, defaults: Dict = None) -> TrailingStopConfig:
            d = defaults or {}
            return TrailingStopConfig(
                enabled=True,
                mode=ts_cfg.get("mode", d.get("mode", "percent")),
                value=ts_cfg.get("value", d.get("value", 1.0)),
                activation_profit_pct=ts_cfg.get("activation_profit_pct", d.get("activation_profit_pct", 0.3)),
                update_only_if_improves=ts_cfg.get("update_only_if_improves", True),
                epsilon_pct=ts_cfg.get("epsilon_pct", d.get("epsilon_pct", 0.02)),
                exit_order_type=ts_cfg.get("exit_order", {}).get("type", "market")
            )
        
        # First, try to get bot-specific config
        try:
            # Check crypto - first bouncebot, then cryptobot
            if asset_class == "crypto":
                # Check if this is a BounceBot position
                from ..core.state import get_state
                bounce_state = get_state(f"bounce_position_{symbol}")
                if bounce_state:
                    bouncebot = bots_config.get("bouncebot", {})
                    # Try dynamic first
                    dyn_result = _try_dynamic(bouncebot, "bouncebot")
                    if dyn_result:
                        return dyn_result
                    ts_cfg = bouncebot.get("risk", {}).get("trailing_stop", {})
                    if ts_cfg.get("enabled"):
                        return _build_static(ts_cfg, {"value": 0.5, "activation_profit_pct": 0.4})
                
                # Fall back to cryptobot config
                cryptobot = bots_config.get("cryptobot", {})
                dyn_result = _try_dynamic(cryptobot, "cryptobot")
                if dyn_result:
                    return dyn_result
                ts_cfg = cryptobot.get("risk", {}).get("trailing_stop", {})
                if ts_cfg.get("enabled"):
                    return _build_static(ts_cfg, {"value": 1.5, "activation_profit_pct": 0.4, "epsilon_pct": 0.05})
            
            # Check TwentyMinuteBot
            twentymin = bots_config.get("twentyminute_bot", {})
            if twentymin:
                twentymin_tickers = twentymin.get("tickers", [])
                if symbol in twentymin_tickers or not twentymin_tickers:
                    dyn_result = _try_dynamic(twentymin, "twentyminute_bot")
                    if dyn_result:
                        return dyn_result
            
            # Check momentum bots
            momentum_bots = bots_config.get("momentum_bots", [])
            for bot in momentum_bots:
                if bot.get("ticker") == symbol:
                    dyn_result = _try_dynamic(bot, f"mom_{symbol}")
                    if dyn_result:
                        return dyn_result
                    ts_cfg = bot.get("risk", {}).get("trailing_stop", {})
                    if ts_cfg.get("enabled"):
                        return _build_static(ts_cfg, {"value": 0.8, "activation_profit_pct": 0.3})
            
            # Check options bot
            if asset_class in ("option", "us_option"):
                optionsbot = bots_config.get("optionsbot", {})
                dyn_result = _try_dynamic(optionsbot, "optionsbot")
                if dyn_result:
                    return dyn_result
                ts_cfg = optionsbot.get("risk", {}).get("trailing_stop", {})
                if ts_cfg.get("enabled"):
                    return _build_static(ts_cfg, {"value": 2.0, "activation_profit_pct": 0.15, "epsilon_pct": 0.05})
                
                # Also check 0DTE
                optionsbot_0dte = bots_config.get("optionsbot_0dte", {})
                dyn_result = _try_dynamic(optionsbot_0dte, "optionsbot_0dte")
                if dyn_result:
                    return dyn_result
                ts_cfg = optionsbot_0dte.get("risk", {}).get("trailing_stop", {})
                if ts_cfg.get("enabled"):
                    return _build_static(ts_cfg, {"value": 0.5, "activation_profit_pct": 5.0})
        except Exception as e:
            self._logger.error(f"Failed to get bot-specific trailing stop config: {e}")
        
        # Fall back to ExitBot default config
        # Try dynamic at the ExitBot level
        default_dyn = exitbot_config.get("dynamic_trailing", {})
        if default_dyn.get("enabled", False):
            dyn_result = _try_dynamic({"risk": {"dynamic_trailing": default_dyn}}, "exitbot_default")
            if dyn_result:
                return dyn_result
        
        default_ts = exitbot_config.get("default_trailing_stop", {})
        
        return TrailingStopConfig(
            enabled=default_ts.get("enabled", True),
            mode=default_ts.get("mode", "percent"),
            value=default_ts.get("value", 1.0),
            activation_profit_pct=default_ts.get("activation_profit_pct", 0.5),
            update_only_if_improves=default_ts.get("update_only_if_improves", True),
            epsilon_pct=default_ts.get("epsilon_pct", 0.02),
            exit_order_type=default_ts.get("exit_order_type", "market")
        )
    
    def _get_stop_jitter_config(self, asset_class: str) -> Dict[str, Any]:
        """Get stop jitter config from exitbot options_exits (Trade-Bot anti-hunt feature)."""
        try:
            from ..core.config import load_bots_config
            bots = load_bots_config()
            exitbot_cfg = bots.get("exitbot", {})
            if asset_class in ("option", "us_option"):
                return exitbot_cfg.get("options_exits", {}).get("stop_jitter", {"enabled": False})
            return {"enabled": False}
        except Exception:
            return {"enabled": False}

    def _get_asset_class_exit_profile(self, asset_class: str, exitbot_config: Dict) -> Dict[str, Any]:
        """
        Get asset-class-specific exit profile from ExitBot config.
        
        This is the SINGLE SOURCE OF TRUTH for all exit settings.
        Individual bot configs are IGNORED when delegate_exits_to_exitbot is True.
        
        Args:
            asset_class: "us_equity", "crypto", or "option"
            exitbot_config: ExitBot configuration from bots.yaml
            
        Returns:
            Dict with exit profile settings:
            - min_hold_minutes: Minimum hold before normal exits
            - catastrophic_stop_pct: Bypass min_hold if loss exceeds this
            - stop_loss_pct: Normal stop loss percentage
            - take_profit_pct: Normal take profit percentage
            - trailing_stop_value: Trailing stop percentage
            - trailing_activation_pct: Arm trailing stop after this profit
        """
        # Default profiles (conservative)
        defaults = {
            "us_equity": {
                "min_hold_minutes": 5,
                "catastrophic_stop_pct": 30.0,
                "stop_loss_pct": 15.0,
                "take_profit_pct": 25.0,
                "trailing_stop_value": 5.0,
                "trailing_activation_pct": 10.0
            },
            "option": {
                "min_hold_minutes": 15,
                "catastrophic_stop_pct": 70.0,
                "stop_loss_pct": 50.0,
                "take_profit_pct": 75.0,
                "trailing_stop_value": 10.0,
                "trailing_activation_pct": 20.0
            },
            "crypto": {
                "min_hold_minutes": 10,
                "catastrophic_stop_pct": 35.0,
                "stop_loss_pct": 20.0,
                "take_profit_pct": 40.0,
                "trailing_stop_value": 8.0,
                "trailing_activation_pct": 15.0
            }
        }
        
        # Map asset class to config key
        # Normalize: Alpaca returns "us_option" but our configs use "option"
        config_key_map = {
            "us_equity": "stock_exits",
            "option": "options_exits",
            "us_option": "options_exits",
            "crypto": "crypto_exits"
        }
        
        config_key = config_key_map.get(asset_class, "stock_exits")
        
        # Normalize asset_class for defaults lookup
        normalized_ac = "option" if asset_class == "us_option" else asset_class
        
        # Get profile from config, fall back to defaults
        profile = exitbot_config.get(config_key, {})
        default_profile = defaults.get(normalized_ac, defaults["us_equity"])
        
        # Merge with defaults
        result = {
            "min_hold_minutes": profile.get("min_hold_minutes", default_profile["min_hold_minutes"]),
            "catastrophic_stop_pct": profile.get("catastrophic_stop_pct", default_profile["catastrophic_stop_pct"]),
            "stop_loss_pct": profile.get("stop_loss_pct", default_profile["stop_loss_pct"]),
            "take_profit_pct": profile.get("take_profit_pct", default_profile["take_profit_pct"]),
            "trailing_stop_value": profile.get("trailing_stop_value", default_profile["trailing_stop_value"]),
            "trailing_activation_pct": profile.get("trailing_activation_pct", default_profile["trailing_activation_pct"])
        }
        
        self._logger.log("exit_profile_loaded", {
            "asset_class": asset_class,
            "config_key": config_key,
            "profile": result
        })
        
        return result
    
    def _get_hard_stop_config(self, symbol: str, asset_class: str,
                               exitbot_config: Dict, bots_config: Dict,
                               tighten_stops: bool = False) -> HardStopConfig:
        """
        Get hard stop-loss configuration for a position
        
        Hard stop-loss exits the FULL position if it drops X% from entry,
        regardless of trailing stop status. This is the ultimate safety net.
        
        Dynamic adjustments applied:
        - ATR scaling: wider stops for volatile assets
        - VIX regime: tighter stops in high volatility markets
        - ML confidence: wider stops for high-confidence trades
        
        Args:
            symbol: Trading symbol
            asset_class: Asset class
            exitbot_config: ExitBot configuration
            bots_config: Full bots configuration
            tighten_stops: If True, reduce stop by 25% (regime warning active)
            
        Returns:
            HardStopConfig for this position
        """
        # Default hard stop config
        hard_stop = HardStopConfig()
        
        try:
            # Get bot-specific stop_loss_pct from config
            if asset_class == "crypto":
                cryptobot = bots_config.get("cryptobot", {})
                exits = cryptobot.get("exits", {})
                stop_pct = exits.get("stop_loss_pct", 5.0)
                hard_stop.stop_loss_pct = stop_pct
                
            elif asset_class in ("option", "us_option"):
                optionsbot = bots_config.get("optionsbot", {})
                exits = optionsbot.get("exits", {})
                stop_pct = exits.get("stop_loss_pct", 50.0)  # Options can move a lot
                hard_stop.stop_loss_pct = stop_pct
                
            else:  # us_equity
                # Check momentum bots first
                momentum_bots = bots_config.get("momentum_bots", [])
                for bot in momentum_bots:
                    if bot.get("ticker") == symbol:
                        exits = bot.get("exits", {})
                        stop_pct = exits.get("stop_loss_pct", 2.0)
                        hard_stop.stop_loss_pct = stop_pct
                        break
                else:
                    # Default for equities
                    hard_stop.stop_loss_pct = 5.0
            
            # Apply VIX regime adjustment
            if hard_stop.vix_regime_adjust and tighten_stops:
                # Tighten by 25% in high volatility regime
                hard_stop.stop_loss_pct = hard_stop.stop_loss_pct * 0.75
                self._logger.log("hard_stop_vix_tightened", {
                    "symbol": symbol,
                    "original_pct": hard_stop.stop_loss_pct / 0.75,
                    "tightened_pct": hard_stop.stop_loss_pct
                })
                
        except Exception as e:
            self._logger.error(f"Failed to get hard stop config: {e}")
            # Return safe defaults
            hard_stop.stop_loss_pct = 5.0
        
        # Check if hard stop is enabled in exitbot config
        hard_stop_cfg = exitbot_config.get("hard_stop", {})
        hard_stop.enabled = hard_stop_cfg.get("enabled", True)
        
        return hard_stop
    
    def _get_reversal_sense_config(self, symbol: str, asset_class: str,
                                    exitbot_config: Dict, bots_config: Dict) -> ReversalSenseStopConfig:
        """
        Get reversal-sense stop configuration for a position
        
        Reversal-sense exits if price drops X% from high water mark, regardless
        of whether the trailing stop was ever armed. This catches positions that
        went up but came back down before hitting the activation threshold.
        
        Args:
            symbol: Trading symbol
            asset_class: Asset class
            exitbot_config: ExitBot configuration
            bots_config: Full bots configuration
            
        Returns:
            ReversalSenseStopConfig for this position
        """
        # Default config
        config = ReversalSenseStopConfig()
        
        try:
            # Check if reversal sense is configured in exitbot config
            reversal_cfg = exitbot_config.get("reversal_sense", {})
            
            if reversal_cfg:
                config.enabled = reversal_cfg.get("enabled", True)
                config.drop_from_high_pct = reversal_cfg.get("drop_from_high_pct", 1.5)
                config.min_high_water_gain_pct = reversal_cfg.get("min_high_water_gain_pct", 0.5)
                config.apply_to_crypto = reversal_cfg.get("apply_to_crypto", True)
                config.apply_to_stocks = reversal_cfg.get("apply_to_stocks", True)
                config.apply_to_options = reversal_cfg.get("apply_to_options", False)
            
            # Check asset class applicability
            if asset_class == "crypto" and not config.apply_to_crypto:
                config.enabled = False
            elif asset_class in ("option", "us_option") and not config.apply_to_options:
                config.enabled = False
            elif asset_class == "us_equity" and not config.apply_to_stocks:
                config.enabled = False
                
        except Exception as e:
            self._logger.error(f"Failed to get reversal sense config: {e}")
            # Return defaults
            pass
        
        return config
    
    def _check_reversal_sense_stop(self, position_info: PositionInfo, 
                                    ts_state: TrailingStopState,
                                    config: ReversalSenseStopConfig) -> bool:
        """
        Check if reversal-sense stop should trigger
        
        Returns True if:
        1. High water mark was at least min_high_water_gain_pct above entry
        2. Current price has dropped drop_from_high_pct from high water
        3. Trailing stop is NOT armed (if armed, regular trailing stop handles it)
        
        Args:
            position_info: Position information
            ts_state: Trailing stop state (for high_water mark)
            config: Reversal sense configuration
            
        Returns:
            True if reversal-sense stop should trigger
        """
        if not config.enabled:
            return False
            
        # Skip if trailing stop is already armed - let regular trailing stop handle it
        if ts_state.armed:
            return False
        
        entry_price = position_info.entry_price
        current_price = position_info.current_price
        
        # Get high water mark based on position side
        if position_info.side == "long":
            high_water = ts_state.high_water
            
            # Calculate how much price went up from entry (high water gain)
            high_water_gain_pct = ((high_water - entry_price) / entry_price) * 100
            
            # Only trigger if price actually went up meaningfully first
            if high_water_gain_pct < config.min_high_water_gain_pct:
                return False
            
            # Calculate drop from high water mark
            drop_from_high_pct = ((high_water - current_price) / high_water) * 100
            
            # Trigger if drop exceeds threshold
            if drop_from_high_pct >= config.drop_from_high_pct:
                self._logger.log("reversal_sense_triggered", {
                    "symbol": position_info.symbol,
                    "side": position_info.side,
                    "entry": round(entry_price, 4),
                    "high_water": round(high_water, 4),
                    "current": round(current_price, 4),
                    "high_water_gain_pct": round(high_water_gain_pct, 2),
                    "drop_from_high_pct": round(drop_from_high_pct, 2),
                    "threshold_pct": config.drop_from_high_pct,
                    "reason": "price_dropped_from_high_water"
                })
                return True
                
        else:  # short position
            low_water = ts_state.low_water
            
            # For shorts, low_water is the lowest price seen
            low_water_gain_pct = ((entry_price - low_water) / entry_price) * 100
            
            if low_water_gain_pct < config.min_high_water_gain_pct:
                return False
            
            # Drop from low water means price went back UP
            rise_from_low_pct = ((current_price - low_water) / low_water) * 100
            
            if rise_from_low_pct >= config.drop_from_high_pct:
                self._logger.log("reversal_sense_triggered", {
                    "symbol": position_info.symbol,
                    "side": position_info.side,
                    "entry": round(entry_price, 4),
                    "low_water": round(low_water, 4),
                    "current": round(current_price, 4),
                    "low_water_gain_pct": round(low_water_gain_pct, 2),
                    "rise_from_low_pct": round(rise_from_low_pct, 2),
                    "threshold_pct": config.drop_from_high_pct,
                    "reason": "price_rose_from_low_water"
                })
                return True
        
        return False
    
    def _execute_reversal_sense_exit(self, position_info: PositionInfo,
                                      ts_state: TrailingStopState,
                                      config: ReversalSenseStopConfig) -> bool:
        """
        Execute exit for reversal-sense stop trigger
        
        Args:
            position_info: Position information
            ts_state: Trailing stop state
            config: Reversal sense configuration
            
        Returns:
            True if exit was executed successfully
        """
        try:
            # Log the exit attempt
            self._logger.log("reversal_sense_exit_attempt", {
                "symbol": position_info.symbol,
                "side": position_info.side,
                "qty": position_info.qty,
                "entry": position_info.entry_price,
                "current": position_info.current_price,
                "high_water": ts_state.high_water if position_info.side == "long" else ts_state.low_water
            })
            
            # Execute the exit order
            exit_side = "sell" if position_info.side == "long" else "buy"
            
            result = self._alpaca.place_market_order(
                symbol=position_info.symbol,
                side=exit_side,
                qty=abs(position_info.qty)
            )
            
            if result and result.get("id"):
                # Calculate P&L
                if position_info.side == "long":
                    pnl = (position_info.current_price - position_info.entry_price) * position_info.qty
                    pnl_pct = ((position_info.current_price - position_info.entry_price) / position_info.entry_price) * 100
                else:
                    pnl = (position_info.entry_price - position_info.current_price) * position_info.qty
                    pnl_pct = ((position_info.entry_price - position_info.current_price) / position_info.entry_price) * 100
                
                # Record the exit
                exit_record = ExitRecord(
                    symbol=position_info.symbol,
                    side=position_info.side,
                    qty=position_info.qty,
                    entry_price=position_info.entry_price,
                    exit_price=position_info.current_price,
                    pnl=pnl,
                    pnl_percent=pnl_pct,
                    reason="reversal_sense",
                    bot_id=position_info.bot_id,
                    timestamp=datetime.utcnow().isoformat()
                )
                self._recent_exits.append(exit_record)
                self._save_recent_exits()
                
                self._logger.log("reversal_sense_exit_success", {
                    "symbol": position_info.symbol,
                    "order_id": result.get("id"),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "reason": "drop_from_high_water_mark"
                })
                
                # Clean up trailing stop state
                self._trailing_stop_mgr.remove_state(
                    bot_id=position_info.bot_id,
                    position_id=position_info.position_id,
                    symbol=position_info.symbol,
                    asset_class=position_info.asset_class
                )
                
                return True
            else:
                self._logger.error(f"Reversal sense exit failed for {position_info.symbol}")
                return False
                
        except Exception as e:
            self._logger.error(f"Reversal sense exit error for {position_info.symbol}: {e}")
            return False
    
    def _get_take_profit_config(self, symbol: str, asset_class: str,
                                  exitbot_config: Dict, bots_config: Dict) -> TakeProfitConfig:
        """
        Get tiered take-profit configuration for a position
        
        Take-profit tiers allow partial position exits at different profit levels:
        - TP1: First target (e.g., 2%), exit 33% of position
        - TP2: Second target (e.g., 4%), exit 50% of remaining
        - TP3: Third target (e.g., 8%), exit 100% of remaining
        
        After hitting TP1, stop moves to breakeven.
        After hitting TP2, stop moves to TP1 level.
        
        Args:
            symbol: Trading symbol
            asset_class: Asset class
            exitbot_config: ExitBot configuration
            bots_config: Full bots configuration
            
        Returns:
            TakeProfitConfig for this position
        """
        # Default take-profit config based on asset class
        tp_config = TakeProfitConfig()
        
        # Per-bot parabolic runner config (will be loaded below based on asset class)
        bot_runner_cfg = {}
        
        try:
            if asset_class == "crypto":
                # Crypto - higher targets due to volatility
                cryptobot = bots_config.get("cryptobot", {})
                exits = cryptobot.get("exits", {})
                
                # Load crypto-specific parabolic runner config if present
                bot_runner_cfg = cryptobot.get("parabolic_runner", {})
                if bot_runner_cfg.get("enabled", False):
                    # Use crypto-specific TP levels (higher than equities)
                    tp_config.tp1_pct = bot_runner_cfg.get("tp1_pct", 4.0)
                    tp_config.tp2_pct = bot_runner_cfg.get("tp2_pct", 8.0)
                    tp_config.tp3_pct = bot_runner_cfg.get("tp3_pct", 15.0)
                    tp_config.runner_widen_trailing_pct = bot_runner_cfg.get("widen_trailing_pct", 70.0)
                else:
                    # Fallback to scaled take_profit_pct
                    take_profit_pct = exits.get("take_profit_pct", 0.8)
                    tp_config.tp1_pct = take_profit_pct * 0.5
                    tp_config.tp2_pct = take_profit_pct
                    tp_config.tp3_pct = take_profit_pct * 2.0
                
            elif asset_class in ("option", "us_option"):
                options_exits = exitbot_config.get("options_exits", {})
                tiered = options_exits.get("tiered_exits", {})
                if tiered.get("enabled", False):
                    t1_mult = tiered.get("tier1", {}).get("multiplier", 3.0)
                    t2_mult = tiered.get("tier2", {}).get("multiplier", 5.0)
                    t3_mult = tiered.get("tier3_runner", {}).get("multiplier", 25.0)
                    tp_config.tp1_pct = (t1_mult - 1.0) * 100  # 3x = 200%
                    tp_config.tp2_pct = (t2_mult - 1.0) * 100  # 5x = 400%
                    tp_config.tp3_pct = (t3_mult - 1.0) * 100  # 25x = 2400%
                    tp_config.tp1_exit_pct = tiered.get("tier1", {}).get("exit_pct", 50.0) / 100.0
                    tp_config.tp2_exit_pct = tiered.get("tier2", {}).get("exit_pct", 50.0) / 100.0
                    tp_config.tp3_exit_pct = tiered.get("tier3_runner", {}).get("exit_pct", 100.0) / 100.0
                    parabolic = options_exits.get("parabolic_runner", {})
                    if parabolic.get("enabled", False):
                        tp_config.parabolic_runner_enabled = True
                        tp_config.runner_widen_trailing_pct = parabolic.get("initial_trail_pct", 30.0)
                else:
                    optionsbot = bots_config.get("optionsbot", {})
                    exits = optionsbot.get("exits", {})
                    take_profit_pct = exits.get("take_profit_pct", 50.0)
                    tp_config.tp1_pct = take_profit_pct * 0.4
                    tp_config.tp2_pct = take_profit_pct * 0.7
                    tp_config.tp3_pct = take_profit_pct
                
            else:  # us_equity
                # Check momentum bots
                momentum_bots = bots_config.get("momentum_bots", [])
                for bot in momentum_bots:
                    if bot.get("ticker") == symbol:
                        exits = bot.get("exits", {})
                        take_profit_pct = exits.get("take_profit_pct", 10.0)
                        
                        tp_config.tp1_pct = take_profit_pct * 0.3  # 30%
                        tp_config.tp2_pct = take_profit_pct * 0.6  # 60%
                        tp_config.tp3_pct = take_profit_pct        # 100%
                        break
                else:
                    # Default equity targets
                    tp_config.tp1_pct = 2.0
                    tp_config.tp2_pct = 4.0
                    tp_config.tp3_pct = 8.0
                    
        except Exception as e:
            self._logger.error(f"Failed to get take-profit config: {e}")
        
        # Check if take-profit is enabled in exitbot config
        tp_cfg = exitbot_config.get("take_profit", {})
        tp_config.enabled = tp_cfg.get("enabled", True)
        
        # Load parabolic runner mode settings from exitbot config (defaults)
        runner_cfg = tp_cfg.get("parabolic_runner", {})
        tp_config.parabolic_runner_enabled = runner_cfg.get("enabled", True)
        
        # Use per-bot widen_trailing_pct if available, otherwise exitbot default
        if not bot_runner_cfg:
            tp_config.runner_widen_trailing_pct = runner_cfg.get("widen_trailing_pct", 50.0)
        
        # Load adaptive threshold config (per-bot takes precedence)
        adaptive_cfg = bot_runner_cfg.get("adaptive", {}) if bot_runner_cfg else runner_cfg.get("adaptive", {})
        if adaptive_cfg:
            tp_config.adaptive = AdaptiveThresholdConfig(
                enabled=adaptive_cfg.get("enabled", True),
                atr_period=adaptive_cfg.get("atr_period", 14),
                base_atr_mult_tp1=adaptive_cfg.get("base_atr_mult_tp1", 0.5),
                base_atr_mult_tp2=adaptive_cfg.get("base_atr_mult_tp2", 1.0),
                base_atr_mult_tp3=adaptive_cfg.get("base_atr_mult_tp3", 2.0),
                min_tp1_pct=adaptive_cfg.get("min_tp1_pct", 1.5),
                max_tp1_pct=adaptive_cfg.get("max_tp1_pct", 4.0),
                min_tp2_pct=adaptive_cfg.get("min_tp2_pct", 3.0),
                max_tp2_pct=adaptive_cfg.get("max_tp2_pct", 8.0),
                min_tp3_pct=adaptive_cfg.get("min_tp3_pct", 6.0),
                max_tp3_pct=adaptive_cfg.get("max_tp3_pct", 15.0),
            )
        
        return tp_config
    
    def _apply_adaptive_thresholds(self, config: TakeProfitConfig, 
                                    symbol: str, entry_price: float) -> TakeProfitConfig:
        """
        Apply ATR-based adaptive thresholds to take-profit config.
        
        Adaptive thresholds auto-adjust TP levels based on current volatility:
        - High volatility (high ATR%) -> wider TP levels to capture larger moves
        - Low volatility (low ATR%) -> tighter TP levels to lock in smaller moves
        
        Formula: TP = base_TP + (ATR% * multiplier), clamped to min/max bounds
        
        Args:
            config: Base take-profit configuration
            symbol: Trading symbol for ATR lookup
            entry_price: Position entry price
            
        Returns:
            Modified TakeProfitConfig with adaptive thresholds
        """
        if not config.adaptive.enabled:
            return config
        
        try:
            # Get current ATR for the symbol
            from .bar_cache import get_bar_cache
            bar_cache = get_bar_cache()
            
            # Get recent bars for ATR calculation
            atr_period = config.adaptive.atr_period
            bars = bar_cache.get_cached_bars(symbol, limit=atr_period + 1)
            
            if not bars or len(bars) < atr_period:
                # Not enough data - use base config
                self._logger.log("adaptive_atr_skip", {"symbol": symbol, "reason": "not_enough_bars"})
                return config
            
            # Calculate ATR - CachedBar is a dataclass with attributes
            atr_sum = 0.0
            for i in range(1, len(bars)):
                bar = bars[i]
                prev_bar = bars[i-1]
                high = getattr(bar, 'high', 0) or 0
                low = getattr(bar, 'low', 0) or 0
                prev_close = getattr(prev_bar, 'close', 0) or 0
                
                if high and low and prev_close:
                    tr = max(
                        high - low,
                        abs(high - prev_close),
                        abs(low - prev_close)
                    )
                    atr_sum += tr
            
            atr = atr_sum / (len(bars) - 1) if len(bars) > 1 else 0
            
            if atr <= 0 or entry_price <= 0:
                return config
            
            # Convert ATR to percentage of entry price
            atr_pct = (atr / entry_price) * 100
            
            # Calculate adaptive thresholds with clamping
            adaptive = config.adaptive
            
            # TP1: base + (ATR% * mult), clamped to bounds
            new_tp1 = config.tp1_pct + (atr_pct * adaptive.base_atr_mult_tp1)
            new_tp1 = max(adaptive.min_tp1_pct, min(adaptive.max_tp1_pct, new_tp1))
            
            # TP2: base + (ATR% * mult), clamped to bounds
            new_tp2 = config.tp2_pct + (atr_pct * adaptive.base_atr_mult_tp2)
            new_tp2 = max(adaptive.min_tp2_pct, min(adaptive.max_tp2_pct, new_tp2))
            
            # TP3: base + (ATR% * mult), clamped to bounds
            new_tp3 = config.tp3_pct + (atr_pct * adaptive.base_atr_mult_tp3)
            new_tp3 = max(adaptive.min_tp3_pct, min(adaptive.max_tp3_pct, new_tp3))
            
            # Log adaptive adjustment
            self._logger.log("adaptive_thresholds_applied", {
                "symbol": symbol,
                "atr": round(atr, 4),
                "atr_pct": round(atr_pct, 2),
                "base_tp1": config.tp1_pct,
                "base_tp2": config.tp2_pct,
                "base_tp3": config.tp3_pct,
                "adaptive_tp1": round(new_tp1, 2),
                "adaptive_tp2": round(new_tp2, 2),
                "adaptive_tp3": round(new_tp3, 2)
            })
            
            # Update config with adaptive values
            config.tp1_pct = new_tp1
            config.tp2_pct = new_tp2
            config.tp3_pct = new_tp3
            
        except Exception as e:
            self._logger.error(f"Adaptive threshold calculation failed for {symbol}: {e}")
        
        return config
    
    def _check_hard_stop_loss(self, position: PositionInfo, pnl_pct: float,
                               config: HardStopConfig) -> bool:
        """
        Check if hard stop-loss should trigger
        
        This is the absolute safety net - exits if position drops X% from entry,
        regardless of trailing stop activation status.
        
        Args:
            position: Position information
            pnl_pct: Current profit/loss percentage (negative = loss)
            config: Hard stop configuration
            
        Returns:
            True if hard stop should trigger exit
        """
        if not config.enabled:
            return False
        
        import random
        stop_pct = config.stop_loss_pct
        jitter_cfg = self._get_stop_jitter_config(position.asset_class)
        if jitter_cfg.get("enabled", False):
            max_j = jitter_cfg.get("max_jitter_pct", 1.5)
            jitter = random.uniform(-max_j, max_j)
            stop_pct = stop_pct + jitter
        
        if pnl_pct <= -stop_pct:
            self._logger.log("hard_stop_triggered", {
                "symbol": position.symbol,
                "side": position.side,
                "entry": round(position.entry_price, 4),
                "current": round(position.current_price, 4),
                "pnl_pct": round(pnl_pct, 2),
                "hard_stop_pct": config.stop_loss_pct,
                "pnl_usd": round(position.unrealized_pnl, 2)
            })
            return True
        
        return False
    
    def _execute_hard_stop_exit(self, position: PositionInfo, pnl_pct: float,
                                 config: HardStopConfig) -> bool:
        """
        Execute hard stop-loss exit (full position)
        
        Args:
            position: Position information
            pnl_pct: Current profit/loss percentage
            config: Hard stop configuration
            
        Returns:
            True if exit was executed successfully
        """
        # Check for exit lock to prevent duplicate orders
        if self._trailing_stop_mgr.has_exit_lock(
            position.bot_id, position.position_id,
            position.symbol, position.asset_class
        ):
            self._logger.log("hard_stop_exit_locked", {
                "symbol": position.symbol,
                "reason": "exit_already_pending"
            })
            return False
        
        # Generate client_order_id for tracking
        client_order_id = f"hard_stop_{position.symbol}_{int(time.time() * 1000)}"
        
        self._logger.log("hard_stop_exit_executing", {
            "symbol": position.symbol,
            "side": position.side,
            "qty": position.qty,
            "entry": round(position.entry_price, 4),
            "current": round(position.current_price, 4),
            "pnl_pct": round(pnl_pct, 2),
            "pnl_usd": round(position.unrealized_pnl, 2),
            "client_order_id": client_order_id
        })
        
        try:
            # Set exit lock to prevent duplicate orders
            self._trailing_stop_mgr.set_exit_lock(
                position.bot_id, position.position_id,
                position.symbol, position.asset_class,
                client_order_id
            )
            
            # Cancel staged orders before manual exit
            self.cancel_staged_orders(position.position_id, "hard_stop")
            
            # Execute full position close (with spread protection)
            close_ok = self._close_with_spread_protection(position, "hard_stop")
            
            if close_ok:
                self._logger.log("hard_stop_exit_success", {
                    "symbol": position.symbol,
                    "pnl_pct": round(pnl_pct, 2),
                    "pnl_usd": round(position.unrealized_pnl, 2)
                })
                
                # Record exit
                self._add_exit_record(position, "hard_stop")
            
            # Clean up trailing stop state
            self._trailing_stop_mgr.remove_state(
                position.bot_id, position.position_id,
                position.symbol, position.asset_class
            )
            
            # Clean up TP tiers tracking
            if position.position_id in self._tp_tiers_hit:
                del self._tp_tiers_hit[position.position_id]
                self._save_tp_tiers_state()
            
            return True
            
        except Exception as e:
            self._logger.error(f"Hard stop exit failed: {e}", symbol=position.symbol)
            # Clear exit lock on exception
            self._trailing_stop_mgr.clear_exit_lock(
                position.bot_id, position.position_id,
                position.symbol, position.asset_class
            )
            return False
    
    def _check_and_execute_take_profit(self, position: PositionInfo, pnl_pct: float,
                                         config: TakeProfitConfig) -> Dict[str, Any]:
        """
        Check and execute tiered take-profit exits
        
        This handles partial position exits at different profit levels:
        - TP1: Exit 33% of position, move stop to breakeven
        - TP2: Exit 50% of remaining, move stop to TP1 level
        - TP3: Exit 100% of remaining
        
        Args:
            position: Position information
            pnl_pct: Current profit percentage
            config: Take-profit configuration
            
        Returns:
            Dict with exit_triggered (bool) and full_exit (bool)
        """
        result = {"exit_triggered": False, "full_exit": False}
        
        if not config.enabled or pnl_pct <= 0:
            return result
        
        # Apply ATR-based adaptive thresholds if enabled
        # This auto-adjusts TP levels based on current volatility
        if config.adaptive.enabled:
            config = self._apply_adaptive_thresholds(
                config, position.symbol, position.entry_price
            )
        
        # Get previously hit tiers for this position
        position_id = position.position_id
        tiers_hit = self._tp_tiers_hit.get(position_id, set())
        
        # =====================================================================
        # PARABOLIC RUNNER MODE CHECK
        # =====================================================================
        # If TP2 has been hit and runner mode is enabled, skip TP3 entirely.
        # Let the trailing stop (which is now at TP1 level) ride the extended move.
        # This captures parabolic runs instead of exiting too early at TP3.
        if config.parabolic_runner_enabled and 2 in tiers_hit and 3 not in tiers_hit:
            # Runner mode is active - log and skip TP3
            self._logger.log("parabolic_runner_active", {
                "symbol": position.symbol,
                "pnl_pct": round(pnl_pct, 2),
                "tp2_pct": config.tp2_pct,
                "tp3_pct_skipped": config.tp3_pct,
                "action": "trailing_stop_rides",
                "widen_pct": config.runner_widen_trailing_pct
            })
            
            # Widen the trailing stop to give parabolic moves more room
            self._widen_trailing_stop_for_runner(position, config)
            
            # Don't check TP3 - let trailing stop handle the exit
            return result
        
        # Check each tier in order
        tier_checks = [
            (3, config.tp3_pct, config.tp3_exit_pct, "tp3"),
            (2, config.tp2_pct, config.tp2_exit_pct, "tp2"),
            (1, config.tp1_pct, config.tp1_exit_pct, "tp1"),
        ]
        
        for tier_num, target_pct, exit_pct, reason in tier_checks:
            # Skip if already hit this tier
            if tier_num in tiers_hit:
                continue
            
            # Check if profit exceeds tier target
            if pnl_pct >= target_pct:
                self._logger.log("take_profit_tier_hit", {
                    "symbol": position.symbol,
                    "tier": tier_num,
                    "target_pct": target_pct,
                    "actual_pnl_pct": round(pnl_pct, 2),
                    "exit_pct": exit_pct
                })
                
                # Execute partial or full exit
                exit_success = self._execute_take_profit_exit(
                    position, tier_num, exit_pct, reason, pnl_pct, config
                )
                
                if exit_success:
                    # Mark tier as hit
                    if position_id not in self._tp_tiers_hit:
                        self._tp_tiers_hit[position_id] = set()
                    self._tp_tiers_hit[position_id].add(tier_num)
                    self._save_tp_tiers_state()
                    
                    result["exit_triggered"] = True
                    result["full_exit"] = (exit_pct >= 1.0 or tier_num == 3)
                    
                    # Only process one tier per iteration
                    break
        
        return result
    
    def _execute_take_profit_exit(self, position: PositionInfo, tier: int,
                                   exit_pct: float, reason: str,
                                   pnl_pct: float, config: TakeProfitConfig) -> bool:
        """
        Execute a take-profit exit (partial or full)
        
        Args:
            position: Position information
            tier: Which tier (1, 2, or 3)
            exit_pct: Percentage of position to exit (0.33, 0.50, or 1.0)
            reason: Exit reason for logging ("tp1", "tp2", "tp3")
            pnl_pct: Current profit percentage
            config: Take-profit configuration
            
        Returns:
            True if exit was executed successfully
        """
        # Check for exit lock to prevent duplicate orders (only for full exits)
        # Partial exits (TP1, TP2) are allowed even if there's a pending order
        if exit_pct >= 1.0 and self._trailing_stop_mgr.has_exit_lock(
            position.bot_id, position.position_id,
            position.symbol, position.asset_class
        ):
            self._logger.log("take_profit_exit_locked", {
                "symbol": position.symbol,
                "tier": tier,
                "reason": "exit_already_pending"
            })
            return False
        
        # Calculate quantity to exit
        exit_qty = position.qty * exit_pct
        
        # Round for crypto or fractional shares
        if position.asset_class == "crypto":
            # Keep 8 decimals for crypto
            exit_qty = round(exit_qty, 8)
        else:
            # Round to 2 decimals for equities
            exit_qty = round(exit_qty, 2)
        
        # Ensure minimum quantity
        if exit_qty < 0.001:
            self._logger.log("take_profit_qty_too_small", {
                "symbol": position.symbol,
                "tier": tier,
                "calculated_qty": exit_qty
            })
            return False
        
        self._logger.log("take_profit_exit_executing", {
            "symbol": position.symbol,
            "tier": tier,
            "exit_pct": exit_pct,
            "exit_qty": exit_qty,
            "total_qty": position.qty,
            "pnl_pct": round(pnl_pct, 2)
        })
        
        try:
            # For full exit, use close_position with spread protection
            if exit_pct >= 1.0:
                # Cancel staged orders before full exit
                self.cancel_staged_orders(position.position_id, "take_profit")
                close_ok = self._close_with_spread_protection(position, "take_profit")
                if not close_ok:
                    self._logger.error(f"Take profit full exit failed for {position.symbol}")
                    return False
                result = {"id": "take_profit_close"}
            else:
                # For partial exit, use place_market_order for the exit quantity
                side = "sell" if position.side == "long" else "buy"
                result = self._alpaca.place_market_order(
                    symbol=position.symbol,
                    side=side,
                    qty=exit_qty
                )
            
            self._logger.log("take_profit_exit_success", {
                "symbol": position.symbol,
                "tier": tier,
                "order_id": result.get("id", "unknown"),
                "exit_qty": exit_qty,
                "pnl_pct": round(pnl_pct, 2)
            })
            
            # Record exit with tier-specific reason
            self._add_exit_record(position, reason)
            
            # After TP1: Move trailing stop to breakeven
            if tier == 1 and config.move_stop_after_tp1 == "breakeven":
                self._adjust_stop_to_breakeven(position)
            
            # After TP2: Move trailing stop to TP1 level
            if tier == 2 and config.move_stop_after_tp2 == "tp1":
                self._adjust_stop_to_tp1_level(position, config)
            
            # Full exit cleanup
            if exit_pct >= 1.0:
                self._trailing_stop_mgr.remove_state(
                    position.bot_id, position.position_id,
                    position.symbol, position.asset_class
                )
                if position.position_id in self._tp_tiers_hit:
                    del self._tp_tiers_hit[position.position_id]
                    self._save_tp_tiers_state()
            
            return True
            
        except Exception as e:
            self._logger.error(f"Take-profit exit failed: {e}", symbol=position.symbol, tier=tier)
            return False
    
    def _adjust_stop_to_breakeven(self, position: PositionInfo) -> None:
        """
        Adjust trailing stop to breakeven (entry price) after hitting TP1
        
        This locks in the trade at no-loss after first profit target is hit.
        """
        try:
            ts_state = self._trailing_stop_mgr.load_state(
                bot_id=position.bot_id,
                position_id=position.position_id,
                symbol=position.symbol,
                asset_class=position.asset_class
            )
            
            if ts_state is not None:
                # Move stop to entry price (breakeven)
                old_stop = ts_state.stop_price
                ts_state.stop_price = position.entry_price
                ts_state.armed = True  # Ensure it's armed
                
                # Use persist_state to save WITHOUT recalculating stop price
                # (update_state would override our manually-set breakeven stop)
                self._trailing_stop_mgr.persist_state(
                    bot_id=position.bot_id,
                    position_id=position.position_id,
                    symbol=position.symbol,
                    asset_class=position.asset_class,
                    state=ts_state
                )
                
                self._logger.log("stop_moved_to_breakeven", {
                    "symbol": position.symbol,
                    "entry_price": round(position.entry_price, 4),
                    "old_stop": round(old_stop, 4) if old_stop else 0,
                    "new_stop": round(ts_state.stop_price, 4)
                })
                
        except Exception as e:
            self._logger.error(f"Failed to move stop to breakeven: {e}")
    
    def _adjust_stop_to_tp1_level(self, position: PositionInfo,
                                   config: TakeProfitConfig) -> None:
        """
        Adjust trailing stop to TP1 level after hitting TP2
        
        This locks in profit at the first target level.
        """
        try:
            ts_state = self._trailing_stop_mgr.load_state(
                bot_id=position.bot_id,
                position_id=position.position_id,
                symbol=position.symbol,
                asset_class=position.asset_class
            )
            
            if ts_state is not None:
                # Calculate TP1 price level
                if position.side == "long":
                    tp1_price = position.entry_price * (1 + config.tp1_pct / 100)
                else:
                    tp1_price = position.entry_price * (1 - config.tp1_pct / 100)
                
                # Move stop to TP1 level
                old_stop = ts_state.stop_price
                ts_state.stop_price = tp1_price
                ts_state.armed = True
                
                # Use persist_state to save WITHOUT recalculating stop price
                # (update_state would override our manually-set TP1 stop)
                self._trailing_stop_mgr.persist_state(
                    bot_id=position.bot_id,
                    position_id=position.position_id,
                    symbol=position.symbol,
                    asset_class=position.asset_class,
                    state=ts_state
                )
                
                self._logger.log("stop_moved_to_tp1", {
                    "symbol": position.symbol,
                    "entry_price": round(position.entry_price, 4),
                    "tp1_pct": config.tp1_pct,
                    "old_stop": round(old_stop, 4) if old_stop else 0,
                    "new_stop": round(tp1_price, 4)
                })
                
        except Exception as e:
            self._logger.error(f"Failed to move stop to TP1 level: {e}")
    
    def _widen_trailing_stop_for_runner(self, position: PositionInfo,
                                          config: TakeProfitConfig) -> None:
        """
        Widen the trailing stop to give parabolic moves more room.
        
        When runner mode activates after TP2, we widen the trailing stop by
        a configurable percentage to let the move extend further before
        triggering an exit.
        
        Example: If trailing stop is 2% and widen_pct is 50%, new stop is 3%.
        """
        try:
            ts_state = self._trailing_stop_mgr.load_state(
                bot_id=position.bot_id,
                position_id=position.position_id,
                symbol=position.symbol,
                asset_class=position.asset_class
            )
            
            if ts_state is None:
                return
            
            # Check if we've already widened for this position (avoid repeated widening)
            runner_key = f"runner_widened_{position.position_id}"
            if get_state(runner_key):
                return  # Already widened
            
            # Calculate widened stop distance
            old_stop = ts_state.stop_price
            widen_factor = 1.0 + (config.runner_widen_trailing_pct / 100.0)
            
            if position.side == "long":
                # For long positions, lower the stop price to give more room
                current_distance = ts_state.high_water - old_stop
                new_distance = current_distance * widen_factor
                new_stop = ts_state.high_water - new_distance
            else:
                # For short positions, raise the stop price
                current_distance = old_stop - ts_state.high_water
                new_distance = current_distance * widen_factor
                new_stop = ts_state.high_water + new_distance
            
            ts_state.stop_price = new_stop
            
            # Persist the widened stop
            self._trailing_stop_mgr.persist_state(
                bot_id=position.bot_id,
                position_id=position.position_id,
                symbol=position.symbol,
                asset_class=position.asset_class,
                state=ts_state
            )
            
            # Mark as widened to prevent repeated widening
            set_state(runner_key, True)
            
            self._logger.log("runner_trailing_stop_widened", {
                "symbol": position.symbol,
                "old_stop": round(old_stop, 4),
                "new_stop": round(new_stop, 4),
                "high_water": round(ts_state.high_water, 4),
                "widen_pct": config.runner_widen_trailing_pct,
                "current_price": round(position.current_price, 4)
            })
            
        except Exception as e:
            self._logger.error(f"Failed to widen trailing stop for runner: {e}")
    
    def _save_tp_tiers_state(self) -> None:
        """Save take-profit tiers state to persistent storage"""
        # Convert sets to lists for JSON serialization
        state_dict = {k: list(v) for k, v in self._tp_tiers_hit.items()}
        set_state("exitbot.tp_tiers_hit", state_dict)
    
    def _execute_trailing_stop_exit(self, position: PositionInfo,
                                     ts_state: TrailingStopState) -> bool:
        """
        Execute an exit when trailing stop is triggered
        
        Args:
            position: Position information
            ts_state: Trailing stop state
            
        Returns:
            True if exit was executed successfully
        """
        # Check for exit lock to prevent duplicate orders
        if self._trailing_stop_mgr.has_exit_lock(
            position.bot_id, position.position_id,
            position.symbol, position.asset_class
        ):
            self._logger.log("trailing_stop_exit_locked", {
                "symbol": position.symbol,
                "position_id": position.position_id
            })
            return False
        
        self._logger.log("trailing_stop_exit_triggered", {
            "symbol": position.symbol,
            "side": position.side,
            "qty": position.qty,
            "entry_price": position.entry_price,
            "current_price": position.current_price,
            "stop_price": ts_state.stop_price,
            "high_water": ts_state.high_water,
            "bot_id": position.bot_id
        })
        
        # Determine exit side (opposite of position side)
        exit_side = "sell" if position.side == "long" else "buy"
        
        # Generate client order ID for tracking
        client_order_id = f"ts_exit_{position.symbol}_{int(time.time())}"
        
        # Set exit lock
        self._trailing_stop_mgr.set_exit_lock(
            position.bot_id, position.position_id,
            position.symbol, position.asset_class,
            client_order_id
        )
        
        # Cancel staged orders before trailing stop exit
        self.cancel_staged_orders(position.position_id, "trailing_stop")
        
        # Execute the exit order with spread protection (works for all asset types)
        try:
            close_ok = self._close_with_spread_protection(position, "trailing_stop")
            
            if not close_ok:
                self._logger.error(f"Trailing stop exit failed for {position.symbol}")
                return False
            
            self._logger.log("trailing_stop_exit_success", {
                "symbol": position.symbol,
                "client_order_id": client_order_id
            })
            
            # Add to recent exits for display
            self._add_exit_record(position, "trailing_stop")
            
            # Record the trade for analytics
            self._record_exit_trade(position, ts_state)
            
            # Remove trailing stop state
            self._trailing_stop_mgr.remove_state(
                position.bot_id, position.position_id,
                position.symbol, position.asset_class
            )
            
            return True
                
        except Exception as e:
            self._logger.error(f"Trailing stop exit exception: {e}")
            
            # Clear exit lock on exception
            self._trailing_stop_mgr.clear_exit_lock(
                position.bot_id, position.position_id,
                position.symbol, position.asset_class
            )
            
            return False
    
    def _check_news_exit(self, position: PositionInfo, intel_config: Dict[str, Any],
                          dry_run: bool = False) -> Optional[Dict[str, Any]]:
        """
        Check if news sentiment warrants an exit for this position
        
        This is an ADDITIONAL exit trigger - it does not replace trailing stops.
        Fail-closed: returns None if intel unavailable or confidence too low.
        
        Args:
            position: Position information
            intel_config: Intelligence configuration from bots.yaml
            dry_run: If True, log but don't execute exit
            
        Returns:
            Dict with exit decision info, or None if no exit warranted
        """
        news_config = intel_config.get("news", {})
        exits_config = news_config.get("exits", {})
        
        # Gate 1: Check if news exits are enabled
        if not news_config.get("enabled", False):
            return None
        if not exits_config.get("enabled", False):
            return None
        
        # Gate 2: Get news and sentiment
        try:
            news_intel = get_news_intelligence()
            sentiment_scorer = get_sentiment_scorer()
            
            # Fetch news for this symbol
            news_items = news_intel.get_news_for_symbol(position.symbol)
            
            if not news_items:
                self._logger.log("news_exit_check", {
                    "symbol": position.symbol,
                    "action": "NONE",
                    "reason": "no_news_available",
                    "gates": {"news_available": False}
                })
                return None
            
            # Score sentiment
            sentiment = sentiment_scorer.score_news(news_items)
            
        except Exception as e:
            self._logger.log("news_exit_check", {
                "symbol": position.symbol,
                "action": "NONE",
                "reason": f"intel_error: {e}",
                "gates": {"intel_available": False}
            })
            return None  # Fail-closed
        
        # Gate 3: Check cache freshness using context-aware staleness
        cache_status = news_intel.get_cache_status(position.symbol)
        cache_age_s = cache_status.age_seconds() if cache_status else 999999
        
        # Use staleness service for context-aware TTL (tighter during market hours)
        staleness = get_data_staleness()
        staleness_info = staleness.get_staleness_info(DataType.NEWS, cache_age_s)
        
        if staleness_info["is_stale"]:
            self._logger.log("news_exit_check", {
                "symbol": position.symbol,
                "action": "NONE",
                "reason": "cache_stale",
                "cache_age_s": round(cache_age_s, 1),
                "ttl_s": staleness_info["ttl_seconds"],
                "session_phase": staleness_info["session_phase"],
                "gates": {"cache_fresh": False}
            })
            return None  # Fail-closed on stale cache
        
        # Gate 4: Check confidence threshold
        min_confidence = exits_config.get("min_confidence", 0.60)
        if sentiment.confidence < min_confidence:
            self._logger.log("news_exit_check", {
                "symbol": position.symbol,
                "sentiment": round(sentiment.sentiment_score, 3),
                "confidence": round(sentiment.confidence, 3),
                "flags": sentiment.flags,
                "action": "NONE",
                "reason": "confidence_too_low",
                "gates": {"confidence_ok": False, "min_confidence": min_confidence}
            })
            return None  # Fail-closed on low confidence
        
        # Calculate position P&L
        if position.side == "long":
            pnl_pct = ((position.current_price - position.entry_price) / position.entry_price) * 100
        else:
            pnl_pct = ((position.entry_price - position.current_price) / position.entry_price) * 100
        
        is_profitable = pnl_pct > 0
        
        # Get thresholds
        negative_threshold = exits_config.get("negative_threshold", -0.70)
        severe_threshold = exits_config.get("severe_threshold", -0.85)
        profit_exit_requires_profit = exits_config.get("profit_exit_requires_profit", True)
        loss_exit_on_severe = exits_config.get("loss_exit_on_severe", True)
        
        # Determine if exit should be triggered
        should_exit = False
        exit_reason = ""
        
        is_negative = sentiment.sentiment_score <= negative_threshold
        is_severe = sentiment.sentiment_score <= severe_threshold
        
        if is_severe and loss_exit_on_severe:
            # Severe negative - exit even if losing
            should_exit = True
            exit_reason = f"SEVERE_NEGATIVE: sentiment={sentiment.sentiment_score:.2f}, flags={sentiment.flags}"
        elif is_negative:
            if is_profitable:
                # Negative and profitable - exit to lock in gains
                should_exit = True
                exit_reason = f"NEGATIVE_PROFITABLE: sentiment={sentiment.sentiment_score:.2f}, pnl={pnl_pct:.2f}%"
            elif not profit_exit_requires_profit:
                # Negative and loss allowed
                should_exit = True
                exit_reason = f"NEGATIVE_LOSS_ALLOWED: sentiment={sentiment.sentiment_score:.2f}"
        
        # Log the check
        self._logger.log("news_exit_check", {
            "symbol": position.symbol,
            "sentiment": round(sentiment.sentiment_score, 3),
            "confidence": round(sentiment.confidence, 3),
            "flags": sentiment.flags,
            "reason_short": sentiment.reason_short,
            "pnl_pct": round(pnl_pct, 2),
            "is_profitable": is_profitable,
            "cache_age_s": round(cache_age_s, 1),
            "thresholds": {
                "negative": negative_threshold,
                "severe": severe_threshold
            },
            "gates": {
                "enabled": True,
                "cache_fresh": True,
                "confidence_ok": True,
                "is_negative": is_negative,
                "is_severe": is_severe
            },
            "action": "EXIT" if should_exit else "NONE",
            "reason": exit_reason if should_exit else "thresholds_not_met",
            "dry_run": dry_run
        })
        
        if should_exit:
            return {
                "should_exit": True,
                "reason": exit_reason,
                "sentiment": sentiment,
                "dry_run": dry_run
            }
        
        return None
    
    def _execute_news_exit(self, position: PositionInfo, exit_info: Dict[str, Any]) -> bool:
        """
        Execute an exit triggered by news sentiment
        
        Args:
            position: Position information
            exit_info: Exit decision info from _check_news_exit
            
        Returns:
            True if exit was executed successfully
        """
        # Check for exit lock (prevent duplicates with trailing stop)
        if self._trailing_stop_mgr.has_exit_lock(
            position.bot_id, position.position_id,
            position.symbol, position.asset_class
        ):
            self._logger.log("news_exit_locked", {
                "symbol": position.symbol,
                "reason": "exit_already_pending"
            })
            return False
        
        if exit_info.get("dry_run", False):
            self._logger.log("news_exit_dry_run", {
                "symbol": position.symbol,
                "reason": exit_info.get("reason", ""),
                "sentiment": exit_info.get("sentiment", {})
            })
            return False
        
        self._logger.log("news_exit_triggered", {
            "symbol": position.symbol,
            "side": position.side,
            "qty": position.qty,
            "entry_price": position.entry_price,
            "current_price": position.current_price,
            "reason": exit_info.get("reason", ""),
            "bot_id": position.bot_id
        })
        
        # Generate client order ID for tracking
        client_order_id = f"news_exit_{position.symbol}_{int(time.time())}"
        
        # Set exit lock to prevent duplicate orders
        self._trailing_stop_mgr.set_exit_lock(
            position.bot_id, position.position_id,
            position.symbol, position.asset_class,
            client_order_id
        )
        
        # Cancel staged orders before news-driven exit
        self.cancel_staged_orders(position.position_id, "news_sentiment")
        
        # Execute the exit order with spread protection
        try:
            close_ok = self._close_with_spread_protection(position, "news_sentiment")
            
            if not close_ok:
                self._logger.error(f"News sentiment exit failed for {position.symbol}")
                return False
            
            self._logger.log("news_exit_success", {
                "symbol": position.symbol,
                "client_order_id": client_order_id
            })
            
            # Add to recent exits for display
            self._add_exit_record(position, "news_sentiment")
            
            # Remove trailing stop state if exists
            self._trailing_stop_mgr.remove_state(
                position.bot_id, position.position_id,
                position.symbol, position.asset_class
            )
            
            return True
            
        except Exception as e:
            self._logger.error(f"News exit exception: {e}")
            
            # Clear exit lock on exception
            self._trailing_stop_mgr.clear_exit_lock(
                position.bot_id, position.position_id,
                position.symbol, position.asset_class
            )
            
            return False
    
    def _record_exit_trade(self, position: PositionInfo, ts_state: TrailingStopState) -> None:
        """
        Record an exit trade for analytics
        
        Args:
            position: Position information
            ts_state: Trailing stop state at exit
        """
        try:
            # Calculate P&L
            if position.side == "long":
                pnl = (position.current_price - position.entry_price) * position.qty
            else:
                pnl = (position.entry_price - position.current_price) * position.qty
            
            # Record trade
            trade_record = {
                "timestamp": time.time(),
                "bot_id": position.bot_id,
                "symbol": position.symbol,
                "side": position.side,
                "qty": position.qty,
                "entry_price": position.entry_price,
                "exit_price": position.current_price,
                "pnl": pnl,
                "exit_reason": "trailing_stop",
                "high_water": ts_state.high_water
            }
            
            # Add to trade history
            trades = get_state("trades", [])
            if not isinstance(trades, list):
                trades = []
            trades.append(trade_record)
            set_state("trades", trades)
            
            # Update stats
            if pnl >= 0:
                wins = get_state("stats.wins", 0) + 1
                set_state("stats.wins", wins)
                total_profit = get_state("stats.total_profit", 0) + pnl
                set_state("stats.total_profit", total_profit)
            else:
                losses = get_state("stats.losses", 0) + 1
                set_state("stats.losses", losses)
                total_loss = get_state("stats.total_loss", 0) + abs(pnl)
                set_state("stats.total_loss", total_loss)
            
            self._logger.log("trade_recorded", trade_record)
            
            # Record exit for strategy system kill-switch (per-strategy drawdown tracking)
            # Resolve strategy_id from position metadata or bot_id
            strategy_id = self._resolve_strategy_id(position)
            if strategy_id:
                try:
                    from ..strategy.registry import StrategyRegistry
                    registry = StrategyRegistry()
                    strategy_cfg = registry.get(strategy_id).data
                    self._strategy_kill_switch.record_exit(strategy_id, pnl, strategy_cfg)
                    self._logger.log("strategy_kill_switch_record", {
                        "strategy_id": strategy_id,
                        "pnl": pnl
                    })
                except KeyError:
                    pass
                except Exception as e:
                    self._logger.warn(f"Strategy kill-switch record failed: {e}")
            
            try:
                if self._session_protection is not None:
                    self._session_protection.record_trade_pnl(pnl, position.symbol, "trailing_stop")
            except Exception as sp_err:
                self._logger.error(f"SessionProtection record failed in _record_exit_trade (fail-open): {sp_err}")
            
        except Exception as e:
            self._logger.error(f"Failed to record exit trade: {e}")
    
    def _resolve_strategy_id(self, position: PositionInfo) -> Optional[str]:
        """
        Resolve strategy_id from position metadata.
        
        Checks multiple state keys for strategy_id:
        1. position.{position_id}.strategy_id
        2. position.{symbol}.strategy_id (for options by contract symbol)
        3. bot_id prefix "strategy:" (for tagged bot executions)
        
        Returns None if not found (manual trades or bots not using strategy system).
        
        Args:
            position: Position information
            
        Returns:
            Strategy ID string or None
        """
        try:
            position_state_key = f"position.{position.position_id}.strategy_id"
            strategy_id = get_state(position_state_key, None)
            if strategy_id:
                return str(strategy_id)
            
            symbol_state_key = f"position.{position.symbol}.strategy_id"
            strategy_id = get_state(symbol_state_key, None)
            if strategy_id:
                return str(strategy_id)
            
            bot_id = position.bot_id or ""
            if bot_id.startswith("strategy:"):
                return bot_id.replace("strategy:", "")
            
            return None
        except Exception:
            return None
    
    def _save_known_positions(self) -> None:
        """Save known positions to state for restart resilience"""
        set_state("exitbot.known_positions", list(self._known_positions))
    
    def _load_position_first_seen(self):
        """Load position first-seen timestamps from persistent state"""
        from ..core.state import get_state
        saved = get_state("exitbot.position_first_seen")
        if saved and isinstance(saved, dict):
            self._position_first_seen = saved
            self._logger.log("position_first_seen_loaded", {"count": len(saved)})

    def _save_position_first_seen(self):
        """Persist position first-seen timestamps to survive restarts"""
        from ..core.state import set_state
        set_state("exitbot.position_first_seen", self._position_first_seen)
    
    def _load_recent_exits(self) -> None:
        """Load recent exits from state"""
        try:
            exits_data = get_state("exitbot.recent_exits", [])
            if isinstance(exits_data, list):
                self._recent_exits = [
                    ExitRecord(
                        symbol=e.get("symbol", ""),
                        side=e.get("side", ""),
                        qty=e.get("qty", 0),
                        entry_price=e.get("entry_price", 0),
                        exit_price=e.get("exit_price", 0),
                        pnl=e.get("pnl", 0),
                        pnl_percent=e.get("pnl_percent", 0),
                        reason=e.get("reason", ""),
                        bot_id=e.get("bot_id", ""),
                        timestamp=e.get("timestamp", "")
                    ) for e in exits_data[:10]  # Keep last 10
                ]
        except Exception as e:
            self._logger.error(f"Failed to load recent exits: {e}")
            self._recent_exits = []
    
    def _save_recent_exits(self) -> None:
        """Save recent exits to state"""
        try:
            exits_data = [
                {
                    "symbol": e.symbol,
                    "side": e.side,
                    "qty": e.qty,
                    "entry_price": e.entry_price,
                    "exit_price": e.exit_price,
                    "pnl": e.pnl,
                    "pnl_percent": e.pnl_percent,
                    "reason": e.reason,
                    "bot_id": e.bot_id,
                    "timestamp": e.timestamp
                } for e in self._recent_exits[:10]  # Keep last 10
            ]
            set_state("exitbot.recent_exits", exits_data)
        except Exception as e:
            self._logger.error(f"Failed to save recent exits: {e}")
    
    def _add_exit_record(self, position: PositionInfo, reason: str) -> None:
        """Add an exit record to recent exits list"""
        try:
            # Calculate P&L
            if position.side == "long":
                pnl = (position.current_price - position.entry_price) * position.qty
                pnl_pct = ((position.current_price - position.entry_price) / position.entry_price) * 100 if position.entry_price else 0
            else:
                pnl = (position.entry_price - position.current_price) * position.qty
                pnl_pct = ((position.entry_price - position.current_price) / position.entry_price) * 100 if position.entry_price else 0
            
            exit_record = ExitRecord(
                symbol=position.symbol,
                side=position.side,
                qty=position.qty,
                entry_price=position.entry_price,
                exit_price=position.current_price,
                pnl=pnl,
                pnl_percent=pnl_pct,
                reason=reason,
                bot_id=position.bot_id,
                timestamp=get_market_clock().now().strftime("%H:%M:%S")
            )
            
            # Add to front of list (most recent first)
            self._recent_exits.insert(0, exit_record)
            
            # Keep only last 10
            self._recent_exits = self._recent_exits[:10]
            
            # Persist
            self._save_recent_exits()
            
            # Log prominently
            self._logger.log("TRADE_EXIT", {
                "symbol": position.symbol,
                "side": position.side,
                "qty": position.qty,
                "entry_price": round(position.entry_price, 4),
                "exit_price": round(position.current_price, 4),
                "pnl": round(pnl, 2),
                "pnl_percent": round(pnl_pct, 2),
                "reason": reason,
                "bot_id": position.bot_id
            })
            
            try:
                risk_integration = get_risk_integration()
                risk_integration.record_trade_outcome(
                    symbol=position.symbol,
                    bot_name=position.bot_id,
                    return_pct=pnl_pct / 100.0,
                    pnl_usd=pnl,
                    is_loss=(pnl < 0)
                )
            except Exception as ri_err:
                self._logger.error(f"Risk integration record failed: {ri_err}")
            
            try:
                if self._session_protection is not None:
                    self._session_protection.record_trade_pnl(pnl, position.symbol, reason)
                    sp_status = self._session_protection.get_session_status()
                    self._logger.log("session_protection_exit_recorded", {
                        "symbol": position.symbol,
                        "trade_pnl": round(pnl, 2),
                        "session_pnl": sp_status.get("realized_pnl_usd", 0),
                        "hwm": sp_status.get("hwm_usd", 0),
                        "locked_floor": sp_status.get("locked_floor_usd", 0),
                        "tighten_active": sp_status.get("tighten_active", False),
                    })
            except Exception as sp_err:
                self._logger.error(f"SessionProtection record failed (fail-open): {sp_err}")
            
        except Exception as e:
            self._logger.error(f"Failed to add exit record: {e}")
    
    def get_monitored_positions_count(self) -> int:
        """Get count of currently monitored positions"""
        return len(self._known_positions)
    
    # ==========================================================================
    # PRE-STAGED EXIT ORDERS - Broker-side SL/TP protection
    # Places OCO orders on Alpaca so positions are protected even if system goes down
    # ==========================================================================

    def _save_staged_orders(self) -> None:
        """Save staged orders to state for restart resilience"""
        set_state("exitbot.staged_orders", self._staged_orders)

    def stage_exit_orders(
        self, position_id: str, symbol: str, qty: float, side: str,
        entry_price: float, stop_pct: float = 0.10, tp_pct: float = 0.001
    ) -> Dict[str, Any]:
        """
        Place OCO exit orders for a new position on Alpaca.
        
        This gives broker-side protection: if system crashes, Alpaca still enforces
        stop-loss and take-profit. When ExitBot is running, it can modify/cancel
        these orders as needed (trailing stop, liquidate-winners, etc).
        
        Args:
            position_id: Unique position identifier
            symbol: Ticker symbol
            qty: Position quantity
            side: "long" or "short"
            entry_price: Entry fill price
            stop_pct: Stop-loss percentage (0.10 = 10% loss)
            tp_pct: Take-profit percentage (0.001 = 0.1% profit for liquidate-winners mode)
            
        Returns:
            Dict with staged order info or error
        """
        if position_id in self._staged_orders:
            self._logger.log("staged_orders_already_exist", {
                "position_id": position_id, "symbol": symbol
            })
            return {"success": True, "already_staged": True}
        
        # OPTIONS: Alpaca doesn't support OCO or standalone stop/sell for options
        # on accounts not eligible for uncovered options trading. Track in-memory only.
        # OCC options format: root (1-6 chars padded to 6) + YYMMDD + C/P + strike (8 digits)
        # Examples: TLT260213C00088000, AAPL250117C00200000, SPY250321P00450000
        import re
        is_option = bool(re.match(r'^[A-Z]{1,6}\d{6}[CP]\d{8}$', symbol))
        if is_option:
            self._logger.log("staged_orders_skipped_options", {
                "position_id": position_id, "symbol": symbol,
                "reason": "options_not_supported_for_broker_side_staging",
                "protection": "exitbot_in_memory_monitoring_active"
            })
            staged_info = {
                "position_id": position_id, "symbol": symbol,
                "qty": qty, "side": side, "entry_price": entry_price,
                "order_type": "in_memory_only",
                "reason": "options_broker_staging_unsupported",
                "created_ts": time.time()
            }
            self._staged_orders[position_id] = staged_info
            self._save_staged_orders()
            return {"success": True, "staged": staged_info}
        
        # FRACTIONAL SHARES: Alpaca rejects OCO/stop orders for fractional quantities.
        # Only whole-share positions can use broker-side exit staging.
        # Use float tolerance to handle precision issues (e.g., 1.0000001)
        is_fractional = abs(qty - round(qty)) > 1e-6
        if is_fractional:
            self._logger.log("staged_orders_skipped_fractional", {
                "position_id": position_id, "symbol": symbol,
                "qty": qty, "reason": "fractional_orders_must_be_simple",
                "protection": "exitbot_in_memory_monitoring_active"
            })
            staged_info = {
                "position_id": position_id, "symbol": symbol,
                "qty": qty, "side": side, "entry_price": entry_price,
                "order_type": "in_memory_only",
                "reason": "fractional_qty_broker_staging_unsupported",
                "created_ts": time.time()
            }
            self._staged_orders[position_id] = staged_info
            self._save_staged_orders()
            return {"success": True, "staged": staged_info}
        
        # Check if Alpaca already has pending exit orders for this symbol
        # This prevents duplicate order spam after restarts when _staged_orders state is lost
        try:
            existing_orders = self._alpaca.get_open_orders(symbol=symbol)
            exit_side = "sell" if side == "long" else "buy"
            has_pending_exit = any(
                o.get("side") == exit_side for o in existing_orders
            )
            if has_pending_exit:
                self._logger.log("staged_orders_already_on_alpaca", {
                    "position_id": position_id, "symbol": symbol,
                    "existing_orders": len(existing_orders)
                })
                # Record in staged_orders so we don't check again next loop
                self._staged_orders[position_id] = {
                    "position_id": position_id, "symbol": symbol,
                    "order_type": "pre_existing", "created_ts": time.time()
                }
                self._save_staged_orders()
                return {"success": True, "already_staged": True}
        except Exception:
            pass  # Best-effort check, proceed with staging attempt
        
        # Calculate exit prices based on position side
        if side == "long":
            stop_price = round(entry_price * (1 - stop_pct), 2)
            tp_price = round(entry_price * (1 + tp_pct), 2)
            exit_side = "sell"
        else:
            stop_price = round(entry_price * (1 + stop_pct), 2)
            tp_price = round(entry_price * (1 - tp_pct), 2)
            exit_side = "buy"
        
        client_order_id = f"staged_{position_id[:8]}_{int(time.time())}"
        
        self._logger.log("staging_exit_orders", {
            "position_id": position_id,
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "tp_price": tp_price,
            "stop_pct": stop_pct,
            "tp_pct": tp_pct
        })
        
        # Place OCO on Alpaca
        result = self._alpaca.place_oco_exit_orders(
            symbol=symbol,
            qty=qty,
            side=exit_side,
            stop_price=stop_price,
            take_profit_price=tp_price,
            client_order_id=client_order_id
        )
        
        if not result.get("success"):
            # Fallback: place standalone stop order
            self._logger.log("oco_failed_fallback_to_stop", {
                "position_id": position_id,
                "error": result.get("error", "unknown"),
                "trying_standalone_stop": True
            })
            
            stop_result = self._alpaca.place_stop_order(
                symbol=symbol,
                qty=qty,
                side=exit_side,
                stop_price=stop_price,
                client_order_id=f"sl_{position_id[:8]}_{int(time.time())}"
            )
            
            if stop_result.get("success"):
                staged_info = {
                    "position_id": position_id,
                    "symbol": symbol,
                    "qty": qty,
                    "side": side,
                    "entry_price": entry_price,
                    "stop_order_id": stop_result["order_id"],
                    "tp_order_id": None,
                    "parent_order_id": None,
                    "stop_price": stop_price,
                    "tp_price": None,
                    "order_type": "standalone_stop",
                    "last_update_ts": time.time(),
                    "created_ts": time.time()
                }
                self._staged_orders[position_id] = staged_info
                self._save_staged_orders()
                
                self._logger.log("staged_stop_only_placed", staged_info)
                return {"success": True, "staged": staged_info}
            else:
                self._logger.error(f"Both OCO and standalone stop failed for {symbol}: {stop_result.get('error')}")
                return {"success": False, "error": "OCO and stop both failed"}
        
        # OCO placed successfully
        staged_info = {
            "position_id": position_id,
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "entry_price": entry_price,
            "stop_order_id": result.get("stop_order_id"),
            "tp_order_id": result.get("tp_order_id"),
            "parent_order_id": result.get("parent_order_id"),
            "stop_price": stop_price,
            "tp_price": tp_price,
            "order_type": "oco",
            "last_update_ts": time.time(),
            "created_ts": time.time()
        }
        
        self._staged_orders[position_id] = staged_info
        self._save_staged_orders()
        
        self._logger.log("staged_oco_exit_placed", staged_info)
        return {"success": True, "staged": staged_info}

    def update_staged_stop(self, position_id: str, new_stop_price: float) -> Dict[str, Any]:
        """
        Update the stop-loss price of a staged exit order.
        
        Used for trailing stop updates - as price moves in our favor,
        ratchet the broker-side stop up to lock in gains.
        
        Args:
            position_id: Position identifier
            new_stop_price: New stop-loss price
            
        Returns:
            Dict with success status
        """
        staged = self._staged_orders.get(position_id)
        if not staged:
            return {"success": False, "error": "No staged order found"}
        
        stop_order_id = staged.get("stop_order_id")
        if not stop_order_id:
            return {"success": False, "error": "No stop order ID tracked"}
        
        old_stop = staged.get("stop_price", 0)
        new_stop_price = round(new_stop_price, 2)
        
        # Only update if price has changed meaningfully (>$0.01)
        if abs(new_stop_price - old_stop) < 0.01:
            return {"success": True, "no_change": True}
        
        self._logger.log("updating_staged_stop", {
            "position_id": position_id,
            "symbol": staged["symbol"],
            "old_stop": old_stop,
            "new_stop": new_stop_price
        })
        
        result = self._alpaca.replace_order(
            order_id=stop_order_id,
            stop_price=new_stop_price
        )
        
        if result.get("success"):
            # Update tracked info
            staged["stop_price"] = new_stop_price
            staged["stop_order_id"] = result.get("new_order_id", stop_order_id)
            staged["last_update_ts"] = time.time()
            self._save_staged_orders()
            
            self._logger.log("staged_stop_updated", {
                "position_id": position_id,
                "symbol": staged["symbol"],
                "new_stop": new_stop_price,
                "new_order_id": result.get("new_order_id")
            })
        else:
            self._logger.error(f"Failed to update staged stop for {position_id}: {result.get('error')}")
        
        return result

    def cancel_staged_orders(self, position_id: str, reason: str = "manual_exit") -> Dict[str, Any]:
        """
        Cancel all staged exit orders for a position.
        
        Called when ExitBot decides to exit a position manually (e.g., 
        liquidate-winners mode, time-based exit, etc). Must cancel the
        broker-side orders so they don't fire after we've already exited.
        
        Args:
            position_id: Position identifier
            reason: Why we're cancelling (for logging)
            
        Returns:
            Dict with cancellation results
        """
        staged = self._staged_orders.get(position_id)
        if not staged:
            return {"success": True, "no_staged_orders": True}
        
        self._logger.log("cancelling_staged_orders", {
            "position_id": position_id,
            "symbol": staged["symbol"],
            "reason": reason,
            "order_type": staged.get("order_type")
        })
        
        cancelled = []
        errors = []
        
        # Cancel stop order
        if staged.get("stop_order_id"):
            result = self._alpaca.cancel_order(staged["stop_order_id"])
            if result.get("success"):
                cancelled.append(("stop", staged["stop_order_id"]))
            else:
                errors.append(("stop", result.get("error", "unknown")))
        
        # Cancel TP order
        if staged.get("tp_order_id"):
            result = self._alpaca.cancel_order(staged["tp_order_id"])
            if result.get("success"):
                cancelled.append(("tp", staged["tp_order_id"]))
            else:
                errors.append(("tp", result.get("error", "unknown")))
        
        # Cancel parent order (OCO parent)
        if staged.get("parent_order_id"):
            result = self._alpaca.cancel_order(staged["parent_order_id"])
            if result.get("success"):
                cancelled.append(("parent", staged["parent_order_id"]))
            else:
                errors.append(("parent", result.get("error", "unknown")))
        
        # Remove from tracking
        del self._staged_orders[position_id]
        self._save_staged_orders()
        
        self._logger.log("staged_orders_cancelled", {
            "position_id": position_id,
            "cancelled": cancelled,
            "errors": errors,
            "reason": reason
        })
        
        return {
            "success": len(errors) == 0,
            "cancelled": cancelled,
            "errors": errors
        }

    def check_staged_order_status(self, position_id: str) -> Optional[Dict[str, Any]]:
        """
        Check if a staged order has filled (indicating SL/TP hit at broker level).
        
        If a staged order filled, the position was exited by Alpaca, and we need 
        to clean up our tracking.
        
        Args:
            position_id: Position identifier
            
        Returns:
            Dict with status info, or None if no staged orders
        """
        staged = self._staged_orders.get(position_id)
        if not staged:
            return None
        
        result = {"position_id": position_id, "filled": False}
        
        # Check stop order status
        if staged.get("stop_order_id"):
            order = self._alpaca.get_order(staged["stop_order_id"])
            if order and order.get("status") == "filled":
                result["filled"] = True
                result["filled_type"] = "stop_loss"
                result["filled_price"] = order.get("filled_avg_price")
                
                self._logger.log("staged_stop_filled", {
                    "position_id": position_id,
                    "symbol": staged["symbol"],
                    "stop_price": staged["stop_price"],
                    "filled_price": order.get("filled_avg_price")
                })
        
        # Check TP order status
        if staged.get("tp_order_id") and not result["filled"]:
            order = self._alpaca.get_order(staged["tp_order_id"])
            if order and order.get("status") == "filled":
                result["filled"] = True
                result["filled_type"] = "take_profit"
                result["filled_price"] = order.get("filled_avg_price")
                
                self._logger.log("staged_tp_filled", {
                    "position_id": position_id,
                    "symbol": staged["symbol"],
                    "tp_price": staged["tp_price"],
                    "filled_price": order.get("filled_avg_price")
                })
        
        # Clean up if filled
        if result["filled"]:
            del self._staged_orders[position_id]
            self._save_staged_orders()
        
        return result

    def reconcile_staged_orders(self) -> Dict[str, Any]:
        """
        Reconcile staged orders with current positions on startup.
        
        Handles edge cases:
        - Position closed while system was down (staged order filled or cancelled)
        - New positions that don't have staged orders yet
        - Stale staged orders for positions that no longer exist
        
        Returns:
            Dict with reconciliation stats
        """
        self._logger.log("staged_orders_reconciliation_start", {
            "tracked_count": len(self._staged_orders)
        })
        
        cleaned_up = 0
        still_active = 0
        errors = 0
        
        stale_position_ids = []
        
        for position_id, staged in list(self._staged_orders.items()):
            # Check if stop order is still alive
            stop_id = staged.get("stop_order_id")
            if stop_id:
                order = self._alpaca.get_order(stop_id)
                if order:
                    status = order.get("status", "").lower()
                    if status in ("filled", "cancelled", "expired", "replaced"):
                        # Order is done - position was exited or order expired
                        stale_position_ids.append(position_id)
                        self._logger.log("staged_order_stale", {
                            "position_id": position_id,
                            "symbol": staged["symbol"],
                            "status": status
                        })
                    else:
                        still_active += 1
                else:
                    # Can't find order - assume stale
                    stale_position_ids.append(position_id)
                    errors += 1
            else:
                stale_position_ids.append(position_id)
        
        # Clean up stale entries
        for pid in stale_position_ids:
            del self._staged_orders[pid]
            cleaned_up += 1
        
        if stale_position_ids:
            self._save_staged_orders()
        
        result = {
            "still_active": still_active,
            "cleaned_up": cleaned_up,
            "errors": errors,
            "remaining": len(self._staged_orders)
        }
        
        self._logger.log("staged_orders_reconciliation_done", result)
        return result

    def get_staged_order_info(self, position_id: str) -> Optional[Dict[str, Any]]:
        """Get staged order info for a position, or None if not staged"""
        return self._staged_orders.get(position_id)

    def has_staged_orders(self, position_id: str) -> bool:
        """Check if a position has staged exit orders"""
        return position_id in self._staged_orders

    def cancel_staged_orders_for_symbol(self, symbol: str, reason: str = "wash_trade_prevention") -> Dict[str, Any]:
        """
        Cancel ALL staged exit orders for a given symbol across all position IDs.
        
        This prevents wash trade collisions when another bot (e.g., MomentumBot)
        needs to submit a sell order for a symbol that ExitBot has staged stop-loss
        orders on. Alpaca rejects the new order with error 40310000 if opposite-side
        orders already exist.
        
        Args:
            symbol: Trading symbol (e.g., "AAPL", "BTC/USD", "BTCUSD")
            reason: Why we're cancelling (for logging)
            
        Returns:
            Dict with cancellation results: {cancelled_count, position_ids, errors}
        """
        symbol_clean = symbol.replace("/", "").upper()
        matching_position_ids = []
        
        for position_id, staged in list(self._staged_orders.items()):
            staged_symbol = staged.get("symbol", "").replace("/", "").upper()
            if staged_symbol == symbol_clean:
                matching_position_ids.append(position_id)
        
        if not matching_position_ids:
            return {"cancelled_count": 0, "position_ids": [], "errors": []}
        
        self._logger.log("wash_trade_prevention_cancelling_staged", {
            "symbol": symbol,
            "symbol_clean": symbol_clean,
            "matching_position_ids": matching_position_ids,
            "reason": reason,
            "staged_order_count": len(matching_position_ids)
        })
        
        all_errors = []
        cancelled_count = 0
        
        for position_id in matching_position_ids:
            result = self.cancel_staged_orders(position_id, reason)
            if result.get("success") or result.get("no_staged_orders"):
                cancelled_count += 1
            else:
                all_errors.extend(result.get("errors", []))
        
        summary = {
            "cancelled_count": cancelled_count,
            "position_ids": matching_position_ids,
            "errors": all_errors
        }
        
        self._logger.log("wash_trade_prevention_complete", {
            "symbol": symbol,
            **summary
        })
        
        return summary

    def clear_all_tracking(self) -> None:
        """Clear all position tracking (for testing/reset)"""
        self._known_positions.clear()
        delete_state("exitbot.known_positions")
        self._staged_orders.clear()
        delete_state("exitbot.staged_orders")
        self._logger.log("exitbot_tracking_cleared", {})


# Global singleton instance
_exitbot: Optional[ExitBot] = None


def get_exitbot() -> ExitBot:
    """Get or create the global ExitBot instance"""
    global _exitbot
    if _exitbot is None:
        _exitbot = ExitBot()
    return _exitbot
