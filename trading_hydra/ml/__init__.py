"""
Machine Learning module for Trading Hydra.

Provides ML-based trade signal scoring and account-level analytics
to augment rule-based trading decisions.
"""

from .signal_service import MLSignalService
from .metrics_repository import (
    MetricsRepository,
    get_metrics_repository,
    DailyMetrics,
    RegimeSnapshot,
    BotPerformance,
    RiskDecision
)
from .base_model import BaseModelService
from .account_analytics import (
    AccountAnalyticsService,
    get_account_analytics_service,
    AccountAnalytics
)
from .models import (
    RiskAdjustmentEngine,
    BotAllocationModel,
    RegimeSizer,
    DrawdownPredictor,
    AnomalyDetector
)
from .trade_outcome_tracker import (
    TradeOutcomeTracker,
    get_trade_tracker,
    TradeOutcome
)
from .performance_metrics import (
    PerformanceTracker,
    get_performance_tracker,
    DailyMetrics as PerformanceDailyMetrics
)

__all__ = [
    "MLSignalService",
    "MetricsRepository",
    "get_metrics_repository",
    "DailyMetrics",
    "RegimeSnapshot",
    "BotPerformance",
    "RiskDecision",
    "BaseModelService",
    "AccountAnalyticsService",
    "get_account_analytics_service",
    "AccountAnalytics",
    "RiskAdjustmentEngine",
    "BotAllocationModel",
    "RegimeSizer",
    "DrawdownPredictor",
    "AnomalyDetector",
    "TradeOutcomeTracker",
    "get_trade_tracker",
    "TradeOutcome",
    "PerformanceTracker",
    "get_performance_tracker",
    "PerformanceDailyMetrics"
]
