"""
Technical Indicators Module
===========================

This module provides technical indicators for trading strategies,
including the Turtle Traders strategy indicators and VWAP Posture Manager.

Key components:
- TurtleTrend: Donchian Channel breakouts, ATR(N), and Turtle-specific signals
- IndicatorEngine: Strategy system indicator calculations (EMA, SMA, RSI)
- VWAPPostureManager: Institutional VWAP posture with sticky states
"""

from .turtle_trend import (
    TurtleTrend,
    TurtleSignal,
    TurtleConfig,
    DonchianChannel,
    get_turtle_trend
)

from .indicator_engine import IndicatorEngine

from .vwap_posture import (
    VWAPPostureManager,
    VWAPPosture,
    VWAPLevel,
    GapState,
    GapFillStatus,
    GapContext,
    PostureDecision,
    get_vwap_posture_manager,
    reset_all_posture_managers
)

__all__ = [
    # Turtle Trend
    "TurtleTrend",
    "TurtleSignal", 
    "TurtleConfig",
    "DonchianChannel",
    "get_turtle_trend",
    # Indicator Engine
    "IndicatorEngine",
    # VWAP Posture
    "VWAPPostureManager",
    "VWAPPosture",
    "VWAPLevel",
    "GapState",
    "GapFillStatus",
    "GapContext",
    "PostureDecision",
    "get_vwap_posture_manager",
    "reset_all_posture_managers"
]
