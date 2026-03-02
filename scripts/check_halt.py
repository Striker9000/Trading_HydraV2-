#!/usr/bin/env python3
"""
Check and Clear Halt State
==========================
Use this script to diagnose and fix halt issues.

Usage:
    python3 scripts/check_halt.py          # Check halt status
    python3 scripts/check_halt.py --clear  # Clear all halts
    python3 scripts/check_halt.py --reset  # Full state reset
"""

import os
import sys
import argparse

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

def main():
    parser = argparse.ArgumentParser(description='Check and manage halt state')
    parser.add_argument('--clear', action='store_true', help='Clear all halts and health failures')
    parser.add_argument('--reset', action='store_true', help='Full state reset (clears everything)')
    args = parser.parse_args()
    
    print("=" * 60)
    print("  TRADING HYDRA - HALT STATUS CHECK")
    print("=" * 60)
    print()
    
    # Check if database exists
    db_path = "trading_state.db"
    if not os.path.exists(db_path):
        print(f"[INFO] No state database found ({db_path})")
        print("       This is normal for a fresh install.")
        print("       The database will be created on first run.")
        return 0
    
    print(f"[INFO] Found state database: {db_path}")
    print()
    
    # Import state functions
    from trading_hydra.core.state import get_state, set_state, delete_state, get_all_states
    from trading_hydra.core.halt import get_halt_manager
    from trading_hydra.core.health import get_health_monitor
    
    # Check halt status
    halt_mgr = get_halt_manager()
    status = halt_mgr.get_status()
    
    print("[HALT STATUS]")
    print(f"  Active: {status.active}")
    if status.reason:
        print(f"  Reason: {status.reason}")
    if status.expires_at:
        print(f"  Expires: {status.expires_at}")
    print()
    
    # Check GLOBAL_TRADING_HALT directly
    global_halt = get_state("GLOBAL_TRADING_HALT", False)
    print(f"[GLOBAL_TRADING_HALT] = {global_halt}")
    print()
    
    # Check health status (this is often the hidden cause!)
    print("[HEALTH STATUS]")
    health = get_health_monitor()
    snapshot = health.get_snapshot()
    print(f"  OK: {snapshot.ok}")
    print(f"  Reason: {snapshot.reason}")
    print(f"  API Failures: {snapshot.api_failures}")
    print(f"  Connection Failures: {snapshot.connection_failures}")
    print(f"  Critical Auth Failure: {snapshot.critical_auth_failure}")
    if snapshot.last_price_tick:
        print(f"  Last Price Tick: {snapshot.last_price_tick}")
        print(f"  Stale Seconds: {snapshot.stale_seconds:.0f}")
    print()
    
    # If clear requested
    if args.clear:
        print("[ACTION] Clearing halt and health state...")
        
        # Clear halt
        halt_mgr.clear_halt()
        delete_state("GLOBAL_TRADING_HALT")
        delete_state("halt.reason")
        delete_state("halt.halted_at")
        delete_state("halt.expires_at")
        print("  ✓ Halt state cleared")
        
        # Clear health failures
        set_state("health.api_failure_count", 0)
        set_state("health.connection_failure_count", 0)
        set_state("health.critical_auth_failure", False)
        delete_state("health.critical_auth_error")
        delete_state("health.critical_auth_timestamp")
        delete_state("health.last_api_failure")
        delete_state("health.last_connection_failure")
        print("  ✓ Health failures cleared")
        
        # Record a fresh price tick to reset staleness
        health.record_price_tick()
        print("  ✓ Price tick recorded")
        
        print()
        
        # Verify
        new_status = halt_mgr.get_status()
        new_snapshot = health.get_snapshot()
        print(f"[VERIFY]")
        print(f"  Halt Active: {new_status.active}")
        print(f"  Health OK: {new_snapshot.ok}")
        print()
        print("=" * 60)
        print("  ✓ ALL CLEARED - Trading should resume")
        print("=" * 60)
        return 0
    
    # If reset requested
    if args.reset:
        print("[ACTION] Full state reset...")
        
        # Delete database contents
        import sqlite3
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("DELETE FROM state")
            conn.commit()
            conn.close()
            print("  ✓ State table cleared")
        except Exception as e:
            print(f"  Warning: {e}")
        
        print("  ✓ Full reset complete")
        print()
        print("  The bot will start fresh on next run.")
        return 0
    
    # Show critical states that affect trading
    print("[CRITICAL STATES]")
    critical_keys = [
        "GLOBAL_TRADING_HALT",
        "halt.reason",
        "halt.halted_at",
        "halt.expires_at",
        "health.api_failure_count",
        "health.connection_failure_count",
        "health.critical_auth_failure",
        "health.critical_auth_error",
    ]
    for key in critical_keys:
        val = get_state(key)
        if val is not None:
            print(f"  {key}: {val}")
    print()
    
    # Determine if halted
    is_halted = status.active or global_halt or not snapshot.ok
    
    if is_halted:
        print("=" * 60)
        print("  ⚠ TRADING IS BLOCKED")
        print("=" * 60)
        print()
        
        # Show specific cause
        if snapshot.critical_auth_failure:
            print("  CAUSE: Critical authentication failure (401/403)")
            print("         Your API keys may not match your endpoint.")
            print("         Paper keys only work with ALPACA_PAPER=true")
            print()
        elif not snapshot.ok:
            print(f"  CAUSE: Health check failed - {snapshot.reason}")
            print()
        elif status.active or global_halt:
            print(f"  CAUSE: Trading halt - {status.reason or 'unknown'}")
            print()
        
        print("  To clear all blocks, run:")
        print("    python3 scripts/check_halt.py --clear")
        print()
        print("  For a full reset (clears all state):")
        print("    python3 scripts/check_halt.py --reset")
        return 1
    else:
        print("=" * 60)
        print("  ✓ NO BLOCKS ACTIVE - Trading is enabled")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
