"""
RiskAdjustmentEngine - Dynamic Risk Adjustment Model.

Uses account metrics to automatically adjust daily risk limits:
- Reduces risk on losing streaks, drawdowns, low win rates
- Increases risk cautiously during winning periods
- Considers market regime for additional context
"""

from typing import Dict, Any, List
import numpy as np
from sklearn.ensemble import GradientBoostingRegressor

from ..base_model import BaseModelService


class RiskAdjustmentEngine(BaseModelService):
    """
    ML model for dynamic risk adjustment.
    
    Predicts optimal risk multiplier (0.25-1.5) based on:
    - Recent equity curve trajectory
    - Current drawdown level
    - Win rate trends
    - Market regime indicators
    """
    
    DEFAULT_RISK = 1.0
    MIN_RISK = 0.25
    MAX_RISK = 1.5
    
    def __init__(self):
        super().__init__("risk_adjustment")
    
    def get_feature_names(self) -> List[str]:
        """Return the list of feature names used by this model."""
        return [
            "daily_pnl_pct_3d_avg",
            "daily_pnl_pct_7d_avg",
            "current_drawdown_pct",
            "max_drawdown_pct",
            "win_rate_7d",
            "profit_factor_7d",
            "losing_streak",
            "winning_streak",
            "vix",
            "position_size_multiplier",
            "equity_trend_7d",
            "volatility_7d"
        ]
    
    def extract_features(self, context: Dict[str, Any]) -> List[float]:
        """
        Extract feature vector from account context.
        
        Args:
            context: Dictionary containing:
                - daily_metrics: List of recent DailyMetrics
                - regime: Current MarketRegimeAnalysis
        """
        daily_metrics = context.get("daily_metrics", [])
        regime = context.get("regime", {})
        
        pnl_3d = self._calc_avg_pnl(daily_metrics, 3)
        pnl_7d = self._calc_avg_pnl(daily_metrics, 7)
        
        latest = daily_metrics[-1] if daily_metrics else {}
        current_dd = getattr(latest, "current_drawdown_pct", 0.0) if hasattr(latest, "current_drawdown_pct") else latest.get("current_drawdown_pct", 0.0)
        max_dd = getattr(latest, "max_drawdown_pct", 0.0) if hasattr(latest, "max_drawdown_pct") else latest.get("max_drawdown_pct", 0.0)
        
        win_rate_7d = self._calc_win_rate(daily_metrics, 7)
        profit_factor_7d = self._calc_profit_factor(daily_metrics, 7)
        
        losing_streak = self._calc_streak(daily_metrics, losing=True)
        winning_streak = self._calc_streak(daily_metrics, losing=False)
        
        vix = getattr(regime, "vix", 18.0) if hasattr(regime, "vix") else regime.get("vix", 18.0)
        pos_mult = getattr(regime, "position_size_multiplier", 1.0) if hasattr(regime, "position_size_multiplier") else regime.get("position_size_multiplier", 1.0)
        
        equity_trend = self._calc_equity_trend(daily_metrics, 7)
        volatility_7d = self._calc_volatility(daily_metrics, 7)
        
        return [
            pnl_3d, pnl_7d, current_dd, max_dd,
            win_rate_7d, profit_factor_7d,
            losing_streak, winning_streak,
            vix, pos_mult, equity_trend, volatility_7d
        ]
    
    def predict(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Predict optimal risk multiplier.
        
        Returns:
            Dictionary with:
                - risk_multiplier: Recommended risk multiplier (0.25-1.5)
                - confidence: Model confidence
                - adjustment_reason: Human-readable reason
        """
        features = self.extract_features(context)
        feature_array = np.array([features])
        
        raw_prediction = self._model.predict(feature_array)[0]
        risk_mult = max(self.MIN_RISK, min(self.MAX_RISK, raw_prediction))
        
        change = risk_mult - self.DEFAULT_RISK
        if change < -0.3:
            reason = "significant_risk_reduction"
        elif change < -0.1:
            reason = "moderate_risk_reduction"
        elif change > 0.3:
            reason = "risk_increase"
        elif change > 0.1:
            reason = "slight_risk_increase"
        else:
            reason = "risk_stable"
        
        confidence = min(1.0, 0.5 + abs(change))
        
        return {
            "risk_multiplier": round(risk_mult, 3),
            "confidence": round(confidence, 3),
            "adjustment_reason": reason,
            "features_used": dict(zip(self.get_feature_names(), features)),
            "model_version": self._config.get("version", "1.0")
        }
    
    def fallback_predict(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Rule-based fallback for risk adjustment."""
        daily_metrics = context.get("daily_metrics", [])
        regime = context.get("regime", {})
        
        risk_mult = self.DEFAULT_RISK
        reasons = []
        
        if daily_metrics:
            latest = daily_metrics[-1]
            current_dd = getattr(latest, "current_drawdown_pct", 0.0) if hasattr(latest, "current_drawdown_pct") else latest.get("current_drawdown_pct", 0.0)
            
            if current_dd > 10:
                risk_mult *= 0.5
                reasons.append("high_drawdown")
            elif current_dd > 5:
                risk_mult *= 0.75
                reasons.append("elevated_drawdown")
        
        losing_streak = self._calc_streak(daily_metrics, losing=True)
        if losing_streak >= 5:
            risk_mult *= 0.5
            reasons.append("losing_streak_5+")
        elif losing_streak >= 3:
            risk_mult *= 0.75
            reasons.append("losing_streak_3+")
        
        win_rate = self._calc_win_rate(daily_metrics, 7)
        if win_rate < 0.35:
            risk_mult *= 0.7
            reasons.append("low_win_rate")
        
        vix = getattr(regime, "vix", 18.0) if hasattr(regime, "vix") else regime.get("vix", 18.0)
        if vix > 30:
            risk_mult *= 0.6
            reasons.append("high_vix")
        elif vix > 25:
            risk_mult *= 0.8
            reasons.append("elevated_vix")
        
        winning_streak = self._calc_streak(daily_metrics, losing=False)
        pnl_7d = self._calc_avg_pnl(daily_metrics, 7)
        if winning_streak >= 5 and pnl_7d > 1.0 and vix < 20:
            risk_mult = min(self.MAX_RISK, risk_mult * 1.2)
            reasons.append("strong_performance")
        
        risk_mult = max(self.MIN_RISK, min(self.MAX_RISK, risk_mult))
        
        return {
            "risk_multiplier": round(risk_mult, 3),
            "confidence": 0.3,
            "adjustment_reason": "_".join(reasons) if reasons else "fallback_default",
            "model_version": "fallback_v1"
        }
    
    def train(self, training_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Train the risk adjustment model.
        
        Training data should include:
        - Features from extract_features
        - Label: optimal_risk_multiplier (based on next-day outcomes)
        """
        if len(training_data) < 30:
            return {"status": "insufficient_data", "count": len(training_data)}
        
        feature_names = self.get_feature_names()
        X = []
        y = []
        
        for record in training_data:
            features = [record.get(fname, 0.0) for fname in feature_names]
            label = record.get("optimal_risk_multiplier", 1.0)
            X.append(features)
            y.append(label)
        
        X = np.array(X)
        y = np.array(y)
        
        means, stds = self._calculate_feature_stats(training_data)
        
        model = GradientBoostingRegressor(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            random_state=42
        )
        model.fit(X, y)
        
        predictions = model.predict(X)
        mse = np.mean((predictions - y) ** 2)
        mae = np.mean(np.abs(predictions - y))
        
        config = {
            "version": "1.0",
            "features": feature_names,
            "feature_means": means,
            "feature_stds": stds,
            "mse": float(mse),
            "mae": float(mae),
            "training_samples": len(training_data)
        }
        
        self._save_model(model, config)
        
        return {
            "status": "success",
            "mse": float(mse),
            "mae": float(mae),
            "samples": len(training_data)
        }
    
    def _calc_avg_pnl(self, metrics: List, days: int) -> float:
        """Calculate average P&L percentage over N days."""
        if not metrics:
            return 0.0
        recent = metrics[-days:] if len(metrics) >= days else metrics
        pnls = []
        for m in recent:
            pnl = getattr(m, "daily_pnl_pct", 0.0) if hasattr(m, "daily_pnl_pct") else m.get("daily_pnl_pct", 0.0)
            pnls.append(pnl)
        return sum(pnls) / len(pnls) if pnls else 0.0
    
    def _calc_win_rate(self, metrics: List, days: int) -> float:
        """Calculate win rate over N days."""
        if not metrics:
            return 0.5
        recent = metrics[-days:] if len(metrics) >= days else metrics
        wins = 0
        total = 0
        for m in recent:
            w = getattr(m, "winning_trades", 0) if hasattr(m, "winning_trades") else m.get("winning_trades", 0)
            l = getattr(m, "losing_trades", 0) if hasattr(m, "losing_trades") else m.get("losing_trades", 0)
            wins += w
            total += w + l
        return wins / total if total > 0 else 0.5
    
    def _calc_profit_factor(self, metrics: List, days: int) -> float:
        """Calculate profit factor over N days."""
        if not metrics:
            return 1.0
        recent = metrics[-days:] if len(metrics) >= days else metrics
        total_wins = 0.0
        total_losses = 0.0
        for m in recent:
            avg_win = getattr(m, "avg_win", 0.0) if hasattr(m, "avg_win") else m.get("avg_win", 0.0)
            avg_loss = getattr(m, "avg_loss", 0.0) if hasattr(m, "avg_loss") else m.get("avg_loss", 0.0)
            w = getattr(m, "winning_trades", 0) if hasattr(m, "winning_trades") else m.get("winning_trades", 0)
            l = getattr(m, "losing_trades", 0) if hasattr(m, "losing_trades") else m.get("losing_trades", 0)
            total_wins += avg_win * w
            total_losses += abs(avg_loss) * l
        return total_wins / total_losses if total_losses > 0 else 1.0
    
    def _calc_streak(self, metrics: List, losing: bool = True) -> int:
        """Calculate current winning/losing streak."""
        if not metrics:
            return 0
        streak = 0
        for m in reversed(metrics):
            pnl = getattr(m, "daily_pnl", 0.0) if hasattr(m, "daily_pnl") else m.get("daily_pnl", 0.0)
            if losing and pnl < 0:
                streak += 1
            elif not losing and pnl > 0:
                streak += 1
            else:
                break
        return streak
    
    def _calc_equity_trend(self, metrics: List, days: int) -> float:
        """Calculate equity trend (slope) over N days."""
        if len(metrics) < 2:
            return 0.0
        recent = metrics[-days:] if len(metrics) >= days else metrics
        equities = []
        for m in recent:
            eq = getattr(m, "equity", 0.0) if hasattr(m, "equity") else m.get("equity", 0.0)
            equities.append(eq)
        if len(equities) < 2:
            return 0.0
        x = np.arange(len(equities))
        slope, _ = np.polyfit(x, equities, 1)
        return slope / equities[0] * 100 if equities[0] > 0 else 0.0
    
    def _calc_volatility(self, metrics: List, days: int) -> float:
        """Calculate daily P&L volatility over N days."""
        if len(metrics) < 2:
            return 0.0
        recent = metrics[-days:] if len(metrics) >= days else metrics
        pnls = []
        for m in recent:
            pnl = getattr(m, "daily_pnl_pct", 0.0) if hasattr(m, "daily_pnl_pct") else m.get("daily_pnl_pct", 0.0)
            pnls.append(pnl)
        return float(np.std(pnls)) if pnls else 0.0
