"""
Dedicated Bot Threads
=====================

Provides dedicated background threads for time-sensitive bots:
- ExitBot: FAST LANE - Always-on position monitoring (2s intervals)
- CryptoBot: 24/7 crypto trading (5s intervals)
- TwentyMinBot: 06:30-06:50 pattern trading (5s intervals, sleeps otherwise)
- BounceBot: Market hours mean-reversion (5s intervals, sleeps outside)
- OptionsBot (core): 06:40-12:30 options strategies (5s intervals)
- OptionsBot (0dte): 06:45-10:00 same-day expiry (5s intervals)

Each thread runs its bot independently from the main loop, providing:
- Faster reaction time for time-critical operations
- No blocking between bots
- Window-aware sleep (threads sleep when outside active hours)

Thread Safety Notes:
- Bot runners use shared Alpaca client (thread-safe: uses connection pooling)
- State read/writes are atomic (thread-safe via get_state/set_state)
- Each bot instance manages its own cooldowns and order tracking
- Note: ExitBot runs in BOTH dedicated thread (5s) AND main loop (30s):
  - Dedicated thread: Fast position monitoring and stop-out response
  - Main loop: Halt status check required for orchestrator flow control
  - This is intentional: ExitBot.run() is idempotent, double execution is safe

Usage:
    manager = get_dedicated_thread_manager()
    manager.start_all()
    
    # Later...
    manager.shutdown()
"""

from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time
from enum import Enum
import threading
import time
import traceback

from ..core.logging import get_logger
from ..core.config import load_bots_config, load_settings
from ..core.state import get_state, set_state
from ..core.halt import get_halt_manager
from ..core.clock import get_market_clock


class ThreadStatus(Enum):
    """Status of a dedicated thread."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    SLEEPING = "sleeping"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class ThreadStats:
    """Statistics for a dedicated thread."""
    name: str
    status: ThreadStatus = ThreadStatus.STOPPED
    loop_count: int = 0
    last_run: Optional[datetime] = None
    last_error: Optional[str] = None
    errors_count: int = 0
    active_window_start: Optional[str] = None
    active_window_end: Optional[str] = None
    is_24_7: bool = False


@dataclass
class BotThreadConfig:
    """Configuration for a dedicated bot thread."""
    name: str
    bot_id: str
    interval_seconds: int = 5
    active_start: Optional[str] = None  # e.g., "06:30" (None = always active)
    active_end: Optional[str] = None    # e.g., "06:50" (None = always active)
    sleep_check_interval: int = 30       # How often to check if window opened
    enabled: bool = True


class DedicatedBotThread:
    """
    A dedicated thread for running a specific bot.
    
    Features:
    - Configurable active window (sleeps outside window)
    - Error isolation (errors don't crash the thread)
    - Health monitoring and statistics
    - Graceful shutdown
    """
    
    def __init__(
        self,
        config: BotThreadConfig,
        run_func: Callable[[], Any],
        logger=None
    ):
        self._config = config
        self._run_func = run_func
        self._logger = logger or get_logger()
        
        self._thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()
        self._stats = ThreadStats(
            name=config.name,
            active_window_start=config.active_start,
            active_window_end=config.active_end,
            is_24_7=(config.active_start is None and config.active_end is None)
        )
        self._stats_lock = threading.Lock()
    
    def _is_in_active_window(self) -> bool:
        """Check if current time is within the active window."""
        if self._config.active_start is None or self._config.active_end is None:
            return True  # 24/7 thread

        try:
            clock = get_market_clock()
            now = clock.now()
            current_time = now.strftime("%H:%M")

            # Don't run market-hours bots on weekends (Saturday=5, Sunday=6)
            if now.weekday() >= 5:
                return False

            start = self._config.active_start
            end = self._config.active_end

            # Simple string comparison works for HH:MM format
            if start <= end:
                return start <= current_time <= end
            else:
                # Window spans midnight (e.g., "22:00" to "06:00")
                return current_time >= start or current_time <= end

        except Exception:
            # On error, assume active (fail-open for bot execution)
            return True
    
    def _minutes_until_window(self) -> int:
        """Calculate minutes until active window opens."""
        if self._config.active_start is None:
            return 0
        
        try:
            clock = get_market_clock()
            now = clock.now()
            
            # Parse window start time
            start_h, start_m = map(int, self._config.active_start.split(":"))
            window_start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
            
            # If window start already passed today, next window is tomorrow
            if now >= window_start:
                # Check if we're in the window (end time)
                if self._config.active_end:
                    end_h, end_m = map(int, self._config.active_end.split(":"))
                    window_end = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
                    if now <= window_end:
                        return 0  # Currently in window
                # Window passed, calculate time until tomorrow
                from datetime import timedelta
                window_start = window_start + timedelta(days=1)
            
            delta = window_start - now
            return max(1, int(delta.total_seconds() / 60))
            
        except Exception:
            return 60  # Default to 1 hour
    
    def _update_status(self, status: ThreadStatus, error: str = None):
        """Update thread status."""
        with self._stats_lock:
            self._stats.status = status
            if error:
                self._stats.last_error = error
                self._stats.errors_count += 1
    
    def _thread_loop(self):
        """Main thread loop."""
        self._update_status(ThreadStatus.RUNNING)
        
        self._logger.log("dedicated_thread_started", {
            "name": self._config.name,
            "bot_id": self._config.bot_id,
            "interval": self._config.interval_seconds,
            "active_start": self._config.active_start,
            "active_end": self._config.active_end,
            "is_24_7": self._stats.is_24_7
        })
        
        while not self._shutdown_event.is_set():
            try:
                # Check if in active window
                if not self._is_in_active_window():
                    self._update_status(ThreadStatus.SLEEPING)
                    
                    minutes_until = self._minutes_until_window()
                    self._logger.log("dedicated_thread_sleeping", {
                        "name": self._config.name,
                        "bot_id": self._config.bot_id,
                        "minutes_until_window": minutes_until,
                        "active_start": self._config.active_start
                    })
                    
                    # Sleep in small intervals to check for shutdown
                    sleep_time = min(self._config.sleep_check_interval, minutes_until * 60)
                    sleep_remaining = sleep_time
                    while sleep_remaining > 0 and not self._shutdown_event.is_set():
                        time.sleep(min(1.0, sleep_remaining))
                        sleep_remaining -= 1.0
                    
                    continue
                
                # In active window - run the bot
                self._update_status(ThreadStatus.RUNNING)
                loop_start = time.time()
                
                # Check halt status
                halt = get_halt_manager()
                if halt.is_halted():
                    self._logger.log("dedicated_thread_halted", {
                        "name": self._config.name,
                        "bot_id": self._config.bot_id,
                        "reason": halt.get_status().reason if halt.get_status() else "unknown"
                    })
                    # Sleep briefly and re-check
                    self._shutdown_event.wait(self._config.interval_seconds)
                    continue
                
                # Execute bot
                try:
                    result = self._run_func()
                    
                    with self._stats_lock:
                        self._stats.loop_count += 1
                        self._stats.last_run = datetime.now()
                    
                except Exception as e:
                    error_msg = str(e)
                    self._update_status(ThreadStatus.ERROR, error_msg)
                    
                    self._logger.log("dedicated_thread_bot_error", {
                        "name": self._config.name,
                        "bot_id": self._config.bot_id,
                        "error": error_msg,
                        "traceback": traceback.format_exc()[:500]
                    })
                    
                    # Continue despite error (don't crash thread)
                    self._update_status(ThreadStatus.RUNNING)
                
                # Sleep for interval
                elapsed = time.time() - loop_start
                sleep_time = max(0, self._config.interval_seconds - elapsed)
                
                if sleep_time > 0:
                    self._shutdown_event.wait(sleep_time)
                    
            except Exception as e:
                # Catastrophic error in thread loop itself
                self._logger.error(f"Dedicated thread {self._config.name} loop error: {e}")
                self._update_status(ThreadStatus.ERROR, str(e))
                
                # Brief sleep before retrying
                self._shutdown_event.wait(5)
        
        self._update_status(ThreadStatus.STOPPED)
        self._logger.log("dedicated_thread_stopped", {
            "name": self._config.name,
            "bot_id": self._config.bot_id,
            "loop_count": self._stats.loop_count
        })
    
    def start(self):
        """Start the dedicated thread."""
        if self._thread is not None and self._thread.is_alive():
            return  # Already running
        
        if not self._config.enabled:
            self._logger.log("dedicated_thread_disabled", {
                "name": self._config.name,
                "bot_id": self._config.bot_id
            })
            return
        
        self._shutdown_event.clear()
        self._update_status(ThreadStatus.STARTING)
        
        self._thread = threading.Thread(
            target=self._thread_loop,
            name=f"Bot_{self._config.name}",
            daemon=True
        )
        self._thread.start()
    
    def stop(self, timeout: float = 10.0):
        """Stop the dedicated thread."""
        if self._thread is None or not self._thread.is_alive():
            return
        
        self._update_status(ThreadStatus.STOPPING)
        self._shutdown_event.set()
        self._thread.join(timeout=timeout)
        
        if self._thread.is_alive():
            self._logger.log("dedicated_thread_stop_timeout", {
                "name": self._config.name,
                "bot_id": self._config.bot_id
            })
    
    def get_stats(self) -> Dict[str, Any]:
        """Get thread statistics."""
        with self._stats_lock:
            return {
                "name": self._stats.name,
                "status": self._stats.status.value,
                "loop_count": self._stats.loop_count,
                "last_run": self._stats.last_run.isoformat() if self._stats.last_run else None,
                "last_error": self._stats.last_error,
                "errors_count": self._stats.errors_count,
                "active_window": f"{self._stats.active_window_start}-{self._stats.active_window_end}" if self._stats.active_window_start else "24/7",
                "is_24_7": self._stats.is_24_7
            }
    
    def is_alive(self) -> bool:
        """Check if thread is alive."""
        return self._thread is not None and self._thread.is_alive()


class DedicatedThreadManager:
    """
    Manages all dedicated bot threads.
    
    Provides centralized control for starting, stopping, and monitoring
    dedicated threads for time-sensitive bots.
    """
    
    _instance: Optional['DedicatedThreadManager'] = None
    _lock = threading.Lock()
    
    def __init__(self):
        self._logger = get_logger()
        self._threads: Dict[str, DedicatedBotThread] = {}
        self._initialized = False
    
    def initialize(self):
        """Initialize dedicated threads for all configured bots."""
        if self._initialized:
            return
        
        self._logger.log("dedicated_threads_initializing", {})
        
        # Load configs
        bots_config = load_bots_config()
        settings = load_settings()
        
        # Get market hours from settings
        market_hours = settings.get("market_hours", {})
        market_open = market_hours.get("market_open", "06:30")
        market_close = market_hours.get("market_close", "13:00")
        
        # Define thread configurations
        thread_configs = [
            # ExitBot - FAST LANE (2s interval for maximum speed)
            # This is the highest priority thread for stop-loss and exit management
            BotThreadConfig(
                name="ExitBot",
                bot_id="exitbot",
                interval_seconds=2,  # Fast lane: 2s for fastest stop-loss response
                active_start=None,  # 24/7
                active_end=None,
                enabled=bots_config.get("exitbot", {}).get("enabled", True)
            ),
            # CryptoBot - 24/7
            BotThreadConfig(
                name="CryptoBot",
                bot_id="crypto_core",
                interval_seconds=5,
                active_start=None,  # 24/7
                active_end=None,
                enabled=bots_config.get("cryptobot", {}).get("enabled", True)
            ),
            # TwentyMinBot - reads session.trade_start and session.trade_end from config
            BotThreadConfig(
                name="TwentyMinBot",
                bot_id="twentymin_core",
                interval_seconds=5,
                active_start=bots_config.get("twentyminute_bot", {}).get("session", {}).get("trade_start", "06:00"),
                active_end=bots_config.get("twentyminute_bot", {}).get("session", {}).get("trade_end", "09:30"),
                enabled=bots_config.get("twentyminute_bot", {}).get("enabled", True)
            ),
            # BounceBot - overnight crypto window (reads from bouncebot config)
            BotThreadConfig(
                name="BounceBot",
                bot_id="bounce_core",
                interval_seconds=bots_config.get("bouncebot", {}).get("session", {}).get("poll_interval_seconds", 5),
                active_start=None,  # 24/7 — BounceBot handles its own window check internally
                active_end=None,
                enabled=bots_config.get("bouncebot", {}).get("enabled", True)
            ),
            # OptionsBot Core - 06:40-12:30
            BotThreadConfig(
                name="OptionsCore",
                bot_id="opt_core",
                interval_seconds=5,
                active_start=bots_config.get("options_bot", {}).get("trade_window_start", "06:40"),
                active_end=bots_config.get("options_bot", {}).get("trade_window_end", "12:30"),
                enabled=bots_config.get("options_bot", {}).get("enabled", True)
            ),
            # OptionsBot 0DTE - 06:45-10:00
            BotThreadConfig(
                name="Options0DTE",
                bot_id="opt_0dte",
                interval_seconds=5,
                active_start=bots_config.get("options_bot_0dte", {}).get("trade_window_start", "06:45"),
                active_end=bots_config.get("options_bot_0dte", {}).get("trade_window_end", "10:00"),
                enabled=bots_config.get("options_bot_0dte", {}).get("enabled", True)
            ),
            # HailMary Bot — Standalone OTM options, same window as OptionsBot Core
            BotThreadConfig(
                name="HailMary",
                bot_id="hm_core",
                interval_seconds=5,
                active_start=bots_config.get("hailmary_bot", {}).get("trade_window_start", "06:40"),
                active_end=bots_config.get("hailmary_bot", {}).get("trade_window_end", "12:30"),
                enabled=bots_config.get("hailmary_bot", {}).get("enabled", False)
            ),
        ]
        
        # Create threads (run functions will be set when starting)
        for config in thread_configs:
            self._threads[config.bot_id] = DedicatedBotThread(
                config=config,
                run_func=lambda: None,  # Placeholder, set in start_all
                logger=self._logger
            )
        
        self._initialized = True
        
        self._logger.log("dedicated_threads_initialized", {
            "thread_count": len(self._threads),
            "threads": [t.name for t in thread_configs]
        })
    
    def set_run_function(self, bot_id: str, run_func: Callable[[], Any]):
        """Set the run function for a specific bot thread."""
        if bot_id in self._threads:
            self._threads[bot_id]._run_func = run_func
    
    def start_all(self):
        """Start all dedicated threads."""
        self._logger.log("dedicated_threads_starting", {})
        
        # Validate run functions are set before starting
        for bot_id, thread in self._threads.items():
            if not thread._config.enabled:
                continue
            # Check if run_func is still the default placeholder
            run_code = getattr(thread._run_func, '__code__', None)
            placeholder_code = (lambda: None).__code__
            if run_code and run_code.co_code == placeholder_code.co_code:
                self._logger.log("dedicated_thread_no_run_func", {
                    "bot_id": bot_id,
                    "warning": "Run function not set, thread will not execute bot"
                })
        
        for bot_id, thread in self._threads.items():
            try:
                thread.start()
            except Exception as e:
                self._logger.error(f"Failed to start thread {bot_id}: {e}")
        
        self._logger.log("dedicated_threads_started", {
            "active_threads": [k for k, v in self._threads.items() if v.is_alive()]
        })
    
    def stop_all(self, timeout: float = 10.0):
        """Stop all dedicated threads."""
        self._logger.log("dedicated_threads_stopping", {})
        
        for bot_id, thread in self._threads.items():
            try:
                thread.stop(timeout=timeout)
            except Exception as e:
                self._logger.error(f"Failed to stop thread {bot_id}: {e}")
        
        self._logger.log("dedicated_threads_stopped", {})
    
    def get_all_stats(self) -> Dict[str, Any]:
        """Get statistics for all threads."""
        stats = {}
        for bot_id, thread in self._threads.items():
            stats[bot_id] = thread.get_stats()
        return stats
    
    def log_stats(self):
        """Log statistics for all threads."""
        stats = self.get_all_stats()
        self._logger.log("dedicated_threads_stats", stats)
    
    def is_healthy(self) -> bool:
        """Check if all threads are healthy."""
        for bot_id, thread in self._threads.items():
            if thread._config.enabled and not thread.is_alive():
                return False
        return True
    
    def get_thread(self, bot_id: str) -> Optional[DedicatedBotThread]:
        """Get a specific thread by bot ID."""
        return self._threads.get(bot_id)


# Singleton instance
_thread_manager: Optional[DedicatedThreadManager] = None
_manager_lock = threading.Lock()


def get_dedicated_thread_manager() -> DedicatedThreadManager:
    """Get or create the dedicated thread manager singleton."""
    global _thread_manager
    
    if _thread_manager is None:
        with _manager_lock:
            if _thread_manager is None:
                _thread_manager = DedicatedThreadManager()
                _thread_manager.initialize()
    
    return _thread_manager


def shutdown_dedicated_threads():
    """Shutdown all dedicated threads."""
    global _thread_manager
    
    if _thread_manager is not None:
        _thread_manager.stop_all()
        _thread_manager = None
