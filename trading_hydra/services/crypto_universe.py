"""
Crypto Universe Service - Dynamic coin selection for CryptoBot
===============================================================

Fetches available crypto pairs from Alpaca and ranks them based on:
- 24h trading volume
- Volatility (ATR-based)
- Spread tightness
- Price momentum

This allows the CryptoBot to dynamically select the best coins to trade
rather than using a static list.
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import statistics

from ..core.logging import get_logger
from ..core.state import get_state, set_state
from ..core.config import load_bots_config
from .alpaca_client import get_alpaca_client


@dataclass
class CryptoAsset:
    """Represents a tradeable crypto asset with screening metrics"""
    symbol: str                    # e.g., "BTC/USD"
    base_currency: str             # e.g., "BTC"
    quote_currency: str            # e.g., "USD"
    tradeable: bool = True
    fractionable: bool = True
    min_order_size: float = 0.0
    min_trade_increment: float = 0.0
    price: float = 0.0
    volume_24h: float = 0.0        # 24h volume in USD
    volatility: float = 0.0        # ATR-based volatility %
    spread_pct: float = 0.0        # Current bid/ask spread %
    momentum_1h: float = 0.0       # 1-hour price change %
    momentum_24h: float = 0.0      # 24-hour price change %
    rank_score: float = 0.0        # Composite ranking score


@dataclass
class CryptoUniverseConfig:
    """Configuration for crypto universe screening"""
    min_volume_24h_usd: float = 1_000_000   # Minimum 24h volume
    max_spread_pct: float = 1.0              # Maximum spread allowed
    min_price_usd: float = 0.01              # Minimum coin price
    max_coins: int = 10                      # Max coins to return
    prefer_high_volume: bool = True          # Weight volume in ranking
    prefer_high_volatility: bool = True      # Weight volatility (for momentum)
    exclude_stablecoins: bool = True         # Exclude USDT, USDC, etc.
    usd_pairs_only: bool = True              # Only include /USD pairs
    refresh_interval_minutes: int = 60       # Cache refresh interval
    # Two-stage ML selection
    ml_rerank_enabled: bool = False          # Enable ML-based re-ranking
    ml_rerank_candidates: int = 10           # Number of candidates to evaluate with ML
    ml_rerank_select: int = 3                # Number of coins to select after ML scoring


# Stablecoins to exclude from trading universe
STABLECOINS = {"USDT", "USDC", "DAI", "BUSD", "TUSD", "USDP", "GUSD", "FRAX", "LUSD"}

# Popular coins that typically have good liquidity on Alpaca
POPULAR_COINS = ["BTC", "ETH", "SOL", "AVAX", "LINK", "DOT", "MATIC", "UNI", "AAVE", "LTC", "DOGE", "SHIB"]


class CryptoUniverseService:
    """
    Service to fetch and screen crypto assets for trading.
    
    Uses Alpaca's crypto assets API to get available pairs, then
    enriches with market data for screening and ranking.
    """
    
    def __init__(self):
        self._logger = get_logger()
        self._alpaca = get_alpaca_client()
        self._cache: Dict[str, CryptoAsset] = {}
        self._cache_timestamp: Optional[datetime] = None
        self._config = self._load_config()
    
    def _load_config(self) -> CryptoUniverseConfig:
        """Load universe config from bots.yaml if present"""
        try:
            bots_config = load_bots_config()
            crypto_config = bots_config.get("cryptobot", {})
            universe_config = crypto_config.get("universe", {})
            
            return CryptoUniverseConfig(
                min_volume_24h_usd=universe_config.get("min_volume_24h_usd", 1_000_000),
                max_spread_pct=universe_config.get("max_spread_pct", 1.0),
                min_price_usd=universe_config.get("min_price_usd", 0.01),
                max_coins=universe_config.get("max_coins", 10),
                prefer_high_volume=universe_config.get("prefer_high_volume", True),
                prefer_high_volatility=universe_config.get("prefer_high_volatility", True),
                exclude_stablecoins=universe_config.get("exclude_stablecoins", True),
                usd_pairs_only=universe_config.get("usd_pairs_only", True),
                refresh_interval_minutes=universe_config.get("refresh_interval_minutes", 60),
                ml_rerank_enabled=universe_config.get("ml_rerank_enabled", False),
                ml_rerank_candidates=universe_config.get("ml_rerank_candidates", 10),
                ml_rerank_select=universe_config.get("ml_rerank_select", 3)
            )
        except Exception as e:
            self._logger.error(f"Failed to load crypto universe config: {e}")
            return CryptoUniverseConfig()
    
    def get_available_pairs(self) -> List[str]:
        """
        Get all available crypto trading pairs from Alpaca.
        
        Returns list of symbols like ["BTC/USD", "ETH/USD", ...]
        """
        try:
            trading_client = self._alpaca._trading_client
            if not trading_client:
                self._logger.error("Alpaca trading client not initialized")
                return []
            
            from alpaca.trading.requests import GetAssetsRequest
            from alpaca.trading.enums import AssetClass, AssetStatus
            
            request = GetAssetsRequest(
                asset_class=AssetClass.CRYPTO,
                status=AssetStatus.ACTIVE
            )
            
            assets = trading_client.get_all_assets(request)
            
            pairs = []
            for asset in assets:
                if asset.tradable and asset.status == AssetStatus.ACTIVE:
                    pairs.append(asset.symbol)
            
            self._logger.log("crypto_universe_fetched", {
                "total_pairs": len(pairs),
                "sample": pairs[:5] if pairs else []
            })
            
            return pairs
            
        except Exception as e:
            self._logger.error(f"Failed to fetch crypto pairs: {e}")
            return ["BTC/USD", "ETH/USD"]
    
    def get_screened_coins(self, force_refresh: bool = False) -> List[CryptoAsset]:
        """
        Get screened and ranked crypto coins for trading.
        
        Fetches market data, applies filters, and ranks by composite score.
        Results are cached for performance.
        
        Args:
            force_refresh: Bypass cache and fetch fresh data
            
        Returns:
            List of CryptoAsset objects, sorted by rank_score descending
        """
        now = datetime.utcnow()
        cache_age = (now - self._cache_timestamp).total_seconds() / 60 if self._cache_timestamp else float('inf')
        
        if not force_refresh and cache_age < self._config.refresh_interval_minutes and self._cache:
            self._logger.log("crypto_universe_cache_hit", {"cache_age_min": round(cache_age, 1)})
            return sorted(self._cache.values(), key=lambda x: x.rank_score, reverse=True)[:self._config.max_coins]
        
        self._logger.log("crypto_universe_refresh_start", {})
        
        pairs = self.get_available_pairs()
        
        if self._config.exclude_stablecoins:
            pairs = [p for p in pairs if not self._is_stablecoin(p)]
        
        if self._config.usd_pairs_only:
            pairs = [p for p in pairs if p.endswith("/USD")]
        
        screened: List[CryptoAsset] = []
        
        for symbol in pairs:
            try:
                asset = self._screen_coin(symbol)
                if asset and self._passes_filters(asset):
                    screened.append(asset)
            except Exception as e:
                continue
        
        for asset in screened:
            asset.rank_score = self._calculate_rank_score(asset)
        
        screened.sort(key=lambda x: x.rank_score, reverse=True)
        
        self._cache = {a.symbol: a for a in screened}
        self._cache_timestamp = now
        
        self._logger.log("crypto_universe_refresh_complete", {
            "total_screened": len(screened),
            "top_coins": [a.symbol for a in screened[:5]]
        })
        
        return screened[:self._config.max_coins]
    
    def _is_stablecoin(self, symbol: str) -> bool:
        """Check if symbol is a stablecoin"""
        base = symbol.split("/")[0] if "/" in symbol else symbol
        return base.upper() in STABLECOINS
    
    def _screen_coin(self, symbol: str) -> Optional[CryptoAsset]:
        """
        Fetch market data for a single coin and create CryptoAsset.
        
        Uses bar data for price, volume, and calculates volatility/momentum.
        """
        try:
            base = symbol.split("/")[0] if "/" in symbol else symbol
            quote = symbol.split("/")[1] if "/" in symbol else "USD"
            
            bars = self._get_recent_bars(symbol, timeframe="1Hour", limit=24)
            
            if not bars or len(bars) < 2:
                return None
            
            price = bars[-1]["close"]
            if price <= 0:
                return None
            
            volume_24h = sum(b.get("volume", 0) * b.get("close", 0) for b in bars)
            volatility = self._calculate_volatility(bars)
            momentum_24h = self._calculate_momentum(bars, periods=min(24, len(bars) - 1))
            momentum_1h = self._calculate_momentum(bars, periods=1)
            
            high = bars[-1]["high"]
            low = bars[-1]["low"]
            spread_pct = ((high - low) / price) * 100 if price > 0 else 0.5
            
            return CryptoAsset(
                symbol=symbol,
                base_currency=base,
                quote_currency=quote,
                tradeable=True,
                price=price,
                volume_24h=volume_24h,
                volatility=volatility,
                spread_pct=min(spread_pct, 5.0),
                momentum_1h=momentum_1h,
                momentum_24h=momentum_24h
            )
            
        except Exception:
            return None
    
    def _get_recent_bars(self, symbol: str, timeframe: str = "1Hour", limit: int = 24) -> List[Dict]:
        """Fetch recent OHLCV bars for a crypto symbol"""
        try:
            if not self._alpaca._crypto_data_client:
                return []
            
            from alpaca.data.requests import CryptoBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
            
            tf_map = {
                "1Min": TimeFrame.Minute,
                "5Min": TimeFrame(5, TimeFrameUnit.Minute),
                "15Min": TimeFrame(15, TimeFrameUnit.Minute),
                "1Hour": TimeFrame.Hour,
                "1Day": TimeFrame.Day
            }
            
            tf = tf_map.get(timeframe, TimeFrame.Hour)
            
            request = CryptoBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=tf,
                limit=limit
            )
            
            bars_response = self._alpaca._crypto_data_client.get_crypto_bars(request)
            
            try:
                symbol_bars = bars_response.data.get(symbol, [])
                if not symbol_bars:
                    return []
                return [
                    {
                        "open": float(b.open),
                        "high": float(b.high),
                        "low": float(b.low),
                        "close": float(b.close),
                        "volume": float(b.volume),
                        "timestamp": b.timestamp.isoformat()
                    }
                    for b in symbol_bars
                ]
            except (KeyError, TypeError, AttributeError) as e:
                self._logger.warn(f"Crypto bars parse error for {symbol}: {e}")
                return []
            
        except Exception as e:
            self._logger.warn(f"Crypto bars fetch error for {symbol}: {e}")
            return []
    
    def _calculate_volatility(self, bars: List[Dict]) -> float:
        """Calculate ATR-based volatility as percentage of price"""
        if len(bars) < 2:
            return 0.0
        
        try:
            true_ranges = []
            for i in range(1, len(bars)):
                high = bars[i]["high"]
                low = bars[i]["low"]
                prev_close = bars[i-1]["close"]
                
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                true_ranges.append(tr)
            
            if not true_ranges:
                return 0.0
            
            atr = statistics.mean(true_ranges)
            current_price = bars[-1]["close"]
            
            volatility_pct = (atr / current_price) * 100 if current_price > 0 else 0
            return round(volatility_pct, 2)
            
        except Exception:
            return 0.0
    
    def _calculate_momentum(self, bars: List[Dict], periods: int = 24) -> float:
        """Calculate price momentum over N periods"""
        if len(bars) < periods + 1:
            return 0.0
        
        try:
            current = bars[-1]["close"]
            past = bars[-(periods + 1)]["close"]
            
            if past <= 0:
                return 0.0
            
            return round(((current - past) / past) * 100, 2)
            
        except Exception:
            return 0.0
    
    def _passes_filters(self, asset: CryptoAsset) -> bool:
        """Check if asset passes all screening filters"""
        if asset.price < self._config.min_price_usd:
            return False
        
        if asset.spread_pct > self._config.max_spread_pct:
            return False
        
        if asset.volume_24h < self._config.min_volume_24h_usd:
            return False
        
        return True
    
    def _calculate_rank_score(self, asset: CryptoAsset) -> float:
        """
        Calculate composite ranking score for a crypto asset.
        
        Combines volume, volatility, momentum, and spread into a single score.
        Higher score = better trading candidate.
        """
        score = 0.0
        
        if self._config.prefer_high_volume and asset.volume_24h > 0:
            volume_score = min(asset.volume_24h / 100_000_000, 10)
            score += volume_score * 0.3
        
        if self._config.prefer_high_volatility and asset.volatility > 0:
            vol_score = min(asset.volatility / 5, 10)
            score += vol_score * 0.25
        
        momentum_score = min(abs(asset.momentum_24h) / 10, 10)
        score += momentum_score * 0.25
        
        if asset.spread_pct > 0:
            spread_score = max(10 - (asset.spread_pct * 20), 0)
            score += spread_score * 0.2
        
        if asset.base_currency in POPULAR_COINS:
            score += 2.0
        
        return round(score, 2)
    
    def get_top_coins(self, n: int = 5) -> List[str]:
        """
        Get top N coin symbols for trading, optionally using ML re-ranking.
        
        If ml_rerank_enabled is True:
        1. Get top ml_rerank_candidates by volume/volatility/momentum
        2. Score each with ML Signal Service for profitability prediction
        3. Re-rank by combined score and select top ml_rerank_select
        
        Returns list of symbols like ["BTC/USD", "ETH/USD", "SOL/USD", ...]
        """
        screened = self.get_screened_coins()
        
        if self._config.ml_rerank_enabled and len(screened) > 0:
            return self._ml_rerank_and_select(screened)
        
        return [a.symbol for a in screened[:n]]
    
    def _ml_rerank_and_select(self, candidates: List[CryptoAsset]) -> List[str]:
        """
        Re-rank candidates using ML profitability scoring and select the best.
        
        Two-stage selection:
        1. Take top N candidates (from volume/volatility ranking)
        2. Score each for profit probability with ML Signal Service
        3. Combine universe score (40%) with ML probability (60%)
        4. Return top M coins by combined score
        
        Args:
            candidates: Pre-screened CryptoAsset list, already sorted by rank_score
            
        Returns:
            List of symbol strings for the best trading candidates
        """
        try:
            from ..ml.signal_service import MLSignalService
            
            ml_service = MLSignalService(logger=self._logger)
            
            n_candidates = min(self._config.ml_rerank_candidates, len(candidates))
            n_select = min(self._config.ml_rerank_select, n_candidates)
            
            top_candidates = candidates[:n_candidates]
            
            self._logger.log("ml_rerank_start", {
                "candidates": [c.symbol for c in top_candidates],
                "n_candidates": n_candidates,
                "n_select": n_select
            })
            
            scored_candidates = []
            
            for asset in top_candidates:
                trade_context = {
                    "symbol": asset.symbol,
                    "side": 0,  # 0=buy, 1=short (numeric encoding for ML)
                    "price": asset.price,
                    "signal_strength": min(asset.rank_score / 10.0, 1.0),
                    "volatility": asset.volatility,
                    "volume_ratio": min(asset.volume_24h / 10_000_000, 5.0),
                    "momentum_1h": asset.momentum_1h,
                    "momentum_24h": asset.momentum_24h,
                    "hour": datetime.utcnow().hour,
                    "day_of_week": datetime.utcnow().weekday()
                }
                
                ml_result = ml_service.score_entry(trade_context)
                ml_probability = ml_result.get("probability", 0.5)
                
                norm_universe_score = min(asset.rank_score / 15.0, 1.0)
                
                combined_score = (norm_universe_score * 0.4) + (ml_probability * 0.6)
                
                scored_candidates.append({
                    "asset": asset,
                    "ml_probability": ml_probability,
                    "ml_recommendation": ml_result.get("recommendation", "unknown"),
                    "universe_score": asset.rank_score,
                    "combined_score": combined_score
                })
            
            scored_candidates.sort(key=lambda x: x["combined_score"], reverse=True)
            
            selected = scored_candidates[:n_select]
            
            self._logger.log("ml_rerank_complete", {
                "selected": [s["asset"].symbol for s in selected],
                "scores": [
                    {
                        "symbol": s["asset"].symbol,
                        "ml_prob": round(s["ml_probability"], 3),
                        "universe": round(s["universe_score"], 2),
                        "combined": round(s["combined_score"], 3),
                        "recommendation": s["ml_recommendation"]
                    }
                    for s in selected
                ],
                "rejected": [
                    {
                        "symbol": s["asset"].symbol,
                        "combined": round(s["combined_score"], 3)
                    }
                    for s in scored_candidates[n_select:]
                ]
            })
            
            return [s["asset"].symbol for s in selected]
            
        except Exception as e:
            self._logger.error(f"ML re-ranking failed, using default ranking: {e}")
            return [a.symbol for a in candidates[:self._config.ml_rerank_select]]
    
    def get_coin_metrics(self, symbol: str) -> Optional[CryptoAsset]:
        """Get current metrics for a specific coin"""
        if symbol in self._cache:
            return self._cache[symbol]
        
        return self._screen_coin(symbol)


_crypto_universe_service: Optional[CryptoUniverseService] = None


def get_crypto_universe() -> CryptoUniverseService:
    """Get singleton instance of CryptoUniverseService"""
    global _crypto_universe_service
    if _crypto_universe_service is None:
        _crypto_universe_service = CryptoUniverseService()
    return _crypto_universe_service
