"""
SensorsManager - Main background thread for HydraSensors.

Orchestrates:
- Watchlist loading
- Market data fetching (quotes/bars)
- Indicator calculation
- Breadth sensor updates
- Regime detection
- JSON output writing

Design:
- Non-blocking startup
- Fail-open (sensors crash = bots use defaults)
- Rate-limit aware
- Thread-safe state
"""

import os
import json
import time
import threading
from datetime import datetime
from typing import Dict, List, Optional, Any

from .state import ThreadSafeState, RegimeData, TickerSignal, BreadthReading, RegimeState
from .watchlists import WatchlistManager
from .cache import MarketDataCache
from .indicators import IndicatorCalculator
from .breadth import BreadthCalculator
from .regime import RegimeDetector

from ..core.logging import get_logger


class SensorsManager:
    """
    Background sensor thread manager.
    
    Usage:
        sensors = SensorsManager()
        sensors.start()  # Non-blocking, returns immediately
        
        # Later, from trading bots:
        regime = sensors.get_regime()
        signals = sensors.get_signals(tag="COMPUTE_CORE")
    """
    
    def __init__(
        self,
        watchlists_path: str = "config/watchlists.yaml",
        sensors_config_path: str = "config/sensors.yaml",
    ):
        self.logger = get_logger()
        
        # Configuration
        self.watchlists_path = watchlists_path
        self.sensors_config_path = sensors_config_path
        self._config = self._load_config()
        
        # Components
        self.watchlists = WatchlistManager(watchlists_path)
        self.cache = MarketDataCache(
            quote_ttl=self._config.get("cache", {}).get("quote_ttl", 30),
            bar_ttl={
                "1m": self._config.get("cache", {}).get("bar_1m_ttl", 120),
                "5m": self._config.get("cache", {}).get("bar_5m_ttl", 600),
                "1d": self._config.get("cache", {}).get("bar_daily_ttl", 3600),
            }
        )
        self.indicators = IndicatorCalculator()
        self.breadth = BreadthCalculator()
        self.regime_detector = RegimeDetector()
        
        # Thread-safe state
        self.state = ThreadSafeState()
        
        # Thread control
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._started = False
        
        # Timing
        self._last_quotes_update = 0
        self._last_bars_update = 0
        self._last_indicators_update = 0
        self._last_breadth_update = 0
        self._last_regime_update = 0
        self._last_output_write = 0
        self._last_heartbeat = 0
        
        # Market data client (lazy loaded)
        self._market_client = None
    
    def _load_config(self) -> Dict:
        """Load sensors configuration."""
        if not os.path.exists(self.sensors_config_path):
            self.logger.log("sensors_config_not_found", {"path": self.sensors_config_path})
            return {}
        
        try:
            import yaml
            with open(self.sensors_config_path, 'r') as f:
                config = yaml.safe_load(f)
            return config or {}
        except Exception as e:
            self.logger.error(f"Failed to load sensors config: {e}")
            return {}
    
    def _get_market_client(self):
        """Lazy load market data client (Alpaca)."""
        if self._market_client is None:
            try:
                from ..services.alpaca_client import get_alpaca_client
                self._market_client = get_alpaca_client()
            except Exception as e:
                self.logger.error(f"Failed to create market client: {e}")
        return self._market_client
    
    # --- Public API ---
    
    def start(self) -> None:
        """
        Start the background sensor thread.
        
        Non-blocking - returns immediately.
        """
        if self._started:
            self.logger.log("sensors_already_started", {})
            return
        
        self._stop_event.clear()
        
        # Initialize watchlists in state
        self.state.set_watchlists(self.watchlists.get_all_watchlists())
        self.state.set_ticker_tags(self.watchlists.get_ticker_tags_map())
        
        # Start background thread
        self._thread = threading.Thread(
            target=self._run_loop,
            name="HydraSensors",
            daemon=True,  # Dies when main program exits
        )
        self._thread.start()
        self._started = True
        
        self.logger.log("sensors_started", {
            "ticker_count": len(self.watchlists.get_all_tickers()),
            "watchlist_count": len(self.watchlists.get_all_watchlists()),
        })
    
    def stop(self) -> None:
        """Stop the background sensor thread."""
        if not self._started:
            return
        
        self._stop_event.set()
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        
        self._started = False
        self.logger.log("sensors_stopped", {})
    
    def is_ready(self) -> bool:
        """Check if sensors have warmed up."""
        return self.state.is_ready()
    
    def get_warmup_progress(self) -> float:
        """Get warmup progress (0.0 to 1.0)."""
        return self.state.get_warmup_progress()
    
    def get_regime(self) -> RegimeData:
        """Get current market regime."""
        return self.state.get_regime()
    
    def get_signals(self, tag: str = None, limit: int = 10) -> List[TickerSignal]:
        """Get signals, optionally filtered by tag."""
        return self.state.get_signals(tag=tag, limit=limit)
    
    def get_signal(self, ticker: str) -> Optional[TickerSignal]:
        """Get signal for a specific ticker."""
        return self.state.get_signal(ticker)
    
    def get_watchlist(self, name: str) -> List[str]:
        """Get tickers in a named watchlist."""
        return self.state.get_watchlist(name)
    
    def get_tickers_by_tag(self, tag: str) -> List[str]:
        """Get all tickers with a specific tag."""
        return self.state.get_tickers_by_tag(tag)
    
    def get_breadth(self) -> Dict[str, BreadthReading]:
        """Get current breadth sensor readings."""
        return self.state.get_breadth()
    
    def get_status(self) -> Dict[str, Any]:
        """Get current sensor status."""
        return {
            "started": self._started,
            "ready": self.state.is_ready(),
            "warmup_progress": self.state.get_warmup_progress(),
            "last_error": self.state.get_last_error(),
            "last_update": self.state.get_last_update(),
            "cache_stats": self.cache.get_stats(),
        }
    
    # --- Background Loop ---
    
    def _run_loop(self) -> None:
        """Main background loop."""
        self.logger.log("sensors_loop_started", {})
        
        # Get polling intervals from config
        polling = self._config.get("polling", {})
        quotes_interval = polling.get("quotes_interval", 15)
        bars_interval = polling.get("bars_daily_interval", 900)
        indicators_interval = polling.get("indicators_interval", 60)
        breadth_interval = polling.get("breadth_interval", 60)
        regime_interval = polling.get("regime_interval", 300)
        heartbeat_interval = self._config.get("logging", {}).get("heartbeat_interval", 60)
        output_interval = self._config.get("outputs", {}).get("write_interval", 30)
        
        warmup_done = False
        
        while not self._stop_event.is_set():
            try:
                now = time.time()
                
                # Update quotes (most frequent)
                if now - self._last_quotes_update >= quotes_interval:
                    self._update_quotes()
                    self._last_quotes_update = now
                
                # Update bars (less frequent)
                if now - self._last_bars_update >= bars_interval:
                    self._update_bars()
                    self._last_bars_update = now
                
                # Update indicators
                if now - self._last_indicators_update >= indicators_interval:
                    self._update_indicators()
                    self._last_indicators_update = now
                
                # Update breadth sensors
                if now - self._last_breadth_update >= breadth_interval:
                    self._update_breadth()
                    self._last_breadth_update = now
                
                # Update regime
                if now - self._last_regime_update >= regime_interval:
                    self._update_regime()
                    self._last_regime_update = now
                
                # Write outputs
                if now - self._last_output_write >= output_interval:
                    self._write_outputs()
                    self._last_output_write = now
                
                # Heartbeat log
                if now - self._last_heartbeat >= heartbeat_interval:
                    self._log_heartbeat()
                    self._last_heartbeat = now
                
                # Check warmup progress
                if not warmup_done:
                    progress = self._calculate_warmup_progress()
                    self.state.set_warmup_progress(progress)
                    
                    ready_threshold = self._config.get("startup", {}).get("ready_threshold", 0.5)
                    if progress >= ready_threshold:
                        self.state.set_ready(True)
                        warmup_done = True
                        self.logger.log("sensors_ready", {"progress": progress})
                
                # Sleep briefly
                self._stop_event.wait(timeout=1.0)
                
            except Exception as e:
                self.logger.error(f"Sensors loop error: {e}")
                self.state.set_error(str(e))
                self._stop_event.wait(timeout=5.0)  # Back off on error
        
        self.logger.log("sensors_loop_stopped", {})
    
    def _update_quotes(self) -> None:
        """Fetch latest quotes for all tickers."""
        client = self._get_market_client()
        if not client:
            return
        
        # Get equity tickers (non-crypto)
        equity_tickers = [
            t for t in self.watchlists.get_all_tickers()
            if self.watchlists.is_equity(t) and not self.watchlists.is_crypto(t)
        ]
        
        if not equity_tickers:
            return
        
        # Fetch quotes one at a time (Alpaca doesn't have batch quote API)
        # Limit to avoid rate limits
        for ticker in equity_tickers[:20]:
            try:
                quote_data = client.get_latest_quote(ticker, asset_class="stock")
                
                if quote_data:
                    self.cache.set_quote(
                        ticker=ticker,
                        bid=quote_data.get("bid"),
                        ask=quote_data.get("ask"),
                        last=quote_data.get("last") or quote_data.get("close"),
                        volume=quote_data.get("volume"),
                    )
                    
                    # Update signal with quote data
                    signal = self.state.get_signal(ticker) or TickerSignal(
                        ticker=ticker,
                        tags=self.watchlists.get_ticker_tags(ticker),
                        priority=self.watchlists.get_ticker_priority(ticker),
                    )
                    signal.last_price = quote_data.get("last") or quote_data.get("close")
                    signal.bid = quote_data.get("bid")
                    signal.ask = quote_data.get("ask")
                    signal.volume = quote_data.get("volume")
                    signal.last_quote_ts = datetime.now()
                    self.state.set_signal(ticker, signal)
                    
            except Exception as e:
                self.logger.error(f"Quote update failed for {ticker}: {e}")
    
    def _update_bars(self) -> None:
        """Fetch daily bars for all tickers."""
        client = self._get_market_client()
        if not client:
            return
        
        equity_tickers = [
            t for t in self.watchlists.get_all_tickers()
            if self.watchlists.is_equity(t)
        ]
        
        lookback_days = self._config.get("market_data", {}).get("bar_lookback_days", 60)
        
        for ticker in equity_tickers[:30]:  # Limit to avoid rate limits
            try:
                cached = self.cache.get_bars(ticker, "1d")
                if cached:
                    continue  # Still valid
                
                bars = client.get_bars(ticker, timeframe="1Day", limit=lookback_days)
                
                if bars:
                    self.cache.set_bars(ticker, "1d", bars)
                    
            except Exception as e:
                self.logger.error(f"Bar update failed for {ticker}: {e}")
    
    def _update_indicators(self) -> None:
        """Calculate indicators for all tickers."""
        # Get SPY bars for relative strength calculation
        spy_bars = self.cache.get_bars("SPY", "1d")
        spy_bar_dicts = []
        if spy_bars:
            spy_bar_dicts = [{"close": b.close, "high": b.high, "low": b.low} for b in spy_bars]
        
        for ticker in self.watchlists.get_all_tickers():
            try:
                cached_bars = self.cache.get_bars(ticker, "1d")
                if not cached_bars:
                    continue
                
                # Convert to dict format
                bar_dicts = [{"close": b.close, "high": b.high, "low": b.low} for b in cached_bars]
                
                # Calculate indicators
                result = self.indicators.calculate_all(
                    ticker=ticker,
                    bars=bar_dicts,
                    benchmark_bars=spy_bar_dicts if (ticker != "SPY" and spy_bar_dicts) else [],
                )
                
                # Update signal
                signal = self.state.get_signal(ticker) or TickerSignal(
                    ticker=ticker,
                    tags=self.watchlists.get_ticker_tags(ticker),
                    priority=self.watchlists.get_ticker_priority(ticker),
                )
                
                signal.sma_20 = result.sma_20
                signal.sma_50 = result.sma_50
                signal.sma_200 = result.sma_200
                signal.rsi_14 = result.rsi_14
                signal.atr_14 = result.atr_14
                signal.return_1d = result.return_1d
                signal.return_5d = result.return_5d
                signal.return_20d = result.return_20d
                signal.rs_vs_spy_5d = result.rs_vs_spy_5d
                signal.rs_vs_spy_20d = result.rs_vs_spy_20d
                signal.last_bar_ts = datetime.now()
                
                self.state.set_signal(ticker, signal)
                
            except Exception as e:
                self.logger.error(f"Indicator calc failed for {ticker}: {e}")
    
    def _update_breadth(self) -> None:
        """Update breadth sensor readings."""
        for pair_name in self.breadth.get_pairs():
            try:
                pair_config = self.breadth.pairs[pair_name]
                
                ticker_bars = self.cache.get_bars(pair_config.ticker, "1d")
                benchmark_bars = self.cache.get_bars(pair_config.benchmark, "1d")
                
                if not ticker_bars or not benchmark_bars:
                    continue
                
                # Convert to dict format
                ticker_dicts = [{"close": b.close} for b in ticker_bars]
                benchmark_dicts = [{"close": b.close} for b in benchmark_bars]
                
                reading = self.breadth.calculate_reading(
                    pair_name=pair_name,
                    ticker_bars=ticker_dicts,
                    benchmark_bars=benchmark_dicts,
                )
                
                self.state.set_breadth(pair_name, reading)
                
            except Exception as e:
                self.logger.error(f"Breadth update failed for {pair_name}: {e}")
    
    def _update_regime(self) -> None:
        """Update regime detection."""
        try:
            breadth_readings = self.state.get_breadth()
            
            # Get VIX if available
            vix = None
            # TODO: Fetch VIX from market data
            
            # Get SPY indicator data
            spy_signal = self.state.get_signal("SPY")
            spy_price_vs_sma20 = None
            spy_price_vs_sma50 = None
            
            if spy_signal and spy_signal.last_price and spy_signal.sma_20:
                spy_price_vs_sma20 = (spy_signal.last_price - spy_signal.sma_20) / spy_signal.sma_20
            if spy_signal and spy_signal.last_price and spy_signal.sma_50:
                spy_price_vs_sma50 = (spy_signal.last_price - spy_signal.sma_50) / spy_signal.sma_50
            
            # Get SMH spread
            smh_reading = breadth_readings.get("SMH_vs_SPY")
            smh_spread = smh_reading.spread_5d if smh_reading else None
            
            regime = self.regime_detector.detect_regime(
                breadth_readings=breadth_readings,
                vix=vix,
                spy_price_vs_sma20=spy_price_vs_sma20,
                spy_price_vs_sma50=spy_price_vs_sma50,
                smh_vs_spy_spread=smh_spread,
            )
            
            self.state.set_regime(regime)
            
        except Exception as e:
            self.logger.error(f"Regime update failed: {e}")
    
    def _write_outputs(self) -> None:
        """Write JSON snapshot files."""
        outputs_config = self._config.get("outputs", {})
        if not outputs_config.get("enabled", True):
            return
        
        output_path = outputs_config.get("path", "./runtime")
        os.makedirs(output_path, exist_ok=True)
        
        try:
            # Regime state
            regime_file = os.path.join(output_path, outputs_config.get("files", {}).get("regime_state", "regime_state.json"))
            regime = self.state.get_regime()
            with open(regime_file, 'w') as f:
                json.dump(regime.to_dict(), f, indent=2)
            
            # Signals
            signals_file = os.path.join(output_path, outputs_config.get("files", {}).get("signals_latest", "signals_latest.json"))
            signals = self.state.get_signals(limit=50)
            signals_data = [s.to_dict() for s in signals]
            with open(signals_file, 'w') as f:
                json.dump({"signals": signals_data, "count": len(signals_data), "timestamp": datetime.now().isoformat()}, f, indent=2)
            
            # Breadth
            breadth_file = os.path.join(output_path, outputs_config.get("files", {}).get("breadth_state", "breadth_state.json"))
            breadth = self.state.get_breadth()
            breadth_data = {k: v.to_dict() for k, v in breadth.items()}
            with open(breadth_file, 'w') as f:
                json.dump(breadth_data, f, indent=2)
                
        except Exception as e:
            self.logger.error(f"Output write failed: {e}")
    
    def _calculate_warmup_progress(self) -> float:
        """Calculate warmup progress based on data availability."""
        all_tickers = self.watchlists.get_all_tickers()
        if not all_tickers:
            return 0.0
        
        # Count tickers with data
        tickers_with_quotes = 0
        tickers_with_bars = 0
        
        for ticker in all_tickers:
            if self.cache.get_quote(ticker):
                tickers_with_quotes += 1
            if self.cache.get_bars(ticker, "1d"):
                tickers_with_bars += 1
        
        # Weight quotes more (faster to get)
        quote_progress = tickers_with_quotes / len(all_tickers) * 0.4
        bar_progress = tickers_with_bars / len(all_tickers) * 0.6
        
        return quote_progress + bar_progress
    
    def _log_heartbeat(self) -> None:
        """Log heartbeat status."""
        self.logger.log("sensors_heartbeat", {
            "ready": self.state.is_ready(),
            "warmup": f"{self.state.get_warmup_progress() * 100:.1f}%",
            "cache": self.cache.get_stats(),
            "regime": self.state.get_regime().state.value,
        })


# Global instance getter
_manager_instance: Optional[SensorsManager] = None


def get_sensors_manager() -> SensorsManager:
    """Get or create the global sensors manager."""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = SensorsManager()
    return _manager_instance
