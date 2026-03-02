"""
Feature Extractor - Computes ML features from price data.

Provides RSI, MACD, volume z-scores, cross-asset correlation,
and other technical indicators for ML trade scoring.
"""

from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
import numpy as np

from ..core.logging import get_logger


@dataclass
class TechnicalFeatures:
    """Container for computed technical features."""
    rsi: float = 50.0
    rsi_signal: int = 0
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0
    macd_signal_line: int = 0
    volume_zscore: float = 0.0
    volume_ratio: float = 1.0
    volume_surge_pct: float = 0.0      # NEW: % above 20-day avg volume
    price_zscore: float = 0.0
    momentum_5: float = 0.0
    momentum_10: float = 0.0
    volatility_20: float = 0.0
    atr_14: float = 0.0
    atr_ratio: float = 1.0             # NEW: Current ATR / 20-day avg ATR
    sma_5: float = 0.0
    sma_10: float = 0.0
    sma_20: float = 0.0
    ema_12: float = 0.0
    ema_26: float = 0.0
    bb_upper: float = 0.0
    bb_lower: float = 0.0
    bb_position: float = 0.5
    stochastic_k: float = 50.0
    stochastic_d: float = 50.0
    gap_pct: float = 0.0               # NEW: Gap from previous close %
    range_position: float = 0.5        # NEW: Position in day's range (0=low, 1=high)
    trend_strength: float = 0.0        # NEW: ADX-like trend strength
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for ML model input."""
        return {
            "rsi": self.rsi,
            "rsi_signal": self.rsi_signal,
            "macd": self.macd,
            "macd_signal": self.macd_signal,
            "macd_histogram": self.macd_histogram,
            "macd_signal_line": self.macd_signal_line,
            "volume_zscore": self.volume_zscore,
            "volume_ratio": self.volume_ratio,
            "volume_surge_pct": self.volume_surge_pct,
            "price_zscore": self.price_zscore,
            "momentum_5": self.momentum_5,
            "momentum_10": self.momentum_10,
            "volatility_20": self.volatility_20,
            "atr_14": self.atr_14,
            "atr_ratio": self.atr_ratio,
            "sma_5": self.sma_5,
            "sma_10": self.sma_10,
            "sma_20": self.sma_20,
            "ema_12": self.ema_12,
            "ema_26": self.ema_26,
            "bb_upper": self.bb_upper,
            "bb_lower": self.bb_lower,
            "bb_position": self.bb_position,
            "stochastic_k": self.stochastic_k,
            "stochastic_d": self.stochastic_d,
            "gap_pct": self.gap_pct,
            "range_position": self.range_position,
            "trend_strength": self.trend_strength
        }


@dataclass
class CrossAssetFeatures:
    """Container for cross-asset correlation features."""
    btc_correlation: float = 0.0
    eth_correlation: float = 0.0
    spy_correlation: float = 0.0
    dxy_correlation: float = 0.0
    sector_momentum: float = 0.0
    relative_strength: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for ML model input."""
        return {
            "btc_correlation": self.btc_correlation,
            "eth_correlation": self.eth_correlation,
            "spy_correlation": self.spy_correlation,
            "dxy_correlation": self.dxy_correlation,
            "sector_momentum": self.sector_momentum,
            "relative_strength": self.relative_strength
        }


class FeatureExtractor:
    """
    Extracts ML features from price and volume data.
    
    Computes technical indicators, volume analysis, and cross-asset
    correlation features for trade scoring models.
    """
    
    def __init__(self):
        self._logger = get_logger()
        self._price_cache: Dict[str, List[Tuple[float, float, float, float, float]]] = {}
        self._volume_cache: Dict[str, List[float]] = {}
    
    def compute_rsi(self, prices: List[float], period: int = 14) -> float:
        """
        Compute Relative Strength Index.
        
        RSI = 100 - (100 / (1 + RS))
        RS = Average Gain / Average Loss
        """
        if len(prices) < period + 1:
            return 50.0
        
        try:
            deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
            gains = [d if d > 0 else 0 for d in deltas]
            losses = [-d if d < 0 else 0 for d in deltas]
            
            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period
            
            if avg_loss == 0:
                return 100.0 if avg_gain > 0 else 50.0
            
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            
            return max(0.0, min(100.0, rsi))
        except Exception:
            return 50.0
    
    def compute_macd(self, prices: List[float], 
                     fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[float, float, float]:
        """
        Compute MACD (Moving Average Convergence Divergence).
        
        Returns: (macd_line, signal_line, histogram)
        """
        if len(prices) < slow:
            return (0.0, 0.0, 0.0)
        
        try:
            ema_fast = self._compute_ema(prices, fast)
            ema_slow = self._compute_ema(prices, slow)
            
            macd_line = ema_fast - ema_slow
            
            if len(prices) >= slow + signal:
                macd_series = []
                for i in range(slow - 1, len(prices)):
                    ef = self._compute_ema(prices[:i+1], fast)
                    es = self._compute_ema(prices[:i+1], slow)
                    macd_series.append(ef - es)
                signal_line = self._compute_ema(macd_series, signal)
            else:
                signal_line = macd_line
            
            histogram = macd_line - signal_line
            
            return (macd_line, signal_line, histogram)
        except Exception:
            return (0.0, 0.0, 0.0)
    
    def _compute_ema(self, data: List[float], period: int) -> float:
        """Compute Exponential Moving Average."""
        if len(data) < period:
            return sum(data) / len(data) if data else 0.0
        
        multiplier = 2 / (period + 1)
        ema = sum(data[:period]) / period
        
        for price in data[period:]:
            ema = (price - ema) * multiplier + ema
        
        return ema
    
    def _compute_sma(self, data: List[float], period: int) -> float:
        """Compute Simple Moving Average."""
        if len(data) < period:
            return sum(data) / len(data) if data else 0.0
        return sum(data[-period:]) / period
    
    def compute_volume_zscore(self, volumes: List[float], lookback: int = 20) -> Tuple[float, float]:
        """
        Compute volume z-score and volume ratio.
        
        Returns: (z_score, volume_ratio)
        """
        if len(volumes) < 2:
            return (0.0, 1.0)
        
        try:
            recent_avg = sum(volumes[-lookback:]) / min(len(volumes), lookback)
            current_vol = volumes[-1]
            
            if len(volumes) >= lookback:
                std = np.std(volumes[-lookback:])
                mean = np.mean(volumes[-lookback:])
                z_score = (current_vol - mean) / std if std > 0 else 0.0
            else:
                z_score = 0.0
            
            ratio = current_vol / recent_avg if recent_avg > 0 else 1.0
            
            return (float(z_score), float(ratio))
        except Exception:
            return (0.0, 1.0)
    
    def compute_atr(self, highs: List[float], lows: List[float], 
                    closes: List[float], period: int = 14) -> float:
        """Compute Average True Range."""
        if len(highs) < period or len(lows) < period or len(closes) < period:
            return 0.0
        
        try:
            true_ranges = []
            for i in range(1, len(highs)):
                high_low = highs[i] - lows[i]
                high_close = abs(highs[i] - closes[i-1])
                low_close = abs(lows[i] - closes[i-1])
                true_ranges.append(max(high_low, high_close, low_close))
            
            if len(true_ranges) < period:
                return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0
            
            return sum(true_ranges[-period:]) / period
        except Exception:
            return 0.0
    
    def compute_bollinger_bands(self, prices: List[float], 
                                period: int = 20, std_dev: float = 2.0) -> Tuple[float, float, float]:
        """
        Compute Bollinger Bands.
        
        Returns: (upper_band, lower_band, position)
        Position: 0 = at lower band, 0.5 = at middle, 1 = at upper band
        """
        if len(prices) < period:
            return (0.0, 0.0, 0.5)
        
        try:
            sma = sum(prices[-period:]) / period
            std = np.std(prices[-period:])
            
            upper = sma + (std_dev * std)
            lower = sma - (std_dev * std)
            
            current = prices[-1]
            band_width = upper - lower
            position = (current - lower) / band_width if band_width > 0 else 0.5
            
            return (float(upper), float(lower), max(0.0, min(1.0, float(position))))
        except Exception:
            return (0.0, 0.0, 0.5)
    
    def compute_stochastic(self, highs: List[float], lows: List[float],
                           closes: List[float], k_period: int = 14, 
                           d_period: int = 3) -> Tuple[float, float]:
        """
        Compute Stochastic Oscillator.
        
        Returns: (%K, %D)
        """
        if len(highs) < k_period or len(lows) < k_period or len(closes) < k_period:
            return (50.0, 50.0)
        
        try:
            highest_high = max(highs[-k_period:])
            lowest_low = min(lows[-k_period:])
            current_close = closes[-1]
            
            if highest_high == lowest_low:
                k = 50.0
            else:
                k = ((current_close - lowest_low) / (highest_high - lowest_low)) * 100
            
            k_values = []
            for i in range(min(d_period, len(closes) - k_period + 1)):
                idx = len(closes) - 1 - i
                hh = max(highs[max(0, idx-k_period+1):idx+1])
                ll = min(lows[max(0, idx-k_period+1):idx+1])
                if hh != ll:
                    k_values.append(((closes[idx] - ll) / (hh - ll)) * 100)
                else:
                    k_values.append(50.0)
            
            d = sum(k_values) / len(k_values) if k_values else k
            
            return (float(k), float(d))
        except Exception:
            return (50.0, 50.0)
    
    def compute_momentum(self, prices: List[float], period: int = 10) -> float:
        """Compute price momentum as percentage change."""
        if len(prices) <= period:
            return 0.0
        
        try:
            old_price = prices[-period-1]
            new_price = prices[-1]
            
            if old_price > 0:
                return ((new_price - old_price) / old_price) * 100
            return 0.0
        except Exception:
            return 0.0
    
    def compute_correlation(self, series1: List[float], series2: List[float]) -> float:
        """Compute Pearson correlation between two price series."""
        if len(series1) < 5 or len(series2) < 5:
            return 0.0
        
        try:
            min_len = min(len(series1), len(series2))
            s1 = series1[-min_len:]
            s2 = series2[-min_len:]
            
            returns1 = [(s1[i] - s1[i-1]) / s1[i-1] if s1[i-1] != 0 else 0 
                        for i in range(1, len(s1))]
            returns2 = [(s2[i] - s2[i-1]) / s2[i-1] if s2[i-1] != 0 else 0 
                        for i in range(1, len(s2))]
            
            if len(returns1) < 3 or len(returns2) < 3:
                return 0.0
            
            corr = np.corrcoef(returns1, returns2)[0, 1]
            return float(corr) if not np.isnan(corr) else 0.0
        except Exception:
            return 0.0
    
    def extract_features(self, prices: List[float],
                        volumes: Optional[List[float]] = None,
                        highs: Optional[List[float]] = None,
                        lows: Optional[List[float]] = None) -> TechnicalFeatures:
        """
        Extract all technical features from price data.
        
        Args:
            prices: List of closing prices (most recent last)
            volumes: Optional list of volumes
            highs: Optional list of high prices
            lows: Optional list of low prices
            
        Returns:
            TechnicalFeatures dataclass with all computed indicators
        """
        features = TechnicalFeatures()
        
        if not prices:
            return features
        
        if highs is None:
            highs = prices
        if lows is None:
            lows = prices
        
        try:
            features.rsi = self.compute_rsi(prices)
            features.rsi_signal = 1 if features.rsi > 70 else (-1 if features.rsi < 30 else 0)
            
            macd, signal, hist = self.compute_macd(prices)
            features.macd = macd
            features.macd_signal = signal
            features.macd_histogram = hist
            features.macd_signal_line = 1 if hist > 0 else (-1 if hist < 0 else 0)
            
            if volumes:
                z, ratio = self.compute_volume_zscore(volumes)
                features.volume_zscore = z
                features.volume_ratio = ratio
            
            if len(prices) >= 20:
                mean = np.mean(prices[-20:])
                std = np.std(prices[-20:])
                features.price_zscore = (prices[-1] - mean) / std if std > 0 else 0.0
                features.volatility_20 = (std / mean) * 100 if mean > 0 else 0.0
            
            features.momentum_5 = self.compute_momentum(prices, 5)
            features.momentum_10 = self.compute_momentum(prices, 10)
            
            features.atr_14 = self.compute_atr(highs, lows, prices)
            
            features.sma_5 = self._compute_sma(prices, 5)
            features.sma_10 = self._compute_sma(prices, 10)
            features.sma_20 = self._compute_sma(prices, 20)
            features.ema_12 = self._compute_ema(prices, 12)
            features.ema_26 = self._compute_ema(prices, 26)
            
            upper, lower, position = self.compute_bollinger_bands(prices)
            features.bb_upper = upper
            features.bb_lower = lower
            features.bb_position = position
            
            k, d = self.compute_stochastic(highs, lows, prices)
            features.stochastic_k = k
            features.stochastic_d = d
            
            # NEW: Volume surge % (current volume vs 20-day average)
            if volumes and len(volumes) >= 20:
                avg_vol = sum(volumes[-20:]) / 20
                if avg_vol > 0:
                    features.volume_surge_pct = ((volumes[-1] - avg_vol) / avg_vol) * 100
            
            # NEW: ATR ratio (current ATR vs 20-day average ATR)
            if len(prices) >= 35:  # Need enough data for rolling ATR
                recent_atr = self.compute_atr(highs[-14:], lows[-14:], prices[-14:])
                older_atr = self.compute_atr(highs[-34:-20], lows[-34:-20], prices[-34:-20])
                if older_atr > 0:
                    features.atr_ratio = recent_atr / older_atr
            
            # NEW: Gap from previous close %
            if len(prices) >= 2:
                prev_close = prices[-2]
                if prev_close > 0:
                    features.gap_pct = ((prices[-1] - prev_close) / prev_close) * 100
            
            # NEW: Range position (where current price is in today's range)
            if len(highs) >= 1 and len(lows) >= 1:
                day_high = highs[-1]
                day_low = lows[-1]
                day_range = day_high - day_low
                if day_range > 0:
                    features.range_position = (prices[-1] - day_low) / day_range
            
            # NEW: Trend strength (ADX-like measure using directional movement)
            if len(prices) >= 14:
                up_moves = []
                down_moves = []
                for i in range(-13, 0):
                    up_move = highs[i] - highs[i-1] if len(highs) > abs(i) else 0
                    down_move = lows[i-1] - lows[i] if len(lows) > abs(i) else 0
                    up_moves.append(max(up_move, 0))
                    down_moves.append(max(down_move, 0))
                avg_up = sum(up_moves) / len(up_moves) if up_moves else 0
                avg_down = sum(down_moves) / len(down_moves) if down_moves else 0
                if avg_up + avg_down > 0:
                    features.trend_strength = abs(avg_up - avg_down) / (avg_up + avg_down) * 100
            
        except Exception as e:
            self._logger.error(f"Feature extraction failed: {e}")
        
        return features
    
    def extract_cross_asset_features(self, symbol_prices: Dict[str, List[float]],
                                     target_symbol: str) -> CrossAssetFeatures:
        """
        Extract cross-asset correlation features.
        
        Args:
            symbol_prices: Dict of symbol -> price history
            target_symbol: The symbol we're computing features for
            
        Returns:
            CrossAssetFeatures with correlation metrics
        """
        features = CrossAssetFeatures()
        
        target_prices = symbol_prices.get(target_symbol, [])
        if not target_prices:
            return features
        
        try:
            if "BTC/USD" in symbol_prices and target_symbol != "BTC/USD":
                features.btc_correlation = self.compute_correlation(
                    target_prices, symbol_prices["BTC/USD"]
                )
            
            if "ETH/USD" in symbol_prices and target_symbol != "ETH/USD":
                features.eth_correlation = self.compute_correlation(
                    target_prices, symbol_prices["ETH/USD"]
                )
            
            if "SPY" in symbol_prices:
                features.spy_correlation = self.compute_correlation(
                    target_prices, symbol_prices["SPY"]
                )
            
            crypto_symbols = [s for s in symbol_prices.keys() if "/USD" in s]
            if len(crypto_symbols) > 1:
                sector_returns = []
                for sym in crypto_symbols:
                    p = symbol_prices[sym]
                    if len(p) >= 5:
                        ret = (p[-1] - p[-5]) / p[-5] if p[-5] > 0 else 0
                        sector_returns.append(ret)
                if sector_returns:
                    features.sector_momentum = np.mean(sector_returns)
            
            if len(target_prices) >= 5:
                target_return = (target_prices[-1] - target_prices[-5]) / target_prices[-5] if target_prices[-5] > 0 else 0
                if features.sector_momentum != 0:
                    features.relative_strength = target_return - features.sector_momentum
            
        except Exception as e:
            self._logger.error(f"Cross-asset feature extraction failed: {e}")
        
        return features


_feature_extractor: Optional[FeatureExtractor] = None


def get_feature_extractor() -> FeatureExtractor:
    """Get or create singleton feature extractor."""
    global _feature_extractor
    if _feature_extractor is None:
        _feature_extractor = FeatureExtractor()
    return _feature_extractor
