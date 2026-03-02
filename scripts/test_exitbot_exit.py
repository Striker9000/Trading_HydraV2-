#!/usr/bin/env python3
"""
Test ExitBot's exit logic by directly triggering a trailing stop exit.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.trading_hydra.services.alpaca_client import get_alpaca_client
from src.trading_hydra.services.exitbot import get_exitbot
from src.trading_hydra.risk.trailing_stop import get_trailing_stop_manager

def main():
    print("=" * 60)
    print("EXITBOT TRAILING STOP EXIT TEST")
    print("=" * 60)
    
    alpaca = get_alpaca_client()
    ts_mgr = get_trailing_stop_manager()
    
    # Get current positions
    print("\n[1] Current positions:")
    positions = alpaca.get_positions()
    
    for p in positions:
        entry = float(p.avg_entry_price)
        current = float(p.current_price)
        pnl_pct = ((current - entry) / entry) * 100 if entry > 0 else 0
        print(f"  {p.symbol}: entry=${entry:.4f}, current=${current:.4f}, P/L={pnl_pct:.1f}%")
    
    # Find DOGEUSD position
    doge = next((p for p in positions if p.symbol == "DOGEUSD"), None)
    if not doge:
        print("\n  No DOGEUSD - using UNIUSD instead")
        doge = next((p for p in positions if p.symbol == "UNIUSD"), None)
    
    if not doge:
        print("  No crypto positions to test with!")
        return
    
    symbol = doge.symbol
    current_price = float(doge.current_price)
    entry_price = float(doge.avg_entry_price)
    
    print(f"\n[2] Testing with {symbol}:")
    print(f"  Entry: ${entry_price:.4f}")
    print(f"  Current: ${current_price:.4f}")
    
    # Initialize trailing stop with a fake low entry to simulate profit
    from src.trading_hydra.risk.trailing_stop import TrailingStopConfig
    
    fake_entry = current_price * 0.5  # Fake 100% profit
    
    config = TrailingStopConfig(
        activation_profit_pct=0.10,  # 10% profit to arm
        value=2.0,                   # 2% trailing
    )
    
    print(f"\n[3] Initializing trailing stop with fake entry ${fake_entry:.4f} (simulating +100% profit)")
    
    ts_state = ts_mgr.init_for_position(
        bot_id="exitbot_test",
        position_id=f"{symbol}_test",
        symbol=symbol,
        asset_class="crypto",
        entry_price=fake_entry,
        side="long",
        config=config
    )
    
    print(f"  Initial state: armed={ts_state.armed}, stop=${ts_state.stop_price:.4f}")
    
    # Update state with current price to arm it
    print(f"\n[4] Updating state with current price ${current_price:.4f}...")
    
    updated = ts_mgr.update_state(
        bot_id="exitbot_test",
        position_id=f"{symbol}_test",
        symbol=symbol,
        asset_class="crypto",
        current_price=current_price,
        state=ts_state
    )
    
    print(f"  Updated state: armed={updated.armed}, high_water=${updated.high_water:.4f}, stop=${updated.stop_price:.4f}")
    
    # Check if should exit with a lower price
    low_price = current_price * 0.97  # 3% drop - should trigger
    print(f"\n[5] Simulating price drop to ${low_price:.4f}...")
    
    should_exit = ts_mgr.should_exit(
        state=updated,
        current_price=low_price
    )
    
    print(f"  Should exit: {should_exit}")
    
    if should_exit:
        print("\n✅ TRAILING STOP EXIT LOGIC VERIFIED!")
        print(f"  Stop price ${updated.stop_price:.4f} > simulated price ${low_price:.4f}")
    else:
        print("\n⚠️ Exit not triggered")
        print(f"  Stop: ${updated.stop_price:.4f}, Price: ${low_price:.4f}")
    
    # Now test with ExitBot directly
    print("\n" + "=" * 60)
    print("FULL EXITBOT RUN")
    print("=" * 60)
    
    exit_bot = get_exitbot()
    account = alpaca.get_account()
    equity = float(account.equity)
    
    print(f"\n[6] Running ExitBot with equity=${equity:.2f}...")
    result = exit_bot.run(equity=equity, day_start_equity=equity)
    
    print(f"\n[7] ExitBot Result:")
    print(f"  Positions monitored: {result.positions_monitored}")
    print(f"  Trailing stops active: {result.trailing_stops_active}")
    print(f"  Exits triggered: {result.exits_triggered}")
    print(f"  Errors: {result.errors}")
    
    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    main()
