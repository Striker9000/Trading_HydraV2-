#!/usr/bin/env python3
"""
Trading Hydra Signal Diagnostics
================================
Manually checks current market conditions and signal generation
to diagnose why options/twenty-minute trades aren't triggering.
"""

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.trading_hydra.core.clock import get_market_clock
from src.trading_hydra.core.config import load_settings, load_bots_config
from src.trading_hydra.services.alpaca_client import AlpacaClient

PST = ZoneInfo("America/Los_Angeles")

def print_header(title: str):
    print(f"\n{'='*60}")
    print(f" {title}")
    print(f"{'='*60}")

def print_section(title: str):
    print(f"\n--- {title} ---")

def check_alpaca_connection():
    """Test Alpaca API connectivity and account status."""
    print_header("ALPACA API CONNECTION TEST")
    
    try:
        client = AlpacaClient()
        
        if not client.has_credentials():
            print(f"  Status: NO CREDENTIALS")
            print(f"  Error: ALPACA_KEY or ALPACA_SECRET not set")
            return False, None
        
        account = client.get_account()
        
        print(f"  Status: CONNECTED")
        print(f"  Paper Trading: {client.is_paper}")
        print(f"  Equity: ${account.equity:,.2f}")
        print(f"  Buying Power: ${account.buying_power:,.2f}")
        print(f"  Cash: ${account.cash:,.2f}")
        print(f"  Account Status: {account.status}")
        
        return True, client
    except Exception as e:
        print(f"  Status: FAILED")
        print(f"  Error: {e}")
        import traceback
        traceback.print_exc()
        return False, None

def check_market_hours():
    """Check current market hours status."""
    print_header("MARKET HOURS CHECK")
    
    clock = get_market_clock()
    now = clock.now()
    
    print(f"  Current Time (PST): {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"  Day of Week: {now.strftime('%A')}")
    print(f"  Is Weekend: {clock.is_weekend()}")
    print(f"  Is Market Hours: {clock.is_market_hours()}")
    print(f"  Is Extended Hours: {clock.is_extended_hours()}")
    print(f"  Market Open (PST): {clock.get_market_open()}")
    print(f"  Market Close (PST): {clock.get_market_close()}")
    
    # Check TwentyMinuteBot window
    settings = load_settings()
    twentymin_config = settings.get("twentyminute_bot", {})
    session = twentymin_config.get("session", {})
    trade_start = session.get("trade_start", "06:30")
    trade_end = session.get("trade_end", "06:50")
    
    print_section("TwentyMinuteBot Session Window")
    print(f"  Configured Window: {trade_start} - {trade_end} PST")
    
    current_time = now.time()
    start_parts = trade_start.split(":")
    end_parts = trade_end.split(":")
    from datetime import time
    start_time = time(int(start_parts[0]), int(start_parts[1]))
    end_time = time(int(end_parts[0]), int(end_parts[1]))
    
    in_window = start_time <= current_time <= end_time
    print(f"  Currently In Window: {in_window}")
    
    if not in_window:
        if current_time < start_time:
            delta = datetime.combine(datetime.today(), start_time) - datetime.combine(datetime.today(), current_time)
            print(f"  Window Opens In: {delta}")
        else:
            print(f"  Window Already Closed Today")
    
    return clock.is_market_hours() or clock.is_extended_hours()

def check_positions(client: AlpacaClient):
    """Check current positions."""
    print_header("CURRENT POSITIONS")
    
    try:
        positions = client.get_positions()
        
        if not positions:
            print("  No open positions")
            return
        
        for pos in positions:
            print(f"  {pos.symbol}: {pos.qty} @ ${pos.avg_entry_price:.2f} (P/L: ${pos.unrealized_pl:.2f})")
    except Exception as e:
        print(f"  Error getting positions: {e}")

def check_orders(client: AlpacaClient):
    """Check open orders."""
    print_header("OPEN ORDERS")
    
    try:
        orders = client.get_open_orders()
        
        if not orders:
            print("  No open orders")
            return
        
        for order in orders:
            symbol = order.get('symbol', 'N/A')
            side = order.get('side', 'N/A')
            qty = order.get('qty', 0)
            order_type = order.get('type', 'N/A')
            status = order.get('status', 'N/A')
            
            print(f"  {symbol}: {side} {qty} ({order_type}) - Status: {status}")
    except Exception as e:
        print(f"  Error getting orders: {e}")

def check_bot_configs():
    """Check bot configurations."""
    print_header("BOT CONFIGURATIONS")
    
    # Use load_bots_config for bot settings, not load_settings
    config = load_bots_config()
    
    # OptionsBot
    options = config.get("optionsbot", {})
    print_section("OptionsBot")
    print(f"  Enabled: {options.get('enabled', False)}")
    tickers = options.get('tickers', [])
    print(f"  Tickers: {tickers[:5] if len(tickers) > 5 else tickers}{'...' if len(tickers) > 5 else ''}")
    print(f"  Max Trades/Day: {options.get('risk', {}).get('max_trades_per_day', 'N/A')}")
    print(f"  Trade Window: {options.get('session', {}).get('trade_start', 'N/A')} - {options.get('session', {}).get('trade_end', 'N/A')} PST")
    print(f"  Strategy System: {options.get('use_strategy_system', False)}")
    
    # TwentyMinuteBot
    twentymin = config.get("twentyminute_bot", {})
    print_section("TwentyMinuteBot")
    print(f"  Enabled: {twentymin.get('enabled', False)}")
    tickers20 = twentymin.get('tickers', [])
    print(f"  Tickers: {len(tickers20)} symbols")
    print(f"  Session Window: {twentymin.get('session', {}).get('trade_start', 'N/A')} - {twentymin.get('session', {}).get('trade_end', 'N/A')} PST")
    print(f"  Min Gap %: {twentymin.get('gap', {}).get('min_gap_pct', 'N/A')}")
    print(f"  Use Options: {twentymin.get('execution', {}).get('use_options', False)}")

def check_options_chain(client: AlpacaClient, tickers: list):
    """Check options chain availability."""
    print_header("OPTIONS CHAIN CHECK")
    
    settings = load_settings()
    options_config = settings.get("optionsbot", {})
    chain_rules = options_config.get("chain_rules", {})
    dte_min = chain_rules.get("dte_min", 7)
    dte_max = chain_rules.get("dte_max", 45)
    
    print(f"  DTE Range: {dte_min} - {dte_max} days")
    print()
    
    for ticker in tickers[:2]:  # Limit to 2 for speed
        try:
            exp_date = (datetime.now() + timedelta(days=dte_min)).strftime("%Y-%m-%d")
            chain = client.get_options_chain(ticker, expiration_date_gte=exp_date)
            
            if chain:
                print(f"  {ticker}: Found {len(chain)} option contracts")
                # Show first few expirations
                expirations = sorted(set(c.get('expiration_date', '') for c in chain if c.get('expiration_date')))[:3]
                print(f"         Next expirations: {expirations}")
            else:
                print(f"  {ticker}: No options chain returned")
        except Exception as e:
            print(f"  {ticker}: Error - {e}")

def main():
    print("\n" + "="*60)
    print(" TRADING HYDRA - SIGNAL DIAGNOSTICS")
    print(" " + datetime.now(PST).strftime("%Y-%m-%d %H:%M:%S PST"))
    print("="*60)
    
    # Step 1: Check Alpaca connection
    connected, client = check_alpaca_connection()
    if not connected:
        print("\n FATAL: Cannot proceed without Alpaca connection!")
        return 1
    
    # Step 2: Check market hours
    market_open = check_market_hours()
    
    # Step 3: Check bot configs
    check_bot_configs()
    
    # Step 4: Check current positions and orders
    check_positions(client)
    check_orders(client)
    
    # Step 5: Check options chains
    check_options_chain(client, ["SPY", "QQQ"])
    
    print_header("DIAGNOSTIC SUMMARY")
    if not market_open:
        print("  NOTE: Market is currently closed. Signals won't trigger until market opens.")
        print(f"        Regular session: 6:30 AM - 1:00 PM PST")
        print(f"        TwentyMinuteBot window: 6:30 AM - 6:50 AM PST (first 20 min only)")
    else:
        print("  Market is OPEN. Check logs for signal generation activity.")
    
    print("\n" + "="*60)
    print(" End of Diagnostics")
    print("="*60 + "\n")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
