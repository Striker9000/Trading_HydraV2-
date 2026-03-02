"""SQLite-backed state store for durable state persistence"""
import os
import json
import sqlite3
import threading
import shutil
import signal
import atexit
from typing import Any, Optional, Dict, Tuple
from datetime import datetime

from .logging import get_logger

_db_path = "./state/trading_state.db"
_db_lock = threading.Lock()
_thread_local = threading.local()
_checkpoint_thread: Optional[threading.Thread] = None
_checkpoint_stop_event = threading.Event()

# Registry of all thread-local connections for proper shutdown
_connection_registry: Dict[int, sqlite3.Connection] = {}
_registry_lock = threading.Lock()


def check_database_integrity() -> Tuple[bool, str]:
    """
    Check if the database file exists and is not corrupted.
    
    Returns:
        Tuple of (is_healthy, message)
    """
    logger = get_logger()
    
    # Check if file exists
    if not os.path.exists(_db_path):
        return True, "Database does not exist yet, will be created"
    
    # Check if file is empty (common corruption case)
    if os.path.getsize(_db_path) == 0:
        return False, "Database file is empty (0 bytes)"
    
    # Try to open and run integrity check
    conn = None
    try:
        conn = sqlite3.connect(_db_path, timeout=10.0)
        cursor = conn.execute("PRAGMA integrity_check")
        result = cursor.fetchone()[0]
        
        if result == "ok":
            return True, "Database integrity check passed"
        else:
            return False, f"Integrity check failed: {result}"
            
    except sqlite3.DatabaseError as e:
        return False, f"Database error: {e}"
    except Exception as e:
        return False, f"Unexpected error checking database: {e}"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _recover_corrupted_database() -> bool:
    """
    Recover from a corrupted database by backing up the bad file and creating fresh.
    
    Returns:
        True if recovery was successful
    """
    logger = get_logger()
    
    try:
        # Create backup directory
        backup_dir = "./state/corrupt_backups"
        os.makedirs(backup_dir, exist_ok=True)
        
        # Generate backup filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Backup the corrupted database and WAL files
        files_to_backup = [
            _db_path,
            f"{_db_path}-wal",
            f"{_db_path}-shm"
        ]
        
        backed_up = []
        for src_path in files_to_backup:
            if os.path.exists(src_path):
                filename = os.path.basename(src_path)
                backup_path = os.path.join(backup_dir, f"{timestamp}_{filename}")
                shutil.move(src_path, backup_path)
                backed_up.append(backup_path)
        
        logger.log("database_corruption_recovered", {
            "action": "backed_up_and_removed",
            "backed_up_files": backed_up,
            "timestamp": timestamp
        })
        
        print(f"[DATABASE RECOVERY] Corrupted database backed up to {backup_dir}")
        print(f"[DATABASE RECOVERY] Fresh database will be created on next access")
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to recover corrupted database: {e}")
        return False


def checkpoint_wal() -> bool:
    """
    Force a WAL checkpoint to flush changes to the main database file.
    
    This reduces the risk of data loss if the process is killed unexpectedly.
    Should be called periodically and before graceful shutdown.
    
    Returns:
        True if checkpoint was successful
    """
    try:
        conn = _get_connection()
        # PRAGMA wal_checkpoint(TRUNCATE) flushes WAL and truncates it
        result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        get_logger().log("wal_checkpoint_complete", {
            "busy": result[0],
            "log": result[1],
            "checkpointed": result[2]
        })
        return True
    except Exception as e:
        get_logger().error(f"WAL checkpoint failed: {e}")
        return False


def _periodic_checkpoint_loop():
    """Background thread that checkpoints WAL every 5 minutes."""
    logger = get_logger()
    interval_seconds = 300  # 5 minutes
    
    while not _checkpoint_stop_event.wait(interval_seconds):
        try:
            checkpoint_wal()
        except Exception as e:
            logger.error(f"Periodic checkpoint error: {e}")


def start_periodic_checkpoint():
    """Start the background checkpoint thread."""
    global _checkpoint_thread
    
    if _checkpoint_thread is not None and _checkpoint_thread.is_alive():
        return  # Already running
    
    _checkpoint_stop_event.clear()
    _checkpoint_thread = threading.Thread(
        target=_periodic_checkpoint_loop,
        name="WALCheckpoint",
        daemon=True
    )
    _checkpoint_thread.start()
    get_logger().log("periodic_checkpoint_started", {"interval_seconds": 300})


def stop_periodic_checkpoint():
    """Stop the background checkpoint thread."""
    global _checkpoint_thread
    
    _checkpoint_stop_event.set()
    if _checkpoint_thread is not None:
        _checkpoint_thread.join(timeout=5.0)
        _checkpoint_thread = None


def graceful_shutdown_handler(signum=None, frame=None):
    """
    Handle graceful shutdown by checkpointing WAL before exit.
    
    This is registered as a signal handler for SIGTERM and SIGINT,
    and also as an atexit handler.
    
    Note: Signal handlers should be kept minimal. We do our best to checkpoint
    and close connections, but errors are caught to prevent crashes.
    """
    logger = get_logger()
    logger.log("graceful_shutdown_initiated", {"signal": signum})
    
    try:
        # Stop periodic checkpoint thread
        stop_periodic_checkpoint()
        
        # Final checkpoint before exit (using current thread's connection)
        checkpoint_wal()
        
        # Close ALL registered connections across all threads
        # This is critical to ensure WAL is fully flushed
        closed = close_all_connections()
        
        logger.log("graceful_shutdown_complete", {"connections_closed": closed})
        
    except Exception as e:
        logger.error(f"Error during graceful shutdown: {e}")
    
    # If called from signal, exit
    if signum is not None:
        import sys
        sys.exit(0)


def register_shutdown_handlers():
    """Register signal handlers and atexit for graceful shutdown."""
    # Register signal handlers (Unix only)
    try:
        signal.signal(signal.SIGTERM, graceful_shutdown_handler)
        signal.signal(signal.SIGINT, graceful_shutdown_handler)
    except (ValueError, OSError):
        # Signal handling may fail in non-main threads
        pass
    
    # Register atexit handler as fallback
    atexit.register(graceful_shutdown_handler)


def _get_connection() -> sqlite3.Connection:
    """
    Get a thread-local SQLite connection.
    
    Each thread gets its own connection to prevent cursor corruption errors like:
    - "error return without exception set"
    - "another row available"  
    - "no more rows available"
    
    WAL mode allows multiple connections to read/write concurrently.
    Connections are registered for proper cleanup on shutdown.
    """
    if not hasattr(_thread_local, 'conn') or _thread_local.conn is None:
        os.makedirs(os.path.dirname(_db_path), exist_ok=True)
        conn = sqlite3.connect(_db_path, timeout=60.0)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = None
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=60000")
        conn.execute("PRAGMA synchronous=NORMAL")
        _thread_local.conn = conn
        
        # Register connection for shutdown cleanup
        thread_id = threading.get_ident()
        with _registry_lock:
            _connection_registry[thread_id] = conn
            
    return _thread_local.conn


def _safe_commit(conn: sqlite3.Connection) -> None:
    """Thread-safe commit with error handling for concurrent access."""
    try:
        conn.commit()
    except sqlite3.OperationalError as e:
        if "no transaction is active" in str(e):
            pass
        else:
            raise


def get_db_connection() -> sqlite3.Connection:
    """
    Get a thread-local SQLite connection for direct queries.
    
    Used by ExitBot and other services that need direct SQL access
    for audit-grade logging and historical analysis.
    
    Note: Each thread gets its own connection to prevent cursor corruption.
    """
    return _get_connection()


def init_state_store() -> None:
    logger = get_logger()
    logger.log("state_store_init", {"path": _db_path})
    
    # Check database integrity before proceeding
    is_healthy, message = check_database_integrity()
    logger.log("database_integrity_check", {"healthy": is_healthy, "message": message})
    
    if not is_healthy:
        print(f"[WARNING] Database corruption detected: {message}")
        logger.log("database_corruption_detected", {"message": message})
        
        # Attempt recovery
        if _recover_corrupted_database():
            print("[INFO] Database recovered, creating fresh database...")
        else:
            raise RuntimeError(f"Failed to recover corrupted database: {message}")

    conn = _get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # Table for order idempotency tracking
    conn.execute("""
        CREATE TABLE IF NOT EXISTS order_ids (
            client_order_id TEXT PRIMARY KEY,
            bot_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            day_key TEXT NOT NULL,
            signal_id TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            alpaca_order_id TEXT
        )
    """)
    
    # =========================================================================
    # ExitBot v2 Tables - Audit-Grade Memory for Elite Exit Intelligence
    # =========================================================================
    
    # exit_trades: One row per completed position lifecycle
    # This is the primary table for "examine the past" queries
    conn.execute("""
        CREATE TABLE IF NOT EXISTS exit_trades (
            position_key TEXT PRIMARY KEY,
            bot_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            asset_class TEXT NOT NULL,
            side TEXT NOT NULL,
            
            entry_ts TEXT NOT NULL,
            entry_price REAL NOT NULL,
            entry_signal_id TEXT,
            entry_client_order_id TEXT,
            entry_alpaca_order_id TEXT,
            
            exit_ts TEXT,
            exit_price REAL,
            exit_reason TEXT,
            
            qty REAL NOT NULL,
            realized_pnl_usd REAL,
            realized_pnl_pct REAL,
            
            mfe_pct REAL,
            mae_pct REAL,
            
            regime_at_entry TEXT,
            regime_at_exit TEXT,
            
            health_score_at_exit INTEGER,
            hold_duration_sec REAL,
            
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    
    # exit_decisions: Every decision made, for forensic analysis
    # Each row is a snapshot of ExitBot's reasoning at a point in time
    conn.execute("""
        CREATE TABLE IF NOT EXISTS exit_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            run_id TEXT NOT NULL,
            position_key TEXT NOT NULL,
            
            action TEXT NOT NULL,
            health_score INTEGER,
            confidence REAL,
            reason TEXT,
            
            current_price REAL,
            unrealized_pnl_pct REAL,
            mfe_pct REAL,
            mae_pct REAL,
            
            trailing_stop_pct REAL,
            hard_stop_pct REAL,
            time_in_trade_sec REAL,
            
            regime TEXT,
            vwap_posture TEXT,
            
            triggers_json TEXT,
            
            FOREIGN KEY(position_key) REFERENCES exit_trades(position_key)
        )
    """)
    
    # exit_options_context: Greeks and IV snapshots for options positions
    # Separate table to avoid cluttering exit_trades with option-specific fields
    conn.execute("""
        CREATE TABLE IF NOT EXISTS exit_options_context (
            position_key TEXT PRIMARY KEY,
            underlying TEXT,
            expiry TEXT,
            strike REAL,
            right TEXT,
            multiplier INTEGER DEFAULT 100,
            
            iv_entry REAL,
            iv_exit REAL,
            iv_rank_entry REAL,
            iv_rank_exit REAL,
            
            delta_entry REAL,
            delta_exit REAL,
            gamma_entry REAL,
            gamma_exit REAL,
            theta_entry REAL,
            theta_exit REAL,
            vega_entry REAL,
            vega_exit REAL,
            
            dte_at_entry INTEGER,
            dte_at_exit INTEGER,
            
            FOREIGN KEY(position_key) REFERENCES exit_trades(position_key)
        )
    """)
    
    # Create indexes for common query patterns
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_exit_trades_symbol 
        ON exit_trades(symbol)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_exit_trades_bot_id 
        ON exit_trades(bot_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_exit_trades_entry_ts 
        ON exit_trades(entry_ts)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_exit_decisions_position_key 
        ON exit_decisions(position_key)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_exit_decisions_run_id 
        ON exit_decisions(run_id)
    """)

    _safe_commit(conn)
    
    # Register shutdown handlers for graceful exit
    register_shutdown_handlers()
    
    # Start periodic WAL checkpoint (every 5 minutes)
    start_periodic_checkpoint()
    
    logger.log("state_store_ready", {
        "path": _db_path, 
        "exitbot_tables": True,
        "periodic_checkpoint": True,
        "shutdown_handlers": True
    })


def get_state(key: str, default: Any = None) -> Any:
    """
    Get a state value. Thread-safe via thread-local connections + WAL mode.
    """
    try:
        conn = _get_connection()
        cursor = conn.execute("SELECT value FROM state WHERE key = ?", (key,))
        row = cursor.fetchone()
        if row:
            return json.loads(row["value"])
        return default
    except Exception as e:
        get_logger().error(f"State get error: {e}", key=key)
        return default


def set_state(key: str, value: Any) -> None:
    """
    Set a state value. Thread-safe via thread-local connections + WAL mode.
    """
    if not isinstance(key, str) or not key.strip():
        raise ValueError("State key must be a non-empty string")

    try:
        conn = _get_connection()
        json_value = json.dumps(value, default=str)
        now = datetime.utcnow().isoformat() + "Z"
        conn.execute("""
            INSERT OR REPLACE INTO state (key, value, updated_at)
            VALUES (?, ?, ?)
        """, (key, json_value, now))
        _safe_commit(conn)
    except (TypeError, ValueError) as e:
        get_logger().error(f"State serialization error: {e}", key=key)
        raise ValueError(f"Cannot serialize value for key '{key}': {e}")
    except Exception as e:
        get_logger().error(f"State set error: {e}", key=key)
        raise


def delete_state(key: str) -> bool:
    """Delete a state value. Thread-safe via thread-local connections."""
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM state WHERE key = ?", (key,))
        _safe_commit(conn)
        get_logger().log("state_deleted", {"key": key})
        return True
    except Exception as e:
        get_logger().error(f"Failed to delete state key {key}: {e}")
        return False


def get_all_state() -> Dict[str, Any]:
    """Get all state entries as a dictionary."""
    try:
        conn = _get_connection()
        cursor = conn.execute("SELECT key, value FROM state")
        result = {}
        for row in cursor.fetchall():
            try:
                result[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                result[row["key"]] = row["value"]
        return result
    except Exception as e:
        get_logger().error(f"Failed to get all state: {e}")
        return {}


def get_keys_by_prefix(prefix: str) -> list:
    """Get all state keys that start with a given prefix."""
    try:
        conn = _get_connection()
        cursor = conn.execute("SELECT key FROM state WHERE key LIKE ?", (prefix + "%",))
        return [row["key"] for row in cursor.fetchall()]
    except Exception as e:
        get_logger().error(f"Failed to get keys by prefix {prefix}: {e}")
        return []


def delete_keys_by_prefix(prefix: str) -> int:
    """Delete all state keys that start with a given prefix."""
    try:
        conn = _get_connection()
        cursor = conn.execute("DELETE FROM state WHERE key LIKE ?", (prefix + "%",))
        _safe_commit(conn)
        deleted = cursor.rowcount
        get_logger().log("state_bulk_deleted", {"prefix": prefix, "count": deleted})
        return deleted
    except Exception as e:
        get_logger().error(f"Failed to delete keys by prefix {prefix}: {e}")
        return 0


def atomic_increment(key: str, max_value: Optional[int] = None) -> tuple:
    """
    Atomically check-and-increment a counter using SQLite IMMEDIATE transaction.
    
    Returns (success, new_value) where:
    - success=True if increment happened (was under max_value or no limit)
    - success=False if at or above max_value (no increment performed)
    - new_value is the value after any increment
    
    Uses BEGIN IMMEDIATE for true database-level atomicity across threads.
    """
    try:
        conn = _get_connection()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = conn.execute("SELECT value FROM state WHERE key = ?", (key,))
            row = cursor.fetchone()
            current = json.loads(row["value"]) if row else 0
            
            if max_value is not None and current >= max_value:
                conn.execute("ROLLBACK")
                return (False, current)
            
            new_value = current + 1
            now = datetime.utcnow().isoformat() + "Z"
            conn.execute("""
                INSERT OR REPLACE INTO state (key, value, updated_at)
                VALUES (?, ?, ?)
            """, (key, json.dumps(new_value), now))
            conn.execute("COMMIT")
            return (True, new_value)
        except:
            conn.execute("ROLLBACK")
            raise
    except Exception as e:
        get_logger().error(f"Atomic increment error: {e}", key=key)
        return (False, -1)


def generate_client_order_id(bot_id: str, symbol: str, signal_id: str) -> str:
    """Generate deterministic client_order_id"""
    from datetime import datetime
    day_key = datetime.utcnow().strftime("%Y%m%d")
    return f"{bot_id}:{symbol}:{day_key}:{signal_id}"


def is_order_already_submitted(client_order_id: str) -> bool:
    """Check if order was already submitted."""
    try:
        conn = _get_connection()
        cursor = conn.execute("SELECT 1 FROM order_ids WHERE client_order_id = ?", (client_order_id,))
        return cursor.fetchone() is not None
    except Exception as e:
        get_logger().error(f"Order check error: {e}", client_order_id=client_order_id)
        return False


def record_order_submission(client_order_id: str, bot_id: str, symbol: str, signal_id: str, alpaca_order_id: Optional[str] = None) -> None:
    """Record order submission for idempotency."""
    from datetime import datetime
    day_key = datetime.utcnow().strftime("%Y%m%d")

    try:
        conn = _get_connection()
        conn.execute("""
            INSERT OR REPLACE INTO order_ids 
            (client_order_id, bot_id, symbol, day_key, signal_id, submitted_at, alpaca_order_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (client_order_id, bot_id, symbol, day_key, signal_id, 
              datetime.utcnow().isoformat() + "Z", alpaca_order_id))
        _safe_commit(conn)
    except Exception as e:
        get_logger().error(f"Order record error: {e}", client_order_id=client_order_id)


def get_last_trade_timestamp(bot_id: str, symbol: str) -> float:
    """Get last trade timestamp for cooldown enforcement"""
    key = f"cooldown:{bot_id}:{symbol}"
    return get_state(key, 0.0)


def set_last_trade_timestamp(bot_id: str, symbol: str, timestamp: float) -> None:
    """Set last trade timestamp for cooldown enforcement"""
    key = f"cooldown:{bot_id}:{symbol}"
    set_state(key, timestamp)


def get_all_states() -> Dict[str, Any]:
    """Get all state key-value pairs"""
    try:
        conn = _get_connection()
        cursor = conn.execute("SELECT key, value FROM state")
        result = {}
        for row in cursor.fetchall():
            try:
                result[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                result[row["key"]] = row["value"]
        return result
    except Exception as e:
        get_logger().error(f"Get all states error: {e}")
        return {}


def close_state_store() -> None:
    """Close the thread-local database connection for the current thread."""
    if hasattr(_thread_local, 'conn') and _thread_local.conn is not None:
        try:
            _thread_local.conn.close()
        except Exception:
            pass
        _thread_local.conn = None
        
        # Remove from registry
        thread_id = threading.get_ident()
        with _registry_lock:
            _connection_registry.pop(thread_id, None)


def close_all_connections() -> int:
    """
    Close ALL registered database connections across all threads.
    
    This should be called during graceful shutdown to ensure WAL is properly
    flushed and no file handles remain open.
    
    Returns:
        Number of connections closed
    """
    logger = get_logger()
    closed_count = 0
    
    with _registry_lock:
        for thread_id, conn in list(_connection_registry.items()):
            try:
                conn.close()
                closed_count += 1
            except Exception as e:
                if "thread" in str(e).lower():
                    logger.log("cross_thread_connection_close_skipped", {
                        "thread_id": thread_id,
                        "detail": str(e)
                    })
                    closed_count += 1
                else:
                    logger.error(f"Error closing connection for thread {thread_id}: {e}")
        
        _connection_registry.clear()
    
    # Also clear thread-local for current thread
    if hasattr(_thread_local, 'conn'):
        _thread_local.conn = None
    
    logger.log("all_connections_closed", {"count": closed_count})
    return closed_count


def clear_all_state(backup: bool = True) -> Dict[str, Any]:
    """
    Clear all state for fresh-start with a new account.
    
    Args:
        backup: If True, returns all existing state before clearing
        
    Returns:
        Dict containing backed up state (empty if backup=False)
    """
    logger = get_logger()
    backup_data = {}
    
    if backup:
        backup_data = get_all_states()
        logger.log("state_backup_created", {"keys_count": len(backup_data)})
    
    try:
        with _db_lock:
            conn = _get_connection()
            
            conn.execute("DELETE FROM state")
            conn.execute("DELETE FROM order_ids")
            _safe_commit(conn)
        
        logger.log("state_cleared_for_fresh_start", {
            "backup_created": backup,
            "keys_cleared": len(backup_data) if backup else "unknown"
        })
        
        return backup_data
        
    except Exception as e:
        logger.error(f"Failed to clear state: {e}")
        raise