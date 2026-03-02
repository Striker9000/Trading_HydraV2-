# Trading Hydra Module Reference

## CLASSIFICATION: DEVELOPER REFERENCE
**Version**: 1.0  
**Last Updated**: January 2026  
**System**: Trading Hydra Autonomous Trading Platform

---

# Table of Contents

1. [Entry Points](#entry-points)
2. [Core Modules](#core-modules)
3. [Trading Bots](#trading-bots)
4. [Services](#services)
5. [Strategy System](#strategy-system)
6. [Machine Learning](#machine-learning)
7. [Risk Management](#risk-management)
8. [Dashboard](#dashboard)
9. [Indicators](#indicators)
10. [Utilities](#utilities)

---

# Entry Points

## main.py

**Purpose**: Primary entry point for the Trading Hydra system.

**Location**: `main.py` (project root)

**Description**:
This is the main executable that starts the entire trading system. It initializes all components, starts the Flask dashboard in a background thread, and runs the 5-step trading loop at configured intervals.

**Key Features**:
- Config-driven loop interval from `settings.yaml`
- Graceful SIGINT/SIGTERM handling for safe shutdown
- Flask dashboard runs on port 5000
- Paper trading by default (controlled by `ALPACA_PAPER` env var)
- Multiple display modes: `--verbose`, `--quiet`, or clean (default)

**Usage**:
```bash
python main.py            # Clean dashboard (default)
python main.py --verbose  # Full dashboard + all JSONL logs
python main.py --quiet    # Minimal one-line summary per loop
```

**Functions**:
| Function | Description |
|----------|-------------|
| `_signal_handler(signum, frame)` | Handles shutdown signals gracefully |
| `_is_shutdown_requested()` | Thread-safe shutdown flag check |
| `_start_dashboard()` | Starts Flask dashboard in background thread |
| `main()` | Main entry point, runs trading loop |

**Dependencies**: orchestrator, core.logging, core.config, core.console, core.clock

---

## orchestrator.py

**Purpose**: Core trading loop orchestrator with 5-step execution.

**Location**: `src/trading_hydra/orchestrator.py`

**Description**:
The TradingOrchestrator class implements the main trading loop that runs every iteration. It coordinates all trading bots, services, and safety checks in a structured 5-step process.

**5-Step Trading Loop**:
1. **Initialize**: Load config, connect to Alpaca, detect account mode
2. **ExitBot**: Monitor positions, update trailing stops, check halt conditions
3. **PortfolioBot**: Allocate budgets across enabled bots
4. **Execution**: Run trading bots (MomentumBot, OptionsBot, CryptoBot, TwentyMinuteBot)
5. **Finalize**: Record metrics, log summary, prepare for next loop

**Key Classes**:

### TradingOrchestrator
```python
class TradingOrchestrator:
    def initialize(self) -> None: ...
    def run_loop(self) -> LoopResult: ...
```

### LoopResult
```python
@dataclass
class LoopResult:
    success: bool
    status: str
    summary: str
    timestamp: str
    display_data: Optional[LoopDisplayData]
```

**Key Methods**:
| Method | Description |
|--------|-------------|
| `initialize()` | One-time setup: state store, ML services, position sizer |
| `run_loop()` | Execute one iteration of the trading loop |
| `_step_initialize()` | Step 1: Account sync and regime detection |
| `_step_exitbot()` | Step 2: Position monitoring and safety |
| `_step_portfoliobot()` | Step 3: Budget allocation |
| `_step_execution()` | Step 4: Run trading bots |

**Dependencies**: All core modules, all services, all bots, ML services, risk managers

---

# Core Modules

Located in `src/trading_hydra/core/`

## config.py

**Purpose**: Configuration loader for YAML files with account mode detection.

**Key Features**:
- Loads `settings.yaml` and `bots.yaml`
- Auto-detects account mode (micro, small, standard) based on equity
- Provides mode-specific risk parameters
- Config doctor for conflict detection

**Account Modes**:
| Mode | Equity Range | Daily Risk | Description |
|------|--------------|------------|-------------|
| Micro | < $1,000 | 8% | Ultra aggressive, crypto-only |
| Small | $1k - $10k | 4% | Medium aggressive, crypto-focused |
| Standard | > $10,000 | 2% | Conservative, full diversity |

**Key Functions**:
```python
def load_settings() -> Dict[str, Any]: ...
def load_bots_config() -> Dict[str, Any]: ...
def auto_detect_account_mode(equity: float) -> bool: ...
def get_account_mode() -> str: ...
def get_account_mode_params() -> Dict[str, Any]: ...
def dump_effective_config(print_summary: bool) -> str: ...
def run_config_doctor(print_output: bool, hard_fail: bool) -> List[str]: ...
```

---

## state.py

**Purpose**: SQLite-backed state store for durable state persistence.

**Database**: `./state/trading_state.db`

**Description**:
Provides a key-value store backed by SQLite for persisting trading state across restarts. All state is JSON-serialized for flexibility.

**Tables**:
- `state`: Key-value store for general state
- `order_ids`: Order idempotency tracking

**Key Functions**:
```python
def init_state_store() -> None: ...
def get_state(key: str, default: Any = None) -> Any: ...
def set_state(key: str, value: Any) -> None: ...
def delete_state(key: str) -> None: ...
def generate_client_order_id(bot_id: str, symbol: str, signal_id: str) -> str: ...
def is_order_already_submitted(client_order_id: str) -> bool: ...
def record_order_submission(client_order_id: str, bot_id: str, symbol: str, ...) -> None: ...
```

---

## clock.py

**Purpose**: Market clock utilities with timezone support.

**Timezone**: Defaults to `America/Los_Angeles` (PST)

**Description**:
Provides consistent time handling across the system. All bots use MarketClock to determine trading windows and session times.

**Key Class**:
```python
class MarketClock:
    def now(self) -> datetime: ...
    def is_market_hours(self) -> bool: ...
    def is_extended_hours(self) -> bool: ...
    def is_weekend(self) -> bool: ...
    def is_pre_market_intel_window(self) -> bool: ...
    def should_skip_stock_bots() -> bool: ...
    def get_market_open() -> time: ...
    def get_market_close() -> time: ...
```

**Market Hours (PST)**:
| Period | Start | End |
|--------|-------|-----|
| Pre-market | 01:00 | 06:30 |
| Market Hours | 06:30 | 13:00 |
| After-hours | 13:00 | 17:00 |

---

## halt.py

**Purpose**: Trading halt management for fail-closed safety.

**Description**:
Implements the global halt mechanism that stops all trading when safety limits are exceeded. Persists halt state in SQLite for restart safety.

**Key Class**:
```python
class HaltManager:
    def set_halt(self, reason: str, cooloff_minutes: int = 60) -> None: ...
    def clear_halt(self) -> None: ...
    def is_halted(self) -> bool: ...
    def get_status(self) -> HaltStatus: ...
    def clear_if_expired(self) -> bool: ...
```

**HaltStatus Dataclass**:
```python
@dataclass
class HaltStatus:
    active: bool
    reason: str
    halted_at: Optional[str]
    expires_at: Optional[str]
```

**Halt Triggers**:
- Daily P&L limit exceeded
- API authentication failure
- Data staleness exceeded
- Manual halt via config

---

## logging.py

**Purpose**: JSONL structured logging with console output control.

**Log File**: `logs/app.jsonl`

**Description**:
Provides structured logging to JSONL files for analysis. Supports log rotation (size-based) and configurable console output modes.

**Key Class**:
```python
class JsonlLogger:
    def log(self, event: str, data: Dict[str, Any]) -> None: ...
    def warn(self, message: str, **kwargs) -> None: ...
    def error(self, message: str, **kwargs) -> None: ...
```

**Log Rotation Settings**:
- `LOG_MAX_SIZE_MB`: 10 MB
- `LOG_MAX_FILES`: 7 (1 week retention)

**Functions**:
```python
def get_logger() -> JsonlLogger: ...
def set_logger_quiet_mode(quiet: bool) -> None: ...
def set_logger_suppress_console(suppress: bool) -> None: ...
```

---

## health.py

**Purpose**: Health monitoring for API and data freshness.

**Description**:
Tracks API failures, connection issues, and data staleness. Triggers halt conditions when thresholds are exceeded.

**Key Class**:
```python
class HealthMonitor:
    def record_api_failure(self, error: str = "") -> None: ...
    def record_critical_auth_failure(self, error: str = "") -> None: ...
    def record_connection_failure(self, error: str = "") -> None: ...
    def record_price_tick(self) -> None: ...
    def get_snapshot(self) -> HealthSnapshot: ...
```

**HealthSnapshot Dataclass**:
```python
@dataclass
class HealthSnapshot:
    ok: bool
    reason: str
    api_failures: int
    last_price_tick: Optional[str]
    stale_seconds: float
    critical_auth_failure: bool
    connection_failures: int
```

---

# Trading Bots

Located in `src/trading_hydra/bots/`

## momentum_bot.py

**Purpose**: Momentum-based trading bot for individual stocks.

**Bot ID**: `mom_*` (e.g., `mom_core`)

**Strategy**: Turtle Traders breakout (Donchian channels)

**Description**:
Implements Richard Dennis's Turtle Traders strategy using Donchian channel breakouts. Detects price trends and executes trades when momentum signals are strong enough.

**Key Features**:
- Donchian channel breakouts (20/55/20 periods)
- ATR-based position sizing
- Trailing stops with activation threshold
- ML signal scoring integration
- Decision record audit trail

**Configuration** (from `bots.yaml`):
```yaml
momentum_bots:
  - bot_id: mom_core
    enabled: true
    tickers: [AAPL, TSLA, NVDA, AMD, MSFT]
    trade_start: "06:35"
    trade_end: "12:55"
    max_trades_per_day: 5
    max_concurrent_positions: 2
```

**Key Functions**:
```python
def run_momentum_bot(bot_config: Dict, equity: float) -> BotResult: ...
def get_momentum_bot(bot_id: str) -> MomentumBot: ...
```

---

## options_bot.py

**Purpose**: Multi-strategy options trading with credit spreads and 0DTE.

**Bot ID**: `opt_core` (standard), `opt_0dte` (same-day expiration)

**Strategies**:
1. Bull Put Spread - Bullish credit spread
2. Bear Call Spread - Bearish credit spread
3. Iron Condor - Neutral credit spread
4. Long Call/Put - Directional bets
5. Straddle - Long volatility

**Description**:
Implements multiple options strategies optimized for consistent small profits ($50-500 per trade). Uses credit spreads for time decay advantage and supports the PDF Rules-Based Strategy System.

**Key Features**:
- Real Alpaca Options Chain API integration
- Complete Greeks calculation
- Strategy System 5-gate pipeline
- Per-strategy kill-switch
- Earnings filter enforcement
- 0DTE mode for SPY/QQQ

**Tickers**:
- Standard: AAPL, AMD, MSFT, NVDA, TSLA, PLTR, BLK
- 0DTE: SPY, QQQ

**Configuration**:
```yaml
optionsbot:
  bot_id: opt_core
  enabled: true
  tickers: [AAPL, AMD, MSFT, NVDA, TSLA]
  trade_start: "06:40"
  trade_end: "12:30"
  use_strategy_system: true
```

**Key Classes**:
```python
class OptionStrategy(Enum):
    LONG_CALL, LONG_PUT, STRADDLE
    BULL_PUT_SPREAD, BEAR_CALL_SPREAD, IRON_CONDOR

class OptionsBot:
    def run(self, equity: float) -> BotResult: ...
```

---

## twenty_minute_bot.py

**Purpose**: Opening window gap trading (first 20 minutes).

**Bot ID**: `twentymin`

**Strategy**: Jeremy Russell's 20-Minute Trader

**Description**:
Exploits predictable patterns during the first 20 minutes after market open (6:30-6:50 AM PST). Analyzes overnight gaps and first-bar patterns for quick entries/exits.

**Key Features**:
- Opening gap analysis
- Pattern recognition (reversal, continuation, breakout)
- Quick 2-15 minute holds
- Micro-profit targets (0.3-0.5%)
- Options execution for leveraged micro-movements

**Patterns Detected**:
| Pattern | Description |
|---------|-------------|
| GAP_REVERSAL | Gap that reverses direction |
| GAP_CONTINUATION | Gap that continues |
| FIRST_BAR_BREAKOUT | Break of first 5-min bar |
| OPENING_RANGE | Break of first 10-min range |

**Configuration**:
```yaml
twentyminute_bot:
  enabled: true
  tickers: [SPY, QQQ, AAPL]
  session_start: "06:30"
  session_end: "06:50"
  max_hold_minutes: 15
  min_gap_pct: 0.3
```

---

## crypto_bot.py

**Purpose**: 24/7 cryptocurrency trading with dynamic universe.

**Bot ID**: `crypto_core`

**Strategy**: Turtle Traders adapted for crypto + RSI/MACD confirmation

**Description**:
Production-ready momentum trading for cryptocurrencies. Unlike stock trading, operates 24/7 with no trading hour restrictions.

**Key Features**:
- Price freshness validation (reject stale quotes > 30 sec)
- Enhanced signal strategy (SMA + RSI + MACD)
- Pre-trade risk checks
- Smart order execution with spread analysis
- Exponential backoff retry logic
- Dynamic universe selection (top N coins)

**Default Pairs**: BTC/USD, ETH/USD

**Configuration**:
```yaml
cryptobot:
  bot_id: crypto_core
  enabled: true
  pairs: [BTC/USD, ETH/USD]
  max_trades_per_day: 10
  min_order_size: 15
```

**Production Constants**:
| Constant | Value | Description |
|----------|-------|-------------|
| MAX_QUOTE_AGE_SECONDS | 30 | Max stale quote age |
| MAX_SPREAD_PCT | 0.5% | Max bid/ask spread |
| MAX_RETRIES | 3 | API retry attempts |
| RSI_OVERSOLD | 30 | RSI buy signal |
| RSI_OVERBOUGHT | 70 | RSI sell signal |

---

# Services

Located in `src/trading_hydra/services/`

## alpaca_client.py

**Purpose**: Alpaca API client for trading operations.

**Description**:
Unified client for all Alpaca API interactions including account management, order execution, position tracking, and market data.

**Key Features**:
- Paper/Live trading support via `ALPACA_PAPER` env var
- Quote and account caching with TTL
- Options chain API integration
- Crypto trading support
- Mock data fallback for development

**Key Class**:
```python
class AlpacaClient:
    def get_account(self) -> AlpacaAccount: ...
    def get_all_positions(self) -> List[AlpacaPosition]: ...
    def get_quote(self, symbol: str) -> Dict[str, Any]: ...
    def submit_order(self, **kwargs) -> Dict[str, Any]: ...
    def cancel_order(self, order_id: str) -> bool: ...
    def get_orders(self, status: str = "open") -> List[Dict]: ...
    def get_options_chain(self, symbol: str) -> List[Dict]: ...
```

**Environment Variables**:
- `ALPACA_KEY`: API key
- `ALPACA_SECRET`: API secret
- `ALPACA_PAPER`: "true" for paper, "false" for live

---

## exitbot.py

**Purpose**: Central position monitor and safety controller.

**Description**:
The MOST IMPORTANT bot in the system. Provides kill-switch functionality, position monitoring, trailing stop management, and automatic exit execution.

**Responsibilities**:
1. Kill-switch functionality (halt on critical failures)
2. Position monitoring for ALL entries (manual + automated)
3. Trailing-stop management for every position
4. Automatic exit execution when stops triggered
5. Daily P&L limit enforcement

**Key Class**:
```python
class ExitBot:
    def run(self, equity: float, day_start_equity: float) -> ExitBotResult: ...
    def _scan_positions(self) -> List[PositionInfo]: ...
    def _update_trailing_stops(self, positions: List[PositionInfo]) -> int: ...
    def _check_exits(self, positions: List[PositionInfo]) -> List[ExitRecord]: ...
```

**ExitBotResult Dataclass**:
```python
@dataclass
class ExitBotResult:
    should_continue: bool
    is_halted: bool
    halt_reason: str
    equity: float
    pnl: float
    positions_monitored: int
    trailing_stops_active: int
    exits_triggered: int
    recent_exits: List[ExitRecord]
```

---

## portfolio.py

**Purpose**: Dynamic budget allocation across trading bots.

**Description**:
Allocates daily risk budget across all enabled bots based on bucket percentages and guardrails defined in config.

**Key Features**:
- Bucket-based allocation (Momentum, Options, Crypto, TwentyMin)
- Cash reserve enforcement (30% default)
- Per-bot min/max limits (guardrails)
- Dynamic splitting between enabled bots

**Key Class**:
```python
class PortfolioBot:
    def run(self, equity: float) -> PortfolioBotResult: ...
```

**Budget Buckets** (% of daily risk):
| Bucket | Default % |
|--------|-----------|
| Momentum | 50% |
| Options | 50% |
| Crypto | 25% |
| TwentyMin | 15% |

---

## execution.py

**Purpose**: Order execution service for running trading bots.

**Description**:
Coordinates the execution of all trading bots, manages cooldowns, and handles order submission to Alpaca.

**Key Class**:
```python
class ExecutionService:
    def run(self, enabled_bots: List[str], equity: float, 
            selected_stocks: List[str], selected_options: List[str]) -> ExecutionResult: ...
```

**ExecutionResult Dataclass**:
```python
@dataclass
class ExecutionResult:
    bots_run: List[str]
    trades_attempted: int
    positions_managed: int
    errors: List[str]
    signals: List[TickerSignal]
    bots_outside_hours: List[str]
```

---

## market_regime.py

**Purpose**: VIX-based market regime detection.

**Description**:
Fetches and analyzes market indicators (VIX, VVIX, TNX, DXY, MOVE) to classify the current market regime. Used for strategy selection and position sizing.

**Indicators Used**:
| Indicator | Purpose |
|-----------|---------|
| VIX | Primary volatility index |
| VVIX | Volatility of VIX (early warning) |
| TNX | 10-Year Treasury yield |
| DXY | US Dollar index |
| MOVE | Bond volatility |

**Regime Classifications**:
```python
class VolatilityRegime(Enum):
    VERY_LOW = "very_low"    # VIX < 12
    LOW = "low"              # VIX 12-15
    NORMAL = "normal"        # VIX 15-20
    ELEVATED = "elevated"    # VIX 20-25
    HIGH = "high"            # VIX 25-35
    EXTREME = "extreme"      # VIX > 35

class MarketSentiment(Enum):
    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"
    EXTREME_FEAR = "extreme_fear"
```

**Key Function**:
```python
def get_current_regime() -> MarketRegimeAnalysis: ...
```

---

## decision_tracker.py

**Purpose**: Audit trail for all trade decisions.

**Output File**: `logs/decision_records.jsonl`

**Description**:
Tracks and exposes decision states from all trading bots. Provides visibility into signals, blockers, and exit proximity for dashboard display and compliance.

**Key Classes**:
```python
@dataclass
class SignalState:
    bot_id: str
    symbol: str
    signal: str  # "buy", "sell", "hold", "wait"
    strength: float
    reason: str
    timestamp: str

@dataclass
class BlockerState:
    bot_id: str
    blocker_type: str  # "cooldown", "max_trades", etc.
    description: str
    clears_at: Optional[str]

class DecisionTracker:
    def record_signal(self, signal: SignalState) -> None: ...
    def record_blocker(self, blocker: BlockerState) -> None: ...
    def get_all_decisions() -> Dict[str, BotDecisionState]: ...
```

---

## Other Services

| File | Purpose |
|------|---------|
| `crypto_universe.py` | Dynamic cryptocurrency universe selection |
| `earnings_calendar.py` | Earnings date lookups via yfinance |
| `options_chain.py` | Options chain data fetching |
| `options_data.py` | Options pricing and Greeks |
| `options_screener.py` | Options opportunity screening |
| `stock_screener.py` | Stock opportunity screening |
| `premarket_intelligence.py` | Pre-market gap and IV analysis |
| `universe_screener.py` | Universe filtering and selection |
| `parameter_resolver.py` | Dynamic parameter resolution |
| `system_state.py` | System-wide state aggregation |
| `fake_broker.py` | Mock broker for testing |
| `mock_data.py` | Development mock data provider |
| `jeremy_bracket.py` | Jeremy-style bracket order helper |

---

# Strategy System

Located in `src/trading_hydra/strategy/`

## registry.py

**Purpose**: Safe strategy configuration loading from YAML files.

**Description**:
Loads strategy configs only from `config/strategies/*.yaml`. Prevents "config hallucinations" by validating required keys and supporting inheritance.

**Key Features**:
- Only loads from disk (no inline configs)
- Supports `extends:` for strategy variants
- Validates required fields
- Returns frozen (immutable) configs

**Required Fields**:
```python
REQUIRED_TOP_KEYS = [
    "id", "name", "family", "direction", "enabled",
    "signal_rules", "backtest_gate", "options_plan", "risk_plan"
]
```

**Key Class**:
```python
class StrategyRegistry:
    def load_all(self) -> None: ...
    def get(self, strategy_id: str) -> Optional[StrategyConfig]: ...
    def list_enabled(self) -> List[str]: ...
```

---

## validator.py

**Purpose**: Deterministic rule evaluator for strategy signals.

**Motto**: "No AI. No vibes. Just pass/fail with receipts."

**Description**:
Evaluates signal rules from strategy YAML against indicator data. Returns pass/fail for each rule with detailed reasons.

**Supported Rule Types**:
| Rule Type | Description |
|-----------|-------------|
| `price_vs_ema` | Compare price to EMA |
| `price_vs_sma` | Compare price to SMA |
| `sma_vs_price` | Compare SMA to price |
| `rsi_threshold` | RSI above/below threshold |

**Key Class**:
```python
class StrategyValidator:
    def evaluate(self, strategy: Dict, symbol: str) -> StrategyDecision: ...
```

**StrategyDecision Dataclass**:
```python
@dataclass
class StrategyDecision:
    strategy_id: str
    symbol: str
    passed: bool
    reasons: List[RuleResult]
```

---

## runner.py

**Purpose**: Single enforcement point for strategy execution.

**Description**:
The "one throat to choke" module that enforces the complete strategy execution pipeline. All strategy trades must go through this runner.

**Pipeline (5 Gates)**:
1. Registry load (only real YAML files)
2. Per-strategy kill-switch check
3. Earnings policy filter
4. Deterministic signal rule validation
5. Backtest gate enforcement
6. Option contract selection
7. Trade execution with logging

**Key Class**:
```python
class StrategyRunner:
    def run_for_symbol(self, symbol: str) -> RunResult: ...
    def execute_trade(self, signal: TradeSignal) -> Dict[str, Any]: ...
```

---

## backtest_gate.py

**Purpose**: Historical performance threshold enforcement.

**Description**:
Hard gate based on strategy's backtest performance. If data missing, fail closed.

**Thresholds**:
- `min_win_rate_1y`: Minimum 1-year win rate
- `min_win_rate_3y`: Minimum 3-year win rate
- `min_total_wins_3y`: Minimum winning trades in 3 years
- `min_total_return_1y`: Minimum 1-year return

**Key Class**:
```python
class BacktestGate:
    def passes(self, gate_cfg: Dict, bt: Optional[BacktestSummary]) -> bool: ...
```

---

## kill_switch.py

**Purpose**: Per-strategy drawdown circuit breaker.

**Description**:
Tracks rolling realized PnL per strategy and disables the strategy when drawdown exceeds threshold. Persists across restarts.

**Key Features**:
- Per-strategy (not global) circuit breaker
- Configurable drawdown threshold and cooloff period
- Rolling trade window for calculation

**Key Class**:
```python
class StrategyKillSwitch:
    def status(self, strategy_id: str) -> KillStatus: ...
    def record_exit(self, strategy_id: str, pnl: float, strategy_cfg: Dict) -> None: ...
    def trigger_kill(self, strategy_id: str, reason: str, cooloff_min: int) -> None: ...
```

---

## earnings_filter.py

**Purpose**: Earnings policy enforcement.

**Modes**:
| Mode | Description |
|------|-------------|
| NEVER | Block within blackout_days of earnings (default) |
| ONLY | Allow only within window_days of earnings |
| PRE | Allow only if earnings upcoming |
| POST | Allow only if earnings just happened |

**Key Class**:
```python
class EarningsFilter:
    def allows(self, ticker: str, strategy_cfg: Dict) -> bool: ...
```

---

## options_selector.py

**Purpose**: Option contract selection based on strategy rules.

**Description**:
Selects optimal option contracts based on strategy's options_plan (DTE range, delta target, spread width, etc.).

---

# Machine Learning

Located in `src/trading_hydra/ml/`

## signal_service.py

**Purpose**: ML-based trade profit probability scoring.

**Model**: LightGBM/GradientBoostingClassifier

**Model Path**: `models/trade_classifier.pkl`

**Description**:
Scores potential trade entries with a probability of profit. Supports adaptive thresholds based on VIX and earnings season.

**Key Class**:
```python
class MLSignalService:
    def score_trade(self, features: Dict[str, Any]) -> float: ...
    def is_available(self) -> bool: ...
    def get_adaptive_threshold(self, base: float, vix: float, is_earnings: bool) -> float: ...
```

**Adaptive Thresholds**:
- Low VIX (< 15): Bonus +3% (more permissive)
- High VIX (> 25): Penalty +5% (stricter)
- Earnings season: Bonus +2%

---

## account_analytics.py

**Purpose**: Account-level ML analytics orchestrator.

**Description**:
Coordinates 5 ML models for holistic portfolio intelligence:

1. **RiskAdjustmentEngine**: Dynamic risk multipliers
2. **BotAllocationModel**: Optimal bot budget allocation
3. **RegimeSizer**: Regime-aware position sizing
4. **DrawdownPredictor**: Probability of upcoming drawdown
5. **AnomalyDetector**: Unusual pattern detection

**Key Class**:
```python
class AccountAnalyticsService:
    def analyze(self, account_state: Dict, regime: MarketRegimeAnalysis) -> AccountAnalytics: ...
```

**AccountAnalytics Dataclass**:
```python
@dataclass
class AccountAnalytics:
    risk_multiplier: float
    bot_allocations: Dict[str, float]
    position_size_multiplier: float
    drawdown_probability: float
    is_anomaly: bool
    overall_health_score: float
    should_halt_trading: bool
```

---

## Other ML Modules

| File | Purpose |
|------|---------|
| `feature_extractor.py` | Extract ML features from market data |
| `trade_outcome_tracker.py` | Track trade outcomes for training |
| `performance_metrics.py` | Calculate Sharpe, win rate, etc. |
| `performance_analytics.py` | Performance analysis and reporting |
| `metrics_repository.py` | Store/retrieve ML metrics |
| `base_model.py` | Base class for ML models |
| `models/risk_adjustment.py` | Risk adjustment model |
| `models/bot_allocation.py` | Bot allocation model |
| `models/regime_sizer.py` | Regime sizing model |
| `models/drawdown_predictor.py` | Drawdown prediction model |
| `models/anomaly_detector.py` | Anomaly detection model |

---

# Risk Management

Located in `src/trading_hydra/risk/`

## position_sizer.py

**Purpose**: Dynamic position sizing with multiple adjustments.

**Description**:
Heuristic position sizing system that calculates optimal position sizes based on volatility, ML confidence, regime, and correlation.

**Adjustments Applied**:
1. Account equity (% of NAV)
2. Asset volatility (ATR-adjusted)
3. ML signal confidence (fractional Kelly)
4. Market regime (VIX-based)
5. Correlation exposure

**Key Class**:
```python
class InstitutionalPositionSizer:
    def calculate_position_size(
        self,
        symbol: str,
        side: str,
        current_price: float,
        equity: float,
        atr: float,
        ml_probability: float,
        vix: float,
        correlation_score: float
    ) -> PositionSizeResult: ...
```

**Constants**:
| Constant | Value | Description |
|----------|-------|-------------|
| BASE_RISK_PCT | 0.5% | Base risk per trade |
| MIN_NOTIONAL | $15 | Minimum position size |
| MAX_SINGLE_POSITION_PCT | 3% | Max single position |
| KELLY_FRACTION | 0.25 | Kelly criterion fraction |

---

## trailing_stop.py

**Purpose**: Synthetic trailing stop implementation.

**Description**:
Manages trailing stops for all asset classes with SQLite persistence. Supports activation thresholds and multiple exit order types.

**Key Class**:
```python
class TrailingStopManager:
    def init_for_position(self, bot_id: str, position_id: str, symbol: str, 
                         entry_price: float, side: str, config: TrailingStopConfig) -> TrailingStopState: ...
    def update(self, bot_id: str, position_id: str, symbol: str, 
              current_price: float) -> Tuple[bool, float]: ...
    def should_exit(self, bot_id: str, position_id: str, symbol: str,
                   current_price: float) -> bool: ...
```

**TrailingStopConfig**:
```python
@dataclass
class TrailingStopConfig:
    enabled: bool = True
    mode: str = "percent"  # or "price"
    value: float = 1.0     # 1% if percent
    activation_profit_pct: float = 0.3
    exit_order_type: str = "market"
```

---

## correlation_manager.py

**Purpose**: Sector and asset correlation management.

**Description**:
Tracks correlation between positions to prevent concentration risk. Reduces position sizes when too many correlated assets are held.

---

## killswitch.py

**Purpose**: Global kill-switch management.

**Description**:
Manages the global trading halt independent of per-strategy kill-switches.

---

# Dashboard

Located in `src/trading_hydra/dashboard/`

## routes.py

**Purpose**: Flask routes for web dashboard.

**Port**: 5000

**Description**:
Provides the web interface for monitoring and controlling the trading system.

**Endpoints**:
| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Dashboard home |
| `/api/status` | GET | System status JSON |
| `/api/positions` | GET | Current positions |
| `/api/bot/<id>/toggle` | POST | Enable/disable bot |
| `/api/halt` | POST | Manual halt |
| `/api/logs` | GET | Recent log entries |

---

# Indicators

Located in `src/trading_hydra/indicators/`

## turtle_trend.py

**Purpose**: Turtle Traders indicator implementation.

**Description**:
Implements Donchian channel breakouts with ATR-based position sizing as used by Richard Dennis's Turtle Traders.

**Key Class**:
```python
class TurtleTrend:
    def calculate(self, symbol: str, prices: List[float]) -> TurtleSignal: ...
```

**TurtleSignal**:
```python
@dataclass
class TurtleSignal:
    signal_type: SignalType  # LONG, SHORT, NONE
    entry_price: float
    stop_loss: float
    atr: float
    channel_high: float
    channel_low: float
```

---

## indicator_engine.py

**Purpose**: General indicator calculation engine.

**Description**:
Provides common technical indicators (SMA, EMA, RSI, MACD) for strategy validation.

---

# Tests

Located in `src/trading_hydra/tests/`

| File | Purpose |
|------|---------|
| `test_core_modules.py` | Core module unit tests |
| `test_momentum_bot.py` | MomentumBot tests |
| `test_options_bot.py` | OptionsBot tests |
| `test_crypto_bot.py` | CryptoBot tests |
| `test_exitbot.py` | ExitBot tests |
| `test_trailing_stops.py` | Trailing stop tests |
| `test_halt_behavior.py` | Halt mechanism tests |
| `test_integration.py` | Integration tests |
| `bot_stress_test.py` | Load/stress testing |
| `qc_runner.py` | Quality check runner |
| `conftest.py` | Pytest fixtures |

---

# Summary

## Module Count by Category

| Category | Count |
|----------|-------|
| Entry Points | 2 |
| Core | 10 |
| Bots | 4 |
| Services | 18 |
| Strategy | 7 |
| ML | 12 |
| Risk | 4 |
| Dashboard | 2 |
| Indicators | 2 |
| Tests | 12 |
| **Total** | **73** |

## Key Design Patterns

1. **Fail-Closed Safety**: All errors trigger halt, not silent failures
2. **Config-Driven**: All parameters in YAML, no hardcoded values
3. **Singleton Services**: `get_*()` functions return cached instances
4. **Decision Records**: Full audit trail for compliance
5. **Graceful Degradation**: ML fallback when models unavailable

---

**END OF MODULE REFERENCE**

*This document is classified DEVELOPER REFERENCE and should be used alongside the SOP and Field Manual for complete system understanding.*
