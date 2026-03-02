# Trading Hydra Module Inventory & Truth Audit

## Core Infrastructure

### 1. main.py
- **Purpose**: Main entry point, long-running process with configurable loop
- **Inputs**: Environment variables (ALPACA_KEY, ALPACA_SECRET), config files
- **Outputs**: Logs to JSONL, state to SQLite
- **Side Effects**: Signal handlers, infinite loop
- **Dependencies**: orchestrator, config, logging, state
- **Status**: IMPLEMENTED

### 2. orchestrator.py
- **Purpose**: 5-step trading loop orchestration
- **Inputs**: Config, market data, account state
- **Outputs**: Trading decisions, position management
- **Side Effects**: Order placement, state updates
- **Dependencies**: All services and bots
- **Status**: IMPLEMENTED

### 3. core/state.py
- **Purpose**: SQLite-backed persistent state management
- **Inputs**: Key-value pairs, JSON serializable data
- **Outputs**: Persistent storage at ./state/trading_state.db
- **Side Effects**: Database operations
- **Dependencies**: sqlite3, logging
- **Status**: IMPLEMENTED

### 4. core/logging.py
- **Purpose**: Structured JSONL logging
- **Inputs**: Log events with metadata
- **Outputs**: ./logs/app.jsonl
- **Side Effects**: File I/O
- **Dependencies**: json, datetime
- **Status**: IMPLEMENTED

### 5. core/halt.py
- **Purpose**: Trading halt management with GLOBAL_TRADING_HALT doctrine
- **Inputs**: Halt commands, config overrides
- **Outputs**: Halt state in SQLite
- **Side Effects**: Blocks new trades, allows position management
- **Dependencies**: state, logging
- **Status**: IMPLEMENTED

## Trading Services

### 6. services/alpaca_client.py
- **Purpose**: Alpaca API integration
- **Inputs**: API credentials, order requests
- **Outputs**: Account data, positions, order responses
- **Side Effects**: Live trading API calls
- **Dependencies**: alpaca-py SDK, requests
- **Status**: IMPLEMENTED
- **Features**: 
  - Full position tracking with avg_entry_price, current_price, asset_class
  - Market and limit orders
  - Quote fetching for stocks and crypto
  - Options chain integration
  - Paper/live trading toggle

### 7. services/execution.py
- **Purpose**: Bot execution coordination with halt-aware logic
- **Inputs**: Bot list, equity, halt status
- **Outputs**: Execution results
- **Side Effects**: Bot execution, order placement
- **Dependencies**: All bots, alpaca_client
- **Status**: IMPLEMENTED

### 8. services/exitbot.py
- **Purpose**: Central position monitor and safety controller
- **Inputs**: Account state, health metrics, all positions
- **Outputs**: Halt decisions, trailing stop management
- **Side Effects**: Emergency halts, automatic exits
- **Dependencies**: health, halt manager, trailing_stop manager
- **Status**: IMPLEMENTED
- **Features**:
  - Monitors ALL positions (manual + automated)
  - Auto-detects new entries and registers trailing stops
  - Bot-specific trailing stop config for known bots
  - Default trailing stop for manual trades
  - Executes exits when stops are triggered
  - Daily P&L limit enforcement
  - Kill condition monitoring

### 9. services/portfolio.py
- **Purpose**: Budget allocation across bots
- **Inputs**: Total equity, bot configurations
- **Outputs**: Per-bot budgets
- **Side Effects**: State updates
- **Dependencies**: config, state
- **Status**: IMPLEMENTED
- **Features**:
  - Bucket-based allocation (Momentum, Options, Crypto, TwentyMin)
  - Cash reserve enforcement (30%)
  - Per-bot min/max limits

### 9a. services/decision_tracker.py
- **Purpose**: Audit trail for all trade decisions
- **Inputs**: Trade decisions, symbol, bot ID
- **Outputs**: decision_records.jsonl
- **Status**: IMPLEMENTED

### 9b. services/market_regime.py
- **Purpose**: VIX-based market regime detection
- **Inputs**: VIX level, market indicators
- **Outputs**: Regime classification (LOW, NORMAL, STRESS)
- **Status**: IMPLEMENTED

### 9c. services/crypto_universe.py
- **Purpose**: Dynamic cryptocurrency universe selection
- **Inputs**: All available crypto pairs
- **Outputs**: Top N coins for trading
- **Status**: IMPLEMENTED

### 9d. services/options_chain.py
- **Purpose**: Options chain data fetching
- **Inputs**: Underlying symbol
- **Outputs**: Options contracts with Greeks
- **Status**: IMPLEMENTED

### 9e. services/premarket_intelligence.py
- **Purpose**: Pre-market gap and volatility analysis
- **Inputs**: Overnight price data
- **Outputs**: Gap analysis, IV data
- **Status**: IMPLEMENTED

## Risk Management

### 10. risk/trailing_stop.py
- **Purpose**: Synthetic trailing stops for all asset classes
- **Inputs**: Position data, current prices, config
- **Outputs**: Exit signals, stop updates
- **Side Effects**: State persistence, exit orders
- **Dependencies**: state, execution service
- **Status**: IMPLEMENTED
- **Features**:
  - High/low water mark tracking
  - Activation threshold (only trail after profit)
  - Update-only-if-improves rule
  - Exit lock to prevent duplicate orders
  - Persists across restarts

### 11. risk/position_sizer.py
- **Purpose**: Kelly criterion-based position sizing
- **Inputs**: Win rate, win/loss ratio, account equity
- **Outputs**: Position size recommendations
- **Dependencies**: config
- **Status**: IMPLEMENTED
- **Features**:
  - Fractional Kelly (25% default)
  - Base risk percentage (0.5% NAV)
  - Max single position limits

### 12. risk/correlation_manager.py
- **Purpose**: Correlation and concentration management
- **Inputs**: Current positions, new trade candidate
- **Outputs**: Trade approval/rejection
- **Dependencies**: position data
- **Status**: IMPLEMENTED
- **Features**:
  - Max pairwise correlation (0.7)
  - Max sector exposure (20%)
  - Max single asset exposure (10%)

## Trading Bots

### 13. bots/momentum_bot.py
- **Purpose**: Stock momentum trading with Turtle Traders strategy
- **Inputs**: Stock price data, technical indicators
- **Outputs**: Directional trades (long/short)
- **Side Effects**: Equity orders
- **Dependencies**: alpaca_client, trailing_stop
- **Status**: PRODUCTION-LEVEL
- **Strategy**: Turtle Traders (Donchian breakouts)
- **Features**:
  - 20-day entry lookback, 10-day exit lookback
  - ATR-based position sizing
  - Pyramiding (up to 4 units)
  - Winner filter to avoid chasing
  - Session windows (6:35 AM - 12:55 PM PST)
  - Trailing stops with 0.8% activation
- **Tickers**: AAPL (TSLA disabled)

### 14. bots/options_bot.py
- **Purpose**: Buy-side options trading with Strategy System
- **Inputs**: Options chain, underlying prices, strategy configs
- **Outputs**: Options orders (calls, puts, straddles)
- **Side Effects**: Options orders
- **Dependencies**: alpaca_client, strategy system
- **Status**: PRODUCTION-LEVEL
- **Modes**:
  - Legacy: IV-aware strategy selection
  - Strategy System: PDF rules-based trading (5-gate pipeline)
- **Tickers**: AAPL, AMD, MSFT, NVDA, TSLA, PLTR, BLK (standard), SPY, QQQ (0DTE mode)
- **Features**:
  - Real Alpaca Options Chain API integration
  - Complete Greeks calculation
  - Margin validation
  - Kill-switch per strategy
  - Earnings filter enforcement
  - 30% profit target, 50% stop loss
  - 0DTE mode for same-day expiration on indices

### 16. bots/twenty_minute_bot.py
- **Purpose**: Opening window gap trading
- **Inputs**: Overnight gap data, first-bar patterns
- **Outputs**: Momentum trades
- **Side Effects**: Stock/options orders
- **Dependencies**: alpaca_client
- **Status**: IMPLEMENTED
- **Strategy**: Jeremy Russell's 20-Minute Trader
- **Features**:
  - Gap analysis (up/down detection)
  - Pattern recognition (reversal, continuation, breakout)
  - First-bar range computation
  - Options execution with bracket bands (4-8%)
  - Max 15-minute hold time
  - Session: 6:30-7:50 AM PST

### 17. bots/crypto_bot.py
- **Purpose**: 24/7 cryptocurrency trading
- **Inputs**: Crypto price data, technical indicators
- **Outputs**: Buy/sell signals, position management
- **Side Effects**: Crypto orders
- **Dependencies**: alpaca_client, trailing_stop
- **Status**: PRODUCTION-LEVEL
- **Strategy**: Turtle Traders adapted for hourly bars
- **Features**:
  - Dynamic universe selection (top 3 from 60+ coins)
  - ML re-ranking for coin selection
  - 480-hour entry lookback (20 days)
  - RSI, MACD confirmation
  - Trailing stops with 1.5% activation
  - Long and short positions
  - Notional-based sizing

## Strategy System (PDF Rules-Based Trading)

### 18. strategy/registry.py
- **Purpose**: Load and validate strategy YAML configs
- **Inputs**: YAML files from config/strategies/
- **Outputs**: Strategy objects
- **Status**: IMPLEMENTED

### 19. strategy/validator.py
- **Purpose**: Evaluate signal rules (price vs EMA, RSI, volume)
- **Inputs**: Market data, signal rules from strategy
- **Outputs**: Pass/fail for signal gate
- **Status**: IMPLEMENTED

### 20. strategy/backtest_gate.py
- **Purpose**: Enforce historical performance thresholds
- **Inputs**: Strategy ID, historical trade data
- **Outputs**: Pass/fail (min 52% win rate, 0.5% return)
- **Status**: IMPLEMENTED

### 21. strategy/options_selector.py
- **Purpose**: Select contracts by delta/DTE/volume
- **Inputs**: Options chain, selection criteria
- **Outputs**: Best matching contract
- **Status**: IMPLEMENTED
- **Features**:
  - Delta range: 0.30-0.60
  - DTE range: 7-45 days
  - Minimum volume/OI requirements

### 22. strategy/earnings_filter.py
- **Purpose**: Earnings blackout enforcement
- **Inputs**: Symbol, earnings dates, policy
- **Outputs**: Pass/fail based on policy (NEVER, ONLY, PRE, POST)
- **Dependencies**: yfinance
- **Status**: IMPLEMENTED

### 23. strategy/kill_switch.py
- **Purpose**: Per-strategy drawdown circuit breaker
- **Inputs**: Strategy trade history
- **Outputs**: Freeze/unfreeze strategy
- **Status**: IMPLEMENTED
- **Features**:
  - Rolling window of last N trades (default: 5)
  - Max drawdown limit (default: -$500)
  - Cooloff period (default: 60 minutes)

### 24. strategy/runner.py
- **Purpose**: Orchestrate 5-gate pipeline
- **Inputs**: Symbol, strategy, market data
- **Outputs**: Trade signal or rejection
- **Status**: IMPLEMENTED

## ML Services

### 25. ml/signal_service.py
- **Purpose**: ML trade scoring (GradientBoostingClassifier)
- **Inputs**: Trade candidate, technical features
- **Outputs**: Probability score 0-1
- **Status**: IMPLEMENTED
- **Features**:
  - 23+ technical features
  - Bot-specific thresholds
  - Adaptive thresholds based on VIX

### 26. ml/feature_extractor.py
- **Purpose**: Extract technical indicators
- **Inputs**: Price data
- **Outputs**: Feature vector
- **Status**: IMPLEMENTED
- **Features**: RSI, MACD, EMA, ATR, volume ratios, etc.

### 27. ml/performance_analytics.py
- **Purpose**: Track trading performance
- **Inputs**: Trade history
- **Outputs**: Sharpe, win rate, profit factor
- **Status**: IMPLEMENTED

### 28. ml/account_analytics.py
- **Purpose**: Account-level ML analytics
- **Inputs**: Account state, market regime
- **Outputs**: Risk adjustments, allocation recommendations
- **Status**: IMPLEMENTED
- **Models**:
  - RiskAdjustmentEngine
  - BotAllocationModel
  - RegimeSizer
  - DrawdownPredictor
  - AnomalyDetector

### 28a. ml/trade_outcome_tracker.py
- **Purpose**: Track trade outcomes for ML training
- **Inputs**: Completed trades
- **Outputs**: Labeled training data
- **Status**: IMPLEMENTED

### 28b. ml/metrics_repository.py
- **Purpose**: Store and retrieve ML metrics
- **Inputs**: Model performance data
- **Outputs**: Historical metrics
- **Status**: IMPLEMENTED

### 28c. risk/killswitch.py
- **Purpose**: Global kill-switch management
- **Inputs**: Halt conditions
- **Outputs**: System halt/resume
- **Status**: IMPLEMENTED

## Dashboard

### 29. dashboard/app.py
- **Purpose**: Flask-based web control panel
- **Inputs**: HTTP requests, config files
- **Outputs**: JSON API responses, HTML pages
- **Side Effects**: Engine start/stop, config updates, trading actions
- **Dependencies**: Flask, all core modules
- **Status**: IMPLEMENTED
- **Features**:
  - Real-time monitoring (equity, P&L, positions)
  - Manual trading controls
  - Bot enable/disable
  - Configuration editor
  - Performance analytics with charts
  - Trade history export (CSV)

## Test Suites

Located in `src/trading_hydra/tests/`:
- `test_exitbot.py` - ExitBot safety mechanisms
- `test_momentum_bot.py` - MomentumBot functionality
- `test_crypto_bot.py` - CryptoBot functionality
- `test_options_bot.py` - OptionsBot functionality

Located in `tests/`:
- `test_strategy_system.py` - Strategy System tests (15 tests)

## Summary

| Component | Status | Strategy/Feature |
|-----------|--------|------------------|
| Orchestrator | COMPLETE | 5-step trading loop |
| ExitBot | COMPLETE | Position monitoring + trailing stops |
| PortfolioBot | COMPLETE | Budget allocation |
| MomentumBot | PRODUCTION | Turtle Traders (Donchian breakouts) |
| OptionsBot | PRODUCTION | Strategy System + 0DTE mode for SPY/QQQ |
| TwentyMinuteBot | COMPLETE | Gap trading (20-Minute Trader) |
| CryptoBot | PRODUCTION | Dynamic universe + Turtle Traders |
| Strategy System | COMPLETE | 10 strategies, 5 gates |
| ML Scoring | COMPLETE | GradientBoostingClassifier |
| Dashboard | COMPLETE | Flask web interface |
| Trailing Stops | COMPLETE | All asset classes |
| Position Sizing | COMPLETE | Kelly criterion |
| Correlation | COMPLETE | Sector/asset limits |

**All components implemented with real, config-driven strategies.**

## Last Updated
2026-01-18
