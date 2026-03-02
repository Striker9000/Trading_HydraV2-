#!/usr/bin/env python3
"""
Train Account-Level ML Models

This script trains the 5 account-level ML models using historical data
collected in state/metrics.db. Run after accumulating enough trading history.

Usage:
    python scripts/ml/train_account_models.py [--min-days 30] [--model all]

Minimum Data Requirements:
    - RiskAdjustmentEngine: 30+ days with varied equity changes
    - BotAllocationModel: 30+ days with bot performance data
    - RegimeSizer: 30+ days with regime history
    - DrawdownPredictor: 60+ days to capture drawdown patterns
    - AnomalyDetector: 30+ days to establish baselines
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier, IsolationForest
from sklearn.preprocessing import StandardScaler
import pickle

from src.trading_hydra.core.logging import get_logger
from src.trading_hydra.ml.metrics_repository import MetricsRepository


logger = get_logger()


def train_risk_adjustment(repo: MetricsRepository, min_days: int = 30) -> bool:
    """Train RiskAdjustmentEngine model."""
    print("\n[1/5] Training RiskAdjustmentEngine...")
    
    metrics = repo.get_daily_metrics_range(days=365)
    
    if len(metrics) < min_days:
        print(f"    Insufficient data: {len(metrics)} days (need {min_days}+)")
        return False
    
    X = []
    y = []
    
    for i in range(5, len(metrics)):
        m = metrics[i]
        prev_metrics = metrics[max(0, i-5):i]
        
        equity_changes = []
        for j in range(1, len(prev_metrics)):
            if prev_metrics[j-1].equity > 0:
                change = (prev_metrics[j].equity - prev_metrics[j-1].equity) / prev_metrics[j-1].equity
                equity_changes.append(change)
        
        features = [
            m.current_drawdown_pct,
            m.max_drawdown_pct,
            m.win_rate,
            m.profit_factor if m.profit_factor < 10 else 10,
            np.mean(equity_changes) if equity_changes else 0,
            np.std(equity_changes) if len(equity_changes) > 1 else 0,
            m.total_trades,
            1 if m.daily_pnl >= 0 else 0
        ]
        
        if i + 1 < len(metrics):
            next_day_pnl = metrics[i + 1].daily_pnl_pct
            if next_day_pnl > 0.5:
                label = 2
            elif next_day_pnl < -0.5:
                label = 0
            else:
                label = 1
        else:
            label = 1
        
        X.append(features)
        y.append(label)
    
    if len(X) < 20:
        print(f"    Insufficient training samples: {len(X)} (need 20+)")
        return False
    
    X = np.array(X)
    y = np.array(y)
    
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    model = GradientBoostingClassifier(
        n_estimators=100,
        max_depth=4,
        learning_rate=0.1,
        random_state=42
    )
    model.fit(X_scaled, y)
    
    model_path = Path("models/account_ml/risk_adjustment.pkl")
    model_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(model_path, 'wb') as f:
        pickle.dump({
            'model': model,
            'scaler': scaler,
            'features': ['drawdown', 'max_dd', 'win_rate', 'profit_factor', 
                        'equity_change_mean', 'equity_change_std', 'total_trades', 'profitable_day'],
            'trained_at': datetime.now().isoformat(),
            'samples': len(X)
        }, f)
    
    print(f"    Trained on {len(X)} samples")
    print(f"    Saved to {model_path}")
    return True


def train_bot_allocation(repo: MetricsRepository, min_days: int = 30) -> bool:
    """Train BotAllocationModel."""
    print("\n[2/5] Training BotAllocationModel...")
    
    bot_perf = repo.get_all_bot_performance_range(days=365)
    
    if len(bot_perf) < min_days:
        print(f"    Insufficient data: {len(bot_perf)} records (need {min_days}+)")
        return False
    
    from collections import defaultdict
    daily_bot_data = defaultdict(lambda: defaultdict(dict))
    
    for bp in bot_perf:
        daily_bot_data[bp.date][bp.bot_id] = {
            'pnl': bp.pnl_today,
            'trades': bp.trades_today,
            'win_rate': bp.win_rate_30d,
            'sharpe': bp.sharpe_ratio_30d
        }
    
    X = []
    y = []
    
    dates = sorted(daily_bot_data.keys())
    for i, date in enumerate(dates[:-1]):
        bots = daily_bot_data[date]
        next_bots = daily_bot_data[dates[i + 1]]
        
        features = []
        for bot_type in ['crypto', 'momentum', 'options']:
            bot_data = None
            for bot_id, data in bots.items():
                if bot_type in bot_id.lower():
                    bot_data = data
                    break
            
            if bot_data:
                features.extend([
                    bot_data.get('pnl', 0),
                    bot_data.get('trades', 0),
                    bot_data.get('win_rate', 0.5),
                    bot_data.get('sharpe', 0)
                ])
            else:
                features.extend([0, 0, 0.5, 0])
        
        best_bot = 'crypto'
        best_pnl = -float('inf')
        for bot_id, data in next_bots.items():
            if data.get('pnl', 0) > best_pnl:
                best_pnl = data['pnl']
                if 'crypto' in bot_id.lower():
                    best_bot = 'crypto'
                elif 'mom' in bot_id.lower():
                    best_bot = 'momentum'
                else:
                    best_bot = 'options'
        
        label = {'crypto': 0, 'momentum': 1, 'options': 2}.get(best_bot, 0)
        
        X.append(features)
        y.append(label)
    
    if len(X) < 20:
        print(f"    Insufficient training samples: {len(X)} (need 20+)")
        return False
    
    X = np.array(X)
    y = np.array(y)
    
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    model = RandomForestClassifier(
        n_estimators=100,
        max_depth=5,
        random_state=42
    )
    model.fit(X_scaled, y)
    
    model_path = Path("models/account_ml/bot_allocation.pkl")
    model_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(model_path, 'wb') as f:
        pickle.dump({
            'model': model,
            'scaler': scaler,
            'features': ['crypto_pnl', 'crypto_trades', 'crypto_wr', 'crypto_sharpe',
                        'mom_pnl', 'mom_trades', 'mom_wr', 'mom_sharpe',
                        'opt_pnl', 'opt_trades', 'opt_wr', 'opt_sharpe'],
            'trained_at': datetime.now().isoformat(),
            'samples': len(X)
        }, f)
    
    print(f"    Trained on {len(X)} samples")
    print(f"    Saved to {model_path}")
    return True


def train_regime_sizer(repo: MetricsRepository, min_days: int = 30) -> bool:
    """Train RegimeSizer model."""
    print("\n[3/5] Training RegimeSizer...")
    
    regimes = repo.get_regime_history(days=365)
    metrics = repo.get_daily_metrics_range(days=365)
    
    if len(regimes) < min_days:
        print(f"    Insufficient regime data: {len(regimes)} records (need {min_days}+)")
        return False
    
    metrics_by_date = {m.date: m for m in metrics}
    
    X = []
    y = []
    
    for r in regimes:
        m = metrics_by_date.get(r.date)
        if not m:
            continue
        
        features = [
            r.vix,
            r.vvix,
            r.tnx,
            r.dxy,
            r.move,
            1 if r.volatility_regime == 'high' else (0.5 if r.volatility_regime == 'elevated' else 0),
            1 if r.halt_new_entries else 0
        ]
        
        if m.daily_pnl_pct > 1.0:
            label = 2
        elif m.daily_pnl_pct > 0:
            label = 1
        elif m.daily_pnl_pct > -1.0:
            label = 0
        else:
            label = 0
        
        X.append(features)
        y.append(label)
    
    if len(X) < 20:
        print(f"    Insufficient training samples: {len(X)} (need 20+)")
        return False
    
    X = np.array(X)
    y = np.array(y)
    
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    model = GradientBoostingClassifier(
        n_estimators=100,
        max_depth=3,
        learning_rate=0.1,
        random_state=42
    )
    model.fit(X_scaled, y)
    
    model_path = Path("models/account_ml/regime_sizer.pkl")
    model_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(model_path, 'wb') as f:
        pickle.dump({
            'model': model,
            'scaler': scaler,
            'features': ['vix', 'vvix', 'tnx', 'dxy', 'move', 'vol_regime', 'halt_flag'],
            'trained_at': datetime.now().isoformat(),
            'samples': len(X)
        }, f)
    
    print(f"    Trained on {len(X)} samples")
    print(f"    Saved to {model_path}")
    return True


def train_drawdown_predictor(repo: MetricsRepository, min_days: int = 60) -> bool:
    """Train DrawdownPredictor model."""
    print("\n[4/5] Training DrawdownPredictor...")
    
    metrics = repo.get_daily_metrics_range(days=365)
    
    if len(metrics) < min_days:
        print(f"    Insufficient data: {len(metrics)} days (need {min_days}+)")
        return False
    
    X = []
    y = []
    
    for i in range(10, len(metrics) - 5):
        m = metrics[i]
        prev_metrics = metrics[max(0, i-10):i]
        future_metrics = metrics[i+1:i+6]
        
        pnl_streak = 0
        for pm in reversed(prev_metrics):
            if pm.daily_pnl >= 0:
                pnl_streak += 1
            else:
                pnl_streak -= 1
                break
        
        vol = np.std([pm.daily_pnl_pct for pm in prev_metrics]) if prev_metrics else 0
        
        features = [
            m.current_drawdown_pct,
            m.max_drawdown_pct,
            m.daily_pnl_pct,
            pnl_streak,
            vol,
            m.win_rate,
            m.open_positions,
            m.total_trades
        ]
        
        max_future_dd = max([fm.current_drawdown_pct for fm in future_metrics]) if future_metrics else 0
        label = 1 if max_future_dd > 2.0 else 0
        
        X.append(features)
        y.append(label)
    
    if len(X) < 30:
        print(f"    Insufficient training samples: {len(X)} (need 30+)")
        return False
    
    X = np.array(X)
    y = np.array(y)
    
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    model = GradientBoostingClassifier(
        n_estimators=100,
        max_depth=4,
        learning_rate=0.1,
        random_state=42
    )
    model.fit(X_scaled, y)
    
    model_path = Path("models/account_ml/drawdown_predictor.pkl")
    model_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(model_path, 'wb') as f:
        pickle.dump({
            'model': model,
            'scaler': scaler,
            'features': ['current_dd', 'max_dd', 'daily_pnl', 'pnl_streak', 
                        'volatility', 'win_rate', 'positions', 'trades'],
            'trained_at': datetime.now().isoformat(),
            'samples': len(X)
        }, f)
    
    print(f"    Trained on {len(X)} samples")
    print(f"    Saved to {model_path}")
    return True


def train_anomaly_detector(repo: MetricsRepository, min_days: int = 30) -> bool:
    """Train AnomalyDetector model."""
    print("\n[5/5] Training AnomalyDetector...")
    
    metrics = repo.get_daily_metrics_range(days=365)
    
    if len(metrics) < min_days:
        print(f"    Insufficient data: {len(metrics)} days (need {min_days}+)")
        return False
    
    X = []
    
    for m in metrics:
        features = [
            m.equity,
            m.daily_pnl,
            m.daily_pnl_pct,
            m.total_trades,
            m.win_rate,
            m.open_positions,
            m.current_drawdown_pct,
            m.profit_factor if m.profit_factor < 10 else 10
        ]
        X.append(features)
    
    X = np.array(X)
    
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    model = IsolationForest(
        n_estimators=100,
        contamination="auto",
        random_state=42
    )
    model.fit(X_scaled)
    
    model_path = Path("models/account_ml/anomaly_detector.pkl")
    model_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(model_path, 'wb') as f:
        pickle.dump({
            'model': model,
            'scaler': scaler,
            'features': ['equity', 'daily_pnl', 'pnl_pct', 'trades', 
                        'win_rate', 'positions', 'drawdown', 'profit_factor'],
            'trained_at': datetime.now().isoformat(),
            'samples': len(X)
        }, f)
    
    print(f"    Trained on {len(X)} samples")
    print(f"    Saved to {model_path}")
    return True


def main():
    parser = argparse.ArgumentParser(description='Train account-level ML models')
    parser.add_argument('--min-days', type=int, default=30, help='Minimum days of data required')
    parser.add_argument('--model', type=str, default='all', 
                       choices=['all', 'risk', 'allocation', 'regime', 'drawdown', 'anomaly'],
                       help='Which model to train')
    args = parser.parse_args()
    
    print("=" * 60)
    print("TRAINING ACCOUNT-LEVEL ML MODELS")
    print("=" * 60)
    
    repo = MetricsRepository()
    
    metrics = repo.get_daily_metrics_range(days=365)
    print(f"\nData available: {len(metrics)} days of metrics")
    
    results = {}
    
    if args.model in ['all', 'risk']:
        results['RiskAdjustmentEngine'] = train_risk_adjustment(repo, args.min_days)
    
    if args.model in ['all', 'allocation']:
        results['BotAllocationModel'] = train_bot_allocation(repo, args.min_days)
    
    if args.model in ['all', 'regime']:
        results['RegimeSizer'] = train_regime_sizer(repo, args.min_days)
    
    if args.model in ['all', 'drawdown']:
        results['DrawdownPredictor'] = train_drawdown_predictor(repo, max(60, args.min_days))
    
    if args.model in ['all', 'anomaly']:
        results['AnomalyDetector'] = train_anomaly_detector(repo, args.min_days)
    
    print("\n" + "=" * 60)
    print("TRAINING SUMMARY")
    print("=" * 60)
    
    for model, success in results.items():
        status = "TRAINED" if success else "SKIPPED (insufficient data)"
        print(f"  {model}: {status}")
    
    trained_count = sum(1 for s in results.values() if s)
    print(f"\n{trained_count}/{len(results)} models trained successfully")
    print("=" * 60)


if __name__ == '__main__':
    main()
