# Configuration Precedence

Trading Hydra uses a layered configuration system. This document explains the merge order, which keys belong where, and how to safely override settings.

## Merge Order (later overrides earlier)

```
1. config/settings.yaml       (base system settings)
2. config/bots.yaml           (per-bot configurations)
3. config/modes/small_account.yaml  (if micro/small account detected)
4. config/modes/dev.yaml      (if development mode)
5. Environment variables      (ALPACA_*, DATABASE_URL, etc.)
6. Account mode params        (runtime adjustments based on equity)
```

**One-line summary:** `settings.yaml → bots.yaml → modes/*.yaml → env_vars → account_mode_params`

## Which Keys Belong Where

### config/settings.yaml (Global System Settings)
- `system.timezone` - PST timezone setting
- `system.log_path` - JSONL log file path
- `system.state_db_path` - SQLite state database path
- `market_hours.*` - Market open/close times
- `runner.loop_interval_seconds` - Main loop frequency
- `console.mode` - Output mode (event/full/quiet)
- `safety.*` - Fail-closed, cooldown settings
- `risk.*` - Global risk parameters (max daily loss, etc.)
- `caching.*` - Quote cache TTL, staleness thresholds
- `health.*` - Health monitoring thresholds

### config/bots.yaml (Per-Bot Configurations)
- `momentum_bot.*` - Stock momentum settings
- `crypto_bot.*` - Cryptocurrency trading settings
- `options_bot.*` - Options trading settings
- `twentyminute_bot.*` - Intraday strategy settings

Each bot section includes:
- `enabled` - Boolean to activate/deactivate
- `session.*` - Trading hours for this bot
- `risk.*` - Per-bot risk limits
- `strategy.*` - Signal generation parameters

### config/modes/small_account.yaml (Account Size Adjustments)
Override values when account equity < $10,000:
- Reduced position sizes
- Tighter risk limits
- Adjusted bot settings for smaller capital

### config/modes/dev.yaml (Development Overrides)
Used for local development:
- Faster loop intervals
- Relaxed safety limits
- Debug logging enabled

### Environment Variables
- `ALPACA_KEY` - Alpaca API key (required)
- `ALPACA_SECRET` - Alpaca API secret (required)
- `ALPACA_PAPER` - "true" for paper trading (default)
- `DATABASE_URL` - PostgreSQL connection string

## How to Override Safely

### DO:
1. **Check effective config first** - Run the system and check `logs/effective_config.json` to see what values are actually active
2. **Use the right file** - Global settings in settings.yaml, bot-specific in bots.yaml
3. **Test in paper mode** - Always verify changes work in paper trading first
4. **Check config doctor** - The system validates configs at startup and blocks on critical conflicts

### DON'T:
1. **Don't set conflicting values** - e.g., `quote_ttl_seconds > max_quote_staleness_seconds` will block startup
2. **Don't edit during runtime** - Config changes require restart
3. **Don't bypass small account mode** - If your equity triggers it, the adjustments exist for safety

## Config Doctor Checks

The config doctor runs at startup and enforces these rules:

| Check | Severity | Rule |
|-------|----------|------|
| quote_freshness | HIGH | `caching.quote_ttl_seconds <= risk.max_quote_staleness_seconds` |
| overtrade_risk | MEDIUM | `options_bot.risk.max_trades_per_day <= 20` |
| performance | LOW | `runner.loop_interval_seconds >= 3` |
| strategy_mismatch | MEDIUM | Small account mode + CryptoBot enabled |

**HIGH severity conflicts block startup (fail-closed).**

## Viewing Effective Config

At startup, the system:
1. Prints a summary banner showing loaded files and key values
2. Writes complete merged config to `logs/effective_config.json`
3. Runs config doctor and prints any conflicts

Example startup banner:
```
============================================================
EFFECTIVE CONFIG SUMMARY
============================================================
Run ID: run_20260115_1430Z_x7k2
Generated: 2026-01-15T14:30:00.000000Z
Account Mode: STANDARD
Small Account Mode: disabled

--- CONFIG FILES LOADED (in order) ---
  1) /home/user/trading_hydra/config/settings.yaml
  2) /home/user/trading_hydra/config/bots.yaml
  Merge: settings.yaml → bots.yaml → modes/*.yaml → env_vars → account_mode_params
...
============================================================
```

## Troubleshooting

**Q: My setting isn't being applied**
- Check `logs/effective_config.json` to see what value is actually active
- Verify you're editing the correct file (settings.yaml vs bots.yaml)
- Restart the system after config changes

**Q: Startup is blocked with "FAIL-CLOSED"**
- Check the config doctor output for the specific conflict
- Fix the conflict in the appropriate config file
- Common: quote_ttl_seconds set higher than max_quote_staleness_seconds

**Q: How do I know which file a setting came from?**
- The `config_files_loaded` list in effective_config.json shows load order
- Later files override earlier ones
- Environment variables override everything
