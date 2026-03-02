"""
Signal Engine - Entry trigger logic for trade suggestions.

Determines entry triggers based on route and market context.
Supports VWAP reclaim/lose, ORB break, gap plays, and more.
"""
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timedelta

from .tradeintent_schema import (
    EntryTrigger,
    TradeDirection,
    RouteType,
    MarketContext,
    ExitPlan
)


def determine_entry_trigger(
    route: RouteType,
    market_context: MarketContext,
    suggestion_type: Optional[str] = None,
    target_price: Optional[float] = None
) -> Tuple[EntryTrigger, Dict[str, Any], TradeDirection]:
    """
    Determine the appropriate entry trigger based on route and context.
    
    Args:
        route: The trading route (twenty_minute, zero_dte, swing, crypto)
        market_context: Current market data
        suggestion_type: User-specified type (gapup, gapdown, target, etc.)
        target_price: User-specified target price
        
    Returns:
        Tuple of (EntryTrigger, trigger_params, TradeDirection)
    """
    trigger = EntryTrigger.IMMEDIATE
    params: Dict[str, Any] = {}
    direction = TradeDirection.LONG
    
    if suggestion_type == "gapup":
        trigger = EntryTrigger.GAP_UP
        direction = TradeDirection.LONG
        params = {
            "gap_pct": market_context.gap_pct,
            "entry_condition": "pullback_to_vwap",
            "confirmation": "hold_above_premarket_low"
        }
    
    elif suggestion_type == "gapdown":
        trigger = EntryTrigger.GAP_DOWN
        direction = TradeDirection.SHORT
        params = {
            "gap_pct": market_context.gap_pct,
            "entry_condition": "rally_to_vwap",
            "confirmation": "fail_at_premarket_high"
        }
    
    elif suggestion_type == "0dte":
        if market_context.trend_bias == "bullish":
            trigger = EntryTrigger.VWAP_RECLAIM
            direction = TradeDirection.LONG
        else:
            trigger = EntryTrigger.VWAP_LOSE
            direction = TradeDirection.SHORT
        params = {
            "confirmation_bars": 2,
            "orb_minutes": 15
        }
    
    elif target_price is not None:
        current = market_context.current_price
        if target_price > current:
            direction = TradeDirection.LONG
            if market_context.trend_bias == "bullish":
                trigger = EntryTrigger.SUPPORT_BOUNCE
            else:
                trigger = EntryTrigger.VWAP_RECLAIM
        else:
            direction = TradeDirection.SHORT
            if market_context.trend_bias == "bearish":
                trigger = EntryTrigger.RESISTANCE_BREAK
            else:
                trigger = EntryTrigger.VWAP_LOSE
        
        params = {
            "target_price": target_price,
            "current_price": current,
            "expected_move_pct": ((target_price - current) / current) * 100
        }
    
    else:
        if route == RouteType.TWENTY_MINUTE:
            if abs(market_context.gap_pct) >= 1.0:
                if market_context.gap_pct > 0:
                    trigger = EntryTrigger.GAP_UP
                    direction = TradeDirection.LONG
                else:
                    trigger = EntryTrigger.GAP_DOWN
                    direction = TradeDirection.SHORT
            else:
                trigger = EntryTrigger.ORB_BREAK
                direction = TradeDirection.LONG if market_context.trend_bias != "bearish" else TradeDirection.SHORT
            
            params = {
                "orb_minutes": 15,
                "gap_pct": market_context.gap_pct,
                "confirmation_volume_mult": 1.5
            }
        
        elif route == RouteType.ZERO_DTE:
            trigger = EntryTrigger.VWAP_RECLAIM if market_context.trend_bias != "bearish" else EntryTrigger.VWAP_LOSE
            direction = TradeDirection.LONG if market_context.trend_bias != "bearish" else TradeDirection.SHORT
            params = {
                "confirmation_bars": 2,
                "orb_minutes": 15
            }
        
        elif route == RouteType.SWING:
            if market_context.sma_9 and market_context.sma_21:
                if market_context.sma_9 > market_context.sma_21:
                    trigger = EntryTrigger.SMA_CROSSOVER
                    direction = TradeDirection.LONG
                else:
                    trigger = EntryTrigger.SMA_CROSSOVER
                    direction = TradeDirection.SHORT
            else:
                trigger = EntryTrigger.SUPPORT_BOUNCE
                direction = TradeDirection.LONG
            
            params = {
                "sma_9": market_context.sma_9,
                "sma_21": market_context.sma_21,
                "trend_bias": market_context.trend_bias
            }
        
        elif route == RouteType.CRYPTO:
            if market_context.gap_pct < -2.0:
                trigger = EntryTrigger.SUPPORT_BOUNCE
                direction = TradeDirection.LONG
                params = {"gap_fill_target": True}
            elif market_context.vwap and market_context.current_price > market_context.vwap:
                trigger = EntryTrigger.VWAP_RECLAIM
                direction = TradeDirection.LONG
            else:
                trigger = EntryTrigger.VWAP_LOSE
                direction = TradeDirection.SHORT
            
            params["twenty_four_seven"] = True
        
        else:
            trigger = EntryTrigger.IMMEDIATE
            direction = TradeDirection.LONG if market_context.trend_bias != "bearish" else TradeDirection.SHORT
    
    return trigger, params, direction


def calculate_exit_plan(
    market_context: MarketContext,
    direction: TradeDirection,
    target_price: Optional[float],
    config: Dict[str, Any]
) -> ExitPlan:
    """
    Calculate exit plan based on market context and config.
    
    Uses ATR-based stops and configurable take-profit levels.
    """
    current = market_context.current_price
    atr = market_context.atr_14
    
    exits_config = config.get("exits", {})
    
    stop_loss_atr_mult = exits_config.get("stop_loss_atr_mult", 1.5)
    trailing_activation = exits_config.get("trailing_stop_activation_pct", 1.5)
    trailing_stop = exits_config.get("trailing_stop_pct", 0.5)
    reversal_sense = exits_config.get("reversal_sense_pct", 1.5)
    
    tp_config = exits_config.get("take_profit", {})
    tp1_pct = tp_config.get("tp1_pct", 1.0)
    tp1_size = tp_config.get("tp1_size_pct", 33)
    tp2_pct = tp_config.get("tp2_pct", 2.0)
    tp2_size = tp_config.get("tp2_size_pct", 33)
    tp3_pct = tp_config.get("tp3_pct", 3.0)
    tp3_size = tp_config.get("tp3_size_pct", 34)
    
    if atr > 0:
        stop_distance = atr * stop_loss_atr_mult
        stop_loss_pct = (stop_distance / current) * 100
    else:
        stop_loss_pct = 2.0
        stop_distance = current * 0.02
    
    if direction == TradeDirection.LONG:
        stop_loss_price = current - stop_distance
        tp1_price = current * (1 + tp1_pct / 100)
        tp2_price = current * (1 + tp2_pct / 100)
        tp3_price = current * (1 + tp3_pct / 100)
    else:
        stop_loss_price = current + stop_distance
        tp1_price = current * (1 - tp1_pct / 100)
        tp2_price = current * (1 - tp2_pct / 100)
        tp3_price = current * (1 - tp3_pct / 100)
    
    if target_price:
        target_distance_pct = abs((target_price - current) / current) * 100
        if target_distance_pct > tp3_pct:
            tp3_price = target_price
            tp3_pct = target_distance_pct
    
    return ExitPlan(
        stop_loss_price=round(stop_loss_price, 2),
        stop_loss_pct=round(stop_loss_pct, 2),
        trailing_stop_activation_pct=trailing_activation,
        trailing_stop_pct=trailing_stop,
        reversal_sense_pct=reversal_sense,
        tp1_price=round(tp1_price, 2),
        tp1_pct=tp1_pct,
        tp1_size_pct=tp1_size,
        tp2_price=round(tp2_price, 2),
        tp2_pct=tp2_pct,
        tp2_size_pct=tp2_size,
        tp3_price=round(tp3_price, 2),
        tp3_pct=tp3_pct,
        tp3_size_pct=tp3_size,
        max_hold_hours=None
    )


def get_trigger_description(trigger: EntryTrigger, params: Dict[str, Any]) -> str:
    """Get human-readable description of entry trigger."""
    descriptions = {
        EntryTrigger.VWAP_RECLAIM: "Enter when price reclaims VWAP from below",
        EntryTrigger.VWAP_LOSE: "Enter when price loses VWAP from above",
        EntryTrigger.ORB_BREAK: f"Enter on opening range breakout ({params.get('orb_minutes', 15)} min)",
        EntryTrigger.GAP_UP: f"Gap up {params.get('gap_pct', 0):.1f}% - buy pullback to VWAP",
        EntryTrigger.GAP_DOWN: f"Gap down {abs(params.get('gap_pct', 0)):.1f}% - short rally to VWAP",
        EntryTrigger.PREMARKET_HIGH_BREAK: "Enter on break above pre-market high",
        EntryTrigger.PREMARKET_LOW_BREAK: "Enter on break below pre-market low",
        EntryTrigger.SUPPORT_BOUNCE: "Enter on bounce from support level",
        EntryTrigger.RESISTANCE_BREAK: "Enter on break above resistance",
        EntryTrigger.SMA_CROSSOVER: "Enter on SMA crossover confirmation",
        EntryTrigger.IMMEDIATE: "Enter at market"
    }
    return descriptions.get(trigger, "Custom entry")
