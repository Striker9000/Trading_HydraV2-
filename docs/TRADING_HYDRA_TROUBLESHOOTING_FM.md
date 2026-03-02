# Trading Hydra Troubleshooting Field Manual

## CLASSIFICATION: OPERATOR REFERENCE
**Version**: 1.0  
**Last Updated**: January 2026  
**System**: Trading Hydra Autonomous Trading Platform

---

# PART I: DIAGNOSTIC FRAMEWORK

## Chapter 1: Troubleshooting Philosophy

### 1.1 The OODA Loop for Trading System Issues

```
OBSERVE → ORIENT → DECIDE → ACT

1. OBSERVE: Gather symptoms (logs, dashboard, behavior)
2. ORIENT: Classify the issue category
3. DECIDE: Select diagnostic procedure
4. ACT: Execute fix and verify
```

### 1.2 Issue Categories

| Category | Symptoms | Priority |
|----------|----------|----------|
| **CRITICAL** | System halted, no trades executing, data loss | Immediate |
| **HIGH** | Trades failing, positions unmanaged, API errors | Within 15 min |
| **MEDIUM** | Performance degraded, ML not scoring, slow response | Within 1 hour |
| **LOW** | Cosmetic issues, non-essential features, warnings | Next maintenance |

### 1.3 First Response Checklist

Before diving into specific troubleshooting:

```bash
# 1. Check system status
tail -20 logs/app.jsonl | jq .

# 2. Check for halt conditions
grep -i "halt\|error\|fail" logs/app.jsonl | tail -10 | jq .

# 3. Verify Alpaca connectivity
python test_alpaca_connection.py

# 4. Check state database
sqlite3 state/trading_state.db "SELECT key, substr(value, 1, 50) FROM kv_store ORDER BY key LIMIT 20;"
```

---

# PART II: SYSTEM HALT ISSUES

## Chapter 2: System Won't Start

### 2.1 Python Import Errors

**Symptom**: `ModuleNotFoundError` or `ImportError` on startup

**Diagnosis**:
```bash
# Check if module exists
python -c "from src.trading_hydra.orchestrator import TradingOrchestrator"

# Check Python path
python -c "import sys; print('\n'.join(sys.path))"
```

**Resolution**:

| Error | Cause | Fix |
|-------|-------|-----|
| `No module named 'alpaca'` | Missing dependency | `pip install alpaca-py` |
| `No module named 'src'` | Wrong working directory | `cd` to project root |
| `Cannot import name 'X'` | Circular import or typo | Check file for errors |

### 2.2 Configuration Parse Errors

**Symptom**: `yaml.YAMLError` or config validation failure

**Diagnosis**:
```bash
# Validate YAML syntax
python -c "import yaml; yaml.safe_load(open('config/settings.yaml'))"
python -c "import yaml; yaml.safe_load(open('config/bots.yaml'))"
```

**Common YAML Errors**:

| Error | Cause | Fix |
|-------|-------|-----|
| `expected <block end>` | Indentation mismatch | Fix spaces (use 2-space indent) |
| `found character that cannot start` | Tab characters | Replace tabs with spaces |
| `mapping values not allowed` | Missing colon | Add `:` after key name |

### 2.3 Database Lock Errors

**Symptom**: `sqlite3.OperationalError: database is locked`

**Diagnosis**:
```bash
# Check for running processes
ps aux | grep python | grep trading

# Check database file
ls -la state/trading_state.db
```

**Resolution**:
```bash
# Option 1: Kill other processes
pkill -f "python main.py"

# Option 2: Wait and retry (lock timeout)
# System will auto-retry for 30 seconds

# Option 3: Fresh start (loses state)
python main.py --fresh-start
```

### 2.4 Port Already in Use

**Symptom**: `OSError: [Errno 98] Address already in use`

**Diagnosis**:
```bash
# Find what's using port 5000
lsof -i :5000
netstat -tlnp | grep 5000
```

**Resolution**:
```bash
# Kill the process using the port
kill -9 $(lsof -t -i:5000)

# Or use a different port
export PORT=5001
python main.py
```

---

## Chapter 3: Trading Halted

### 3.1 Global Halt Active

**Symptom**: Dashboard shows "HALTED", no new trades

**Diagnosis**:
```bash
# Check halt state
sqlite3 state/trading_state.db "SELECT value FROM kv_store WHERE key='global_halt';"

# Check halt reason in logs
grep -i "halt" logs/app.jsonl | tail -5 | jq .
```

**Resolution**:

| Halt Reason | Action |
|-------------|--------|
| Daily P&L limit | Wait for cooloff (390 min) or next trading day |
| API failure | Verify Alpaca credentials and status |
| Data staleness | Check market data feed |
| Manual halt | Set `trading.global_halt: false` in settings.yaml |

```bash
# Clear halt via dashboard or:
sqlite3 state/trading_state.db "DELETE FROM kv_store WHERE key='global_halt';"
```

### 3.2 Daily P&L Limit Exceeded

**Symptom**: Halt triggered, logs show "daily loss limit exceeded"

**Diagnosis**:
```bash
# Check daily P&L
grep "daily_pnl" logs/app.jsonl | tail -1 | jq .

# Check limit setting
grep "global_max_daily_loss" config/settings.yaml
```

**Resolution**:
- Wait for cooloff period (390 minutes = 6.5 hours)
- Or wait until next trading day (state resets at midnight PST)
- Review positions and strategy if this happens frequently

### 3.3 API Authentication Failure

**Symptom**: `alpaca.common.exceptions.APIError: forbidden`

**Diagnosis**:
```bash
# Test credentials
python -c "
import os
print('ALPACA_KEY:', 'SET' if os.getenv('ALPACA_KEY') else 'MISSING')
print('ALPACA_SECRET:', 'SET' if os.getenv('ALPACA_SECRET') else 'MISSING')
print('ALPACA_PAPER:', os.getenv('ALPACA_PAPER', 'not set'))
"
```

**Resolution**:

| Issue | Fix |
|-------|-----|
| Missing credentials | Set environment variables |
| Wrong credentials | Get new keys from Alpaca dashboard |
| Paper vs Live mismatch | Verify `ALPACA_PAPER` matches key type |
| Rate limited | Wait 1 minute, reduce `loop_interval_seconds` |

### 3.4 Stale Data Halt

**Symptom**: Halt triggered, logs show "data staleness exceeded"

**Diagnosis**:
```bash
# Check quote ages
grep "quote_age\|stale" logs/app.jsonl | tail -10 | jq .

# Check cache settings
grep "quote_ttl\|staleness" config/settings.yaml
```

**Resolution**:
- Verify internet connectivity
- Check Alpaca API status: https://status.alpaca.markets/
- Increase `max_quote_staleness_seconds` if network is slow
- Clear quote cache: restart system

---

# PART III: TRADE EXECUTION ISSUES

## Chapter 4: No Trades Executing

### 4.1 Bot Disabled

**Symptom**: Specific bot not generating trades

**Diagnosis**:
```bash
# Check bot enabled status
grep "enabled:" config/bots.yaml
```

**Resolution**:
```yaml
# config/bots.yaml - set to true
momentum_bots:
  - enabled: true  # Change from false
```

### 4.2 Outside Trading Session

**Symptom**: Bot active but no trades during certain hours

**Diagnosis**:
```bash
# Check current time vs session
date
grep -A5 "session:" config/bots.yaml
```

**Trading Sessions (PST)**:

| Bot | Start | End |
|-----|-------|-----|
| MomentumBot | 06:35 | 12:55 |
| OptionsBot | 06:40 | 12:30 |
| TwentyMinuteBot | 06:30 | 07:50 |
| CryptoBot | 00:00 | 23:59 |

### 4.3 ML Score Too Low

**Symptom**: Trade candidates rejected, logs show "ML score below threshold"

**Diagnosis**:
```bash
# Check recent ML scores
grep "ml_score\|probability" logs/app.jsonl | tail -20 | jq .

# Check threshold
grep "min_probability" config/settings.yaml
```

**Resolution**:
- Lower `ml.min_probability` (default 0.58, try 0.50)
- Retrain ML model if consistently low scores
- Check if market conditions are unusual (high VIX)

### 4.4 Risk Limits Blocking

**Symptom**: Trades rejected, logs show "risk limit exceeded"

**Diagnosis**:
```bash
# Check risk reasons
grep -i "risk\|block\|reject" logs/app.jsonl | tail -20 | jq .
```

**Common Risk Blocks**:

| Block Reason | Cause | Resolution |
|--------------|-------|------------|
| Max trades per day | Hit daily limit | Wait for next day |
| Max concurrent positions | Too many open | Close some positions |
| Correlation too high | Similar positions | Wait for diversity |
| Sector exposure | Concentrated | Trade different sector |
| Insufficient buying power | Low cash | Reduce position size or close positions |

### 4.5 Strategy Kill-Switch Active

**Symptom**: Specific strategy not firing

**Diagnosis**:
```bash
# Check kill-switch status
grep "kill_switch\|frozen" logs/app.jsonl | tail -10 | jq .
```

**Resolution**:
- Wait for cooloff period (60 minutes default)
- Check strategy's recent performance
- Review `max_drawdown_usd` setting in strategy YAML

---

## Chapter 5: Order Failures

### 5.1 Insufficient Buying Power

**Symptom**: `insufficient buying power` error

**Diagnosis**:
```bash
# Check account status
python -c "
from src.trading_hydra.services.alpaca_client import AlpacaClient
client = AlpacaClient()
acct = client.get_account()
print(f'Equity: {acct.equity}')
print(f'Buying Power: {acct.buying_power}')
print(f'Cash: {acct.cash}')
"
```

**Resolution**:
- Reduce position sizes in config
- Close existing positions to free capital
- Increase `cash_reserve_pct` to prevent over-allocation

### 5.2 Market Closed

**Symptom**: `market is closed` error for stock/options orders

**Diagnosis**:
```bash
# Check market status
python -c "
from src.trading_hydra.services.alpaca_client import AlpacaClient
client = AlpacaClient()
clock = client.get_clock()
print(f'Market Open: {clock.is_open}')
print(f'Next Open: {clock.next_open}')
"
```

**Resolution**:
- Wait for market to open
- For crypto: should work 24/7, check Alpaca crypto status
- Verify session windows match market hours

### 5.3 Invalid Symbol

**Symptom**: `symbol not found` or `asset not tradable`

**Diagnosis**:
```bash
# Check if symbol is tradable
python -c "
from src.trading_hydra.services.alpaca_client import AlpacaClient
client = AlpacaClient()
try:
    asset = client.get_asset('SYMBOL')
    print(f'Tradable: {asset.tradable}')
    print(f'Class: {asset.asset_class}')
except Exception as e:
    print(f'Error: {e}')
"
```

**Resolution**:
- Verify symbol spelling
- Check if asset is delisted
- For crypto: use format `BTC/USD` not `BTCUSD`

### 5.4 Options Order Rejected

**Symptom**: Options order fails with various errors

**Common Options Errors**:

| Error | Cause | Resolution |
|-------|-------|------------|
| `insufficient option buying power` | Not enough margin | Reduce contracts or close positions |
| `symbol not found` | Wrong OCC format | Check contract symbol format |
| `order would exceed position limit` | Too many contracts | Reduce order size |
| `contract not tradable` | Low liquidity | Choose different strike/expiry |

---

# PART IV: DATA AND ML ISSUES

## Chapter 6: Market Data Problems

### 6.1 Quotes Not Updating

**Symptom**: Same prices showing repeatedly

**Diagnosis**:
```bash
# Check quote cache
grep "quote\|price" logs/app.jsonl | tail -10 | jq .

# Check cache TTL
grep "quote_ttl" config/settings.yaml
```

**Resolution**:
- Reduce `quote_ttl_seconds` for fresher data
- Restart system to clear cache
- Check Alpaca data subscription status

### 6.2 Missing Historical Data

**Symptom**: Indicator calculations failing, "insufficient data" errors

**Diagnosis**:
```bash
# Check data availability
grep "bars\|insufficient\|lookback" logs/app.jsonl | tail -10 | jq .
```

**Resolution**:
- Wait for more data to accumulate
- Reduce lookback periods temporarily
- Check if symbol has sufficient trading history

### 6.3 Options Chain Unavailable

**Symptom**: Options trades not executing, "chain not available"

**Diagnosis**:
```bash
# Test options chain fetch
python -c "
from src.trading_hydra.services.options_chain import OptionsChainService
svc = OptionsChainService()
chain = svc.get_chain('AAPL')
print(f'Contracts: {len(chain) if chain else 0}')
"
```

**Resolution**:
- Verify Alpaca options subscription
- Check if market hours allow options data
- Fallback to simulation mode in config

---

## Chapter 7: ML Model Issues

### 7.1 Model Not Loading

**Symptom**: "model not found" or ML scoring disabled

**Diagnosis**:
```bash
# Check model files
ls -la models/

# Check ML config
grep -A10 "ml:" config/settings.yaml
```

**Resolution**:
```bash
# Retrain models
python scripts/ml/train_model.py

# Or disable ML temporarily
# config/settings.yaml: ml.enabled: false
```

### 7.2 All Scores Too Low

**Symptom**: Every trade candidate rejected by ML

**Diagnosis**:
```bash
# Check score distribution
grep "ml_score" logs/app.jsonl | tail -50 | jq -r '.ml_score' | sort -n
```

**Resolution**:
- Lower threshold: `ml.min_probability: 0.50`
- Check if market regime is unusual (VIX > 30)
- Retrain model with recent data
- Check feature extraction isn't failing

### 7.3 Model Prediction Errors

**Symptom**: `ValueError` or `IndexError` during ML scoring

**Diagnosis**:
```bash
# Check for feature extraction errors
grep -i "feature\|extract\|error" logs/app.jsonl | tail -10 | jq .
```

**Resolution**:
- Verify all required data fields are present
- Check for NaN or infinite values in data
- Retrain model if schema changed

---

# PART V: PERFORMANCE ISSUES

## Chapter 8: System Running Slow

### 8.1 High CPU Usage

**Symptom**: System unresponsive, loop taking > 5 seconds

**Diagnosis**:
```bash
# Check loop timing
grep "loop_duration\|elapsed" logs/app.jsonl | tail -10 | jq .

# Check system resources
top -p $(pgrep -f "python main.py")
```

**Resolution**:
- Increase `loop_interval_seconds` (5 → 10)
- Reduce number of symbols being tracked
- Check for runaway calculations

### 8.2 Memory Leaks

**Symptom**: Memory usage growing over time

**Diagnosis**:
```bash
# Monitor memory
ps aux | grep python | grep main.py

# Check log file sizes
ls -lh logs/
```

**Resolution**:
- Restart system periodically
- Enable log rotation (check `max_log_size_mb`)
- Check for unbounded data structures

### 8.3 Database Growing Large

**Symptom**: `trading_state.db` becoming very large

**Diagnosis**:
```bash
# Check database size
ls -lh state/trading_state.db

# Check table sizes
sqlite3 state/trading_state.db "SELECT name, COUNT(*) FROM sqlite_master WHERE type='table' GROUP BY name;"
```

**Resolution**:
```bash
# Vacuum database
sqlite3 state/trading_state.db "VACUUM;"

# Or fresh start if acceptable
python main.py --fresh-start
```

---

# PART VI: POSITION MANAGEMENT ISSUES

## Chapter 9: ExitBot Problems

### 9.1 Trailing Stops Not Triggering

**Symptom**: Positions held beyond expected exit points

**Diagnosis**:
```bash
# Check trailing stop state
grep "trailing\|stop" logs/app.jsonl | tail -20 | jq .

# Check high water marks
sqlite3 state/trading_state.db "SELECT * FROM kv_store WHERE key LIKE '%trailing%';"
```

**Resolution**:
- Verify `activation_profit_pct` threshold
- Check if position has reached activation level
- Verify ExitBot is running (check logs for "exitbot")

### 9.2 Positions Not Being Managed

**Symptom**: Orphaned positions with no stops

**Diagnosis**:
```bash
# List all positions
python -c "
from src.trading_hydra.services.alpaca_client import AlpacaClient
client = AlpacaClient()
positions = client.get_all_positions()
for p in positions:
    print(f'{p.symbol}: {p.qty} @ {p.avg_entry_price}')
"
```

**Resolution**:
- Restart system to re-scan positions
- ExitBot will auto-detect and register new positions
- Manually close if needed via dashboard

### 9.3 Premature Exits

**Symptom**: Positions closed too early

**Diagnosis**:
```bash
# Check exit reasons
grep "exit\|close\|stop" logs/app.jsonl | tail -20 | jq .
```

**Resolution**:
- Review stop-loss and trailing stop settings
- Increase `activation_profit_pct` for later trailing activation
- Increase `trailing_pct` for wider stops

---

# PART VII: STRATEGY SYSTEM ISSUES

## Chapter 10: Strategy Not Firing

### 10.1 Strategy Disabled

**Symptom**: Strategy exists but never generates signals

**Diagnosis**:
```bash
# Check strategy YAML
cat config/strategies/bullish_bursts.yaml | head -20
```

**Resolution**:
```yaml
# Ensure enabled: true in strategy file
enabled: true
```

### 10.2 Earnings Filter Blocking

**Symptom**: Strategy blocked during earnings period

**Diagnosis**:
```bash
# Check earnings dates
grep "earnings" logs/app.jsonl | tail -10 | jq .
```

**Resolution**:
- Use `*_only_earnings` strategy variant during earnings
- Or use base strategy with `earnings_policy: null`

### 10.3 Backtest Gate Failing

**Symptom**: Strategy rejected by backtest performance check

**Diagnosis**:
```bash
# Check backtest results
grep "backtest\|win_rate\|return" logs/app.jsonl | tail -10 | jq .
```

**Resolution**:
- Lower thresholds in strategy YAML:
  ```yaml
  backtest_gate:
    min_win_rate: 0.45  # from 0.52
    min_return: 0.003   # from 0.005
  ```

### 10.4 Signal Rules Not Matching

**Symptom**: Market conditions never trigger signal

**Diagnosis**:
```bash
# Check rule evaluation
grep "signal_rule\|validator" logs/app.jsonl | tail -20 | jq .
```

**Resolution**:
- Review signal rules in strategy YAML
- Relax conditions if too strict:
  ```yaml
  signal_rules:
    rsi_above: 40  # from 50
    price_above_ema_20: true
  ```

---

# PART VIII: RECOVERY PROCEDURES

## Chapter 11: Emergency Recovery

### 11.1 Full System Recovery

```bash
# 1. Stop all processes
pkill -f "python main.py"

# 2. Check for corrupted state
sqlite3 state/trading_state.db "PRAGMA integrity_check;"

# 3. Backup current state
cp state/trading_state.db state/trading_state.db.bak
cp logs/app.jsonl logs/app.jsonl.bak

# 4. Clear halt flags
sqlite3 state/trading_state.db "DELETE FROM kv_store WHERE key='global_halt';"

# 5. Restart
python main.py
```

### 11.2 Position Reconciliation

```bash
# 1. Get positions from Alpaca
python -c "
from src.trading_hydra.services.alpaca_client import AlpacaClient
client = AlpacaClient()
for p in client.get_all_positions():
    print(f'{p.symbol},{p.qty},{p.avg_entry_price},{p.unrealized_pl}')
" > /tmp/positions.csv

# 2. Compare with state
sqlite3 state/trading_state.db "SELECT * FROM kv_store WHERE key LIKE '%position%';"

# 3. Reset state if needed
python main.py --fresh-start
```

### 11.3 Log Recovery

```bash
# Find recent errors
grep -i "error\|exception\|fail" logs/app.jsonl | tail -50 > /tmp/errors.log

# Extract timeline
jq -r '.timestamp + " " + .event' logs/app.jsonl | tail -100

# Rotate large logs
mv logs/app.jsonl logs/app.jsonl.$(date +%Y%m%d)
touch logs/app.jsonl
```

---

# PART IX: QUICK REFERENCE

## Appendix A: Diagnostic Commands

```bash
# System status
tail -f logs/app.jsonl | jq .

# Halt status
sqlite3 state/trading_state.db "SELECT * FROM kv_store WHERE key LIKE '%halt%';"

# Position count
python -c "from src.trading_hydra.services.alpaca_client import AlpacaClient; print(len(AlpacaClient().get_all_positions()))"

# Account equity
python -c "from src.trading_hydra.services.alpaca_client import AlpacaClient; print(AlpacaClient().get_account().equity)"

# ML model status
ls -la models/*.pkl 2>/dev/null || echo "No models"

# Database health
sqlite3 state/trading_state.db "PRAGMA integrity_check;"
```

## Appendix B: Common Error Messages

| Error Message | Likely Cause | Quick Fix |
|---------------|--------------|-----------|
| `APIError: forbidden` | Bad credentials | Check ALPACA_KEY/SECRET |
| `database is locked` | Multiple processes | Kill other Python processes |
| `insufficient buying power` | Low cash | Close positions or reduce size |
| `market is closed` | Wrong hours | Wait for market open |
| `model not found` | Missing ML model | Run training script |
| `quote stale` | Data delay | Check internet/Alpaca status |
| `halt triggered` | Safety limit hit | Check logs for reason |
| `kill_switch activated` | Strategy drawdown | Wait for cooloff |

## Appendix C: Config Quick Fixes

```yaml
# Disable ML (emergency)
ml:
  enabled: false

# Increase loop interval (reduce load)
runner:
  loop_interval_seconds: 15

# Lower ML threshold (more trades)
ml:
  min_probability: 0.45

# Increase daily loss limit (more risk)
risk:
  global_max_daily_loss_pct: 3.0

# Disable specific bot
momentum_bots:
  - enabled: false
```

## Appendix D: Support Escalation

| Issue Level | Response Time | Action |
|-------------|---------------|--------|
| CRITICAL | Immediate | Emergency halt, preserve state |
| HIGH | 15 minutes | Diagnose, apply quick fix |
| MEDIUM | 1 hour | Scheduled maintenance |
| LOW | Next session | Document and plan fix |

---

**END OF TROUBLESHOOTING FIELD MANUAL**

*This document is classified OPERATOR REFERENCE and should be treated as the authoritative troubleshooting guide for Trading Hydra operations.*
