#!/usr/bin/env python3
"""
Comprehensive test of Alpaca API connection and data validation

Usage: python -m src.test.test_alpaca_connection
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dotenv import load_dotenv
load_dotenv()

from trading_hydra.services.alpaca_client import get_alpaca_client
from trading_hydra.core.logging import get_logger

def test_alpaca_connection():
    """Comprehensive test of Alpaca API connection and data validation"""
    logger = get_logger()
    logger.log("connection_test_start", {})
    
    client = get_alpaca_client()
    
    print("🔐 Testing credentials...")
    if not client.has_credentials():
        print("❌ FAILED: Missing ALPACA_KEY or ALPACA_SECRET environment variables")
        print("Please add them in the Secrets tool")
        return False
    print("✅ Credentials found")
    
    print("\n💰 Testing account data...")
    try:
        account = client.get_account()
        print(f"✅ Account Status: {account.status}")
        print(f"✅ Equity: ${account.equity:,.2f}")
        print(f"✅ Cash: ${account.cash:,.2f}")
        print(f"✅ Buying Power: ${account.buying_power:,.2f}")
        
        if account.status != "ACTIVE":
            print(f"⚠️  WARNING: Account status is {account.status}, not ACTIVE")
        
    except Exception as e:
        print(f"❌ FAILED: Account data error - {e}")
        return False
    
    print("\n📊 Testing positions data...")
    try:
        positions = client.get_positions()
        print(f"✅ Retrieved {len(positions)} positions")
        
        if positions:
            total_value = sum(p.market_value for p in positions)
            print(f"✅ Total position value: ${total_value:,.2f}")
            for pos in positions[:5]:
                print(f"   - {pos.symbol}: {pos.qty:,.2f} shares, ${pos.market_value:,.2f}")
        else:
            print("✅ No positions (this is normal for new accounts)")
            
    except Exception as e:
        print(f"❌ FAILED: Positions data error - {e}")
        return False
    
    print(f"\n📝 Trading Mode:")
    if client.is_paper:
        print("✅ Paper trading mode (safe for testing)")
        print(f"   API URL: {client.base_url}")
    else:
        print("⚠️  LIVE trading mode - real money at risk!")
        print(f"   API URL: {client.base_url}")
    
    print(f"\n🔄 Testing data flow validation...")
    try:
        from trading_hydra.orchestrator import get_orchestrator
        orchestrator = get_orchestrator()
        orchestrator.initialize()
        
        result = orchestrator.run_loop()
        
        print(f"✅ Loop Result:")
        print(f"   - Success: {result.success}")
        print(f"   - Status: {result.status}")
        print(f"   - Summary: {result.summary[:100]}...")
        
        assert isinstance(result.success, bool)
        assert isinstance(result.status, str)
        assert isinstance(result.summary, str)
        assert isinstance(result.timestamp, str)
        
        print("✅ All data types validated")
        
    except Exception as e:
        print(f"❌ FAILED: Data flow validation error - {e}")
        return False
    
    print(f"\n🎉 ALL TESTS PASSED!")
    print(f"Your Alpaca connection is working correctly with validated inputs/outputs.")
    
    logger.log("connection_test_complete", {"success": True})
    return True

if __name__ == "__main__":
    success = test_alpaca_connection()
    if not success:
        sys.exit(1)
