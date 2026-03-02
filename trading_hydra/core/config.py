"""Configuration loader for YAML files"""
import os
import json
import yaml
from typing import Any, Dict, Optional, List
from enum import Enum
from datetime import datetime
from pathlib import Path

_settings_cache: Optional[Dict[str, Any]] = None
_bots_config_cache: Optional[Dict[str, Any]] = None
_small_account_mode: bool = False
_current_account_mode: str = "standard"  # micro, small, or standard

# Account size thresholds for automatic mode detection
# 3-tier system: Micro -> Small -> Standard
# These are defaults; overridden at module init after _load_account_modes() runs.
MICRO_ACCOUNT_THRESHOLD: float = 1000.0   # Under $1k = Micro (ultra aggressive)
SMALL_ACCOUNT_THRESHOLD: float = 10000.0  # $1k-$10k = Small (medium aggressive)
# Above SMALL_ACCOUNT_THRESHOLD = Standard (conservative)

# FALLBACK risk parameters for each account mode.
# Primary source: config/modes/account_modes.yaml  (modes section)
# This dict is only used when the YAML file is missing or doesn't contain
# the requested mode.  Edit the YAML to change values in production.
ACCOUNT_MODE_PARAMS: Dict[str, Dict[str, Any]] = {
    "micro": {
        # Ultra aggressive - goal: grow to $1,000
        "daily_risk_pct": 8.0,           # 8% daily risk (was 2%)
        "position_size_multiplier": 2.5,  # 2.5x normal sizing
        "stop_loss_multiplier": 1.5,      # Wider stops to avoid shakeouts
        "take_profit_multiplier": 1.2,    # Slightly tighter TP for faster wins
        "max_concurrent_multiplier": 0.5, # Fewer positions, bigger each
        "trailing_activation_pct": 0.3,   # Activate trailing earlier
        "focus_crypto": True,             # Crypto only (fractional, 24/7)
        "disable_options": True,          # Options require too much buying power
        "disable_momentum": True,         # Stocks too expensive per share
        "description": "MICRO MODE - Ultra Aggressive (Goal: $1,000)",
    },
    "small": {
        # Medium aggressive - goal: grow to $10,000
        "daily_risk_pct": 4.0,            # 4% daily risk
        "position_size_multiplier": 1.5,  # 1.5x normal sizing
        "stop_loss_multiplier": 1.2,      # Slightly wider stops
        "take_profit_multiplier": 1.0,    # Normal TP
        "max_concurrent_multiplier": 0.75, # Moderate positions
        "trailing_activation_pct": 0.4,   # Normal trailing activation
        "focus_crypto": True,             # Still crypto-focused
        "disable_options": False,         # Options allowed if buying power available
        "disable_momentum": True,         # Still avoid stocks (PDT concern)
        "description": "SMALL MODE - Medium Aggressive (Goal: $10,000)",
    },
    "standard": {
        # Conservative - preserve and compound
        "daily_risk_pct": 2.0,            # 2% daily risk (standard)
        "position_size_multiplier": 1.0,  # Normal sizing
        "stop_loss_multiplier": 1.0,      # Normal stops
        "take_profit_multiplier": 1.0,    # Normal TP
        "max_concurrent_multiplier": 1.0, # Normal positions
        "trailing_activation_pct": 0.5,   # Normal trailing
        "focus_crypto": False,            # Full bot diversity
        "disable_options": False,         # All bots enabled
        "disable_momentum": False,
        "description": "STANDARD MODE - Conservative (Preserve & Compound)",
    },
}


def get_account_mode() -> str:
    """Get the current account mode: micro, small, or standard."""
    return _current_account_mode


def get_account_mode_params() -> Dict[str, Any]:
    """Get the risk parameters for the current account mode.

    Checks config/modes/account_modes.yaml first (primary source).
    Falls back to hardcoded ACCOUNT_MODE_PARAMS dict if YAML is missing
    or doesn't contain the requested mode.
    """
    yaml_modes = _account_modes_yaml.get("modes", {})
    if _current_account_mode in yaml_modes:
        return yaml_modes[_current_account_mode]
    return ACCOUNT_MODE_PARAMS.get(_current_account_mode, ACCOUNT_MODE_PARAMS["standard"])


def auto_detect_account_mode(equity: float) -> bool:
    """
    Automatically detect and set account mode based on equity.
    
    3-tier system:
    - Micro (<$1,000): Ultra aggressive, crypto-only, 8% daily risk
    - Small ($1,000-$10,000): Medium aggressive, mostly crypto, 4% daily risk
    - Standard (>$10,000): Conservative, all bots, 2% daily risk
    
    Args:
        equity: Current account equity in dollars
        
    Returns:
        True if small/micro account mode was enabled, False for standard
    """
    global _current_account_mode
    
    if equity < MICRO_ACCOUNT_THRESHOLD:
        _current_account_mode = "micro"
        enable_small_account_mode(True)
        return True
    elif equity < SMALL_ACCOUNT_THRESHOLD:
        _current_account_mode = "small"
        enable_small_account_mode(True)
        return True
    else:
        _current_account_mode = "standard"
        enable_small_account_mode(False)
        return False


def enable_small_account_mode(enabled: bool = True) -> None:
    """
    Enable or disable small account mode.
    
    When enabled, loads config/small_account_mode.yaml and merges it
    with the base bots.yaml configuration, overriding values.
    
    Args:
        enabled: True to enable small account mode
    """
    global _small_account_mode, _bots_config_cache
    _small_account_mode = enabled
    _bots_config_cache = None  # Force reload on next access


def is_small_account_mode() -> bool:
    """Check if small account mode is enabled (micro or small)."""
    return _small_account_mode


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep merge two dictionaries. Override values take precedence.
    
    Args:
        base: Base dictionary
        override: Override dictionary (values take precedence)
        
    Returns:
        Merged dictionary
    """
    result = base.copy()
    
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    
    return result


def _get_config_root() -> str:
    """Get the config directory root path."""
    possible_roots = [
        os.path.join(os.getcwd(), "config"),
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "config"),
        "/home/runner/workspace/config",
    ]
    for root in possible_roots:
        if os.path.isdir(root):
            return os.path.abspath(root)
    return possible_roots[0]


def _find_config_path(filename: str, subdir: Optional[str] = None) -> str:
    """
    Find a config file path.
    
    Args:
        filename: The config file name (e.g., "settings.yaml")
        subdir: Optional subdirectory (e.g., "modes" for mode overrides)
    
    Returns:
        Full path to the config file
    """
    config_root = _get_config_root()
    
    if subdir:
        possible_paths = [
            os.path.join(config_root, subdir, filename),
            os.path.join(config_root, filename),  # Fallback to root
        ]
    else:
        possible_paths = [
            os.path.join(config_root, filename),
        ]
    
    for p in possible_paths:
        if os.path.exists(p):
            return p
    
    raise FileNotFoundError(f"Config file not found: {filename}. Searched: {possible_paths}")


def _load_account_modes() -> Dict[str, Any]:
    """Load account mode config from YAML with hardcoded fallbacks.

    Primary source: config/modes/account_modes.yaml
    If the YAML file is missing or malformed, returns empty dict
    and callers fall back to the hardcoded ACCOUNT_MODE_PARAMS below.
    """
    try:
        config_path = _find_config_path("account_modes.yaml", subdir="modes")
        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    except (FileNotFoundError, yaml.YAMLError):
        return {}


_account_modes_yaml = _load_account_modes()

# Override thresholds from YAML (primary source: config/modes/account_modes.yaml)
MICRO_ACCOUNT_THRESHOLD = _account_modes_yaml.get("thresholds", {}).get("micro_account", MICRO_ACCOUNT_THRESHOLD)
SMALL_ACCOUNT_THRESHOLD = _account_modes_yaml.get("thresholds", {}).get("small_account", SMALL_ACCOUNT_THRESHOLD)


def load_settings(config_path: Optional[str] = None) -> Dict[str, Any]:
    global _settings_cache
    if _settings_cache is not None:
        return _settings_cache
    
    try:
        file_path = config_path or _find_config_path("settings.yaml")
        with open(file_path, "r") as f:
            _settings_cache = yaml.safe_load(f)
        
        # Validate required settings structure
        if not isinstance(_settings_cache, dict):
            raise ValueError("Settings file must contain a dictionary")
        
        # Ensure runner config exists with defaults
        if "runner" not in _settings_cache:
            _settings_cache["runner"] = {}
        if "loop_interval_seconds" not in _settings_cache["runner"]:
            _settings_cache["runner"]["loop_interval_seconds"] = 5

        # Merge generated tier override for settings-level params
        try:
            tier_path = _find_config_path("tier_override.yaml", subdir="generated")
            with open(tier_path, "r") as f:
                tier_config = yaml.safe_load(f)
            if tier_config:
                for settings_key in ["institutional_sizing", "risk", "dynamic_budget"]:
                    if settings_key in tier_config:
                        if settings_key in _settings_cache and isinstance(_settings_cache[settings_key], dict):
                            _settings_cache[settings_key] = _deep_merge(
                                _settings_cache[settings_key], tier_config[settings_key]
                            )
                        else:
                            _settings_cache[settings_key] = tier_config[settings_key]
        except (FileNotFoundError, yaml.YAMLError):
            pass

    except FileNotFoundError as e:
        raise FileNotFoundError(f"Configuration error: {e}")
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in settings file: {e}")
    
    return _settings_cache


def load_bots_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    global _bots_config_cache
    if _bots_config_cache is not None:
        return _bots_config_cache
    
    # Load base bots.yaml
    file_path = config_path or _find_config_path("bots.yaml")
    with open(file_path, "r") as f:
        _bots_config_cache = yaml.safe_load(f)
    
    # Merge symbol_profiles from separate file if it exists
    try:
        sp_path = _find_config_path("symbol_profiles.yaml")
        with open(sp_path, "r") as f:
            sp_config = yaml.safe_load(f)
        if sp_config and "symbol_profiles" not in _bots_config_cache:
            _bots_config_cache.update(sp_config)
    except FileNotFoundError:
        pass  # symbol_profiles in bots.yaml or not needed
    
    # If small account mode is enabled, merge with modes/small_account.yaml
    if _small_account_mode:
        try:
            try:
                small_path = _find_config_path("small_account.yaml", subdir="modes")
            except FileNotFoundError:
                small_path = _find_config_path("small_account_mode.yaml")
            
            with open(small_path, "r") as f:
                small_config = yaml.safe_load(f)
            
            if small_config:
                _bots_config_cache = _deep_merge(_bots_config_cache, small_config)
        except FileNotFoundError:
            pass

    # Merge generated tier override (from sweep optimizer) - highest priority
    try:
        tier_path = _find_config_path("tier_override.yaml", subdir="generated")
        with open(tier_path, "r") as f:
            tier_config = yaml.safe_load(f)
        if tier_config:
            meta = tier_config.pop("_meta", {})
            _bots_config_cache = _deep_merge(_bots_config_cache, tier_config)
            _bots_config_cache["_sweep_meta"] = meta
    except FileNotFoundError:
        pass

    return _bots_config_cache


def reload_configs() -> None:
    global _settings_cache, _bots_config_cache
    _settings_cache = None
    _bots_config_cache = None


def save_settings(settings: Dict[str, Any], config_path: Optional[str] = None) -> None:
    global _settings_cache
    file_path = config_path or _find_config_path("settings.yaml")
    with open(file_path, "w") as f:
        yaml.dump(settings, f, default_flow_style=False)
    _settings_cache = None


def save_bots_config(config: Dict[str, Any], config_path: Optional[str] = None) -> None:
    global _bots_config_cache
    file_path = config_path or _find_config_path("bots.yaml")
    with open(file_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)
    _bots_config_cache = None


def _generate_run_id() -> str:
    """Generate a unique run ID for this session."""
    import random
    import string
    now = datetime.utcnow()
    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"run_{now.strftime('%Y%m%d_%H%M')}Z_{suffix}"


# Global run_id for this session
_RUN_ID: Optional[str] = None


def get_run_id() -> str:
    """Get the current session run ID."""
    global _RUN_ID
    if _RUN_ID is None:
        _RUN_ID = _generate_run_id()
    return _RUN_ID


def get_effective_config() -> Dict[str, Any]:
    """
    Build and return the fully merged effective configuration.
    
    Precedence order (later overrides earlier):
    1. settings.yaml (global defaults)
    2. bots.yaml (per-bot configs)
    3. small_account_mode.yaml (if micro/small account)
    4. development.yaml (if exists and DEV mode)
    5. Environment variables (ALPACA_*, etc.)
    6. Account mode runtime adjustments
    
    Returns:
        Complete merged configuration dictionary
    """
    config_root = _get_config_root()
    
    # Build loaded files list with full paths
    loaded_files = []
    settings_path = os.path.join(config_root, "settings.yaml")
    bots_path = os.path.join(config_root, "bots.yaml")
    small_account_path = os.path.join(config_root, "modes", "small_account.yaml")
    dev_path = os.path.join(config_root, "modes", "dev.yaml")
    
    effective = {
        "meta": {
            "run_id": get_run_id(),
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "account_mode": get_account_mode(),
            "small_account_mode_enabled": is_small_account_mode(),
            "config_files_loaded": [],
            "merge_precedence": "settings.yaml → bots.yaml → modes/*.yaml → env_vars → account_mode_params",
            "precedence_order": [
                settings_path,
                bots_path, 
                f"{small_account_path} (if applicable)",
                f"{dev_path} (if applicable)",
                "environment_variables",
                "account_mode_params"
            ],
            "config_root": config_root
        },
        "settings": {},
        "bots": {},
        "account_mode_params": get_account_mode_params(),
        "environment": {}
    }
    
    # Load settings
    try:
        effective["settings"] = load_settings()
        effective["meta"]["config_files_loaded"].append(settings_path)
    except Exception as e:
        effective["settings"] = {"error": str(e)}
    
    # Load bots config (includes small_account_mode.yaml merge if enabled)
    try:
        effective["bots"] = load_bots_config()
        effective["meta"]["config_files_loaded"].append(bots_path)
        if is_small_account_mode():
            effective["meta"]["config_files_loaded"].append(small_account_path)
    except Exception as e:
        effective["bots"] = {"error": str(e)}
    
    # Capture relevant environment variables
    env_keys = ["ALPACA_KEY", "ALPACA_SECRET", "ALPACA_PAPER", "DATABASE_URL"]
    for key in env_keys:
        val = os.environ.get(key)
        if val:
            # Mask secrets
            if "KEY" in key or "SECRET" in key or "URL" in key:
                effective["environment"][key] = f"***SET*** ({len(val)} chars)"
            else:
                effective["environment"][key] = val
        else:
            effective["environment"][key] = "NOT_SET"
    
    return effective


def dump_effective_config(print_summary: bool = True) -> str:
    """
    Dump the effective configuration to logs/effective_config.json.
    
    This is the "cheat code" for debugging: always know exactly what
    config values were active for any given run.
    
    Args:
        print_summary: If True, print a console summary
        
    Returns:
        Path to the saved config file
    """
    effective = get_effective_config()
    
    # Ensure logs directory exists
    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    
    # Write to file
    config_path = logs_dir / "effective_config.json"
    with open(config_path, "w") as f:
        json.dump(effective, f, indent=2, default=str)
    
    if print_summary:
        print_config_summary(effective)
    
    return str(config_path)


def print_config_summary(effective: Dict[str, Any] = None) -> None:
    """Print a concise config summary to console at startup."""
    if effective is None:
        effective = get_effective_config()
    
    meta = effective.get("meta", {})
    settings = effective.get("settings", {})
    bots = effective.get("bots", {})
    account_params = effective.get("account_mode_params", {})
    env = effective.get("environment", {})
    
    # Build loaded files display
    loaded_files = meta.get("config_files_loaded", [])
    
    # Build summary
    lines = [
        "",
        "=" * 60,
        "EFFECTIVE CONFIG SUMMARY",
        "=" * 60,
        f"Run ID: {meta.get('run_id', 'unknown')}",
        f"Generated: {meta.get('generated_at', 'unknown')}",
        f"Account Mode: {meta.get('account_mode', 'standard').upper()}",
        f"Small Account Mode: {'ENABLED' if meta.get('small_account_mode_enabled') else 'disabled'}",
        "",
        "--- CONFIG FILES LOADED (in order) ---",
    ]
    
    for i, fp in enumerate(loaded_files, 1):
        lines.append(f"  {i}) {fp}")
    
    lines.append(f"  Merge: {meta.get('merge_precedence', 'unknown')}")
    lines.append("")
    lines.append("--- RUNTIME PARAMETERS ---")
    lines.append(f"  Loop Interval: {settings.get('runner', {}).get('loop_interval_seconds', 5)}s")
    lines.append(f"  Daily Risk %: {account_params.get('daily_risk_pct', 2.0)}%")
    lines.append(f"  Position Size Mult: {account_params.get('position_size_multiplier', 1.0)}x")
    lines.append("")
    lines.append("--- BOT STATUS ---")
    
    # Check enabled bots - handle different config structures
    # momentum_bots is a list, others are dicts
    mom_bots = bots.get("momentum_bots", [])
    if isinstance(mom_bots, list):
        mom_enabled = any(b.get("enabled", False) for b in mom_bots)
    else:
        mom_enabled = mom_bots.get("enabled", False) if mom_bots else False
    lines.append(f"  momentum_bot: {'ENABLED' if mom_enabled else 'disabled'}")
    
    # Other bots are dicts
    for display_name, config_key in [("crypto_bot", "cryptobot"), ("options_bot", "optionsbot"), ("twentyminute_bot", "twentyminute_bot")]:
        bot_cfg = bots.get(config_key, {})
        enabled = bot_cfg.get("enabled", False)
        status = "ENABLED" if enabled else "disabled"
        lines.append(f"  {display_name}: {status}")
    
    lines.append("")
    lines.append("--- ENVIRONMENT ---")
    for key, val in env.items():
        lines.append(f"  {key}: {val}")
    
    lines.append("")
    lines.append(f"Full config saved to: logs/effective_config.json")
    lines.append("=" * 60)
    lines.append("")
    
    print("\n".join(lines))


def check_config_conflicts() -> List[Dict[str, Any]]:
    """
    Config doctor: check for common configuration conflicts.
    
    Returns list of warnings/conflicts found.
    Example: quote_cache_ttl > max_staleness_seconds
    """
    conflicts = []
    
    try:
        settings = load_settings()
        bots = load_bots_config()
        
        # Check 1: Quote cache TTL vs max staleness
        cache_ttl = settings.get("caching", {}).get("quote_ttl_seconds", 30)
        max_staleness = settings.get("risk", {}).get("max_quote_staleness_seconds", 15)
        
        if cache_ttl > max_staleness:
            conflicts.append({
                "severity": "HIGH",
                "type": "quote_freshness",
                "message": f"Quote cache TTL ({cache_ttl}s) > max staleness ({max_staleness}s). "
                          f"Stale quotes may pass freshness check.",
                "fix": f"Set caching.quote_ttl_seconds <= {max_staleness}"
            })
        
        # Check 2: Options max trades per day
        # Default to 10 (conservative) if not found - config should always have this set
        options_max_trades = bots.get("options_bot", {}).get("risk", {}).get("max_trades_per_day", 10)
        if options_max_trades > 20:
            conflicts.append({
                "severity": "MEDIUM",
                "type": "overtrade_risk",
                "message": f"Options max_trades_per_day ({options_max_trades}) is high. "
                          f"Risk of churning in volatile markets.",
                "fix": "Consider setting options_bot.risk.max_trades_per_day <= 10"
            })
        
        # Check 3: Loop interval vs data refresh
        loop_interval = settings.get("runner", {}).get("loop_interval_seconds", 5)
        if loop_interval < 3:
            conflicts.append({
                "severity": "LOW",
                "type": "performance",
                "message": f"Loop interval ({loop_interval}s) is very fast. May cause API rate limits.",
                "fix": "Consider loop_interval_seconds >= 5"
            })
        
        # Check 4: Small account mode consistency
        if is_small_account_mode():
            # Check if crypto bot is enabled (should be for small accounts)
            crypto_enabled = bots.get("crypto_bot", {}).get("enabled", False)
            if not crypto_enabled:
                conflicts.append({
                    "severity": "MEDIUM",
                    "type": "strategy_mismatch",
                    "message": "Small account mode enabled but CryptoBot is disabled. "
                              "Crypto is recommended for small accounts (fractional, 24/7).",
                    "fix": "Enable crypto_bot in bots.yaml"
                })
    
    except Exception as e:
        conflicts.append({
            "severity": "HIGH",
            "type": "load_error",
            "message": f"Failed to load configs for conflict check: {e}",
            "fix": "Check config file syntax"
        })
    
    return conflicts


def run_config_doctor(print_output: bool = True, hard_fail: bool = False) -> List[Dict[str, Any]]:
    """
    Run config doctor to find and report configuration conflicts.
    
    Args:
        print_output: If True, print conflicts to console
        hard_fail: If True, raise exception on HIGH severity conflicts (fail-closed)
        
    Returns:
        List of conflict dictionaries
        
    Raises:
        SystemExit: If hard_fail=True and HIGH severity conflicts exist
    """
    conflicts = check_config_conflicts()
    
    if print_output:
        if not conflicts:
            print("\n[CONFIG DOCTOR] ✓ No conflicts found\n")
        else:
            print("\n" + "=" * 60)
            print("CONFIG DOCTOR - CONFLICTS DETECTED")
            print("=" * 60)
            for c in conflicts:
                sev = c["severity"]
                icon = "🔴" if sev == "HIGH" else "🟡" if sev == "MEDIUM" else "🔵"
                print(f"\n{icon} [{sev}] {c['type']}")
                print(f"   {c['message']}")
                print(f"   Fix: {c['fix']}")
            print("\n" + "=" * 60 + "\n")
    
    # HARD FAIL on HIGH severity conflicts if requested
    if hard_fail:
        high_severity = [c for c in conflicts if c["severity"] == "HIGH"]
        if high_severity:
            print("\n" + "=" * 60)
            print("🔴 FAIL-CLOSED: CONFIG DOCTOR BLOCKING STARTUP")
            print("=" * 60)
            for c in high_severity:
                print(f"  CRITICAL: {c['type']}")
                print(f"  {c['message']}")
                print(f"  Fix: {c['fix']}")
            print("\n  Trading CANNOT proceed until these are resolved.")
            print("=" * 60 + "\n")
            raise SystemExit(1)
    
    return conflicts
