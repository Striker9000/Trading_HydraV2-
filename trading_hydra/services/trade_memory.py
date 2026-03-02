"""
TradeMemoryEngine - Historical Exit Intelligence for ExitBot v2
================================================================

This module provides "memory" for ExitBot by querying historical trade data
to build exit fingerprints. It answers questions like:
- "How did trades like this perform in the past?"
- "What's the typical MFE/MAE for this symbol/strategy/regime?"
- "When do trades of this type typically stall?"

This is how ExitBot "examines the past" instead of guessing.

Key Features:
- Query historical MFE/MAE by symbol, strategy, regime
- Calculate exit fingerprints (expected stall points, typical exits)
- Provide memory-based guidance for exit decisions
- Build pattern recognition for symbol+strategy+regime combos
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict
import statistics
import time

from ..core.logging import get_logger
from ..core.state import get_db_connection

ROLLING_WINDOW_DAYS = 31


@dataclass
class ExitFingerprint:
    """
    Exit fingerprint for a specific symbol/strategy/regime combination.
    
    Represents historical patterns for how similar trades have exited.
    """
    symbol: str
    strategy: str                    # Bot ID or strategy type
    regime: Optional[str]            # Market regime at entry
    
    # Sample size
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: float
    
    # MFE/MAE statistics
    avg_mfe_pct: float              # Average max favorable excursion
    median_mfe_pct: float
    std_mfe_pct: float
    avg_mae_pct: float              # Average max adverse excursion
    median_mae_pct: float
    std_mae_pct: float
    
    # P&L statistics
    avg_pnl_pct: float
    median_pnl_pct: float
    avg_pnl_usd: float
    
    # Timing patterns
    avg_hold_duration_sec: float
    median_hold_duration_sec: float
    
    # Stall detection (MFE that wasn't captured)
    avg_stall_point_pct: float      # Where trades typically stall (MFE - realized)
    
    # Exit reason distribution
    exit_reasons: Dict[str, int]    # {"take_profit": 5, "stop_loss": 3, ...}
    
    # Confidence in this fingerprint
    confidence: float               # 0.0-1.0 based on sample size


@dataclass
class HistoricalContext:
    """
    Historical context for a position, used for exit intelligence.
    """
    fingerprint: Optional[ExitFingerprint]
    expected_mfe_pct: float         # What MFE to expect
    expected_stall_pct: float       # Where it might stall
    suggested_tp1_pct: float        # Suggested first take-profit
    suggested_stop_pct: float       # Suggested stop based on MAE
    confidence: float               # How confident we are in this context
    trade_count: int                # How many similar trades we've seen
    notes: str                      # Human-readable explanation


class TradeMemoryEngine:
    """
    Historical trade memory engine for ExitBot.
    
    Queries the exit_trades table to build understanding of how
    similar trades have performed historically.
    """
    
    def __init__(self):
        self._logger = get_logger()
        self._cache: Dict[str, ExitFingerprint] = {}
        self._cache_ttl_sec = 3600  # Cache fingerprints for 1 hour
        self._last_cache_update: float = 0
        
        self._last_rotation_day: Optional[str] = None
        
        self._logger.log("trade_memory_engine_initialized", {
            "rolling_window_days": ROLLING_WINDOW_DAYS
        })
    
    def get_total_trade_count(self) -> int:
        """
        Get total count of completed trades within the rolling window.
        
        Returns:
            Number of trades with exit_ts set within ROLLING_WINDOW_DAYS
        """
        try:
            conn = get_db_connection()
            cutoff = self._get_rolling_cutoff_iso()
            
            cursor = conn.execute("""
                SELECT COUNT(*) FROM exit_trades
                WHERE exit_ts IS NOT NULL
                AND exit_ts >= ?
            """, (cutoff,))
            
            count = cursor.fetchone()[0]
            return count
        except Exception as e:
            self._logger.error(f"Failed to get total trade count: {e}")
            return 0
    
    def _get_rolling_cutoff_iso(self) -> str:
        """Get ISO timestamp for the rolling window cutoff (ROLLING_WINDOW_DAYS ago)."""
        cutoff_dt = datetime.utcnow() - timedelta(days=ROLLING_WINDOW_DAYS)
        return cutoff_dt.isoformat() + "Z"
    
    def rotate_old_data(self) -> Dict[str, int]:
        """
        Delete trade data older than ROLLING_WINDOW_DAYS.
        
        Rotates out old rows from exit_trades, exit_decisions, and exit_options_context.
        Should be called once per day (idempotent, safe to call more often).
        
        Returns:
            Dict with counts of deleted rows per table
        """
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if self._last_rotation_day == today:
            return {"skipped": True, "reason": "already_rotated_today"}
        
        cutoff = self._get_rolling_cutoff_iso()
        deleted = {"exit_trades": 0, "exit_decisions": 0, "exit_options_context": 0}
        
        try:
            conn = get_db_connection()
            
            old_position_keys = conn.execute("""
                SELECT position_key FROM exit_trades
                WHERE exit_ts IS NOT NULL AND exit_ts < ?
            """, (cutoff,)).fetchall()
            old_keys = [row[0] for row in old_position_keys]
            
            if old_keys:
                placeholders = ",".join(["?" for _ in old_keys])
                
                cursor = conn.execute(
                    f"DELETE FROM exit_options_context WHERE position_key IN ({placeholders})",
                    old_keys
                )
                deleted["exit_options_context"] = cursor.rowcount
                
                cursor = conn.execute(
                    f"DELETE FROM exit_decisions WHERE position_key IN ({placeholders})",
                    old_keys
                )
                deleted["exit_decisions"] = cursor.rowcount
                
                cursor = conn.execute(
                    f"DELETE FROM exit_trades WHERE position_key IN ({placeholders})",
                    old_keys
                )
                deleted["exit_trades"] = cursor.rowcount
                
                conn.commit()
            
            self._last_rotation_day = today
            self.clear_cache()
            
            self._logger.log("trade_memory_rotation_complete", {
                "cutoff": cutoff,
                "rolling_window_days": ROLLING_WINDOW_DAYS,
                "deleted": deleted,
                "old_keys_found": len(old_keys)
            })
            
        except Exception as e:
            self._logger.error(f"Failed to rotate old trade data: {e}")
        
        return deleted
    
    def get_fingerprint(
        self,
        symbol: str,
        strategy: str,
        regime: Optional[str] = None,
        min_trades: int = 5
    ) -> Optional[ExitFingerprint]:
        """
        Get exit fingerprint for a symbol/strategy/regime combination.
        
        Args:
            symbol: Trading symbol
            strategy: Bot ID or strategy type
            regime: Market regime (optional, for more specific matching)
            min_trades: Minimum trades required for valid fingerprint
            
        Returns:
            ExitFingerprint if sufficient data exists, None otherwise
        """
        cache_key = f"{symbol}:{strategy}:{regime or 'any'}"
        
        # Check cache
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        # Query database
        trades = self._query_historical_trades(symbol, strategy, regime)
        
        if len(trades) < min_trades:
            self._logger.log("trade_memory_insufficient_data", {
                "symbol": symbol,
                "strategy": strategy,
                "regime": regime,
                "trade_count": len(trades),
                "min_required": min_trades
            })
            return None
        
        # Build fingerprint
        fingerprint = self._build_fingerprint(symbol, strategy, regime, trades)
        
        # Cache it
        self._cache[cache_key] = fingerprint
        
        self._logger.log("trade_memory_fingerprint_built", {
            "symbol": symbol,
            "strategy": strategy,
            "regime": regime,
            "trade_count": fingerprint.trade_count,
            "win_rate": round(fingerprint.win_rate, 3),
            "avg_mfe_pct": round(fingerprint.avg_mfe_pct, 3)
        })
        
        return fingerprint
    
    def get_historical_context(
        self,
        symbol: str,
        strategy: str,
        regime: Optional[str] = None
    ) -> HistoricalContext:
        """
        Get historical context for exit intelligence.
        
        This is the main interface for ExitBot to get guidance
        based on historical performance.
        
        Args:
            symbol: Trading symbol
            strategy: Bot ID
            regime: Current market regime
            
        Returns:
            HistoricalContext with suggested parameters
        """
        # Try exact match first
        fingerprint = self.get_fingerprint(symbol, strategy, regime)
        
        # Fall back to regime-agnostic if no specific match
        if fingerprint is None and regime:
            fingerprint = self.get_fingerprint(symbol, strategy, None)
        
        # Fall back to strategy-only (all symbols)
        if fingerprint is None:
            fingerprint = self._get_strategy_fingerprint(strategy)
        
        if fingerprint is None:
            return HistoricalContext(
                fingerprint=None,
                expected_mfe_pct=2.0,      # Default expectations
                expected_stall_pct=1.5,
                suggested_tp1_pct=1.0,
                suggested_stop_pct=3.0,
                confidence=0.0,
                trade_count=0,
                notes="No historical data available - using defaults"
            )
        
        # Calculate suggestions based on fingerprint
        expected_mfe = fingerprint.avg_mfe_pct
        expected_stall = fingerprint.avg_stall_point_pct
        
        # Suggested TP1: Capture 50-70% of expected MFE (conservative)
        suggested_tp1 = expected_mfe * 0.6
        
        # Suggested stop: Based on historical MAE + buffer
        suggested_stop = abs(fingerprint.avg_mae_pct) * 1.2
        
        # Confidence based on trade count
        confidence = min(1.0, fingerprint.trade_count / 50)
        
        notes = self._generate_context_notes(fingerprint)
        
        return HistoricalContext(
            fingerprint=fingerprint,
            expected_mfe_pct=expected_mfe,
            expected_stall_pct=expected_stall,
            suggested_tp1_pct=suggested_tp1,
            suggested_stop_pct=suggested_stop,
            confidence=confidence,
            trade_count=fingerprint.trade_count,
            notes=notes
        )
    
    def get_mfe_mae_stats(
        self,
        symbol: str,
        strategy: str,
        regime: Optional[str] = None
    ) -> Dict[str, float]:
        """
        Get MFE/MAE statistics for a symbol/strategy combination.
        
        Returns:
            Dict with avg/median/std for both MFE and MAE
        """
        fingerprint = self.get_fingerprint(symbol, strategy, regime)
        
        if fingerprint is None:
            return {
                "avg_mfe_pct": 0.0,
                "median_mfe_pct": 0.0,
                "std_mfe_pct": 0.0,
                "avg_mae_pct": 0.0,
                "median_mae_pct": 0.0,
                "std_mae_pct": 0.0,
                "trade_count": 0
            }
        
        return {
            "avg_mfe_pct": fingerprint.avg_mfe_pct,
            "median_mfe_pct": fingerprint.median_mfe_pct,
            "std_mfe_pct": fingerprint.std_mfe_pct,
            "avg_mae_pct": fingerprint.avg_mae_pct,
            "median_mae_pct": fingerprint.median_mae_pct,
            "std_mae_pct": fingerprint.std_mae_pct,
            "trade_count": fingerprint.trade_count
        }
    
    def get_stall_probability(
        self,
        symbol: str,
        strategy: str,
        current_gain_pct: float,
        regime: Optional[str] = None
    ) -> float:
        """
        Calculate probability that the trade will stall at current gain level.
        
        Based on historical MFE distribution.
        
        Args:
            symbol: Trading symbol
            strategy: Bot ID
            current_gain_pct: Current unrealized gain percentage
            regime: Market regime
            
        Returns:
            Probability (0.0-1.0) that trade will stall here
        """
        fingerprint = self.get_fingerprint(symbol, strategy, regime)
        
        if fingerprint is None or fingerprint.trade_count < 5:
            return 0.5  # Unknown - 50/50
        
        # If current gain >= historical stall point, high stall probability
        if current_gain_pct >= fingerprint.avg_stall_point_pct:
            # Probability increases as we exceed stall point
            excess = current_gain_pct - fingerprint.avg_stall_point_pct
            return min(0.9, 0.5 + (excess / fingerprint.avg_mfe_pct) * 0.4)
        
        # If current gain is small, low stall probability
        if current_gain_pct < fingerprint.avg_mfe_pct * 0.3:
            return 0.2
        
        # Linear interpolation between
        progress = current_gain_pct / fingerprint.avg_stall_point_pct
        return 0.2 + progress * 0.4
    
    def _query_historical_trades(
        self,
        symbol: str,
        strategy: str,
        regime: Optional[str]
    ) -> List[Dict]:
        """Query exit_trades table for historical trades within rolling window."""
        try:
            conn = get_db_connection()
            cutoff = self._get_rolling_cutoff_iso()
            
            if regime:
                query = """
                    SELECT * FROM exit_trades
                    WHERE symbol = ? AND bot_id = ? AND regime_at_entry = ?
                    AND exit_ts IS NOT NULL
                    AND exit_ts >= ?
                    ORDER BY exit_ts DESC
                    LIMIT 200
                """
                params = (symbol, strategy, regime, cutoff)
            else:
                query = """
                    SELECT * FROM exit_trades
                    WHERE symbol = ? AND bot_id = ?
                    AND exit_ts IS NOT NULL
                    AND exit_ts >= ?
                    ORDER BY exit_ts DESC
                    LIMIT 200
                """
                params = (symbol, strategy, cutoff)
            
            cursor = conn.execute(query, params)
            columns = [desc[0] for desc in cursor.description]
            trades = [dict(zip(columns, row)) for row in cursor.fetchall()]
            
            return trades
            
        except Exception as e:
            self._logger.error(f"Failed to query historical trades: {e}")
            return []
    
    def _get_strategy_fingerprint(self, strategy: str) -> Optional[ExitFingerprint]:
        """Get aggregate fingerprint for a strategy across all symbols within rolling window."""
        try:
            conn = get_db_connection()
            cutoff = self._get_rolling_cutoff_iso()
            
            query = """
                SELECT * FROM exit_trades
                WHERE bot_id = ? AND exit_ts IS NOT NULL
                AND exit_ts >= ?
                ORDER BY exit_ts DESC
                LIMIT 500
            """
            
            cursor = conn.execute(query, (strategy, cutoff))
            columns = [desc[0] for desc in cursor.description]
            trades = [dict(zip(columns, row)) for row in cursor.fetchall()]
            
            if len(trades) < 10:
                return None
            
            return self._build_fingerprint("*", strategy, None, trades)
            
        except Exception as e:
            self._logger.error(f"Failed to get strategy fingerprint: {e}")
            return None
    
    def _build_fingerprint(
        self,
        symbol: str,
        strategy: str,
        regime: Optional[str],
        trades: List[Dict]
    ) -> ExitFingerprint:
        """Build exit fingerprint from historical trades."""
        
        mfe_values = []
        mae_values = []
        pnl_pct_values = []
        pnl_usd_values = []
        hold_durations = []
        stall_points = []
        exit_reasons: Dict[str, int] = defaultdict(int)
        
        win_count = 0
        loss_count = 0
        
        for trade in trades:
            mfe = trade.get("mfe_pct") or 0.0
            mae = trade.get("mae_pct") or 0.0
            pnl_pct = trade.get("realized_pnl_pct") or 0.0
            pnl_usd = trade.get("realized_pnl_usd") or 0.0
            hold_sec = trade.get("hold_duration_sec") or 0.0
            exit_reason = trade.get("exit_reason") or "unknown"
            
            mfe_values.append(mfe)
            mae_values.append(mae)
            pnl_pct_values.append(pnl_pct)
            pnl_usd_values.append(pnl_usd)
            
            if hold_sec > 0:
                hold_durations.append(hold_sec)
            
            # Stall point = MFE - realized gain (how much we left on the table)
            if mfe > 0 and pnl_pct >= 0:
                stall_points.append(mfe)
            
            exit_reasons[exit_reason] += 1
            
            if pnl_pct > 0:
                win_count += 1
            else:
                loss_count += 1
        
        trade_count = len(trades)
        
        # Calculate statistics with safe defaults
        def safe_mean(values, default=0.0):
            return statistics.mean(values) if values else default
        
        def safe_median(values, default=0.0):
            return statistics.median(values) if values else default
        
        def safe_stdev(values, default=0.0):
            return statistics.stdev(values) if len(values) > 1 else default
        
        return ExitFingerprint(
            symbol=symbol,
            strategy=strategy,
            regime=regime,
            trade_count=trade_count,
            win_count=win_count,
            loss_count=loss_count,
            win_rate=win_count / trade_count if trade_count > 0 else 0.0,
            avg_mfe_pct=safe_mean(mfe_values),
            median_mfe_pct=safe_median(mfe_values),
            std_mfe_pct=safe_stdev(mfe_values),
            avg_mae_pct=safe_mean(mae_values),
            median_mae_pct=safe_median(mae_values),
            std_mae_pct=safe_stdev(mae_values),
            avg_pnl_pct=safe_mean(pnl_pct_values),
            median_pnl_pct=safe_median(pnl_pct_values),
            avg_pnl_usd=safe_mean(pnl_usd_values),
            avg_hold_duration_sec=safe_mean(hold_durations),
            median_hold_duration_sec=safe_median(hold_durations),
            avg_stall_point_pct=safe_mean(stall_points, default=2.0),
            exit_reasons=dict(exit_reasons),
            confidence=min(1.0, trade_count / 50)
        )
    
    def _generate_context_notes(self, fp: ExitFingerprint) -> str:
        """Generate human-readable notes from fingerprint."""
        notes = []
        
        notes.append(f"{fp.trade_count} similar trades analyzed")
        notes.append(f"Win rate: {fp.win_rate*100:.0f}%")
        
        if fp.avg_mfe_pct > 0:
            notes.append(f"Avg MFE: +{fp.avg_mfe_pct:.1f}%")
        
        if fp.avg_stall_point_pct > 0:
            notes.append(f"Typical stall: +{fp.avg_stall_point_pct:.1f}%")
        
        if fp.avg_mae_pct < 0:
            notes.append(f"Avg MAE: {fp.avg_mae_pct:.1f}%")
        
        # Most common exit reason
        if fp.exit_reasons:
            top_reason = max(fp.exit_reasons.items(), key=lambda x: x[1])
            notes.append(f"Most common exit: {top_reason[0]} ({top_reason[1]}x)")
        
        return " | ".join(notes)
    
    def clear_cache(self) -> None:
        """Clear the fingerprint cache."""
        self._cache.clear()
        self._logger.log("trade_memory_cache_cleared", {})


# Global singleton
_trade_memory: Optional[TradeMemoryEngine] = None


def get_trade_memory() -> TradeMemoryEngine:
    """Get or create the global TradeMemoryEngine instance."""
    global _trade_memory
    if _trade_memory is None:
        _trade_memory = TradeMemoryEngine()
    return _trade_memory
