"""
=============================================================================
JSONL File Logger for Trading Events
=============================================================================
Provides structured logging to JSONL files with optional console output.
In quiet mode, only critical errors are printed to console.
In verbose mode (default), all events are printed to console.

Features:
- Structured JSONL output for analysis
- Automatic log rotation (size-based and time-based)
- Configurable retention policy
=============================================================================
"""
import os
import json
import gzip
import logging
import sys
import glob as glob_module
import shutil
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, List

# Global logger instance
_jsonl_logger: Optional["JsonlLogger"] = None

# Global quiet mode flag - controls console output
_quiet_mode: bool = False
_suppress_all_console: bool = False

# Log rotation settings (configurable)
LOG_MAX_SIZE_MB = 10          # Rotate when file exceeds 10MB
LOG_MAX_FILES = 7             # Keep 7 rotated files (1 week at daily rotation)
LOG_ROTATION_CHECK_INTERVAL = 100  # Check rotation every N log entries
LOG_ARCHIVE_DIR = "./logs/archive"  # Compressed archive directory
LOG_ARCHIVE_RETENTION_DAYS = 90    # Keep archived logs for 90 days


def set_logger_suppress_console(suppress: bool) -> None:
    """
    Suppress ALL console output from the logger.
    
    Used for in-place dashboard mode where any console output
    would break the display.
    
    Args:
        suppress: If True, no logs will be printed to console (still written to file)
    """
    global _suppress_all_console
    _suppress_all_console = suppress


def set_logger_quiet_mode(quiet: bool) -> None:
    """
    Set the global quiet mode for the logger.
    
    Args:
        quiet: If True, suppress non-critical console output
    """
    global _quiet_mode
    _quiet_mode = quiet


class JsonlLogger:
    """
    JSONL structured logger with optional console output and log rotation.
    
    Always writes to JSONL file for analysis.
    Console output controlled by global quiet mode:
    - Quiet: Only errors and halt events print to console
    - Verbose: All events print to console
    
    Log Rotation:
    - Size-based: Rotates when file exceeds LOG_MAX_SIZE_MB
    - Retention: Keeps LOG_MAX_FILES rotated files
    """
    
    def __init__(self, log_path: str = "./logs/app.jsonl"):
        self.log_path = log_path
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        self._recent_logs = []  # Track recent logs for execution service
        self._log_count = 0     # Counter for rotation check interval
        self._setup_console_logging()
        
        # Perform initial rotation check on startup
        self._check_rotation()
    
    def _setup_console_logging(self) -> None:
        """Configure console logging handler"""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            handlers=[logging.StreamHandler(sys.stdout)]
        )
        self._console = logging.getLogger("trading_hydra")
    
    def _check_rotation(self) -> None:
        """
        Check if log rotation is needed and perform if necessary.
        
        Rotation triggers:
        1. File size exceeds LOG_MAX_SIZE_MB
        
        After rotation:
        - Current file renamed to app.jsonl.1, app.jsonl.2, etc.
        - Old files beyond LOG_MAX_FILES are deleted
        """
        if not os.path.exists(self.log_path):
            return
        
        try:
            file_size_mb = os.path.getsize(self.log_path) / (1024 * 1024)
            
            if file_size_mb >= LOG_MAX_SIZE_MB:
                self._rotate_logs()
        except Exception:
            pass  # Don't break logging on rotation errors
    
    def _archive_log(self, log_path: str) -> None:
        """
        Compress a log file into the archive directory with date stamp.
        Archive format: logs/archive/app_YYYYMMDD_HHMMSS.jsonl.gz
        """
        try:
            os.makedirs(LOG_ARCHIVE_DIR, exist_ok=True)
            
            file_mtime = os.path.getmtime(log_path)
            file_date = datetime.fromtimestamp(file_mtime)
            date_str = file_date.strftime("%Y%m%d_%H%M%S")
            
            base = os.path.splitext(os.path.basename(self.log_path))[0]
            archive_name = f"{base}_{date_str}.jsonl.gz"
            archive_path = os.path.join(LOG_ARCHIVE_DIR, archive_name)
            
            if os.path.exists(archive_path):
                archive_name = f"{base}_{date_str}_{os.getpid()}.jsonl.gz"
                archive_path = os.path.join(LOG_ARCHIVE_DIR, archive_name)
            
            with open(log_path, 'rb') as f_in:
                with gzip.open(archive_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            
            os.remove(log_path)
        except Exception:
            try:
                os.remove(log_path)
            except Exception:
                pass
    
    def _cleanup_old_archives(self) -> None:
        """
        Remove archived logs older than LOG_ARCHIVE_RETENTION_DAYS.
        """
        try:
            if not os.path.exists(LOG_ARCHIVE_DIR):
                return
            
            cutoff = datetime.now() - timedelta(days=LOG_ARCHIVE_RETENTION_DAYS)
            cutoff_ts = cutoff.timestamp()
            
            for f in glob_module.glob(os.path.join(LOG_ARCHIVE_DIR, "*.gz")):
                try:
                    if os.path.getmtime(f) < cutoff_ts:
                        os.remove(f)
                except Exception:
                    pass
        except Exception:
            pass
    
    def _rotate_logs(self) -> None:
        """
        Rotate log files: current -> .1, .1 -> .2, etc.
        Files beyond LOG_MAX_FILES are compressed into logs/archive/ instead of deleted.
        Old archives beyond LOG_ARCHIVE_RETENTION_DAYS are cleaned up.
        """
        log_dir = os.path.dirname(self.log_path)
        base_name = os.path.basename(self.log_path)
        
        pattern = os.path.join(log_dir, f"{base_name}.*")
        existing = sorted(glob_module.glob(pattern), reverse=True)
        
        for old_path in existing:
            try:
                suffix = old_path.rsplit('.', 1)[-1]
                if suffix.isdigit():
                    num = int(suffix)
                    new_num = num + 1
                    new_path = f"{self.log_path}.{new_num}"
                    
                    if new_num > LOG_MAX_FILES:
                        self._archive_log(old_path)
                    else:
                        shutil.move(old_path, new_path)
            except Exception:
                pass
        
        try:
            shutil.move(self.log_path, f"{self.log_path}.1")
        except Exception:
            pass
        
        self._cleanup_old_archives()
    
    def _maybe_rotate(self) -> None:
        """Check rotation periodically (not every log entry for performance)"""
        self._log_count += 1
        if self._log_count >= LOG_ROTATION_CHECK_INTERVAL:
            self._log_count = 0
            self._check_rotation()
    
    def log(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        """
        Log an event to JSONL file and optionally to console.
        
        Args:
            event_type: Type of event (e.g., 'loop_start', 'trade_executed')
            data: Additional data to log with the event
        """
        if data is None:
            data = {}
        
        entry = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "event": event_type,
            **data
        }
        
        # Track recent logs (keep last 100)
        self._recent_logs.append(event_type)
        if len(self._recent_logs) > 100:
            self._recent_logs.pop(0)
        
        # Always write to JSONL file
        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
            
            # Check for log rotation periodically
            self._maybe_rotate()
        except Exception as e:
            self._console.error(f"Failed to write JSONL: {e}")
        
        # In suppress_all mode (for in-place dashboard), skip all console output
        if _suppress_all_console:
            return
        
        # In quiet mode, only print errors and halt events to console
        # In verbose mode, print everything
        is_critical = "halt" in event_type or "error" in event_type or "fail" in event_type
        
        if _quiet_mode:
            # Quiet mode: only critical events go to console
            if is_critical:
                level = logging.WARNING
                self._console.log(level, f"[{event_type}] {json.dumps(data)}")
        else:
            # Verbose mode: all events go to console
            level = logging.WARNING if is_critical else logging.INFO
            self._console.log(level, f"[{event_type}] {json.dumps(data)}")
    
    def info(self, msg: str, **kwargs: Any) -> None:
        """Log an info message"""
        self.log("info", {"message": msg, **kwargs})
    
    def warn(self, msg: str, **kwargs: Any) -> None:
        """Log a warning message"""
        self.log("warn", {"message": msg, **kwargs})
    
    def error(self, msg: str, **kwargs: Any) -> None:
        """Log an error message (always prints to console)"""
        self.log("error", {"message": msg, **kwargs})


def get_logger() -> JsonlLogger:
    """
    Get the global JSONL logger instance.
    
    Returns:
        The singleton JsonlLogger instance
    """
    global _jsonl_logger
    if _jsonl_logger is None:
        _jsonl_logger = JsonlLogger()
    return _jsonl_logger
