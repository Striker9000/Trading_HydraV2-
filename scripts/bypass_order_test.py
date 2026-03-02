#!/usr/bin/env python3
"""
Bypass Order Test Machine
=========================

This script bypasses normal safety checks to execute the best suggested trade
from each bot, allowing ExitBot monitoring to be tested.

For each bot:
1. Get the bot's best trade suggestion (without executing)
2. Execute the trade directly via Alpaca
3. Register with ExitBot for monitoring

Usage:
    python scripts/bypass_order_test.py              # Run all bots
    python scripts/bypass_order_test.py --bot crypto # Run only CryptoBot
    python scripts/bypass_order_test.py --bot options # Run only OptionsBot
    python scripts/bypass_order_test.py --bot twentymin # Run only TwentyMinuteBot
    python scripts/bypass_order_test.py --list       # List available bots
"""

import sys
import os
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.trading_hydra.services.alpaca_client import get_alpaca_client
from src.trading_hydra.services.exitbot import get_exitbot
from src.trading_hydra.core.logging import get_logger
from src.trading_hydra.core.config import load_bots_config

logger = get_logger()
alpaca = get_alpaca_client()
exitbot = get_exitbot()


def get_crypto_suggestion():
    """Get CryptoBot's best trade suggestion without executing."""
    from src.trading_hydra.bots.crypto_bot import CryptoBot
    
    try:
        bot = CryptoBot()
        pairs = bot._config.pairs if bot._config else ["BTC/USD", "ETH/USD"]
        
        best_suggestion = None
        best_score = -1
        
        for pair in pairs:
            try:
                signal = bot._analyze_pair(pair)
                if signal and signal.get("action") in ["BUY", "SELL"]:
                    score = signal.get("strength", 0)
                    if score > best_score:
                        best_score = score
                        best_suggestion = {
                            "bot": "crypto_core",
                            "symbol": pair,
                            "side": "buy" if signal["action"] == "BUY" else "sell",
                            "qty": 0.001 if "BTC" in pair else 0.01,
                            "price": signal.get("price"),
                            "score": score,
                            "reason": signal.get("reason", "momentum signal"),
                            "indicators": signal.get("indicators", {})
                        }
            except Exception as e:
                logger.log("bypass_crypto_error", {"pair": pair, "error": str(e)})
        
        if not best_suggestion:
            return {
                "bot": "crypto_core",
                "symbol": "BTC/USD",
                "side": "buy",
                "qty": 0.001,
                "price": None,
                "score": 0,
                "reason": "No signal - using default BTC buy",
                "indicators": {}
            }
        
        return best_suggestion
        
    except Exception as e:
        logger.log("bypass_crypto_init_error", {"error": str(e)})
        return {
            "bot": "crypto_core",
            "symbol": "BTC/USD",
            "side": "buy",
            "qty": 0.001,
            "price": None,
            "score": 0,
            "reason": f"Bot init failed: {e} - using default",
            "indicators": {}
        }


def get_options_suggestion():
    """Get OptionsBot's best trade suggestion - finds a real options contract."""
    from datetime import datetime, timedelta
    
    try:
        tickers = ["SPY", "QQQ", "AAPL"]
        
        for ticker in tickers:
            try:
                # Get current price from bid/ask midpoint
                quote = alpaca.get_latest_quote(ticker, asset_class="stock")
                if not quote:
                    continue
                bid = float(quote.get("bid", 0))
                ask = float(quote.get("ask", 0))
                current_price = (bid + ask) / 2 if bid and ask else 0
                if current_price <= 0:
                    continue
                
                # Look for options expiring in 7-30 days
                today = datetime.now()
                exp_gte = (today + timedelta(days=7)).strftime("%Y-%m-%d")
                exp_lte = (today + timedelta(days=30)).strftime("%Y-%m-%d")
                
                # Get slightly OTM calls (strikes 1-5% above current)
                strike_gte = current_price * 1.01
                strike_lte = current_price * 1.05
                
                chain = alpaca.get_options_chain(
                    underlying_symbol=ticker,
                    expiration_date_gte=exp_gte,
                    expiration_date_lte=exp_lte,
                    strike_price_gte=strike_gte,
                    strike_price_lte=strike_lte,
                    option_type="call"
                )
                
                logger.log("bypass_options_chain_result", {
                    "ticker": ticker, 
                    "chain_size": len(chain) if chain else 0,
                    "current_price": current_price,
                    "strike_range": f"{strike_gte:.0f}-{strike_lte:.0f}"
                })
                
                if chain:
                    # Find cheapest contract (relaxed filters for testing)
                    valid_contracts = [
                        c for c in chain
                        if c.get("ask", 0) and c.get("ask", 0) < 10.0  # Under $1000 per contract
                    ]
                    
                    if not valid_contracts:
                        # If no valid contracts with filters, just take first one
                        valid_contracts = [c for c in chain if c.get("symbol")]
                    
                    if valid_contracts:
                        # Sort by ask price
                        valid_contracts.sort(key=lambda x: x.get("ask", 999) if x.get("ask") else 999)
                        contract = valid_contracts[0]
                        
                        return {
                            "bot": "opt_core",
                            "symbol": contract.get("symbol"),
                            "side": "buy",
                            "qty": 1,
                            "price": contract.get("ask"),
                            "score": 50,
                            "reason": f"OTM call on {ticker}: strike ${contract.get('strike', 0):.0f}, exp {contract.get('expiration', 'N/A')}",
                            "is_option": True,
                            "indicators": {
                                "underlying": ticker,
                                "strike": contract.get("strike"),
                                "expiration": contract.get("expiration"),
                                "delta": contract.get("delta"),
                                "iv": contract.get("implied_volatility"),
                                "bid": contract.get("bid"),
                                "ask": contract.get("ask")
                            }
                        }
                        
                logger.log("bypass_options_no_contracts", {"ticker": ticker, "chain_size": len(chain) if chain else 0})
                
            except Exception as e:
                logger.log("bypass_options_chain_error", {"ticker": ticker, "error": str(e)})
        
        # Fallback to stock if no options found
        return {
            "bot": "opt_core",
            "symbol": "SPY",
            "side": "buy",
            "qty": 1,
            "price": None,
            "score": 0,
            "reason": "No options contracts found - using default SPY stock",
            "use_stock": True,
            "indicators": {}
        }
        
    except Exception as e:
        logger.log("bypass_options_init_error", {"error": str(e)})
        return {
            "bot": "opt_core",
            "symbol": "SPY",
            "side": "buy",
            "qty": 1,
            "price": None,
            "score": 0,
            "reason": f"Options search failed: {e} - using default SPY stock",
            "use_stock": True,
            "indicators": {}
        }


def get_twentymin_suggestion():
    """Get TwentyMinuteBot's best trade suggestion without executing."""
    from src.trading_hydra.bots.twenty_minute_bot import TwentyMinuteBot
    
    try:
        bot = TwentyMinuteBot()
        tickers = bot._config.tickers if bot._config else ["SPY", "QQQ", "AAPL"]
        
        best_suggestion = None
        best_gap = 0
        
        for ticker in tickers[:10]:
            try:
                gap = bot._analyze_gap(ticker)
                if gap and gap.is_significant:
                    if abs(gap.gap_pct) > abs(best_gap):
                        best_gap = gap.gap_pct
                        best_suggestion = {
                            "bot": "twentymin_core",
                            "symbol": ticker,
                            "side": "buy" if gap.gap_pct > 0 else "sell",
                            "qty": 1,
                            "price": gap.current_price,
                            "score": abs(gap.gap_pct) * 10,
                            "reason": f"Gap: {gap.gap_pct:.2f}%",
                            "indicators": {
                                "gap_pct": gap.gap_pct,
                                "prev_close": gap.prev_close,
                                "current": gap.current_price
                            }
                        }
            except Exception as e:
                logger.log("bypass_twentymin_gap_error", {"ticker": ticker, "error": str(e)})
        
        if not best_suggestion:
            return {
                "bot": "twentymin_core",
                "symbol": "SPY",
                "side": "buy",
                "qty": 1,
                "price": None,
                "score": 0,
                "reason": "No gap signal - using default SPY",
                "indicators": {}
            }
        
        return best_suggestion
        
    except Exception as e:
        logger.log("bypass_twentymin_init_error", {"error": str(e)})
        return {
            "bot": "twentymin_core",
            "symbol": "SPY",
            "side": "buy",
            "qty": 1,
            "price": None,
            "score": 0,
            "reason": f"Bot init failed: {e} - using default",
            "indicators": {}
        }


def get_momentum_suggestion():
    """Get MomentumBot's best trade suggestion."""
    from src.trading_hydra.bots.momentum_bot import MomentumBot
    
    try:
        bot_configs = [
            ("mom_AAPL", "AAPL"),
            ("mom_TSLA", "TSLA"),
            ("mom_NVDA", "NVDA"),
            ("mom_AMD", "AMD"),
            ("mom_GOOGL", "GOOGL")
        ]
        best_suggestion = None
        best_score = 0
        
        for bot_id, ticker in bot_configs:
            try:
                bot = MomentumBot(bot_id=bot_id, ticker=ticker)
                signal = bot._analyze_trend()
                if signal and signal.get("action") in ["BUY", "SELL"]:
                    score = signal.get("strength", 0)
                    if score > best_score:
                        best_score = score
                        best_suggestion = {
                            "bot": bot_id,
                            "symbol": ticker,
                            "side": "buy" if signal["action"] == "BUY" else "sell",
                            "qty": 1,
                            "price": signal.get("price"),
                            "score": score,
                            "reason": signal.get("reason", "momentum signal"),
                            "indicators": signal.get("indicators", {})
                        }
            except Exception as e:
                logger.log("bypass_momentum_error", {"bot_id": bot_id, "error": str(e)})
        
        if not best_suggestion:
            return {
                "bot": "mom_AAPL",
                "symbol": "AAPL",
                "side": "buy",
                "qty": 1,
                "price": None,
                "score": 0,
                "reason": "No momentum signal - using default AAPL",
                "indicators": {}
            }
        
        return best_suggestion
        
    except Exception as e:
        return {
            "bot": "mom_AAPL",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 1,
            "price": None,
            "score": 0,
            "reason": f"Bot init failed: {e}",
            "indicators": {}
        }


def execute_suggestion(suggestion: dict) -> dict:
    """Execute a trade suggestion via Alpaca and register with ExitBot."""
    import uuid
    
    symbol = suggestion["symbol"]
    side = suggestion["side"]
    qty = suggestion["qty"]
    bot_id = suggestion["bot"]
    
    print(f"\n{'='*60}")
    print(f"EXECUTING: {bot_id}")
    print(f"{'='*60}")
    print(f"  Symbol: {symbol}")
    print(f"  Side:   {side}")
    print(f"  Qty:    {qty}")
    print(f"  Score:  {suggestion.get('score', 0)}")
    print(f"  Reason: {suggestion.get('reason', 'N/A')}")
    
    logger.log("bypass_execute_start", {
        "bot": bot_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "score": suggestion.get("score", 0),
        "reason": suggestion.get("reason", "")
    })
    
    try:
        is_crypto = "/" in symbol
        is_option = suggestion.get("is_option", False)
        
        # Determine asset class
        if is_option:
            asset_class = "us_option"
        elif is_crypto:
            asset_class = "crypto"
        else:
            asset_class = "us_equity"
        
        # Generate client order ID BEFORE placing order
        client_order_id = f"bypass_{bot_id}_{symbol.replace('/', '')}_{datetime.now().strftime('%H%M%S')}_{uuid.uuid4().hex[:8]}"
        signal_id = f"bypass_{bot_id}_{symbol}_{datetime.now().strftime('%H%M%S')}"
        
        # CRITICAL: Register entry intent BEFORE placing order
        # This prevents ExitBot from closing the position as "unknown"
        try:
            position_key = exitbot.register_entry_intent(
                bot_id=bot_id,
                symbol=symbol,
                side="long" if side == "buy" else "short",
                qty=qty,
                entry_price=float(suggestion.get("price") or 100.0),
                signal_id=signal_id,
                client_order_id=client_order_id,
                alpaca_order_id=None,  # Will update after order placed
                asset_class=asset_class,
                options=suggestion.get("indicators") if is_option else None
            )
            print(f"  ExitBot:  Pre-registered as {position_key}")
            logger.log("bypass_exitbot_preregistered", {
                "bot": bot_id,
                "symbol": symbol,
                "position_key": position_key,
                "client_order_id": client_order_id
            })
        except Exception as e:
            print(f"  ExitBot:  Pre-registration failed - {e}")
            logger.log("bypass_exitbot_preregister_error", {"error": str(e)})
        
        # Now place the order
        result = alpaca.place_market_order(
            symbol=symbol,
            side=side,
            qty=qty,
            client_order_id=client_order_id
        )
        
        if result:
            order_id = result.get("id")
            status = result.get("status")
            
            print(f"  Order ID: {order_id}")
            print(f"  Status:   {status}")
            
            logger.log("bypass_execute_success", {
                "bot": bot_id,
                "symbol": symbol,
                "order_id": order_id,
                "status": str(status),
                "client_order_id": client_order_id
            })
            
            return {
                "success": True,
                "order_id": order_id,
                "status": str(status),
                "suggestion": suggestion
            }
        else:
            print(f"  ERROR: No result from order")
            return {"success": False, "error": "No result", "suggestion": suggestion}
            
    except Exception as e:
        print(f"  ERROR: {e}")
        logger.log("bypass_execute_error", {
            "bot": bot_id,
            "symbol": symbol,
            "error": str(e)
        })
        return {"success": False, "error": str(e), "suggestion": suggestion}


def run_exitbot():
    """Run ExitBot to monitor all positions."""
    print(f"\n{'='*60}")
    print("RUNNING EXITBOT")
    print(f"{'='*60}")
    
    acct = alpaca.get_account()
    equity = float(acct.equity)
    
    result = exitbot.run(equity=equity, day_start_equity=equity)
    
    print(f"  Positions monitored: {result.positions_monitored}")
    print(f"  Trailing stops active: {result.trailing_stops_active}")
    print(f"  Exits triggered: {result.exits_triggered}")
    
    return result


def main():
    parser = argparse.ArgumentParser(description="Bypass Order Test Machine")
    parser.add_argument("--bot", type=str, help="Run specific bot: crypto, options, twentymin, momentum, all")
    parser.add_argument("--list", action="store_true", help="List available bots")
    parser.add_argument("--dry-run", action="store_true", help="Show suggestions without executing")
    args = parser.parse_args()
    
    if args.list:
        print("Available bots:")
        print("  crypto    - CryptoBot (BTC, ETH, etc.)")
        print("  options   - OptionsBot (SPY, QQQ options)")
        print("  twentymin - TwentyMinuteBot (gap trading)")
        print("  momentum  - MomentumBot (trend following)")
        print("  all       - Run all bots (default)")
        return
    
    bot_filter = args.bot.lower() if args.bot else "all"
    
    print("\n" + "="*60)
    print("BYPASS ORDER TEST MACHINE")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    suggestions = []
    
    if bot_filter in ["all", "crypto"]:
        print("\nGetting CryptoBot suggestion...")
        suggestions.append(get_crypto_suggestion())
        
    if bot_filter in ["all", "options"]:
        print("\nGetting OptionsBot suggestion...")
        suggestions.append(get_options_suggestion())
        
    if bot_filter in ["all", "twentymin"]:
        print("\nGetting TwentyMinuteBot suggestion...")
        suggestions.append(get_twentymin_suggestion())
        
    if bot_filter in ["all", "momentum"]:
        print("\nGetting MomentumBot suggestion...")
        suggestions.append(get_momentum_suggestion())
    
    print("\n" + "-"*60)
    print("SUGGESTIONS SUMMARY")
    print("-"*60)
    for s in suggestions:
        print(f"  {s['bot']:15} | {s['symbol']:10} | {s['side']:4} | Score: {s.get('score', 0):.1f} | {s.get('reason', '')[:40]}")
    
    if args.dry_run:
        print("\n[DRY RUN] No orders executed.")
        return
    
    results = []
    for suggestion in suggestions:
        result = execute_suggestion(suggestion)
        results.append(result)
    
    import time
    print("\nWaiting 3 seconds for orders to fill...")
    time.sleep(3)
    
    positions = alpaca.get_positions()
    print(f"\nPositions after execution: {len(positions)}")
    for p in positions:
        print(f"  {p.symbol}: {p.qty} @ ${float(p.current_price):.2f} (P&L: ${float(p.unrealized_pl):.2f})")
    
    if positions:
        exitbot_result = run_exitbot()
    else:
        print("\nNo positions to monitor with ExitBot.")
    
    print("\n" + "="*60)
    print("BYPASS TEST COMPLETE")
    print("="*60)
    successful = sum(1 for r in results if r.get("success"))
    print(f"  Orders executed: {successful}/{len(results)}")
    print(f"  Positions open:  {len(positions)}")


if __name__ == "__main__":
    main()
