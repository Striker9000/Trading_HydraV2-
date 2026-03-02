#!/usr/bin/env python3
"""
Trading Hydra Alpha Demo
========================

Demonstrates the system's alpha-generating capabilities through
a structured walkthrough of each edge source and risk layer.

Run: python scripts/alpha_demo.py
"""

import sys
import time
from datetime import datetime, timedelta
from typing import Dict, Any

sys.path.insert(0, ".")

# ANSI colors for terminal output
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def print_header(text: str):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{text.center(60)}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.ENDC}\n")

def print_section(text: str):
    print(f"\n{Colors.CYAN}{Colors.BOLD}>>> {text}{Colors.ENDC}")
    print(f"{Colors.CYAN}{'-'*50}{Colors.ENDC}")

def print_success(text: str):
    print(f"{Colors.GREEN}[OK] {text}{Colors.ENDC}")

def print_info(text: str):
    print(f"{Colors.BLUE}[i] {text}{Colors.ENDC}")

def print_warning(text: str):
    print(f"{Colors.YELLOW}[!] {text}{Colors.ENDC}")

def print_metric(name: str, value: Any, unit: str = ""):
    print(f"    {Colors.BOLD}{name}:{Colors.ENDC} {value}{unit}")

def pause(seconds: float = 1.0):
    time.sleep(seconds)

def demo_intro():
    print_header("TRADING HYDRA ALPHA DEMO")
    print("""
    This demo showcases the alpha-generating capabilities of
    Trading Hydra—a systematic trading system built on three pillars:

    1. TEMPORAL ARBITRAGE  - Trading forced flows
    2. VOLATILITY CAPTURE  - Harvesting mispriced premium  
    3. SURVIVAL DISCIPLINE - Living to trade another day

    The alpha is not in the signals. The alpha is in the risk management.
    """)
    pause(2)

def demo_market_clock():
    print_section("1. MARKET CLOCK — Temporal Awareness")
    
    try:
        from src.trading_hydra.core.clock import get_market_clock
        clock = get_market_clock()
        
        now = clock.now_naive()
        print_info(f"Current time (PST): {now.strftime('%Y-%m-%d %H:%M:%S')}")
        
        is_open = clock.in_market_hours() if hasattr(clock, 'in_market_hours') else "N/A"
        is_extended = clock.in_extended_hours() if hasattr(clock, 'in_extended_hours') else "N/A"
        print_info(f"Market open: {is_open}")
        print_info(f"Extended hours: {is_extended}")
        
        print_success("MarketClock provides consistent PST timezone across all bots")
        print_info("Why PST? US markets operate on Eastern time, but PST simplifies")
        print_info("overnight logic for West Coast operators.")
        
    except Exception as e:
        print_warning(f"MarketClock demo skipped: {e}")
    
    pause(1)

def demo_risk_gates():
    print_section("2. RISK GATES — Multi-Layer Protection")
    
    print("""
    Every entry must pass through multiple gates before execution:
    """)
    
    gates = [
        ("IV Percentile Gate", "Only trade when IV is favorable for strategy type"),
        ("Greek Limits Gate", "Portfolio delta/gamma within caps"),
        ("News Risk Gate", "No severe negative sentiment"),
        ("Correlation Guard", "Sector exposure within limits"),
        ("Vol-of-Vol Gate", "VIX rate-of-change not spiking"),
        ("Budget Gate", "Sufficient capital allocated"),
        ("Universe Guard", "Symbol in premarket-selected universe"),
    ]
    
    for gate, description in gates:
        print_metric(gate, description)
        pause(0.3)
    
    print()
    print_success("Gates are fail-closed: If uncertain, don't trade")
    print_info("Any gate can veto an entry. All must pass.")
    
    pause(1)

def demo_anti_churn():
    print_section("3. ANTI-CHURN — Preventing Overtrading")
    
    print("""
    The Problem:
    DOT/USD was getting stopped out 15+ times in 4 hours,
    losing $50-100 per round-trip due to:
    
    - Tight 0.4% stop hit by normal crypto volatility
    - 1-minute cooldown allowing immediate re-entry
    - No detection of choppy/whipsaw conditions
    
    Total loss: ~$3,600 in a single day on noise.
    """)
    pause(1)
    
    print("\n    The Solution:\n")
    
    protections = [
        ("Minimum Hold Time", "10 min", "Soft stop won't trigger until position aged"),
        ("Extended Cooldown", "30 min", "After stop-out, wait before re-entry"),
        ("Whipsaw Detection", "2 hr pause", "After 3 consecutive stop-outs"),
        ("Widened Stops", "1.2% / 2.0%", "Stop-loss / take-profit (was 0.4% / 0.8%)"),
    ]
    
    for name, value, description in protections:
        print(f"    {Colors.GREEN}{name}:{Colors.ENDC} {value}")
        print(f"      {Colors.BLUE}{description}{Colors.ENDC}")
        pause(0.3)
    
    print()
    print_success("Hard stop-loss ALWAYS fires immediately (safety first)")
    print_info("Only soft stops respect the minimum hold time")
    
    pause(1)

def demo_prestaged_entries():
    print_section("4. PRESTAGED ENTRIES — Never Enter Late")
    
    print("""
    The Problem:
    By the time we detect a gap and analyze it, the move is often
    50-70% exhausted. Late entry = poor risk:reward.
    
    The Solution:
    Calculate entries BEFORE market opens.
    """)
    pause(1)
    
    timeline = [
        ("6:00 AM", "PreStagedEntry scans for gaps, reversals, breakouts"),
        ("6:00 AM", "Calculate trigger levels for each setup"),
        ("6:30 AM", "Market opens, entries fire INSTANTLY when price hits trigger"),
    ]
    
    for time_str, action in timeline:
        print(f"    {Colors.YELLOW}{time_str}:{Colors.ENDC} {action}")
        pause(0.4)
    
    print()
    
    entry_types = [
        "Gap Down Breakdown (puts)",
        "Gap Up Breakout (calls)", 
        "Gap Fade Reversal",
        "Mean Reversion Bounce",
    ]
    
    print_info("Entry types pre-calculated:")
    for entry_type in entry_types:
        print(f"      - {entry_type}")
    
    print()
    print_success("No more chasing. Entries are predetermined.")
    
    pause(1)

def demo_exit_layers():
    print_section("5. EXIT PROTECTION — Multi-Layer Exits")
    
    print("""
    ExitBot runs every loop, checking multiple exit conditions:
    """)
    
    layers = [
        ("Hard Stop-Loss", "-X%", "Absolute floor, ALWAYS fires", Colors.RED),
        ("Soft Stop-Loss", "-Y%", "Respects min hold time", Colors.YELLOW),
        ("Trailing Stop", "Activated at +Z%", "Locks in profits", Colors.GREEN),
        ("TP1", "+A%", "Exit 33%, move stop to breakeven", Colors.GREEN),
        ("TP2", "+B%", "Exit 50% of remaining", Colors.GREEN),
        ("TP3", "+C%", "Exit 100% (full close)", Colors.GREEN),
        ("Time Stop", "N minutes", "Max hold duration", Colors.YELLOW),
        ("News Exit", "Negative sentiment", "AI-triggered emergency exit", Colors.RED),
    ]
    
    for name, threshold, description, color in layers:
        print(f"    {color}{name}:{Colors.ENDC} {threshold} — {description}")
        pause(0.3)
    
    print()
    print_success("Tiered exits let winners run while banking partial profits")
    
    pause(1)

def demo_greek_risk():
    print_section("6. GREEK RISK MANAGEMENT — Options Exposure")
    
    print("""
    Portfolio-level limits on options Greeks:
    """)
    
    greek_limits = [
        ("Delta Limit", "20% of equity", "Max directional exposure"),
        ("Gamma Limit", "5.0 per $1 move", "Max acceleration risk"),
    ]
    
    for name, limit, description in greek_limits:
        print_metric(name, f"{limit} — {description}")
    
    print()
    print_info("Status levels: WITHIN_LIMITS → NEAR_LIMIT → AT_LIMIT → BREACHED")
    print_success("ExitBot logs Greek exposure every loop for monitoring")
    
    pause(1)

def demo_pnl_attribution():
    print_section("7. P&L ATTRIBUTION — Know Why You Made/Lost Money")
    
    print("""
    Every closed option trade is decomposed into Greek components:
    """)
    
    components = [
        ("Delta P&L", "From underlying price movement"),
        ("Gamma P&L", "From delta acceleration"),
        ("Theta P&L", "From time decay"),
        ("Vega P&L", "From IV changes"),
        ("Residual P&L", "Unexplained (bid-ask, rho, model error)"),
    ]
    
    for name, description in components:
        print_metric(name, description)
    
    print()
    print_success("Persisted to pnl_attribution.jsonl for analysis")
    print_info("Aggregated by strategy and bot for performance insights")
    
    pause(1)

def demo_intelligence():
    print_section("8. MARKET INTELLIGENCE — Information Edge")
    
    sources = [
        ("News Intelligence", "Real-time AI sentiment on headlines"),
        ("Macro Intel", "Fed communication analysis (hawkish/dovish)"),
        ("Smart Money", "Congress trades, 13F institutional holdings"),
        ("Premarket Scan", "Multi-factor symbol ranking before open"),
    ]
    
    for name, description in sources:
        print_metric(name, description)
        pause(0.3)
    
    print()
    print_info("Regime modifiers: NORMAL / CAUTION / STRESS")
    print_info("Trade sizing adjusted by macro regime")
    print_success("Fail-closed: If intelligence is uncertain, don't trade")
    
    pause(1)

def demo_bot_summary():
    print_section("9. EXECUTION BOTS — Strategy Specialization")
    
    bots = [
        ("TwentyMinuteBot", "Opening auction instability", "First 20-30 min"),
        ("OptionsBot", "Premium capture with IV gates", "5-60 DTE swings"),
        ("MomentumBot", "Multi-week trend persistence", "ETFs/indices"),
        ("CryptoBot", "24/7 with anti-churn", "BTC, ETH, alts"),
        ("BounceBot", "Overnight dip-buying", "1-5:30 AM PST"),
    ]
    
    for bot, edge, context in bots:
        print(f"    {Colors.CYAN}{bot}:{Colors.ENDC}")
        print(f"      Edge: {edge}")
        print(f"      Context: {context}")
        pause(0.4)
    
    print()
    print_success("Each bot trades its own edge, within unified risk framework")
    
    pause(1)

def demo_philosophy():
    print_section("10. CORE PHILOSOPHY")
    
    print(f"""
    {Colors.BOLD}The alpha is not in the signals.{Colors.ENDC}
    {Colors.BOLD}The alpha is in the risk management.{Colors.ENDC}
    
    We do not try to be smarter than the market.
    
    We position where others are {Colors.YELLOW}forced to act{Colors.ENDC}.
    We harvest {Colors.GREEN}structural premium{Colors.ENDC}.
    We survive through {Colors.CYAN}discipline{Colors.ENDC}.
    We exit before edge conditions expire.
    
    {Colors.BOLD}Survive first. Profit second.{Colors.ENDC}
    """)
    
    pause(2)

def demo_metrics():
    print_section("PERFORMANCE TARGETS")
    
    metrics = [
        ("Gross Sharpe", "1.5 - 2.0", "> 2.5 = investigate"),
        ("Net Sharpe", "1.0 - 1.3", "> 2.0 = investigate"),
        ("Max Drawdown", "< 15%", "> 20% = system review"),
        ("Win Rate", "50 - 60%", "> 70% = overfitting"),
    ]
    
    print(f"\n    {'Metric':<20} {'Target':<15} {'Suspicion':<25}")
    print(f"    {'-'*20} {'-'*15} {'-'*25}")
    
    for metric, target, suspicion in metrics:
        print(f"    {metric:<20} {target:<15} {suspicion:<25}")
    
    pause(1)

def main():
    demo_intro()
    demo_market_clock()
    demo_risk_gates()
    demo_anti_churn()
    demo_prestaged_entries()
    demo_exit_layers()
    demo_greek_risk()
    demo_pnl_attribution()
    demo_intelligence()
    demo_bot_summary()
    demo_philosophy()
    demo_metrics()
    
    print_header("DEMO COMPLETE")
    print("""
    For more information, see:
    
    - ALPHA_MEMO.md      — Investment thesis and edge sources
    - docs/PHILOSOPHY.md — Module-by-module design philosophy
    - replit.md          — Technical architecture overview
    
    The code just keeps score. Discipline is the alpha.
    """)

if __name__ == "__main__":
    main()
