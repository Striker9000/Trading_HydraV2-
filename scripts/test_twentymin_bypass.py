#!/usr/bin/env python3
"""
TwentyMinuteBot Decision-Making Bypass Test

Forces gap analysis and pattern detection regardless of session time.
This lets us see what the bot WOULD do if the market was open.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.trading_hydra.bots.twenty_minute_bot import TwentyMinuteBot, SignalDirection
from src.trading_hydra.services.alpaca_client import get_alpaca_client
from src.trading_hydra.core.logging import get_logger
from src.trading_hydra.core.clock import get_market_clock

logger = get_logger()

def test_gap_analysis_bypass():
    """Run gap analysis on all tickers, bypassing session check."""
    print("=" * 70)
    print("TWENTYMINUTEBOT - DECISION BYPASS TEST")
    print("=" * 70)
    
    clock = get_market_clock()
    now = clock.now()
    print(f"\nCurrent Time PST: {now.strftime('%H:%M:%S')}")
    
    bot = TwentyMinuteBot()
    print(f"Session Window: {bot._config.session_start} - {bot._config.session_end}")
    print(f"In Session: {bot._is_in_session()}")
    print(f"Min Gap %: {bot._config.min_gap_pct}")
    print(f"Tickers: {len(bot._config.tickers)}")
    
    # Get current positions to exclude
    alpaca = get_alpaca_client()
    positions = alpaca.get_positions()
    my_positions = {p.symbol for p in positions if p.symbol in bot._config.tickers}
    print(f"Already holding: {my_positions}")
    
    print("\n" + "=" * 70)
    print("BYPASS: FORCING GAP ANALYSIS ON ALL TICKERS")
    print("=" * 70)
    
    all_gaps = []
    significant_gaps = []
    patterns_found = []
    
    # Test on a subset for speed
    test_tickers = bot._config.tickers[:20]  # First 20 tickers
    
    for i, ticker in enumerate(test_tickers):
        if ticker in my_positions:
            print(f"[{i+1:2d}/{len(test_tickers)}] {ticker:6s} - SKIP (already holding)")
            continue
        
        try:
            # Force gap analysis
            gap = bot._analyze_gap(ticker)
            
            if gap:
                gap_info = {
                    "ticker": ticker,
                    "gap_pct": round(gap.gap_pct, 3),
                    "direction": gap.gap_direction.value,
                    "prev_close": round(gap.prev_close, 2),
                    "current": round(gap.current_price, 2),
                    "volume_ratio": round(gap.volume_ratio, 2),
                    "is_significant": gap.is_significant
                }
                all_gaps.append(gap_info)
                
                status = "SIGNIFICANT" if gap.is_significant else "small"
                direction = "UP" if gap.gap_direction == SignalDirection.LONG else "DOWN"
                print(f"[{i+1:2d}/{len(test_tickers)}] {ticker:6s} - Gap: {gap.gap_pct:+6.2f}% {direction:5s} | Vol: {gap.volume_ratio:.1f}x | {status}")
                
                if gap.is_significant:
                    significant_gaps.append(gap_info)
                    
                    # Try pattern detection
                    pattern = bot._detect_pattern(ticker, gap)
                    
                    if pattern and pattern.pattern.value != "no_pattern":
                        patterns_found.append({
                            "ticker": ticker,
                            "pattern": pattern.pattern.value,
                            "direction": pattern.direction.value,
                            "confidence": round(pattern.confidence, 2),
                            "entry": round(pattern.entry_price, 2),
                            "stop": round(pattern.stop_price, 2),
                            "target": round(pattern.target_price, 2),
                            "reason": pattern.reason
                        })
                        print(f"         → PATTERN: {pattern.pattern.value} ({pattern.direction.value}) confidence={pattern.confidence:.0%}")
            else:
                print(f"[{i+1:2d}/{len(test_tickers)}] {ticker:6s} - No data")
                
        except Exception as e:
            print(f"[{i+1:2d}/{len(test_tickers)}] {ticker:6s} - ERROR: {e}")
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    print(f"\nTotal Gaps Analyzed: {len(all_gaps)}")
    print(f"Significant Gaps (>= {bot._config.min_gap_pct}%): {len(significant_gaps)}")
    print(f"Patterns Detected: {len(patterns_found)}")
    
    if significant_gaps:
        print("\n--- SIGNIFICANT GAPS ---")
        sorted_gaps = sorted(significant_gaps, key=lambda x: abs(x["gap_pct"]), reverse=True)
        for g in sorted_gaps:
            print(f"  {g['ticker']:6s}: {g['gap_pct']:+6.2f}% | Vol: {g['volume_ratio']:.1f}x")
    
    if patterns_found:
        print("\n--- TRADEABLE PATTERNS ---")
        for p in patterns_found:
            print(f"  {p['ticker']:6s}: {p['pattern']} ({p['direction']}) @ ${p['entry']:.2f}")
            print(f"           Stop: ${p['stop']:.2f} | Target: ${p['target']:.2f} | Confidence: {p['confidence']:.0%}")
    else:
        print("\n⚠️  NO TRADEABLE PATTERNS FOUND")
        print("   Possible reasons:")
        print(f"   - Min gap threshold: {bot._config.min_gap_pct}% (try lowering)")
        print("   - Market alignment filter (SPY/QQQ direction)")
        print("   - VWAP/EMA confirmation not met")
        print("   - ML score below threshold")
    
    logger.log("twentymin_bypass_test_complete", {
        "gaps_analyzed": len(all_gaps),
        "significant_gaps": len(significant_gaps),
        "patterns_found": len(patterns_found)
    })
    
    return {
        "all_gaps": all_gaps,
        "significant_gaps": significant_gaps,
        "patterns_found": patterns_found
    }


if __name__ == "__main__":
    result = test_gap_analysis_bypass()
