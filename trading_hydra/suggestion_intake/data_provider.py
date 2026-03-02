"""
Data Provider - Market data adapter for Suggestion Intake Bot.

Uses Alpaca API for market data with fallback adapter interface.
Provides price, ATR, volume, trend context, and VWAP.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta, date
from dataclasses import dataclass
import os

from .tradeintent_schema import MarketContext


class DataProviderInterface(ABC):
    """Abstract interface for market data providers."""
    
    @abstractmethod
    def get_current_price(self, symbol: str) -> float:
        """Get current/latest price for symbol."""
        pass
    
    @abstractmethod
    def get_prev_close(self, symbol: str) -> float:
        """Get previous day's closing price."""
        pass
    
    @abstractmethod
    def get_historical_bars(
        self, 
        symbol: str, 
        timeframe: str, 
        start: datetime, 
        end: datetime
    ) -> List[Dict[str, Any]]:
        """Get historical OHLCV bars."""
        pass
    
    @abstractmethod
    def get_quote(self, symbol: str) -> Dict[str, Any]:
        """Get current bid/ask quote."""
        pass
    
    @abstractmethod
    def get_option_chain(
        self,
        underlying: str,
        expiration_start: date,
        expiration_end: date
    ) -> List[Dict[str, Any]]:
        """Get options chain for underlying."""
        pass


class AlpacaDataProvider(DataProviderInterface):
    """Alpaca API implementation for market data."""
    
    def __init__(self):
        from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
        from alpaca.trading.client import TradingClient
        from alpaca.data.requests import (
            StockBarsRequest, StockLatestQuoteRequest,
            CryptoBarsRequest, CryptoLatestQuoteRequest
        )
        from alpaca.data.timeframe import TimeFrame
        
        api_key = os.environ.get("ALPACA_API_KEY", os.environ.get("APCA_API_KEY_ID", os.environ.get("ALPACA_KEY")))
        secret_key = os.environ.get("ALPACA_SECRET_KEY", os.environ.get("APCA_API_SECRET_KEY", os.environ.get("ALPACA_SECRET")))
        
        self.stock_client = StockHistoricalDataClient(api_key, secret_key)
        self.crypto_client = CryptoHistoricalDataClient()
        self.trading_client = TradingClient(api_key, secret_key, paper=True)
        self.TimeFrame = TimeFrame
        self.StockBarsRequest = StockBarsRequest
        self.StockLatestQuoteRequest = StockLatestQuoteRequest
        self.CryptoBarsRequest = CryptoBarsRequest
        self.CryptoLatestQuoteRequest = CryptoLatestQuoteRequest
        
        self._bar_cache: Dict[str, List[Dict[str, Any]]] = {}
    
    def _is_crypto(self, symbol: str) -> bool:
        """Check if symbol is crypto (contains / or ends with USD)."""
        return "/" in symbol or symbol.endswith("USD")
    
    def get_current_price(self, symbol: str) -> float:
        """Get latest trade price."""
        try:
            if self._is_crypto(symbol):
                from alpaca.data.requests import CryptoLatestTradeRequest
                req = CryptoLatestTradeRequest(symbol_or_symbols=[symbol])
                trade = self.crypto_client.get_crypto_latest_trade(req)
                return float(trade[symbol].price)
            else:
                from alpaca.data.requests import StockLatestTradeRequest
                req = StockLatestTradeRequest(symbol_or_symbols=[symbol])
                trade = self.stock_client.get_stock_latest_trade(req)
                return float(trade[symbol].price)
        except Exception as e:
            bars = self.get_historical_bars(
                symbol, "1Hour", 
                datetime.now() - timedelta(hours=24), 
                datetime.now()
            )
            if bars:
                return bars[-1]["close"]
            raise ValueError(f"Could not get price for {symbol}: {e}")
    
    def get_prev_close(self, symbol: str) -> float:
        """Get previous session close."""
        end = datetime.now()
        start = end - timedelta(days=10)
        
        bars = self.get_historical_bars(symbol, "1Day", start, end)
        if bars and len(bars) >= 2:
            return bars[-2]["close"]
        elif bars and len(bars) >= 1:
            return bars[-1]["close"]
        raise ValueError(f"Could not get prev close for {symbol}")
    
    def get_historical_bars(
        self, 
        symbol: str, 
        timeframe: str, 
        start: datetime, 
        end: datetime
    ) -> List[Dict[str, Any]]:
        """Get historical OHLCV bars."""
        cache_key = f"{symbol}_{timeframe}_{start.date()}_{end.date()}"
        if cache_key in self._bar_cache:
            return self._bar_cache[cache_key]
        
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        tf_map = {
            "1Min": TimeFrame.Minute,
            "5Min": TimeFrame(5, TimeFrameUnit.Minute),
            "15Min": TimeFrame(15, TimeFrameUnit.Minute),
            "1Hour": TimeFrame.Hour,
            "1Day": TimeFrame.Day
        }
        tf = tf_map.get(timeframe, TimeFrame.Hour)
        
        try:
            if self._is_crypto(symbol):
                req = self.CryptoBarsRequest(
                    symbol_or_symbols=[symbol],
                    timeframe=tf,
                    start=start,
                    end=end
                )
                bars_data = self.crypto_client.get_crypto_bars(req)
            else:
                from alpaca.data.enums import DataFeed
                req = self.StockBarsRequest(
                    symbol_or_symbols=[symbol],
                    timeframe=tf,
                    start=start,
                    end=end,
                    feed=DataFeed.IEX
                )
                bars_data = self.stock_client.get_stock_bars(req)
            
            bars = []
            symbol_bars = bars_data.get(symbol, []) if hasattr(bars_data, 'get') else bars_data.data.get(symbol, [])
            for bar in symbol_bars:
                bars.append({
                    "timestamp": bar.timestamp,
                    "open": float(bar.open),
                    "high": float(bar.high),
                    "low": float(bar.low),
                    "close": float(bar.close),
                    "volume": int(bar.volume) if bar.volume >= 1 else 1
                })
            
            self._bar_cache[cache_key] = bars
            return bars
            
        except Exception as e:
            print(f"Error fetching bars for {symbol}: {e}")
            return []
    
    def get_quote(self, symbol: str) -> Dict[str, Any]:
        """Get current bid/ask."""
        try:
            if self._is_crypto(symbol):
                req = self.CryptoLatestQuoteRequest(symbol_or_symbols=[symbol])
                quote = self.crypto_client.get_crypto_latest_quote(req)
                q = quote[symbol]
                return {
                    "bid": float(q.bid_price),
                    "ask": float(q.ask_price),
                    "bid_size": float(q.bid_size),
                    "ask_size": float(q.ask_size)
                }
            else:
                req = self.StockLatestQuoteRequest(symbol_or_symbols=[symbol])
                quote = self.stock_client.get_stock_latest_quote(req)
                q = quote[symbol]
                return {
                    "bid": float(q.bid_price),
                    "ask": float(q.ask_price),
                    "bid_size": int(q.bid_size),
                    "ask_size": int(q.ask_size)
                }
        except Exception as e:
            print(f"Error getting quote for {symbol}: {e}")
            return {"bid": 0, "ask": 0, "bid_size": 0, "ask_size": 0}
    
    def get_option_chain(
        self,
        underlying: str,
        expiration_start: date,
        expiration_end: date
    ) -> List[Dict[str, Any]]:
        """Get options chain from Alpaca."""
        try:
            from alpaca.trading.requests import GetOptionContractsRequest
            
            req = GetOptionContractsRequest(
                underlying_symbols=[underlying],
                expiration_date_gte=expiration_start.isoformat(),
                expiration_date_lte=expiration_end.isoformat(),
                status="active"
            )
            
            contracts = self.trading_client.get_option_contracts(req)
            
            result = []
            for contract in contracts.option_contracts or []:
                result.append({
                    "symbol": contract.symbol,
                    "underlying": underlying,
                    "strike": float(contract.strike_price),
                    "expiration": contract.expiration_date,
                    "option_type": contract.type.value.lower(),
                    "status": contract.status
                })
            
            return result
            
        except Exception as e:
            print(f"Error fetching option chain for {underlying}: {e}")
            return []


def calculate_atr(bars: List[Dict[str, Any]], period: int = 14) -> float:
    """Calculate Average True Range from OHLCV bars."""
    if len(bars) < period + 1:
        return 0.0
    
    true_ranges = []
    for i in range(1, len(bars)):
        high = bars[i]["high"]
        low = bars[i]["low"]
        prev_close = bars[i - 1]["close"]
        
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        true_ranges.append(tr)
    
    if len(true_ranges) < period:
        return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0
    
    return sum(true_ranges[-period:]) / period


def calculate_sma(bars: List[Dict[str, Any]], period: int) -> Optional[float]:
    """Calculate Simple Moving Average."""
    if len(bars) < period:
        return None
    closes = [b["close"] for b in bars[-period:]]
    return sum(closes) / period


def calculate_vwap(bars: List[Dict[str, Any]]) -> Optional[float]:
    """Calculate VWAP from intraday bars."""
    if not bars:
        return None
    
    total_pv = 0.0
    total_volume = 0
    
    for bar in bars:
        typical_price = (bar["high"] + bar["low"] + bar["close"]) / 3
        total_pv += typical_price * bar["volume"]
        total_volume += bar["volume"]
    
    if total_volume == 0:
        return None
    
    return total_pv / total_volume


def get_market_context(
    provider: DataProviderInterface,
    symbol: str
) -> MarketContext:
    """
    Build complete market context for a symbol.
    
    Fetches price, ATR, volume, trend indicators, and gap analysis.
    """
    now = datetime.now()
    
    current_price = provider.get_current_price(symbol)
    prev_close = provider.get_prev_close(symbol)
    
    daily_bars = provider.get_historical_bars(
        symbol, "1Day",
        now - timedelta(days=30),
        now
    )
    
    hourly_bars = provider.get_historical_bars(
        symbol, "1Hour",
        now - timedelta(days=2),
        now
    )
    
    atr_14 = calculate_atr(daily_bars, 14)
    atr_pct = (atr_14 / current_price * 100) if current_price > 0 else 0.0
    
    gap_pct = ((current_price - prev_close) / prev_close * 100) if prev_close > 0 else 0.0
    
    current_volume = daily_bars[-1]["volume"] if daily_bars else 0
    avg_volume_20d = sum(b["volume"] for b in daily_bars[-20:]) // 20 if len(daily_bars) >= 20 else current_volume
    relative_volume = current_volume / avg_volume_20d if avg_volume_20d > 0 else 1.0
    
    sma_9 = calculate_sma(daily_bars, 9)
    sma_21 = calculate_sma(daily_bars, 21)
    
    if sma_9 and sma_21:
        if sma_9 > sma_21 and current_price > sma_9:
            trend_bias = "bullish"
        elif sma_9 < sma_21 and current_price < sma_9:
            trend_bias = "bearish"
        else:
            trend_bias = "neutral"
    else:
        trend_bias = "neutral"
    
    today_bars = [b for b in hourly_bars if b["timestamp"].date() == now.date()]
    vwap = calculate_vwap(today_bars) if today_bars else None
    
    premarket_high = None
    premarket_low = None
    
    return MarketContext(
        symbol=symbol,
        current_price=current_price,
        prev_close=prev_close,
        vwap=vwap,
        atr_14=atr_14,
        atr_pct=atr_pct,
        gap_pct=gap_pct,
        volume=current_volume,
        avg_volume_20d=avg_volume_20d,
        relative_volume=relative_volume,
        trend_bias=trend_bias,
        sma_9=sma_9,
        sma_21=sma_21,
        premarket_high=premarket_high,
        premarket_low=premarket_low,
        timestamp=now
    )


def create_data_provider() -> DataProviderInterface:
    """Factory function to create appropriate data provider."""
    api_key = os.environ.get("ALPACA_API_KEY", os.environ.get("APCA_API_KEY_ID", os.environ.get("ALPACA_KEY")))
    if api_key:
        return AlpacaDataProvider()
    else:
        raise ValueError(
            "No Alpaca API key found. Set ALPACA_API_KEY, APCA_API_KEY_ID, or ALPACA_KEY environment variable."
        )
