#!/usr/bin/env python3
"""
Run trades through ALL bots, logging every decision, block, and error.
Then run ExitBot to log its decisions.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.trading_hydra.services.alpaca_client import get_alpaca_client
from src.trading_hydra.services.exitbot import get_exitbot
from src.trading_hydra.bots.options_bot import OptionsBot
from src.trading_hydra.bots.twenty_minute_bot import TwentyMinuteBot
from src.trading_hydra.bots.crypto_bot import CryptoBot
from src.trading_hydra.core.logging import get_logger

logger = get_logger()

def run_options_bot():
    """Run OptionsBot (opt_core) and log all decisions"""
    print("\n" + "=" * 60)
    print("OPTIONSBOT (opt_core) - Swing Options")
    print("=" * 60)
    
    logger.log("test_bot_start", {"bot": "opt_core", "type": "options"})
    
    try:
        bot = OptionsBot(bot_id="opt_core")
        print(f"Tickers: {bot.tickers}")
        print(f"Is 0DTE: {bot._is_0dte}")
        
        result = bot.execute(max_daily_loss=500, halt_new_trades=False)
        
        logger.log("test_bot_result", {
            "bot": "opt_core",
            "trades_attempted": result.get("trades_attempted", 0),
            "positions_managed": result.get("positions_managed", 0),
            "strategies_analyzed": result.get("strategies_analyzed", 0),
            "errors": result.get("errors", [])
        })
        
        print(f"\nResult:")
        print(f"  Trades attempted: {result.get('trades_attempted', 0)}")
        print(f"  Positions managed: {result.get('positions_managed', 0)}")
        print(f"  Errors: {result.get('errors', [])}")
        
        return result
        
    except Exception as e:
        logger.log("test_bot_error", {"bot": "opt_core", "error": str(e)})
        print(f"ERROR: {e}")
        return {"error": str(e)}

def run_options_bot_0dte():
    """Run OptionsBot 0DTE and log all decisions"""
    print("\n" + "=" * 60)
    print("OPTIONSBOT 0DTE (opt_0dte) - Same-Day Expiration")
    print("=" * 60)
    
    logger.log("test_bot_start", {"bot": "opt_0dte", "type": "options_0dte"})
    
    try:
        bot = OptionsBot(bot_id="opt_0dte")
        print(f"Tickers: {bot.tickers}")
        print(f"Is 0DTE: {bot._is_0dte}")
        
        result = bot.execute(max_daily_loss=300, halt_new_trades=False)
        
        logger.log("test_bot_result", {
            "bot": "opt_0dte",
            "trades_attempted": result.get("trades_attempted", 0),
            "positions_managed": result.get("positions_managed", 0),
            "errors": result.get("errors", [])
        })
        
        print(f"\nResult:")
        print(f"  Trades attempted: {result.get('trades_attempted', 0)}")
        print(f"  Positions managed: {result.get('positions_managed', 0)}")
        print(f"  Errors: {result.get('errors', [])}")
        
        return result
        
    except Exception as e:
        logger.log("test_bot_error", {"bot": "opt_0dte", "error": str(e)})
        print(f"ERROR: {e}")
        return {"error": str(e)}

def run_twentymin_bot():
    """Run TwentyMinuteBot and log all decisions"""
    print("\n" + "=" * 60)
    print("TWENTYMINUTEBOT (twentymin_core) - Gap Momentum")
    print("=" * 60)
    
    logger.log("test_bot_start", {"bot": "twentymin_core", "type": "twentymin"})
    
    try:
        bot = TwentyMinuteBot()
        print(f"Tickers: {len(bot._config.tickers) if bot._config else 0} symbols")
        print(f"Session: {bot._config.session_start if bot._config else 'N/A'} - {bot._config.session_end if bot._config else 'N/A'}")
        print(f"In session: {bot._is_in_session()}")
        
        result = bot.execute(budget=500, halt_new_trades=False)
        
        logger.log("test_bot_result", {
            "bot": "twentymin_core",
            "trades_attempted": result.get("trades_attempted", 0),
            "gaps_analyzed": result.get("gaps_analyzed", 0),
            "patterns_detected": result.get("patterns_detected", 0),
            "positions_managed": result.get("positions_managed", 0),
            "errors": result.get("errors", [])
        })
        
        print(f"\nResult:")
        print(f"  Trades attempted: {result.get('trades_attempted', 0)}")
        print(f"  Gaps analyzed: {result.get('gaps_analyzed', 0)}")
        print(f"  Patterns detected: {result.get('patterns_detected', 0)}")
        print(f"  Errors: {result.get('errors', [])}")
        
        return result
        
    except Exception as e:
        logger.log("test_bot_error", {"bot": "twentymin_core", "error": str(e)})
        print(f"ERROR: {e}")
        return {"error": str(e)}

def run_crypto_bot():
    """Run CryptoBot and log all decisions"""
    print("\n" + "=" * 60)
    print("CRYPTOBOT (crypto_core) - 24/7 Crypto Trading")
    print("=" * 60)
    
    logger.log("test_bot_start", {"bot": "crypto_core", "type": "crypto"})
    
    try:
        bot = CryptoBot()
        print(f"Bot ID: {bot.bot_id}")
        
        result = bot.execute(max_daily_loss=500)
        
        logger.log("test_bot_result", {
            "bot": "crypto_core",
            "result": str(result)[:500]
        })
        
        print(f"\nResult: {result}")
        
        return result
        
    except Exception as e:
        logger.log("test_bot_error", {"bot": "crypto_core", "error": str(e)})
        print(f"ERROR: {e}")
        return {"error": str(e)}

def run_exitbot():
    """Run ExitBot and log all position decisions"""
    print("\n" + "=" * 60)
    print("EXITBOT - Position Monitoring & Trailing Stops")
    print("=" * 60)
    
    logger.log("test_bot_start", {"bot": "exitbot", "type": "exit_management"})
    
    try:
        alpaca = get_alpaca_client()
        account = alpaca.get_account()
        equity = float(account.equity)
        
        print(f"Account equity: ${equity:.2f}")
        
        exit_bot = get_exitbot()
        result = exit_bot.run(equity=equity, day_start_equity=equity)
        
        logger.log("test_exitbot_result", {
            "positions_monitored": result.positions_monitored,
            "trailing_stops_active": result.trailing_stops_active,
            "exits_triggered": result.exits_triggered
        })
        
        print(f"\nResult:")
        print(f"  Positions monitored: {result.positions_monitored}")
        print(f"  Trailing stops active: {result.trailing_stops_active}")
        print(f"  Exits triggered: {result.exits_triggered}")
        
        return result
        
    except Exception as e:
        logger.log("test_bot_error", {"bot": "exitbot", "error": str(e)})
        print(f"ERROR: {e}")
        return {"error": str(e)}

def main():
    print("=" * 60)
    print("COMPREHENSIVE BOT TEST - ALL BOTS")
    print("=" * 60)
    
    alpaca = get_alpaca_client()
    account = alpaca.get_account()
    print(f"\nAccount: ${float(account.equity):.2f} equity")
    
    positions = alpaca.get_positions()
    print(f"Current positions: {len(positions)}")
    
    logger.log("test_session_start", {
        "equity": float(account.equity),
        "positions": len(positions),
        "bots_to_test": ["opt_core", "opt_0dte", "twentymin_core", "crypto_core", "exitbot"]
    })
    
    # Run each bot
    results = {}
    
    results["opt_core"] = run_options_bot()
    results["opt_0dte"] = run_options_bot_0dte()
    results["twentymin_core"] = run_twentymin_bot()
    results["crypto_core"] = run_crypto_bot()
    results["exitbot"] = run_exitbot()
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST COMPLETE - SUMMARY")
    print("=" * 60)
    
    logger.log("test_session_complete", {"results": str(results)[:1000]})
    
    print("\nCheck logs/app.jsonl for detailed analysis")
    print("Look for events: strategy_system_skipped, twentymin_ml_skip, exitbot_position_status")

if __name__ == "__main__":
    main()
