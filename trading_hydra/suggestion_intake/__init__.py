"""
Suggestion Intake Bot - CLI for manual trade ideas.

Converts manual trade suggestions into standardized TradeIntent objects
and queues them for execution by existing trading bots.
"""
from .tradeintent_schema import (
    TradeIntent,
    MarketContext,
    ExitPlan,
    OptionsContract,
    ValidationResult,
    AssetType,
    TradeDirection,
    EntryTrigger,
    RouteType,
    ValidationStatus
)

__all__ = [
    "TradeIntent",
    "MarketContext",
    "ExitPlan",
    "OptionsContract",
    "ValidationResult",
    "AssetType",
    "TradeDirection",
    "EntryTrigger",
    "RouteType",
    "ValidationStatus"
]
