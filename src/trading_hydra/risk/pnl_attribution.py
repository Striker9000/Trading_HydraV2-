"""P&L Attribution - Track how much P&L comes from each Greek.

Decomposes option P&L into components:
- Delta P&L: From underlying price movement
- Gamma P&L: From delta acceleration (second-order effect)
- Theta P&L: From time decay
- Vega P&L: From IV changes
- Residual: Unexplained (bid-ask, higher-order Greeks, rho, etc.)

This is essential for understanding whether profits come from:
- Correct directional bets (delta)
- Volatility harvesting (gamma scalping)
- Premium capture (theta)
- IV expansion/contraction (vega)

Implementation follows Taylor series expansion of option price.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from collections import defaultdict
import json

from ..core.logging import get_logger
from ..core.state import get_state, set_state
from ..core.config import load_bots_config


@dataclass
class EntrySnapshot:
    """Snapshot of position state at entry."""
    symbol: str
    underlying_symbol: str
    entry_time: datetime
    entry_price: float  # Option price
    underlying_price: float
    contracts: int
    side: str  # "long" or "short"
    
    # Greeks at entry
    delta: float
    gamma: float
    theta: float  # Per day (negative for long options)
    vega: float
    iv: float  # Implied volatility %
    
    # Strategy context
    strategy: str = ""
    bot_id: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "underlying_symbol": self.underlying_symbol,
            "entry_time": self.entry_time.isoformat(),
            "entry_price": self.entry_price,
            "underlying_price": self.underlying_price,
            "contracts": self.contracts,
            "side": self.side,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
            "iv": self.iv,
            "strategy": self.strategy,
            "bot_id": self.bot_id
        }


@dataclass
class PnLBreakdown:
    """P&L attribution breakdown for a single trade."""
    symbol: str
    total_pnl: float  # Total realized P&L
    
    # Component P&L
    delta_pnl: float  # From underlying movement
    gamma_pnl: float  # From delta acceleration
    theta_pnl: float  # From time decay
    vega_pnl: float   # From IV changes
    residual_pnl: float  # Unexplained (bid-ask, rho, etc.)
    
    # Component percentages
    delta_pct: float = 0.0
    gamma_pct: float = 0.0
    theta_pct: float = 0.0
    vega_pct: float = 0.0
    residual_pct: float = 0.0
    
    # Context
    hold_days: float = 0.0
    underlying_change_pct: float = 0.0
    iv_change: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "total_pnl": round(self.total_pnl, 2),
            "delta_pnl": round(self.delta_pnl, 2),
            "gamma_pnl": round(self.gamma_pnl, 2),
            "theta_pnl": round(self.theta_pnl, 2),
            "vega_pnl": round(self.vega_pnl, 2),
            "residual_pnl": round(self.residual_pnl, 2),
            "delta_pct": round(self.delta_pct, 1),
            "gamma_pct": round(self.gamma_pct, 1),
            "theta_pct": round(self.theta_pct, 1),
            "vega_pct": round(self.vega_pct, 1),
            "residual_pct": round(self.residual_pct, 1),
            "hold_days": round(self.hold_days, 2),
            "underlying_change_pct": round(self.underlying_change_pct, 2),
            "iv_change": round(self.iv_change, 2)
        }


@dataclass
class AttributionSummary:
    """Aggregate P&L attribution summary."""
    total_trades: int = 0
    total_pnl: float = 0.0
    
    # Aggregate component P&L
    total_delta_pnl: float = 0.0
    total_gamma_pnl: float = 0.0
    total_theta_pnl: float = 0.0
    total_vega_pnl: float = 0.0
    total_residual_pnl: float = 0.0
    
    # Component percentages of total
    delta_pct_of_total: float = 0.0
    gamma_pct_of_total: float = 0.0
    theta_pct_of_total: float = 0.0
    vega_pct_of_total: float = 0.0
    residual_pct_of_total: float = 0.0
    
    # Strategy breakdown
    by_strategy: Dict[str, Dict[str, float]] = field(default_factory=dict)
    by_bot: Dict[str, Dict[str, float]] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_trades": self.total_trades,
            "total_pnl": round(self.total_pnl, 2),
            "components": {
                "delta": {"pnl": round(self.total_delta_pnl, 2), "pct": round(self.delta_pct_of_total, 1)},
                "gamma": {"pnl": round(self.total_gamma_pnl, 2), "pct": round(self.gamma_pct_of_total, 1)},
                "theta": {"pnl": round(self.total_theta_pnl, 2), "pct": round(self.theta_pct_of_total, 1)},
                "vega": {"pnl": round(self.total_vega_pnl, 2), "pct": round(self.vega_pct_of_total, 1)},
                "residual": {"pnl": round(self.total_residual_pnl, 2), "pct": round(self.residual_pct_of_total, 1)},
            },
            "by_strategy": self.by_strategy,
            "by_bot": self.by_bot
        }


class PnLAttributionService:
    """
    Track and attribute option P&L to individual Greeks.
    
    Philosophy:
    - Capture Greeks at entry to enable attribution at exit
    - Use Taylor series approximation: dC ≈ Δ·dS + ½Γ·dS² + Θ·dt + V·dσ
    - Residual captures bid-ask, model error, higher-order effects
    
    Usage:
        # At entry
        attribution.record_entry(symbol, entry_data)
        
        # At exit
        breakdown = attribution.record_exit(symbol, exit_data)
        
        # Get summary
        summary = attribution.get_summary()
    """
    
    def __init__(self):
        self._logger = get_logger()
        self._positions: Dict[str, EntrySnapshot] = {}
        self._history: List[PnLBreakdown] = []
        self._enabled = True
        
        self._load_config()
        self._restore_positions()
        
        self._logger.log("pnl_attribution_init", {
            "enabled": self._enabled,
            "active_positions": len(self._positions)
        })
    
    def _load_config(self):
        """Load configuration from bots.yaml."""
        try:
            config = load_bots_config()
            attr_config = config.get("pnl_attribution", {})
            self._enabled = attr_config.get("enabled", True)
        except Exception as e:
            self._logger.error(f"P&L attribution config error: {e}")
            self._enabled = True
    
    def _restore_positions(self):
        """Restore active position snapshots from state."""
        try:
            saved = get_state("pnl_attribution.positions", {})
            if saved:
                for symbol, data in saved.items():
                    self._positions[symbol] = EntrySnapshot(
                        symbol=data["symbol"],
                        underlying_symbol=data["underlying_symbol"],
                        entry_time=datetime.fromisoformat(data["entry_time"]),
                        entry_price=data["entry_price"],
                        underlying_price=data["underlying_price"],
                        contracts=data["contracts"],
                        side=data["side"],
                        delta=data["delta"],
                        gamma=data["gamma"],
                        theta=data["theta"],
                        vega=data["vega"],
                        iv=data["iv"],
                        strategy=data.get("strategy", ""),
                        bot_id=data.get("bot_id", "")
                    )
        except Exception as e:
            self._logger.error(f"Failed to restore P&L attribution positions: {e}")
    
    def _persist_positions(self):
        """Persist active positions to state."""
        try:
            data = {symbol: snap.to_dict() for symbol, snap in self._positions.items()}
            set_state("pnl_attribution.positions", data)
        except Exception as e:
            self._logger.error(f"Failed to persist P&L attribution positions: {e}")
    
    def record_entry(
        self,
        symbol: str,
        underlying_symbol: str,
        entry_price: float,
        underlying_price: float,
        contracts: int,
        side: str,
        delta: float,
        gamma: float,
        theta: float,
        vega: float,
        iv: float,
        strategy: str = "",
        bot_id: str = ""
    ) -> bool:
        """
        Record entry Greeks for P&L attribution.
        
        Args:
            symbol: Option symbol
            underlying_symbol: Underlying ticker (e.g., "SPY")
            entry_price: Option price at entry
            underlying_price: Underlying price at entry
            contracts: Number of contracts
            side: "long" or "short"
            delta: Position delta (per contract)
            gamma: Position gamma
            theta: Position theta (daily decay)
            vega: Position vega
            iv: Entry implied volatility (as decimal, e.g., 0.25 for 25%)
            strategy: Strategy name
            bot_id: Bot identifier
            
        Returns:
            True if recorded successfully
        """
        if not self._enabled:
            return False
        
        snapshot = EntrySnapshot(
            symbol=symbol,
            underlying_symbol=underlying_symbol,
            entry_time=datetime.utcnow(),
            entry_price=entry_price,
            underlying_price=underlying_price,
            contracts=contracts,
            side=side,
            delta=delta,
            gamma=gamma,
            theta=theta,
            vega=vega,
            iv=iv,
            strategy=strategy,
            bot_id=bot_id
        )
        
        self._positions[symbol] = snapshot
        self._persist_positions()
        
        self._logger.log("pnl_attribution_entry", {
            "symbol": symbol,
            "underlying": underlying_symbol,
            "entry_price": entry_price,
            "underlying_price": underlying_price,
            "contracts": contracts,
            "side": side,
            "delta": round(delta, 4),
            "gamma": round(gamma, 6),
            "theta": round(theta, 4),
            "vega": round(vega, 4),
            "iv": round(iv, 4),
            "strategy": strategy,
            "bot_id": bot_id
        })
        
        return True
    
    def record_exit(
        self,
        symbol: str,
        exit_price: float,
        underlying_price: float,
        exit_iv: Optional[float] = None,
        partial_contracts: Optional[int] = None
    ) -> Optional[PnLBreakdown]:
        """
        Record exit and calculate P&L attribution.
        
        Uses Taylor series approximation:
        dC ≈ Δ·dS + ½Γ·(dS)² + Θ·dt + V·dσ
        
        Args:
            symbol: Option symbol
            exit_price: Option price at exit
            underlying_price: Underlying price at exit
            exit_iv: Exit implied volatility (optional, for vega P&L)
            partial_contracts: For partial exits, how many contracts
            
        Returns:
            PnLBreakdown or None if position not found
        """
        if not self._enabled:
            return None
        
        if symbol not in self._positions:
            self._logger.log("pnl_attribution_exit_no_entry", {"symbol": symbol})
            return None
        
        entry = self._positions[symbol]
        now = datetime.utcnow()
        
        # Calculate hold time
        hold_time = now - entry.entry_time
        hold_days = hold_time.total_seconds() / 86400.0
        
        # Calculate underlying price change
        underlying_change = underlying_price - entry.underlying_price
        underlying_change_pct = (underlying_change / entry.underlying_price) * 100 if entry.underlying_price else 0
        
        # Calculate IV change
        iv_change = (exit_iv - entry.iv) if exit_iv is not None else 0.0
        
        # Contracts for this exit (partial or full)
        contracts = partial_contracts or entry.contracts
        multiplier = 100  # Standard options multiplier
        
        # Direction multiplier (short positions invert P&L)
        direction = 1 if entry.side == "long" else -1
        
        # Calculate component P&L using Taylor expansion
        # Delta P&L: Δ * dS * contracts * 100
        delta_pnl = entry.delta * underlying_change * contracts * multiplier * direction
        
        # Gamma P&L: 0.5 * Γ * (dS)² * contracts * 100
        # This is the second-order effect from delta changing
        gamma_pnl = 0.5 * entry.gamma * (underlying_change ** 2) * contracts * multiplier * direction
        
        # Theta P&L: θ * days * contracts * 100
        # Theta is already per-day per-contract
        theta_pnl = entry.theta * hold_days * contracts * multiplier * direction
        
        # Vega P&L: ν * dσ * contracts * 100
        # Vega is sensitivity per 1% IV change
        vega_pnl = entry.vega * (iv_change * 100) * contracts * multiplier * direction if iv_change else 0.0
        
        # Total actual P&L
        price_change = exit_price - entry.entry_price
        total_pnl = price_change * contracts * multiplier * direction
        
        # Residual = Total - (Delta + Gamma + Theta + Vega)
        explained_pnl = delta_pnl + gamma_pnl + theta_pnl + vega_pnl
        residual_pnl = total_pnl - explained_pnl
        
        # Calculate percentages (avoid division by zero)
        def safe_pct(component: float, total: float) -> float:
            if abs(total) < 0.01:
                return 0.0
            return (component / abs(total)) * 100
        
        breakdown = PnLBreakdown(
            symbol=symbol,
            total_pnl=total_pnl,
            delta_pnl=delta_pnl,
            gamma_pnl=gamma_pnl,
            theta_pnl=theta_pnl,
            vega_pnl=vega_pnl,
            residual_pnl=residual_pnl,
            delta_pct=safe_pct(delta_pnl, total_pnl),
            gamma_pct=safe_pct(gamma_pnl, total_pnl),
            theta_pct=safe_pct(theta_pnl, total_pnl),
            vega_pct=safe_pct(vega_pnl, total_pnl),
            residual_pct=safe_pct(residual_pnl, total_pnl),
            hold_days=hold_days,
            underlying_change_pct=underlying_change_pct,
            iv_change=iv_change
        )
        
        # Store in history
        self._history.append(breakdown)
        
        # Remove position if full exit
        if partial_contracts is None or partial_contracts >= entry.contracts:
            del self._positions[symbol]
            self._persist_positions()
        else:
            # Update remaining contracts for partial exit
            entry.contracts -= partial_contracts
            self._persist_positions()
        
        # Log attribution
        self._logger.log("pnl_attribution_exit", {
            "symbol": symbol,
            "strategy": entry.strategy,
            "bot_id": entry.bot_id,
            "total_pnl": round(total_pnl, 2),
            "delta_pnl": round(delta_pnl, 2),
            "gamma_pnl": round(gamma_pnl, 2),
            "theta_pnl": round(theta_pnl, 2),
            "vega_pnl": round(vega_pnl, 2),
            "residual_pnl": round(residual_pnl, 2),
            "hold_days": round(hold_days, 2),
            "underlying_change": round(underlying_change, 2),
            "underlying_change_pct": round(underlying_change_pct, 2),
            "iv_change": round(iv_change * 100, 2) if iv_change else 0,
            "explained_pct": round(100 - abs(breakdown.residual_pct), 1)
        })
        
        # Write to attribution log file for analysis
        self._write_attribution_record(entry, breakdown)
        
        return breakdown
    
    def _write_attribution_record(self, entry: EntrySnapshot, breakdown: PnLBreakdown):
        """Write attribution record to JSONL file."""
        try:
            record = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "symbol": entry.symbol,
                "underlying": entry.underlying_symbol,
                "strategy": entry.strategy,
                "bot_id": entry.bot_id,
                "side": entry.side,
                "contracts": entry.contracts,
                "entry": {
                    "time": entry.entry_time.isoformat(),
                    "price": entry.entry_price,
                    "underlying_price": entry.underlying_price,
                    "delta": entry.delta,
                    "gamma": entry.gamma,
                    "theta": entry.theta,
                    "vega": entry.vega,
                    "iv": entry.iv
                },
                "attribution": breakdown.to_dict()
            }
            
            with open("pnl_attribution.jsonl", "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            self._logger.error(f"Failed to write attribution record: {e}")
    
    def get_summary(self, days: int = 30) -> AttributionSummary:
        """
        Get aggregate P&L attribution summary.
        
        Args:
            days: Number of days to include (from history)
            
        Returns:
            AttributionSummary with totals and breakdowns
        """
        summary = AttributionSummary()
        by_strategy: Dict[str, Dict[str, float]] = defaultdict(lambda: {
            "pnl": 0.0, "delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0
        })
        by_bot: Dict[str, Dict[str, float]] = defaultdict(lambda: {
            "pnl": 0.0, "delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0
        })
        
        # Load historical data from JSONL
        try:
            with open("pnl_attribution.jsonl", "r") as f:
                for line in f:
                    try:
                        record = json.loads(line.strip())
                        attr = record.get("attribution", {})
                        strategy = record.get("strategy", "unknown")
                        bot_id = record.get("bot_id", "unknown")
                        
                        summary.total_trades += 1
                        summary.total_pnl += attr.get("total_pnl", 0)
                        summary.total_delta_pnl += attr.get("delta_pnl", 0)
                        summary.total_gamma_pnl += attr.get("gamma_pnl", 0)
                        summary.total_theta_pnl += attr.get("theta_pnl", 0)
                        summary.total_vega_pnl += attr.get("vega_pnl", 0)
                        summary.total_residual_pnl += attr.get("residual_pnl", 0)
                        
                        by_strategy[strategy]["pnl"] += attr.get("total_pnl", 0)
                        by_strategy[strategy]["delta"] += attr.get("delta_pnl", 0)
                        by_strategy[strategy]["gamma"] += attr.get("gamma_pnl", 0)
                        by_strategy[strategy]["theta"] += attr.get("theta_pnl", 0)
                        by_strategy[strategy]["vega"] += attr.get("vega_pnl", 0)
                        
                        by_bot[bot_id]["pnl"] += attr.get("total_pnl", 0)
                        by_bot[bot_id]["delta"] += attr.get("delta_pnl", 0)
                        by_bot[bot_id]["gamma"] += attr.get("gamma_pnl", 0)
                        by_bot[bot_id]["theta"] += attr.get("theta_pnl", 0)
                        by_bot[bot_id]["vega"] += attr.get("vega_pnl", 0)
                        
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            pass
        
        # Calculate percentages
        if abs(summary.total_pnl) > 0.01:
            summary.delta_pct_of_total = (summary.total_delta_pnl / abs(summary.total_pnl)) * 100
            summary.gamma_pct_of_total = (summary.total_gamma_pnl / abs(summary.total_pnl)) * 100
            summary.theta_pct_of_total = (summary.total_theta_pnl / abs(summary.total_pnl)) * 100
            summary.vega_pct_of_total = (summary.total_vega_pnl / abs(summary.total_pnl)) * 100
            summary.residual_pct_of_total = (summary.total_residual_pnl / abs(summary.total_pnl)) * 100
        
        summary.by_strategy = dict(by_strategy)
        summary.by_bot = dict(by_bot)
        
        return summary
    
    def has_entry(self, symbol: str) -> bool:
        """Check if we have an entry snapshot for a symbol."""
        return symbol in self._positions
    
    def get_entry(self, symbol: str) -> Optional[EntrySnapshot]:
        """Get entry snapshot for a symbol."""
        return self._positions.get(symbol)
    
    def get_active_positions(self) -> List[str]:
        """Get list of symbols with active entry snapshots."""
        return list(self._positions.keys())


# Singleton
_attribution_service: Optional[PnLAttributionService] = None


def get_pnl_attribution() -> PnLAttributionService:
    """Get or create PnLAttributionService singleton."""
    global _attribution_service
    if _attribution_service is None:
        _attribution_service = PnLAttributionService()
    return _attribution_service
