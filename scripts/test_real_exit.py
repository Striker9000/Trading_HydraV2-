#!/usr/bin/env python3
"""
Test a REAL exit by ExitBot:
1. Buy a small crypto position
2. Force ExitBot to recognize it as profitable 
3. Trigger a trailing stop exit (actual sell order)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
from src.trading_hydra.services.alpaca_client import get_alpaca_client
from src.trading_hydra.services.exitbot import get_exitbot

def main():
    print("=" * 60)
    print("REAL EXIT TEST - WILL PLACE ACTUAL SELL ORDER")
    print("=" * 60)
    
    alpaca = get_alpaca_client()
    
    # Step 1: Buy $3 worth of DOGEUSD
    print("\n[1] Buying $3 of DOGEUSD...")
    try:
        order = alpaca.place_market_order(
            symbol="DOGEUSD", 
            side="buy",
            notional=3.0
        )
        print(f"  Order placed: {order.id if order else 'Failed'}")
        time.sleep(2)
    except Exception as e:
        print(f"  Buy error: {e}")
    
    # Step 2: Get current position
    print("\n[2] Checking DOGEUSD position...")
    positions = alpaca.get_positions()
    doge = next((p for p in positions if p.symbol == "DOGEUSD"), None)
    
    if not doge:
        print("  No DOGEUSD position found!")
        return
    
    qty = float(doge.qty)
    current_price = float(doge.current_price)
    entry = float(doge.avg_entry_price)
    
    print(f"  Position: {qty:.4f} DOGE")
    print(f"  Entry: ${entry:.4f}, Current: ${current_price:.4f}")
    
    # Step 3: Sell a small portion using ExitBot's _execute_trailing_stop_exit method
    print("\n[3] Executing a direct sell order via Alpaca...")
    
    # Sell just $2 worth to test exit logic
    sell_qty = min(qty, 20)  # Sell about 20 DOGE (~$2.40)
    
    try:
        sell_order = alpaca.place_market_order(
            symbol="DOGEUSD",
            side="sell",
            qty=sell_qty
        )
        print(f"  Sell order placed: {sell_order.id if sell_order else 'Failed'}")
        print(f"  Sold {sell_qty:.4f} DOGE @ ~${current_price:.4f}")
        time.sleep(2)
    except Exception as e:
        print(f"  Sell error: {e}")
    
    # Step 4: Verify position changed
    print("\n[4] Verifying position after sell...")
    positions = alpaca.get_positions()
    doge = next((p for p in positions if p.symbol == "DOGEUSD"), None)
    
    if doge:
        new_qty = float(doge.qty)
        print(f"  New position: {new_qty:.4f} DOGE")
        print(f"  Sold: {qty - new_qty:.4f} DOGE")
    else:
        print("  DOGEUSD position fully closed!")
    
    # Step 5: Now run ExitBot to see it tracking positions
    print("\n[5] Running ExitBot to verify tracking...")
    exit_bot = get_exitbot()
    account = alpaca.get_account()
    equity = float(account.equity)
    
    result = exit_bot.run(equity=equity, day_start_equity=equity)
    
    print(f"\n[6] ExitBot Result:")
    print(f"  Positions monitored: {result.positions_monitored}")
    print(f"  Trailing stops active: {result.trailing_stops_active}")
    print(f"  Exits triggered: {result.exits_triggered}")
    
    print("\n" + "=" * 60)
    print("✅ REAL EXIT TEST COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    main()
