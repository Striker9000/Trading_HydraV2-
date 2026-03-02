#!/usr/bin/env python3
"""
API Diagnostics Script
======================
Run this script to diagnose Alpaca API connection issues.

Usage:
    python3 scripts/diagnose_api.py
"""

import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

def main():
    print("=" * 60)
    print("  TRADING HYDRA - API DIAGNOSTICS")
    print("=" * 60)
    print()
    
    # Step 1: Check .env file
    print("[1] Checking .env file...")
    env_file = Path(".env")
    if env_file.exists():
        print(f"    ✓ Found: {env_file.absolute()}")
        # Load it
        from dotenv import load_dotenv
        load_dotenv(env_file, override=True)
    else:
        print("    ⚠ No .env file found in current directory")
        print("    → Copy .env.example to .env and add your keys")
    print()
    
    # Step 2: Check environment variables
    print("[2] Checking credentials...")
    key = os.environ.get("ALPACA_KEY", "")
    secret = os.environ.get("ALPACA_SECRET", "")
    paper = os.environ.get("ALPACA_PAPER", "true")
    
    print(f"    ALPACA_KEY:    {'SET (' + str(len(key)) + ' chars)' if key else '❌ NOT SET'}")
    print(f"    ALPACA_SECRET: {'SET (' + str(len(secret)) + ' chars)' if secret else '❌ NOT SET'}")
    print(f"    ALPACA_PAPER:  {paper}")
    
    if not key or not secret:
        print()
        print("    ❌ FATAL: API keys not configured!")
        print("    → Edit .env and add your Alpaca API keys")
        return 1
    print()
    
    # Step 3: Determine endpoint
    print("[3] Checking endpoint...")
    is_paper = paper.lower() != "false"
    base_url = "https://paper-api.alpaca.markets" if is_paper else "https://api.alpaca.markets"
    print(f"    Mode: {'PAPER TRADING' if is_paper else 'LIVE TRADING'}")
    print(f"    Endpoint: {base_url}")
    print()
    
    # Step 4: Test API connection
    print("[4] Testing API connection...")
    import requests
    
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    }
    
    try:
        response = requests.get(f"{base_url}/v2/account", headers=headers, timeout=10)
        
        if response.status_code == 200:
            account = response.json()
            print(f"    ✓ SUCCESS! Connected to Alpaca")
            print()
            print(f"    Account ID: {account.get('account_number', 'N/A')}")
            print(f"    Equity:     ${float(account.get('equity', 0)):,.2f}")
            print(f"    Status:     {account.get('status', 'N/A')}")
            print()
            print("=" * 60)
            print("  ✓ API CONNECTION WORKING")
            print("=" * 60)
            return 0
            
        elif response.status_code == 401:
            print(f"    ❌ 401 UNAUTHORIZED")
            print()
            print("    This means Alpaca rejected your credentials.")
            print()
            print("    MOST COMMON CAUSE:")
            print("    → Your keys don't match your endpoint!")
            print()
            if is_paper:
                print("    You have ALPACA_PAPER=true (paper trading)")
                print("    But your keys might be LIVE trading keys.")
                print()
                print("    FIX: Get PAPER trading keys from:")
                print("    → https://paper.alpaca.markets (API Keys section)")
            else:
                print("    You have ALPACA_PAPER=false (live trading)")
                print("    But your keys might be PAPER trading keys.")
                print()
                print("    FIX: Get LIVE trading keys from:")
                print("    → https://alpaca.markets (API Keys section)")
            print()
            print("    Or if you want to use your current keys:")
            if is_paper:
                print("    → Set ALPACA_PAPER=false in .env (for live trading)")
            else:
                print("    → Set ALPACA_PAPER=true in .env (for paper trading)")
            return 1
            
        elif response.status_code == 403:
            print(f"    ❌ 403 FORBIDDEN")
            print("    Your account may be restricted. Check Alpaca dashboard.")
            return 1
            
        else:
            print(f"    ❌ HTTP {response.status_code}")
            print(f"    Response: {response.text[:200]}")
            return 1
            
    except requests.exceptions.Timeout:
        print("    ❌ Connection timed out")
        print("    Check your internet connection")
        return 1
    except requests.exceptions.ConnectionError as e:
        print(f"    ❌ Connection failed: {e}")
        print("    Check your internet connection and firewall")
        return 1
    except Exception as e:
        print(f"    ❌ Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
