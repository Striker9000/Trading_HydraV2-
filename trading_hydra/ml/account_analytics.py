"""
AccountAnalyticsService - Orchestrates all account-level ML models.

Central service that coordinates:
- Dynamic Risk Adjustment
- Bot Allocation
- Regime-Based Position Sizing
- Drawdown Prediction
- Anomaly Detection

Provides a unified interface for the trading loop to access ML insights.
"""

from datetime import datetime, date
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict

from ..core.logging import get_logger
from ..services.market_regime import get_market_regime_service, MarketRegimeAnalysis

from .metrics_repository import (
    MetricsRepository, 
    get_metrics_repository,
    DailyMetrics,
    RegimeSnapshot,
    BotPerformance,
    RiskDecision
)
from .models import (
    RiskAdjustmentEngine,
    BotAllocationModel,
    RegimeSizer,
    DrawdownPredictor,
    AnomalyDetector
)


@dataclass
class AccountAnalytics:
    """Complete account analytics result from all ML models."""
    timestamp: str
    
    risk_multiplier: float
    risk_adjustment_reason: str
    risk_confidence: float
    
    bot_allocations: Dict[str, float]
    recommended_bot: str
    allocation_confidence: float
    
    position_size_multiplier: float
    regime_assessment: str
    sizing_confidence: float
    
    drawdown_probability: float
    drawdown_risk_level: str
    drawdown_recommendation: str
    
    is_anomaly: bool
    anomaly_type: str
    anomaly_score: float
    anomaly_details: List[Dict[str, Any]]
    
    overall_health_score: float
    should_halt_trading: bool
    halt_reasons: List[str]


class AccountAnalyticsService:
    """
    Central orchestrator for account-level ML analytics.
    
    Coordinates all 5 ML models and provides unified insights
    for the trading loop to use in decision-making.
    """
    
    def __init__(self, enabled: bool = True):
        """
        Initialize the AccountAnalyticsService.
        
        Args:
            enabled: Whether ML analytics is enabled (can be disabled via config)
        """
        self._logger = get_logger()
        self._enabled = enabled
        
        self._metrics_repo: Optional[MetricsRepository] = None
        self._regime_service = None
        
        self._risk_engine: Optional[RiskAdjustmentEngine] = None
        self._bot_allocator: Optional[BotAllocationModel] = None
        self._regime_sizer: Optional[RegimeSizer] = None
        self._drawdown_predictor: Optional[DrawdownPredictor] = None
        self._anomaly_detector: Optional[AnomalyDetector] = None
        
        self._last_analytics: Optional[AccountAnalytics] = None
        self._last_update: Optional[datetime] = None
        
        if enabled:
            self._initialize_models()
    
    def _initialize_models(self) -> None:
        """Initialize all ML models."""
        try:
            self._metrics_repo = get_metrics_repository()
            self._regime_service = get_market_regime_service()
            
            self._risk_engine = RiskAdjustmentEngine()
            self._bot_allocator = BotAllocationModel()
            self._regime_sizer = RegimeSizer()
            self._drawdown_predictor = DrawdownPredictor()
            self._anomaly_detector = AnomalyDetector()
            
            self._logger.log("account_analytics_init", {
                "risk_engine": self._risk_engine.is_available,
                "bot_allocator": self._bot_allocator.is_available,
                "regime_sizer": self._regime_sizer.is_available,
                "drawdown_predictor": self._drawdown_predictor.is_available,
                "anomaly_detector": self._anomaly_detector.is_available
            })
        except Exception as e:
            self._logger.error(f"AccountAnalyticsService init failed: {e}")
            self._enabled = False
    
    @property
    def is_enabled(self) -> bool:
        """Check if account analytics is enabled."""
        return self._enabled
    
    def analyze(
        self,
        account_info: Dict[str, Any],
        positions: List[Dict[str, Any]],
        bot_stats: Dict[str, Dict[str, Any]],
        api_stats: Optional[Dict[str, Any]] = None
    ) -> AccountAnalytics:
        """
        Run all account-level ML analyses.
        
        Args:
            account_info: Current account information from Alpaca
            positions: List of current positions
            bot_stats: Per-bot statistics (trades, P&L, etc.)
            api_stats: Optional API error/call statistics
            
        Returns:
            AccountAnalytics with all model predictions
        """
        now = datetime.utcnow()
        
        if not self._enabled:
            return self._get_default_analytics(now)
        
        try:
            daily_metrics = self._metrics_repo.get_daily_metrics_range(days=30)
            regime = self._regime_service.get_regime()
            regime_history = self._metrics_repo.get_regime_history(days=30)
            
            today_str = now.strftime("%Y-%m-%d")
            bot_performance = self._metrics_repo.get_all_bots_performance_today(today_str)
            
            today_metrics = self._build_today_metrics(account_info, positions, bot_stats)
            
            base_context = {
                "daily_metrics": daily_metrics,
                "regime": regime,
                "regime_history": regime_history,
                "bot_performance": bot_performance,
                "today_metrics": today_metrics,
                "account_metrics": today_metrics,
                "hour": now.hour,
                "day_of_week": now.weekday(),
                "api_stats": api_stats or {}
            }
            
            risk_result = self._risk_engine.safe_predict(base_context)
            alloc_result = self._bot_allocator.safe_predict(base_context)
            size_result = self._regime_sizer.safe_predict(base_context)
            dd_result = self._drawdown_predictor.safe_predict(base_context)
            anomaly_result = self._anomaly_detector.safe_predict(base_context)
            
            halt_reasons = []
            should_halt = False
            
            if risk_result["risk_multiplier"] < 0.3:
                halt_reasons.append("very_low_risk_multiplier")
            
            if dd_result["drawdown_probability"] > 0.7:
                halt_reasons.append("high_drawdown_probability")
                should_halt = True
            
            if anomaly_result["is_anomaly"] and anomaly_result["anomaly_type"] in ["api_issues", "order_rejections"]:
                halt_reasons.append(f"anomaly_{anomaly_result['anomaly_type']}")
                should_halt = True
            
            if size_result["size_multiplier"] < 0.1:
                halt_reasons.append("extreme_regime_conditions")
                should_halt = True
            
            health_score = self._calculate_health_score(
                risk_result, alloc_result, size_result, dd_result, anomaly_result
            )
            
            analytics = AccountAnalytics(
                timestamp=now.isoformat(),
                risk_multiplier=risk_result["risk_multiplier"],
                risk_adjustment_reason=risk_result["adjustment_reason"],
                risk_confidence=risk_result["confidence"],
                bot_allocations=alloc_result["allocations"],
                recommended_bot=alloc_result["recommended_bot"],
                allocation_confidence=alloc_result["confidence"],
                position_size_multiplier=size_result["size_multiplier"],
                regime_assessment=size_result["regime_assessment"],
                sizing_confidence=size_result["confidence"],
                drawdown_probability=dd_result["drawdown_probability"],
                drawdown_risk_level=dd_result["risk_level"],
                drawdown_recommendation=dd_result["recommendation"],
                is_anomaly=anomaly_result["is_anomaly"],
                anomaly_type=anomaly_result["anomaly_type"],
                anomaly_score=anomaly_result["anomaly_score"],
                anomaly_details=anomaly_result["details"],
                overall_health_score=health_score,
                should_halt_trading=should_halt,
                halt_reasons=halt_reasons
            )
            
            self._last_analytics = analytics
            self._last_update = now
            
            self._logger.log("account_analytics_complete", {
                "risk_mult": analytics.risk_multiplier,
                "size_mult": analytics.position_size_multiplier,
                "dd_prob": analytics.drawdown_probability,
                "is_anomaly": analytics.is_anomaly,
                "health_score": analytics.overall_health_score,
                "should_halt": analytics.should_halt_trading
            })
            
            return analytics
            
        except Exception as e:
            self._logger.error(f"Account analytics failed: {e}")
            return self._get_default_analytics(now)
    
    def record_daily_snapshot(
        self,
        account_info: Dict[str, Any],
        positions: List[Dict[str, Any]],
        trade_stats: Dict[str, Any],
        regime: Optional[MarketRegimeAnalysis] = None
    ) -> None:
        """
        Record daily metrics snapshot for future training.
        
        Should be called at end of each trading day.
        """
        if not self._enabled or not self._metrics_repo:
            return
        
        try:
            today = datetime.utcnow().strftime("%Y-%m-%d")
            
            equity = float(account_info.get("equity", 0))
            cash = float(account_info.get("cash", 0))
            buying_power = float(account_info.get("buying_power", 0))
            
            prev_metrics = self._metrics_repo.get_daily_metrics(
                (datetime.utcnow() - __import__('datetime').timedelta(days=1)).strftime("%Y-%m-%d")
            )
            prev_equity = prev_metrics.equity if prev_metrics else equity
            
            daily_pnl = equity - prev_equity
            daily_pnl_pct = (daily_pnl / prev_equity * 100) if prev_equity > 0 else 0
            
            equity_curve = self._metrics_repo.get_equity_curve(days=90)
            if equity_curve:
                max_equity = max(eq for _, eq in equity_curve)
                current_dd = (max_equity - equity) / max_equity * 100 if max_equity > 0 else 0
            else:
                current_dd = 0
                max_equity = equity
            
            cumulative_pnl = equity - (equity_curve[0][1] if equity_curve else equity)
            
            metrics_range = self._metrics_repo.get_daily_metrics_range(days=90)
            max_dd = max((m.max_drawdown_pct for m in metrics_range), default=current_dd)
            max_dd = max(max_dd, current_dd)
            
            total_trades = trade_stats.get("total_trades", 0)
            winning = trade_stats.get("winning_trades", 0)
            losing = trade_stats.get("losing_trades", 0)
            win_rate = winning / (winning + losing) if (winning + losing) > 0 else 0.5
            avg_win = trade_stats.get("avg_win", 0)
            avg_loss = trade_stats.get("avg_loss", 0)
            profit_factor = abs(avg_win * winning) / abs(avg_loss * losing) if losing > 0 and avg_loss != 0 else 1.0
            
            crypto_pos = sum(1 for p in positions if "USD" in p.get("symbol", ""))
            options_pos = sum(1 for p in positions if p.get("asset_class") == "us_option")
            stock_pos = len(positions) - crypto_pos - options_pos
            
            if equity < 1000:
                mode = "MICRO"
            elif equity < 10000:
                mode = "SMALL"
            else:
                mode = "STANDARD"
            
            risk_mult = self._last_analytics.risk_multiplier if self._last_analytics else 1.0
            
            daily = DailyMetrics(
                date=today,
                equity=equity,
                cash=cash,
                buying_power=buying_power,
                daily_pnl=daily_pnl,
                daily_pnl_pct=daily_pnl_pct,
                cumulative_pnl=cumulative_pnl,
                max_drawdown_pct=max_dd,
                current_drawdown_pct=current_dd,
                total_trades=total_trades,
                winning_trades=winning,
                losing_trades=losing,
                win_rate=win_rate,
                avg_win=avg_win,
                avg_loss=avg_loss,
                profit_factor=profit_factor,
                open_positions=len(positions),
                crypto_positions=crypto_pos,
                stock_positions=stock_pos,
                options_positions=options_pos,
                account_mode=mode,
                risk_multiplier=risk_mult
            )
            
            self._metrics_repo.save_daily_metrics(daily)
            
            if regime:
                snapshot = RegimeSnapshot(
                    timestamp=datetime.utcnow().isoformat(),
                    date=today,
                    vix=regime.vix,
                    vvix=regime.vvix,
                    tnx=regime.tnx,
                    dxy=regime.dxy,
                    move=regime.move,
                    volatility_regime=regime.volatility_regime.value if hasattr(regime.volatility_regime, 'value') else str(regime.volatility_regime),
                    sentiment=regime.sentiment.value if hasattr(regime.sentiment, 'value') else str(regime.sentiment),
                    rate_environment=regime.rate_environment.value if hasattr(regime.rate_environment, 'value') else str(regime.rate_environment),
                    dollar_environment=regime.dollar_environment.value if hasattr(regime.dollar_environment, 'value') else str(regime.dollar_environment),
                    position_size_multiplier=regime.position_size_multiplier,
                    halt_new_entries=regime.halt_new_entries,
                    vvix_warning=regime.vvix_warning,
                    rate_shock_warning=regime.rate_shock_warning,
                    dollar_surge_warning=regime.dollar_surge_warning
                )
                self._metrics_repo.save_regime_snapshot(snapshot)
            
            self._logger.log("daily_snapshot_recorded", {
                "date": today,
                "equity": equity,
                "daily_pnl": daily_pnl,
                "current_dd": current_dd
            })
            
        except Exception as e:
            self._logger.error(f"Failed to record daily snapshot: {e}")
    
    def record_bot_performance(
        self,
        bot_id: str,
        stats: Dict[str, Any]
    ) -> None:
        """Record per-bot performance metrics."""
        if not self._enabled or not self._metrics_repo:
            return
        
        try:
            today = datetime.utcnow().strftime("%Y-%m-%d")
            
            perf = BotPerformance(
                date=today,
                bot_id=bot_id,
                trades_today=stats.get("trades_today", 0),
                wins_today=stats.get("wins_today", 0),
                losses_today=stats.get("losses_today", 0),
                pnl_today=stats.get("pnl_today", 0.0),
                pnl_pct_today=stats.get("pnl_pct_today", 0.0),
                avg_hold_time_mins=stats.get("avg_hold_time_mins", 0.0),
                sharpe_ratio_30d=stats.get("sharpe_ratio_30d", 0.0),
                win_rate_30d=stats.get("win_rate_30d", 0.5),
                max_drawdown_30d=stats.get("max_drawdown_30d", 0.0),
                total_allocated=stats.get("total_allocated", 0.0),
                total_returned=stats.get("total_returned", 0.0)
            )
            
            self._metrics_repo.save_bot_performance(perf)
            
        except Exception as e:
            self._logger.error(f"Failed to record bot performance: {e}")
    
    def get_effective_risk_multiplier(self) -> float:
        """Get the current effective risk multiplier."""
        if self._last_analytics:
            return self._last_analytics.risk_multiplier
        return 1.0
    
    def get_effective_size_multiplier(self) -> float:
        """Get the current effective position size multiplier."""
        if self._last_analytics:
            return self._last_analytics.position_size_multiplier
        return 1.0
    
    def get_bot_allocations(self) -> Dict[str, float]:
        """Get current recommended bot allocations."""
        if self._last_analytics:
            return self._last_analytics.bot_allocations
        return {"crypto_bot": 0.33, "momentum_bot": 0.33, "options_bot": 0.34}
    
    def should_halt_trading(self) -> tuple:
        """Check if trading should be halted and why."""
        if self._last_analytics:
            return self._last_analytics.should_halt_trading, self._last_analytics.halt_reasons
        return False, []
    
    def get_model_status(self) -> Dict[str, Any]:
        """Get status of all ML models."""
        if not self._enabled:
            return {"status": "disabled"}
        
        return {
            "status": "enabled",
            "models": {
                "risk_engine": self._risk_engine.get_model_info() if self._risk_engine else None,
                "bot_allocator": self._bot_allocator.get_model_info() if self._bot_allocator else None,
                "regime_sizer": self._regime_sizer.get_model_info() if self._regime_sizer else None,
                "drawdown_predictor": self._drawdown_predictor.get_model_info() if self._drawdown_predictor else None,
                "anomaly_detector": self._anomaly_detector.get_model_info() if self._anomaly_detector else None
            },
            "last_update": self._last_update.isoformat() if self._last_update else None
        }
    
    def _build_today_metrics(
        self,
        account_info: Dict[str, Any],
        positions: List[Dict[str, Any]],
        bot_stats: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Build today's metrics from current state."""
        equity = float(account_info.get("equity", 0))
        
        total_trades = sum(s.get("trades_today", 0) for s in bot_stats.values())
        winning = sum(s.get("wins_today", 0) for s in bot_stats.values())
        losing = sum(s.get("losses_today", 0) for s in bot_stats.values())
        win_rate = winning / (winning + losing) if (winning + losing) > 0 else 0.5
        
        total_pnl = sum(s.get("pnl_today", 0) for s in bot_stats.values())
        pnl_pct = (total_pnl / equity * 100) if equity > 0 else 0
        
        return {
            "equity": equity,
            "daily_pnl": total_pnl,
            "daily_pnl_pct": pnl_pct,
            "current_drawdown_pct": 0,
            "max_drawdown_pct": 0,
            "total_trades": total_trades,
            "winning_trades": winning,
            "losing_trades": losing,
            "win_rate": win_rate,
            "open_positions": len(positions),
            "risk_multiplier": self._last_analytics.risk_multiplier if self._last_analytics else 1.0
        }
    
    def _calculate_health_score(
        self,
        risk: Dict[str, Any],
        alloc: Dict[str, Any],
        size: Dict[str, Any],
        dd: Dict[str, Any],
        anomaly: Dict[str, Any]
    ) -> float:
        """Calculate overall account health score (0-100)."""
        score = 100.0
        
        if risk["risk_multiplier"] < 0.5:
            score -= 20
        elif risk["risk_multiplier"] < 0.75:
            score -= 10
        
        if dd["drawdown_probability"] > 0.6:
            score -= 30
        elif dd["drawdown_probability"] > 0.4:
            score -= 15
        elif dd["drawdown_probability"] > 0.2:
            score -= 5
        
        if anomaly["is_anomaly"]:
            score -= 20
        
        if size["size_multiplier"] < 0.3:
            score -= 15
        elif size["size_multiplier"] < 0.5:
            score -= 5
        
        return max(0, min(100, score))
    
    def _get_default_analytics(self, timestamp: datetime) -> AccountAnalytics:
        """Return default analytics when ML is disabled or failed."""
        return AccountAnalytics(
            timestamp=timestamp.isoformat(),
            risk_multiplier=1.0,
            risk_adjustment_reason="default",
            risk_confidence=0.0,
            bot_allocations={"crypto_bot": 0.33, "momentum_bot": 0.33, "options_bot": 0.34},
            recommended_bot="crypto_bot",
            allocation_confidence=0.0,
            position_size_multiplier=1.0,
            regime_assessment="unknown",
            sizing_confidence=0.0,
            drawdown_probability=0.0,
            drawdown_risk_level="unknown",
            drawdown_recommendation="normal_operations",
            is_anomaly=False,
            anomaly_type="normal",
            anomaly_score=0.0,
            anomaly_details=[],
            overall_health_score=100.0,
            should_halt_trading=False,
            halt_reasons=[]
        )


_account_analytics_service: Optional[AccountAnalyticsService] = None


def get_account_analytics_service(enabled: bool = True) -> AccountAnalyticsService:
    """Get or create singleton AccountAnalyticsService instance."""
    global _account_analytics_service
    if _account_analytics_service is None:
        _account_analytics_service = AccountAnalyticsService(enabled=enabled)
    return _account_analytics_service
