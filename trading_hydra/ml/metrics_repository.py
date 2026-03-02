"""
MetricsRepository - SQLite persistence for account-level ML features.

Stores historical data needed for training and inference:
- daily_metrics: Daily account snapshots (equity, P&L, trades, positions)
- regime_history: Historical market regime data (VIX, VVIX, TNX, DXY, MOVE)
- bot_performance: Per-bot performance tracking
- risk_decisions: Historical risk adjustment decisions for model training
"""

import sqlite3
import json
from datetime import datetime, date, timedelta
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, asdict
from pathlib import Path

from ..core.logging import get_logger
from ..core.clock import get_market_clock


@dataclass
class DailyMetrics:
    """Daily account snapshot for ML training and analysis."""
    date: str
    equity: float
    cash: float
    buying_power: float
    daily_pnl: float
    daily_pnl_pct: float
    cumulative_pnl: float
    max_drawdown_pct: float
    current_drawdown_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    open_positions: int
    crypto_positions: int
    stock_positions: int
    options_positions: int
    account_mode: str
    risk_multiplier: float


@dataclass
class RegimeSnapshot:
    """Market regime snapshot for ML features."""
    timestamp: str
    date: str
    vix: float
    vvix: float
    tnx: float
    dxy: float
    move: float
    volatility_regime: str
    sentiment: str
    rate_environment: str
    dollar_environment: str
    position_size_multiplier: float
    halt_new_entries: bool
    vvix_warning: bool
    rate_shock_warning: bool
    dollar_surge_warning: bool


@dataclass
class BotPerformance:
    """Per-bot performance metrics for allocation decisions."""
    date: str
    bot_id: str
    trades_today: int
    wins_today: int
    losses_today: int
    pnl_today: float
    pnl_pct_today: float
    avg_hold_time_mins: float
    sharpe_ratio_30d: float
    win_rate_30d: float
    max_drawdown_30d: float
    total_allocated: float
    total_returned: float


@dataclass
class RiskDecision:
    """Historical risk adjustment decision for training."""
    timestamp: str
    date: str
    decision_type: str
    previous_value: float
    new_value: float
    reason: str
    features_json: str
    outcome_pnl_next_day: Optional[float] = None
    outcome_drawdown_next_day: Optional[float] = None


class MetricsRepository:
    """
    SQLite repository for account-level ML metrics.
    
    Stores historical data for training dynamic risk adjustment,
    bot allocation, drawdown prediction, and anomaly detection models.
    """
    
    DB_PATH = Path("./state/metrics.db")
    
    def __init__(self):
        self._logger = get_logger()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_database()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._conn is None:
            self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.DB_PATH), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn
    
    def _init_database(self) -> None:
        """Initialize all database tables."""
        conn = self._get_connection()
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_metrics (
                date TEXT PRIMARY KEY,
                equity REAL NOT NULL,
                cash REAL NOT NULL,
                buying_power REAL NOT NULL,
                daily_pnl REAL NOT NULL,
                daily_pnl_pct REAL NOT NULL,
                cumulative_pnl REAL NOT NULL,
                max_drawdown_pct REAL NOT NULL,
                current_drawdown_pct REAL NOT NULL,
                total_trades INTEGER NOT NULL,
                winning_trades INTEGER NOT NULL,
                losing_trades INTEGER NOT NULL,
                win_rate REAL NOT NULL,
                avg_win REAL NOT NULL,
                avg_loss REAL NOT NULL,
                profit_factor REAL NOT NULL,
                open_positions INTEGER NOT NULL,
                crypto_positions INTEGER NOT NULL,
                stock_positions INTEGER NOT NULL,
                options_positions INTEGER NOT NULL,
                account_mode TEXT NOT NULL,
                risk_multiplier REAL NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS regime_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                date TEXT NOT NULL,
                vix REAL NOT NULL,
                vvix REAL NOT NULL,
                tnx REAL NOT NULL,
                dxy REAL NOT NULL,
                move REAL NOT NULL,
                volatility_regime TEXT NOT NULL,
                sentiment TEXT NOT NULL,
                rate_environment TEXT NOT NULL,
                dollar_environment TEXT NOT NULL,
                position_size_multiplier REAL NOT NULL,
                halt_new_entries INTEGER NOT NULL,
                vvix_warning INTEGER NOT NULL,
                rate_shock_warning INTEGER NOT NULL,
                dollar_surge_warning INTEGER NOT NULL
            )
        """)
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                bot_id TEXT NOT NULL,
                trades_today INTEGER NOT NULL,
                wins_today INTEGER NOT NULL,
                losses_today INTEGER NOT NULL,
                pnl_today REAL NOT NULL,
                pnl_pct_today REAL NOT NULL,
                avg_hold_time_mins REAL NOT NULL,
                sharpe_ratio_30d REAL NOT NULL,
                win_rate_30d REAL NOT NULL,
                max_drawdown_30d REAL NOT NULL,
                total_allocated REAL NOT NULL,
                total_returned REAL NOT NULL,
                UNIQUE(date, bot_id)
            )
        """)
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS risk_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                date TEXT NOT NULL,
                decision_type TEXT NOT NULL,
                previous_value REAL NOT NULL,
                new_value REAL NOT NULL,
                reason TEXT NOT NULL,
                features_json TEXT NOT NULL,
                outcome_pnl_next_day REAL,
                outcome_drawdown_next_day REAL
            )
        """)
        
        conn.execute("CREATE INDEX IF NOT EXISTS idx_regime_date ON regime_history(date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_regime_timestamp ON regime_history(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bot_perf_date ON bot_performance(date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bot_perf_bot ON bot_performance(bot_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_risk_date ON risk_decisions(date)")
        
        conn.commit()
        self._logger.log("metrics_repository_init", {"db_path": str(self.DB_PATH)})
    
    def save_daily_metrics(self, metrics: DailyMetrics) -> None:
        """Save or update daily metrics snapshot."""
        conn = self._get_connection()
        now = datetime.utcnow().isoformat() + "Z"
        
        conn.execute("""
            INSERT OR REPLACE INTO daily_metrics (
                date, equity, cash, buying_power, daily_pnl, daily_pnl_pct,
                cumulative_pnl, max_drawdown_pct, current_drawdown_pct,
                total_trades, winning_trades, losing_trades, win_rate,
                avg_win, avg_loss, profit_factor, open_positions,
                crypto_positions, stock_positions, options_positions,
                account_mode, risk_multiplier, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            metrics.date, metrics.equity, metrics.cash, metrics.buying_power,
            metrics.daily_pnl, metrics.daily_pnl_pct, metrics.cumulative_pnl,
            metrics.max_drawdown_pct, metrics.current_drawdown_pct,
            metrics.total_trades, metrics.winning_trades, metrics.losing_trades,
            metrics.win_rate, metrics.avg_win, metrics.avg_loss, metrics.profit_factor,
            metrics.open_positions, metrics.crypto_positions, metrics.stock_positions,
            metrics.options_positions, metrics.account_mode, metrics.risk_multiplier, now
        ))
        conn.commit()
        
        self._logger.log("daily_metrics_saved", {
            "date": metrics.date,
            "equity": metrics.equity,
            "daily_pnl": metrics.daily_pnl
        })
    
    def get_daily_metrics(self, date_str: str) -> Optional[DailyMetrics]:
        """Get daily metrics for a specific date."""
        conn = self._get_connection()
        cursor = conn.execute("SELECT * FROM daily_metrics WHERE date = ?", (date_str,))
        row = cursor.fetchone()
        
        if row:
            return DailyMetrics(
                date=row["date"],
                equity=row["equity"],
                cash=row["cash"],
                buying_power=row["buying_power"],
                daily_pnl=row["daily_pnl"],
                daily_pnl_pct=row["daily_pnl_pct"],
                cumulative_pnl=row["cumulative_pnl"],
                max_drawdown_pct=row["max_drawdown_pct"],
                current_drawdown_pct=row["current_drawdown_pct"],
                total_trades=row["total_trades"],
                winning_trades=row["winning_trades"],
                losing_trades=row["losing_trades"],
                win_rate=row["win_rate"],
                avg_win=row["avg_win"],
                avg_loss=row["avg_loss"],
                profit_factor=row["profit_factor"],
                open_positions=row["open_positions"],
                crypto_positions=row["crypto_positions"],
                stock_positions=row["stock_positions"],
                options_positions=row["options_positions"],
                account_mode=row["account_mode"],
                risk_multiplier=row["risk_multiplier"]
            )
        return None
    
    def get_daily_metrics_range(self, days: int = 30) -> List[DailyMetrics]:
        """Get daily metrics for the last N days."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM daily_metrics ORDER BY date DESC LIMIT ?", (days,)
        )
        
        results = []
        for row in cursor.fetchall():
            results.append(DailyMetrics(
                date=row["date"],
                equity=row["equity"],
                cash=row["cash"],
                buying_power=row["buying_power"],
                daily_pnl=row["daily_pnl"],
                daily_pnl_pct=row["daily_pnl_pct"],
                cumulative_pnl=row["cumulative_pnl"],
                max_drawdown_pct=row["max_drawdown_pct"],
                current_drawdown_pct=row["current_drawdown_pct"],
                total_trades=row["total_trades"],
                winning_trades=row["winning_trades"],
                losing_trades=row["losing_trades"],
                win_rate=row["win_rate"],
                avg_win=row["avg_win"],
                avg_loss=row["avg_loss"],
                profit_factor=row["profit_factor"],
                open_positions=row["open_positions"],
                crypto_positions=row["crypto_positions"],
                stock_positions=row["stock_positions"],
                options_positions=row["options_positions"],
                account_mode=row["account_mode"],
                risk_multiplier=row["risk_multiplier"]
            ))
        
        return list(reversed(results))
    
    def save_regime_snapshot(self, snapshot: RegimeSnapshot) -> None:
        """Save market regime snapshot."""
        conn = self._get_connection()
        
        conn.execute("""
            INSERT INTO regime_history (
                timestamp, date, vix, vvix, tnx, dxy, move,
                volatility_regime, sentiment, rate_environment, dollar_environment,
                position_size_multiplier, halt_new_entries,
                vvix_warning, rate_shock_warning, dollar_surge_warning
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            snapshot.timestamp, snapshot.date, snapshot.vix, snapshot.vvix,
            snapshot.tnx, snapshot.dxy, snapshot.move, snapshot.volatility_regime,
            snapshot.sentiment, snapshot.rate_environment, snapshot.dollar_environment,
            snapshot.position_size_multiplier, int(snapshot.halt_new_entries),
            int(snapshot.vvix_warning), int(snapshot.rate_shock_warning),
            int(snapshot.dollar_surge_warning)
        ))
        conn.commit()
    
    def get_regime_history(self, days: int = 30) -> List[RegimeSnapshot]:
        """Get regime history for the last N days."""
        conn = self._get_connection()
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        
        cursor = conn.execute(
            "SELECT * FROM regime_history WHERE timestamp > ? ORDER BY timestamp",
            (cutoff,)
        )
        
        results = []
        for row in cursor.fetchall():
            results.append(RegimeSnapshot(
                timestamp=row["timestamp"],
                date=row["date"],
                vix=row["vix"],
                vvix=row["vvix"],
                tnx=row["tnx"],
                dxy=row["dxy"],
                move=row["move"],
                volatility_regime=row["volatility_regime"],
                sentiment=row["sentiment"],
                rate_environment=row["rate_environment"],
                dollar_environment=row["dollar_environment"],
                position_size_multiplier=row["position_size_multiplier"],
                halt_new_entries=bool(row["halt_new_entries"]),
                vvix_warning=bool(row["vvix_warning"]),
                rate_shock_warning=bool(row["rate_shock_warning"]),
                dollar_surge_warning=bool(row["dollar_surge_warning"])
            ))
        
        return results
    
    def get_latest_regime(self) -> Optional[RegimeSnapshot]:
        """Get the most recent regime snapshot."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM regime_history ORDER BY timestamp DESC LIMIT 1"
        )
        row = cursor.fetchone()
        
        if row:
            return RegimeSnapshot(
                timestamp=row["timestamp"],
                date=row["date"],
                vix=row["vix"],
                vvix=row["vvix"],
                tnx=row["tnx"],
                dxy=row["dxy"],
                move=row["move"],
                volatility_regime=row["volatility_regime"],
                sentiment=row["sentiment"],
                rate_environment=row["rate_environment"],
                dollar_environment=row["dollar_environment"],
                position_size_multiplier=row["position_size_multiplier"],
                halt_new_entries=bool(row["halt_new_entries"]),
                vvix_warning=bool(row["vvix_warning"]),
                rate_shock_warning=bool(row["rate_shock_warning"]),
                dollar_surge_warning=bool(row["dollar_surge_warning"])
            )
        return None
    
    def save_bot_performance(self, perf: BotPerformance) -> None:
        """Save bot performance metrics."""
        conn = self._get_connection()
        
        conn.execute("""
            INSERT OR REPLACE INTO bot_performance (
                date, bot_id, trades_today, wins_today, losses_today,
                pnl_today, pnl_pct_today, avg_hold_time_mins,
                sharpe_ratio_30d, win_rate_30d, max_drawdown_30d,
                total_allocated, total_returned
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            perf.date, perf.bot_id, perf.trades_today, perf.wins_today,
            perf.losses_today, perf.pnl_today, perf.pnl_pct_today,
            perf.avg_hold_time_mins, perf.sharpe_ratio_30d, perf.win_rate_30d,
            perf.max_drawdown_30d, perf.total_allocated, perf.total_returned
        ))
        conn.commit()
    
    def get_bot_performance_range(self, bot_id: str, days: int = 30) -> List[BotPerformance]:
        """Get bot performance for the last N days."""
        conn = self._get_connection()
        cursor = conn.execute(
            """SELECT * FROM bot_performance 
               WHERE bot_id = ? 
               ORDER BY date DESC LIMIT ?""",
            (bot_id, days)
        )
        
        results = []
        for row in cursor.fetchall():
            results.append(BotPerformance(
                date=row["date"],
                bot_id=row["bot_id"],
                trades_today=row["trades_today"],
                wins_today=row["wins_today"],
                losses_today=row["losses_today"],
                pnl_today=row["pnl_today"],
                pnl_pct_today=row["pnl_pct_today"],
                avg_hold_time_mins=row["avg_hold_time_mins"],
                sharpe_ratio_30d=row["sharpe_ratio_30d"],
                win_rate_30d=row["win_rate_30d"],
                max_drawdown_30d=row["max_drawdown_30d"],
                total_allocated=row["total_allocated"],
                total_returned=row["total_returned"]
            ))
        
        return list(reversed(results))
    
    def get_all_bot_performance_range(self, days: int = 30) -> List[BotPerformance]:
        """Get bot performance for all bots for the last N days."""
        conn = self._get_connection()
        cutoff = (get_market_clock().now() - timedelta(days=days)).strftime("%Y-%m-%d")
        cursor = conn.execute(
            """SELECT * FROM bot_performance 
               WHERE date >= ? 
               ORDER BY date ASC, bot_id""",
            (cutoff,)
        )
        
        results = []
        for row in cursor.fetchall():
            results.append(BotPerformance(
                date=row["date"],
                bot_id=row["bot_id"],
                trades_today=row["trades_today"],
                wins_today=row["wins_today"],
                losses_today=row["losses_today"],
                pnl_today=row["pnl_today"],
                pnl_pct_today=row["pnl_pct_today"],
                avg_hold_time_mins=row["avg_hold_time_mins"],
                sharpe_ratio_30d=row["sharpe_ratio_30d"],
                win_rate_30d=row["win_rate_30d"],
                max_drawdown_30d=row["max_drawdown_30d"],
                total_allocated=row["total_allocated"],
                total_returned=row["total_returned"]
            ))
        
        return results
    
    def get_all_bots_performance_today(self, date_str: str) -> Dict[str, BotPerformance]:
        """Get today's performance for all bots."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM bot_performance WHERE date = ?", (date_str,)
        )
        
        results = {}
        for row in cursor.fetchall():
            results[row["bot_id"]] = BotPerformance(
                date=row["date"],
                bot_id=row["bot_id"],
                trades_today=row["trades_today"],
                wins_today=row["wins_today"],
                losses_today=row["losses_today"],
                pnl_today=row["pnl_today"],
                pnl_pct_today=row["pnl_pct_today"],
                avg_hold_time_mins=row["avg_hold_time_mins"],
                sharpe_ratio_30d=row["sharpe_ratio_30d"],
                win_rate_30d=row["win_rate_30d"],
                max_drawdown_30d=row["max_drawdown_30d"],
                total_allocated=row["total_allocated"],
                total_returned=row["total_returned"]
            )
        
        return results
    
    def save_risk_decision(self, decision: RiskDecision) -> None:
        """Save a risk adjustment decision for training."""
        conn = self._get_connection()
        
        conn.execute("""
            INSERT INTO risk_decisions (
                timestamp, date, decision_type, previous_value, new_value,
                reason, features_json, outcome_pnl_next_day, outcome_drawdown_next_day
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            decision.timestamp, decision.date, decision.decision_type,
            decision.previous_value, decision.new_value, decision.reason,
            decision.features_json, decision.outcome_pnl_next_day,
            decision.outcome_drawdown_next_day
        ))
        conn.commit()
        
        self._logger.log("risk_decision_saved", {
            "type": decision.decision_type,
            "previous": decision.previous_value,
            "new": decision.new_value
        })
    
    def get_risk_decisions(self, days: int = 90) -> List[RiskDecision]:
        """Get risk decisions for the last N days."""
        conn = self._get_connection()
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        cursor = conn.execute(
            "SELECT * FROM risk_decisions WHERE date >= ? ORDER BY timestamp",
            (cutoff,)
        )
        
        results = []
        for row in cursor.fetchall():
            results.append(RiskDecision(
                timestamp=row["timestamp"],
                date=row["date"],
                decision_type=row["decision_type"],
                previous_value=row["previous_value"],
                new_value=row["new_value"],
                reason=row["reason"],
                features_json=row["features_json"],
                outcome_pnl_next_day=row["outcome_pnl_next_day"],
                outcome_drawdown_next_day=row["outcome_drawdown_next_day"]
            ))
        
        return results
    
    def update_risk_decision_outcome(
        self, 
        decision_id: int, 
        pnl_next_day: float, 
        drawdown_next_day: float
    ) -> None:
        """Update a risk decision with its outcome (for training labels)."""
        conn = self._get_connection()
        
        conn.execute("""
            UPDATE risk_decisions 
            SET outcome_pnl_next_day = ?, outcome_drawdown_next_day = ?
            WHERE id = ?
        """, (pnl_next_day, drawdown_next_day, decision_id))
        conn.commit()
    
    def get_equity_curve(self, days: int = 30) -> List[Tuple[str, float]]:
        """Get equity curve data for charting/analysis."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT date, equity FROM daily_metrics ORDER BY date DESC LIMIT ?",
            (days,)
        )
        
        results = [(row["date"], row["equity"]) for row in cursor.fetchall()]
        return list(reversed(results))
    
    def get_drawdown_series(self, days: int = 30) -> List[Tuple[str, float]]:
        """Get drawdown series for analysis."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT date, current_drawdown_pct FROM daily_metrics ORDER BY date DESC LIMIT ?",
            (days,)
        )
        
        results = [(row["date"], row["current_drawdown_pct"]) for row in cursor.fetchall()]
        return list(reversed(results))
    
    def get_training_dataset(self, min_days: int = 30) -> List[Dict[str, Any]]:
        """
        Build a unified training dataset combining all metrics.
        
        Returns a list of dictionaries with features and labels for ML training.
        """
        daily_metrics = self.get_daily_metrics_range(days=min_days)
        regime_history = self.get_regime_history(days=min_days)
        
        if len(daily_metrics) < min_days:
            return []
        
        dataset = []
        regime_by_date = {}
        for r in regime_history:
            if r.date not in regime_by_date:
                regime_by_date[r.date] = r
        
        for i, metrics in enumerate(daily_metrics[:-1]):
            next_metrics = daily_metrics[i + 1]
            regime = regime_by_date.get(metrics.date)
            
            record = {
                "date": metrics.date,
                "equity": metrics.equity,
                "daily_pnl_pct": metrics.daily_pnl_pct,
                "cumulative_pnl": metrics.cumulative_pnl,
                "current_drawdown_pct": metrics.current_drawdown_pct,
                "max_drawdown_pct": metrics.max_drawdown_pct,
                "win_rate": metrics.win_rate,
                "profit_factor": metrics.profit_factor,
                "total_trades": metrics.total_trades,
                "open_positions": metrics.open_positions,
                "risk_multiplier": metrics.risk_multiplier,
                "vix": regime.vix if regime else 18.0,
                "vvix": regime.vvix if regime else 100.0,
                "tnx": regime.tnx if regime else 4.0,
                "position_size_multiplier": regime.position_size_multiplier if regime else 1.0,
                "next_day_pnl_pct": next_metrics.daily_pnl_pct,
                "next_day_drawdown_pct": next_metrics.current_drawdown_pct
            }
            dataset.append(record)
        
        return dataset
    
    def close(self) -> None:
        """Close database connection with thread-safety handling."""
        if self._conn:
            try:
                self._conn.close()
            except Exception as e:
                if "thread" in str(e).lower():
                    self._logger.log("cross_thread_connection_close_skipped", {
                        "detail": str(e),
                        "component": "metrics_repository"
                    })
                else:
                    self._logger.error(f"Error closing metrics repository connection: {e}")
            finally:
                self._conn = None


_metrics_repo: Optional[MetricsRepository] = None


def get_metrics_repository() -> MetricsRepository:
    """Get or create singleton MetricsRepository instance."""
    global _metrics_repo
    if _metrics_repo is None:
        _metrics_repo = MetricsRepository()
    return _metrics_repo
