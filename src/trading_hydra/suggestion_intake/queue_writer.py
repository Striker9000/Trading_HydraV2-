"""
Queue Writer - Write TradeIntent to queue directory.

Outputs standardized JSON files for bot consumption.
"""
import os
import json
from datetime import datetime
from typing import Optional

from .tradeintent_schema import TradeIntent, ValidationStatus


QUEUE_DIR = "./queue"


def ensure_queue_dir():
    """Ensure queue directory exists."""
    os.makedirs(QUEUE_DIR, exist_ok=True)


def generate_intent_id(symbol: str) -> str:
    """Generate unique ID for trade intent."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    symbol_clean = symbol.replace("/", "_").replace(" ", "_")
    return f"intent_{symbol_clean}_{timestamp}"


def write_trade_intent(
    intent: TradeIntent,
    queue_dir: Optional[str] = None
) -> str:
    """
    Write TradeIntent to queue as JSON file.
    
    Args:
        intent: The TradeIntent to write
        queue_dir: Optional custom queue directory
        
    Returns:
        Path to the written file
    """
    target_dir = queue_dir or QUEUE_DIR
    os.makedirs(target_dir, exist_ok=True)
    
    filename = f"{intent.id}.json"
    filepath = os.path.join(target_dir, filename)
    
    with open(filepath, "w") as f:
        f.write(intent.to_json(indent=2))
    
    return filepath


def read_trade_intent(filepath: str) -> TradeIntent:
    """Read TradeIntent from JSON file."""
    with open(filepath, "r") as f:
        data = json.load(f)
    return TradeIntent.from_dict(data)


def list_pending_intents(queue_dir: Optional[str] = None) -> list:
    """List all pending trade intents in queue."""
    target_dir = queue_dir or QUEUE_DIR
    
    if not os.path.exists(target_dir):
        return []
    
    intents = []
    for filename in os.listdir(target_dir):
        if filename.endswith(".json") and filename.startswith("intent_"):
            filepath = os.path.join(target_dir, filename)
            try:
                intent = read_trade_intent(filepath)
                intents.append(intent)
            except Exception as e:
                print(f"Error reading {filepath}: {e}")
    
    intents.sort(key=lambda x: x.created_at, reverse=True)
    return intents


def archive_intent(intent_id: str, queue_dir: Optional[str] = None) -> bool:
    """Move processed intent to archive."""
    target_dir = queue_dir or QUEUE_DIR
    archive_dir = os.path.join(target_dir, "archive")
    os.makedirs(archive_dir, exist_ok=True)
    
    source = os.path.join(target_dir, f"{intent_id}.json")
    dest = os.path.join(archive_dir, f"{intent_id}.json")
    
    if os.path.exists(source):
        os.rename(source, dest)
        return True
    return False


def delete_intent(intent_id: str, queue_dir: Optional[str] = None) -> bool:
    """Delete a trade intent from queue."""
    target_dir = queue_dir or QUEUE_DIR
    filepath = os.path.join(target_dir, f"{intent_id}.json")
    
    if os.path.exists(filepath):
        os.remove(filepath)
        return True
    return False


def format_trade_brief(intent: TradeIntent) -> str:
    """
    Format human-readable trade brief for console output.
    
    Returns formatted string with all trade details.
    """
    lines = []
    lines.append("=" * 60)
    lines.append("TRADE BRIEF")
    lines.append("=" * 60)
    lines.append("")
    
    lines.append(f"Symbol:      {intent.symbol}")
    lines.append(f"Direction:   {intent.direction.value.upper()}")
    lines.append(f"Asset Type:  {intent.asset_type.value}")
    lines.append(f"Route:       {intent.route.value}")
    lines.append(f"Horizon:     {intent.horizon}")
    if intent.target_price:
        lines.append(f"Target:      ${intent.target_price:.2f}")
    lines.append("")
    
    lines.append("-" * 60)
    lines.append("MARKET CONTEXT")
    lines.append("-" * 60)
    ctx = intent.market_context
    lines.append(f"Current Price:   ${ctx.current_price:.2f}")
    lines.append(f"Prev Close:      ${ctx.prev_close:.2f}")
    lines.append(f"Gap:             {ctx.gap_pct:+.2f}%")
    if ctx.vwap:
        lines.append(f"VWAP:            ${ctx.vwap:.2f}")
    lines.append(f"ATR (14):        ${ctx.atr_14:.2f} ({ctx.atr_pct:.2f}%)")
    lines.append(f"Relative Volume: {ctx.relative_volume:.2f}x")
    lines.append(f"Trend Bias:      {ctx.trend_bias}")
    lines.append("")
    
    lines.append("-" * 60)
    lines.append("ENTRY PLAN")
    lines.append("-" * 60)
    lines.append(f"Trigger: {intent.entry_trigger.value}")
    for key, val in intent.entry_trigger_params.items():
        lines.append(f"  {key}: {val}")
    lines.append("")
    
    lines.append("-" * 60)
    lines.append("EXIT PLAN")
    lines.append("-" * 60)
    exit = intent.exit_plan
    lines.append(f"Stop Loss:     ${exit.stop_loss_price:.2f} ({exit.stop_loss_pct:.1f}%)")
    lines.append(f"Trailing:      Activates at +{exit.trailing_stop_activation_pct}%, trails {exit.trailing_stop_pct}%")
    lines.append(f"Reversal Stop: Exits if drops {exit.reversal_sense_pct}% from high")
    lines.append(f"TP1:           ${exit.tp1_price:.2f} (+{exit.tp1_pct:.1f}%) - {exit.tp1_size_pct}%")
    if exit.tp2_price:
        lines.append(f"TP2:           ${exit.tp2_price:.2f} (+{exit.tp2_pct:.1f}%) - {exit.tp2_size_pct}%")
    if exit.tp3_price:
        lines.append(f"TP3:           ${exit.tp3_price:.2f} (+{exit.tp3_pct:.1f}%) - {exit.tp3_size_pct}%")
    lines.append("")
    
    if intent.options_contract:
        lines.append("-" * 60)
        lines.append("OPTIONS CONTRACT")
        lines.append("-" * 60)
        opt = intent.options_contract
        lines.append(f"Contract:    {opt.symbol}")
        lines.append(f"Strike:      ${opt.strike:.2f} {opt.option_type.upper()}")
        lines.append(f"Expiration:  {opt.expiration}")
        lines.append(f"Bid/Ask:     ${opt.bid:.2f} / ${opt.ask:.2f} (spread: ${opt.spread:.2f})")
        lines.append(f"Volume/OI:   {opt.volume} / {opt.open_interest}")
        lines.append(f"Greeks:      Δ={opt.delta:.2f}  Γ={opt.gamma:.3f}  Θ={opt.theta:.3f}  V={opt.vega:.3f}")
        lines.append(f"IV:          {opt.iv*100:.1f}%")
        lines.append(f"⚠️  Expected 1-day theta decay: ${opt.expected_theta_decay_1d:.2f}")
        lines.append("")
    
    lines.append("-" * 60)
    lines.append("RISK & SIZING")
    lines.append("-" * 60)
    lines.append(f"Position Size: {intent.position_size_pct}% of portfolio")
    lines.append(f"Max Risk:      ${intent.max_risk_usd:.2f}")
    lines.append("")
    
    lines.append("-" * 60)
    lines.append("VALIDATION")
    lines.append("-" * 60)
    val = intent.validation
    status_icon = "✅" if val.status == ValidationStatus.APPROVED else "❌"
    lines.append(f"Status: {status_icon} {val.status.value.upper()}")
    
    if val.passed_checks:
        lines.append("Passed:")
        for check in val.passed_checks[:5]:
            lines.append(f"  ✓ {check}")
    
    if val.failed_checks:
        lines.append("Failed:")
        for check in val.failed_checks:
            lines.append(f"  ✗ {check}")
    
    if val.warnings:
        lines.append("Warnings:")
        for warn in val.warnings:
            lines.append(f"  ⚠ {warn}")
    
    if val.rejection_reason:
        lines.append(f"\nRejection: {val.rejection_reason}")
    
    lines.append("")
    lines.append("=" * 60)
    lines.append(f"Intent ID: {intent.id}")
    lines.append(f"Created:   {intent.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 60)
    
    return "\n".join(lines)
