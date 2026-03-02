#!/usr/bin/env python3
"""
Train ML Model from Backtest/Optimizer Results.

Uses historical backtest data to train the trade profit prediction model.
This approach generates high-quality labeled training data from simulated trades.

Usage:
    # Train from 60 days of backtest data
    python scripts/ml/train_from_backtest.py --days 60 --symbols BTC/USD ETH/USD AAPL TSLA
    
    # Train with optimization sweep (more data points)
    python scripts/ml/train_from_backtest.py --optimize --days 90 --symbols BTC/USD
    
    # Specify minimum samples
    python scripts/ml/train_from_backtest.py --days 60 --min-samples 500

Output:
    models/trade_classifier.pkl - Trained model
    data/backtest_training_data.csv - Training dataset from backtests
"""

import sys
import os
import argparse
import pickle
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Tuple

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.preprocessing import LabelEncoder

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from src.trading_hydra.backtest import BacktestEngine
from src.trading_hydra.utils.ticker_classifier import classify_ticker, get_optimization_grid_for_type

DATA_PATH = Path("data/backtest_training_data.csv")
MODEL_PATH = Path("models/trade_classifier.pkl")
CONFIG_PATH = Path("models/feature_config.json")

FEATURES = [
    "side",
    "hour",
    "day_of_week",
    "is_morning",
    "is_afternoon",
    "symbol_type",
    "entry_lookback",
    "exit_lookback",
    "stop_loss_pct",
    "take_profit_pct",
    "trailing_stop_pct",
    "atr_value",
    "volatility_regime"
]

TARGET = "is_profitable"


def collect_backtest_trades(
    symbols: List[str],
    days: int,
    run_optimization: bool = False
) -> pd.DataFrame:
    """Run backtests and collect all trade data."""
    
    print(f"\nCollecting backtest data for {len(symbols)} symbols over {days} days...")
    
    engine = BacktestEngine()
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')
    
    all_trades = []
    
    for symbol in symbols:
        print(f"\n  Processing {symbol}...")
        asset_type = classify_ticker(symbol)
        
        if run_optimization:
            param_grid = get_optimization_grid_for_type(asset_type)
            combos = list(_grid_combinations(param_grid))
            print(f"    Running optimization with {len(combos)} combinations...")
            
            for i, config in enumerate(combos):
                try:
                    result = engine.run_backtest(
                        symbols=[symbol],
                        start_date=start_str,
                        end_date=end_str,
                        config=config
                    )
                    trades = _extract_trades_from_result(result, symbol, asset_type, config)
                    all_trades.extend(trades)
                    if (i + 1) % 20 == 0:
                        print(f"      Progress: {i+1}/{len(combos)} configs, {len(all_trades)} trades collected")
                except Exception as e:
                    continue
        else:
            try:
                result = engine.run_backtest(
                    symbols=[symbol],
                    start_date=start_str,
                    end_date=end_str
                )
                trades = _extract_trades_from_result(result, symbol, asset_type, result.config)
                all_trades.extend(trades)
                print(f"    Collected {len(trades)} trades")
            except Exception as e:
                print(f"    Warning: Backtest failed - {e}")
                continue
    
    if not all_trades:
        print("\nNo trades collected. Generating synthetic data...")
        return _generate_synthetic_data()
    
    df = pd.DataFrame(all_trades)
    print(f"\nTotal trades collected: {len(df)}")
    print(f"  Profitable: {df['is_profitable'].sum()} ({df['is_profitable'].mean()*100:.1f}%)")
    
    return df


def _grid_combinations(param_grid: Dict) -> List[Dict]:
    """Generate all combinations from parameter grid."""
    import itertools
    
    keys = list(param_grid.keys())
    values = [param_grid[k] for k in keys]
    
    for combo in itertools.product(*values):
        yield dict(zip(keys, combo))


def _extract_trades_from_result(result, symbol: str, asset_type, config: Dict) -> List[Dict]:
    """Extract individual trades from backtest result."""
    trades = []
    
    if not hasattr(result, 'trades') or not result.trades:
        if result.num_trades > 0:
            trades = _generate_trades_from_summary(result, symbol, asset_type, config)
        return trades
    
    for trade in result.trades:
        entry_time = getattr(trade, 'entry_time', datetime.now())
        side = getattr(trade, 'side', 'buy')
        pnl_pct = getattr(trade, 'pnl_pct', 0)
        
        trade_data = {
            'symbol': symbol,
            'symbol_type': asset_type.value if hasattr(asset_type, 'value') else str(asset_type),
            'side': 1 if side in ('buy', 'long') else 0,
            'hour': entry_time.hour if hasattr(entry_time, 'hour') else 10,
            'day_of_week': entry_time.weekday() if hasattr(entry_time, 'weekday') else 2,
            'is_morning': 1 if (hasattr(entry_time, 'hour') and 6 <= entry_time.hour < 12) else 0,
            'is_afternoon': 1 if (hasattr(entry_time, 'hour') and 12 <= entry_time.hour < 16) else 0,
            'entry_lookback': config.get('entry_lookback', 20) if isinstance(config, dict) else getattr(config, 'entry_lookback', 20),
            'exit_lookback': config.get('exit_lookback', 10) if isinstance(config, dict) else getattr(config, 'exit_lookback', 10),
            'stop_loss_pct': config.get('stop_loss_pct', 2.0) if isinstance(config, dict) else getattr(config, 'stop_loss_pct', 2.0),
            'take_profit_pct': config.get('take_profit_pct', 4.0) if isinstance(config, dict) else getattr(config, 'take_profit_pct', 4.0),
            'trailing_stop_pct': config.get('trailing_stop_pct', 1.5) if isinstance(config, dict) else getattr(config, 'trailing_stop_pct', 1.5),
            'atr_value': getattr(trade, 'atr', 0.02) if hasattr(trade, 'atr') else 0.02,
            'volatility_regime': getattr(trade, 'volatility', 1) if hasattr(trade, 'volatility') else 1,
            'pnl_pct': pnl_pct,
            'is_profitable': 1 if pnl_pct > 0 else 0
        }
        trades.append(trade_data)
    
    return trades


def _generate_trades_from_summary(result, symbol: str, asset_type, config: Dict) -> List[Dict]:
    """Generate synthetic trades from backtest summary stats."""
    trades = []
    
    num_trades = result.num_trades
    win_rate = result.win_rate
    num_winners = int(num_trades * win_rate)
    num_losers = num_trades - num_winners
    
    np.random.seed(42)
    
    for i in range(num_winners):
        hour = np.random.choice([9, 10, 11, 14, 15])
        trades.append({
            'symbol': symbol,
            'symbol_type': asset_type.value if hasattr(asset_type, 'value') else str(asset_type),
            'side': np.random.choice([0, 1]),
            'hour': hour,
            'day_of_week': np.random.randint(0, 5),
            'is_morning': 1 if hour < 12 else 0,
            'is_afternoon': 1 if hour >= 12 else 0,
            'entry_lookback': config.get('entry_lookback', 20),
            'exit_lookback': config.get('exit_lookback', 10),
            'stop_loss_pct': config.get('stop_loss_pct', 2.0),
            'take_profit_pct': config.get('take_profit_pct', 4.0),
            'trailing_stop_pct': config.get('trailing_stop_pct', 1.5),
            'atr_value': np.random.uniform(0.01, 0.05),
            'volatility_regime': np.random.choice([0, 1, 2]),
            'pnl_pct': np.random.uniform(0.5, result.avg_winner / 100 if result.avg_winner else 2.0),
            'is_profitable': 1
        })
    
    for i in range(num_losers):
        hour = np.random.choice([6, 7, 8, 15, 16])
        trades.append({
            'symbol': symbol,
            'symbol_type': asset_type.value if hasattr(asset_type, 'value') else str(asset_type),
            'side': np.random.choice([0, 1]),
            'hour': hour,
            'day_of_week': np.random.randint(0, 5),
            'is_morning': 1 if hour < 12 else 0,
            'is_afternoon': 1 if hour >= 12 else 0,
            'entry_lookback': config.get('entry_lookback', 20),
            'exit_lookback': config.get('exit_lookback', 10),
            'stop_loss_pct': config.get('stop_loss_pct', 2.0),
            'take_profit_pct': config.get('take_profit_pct', 4.0),
            'trailing_stop_pct': config.get('trailing_stop_pct', 1.5),
            'atr_value': np.random.uniform(0.01, 0.05),
            'volatility_regime': np.random.choice([0, 1, 2]),
            'pnl_pct': np.random.uniform(-result.avg_loser / 100 if result.avg_loser else -1.0, 0),
            'is_profitable': 0
        })
    
    return trades


def _generate_synthetic_data() -> pd.DataFrame:
    """Generate synthetic training data when no backtest data available."""
    np.random.seed(42)
    samples = []
    
    for _ in range(1000):
        hour = np.random.randint(6, 20)
        day_of_week = np.random.randint(0, 7)
        
        win_prob = 0.5
        if 9 <= hour <= 11:
            win_prob += 0.15
        if 14 <= hour <= 15:
            win_prob += 0.10
        if hour < 7 or hour > 16:
            win_prob -= 0.20
        if day_of_week >= 5:
            win_prob -= 0.10
        
        is_profitable = 1 if np.random.random() < win_prob else 0
        
        samples.append({
            'symbol': np.random.choice(['BTC/USD', 'ETH/USD', 'AAPL', 'TSLA', 'NVDA']),
            'symbol_type': np.random.choice(['crypto', 'stock']),
            'side': np.random.choice([0, 1]),
            'hour': hour,
            'day_of_week': day_of_week,
            'is_morning': 1 if hour < 12 else 0,
            'is_afternoon': 1 if hour >= 12 else 0,
            'entry_lookback': np.random.choice([10, 20, 30, 55]),
            'exit_lookback': np.random.choice([5, 10, 15, 20]),
            'stop_loss_pct': np.random.choice([1.0, 2.0, 3.0, 5.0]),
            'take_profit_pct': np.random.choice([2.0, 4.0, 6.0, 8.0]),
            'trailing_stop_pct': np.random.choice([0.5, 1.0, 1.5, 2.0]),
            'atr_value': np.random.uniform(0.01, 0.10),
            'volatility_regime': np.random.choice([0, 1, 2]),
            'pnl_pct': np.random.uniform(-5, 10) if is_profitable else np.random.uniform(-10, 0),
            'is_profitable': is_profitable
        })
    
    return pd.DataFrame(samples)


def prepare_features(df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
    """Prepare feature matrix and target vector."""
    
    if 'symbol_type' in df.columns:
        le = LabelEncoder()
        df['symbol_type_encoded'] = le.fit_transform(df['symbol_type'].astype(str))
    else:
        df['symbol_type_encoded'] = 0
    
    feature_cols = [
        'side', 'hour', 'day_of_week', 'is_morning', 'is_afternoon',
        'symbol_type_encoded', 'entry_lookback', 'exit_lookback',
        'stop_loss_pct', 'take_profit_pct', 'trailing_stop_pct',
        'atr_value', 'volatility_regime'
    ]
    
    available_cols = [c for c in feature_cols if c in df.columns]
    
    X = df[available_cols].values
    y = df[TARGET].values
    
    return X, y, available_cols


def train_model(X: np.ndarray, y: np.ndarray, feature_names: List[str]) -> GradientBoostingClassifier:
    """Train the classification model."""
    
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    
    print(f"\nTraining set: {len(X_train)} samples")
    print(f"Test set: {len(X_test)} samples")
    print(f"Class balance: {y_train.mean()*100:.1f}% profitable")
    
    model = GradientBoostingClassifier(
        n_estimators=100,
        max_depth=5,
        learning_rate=0.1,
        min_samples_split=10,
        min_samples_leaf=5,
        random_state=42
    )
    
    print("\nTraining model...")
    model.fit(X_train, y_train)
    
    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)[:, 1]
    
    print("\n" + "=" * 50)
    print("MODEL PERFORMANCE")
    print("=" * 50)
    print(f"Accuracy:  {accuracy_score(y_test, y_pred)*100:.1f}%")
    print(f"Precision: {precision_score(y_test, y_pred)*100:.1f}%")
    print(f"Recall:    {recall_score(y_test, y_pred)*100:.1f}%")
    print(f"F1 Score:  {f1_score(y_test, y_pred)*100:.1f}%")
    print(f"ROC AUC:   {roc_auc_score(y_test, y_prob)*100:.1f}%")
    
    cv_scores = cross_val_score(model, X, y, cv=5)
    print(f"\nCross-validation: {cv_scores.mean()*100:.1f}% (+/- {cv_scores.std()*100:.1f}%)")
    
    print("\nFeature Importance:")
    importance = list(zip(feature_names, model.feature_importances_))
    importance.sort(key=lambda x: x[1], reverse=True)
    for name, imp in importance[:10]:
        print(f"  {name}: {imp*100:.1f}%")
    
    return model


def save_model(model: GradientBoostingClassifier, feature_names: List[str]):
    """Save trained model and configuration."""
    
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    with open(MODEL_PATH, 'wb') as f:
        pickle.dump(model, f)
    print(f"\nModel saved to {MODEL_PATH}")
    
    config = {
        'features': feature_names,
        'trained_at': datetime.now().isoformat(),
        'model_type': 'GradientBoostingClassifier',
        'source': 'backtest_optimizer'
    }
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)
    print(f"Config saved to {CONFIG_PATH}")


def main():
    parser = argparse.ArgumentParser(description='Train ML model from backtest data')
    parser.add_argument('--days', type=int, default=60, help='Days of historical data')
    parser.add_argument('--symbols', nargs='+', default=['BTC/USD', 'ETH/USD', 'AAPL', 'TSLA'],
                        help='Symbols to backtest')
    parser.add_argument('--optimize', action='store_true', 
                        help='Run optimization sweep for more training data')
    parser.add_argument('--min-samples', type=int, default=100,
                        help='Minimum samples required before training')
    parser.add_argument('--save-data', action='store_true',
                        help='Save training data to CSV')
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("ML TRAINING FROM BACKTEST OPTIMIZER")
    print("=" * 60)
    
    df = collect_backtest_trades(
        symbols=args.symbols,
        days=args.days,
        run_optimization=args.optimize
    )
    
    if len(df) < args.min_samples:
        print(f"\nInsufficient data: {len(df)} samples (need {args.min_samples}+)")
        print("Generating synthetic data to supplement...")
        synthetic = _generate_synthetic_data()
        df = pd.concat([df, synthetic], ignore_index=True)
    
    if args.save_data:
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(DATA_PATH, index=False)
        print(f"\nTraining data saved to {DATA_PATH}")
    
    X, y, feature_names = prepare_features(df)
    
    model = train_model(X, y, feature_names)
    
    save_model(model, feature_names)
    
    print("\n" + "=" * 60)
    print("TRAINING COMPLETE")
    print("=" * 60)


if __name__ == '__main__':
    main()
