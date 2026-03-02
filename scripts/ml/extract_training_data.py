#!/usr/bin/env python3
"""
Extract Training Data from Trading Logs.

Reads the app.jsonl log file and extracts trade entries with their
outcomes (profitable or not) to create a labeled dataset for ML training.

Usage:
    python scripts/ml/extract_training_data.py
    
Output:
    data/training_data.csv - Labeled dataset for model training
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import defaultdict

import pandas as pd
import numpy as np

LOGS_PATH = Path("logs/app.jsonl")
OUTPUT_PATH = Path("data/training_data.csv")
PROFIT_HORIZON_MINUTES = 30


def parse_jsonl(filepath: Path) -> List[Dict[str, Any]]:
    """Parse JSONL log file into list of events."""
    events = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def extract_order_events(events: List[Dict[str, Any]]) -> Dict[str, List[Dict]]:
    """Extract order placement and close events grouped by symbol."""
    orders_by_symbol = defaultdict(list)
    closes_by_symbol = defaultdict(list)
    
    for event in events:
        event_type = event.get("event", "")
        
        if event_type in ["crypto_order_placed", "momentum_order_placed", "options_order_placed"]:
            symbol = event.get("symbol", "").replace("/", "")
            orders_by_symbol[symbol].append({
                "timestamp": event.get("ts"),
                "side": event.get("side"),
                "qty": event.get("qty"),
                "notional": event.get("notional"),
                "price": event.get("mid_price") or event.get("price"),
                "order_id": event.get("order_id"),
                "bot_type": event_type.split("_")[0]
            })
        
        elif event_type in ["crypto_position_closed", "momentum_position_closed", "options_position_closed"]:
            symbol = event.get("symbol", "").replace("/", "")
            closes_by_symbol[symbol].append({
                "timestamp": event.get("ts"),
                "pnl_pct": event.get("pnl_pct"),
                "pnl_dollars": event.get("pnl_dollars"),
                "reason": event.get("reason"),
                "side": event.get("side")
            })
    
    return orders_by_symbol, closes_by_symbol


def extract_regime_data(events: List[Dict[str, Any]]) -> Dict[str, Dict]:
    """Extract market regime data indexed by timestamp (hour)."""
    regime_by_hour = {}
    
    for event in events:
        if event.get("event") == "market_regime_update" or "vix" in event:
            ts = event.get("ts", "")
            if ts:
                try:
                    hour_key = ts[:13]
                    regime_by_hour[hour_key] = {
                        "vix": event.get("vix", 20),
                        "sentiment": event.get("sentiment", "neutral"),
                        "regime": event.get("regime", "NORMAL")
                    }
                except:
                    pass
    
    return regime_by_hour


def match_trades_with_outcomes(
    orders: Dict[str, List],
    closes: Dict[str, List]
) -> List[Dict[str, Any]]:
    """Match order entries with their close events to determine profit/loss."""
    training_samples = []
    
    for symbol, order_list in orders.items():
        close_list = closes.get(symbol, [])
        
        for order in order_list:
            order_ts = order.get("timestamp", "")
            if not order_ts:
                continue
            
            try:
                order_time = datetime.fromisoformat(order_ts.replace("Z", "+00:00"))
            except:
                continue
            
            matched_close = None
            for close in close_list:
                close_ts = close.get("timestamp", "")
                if not close_ts:
                    continue
                try:
                    close_time = datetime.fromisoformat(close_ts.replace("Z", "+00:00"))
                    if close_time > order_time:
                        matched_close = close
                        break
                except:
                    continue
            
            if matched_close:
                pnl_pct = matched_close.get("pnl_pct", 0) or 0
                is_profitable = 1 if pnl_pct > 0 else 0
                
                hour = order_time.hour
                day_of_week = order_time.weekday()
                
                sample = {
                    "symbol": symbol,
                    "side": 1 if order.get("side") == "buy" else 0,
                    "price": order.get("price", 0),
                    "hour": hour,
                    "day_of_week": day_of_week,
                    "bot_type": order.get("bot_type", "unknown"),
                    "pnl_pct": pnl_pct,
                    "is_profitable": is_profitable,
                    "close_reason": matched_close.get("reason", "unknown")
                }
                training_samples.append(sample)
    
    return training_samples


def add_technical_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add derived technical features to the dataset."""
    df = df.copy()
    
    df["is_morning"] = (df["hour"] >= 9) & (df["hour"] < 12)
    df["is_afternoon"] = (df["hour"] >= 12) & (df["hour"] < 16)
    df["is_weekend"] = df["day_of_week"] >= 5
    
    symbol_profit_rate = df.groupby("symbol")["is_profitable"].transform("mean")
    df["symbol_win_rate"] = symbol_profit_rate
    
    hour_profit_rate = df.groupby("hour")["is_profitable"].transform("mean")
    df["hour_win_rate"] = hour_profit_rate
    
    return df


def main():
    """Main extraction pipeline."""
    print("=" * 60)
    print("Trading Data Extraction for ML Training")
    print("=" * 60)
    
    if not LOGS_PATH.exists():
        print(f"Error: Log file not found at {LOGS_PATH}")
        sys.exit(1)
    
    print(f"\n[1/5] Reading log file: {LOGS_PATH}")
    events = parse_jsonl(LOGS_PATH)
    print(f"      Found {len(events)} log events")
    
    print("\n[2/5] Extracting order and close events...")
    orders, closes = extract_order_events(events)
    total_orders = sum(len(v) for v in orders.values())
    total_closes = sum(len(v) for v in closes.values())
    print(f"      Orders: {total_orders}, Closes: {total_closes}")
    
    print("\n[3/5] Extracting market regime data...")
    regime_data = extract_regime_data(events)
    print(f"      Regime snapshots: {len(regime_data)}")
    
    print("\n[4/5] Matching trades with outcomes...")
    samples = match_trades_with_outcomes(orders, closes)
    print(f"      Matched samples: {len(samples)}")
    
    if not samples:
        print("\nWarning: No matched trade samples found.")
        print("The model needs historical trade data with outcomes.")
        print("Run the bot for a while to collect training data.")
        
        print("\nCreating synthetic sample dataset for demonstration...")
        samples = create_synthetic_samples()
    
    print("\n[5/5] Adding features and saving dataset...")
    df = pd.DataFrame(samples)
    df = add_technical_features(df)
    
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_PATH, index=False)
    
    print(f"\n{'=' * 60}")
    print(f"Dataset saved to: {OUTPUT_PATH}")
    print(f"Total samples: {len(df)}")
    print(f"Profitable trades: {df['is_profitable'].sum()} ({df['is_profitable'].mean()*100:.1f}%)")
    print(f"Features: {list(df.columns)}")
    print("=" * 60)


def create_synthetic_samples() -> List[Dict[str, Any]]:
    """Create synthetic training samples for demonstration."""
    np.random.seed(42)
    samples = []
    
    symbols = ["BTCUSD", "ETHUSD", "LINKUSD", "DOGEUSD", "AVAXUSD", 
               "AAPL", "AMD", "MSFT", "SPY", "QQQ"]
    
    for _ in range(500):
        symbol = np.random.choice(symbols)
        side = np.random.choice([0, 1])
        hour = np.random.randint(0, 24)
        day = np.random.randint(0, 7)
        
        base_profit_prob = 0.52
        if hour >= 9 and hour <= 15:
            base_profit_prob += 0.05
        if day < 5:
            base_profit_prob += 0.03
        if symbol in ["BTCUSD", "ETHUSD"]:
            base_profit_prob -= 0.02
        
        is_profitable = 1 if np.random.random() < base_profit_prob else 0
        pnl_pct = np.random.uniform(0.1, 2.0) if is_profitable else np.random.uniform(-2.0, -0.1)
        
        samples.append({
            "symbol": symbol,
            "side": side,
            "price": np.random.uniform(10, 50000),
            "hour": hour,
            "day_of_week": day,
            "bot_type": "crypto" if "USD" in symbol else "momentum",
            "pnl_pct": pnl_pct,
            "is_profitable": is_profitable,
            "close_reason": "take_profit" if is_profitable else "stop_loss"
        })
    
    return samples


if __name__ == "__main__":
    main()
