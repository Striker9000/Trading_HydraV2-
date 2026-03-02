# Trading Hydra MVP

## Overview
Trading Hydra is an autonomous Python-based system designed for automated financial market participation. It integrates execution bots with robust risk management, dynamic budget allocation, and state persistence, primarily leveraging the Alpaca API. The project aims to deliver an adaptive platform for automated trading strategies, incorporating ML-driven trade scoring, institutional-grade risk controls, and continuous market adaptation for optimal performance and risk management across various market conditions and account sizes.

## User Preferences
- Pure Python only (no TypeScript/Node)
- SQLite for local state persistence
- JSONL for structured logging
- Config-driven loop interval (not cron)
- Paper trading by default for safety
- Comprehensive code comments for maintainability

## System Architecture
The system employs a modular, config-driven architecture centered around a 5-step trading loop orchestrated by `orchestrator.py`. Key architectural decisions include durable state management via an SQLite database and defining all operational parameters within YAML configuration files. A Flask-based web dashboard provides real-time monitoring and manual controls.

### Dynamic Optimization System ($1K-$200K)
The system features a dynamic optimization system that automatically adjusts trading parameters based on Alpaca equity. It uses a 15-tier capital sweep (`export/results/capital_sweep_dynamic.json`) to select optimal configurations for different account sizes, ranging from aggressive growth strategies for smaller accounts ($1K-$15K) to capital preservation for larger, institutional accounts ($150K-$200K). Configurations are applied with a specific precedence: `settings.yaml` -> `bots.yaml` -> `small_account.yaml` -> `tier_override.yaml`.

### Core Components and Design Patterns
- **Trading Loop**: A recurring 5-step process (Initialize, HaltCheck, PortfolioBot, Execution, Finalize) with `ExitBot` running in a dedicated thread.
- **Config-Driven**: All system parameters, bot behaviors, and risk controls are defined in YAML files.
- **Centralized Time Source**: `MarketClock` for consistent time handling and market hour configuration.
- **Fail-Closed Safety**: `ExitBot` and `HaltManager` ensure safe system shutdown upon critical failures.
- **Durable State**: Trading state is persisted in `trading_state.db` (SQLite).
- **Structured Logging**: Events are logged in JSONL format (`app.jsonl`) with automatic rotation.
- **Graceful Shutdown**: Handles SIGINT/SIGTERM for clean termination.
- **HydraSensors**: A non-blocking sensor layer for continuous market intelligence, including watchlist management, market data caching, technical indicators, breadth sensors, and regime detection.
- **Specialized Trading Bots**: Includes MomentumBot, OptionsBot (with Hail Mary strategy), TwentyMinuteBot, CryptoBot, WhipsawTrader, BounceBot, OptionsBot0DTE, and a PreStagedEntry System.
- **VWAP Posture System**: Implements institutional VWAP methodology with sticky posture states, gap detection, VWAP deviation bands, and retest logic.
- **Risk Management**: Features ExitBot v2 Elite (institutional-grade exit intelligence with authority-based decision making, ForwardProjectionEngine, PartialExitDoctrine, FailSafeController), HaltManager, MarketRegimeService, Kelly Criterion for position sizing, an 8-Strategy Profile System, Unified Risk Integration (`RiskOrchestratorIntegration`), PolicyGate for pre-trade validation, OrderStateMachine, PnLAttributionService, GreekRiskMonitor, and IV Percentile Entry Gate. It also incorporates ProfitSniper for dynamic exit intelligence and SessionProtection with HWM Giveback Cap, Profit Locks, and Trailing Tighten.
- **Production Hardening**: Includes MLGovernance, CircuitBreakerRegistry, ConfigPerformanceLogger, MLConfigTuner, NewsCatalystService, AIStrategyAdvisor, and HealthCheckService.
- **Backtesting & Auto-Optimization**: `BacktestEngine` for historical data testing and parameter optimization.
- **ML Features**: `MLSignalService` for pre-trade scoring, `AccountAnalyticsService` for portfolio intelligence, and Decision Tracker.
- **Market Intelligence System**: An AI-powered layer providing NewsIntelligence, SentimentScorer, SmartMoneyService, and MacroIntelService.
- **Universe Selection**: `PremarketIntelligenceService` for multi-factor symbol ranking, `UniverseGuard`, and `SessionSelectorBot` for dynamic pre-market ticker selection.
- **Console Dashboard**: A decision dashboard for AI reasoning and alerts.

## Recent Changes (Feb 2026)
- **HailMary Standalone Bot**: Extracted from OptionsBot as independent entry bot with sweep-optimized config
- **Unified $1K/Day Sweep**: All 7 entry bots benchmarked at normalized $1K/day budget over 600 days
- **Sweep-Optimized Budget Allocation**: PortfolioBot bucket percentages reweighted by sweep ranking (HailMary 30%, TwentyMin 25%, Options 20%, Crypto 10%, Bounce 8%, Momentum 4%, Whipsaw 3%)
- **Broker Meeting**: Alpaca account verified at $51,760 equity, budgets set per sweep ranking
- **1000-Day HailMary Backtest**: 5,339 trades, 24.1% WR, $26.6M PnL, 7.63 PF, every month profitable
- **HailMary Strategy Guide**: Complete 12-module course/PDF product at `export/docs/HAILMARY_STRATEGY_GUIDE.md`
- **portfolio.py Updated**: Added hailmary_bucket and whipsaw_bucket support to PortfolioBot
- **MAX PROFIT Mode Active**: Daily loss limit raised 3%→8% ($4K cap), drawdown reduce 3%→5%, halt 10%→12%
- **TwentyMinuteBot**: delegate_exits_to_exitbot=false (ExitBot won't override "no stops" strategy)
- **Budget Allocations**: TwentyMin 45%, HailMary 35%, Options 15%, Momentum 3% (Crypto/Bounce/Whipsaw disabled at 0%)
- **Disabled Bots**: CryptoBot, WhipsawTrader, BounceBot set to enabled:false and 0% budget (will become separate standalone bots)
- **Operating Hours**: Market open set to 6:00 AM (was 6:30 AM), market close 1:00 PM PST
- **ProfitSniper Options REVERTED (02/13)**: Tight options config (0.5% arm, 0.15% pullback) was killing winners on bid-ask noise. Reverted to export-matching wider settings: 3% arm, 2% pullback, 1.0 velocity reversal. Stock ProfitSniper stays tight (sweep-validated for equities).
- **200-Day Sweep Optimizer (02/13)**: Ran 200-iteration sweep across all 6 bots with disk-cached Alpaca data. Results: Whipsaw 95.2% WR (57 configs at 80%+), Crypto 89.1% WR, Momentum 78%, ExitBot 77.5%, BounceBot 77.3%, TwentyMin 75.7%.
- **TwentyMinute 200D-Sweep Params (02/13)**: min_gap 0.3% (was 0.5%), confirmation_bars 3 (was 1), rsi_overbought 70 (was 90), require_volume_spike re-enabled. Higher WR through quality filtering.
- **Whipsaw Re-enabled (02/13)**: 8% budget allocation. 200D-sweep params: std_dev_mult 2.75, TP 1.5%, SL 5%, max_hold 3 bars. Note: strategy module exists but NOT wired into orchestrator — needs integration.
- **Budget Rebalanced (02/13)**: TwentyMin 40% (was 45%), HailMary 32% (was 35%), Whipsaw 8% (new), Options 15%, Momentum 3%
- **Disk Cache Added (02/13)**: BacktestEngine now caches Alpaca bar data to `cache/bars/` with retry logic for rate limits. Subsequent sweep runs load from disk instantly.
- **Critical Bug Fixes (02/13)**: Fixed 3 production bugs causing 186+ errors:
  - Fractional/notional sell orders: Alpaca rejects notional for sells; `place_market_order` now converts notional→qty for sells (min 1 share) and rounds fractional qty to whole shares for GTC TIF
  - Options TIF: All 4 order methods (`place_market_order`, `place_oco_exit_order`, `place_stop_order`, `place_limit_order`) now force DAY TIF for options symbols (Alpaca rejects GTC for options)
  - ExitBot position monitoring: Wrapped per-position processing in try/except so one bad position can't crash monitoring for all positions
  - OptionsBot `_close_position`: Fixed undefined `side`/`qty` variables causing crashes when closing positions

## Key Export Files
- `export/docs/HAILMARY_STRATEGY_GUIDE.md` — Complete trading course (12 modules + appendices)
- `export/docs/EXITBOT_STRATEGY_GUIDE.md` — ExitBot v2 Elite documentation (13 modules + 4 appendices, 1,509 lines)
- `export/results/sweep_all_1k.json` — Unified bot ranking by profitability
- `export/results/backtest_hailmary_1000d.json` — 1000-day HailMary backtest data
- `export/config/bots.yaml` — Sweep-optimized bot configs and budget allocations
- `export/src/trading_hydra/services/portfolio.py` — Budget allocation engine

## External Dependencies
- **Alpaca API**: Market data, order execution, and account management.
- **SQLite**: Local state persistence.
- **Flask**: Administrative dashboard.
- **yfinance**: Earnings data, VIX/VVIX/TNX fallback quotes.
- **OpenAI**: AI-powered market intelligence (sentiment analysis, market analysis).