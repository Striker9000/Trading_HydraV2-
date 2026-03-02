"""
=============================================================================
Strategy Registry - Safe Strategy Configuration Loading
=============================================================================
Loads strategy configs only from config/strategies/*.yaml files.
Prevents "config hallucinations" by validating required keys and
supporting inheritance via 'extends' field.

Features:
- Only loads from disk (no inline/dynamic configs)
- Supports 'extends:' for strategy variants
- Validates required fields
- Returns frozen dicts (immutable at runtime)
=============================================================================
"""
from __future__ import annotations

import os
import glob as glob_module
import yaml
from dataclasses import dataclass
from typing import Any, Dict, Optional, List

from ..core.logging import get_logger


REQUIRED_TOP_KEYS = [
    "id", "name", "family", "direction", "enabled",
    "signal_rules", "backtest_gate", "options_plan", "risk_plan"
]


@dataclass(frozen=True)
class StrategyConfig:
    """Immutable strategy configuration wrapper."""
    data: Dict[str, Any]


class StrategyRegistry:
    """
    Registry for strategy configurations.
    Only loads from config/strategies/*.yaml files.
    """

    def __init__(self, strategies_dir: Optional[str] = None):
        self._logger = get_logger()
        self._dir = strategies_dir or self._find_strategies_dir()
        self._cache: Dict[str, StrategyConfig] = {}
        self._raw: Dict[str, Dict[str, Any]] = {}

    def load_all(self) -> None:
        """
        Load all strategy YAML files from the strategies directory.
        Resolves 'extends' inheritance and validates required keys.
        """
        self._raw = {}
        self._cache = {}

        paths = sorted(glob_module.glob(os.path.join(self._dir, "*.yaml")))
        if not paths:
            self._logger.warn(f"No strategy YAML files found in {self._dir}")
            return

        for p in paths:
            try:
                with open(p, "r") as f:
                    cfg = yaml.safe_load(f) or {}
                if not isinstance(cfg, dict):
                    raise ValueError(f"Strategy YAML must be a dict: {p}")
                sid = cfg.get("id") or os.path.splitext(os.path.basename(p))[0]
                self._raw[sid] = cfg
            except Exception as e:
                self._logger.error(f"Failed to load strategy {p}: {e}")

        for sid in list(self._raw.keys()):
            try:
                resolved = self._resolve_strategy(sid, stack=[])
                self._validate(resolved, sid)
                self._cache[sid] = StrategyConfig(data=_deep_freeze(resolved))
            except Exception as e:
                self._logger.error(f"Failed to resolve strategy {sid}: {e}")

        self._logger.log("strategy_registry_loaded", {
            "count": len(self._cache),
            "dir": self._dir,
            "strategies": sorted(self._cache.keys()),
        })

    def get(self, strategy_id: str) -> StrategyConfig:
        """
        Get a strategy by ID.
        
        Args:
            strategy_id: Strategy identifier
            
        Returns:
            StrategyConfig wrapper
            
        Raises:
            KeyError if strategy not found
        """
        if not self._cache:
            self.load_all()
        if strategy_id not in self._cache:
            raise KeyError(f"Unknown strategy_id={strategy_id}. Loaded={list(self._cache.keys())}")
        return self._cache[strategy_id]

    def enabled_strategies(self) -> List[StrategyConfig]:
        """
        Get all enabled strategies.
        
        Returns:
            List of StrategyConfig where enabled=True
        """
        if not self._cache:
            self.load_all()
        return [s for s in self._cache.values() if s.data.get("enabled", False)]

    def all_strategies(self) -> List[StrategyConfig]:
        """
        Get all loaded strategies (enabled or disabled).
        
        Returns:
            List of all StrategyConfig
        """
        if not self._cache:
            self.load_all()
        return list(self._cache.values())

    def _resolve_strategy(self, sid: str, stack: List[str]) -> Dict[str, Any]:
        """Resolve strategy with 'extends' inheritance."""
        if sid in stack:
            raise ValueError(f"Strategy extends loop detected: {' -> '.join(stack + [sid])}")

        base = self._raw.get(sid)
        if base is None:
            raise KeyError(f"Strategy id not found during resolve: {sid}")

        parent_id = base.get("extends")
        if not parent_id:
            return dict(base)

        stack2 = stack + [sid]
        parent = self._resolve_strategy(parent_id, stack2)
        merged = _deep_merge(parent, base)
        merged.pop("extends", None)
        return merged

    def _validate(self, cfg: Dict[str, Any], sid: str) -> None:
        """Validate required fields in strategy config."""
        missing = [k for k in REQUIRED_TOP_KEYS if k not in cfg]
        if missing:
            raise ValueError(f"Strategy {sid} missing keys: {missing}")

        if cfg["direction"] not in ("bullish", "bearish"):
            raise ValueError(f"Strategy {sid} bad direction: {cfg['direction']}")

        if not isinstance(cfg["signal_rules"], list) or not cfg["signal_rules"]:
            raise ValueError(f"Strategy {sid} signal_rules must be a non-empty list")

    def _find_strategies_dir(self) -> str:
        """Find the config/strategies directory."""
        candidates = [
            os.path.join(os.getcwd(), "config", "strategies"),
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "strategies"),
            "/home/runner/workspace/config/strategies",
        ]
        for p in candidates:
            if os.path.isdir(p):
                return os.path.abspath(p)
        
        default = candidates[0]
        os.makedirs(default, exist_ok=True)
        return default


def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge dict b into dict a."""
    out = dict(a)
    for k, v in (b or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _deep_freeze(x: Any) -> Any:
    """Recursively convert dicts to dicts and lists to tuples (pseudo-freeze)."""
    if isinstance(x, dict):
        return {k: _deep_freeze(v) for k, v in x.items()}
    if isinstance(x, list):
        return tuple(_deep_freeze(v) for v in x)
    return x
