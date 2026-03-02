# src/trading_hydra/hub/hub_store_mysql.py
"""
HubStoreMySQL - Single point of MySQL access for inter-bot communication.

Pattern:
- MarketData writes market_snapshots
- Strategy reads latest snapshot, writes trade_intents
- Execution leases trade_intents, writes order_events, acks/fails intents
- Exit upserts positions, writes pnl_events, updates strategy_kill_state

This class is intentionally boring and deterministic.
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import mysql.connector
    from mysql.connector import errorcode
except Exception as e:
    raise RuntimeError(
        "Missing dependency: mysql-connector-python. Install with: pip install mysql-connector-python"
    ) from e


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ts6(dt: Optional[datetime] = None) -> str:
    """MySQL TIMESTAMP(6) string."""
    if dt is None:
        dt = _utcnow()
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


@dataclass(frozen=True)
class LeaseResult:
    leased_intent_ids: List[str]
    leased_by: str
    lease_seconds: int


class HubStoreMySQL:
    """
    HubStoreMySQL is the only place that knows how bots communicate via MySQL.

    Pattern:
    - MarketData writes market_snapshots
    - Strategy reads latest snapshot, writes trade_intents
    - Execution leases trade_intents, writes order_events, acks/fails intents
    - Exit upserts positions, writes pnl_events, updates strategy_kill_state

    This class is intentionally boring and deterministic.
    """

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        *,
        connect_timeout: int = 5,
        autocommit: bool = True,
        retry_attempts: int = 5,
        retry_backoff_base_s: float = 0.25,
        app_name: str = "trading_hydra",
    ) -> None:
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        self.database = database
        self.connect_timeout = int(connect_timeout)
        self.autocommit = bool(autocommit)
        self.retry_attempts = int(retry_attempts)
        self.retry_backoff_base_s = float(retry_backoff_base_s)
        self.app_name = app_name

        self._conn = None

    # -------------------------
    # Connection + helpers
    # -------------------------

    def connect(self) -> None:
        if self._conn is not None and self._conn.is_connected():
            return

        self._conn = mysql.connector.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            connection_timeout=self.connect_timeout,
            autocommit=self.autocommit,
        )

        cur = self._conn.cursor()
        cur.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED;")
        cur.close()

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None

    def _cursor(self, dictionary: bool = True):
        self.connect()
        assert self._conn is not None
        return self._conn.cursor(dictionary=dictionary)

    def _exec(self, sql: str, params: Optional[Sequence[Any]] = None) -> int:
        """Execute a statement, return affected rowcount."""
        for attempt in range(self.retry_attempts):
            try:
                cur = self._cursor(dictionary=False)
                cur.execute(sql, params or ())
                rowcount = cur.rowcount
                cur.close()
                return rowcount
            except mysql.connector.Error as e:
                self._maybe_retry(attempt, e)
        raise RuntimeError("DB exec failed after retries")

    def _fetchone(self, sql: str, params: Optional[Sequence[Any]] = None) -> Optional[Dict[str, Any]]:
        for attempt in range(self.retry_attempts):
            try:
                cur = self._cursor(dictionary=True)
                cur.execute(sql, params or ())
                row = cur.fetchone()
                cur.close()
                return row
            except mysql.connector.Error as e:
                self._maybe_retry(attempt, e)
        raise RuntimeError("DB fetchone failed after retries")

    def _fetchall(self, sql: str, params: Optional[Sequence[Any]] = None) -> List[Dict[str, Any]]:
        for attempt in range(self.retry_attempts):
            try:
                cur = self._cursor(dictionary=True)
                cur.execute(sql, params or ())
                rows = cur.fetchall() or []
                cur.close()
                return rows
            except mysql.connector.Error as e:
                self._maybe_retry(attempt, e)
        raise RuntimeError("DB fetchall failed after retries")

    def _maybe_retry(self, attempt: int, err: mysql.connector.Error) -> None:
        transient = {
            errorcode.CR_SERVER_LOST,
            errorcode.CR_SERVER_GONE_ERROR,
            errorcode.CR_CONNECTION_ERROR,
            errorcode.ER_LOCK_DEADLOCK,
            errorcode.ER_LOCK_WAIT_TIMEOUT,
        }
        errno = getattr(err, "errno", None)
        if attempt >= self.retry_attempts - 1 or errno not in transient:
            raise
        sleep_s = self.retry_backoff_base_s * (2 ** attempt)
        time.sleep(sleep_s)
        self.close()

    # -------------------------
    # Identity helpers
    # -------------------------

    @staticmethod
    def default_bot_name() -> str:
        return socket.gethostname()

    @staticmethod
    def make_worker_id(role: str) -> str:
        return f"{role}@{socket.gethostname()}"

    # -------------------------
    # Heartbeats
    # -------------------------

    def upsert_heartbeat(
        self,
        bot_name: str,
        role: str,
        *,
        host: Optional[str] = None,
        pid: Optional[int] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        sql = """
        INSERT INTO bot_heartbeats (bot_name, role, host, pid, meta_json)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          host = VALUES(host),
          pid = VALUES(pid),
          meta_json = VALUES(meta_json),
          last_seen_ts = CURRENT_TIMESTAMP(6);
        """
        meta_json = json.dumps(meta or {}, separators=(",", ":"))
        self._exec(sql, (bot_name, role, host or bot_name, pid, meta_json))

    # -------------------------
    # Snapshots
    # -------------------------

    def insert_snapshot(
        self,
        *,
        source_bot: str,
        market_session: str,
        payload: Dict[str, Any],
        vix: Optional[float] = None,
        regime: Optional[str] = None,
    ) -> str:
        snapshot_id = str(uuid.uuid4())
        symbols_count = int(payload.get("symbols_count") or payload.get("count") or 0)
        payload_json = json.dumps(payload, separators=(",", ":"))

        sql = """
        INSERT INTO market_snapshots
          (snapshot_id, source_bot, market_session, vix, regime, symbols_count, payload_json)
        VALUES
          (%s, %s, %s, %s, %s, %s, %s);
        """
        self._exec(sql, (snapshot_id, source_bot, market_session, vix, regime, symbols_count, payload_json))
        return snapshot_id

    def get_latest_snapshot(self) -> Optional[Dict[str, Any]]:
        sql = """
        SELECT snapshot_id, created_ts, source_bot, market_session, vix, regime, symbols_count, payload_json
        FROM market_snapshots
        ORDER BY created_ts DESC
        LIMIT 1;
        """
        row = self._fetchone(sql)
        if not row:
            return None
        row["payload"] = json.loads(row["payload_json"])
        return row

    # -------------------------
    # Posture
    # -------------------------

    def get_posture(self) -> str:
        sql = """
        SELECT posture_value
        FROM system_posture
        WHERE posture_key='risk_mode'
        LIMIT 1;
        """
        row = self._fetchone(sql)
        return (row or {}).get("posture_value") or "STANDARD"

    def set_posture(self, posture_value: str) -> None:
        sql = """
        INSERT INTO system_posture (posture_key, posture_value)
        VALUES ('risk_mode', %s)
        ON DUPLICATE KEY UPDATE
          posture_value=VALUES(posture_value),
          updated_ts=CURRENT_TIMESTAMP(6);
        """
        self._exec(sql, (posture_value,))

    # -------------------------
    # Kill-switch
    # -------------------------

    def get_kill_state(self, strategy_id: str) -> Dict[str, Any]:
        sql = """
        SELECT strategy_id, is_killed, killed_until_ts, killed_reason, killed_ts, rolling_trades, max_drawdown, meta_json
        FROM strategy_kill_state
        WHERE strategy_id=%s
        LIMIT 1;
        """
        row = self._fetchone(sql, (strategy_id,))
        if not row:
            return {
                "strategy_id": strategy_id,
                "is_killed": 0,
                "killed_until_ts": None,
                "killed_reason": None,
                "killed_ts": None,
                "rolling_trades": 20,
                "max_drawdown": 250.0,
                "meta": {},
            }
        row["meta"] = json.loads(row["meta_json"]) if row.get("meta_json") else {}
        return row

    def upsert_kill_state(
        self,
        strategy_id: str,
        *,
        is_killed: bool,
        killed_reason: Optional[str],
        killed_until_ts: Optional[datetime],
        rolling_trades: int,
        max_drawdown: float,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        sql = """
        INSERT INTO strategy_kill_state
          (strategy_id, is_killed, killed_reason, killed_ts, killed_until_ts, rolling_trades, max_drawdown, meta_json)
        VALUES
          (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          is_killed=VALUES(is_killed),
          killed_reason=VALUES(killed_reason),
          killed_ts=VALUES(killed_ts),
          killed_until_ts=VALUES(killed_until_ts),
          rolling_trades=VALUES(rolling_trades),
          max_drawdown=VALUES(max_drawdown),
          meta_json=VALUES(meta_json),
          updated_ts=CURRENT_TIMESTAMP(6);
        """
        killed_ts = _utcnow() if is_killed else None
        self._exec(
            sql,
            (
                strategy_id,
                1 if is_killed else 0,
                killed_reason,
                _ts6(killed_ts) if killed_ts else None,
                _ts6(killed_until_ts) if killed_until_ts else None,
                int(rolling_trades),
                float(max_drawdown),
                json.dumps(meta or {}, separators=(",", ":")),
            ),
        )

    # -------------------------
    # Intents
    # -------------------------

    def create_intent(
        self,
        *,
        snapshot_id: str,
        strategy_id: str,
        symbol: str,
        side: str,
        instrument: str,
        idempotency_key: str,
        receipt: Dict[str, Any],
        qty: Optional[int] = None,
        limit_price: Optional[float] = None,
        max_spread: Optional[float] = None,
        option_symbol: Optional[str] = None,
        expiry: Optional[str] = None,
        strike: Optional[float] = None,
        right: Optional[str] = None,
    ) -> str:
        intent_id = str(uuid.uuid4())
        sql = """
        INSERT INTO trade_intents (
          intent_id, snapshot_id, strategy_id, symbol, side, instrument,
          idempotency_key,
          option_symbol, expiry, strike, right,
          qty, limit_price, max_spread,
          receipt_json
        ) VALUES (
          %s, %s, %s, %s, %s, %s,
          %s,
          %s, %s, %s, %s,
          %s, %s, %s,
          %s
        )
        ON DUPLICATE KEY UPDATE
          updated_ts=CURRENT_TIMESTAMP(6);
        """
        self._exec(
            sql,
            (
                intent_id,
                snapshot_id,
                strategy_id,
                symbol,
                side,
                instrument,
                idempotency_key,
                option_symbol,
                expiry,
                strike,
                right,
                qty,
                limit_price,
                max_spread,
                json.dumps(receipt, separators=(",", ":")),
            ),
        )
        return intent_id

    def lease_intents(
        self,
        *,
        leased_by: str,
        limit: int = 5,
        lease_seconds: int = 45,
        prefer_skip_locked: bool = True,
    ) -> LeaseResult:
        """
        Lease NEW intents (or expired leases) safely.

        Best case uses SKIP LOCKED (MySQL 8+).
        Fallback uses atomic UPDATE loop.
        """
        self.connect()
        assert self._conn is not None

        leased_ids: List[str] = []

        if prefer_skip_locked:
            try:
                cur = self._conn.cursor(dictionary=False)
                self._conn.start_transaction()

                cur.execute(
                    """
                    SELECT intent_id
                    FROM trade_intents
                    WHERE
                      (status='NEW' OR (status='LEASED' AND leased_until_ts < CURRENT_TIMESTAMP(6)))
                    ORDER BY created_ts ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED;
                    """,
                    (int(limit),),
                )
                rows = cur.fetchall() or []
                leased_ids = [r[0] for r in rows]

                if leased_ids:
                    placeholders = ",".join(["%s"] * len(leased_ids))
                    cur.execute(
                        f"""
                        UPDATE trade_intents
                        SET
                          status='LEASED',
                          leased_by=%s,
                          leased_until_ts=DATE_ADD(CURRENT_TIMESTAMP(6), INTERVAL %s SECOND),
                          updated_ts=CURRENT_TIMESTAMP(6)
                        WHERE intent_id IN ({placeholders});
                        """,
                        (leased_by, int(lease_seconds), *leased_ids),
                    )

                self._conn.commit()
                cur.close()
                return LeaseResult(leased_intent_ids=leased_ids, leased_by=leased_by, lease_seconds=lease_seconds)

            except mysql.connector.Error:
                try:
                    self._conn.rollback()
                except Exception:
                    pass

        for _ in range(int(limit)):
            row = self._fetchone(
                """
                SELECT intent_id
                FROM trade_intents
                WHERE
                  (status='NEW' OR (status='LEASED' AND leased_until_ts < CURRENT_TIMESTAMP(6)))
                ORDER BY created_ts ASC
                LIMIT 1;
                """
            )
            if not row:
                break
            intent_id = row["intent_id"]
            rc = self._exec(
                """
                UPDATE trade_intents
                SET
                  status='LEASED',
                  leased_by=%s,
                  leased_until_ts=DATE_ADD(CURRENT_TIMESTAMP(6), INTERVAL %s SECOND),
                  updated_ts=CURRENT_TIMESTAMP(6)
                WHERE intent_id=%s
                  AND (status='NEW' OR (status='LEASED' AND leased_until_ts < CURRENT_TIMESTAMP(6)));
                """,
                (leased_by, int(lease_seconds), intent_id),
            )
            if rc == 1:
                leased_ids.append(intent_id)

        return LeaseResult(leased_intent_ids=leased_ids, leased_by=leased_by, lease_seconds=lease_seconds)

    def get_intents_by_ids(self, intent_ids: Sequence[str]) -> List[Dict[str, Any]]:
        if not intent_ids:
            return []
        placeholders = ",".join(["%s"] * len(intent_ids))
        rows = self._fetchall(
            f"SELECT * FROM trade_intents WHERE intent_id IN ({placeholders});",
            list(intent_ids),
        )
        for r in rows:
            r["receipt"] = json.loads(r["receipt_json"]) if r.get("receipt_json") else {}
        return rows

    def mark_intent_submitted(self, *, intent_id: str, leased_by: str) -> None:
        self._exec(
            """
            UPDATE trade_intents
            SET status='SUBMITTED',
                updated_ts=CURRENT_TIMESTAMP(6),
                last_error=NULL
            WHERE intent_id=%s
              AND status='LEASED'
              AND leased_by=%s;
            """,
            (intent_id, leased_by),
        )

    def ack_intent(self, *, intent_id: str, leased_by: str) -> None:
        self._exec(
            """
            UPDATE trade_intents
            SET status='ACKED',
                updated_ts=CURRENT_TIMESTAMP(6)
            WHERE intent_id=%s
              AND status IN ('LEASED','SUBMITTED')
              AND leased_by=%s;
            """,
            (intent_id, leased_by),
        )

    def fail_intent(self, *, intent_id: str, leased_by: str, error_text: str) -> None:
        self._exec(
            """
            UPDATE trade_intents
            SET status='FAILED',
                last_error=%s,
                updated_ts=CURRENT_TIMESTAMP(6)
            WHERE intent_id=%s
              AND leased_by=%s;
            """,
            (error_text[:512], intent_id, leased_by),
        )

    # -------------------------
    # Order events
    # -------------------------

    def insert_order_event(
        self,
        *,
        intent_id: Optional[str],
        strategy_id: Optional[str],
        symbol: str,
        broker_order_id: Optional[str],
        event_type: str,
        qty: Optional[int] = None,
        price: Optional[float] = None,
        payload: Optional[Dict[str, Any]] = None,
        error_text: Optional[str] = None,
        broker: str = "alpaca",
    ) -> None:
        sql = """
        INSERT INTO order_events (
          intent_id, strategy_id, symbol, broker, broker_order_id,
          event_type, qty, price, payload_json, error_text
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);
        """
        payload_json = json.dumps(payload or {}, separators=(",", ":")) if payload is not None else None
        self._exec(
            sql,
            (
                intent_id,
                strategy_id,
                symbol,
                broker,
                broker_order_id,
                event_type,
                qty,
                price,
                payload_json,
                error_text[:512] if error_text else None,
            ),
        )

    # -------------------------
    # Positions + PnL
    # -------------------------

    def upsert_position(
        self,
        *,
        position_id: str,
        symbol: str,
        instrument: str,
        qty: int,
        avg_price: Optional[float],
        market_price: Optional[float],
        unrealized_pnl: Optional[float],
        realized_pnl: Optional[float],
        status: str,
        strategy_id: Optional[str] = None,
        intent_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        sql = """
        INSERT INTO positions (
          position_id, symbol, instrument, strategy_id, intent_id,
          qty, avg_price, market_price, unrealized_pnl, realized_pnl,
          status, meta_json
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          qty=VALUES(qty),
          avg_price=VALUES(avg_price),
          market_price=VALUES(market_price),
          unrealized_pnl=VALUES(unrealized_pnl),
          realized_pnl=VALUES(realized_pnl),
          status=VALUES(status),
          meta_json=VALUES(meta_json),
          updated_ts=CURRENT_TIMESTAMP(6);
        """
        self._exec(
            sql,
            (
                position_id,
                symbol,
                instrument,
                strategy_id,
                intent_id,
                int(qty),
                avg_price,
                market_price,
                unrealized_pnl,
                realized_pnl,
                status,
                json.dumps(meta or {}, separators=(",", ":")),
            ),
        )

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Get all open or closing positions."""
        sql = """
        SELECT position_id, symbol, instrument, strategy_id, intent_id,
               qty, avg_price, market_price, unrealized_pnl, realized_pnl,
               status, meta_json, updated_ts
        FROM positions
        WHERE status IN ('OPEN', 'CLOSING');
        """
        rows = self._fetchall(sql)
        for r in rows:
            r["meta"] = json.loads(r["meta_json"]) if r.get("meta_json") else {}
        return rows

    def insert_pnl_event(
        self,
        *,
        position_id: str,
        symbol: str,
        realized_pnl: float,
        strategy_id: Optional[str] = None,
        intent_id: Optional[str] = None,
        notes: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        sql = """
        INSERT INTO pnl_events (
          position_id, strategy_id, intent_id, symbol, realized_pnl, notes, payload_json
        ) VALUES (%s,%s,%s,%s,%s,%s,%s);
        """
        self._exec(
            sql,
            (
                position_id,
                strategy_id,
                intent_id,
                symbol,
                float(realized_pnl),
                notes[:256] if notes else None,
                json.dumps(payload or {}, separators=(",", ":")) if payload is not None else None,
            ),
        )

    def get_recent_realized_pnl(self, *, strategy_id: str, limit: int) -> List[float]:
        rows = self._fetchall(
            """
            SELECT realized_pnl
            FROM pnl_events
            WHERE strategy_id=%s
            ORDER BY created_ts DESC
            LIMIT %s;
            """,
            (strategy_id, int(limit)),
        )
        return [float(r["realized_pnl"]) for r in rows]

    # -------------------------
    # Utilities
    # -------------------------

    @staticmethod
    def compute_rolling_drawdown(pnls_most_recent_first: List[float]) -> float:
        """
        Given realized PnL events most recent first, compute max drawdown
        over that window (in absolute currency units).
        """
        pnls = list(reversed(pnls_most_recent_first))
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            equity += float(p)
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
        return max_dd

    def enforce_kill_switch_from_recent_pnl(
        self,
        *,
        strategy_id: str,
        rolling_trades: int,
        max_drawdown: float,
        cooloff_minutes: int,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        ExitBot calls this after writing a pnl_event.

        It pulls last N realized pnls, computes rolling drawdown,
        and sets killed_until if breached.
        """
        pnls = self.get_recent_realized_pnl(strategy_id=strategy_id, limit=int(rolling_trades))
        dd = self.compute_rolling_drawdown(pnls)
        breached = dd >= float(max_drawdown)

        killed_until = _utcnow() + timedelta(minutes=int(cooloff_minutes)) if breached else None
        reason = f"rolling_drawdown {dd:.2f} >= {max_drawdown:.2f}" if breached else None

        self.upsert_kill_state(
            strategy_id,
            is_killed=breached,
            killed_reason=reason,
            killed_until_ts=killed_until,
            rolling_trades=int(rolling_trades),
            max_drawdown=float(max_drawdown),
            meta={"rolling_drawdown": dd, **(meta or {})},
        )
        return {"strategy_id": strategy_id, "breached": breached, "rolling_drawdown": dd, "killed_until": killed_until}


def create_hub_store_from_env() -> HubStoreMySQL:
    """
    Factory function to create HubStoreMySQL from environment variables.
    
    Required env vars:
    - HUB_DB_HOST
    - HUB_DB_PORT
    - HUB_DB_NAME
    - HUB_DB_USER
    - HUB_DB_PASS
    
    Fails closed if any are missing.
    """
    required = ["HUB_DB_HOST", "HUB_DB_PORT", "HUB_DB_NAME", "HUB_DB_USER", "HUB_DB_PASS"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Missing required hub database env vars: {', '.join(missing)}")
    
    return HubStoreMySQL(
        host=os.environ["HUB_DB_HOST"],
        port=int(os.environ["HUB_DB_PORT"]),
        user=os.environ["HUB_DB_USER"],
        password=os.environ["HUB_DB_PASS"],
        database=os.environ["HUB_DB_NAME"],
    )
