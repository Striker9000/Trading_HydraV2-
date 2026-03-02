"""
Risk Management Module - Institutional-Grade Risk Controls.

Exports:
- TrailingStopManager: Trailing stop management
- InstitutionalPositionSizer: Dynamic position sizing with Kelly criterion
- CorrelationManager: Cross-asset correlation and exposure limits
- DynamicBudgetManager: Equity/drawdown-scaled budget allocation
- CorrelationGuard: Multi-loss detection and auto risk-down
- VolOfVolMonitor: VIX rate-of-change detection
- NewsRiskGate: Unified news sentiment gate for all bots
- PnLDistributionMonitor: Fat-tail detection and auto-halt
"""

from .trailing_stop import (
    TrailingStopManager,
    get_trailing_stop_manager,
    TrailingStopConfig,
    DynamicTrailingConfig,
    DYNAMIC_TRAIL_DEFAULTS
)

from .position_sizer import (
    InstitutionalPositionSizer,
    get_position_sizer,
    PositionSizeResult
)

from .correlation_manager import (
    CorrelationManager,
    get_correlation_manager,
    CorrelationResult,
    PortfolioExposure
)

from .dynamic_budget import (
    DynamicBudgetManager,
    get_dynamic_budget_manager,
    BudgetAllocation,
    BotPerformance
)

from .correlation_guard import (
    CorrelationGuard,
    get_correlation_guard,
    CorrelationGuardState,
    LossEvent
)

from .vol_of_vol_monitor import (
    VolOfVolMonitor,
    get_vol_of_vol_monitor,
    VolOfVolState
)

from .news_risk_gate import (
    NewsRiskGate,
    get_news_risk_gate,
    NewsGateResult,
    NewsAction
)

from .pnl_monitor import (
    PnLDistributionMonitor,
    get_pnl_monitor,
    PnLMonitorState,
    DistributionMetrics
)

from .greek_limits import (
    GreekRiskMonitor,
    get_greek_risk_monitor,
    GreekLimitResult,
    GreekLimitStatus,
    GreekExposure
)

from .pnl_attribution import (
    PnLAttributionService,
    get_pnl_attribution,
    PnLBreakdown,
    AttributionSummary,
    EntrySnapshot
)

from .session_protection import (
    SessionProtection,
    get_session_protection,
    SessionProtectionConfig
)

__all__ = [
    # Trailing stops
    "TrailingStopManager",
    "get_trailing_stop_manager",
    "TrailingStopConfig",
    "DynamicTrailingConfig",
    "DYNAMIC_TRAIL_DEFAULTS",
    # Position sizing
    "InstitutionalPositionSizer",
    "get_position_sizer",
    "PositionSizeResult",
    # Correlation management
    "CorrelationManager",
    "get_correlation_manager",
    "CorrelationResult",
    "PortfolioExposure",
    # Dynamic budget
    "DynamicBudgetManager",
    "get_dynamic_budget_manager",
    "BudgetAllocation",
    "BotPerformance",
    # Correlation guard
    "CorrelationGuard",
    "get_correlation_guard",
    "CorrelationGuardState",
    "LossEvent",
    # Vol-of-vol
    "VolOfVolMonitor",
    "get_vol_of_vol_monitor",
    "VolOfVolState",
    # News risk gate
    "NewsRiskGate",
    "get_news_risk_gate",
    "NewsGateResult",
    "NewsAction",
    # PnL monitor
    "PnLDistributionMonitor",
    "get_pnl_monitor",
    "PnLMonitorState",
    "DistributionMetrics",
    # Greek limits
    "GreekRiskMonitor",
    "get_greek_risk_monitor",
    "GreekLimitResult",
    "GreekLimitStatus",
    "GreekExposure",
    # P&L Attribution
    "PnLAttributionService",
    "get_pnl_attribution",
    "PnLBreakdown",
    "AttributionSummary",
    "EntrySnapshot",
    # Session protection
    "SessionProtection",
    "get_session_protection",
    "SessionProtectionConfig"
]
