"""
AnomalyDetector - Performance Anomaly Detection Model.

Detects unusual account behavior that may indicate:
- System malfunctions
- Unusual market conditions
- Bot-specific issues
- Data quality problems
"""

from typing import Dict, Any, List
import numpy as np
from sklearn.ensemble import IsolationForest

from ..base_model import BaseModelService


class AnomalyDetector(BaseModelService):
    """
    ML model for performance anomaly detection.
    
    Uses Isolation Forest to detect unusual patterns in:
    - Daily P&L vs historical norms
    - Trade frequency deviations
    - Win rate anomalies
    - Position size anomalies
    - Regime-behavior mismatches
    """
    
    ANOMALY_THRESHOLD = -0.5
    
    def __init__(self):
        super().__init__("anomaly_detector")
    
    def get_feature_names(self) -> List[str]:
        """Return the list of feature names used by this model."""
        return [
            "daily_pnl_zscore",
            "trade_count_zscore",
            "win_rate_zscore",
            "avg_trade_size_zscore",
            "position_count_zscore",
            "hold_time_zscore",
            "drawdown_vs_expected",
            "pnl_vs_regime_expected",
            "crypto_pnl_zscore",
            "momentum_pnl_zscore",
            "options_pnl_zscore",
            "api_error_rate",
            "order_rejection_rate"
        ]
    
    def extract_features(self, context: Dict[str, Any]) -> List[float]:
        """
        Extract feature vector for anomaly detection.
        
        Args:
            context: Dictionary containing:
                - daily_metrics: List of recent DailyMetrics
                - today_metrics: Current day's metrics
                - bot_performance: Dict of bot_id -> BotPerformance
                - regime: Current MarketRegimeAnalysis
                - api_stats: Dict with error counts
        """
        daily_metrics = context.get("daily_metrics", [])
        today = context.get("today_metrics", {})
        bot_perf = context.get("bot_performance", {})
        regime = context.get("regime", {})
        api_stats = context.get("api_stats", {})
        
        def calc_zscore(value: float, history: List[float]) -> float:
            if not history or len(history) < 2:
                return 0.0
            mean = np.mean(history)
            std = np.std(history)
            return (value - mean) / std if std > 0 else 0.0
        
        pnl_history = [getattr(m, "daily_pnl_pct", 0) if hasattr(m, "daily_pnl_pct") else m.get("daily_pnl_pct", 0) for m in daily_metrics]
        today_pnl = getattr(today, "daily_pnl_pct", 0) if hasattr(today, "daily_pnl_pct") else today.get("daily_pnl_pct", 0)
        pnl_zscore = calc_zscore(today_pnl, pnl_history)
        
        trade_history = [getattr(m, "total_trades", 0) if hasattr(m, "total_trades") else m.get("total_trades", 0) for m in daily_metrics]
        today_trades = getattr(today, "total_trades", 0) if hasattr(today, "total_trades") else today.get("total_trades", 0)
        trade_zscore = calc_zscore(today_trades, trade_history)
        
        wr_history = [getattr(m, "win_rate", 0.5) if hasattr(m, "win_rate") else m.get("win_rate", 0.5) for m in daily_metrics]
        today_wr = getattr(today, "win_rate", 0.5) if hasattr(today, "win_rate") else today.get("win_rate", 0.5)
        wr_zscore = calc_zscore(today_wr, wr_history)
        
        avg_trade_size_zscore = 0.0
        
        pos_history = [getattr(m, "open_positions", 0) if hasattr(m, "open_positions") else m.get("open_positions", 0) for m in daily_metrics]
        today_pos = getattr(today, "open_positions", 0) if hasattr(today, "open_positions") else today.get("open_positions", 0)
        pos_zscore = calc_zscore(today_pos, pos_history)
        
        hold_time_zscore = 0.0
        
        dd_history = [getattr(m, "current_drawdown_pct", 0) if hasattr(m, "current_drawdown_pct") else m.get("current_drawdown_pct", 0) for m in daily_metrics]
        today_dd = getattr(today, "current_drawdown_pct", 0) if hasattr(today, "current_drawdown_pct") else today.get("current_drawdown_pct", 0)
        expected_dd = np.mean(dd_history) if dd_history else 0
        dd_vs_expected = today_dd - expected_dd
        
        vix = getattr(regime, "vix", 18.0) if hasattr(regime, "vix") else regime.get("vix", 18.0)
        if vix > 30:
            expected_pnl = -0.5
        elif vix > 25:
            expected_pnl = -0.2
        elif vix < 15:
            expected_pnl = 0.3
        else:
            expected_pnl = 0.1
        pnl_vs_regime = today_pnl - expected_pnl
        
        def get_bot_pnl_zscore(bot_id: str) -> float:
            perf = bot_perf.get(bot_id, {})
            pnl = getattr(perf, "pnl_pct_today", 0) if hasattr(perf, "pnl_pct_today") else perf.get("pnl_pct_today", 0)
            return calc_zscore(pnl, pnl_history)
        
        crypto_zscore = get_bot_pnl_zscore("crypto_bot")
        momentum_zscore = get_bot_pnl_zscore("momentum_bot")
        options_zscore = get_bot_pnl_zscore("options_bot")
        
        api_errors = api_stats.get("errors", 0)
        api_calls = api_stats.get("total_calls", 1)
        api_error_rate = api_errors / api_calls if api_calls > 0 else 0
        
        rejections = api_stats.get("order_rejections", 0)
        orders = api_stats.get("orders_submitted", 1)
        rejection_rate = rejections / orders if orders > 0 else 0
        
        return [
            pnl_zscore, trade_zscore, wr_zscore, avg_trade_size_zscore,
            pos_zscore, hold_time_zscore, dd_vs_expected, pnl_vs_regime,
            crypto_zscore, momentum_zscore, options_zscore,
            api_error_rate, rejection_rate
        ]
    
    def predict(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Detect performance anomalies.
        
        Returns:
            Dictionary with:
                - is_anomaly: True if current behavior is anomalous
                - anomaly_score: Score from Isolation Forest (-1 to 1)
                - anomaly_type: Classification of anomaly type
                - details: Specific features that triggered the anomaly
        """
        features = self.extract_features(context)
        feature_array = np.array([features])
        
        score = self._model.decision_function(feature_array)[0]
        prediction = self._model.predict(feature_array)[0]
        
        is_anomaly = prediction == -1 or score < self.ANOMALY_THRESHOLD
        
        anomaly_type = "normal"
        details = []
        
        if is_anomaly:
            feature_names = self.get_feature_names()
            for i, (fname, fval) in enumerate(zip(feature_names, features)):
                if abs(fval) > 2.5:
                    details.append({"feature": fname, "zscore": round(fval, 2)})
            
            if features[0] < -2.5:
                anomaly_type = "unusual_loss"
            elif features[0] > 2.5:
                anomaly_type = "unusual_gain"
            elif features[1] < -2:
                anomaly_type = "low_activity"
            elif features[1] > 2:
                anomaly_type = "high_activity"
            elif features[11] > 0.1:
                anomaly_type = "api_issues"
            elif features[12] > 0.1:
                anomaly_type = "order_rejections"
            else:
                anomaly_type = "general_anomaly"
        
        return {
            "is_anomaly": is_anomaly,
            "anomaly_score": round(float(score), 3),
            "anomaly_type": anomaly_type,
            "details": details,
            "model_version": self._config.get("version", "1.0")
        }
    
    def fallback_predict(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Rule-based fallback for anomaly detection."""
        features = self.extract_features(context)
        
        is_anomaly = False
        anomaly_type = "normal"
        details = []
        
        feature_names = self.get_feature_names()
        for i, (fname, fval) in enumerate(zip(feature_names, features)):
            if abs(fval) > 3.0:
                is_anomaly = True
                details.append({"feature": fname, "zscore": round(fval, 2)})
        
        if features[11] > 0.15:
            is_anomaly = True
            anomaly_type = "high_api_errors"
        elif features[12] > 0.2:
            is_anomaly = True
            anomaly_type = "high_rejection_rate"
        elif features[0] < -3:
            anomaly_type = "extreme_loss"
        elif features[0] > 3:
            anomaly_type = "extreme_gain"
        elif is_anomaly:
            anomaly_type = "statistical_outlier"
        
        score = -1.0 if is_anomaly else 0.5
        
        return {
            "is_anomaly": is_anomaly,
            "anomaly_score": round(score, 3),
            "anomaly_type": anomaly_type,
            "details": details,
            "model_version": "fallback_v1"
        }
    
    def train(self, training_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Train the anomaly detector model.
        
        Uses Isolation Forest for unsupervised anomaly detection.
        Training data should include features from normal operations.
        """
        if len(training_data) < 30:
            return {"status": "insufficient_data", "count": len(training_data)}
        
        feature_names = self.get_feature_names()
        X = []
        
        for record in training_data:
            features = [record.get(fname, 0.0) for fname in feature_names]
            X.append(features)
        
        X = np.array(X)
        
        means, stds = self._calculate_feature_stats(training_data)
        
        model = IsolationForest(
            n_estimators=100,
            contamination=0.1,
            random_state=42
        )
        model.fit(X)
        
        predictions = model.predict(X)
        anomaly_rate = float(np.mean(predictions == -1))
        
        config = {
            "version": "1.0",
            "features": feature_names,
            "feature_means": means,
            "feature_stds": stds,
            "contamination": 0.1,
            "anomaly_rate": anomaly_rate,
            "training_samples": len(training_data)
        }
        
        self._save_model(model, config)
        
        return {
            "status": "success",
            "anomaly_rate": anomaly_rate,
            "samples": len(training_data)
        }
