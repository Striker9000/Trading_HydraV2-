"""
BaseModelService - Abstract base class for account-level ML models.

Provides common infrastructure for:
- Model persistence (pickle)
- Feature extraction
- Training/retraining
- Fallback scoring when model unavailable
- Logging and error handling
"""

import pickle
import json
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path

import numpy as np
from sklearn.base import BaseEstimator

from ..core.logging import get_logger


class BaseModelService(ABC):
    """
    Abstract base class for account-level ML models.
    
    Subclasses implement specific prediction logic for:
    - RiskAdjustmentEngine: Dynamic risk adjustment
    - BotAllocationModel: Optimal bot allocation
    - RegimeSizer: Regime-based position sizing
    - DrawdownPredictor: Drawdown prediction
    - AnomalyDetector: Performance anomaly detection
    """
    
    MODEL_DIR = Path("models/account_ml")
    
    def __init__(self, model_name: str):
        """
        Initialize the model service.
        
        Args:
            model_name: Unique name for this model (used for file paths)
        """
        self.model_name = model_name
        self._logger = get_logger()
        self._model: Optional[BaseEstimator] = None
        self._config: Dict[str, Any] = {}
        self._is_available = False
        self._last_trained: Optional[datetime] = None
        
        self.MODEL_DIR.mkdir(parents=True, exist_ok=True)
        self._load_model()
    
    @property
    def model_path(self) -> Path:
        """Path to the pickled model file."""
        return self.MODEL_DIR / f"{self.model_name}.pkl"
    
    @property
    def config_path(self) -> Path:
        """Path to the model config file."""
        return self.MODEL_DIR / f"{self.model_name}_config.json"
    
    @property
    def is_available(self) -> bool:
        """Check if the model is loaded and available."""
        return self._is_available
    
    def _load_model(self) -> None:
        """Load the trained model and configuration."""
        try:
            if self.model_path.exists() and self.config_path.exists():
                with open(self.model_path, 'rb') as f:
                    self._model = pickle.load(f)
                
                with open(self.config_path, 'r') as f:
                    self._config = json.load(f)
                
                self._is_available = True
                trained_on = self._config.get("trained_on", "unknown")
                
                self._logger.log(f"{self.model_name}_loaded", {
                    "model_path": str(self.model_path),
                    "trained_on": trained_on,
                    "features": len(self._config.get("features", []))
                })
            else:
                self._logger.log(f"{self.model_name}_not_found", {
                    "model_path": str(self.model_path),
                    "status": "using_fallback"
                })
        except Exception as e:
            self._logger.error(f"{self.model_name} load failed: {e}")
            self._is_available = False
    
    def _save_model(
        self, 
        model: BaseEstimator, 
        config: Dict[str, Any]
    ) -> None:
        """
        Save trained model and configuration.
        
        Args:
            model: Trained sklearn model
            config: Model configuration including features, thresholds, etc.
        """
        try:
            config["trained_on"] = datetime.utcnow().isoformat()
            config["model_name"] = self.model_name
            
            with open(self.model_path, 'wb') as f:
                pickle.dump(model, f)
            
            with open(self.config_path, 'w') as f:
                json.dump(config, f, indent=2)
            
            self._model = model
            self._config = config
            self._is_available = True
            self._last_trained = datetime.utcnow()
            
            self._logger.log(f"{self.model_name}_saved", {
                "model_path": str(self.model_path),
                "features": len(config.get("features", []))
            })
        except Exception as e:
            self._logger.error(f"{self.model_name} save failed: {e}")
    
    @abstractmethod
    def get_feature_names(self) -> List[str]:
        """Return the list of feature names used by this model."""
        pass
    
    @abstractmethod
    def extract_features(self, context: Dict[str, Any]) -> List[float]:
        """
        Extract feature vector from input context.
        
        Args:
            context: Dictionary containing input data
            
        Returns:
            List of feature values in the correct order
        """
        pass
    
    @abstractmethod
    def predict(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Make a prediction using the model.
        
        Args:
            context: Dictionary containing input data
            
        Returns:
            Dictionary with prediction results
        """
        pass
    
    @abstractmethod
    def fallback_predict(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Provide a rule-based fallback prediction when model unavailable.
        
        Args:
            context: Dictionary containing input data
            
        Returns:
            Dictionary with fallback prediction results
        """
        pass
    
    @abstractmethod
    def train(self, training_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Train or retrain the model on new data.
        
        Args:
            training_data: List of training examples
            
        Returns:
            Dictionary with training metrics (accuracy, etc.)
        """
        pass
    
    def safe_predict(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Make a prediction with automatic fallback on failure.
        
        Args:
            context: Dictionary containing input data
            
        Returns:
            Dictionary with prediction results
        """
        if not self._is_available:
            return self.fallback_predict(context)
        
        try:
            return self.predict(context)
        except Exception as e:
            self._logger.error(f"{self.model_name} prediction failed: {e}")
            return self.fallback_predict(context)
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get information about the loaded model."""
        if not self._is_available:
            return {
                "model_name": self.model_name,
                "status": "unavailable",
                "reason": "model_not_loaded"
            }
        
        return {
            "model_name": self.model_name,
            "status": "available",
            "version": self._config.get("version", "1.0"),
            "features": self._config.get("features", []),
            "trained_on": self._config.get("trained_on", "unknown"),
            "accuracy": self._config.get("accuracy"),
            "thresholds": self._config.get("thresholds", {})
        }
    
    def needs_retraining(self, max_age_days: int = 7) -> bool:
        """
        Check if the model needs retraining based on age.
        
        Args:
            max_age_days: Maximum days before retraining is recommended
            
        Returns:
            True if model should be retrained
        """
        if not self._is_available:
            return True
        
        trained_on = self._config.get("trained_on")
        if not trained_on:
            return True
        
        try:
            trained_dt = datetime.fromisoformat(trained_on.replace("Z", "+00:00"))
            age_days = (datetime.utcnow() - trained_dt.replace(tzinfo=None)).days
            return age_days > max_age_days
        except (ValueError, TypeError):
            return True
    
    def _normalize_features(
        self, 
        features: List[float], 
        means: Optional[List[float]] = None,
        stds: Optional[List[float]] = None
    ) -> List[float]:
        """
        Normalize feature values using stored means/stds.
        
        Args:
            features: Raw feature values
            means: Optional means for each feature
            stds: Optional standard deviations for each feature
            
        Returns:
            Normalized feature values
        """
        if means is None:
            means = self._config.get("feature_means", [0.0] * len(features))
        if stds is None:
            stds = self._config.get("feature_stds", [1.0] * len(features))
        
        normalized = []
        for i, (val, mean, std) in enumerate(zip(features, means, stds)):
            if std > 0:
                normalized.append((val - mean) / std)
            else:
                normalized.append(val - mean)
        
        return normalized
    
    def _calculate_feature_stats(
        self, 
        training_data: List[Dict[str, Any]]
    ) -> Tuple[List[float], List[float]]:
        """
        Calculate feature means and standard deviations from training data.
        
        Args:
            training_data: List of training examples
            
        Returns:
            Tuple of (means, stds) lists
        """
        feature_names = self.get_feature_names()
        n_features = len(feature_names)
        
        all_features = []
        for record in training_data:
            features = [record.get(fname, 0.0) for fname in feature_names]
            all_features.append(features)
        
        if not all_features:
            return [0.0] * n_features, [1.0] * n_features
        
        arr = np.array(all_features)
        means = np.mean(arr, axis=0).tolist()
        stds = np.std(arr, axis=0).tolist()
        
        stds = [s if s > 0 else 1.0 for s in stds]
        
        return means, stds
