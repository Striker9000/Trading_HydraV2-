"""
Cross-Bot Correlation Monitor - Aggregate exposure across all bots.

Tracks total directional exposure across all bots to prevent
correlated drawdowns when multiple bots go the same direction.

Key features:
- Aggregate delta exposure across all bots
- Sector concentration limits
- Cross-bot loss correlation detection
- Automatic hedging triggers
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Set
from collections import defaultdict

from ..core.logging import get_logger
from ..core.state import get_state, set_state


@dataclass
class BotExposure:
    """Exposure snapshot for a single bot."""
    bot_id: str
    direction: str  # long, short, neutral
    delta_dollars: float  # Directional exposure in dollars
    positions: List[str]  # List of symbols
    sectors: Dict[str, float]  # Sector -> exposure
    timestamp: str


@dataclass
class AggregateExposure:
    """Aggregate exposure across all bots."""
    total_long_dollars: float
    total_short_dollars: float
    net_delta_dollars: float
    net_delta_pct: float  # As % of equity
    sector_exposure: Dict[str, float]
    bot_exposures: Dict[str, BotExposure]
    status: str  # within_limits, near_limit, breached
    warnings: List[str]
    timestamp: str


class CrossBotMonitor:
    """
    Monitor aggregate exposure across all bots.
    
    Philosophy:
    - Individual bot risk limits are not enough
    - If all bots go long together, a crash hurts everything
    - Aggregate exposure must be capped
    
    Limits:
    - Max net delta: 40% of equity
    - Max sector concentration: 30% of equity
    - Cross-bot loss trigger: 3+ bots losing in same hour
    """
    
    # Exposure limits (as % of equity)
    MAX_NET_DELTA_PCT = 40.0
    NEAR_LIMIT_PCT = 30.0
    MAX_SECTOR_PCT = 30.0
    
    # Cross-bot loss detection
    LOSS_WINDOW_MINUTES = 60
    LOSS_TRIGGER_COUNT = 3
    
    # Sector mappings (simplified)
    SECTOR_MAP = {
        "AAPL": "tech", "MSFT": "tech", "GOOGL": "tech", "META": "tech",
        "AMZN": "consumer", "TSLA": "consumer", "NVDA": "tech", "AMD": "tech",
        "JPM": "finance", "BAC": "finance", "GS": "finance", "MS": "finance",
        "XOM": "energy", "CVX": "energy", "OXY": "energy",
        "JNJ": "healthcare", "UNH": "healthcare", "PFE": "healthcare",
        "BTC": "crypto", "ETH": "crypto", "SOL": "crypto", "DOT": "crypto",
        "SPY": "index", "QQQ": "index", "IWM": "index", "DIA": "index",
    }
    
    def __init__(self):
        self._logger = get_logger()
        self._bot_exposures: Dict[str, BotExposure] = {}
        self._recent_losses: List[Dict[str, Any]] = []
        self._equity: float = 100000  # Default, updated by orchestrator
        
        self._load_state()
    
    def _load_state(self) -> None:
        """Load persisted state."""
        try:
            saved = get_state("cross_bot.exposures", {})
            self._equity = get_state("cross_bot.equity", 100000)
        except Exception as e:
            self._logger.error(f"Failed to load cross-bot state: {e}")
    
    def set_equity(self, equity: float) -> None:
        """Update current equity for percentage calculations."""
        self._equity = equity
        set_state("cross_bot.equity", equity)
    
    def _get_sector(self, symbol: str) -> str:
        """Get sector for a symbol."""
        clean = symbol.replace("/USD", "").replace("USD", "").upper()
        return self.SECTOR_MAP.get(clean, "other")
    
    def update_bot_exposure(
        self,
        bot_id: str,
        positions: List[Dict[str, Any]]
    ) -> BotExposure:
        """
        Update exposure for a bot.
        
        Args:
            bot_id: Bot identifier
            positions: List of position dicts with symbol, qty, market_value, side
            
        Returns:
            BotExposure snapshot
        """
        delta_dollars = 0.0
        symbols = []
        sectors: Dict[str, float] = defaultdict(float)
        
        for pos in positions:
            symbol = pos.get("symbol", "")
            market_value = float(pos.get("market_value", 0))
            side = pos.get("side", "long")
            
            # Calculate delta
            if side == "long":
                delta_dollars += market_value
            else:
                delta_dollars -= market_value
            
            symbols.append(symbol)
            sector = self._get_sector(symbol)
            sectors[sector] += abs(market_value)
        
        # Determine direction
        if delta_dollars > 1000:
            direction = "long"
        elif delta_dollars < -1000:
            direction = "short"
        else:
            direction = "neutral"
        
        exposure = BotExposure(
            bot_id=bot_id,
            direction=direction,
            delta_dollars=delta_dollars,
            positions=symbols,
            sectors=dict(sectors),
            timestamp=datetime.utcnow().isoformat()
        )
        
        self._bot_exposures[bot_id] = exposure
        
        return exposure
    
    def get_aggregate_exposure(self) -> AggregateExposure:
        """Calculate aggregate exposure across all bots."""
        total_long = 0.0
        total_short = 0.0
        sector_totals: Dict[str, float] = defaultdict(float)
        warnings = []
        
        for bot_id, exposure in self._bot_exposures.items():
            if exposure.delta_dollars > 0:
                total_long += exposure.delta_dollars
            else:
                total_short += abs(exposure.delta_dollars)
            
            for sector, amount in exposure.sectors.items():
                sector_totals[sector] += amount
        
        net_delta = total_long - total_short
        net_delta_pct = (net_delta / self._equity * 100) if self._equity > 0 else 0
        
        # Check limits
        if abs(net_delta_pct) >= self.MAX_NET_DELTA_PCT:
            status = "breached"
            warnings.append(f"Net delta {net_delta_pct:.1f}% exceeds limit {self.MAX_NET_DELTA_PCT}%")
        elif abs(net_delta_pct) >= self.NEAR_LIMIT_PCT:
            status = "near_limit"
            warnings.append(f"Net delta {net_delta_pct:.1f}% approaching limit")
        else:
            status = "within_limits"
        
        # Check sector concentration
        for sector, amount in sector_totals.items():
            sector_pct = (amount / self._equity * 100) if self._equity > 0 else 0
            if sector_pct >= self.MAX_SECTOR_PCT:
                warnings.append(f"Sector {sector} at {sector_pct:.1f}% exceeds limit")
                if status == "within_limits":
                    status = "near_limit"
        
        result = AggregateExposure(
            total_long_dollars=round(total_long, 2),
            total_short_dollars=round(total_short, 2),
            net_delta_dollars=round(net_delta, 2),
            net_delta_pct=round(net_delta_pct, 2),
            sector_exposure=dict(sector_totals),
            bot_exposures=self._bot_exposures.copy(),
            status=status,
            warnings=warnings,
            timestamp=datetime.utcnow().isoformat()
        )
        
        # Log
        self._logger.log("cross_bot_exposure", {
            "total_long": result.total_long_dollars,
            "total_short": result.total_short_dollars,
            "net_delta": result.net_delta_dollars,
            "net_delta_pct": result.net_delta_pct,
            "status": result.status,
            "warnings": result.warnings,
            "bots_tracked": len(self._bot_exposures)
        })
        
        return result
    
    def record_bot_loss(
        self,
        bot_id: str,
        symbol: str,
        loss_usd: float
    ) -> bool:
        """
        Record a loss and check for cross-bot correlation.
        
        Args:
            bot_id: Bot that took the loss
            symbol: Symbol that lost
            loss_usd: Dollar amount of loss
            
        Returns:
            True if cross-bot loss threshold triggered
        """
        now = datetime.utcnow()
        
        self._recent_losses.append({
            "timestamp": now,
            "bot_id": bot_id,
            "symbol": symbol,
            "loss_usd": loss_usd
        })
        
        # Clean old losses
        cutoff = now - timedelta(minutes=self.LOSS_WINDOW_MINUTES)
        self._recent_losses = [
            loss for loss in self._recent_losses
            if loss["timestamp"] >= cutoff
        ]
        
        # Check for cross-bot losses
        unique_bots = set(loss["bot_id"] for loss in self._recent_losses)
        
        if len(unique_bots) >= self.LOSS_TRIGGER_COUNT:
            total_loss = sum(loss["loss_usd"] for loss in self._recent_losses)
            
            self._logger.log("cross_bot_loss_correlation", {
                "bots_affected": list(unique_bots),
                "loss_count": len(self._recent_losses),
                "total_loss_usd": total_loss,
                "window_minutes": self.LOSS_WINDOW_MINUTES,
                "action": "pause_all_entries_2_hours"
            })
            
            return True
        
        return False
    
    def should_allow_entry(self, bot_id: str, direction: str = "long") -> bool:
        """
        Check if a new entry should be allowed based on aggregate exposure.
        
        Args:
            bot_id: Bot requesting entry
            direction: "long" or "short"
            
        Returns:
            True if entry is allowed
        """
        exposure = self.get_aggregate_exposure()
        
        # Block if breached
        if exposure.status == "breached":
            self._logger.log("cross_bot_entry_blocked", {
                "bot_id": bot_id,
                "direction": direction,
                "reason": "aggregate_exposure_breached",
                "net_delta_pct": exposure.net_delta_pct
            })
            return False
        
        # Block if adding to already-heavy direction
        if exposure.status == "near_limit":
            if direction == "long" and exposure.net_delta_pct > 0:
                self._logger.log("cross_bot_entry_blocked", {
                    "bot_id": bot_id,
                    "direction": direction,
                    "reason": "would_increase_existing_long_bias"
                })
                return False
            elif direction == "short" and exposure.net_delta_pct < 0:
                self._logger.log("cross_bot_entry_blocked", {
                    "bot_id": bot_id,
                    "direction": direction,
                    "reason": "would_increase_existing_short_bias"
                })
                return False
        
        return True
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary for dashboard."""
        exposure = self.get_aggregate_exposure()
        
        return {
            "net_delta_dollars": exposure.net_delta_dollars,
            "net_delta_pct": exposure.net_delta_pct,
            "status": exposure.status,
            "warnings": exposure.warnings,
            "bots": {
                bot_id: {
                    "direction": exp.direction,
                    "delta_dollars": exp.delta_dollars
                }
                for bot_id, exp in exposure.bot_exposures.items()
            },
            "limits": {
                "max_net_delta_pct": self.MAX_NET_DELTA_PCT,
                "max_sector_pct": self.MAX_SECTOR_PCT
            }
        }


# Singleton
_cross_bot_monitor: Optional[CrossBotMonitor] = None


def get_cross_bot_monitor() -> CrossBotMonitor:
    """Get or create CrossBotMonitor singleton."""
    global _cross_bot_monitor
    if _cross_bot_monitor is None:
        _cross_bot_monitor = CrossBotMonitor()
    return _cross_bot_monitor
