#!/usr/bin/env python3
"""
Config Key Consistency Tests
=============================
Verifies that config file keys match what the code actually uses.

Catches the CRITICAL class of bugs where config files use keys like
'crypto_bot' but code expects 'cryptobot', causing entire bot thread
configurations to be silently ignored.

Run:
    cd export && python tests/test_config_key_consistency.py
"""

import os
import re
import sys
import yaml

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
SRC_DIR = os.path.join(BASE_DIR, "src", "trading_hydra")

results = []


def record(name, passed, detail=""):
    results.append({"name": name, "passed": passed, "detail": detail})
    status = "PASS" if passed else "FAIL"
    msg = f"  [{status}] {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)


def load_yaml(filename):
    path = os.path.join(CONFIG_DIR, filename)
    with open(path) as f:
        return yaml.safe_load(f)


def read_file(path):
    with open(path) as f:
        return f.read()


def extract_bots_yaml_get_keys(source):
    """Extract keys used via bots_config.get('key', ...) patterns."""
    pattern = r'bots_config\.get\(\s*["\'](\w+)["\']\s*[,)]'
    return set(re.findall(pattern, source))


def extract_settings_get_keys(source):
    """Extract keys used via self._settings.get('key', ...) patterns."""
    pattern = r'(?:self\._settings|settings)\.get\(\s*["\'](\w+)["\']\s*[,)]'
    return set(re.findall(pattern, source))


def find_snake_case_mismatches(keys):
    """Find potential snake_case vs no-separator mismatches in a set of keys."""
    mismatches = []
    normalized = {}
    for k in keys:
        norm = k.replace("_", "").lower()
        if norm in normalized and normalized[norm] != k:
            mismatches.append((normalized[norm], k))
        normalized[norm] = k
    return mismatches


# ─── Load configs ───────────────────────────────────────────────

print("=" * 60)
print("Config Key Consistency Tests")
print("=" * 60)
print()

bots_yaml = load_yaml("bots.yaml")
settings_yaml = load_yaml("settings.yaml")
bots_yaml_keys = set(bots_yaml.keys())
settings_yaml_keys = set(settings_yaml.keys())

print(f"bots.yaml top-level keys ({len(bots_yaml_keys)}): {sorted(bots_yaml_keys)}")
print(f"settings.yaml top-level keys ({len(settings_yaml_keys)}): {sorted(settings_yaml_keys)}")
print()

# ─── Test 1: dedicated_threads.py bot config keys ──────────────

print("--- Test Group 1: dedicated_threads.py config key references ---")

dt_source = read_file(os.path.join(SRC_DIR, "services", "dedicated_threads.py"))
dt_config_keys = extract_bots_yaml_get_keys(dt_source)

for key in sorted(dt_config_keys):
    exists = key in bots_yaml_keys
    record(
        f"dedicated_threads config key '{key}' exists in bots.yaml",
        exists,
        "" if exists else f"MISSING — code reads bots_config.get('{key}') but key not in bots.yaml"
    )

print()

# ─── Test 2: Bot module config key references ──────────────────

print("--- Test Group 2: Bot module config key references ---")

bot_files = {
    "momentum_bot.py": os.path.join(SRC_DIR, "bots", "momentum_bot.py"),
    "crypto_bot.py": os.path.join(SRC_DIR, "bots", "crypto_bot.py"),
    "bounce_bot.py": os.path.join(SRC_DIR, "bots", "bounce_bot.py"),
    "twenty_minute_bot.py": os.path.join(SRC_DIR, "bots", "twenty_minute_bot.py"),
    "options_bot.py": os.path.join(SRC_DIR, "bots", "options_bot.py"),
}

all_bot_config_keys = set()
for filename, filepath in bot_files.items():
    if not os.path.exists(filepath):
        record(f"Bot file {filename} exists", False, "FILE NOT FOUND")
        continue

    source = read_file(filepath)
    keys = extract_bots_yaml_get_keys(source)
    all_bot_config_keys.update(keys)

    for key in sorted(keys):
        exists = key in bots_yaml_keys
        record(
            f"{filename} config key '{key}' exists in bots.yaml",
            exists,
            "" if exists else f"MISSING — {filename} reads '{key}' but key not in bots.yaml"
        )

    config_key_pattern = r'config_key\s*=\s*["\'](\w+)["\']'
    explicit_keys = re.findall(config_key_pattern, source)
    for key in explicit_keys:
        exists = key in bots_yaml_keys
        record(
            f"{filename} explicit config_key '{key}' exists in bots.yaml",
            exists,
            "" if exists else f"MISSING — {filename} sets config_key='{key}' but key not in bots.yaml"
        )
        all_bot_config_keys.add(key)

print()

# ─── Test 3: Snake_case vs no-separator mismatch detection ─────

print("--- Test Group 3: Snake_case vs no-separator mismatch detection ---")

known_bot_names = [
    "cryptobot", "crypto_bot",
    "bouncebot", "bounce_bot",
    "optionsbot", "options_bot",
    "optionsbot_0dte", "options_bot_0dte",
    "exitbot", "exit_bot",
    "portfoliobot", "portfolio_bot",
    "twentyminute_bot", "twenty_minute_bot",
    "momentumbot", "momentum_bot",
]

for variant in known_bot_names:
    if variant in bots_yaml_keys:
        norm = variant.replace("_", "").lower()
        for other in known_bot_names:
            if other != variant and other.replace("_", "").lower() == norm:
                if other in bots_yaml_keys:
                    record(
                        f"No duplicate normalized key for '{variant}'",
                        False,
                        f"CONFLICT: Both '{variant}' and '{other}' exist in bots.yaml"
                    )

all_code_keys = all_bot_config_keys | dt_config_keys
for code_key in sorted(all_code_keys):
    if code_key not in bots_yaml_keys:
        norm = code_key.replace("_", "").lower()
        near_matches = [
            k for k in bots_yaml_keys
            if k.replace("_", "").lower() == norm and k != code_key
        ]
        if near_matches:
            record(
                f"No naming mismatch for code key '{code_key}'",
                False,
                f"CODE uses '{code_key}' but bots.yaml has '{near_matches[0]}' — snake_case mismatch!"
            )
        else:
            pass

for yaml_key in sorted(bots_yaml_keys):
    norm = yaml_key.replace("_", "").lower()
    for code_key in all_code_keys:
        if code_key != yaml_key and code_key.replace("_", "").lower() == norm:
            record(
                f"No naming mismatch for yaml key '{yaml_key}'",
                False,
                f"bots.yaml has '{yaml_key}' but code uses '{code_key}' — snake_case mismatch!"
            )

no_mismatches = all(r["passed"] for r in results if "mismatch" in r["name"].lower())
if no_mismatches:
    record("No snake_case vs no-separator mismatches detected", True)

print()

# ─── Test 4: Risk module settings.yaml key references ──────────

print("--- Test Group 4: Risk module settings.yaml key references ---")

risk_files = {
    "risk_integration.py": os.path.join(SRC_DIR, "risk", "risk_integration.py"),
    "policy_gate.py": os.path.join(SRC_DIR, "risk", "policy_gate.py"),
    "dynamic_budget.py": os.path.join(SRC_DIR, "risk", "dynamic_budget.py"),
}

for filename, filepath in risk_files.items():
    if not os.path.exists(filepath):
        record(f"Risk file {filename} exists", False, "FILE NOT FOUND")
        continue

    source = read_file(filepath)
    keys = extract_settings_get_keys(source)

    for key in sorted(keys):
        exists = key in settings_yaml_keys
        record(
            f"{filename} settings key '{key}' exists in settings.yaml",
            exists,
            "" if exists else f"MISSING — {filename} reads settings['{key}'] but key not in settings.yaml"
        )

print()

# ─── Test 5: Settings.yaml nested key validation ──────────────

print("--- Test Group 5: Settings.yaml nested key spot checks ---")

critical_paths = [
    ("risk", "global_max_daily_loss_pct", "risk.global_max_daily_loss_pct"),
    ("risk", "max_orders_per_minute", "risk.max_orders_per_minute"),
    ("policy_gate", "enabled", "policy_gate.enabled"),
    ("policy_gate", "slippage_budget_pct", "policy_gate.slippage_budget_pct"),
    ("policy_gate", "min_ml_confidence", "policy_gate.min_ml_confidence"),
    ("policy_gate", "require_ml_signal", "policy_gate.require_ml_signal"),
    ("dynamic_budget", "enabled", "dynamic_budget.enabled"),
    ("dynamic_budget", "daily_budget_pct", "dynamic_budget.daily_budget_pct"),
    ("dynamic_budget", "max_position_pct", "dynamic_budget.max_position_pct"),
    ("dynamic_budget", "min_daily_budget_usd", "dynamic_budget.min_daily_budget_usd"),
    ("dynamic_budget", "max_daily_budget_usd", "dynamic_budget.max_daily_budget_usd"),
    ("dynamic_budget", "dd_threshold_reduce", "dynamic_budget.dd_threshold_reduce"),
    ("dynamic_budget", "dd_threshold_halt", "dynamic_budget.dd_threshold_halt"),
    ("dynamic_budget", "dd_min_multiplier", "dynamic_budget.dd_min_multiplier"),
    ("dynamic_budget", "performance_scaling_enabled", "dynamic_budget.performance_scaling_enabled"),
    ("dynamic_budget", "perf_max_boost", "dynamic_budget.perf_max_boost"),
    ("risk_integration", "enabled", "risk_integration.enabled"),
    ("ml", "enabled", "ml.enabled"),
]

for section, key, path_label in critical_paths:
    section_data = settings_yaml.get(section)
    if section_data is None:
        record(f"settings.yaml path '{path_label}'", False, f"Section '{section}' missing entirely")
        continue
    if not isinstance(section_data, dict):
        record(f"settings.yaml path '{path_label}'", False, f"Section '{section}' is not a dict")
        continue
    exists = key in section_data
    record(
        f"settings.yaml path '{path_label}' exists",
        exists,
        "" if exists else f"Key '{key}' missing from settings.yaml['{section}']"
    )

print()

# ─── Test 6: Bot-specific bots.yaml structure checks ──────────

print("--- Test Group 6: Bot-specific bots.yaml structure validation ---")

bot_required_fields = {
    "exitbot": ["enabled"],
    "cryptobot": ["enabled", "bot_id", "pairs", "session", "execution"],
    "bouncebot": ["enabled", "bot_id", "pairs", "session"],
    "twentyminute_bot": ["enabled", "bot_id", "tickers", "session"],
    "optionsbot": ["enabled", "bot_id", "tickers", "session", "strategies"],
    "optionsbot_0dte": ["enabled", "bot_id", "tickers", "session", "strategies"],
}

for bot_key, required in bot_required_fields.items():
    bot_data = bots_yaml.get(bot_key)
    if bot_data is None:
        record(f"bots.yaml['{bot_key}'] section exists", False, "Section missing entirely")
        continue

    record(f"bots.yaml['{bot_key}'] section exists", True)

    for field in required:
        has_field = field in bot_data
        record(
            f"bots.yaml['{bot_key}'].{field} exists",
            has_field,
            "" if has_field else f"Required field '{field}' missing from {bot_key} config"
        )

momentum_bots = bots_yaml.get("momentum_bots")
if momentum_bots is None:
    record("bots.yaml['momentum_bots'] section exists", False, "Section missing entirely")
elif not isinstance(momentum_bots, list):
    record("bots.yaml['momentum_bots'] is a list", False, f"Expected list, got {type(momentum_bots).__name__}")
else:
    record("bots.yaml['momentum_bots'] is a list", True, f"{len(momentum_bots)} bot(s) configured")
    for i, bot in enumerate(momentum_bots):
        has_id = "bot_id" in bot
        record(
            f"momentum_bots[{i}] has bot_id",
            has_id,
            f"bot_id='{bot.get('bot_id', 'MISSING')}'" if has_id else "bot_id field missing"
        )

print()

# ─── Test 7: Cross-reference dedicated_threads bot_ids with bots.yaml bot_ids ─

print("--- Test Group 7: Dedicated thread bot_ids match bots.yaml bot_ids ---")

dt_bot_id_pattern = r'bot_id\s*=\s*["\'](\w+)["\']'
dt_bot_ids = set(re.findall(dt_bot_id_pattern, dt_source))

yaml_bot_ids = set()
for key in ["exitbot", "cryptobot", "bouncebot", "twentyminute_bot", "optionsbot", "optionsbot_0dte"]:
    section = bots_yaml.get(key, {})
    if isinstance(section, dict) and "bot_id" in section:
        yaml_bot_ids.add(section["bot_id"])

if isinstance(bots_yaml.get("momentum_bots"), list):
    for bot in bots_yaml["momentum_bots"]:
        if isinstance(bot, dict) and "bot_id" in bot:
            yaml_bot_ids.add(bot["bot_id"])

exitbot_section = bots_yaml.get("exitbot", {})
if isinstance(exitbot_section, dict) and "bot_id" not in exitbot_section:
    yaml_bot_ids.add("exitbot")

for dt_id in sorted(dt_bot_ids):
    found = dt_id in yaml_bot_ids
    if not found and dt_id == "exitbot":
        found = "exitbot" in bots_yaml_keys
    record(
        f"dedicated_threads bot_id '{dt_id}' has config in bots.yaml",
        found,
        "" if found else f"bot_id '{dt_id}' used in dedicated_threads.py but no matching bot_id in bots.yaml"
    )

print()

# ─── Summary ───────────────────────────────────────────────────

print("=" * 60)
print("SUMMARY")
print("=" * 60)

total = len(results)
passed = sum(1 for r in results if r["passed"])
failed = sum(1 for r in results if not r["passed"])

print(f"Total checks: {total}")
print(f"Passed:       {passed}")
print(f"Failed:       {failed}")
print()

if failed > 0:
    print("FAILURES:")
    for r in results:
        if not r["passed"]:
            print(f"  ✗ {r['name']}")
            if r["detail"]:
                print(f"    → {r['detail']}")
    print()
    print("RESULT: FAIL")
    sys.exit(1)
else:
    print("All checks passed!")
    print()
    print("RESULT: PASS")
    sys.exit(0)
