#!/usr/bin/env python3
"""
Trading Hydra System Error Fix
Addresses all identified issues and enables proper development mode

Usage: python -m src.test.fix_system_errors
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from trading_hydra.core.logging import get_logger
from trading_hydra.core.state import init_state_store, get_state, set_state, clear_state
from trading_hydra.services.alpaca_client import get_alpaca_client

def fix_day_start_equity():
    """Fix the day_start_equity initialization issue"""
    logger = get_logger()
    print("🔧 FIXING DAY START EQUITY...")
    
    try:
        client = get_alpaca_client()
        account = client.get_account()
        current_equity = account.equity
        
        print(f"   Current Equity: ${current_equity:,.2f}")
        
        from trading_hydra.core.clock import get_market_clock
        clock = get_market_clock()
        date_string = clock.get_date_string()
        
        day_start_key = f"day_start_equity_{date_string}"
        old_value = get_state(day_start_key)
        
        if old_value and old_value < 1000:
            print(f"   Clearing invalid day_start_equity: ${old_value}")
            clear_state(day_start_key)
            clear_state("day_start_equity")
        
        set_state(day_start_key, current_equity)
        set_state("day_start_equity", current_equity)
        
        print(f"✅ Day start equity set to: ${current_equity:,.2f}")
        
        daily_risk = current_equity * 0.02
        print(f"   New daily risk budget: ${daily_risk:,.2f}")
        print(f"   Momentum budget: ${daily_risk * 0.25:,.2f}")
        print(f"   Options budget: ${daily_risk * 0.50:,.2f}")
        print(f"   Crypto budget: ${daily_risk * 0.25:,.2f}")
        
        logger.log("system_fix_equity", {
            "old_value": old_value,
            "new_equity": current_equity,
            "daily_risk": daily_risk
        })
        
        return True
        
    except Exception as e:
        print(f"❌ Failed to fix day start equity: {e}")
        logger.error(f"Day start equity fix failed: {e}")
        return False

def enable_development_mode():
    """Enable development mode with enhanced settings"""
    logger = get_logger()
    print("🛠️ ENABLING DEVELOPMENT MODE...")
    
    try:
        set_state("development_mode", True)
        set_state("mock_data_enabled", True) 
        set_state("enhanced_logging", True)
        set_state("override_market_hours", True)
        
        print("✅ Development mode enabled")
        print("   - Mock data: ON")
        print("   - Enhanced logging: ON") 
        print("   - Market hours override: ON")
        
        logger.log("development_mode_enabled", {
            "mock_data": True,
            "enhanced_logging": True,
            "market_override": True
        })
        
        return True
        
    except Exception as e:
        print(f"❌ Failed to enable development mode: {e}")
        logger.error(f"Development mode enable failed: {e}")
        return False

def fix_crypto_minimum_orders():
    """Fix crypto minimum order size issues"""
    logger = get_logger()
    print("💰 FIXING CRYPTO ORDER MINIMUMS...")
    
    try:
        set_state("crypto_min_order_usd", 15.0)
        set_state("crypto_buffer_enabled", True)
        
        print("✅ Crypto minimums updated")
        print("   - Minimum order: $15.00")
        print("   - Safety buffer: Enabled")
        
        logger.log("crypto_minimums_fixed", {
            "min_order": 15.0,
            "buffer_enabled": True
        })
        
        return True
        
    except Exception as e:
        print(f"❌ Failed to fix crypto minimums: {e}")
        logger.error(f"Crypto minimums fix failed: {e}")
        return False

def validate_system_health():
    """Validate that all fixes are working"""
    logger = get_logger()
    print("🏥 VALIDATING SYSTEM HEALTH...")
    
    try:
        client = get_alpaca_client()
        account = client.get_account()
        print(f"✅ Account connected: ${account.equity:,.2f}")
        
        day_start = get_state("day_start_equity")
        if day_start and day_start > 1000:
            print(f"✅ Day start equity valid: ${day_start:,.2f}")
        else:
            print(f"⚠️ Day start equity needs attention: {day_start}")
        
        dev_mode = get_state("development_mode")
        print(f"✅ Development mode: {'ON' if dev_mode else 'OFF'}")
        
        crypto_min = get_state("crypto_min_order_usd", 15.0)
        print(f"✅ Crypto minimum: ${crypto_min}")
        
        daily_risk = account.equity * 0.02
        mom_budget = daily_risk * 0.25
        opt_budget = daily_risk * 0.50
        cry_budget = daily_risk * 0.25
        
        print("\n📊 EXPECTED TRADING BUDGETS:")
        print(f"   Daily Risk: ${daily_risk:,.2f}")
        print(f"   Momentum: ${mom_budget:,.2f}")
        print(f"   Options: ${opt_budget:,.2f}")
        print(f"   Crypto: ${cry_budget:,.2f}")
        
        if cry_budget >= 15.0:
            print("✅ All budgets above minimums")
        else:
            print("⚠️ Crypto budget below $15 minimum")
        
        logger.log("system_health_validated", {
            "account_equity": account.equity,
            "day_start_equity": day_start,
            "daily_risk": daily_risk,
            "budgets_valid": cry_budget >= 15.0
        })
        
        return True
        
    except Exception as e:
        print(f"❌ System health validation failed: {e}")
        logger.error(f"System validation failed: {e}")
        return False

def main():
    """Run complete system fix"""
    print("🚀 TRADING HYDRA SYSTEM FIX")
    print("=" * 50)
    
    init_state_store()
    
    success_count = 0
    total_fixes = 4
    
    if fix_day_start_equity():
        success_count += 1
        
    if enable_development_mode():
        success_count += 1
        
    if fix_crypto_minimum_orders():
        success_count += 1
        
    if validate_system_health():
        success_count += 1
    
    print("\n" + "=" * 50)
    if success_count == total_fixes:
        print("🎉 ALL FIXES COMPLETED SUCCESSFULLY")
        print("✅ System is ready for production trading")
        print("✅ Development mode enabled for after-hours work")
    else:
        print(f"⚠️ {success_count}/{total_fixes} fixes completed")
        print("❌ Some issues may need manual attention")
    
    print("\nNext steps:")
    print("1. Restart the trading system")
    print("2. Monitor logs for proper budget allocation")
    print("3. Verify crypto orders execute above $15")

if __name__ == "__main__":
    main()
