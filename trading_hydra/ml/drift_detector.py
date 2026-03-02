"""
ML Model Drift Detector - Detect when ML models need retraining.

Monitors feature distributions and prediction calibration to detect
when models are no longer accurate.

Key metrics:
- Feature distribution shift (KS test)
- Prediction calibration (predicted vs actual)
- Model accuracy over rolling windows
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from collections import defaultdict
import math

from ..core.logging import get_logger
from ..core.state import get_state, set_state


@dataclass
class DriftStatus:
    """Status of model drift detection."""
    model_id: str
    feature_drift_detected: bool
    calibration_drift_detected: bool
    accuracy_degraded: bool
    drift_score: float  # 0-1, higher = more drift
    status: str  # healthy, warning, retrain_needed
    recommended_action: str
    details: Dict[str, Any]
    last_checked: str


class DriftDetector:
    """
    Detect ML model drift and trigger retraining.
    
    Philosophy:
    - Models trained on historical data may not work in new regimes
    - Feature distributions shift over time
    - Predicted probabilities should match actual outcomes
    
    Detection methods:
    1. Feature drift: Compare recent feature distributions to training
    2. Calibration drift: Compare predicted vs actual probabilities
    3. Accuracy tracking: Rolling accuracy over time
    """
    
    # Thresholds
    KS_THRESHOLD = 0.3  # Kolmogorov-Smirnov statistic threshold
    CALIBRATION_THRESHOLD = 0.15  # Max allowed calibration error
    ACCURACY_DROP_THRESHOLD = 0.10  # 10% accuracy drop triggers warning
    
    # History limits
    MAX_PREDICTIONS = 500
    MAX_FEATURES = 1000
    
    def __init__(self):
        self._logger = get_logger()
        
        # Per-model tracking
        self._predictions: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._feature_history: Dict[str, List[Dict[str, float]]] = defaultdict(list)
        self._baseline_features: Dict[str, Dict[str, Dict[str, float]]] = {}
        self._last_status: Dict[str, DriftStatus] = {}
        
        self._load_state()
    
    def _load_state(self) -> None:
        """Load persisted state."""
        try:
            self._baseline_features = get_state("drift.baselines", {})
        except Exception as e:
            self._logger.error(f"Failed to load drift state: {e}")
    
    def _save_state(self) -> None:
        """Persist state."""
        try:
            set_state("drift.baselines", self._baseline_features)
        except Exception as e:
            self._logger.error(f"Failed to save drift state: {e}")
    
    def set_baseline(
        self,
        model_id: str,
        feature_stats: Dict[str, Dict[str, float]]
    ) -> None:
        """
        Set baseline feature statistics from training data.
        
        Args:
            model_id: Model identifier
            feature_stats: Dict of feature name -> {mean, std, min, max}
        """
        self._baseline_features[model_id] = feature_stats
        self._save_state()
        
        self._logger.log("drift_baseline_set", {
            "model_id": model_id,
            "feature_count": len(feature_stats)
        })
    
    def record_prediction(
        self,
        model_id: str,
        predicted_prob: float,
        actual_outcome: int,  # 0 or 1
        features: Optional[Dict[str, float]] = None
    ) -> None:
        """
        Record a prediction and its outcome for drift detection.
        
        Args:
            model_id: Model identifier
            predicted_prob: Predicted probability (0-1)
            actual_outcome: Actual binary outcome (0 or 1)
            features: Optional feature values used for prediction
        """
        self._predictions[model_id].append({
            "timestamp": datetime.utcnow().isoformat(),
            "predicted_prob": predicted_prob,
            "actual": actual_outcome,
            "predicted_class": 1 if predicted_prob >= 0.5 else 0
        })
        
        # Trim history
        if len(self._predictions[model_id]) > self.MAX_PREDICTIONS:
            self._predictions[model_id] = self._predictions[model_id][-self.MAX_PREDICTIONS:]
        
        # Track features
        if features:
            self._feature_history[model_id].append(features)
            if len(self._feature_history[model_id]) > self.MAX_FEATURES:
                self._feature_history[model_id] = self._feature_history[model_id][-self.MAX_FEATURES:]
    
    def _calculate_ks_statistic(
        self,
        baseline_stats: Dict[str, float],
        recent_values: List[float]
    ) -> float:
        """
        Approximate Kolmogorov-Smirnov statistic.
        
        Simplified: Compare recent mean/std to baseline.
        """
        if not recent_values or len(recent_values) < 10:
            return 0.0
        
        baseline_mean = baseline_stats.get("mean", 0)
        baseline_std = baseline_stats.get("std", 1)
        
        recent_mean = sum(recent_values) / len(recent_values)
        recent_std = math.sqrt(
            sum((x - recent_mean) ** 2 for x in recent_values) / len(recent_values)
        ) if len(recent_values) > 1 else 0
        
        # Normalized difference
        if baseline_std > 0:
            mean_shift = abs(recent_mean - baseline_mean) / baseline_std
        else:
            mean_shift = abs(recent_mean - baseline_mean)
        
        if baseline_std > 0 and recent_std > 0:
            std_ratio = max(recent_std / baseline_std, baseline_std / recent_std)
        else:
            std_ratio = 1.0
        
        # Combine into pseudo-KS statistic
        ks = min(1.0, (mean_shift / 3) + (std_ratio - 1) / 2)
        return ks
    
    def _calculate_calibration_error(
        self,
        predictions: List[Dict[str, Any]],
        n_bins: int = 10
    ) -> float:
        """
        Calculate Expected Calibration Error (ECE).
        
        Lower is better. Measures how well predicted probabilities
        match actual frequencies.
        """
        if len(predictions) < 20:
            return 0.0
        
        # Bin predictions
        bins: Dict[int, List[Tuple[float, int]]] = defaultdict(list)
        
        for pred in predictions:
            prob = pred["predicted_prob"]
            actual = pred["actual"]
            bin_idx = min(int(prob * n_bins), n_bins - 1)
            bins[bin_idx].append((prob, actual))
        
        # Calculate ECE
        total_error = 0.0
        total_samples = len(predictions)
        
        for bin_idx, items in bins.items():
            if not items:
                continue
            
            bin_size = len(items)
            avg_predicted = sum(p for p, _ in items) / bin_size
            avg_actual = sum(a for _, a in items) / bin_size
            
            error = abs(avg_predicted - avg_actual) * (bin_size / total_samples)
            total_error += error
        
        return total_error
    
    def _calculate_accuracy(
        self,
        predictions: List[Dict[str, Any]]
    ) -> float:
        """Calculate accuracy from predictions."""
        if not predictions:
            return 0.5
        
        correct = sum(
            1 for p in predictions
            if p["predicted_class"] == p["actual"]
        )
        return correct / len(predictions)
    
    def detect_drift(self, model_id: str) -> DriftStatus:
        """
        Run full drift detection for a model.
        
        Args:
            model_id: Model to check
            
        Returns:
            DriftStatus with recommendations
        """
        predictions = self._predictions.get(model_id, [])
        features = self._feature_history.get(model_id, [])
        baseline = self._baseline_features.get(model_id, {})
        
        details = {}
        
        # 1. Feature drift
        feature_drift_detected = False
        if baseline and features:
            max_ks = 0.0
            drifted_features = []
            
            for feature_name, stats in baseline.items():
                values = [f.get(feature_name) for f in features if feature_name in f]
                values = [v for v in values if v is not None]
                
                if values:
                    ks = self._calculate_ks_statistic(stats, values)
                    if ks > max_ks:
                        max_ks = ks
                    if ks > self.KS_THRESHOLD:
                        drifted_features.append(feature_name)
            
            feature_drift_detected = len(drifted_features) > 0
            details["max_ks_statistic"] = round(max_ks, 3)
            details["drifted_features"] = drifted_features[:5]  # Top 5
        
        # 2. Calibration drift
        calibration_error = self._calculate_calibration_error(predictions)
        calibration_drift_detected = calibration_error > self.CALIBRATION_THRESHOLD
        details["calibration_error"] = round(calibration_error, 3)
        
        # 3. Accuracy degradation
        if len(predictions) >= 50:
            recent_acc = self._calculate_accuracy(predictions[-50:])
            older_acc = self._calculate_accuracy(predictions[-200:-50]) if len(predictions) >= 200 else recent_acc
            accuracy_drop = older_acc - recent_acc
            accuracy_degraded = accuracy_drop > self.ACCURACY_DROP_THRESHOLD
            details["recent_accuracy"] = round(recent_acc, 3)
            details["older_accuracy"] = round(older_acc, 3)
            details["accuracy_drop"] = round(accuracy_drop, 3)
        else:
            accuracy_degraded = False
            details["recent_accuracy"] = self._calculate_accuracy(predictions) if predictions else 0.5
        
        # Calculate drift score
        drift_components = [
            0.4 if feature_drift_detected else 0.0,
            0.3 if calibration_drift_detected else 0.0,
            0.3 if accuracy_degraded else 0.0
        ]
        drift_score = sum(drift_components)
        
        # Determine status
        if drift_score >= 0.6:
            status = "retrain_needed"
            action = "retrain_model_immediately"
        elif drift_score >= 0.3:
            status = "warning"
            action = "monitor_and_prepare_retraining"
        else:
            status = "healthy"
            action = "continue_monitoring"
        
        result = DriftStatus(
            model_id=model_id,
            feature_drift_detected=feature_drift_detected,
            calibration_drift_detected=calibration_drift_detected,
            accuracy_degraded=accuracy_degraded,
            drift_score=round(drift_score, 3),
            status=status,
            recommended_action=action,
            details=details,
            last_checked=datetime.utcnow().isoformat()
        )
        
        self._last_status[model_id] = result
        
        # Log
        self._logger.log("drift_detection", {
            "model_id": model_id,
            "feature_drift": feature_drift_detected,
            "calibration_drift": calibration_drift_detected,
            "accuracy_degraded": accuracy_degraded,
            "drift_score": result.drift_score,
            "status": result.status,
            "action": result.recommended_action
        })
        
        return result
    
    def should_retrain(self, model_id: str) -> bool:
        """Check if model needs retraining."""
        if model_id not in self._last_status:
            self.detect_drift(model_id)
        
        status = self._last_status.get(model_id)
        return status.status == "retrain_needed" if status else False
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all model drift statuses."""
        # Detect drift for all tracked models
        for model_id in self._predictions.keys():
            if model_id not in self._last_status:
                self.detect_drift(model_id)
        
        return {
            "models": {
                model_id: {
                    "drift_score": status.drift_score,
                    "status": status.status,
                    "action": status.recommended_action
                }
                for model_id, status in self._last_status.items()
            },
            "thresholds": {
                "ks_threshold": self.KS_THRESHOLD,
                "calibration_threshold": self.CALIBRATION_THRESHOLD,
                "accuracy_drop_threshold": self.ACCURACY_DROP_THRESHOLD
            }
        }


# Singleton
_drift_detector: Optional[DriftDetector] = None


def get_drift_detector() -> DriftDetector:
    """Get or create DriftDetector singleton."""
    global _drift_detector
    if _drift_detector is None:
        _drift_detector = DriftDetector()
    return _drift_detector
