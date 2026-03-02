"""
TradeIntent Schema - Standardized trade idea representation.

This schema captures all aspects of a manual trade suggestion that
can be queued for execution by the existing trading bots.
"""
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, Any, List, Optional, Literal
from enum import Enum
import json


class AssetType(Enum):
    EQUITY = "equity"
    OPTIONS = "options"
    CRYPTO = "crypto"


class TradeDirection(Enum):
    LONG = "long"
    SHORT = "short"


class EntryTrigger(Enum):
    VWAP_RECLAIM = "vwap_reclaim"
    VWAP_LOSE = "vwap_lose"
    ORB_BREAK = "orb_break"
    GAP_UP = "gap_up"
    GAP_DOWN = "gap_down"
    PREMARKET_HIGH_BREAK = "premarket_high_break"
    PREMARKET_LOW_BREAK = "premarket_low_break"
    SUPPORT_BOUNCE = "support_bounce"
    RESISTANCE_BREAK = "resistance_break"
    SMA_CROSSOVER = "sma_crossover"
    IMMEDIATE = "immediate"


class RouteType(Enum):
    TWENTY_MINUTE = "twenty_minute"
    ZERO_DTE = "zero_dte"
    SWING = "swing"
    CRYPTO = "crypto"
    AUTO = "auto"


class ValidationStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class OptionsContract:
    """Selected options contract details."""
    symbol: str
    underlying: str
    strike: float
    expiration: date
    option_type: Literal["call", "put"]
    bid: float
    ask: float
    spread: float
    spread_pct: float
    volume: int
    open_interest: int
    delta: float
    gamma: float
    theta: float
    vega: float
    iv: float
    expected_theta_decay_1d: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "underlying": self.underlying,
            "strike": self.strike,
            "expiration": self.expiration.isoformat(),
            "option_type": self.option_type,
            "bid": self.bid,
            "ask": self.ask,
            "spread": self.spread,
            "spread_pct": self.spread_pct,
            "volume": self.volume,
            "open_interest": self.open_interest,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
            "iv": self.iv,
            "expected_theta_decay_1d": self.expected_theta_decay_1d
        }


@dataclass
class MarketContext:
    """Current market data snapshot for the symbol."""
    symbol: str
    current_price: float
    prev_close: float
    vwap: Optional[float]
    atr_14: float
    atr_pct: float
    gap_pct: float
    volume: int
    avg_volume_20d: int
    relative_volume: float
    trend_bias: Literal["bullish", "bearish", "neutral"]
    sma_9: Optional[float]
    sma_21: Optional[float]
    premarket_high: Optional[float]
    premarket_low: Optional[float]
    timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "current_price": self.current_price,
            "prev_close": self.prev_close,
            "vwap": self.vwap,
            "atr_14": self.atr_14,
            "atr_pct": self.atr_pct,
            "gap_pct": self.gap_pct,
            "volume": self.volume,
            "avg_volume_20d": self.avg_volume_20d,
            "relative_volume": self.relative_volume,
            "trend_bias": self.trend_bias,
            "sma_9": self.sma_9,
            "sma_21": self.sma_21,
            "premarket_high": self.premarket_high,
            "premarket_low": self.premarket_low,
            "timestamp": self.timestamp.isoformat()
        }


@dataclass
class ExitPlan:
    """Exit strategy for the trade."""
    stop_loss_price: float
    stop_loss_pct: float
    trailing_stop_activation_pct: float
    trailing_stop_pct: float
    reversal_sense_pct: float
    tp1_price: float
    tp1_pct: float
    tp1_size_pct: int
    tp2_price: Optional[float]
    tp2_pct: Optional[float]
    tp2_size_pct: Optional[int]
    tp3_price: Optional[float]
    tp3_pct: Optional[float]
    tp3_size_pct: Optional[int]
    max_hold_hours: Optional[int]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stop_loss_price": self.stop_loss_price,
            "stop_loss_pct": self.stop_loss_pct,
            "trailing_stop_activation_pct": self.trailing_stop_activation_pct,
            "trailing_stop_pct": self.trailing_stop_pct,
            "reversal_sense_pct": self.reversal_sense_pct,
            "tp1_price": self.tp1_price,
            "tp1_pct": self.tp1_pct,
            "tp1_size_pct": self.tp1_size_pct,
            "tp2_price": self.tp2_price,
            "tp2_pct": self.tp2_pct,
            "tp2_size_pct": self.tp2_size_pct,
            "tp3_price": self.tp3_price,
            "tp3_pct": self.tp3_pct,
            "tp3_size_pct": self.tp3_size_pct,
            "max_hold_hours": self.max_hold_hours
        }


@dataclass
class ValidationResult:
    """Pre-trade validation outcome."""
    status: ValidationStatus
    passed_checks: List[str] = field(default_factory=list)
    failed_checks: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    rejection_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "warnings": self.warnings,
            "rejection_reason": self.rejection_reason
        }


@dataclass
class TradeIntent:
    """
    Complete trade suggestion ready for bot execution.
    
    This is the standardized output of the Suggestion Intake Bot,
    containing all information needed for execution.
    """
    id: str
    created_at: datetime
    symbol: str
    direction: TradeDirection
    asset_type: AssetType
    route: RouteType
    horizon: str
    target_price: Optional[float]
    entry_trigger: EntryTrigger
    entry_trigger_params: Dict[str, Any]
    market_context: MarketContext
    exit_plan: ExitPlan
    options_contract: Optional[OptionsContract]
    position_size_pct: float
    max_risk_usd: float
    validation: ValidationResult
    user_notes: Optional[str]
    source: str = "suggestion_intake"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "symbol": self.symbol,
            "direction": self.direction.value,
            "asset_type": self.asset_type.value,
            "route": self.route.value,
            "horizon": self.horizon,
            "target_price": self.target_price,
            "entry_trigger": self.entry_trigger.value,
            "entry_trigger_params": self.entry_trigger_params,
            "market_context": self.market_context.to_dict(),
            "exit_plan": self.exit_plan.to_dict(),
            "options_contract": self.options_contract.to_dict() if self.options_contract else None,
            "position_size_pct": self.position_size_pct,
            "max_risk_usd": self.max_risk_usd,
            "validation": self.validation.to_dict(),
            "user_notes": self.user_notes,
            "source": self.source
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradeIntent":
        """Reconstruct TradeIntent from dictionary."""
        market_ctx = MarketContext(
            symbol=data["market_context"]["symbol"],
            current_price=data["market_context"]["current_price"],
            prev_close=data["market_context"]["prev_close"],
            vwap=data["market_context"]["vwap"],
            atr_14=data["market_context"]["atr_14"],
            atr_pct=data["market_context"]["atr_pct"],
            gap_pct=data["market_context"]["gap_pct"],
            volume=data["market_context"]["volume"],
            avg_volume_20d=data["market_context"]["avg_volume_20d"],
            relative_volume=data["market_context"]["relative_volume"],
            trend_bias=data["market_context"]["trend_bias"],
            sma_9=data["market_context"]["sma_9"],
            sma_21=data["market_context"]["sma_21"],
            premarket_high=data["market_context"]["premarket_high"],
            premarket_low=data["market_context"]["premarket_low"],
            timestamp=datetime.fromisoformat(data["market_context"]["timestamp"])
        )
        
        exit_plan = ExitPlan(
            stop_loss_price=data["exit_plan"]["stop_loss_price"],
            stop_loss_pct=data["exit_plan"]["stop_loss_pct"],
            trailing_stop_activation_pct=data["exit_plan"]["trailing_stop_activation_pct"],
            trailing_stop_pct=data["exit_plan"]["trailing_stop_pct"],
            reversal_sense_pct=data["exit_plan"]["reversal_sense_pct"],
            tp1_price=data["exit_plan"]["tp1_price"],
            tp1_pct=data["exit_plan"]["tp1_pct"],
            tp1_size_pct=data["exit_plan"]["tp1_size_pct"],
            tp2_price=data["exit_plan"]["tp2_price"],
            tp2_pct=data["exit_plan"]["tp2_pct"],
            tp2_size_pct=data["exit_plan"]["tp2_size_pct"],
            tp3_price=data["exit_plan"]["tp3_price"],
            tp3_pct=data["exit_plan"]["tp3_pct"],
            tp3_size_pct=data["exit_plan"]["tp3_size_pct"],
            max_hold_hours=data["exit_plan"]["max_hold_hours"]
        )
        
        validation = ValidationResult(
            status=ValidationStatus(data["validation"]["status"]),
            passed_checks=data["validation"]["passed_checks"],
            failed_checks=data["validation"]["failed_checks"],
            warnings=data["validation"]["warnings"],
            rejection_reason=data["validation"]["rejection_reason"]
        )
        
        options_contract = None
        if data.get("options_contract"):
            opt = data["options_contract"]
            options_contract = OptionsContract(
                symbol=opt["symbol"],
                underlying=opt["underlying"],
                strike=opt["strike"],
                expiration=date.fromisoformat(opt["expiration"]),
                option_type=opt["option_type"],
                bid=opt["bid"],
                ask=opt["ask"],
                spread=opt["spread"],
                spread_pct=opt["spread_pct"],
                volume=opt["volume"],
                open_interest=opt["open_interest"],
                delta=opt["delta"],
                gamma=opt["gamma"],
                theta=opt["theta"],
                vega=opt["vega"],
                iv=opt["iv"],
                expected_theta_decay_1d=opt["expected_theta_decay_1d"]
            )
        
        return cls(
            id=data["id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            symbol=data["symbol"],
            direction=TradeDirection(data["direction"]),
            asset_type=AssetType(data["asset_type"]),
            route=RouteType(data["route"]),
            horizon=data["horizon"],
            target_price=data.get("target_price"),
            entry_trigger=EntryTrigger(data["entry_trigger"]),
            entry_trigger_params=data["entry_trigger_params"],
            market_context=market_ctx,
            exit_plan=exit_plan,
            options_contract=options_contract,
            position_size_pct=data["position_size_pct"],
            max_risk_usd=data["max_risk_usd"],
            validation=validation,
            user_notes=data.get("user_notes"),
            source=data.get("source", "suggestion_intake")
        )
