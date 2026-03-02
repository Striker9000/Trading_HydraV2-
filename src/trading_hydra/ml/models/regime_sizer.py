"""
RegimeSizer - Regime-Based Position Sizing Model.

Uses ML to determine optimal position sizes based on market regime indicators
rather than using hard-coded rules.
"""

from typing import Dict, Any, List
import numpy as np
from sklearn.ensemble import GradientBoostingRegressor

from ..base_model import BaseModelService


class RegimeSizer(BaseModelService):
    """
    ML model for regime-based position sizing.
    
    Predicts optimal position size multiplier (0.0-1.5) based on:
    - VIX, VVIX, TNX, DXY, MOVE indicators
    - Historical regime-return relationships
    - Cross-indicator patterns
    """
    
    MIN_SIZE = 0.0
    MAX_SIZE = 1.5
    
    def __init__(self):
        super().__init__("regime_sizer")
    
    def get_feature_names(self) -> List[str]:
        """Return the list of feature names used by this model."""
        return [
            "vix",
            "vix_change_1d",
            "vix_change_5d",
            "vvix",
            "vvix_vix_ratio",
            "tnx",
            "tnx_change_1d",
            "dxy",
            "dxy_change_1d",
            "move",
            "vol_regime_encoded",
            "sentiment_encoded",
            "vvix_warning",
            "rate_shock_warning",
            "dollar_surge_warning",
            "historical_return_low_vol",
            "historical_return_high_vol"
        ]
    
    def extract_features(self, context: Dict[str, Any]) -> List[float]:
        """
        Extract feature vector from regime context.
        
        Args:
            context: Dictionary containing:
                - regime: Current MarketRegimeAnalysis
                - regime_history: List of recent RegimeSnapshots
                - returns_by_regime: Dict of regime -> historical returns
        """
        regime = context.get("regime", {})
        history = context.get("regime_history", [])
        returns = context.get("returns_by_regime", {})
        
        vix = getattr(regime, "vix", 18.0) if hasattr(regime, "vix") else regime.get("vix", 18.0)
        vvix = getattr(regime, "vvix", 100.0) if hasattr(regime, "vvix") else regime.get("vvix", 100.0)
        tnx = getattr(regime, "tnx", 4.0) if hasattr(regime, "tnx") else regime.get("tnx", 4.0)
        dxy = getattr(regime, "dxy", 100.0) if hasattr(regime, "dxy") else regime.get("dxy", 100.0)
        move = getattr(regime, "move", 100.0) if hasattr(regime, "move") else regime.get("move", 100.0)
        
        vix_1d = 0.0
        vix_5d = 0.0
        tnx_1d = 0.0
        dxy_1d = 0.0
        
        if len(history) >= 2:
            prev = history[-2]
            prev_vix = getattr(prev, "vix", vix) if hasattr(prev, "vix") else prev.get("vix", vix)
            prev_tnx = getattr(prev, "tnx", tnx) if hasattr(prev, "tnx") else prev.get("tnx", tnx)
            prev_dxy = getattr(prev, "dxy", dxy) if hasattr(prev, "dxy") else prev.get("dxy", dxy)
            vix_1d = vix - prev_vix
            tnx_1d = tnx - prev_tnx
            dxy_1d = dxy - prev_dxy
        
        if len(history) >= 6:
            prev5 = history[-6]
            prev5_vix = getattr(prev5, "vix", vix) if hasattr(prev5, "vix") else prev5.get("vix", vix)
            vix_5d = vix - prev5_vix
        
        vvix_vix_ratio = vvix / vix if vix > 0 else 5.0
        
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
        
        vvix_warn = 1.0 if (getattr(regime, "vvix_warning", False) if hasattr(regime, "vvix_warning") else regime.get("vvix_warning", False)) else 0.0
        rate_warn = 1.0 if (getattr(regime, "rate_shock_warning", False) if hasattr(regime, "rate_shock_warning") else regime.get("rate_shock_warning", False)) else 0.0
        dollar_warn = 1.0 if (getattr(regime, "dollar_surge_warning", False) if hasattr(regime, "dollar_surge_warning") else regime.get("dollar_surge_warning", False)) else 0.0
        
        low_vol_return = returns.get("low_vol", 0.5)
        high_vol_return = returns.get("high_vol", -0.2)
        
        return [
            vix, vix_1d, vix_5d, vvix, vvix_vix_ratio,
            tnx, tnx_1d, dxy, dxy_1d, move,
            vol_encoded, sent_encoded,
            vvix_warn, rate_warn, dollar_warn,
            low_vol_return, high_vol_return
        ]
    
    def predict(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Predict optimal position size multiplier.
        
        Returns:
            Dictionary with:
                - size_multiplier: Recommended size multiplier (0.0-1.5)
                - regime_assessment: Description of current regime
                - confidence: Model confidence
        """
        features = self.extract_features(context)
        feature_array = np.array([features])
        
        raw_prediction = self._model.predict(feature_array)[0]
        size_mult = max(self.MIN_SIZE, min(self.MAX_SIZE, raw_prediction))
        
        vol_encoded = features[10]
        if vol_encoded <= 1:
            assessment = "favorable_low_volatility"
        elif vol_encoded == 2:
            assessment = "normal_conditions"
        elif vol_encoded == 3:
            assessment = "elevated_caution"
        elif vol_encoded == 4:
            assessment = "high_volatility_reduce"
        else:
            assessment = "extreme_halt_recommended"
        
        confidence = min(1.0, 0.5 + (1.0 - size_mult) * 0.3)
        
        return {
            "size_multiplier": round(size_mult, 3),
            "regime_assessment": assessment,
            "confidence": round(confidence, 3),
            "vix": features[0],
            "vvix": features[3],
            "model_version": self._config.get("version", "1.0")
        }
    
    def fallback_predict(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Rule-based fallback for regime sizing."""
        regime = context.get("regime", {})
        
        vix = getattr(regime, "vix", 18.0) if hasattr(regime, "vix") else regime.get("vix", 18.0)
        vvix = getattr(regime, "vvix", 100.0) if hasattr(regime, "vvix") else regime.get("vvix", 100.0)
        
        vvix_warn = getattr(regime, "vvix_warning", False) if hasattr(regime, "vvix_warning") else regime.get("vvix_warning", False)
        rate_warn = getattr(regime, "rate_shock_warning", False) if hasattr(regime, "rate_shock_warning") else regime.get("rate_shock_warning", False)
        dollar_warn = getattr(regime, "dollar_surge_warning", False) if hasattr(regime, "dollar_surge_warning") else regime.get("dollar_surge_warning", False)
        
        if vix < 12:
            size_mult = 1.2
            assessment = "very_low_volatility"
        elif vix < 15:
            size_mult = 1.1
            assessment = "low_volatility"
        elif vix < 20:
            size_mult = 1.0
            assessment = "normal"
        elif vix < 25:
            size_mult = 0.7
            assessment = "elevated"
        elif vix < 35:
            size_mult = 0.4
            assessment = "high"
        else:
            size_mult = 0.0
            assessment = "extreme"
        
        warning_count = sum([vvix_warn, rate_warn, dollar_warn])
        if warning_count >= 2:
            size_mult *= 0.5
            assessment += "_multiple_warnings"
        elif warning_count >= 1:
            size_mult *= 0.8
            assessment += "_warning"
        
        size_mult = max(self.MIN_SIZE, min(self.MAX_SIZE, size_mult))
        
        return {
            "size_multiplier": round(size_mult, 3),
            "regime_assessment": assessment,
            "confidence": 0.3,
            "vix": vix,
            "vvix": vvix,
            "model_version": "fallback_v1"
        }
    
    def train(self, training_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Train the regime sizer model.
        
        Training data should include:
        - Features from extract_features
        - Label: optimal_size_multiplier (based on subsequent returns)
        """
        if len(training_data) < 30:
            return {"status": "insufficient_data", "count": len(training_data)}
        
        feature_names = self.get_feature_names()
        X = []
        y = []
        
        for record in training_data:
            features = [record.get(fname, 0.0) for fname in feature_names]
            label = record.get("optimal_size_multiplier", 1.0)
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
