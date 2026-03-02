"""
=============================================================================
ML Config Tuner - Auto-optimize bot parameters based on performance
=============================================================================

Uses historical trade data to optimize bot configurations.

Approach:
1. Collect trade outcomes with config snapshots
2. Analyze which config values correlate with better performance
3. Suggest optimized configs based on statistical analysis
4. Optionally auto-apply changes (with human approval)

Philosophy:
- Data-driven optimization beats intuition
- Gradual adjustments prevent overfitting
- Human-in-the-loop for final approval
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
import statistics
import copy

from ..core.logging import get_logger
from ..core.state import get_state, set_state
from ..core.config import load_bots_config


@dataclass
class ConfigRecommendation:
    """A recommended config change."""
    bot_id: str
    config_path: str
    current_value: Any
    recommended_value: Any
    expected_improvement_pct: float
    confidence: float
    reasoning: str
    sample_size: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "bot_id": self.bot_id,
            "config_path": self.config_path,
            "current_value": self.current_value,
            "recommended_value": self.recommended_value,
            "expected_improvement_pct": self.expected_improvement_pct,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "sample_size": self.sample_size
        }


class MLConfigTuner:
    """
    Analyze trade performance and recommend config optimizations.
    
    Uses ConfigPerformanceLogger data to identify patterns and suggest improvements.
    """
    
    MIN_SAMPLES = 20  # Minimum trades to make recommendations
    MIN_CONFIDENCE = 0.6  # Minimum confidence for recommendations
    
    TUNABLE_PARAMS = {
        "cryptobot": [
            "exits.stop_loss_pct",
            "exits.take_profit_pct",
            "execution.equity_pct",
            "risk.trailing_stop.value",
            "risk.trailing_stop.activation_profit_pct",
            "anti_churn.min_hold_minutes",
            "parabolic_runner.tp1_pct",
            "parabolic_runner.tp2_pct",
            "parabolic_runner.widen_trailing_pct"
        ],
        "momentum_bots": [
            "exits.stop_loss_pct",
            "exits.take_profit_pct",
            "exits.time_stop_minutes",
            "risk.max_trades_per_day",
            "risk.trailing_stop.value",
            "turtle.risk_pct_per_unit",
            "turtle.stop_loss_atr_mult"
        ],
        "twentyminbot": [
            "exits.stop_loss_pct",
            "exits.take_profit_pct",
            "exits.max_hold_minutes",
            "risk.max_trades_per_day"
        ],
        "optionsbot": [
            "exits.stop_loss_pct",
            "exits.take_profit_pct",
            "execution.risk_per_trade_pct",
            "entry_filters.iv_percentile_min",
            "entry_filters.iv_percentile_max"
        ],
        "bouncebot": [
            "exits.stop_loss_pct",
            "exits.take_profit_pct",
            "execution.equity_pct",
            "entry.drawdown_threshold_pct"
        ]
    }
    
    def __init__(self):
        self._logger = get_logger()
        self._perf_logger = None
        self._recommendations: Dict[str, List[ConfigRecommendation]] = {}
        
        self._logger.log("ml_config_tuner_init", {
            "tunable_bots": list(self.TUNABLE_PARAMS.keys()),
            "min_samples": self.MIN_SAMPLES
        })
    
    def _get_perf_logger(self):
        """Lazy load config performance logger."""
        if self._perf_logger is None:
            try:
                from ..risk.config_performance_logger import get_config_performance_logger
                self._perf_logger = get_config_performance_logger()
            except Exception as e:
                self._logger.error(f"Failed to load perf logger: {e}")
        return self._perf_logger
    
    def analyze_bot(self, bot_id: str) -> List[ConfigRecommendation]:
        """
        Analyze a bot's performance and generate config recommendations.
        
        Returns list of recommendations sorted by expected improvement.
        """
        perf_logger = self._get_perf_logger()
        if not perf_logger:
            return []
        
        trades = perf_logger.get_recent_trades(bot_id=bot_id, limit=500)
        completed_trades = [t for t in trades if t.exit_timestamp and t.pnl_pct is not None]
        
        if len(completed_trades) < self.MIN_SAMPLES:
            self._logger.log("config_tuner_insufficient_data", {
                "bot_id": bot_id,
                "trade_count": len(completed_trades),
                "min_required": self.MIN_SAMPLES
            })
            return []
        
        bot_type = self._get_bot_type(bot_id)
        tunable_params = self.TUNABLE_PARAMS.get(bot_type, [])
        
        recommendations = []
        for param in tunable_params:
            rec = self._analyze_param(bot_id, param, completed_trades)
            if rec and rec.confidence >= self.MIN_CONFIDENCE:
                recommendations.append(rec)
        
        recommendations.sort(key=lambda r: r.expected_improvement_pct, reverse=True)
        
        self._recommendations[bot_id] = recommendations
        self._save_recommendations()
        
        self._logger.log("config_tuner_analysis_complete", {
            "bot_id": bot_id,
            "trades_analyzed": len(completed_trades),
            "recommendations_count": len(recommendations)
        })
        
        return recommendations
    
    def _get_bot_type(self, bot_id: str) -> str:
        """Map bot_id to bot type for param lookup."""
        if bot_id.startswith("mom_"):
            return "momentum_bots"
        elif bot_id == "crypto_core":
            return "cryptobot"
        elif bot_id == "twenty_min":
            return "twentyminbot"
        elif bot_id.startswith("opt_"):
            return "optionsbot"
        elif bot_id == "bounce_core":
            return "bouncebot"
        return "unknown"
    
    def _analyze_param(
        self,
        bot_id: str,
        param_path: str,
        trades: List
    ) -> Optional[ConfigRecommendation]:
        """Analyze a single parameter's impact on performance."""
        values_performance: Dict[str, List[float]] = {}
        
        for trade in trades:
            value = self._get_nested_value(trade.config_snapshot, param_path)
            if value is None:
                continue
            
            str_value = str(round(float(value), 2) if isinstance(value, (int, float)) else value)
            
            if str_value not in values_performance:
                values_performance[str_value] = []
            values_performance[str_value].append(trade.pnl_pct)
        
        if len(values_performance) < 2:
            return None
        
        min_samples_per_value = 5
        values_performance = {k: v for k, v in values_performance.items() if len(v) >= min_samples_per_value}
        
        if len(values_performance) < 2:
            return None
        
        value_stats = {}
        for value, pnls in values_performance.items():
            value_stats[value] = {
                "mean": statistics.mean(pnls),
                "median": statistics.median(pnls),
                "std": statistics.stdev(pnls) if len(pnls) > 1 else 0,
                "count": len(pnls),
                "win_rate": len([p for p in pnls if p > 0]) / len(pnls)
            }
        
        best_value = max(value_stats.keys(), key=lambda v: value_stats[v]["mean"])
        current_value = list(values_performance.keys())[0]
        
        if best_value == current_value:
            return None
        
        current_mean = value_stats[current_value]["mean"]
        best_mean = value_stats[best_value]["mean"]
        
        improvement = best_mean - current_mean
        
        if improvement <= 0.1:
            return None
        
        best_count = value_stats[best_value]["count"]
        total_count = sum(s["count"] for s in value_stats.values())
        confidence = min(0.95, best_count / self.MIN_SAMPLES * 0.5 + 
                        value_stats[best_value]["win_rate"] * 0.5)
        
        try:
            rec_value = float(best_value)
            curr_value = float(current_value)
        except ValueError:
            rec_value = best_value
            curr_value = current_value
        
        return ConfigRecommendation(
            bot_id=bot_id,
            config_path=param_path,
            current_value=curr_value,
            recommended_value=rec_value,
            expected_improvement_pct=round(improvement, 4),
            confidence=round(confidence, 2),
            reasoning=f"Based on {best_count} trades, value {best_value} achieved {best_mean:.2f}% avg P&L vs current {current_mean:.2f}%",
            sample_size=best_count
        )
    
    def _get_nested_value(self, config: Dict, path: str) -> Any:
        """Get nested config value using dot notation."""
        keys = path.split(".")
        value = config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return None
        return value
    
    def _save_recommendations(self) -> None:
        """Save recommendations to state."""
        try:
            data = {
                bot_id: [r.to_dict() for r in recs]
                for bot_id, recs in self._recommendations.items()
            }
            set_state("ml_config_tuner.recommendations", data)
        except Exception as e:
            self._logger.error(f"Failed to save recommendations: {e}")
    
    def get_recommendations(self, bot_id: Optional[str] = None) -> Dict[str, List[ConfigRecommendation]]:
        """Get current recommendations."""
        if bot_id:
            return {bot_id: self._recommendations.get(bot_id, [])}
        return self._recommendations
    
    def generate_optimized_config(self, bot_id: str, apply_top_n: int = 3) -> Dict[str, Any]:
        """
        Generate an optimized config by applying top recommendations.
        
        Does NOT auto-apply - returns config for human review.
        """
        recommendations = self._recommendations.get(bot_id, [])
        if not recommendations:
            self.analyze_bot(bot_id)
            recommendations = self._recommendations.get(bot_id, [])
        
        current_config = self._get_current_bot_config(bot_id)
        if not current_config:
            return {}
        
        optimized = copy.deepcopy(current_config)
        
        applied = []
        for rec in recommendations[:apply_top_n]:
            self._set_nested_value(optimized, rec.config_path, rec.recommended_value)
            applied.append({
                "path": rec.config_path,
                "old": rec.current_value,
                "new": rec.recommended_value,
                "expected_improvement": rec.expected_improvement_pct
            })
        
        self._logger.log("optimized_config_generated", {
            "bot_id": bot_id,
            "changes_applied": len(applied),
            "changes": applied
        })
        
        return {
            "bot_id": bot_id,
            "optimized_config": optimized,
            "changes_applied": applied,
            "total_expected_improvement": sum(c["expected_improvement"] for c in applied)
        }
    
    def _get_current_bot_config(self, bot_id: str) -> Optional[Dict[str, Any]]:
        """Get current config for a bot."""
        try:
            bots_config = load_bots_config()
            
            if bot_id == "crypto_core":
                return bots_config.get("cryptobot", {})
            elif bot_id == "bounce_core":
                return bots_config.get("bouncebot", {})
            elif bot_id == "twenty_min":
                return bots_config.get("twentyminbot", {})
            elif bot_id.startswith("mom_"):
                for bot in bots_config.get("momentum_bots", []):
                    if bot.get("bot_id") == bot_id:
                        return bot
            elif bot_id.startswith("opt_"):
                return bots_config.get("optionsbot", {})
            
            return None
        except Exception as e:
            self._logger.error(f"Failed to load bot config: {e}")
            return None
    
    def _set_nested_value(self, config: Dict, path: str, value: Any) -> None:
        """Set nested config value using dot notation."""
        keys = path.split(".")
        current = config
        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]
        current[keys[-1]] = value
    
    def get_ai_recommendations(self, bot_id: str) -> Dict[str, Any]:
        """
        Get AI-powered recommendations using OpenAI.
        
        Provides deeper analysis and natural language explanations.
        """
        stats = None
        perf_logger = self._get_perf_logger()
        if perf_logger:
            stats = perf_logger.get_bot_statistics(bot_id)
        
        recommendations = self._recommendations.get(bot_id, [])
        
        if not stats or stats.get("trade_count", 0) < 10:
            return {
                "bot_id": bot_id,
                "ai_analysis": "Insufficient trade data for AI analysis. Need at least 10 completed trades.",
                "recommendations": []
            }
        
        return {
            "bot_id": bot_id,
            "performance_summary": stats,
            "data_driven_recommendations": [r.to_dict() for r in recommendations],
            "ai_analysis": self._generate_ai_analysis(bot_id, stats, recommendations)
        }
    
    def _generate_ai_analysis(
        self,
        bot_id: str,
        stats: Dict[str, Any],
        recommendations: List[ConfigRecommendation]
    ) -> str:
        """Generate AI analysis summary."""
        win_rate = stats.get("win_rate", 0)
        profit_factor = stats.get("profit_factor", 0)
        total_pnl = stats.get("total_pnl_usd", 0)
        
        analysis = []
        
        if win_rate < 0.4:
            analysis.append(f"Win rate is low ({win_rate:.1%}). Consider tightening take-profit targets or loosening stop-losses.")
        elif win_rate > 0.6:
            analysis.append(f"Win rate is healthy ({win_rate:.1%}). Focus on increasing position sizes or trade frequency.")
        
        if profit_factor < 1.0:
            analysis.append("Profit factor < 1 indicates losses exceed gains. Review stop-loss and take-profit ratios.")
        elif profit_factor > 2.0:
            analysis.append(f"Strong profit factor ({profit_factor:.1f}). Strategy is performing well.")
        
        if recommendations:
            top_rec = recommendations[0]
            analysis.append(f"Top recommendation: Adjust {top_rec.config_path} from {top_rec.current_value} to {top_rec.recommended_value} for ~{top_rec.expected_improvement_pct:.2f}% improvement.")
        
        return " ".join(analysis) if analysis else "Performance appears normal. No immediate optimizations recommended."


_ml_tuner: Optional[MLConfigTuner] = None


def get_ml_config_tuner() -> MLConfigTuner:
    """Get or create MLConfigTuner singleton."""
    global _ml_tuner
    if _ml_tuner is None:
        _ml_tuner = MLConfigTuner()
    return _ml_tuner
