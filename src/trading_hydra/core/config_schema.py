"""Config schema validation module for Trading Hydra.

Validates all YAML config files at startup to catch dangerous misconfigurations
before they cause real-money bugs. Uses pure Python dataclasses.
"""
import logging
import os
import yaml
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("trading_hydra.config_schema")


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"


@dataclass
class ConfigError:
    severity: Severity
    field_path: str
    message: str
    file: str = ""

    def __str__(self) -> str:
        sev_icon = "🔴" if self.severity == Severity.CRITICAL else "🟡"
        return f"{sev_icon} [{self.severity.value}] {self.file}:{self.field_path} — {self.message}"


@dataclass
class FieldSpec:
    field_type: type = str
    required: bool = False
    default: Any = None
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    choices: Optional[list] = None
    must_be_true: bool = False


SETTINGS_SCHEMA: Dict[str, FieldSpec] = {
    "safety.fail_closed": FieldSpec(field_type=bool, required=True, must_be_true=True),
    "safety.allow_budget_fallback": FieldSpec(field_type=bool, required=False),
    "safety.global_cooldown_minutes": FieldSpec(field_type=(int, float), required=False, min_val=0, max_val=120),
    "risk.global_max_daily_loss_pct": FieldSpec(field_type=(int, float), required=True, min_val=1.0, max_val=10.0),
    "risk.max_orders_per_minute": FieldSpec(field_type=(int, float), required=True, min_val=1, max_val=60),
    "runner.loop_interval_seconds": FieldSpec(field_type=(int, float), required=True, min_val=1, max_val=300),
    "system.timezone": FieldSpec(field_type=str, required=True),
    "system.log_path": FieldSpec(field_type=str, required=True),
    "system.state_db_path": FieldSpec(field_type=str, required=True),
    "trading.global_halt": FieldSpec(field_type=bool, required=False),
    "trading.allow_live": FieldSpec(field_type=bool, required=False),
    "health.max_price_staleness_seconds": FieldSpec(field_type=(int, float), required=False, min_val=1, max_val=300),
    "caching.quote_ttl_seconds": FieldSpec(field_type=(int, float), required=False, min_val=1, max_val=300),
    "institutional_sizing.base_risk_pct": FieldSpec(field_type=(int, float), required=False, min_val=0.1, max_val=10.0),
    "institutional_sizing.max_single_position_pct": FieldSpec(field_type=(int, float), required=False, min_val=1.0, max_val=25.0),
    "institutional_sizing.min_notional": FieldSpec(field_type=(int, float), required=False, min_val=1.0),
    "institutional_sizing.max_notional": FieldSpec(field_type=(int, float), required=False, min_val=100.0),
    "dynamic_budget.daily_budget_pct": FieldSpec(field_type=(int, float), required=False, min_val=1.0, max_val=50.0),
    "dynamic_budget.max_position_pct": FieldSpec(field_type=(int, float), required=False, min_val=1.0, max_val=25.0),
}

CORRECT_BOT_KEY_NAMES: Set[str] = {
    "exitbot",
    "cryptobot",
    "bouncebot",
    "optionsbot",
    "optionsbot_0dte",
    "portfoliobot",
    "momentum_bots",
    "twentyminute_bot",
    "whipsaw_trader",
}

WRONG_BOT_KEY_NAMES: Dict[str, str] = {
    "exit_bot": "exitbot",
    "crypto_bot": "cryptobot",
    "bounce_bot": "bouncebot",
    "options_bot": "optionsbot",
    "options_bot_0dte": "optionsbot_0dte",
    "portfolio_bot": "portfoliobot",
}

BOTS_SCHEMA: Dict[str, FieldSpec] = {
    "exitbot.enabled": FieldSpec(field_type=bool, required=True),
    "exitbot.stock_exits.catastrophic_stop_pct": FieldSpec(field_type=(int, float), required=False, min_val=1.0, max_val=50.0),
    "exitbot.options_exits.catastrophic_stop_pct": FieldSpec(field_type=(int, float), required=False, min_val=1.0, max_val=100.0),
    "exitbot.crypto_exits.catastrophic_stop_pct": FieldSpec(field_type=(int, float), required=False, min_val=1.0, max_val=50.0),
    "cryptobot.enabled": FieldSpec(field_type=bool, required=True),
    "cryptobot.risk.max_trades_per_day": FieldSpec(field_type=(int, float), required=False, min_val=1, max_val=500),
    "cryptobot.risk.max_concurrent_positions": FieldSpec(field_type=(int, float), required=False, min_val=1, max_val=50),
    "optionsbot.enabled": FieldSpec(field_type=bool, required=True),
    "optionsbot.risk.max_trades_per_day": FieldSpec(field_type=(int, float), required=False, min_val=1, max_val=100),
    "optionsbot.risk.max_concurrent_positions": FieldSpec(field_type=(int, float), required=False, min_val=1, max_val=50),
    "optionsbot.risk.max_position_size_usd": FieldSpec(field_type=(int, float), required=False, min_val=10.0),
    "bouncebot.enabled": FieldSpec(field_type=bool, required=True),
    "bouncebot.risk.max_trades_per_session": FieldSpec(field_type=(int, float), required=False, min_val=1, max_val=50),
    "portfoliobot.enabled": FieldSpec(field_type=bool, required=True),
    "portfoliobot.cash_reserve_pct": FieldSpec(field_type=(int, float), required=False, min_val=0, max_val=100),
    "twentyminute_bot.enabled": FieldSpec(field_type=bool, required=True),
}

SENSORS_SCHEMA: Dict[str, FieldSpec] = {
    "polling.quotes_interval": FieldSpec(field_type=(int, float), required=True, min_val=1, max_val=300),
    "polling.bars_1m_interval": FieldSpec(field_type=(int, float), required=True, min_val=10, max_val=600),
    "polling.regime_interval": FieldSpec(field_type=(int, float), required=True, min_val=30, max_val=3600),
    "cache.quote_ttl": FieldSpec(field_type=(int, float), required=False, min_val=1, max_val=300),
    "startup.warmup_timeout": FieldSpec(field_type=(int, float), required=False, min_val=1, max_val=300),
    "startup.ready_threshold": FieldSpec(field_type=(int, float), required=False, min_val=0.0, max_val=1.0),
}

TICKER_UNIVERSE_SCHEMA: Dict[str, FieldSpec] = {
    "tiers": FieldSpec(field_type=dict, required=True),
    "limits.max_universe_size": FieldSpec(field_type=(int, float), required=False, min_val=1, max_val=500),
    "limits.max_per_sector": FieldSpec(field_type=(int, float), required=False, min_val=1, max_val=50),
    "filters.min_avg_volume": FieldSpec(field_type=(int, float), required=False, min_val=0),
    "stocks.candidates": FieldSpec(field_type=list, required=True),
    "options.candidates": FieldSpec(field_type=list, required=True),
}

WATCHLISTS_SCHEMA: Dict[str, FieldSpec] = {
    "tickers": FieldSpec(field_type=dict, required=True),
    "watchlists": FieldSpec(field_type=dict, required=True),
}


def _resolve_dotpath(data: Dict[str, Any], path: str) -> Any:
    keys = path.split(".")
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


def _has_dotpath(data: Dict[str, Any], path: str) -> bool:
    keys = path.split(".")
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return False
        if key not in current:
            return False
        current = current[key]
    return True


def _get_config_root() -> str:
    possible_roots = [
        os.path.join(os.getcwd(), "config"),
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "config"),
        "/home/runner/workspace/config",
        "/home/runner/workspace/export/config",
    ]
    for root in possible_roots:
        if os.path.isdir(root):
            return os.path.abspath(root)
    return possible_roots[0]


def _load_yaml(filepath: str) -> Optional[Dict[str, Any]]:
    try:
        with open(filepath, "r") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return None
        return data
    except Exception as e:
        logger.error(f"Failed to load YAML file {filepath}: {e}")
        return None


class ConfigValidator:
    def __init__(self, config_root: Optional[str] = None):
        self.config_root = config_root or _get_config_root()
        self.errors: List[ConfigError] = []
        logger.info(f"[ConfigValidator] Initialized with config_root={self.config_root}")

    def _validate_schema(
        self,
        data: Dict[str, Any],
        schema: Dict[str, FieldSpec],
        filename: str,
    ) -> List[ConfigError]:
        errors: List[ConfigError] = []
        logger.info(f"[ConfigValidator] Validating schema for {filename} ({len(schema)} fields)")

        for field_path, spec in schema.items():
            parts = field_path.split(".")
            top_key = parts[0]

            if len(parts) == 1:
                present = top_key in data
                value = data.get(top_key)
            else:
                present = _has_dotpath(data, field_path)
                value = _resolve_dotpath(data, field_path)

            if spec.required and not present:
                errors.append(ConfigError(
                    severity=Severity.CRITICAL,
                    field_path=field_path,
                    message=f"Required field missing",
                    file=filename,
                ))
                logger.error(f"[ConfigValidator] CRITICAL: {filename}:{field_path} — required field missing")
                continue

            if not present:
                logger.debug(f"[ConfigValidator] {filename}:{field_path} — not present (optional, skipping)")
                continue

            if value is not None:
                expected = spec.field_type
                if isinstance(expected, tuple):
                    type_ok = isinstance(value, expected)
                else:
                    type_ok = isinstance(value, expected)
                if not type_ok:
                    errors.append(ConfigError(
                        severity=Severity.CRITICAL,
                        field_path=field_path,
                        message=f"Expected type {expected}, got {type(value).__name__} (value={value})",
                        file=filename,
                    ))
                    logger.error(f"[ConfigValidator] CRITICAL: {filename}:{field_path} — type mismatch: expected {expected}, got {type(value).__name__}")
                    continue

            if isinstance(value, (int, float)):
                if spec.min_val is not None and value < spec.min_val:
                    sev = Severity.CRITICAL
                    errors.append(ConfigError(
                        severity=sev,
                        field_path=field_path,
                        message=f"Value {value} below minimum {spec.min_val}",
                        file=filename,
                    ))
                    logger.error(f"[ConfigValidator] {sev.value}: {filename}:{field_path} — {value} < {spec.min_val}")
                if spec.max_val is not None and value > spec.max_val:
                    sev = Severity.CRITICAL
                    errors.append(ConfigError(
                        severity=sev,
                        field_path=field_path,
                        message=f"Value {value} exceeds maximum {spec.max_val}",
                        file=filename,
                    ))
                    logger.error(f"[ConfigValidator] {sev.value}: {filename}:{field_path} — {value} > {spec.max_val}")

            if spec.choices is not None and value not in spec.choices:
                errors.append(ConfigError(
                    severity=Severity.CRITICAL,
                    field_path=field_path,
                    message=f"Value '{value}' not in allowed choices: {spec.choices}",
                    file=filename,
                ))
                logger.error(f"[ConfigValidator] CRITICAL: {filename}:{field_path} — '{value}' not in {spec.choices}")

            if spec.must_be_true and value is not True:
                errors.append(ConfigError(
                    severity=Severity.CRITICAL,
                    field_path=field_path,
                    message=f"SAFETY: Must be true, got {value}. System must fail-closed.",
                    file=filename,
                ))
                logger.error(f"[ConfigValidator] CRITICAL: {filename}:{field_path} — must be true, got {value}")

        return errors

    def validate_settings(self) -> List[ConfigError]:
        filepath = os.path.join(self.config_root, "settings.yaml")
        logger.info(f"[ConfigValidator] Validating settings: {filepath}")
        data = _load_yaml(filepath)
        if data is None:
            err = ConfigError(
                severity=Severity.CRITICAL,
                field_path="(file)",
                message=f"Cannot load or parse settings.yaml at {filepath}",
                file="settings.yaml",
            )
            logger.error(f"[ConfigValidator] {err}")
            return [err]

        errors = self._validate_schema(data, SETTINGS_SCHEMA, "settings.yaml")

        cache_ttl = _resolve_dotpath(data, "caching.quote_ttl_seconds")
        max_staleness = _resolve_dotpath(data, "health.max_price_staleness_seconds")
        if cache_ttl is not None and max_staleness is not None:
            if cache_ttl > max_staleness:
                errors.append(ConfigError(
                    severity=Severity.WARNING,
                    field_path="caching.quote_ttl_seconds",
                    message=f"Quote cache TTL ({cache_ttl}s) > max staleness ({max_staleness}s). Stale quotes may pass freshness check.",
                    file="settings.yaml",
                ))
                logger.warning(f"[ConfigValidator] WARNING: quote cache TTL ({cache_ttl}) > max staleness ({max_staleness})")

        logger.info(f"[ConfigValidator] settings.yaml: {len(errors)} errors found")
        return errors

    def validate_bots(self) -> List[ConfigError]:
        filepath = os.path.join(self.config_root, "bots.yaml")
        logger.info(f"[ConfigValidator] Validating bots config: {filepath}")
        data = _load_yaml(filepath)
        if data is None:
            err = ConfigError(
                severity=Severity.CRITICAL,
                field_path="(file)",
                message=f"Cannot load or parse bots.yaml at {filepath}",
                file="bots.yaml",
            )
            logger.error(f"[ConfigValidator] {err}")
            return [err]

        errors = self._validate_schema(data, BOTS_SCHEMA, "bots.yaml")
        errors.extend(self.validate_config_keys_match_bot_names(data))

        return errors

    def validate_config_keys_match_bot_names(self, bots_data: Optional[Dict[str, Any]] = None) -> List[ConfigError]:
        if bots_data is None:
            filepath = os.path.join(self.config_root, "bots.yaml")
            bots_data = _load_yaml(filepath)
            if bots_data is None:
                return [ConfigError(
                    severity=Severity.CRITICAL,
                    field_path="(file)",
                    message="Cannot load bots.yaml for key name validation",
                    file="bots.yaml",
                )]

        errors: List[ConfigError] = []
        logger.info("[ConfigValidator] Checking bot config key naming consistency")

        for wrong_name, correct_name in WRONG_BOT_KEY_NAMES.items():
            if wrong_name in bots_data:
                errors.append(ConfigError(
                    severity=Severity.CRITICAL,
                    field_path=wrong_name,
                    message=f"Wrong config key name '{wrong_name}' — should be '{correct_name}'. "
                            f"This mismatch causes the bot to silently use default config instead of the intended overrides.",
                    file="bots.yaml",
                ))
                logger.error(f"[ConfigValidator] CRITICAL: bots.yaml uses '{wrong_name}' instead of '{correct_name}'")

        for correct_name in CORRECT_BOT_KEY_NAMES:
            if correct_name == "momentum_bots":
                if correct_name in bots_data:
                    val = bots_data[correct_name]
                    if not isinstance(val, list):
                        errors.append(ConfigError(
                            severity=Severity.WARNING,
                            field_path=correct_name,
                            message=f"momentum_bots should be a list of bot configs, got {type(val).__name__}",
                            file="bots.yaml",
                        ))
                continue

            if correct_name not in bots_data:
                if correct_name in ("optionsbot_0dte",):
                    logger.debug(f"[ConfigValidator] Optional bot key '{correct_name}' not present (OK)")
                    continue
                errors.append(ConfigError(
                    severity=Severity.WARNING,
                    field_path=correct_name,
                    message=f"Expected bot config key '{correct_name}' not found in bots.yaml",
                    file="bots.yaml",
                ))
                logger.warning(f"[ConfigValidator] WARNING: expected bot key '{correct_name}' missing from bots.yaml")

        dedicated_threads = bots_data.get("dedicated_threads", {})
        if isinstance(dedicated_threads, dict):
            logger.info("[ConfigValidator] Checking dedicated_threads keys match bot names")
            for thread_key in dedicated_threads:
                if thread_key not in CORRECT_BOT_KEY_NAMES and thread_key not in bots_data:
                    errors.append(ConfigError(
                        severity=Severity.WARNING,
                        field_path=f"dedicated_threads.{thread_key}",
                        message=f"Dedicated thread key '{thread_key}' does not match any known bot config key",
                        file="bots.yaml",
                    ))
                    logger.warning(f"[ConfigValidator] WARNING: dedicated_threads key '{thread_key}' doesn't match any bot")

        logger.info(f"[ConfigValidator] Bot key naming check: {len(errors)} issues found")
        return errors

    def validate_sensors(self) -> List[ConfigError]:
        filepath = os.path.join(self.config_root, "sensors.yaml")
        logger.info(f"[ConfigValidator] Validating sensors config: {filepath}")
        data = _load_yaml(filepath)
        if data is None:
            err = ConfigError(
                severity=Severity.CRITICAL,
                field_path="(file)",
                message=f"Cannot load or parse sensors.yaml at {filepath}",
                file="sensors.yaml",
            )
            logger.error(f"[ConfigValidator] {err}")
            return [err]

        return self._validate_schema(data, SENSORS_SCHEMA, "sensors.yaml")

    def validate_ticker_universe(self) -> List[ConfigError]:
        filepath = os.path.join(self.config_root, "ticker_universe.yaml")
        logger.info(f"[ConfigValidator] Validating ticker_universe config: {filepath}")
        data = _load_yaml(filepath)
        if data is None:
            err = ConfigError(
                severity=Severity.CRITICAL,
                field_path="(file)",
                message=f"Cannot load or parse ticker_universe.yaml at {filepath}",
                file="ticker_universe.yaml",
            )
            logger.error(f"[ConfigValidator] {err}")
            return [err]

        errors = self._validate_schema(data, TICKER_UNIVERSE_SCHEMA, "ticker_universe.yaml")

        tiers = data.get("tiers", {})
        if isinstance(tiers, dict):
            for tier_name, tickers in tiers.items():
                if not isinstance(tickers, list):
                    errors.append(ConfigError(
                        severity=Severity.WARNING,
                        field_path=f"tiers.{tier_name}",
                        message=f"Tier '{tier_name}' should be a list of tickers, got {type(tickers).__name__}",
                        file="ticker_universe.yaml",
                    ))
                elif len(tickers) == 0:
                    errors.append(ConfigError(
                        severity=Severity.WARNING,
                        field_path=f"tiers.{tier_name}",
                        message=f"Tier '{tier_name}' is empty — no tickers defined",
                        file="ticker_universe.yaml",
                    ))

        logger.info(f"[ConfigValidator] ticker_universe.yaml: {len(errors)} errors found")
        return errors

    def validate_watchlists(self) -> List[ConfigError]:
        filepath = os.path.join(self.config_root, "watchlists.yaml")
        logger.info(f"[ConfigValidator] Validating watchlists config: {filepath}")
        data = _load_yaml(filepath)
        if data is None:
            err = ConfigError(
                severity=Severity.CRITICAL,
                field_path="(file)",
                message=f"Cannot load or parse watchlists.yaml at {filepath}",
                file="watchlists.yaml",
            )
            logger.error(f"[ConfigValidator] {err}")
            return [err]

        errors = self._validate_schema(data, WATCHLISTS_SCHEMA, "watchlists.yaml")

        tickers = data.get("tickers", {})
        if isinstance(tickers, dict):
            for ticker, meta in tickers.items():
                if not isinstance(meta, dict):
                    errors.append(ConfigError(
                        severity=Severity.WARNING,
                        field_path=f"tickers.{ticker}",
                        message=f"Ticker '{ticker}' metadata should be a dict, got {type(meta).__name__}",
                        file="watchlists.yaml",
                    ))
                elif "tags" not in meta:
                    errors.append(ConfigError(
                        severity=Severity.WARNING,
                        field_path=f"tickers.{ticker}.tags",
                        message=f"Ticker '{ticker}' is missing 'tags' field",
                        file="watchlists.yaml",
                    ))

        watchlists = data.get("watchlists", {})
        if isinstance(watchlists, dict):
            for wl_name, wl_def in watchlists.items():
                if not isinstance(wl_def, dict):
                    errors.append(ConfigError(
                        severity=Severity.WARNING,
                        field_path=f"watchlists.{wl_name}",
                        message=f"Watchlist '{wl_name}' definition should be a dict",
                        file="watchlists.yaml",
                    ))

        logger.info(f"[ConfigValidator] watchlists.yaml: {len(errors)} errors found")
        return errors

    def validate_all(self) -> List[ConfigError]:
        logger.info("=" * 60)
        logger.info("[ConfigValidator] Starting full config validation")
        logger.info(f"[ConfigValidator] Config root: {self.config_root}")
        logger.info("=" * 60)

        all_errors: List[ConfigError] = []

        validators = [
            ("settings.yaml", self.validate_settings),
            ("bots.yaml", self.validate_bots),
            ("sensors.yaml", self.validate_sensors),
            ("ticker_universe.yaml", self.validate_ticker_universe),
            ("watchlists.yaml", self.validate_watchlists),
        ]

        for name, validator_fn in validators:
            logger.info(f"[ConfigValidator] --- Validating {name} ---")
            try:
                errors = validator_fn()
                all_errors.extend(errors)
                if errors:
                    logger.warning(f"[ConfigValidator] {name}: {len(errors)} issue(s) found")
                else:
                    logger.info(f"[ConfigValidator] {name}: ✓ PASS")
            except Exception as e:
                err = ConfigError(
                    severity=Severity.CRITICAL,
                    field_path="(exception)",
                    message=f"Validation failed with exception: {e}",
                    file=name,
                )
                all_errors.append(err)
                logger.exception(f"[ConfigValidator] Exception validating {name}: {e}")

        self.errors = all_errors

        critical_count = sum(1 for e in all_errors if e.severity == Severity.CRITICAL)
        warning_count = sum(1 for e in all_errors if e.severity == Severity.WARNING)

        logger.info("=" * 60)
        logger.info(f"[ConfigValidator] Validation complete: {critical_count} CRITICAL, {warning_count} WARNING")
        if critical_count > 0:
            logger.error(f"[ConfigValidator] 🔴 {critical_count} CRITICAL issues must be fixed before trading!")
        if warning_count > 0:
            logger.warning(f"[ConfigValidator] 🟡 {warning_count} warnings should be reviewed")
        if critical_count == 0 and warning_count == 0:
            logger.info("[ConfigValidator] ✅ All config files passed validation")
        logger.info("=" * 60)

        return all_errors


def run_config_validation(
    config_root: Optional[str] = None,
    hard_fail: bool = False,
    print_output: bool = True,
) -> List[ConfigError]:
    logger.info("[run_config_validation] Starting startup config validation")

    validator = ConfigValidator(config_root=config_root)
    errors = validator.validate_all()

    if print_output:
        if not errors:
            print("\n[CONFIG VALIDATION] ✅ All config files passed validation\n")
        else:
            print("\n" + "=" * 60)
            print("CONFIG VALIDATION RESULTS")
            print("=" * 60)
            for err in errors:
                print(f"  {err}")
            critical_count = sum(1 for e in errors if e.severity == Severity.CRITICAL)
            warning_count = sum(1 for e in errors if e.severity == Severity.WARNING)
            print(f"\n  Total: {critical_count} CRITICAL, {warning_count} WARNING")
            print("=" * 60 + "\n")

    if hard_fail:
        critical_errors = [e for e in errors if e.severity == Severity.CRITICAL]
        if critical_errors:
            logger.error("[run_config_validation] FAIL-CLOSED: Critical config errors blocking startup")
            print("\n🔴 FAIL-CLOSED: Critical config errors prevent startup!")
            for err in critical_errors:
                print(f"  {err}")
            raise SystemExit(1)

    logger.info(f"[run_config_validation] Validation complete, returning {len(errors)} errors")
    return errors
