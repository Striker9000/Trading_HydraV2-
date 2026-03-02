"""
Correlation Manager - Heuristic Cross-Asset Correlation Tracking v1.

NOTE: Uses heuristic correlation assumptions that should be validated.

Implements:
- Rolling correlation matrix computation
- Correlation-based position blocking
- Sector/asset-class exposure limits
- Concentration risk monitoring

Target: Prevent concentrated exposure in correlated assets.
Max exposure per sector/asset-class: 20% NAV
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict
import math

from ..core.logging import get_logger
from ..core.config import load_settings
from ..core.state import get_state, set_state


@dataclass
class CorrelationResult:
    """Result of correlation check for a proposed trade."""
    approved: bool
    correlation_exposure: float
    max_correlated_asset: str
    max_correlation: float
    sector_exposure: float
    sector_limit: float
    blocking_reason: str
    recommendations: List[str]


@dataclass
class PortfolioExposure:
    """Current portfolio exposure breakdown."""
    total_exposure: float
    crypto_exposure: float
    stock_exposure: float
    options_exposure: float
    by_sector: Dict[str, float]
    by_asset: Dict[str, float]
    correlation_matrix: Dict[str, Dict[str, float]]
    concentration_score: float


class CorrelationManager:
    """
    Manages correlation limits and sector exposure.
    
    Key Features:
    1. Track rolling correlations between held assets
    2. Block trades that would exceed correlation thresholds
    3. Enforce sector/asset-class exposure limits
    4. Provide correlation exposure for position sizing
    """
    
    MAX_PAIRWISE_CORRELATION = 0.7
    MAX_SECTOR_EXPOSURE_PCT = 20.0
    MAX_SINGLE_ASSET_PCT = 10.0
    CORRELATION_WINDOW_DAYS = 20
    
    SECTOR_MAP = {
        "BTC/USD": "crypto_major",
        "ETH/USD": "crypto_major",
        "BTCUSD": "crypto_major",
        "ETHUSD": "crypto_major",
        "SOL/USD": "crypto_alt",
        "SOLUSD": "crypto_alt",
        "AVAX/USD": "crypto_alt",
        "AVAXUSD": "crypto_alt",
        "LINK/USD": "crypto_alt",
        "LINKUSD": "crypto_alt",
        "DOGE/USD": "crypto_meme",
        "DOGEUSD": "crypto_meme",
        "SHIB/USD": "crypto_meme",
        "SHIBUSD": "crypto_meme",
        "LTC/USD": "crypto_legacy",
        "LTCUSD": "crypto_legacy",
        "AAVE/USD": "crypto_defi",
        "AAVEUSD": "crypto_defi",
        "UNI/USD": "crypto_defi",
        "UNIUSD": "crypto_defi",
        "AAPL": "tech_megacap",
        "MSFT": "tech_megacap",
        "GOOGL": "tech_megacap",
        "AMZN": "tech_megacap",
        "AMD": "tech_semis",
        "NVDA": "tech_semis",
        "INTC": "tech_semis",
        "TSLA": "ev_auto",
        "SPY": "index_etf",
        "QQQ": "index_etf",
    }
    
    KNOWN_CORRELATIONS = {
        ("crypto_major", "crypto_major"): 0.85,
        ("crypto_major", "crypto_alt"): 0.75,
        ("crypto_major", "crypto_defi"): 0.70,
        ("crypto_major", "crypto_meme"): 0.65,
        ("crypto_major", "crypto_legacy"): 0.80,
        ("crypto_alt", "crypto_alt"): 0.70,
        ("crypto_alt", "crypto_defi"): 0.65,
        ("crypto_alt", "crypto_meme"): 0.55,
        ("crypto_defi", "crypto_defi"): 0.75,
        ("crypto_meme", "crypto_meme"): 0.60,
        ("tech_megacap", "tech_megacap"): 0.80,
        ("tech_megacap", "tech_semis"): 0.70,
        ("tech_semis", "tech_semis"): 0.75,
        ("index_etf", "tech_megacap"): 0.85,
    }
    
    def __init__(self):
        self._logger = get_logger()
        self._settings = load_settings()
        
        corr_config = self._settings.get("correlation_management", {})
        self._enabled = corr_config.get("enabled", True)
        self._max_correlation = corr_config.get("max_pairwise_correlation", self.MAX_PAIRWISE_CORRELATION)
        self._max_sector_pct = corr_config.get("max_sector_exposure_pct", self.MAX_SECTOR_EXPOSURE_PCT)
        self._max_asset_pct = corr_config.get("max_single_asset_pct", self.MAX_SINGLE_ASSET_PCT)
        
        self._price_history: Dict[str, List[Tuple[datetime, float]]] = {}
        self._correlation_cache: Dict[str, Dict[str, float]] = {}
        self._cache_timestamp: Optional[datetime] = None
        
        self._logger.log("correlation_manager_init", {
            "enabled": self._enabled,
            "max_correlation": self._max_correlation,
            "max_sector_pct": self._max_sector_pct
        })
    
    def check_trade_correlation(
        self,
        symbol: str,
        notional: float,
        side: str,
        current_positions: List[Dict[str, Any]],
        equity: float,
        asset_class: str = "crypto"
    ) -> CorrelationResult:
        """
        Check if a proposed trade would violate correlation/exposure limits.
        
        Args:
            symbol: Symbol to trade
            notional: Proposed trade notional
            side: "buy" or "sell"/"short"
            current_positions: List of current position dicts
            equity: Total account equity
            asset_class: Asset class of proposed trade
            
        Returns:
            CorrelationResult with approval status and exposure metrics
        """
        if not self._enabled:
            return CorrelationResult(
                approved=True,
                correlation_exposure=0.0,
                max_correlated_asset="",
                max_correlation=0.0,
                sector_exposure=0.0,
                sector_limit=self._max_sector_pct,
                blocking_reason="",
                recommendations=[]
            )
        
        new_symbol_sector = self._get_sector(symbol)
        
        sector_exposure = self._calculate_sector_exposure(
            current_positions, equity, new_symbol_sector
        )
        new_sector_exposure = sector_exposure + (notional / equity * 100) if equity > 0 else 0
        
        max_corr = 0.0
        max_corr_asset = ""
        total_corr_weighted = 0.0
        
        for pos in current_positions:
            pos_symbol = pos.get("symbol", "")
            pos_value = abs(float(pos.get("market_value", 0)))
            
            if pos_value <= 0 or equity <= 0:
                continue
            
            correlation = self._get_correlation(symbol, pos_symbol)
            
            if correlation > max_corr:
                max_corr = correlation
                max_corr_asset = pos_symbol
            
            weight = pos_value / equity
            total_corr_weighted += correlation * weight
        
        blocking_reason = ""
        recommendations = []
        approved = True
        
        if new_sector_exposure > self._max_sector_pct:
            approved = False
            blocking_reason = f"Sector exposure {new_sector_exposure:.1f}% exceeds limit {self._max_sector_pct}%"
            recommendations.append(f"Reduce {new_symbol_sector} sector positions before adding {symbol}")
        
        if max_corr > self._max_correlation:
            approved = False
            if blocking_reason:
                blocking_reason += f"; High correlation {max_corr:.2f} with {max_corr_asset}"
            else:
                blocking_reason = f"High correlation {max_corr:.2f} with {max_corr_asset} exceeds limit {self._max_correlation}"
            recommendations.append(f"Consider closing {max_corr_asset} position first")
        
        single_asset_pct = notional / equity * 100 if equity > 0 else 0
        if single_asset_pct > self._max_asset_pct:
            approved = False
            if blocking_reason:
                blocking_reason += f"; Single asset exposure {single_asset_pct:.1f}% exceeds {self._max_asset_pct}%"
            else:
                blocking_reason = f"Single asset exposure {single_asset_pct:.1f}% exceeds limit {self._max_asset_pct}%"
            recommendations.append(f"Reduce position size to below {self._max_asset_pct}% of NAV")
        
        result = CorrelationResult(
            approved=approved,
            correlation_exposure=min(1.0, total_corr_weighted),
            max_correlated_asset=max_corr_asset,
            max_correlation=max_corr,
            sector_exposure=new_sector_exposure,
            sector_limit=self._max_sector_pct,
            blocking_reason=blocking_reason,
            recommendations=recommendations
        )
        
        self._logger.log("correlation_check", {
            "symbol": symbol,
            "approved": approved,
            "correlation_exposure": round(total_corr_weighted, 3),
            "max_correlation": round(max_corr, 3),
            "max_corr_asset": max_corr_asset,
            "sector": new_symbol_sector,
            "sector_exposure": round(new_sector_exposure, 2),
            "blocking_reason": blocking_reason
        })
        
        return result
    
    def get_portfolio_exposure(
        self,
        positions: List[Dict[str, Any]],
        equity: float
    ) -> PortfolioExposure:
        """
        Calculate complete portfolio exposure breakdown.
        
        Args:
            positions: List of current positions
            equity: Total account equity
            
        Returns:
            PortfolioExposure with detailed breakdown
        """
        crypto_exposure = 0.0
        stock_exposure = 0.0
        options_exposure = 0.0
        sector_exposure: Dict[str, float] = defaultdict(float)
        asset_exposure: Dict[str, float] = {}
        
        for pos in positions:
            symbol = pos.get("symbol", "")
            value = abs(float(pos.get("market_value", 0)))
            asset_class = pos.get("asset_class", "")
            
            if equity > 0:
                pct = value / equity * 100
            else:
                pct = 0
            
            asset_exposure[symbol] = pct
            
            sector = self._get_sector(symbol)
            sector_exposure[sector] += pct
            
            if "USD" in symbol or asset_class == "crypto":
                crypto_exposure += pct
            elif asset_class == "us_option":
                options_exposure += pct
            else:
                stock_exposure += pct
        
        total_exposure = crypto_exposure + stock_exposure + options_exposure
        
        correlation_matrix = self._build_correlation_matrix(list(asset_exposure.keys()))
        
        concentration_score = self._calculate_concentration(asset_exposure)
        
        return PortfolioExposure(
            total_exposure=round(total_exposure, 2),
            crypto_exposure=round(crypto_exposure, 2),
            stock_exposure=round(stock_exposure, 2),
            options_exposure=round(options_exposure, 2),
            by_sector=dict(sector_exposure),
            by_asset=asset_exposure,
            correlation_matrix=correlation_matrix,
            concentration_score=round(concentration_score, 3)
        )
    
    def update_price(self, symbol: str, price: float) -> None:
        """Update price history for correlation calculation."""
        now = datetime.utcnow()
        
        if symbol not in self._price_history:
            self._price_history[symbol] = []
        
        self._price_history[symbol].append((now, price))
        
        cutoff = now - timedelta(days=self.CORRELATION_WINDOW_DAYS)
        self._price_history[symbol] = [
            (t, p) for t, p in self._price_history[symbol] if t > cutoff
        ]
        
        self._correlation_cache = {}
        self._cache_timestamp = None
    
    def _get_sector(self, symbol: str) -> str:
        """Get sector classification for a symbol."""
        normalized = symbol.replace("/", "").upper()
        
        if normalized in self.SECTOR_MAP:
            return self.SECTOR_MAP[normalized]
        
        if symbol in self.SECTOR_MAP:
            return self.SECTOR_MAP[symbol]
        
        if "USD" in normalized:
            return "crypto_other"
        
        return "equity_other"
    
    def _calculate_sector_exposure(
        self,
        positions: List[Dict[str, Any]],
        equity: float,
        target_sector: str
    ) -> float:
        """Calculate current exposure to a specific sector."""
        if equity <= 0:
            return 0.0
        
        sector_value = 0.0
        for pos in positions:
            symbol = pos.get("symbol", "")
            value = abs(float(pos.get("market_value", 0)))
            sector = self._get_sector(symbol)
            
            if sector == target_sector:
                sector_value += value
        
        return (sector_value / equity) * 100
    
    def _get_correlation(self, symbol1: str, symbol2: str) -> float:
        """
        Get correlation between two assets.
        Uses historical calculation if available, otherwise sector-based estimate.
        """
        if symbol1 == symbol2:
            return 1.0
        
        cache_key = f"{min(symbol1, symbol2)}_{max(symbol1, symbol2)}"
        if cache_key in self._correlation_cache:
            return self._correlation_cache[cache_key]
        
        hist_corr = self._calculate_historical_correlation(symbol1, symbol2)
        if hist_corr is not None:
            self._correlation_cache[cache_key] = hist_corr
            return hist_corr
        
        sector1 = self._get_sector(symbol1)
        sector2 = self._get_sector(symbol2)
        
        key = (min(sector1, sector2), max(sector1, sector2))
        if key in self.KNOWN_CORRELATIONS:
            corr = self.KNOWN_CORRELATIONS[key]
        elif sector1 == sector2:
            corr = 0.6
        else:
            corr = 0.3
        
        self._correlation_cache[cache_key] = corr
        return corr
    
    def _calculate_historical_correlation(
        self,
        symbol1: str,
        symbol2: str
    ) -> Optional[float]:
        """Calculate correlation from historical price data."""
        hist1 = self._price_history.get(symbol1, [])
        hist2 = self._price_history.get(symbol2, [])
        
        if len(hist1) < 5 or len(hist2) < 5:
            return None
        
        returns1 = []
        returns2 = []
        
        for i in range(1, min(len(hist1), 20)):
            ret = (hist1[i][1] - hist1[i-1][1]) / hist1[i-1][1] if hist1[i-1][1] > 0 else 0
            returns1.append(ret)
        
        for i in range(1, min(len(hist2), 20)):
            ret = (hist2[i][1] - hist2[i-1][1]) / hist2[i-1][1] if hist2[i-1][1] > 0 else 0
            returns2.append(ret)
        
        min_len = min(len(returns1), len(returns2))
        if min_len < 5:
            return None
        
        returns1 = returns1[:min_len]
        returns2 = returns2[:min_len]
        
        mean1 = sum(returns1) / len(returns1)
        mean2 = sum(returns2) / len(returns2)
        
        covariance = sum((r1 - mean1) * (r2 - mean2) for r1, r2 in zip(returns1, returns2)) / len(returns1)
        
        var1 = sum((r - mean1) ** 2 for r in returns1) / len(returns1)
        var2 = sum((r - mean2) ** 2 for r in returns2) / len(returns2)
        
        std1 = math.sqrt(var1) if var1 > 0 else 1
        std2 = math.sqrt(var2) if var2 > 0 else 1
        
        correlation = covariance / (std1 * std2) if (std1 * std2) > 0 else 0
        
        return max(-1, min(1, correlation))
    
    def _build_correlation_matrix(
        self,
        symbols: List[str]
    ) -> Dict[str, Dict[str, float]]:
        """Build correlation matrix for given symbols."""
        matrix: Dict[str, Dict[str, float]] = {}
        
        for s1 in symbols:
            matrix[s1] = {}
            for s2 in symbols:
                matrix[s1][s2] = self._get_correlation(s1, s2)
        
        return matrix
    
    def _calculate_concentration(
        self,
        asset_exposure: Dict[str, float]
    ) -> float:
        """
        Calculate portfolio concentration score (Herfindahl-Hirschman Index).
        0 = perfectly diversified, 1 = single asset
        """
        if not asset_exposure:
            return 0.0
        
        total = sum(asset_exposure.values())
        if total <= 0:
            return 0.0
        
        hhi = sum((v / total) ** 2 for v in asset_exposure.values())
        
        return hhi


_correlation_manager: Optional[CorrelationManager] = None


def get_correlation_manager() -> CorrelationManager:
    """Get or create singleton correlation manager."""
    global _correlation_manager
    if _correlation_manager is None:
        _correlation_manager = CorrelationManager()
    return _correlation_manager
