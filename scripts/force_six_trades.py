#!/usr/bin/env python3
"""
Force 6 best gap candidates through TwentyMinuteBot bypassing gates.
Log all decisions for analysis.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.trading_hydra.services.alpaca_client import get_alpaca_client
from src.trading_hydra.services.exitbot import get_exitbot
from src.trading_hydra.core.logging import get_logger

logger = get_logger()

FORCE_SYMBOLS = [
    ("ASML", "buy", 24.40),    # Massive gap up - LONG
    ("AMD", "buy", 6.52),      # Strong gap up - LONG
    ("BA", "buy", 5.27),       # Strong gap up - LONG
    ("VIXY", "sell", -4.35),   # Gap down - SHORT
    ("KRE", "buy", 3.49),      # Gap up - LONG
    ("PLTR", "sell", -3.36),   # Gap down - SHORT
]

def get_quote(alpaca, symbol):
    """Get current quote for symbol"""
    try:
        quote = alpaca.get_latest_quote(symbol, asset_class="stock")
        if quote and 'bid' in quote and 'ask' in quote:
            bid = float(quote['bid'])
            ask = float(quote['ask'])
            if bid > 0 and ask > 0:
                return (bid + ask) / 2
            elif ask > 0:
                return ask
        elif quote and 'price' in quote:
            return float(quote['price'])
    except Exception as e:
        logger.log("quote_error", {"symbol": symbol, "error": str(e)})
    return None

def force_trade(alpaca, symbol, side, gap_pct, budget=50.0):
    """Force a trade through and log the decision"""
    
    price = get_quote(alpaca, symbol)
    if not price:
        logger.log("force_trade_skip", {"symbol": symbol, "reason": "no_quote"})
        print(f"  ❌ {symbol}: No quote available")
        return None
    
    # Calculate position size
    shares = int(budget / price)
    if shares < 1:
        shares = 1
    
    notional = shares * price
    
    # Log the decision
    logger.log("force_trade_decision", {
        "symbol": symbol,
        "side": side,
        "gap_pct": gap_pct,
        "price": price,
        "shares": shares,
        "notional": notional,
        "reason": "forced_bypass_gates",
        "ml_score": "bypassed",
        "pattern": "gap_momentum"
    })
    
    print(f"\n  📊 {symbol} Decision:")
    print(f"     Side: {side.upper()}")
    print(f"     Gap: {gap_pct:+.2f}%")
    print(f"     Price: ${price:.2f}")
    print(f"     Shares: {shares}")
    print(f"     Notional: ${notional:.2f}")
    
    # Place the order
    try:
        order = alpaca.place_market_order(
            symbol=symbol,
            side=side,
            qty=shares
        )
        
        if order and isinstance(order, dict):
            order_id = order.get('id', 'unknown')
            status = order.get('status', 'unknown')
            
            logger.log("force_trade_executed", {
                "symbol": symbol,
                "side": side,
                "order_id": order_id,
                "status": status,
                "shares": shares,
                "price": price
            })
            
            print(f"     ✅ Order: {order_id[:8]}... ({status})")
            return order
        else:
            logger.log("force_trade_failed", {"symbol": symbol, "response": str(order)})
            print(f"     ❌ Order failed: {order}")
            return None
            
    except Exception as e:
        logger.log("force_trade_error", {"symbol": symbol, "error": str(e)})
        print(f"     ❌ Error: {e}")
        return None

def main():
    print("=" * 60)
    print("FORCING 6 BEST GAP TRADES")
    print("=" * 60)
    
    alpaca = get_alpaca_client()
    
    # Get account info
    account = alpaca.get_account()
    equity = float(account.equity)
    print(f"\nAccount equity: ${equity:.2f}")
    
    # Log session start
    logger.log("force_trades_session_start", {
        "symbols": [s[0] for s in FORCE_SYMBOLS],
        "equity": equity,
        "budget_per_trade": 50.0
    })
    
    # Force each trade
    print("\n--- Forcing Trades ---")
    orders = []
    for symbol, side, gap in FORCE_SYMBOLS:
        order = force_trade(alpaca, symbol, side, gap, budget=50.0)
        if order:
            orders.append((symbol, order))
    
    print(f"\n--- Placed {len(orders)} orders ---")
    
    # Wait for fills
    import time
    print("\nWaiting 3s for fills...")
    time.sleep(3)
    
    # Check positions
    print("\n--- Current Positions ---")
    positions = alpaca.get_positions()
    
    for p in positions:
        entry = float(p.avg_entry_price)
        current = float(p.current_price)
        pnl_pct = ((current - entry) / entry) * 100 if entry > 0 else 0
        qty = float(p.qty)
        
        logger.log("position_status", {
            "symbol": p.symbol,
            "qty": qty,
            "entry": entry,
            "current": current,
            "pnl_pct": pnl_pct,
            "market_value": float(p.market_value)
        })
        
        print(f"  {p.symbol}: {qty:.4f} @ ${entry:.2f} → ${current:.2f} ({pnl_pct:+.2f}%)")
    
    # Run ExitBot
    print("\n--- Running ExitBot ---")
    exit_bot = get_exitbot()
    result = exit_bot.run(equity=equity, day_start_equity=equity)
    
    logger.log("exitbot_summary", {
        "positions_monitored": result.positions_monitored,
        "trailing_stops_active": result.trailing_stops_active,
        "exits_triggered": result.exits_triggered
    })
    
    print(f"\n  Positions monitored: {result.positions_monitored}")
    print(f"  Trailing stops active: {result.trailing_stops_active}")
    print(f"  Exits triggered: {result.exits_triggered}")
    
    print("\n" + "=" * 60)
    print("FORCE TRADES COMPLETE - CHECK LOGS")
    print("=" * 60)
    print("\nLog file: logs/app.jsonl")

if __name__ == "__main__":
    main()
