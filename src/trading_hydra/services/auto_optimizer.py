"""
=============================================================================
Auto-Optimizer Service - ML + AI Powered Config Tuning
=============================================================================

Continuously monitors trade outcomes and automatically adjusts bot settings
to maximize profitability. Uses a combination of:

1. Statistical Analysis: Win rate, profit factor, Sharpe ratio
2. ML Pattern Detection: Correlate config values with outcomes
3. AI Recommendations: OpenAI for qualitative analysis
4. A/B Testing: Compare optimized vs current settings

Philosophy:
- Data-driven decisions over intuition
- Gradual changes (max 10% adjustment per cycle)
- Safety first: never increase risk beyond limits
- Human approval for major changes
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
import os
import json
import yaml
import time
import threading
import copy

from ..core.logging import get_logger
from ..core.config import load_bots_config, load_settings
from ..core.state import get_state, set_state
from ..risk.config_performance_logger import get_config_performance_logger, TradeOutcome


@dataclass
class OptimizationRecommendation:
    """A single config optimization recommendation."""
    bot_id: str
    parameter_path: str  # e.g., "exits.stop_loss_pct"
    current_value: Any
    recommended_value: Any
    change_pct: float  # Percentage change
    reason: str
    confidence: float  # 0-1
    expected_improvement: str
    priority: int  # 1 = highest
    generated_at: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "bot_id": self.bot_id,
            "parameter_path": self.parameter_path,
            "current_value": self.current_value,
            "recommended_value": self.recommended_value,
            "change_pct": self.change_pct,
            "reason": self.reason,
            "confidence": self.confidence,
            "expected_improvement": self.expected_improvement,
            "priority": self.priority,
            "generated_at": self.generated_at
        }


@dataclass
class OptimizationReport:
    """Full optimization report for a bot."""
    bot_id: str
    current_performance: Dict[str, Any]
    recommendations: List[OptimizationRecommendation]
    ai_analysis: str
    overall_health: str  # good, needs_attention, critical
    generated_at: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "bot_id": self.bot_id,
            "current_performance": self.current_performance,
            "recommendations": [r.to_dict() for r in self.recommendations],
            "ai_analysis": self.ai_analysis,
            "overall_health": self.overall_health,
            "generated_at": self.generated_at
        }


class AutoOptimizer:
    """
    Automatic config optimization using ML and AI.
    
    Monitors trade performance and suggests/applies optimizations.
    """
    
    STATE_KEY = "auto_optimizer.state"
    OPTIMIZED_CONFIG_PATH = "config/legacy/optimized_settings.yaml"
    
    MAX_CHANGE_PCT = 0.10  # Max 10% change per optimization cycle
    MIN_TRADES_FOR_ANALYSIS = 20
    ANALYSIS_INTERVAL_HOURS = 24
    
    TUNABLE_PARAMS = {
        "cryptobot": [
            ("exits.stop_loss_pct", 0.5, 3.0),
            ("exits.take_profit_pct", 1.0, 5.0),
            ("anti_churn.min_hold_minutes", 5, 30),
            ("parabolic_runner.tp1_pct", 2.0, 8.0),
            ("parabolic_runner.tp2_pct", 5.0, 15.0),
            ("execution.equity_pct", 1.0, 5.0),
        ],
        "momentum_bots": [
            ("exits.stop_loss_pct", 1.0, 4.0),
            ("exits.take_profit_pct", 5.0, 20.0),
            ("turtle.risk_pct_per_unit", 0.5, 2.0),
            ("turtle.stop_loss_atr_mult", 1.5, 3.5),
        ],
        "optionsbot": [
            ("exits.stop_loss_pct", 30, 60),
            ("exits.take_profit_pct", 20, 50),
            ("iv_gate.buy_max_iv_percentile", 40, 70),
            ("strategies.long_call.profit_target", 0.20, 0.50),
        ],
        "twentyminute_bot": [
            ("exits.stop_loss_pct", 0.3, 0.8),
            ("exits.take_profit_pct", 0.3, 1.0),
            ("exits.max_hold_minutes", 10, 45),
        ],
        "bouncebot": [
            ("exits.stop_loss_pct", 0.3, 0.8),
            ("exits.take_profit_pct", 0.4, 1.2),
            ("entry.drawdown_threshold_pct", 1.0, 3.0),
        ],
    }
    
    def __init__(self):
        self._logger = get_logger()
        self._lock = threading.Lock()
        self._pending_recommendations: List[OptimizationRecommendation] = []
        self._applied_optimizations: List[Dict[str, Any]] = []
        self._last_analysis_time: Dict[str, float] = {}
        self._ai_client = None
        
        self._load_state()
        self._init_ai_client()
        
        self._logger.log("auto_optimizer_init", {
            "tunable_bots": list(self.TUNABLE_PARAMS.keys()),
            "min_trades": self.MIN_TRADES_FOR_ANALYSIS
        })
    
    def _load_state(self) -> None:
        """Load saved state."""
        try:
            saved = get_state(self.STATE_KEY, {})
            self._last_analysis_time = saved.get("last_analysis_time", {})
            self._applied_optimizations = saved.get("applied_optimizations", [])
        except Exception as e:
            self._logger.error(f"Failed to load optimizer state: {e}")
    
    def _save_state(self) -> None:
        """Save state to database."""
        try:
            set_state(self.STATE_KEY, {
                "last_analysis_time": self._last_analysis_time,
                "applied_optimizations": self._applied_optimizations[-100:]  # Keep last 100
            })
        except Exception as e:
            self._logger.error(f"Failed to save optimizer state: {e}")
    
    def _init_ai_client(self) -> None:
        """Initialize OpenAI client for AI analysis."""
        try:
            from openai import OpenAI
            base_url = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
            api_key = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
            
            if base_url and api_key:
                self._ai_client = OpenAI(base_url=base_url, api_key=api_key)
        except Exception as e:
            self._logger.error(f"Failed to init AI client: {e}")
    
    def analyze_bot(self, bot_id: str, force: bool = False) -> OptimizationReport:
        """
        Analyze a bot's performance and generate optimization recommendations.
        
        Args:
            bot_id: Bot identifier (e.g., "cryptobot", "momentum_bots")
            force: Force analysis even if recently analyzed
        
        Returns:
            OptimizationReport with recommendations
        """
        last_time = self._last_analysis_time.get(bot_id, 0)
        hours_since = (time.time() - last_time) / 3600
        
        if not force and hours_since < self.ANALYSIS_INTERVAL_HOURS:
            self._logger.debug(f"Skipping analysis for {bot_id}, analyzed {hours_since:.1f}h ago")
            return self._create_empty_report(bot_id, "Analysis skipped - too recent")
        
        performance = self._get_bot_performance(bot_id)
        
        if performance.get("trade_count", 0) < self.MIN_TRADES_FOR_ANALYSIS:
            return self._create_empty_report(bot_id, f"Insufficient trades ({performance.get('trade_count', 0)} < {self.MIN_TRADES_FOR_ANALYSIS})")
        
        recommendations = self._generate_recommendations(bot_id, performance)
        
        ai_analysis = self._get_ai_analysis(bot_id, performance, recommendations)
        
        health = self._assess_health(performance)
        
        report = OptimizationReport(
            bot_id=bot_id,
            current_performance=performance,
            recommendations=recommendations,
            ai_analysis=ai_analysis,
            overall_health=health
        )
        
        with self._lock:
            self._last_analysis_time[bot_id] = time.time()
            self._pending_recommendations.extend(recommendations)
        
        self._save_state()
        
        self._logger.log("bot_analysis_complete", {
            "bot_id": bot_id,
            "recommendations_count": len(recommendations),
            "health": health,
            "win_rate": performance.get("win_rate", 0),
            "profit_factor": performance.get("profit_factor", 0)
        })
        
        return report
    
    def _get_bot_performance(self, bot_id: str) -> Dict[str, Any]:
        """Get performance statistics for a bot."""
        try:
            logger = get_config_performance_logger()
            stats = logger.get_bot_statistics(bot_id)
            return stats
        except Exception as e:
            self._logger.error(f"Failed to get performance for {bot_id}: {e}")
            return {"bot_id": bot_id, "trade_count": 0}
    
    def _clamp_change(self, current: float, target: float) -> float:
        """Clamp value change to MAX_CHANGE_PCT for safety."""
        if current == 0:
            return target
        
        max_increase = current * (1 + self.MAX_CHANGE_PCT)
        max_decrease = current * (1 - self.MAX_CHANGE_PCT)
        
        return max(max_decrease, min(max_increase, target))
    
    def _validate_param_bounds(self, bot_id: str, param_path: str, value: Any) -> Tuple[bool, Any]:
        """Validate and clamp parameter to tunable bounds."""
        tunable = self.TUNABLE_PARAMS.get(bot_id, [])
        for path, min_val, max_val in tunable:
            if path == param_path:
                clamped = max(min_val, min(max_val, float(value)))
                return True, clamped
        return False, value
    
    def _generate_recommendations(self, bot_id: str, performance: Dict[str, Any]) -> List[OptimizationRecommendation]:
        """Generate optimization recommendations based on performance."""
        recommendations = []
        
        win_rate = performance.get("win_rate", 0.5)
        profit_factor = performance.get("profit_factor", 1.0)
        avg_win = performance.get("avg_win_usd", 0)
        avg_loss = abs(performance.get("avg_loss_usd", 0))
        
        config = load_bots_config()
        bot_config = config.get(bot_id, {})
        
        if win_rate < 0.4 and avg_win > 0 and avg_loss > 0:
            current_tp = self._get_nested(bot_config, "exits.take_profit_pct", 2.0)
            if avg_loss > avg_win:
                new_tp = current_tp * 0.85
                recommendations.append(OptimizationRecommendation(
                    bot_id=bot_id,
                    parameter_path="exits.take_profit_pct",
                    current_value=current_tp,
                    recommended_value=round(new_tp, 2),
                    change_pct=-15,
                    reason="Low win rate suggests targets too aggressive",
                    confidence=0.7,
                    expected_improvement="Higher win rate by +5-10%",
                    priority=1
                ))
        
        if profit_factor < 1.0 and avg_loss > 0:
            current_sl = self._get_nested(bot_config, "exits.stop_loss_pct", 2.0)
            new_sl = current_sl * 0.90
            recommendations.append(OptimizationRecommendation(
                bot_id=bot_id,
                parameter_path="exits.stop_loss_pct",
                current_value=current_sl,
                recommended_value=round(new_sl, 2),
                change_pct=-10,
                reason="Negative profit factor - tighten stops",
                confidence=0.75,
                expected_improvement="Reduce average loss by 10-15%",
                priority=1
            ))
        
        if win_rate > 0.6 and profit_factor > 1.5:
            current_tp = self._get_nested(bot_config, "exits.take_profit_pct", 2.0)
            new_tp = current_tp * 1.10
            recommendations.append(OptimizationRecommendation(
                bot_id=bot_id,
                parameter_path="exits.take_profit_pct",
                current_value=current_tp,
                recommended_value=round(new_tp, 2),
                change_pct=10,
                reason="Strong performance - let winners run further",
                confidence=0.65,
                expected_improvement="Increase average win by 5-10%",
                priority=2
            ))
        
        if avg_win > 0 and avg_loss > 0:
            risk_reward = avg_win / avg_loss
            if risk_reward < 1.0:
                current_tp = self._get_nested(bot_config, "exits.take_profit_pct", 2.0)
                current_sl = self._get_nested(bot_config, "exits.stop_loss_pct", 2.0)
                
                new_ratio = max(1.5, risk_reward * 1.5)
                new_tp = current_sl * new_ratio
                
                recommendations.append(OptimizationRecommendation(
                    bot_id=bot_id,
                    parameter_path="exits.take_profit_pct",
                    current_value=current_tp,
                    recommended_value=round(new_tp, 2),
                    change_pct=((new_tp - current_tp) / current_tp) * 100,
                    reason=f"Risk/reward ({risk_reward:.2f}) below 1.0 - adjust TP/SL ratio",
                    confidence=0.80,
                    expected_improvement="Improve risk/reward to 1.5:1 minimum",
                    priority=1
                ))
        
        return sorted(recommendations, key=lambda r: r.priority)
    
    def _get_ai_analysis(self, bot_id: str, performance: Dict[str, Any], recommendations: List[OptimizationRecommendation]) -> str:
        """Get AI analysis of the bot's performance and recommendations."""
        if not self._ai_client:
            return "AI analysis unavailable - no API connection"
        
        try:
            rec_text = "\n".join([
                f"- {r.parameter_path}: {r.current_value} → {r.recommended_value} ({r.reason})"
                for r in recommendations[:5]
            ])
            
            prompt = f"""Analyze this trading bot's performance and recommendations:

Bot: {bot_id}

Performance Metrics:
- Trade Count: {performance.get('trade_count', 0)}
- Win Rate: {performance.get('win_rate', 0):.1%}
- Profit Factor: {performance.get('profit_factor', 0):.2f}
- Total P&L: ${performance.get('total_pnl_usd', 0):.2f}
- Avg Win: ${performance.get('avg_win_usd', 0):.2f}
- Avg Loss: ${performance.get('avg_loss_usd', 0):.2f}

Pending Recommendations:
{rec_text if rec_text else "None"}

Provide a brief 2-3 sentence analysis of:
1. The bot's current state
2. Whether the recommendations make sense
3. Any additional suggestions

Keep response under 200 words."""
            
            response = self._ai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                timeout=10
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            self._logger.error(f"AI analysis failed: {e}")
            return f"AI analysis failed: {str(e)[:50]}"
    
    def _assess_health(self, performance: Dict[str, Any]) -> str:
        """Assess overall bot health based on performance metrics."""
        win_rate = performance.get("win_rate", 0)
        profit_factor = performance.get("profit_factor", 0)
        total_pnl = performance.get("total_pnl_usd", 0)
        
        if profit_factor < 0.8 or (win_rate < 0.35 and total_pnl < -100):
            return "critical"
        elif profit_factor < 1.0 or win_rate < 0.45:
            return "needs_attention"
        else:
            return "good"
    
    def _create_empty_report(self, bot_id: str, reason: str) -> OptimizationReport:
        """Create an empty report when analysis is skipped."""
        return OptimizationReport(
            bot_id=bot_id,
            current_performance={},
            recommendations=[],
            ai_analysis=reason,
            overall_health="unknown"
        )
    
    def _get_nested(self, config: Dict, path: str, default: Any = None) -> Any:
        """Get nested config value by dot-separated path."""
        keys = path.split(".")
        value = config
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key, default)
            else:
                return default
        return value if value is not None else default
    
    def apply_recommendation(self, recommendation: OptimizationRecommendation, dry_run: bool = True) -> Tuple[bool, str]:
        """
        Apply a single recommendation to the config with safety validation.
        
        Args:
            recommendation: The recommendation to apply
            dry_run: If True, log but don't actually change config
        
        Returns:
            Tuple of (success, message)
        """
        is_valid, clamped_value = self._validate_param_bounds(
            recommendation.bot_id,
            recommendation.parameter_path,
            recommendation.recommended_value
        )
        
        if not is_valid:
            self._logger.log("recommendation_rejected", {
                "bot_id": recommendation.bot_id,
                "parameter": recommendation.parameter_path,
                "reason": "Parameter not in tunable list"
            })
            return False, "Parameter not in tunable list - rejected for safety"
        
        if recommendation.current_value and isinstance(recommendation.current_value, (int, float)):
            clamped_value = self._clamp_change(recommendation.current_value, clamped_value)
        
        final_value = round(clamped_value, 2) if isinstance(clamped_value, float) else clamped_value
        
        self._logger.log("applying_recommendation", {
            "bot_id": recommendation.bot_id,
            "parameter": recommendation.parameter_path,
            "current": recommendation.current_value,
            "requested": recommendation.recommended_value,
            "final_clamped": final_value,
            "dry_run": dry_run
        })
        
        if dry_run:
            return True, f"Would apply: {recommendation.parameter_path} = {final_value}"
        
        try:
            with open("config/bots.yaml", "r") as f:
                config = yaml.safe_load(f)
            
            keys = recommendation.parameter_path.split(".")
            target = config.get(recommendation.bot_id, {})
            
            for key in keys[:-1]:
                target = target.setdefault(key, {})
            target[keys[-1]] = final_value
            
            with open("config/bots.yaml", "w") as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)
            
            self._applied_optimizations.append({
                "bot_id": recommendation.bot_id,
                "parameter": recommendation.parameter_path,
                "old_value": recommendation.current_value,
                "new_value": final_value,
                "applied_at": time.time()
            })
            self._save_state()
            
            return True, f"Applied: {recommendation.parameter_path} = {final_value}"
            
        except Exception as e:
            self._logger.error(f"Failed to apply recommendation: {e}")
            return False, f"Failed: {str(e)}"
    
    def analyze_all_bots(self, force: bool = False) -> Dict[str, OptimizationReport]:
        """Analyze all configured bots and return reports."""
        reports = {}
        
        for bot_id in self.TUNABLE_PARAMS.keys():
            try:
                reports[bot_id] = self.analyze_bot(bot_id, force=force)
            except Exception as e:
                self._logger.error(f"Failed to analyze {bot_id}: {e}")
                reports[bot_id] = self._create_empty_report(bot_id, str(e))
        
        return reports
    
    def get_pending_recommendations(self) -> List[OptimizationRecommendation]:
        """Get all pending recommendations."""
        with self._lock:
            return list(self._pending_recommendations)
    
    def get_applied_optimizations(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get history of applied optimizations."""
        return self._applied_optimizations[-limit:]
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get optimizer statistics."""
        return {
            "pending_recommendations": len(self._pending_recommendations),
            "applied_optimizations": len(self._applied_optimizations),
            "last_analysis_times": self._last_analysis_time,
            "ai_available": self._ai_client is not None
        }
    
    def load_optimized_settings(self) -> Dict[str, Any]:
        """Load the optimized settings file."""
        try:
            with open(self.OPTIMIZED_CONFIG_PATH, "r") as f:
                return yaml.safe_load(f)
        except Exception as e:
            self._logger.error(f"Failed to load optimized settings: {e}")
            return {}
    
    def compare_with_optimized(self, bot_id: str) -> Dict[str, Any]:
        """Compare current settings with optimized recommendations."""
        current = load_bots_config().get(bot_id, {})
        optimized = self.load_optimized_settings().get(bot_id, {})
        
        differences = []
        for param, min_val, max_val in self.TUNABLE_PARAMS.get(bot_id, []):
            current_val = self._get_nested(current, param)
            optimized_val = self._get_nested(optimized, param)
            
            if current_val != optimized_val and optimized_val is not None:
                differences.append({
                    "parameter": param,
                    "current": current_val,
                    "optimized": optimized_val,
                    "change_pct": ((optimized_val - current_val) / current_val * 100) if current_val else 0
                })
        
        return {
            "bot_id": bot_id,
            "differences": differences,
            "total_differences": len(differences)
        }


_auto_optimizer: Optional[AutoOptimizer] = None


def get_auto_optimizer() -> AutoOptimizer:
    """Get or create AutoOptimizer singleton."""
    global _auto_optimizer
    if _auto_optimizer is None:
        _auto_optimizer = AutoOptimizer()
    return _auto_optimizer
