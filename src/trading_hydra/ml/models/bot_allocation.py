"""
BotAllocationModel - Optimal Bot Allocation Predictor.

Predicts which bot (Crypto, Momentum, Options) is likely to perform best
given current market conditions and historical performance.
"""

from typing import Dict, Any, List
import numpy as np
from sklearn.ensemble import RandomForestClassifier

from ..base_model import BaseModelService


class BotAllocationModel(BaseModelService):
    """
    ML model for optimal bot allocation.
    
    Predicts which bot should receive the highest allocation based on:
    - Current market regime
    - Historical bot performance
    - Time of day/week patterns
    - Recent bot-specific metrics
    """
    
    BOT_IDS = ["crypto_bot", "momentum_bot", "options_bot"]
    
    def __init__(self):
        super().__init__("bot_allocation")
    
    def get_feature_names(self) -> List[str]:
        """Return the list of feature names used by this model."""
        return [
            "vix",
            "vvix",
            "volatility_regime_encoded",
            "sentiment_encoded",
            "hour_of_day",
            "day_of_week",
            "is_market_hours",
            "crypto_sharpe_30d",
            "crypto_win_rate_7d",
            "crypto_pnl_7d",
            "momentum_sharpe_30d",
            "momentum_win_rate_7d",
            "momentum_pnl_7d",
            "options_sharpe_30d",
            "options_win_rate_7d",
            "options_pnl_7d",
            "account_drawdown_pct",
            "risk_multiplier"
        ]
    
    def extract_features(self, context: Dict[str, Any]) -> List[float]:
        """
        Extract feature vector from context.
        
        Args:
            context: Dictionary containing:
                - regime: Current MarketRegimeAnalysis
                - bot_performance: Dict of bot_id -> BotPerformance
                - account_metrics: Current DailyMetrics
                - hour: Current hour (0-23)
                - day_of_week: Day of week (0-6)
        """
        regime = context.get("regime", {})
        bot_perf = context.get("bot_performance", {})
        account = context.get("account_metrics", {})
        
        vix = getattr(regime, "vix", 18.0) if hasattr(regime, "vix") else regime.get("vix", 18.0)
        vvix = getattr(regime, "vvix", 100.0) if hasattr(regime, "vvix") else regime.get("vvix", 100.0)
        
        vol_regime = getattr(regime, "volatility_regime", "normal") if hasattr(regime, "volatility_regime") else regime.get("volatility_regime", "normal")
        if hasattr(vol_regime, "value"):
            vol_regime = vol_regime.value
        vol_map = {"very_low": 0, "low": 1, "normal": 2, "elevated": 3, "high": 4, "extreme": 5}
        vol_encoded = vol_map.get(vol_regime, 2)
        
        sentiment = getattr(regime, "sentiment", "neutral") if hasattr(regime, "sentiment") else regime.get("sentiment", "neutral")
        if hasattr(sentiment, "value"):
            sentiment = sentiment.value
        sent_map = {"risk_on": 0, "neutral": 1, "risk_off": 2, "extreme_fear": 3}
        sent_encoded = sent_map.get(sentiment, 1)
        
        hour = context.get("hour", 12)
        day = context.get("day_of_week", 2)
        is_market_hours = 1.0 if 9 <= hour <= 16 and day < 5 else 0.0
        
        def get_bot_metrics(bot_id: str):
            perf = bot_perf.get(bot_id, {})
            sharpe = getattr(perf, "sharpe_ratio_30d", 0.0) if hasattr(perf, "sharpe_ratio_30d") else perf.get("sharpe_ratio_30d", 0.0)
            win_rate = getattr(perf, "win_rate_30d", 0.5) if hasattr(perf, "win_rate_30d") else perf.get("win_rate_30d", 0.5)
            pnl = getattr(perf, "pnl_today", 0.0) if hasattr(perf, "pnl_today") else perf.get("pnl_today", 0.0)
            return sharpe, win_rate, pnl
        
        crypto_sharpe, crypto_wr, crypto_pnl = get_bot_metrics("crypto_bot")
        momentum_sharpe, momentum_wr, momentum_pnl = get_bot_metrics("momentum_bot")
        options_sharpe, options_wr, options_pnl = get_bot_metrics("options_bot")
        
        drawdown = getattr(account, "current_drawdown_pct", 0.0) if hasattr(account, "current_drawdown_pct") else account.get("current_drawdown_pct", 0.0)
        risk_mult = getattr(account, "risk_multiplier", 1.0) if hasattr(account, "risk_multiplier") else account.get("risk_multiplier", 1.0)
        
        return [
            vix, vvix, vol_encoded, sent_encoded,
            hour, day, is_market_hours,
            crypto_sharpe, crypto_wr, crypto_pnl,
            momentum_sharpe, momentum_wr, momentum_pnl,
            options_sharpe, options_wr, options_pnl,
            drawdown, risk_mult
        ]
    
    def predict(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Predict optimal bot allocation weights.
        
        Returns:
            Dictionary with:
                - allocations: Dict of bot_id -> allocation weight (0.0-1.0)
                - recommended_bot: Bot with highest allocation
                - confidence: Model confidence
        """
        features = self.extract_features(context)
        feature_array = np.array([features])
        
        proba = self._model.predict_proba(feature_array)[0]
        
        allocations = {}
        for i, bot_id in enumerate(self.BOT_IDS):
            allocations[bot_id] = round(float(proba[i]) if i < len(proba) else 0.33, 3)
        
        total = sum(allocations.values())
        if total > 0:
            allocations = {k: round(v / total, 3) for k, v in allocations.items()}
        
        recommended = max(allocations, key=allocations.get)
        confidence = allocations[recommended]
        
        return {
            "allocations": allocations,
            "recommended_bot": recommended,
            "confidence": round(confidence, 3),
            "model_version": self._config.get("version", "1.0")
        }
    
    def fallback_predict(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Rule-based fallback for bot allocation."""
        regime = context.get("regime", {})
        hour = context.get("hour", 12)
        day = context.get("day_of_week", 2)
        
        is_market_hours = 9 <= hour <= 16 and day < 5
        
        vol_regime = getattr(regime, "volatility_regime", "normal") if hasattr(regime, "volatility_regime") else regime.get("volatility_regime", "normal")
        if hasattr(vol_regime, "value"):
            vol_regime = vol_regime.value
        
        sentiment = getattr(regime, "sentiment", "neutral") if hasattr(regime, "sentiment") else regime.get("sentiment", "neutral")
        if hasattr(sentiment, "value"):
            sentiment = sentiment.value
        
        crypto_alloc = 0.33
        momentum_alloc = 0.33
        options_alloc = 0.34
        
        if not is_market_hours:
            crypto_alloc = 0.7
            momentum_alloc = 0.15
            options_alloc = 0.15
        elif vol_regime in ["high", "extreme"]:
            crypto_alloc = 0.2
            momentum_alloc = 0.3
            options_alloc = 0.5
        elif vol_regime in ["very_low", "low"]:
            crypto_alloc = 0.3
            momentum_alloc = 0.4
            options_alloc = 0.3
        
        if sentiment == "extreme_fear":
            crypto_alloc *= 0.5
            momentum_alloc *= 0.7
            options_alloc *= 1.3
        elif sentiment == "risk_on":
            crypto_alloc *= 1.2
            momentum_alloc *= 1.1
            options_alloc *= 0.8
        
        total = crypto_alloc + momentum_alloc + options_alloc
        allocations = {
            "crypto_bot": round(crypto_alloc / total, 3),
            "momentum_bot": round(momentum_alloc / total, 3),
            "options_bot": round(options_alloc / total, 3)
        }
        
        recommended = max(allocations, key=allocations.get)
        
        return {
            "allocations": allocations,
            "recommended_bot": recommended,
            "confidence": 0.3,
            "model_version": "fallback_v1"
        }
    
    def train(self, training_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Train the bot allocation model.
        
        Training data should include:
        - Features from extract_features
        - Label: best_bot (0=crypto, 1=momentum, 2=options based on next-day performance)
        """
        if len(training_data) < 30:
            return {"status": "insufficient_data", "count": len(training_data)}
        
        feature_names = self.get_feature_names()
        X = []
        y = []
        
        for record in training_data:
            features = [record.get(fname, 0.0) for fname in feature_names]
            label = record.get("best_bot", 0)
            X.append(features)
            y.append(label)
        
        X = np.array(X)
        y = np.array(y)
        
        means, stds = self._calculate_feature_stats(training_data)
        
        model = RandomForestClassifier(
            n_estimators=100,
            max_depth=6,
            random_state=42
        )
        model.fit(X, y)
        
        predictions = model.predict(X)
        accuracy = float(np.mean(predictions == y))
        
        config = {
            "version": "1.0",
            "features": feature_names,
            "feature_means": means,
            "feature_stds": stds,
            "accuracy": accuracy,
            "training_samples": len(training_data),
            "classes": self.BOT_IDS
        }
        
        self._save_model(model, config)
        
        return {
            "status": "success",
            "accuracy": accuracy,
            "samples": len(training_data)
        }
