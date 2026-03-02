"""Tests for config schema validation module.

Run with: cd export && python -m pytest tests/test_config_validation.py -v
"""
import os
import sys
import tempfile
import yaml
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trading_hydra.core.config_schema import (
    ConfigValidator,
    ConfigError,
    Severity,
    run_config_validation,
)


def _write_yaml(tmpdir, filename, data):
    filepath = os.path.join(tmpdir, filename)
    with open(filepath, "w") as f:
        yaml.dump(data, f, default_flow_style=False)
    return filepath


def _make_valid_settings():
    return {
        "system": {
            "timezone": "America/Los_Angeles",
            "log_path": "./logs/app.jsonl",
            "state_db_path": "./state/trading_state.db",
        },
        "safety": {
            "fail_closed": True,
            "allow_budget_fallback": False,
            "global_cooldown_minutes": 15,
        },
        "risk": {
            "global_max_daily_loss_pct": 5.0,
            "max_orders_per_minute": 10,
        },
        "runner": {
            "loop_interval_seconds": 15,
        },
        "trading": {
            "global_halt": False,
            "allow_live": True,
        },
        "health": {
            "max_price_staleness_seconds": 15,
        },
        "caching": {
            "quote_ttl_seconds": 15,
        },
    }


def _make_valid_bots():
    return {
        "exitbot": {"enabled": True},
        "cryptobot": {"enabled": True, "risk": {"max_trades_per_day": 300, "max_concurrent_positions": 8}},
        "optionsbot": {"enabled": True, "risk": {"max_trades_per_day": 15, "max_concurrent_positions": 8, "max_position_size_usd": 500}},
        "bouncebot": {"enabled": True, "risk": {"max_trades_per_session": 3}},
        "portfoliobot": {"enabled": True, "cash_reserve_pct": 15},
        "momentum_bots": [{"bot_id": "mom_AAPL", "enabled": True, "ticker": "AAPL"}],
        "twentyminute_bot": {"enabled": True},
    }


def _make_valid_sensors():
    return {
        "polling": {
            "quotes_interval": 15,
            "bars_1m_interval": 60,
            "bars_5m_interval": 300,
            "bars_daily_interval": 900,
            "indicators_interval": 60,
            "regime_interval": 300,
            "breadth_interval": 60,
        },
        "cache": {"quote_ttl": 15},
        "startup": {"warmup_timeout": 30, "ready_threshold": 0.5},
    }


def _make_valid_ticker_universe():
    return {
        "tiers": {
            "core_indices": ["SPY", "QQQ", "IWM"],
            "large_cap_tech": ["AAPL", "MSFT", "NVDA"],
        },
        "limits": {"max_universe_size": 25, "max_per_sector": 4},
        "filters": {"min_avg_volume": 1000000},
        "stocks": {"candidates": ["AAPL", "MSFT"]},
        "options": {"candidates": ["SPY", "QQQ"]},
    }


def _make_valid_watchlists():
    return {
        "tickers": {
            "SPY": {"tags": ["CORE_MACRO", "INDEX"], "priority": 1},
            "AAPL": {"tags": ["COMPUTE_CORE", "MEGA_CAP"], "priority": 1},
        },
        "watchlists": {
            "core_macro": {"description": "Core market", "tickers": ["SPY"]},
        },
    }


def _create_full_valid_config(tmpdir):
    _write_yaml(tmpdir, "settings.yaml", _make_valid_settings())
    _write_yaml(tmpdir, "bots.yaml", _make_valid_bots())
    _write_yaml(tmpdir, "sensors.yaml", _make_valid_sensors())
    _write_yaml(tmpdir, "ticker_universe.yaml", _make_valid_ticker_universe())
    _write_yaml(tmpdir, "watchlists.yaml", _make_valid_watchlists())


class TestValidConfigPasses:
    def test_validate_all_passes_with_correct_configs(self, tmp_path):
        _create_full_valid_config(str(tmp_path))
        validator = ConfigValidator(config_root=str(tmp_path))
        errors = validator.validate_all()
        critical_errors = [e for e in errors if e.severity == Severity.CRITICAL]
        assert len(critical_errors) == 0, f"Unexpected critical errors: {critical_errors}"

    def test_run_config_validation_passes(self, tmp_path):
        _create_full_valid_config(str(tmp_path))
        errors = run_config_validation(config_root=str(tmp_path), print_output=False)
        critical_errors = [e for e in errors if e.severity == Severity.CRITICAL]
        assert len(critical_errors) == 0


class TestWrongBotKeyNames:
    def test_catches_crypto_bot_instead_of_cryptobot(self, tmp_path):
        bots = _make_valid_bots()
        del bots["cryptobot"]
        bots["crypto_bot"] = {"enabled": True}
        _write_yaml(str(tmp_path), "bots.yaml", bots)

        validator = ConfigValidator(config_root=str(tmp_path))
        errors = validator.validate_bots()
        wrong_key_errors = [
            e for e in errors
            if e.severity == Severity.CRITICAL and "crypto_bot" in e.field_path
        ]
        assert len(wrong_key_errors) >= 1, f"Should catch 'crypto_bot' naming error, got: {errors}"
        assert any("cryptobot" in e.message for e in wrong_key_errors)

    def test_catches_exit_bot_instead_of_exitbot(self, tmp_path):
        bots = _make_valid_bots()
        del bots["exitbot"]
        bots["exit_bot"] = {"enabled": True}
        _write_yaml(str(tmp_path), "bots.yaml", bots)

        validator = ConfigValidator(config_root=str(tmp_path))
        errors = validator.validate_bots()
        wrong_key_errors = [
            e for e in errors
            if e.severity == Severity.CRITICAL and "exit_bot" in e.field_path
        ]
        assert len(wrong_key_errors) >= 1

    def test_catches_bounce_bot_instead_of_bouncebot(self, tmp_path):
        bots = _make_valid_bots()
        del bots["bouncebot"]
        bots["bounce_bot"] = {"enabled": True}
        _write_yaml(str(tmp_path), "bots.yaml", bots)

        validator = ConfigValidator(config_root=str(tmp_path))
        errors = validator.validate_bots()
        wrong_key_errors = [
            e for e in errors
            if e.severity == Severity.CRITICAL and "bounce_bot" in e.field_path
        ]
        assert len(wrong_key_errors) >= 1

    def test_catches_options_bot_instead_of_optionsbot(self, tmp_path):
        bots = _make_valid_bots()
        del bots["optionsbot"]
        bots["options_bot"] = {"enabled": True}
        _write_yaml(str(tmp_path), "bots.yaml", bots)

        validator = ConfigValidator(config_root=str(tmp_path))
        errors = validator.validate_bots()
        wrong_key_errors = [
            e for e in errors
            if e.severity == Severity.CRITICAL and "options_bot" in e.message
        ]
        assert len(wrong_key_errors) >= 1

    def test_catches_options_bot_0dte_instead_of_optionsbot_0dte(self, tmp_path):
        bots = _make_valid_bots()
        bots["options_bot_0dte"] = {"enabled": True}
        _write_yaml(str(tmp_path), "bots.yaml", bots)

        validator = ConfigValidator(config_root=str(tmp_path))
        errors = validator.validate_bots()
        wrong_key_errors = [
            e for e in errors
            if e.severity == Severity.CRITICAL and "options_bot_0dte" in e.field_path
        ]
        assert len(wrong_key_errors) >= 1


class TestDangerousSafetyLimits:
    def test_catches_100_pct_daily_loss(self, tmp_path):
        settings = _make_valid_settings()
        settings["risk"]["global_max_daily_loss_pct"] = 100.0
        _write_yaml(str(tmp_path), "settings.yaml", settings)

        validator = ConfigValidator(config_root=str(tmp_path))
        errors = validator.validate_settings()
        loss_errors = [
            e for e in errors
            if e.severity == Severity.CRITICAL and "global_max_daily_loss_pct" in e.field_path
        ]
        assert len(loss_errors) >= 1, f"Should catch 100% daily loss limit, got: {errors}"

    def test_catches_50_pct_daily_loss(self, tmp_path):
        settings = _make_valid_settings()
        settings["risk"]["global_max_daily_loss_pct"] = 50.0
        _write_yaml(str(tmp_path), "settings.yaml", settings)

        validator = ConfigValidator(config_root=str(tmp_path))
        errors = validator.validate_settings()
        loss_errors = [
            e for e in errors
            if e.severity == Severity.CRITICAL and "global_max_daily_loss_pct" in e.field_path
        ]
        assert len(loss_errors) >= 1

    def test_catches_fail_closed_false(self, tmp_path):
        settings = _make_valid_settings()
        settings["safety"]["fail_closed"] = False
        _write_yaml(str(tmp_path), "settings.yaml", settings)

        validator = ConfigValidator(config_root=str(tmp_path))
        errors = validator.validate_settings()
        safety_errors = [
            e for e in errors
            if e.severity == Severity.CRITICAL and "fail_closed" in e.field_path
        ]
        assert len(safety_errors) >= 1, f"Should catch fail_closed=false, got: {errors}"

    def test_catches_excessive_orders_per_minute(self, tmp_path):
        settings = _make_valid_settings()
        settings["risk"]["max_orders_per_minute"] = 100
        _write_yaml(str(tmp_path), "settings.yaml", settings)

        validator = ConfigValidator(config_root=str(tmp_path))
        errors = validator.validate_settings()
        order_errors = [
            e for e in errors
            if e.severity == Severity.CRITICAL and "max_orders_per_minute" in e.field_path
        ]
        assert len(order_errors) >= 1

    def test_5_pct_daily_loss_passes(self, tmp_path):
        settings = _make_valid_settings()
        settings["risk"]["global_max_daily_loss_pct"] = 5.0
        _write_yaml(str(tmp_path), "settings.yaml", settings)

        validator = ConfigValidator(config_root=str(tmp_path))
        errors = validator.validate_settings()
        loss_errors = [
            e for e in errors
            if "global_max_daily_loss_pct" in e.field_path
        ]
        assert len(loss_errors) == 0


class TestMissingRequiredFields:
    def test_missing_risk_section(self, tmp_path):
        settings = _make_valid_settings()
        del settings["risk"]
        _write_yaml(str(tmp_path), "settings.yaml", settings)

        validator = ConfigValidator(config_root=str(tmp_path))
        errors = validator.validate_settings()
        critical = [e for e in errors if e.severity == Severity.CRITICAL]
        assert len(critical) >= 1

    def test_missing_safety_fail_closed(self, tmp_path):
        settings = _make_valid_settings()
        del settings["safety"]["fail_closed"]
        _write_yaml(str(tmp_path), "settings.yaml", settings)

        validator = ConfigValidator(config_root=str(tmp_path))
        errors = validator.validate_settings()
        critical = [
            e for e in errors
            if e.severity == Severity.CRITICAL and "fail_closed" in e.field_path
        ]
        assert len(critical) >= 1


class TestCacheStalenessConflict:
    def test_cache_ttl_exceeds_staleness(self, tmp_path):
        settings = _make_valid_settings()
        settings["caching"]["quote_ttl_seconds"] = 30
        settings["health"]["max_price_staleness_seconds"] = 15
        _write_yaml(str(tmp_path), "settings.yaml", settings)

        validator = ConfigValidator(config_root=str(tmp_path))
        errors = validator.validate_settings()
        staleness_warnings = [
            e for e in errors
            if e.severity == Severity.WARNING and "quote_ttl" in e.field_path
        ]
        assert len(staleness_warnings) >= 1


class TestTickerUniverseValidation:
    def test_empty_tier_warning(self, tmp_path):
        tu = _make_valid_ticker_universe()
        tu["tiers"]["empty_tier"] = []
        _write_yaml(str(tmp_path), "ticker_universe.yaml", tu)

        validator = ConfigValidator(config_root=str(tmp_path))
        errors = validator.validate_ticker_universe()
        empty_warnings = [
            e for e in errors
            if e.severity == Severity.WARNING and "empty_tier" in e.field_path
        ]
        assert len(empty_warnings) >= 1

    def test_missing_tiers_critical(self, tmp_path):
        tu = _make_valid_ticker_universe()
        del tu["tiers"]
        _write_yaml(str(tmp_path), "ticker_universe.yaml", tu)

        validator = ConfigValidator(config_root=str(tmp_path))
        errors = validator.validate_ticker_universe()
        critical = [e for e in errors if e.severity == Severity.CRITICAL and "tiers" in e.field_path]
        assert len(critical) >= 1


class TestRunConfigValidation:
    def test_run_config_validation_standalone(self, tmp_path):
        _create_full_valid_config(str(tmp_path))
        errors = run_config_validation(config_root=str(tmp_path), print_output=True)
        assert isinstance(errors, list)

    def test_hard_fail_on_critical_errors(self, tmp_path):
        settings = _make_valid_settings()
        settings["risk"]["global_max_daily_loss_pct"] = 100.0
        _write_yaml(str(tmp_path), "settings.yaml", settings)
        _write_yaml(str(tmp_path), "bots.yaml", _make_valid_bots())
        _write_yaml(str(tmp_path), "sensors.yaml", _make_valid_sensors())
        _write_yaml(str(tmp_path), "ticker_universe.yaml", _make_valid_ticker_universe())
        _write_yaml(str(tmp_path), "watchlists.yaml", _make_valid_watchlists())

        with pytest.raises(SystemExit):
            run_config_validation(config_root=str(tmp_path), hard_fail=True, print_output=False)


class TestRealConfigFiles:
    def test_validate_real_export_configs(self):
        config_root = os.path.join(os.path.dirname(__file__), "..", "config")
        if not os.path.isdir(config_root):
            pytest.skip("export/config directory not found")

        validator = ConfigValidator(config_root=config_root)
        errors = validator.validate_all()

        critical_errors = [e for e in errors if e.severity == Severity.CRITICAL]
        for err in errors:
            print(f"  {err}")
        assert len(critical_errors) == 0, (
            f"Real config files have {len(critical_errors)} critical errors: "
            + "; ".join(str(e) for e in critical_errors)
        )
