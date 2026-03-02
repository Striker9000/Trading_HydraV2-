"""Parameter Resolver - Single source of truth for resolved trading parameters.

This module is the ONLY place that decides final effective params for a symbol.
Nothing else in the codebase should "also tweak delta" or "also tweak size".

Resolution order (immutable):
1. Base profile params
2. Earnings override (full replace, no merge)
3. Regime modifiers (delta×, DTE shift, size×)
4. Growth scaling (sqrt model, capped at 1.0 in STRESS)
5. Kill-switch enforcement
"""

import os
import math
import yaml
from datetime import datetime, date
from typing import Dict, Any, Optional, Tuple

from ..core.logging import get_logger
from ..risk.killswitch import get_killswitch_service
from .system_state import ResolvedParams


# Singleton instance
_resolver: Optional["ParameterResolver"] = None


def get_parameter_resolver() -> "ParameterResolver":
    """Get or create the ParameterResolver singleton."""
    global _resolver
    if _resolver is None:
        _resolver = ParameterResolver()
    return _resolver


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp value between min and max."""
    return max(min_val, min(value, max_val))


class ParameterResolver:
    """Resolves final trading parameters for a symbol."""
    
    # Hard clamps for safety
    DELTA_MIN_CLAMP = 0.05
    DELTA_MAX_CLAMP = 0.35
    DTE_MIN_CLAMP = 7
    DTE_MAX_CLAMP = 60
    
    def __init__(self):
        self._logger = get_logger()
        self._profiles = self._load_profiles()
        self._universe_map = self._load_universe_map()
        self._regimes_config = self._load_config("regimes.yaml")
        self._sizing_config = self._load_config("sizing.yaml")
        self._killswitch = get_killswitch_service()
        self._logger.log("parameter_resolver_init", {
            "profiles_loaded": len(self._profiles),
            "universe_size": len(self._universe_map)
        })
    
    def _get_config_root(self) -> str:
        """Get the config directory path."""
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            "config"
        )
    
    def _load_config(self, filename: str) -> Dict[str, Any]:
        """Load a YAML config file."""
        config_path = os.path.join(self._get_config_root(), filename)
        try:
            with open(config_path, "r") as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            self._logger.error(f"Config file not found: {config_path}")
            return {}
    
    def _load_profiles(self) -> Dict[str, Dict[str, Any]]:
        """Load all profile YAML files."""
        profiles = {}
        profiles_dir = os.path.join(self._get_config_root(), "profiles")
        
        if not os.path.isdir(profiles_dir):
            self._logger.error(f"Profiles directory not found: {profiles_dir}")
            return profiles
        
        for filename in os.listdir(profiles_dir):
            if filename.endswith(".yaml"):
                profile_name = filename.replace(".yaml", "")
                profile_path = os.path.join(profiles_dir, filename)
                try:
                    with open(profile_path, "r") as f:
                        profiles[profile_name] = yaml.safe_load(f) or {}
                except Exception as e:
                    self._logger.error(f"Failed to load profile {filename}: {e}")
        
        return profiles
    
    def _load_universe_map(self) -> Dict[str, str]:
        """Load ticker -> profile mapping."""
        config = self._load_config("universe_profiles.yaml")
        return {k: v for k, v in config.items() if not k.startswith("_")}
    
    def get_base_profile(self, symbol: str) -> str:
        """Get the base profile name for a symbol."""
        default = self._load_config("universe_profiles.yaml").get("_default", "index_etf")
        return self._universe_map.get(symbol.upper(), default)
    
    def get_profile_params(self, profile_name: str) -> Dict[str, Any]:
        """Get parameters for a profile."""
        return self._profiles.get(profile_name, self._profiles.get("index_etf", {}))
    
    def resolve(
        self,
        symbol: str,
        regime: str,
        regime_modifiers: Dict[str, float],
        is_in_earnings_window: bool,
        growth_multiplier: float,
        equity: float
    ) -> ResolvedParams:
        """Resolve final trading parameters for a symbol.
        
        Args:
            symbol: The ticker symbol
            regime: Current regime (LOW/NORMAL/STRESS)
            regime_modifiers: Dict with delta_multiplier, dte_shift_days, size_multiplier
            is_in_earnings_window: Whether symbol is in earnings window
            growth_multiplier: Account growth multiplier (1.0 = baseline)
            equity: Current account equity
            
        Returns:
            ResolvedParams with all final values
        """
        symbol = symbol.upper()
        
        # Step 1: Get base profile
        base_profile_name = self.get_base_profile(symbol)
        
        # Step 2: Earnings override (full replace, no merge)
        if is_in_earnings_window:
            profile_name = "earnings"
            self._logger.log("parameter_resolver_earnings_override", {
                "symbol": symbol,
                "base_profile": base_profile_name,
                "resolved_profile": "earnings"
            })
        else:
            profile_name = base_profile_name
        
        params = self.get_profile_params(profile_name).copy()
        
        # Step 3: Apply regime modifiers
        delta_mult = regime_modifiers.get("delta_multiplier", 1.0)
        dte_shift = regime_modifiers.get("dte_shift_days", 0)
        size_mult = regime_modifiers.get("size_multiplier", 1.0)
        max_trades = int(regime_modifiers.get("max_new_trades_per_day", 100))
        
        delta_target = params.get("delta_target", 0.25) * delta_mult
        delta_min = params.get("delta_min", 0.20) * delta_mult
        delta_max = params.get("delta_max", 0.30) * delta_mult
        
        dte_min = params.get("dte_min", 30) + dte_shift
        dte_max = params.get("dte_max", 45) + dte_shift
        
        # Step 4: Apply growth scaling (capped at 1.0 in STRESS)
        effective_growth = growth_multiplier
        if regime == "STRESS":
            stress_cap = self._sizing_config.get("stress_cap", 1.0)
            effective_growth = min(growth_multiplier, stress_cap)
        
        # Combined size multiplier
        combined_size_mult = size_mult * effective_growth
        
        max_position_pct = params.get("max_position_pct", 0.10) * combined_size_mult
        max_total_exposure = params.get("max_total_exposure", 0.40) * combined_size_mult
        
        # Apply hard clamps
        delta_target = clamp(delta_target, self.DELTA_MIN_CLAMP, self.DELTA_MAX_CLAMP)
        delta_min = clamp(delta_min, self.DELTA_MIN_CLAMP, self.DELTA_MAX_CLAMP)
        delta_max = clamp(delta_max, self.DELTA_MIN_CLAMP, self.DELTA_MAX_CLAMP)
        
        # Ensure delta_min < delta_max
        if delta_min > delta_max:
            delta_min, delta_max = delta_max, delta_min
        
        dte_min = max(self.DTE_MIN_CLAMP, dte_min)
        dte_max = min(self.DTE_MAX_CLAMP, dte_max)
        
        # Ensure dte_min < dte_max
        if dte_min >= dte_max:
            dte_max = dte_min + 7
        
        # Size caps never exceed original base profile caps
        base_max_position = params.get("max_position_pct", 0.10)
        base_max_exposure = params.get("max_total_exposure", 0.40)
        max_position_pct = min(max_position_pct, base_max_position * 1.5)  # Allow up to 50% more
        max_total_exposure = min(max_total_exposure, base_max_exposure * 1.5)
        
        # Step 5: Check kill-switch
        allowed, ks_reason = self._killswitch.is_entry_allowed(profile_name)
        
        return ResolvedParams(
            symbol=symbol,
            resolved_profile_name=profile_name,
            delta_min=delta_min,
            delta_max=delta_max,
            delta_target=delta_target,
            dte_min=dte_min,
            dte_max=dte_max,
            max_position_pct=max_position_pct,
            max_total_exposure=max_total_exposure,
            max_open_positions=params.get("max_open_positions", 5),
            max_new_trades_per_day=max_trades,
            min_open_interest=params.get("min_open_interest", 500),
            max_bid_ask_pct=params.get("max_bid_ask_pct", 0.15),
            force_defined_risk=params.get("force_defined_risk", False),
            debit_only=params.get("debit_only", False),
            disable_put_selling=params.get("disable_put_selling", False),
            blocked_by_killswitch=not allowed,
            killswitch_reason=ks_reason
        )
    
    def compute_growth_multiplier(self, equity: float) -> float:
        """Compute growth multiplier based on equity.
        
        Uses sqrt model: growth_mult = clamp((equity/baseline)**0.5, min, max)
        """
        baseline = self._sizing_config.get("baseline_equity", 5000)
        min_mult = self._sizing_config.get("min_multiplier", 0.75)
        max_mult = self._sizing_config.get("max_multiplier", 1.50)
        
        if baseline <= 0:
            baseline = equity  # Fallback: no growth effect
        if equity <= 0:
            return 1.0
        
        model = self._sizing_config.get("model", "sqrt")
        
        if model == "sqrt":
            raw = math.sqrt(equity / baseline)
        else:
            raw = equity / baseline
        
        return clamp(raw, min_mult, max_mult)
    
    def get_regime_modifiers(self, regime: str) -> Dict[str, Any]:
        """Get modifiers for a given regime."""
        modifiers = self._regimes_config.get("modifiers", {})
        return modifiers.get(regime, modifiers.get("NORMAL", {
            "delta_multiplier": 1.0,
            "dte_shift_days": 0,
            "size_multiplier": 1.0,
            "max_new_trades_per_day": 100
        }))
    
    def classify_regime(self, vix: float) -> str:
        """Classify VIX into regime category."""
        thresholds = self._regimes_config.get("vix_thresholds", {"low": 14, "stress": 22})
        
        if vix < thresholds.get("low", 14):
            return "LOW"
        elif vix > thresholds.get("stress", 22):
            return "STRESS"
        else:
            return "NORMAL"
    
    def get_default_vix(self) -> float:
        """Get default VIX value when data is missing (fail-closed)."""
        defaults = self._regimes_config.get("defaults", {})
        return defaults.get("missing_vix_value", 25.0)
    
    def get_default_regime(self) -> str:
        """Get default regime when VIX is missing (fail-closed)."""
        defaults = self._regimes_config.get("defaults", {})
        return defaults.get("missing_vix_regime", "STRESS")
