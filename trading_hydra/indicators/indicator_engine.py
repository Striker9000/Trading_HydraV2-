"""
=============================================================================
Indicator Engine - Technical Indicator Calculations
=============================================================================
Provides indicator data for strategy signal evaluation.
Implements the IndicatorEngine protocol required by StrategyValidator.

Supports:
- EMA (Exponential Moving Average)
- SMA (Simple Moving Average)  
- RSI (Relative Strength Index)
- Last close price (with lookback)
=============================================================================
"""
from __future__ import annotations

from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import numpy as np

from ..core.logging import get_logger
from ..core.state import get_state, set_state
from ..services.alpaca_client import get_alpaca_client


class IndicatorEngine:
    """
    Technical indicator calculator for strategy signal evaluation.
    
    Caches bar data to avoid repeated API calls within same session.
    Uses Alpaca market data for price history.
    """
    
    def __init__(self):
        self._logger = get_logger()
        self._alpaca = get_alpaca_client()
        self._bar_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._cache_ts: Dict[str, float] = {}
        self._cache_duration = 300  # 5 minutes cache
    
    def ema(self, symbol: str, period: int, lookback_days: int = 0) -> float:
        """
        Calculate EMA for a symbol.
        
        Args:
            symbol: Stock symbol
            period: EMA period (e.g., 10 for EMA10)
            lookback_days: Days back from today (0 = today)
            
        Returns:
            EMA value
        """
        bars = self._get_bars(symbol, period + 10 + lookback_days)
        if not bars or len(bars) < period:
            raise ValueError(f"Insufficient data for EMA({period}) on {symbol}")
        
        target_idx = len(bars) - 1 - lookback_days
        if target_idx < period:
            raise ValueError(f"Not enough data for lookback_days={lookback_days}")
        
        closes = [float(self._get_bar_close(b)) for b in bars[:target_idx + 1]]
        return self._calc_ema(closes, period)
    
    def sma(self, symbol: str, period: int) -> float:
        """
        Calculate SMA for a symbol (current day).
        
        Args:
            symbol: Stock symbol
            period: SMA period (e.g., 200 for SMA200)
            
        Returns:
            SMA value
        """
        bars = self._get_bars(symbol, period + 10)
        if not bars or len(bars) < period:
            raise ValueError(f"Insufficient data for SMA({period}) on {symbol}")
        
        closes = [float(self._get_bar_close(b)) for b in bars[-period:]]
        return sum(closes) / len(closes)
    
    def rsi(self, symbol: str, period: int) -> float:
        """
        Calculate RSI for a symbol.
        
        Args:
            symbol: Stock symbol
            period: RSI period (e.g., 14 for RSI14)
            
        Returns:
            RSI value (0-100)
        """
        bars = self._get_bars(symbol, period + 10)
        if not bars or len(bars) < period + 1:
            raise ValueError(f"Insufficient data for RSI({period}) on {symbol}")
        
        closes = [float(self._get_bar_close(b)) for b in bars]
        return self._calc_rsi(closes, period)
    
    def last_close(self, symbol: str, lookback_days: int = 0) -> float:
        """
        Get the last closing price.
        
        Args:
            symbol: Stock symbol
            lookback_days: Days back from today (0 = today's close)
            
        Returns:
            Close price
        """
        bars = self._get_bars(symbol, lookback_days + 5)
        if not bars:
            raise ValueError(f"No bar data for {symbol}")
        
        target_idx = len(bars) - 1 - lookback_days
        if target_idx < 0:
            raise ValueError(f"Not enough data for lookback_days={lookback_days}")
        
        return float(self._get_bar_close(bars[target_idx]))
    
    def _get_bar_close(self, bar) -> float:
        """
        Extract close price from bar object or dictionary.
        
        Args:
            bar: Bar object or dictionary with close price
            
        Returns:
            Close price as float
        """
        if hasattr(bar, 'close'):
            return float(bar.close)
        elif isinstance(bar, dict):
            return float(bar.get('close', 0))
        else:
            return 0.0
    
    def _get_bars(self, symbol: str, days: int) -> List[Dict[str, Any]]:
        """
        Get historical bar data with caching.
        
        Args:
            symbol: Stock symbol
            days: Number of days of history needed
            
        Returns:
            List of bar dictionaries with OHLCV data
        """
        import time
        cache_key = f"{symbol}_{days}"
        now = time.time()
        
        if cache_key in self._bar_cache:
            if now - self._cache_ts.get(cache_key, 0) < self._cache_duration:
                return self._bar_cache[cache_key]
        
        try:
            bars = self._alpaca.get_bars(symbol, days=days, timeframe="1Day")
            if bars:
                self._bar_cache[cache_key] = bars
                self._cache_ts[cache_key] = now
            return bars
        except Exception as e:
            self._logger.error(f"Failed to get bars for {symbol}: {e}")
            return []
    
    def _calc_ema(self, closes: List[float], period: int) -> float:
        """Calculate EMA from close prices."""
        if len(closes) < period:
            return closes[-1] if closes else 0.0
        
        multiplier = 2 / (period + 1)
        ema = sum(closes[:period]) / period  # SMA for first period
        
        for price in closes[period:]:
            ema = (price * multiplier) + (ema * (1 - multiplier))
        
        return ema
    
    def _calc_rsi(self, closes: List[float], period: int) -> float:
        """Calculate RSI from close prices."""
        if len(closes) < period + 1:
            return 50.0  # Neutral if not enough data
        
        changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains = [c if c > 0 else 0 for c in changes]
        losses = [-c if c < 0 else 0 for c in changes]
        
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
