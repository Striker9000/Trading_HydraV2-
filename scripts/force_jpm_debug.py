#!/usr/bin/env python3
"""
Force JPM Debug Script
======================
Forces JPM through TwentyMinute bot AND Options bot with detailed logging
of every gate/check that would normally block it.

Bypasses all checks until position is in ExitBot's hands.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from datetime import datetime
from typing import Dict, Any, Optional, List
import json

from trading_hydra.services.alpaca_client import get_alpaca_client
from trading_hydra.core.clock import get_market_clock
from trading_hydra.core.logging import get_logger
from trading_hydra.core.config import load_bots_config, load_settings
from trading_hydra.risk.universe_guard import get_universe_guard
from trading_hydra.bots.twenty_minute_bot import TwentyMinuteBot
from trading_hydra.bots.options_bot import OptionsBot

SYMBOL = "JPM"
FORCE_BUDGET = 500.0  # Dollar amount for test trades

class ForceTradeLogger:
    """Logs all gates and their bypass status."""
    
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.gates = []
        self.start_time = datetime.now()
        
    def gate(self, name: str, would_block: bool, reason: str, bypassed: bool = True):
        """Log a gate check."""
        status = "🚫 BLOCKED" if would_block else "✅ PASSED"
        bypass_str = " → 🔓 BYPASSED" if (would_block and bypassed) else ""
        
        entry = {
            "gate": name,
            "would_block": would_block,
            "reason": reason,
            "bypassed": bypassed
        }
        self.gates.append(entry)
        
        print(f"  {status} [{name}] {reason}{bypass_str}")
        
    def section(self, title: str):
        """Print section header."""
        print(f"\n{'='*60}")
        print(f"  {title}")
        print(f"{'='*60}")
        
    def summary(self):
        """Print summary of all gates."""
        blocked = [g for g in self.gates if g["would_block"]]
        passed = [g for g in self.gates if not g["would_block"]]
        
        print(f"\n{'='*60}")
        print(f"  SUMMARY: {self.symbol}")
        print(f"{'='*60}")
        print(f"  Total gates checked: {len(self.gates)}")
        print(f"  ✅ Passed: {len(passed)}")
        print(f"  🚫 Would have blocked: {len(blocked)}")
        
        if blocked:
            print(f"\n  Gates that would have blocked:")
            for g in blocked:
                print(f"    - {g['gate']}: {g['reason']}")


def force_twentyminute_bot(logger: ForceTradeLogger) -> Optional[Dict[str, Any]]:
    """Force JPM through TwentyMinute bot pipeline."""
    
    logger.section(f"TWENTYMINUTE BOT - {SYMBOL}")
    
    alpaca = get_alpaca_client()
    clock = get_market_clock()
    now = clock.now()
    
    print(f"  Current time (PST): {now.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Create bot instance
    bot = TwentyMinuteBot()
    bot_config = bot._config
    
    if not bot_config:
        logger.gate("config_load", True, "Failed to load TwentyMinute config", bypassed=False)
        return None
    logger.gate("config_load", False, "Config loaded successfully")
    
    # Gate 1: Bot enabled
    enabled = bot_config.enabled
    logger.gate("bot_enabled", not enabled, f"enabled={enabled}")
    
    # Gate 2: Trading session
    in_session = bot._is_in_session()
    pre_session = bot._is_pre_session()
    session_ok = in_session or pre_session
    logger.gate("trading_session", not session_ok, 
                f"in_session={in_session}, pre_session={pre_session}, "
                f"window={bot_config.session_start}-{bot_config.session_end}")
    
    # Gate 3: Symbol in ticker list
    in_tickers = SYMBOL in bot_config.tickers
    logger.gate("ticker_list", not in_tickers, f"JPM in tickers list: {in_tickers}")
    
    # Gate 4: Can trade today (max trades check)
    can_trade = bot._can_trade_today()
    trades_today = getattr(bot, '_trades_today', 0)
    logger.gate("max_trades_today", not can_trade, 
                f"trades_today={trades_today}, max={bot_config.max_trades_per_day}")
    
    # Gate 5: Concurrent positions
    positions = alpaca.get_positions()
    my_positions = [p for p in positions if p.symbol in bot_config.tickers]
    under_limit = len(my_positions) < bot_config.max_concurrent_positions
    logger.gate("concurrent_positions", not under_limit,
                f"current={len(my_positions)}, max={bot_config.max_concurrent_positions}")
    
    # Gate 6: Already have JPM position
    has_jpm = any(p.symbol == SYMBOL for p in positions)
    logger.gate("existing_position", has_jpm, f"Already holding {SYMBOL}: {has_jpm}")
    
    # Gate 7: Gap analysis
    print(f"\n  Analyzing gap for {SYMBOL}...")
    gap = bot._analyze_gap(SYMBOL)
    if gap:
        print(f"    Gap: {gap.gap_pct:.2f}%, Direction: {gap.gap_direction.value}")
        print(f"    Volume ratio: {gap.volume_ratio:.2f}x")
        print(f"    Significant: {gap.is_significant}")
        logger.gate("gap_analysis", not gap.is_significant,
                    f"gap={gap.gap_pct:.2f}%, significant={gap.is_significant}, "
                    f"min_required={bot_config.min_gap_pct}%")
    else:
        logger.gate("gap_analysis", True, "No gap data available")
        gap = None
    
    # Gate 8: Pattern detection (if gap exists)
    pattern = None
    if gap:
        from trading_hydra.bots.twenty_minute_bot import PatternType
        pattern = bot._detect_pattern(SYMBOL, gap)
        if pattern:
            print(f"    Pattern: {pattern.pattern.value}")
            print(f"    Direction: {pattern.direction.value}")
            print(f"    Confidence: {pattern.confidence:.2f}")
            print(f"    Reason: {pattern.reason}")
            valid_pattern = pattern.pattern != PatternType.NO_PATTERN
            logger.gate("pattern_detection", not valid_pattern,
                        f"pattern={pattern.pattern.value}, confidence={pattern.confidence:.2f}")
        else:
            logger.gate("pattern_detection", True, "No pattern detected")
    
    # Gate 9: ML score (if ML enabled)
    ml_enabled = getattr(bot, '_ml_enabled', False)
    if ml_enabled and gap and pattern:
        ml_score = bot._score_with_ml(SYMBOL, pattern, gap)
        ml_threshold = getattr(bot, '_ml_min_probability', 0.5)
        logger.gate("ml_score", ml_score < ml_threshold,
                    f"score={ml_score:.2f}, threshold={ml_threshold}")
    else:
        logger.gate("ml_score", False, f"ML disabled or no pattern (ml_enabled={ml_enabled})")
    
    # FORCE EXECUTION
    logger.section(f"FORCE EXECUTION - TWENTYMINUTE {SYMBOL}")
    
    if has_jpm:
        print(f"  ⚠️  Already have {SYMBOL} position - skipping TwentyMin entry")
        return {"skipped": True, "reason": "existing_position"}
    
    print(f"  🔥 FORCING {SYMBOL} entry via TwentyMinute bot...")
    print(f"  Budget: ${FORCE_BUDGET}")
    
    # Get current price
    try:
        quote = alpaca.get_latest_quote(SYMBOL)
        if isinstance(quote, dict):
            current_price = float(quote.get('ask') or quote.get('bid') or 0)
        else:
            current_price = float(quote.ask_price) if quote.ask_price else float(quote.bid_price)
        print(f"  Current price: ${current_price:.2f}")
    except Exception as e:
        print(f"  ❌ Failed to get quote: {e}")
        return None
    
    # Check if options execution is enabled
    use_options = bot_config.use_options
    print(f"  Options execution: {use_options}")
    
    if use_options:
        # Force through options path
        print(f"\n  📊 Attempting OPTIONS entry for {SYMBOL}...")
        try:
            # Create a mock pattern if none detected
            if not pattern or not gap:
                from trading_hydra.bots.twenty_minute_bot import PatternSignal, PatternType, GapDirection
                from dataclasses import dataclass
                
                print(f"  ⚠️  No valid pattern - creating FORCED pattern")
                
                # Determine direction based on price action
                bars = alpaca.get_bars(SYMBOL, "5Min", limit=5)
                if bars:
                    first_bar = bars[0]
                    last_bar = bars[-1]
                    direction = GapDirection.UP if last_bar.close > first_bar.open else GapDirection.DOWN
                else:
                    direction = GapDirection.UP
                
                pattern = PatternSignal(
                    pattern=PatternType.FIRST_BAR_BREAK,
                    direction=direction,
                    entry_price=current_price,
                    stop_price=current_price * 0.98,
                    target_price=current_price * 1.02,
                    confidence=0.75,
                    reason="FORCED_DEBUG_ENTRY"
                )
                print(f"    Forced pattern: {pattern.pattern.value}, direction: {direction.value}")
            
            # Execute options entry
            result = bot._execute_options_entry(SYMBOL, pattern, FORCE_BUDGET, {})
            
            if result and result.get("success"):
                print(f"\n  ✅ OPTIONS ENTRY SUCCESSFUL!")
                print(f"     Order ID: {result.get('order_id')}")
                print(f"     Contract: {result.get('contract')}")
                print(f"     Position now managed by ExitBot")
                return result
            else:
                print(f"\n  ❌ Options entry failed: {result}")
                # Fall back to stock entry
                print(f"\n  Falling back to stock entry...")
                
        except Exception as e:
            print(f"  ❌ Options entry error: {e}")
            import traceback
            traceback.print_exc()
    
    # Stock entry fallback
    print(f"\n  📊 Attempting STOCK entry for {SYMBOL}...")
    try:
        shares = int(FORCE_BUDGET / current_price)
        if shares < 1:
            shares = 1
        
        print(f"  Submitting order: {shares} shares @ ~${current_price:.2f}")
        
        order = alpaca.place_market_order(
            symbol=SYMBOL,
            side="buy",
            qty=shares
        )
        
        print(f"\n  ✅ STOCK ENTRY SUCCESSFUL!")
        print(f"     Order ID: {order.get('id')}")
        print(f"     Shares: {shares}")
        print(f"     Position now managed by ExitBot")
        
        return {
            "success": True,
            "order_id": order.get('id'),
            "symbol": SYMBOL,
            "shares": shares,
            "bot": "twentyminute"
        }
        
    except Exception as e:
        print(f"  ❌ Stock entry error: {e}")
        import traceback
        traceback.print_exc()
        return None


def force_options_bot(logger: ForceTradeLogger) -> Optional[Dict[str, Any]]:
    """Force JPM through Options bot pipeline."""
    
    logger.section(f"OPTIONS BOT - {SYMBOL}")
    
    alpaca = get_alpaca_client()
    clock = get_market_clock()
    now = clock.now()
    
    print(f"  Current time (PST): {now.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Create bot instance
    bot = OptionsBot()
    bot_config = bot._config
    
    if not bot_config:
        logger.gate("config_load", True, "Failed to load Options bot config", bypassed=False)
        return None
    logger.gate("config_load", False, "Config loaded successfully")
    
    # Gate 1: Bot enabled
    enabled = bot_config.enabled
    logger.gate("bot_enabled", not enabled, f"enabled={enabled}")
    
    # Gate 2: Trading hours
    in_hours = bot._is_trading_hours()
    logger.gate("trading_hours", not in_hours,
                f"in_hours={in_hours}, window={bot_config.trade_start}-{bot_config.trade_end}")
    
    # Gate 3: Symbol in ticker list
    in_tickers = SYMBOL in bot.tickers
    logger.gate("ticker_list", not in_tickers, f"JPM in options tickers: {in_tickers}")
    
    # Gate 4: Risk limits
    risk_ok = bot._check_risk_limits()
    logger.gate("risk_limits", not risk_ok, f"risk_limits_ok={risk_ok}")
    
    # Gate 5: Trades today
    trades_today = bot._get_trades_today()
    max_trades = bot_config.max_trades_per_day
    under_limit = trades_today < max_trades
    logger.gate("max_trades_today", not under_limit,
                f"trades_today={trades_today}, max={max_trades}")
    
    # Gate 6: Concurrent positions
    positions = bot._get_options_positions()
    current_positions = len(positions)
    under_pos_limit = current_positions < bot_config.max_concurrent_positions
    logger.gate("concurrent_positions", not under_pos_limit,
                f"current={current_positions}, max={bot_config.max_concurrent_positions}")
    
    # Gate 7: Universe Guard
    guard = get_universe_guard()
    in_universe = guard.is_symbol_allowed(SYMBOL, bot_id=bot.bot_id)
    logger.gate("universe_guard", not in_universe,
                f"in_selected_universe={in_universe}")
    
    # Gate 8: Pending orders
    open_orders = alpaca.get_open_orders()
    pending_for_jpm = any(SYMBOL in o.get("symbol", "") for o in open_orders)
    logger.gate("pending_orders", pending_for_jpm,
                f"has_pending_order_for_{SYMBOL}={pending_for_jpm}")
    
    # Gate 9: Market analysis
    print(f"\n  Analyzing market conditions for {SYMBOL}...")
    try:
        analysis = bot._analyze_market_conditions(SYMBOL)
        print(f"    Trend: {analysis.get('trend', 'unknown')}")
        print(f"    Volatility regime: {analysis.get('volatility_regime', 'unknown')}")
        print(f"    IV rank: {analysis.get('iv_rank', 'N/A')}")
        print(f"    Halt new entries: {analysis.get('halt_new_entries', False)}")
        
        halt_entries = analysis.get("halt_new_entries", False)
        logger.gate("market_analysis", halt_entries,
                    f"halt_new_entries={halt_entries}, trend={analysis.get('trend')}")
    except Exception as e:
        print(f"    ⚠️  Analysis error: {e}")
        analysis = {}
        logger.gate("market_analysis", True, f"Analysis failed: {e}")
    
    # Gate 10: Strategy selection
    print(f"\n  Selecting optimal strategy...")
    try:
        best_strategy = bot._select_optimal_strategy(analysis)
        if best_strategy:
            print(f"    Selected: {best_strategy}")
            logger.gate("strategy_selection", False, f"strategy={best_strategy}")
        else:
            print(f"    No strategy selected")
            logger.gate("strategy_selection", True, "No strategy matched conditions")
    except Exception as e:
        print(f"    ⚠️  Strategy selection error: {e}")
        best_strategy = None
        logger.gate("strategy_selection", True, f"Selection failed: {e}")
    
    # FORCE EXECUTION
    logger.section(f"FORCE EXECUTION - OPTIONS BOT {SYMBOL}")
    
    # Check if we already have JPM options
    jpm_options = [p for p in positions if SYMBOL in p.symbol]
    if jpm_options:
        print(f"  ⚠️  Already have {SYMBOL} options position - skipping")
        for p in jpm_options:
            print(f"       {p.symbol}: {p.qty} contracts")
        return {"skipped": True, "reason": "existing_position"}
    
    print(f"  🔥 FORCING {SYMBOL} options entry...")
    
    # Get current price
    try:
        quote = alpaca.get_latest_quote(SYMBOL)
        if isinstance(quote, dict):
            current_price = float(quote.get('ask') or quote.get('bid') or 0)
        else:
            current_price = float(quote.ask_price) if quote.ask_price else float(quote.bid_price)
        print(f"  Current price: ${current_price:.2f}")
    except Exception as e:
        print(f"  ❌ Failed to get quote: {e}")
        return None
    
    # Force a long call entry (simplest options strategy)
    print(f"\n  📊 Attempting LONG CALL entry for {SYMBOL}...")
    
    try:
        from trading_hydra.bots.options_bot import OptionStrategy
        
        result = bot._execute_long_call(
            ticker=SYMBOL,
            underlying_price=current_price,
            max_daily_loss=FORCE_BUDGET
        )
        
        if result and result.get("success"):
            print(f"\n  ✅ OPTIONS ENTRY SUCCESSFUL!")
            print(f"     Order ID: {result.get('order_id')}")
            print(f"     Contract: {result.get('contract')}")
            print(f"     Strike: {result.get('strike')}")
            print(f"     Expiry: {result.get('expiry')}")
            print(f"     Position now managed by ExitBot")
            return result
        else:
            print(f"\n  ❌ Long call entry failed: {result}")
            
    except Exception as e:
        print(f"  ❌ Long call error: {e}")
        import traceback
        traceback.print_exc()
    
    return None


def main():
    """Run force trade debug for JPM."""
    
    print("\n" + "="*60)
    print("  FORCE TRADE DEBUG - JPM")
    print("  " + "="*56)
    print(f"  Symbol: {SYMBOL}")
    print(f"  Budget: ${FORCE_BUDGET}")
    print(f"  Purpose: Log all gates, bypass and force entry")
    print("="*60)
    
    logger = ForceTradeLogger(SYMBOL)
    
    # Run through both bots
    twentymin_result = force_twentyminute_bot(logger)
    options_result = force_options_bot(logger)
    
    # Summary
    logger.summary()
    
    print(f"\n{'='*60}")
    print("  FINAL RESULTS")
    print(f"{'='*60}")
    
    if twentymin_result and twentymin_result.get("success"):
        print(f"  ✅ TwentyMinute: Entry successful")
    elif twentymin_result and twentymin_result.get("skipped"):
        print(f"  ⏭️  TwentyMinute: Skipped ({twentymin_result.get('reason')})")
    else:
        print(f"  ❌ TwentyMinute: Entry failed")
    
    if options_result and options_result.get("success"):
        print(f"  ✅ Options Bot: Entry successful")
    elif options_result and options_result.get("skipped"):
        print(f"  ⏭️  Options Bot: Skipped ({options_result.get('reason')})")
    else:
        print(f"  ❌ Options Bot: Entry failed")
    
    print(f"\n  Positions are now in ExitBot's hands.")
    print(f"  Run the main trading loop to see ExitBot manage them.")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
