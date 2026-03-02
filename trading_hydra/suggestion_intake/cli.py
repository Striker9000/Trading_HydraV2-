#!/usr/bin/env python3
"""
Suggestion Intake Bot CLI - Turn trade ideas into queued intents.

Usage:
    suggest TSLA target 600 --horizon 5d
    suggest TSLA gapup --route twenty_minute
    suggest SPY 0dte --route zero_dte
    suggest NVDA gapdown
    suggest BTC/USD --route crypto
    
    suggest --list              # List pending intents
    suggest --clear             # Clear all pending intents
"""
import argparse
import sys
import os
from datetime import datetime
from typing import Optional

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from src.trading_hydra.suggestion_intake.tradeintent_schema import (
    TradeIntent,
    AssetType,
    TradeDirection,
    EntryTrigger,
    RouteType,
    ValidationStatus
)
from src.trading_hydra.suggestion_intake.data_provider import (
    create_data_provider,
    get_market_context
)
from src.trading_hydra.suggestion_intake.signal_engine import (
    determine_entry_trigger,
    calculate_exit_plan,
    get_trigger_description
)
from src.trading_hydra.suggestion_intake.options_selector import (
    select_best_option,
    GreeksFilter,
    LiquidityFilter
)
from src.trading_hydra.suggestion_intake.risk_engine import (
    validate_trade_idea,
    calculate_position_size
)
from src.trading_hydra.suggestion_intake.queue_writer import (
    write_trade_intent,
    list_pending_intents,
    format_trade_brief,
    generate_intent_id,
    delete_intent
)


def load_config() -> dict:
    """Load suggestion intake configuration."""
    config_path = os.path.join(
        os.path.dirname(__file__), 
        "config.yaml"
    )
    
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            return yaml.safe_load(f)
    
    return {"suggestion_intake": {}}


def parse_suggestion_type(args: list) -> tuple:
    """
    Parse suggestion type from positional arguments.
    
    Returns (suggestion_type, target_price)
    """
    suggestion_type = None
    target_price = None
    
    for arg in args:
        arg_lower = arg.lower()
        if arg_lower in ["gapup", "gap_up", "gap-up"]:
            suggestion_type = "gapup"
        elif arg_lower in ["gapdown", "gap_down", "gap-down"]:
            suggestion_type = "gapdown"
        elif arg_lower in ["0dte", "zero_dte", "zerodte"]:
            suggestion_type = "0dte"
        elif arg_lower == "target":
            continue
        else:
            try:
                target_price = float(arg)
            except ValueError:
                pass
    
    return suggestion_type, target_price


def determine_asset_type(symbol: str, route: RouteType) -> AssetType:
    """Determine asset type from symbol and route."""
    if "/" in symbol or symbol.endswith("USD"):
        return AssetType.CRYPTO
    
    if route == RouteType.ZERO_DTE:
        return AssetType.OPTIONS
    
    if route == RouteType.CRYPTO:
        return AssetType.CRYPTO
    
    return AssetType.EQUITY


def determine_route(route_str: Optional[str], suggestion_type: Optional[str], symbol: str) -> RouteType:
    """Determine route from CLI input."""
    if route_str:
        route_map = {
            "twenty_minute": RouteType.TWENTY_MINUTE,
            "twentyminute": RouteType.TWENTY_MINUTE,
            "20min": RouteType.TWENTY_MINUTE,
            "zero_dte": RouteType.ZERO_DTE,
            "zerodte": RouteType.ZERO_DTE,
            "0dte": RouteType.ZERO_DTE,
            "swing": RouteType.SWING,
            "crypto": RouteType.CRYPTO,
            "auto": RouteType.AUTO
        }
        return route_map.get(route_str.lower(), RouteType.AUTO)
    
    if suggestion_type == "0dte":
        return RouteType.ZERO_DTE
    
    if suggestion_type in ["gapup", "gapdown"]:
        return RouteType.TWENTY_MINUTE
    
    if "/" in symbol or symbol.endswith("USD"):
        return RouteType.CRYPTO
    
    return RouteType.SWING


def process_suggestion(
    symbol: str,
    args: list,
    route: Optional[str],
    horizon: Optional[str],
    dry_run: bool = False
) -> Optional[TradeIntent]:
    """
    Process a trade suggestion and create TradeIntent.
    
    Main workflow:
    1. Parse suggestion type and target
    2. Fetch market data
    3. Determine entry trigger and direction
    4. Select options contract if applicable
    5. Calculate exit plan
    6. Validate trade idea
    7. Write to queue
    """
    config = load_config()
    intake_config = config.get("suggestion_intake", {})
    
    suggestion_type, target_price = parse_suggestion_type(args)
    
    route_type = determine_route(route, suggestion_type, symbol)
    asset_type = determine_asset_type(symbol, route_type)
    
    route_config = intake_config.get("routes", {}).get(route_type.value, {})
    default_horizon = route_config.get("default_horizon", "1d")
    horizon = horizon or default_horizon
    
    print(f"Processing suggestion: {symbol}")
    print(f"  Type: {suggestion_type or 'auto'}")
    print(f"  Route: {route_type.value}")
    print(f"  Horizon: {horizon}")
    if target_price:
        print(f"  Target: ${target_price:.2f}")
    print()
    
    try:
        provider = create_data_provider()
        market_context = get_market_context(provider, symbol)
    except Exception as e:
        print(f"Error fetching market data: {e}")
        return None
    
    entry_trigger, trigger_params, direction = determine_entry_trigger(
        route_type, market_context, suggestion_type, target_price
    )
    
    options_contract = None
    route_asset_types = route_config.get("asset_types", ["equity"])
    
    if "options" in route_asset_types and asset_type != AssetType.CRYPTO:
        print("Searching for suitable options contract...")
        
        dte_range = route_config.get("options_dte_range", [1, 5])
        greeks_config = route_config.get("greeks") or intake_config.get("options", {}).get("default_greeks", {})
        liquidity_config = intake_config.get("options", {}).get("liquidity", {})
        spread_config = intake_config.get("options", {}).get("spread_limits", {})
        
        greeks_filter = GreeksFilter(
            delta_min=greeks_config.get("delta_min", 0.30),
            delta_max=greeks_config.get("delta_max", 0.70),
            theta_max_pct=greeks_config.get("theta_max_pct", -5.0),
            gamma_min=greeks_config.get("gamma_min", 0.01)
        )
        
        liquidity_filter = LiquidityFilter(
            max_spread_dollars=spread_config.get("max_spread_dollars", 0.05),
            min_spread_dollars=spread_config.get("min_spread_dollars", 0.02),
            max_spread_pct=spread_config.get("max_spread_pct", 5.0),
            min_volume=liquidity_config.get("min_volume", 100),
            min_open_interest=liquidity_config.get("min_open_interest", 500)
        )
        
        try:
            options_contract = select_best_option(
                provider=provider,
                underlying=symbol,
                underlying_price=market_context.current_price,
                direction=direction,
                dte_range=tuple(dte_range),
                greeks_filter=greeks_filter,
                liquidity_filter=liquidity_filter
            )
            
            if options_contract:
                print(f"  Found: {options_contract.symbol}")
                asset_type = AssetType.OPTIONS
            else:
                print("  No suitable options found, using equity")
        except Exception as e:
            print(f"  Options search failed: {e}")
    
    exit_plan = calculate_exit_plan(
        market_context, direction, target_price, intake_config
    )
    
    risk_config = intake_config.get("risk", {})
    max_risk_usd = risk_config.get("max_loss_per_trade_usd", 100.0)
    
    position_size_pct = calculate_position_size(
        market_context,
        exit_plan.stop_loss_pct,
        max_risk_usd,
        account_equity=10000.0
    )
    
    validation = validate_trade_idea(
        market_context=market_context,
        direction=direction,
        route=route_type,
        options_contract=options_contract,
        position_size_pct=position_size_pct,
        stop_loss_pct=exit_plan.stop_loss_pct,
        account_equity=10000.0,
        risk_config=risk_config
    )
    
    intent = TradeIntent(
        id=generate_intent_id(symbol),
        created_at=datetime.now(),
        symbol=symbol,
        direction=direction,
        asset_type=asset_type,
        route=route_type,
        horizon=horizon,
        target_price=target_price,
        entry_trigger=entry_trigger,
        entry_trigger_params=trigger_params,
        market_context=market_context,
        exit_plan=exit_plan,
        options_contract=options_contract,
        position_size_pct=position_size_pct,
        max_risk_usd=max_risk_usd,
        validation=validation,
        user_notes=None,
        source="suggestion_intake"
    )
    
    print()
    print(format_trade_brief(intent))
    
    if not dry_run:
        if validation.status == ValidationStatus.APPROVED:
            filepath = write_trade_intent(intent)
            print(f"\n✅ Intent queued: {filepath}")
        else:
            print(f"\n❌ Intent REJECTED - not queued")
            print(f"   Reason: {validation.rejection_reason}")
    else:
        print(f"\n[DRY RUN] Would queue intent: {intent.id}")
    
    return intent


def list_queue():
    """List all pending trade intents."""
    intents = list_pending_intents()
    
    if not intents:
        print("No pending trade intents in queue.")
        return
    
    print(f"Pending Trade Intents ({len(intents)}):")
    print("-" * 60)
    
    for intent in intents:
        status_icon = "✅" if intent.validation.status == ValidationStatus.APPROVED else "❌"
        print(f"{status_icon} {intent.id}")
        print(f"   {intent.symbol} {intent.direction.value.upper()} via {intent.route.value}")
        print(f"   Entry: {intent.entry_trigger.value}")
        print(f"   Created: {intent.created_at.strftime('%Y-%m-%d %H:%M')}")
        print()


def clear_queue():
    """Clear all pending intents."""
    intents = list_pending_intents()
    
    if not intents:
        print("Queue is already empty.")
        return
    
    for intent in intents:
        delete_intent(intent.id)
    
    print(f"Cleared {len(intents)} intent(s) from queue.")


def main():
    parser = argparse.ArgumentParser(
        description="Suggestion Intake Bot - Queue trade ideas for execution",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  suggest TSLA target 600 --horizon 5d
  suggest TSLA gapup --route twenty_minute  
  suggest SPY 0dte --route zero_dte
  suggest NVDA gapdown
  suggest BTC/USD --route crypto
  
  suggest --list              List pending intents
  suggest --clear             Clear all pending intents
"""
    )
    
    parser.add_argument("symbol", nargs="?", help="Trading symbol (e.g., TSLA, SPY, BTC/USD)")
    parser.add_argument("args", nargs="*", help="Suggestion type and/or target (e.g., gapup, target 600)")
    parser.add_argument("--route", "-r", type=str, 
                        choices=["twenty_minute", "zero_dte", "swing", "crypto", "auto"],
                        help="Trading route/bot")
    parser.add_argument("--horizon", "-H", type=str, help="Trade horizon (e.g., 1d, 5d)")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Don't write to queue")
    parser.add_argument("--list", "-l", action="store_true", help="List pending intents")
    parser.add_argument("--clear", action="store_true", help="Clear all pending intents")
    
    args = parser.parse_args()
    
    if args.list:
        list_queue()
        return
    
    if args.clear:
        clear_queue()
        return
    
    if not args.symbol:
        parser.print_help()
        return
    
    process_suggestion(
        symbol=args.symbol.upper(),
        args=args.args,
        route=args.route,
        horizon=args.horizon,
        dry_run=args.dry_run
    )


if __name__ == "__main__":
    main()
