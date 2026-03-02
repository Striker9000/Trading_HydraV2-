"""
DrawdownPredictor - Drawdown Prediction Model.

Predicts the probability of experiencing a significant drawdown
in the next N days based on account and market conditions.
"""

from typing import Dict, Any, List
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier

from ..base_model import BaseModelService


class DrawdownPredictor(BaseModelService):
    """
    ML model for drawdown prediction.
    
    Predicts probability of significant drawdown based on:
    - Current equity curve momentum
    - Recent volatility patterns
    - Market regime indicators
    - Historical drawdown patterns
    """
    
    DRAWDOWN_THRESHOLD = 3.0
    
    def __init__(self):
        super().__init__("drawdown_predictor")
    
    def get_feature_names(self) -> List[str]:
        """Return the list of feature names used by this model."""
        return [
            "equity_momentum_3d",
            "equity_momentum_7d",
            "current_drawdown_pct",
            "max_drawdown_pct",
            "drawdown_velocity",
            "pnl_volatility_7d",
            "pnl_volatility_14d",
            "losing_streak",
            "win_rate_7d",
            "profit_factor_7d",
            "vix",
            "vix_change_5d",
            "vvix",
            "sentiment_encoded",
            "risk_multiplier",
            "open_positions",
            "position_concentration"
        ]
    
    def extract_features(self, context: Dict[str, Any]) -> List[float]:
        """
        Extract feature vector from account/regime context.
        
        Args:
            context: Dictionary containing:
                - daily_metrics: List of recent DailyMetrics
                - regime: Current MarketRegimeAnalysis
                - regime_history: List of recent RegimeSnapshots
        """
        daily_metrics = context.get("daily_metrics", [])
        regime = context.get("regime", {})
        regime_history = context.get("regime_history", [])
        
        eq_mom_3d = self._calc_momentum(daily_metrics, 3)
        eq_mom_7d = self._calc_momentum(daily_metrics, 7)
        
        latest = daily_metrics[-1] if daily_metrics else {}
        current_dd = getattr(latest, "current_drawdown_pct", 0.0) if hasattr(latest, "current_drawdown_pct") else latest.get("current_drawdown_pct", 0.0)
        max_dd = getattr(latest, "max_drawdown_pct", 0.0) if hasattr(latest, "max_drawdown_pct") else latest.get("max_drawdown_pct", 0.0)
        
        dd_velocity = self._calc_drawdown_velocity(daily_metrics, 3)
        
        pnl_vol_7d = self._calc_pnl_volatility(daily_metrics, 7)
        pnl_vol_14d = self._calc_pnl_volatility(daily_metrics, 14)
        
        losing_streak = self._calc_losing_streak(daily_metrics)
        win_rate_7d = self._calc_win_rate(daily_metrics, 7)
        pf_7d = self._calc_profit_factor(daily_metrics, 7)
        
        vix = getattr(regime, "vix", 18.0) if hasattr(regime, "vix") else regime.get("vix", 18.0)
        vvix = getattr(regime, "vvix", 100.0) if hasattr(regime, "vvix") else regime.get("vvix", 100.0)
        
        vix_change_5d = 0.0
        if len(regime_history) >= 6:
            prev_vix = getattr(regime_history[-6], "vix", vix) if hasattr(regime_history[-6], "vix") else regime_history[-6].get("vix", vix)
            vix_change_5d = vix - prev_vix
        
        sentiment = getattr(regime, "sentiment", "neutral") if hasattr(regime, "sentiment") else regime.get("sentiment", "neutral")
        if hasattr(sentiment, "value"):
            sentiment = sentiment.value
        sent_map = {"risk_on": 0, "neutral": 1, "risk_off": 2, "extreme_fear": 3}
        sent_encoded = sent_map.get(sentiment, 1)
        
        risk_mult = getattr(latest, "risk_multiplier", 1.0) if hasattr(latest, "risk_multiplier") else latest.get("risk_multiplier", 1.0)
        open_pos = getattr(latest, "open_positions", 0) if hasattr(latest, "open_positions") else latest.get("open_positions", 0)
        
        pos_conc = self._calc_position_concentration(latest)
        
        return [
            eq_mom_3d, eq_mom_7d, current_dd, max_dd, dd_velocity,
            pnl_vol_7d, pnl_vol_14d, losing_streak, win_rate_7d, pf_7d,
            vix, vix_change_5d, vvix, sent_encoded,
            risk_mult, open_pos, pos_conc
        ]
    
    def predict(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Predict drawdown probability.
        
        Returns:
            Dictionary with:
                - drawdown_probability: Probability of >3% drawdown in next 5 days
                - risk_level: low/medium/high/critical
                - recommendation: Action recommendation
                - confidence: Model confidence
        """
        features = self.extract_features(context)
        feature_array = np.array([features])
        
        proba = self._model.predict_proba(feature_array)[0]
        dd_prob = float(proba[1]) if len(proba) > 1 else 0.5
        
        if dd_prob < 0.2:
            risk_level = "low"
            recommendation = "normal_operations"
        elif dd_prob < 0.4:
            risk_level = "medium"
            recommendation = "monitor_closely"
        elif dd_prob < 0.6:
            risk_level = "high"
            recommendation = "reduce_positions"
        else:
            risk_level = "critical"
            recommendation = "halt_new_entries"
        
        confidence = abs(dd_prob - 0.5) * 2
        
        return {
            "drawdown_probability": round(dd_prob, 3),
            "risk_level": risk_level,
            "recommendation": recommendation,
            "confidence": round(confidence, 3),
            "threshold_used": self.DRAWDOWN_THRESHOLD,
            "model_version": self._config.get("version", "1.0")
        }
    
    def fallback_predict(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Rule-based fallback for drawdown prediction."""
        daily_metrics = context.get("daily_metrics", [])
        regime = context.get("regime", {})
        
        risk_score = 0.0
        
        if daily_metrics:
            latest = daily_metrics[-1]
            current_dd = getattr(latest, "current_drawdown_pct", 0.0) if hasattr(latest, "current_drawdown_pct") else latest.get("current_drawdown_pct", 0.0)
            
            if current_dd > 5:
                risk_score += 0.3
            elif current_dd > 2:
                risk_score += 0.15
        
        losing_streak = self._calc_losing_streak(daily_metrics)
        if losing_streak >= 5:
            risk_score += 0.25
        elif losing_streak >= 3:
            risk_score += 0.15
        
        pnl_vol = self._calc_pnl_volatility(daily_metrics, 7)
        if pnl_vol > 3.0:
            risk_score += 0.2
        elif pnl_vol > 1.5:
            risk_score += 0.1
        
        vix = getattr(regime, "vix", 18.0) if hasattr(regime, "vix") else regime.get("vix", 18.0)
        if vix > 30:
            risk_score += 0.2
        elif vix > 25:
            risk_score += 0.1
        
        dd_prob = min(1.0, risk_score)
        
        if dd_prob < 0.2:
            risk_level = "low"
            recommendation = "normal_operations"
        elif dd_prob < 0.4:
            risk_level = "medium"
            recommendation = "monitor_closely"
        elif dd_prob < 0.6:
            risk_level = "high"
            recommendation = "reduce_positions"
        else:
            risk_level = "critical"
            recommendation = "halt_new_entries"
        
        return {
            "drawdown_probability": round(dd_prob, 3),
            "risk_level": risk_level,
            "recommendation": recommendation,
            "confidence": 0.3,
            "threshold_used": self.DRAWDOWN_THRESHOLD,
            "model_version": "fallback_v1"
        }
    
    def train(self, training_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Train the drawdown predictor model.
        
        Training data should include:
        - Features from extract_features
        - Label: had_drawdown (1 if >3% drawdown in next 5 days, 0 otherwise)
        """
        if len(training_data) < 30:
            return {"status": "insufficient_data", "count": len(training_data)}
        
        feature_names = self.get_feature_names()
        X = []
        y = []
        
        for record in training_data:
            features = [record.get(fname, 0.0) for fname in feature_names]
            label = record.get("had_drawdown", 0)
            X.append(features)
            y.append(label)
        
        X = np.array(X)
        y = np.array(y)
        
        means, stds = self._calculate_feature_stats(training_data)
        
        model = GradientBoostingClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            random_state=42
        )
        model.fit(X, y)
        
        predictions = model.predict(X)
        accuracy = float(np.mean(predictions == y))
        
        proba = model.predict_proba(X)[:, 1]
        from sklearn.metrics import roc_auc_score
        try:
            auc = float(roc_auc_score(y, proba))
        except ValueError:
            auc = 0.5
        
        config = {
            "version": "1.0",
            "features": feature_names,
            "feature_means": means,
            "feature_stds": stds,
            "accuracy": accuracy,
            "auc": auc,
            "training_samples": len(training_data),
            "drawdown_threshold": self.DRAWDOWN_THRESHOLD
        }
        
        self._save_model(model, config)
        
        return {
            "status": "success",
            "accuracy": accuracy,
            "auc": auc,
            "samples": len(training_data)
        }
    
    def _calc_momentum(self, metrics: List, days: int) -> float:
        """Calculate equity momentum over N days."""
        if len(metrics) < 2:
            return 0.0
        recent = metrics[-days:] if len(metrics) >= days else metrics
        if len(recent) < 2:
            return 0.0
        
        first_eq = getattr(recent[0], "equity", 0) if hasattr(recent[0], "equity") else recent[0].get("equity", 0)
        last_eq = getattr(recent[-1], "equity", 0) if hasattr(recent[-1], "equity") else recent[-1].get("equity", 0)
        
        return (last_eq - first_eq) / first_eq * 100 if first_eq > 0 else 0.0
    
    def _calc_drawdown_velocity(self, metrics: List, days: int) -> float:
        """Calculate rate of drawdown change."""
        if len(metrics) < 2:
            return 0.0
        recent = metrics[-days:] if len(metrics) >= days else metrics
        if len(recent) < 2:
            return 0.0
        
        first_dd = getattr(recent[0], "current_drawdown_pct", 0) if hasattr(recent[0], "current_drawdown_pct") else recent[0].get("current_drawdown_pct", 0)
        last_dd = getattr(recent[-1], "current_drawdown_pct", 0) if hasattr(recent[-1], "current_drawdown_pct") else recent[-1].get("current_drawdown_pct", 0)
        
        return last_dd - first_dd
    
    def _calc_pnl_volatility(self, metrics: List, days: int) -> float:
        """Calculate P&L volatility."""
        if len(metrics) < 2:
            return 0.0
        recent = metrics[-days:] if len(metrics) >= days else metrics
        pnls = []
        for m in recent:
            pnl = getattr(m, "daily_pnl_pct", 0) if hasattr(m, "daily_pnl_pct") else m.get("daily_pnl_pct", 0)
            pnls.append(pnl)
        return float(np.std(pnls)) if pnls else 0.0
    
    def _calc_losing_streak(self, metrics: List) -> int:
        """Calculate current losing streak."""
        streak = 0
        for m in reversed(metrics):
            pnl = getattr(m, "daily_pnl", 0) if hasattr(m, "daily_pnl") else m.get("daily_pnl", 0)
            if pnl < 0:
                streak += 1
            else:
                break
        return streak
    
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
        """Calculate profit factor."""
        if not metrics:
            return 1.0
        recent = metrics[-days:] if len(metrics) >= days else metrics
        total_wins = 0.0
        total_losses = 0.0
        for m in recent:
            avg_win = getattr(m, "avg_win", 0) if hasattr(m, "avg_win") else m.get("avg_win", 0)
            avg_loss = getattr(m, "avg_loss", 0) if hasattr(m, "avg_loss") else m.get("avg_loss", 0)
            w = getattr(m, "winning_trades", 0) if hasattr(m, "winning_trades") else m.get("winning_trades", 0)
            l = getattr(m, "losing_trades", 0) if hasattr(m, "losing_trades") else m.get("losing_trades", 0)
            total_wins += avg_win * w
            total_losses += abs(avg_loss) * l
        return total_wins / total_losses if total_losses > 0 else 1.0
    
    def _calc_position_concentration(self, metrics: Any) -> float:
        """Calculate position concentration (max single position % of total)."""
        open_pos = getattr(metrics, "open_positions", 0) if hasattr(metrics, "open_positions") else metrics.get("open_positions", 0) if isinstance(metrics, dict) else 0
        if open_pos <= 0:
            return 0.0
        return 1.0 / open_pos
