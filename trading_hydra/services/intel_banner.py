"""
Intelligence Banner - Console display for intel modules status

Prints a clear banner showing:
- Which intel modules are enabled
- Last update timestamps
- Current regime modifier
- Cache status
"""

from datetime import datetime, timezone
from typing import Optional

from ..core.config import load_bots_config
from .news_intelligence import get_news_intelligence
from .sentiment_scorer import get_sentiment_scorer
from .smart_money_service import get_smart_money_service
from .macro_intel_service import get_macro_intel_service, RegimeModifier


def print_intel_banner() -> None:
    """Print intelligence system status banner to console"""
    
    try:
        config = load_bots_config()
        intel_config = config.get("intelligence", {})
    except Exception:
        print("[INTEL] Configuration unavailable")
        return
    
    # Header
    print()
    print("=" * 60)
    print("  MARKET INTELLIGENCE SYSTEM STATUS")
    print("=" * 60)
    
    # Global settings
    dry_run = intel_config.get("dry_run", False)
    debug = intel_config.get("debug", {})
    sim_mode = debug.get("simulation_mode", False)
    
    print(f"  Mode: {'DRY_RUN' if dry_run else 'LIVE'} | Simulation: {'ON' if sim_mode else 'OFF'}")
    print("-" * 60)
    
    # News Intelligence
    news_config = intel_config.get("news", {})
    news_enabled = news_config.get("enabled", False)
    exits_enabled = news_config.get("exits", {}).get("enabled", False)
    entry_filter_enabled = news_config.get("entry_filter", {}).get("enabled", False)
    
    news_last_updated = _get_news_last_updated()
    news_status = "ENABLED" if news_enabled else "disabled"
    
    print(f"  [NEWS] {news_status}")
    print(f"         Exits: {'ON' if exits_enabled else 'OFF'} | Entry Filter: {'ON' if entry_filter_enabled else 'OFF'}")
    print(f"         Last Update: {news_last_updated}")
    
    # Smart Money
    sm_config = intel_config.get("smart_money", {})
    sm_enabled = sm_config.get("enabled", False)
    congress_enabled = sm_config.get("congress", {}).get("enabled", False)
    inst_enabled = sm_config.get("institutional", {}).get("enabled", False)
    
    sm_status = "ENABLED" if sm_enabled else "disabled"
    print(f"  [SMART MONEY] {sm_status}")
    print(f"         Congress: {'ON' if congress_enabled else 'OFF'} | 13F: {'ON' if inst_enabled else 'OFF'}")
    
    # Macro Intel
    macro_config = intel_config.get("macro", {})
    macro_enabled = macro_config.get("enabled", False)
    
    regime = _get_current_regime()
    macro_status = "ENABLED" if macro_enabled else "disabled"
    
    print(f"  [MACRO] {macro_status}")
    print(f"         Current Regime: {regime}")
    
    print("-" * 60)
    
    # Active modules summary
    active = []
    if news_enabled:
        active.append("NEWS")
    if sm_enabled:
        active.append("SMART_MONEY")
    if macro_enabled:
        active.append("MACRO")
    
    if active:
        print(f"  Active Modules: {', '.join(active)}")
    else:
        print("  Active Modules: NONE (all disabled)")
    
    print("=" * 60)
    print()


def _get_news_last_updated() -> str:
    """Get last news update timestamp"""
    try:
        intel = get_news_intelligence()
        last_ts = intel.get_last_updated()
        if last_ts:
            age_s = int(__import__("time").time() - last_ts)
            if age_s < 60:
                return f"{age_s}s ago"
            elif age_s < 3600:
                return f"{age_s // 60}m ago"
            else:
                return f"{age_s // 3600}h ago"
        return "never"
    except Exception:
        return "unknown"


def _get_current_regime() -> str:
    """Get current macro regime modifier"""
    try:
        macro = get_macro_intel_service()
        if not macro.is_enabled():
            return "N/A (disabled)"
        regime = macro.get_current_regime_modifier()
        return regime.value
    except Exception:
        return "unknown"


def get_intel_status_dict() -> dict:
    """Get intel status as a dictionary for logging/API"""
    try:
        config = load_bots_config()
        intel_config = config.get("intelligence", {})
        
        return {
            "news": {
                "enabled": intel_config.get("news", {}).get("enabled", False),
                "exits_enabled": intel_config.get("news", {}).get("exits", {}).get("enabled", False),
                "entry_filter_enabled": intel_config.get("news", {}).get("entry_filter", {}).get("enabled", False),
                "last_updated": _get_news_last_updated()
            },
            "smart_money": {
                "enabled": intel_config.get("smart_money", {}).get("enabled", False),
                "congress_enabled": intel_config.get("smart_money", {}).get("congress", {}).get("enabled", False),
                "institutional_enabled": intel_config.get("smart_money", {}).get("institutional", {}).get("enabled", False)
            },
            "macro": {
                "enabled": intel_config.get("macro", {}).get("enabled", False),
                "current_regime": _get_current_regime()
            },
            "dry_run": intel_config.get("dry_run", False),
            "simulation_mode": intel_config.get("debug", {}).get("simulation_mode", False)
        }
    except Exception as e:
        return {"error": str(e)}
