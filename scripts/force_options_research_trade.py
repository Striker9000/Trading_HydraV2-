#!/usr/bin/env python3
"""
Force Options Research Trade Script
====================================
This script:
1. Scans the options universe for the best trading setup (IV, liquidity, spread)
2. Uses AI (OpenAI) to research news and sentiment for the top candidate
3. Selects the best option contract (call or put based on sentiment)
4. Forces the trade through, bypassing normal risk gates

Usage:
    python scripts/force_options_research_trade.py
    python scripts/force_options_research_trade.py --budget 200  # Custom budget
    python scripts/force_options_research_trade.py --dry-run    # Research only, no trade
"""

import sys
import os
import time
import json
import argparse
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.trading_hydra.services.alpaca_client import get_alpaca_client
from src.trading_hydra.core.logging import get_logger
from src.trading_hydra.core.config import load_bots_config


@dataclass
class TickerAnalysis:
    """Analysis result for a single ticker"""
    ticker: str
    price: float
    iv_rank: float
    spread_pct: float
    volume_score: float
    composite_score: float
    trend: str
    reason: str


@dataclass
class AIResearchResult:
    """AI research result for a ticker"""
    ticker: str
    sentiment_score: float
    sentiment_label: str
    confidence: float
    summary: str
    key_catalysts: List[str]
    recommended_direction: str
    reasoning: str


@dataclass
class OptionContract:
    """Selected option contract"""
    symbol: str
    underlying: str
    strike: float
    expiry: str
    contract_type: str
    bid: float
    ask: float
    mid_price: float
    delta: float
    dte: int
    volume: int
    open_interest: int


logger = get_logger()


def get_options_universe() -> List[str]:
    """Get the options universe from bots.yaml config"""
    try:
        config = load_bots_config()
        optionsbot_config = config.get("optionsbot", {})
        tickers = optionsbot_config.get("tickers", [])
        if tickers:
            return tickers
    except Exception as e:
        logger.log("config_load_error", {"error": str(e)})
    
    return ["AAPL", "AMD", "MSFT", "NVDA", "TSLA", "PLTR", "BLK"]


def analyze_ticker(alpaca, ticker: str) -> Optional[TickerAnalysis]:
    """
    Analyze a single ticker for options trading potential.
    Uses stock liquidity metrics as proxy for options liquidity.
    """
    try:
        quote = alpaca.get_latest_quote(ticker, asset_class="stock")
        if not quote or quote.get("bid", 0) <= 0:
            return None
        
        bid = float(quote.get("bid", 0))
        ask = float(quote.get("ask", 0))
        price = (bid + ask) / 2 if bid > 0 and ask > 0 else float(quote.get("price", 0))
        
        if price <= 0:
            return None
        
        spread_pct = ((ask - bid) / price * 100) if price > 0 else 100
        
        liquidity_score = max(0, 100 - spread_pct * 50)
        
        iv_rank = 50.0
        volume_score = 70.0
        
        composite_score = liquidity_score * 0.6 + iv_rank * 0.2 + volume_score * 0.2
        composite_score = max(0, min(100, composite_score))
        
        trend = "neutral"
        if spread_pct < 0.1:
            trend = "bullish"
        elif spread_pct > 0.5:
            trend = "bearish"
        
        return TickerAnalysis(
            ticker=ticker,
            price=price,
            iv_rank=iv_rank,
            spread_pct=spread_pct,
            volume_score=volume_score,
            composite_score=composite_score,
            trend=trend,
            reason=f"Price ${price:.2f}, Spread {spread_pct:.2f}%, Liquidity {liquidity_score:.0f}"
        )
        
    except Exception as e:
        logger.log("ticker_analysis_error", {"ticker": ticker, "error": str(e)})
        return None


def scan_universe(alpaca, universe: List[str]) -> List[TickerAnalysis]:
    """Scan the universe and return ranked tickers"""
    print("\n" + "=" * 60)
    print("STEP 1: SCANNING OPTIONS UNIVERSE")
    print("=" * 60)
    
    results = []
    for ticker in universe:
        print(f"  Analyzing {ticker}...", end=" ")
        analysis = analyze_ticker(alpaca, ticker)
        if analysis:
            results.append(analysis)
            print(f"Score: {analysis.composite_score:.1f}")
        else:
            print("SKIP (no data)")
    
    results.sort(key=lambda x: x.composite_score, reverse=True)
    
    print(f"\nTop candidates:")
    for i, r in enumerate(results[:5], 1):
        print(f"  {i}. {r.ticker}: Score {r.composite_score:.1f} ({r.reason})")
    
    logger.log("universe_scan_complete", {
        "scanned": len(universe),
        "valid": len(results),
        "top_ticker": results[0].ticker if results else None
    })
    
    return results


def research_with_ai(ticker: str, price: float) -> AIResearchResult:
    """Use OpenAI to research the ticker and provide trading recommendation"""
    print("\n" + "=" * 60)
    print(f"STEP 2: AI RESEARCH FOR {ticker}")
    print("=" * 60)
    
    try:
        from openai import OpenAI
        
        base_url = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
        api_key = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
        
        if not api_key:
            api_key = os.environ.get("OPENAI_API_KEY")
            base_url = None
        
        if not api_key:
            print("  OpenAI not configured, using default analysis")
            print("  Set OPENAI_API_KEY environment variable for AI research")
            return AIResearchResult(
                ticker=ticker,
                sentiment_score=0.2,
                sentiment_label="slightly_bullish",
                confidence=0.5,
                summary="Unable to fetch AI research - OpenAI not configured",
                key_catalysts=["Technical analysis only"],
                recommended_direction="call",
                reasoning="Defaulting to bullish bias without news data"
            )
        
        if base_url:
            client = OpenAI(base_url=base_url, api_key=api_key)
        else:
            client = OpenAI(api_key=api_key)
        
        prompt = f"""You are a professional options trader analyzing {ticker} (current price: ${price:.2f}).

Research this stock and provide a trading recommendation. Consider:
1. Recent news and catalysts (earnings, product launches, partnerships)
2. Market sentiment and analyst ratings
3. Technical indicators and price action
4. Sector trends and macro factors

Respond in this exact JSON format:
{{
    "sentiment_score": <float from -1.0 (very bearish) to 1.0 (very bullish)>,
    "sentiment_label": "<bearish|slightly_bearish|neutral|slightly_bullish|bullish>",
    "confidence": <float from 0.0 to 1.0>,
    "summary": "<2-3 sentence summary of your analysis>",
    "key_catalysts": ["<catalyst 1>", "<catalyst 2>", "<catalyst 3>"],
    "recommended_direction": "<call|put>",
    "reasoning": "<1-2 sentence explanation of your recommendation>"
}}"""

        print("  Querying OpenAI for research...")
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a professional options trader. Respond only with valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=500
        )
        
        content = response.choices[0].message.content.strip()
        
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        content = content.strip()
        
        data = json.loads(content)
        
        result = AIResearchResult(
            ticker=ticker,
            sentiment_score=float(data.get("sentiment_score", 0)),
            sentiment_label=data.get("sentiment_label", "neutral"),
            confidence=float(data.get("confidence", 0.5)),
            summary=data.get("summary", "No summary available"),
            key_catalysts=data.get("key_catalysts", []),
            recommended_direction=data.get("recommended_direction", "call"),
            reasoning=data.get("reasoning", "No reasoning provided")
        )
        
        print(f"\n  AI Research Results:")
        print(f"  Sentiment: {result.sentiment_label} ({result.sentiment_score:+.2f})")
        print(f"  Confidence: {result.confidence:.0%}")
        print(f"  Summary: {result.summary}")
        print(f"  Key Catalysts:")
        for cat in result.key_catalysts[:3]:
            print(f"    - {cat}")
        print(f"  Recommendation: {result.recommended_direction.upper()}")
        print(f"  Reasoning: {result.reasoning}")
        
        logger.log("ai_research_complete", {
            "ticker": ticker,
            "sentiment_score": result.sentiment_score,
            "direction": result.recommended_direction,
            "confidence": result.confidence
        })
        
        return result
        
    except Exception as e:
        logger.log("ai_research_error", {"ticker": ticker, "error": str(e)})
        print(f"  AI research failed: {e}")
        print("  Falling back to default analysis")
        
        return AIResearchResult(
            ticker=ticker,
            sentiment_score=0.1,
            sentiment_label="slightly_bullish",
            confidence=0.4,
            summary=f"AI research unavailable for {ticker}",
            key_catalysts=["Unable to fetch catalysts"],
            recommended_direction="call",
            reasoning="Defaulting to call with low confidence"
        )


def select_option_contract(alpaca, ticker: str, price: float, direction: str, budget: float = 100.0) -> Optional[OptionContract]:
    """Select the best option contract for the trade using real chain data"""
    print("\n" + "=" * 60)
    print(f"STEP 3: SELECTING {direction.upper()} OPTION FOR {ticker}")
    print("=" * 60)
    
    try:
        from src.trading_hydra.services.options_data import get_options_data_service
        
        options_service = get_options_data_service()
        
        config = load_bots_config()
        optionsbot_config = config.get("optionsbot", {})
        chain_rules = optionsbot_config.get("chain_rules", {})
        
        min_dte = chain_rules.get("dte_min", 7)
        max_dte = chain_rules.get("dte_max", 45)
        delta_min = chain_rules.get("delta_min", 0.30)
        delta_max = chain_rules.get("delta_max", 0.60)
        min_volume = chain_rules.get("min_volume", 50)
        min_oi = chain_rules.get("min_open_interest", 100)
        
        strategy_config = optionsbot_config.get("strategies", {}).get(f"long_{direction}", {})
        max_cost = strategy_config.get("max_cost", 3.00)
        
        print(f"  Fetching options chain for {ticker}...")
        print(f"  Filters: DTE {min_dte}-{max_dte}, Delta {delta_min:.2f}-{delta_max:.2f}, Max Cost ${max_cost:.2f}")
        
        chain = options_service.get_options_chain(ticker)
        
        is_synthetic_chain = False
        if not chain:
            print(f"  [WARNING] Real chain unavailable, generating synthetic chain...")
            print(f"  [WARNING] LIVE TRADING BLOCKED when using synthetic data")
            logger.log("options_chain_fallback", {
                "ticker": ticker,
                "reason": "alpaca_chain_unavailable",
                "fallback": "synthetic_black_scholes",
                "live_trading_blocked": True
            })
            is_synthetic_chain = True
            underlying_price = price
            chain = options_service._generate_realistic_chain(ticker, underlying_price)
            
        if not chain:
            logger.log("no_options_chain", {"ticker": ticker})
            print(f"  No options chain available for {ticker}")
            return None
        
        print(f"  Retrieved {len(chain)} option contracts")
        
        filtered = []
        for opt in chain:
            if opt.get("type") != direction:
                continue
            
            dte = opt.get("dte", 0)
            if not (min_dte <= dte <= max_dte):
                continue
            
            delta = abs(opt.get("delta", 0))
            if not (delta_min <= delta <= delta_max):
                continue
            
            bid = opt.get("bid", 0)
            ask = opt.get("ask", 0)
            mid = (bid + ask) / 2 if bid > 0 and ask > 0 else opt.get("last", 0)
            
            if mid <= 0 or mid > max_cost:
                continue
            
            cost_per_contract = mid * 100
            if cost_per_contract > budget * 2:
                continue
            
            volume = opt.get("volume", 0)
            oi = opt.get("open_interest", 0)
            if volume < min_volume or oi < min_oi:
                continue
            
            opt["mid_price"] = mid
            opt["score"] = delta * 100 + volume / 100 + oi / 1000 - abs(dte - 14)
            filtered.append(opt)
        
        if not filtered:
            logger.log("no_valid_contracts", {"ticker": ticker, "direction": direction})
            print(f"  No contracts found matching criteria")
            print(f"  Relaxing filters and retrying...")
            
            for opt in chain:
                if opt.get("type") != direction:
                    continue
                dte = opt.get("dte", 0)
                if not (5 <= dte <= 60):
                    continue
                delta = abs(opt.get("delta", 0))
                if not (0.25 <= delta <= 0.70):
                    continue
                bid = opt.get("bid", 0)
                ask = opt.get("ask", 0)
                mid = (bid + ask) / 2 if bid > 0 and ask > 0 else opt.get("last", 0)
                if mid <= 0 or mid > 5.00:
                    continue
                opt["mid_price"] = mid
                opt["score"] = delta * 100 - abs(dte - 14)
                filtered.append(opt)
        
        if not filtered:
            logger.log("no_contracts_after_relax", {"ticker": ticker})
            print(f"  Still no valid contracts found")
            return None
        
        filtered.sort(key=lambda x: x.get("score", 0), reverse=True)
        best = filtered[0]
        
        print(f"\n  Found {len(filtered)} valid contracts, selecting best:")
        
        expiry = best.get("expiry")
        expiry_str = expiry.strftime("%Y-%m-%d") if hasattr(expiry, 'strftime') else str(expiry)[:10]
        
        symbol = best.get("symbol", "")
        if not symbol or "_" in symbol:
            expiry_fmt = expiry.strftime("%y%m%d") if hasattr(expiry, 'strftime') else ""
            opt_type = "C" if direction == "call" else "P"
            strike_int = int(best.get("strike", 0) * 1000)
            symbol = f"{ticker}{expiry_fmt}{opt_type}{strike_int:08d}"
        
        contract = OptionContract(
            symbol=symbol,
            underlying=ticker,
            strike=best.get("strike", 0),
            expiry=expiry_str,
            contract_type=direction,
            bid=best.get("bid", 0),
            ask=best.get("ask", 0),
            mid_price=best.get("mid_price", 0),
            delta=best.get("delta", 0),
            dte=best.get("dte", 0),
            volume=best.get("volume", 0),
            open_interest=best.get("open_interest", 0)
        )
        
        print(f"  Symbol: {contract.symbol}")
        print(f"  Type: {contract.contract_type.upper()}")
        print(f"  Strike: ${contract.strike:.2f}")
        print(f"  Expiry: {contract.expiry} ({contract.dte} DTE)")
        print(f"  Bid: ${contract.bid:.2f} / Ask: ${contract.ask:.2f}")
        print(f"  Mid Price: ${contract.mid_price:.2f}")
        print(f"  Delta: {contract.delta:.2f}")
        print(f"  Volume: {contract.volume:,} / OI: {contract.open_interest:,}")
        
        is_low_liquidity = contract.volume < 10 or contract.open_interest < 50
        if is_low_liquidity:
            print(f"\n  [WARNING] LOW LIQUIDITY CONTRACT - Volume/OI below threshold")
            logger.log("low_liquidity_warning", {
                "symbol": contract.symbol,
                "volume": contract.volume,
                "open_interest": contract.open_interest
            })
        
        logger.log("option_contract_selected", {
            "symbol": contract.symbol,
            "strike": contract.strike,
            "expiry": contract.expiry,
            "type": contract.contract_type,
            "mid_price": contract.mid_price,
            "delta": contract.delta,
            "volume": contract.volume,
            "open_interest": contract.open_interest,
            "candidates": len(filtered),
            "is_synthetic": is_synthetic_chain,
            "is_low_liquidity": is_low_liquidity
        })
        
        return contract, is_synthetic_chain
        
    except Exception as e:
        logger.log("option_selection_error", {"ticker": ticker, "error": str(e)})
        print(f"  Error selecting option: {e}")
        import traceback
        traceback.print_exc()
        return None, False


def execute_trade(alpaca, contract: OptionContract, budget: float, dry_run: bool = False, is_synthetic: bool = False) -> Optional[Dict[str, Any]]:
    """Execute the options trade with force-trade bypass logging"""
    print("\n" + "=" * 60)
    print("STEP 4: EXECUTING TRADE" + (" (DRY RUN)" if dry_run else " [FORCE MODE]"))
    print("=" * 60)
    
    if is_synthetic and not dry_run:
        print(f"\n  [BLOCKED] Cannot execute live trade with synthetic chain data")
        print(f"  Use --dry-run to test, or wait for real market data")
        logger.log("live_trade_blocked_synthetic", {
            "symbol": contract.symbol,
            "reason": "synthetic_chain_data"
        })
        return None
    
    config = load_bots_config()
    optionsbot_config = config.get("optionsbot", {})
    risk_config = optionsbot_config.get("risk", {})
    max_position_size_usd = risk_config.get("max_position_size_usd", 500)
    max_trades_per_day = risk_config.get("max_trades_per_day", 15)
    
    cost_per_contract = contract.mid_price * 100
    qty = max(1, int(budget / cost_per_contract))
    total_cost = qty * cost_per_contract
    
    if total_cost > max_position_size_usd:
        reduced_qty = max(1, int(max_position_size_usd / cost_per_contract))
        print(f"\n  Position size ${total_cost:.2f} exceeds max ${max_position_size_usd:.2f}")
        print(f"  Reducing quantity from {qty} to {reduced_qty} contracts")
        qty = reduced_qty
        total_cost = qty * cost_per_contract
    
    logger.log("force_trade_bypass", {
        "mode": "FORCE_TRADE",
        "bypassed_gates": ["max_trades_per_day", "halt_check", "cooldown", "ml_scoring"],
        "enforced_limits": {
            "max_position_size_usd": max_position_size_usd,
            "max_cost_from_config": True
        },
        "contract": contract.symbol,
        "total_cost": total_cost
    })
    
    print(f"\n  [FORCE TRADE] Bypassing: max_trades_per_day, halt_check, cooldown, ml_scoring")
    print(f"  [FORCE TRADE] Enforcing: max_position_size_usd (${max_position_size_usd})")
    
    print(f"\n  Trade Details:")
    print(f"  Contract: {contract.symbol}")
    print(f"  Direction: BUY {contract.contract_type.upper()}")
    print(f"  Quantity: {qty} contracts")
    print(f"  Estimated Cost: ${total_cost:.2f}")
    print(f"  Budget: ${budget:.2f}")
    
    if dry_run:
        print(f"\n  [DRY RUN] Trade would be executed with above parameters")
        logger.log("dry_run_trade", {
            "symbol": contract.symbol,
            "qty": qty,
            "estimated_cost": total_cost,
            "force_mode": True
        })
        return {"dry_run": True, "symbol": contract.symbol, "qty": qty, "total_cost": total_cost}
    
    try:
        print(f"\n  Placing order...")
        
        order = alpaca.place_options_order(
            symbol=contract.symbol,
            qty=qty,
            side="buy",
            order_type="market",
            time_in_force="day"
        )
        
        if order and isinstance(order, dict):
            order_id = order.get("id", "unknown")
            status = order.get("status", "unknown")
            
            print(f"  Order placed successfully!")
            print(f"  Order ID: {order_id}")
            print(f"  Status: {status}")
            
            logger.log("force_options_trade_executed", {
                "symbol": contract.symbol,
                "qty": qty,
                "order_id": order_id,
                "status": status,
                "estimated_cost": total_cost,
                "force_mode": True,
                "bypassed_gates": ["max_trades_per_day", "halt_check", "cooldown", "ml_scoring"]
            })
            
            return order
        else:
            print(f"  Order failed: {order}")
            logger.log("force_trade_order_failed", {"symbol": contract.symbol, "response": str(order)})
            return None
            
    except Exception as e:
        logger.log("trade_execution_error", {"symbol": contract.symbol, "error": str(e), "force_mode": True})
        print(f"  Trade execution failed: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Force options research and trade")
    parser.add_argument("--budget", type=float, default=100.0, help="Budget for the trade (default: $100)")
    parser.add_argument("--dry-run", action="store_true", help="Research only, don't execute trade")
    parser.add_argument("--ticker", type=str, default=None, help="Force specific ticker instead of scanning")
    args = parser.parse_args()
    
    print("\n" + "=" * 60)
    print("FORCE OPTIONS RESEARCH & TRADE")
    print("=" * 60)
    print(f"Budget: ${args.budget:.2f}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE TRADING'}")
    
    alpaca = get_alpaca_client()
    
    try:
        account = alpaca.get_account()
        equity = float(account.equity)
        print(f"Account Equity: ${equity:,.2f}")
    except Exception as e:
        print(f"Error getting account: {e}")
        return
    
    logger.log("force_options_session_start", {
        "budget": args.budget,
        "dry_run": args.dry_run,
        "equity": equity
    })
    
    if args.ticker:
        analysis = analyze_ticker(alpaca, args.ticker)
        if not analysis:
            print(f"Failed to analyze {args.ticker}")
            return
        top_ticker = analysis
    else:
        universe = get_options_universe()
        print(f"Universe: {', '.join(universe[:5])}...")
        
        ranked = scan_universe(alpaca, universe)
        if not ranked:
            print("No valid tickers found in universe")
            return
        
        top_ticker = ranked[0]
    
    print(f"\nSelected: {top_ticker.ticker} (Score: {top_ticker.composite_score:.1f})")
    
    research = research_with_ai(top_ticker.ticker, top_ticker.price)
    
    contract, is_synthetic = select_option_contract(
        alpaca, 
        top_ticker.ticker, 
        top_ticker.price, 
        research.recommended_direction,
        budget=args.budget
    )
    
    if not contract:
        print("Failed to select option contract")
        return
    
    result = execute_trade(alpaca, contract, args.budget, dry_run=args.dry_run, is_synthetic=is_synthetic)
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Ticker: {top_ticker.ticker}")
    print(f"AI Sentiment: {research.sentiment_label} ({research.sentiment_score:+.2f})")
    print(f"Direction: {research.recommended_direction.upper()}")
    print(f"Contract: {contract.symbol}")
    
    if result and not args.dry_run:
        print(f"Order Status: {result.get('status', 'unknown')}")
        print(f"Order ID: {result.get('id', 'N/A')[:16]}...")
    elif args.dry_run:
        print("Status: DRY RUN - No order placed")
    else:
        print("Status: FAILED")
    
    print("=" * 60)
    
    logger.log("force_options_session_complete", {
        "ticker": top_ticker.ticker,
        "direction": research.recommended_direction,
        "sentiment": research.sentiment_score,
        "contract": contract.symbol,
        "success": result is not None
    })


if __name__ == "__main__":
    main()
