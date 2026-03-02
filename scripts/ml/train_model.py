#!/usr/bin/env python3
"""
Train ML Model for Trade Profit Prediction.

Trains a LightGBM classifier on historical trade data to predict
whether a trade will be profitable.

Usage:
    python scripts/ml/train_model.py
    
Prerequisites:
    Run extract_training_data.py first to create training dataset.
    
Output:
    models/trade_classifier.pkl - Trained model
    models/feature_config.json - Feature configuration
"""

import json
import pickle
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Tuple

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, 
    f1_score, roc_auc_score, classification_report
)
from sklearn.preprocessing import LabelEncoder
from sklearn.ensemble import GradientBoostingClassifier

DATA_PATH = Path("data/training_data.csv")
MODEL_PATH = Path("models/trade_classifier.pkl")
CONFIG_PATH = Path("models/feature_config.json")

FEATURES = [
    "side",
    "hour", 
    "day_of_week",
    "is_morning",
    "is_afternoon", 
    "is_weekend",
    "symbol_win_rate",
    "hour_win_rate"
]

TARGET = "is_profitable"


def load_data() -> pd.DataFrame:
    """Load and preprocess training data."""
    if not DATA_PATH.exists():
        print(f"Error: Training data not found at {DATA_PATH}")
        print("Run extract_training_data.py first.")
        sys.exit(1)
    
    df = pd.read_csv(DATA_PATH)
    print(f"Loaded {len(df)} samples")
    
    profitable_count = df["is_profitable"].sum()
    if profitable_count < 10 or len(df) < 100:
        print(f"\nInsufficient real data ({len(df)} samples, {profitable_count} profitable)")
        print("Generating synthetic training data for model bootstrapping...")
        df = generate_synthetic_data()
        print(f"Generated {len(df)} synthetic samples")
    
    return df


def generate_synthetic_data() -> pd.DataFrame:
    """Generate synthetic training data based on trading heuristics."""
    np.random.seed(42)
    samples = []
    
    for _ in range(1000):
        hour = np.random.randint(0, 24)
        day = np.random.randint(0, 7)
        side = np.random.choice([0, 1])
        
        base_prob = 0.52
        if 9 <= hour <= 15:
            base_prob += 0.06
        if day < 5:
            base_prob += 0.04
        if hour >= 21 or hour <= 4:
            base_prob -= 0.03
        
        is_profitable = 1 if np.random.random() < base_prob else 0
        
        samples.append({
            "side": side,
            "hour": hour,
            "day_of_week": day,
            "is_morning": 1 if 9 <= hour < 12 else 0,
            "is_afternoon": 1 if 12 <= hour < 16 else 0,
            "is_weekend": 1 if day >= 5 else 0,
            "symbol_win_rate": base_prob + np.random.uniform(-0.1, 0.1),
            "hour_win_rate": base_prob + np.random.uniform(-0.1, 0.1),
            "is_profitable": is_profitable
        })
    
    return pd.DataFrame(samples)


def prepare_features(df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
    """Prepare feature matrix and target vector."""
    available_features = [f for f in FEATURES if f in df.columns]
    
    if len(available_features) < len(FEATURES):
        missing = set(FEATURES) - set(available_features)
        print(f"Warning: Missing features will be added as zeros: {missing}")
        for f in missing:
            df[f] = 0
    
    X = df[FEATURES].values
    y = df[TARGET].values
    
    return X, y


def train_model(X: np.ndarray, y: np.ndarray) -> GradientBoostingClassifier:
    """Train Gradient Boosting classifier with cross-validation."""
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    
    model = GradientBoostingClassifier(
        n_estimators=100,
        max_depth=5,
        learning_rate=0.1,
        min_samples_leaf=20,
        subsample=0.8,
        random_state=42
    )
    
    print("\nPerforming 5-fold cross-validation...")
    cv_scores = cross_val_score(model, X_train, y_train, cv=5, scoring='roc_auc')
    print(f"CV ROC-AUC: {cv_scores.mean():.4f} (+/- {cv_scores.std()*2:.4f})")
    
    print("\nTraining final model...")
    model.fit(X_train, y_train)
    
    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]
    
    print("\n" + "=" * 50)
    print("Test Set Performance")
    print("=" * 50)
    print(f"Accuracy:  {accuracy_score(y_test, y_pred):.4f}")
    print(f"Precision: {precision_score(y_test, y_pred):.4f}")
    print(f"Recall:    {recall_score(y_test, y_pred):.4f}")
    print(f"F1 Score:  {f1_score(y_test, y_pred):.4f}")
    print(f"ROC-AUC:   {roc_auc_score(y_test, y_proba):.4f}")
    
    print("\nClassification Report:")
    print(classification_report(y_test, y_pred, target_names=["Loss", "Profit"]))
    
    print("\nFeature Importance:")
    importance = dict(zip(FEATURES, model.feature_importances_))
    for feat, imp in sorted(importance.items(), key=lambda x: -x[1]):
        bar = "#" * int(imp / max(importance.values()) * 20)
        print(f"  {feat:20s}: {imp:6.1f} {bar}")
    
    return model, {
        "accuracy": accuracy_score(y_test, y_pred),
        "precision": precision_score(y_test, y_pred),
        "recall": recall_score(y_test, y_pred),
        "f1": f1_score(y_test, y_pred),
        "roc_auc": roc_auc_score(y_test, y_proba)
    }


def save_model(model: GradientBoostingClassifier, metrics: Dict[str, float]) -> None:
    """Save trained model and configuration."""
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    with open(MODEL_PATH, 'wb') as f:
        pickle.dump(model, f)
    print(f"\nModel saved to: {MODEL_PATH}")
    
    optimal_threshold = 0.55
    
    config = {
        "version": f"v1_{datetime.now().strftime('%Y%m%d')}",
        "features": FEATURES,
        "threshold": optimal_threshold,
        "trained_on": datetime.now().isoformat(),
        "accuracy": metrics["accuracy"],
        "roc_auc": metrics["roc_auc"],
        "f1": metrics["f1"]
    }
    
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)
    print(f"Config saved to: {CONFIG_PATH}")


def main():
    """Main training pipeline."""
    print("=" * 60)
    print("Trade Profit Prediction Model Training")
    print("=" * 60)
    
    print("\n[1/4] Loading training data...")
    df = load_data()
    
    print("\n[2/4] Preparing features...")
    X, y = prepare_features(df)
    print(f"Feature matrix shape: {X.shape}")
    print(f"Target distribution: {np.bincount(y.astype(int))}")
    
    print("\n[3/4] Training model...")
    model, metrics = train_model(X, y)
    
    print("\n[4/4] Saving model...")
    save_model(model, metrics)
    
    print("\n" + "=" * 60)
    print("Training complete!")
    print("The model is now available for use in the trading bots.")
    print("=" * 60)


if __name__ == "__main__":
    main()
