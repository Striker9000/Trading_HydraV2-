"""
=============================================================================
Backtest Engine - Historical Data Testing & Auto-Optimization
=============================================================================

Simulates trading strategies against historical data to find optimal configs.

Features:
1. Load historical OHLC data from Alpaca (stocks & crypto)
2. Simulate Turtle breakout strategy with configurable parameters
3. Track simulated P&L, win rate, Sharpe ratio, max drawdown
4. Grid search across parameter combinations
5. Output recommended configs based on backtest results

Usage:
    from trading_hydra.backtest import BacktestEngine
    
    engine = BacktestEngine()
    results = engine.run_backtest(
        symbols=["BTC/USD", "ETH/USD"],
        start_date="2025-01-01",
        end_date="2025-01-28",
        strategy="turtle"
    )
    
    # Auto-optimize
    best_config = engine.optimize(
        symbols=["BTC/USD"],
        param_grid={"entry_lookback": [120, 240, 480], "stop_loss_pct": [1.0, 1.5, 2.0]}
    )
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
import statistics
import json
import yaml
import os

from ..core.logging import get_logger
from ..risk.math_validation import get_math_validator, TransactionCosts

logger = get_logger()

DEFAULT_TRANSACTION_COSTS = TransactionCosts(
    spread_cost_bps=5.0,
    slippage_bps=5.0
)


@dataclass
class Trade:
    """A simulated trade."""
    symbol: str
    side: str  # "buy" or "sell"
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None  # "stop_loss", "take_profit", "trailing_stop", "time_stop", "signal"
    quantity: float = 1.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    
    @property
    def is_open(self) -> bool:
        return self.exit_time is None
    
    @property
    def is_winner(self) -> bool:
        return self.pnl > 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "entry_price": self.entry_price,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "quantity": self.quantity,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct
        }


@dataclass
class BacktestResult:
    """Results from a single backtest run."""
    strategy: str
    symbols: List[str]
    start_date: datetime
    end_date: datetime
    config: Dict[str, Any]
    trades: List[Trade]
    
    # Performance metrics
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    num_trades: int = 0
    avg_trade_pnl: float = 0.0
    avg_winner: float = 0.0
    avg_loser: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "symbols": self.symbols,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "config": self.config,
            "metrics": {
                "total_pnl": round(self.total_pnl, 2),
                "total_pnl_pct": round(self.total_pnl_pct, 2),
                "win_rate": round(self.win_rate * 100, 1),
                "profit_factor": round(self.profit_factor, 2),
                "sharpe_ratio": round(self.sharpe_ratio, 2),
                "max_drawdown_pct": round(self.max_drawdown_pct, 2),
                "num_trades": self.num_trades,
                "avg_trade_pnl": round(self.avg_trade_pnl, 2),
                "avg_winner": round(self.avg_winner, 2),
                "avg_loser": round(self.avg_loser, 2)
            },
            "trades": [t.to_dict() for t in self.trades]
        }


@dataclass
class OptimizationResult:
    """Results from parameter optimization."""
    best_config: Dict[str, Any]
    best_metrics: Dict[str, float]
    all_results: List[Dict[str, Any]]
    improvement_vs_default: float
    recommended_changes: List[Dict[str, Any]]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "best_config": self.best_config,
            "best_metrics": self.best_metrics,
            "num_combinations_tested": len(self.all_results),
            "improvement_vs_default_pct": round(self.improvement_vs_default, 1),
            "recommended_changes": self.recommended_changes,
            "top_5_configs": sorted(self.all_results, key=lambda x: x["sharpe_ratio"], reverse=True)[:5]
        }


class BacktestEngine:
    """
    Backtesting engine for historical strategy testing and optimization.
    
    Features:
    - Auto-detects ticker type (crypto/stock/ETF)
    - Uses symbol-specific parameters when available
    - Routes optimizations to correct bot configs
    """
    
    def __init__(self, initial_capital: float = 10000.0):
        """
        Initialize backtest engine.
        
        Args:
            initial_capital: Starting capital for simulations ($)
        """
        self.initial_capital = initial_capital
        self.alpaca_client = None
        self._data_cache = {}  # Cache for historical data
        self._symbol_profiles = {}  # Cache for symbol-specific params
        self._init_alpaca()
        self._load_symbol_profiles()
    
    def _load_symbol_profiles(self):
        """Load symbol-specific parameters from config."""
        try:
            config_path = "config/bots.yaml"
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    config = yaml.safe_load(f)
                self._symbol_profiles = config.get("symbol_profiles", {})
                logger.info(f"Loaded {len(self._symbol_profiles)} symbol profiles")
        except Exception as e:
            logger.warn(f"Could not load symbol profiles: {e}")
            self._symbol_profiles = {}
    
    def get_symbol_params(self, symbol: str) -> Dict[str, Any]:
        """
        Get parameters for a specific symbol.
        
        Checks symbol_profiles first, then falls back to defaults.
        
        Args:
            symbol: Trading symbol (e.g., "BTC/USD", "AAPL")
            
        Returns:
            Dict of parameters for this symbol
        """
        # Convert symbol to profile key (BTC/USD -> BTC_USD)
        profile_key = symbol.replace("/", "_").upper()
        
        # Default params
        defaults = {
            "entry_lookback": 240,
            "exit_lookback": 120,
            "atr_period": 240,
            "stop_loss_pct": 1.5,
            "take_profit_pct": 1.5,
            "trailing_stop_pct": 0.6,
            "trailing_activation_pct": 1.5
        }
        
        # Overlay symbol-specific params
        if profile_key in self._symbol_profiles:
            profile = self._symbol_profiles[profile_key]
            for key, value in profile.items():
                if key != "last_optimized" and value is not None:
                    defaults[key] = value
            logger.info(f"Using custom params for {symbol}")
        
        return defaults
        
    def _init_alpaca(self):
        """Initialize Alpaca client for historical data."""
        try:
            from alpaca.data import StockHistoricalDataClient, CryptoHistoricalDataClient
            
            api_key = os.environ.get("ALPACA_KEY") or os.environ.get("ALPACA_API_KEY") or os.environ.get("APCA_API_KEY_ID")
            secret_key = os.environ.get("ALPACA_SECRET") or os.environ.get("ALPACA_SECRET_KEY") or os.environ.get("APCA_API_SECRET_KEY")
            
            if api_key and secret_key:
                self.stock_client = StockHistoricalDataClient(api_key, secret_key)
                self.crypto_client = CryptoHistoricalDataClient(api_key, secret_key)
                logger.info("Alpaca clients initialized for backtest data")
            else:
                logger.warn("No Alpaca credentials - using cached/mock data only")
                self.stock_client = None
                self.crypto_client = None
        except Exception as e:
            logger.error(f"Failed to init Alpaca: {e}")
            self.stock_client = None
            self.crypto_client = None
    
    def load_historical_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        timeframe: str = "1Hour"
    ) -> List[Dict[str, Any]]:
        """
        Load historical OHLCV data from Alpaca.
        
        Args:
            symbol: Trading symbol (e.g., "BTC/USD", "AAPL")
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            timeframe: Bar timeframe ("1Min", "1Hour", "1Day")
            
        Returns:
            List of OHLCV bars with timestamp, open, high, low, close, volume
        """
        # Check cache first
        cache_key = f"{symbol}:{start_date}:{end_date}:{timeframe}"
        if cache_key in self._data_cache:
            return self._data_cache[cache_key]
        
        bars = []
        
        try:
            from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            
            # Parse timeframe
            tf_map = {
                "1Min": TimeFrame.Minute,
                "1Hour": TimeFrame.Hour,
                "1Day": TimeFrame.Day
            }
            tf = tf_map.get(timeframe, TimeFrame.Hour)
            
            start = datetime.fromisoformat(start_date)
            end = datetime.fromisoformat(end_date)
            
            # Determine if crypto or stock
            is_crypto = "/" in symbol
            
            if is_crypto and self.crypto_client:
                request = CryptoBarsRequest(
                    symbol_or_symbols=[symbol],
                    timeframe=tf,
                    start=start,
                    end=end
                )
                response = self.crypto_client.get_crypto_bars(request)
                response_data = response.data if hasattr(response, 'data') else response
                
                try:
                    for bar in response_data.get(symbol, []):
                        bars.append({
                            "timestamp": bar.timestamp,
                            "open": float(bar.open),
                            "high": float(bar.high),
                            "low": float(bar.low),
                            "close": float(bar.close),
                            "volume": float(bar.volume)
                        })
                except (KeyError, TypeError, AttributeError):
                    pass
                        
            elif not is_crypto and self.stock_client:
                request = StockBarsRequest(
                    symbol_or_symbols=[symbol],
                    timeframe=tf,
                    start=start,
                    end=end
                )
                response = self.stock_client.get_stock_bars(request)
                response_data = response.data if hasattr(response, 'data') else response
                
                try:
                    for bar in response_data.get(symbol, []):
                        bars.append({
                            "timestamp": bar.timestamp,
                            "open": float(bar.open),
                            "high": float(bar.high),
                            "low": float(bar.low),
                            "close": float(bar.close),
                            "volume": float(bar.volume)
                        })
                except (KeyError, TypeError, AttributeError):
                    pass
            
            logger.info(f"Loaded {len(bars)} bars for {symbol} from {start_date} to {end_date}")
            
            # Cache the data
            self._data_cache[cache_key] = bars
            
        except Exception as e:
            logger.error(f"Failed to load data for {symbol}: {e}")
            
        return bars
    
    def _calculate_channel(self, bars: List[Dict], lookback: int, idx: int) -> Tuple[float, float]:
        """Calculate Turtle channel high/low over lookback period."""
        if idx < lookback:
            return 0.0, float('inf')
            
        window = bars[idx - lookback:idx]
        channel_high = max(b["high"] for b in window)
        channel_low = min(b["low"] for b in window)
        return channel_high, channel_low
    
    def _calculate_atr(self, bars: List[Dict], period: int, idx: int) -> float:
        """Calculate Average True Range."""
        if idx < period + 1:
            return 0.0
            
        trs = []
        for i in range(idx - period, idx):
            high = bars[i]["high"]
            low = bars[i]["low"]
            prev_close = bars[i - 1]["close"]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)
            
        return statistics.mean(trs) if trs else 0.0
    
    def simulate_turtle_strategy(
        self,
        bars: List[Dict[str, Any]],
        symbol: str,
        config: Dict[str, Any]
    ) -> List[Trade]:
        """
        Simulate Turtle breakout strategy on historical bars.
        
        Args:
            bars: List of OHLCV bars
            symbol: Trading symbol
            config: Strategy configuration
            
        Returns:
            List of simulated trades
        """
        trades = []
        current_trade: Optional[Trade] = None
        trailing_stop_price = 0.0
        
        # Config params
        entry_lookback = config.get("entry_lookback", 240)  # 10 days in hours
        exit_lookback = config.get("exit_lookback", 120)   # 5 days in hours
        atr_period = config.get("atr_period", 240)
        stop_loss_pct = config.get("stop_loss_pct", 1.5) / 100
        take_profit_pct = config.get("take_profit_pct", 1.5) / 100
        trailing_stop_pct = config.get("trailing_stop_pct", 0.6) / 100
        trailing_activation_pct = config.get("trailing_activation_pct", 1.5) / 100
        
        for idx in range(entry_lookback + 1, len(bars)):
            bar = bars[idx]
            price = bar["close"]
            timestamp = bar["timestamp"]
            
            # Calculate channels
            entry_high, entry_low = self._calculate_channel(bars, entry_lookback, idx)
            exit_high, exit_low = self._calculate_channel(bars, exit_lookback, idx)
            atr = self._calculate_atr(bars, atr_period, idx)
            
            # Manage existing position
            if current_trade and not current_trade.is_open:
                current_trade = None
                
            if current_trade:
                entry_price = current_trade.entry_price
                pnl_pct = (price - entry_price) / entry_price
                
                # Check stop-loss
                if pnl_pct <= -stop_loss_pct:
                    current_trade.exit_time = timestamp
                    current_trade.exit_price = price
                    current_trade.exit_reason = "stop_loss"
                    current_trade.pnl = (price - entry_price) * current_trade.quantity
                    current_trade.pnl_pct = pnl_pct * 100
                    trades.append(current_trade)
                    current_trade = None
                    continue
                
                # Check take-profit
                if pnl_pct >= take_profit_pct:
                    current_trade.exit_time = timestamp
                    current_trade.exit_price = price
                    current_trade.exit_reason = "take_profit"
                    current_trade.pnl = (price - entry_price) * current_trade.quantity
                    current_trade.pnl_pct = pnl_pct * 100
                    trades.append(current_trade)
                    current_trade = None
                    continue
                
                # Update trailing stop if activated
                if pnl_pct >= trailing_activation_pct:
                    new_stop = price * (1 - trailing_stop_pct)
                    trailing_stop_price = max(trailing_stop_price, new_stop)
                    
                    if price <= trailing_stop_price:
                        current_trade.exit_time = timestamp
                        current_trade.exit_price = price
                        current_trade.exit_reason = "trailing_stop"
                        current_trade.pnl = (price - entry_price) * current_trade.quantity
                        current_trade.pnl_pct = pnl_pct * 100
                        trades.append(current_trade)
                        current_trade = None
                        continue
                
                # Check exit channel (Turtle exit)
                if price <= exit_low:
                    current_trade.exit_time = timestamp
                    current_trade.exit_price = price
                    current_trade.exit_reason = "signal"
                    current_trade.pnl = (price - entry_price) * current_trade.quantity
                    current_trade.pnl_pct = pnl_pct * 100
                    trades.append(current_trade)
                    current_trade = None
                    continue
            
            # Check for entry (no position)
            if not current_trade:
                if price > entry_high and entry_high > 0:
                    position_size = max(500.0, self.initial_capital * 0.08) / price
                    current_trade = Trade(
                        symbol=symbol,
                        side="buy",
                        entry_time=timestamp,
                        entry_price=price,
                        quantity=position_size
                    )
                    trailing_stop_price = 0.0
        
        # Close any remaining position at last price
        if current_trade:
            last_bar = bars[-1]
            current_trade.exit_time = last_bar["timestamp"]
            current_trade.exit_price = last_bar["close"]
            current_trade.exit_reason = "end_of_data"
            current_trade.pnl = (last_bar["close"] - current_trade.entry_price) * current_trade.quantity
            current_trade.pnl_pct = ((last_bar["close"] - current_trade.entry_price) / current_trade.entry_price) * 100
            trades.append(current_trade)
        
        return trades
    
    def simulate_whipsaw_strategy(
        self,
        bars: List[Dict[str, Any]],
        symbol: str,
        config: Dict[str, Any]
    ) -> List[Trade]:
        """
        Simulate WhipsawTrader mean-reversion strategy on historical bars.
        
        Buys near support (recent lows), sells near resistance (recent highs).
        Uses tight take-profit and stop-loss for quick range captures.
        
        Args:
            bars: List of OHLCV bars
            symbol: Trading symbol
            config: Strategy configuration
            
        Returns:
            List of simulated trades
        """
        trades = []
        current_trade: Optional[Trade] = None
        
        # Config params (defaults from WhipsawTrader)
        range_lookback = config.get("range_lookback", 20)  # Bars to calculate support/resistance
        support_buffer_pct = config.get("support_buffer_pct", 0.2) / 100  # Buffer above support
        resistance_buffer_pct = config.get("resistance_buffer_pct", 0.2) / 100  # Buffer below resistance
        take_profit_pct = config.get("take_profit_pct", 0.75) / 100  # Quick 0.75% TP
        stop_loss_pct = config.get("stop_loss_pct", 0.5) / 100  # Tight 0.5% SL
        trailing_stop_pct = config.get("trailing_stop_pct", 0.3) / 100
        trailing_activation_pct = config.get("trailing_activation_pct", 0.5) / 100
        
        trailing_stop_price = 0.0
        
        for idx in range(range_lookback + 1, len(bars)):
            bar = bars[idx]
            price = bar["close"]
            timestamp = bar["timestamp"]
            
            # Calculate support and resistance levels from recent range
            lookback_bars = bars[idx - range_lookback:idx]
            support = min(b["low"] for b in lookback_bars)
            resistance = max(b["high"] for b in lookback_bars)
            range_size = resistance - support
            
            # Skip if range is too tight (< 0.5% - not enough room for profit)
            if range_size / price < 0.005:
                continue
            
            # Entry zones with buffers
            support_entry_zone = support * (1 + support_buffer_pct)
            resistance_exit_zone = resistance * (1 - resistance_buffer_pct)
            
            # Manage existing position
            if current_trade and not current_trade.is_open:
                current_trade = None
                
            if current_trade:
                entry_price = current_trade.entry_price
                pnl_pct = (price - entry_price) / entry_price
                
                # Check stop-loss
                if pnl_pct <= -stop_loss_pct:
                    current_trade.exit_time = timestamp
                    current_trade.exit_price = price
                    current_trade.exit_reason = "stop_loss"
                    current_trade.pnl = (price - entry_price) * current_trade.quantity
                    current_trade.pnl_pct = pnl_pct * 100
                    trades.append(current_trade)
                    current_trade = None
                    trailing_stop_price = 0.0
                    continue
                
                # Check take-profit
                if pnl_pct >= take_profit_pct:
                    current_trade.exit_time = timestamp
                    current_trade.exit_price = price
                    current_trade.exit_reason = "take_profit"
                    current_trade.pnl = (price - entry_price) * current_trade.quantity
                    current_trade.pnl_pct = pnl_pct * 100
                    trades.append(current_trade)
                    current_trade = None
                    trailing_stop_price = 0.0
                    continue
                
                # Update trailing stop if activated
                if pnl_pct >= trailing_activation_pct:
                    new_stop = price * (1 - trailing_stop_pct)
                    trailing_stop_price = max(trailing_stop_price, new_stop)
                    
                    if price <= trailing_stop_price:
                        current_trade.exit_time = timestamp
                        current_trade.exit_price = price
                        current_trade.exit_reason = "trailing_stop"
                        current_trade.pnl = (price - entry_price) * current_trade.quantity
                        current_trade.pnl_pct = pnl_pct * 100
                        trades.append(current_trade)
                        current_trade = None
                        trailing_stop_price = 0.0
                        continue
                
                # Exit at resistance zone (mean-reversion target)
                if price >= resistance_exit_zone:
                    current_trade.exit_time = timestamp
                    current_trade.exit_price = price
                    current_trade.exit_reason = "resistance_target"
                    current_trade.pnl = (price - entry_price) * current_trade.quantity
                    current_trade.pnl_pct = pnl_pct * 100
                    trades.append(current_trade)
                    current_trade = None
                    trailing_stop_price = 0.0
                    continue
            
            # Check for entry at support zone (no position)
            if not current_trade:
                if price <= support_entry_zone and support > 0:
                    position_size = max(500.0, self.initial_capital * 0.08) / price
                    current_trade = Trade(
                        symbol=symbol,
                        side="buy",
                        entry_time=timestamp,
                        entry_price=price,
                        quantity=position_size
                    )
                    trailing_stop_price = 0.0
        
        # Close any remaining position at last price
        if current_trade:
            last_bar = bars[-1]
            current_trade.exit_time = last_bar["timestamp"]
            current_trade.exit_price = last_bar["close"]
            current_trade.exit_reason = "end_of_data"
            current_trade.pnl = (last_bar["close"] - current_trade.entry_price) * current_trade.quantity
            current_trade.pnl_pct = ((last_bar["close"] - current_trade.entry_price) / current_trade.entry_price) * 100
            trades.append(current_trade)
        
        return trades
    
    def calculate_metrics(
        self,
        trades: List[Trade],
        include_transaction_costs: bool = True,
        asset_class: str = "crypto"
    ) -> Dict[str, float]:
        """
        Calculate performance metrics from trades.
        
        IMPROVED: Now includes transaction cost modeling and proper Sharpe calculation.
        """
        if not trades:
            return {
                "total_pnl": 0, "total_pnl_pct": 0, "win_rate": 0, "profit_factor": 0,
                "sharpe_ratio": 0, "sortino_ratio": 0, "max_drawdown_pct": 0, "num_trades": 0,
                "avg_trade_pnl": 0, "avg_winner": 0, "avg_loser": 0,
                "transaction_costs": 0, "expectancy": 0, "kelly_fraction": 0
            }
        
        pnl_pcts = [t.pnl_pct / 100 for t in trades]
        
        if include_transaction_costs:
            total_costs = 0
            pnls = []
            for t in trades:
                notional = t.entry_price * t.quantity
                cost = DEFAULT_TRANSACTION_COSTS.estimate_round_trip_cost(
                    notional, t.quantity, asset_class
                )
                total_costs += cost
                pnls.append(t.pnl - cost)
        else:
            total_costs = 0
            pnls = [t.pnl for t in trades]
        
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p < 0]
        
        total_pnl = sum(pnls)
        win_rate = len(winners) / len(trades) if trades else 0
        
        gross_profit = sum(winners) if winners else 0
        gross_loss = abs(sum(losers)) if losers else 0
        
        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        elif gross_profit > 0:
            profit_factor = 999.0
        else:
            profit_factor = 0.0
        
        validator = get_math_validator()
        
        daily_returns = self._convert_trades_to_daily_returns(trades)
        periods = 365 if asset_class == "crypto" else 252
        
        sharpe_ratio = validator.annualized_sharpe_ratio(
            daily_returns, periods_per_year=periods
        ) if len(daily_returns) > 1 else 0
        
        sortino_ratio = validator.annualized_sortino_ratio(
            daily_returns, periods_per_year=periods
        ) if len(daily_returns) > 1 else 0
        
        equity_curve = [self.initial_capital]
        for pnl in pnls:
            equity_curve.append(equity_curve[-1] + pnl)
        
        peak = equity_curve[0]
        max_dd = 0
        for eq in equity_curve:
            peak = max(peak, eq)
            dd = (peak - eq) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
        
        avg_winner = statistics.mean(winners) if winners else 0
        avg_loser = abs(statistics.mean(losers)) if losers else 0
        
        expectancy = validator.expectancy(win_rate, avg_winner, avg_loser) if avg_loser > 0 else 0
        kelly = validator.kelly_criterion(win_rate, avg_winner, avg_loser) if avg_loser > 0 else 0
        
        return {
            "total_pnl": total_pnl,
            "total_pnl_pct": (total_pnl / self.initial_capital) * 100,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "sharpe_ratio": sharpe_ratio,
            "sortino_ratio": sortino_ratio,
            "max_drawdown_pct": max_dd * 100,
            "num_trades": len(trades),
            "avg_trade_pnl": statistics.mean(pnls) if pnls else 0,
            "avg_winner": avg_winner,
            "avg_loser": -abs(avg_loser) if avg_loser else 0,
            "transaction_costs": total_costs,
            "expectancy": expectancy,
            "kelly_fraction": kelly
        }
    
    def _convert_trades_to_daily_returns(self, trades: List[Trade]) -> List[float]:
        """Convert trades to daily returns for Sharpe calculation."""
        if not trades:
            return []
        
        daily_pnl: Dict[str, float] = {}
        
        for t in trades:
            if t.exit_time:
                date_key = t.exit_time.strftime("%Y-%m-%d") if hasattr(t.exit_time, 'strftime') else str(t.exit_time)[:10]
                daily_pnl[date_key] = daily_pnl.get(date_key, 0) + t.pnl
        
        if not daily_pnl:
            return []
        
        daily_returns = [pnl / self.initial_capital for pnl in daily_pnl.values()]
        return daily_returns
    
    def run_backtest(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
        strategy: str = "turtle",
        config: Optional[Dict[str, Any]] = None,
        use_symbol_profiles: bool = True
    ) -> BacktestResult:
        """
        Run a single backtest with given configuration.
        
        Args:
            symbols: List of symbols to test
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            strategy: Strategy name ("turtle")
            config: Strategy configuration (uses defaults if None)
            use_symbol_profiles: If True, load symbol-specific params from config
            
        Returns:
            BacktestResult with trades and metrics
        """
        # Use provided config, or load from symbol profiles, or use defaults
        base_config = config if config else {
            "entry_lookback": 240,
            "exit_lookback": 120,
            "atr_period": 240,
            "stop_loss_pct": 1.5,
            "take_profit_pct": 1.5,
            "trailing_stop_pct": 0.6,
            "trailing_activation_pct": 1.5
        }
        
        all_trades = []
        
        for symbol in symbols:
            logger.info(f"Running backtest for {symbol} from {start_date} to {end_date}")
            
            # Get symbol-specific config if enabled
            if use_symbol_profiles and config is None:
                symbol_config = self.get_symbol_params(symbol)
            else:
                symbol_config = base_config
            
            # Load historical data
            bars = self.load_historical_data(symbol, start_date, end_date, "1Hour")
            
            if not bars:
                logger.warn(f"No data for {symbol}, skipping")
                continue
            
            # Run strategy simulation
            if strategy == "turtle":
                trades = self.simulate_turtle_strategy(bars, symbol, symbol_config)
            elif strategy == "whipsaw":
                trades = self.simulate_whipsaw_strategy(bars, symbol, symbol_config)
            else:
                logger.error(f"Unknown strategy: {strategy}")
                continue
            
            all_trades.extend(trades)
            logger.info(f"{symbol}: {len(trades)} trades simulated")
        
        # Calculate metrics
        metrics = self.calculate_metrics(all_trades)
        
        result = BacktestResult(
            strategy=strategy,
            symbols=symbols,
            start_date=datetime.fromisoformat(start_date),
            end_date=datetime.fromisoformat(end_date),
            config=config or {},
            trades=all_trades,
            total_pnl=metrics["total_pnl"],
            total_pnl_pct=metrics["total_pnl_pct"],
            win_rate=metrics["win_rate"],
            profit_factor=metrics["profit_factor"],
            sharpe_ratio=metrics["sharpe_ratio"],
            max_drawdown_pct=metrics["max_drawdown_pct"],
            num_trades=int(metrics["num_trades"]),
            avg_trade_pnl=metrics["avg_trade_pnl"],
            avg_winner=metrics["avg_winner"],
            avg_loser=metrics["avg_loser"]
        )
        
        return result
    
    def optimize(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
        param_grid: Dict[str, List[Any]],
        strategy: str = "turtle",
        optimize_for: str = "sharpe_ratio"
    ) -> OptimizationResult:
        """
        Grid search optimization across parameter combinations.
        
        Args:
            symbols: Symbols to test
            start_date: Start date
            end_date: End date
            param_grid: Dict of param name -> list of values to test
            strategy: Strategy name
            optimize_for: Metric to optimize ("sharpe_ratio", "total_pnl", "win_rate", "profit_factor")
            
        Returns:
            OptimizationResult with best config and all results
        """
        from itertools import product
        
        # Generate all parameter combinations
        param_names = list(param_grid.keys())
        param_values = list(param_grid.values())
        combinations = list(product(*param_values))
        
        logger.info(f"Testing {len(combinations)} parameter combinations")
        
        all_results = []
        default_config = {
            "entry_lookback": 240,
            "exit_lookback": 120,
            "atr_period": 240,
            "stop_loss_pct": 1.5,
            "take_profit_pct": 1.5,
            "trailing_stop_pct": 0.6,
            "trailing_activation_pct": 1.5
        }
        
        # Run default first
        default_result = self.run_backtest(symbols, start_date, end_date, strategy, default_config)
        default_metric = getattr(default_result, optimize_for, 0)
        
        # Test each combination
        for combo in combinations:
            config = default_config.copy()
            for i, param in enumerate(param_names):
                config[param] = combo[i]
            
            result = self.run_backtest(symbols, start_date, end_date, strategy, config)
            
            all_results.append({
                "config": config.copy(),
                "total_pnl": result.total_pnl,
                "win_rate": result.win_rate,
                "profit_factor": result.profit_factor,
                "sharpe_ratio": result.sharpe_ratio,
                "max_drawdown_pct": result.max_drawdown_pct,
                "num_trades": result.num_trades
            })
        
        # Find best configuration
        best_result = max(all_results, key=lambda x: x.get(optimize_for, 0))
        best_metric = best_result.get(optimize_for, 0)
        
        if default_metric != 0:
            improvement = ((best_metric - default_metric) / abs(default_metric) * 100)
        elif best_metric > 0:
            improvement = 100.0  # Any positive result is improvement over 0
        else:
            improvement = 0.0
        
        # Generate recommended changes
        recommended_changes = []
        for param in param_names:
            # Skip params not in default config (asset-type specific params)
            if param not in default_config:
                continue
            if best_result["config"][param] != default_config[param]:
                default_val = default_config[param]
                best_val = best_result["config"][param]
                change_pct = 0
                if default_val != 0:
                    change_pct = ((best_val - default_val) / default_val * 100)
                recommended_changes.append({
                    "parameter": param,
                    "current": default_val,
                    "recommended": best_val,
                    "change_pct": change_pct
                })
        
        return OptimizationResult(
            best_config=best_result["config"],
            best_metrics={
                "total_pnl": best_result["total_pnl"],
                "win_rate": best_result["win_rate"],
                "profit_factor": best_result["profit_factor"],
                "sharpe_ratio": best_result["sharpe_ratio"],
                "max_drawdown_pct": best_result["max_drawdown_pct"],
                "num_trades": best_result["num_trades"]
            },
            all_results=all_results,
            improvement_vs_default=improvement,
            recommended_changes=recommended_changes
        )
    
    def export_results(self, result: BacktestResult, filepath: str):
        """Export backtest results to JSON file."""
        with open(filepath, 'w') as f:
            json.dump(result.to_dict(), f, indent=2, default=str)
        logger.info(f"Results exported to {filepath}")
    
    def export_optimization(self, result: OptimizationResult, filepath: str):
        """Export optimization results to JSON file."""
        with open(filepath, 'w') as f:
            json.dump(result.to_dict(), f, indent=2, default=str)
        logger.info(f"Optimization results exported to {filepath}")


# Convenience function for quick backtesting
def run_quick_backtest(
    symbols: List[str] = ["BTC/USD", "ETH/USD"],
    days: int = 30,
    config: Optional[Dict] = None
) -> BacktestResult:
    """
    Run a quick backtest on recent data.
    
    Args:
        symbols: Symbols to test
        days: Number of days to backtest
        config: Strategy config (optional)
        
    Returns:
        BacktestResult
    """
    engine = BacktestEngine()
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    
    return engine.run_backtest(symbols, start_date, end_date, "turtle", config)


# Convenience function for quick optimization
def run_quick_optimization(
    symbols: List[str] = ["BTC/USD"],
    days: int = 60
) -> OptimizationResult:
    """
    Run quick parameter optimization.
    
    Args:
        symbols: Symbols to test
        days: Days of historical data
        
    Returns:
        OptimizationResult with best config
    """
    engine = BacktestEngine()
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    
    param_grid = {
        "entry_lookback": [120, 240, 480],  # 5, 10, 20 days
        "exit_lookback": [60, 120, 240],    # 2.5, 5, 10 days
        "stop_loss_pct": [1.0, 1.5, 2.0],
        "take_profit_pct": [1.0, 1.5, 2.0, 2.5],
        "trailing_stop_pct": [0.4, 0.6, 0.8]
    }
    
    return engine.optimize(symbols, start_date, end_date, param_grid)
