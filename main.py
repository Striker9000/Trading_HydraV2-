#!/usr/bin/env python3
"""
=============================================================================
Trading Hydra - Main Entry Point
=============================================================================
This is the primary entry point for the Trading Hydra automated trading system.
It runs the trading loop with config-driven intervals and provides graceful
shutdown handling for safe operation.

Features:
- Config-driven loop interval from settings.yaml
- Graceful SIGINT/SIGTERM handling for safe shutdown
- Flask dashboard runs in background thread on port 5000
- Paper trading by default (controlled by ALPACA_PAPER env var)
- Comprehensive logging to JSONL format
- Human-readable console output with multiple display modes

Usage:
    python main.py            # Clean dashboard (default, recommended)
    python main.py --verbose  # Full dashboard + all JSONL logs
    python main.py --quiet    # Minimal one-line summary per loop

The system will:
1. Start the Flask dashboard on http://0.0.0.0:5000
2. Run the 5-step trading loop at configured intervals
3. Handle shutdown signals gracefully, closing all positions if configured
=============================================================================
"""

# =============================================================================
# ENVIRONMENT LOADING - Must happen FIRST before any other imports
# =============================================================================
# Load .env file if present (single source of truth for all credentials)
# This must be at the very top so all subsequent imports see the env vars

import os
import tempfile
import platform
from pathlib import Path

# Find .env file - check current dir, parent, and script location
def _find_and_load_dotenv():
    """Load .env file from the project root."""
    from dotenv import load_dotenv
    
    # Possible locations for .env file
    script_dir = Path(__file__).parent.resolve()
    possible_paths = [
        Path.cwd() / ".env",           # Current working directory
        script_dir / ".env",           # Same dir as main.py
        script_dir.parent / ".env",    # Parent directory
    ]
    
    for env_path in possible_paths:
        if env_path.exists():
            load_dotenv(env_path, override=False)
            print(f"[ENV] Loaded credentials from: {env_path}")
            return str(env_path)
    
    # No .env found - that's okay, will use environment variables
    print("[ENV] No .env file found - using system environment variables")
    return None

_loaded_env_path = _find_and_load_dotenv()

# =============================================================================
# STANDARD IMPORTS - Now safe to import (env vars are loaded)
# =============================================================================

import sys
import time
import signal
import threading
import argparse
from datetime import datetime

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from trading_hydra.orchestrator import TradingOrchestrator
from trading_hydra.core.logging import get_logger, set_logger_quiet_mode, set_logger_suppress_console
from trading_hydra.core.config import load_settings
from trading_hydra.core.console import get_console_formatter, set_quiet_mode, set_event_mode
from trading_hydra.core.clock import get_market_clock


# =============================================================================
# GLOBAL STATE - Shared across signal handlers and main loop
# =============================================================================

# Flag to signal graceful shutdown
_shutdown_requested = False

# Lock for thread-safe shutdown flag access
_shutdown_lock = threading.Lock()

# Reference to dashboard thread for cleanup
_dashboard_thread = None


# =============================================================================
# SIGNAL HANDLERS - Graceful shutdown on SIGINT/SIGTERM
# =============================================================================

def _signal_handler(signum, frame):
    """
    Handle shutdown signals (SIGINT, SIGTERM) gracefully.
    
    Sets the global shutdown flag which causes the main loop to exit cleanly
    after completing the current iteration.
    
    Args:
        signum: Signal number received
        frame: Current stack frame (unused)
    """
    global _shutdown_requested
    
    signal_name = signal.Signals(signum).name
    logger = get_logger()
    
    with _shutdown_lock:
        if _shutdown_requested:
            # Second signal - force immediate exit
            logger.log("shutdown_forced", {"signal": signal_name})
            print(f"\n[SHUTDOWN] Forced exit on second {signal_name}")
            sys.exit(1)
        
        _shutdown_requested = True
        logger.log("shutdown_requested", {"signal": signal_name})
        print(f"\n[SHUTDOWN] {signal_name} received - completing current loop then exiting...")


def _is_shutdown_requested() -> bool:
    """
    Thread-safe check if shutdown has been requested.
    
    Returns:
        True if shutdown signal received, False otherwise
    """
    with _shutdown_lock:
        return _shutdown_requested


# =============================================================================
# DASHBOARD THREAD - Flask web interface runs in background
# =============================================================================

# Global quiet mode for dashboard thread
_dashboard_quiet_mode = False


def _start_dashboard():
    """
    Start the Flask dashboard in a background thread.
    
    The dashboard provides:
    - Real-time equity and P&L monitoring
    - Bot status and enable/disable controls
    - Manual trading interface
    - Configuration editor
    - Log viewer
    
    Runs on http://0.0.0.0:5000
    """
    global _dashboard_quiet_mode
    
    try:
        from trading_hydra.dashboard import create_app
        import logging as flask_logging
        
        app = create_app()
        
        # In quiet mode, suppress Flask/Werkzeug access logs
        if _dashboard_quiet_mode:
            flask_logging.getLogger('werkzeug').setLevel(flask_logging.ERROR)
        
        # Run Flask with threading enabled, reloader disabled
        # use_reloader=False is critical - reloader spawns child processes
        # which breaks our signal handling
        app.run(
            host='0.0.0.0',
            port=5000,
            threaded=True,
            use_reloader=False,
            debug=False
        )
    except ImportError as e:
        print(f"[WARNING] Dashboard not available: {e}")
        print("[INFO] Trading engine will run without web interface")
    except Exception as e:
        print(f"[ERROR] Dashboard failed to start: {e}")


# =============================================================================
# MAIN TRADING LOOP - Core execution engine
# =============================================================================

def run_trading_loop(quiet: bool = False, verbose: bool = False, inplace: bool = False):
    """
    Run the main trading loop with config-driven intervals.
    
    Args:
        quiet: If True, show minimal one-line output per loop
        verbose: If True, show full dashboard + all JSONL console logs
        inplace: If True, clear screen and redraw dashboard each loop
    
    Output Modes:
    - Clean (default): Full dashboard, no log noise (recommended for monitoring)
    - Verbose (--verbose): Full dashboard + all JSONL console logs
    - Quiet (--quiet): Minimal one-line summary per loop
    - In-place (--inplace): Clear screen each loop for static dashboard
    
    The loop:
    1. Loads settings to get loop_interval_seconds
    2. Initializes the TradingOrchestrator
    3. Runs the 5-step loop (Init, ExitBot, Portfolio, Execute, Finalize)
    4. Sleeps for the configured interval
    5. Repeats until shutdown signal received
    
    On shutdown, completes the current loop iteration before exiting.
    """
    global _dashboard_thread, _dashboard_quiet_mode
    
    # Determine if logs should be suppressed
    # Clean mode (default) and quiet mode both suppress logs
    # Only verbose mode shows all logs
    suppress_logs = not verbose
    
    # Set quiet mode for logger BEFORE getting logger instance
    # This suppresses verbose JSONL console output unless --verbose is used
    set_logger_quiet_mode(suppress_logs)
    _dashboard_quiet_mode = suppress_logs
    
    # In-place mode requires ALL console output suppressed to prevent display corruption
    if inplace:
        set_logger_suppress_console(True)
    
    logger = get_logger()
    
    # Load settings early for console config and other startup config
    settings = load_settings()
    
    # Read console mode from config (event, full, quiet)
    console_config = settings.get('console', {})
    config_mode = console_config.get('mode', 'full')
    config_inplace = console_config.get('inplace', False)
    
    # CLI flags override config: --quiet > config, --verbose forces full mode
    event_mode = False
    if quiet:
        # --quiet flag: minimal one-line output
        event_mode = False
    elif verbose:
        # --verbose flag: full dashboard + all logs
        event_mode = False
    elif config_mode == 'event':
        # Config says event mode: heartbeat + event blocks (recommended)
        event_mode = True
    elif config_mode == 'quiet':
        # Config says quiet mode
        quiet = True
    # else: full mode (dashboard every loop)
    
    # Apply inplace from config if not already set by CLI
    if not inplace and config_inplace:
        inplace = config_inplace
    
    # In-place mode clears screen each loop for a static dashboard view
    # Enable with --inplace or -i flag
    formatter = get_console_formatter(quiet=quiet, inplace=inplace, event_mode=event_mode)
    
    # Settings already loaded above for console config
    runner_config = settings.get('runner', {})
    loop_interval = runner_config.get('loop_interval_seconds', 5)
    
    # Log startup configuration (always show startup banner)
    # Determine output mode label
    if quiet:
        output_mode_label = "Quiet (one-line summary)"
    elif verbose:
        output_mode_label = "Verbose (dashboard + all logs)"
    elif event_mode:
        output_mode_label = "Event (heartbeat + events only - RECOMMENDED)"
    elif inplace:
        output_mode_label = "In-place (static dashboard, clears screen)"
    else:
        output_mode_label = "Full (dashboard every loop)"
    
    # Check dashboard status for banner
    dashboard_enabled = os.environ.get('ENABLE_DASHBOARD', '').strip().lower() in ('true', '1', 'yes')
    dashboard_status = "http://0.0.0.0:5000" if dashboard_enabled else "DISABLED"
    
    print("=" * 72)
    print("  TRADING HYDRA - Automated Trading System")
    print("=" * 72)
    print(f"  Loop Interval: {loop_interval} seconds")
    print(f"  Dashboard: {dashboard_status}")
    print(f"  Paper Mode: {os.environ.get('ALPACA_PAPER', 'true')}")
    print(f"  Output Mode: {output_mode_label}")
    print("=" * 72)
    
    # MERGED CONFIG DUMP: Print the "truth config" at startup
    # This is critical for operators to verify actual runtime values
    from trading_hydra.core.config import load_bots_config
    bots_config = load_bots_config()
    
    print()
    print("-" * 72)
    print("  MERGED CONFIG (runtime truth)")
    print("-" * 72)
    
    # Safety-critical settings
    print(f"  [SAFETY]")
    print(f"    fail_closed: {settings.get('safety', {}).get('fail_closed', True)}")
    print(f"    max_price_staleness: {settings.get('health', {}).get('max_price_staleness_seconds', 15)}s")
    print(f"    quote_ttl: {settings.get('caching', {}).get('quote_ttl_seconds', 15)}s")
    print(f"    global_max_daily_loss_pct: {settings.get('risk', {}).get('global_max_daily_loss_pct', 2.0)}%")
    print(f"    max_spread_pct: {settings.get('smart_execution', {}).get('max_spread_pct', 0.5)}%")
    
    # Bot status - correctly read from proper config keys
    print(f"  [BOT STATUS]")
    # Momentum: enabled if ANY momentum_bots[].enabled is true
    momentum_bots = bots_config.get("momentum_bots", [])
    momentum_enabled = any(bot.get("enabled", False) for bot in momentum_bots) if momentum_bots else False
    # Options: from optionsbot.enabled
    optionsbot = bots_config.get("optionsbot", {})
    options_enabled = optionsbot.get("enabled", False)
    # Crypto: from cryptobot.enabled
    cryptobot = bots_config.get("cryptobot", {})
    crypto_enabled = cryptobot.get("enabled", False)
    # TwentyMinute: from twentyminute_bot.enabled
    twentymin_bot = bots_config.get("twentyminute_bot", {})
    twentymin_enabled = twentymin_bot.get("enabled", False)
    
    print(f"    momentum: {'ENABLED' if momentum_enabled else 'disabled'} ({len([b for b in momentum_bots if b.get('enabled')])} bots)")
    print(f"    options: {'ENABLED' if options_enabled else 'disabled'}")
    print(f"    crypto: {'ENABLED' if crypto_enabled else 'disabled'}")
    print(f"    twentyminute: {'ENABLED' if twentymin_enabled else 'disabled'}")
    
    # Bot limits
    print(f"  [BOT LIMITS]")
    print(f"    options max_trades_per_day: {optionsbot.get('risk', {}).get('max_trades_per_day', 'N/A')}")
    print(f"    options max_concurrent: {optionsbot.get('risk', {}).get('max_concurrent_positions', 'N/A')}")
    print(f"    crypto max_trades_per_day: {cryptobot.get('risk', {}).get('max_trades_per_day', 'N/A')}")
    print(f"    crypto max_concurrent: {cryptobot.get('risk', {}).get('max_concurrent_positions', 'N/A')}")
    
    # ML settings
    print(f"  [ML]")
    ml_settings = settings.get('ml', {})
    print(f"    enabled: {ml_settings.get('enabled', False)}")
    print(f"    min_probability: {ml_settings.get('min_probability', 0.58)}")
    print(f"    options_threshold: {ml_settings.get('options_threshold', 0.55)}")
    print(f"    crypto_threshold: {ml_settings.get('crypto_threshold', 0.55)}")
    
    print("-" * 72)
    print()
    
    logger.log("trading_hydra_start", {
        "loop_interval": loop_interval,
        "paper_mode": os.environ.get('ALPACA_PAPER', 'true'),
        "output_mode": "quiet" if quiet else ("verbose" if verbose else "clean"),
        "timestamp": datetime.utcnow().isoformat()
    })
    
    # Start dashboard in background thread ONLY if explicitly enabled
    # Dashboard is OFF by default for security and resource efficiency
    # Enable with: ENABLE_DASHBOARD=true
    enable_dashboard = os.environ.get('ENABLE_DASHBOARD', '').strip().lower() in ('true', '1', 'yes')
    
    if enable_dashboard:
        _dashboard_thread = threading.Thread(
            target=_start_dashboard,
            name="DashboardThread",
            daemon=True  # Daemon thread dies when main thread exits
        )
        _dashboard_thread.start()
        print("[INFO] Dashboard thread started on http://0.0.0.0:5000")
        # Give dashboard a moment to initialize
        time.sleep(1)
    else:
        print("[INFO] Dashboard disabled (set ENABLE_DASHBOARD=true to enable)")
    
    # Start HydraSensors background thread (non-blocking)
    # Sensors provide watchlists, market data caching, indicators, breadth, and regime detection
    sensors_manager = None
    try:
        from trading_hydra.sensors import get_sensors
        sensors_manager = get_sensors()
        sensors_manager.start()
        logger.log("sensors_started", {"status": "background_thread_launched"})
        print("[INFO] HydraSensors started (background monitoring)")
    except Exception as e:
        logger.error(f"Failed to start sensors: {e}")
        print(f"[WARN] HydraSensors failed to start: {e}")
        print("[WARN] Bots will use defaults (fail-open design)")
        sensors_manager = None
    
    # Initialize dedicated bot threads
    # These run CryptoBot, ExitBot, Options, Bounce, TwentyMin at 5s intervals
    thread_manager = None
    try:
        from trading_hydra.services.dedicated_threads import (
            get_dedicated_thread_manager, shutdown_dedicated_threads
        )
        from trading_hydra.services.bot_runners import create_bot_runners
        
        thread_manager = get_dedicated_thread_manager()
        bot_runners = create_bot_runners()
        
        # Set run functions for each thread
        for bot_id, runner in bot_runners.items():
            thread_manager.set_run_function(bot_id, runner)
        
        # Start all dedicated threads
        thread_manager.start_all()
        logger.log("dedicated_threads_started", {
            "bots": list(bot_runners.keys())
        })
        print(f"[INFO] Started {len(bot_runners)} dedicated bot threads (crypto, options, exit, bounce, 20min)")
    except Exception as e:
        logger.error(f"Failed to start dedicated threads: {e}")
        print(f"[WARN] Dedicated threads failed to start: {e}")
        print("[WARN] Crypto and other dedicated bots will NOT run!")
        thread_manager = None
    
    # Initialize the trading orchestrator
    orchestrator = TradingOrchestrator()
    
    # Track loop statistics
    loop_count = 0
    start_time = datetime.now()
    
    print("[INFO] Entering trading loop (Ctrl+C to stop)")
    print()
    
    # Main trading loop
    while not _is_shutdown_requested():
        loop_count += 1
        loop_start = time.time()
        
        try:
            # Run the 5-step trading loop
            result = orchestrator.run_loop()
            
            # Update loop number in display data
            if result.display_data:
                result.display_data.loop_number = loop_count
                result.display_data.next_scan_seconds = loop_interval
            
            # Format and print human-readable output
            if result.display_data:
                output = formatter.format_loop(result.display_data)
                # Use sys.stdout.write + flush for proper ANSI escape code handling
                sys.stdout.write(output + "\n")
                sys.stdout.flush()
            else:
                # Fallback to simple output if no display data
                status_icon = "\u2713" if result.success else "\u2717"
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Loop #{loop_count} {status_icon} - {result.status}")
            
            if not result.success:
                logger.log("loop_failed", {
                    "loop_count": loop_count,
                    "status": result.status,
                    "summary": result.summary
                })
                
        except Exception as e:
            error_str = str(e)
            is_transient = any(msg in error_str for msg in [
                "Broken pipe", "Connection reset", "Connection refused",
                "timed out", "Temporary failure", "Network unreachable",
                "Connection aborted", "SSLError", "ConnectionError"
            ])
            
            if is_transient:
                transient_errors = getattr(run_trading_loop, '_transient_errors', 0) + 1
                run_trading_loop._transient_errors = transient_errors
                
                logger.log("transient_error", {
                    "loop_count": loop_count, 
                    "error": error_str,
                    "consecutive_count": transient_errors
                })
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Loop #{loop_count} Network error (retry {transient_errors}/3): {error_str[:50]}")
                
                if transient_errors >= 3:
                    logger.error(f"Loop #{loop_count} exception after 3 retries: {e}")
                    try:
                        from trading_hydra.core.halt import get_halt_manager
                        halt_manager = get_halt_manager()
                        halt_manager.set_halt(f"Persistent network error after 3 retries: {error_str[:80]}")
                        logger.log("fail_closed_halt", {"loop_count": loop_count, "error": error_str})
                        print(f"[FAIL-CLOSED] Trading halted after 3 consecutive network errors")
                    except Exception as halt_err:
                        logger.error(f"Failed to set halt after network errors: {halt_err}")
                    run_trading_loop._transient_errors = 0
                else:
                    time.sleep(2)
                    continue
            else:
                logger.error(f"Loop #{loop_count} exception: {e}")
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Loop #{loop_count} ERROR: {e}")
                
                try:
                    from trading_hydra.core.halt import get_halt_manager
                    halt_manager = get_halt_manager()
                    halt_manager.set_halt(f"Critical exception in loop #{loop_count}: {error_str[:100]}")
                    logger.log("fail_closed_halt", {"loop_count": loop_count, "error": error_str})
                    print(f"[FAIL-CLOSED] Trading halted due to critical error")
                except Exception as halt_err:
                    logger.error(f"Failed to set halt after critical error: {halt_err}")
        
        if hasattr(run_trading_loop, '_transient_errors'):
            run_trading_loop._transient_errors = 0
        
        # Calculate time spent and sleep remaining interval
        # Always use 5-second interval: ExitBot and CryptoBot need constant monitoring
        # Stock/Options bots skip internally when markets are closed
        elapsed = time.time() - loop_start
        sleep_time = max(0, loop_interval - elapsed)
        
        # Sleep in small increments to allow faster shutdown response
        sleep_remaining = sleep_time
        while sleep_remaining > 0 and not _is_shutdown_requested():
            time.sleep(min(0.5, sleep_remaining))
            sleep_remaining -= 0.5
    
    # Shutdown sequence
    uptime = datetime.now() - start_time
    
    # Stop sensors first
    if sensors_manager is not None:
        try:
            print("[INFO] Stopping HydraSensors...")
            sensors_manager.stop()
            logger.log("sensors_stopped", {})
            print("[INFO] HydraSensors stopped")
        except Exception as e:
            logger.error(f"Error stopping sensors: {e}")
    
    # Stop dedicated threads
    if thread_manager is not None:
        try:
            print("[INFO] Stopping dedicated bot threads...")
            thread_manager.stop_all(timeout=5.0)
            logger.log("dedicated_threads_stopped", {})
            print("[INFO] Dedicated threads stopped")
        except Exception as e:
            logger.error(f"Error stopping dedicated threads: {e}")
            print(f"[WARN] Error stopping dedicated threads: {e}")
    
    print()
    print("=" * 72)
    print("  SHUTDOWN COMPLETE")
    print("=" * 72)
    print(f"  Total Loops: {loop_count}")
    print(f"  Uptime: {uptime}")
    print("=" * 72)
    
    logger.log("trading_hydra_stop", {
        "loop_count": loop_count,
        "uptime_seconds": uptime.total_seconds(),
        "timestamp": datetime.utcnow().isoformat()
    })


# =============================================================================
# FRESH START - Reset for new account
# =============================================================================

def perform_fresh_start():
    """
    Reset all state for a new account.
    
    This function:
    1. Backs up existing state to a JSON file
    2. Clears all trading state (positions, halts, day equity, etc.)
    3. Clears ML baseline data
    4. Logs the reset for audit purposes
    
    After running, the new user should:
    1. Update ALPACA_KEY and ALPACA_SECRET with their credentials
    2. Start the application normally with: python main.py
    """
    import json
    import shutil
    
    print("=" * 72)
    print("  TRADING HYDRA - FRESH START")
    print("=" * 72)
    print()
    print("  This will reset ALL account-specific state including:")
    print("    - Trading state database")
    print("    - Position tracking data")
    print("    - Day-start equity records")
    print("    - Trading halt history")
    print("    - Order history")
    print()
    
    # Add src to path for imports
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
    
    from trading_hydra.core.state import init_state_store, clear_all_state, close_state_store
    from trading_hydra.core.logging import get_logger
    
    logger = get_logger()
    
    # Initialize state store to ensure tables exist
    init_state_store()
    
    # Create backup directory
    backup_dir = "./state/backups"
    os.makedirs(backup_dir, exist_ok=True)
    
    # Generate backup filename with timestamp
    backup_filename = f"state_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    backup_path = os.path.join(backup_dir, backup_filename)
    
    print(f"  Creating backup: {backup_path}")
    
    # Clear state and get backup data
    backup_data = clear_all_state(backup=True)
    
    # Save backup to file
    if backup_data:
        with open(backup_path, 'w') as f:
            json.dump(backup_data, f, indent=2, default=str)
        print(f"  Backup saved: {len(backup_data)} state entries preserved")
    else:
        print("  No existing state to backup (already clean)")
    
    # Log the fresh start event
    logger.log("fresh_start_complete", {
        "backup_path": backup_path,
        "entries_cleared": len(backup_data),
        "timestamp": datetime.utcnow().isoformat()
    })
    
    # Close state store connection
    close_state_store()
    
    print()
    print("  RESET COMPLETE")
    print("=" * 72)
    print()
    print("  Next steps for the new user:")
    print("    1. Update environment secrets:")
    print("       - ALPACA_KEY (new account's API key)")
    print("       - ALPACA_SECRET (new account's secret)")
    print("    2. Keep ALPACA_PAPER=true for initial testing")
    print("    3. Start the application: python main.py")
    print()
    print("=" * 72)


# =============================================================================
# ENTRY POINT
# =============================================================================

def _acquire_process_lock():
    """Acquire exclusive process lock to prevent multiple instances.

    Uses a platform-appropriate temp directory so the lock is shared across
    all instances on any OS (Linux, macOS, Windows).
    """
    lock_path = os.path.join(tempfile.gettempdir(), 'trading_hydra.lock')

    if platform.system() == 'Windows':
        # Windows: PID-file lock using psutil to detect stale PIDs
        try:
            if os.path.exists(lock_path):
                try:
                    with open(lock_path, 'r') as lf:
                        raw = lf.read().strip()
                    if raw:
                        import psutil
                        old_pid = int(raw)
                        if psutil.pid_exists(old_pid):
                            try:
                                proc = psutil.Process(old_pid)
                                cmdline = ' '.join(proc.cmdline())
                                if 'main.py' in cmdline and proc.is_running():
                                    print("Another Trading Hydra instance is already running. Exiting to prevent API rate limiting.")
                                    print("   Only one instance should run at a time.")
                                    sys.exit(1)
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass  # PID reused by another process, stale lock
                except (ValueError, OSError, ImportError):
                    pass  # Stale or unreadable lock file, overwrite it
            lock_file = open(lock_path, 'w')
            lock_file.write(str(os.getpid()))
            lock_file.flush()
            return lock_file
        except OSError as e:
            print(f"Cannot acquire process lock: {e}. Proceeding without lock.")
            return None
    else:
        # Unix: fcntl-based exclusive file lock
        import fcntl
        lock_file = open(lock_path, 'w')
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_file.write(str(os.getpid()))
            lock_file.flush()
            return lock_file
        except (IOError, OSError):
            print("Another Trading Hydra instance is already running. Exiting to prevent API rate limiting.")
            print("   Only one instance should run at a time.")
            lock_file.close()
            sys.exit(1)


def main():
    """
    Main entry point for Trading Hydra.
    
    Parses command line arguments and starts the trading loop.
    
    FULLBOT MODE (for customers):
        BOT_ROLE=fullbot python main.py
        - Forces role=all, dashboard=off
        - Individual roles are blocked (internal-only)
    
    MODULAR ROLES (internal deployment only):
        python main.py --role marketdata --no-dashboard
        python main.py --role strategy --no-dashboard
        python main.py --role execution --no-dashboard
        python main.py --role exit --no-dashboard
    """
    # Acquire exclusive process lock - prevents duplicate instances from rate-limiting Alpaca
    _lock_handle = _acquire_process_lock()
    print("🔒 Process lock acquired (single instance mode)")
    
    # =========================================================================
    # FULLBOT MODE DETECTION - Customer drop-in deployment
    # =========================================================================
    # If BOT_ROLE=fullbot, force all roles in-process with no dashboard
    # This is the ONLY supported mode for customer installations
    bot_role_env = os.environ.get('BOT_ROLE', '').strip().lower()
    is_fullbot_mode = (bot_role_env == 'fullbot')
    
    if is_fullbot_mode:
        print("[FULLBOT] Running in fullbot mode (all roles, no dashboard)")
        # Force fullbot behavior regardless of CLI args
        os.environ['HYDRA_NO_DASHBOARD'] = '1'
        # Clear any conflicting role env var
        if 'HYDRA_ROLE' in os.environ:
            del os.environ['HYDRA_ROLE']
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Trading Hydra - Automated Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Roles (for multi-container deployment):
  all         Full trading loop (default, current behavior)
  marketdata  Only market data collection/snapshot publishing
  strategy    Only signal generation (NO broker orders)
  execution   Only intent consumption and order placement
  exit        Only exit/position management (stops/TPs/close)

Output Modes:
  (default)     Clean   - Full dashboard, no log noise (recommended)
  --verbose, -v Verbose - Full dashboard + all JSONL logs
  --quiet, -q   Quiet   - Minimal one-line summary per loop

Environment Variables:
  HYDRA_ROLE         Override default role (if set)
  HYDRA_NO_DASHBOARD Set to 1 to force --no-dashboard

Examples:
  python main.py                                    Full system (default)
  python main.py --role marketdata --no-dashboard   Market data container
  python main.py --role strategy --no-dashboard     Strategy container
  python main.py --role execution                   Execution container
  python main.py --role exit                        Exit management container
  HYDRA_ROLE=strategy HYDRA_NO_DASHBOARD=1 python main.py
        """
    )
    
    # Role-based execution (for multi-container deployment)
    parser.add_argument(
        '--role',
        type=str,
        choices=['all', 'marketdata', 'strategy', 'execution', 'exit'],
        default=None,
        help='Role for this container (default: all). Override with HYDRA_ROLE env var.'
    )
    parser.add_argument(
        '--no-dashboard',
        action='store_true',
        help='Disable Flask web dashboard. Override with HYDRA_NO_DASHBOARD=1 env var.'
    )
    
    # Mutually exclusive output mode group
    output_mode = parser.add_mutually_exclusive_group()
    output_mode.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Show full dashboard with all JSONL log output'
    )
    output_mode.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Show minimal one-line summary per loop'
    )
    parser.add_argument(
        '--inplace', '-i',
        action='store_true',
        help='Enable in-place dashboard updates (clears screen each loop)'
    )
    parser.add_argument(
        '--fresh-start',
        action='store_true',
        help='Reset all state for a new account (clears positions, halts, and ML data)'
    )
    args = parser.parse_args()
    
    # Handle fresh-start before anything else
    if args.fresh_start:
        perform_fresh_start()
        sys.exit(0)
    
    # =========================================================================
    # FULLBOT MODE ENFORCEMENT
    # =========================================================================
    if is_fullbot_mode:
        # In fullbot mode: ignore --role, force no-dashboard
        args.role = None  # Will default to 'all' behavior
        args.no_dashboard = True
        print("[FULLBOT] CLI role/dashboard args overridden")
    
    # Block individual roles if BOT_ROLE is set but not 'fullbot'
    # Individual roles are internal-only (Ansible/Proxmox deployment)
    if bot_role_env and bot_role_env != 'fullbot':
        print(f"[ERROR] Invalid BOT_ROLE='{bot_role_env}'. Only 'fullbot' is supported.")
        print("[ERROR] Individual roles (marketdata, strategy, execution, exit) are internal-only.")
        sys.exit(1)
    
    # Determine if we should use role-based routing
    # Role routing is used when:
    # 1. --role is explicitly set (not 'all')
    # 2. HYDRA_ROLE env var is set
    # 3. --no-dashboard is set (implies container mode)
    use_role_router = (
        (args.role is not None and args.role != 'all') or
        os.environ.get('HYDRA_ROLE') is not None or
        args.no_dashboard or
        os.environ.get('HYDRA_NO_DASHBOARD', '').strip() in ('1', 'true', 'yes')
    )
    
    if use_role_router:
        # Use new role-based routing for container deployment
        from trading_hydra.role_router import run_role
        try:
            run_role(args.role or 'all', args.no_dashboard)
        except KeyboardInterrupt:
            print("\n[SHUTDOWN] KeyboardInterrupt caught")
        except Exception as e:
            print(f"[FATAL] Unhandled exception: {e}")
            sys.exit(1)
    else:
        # Original behavior: full trading loop with dashboard
        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
        
        try:
            run_trading_loop(quiet=args.quiet, verbose=args.verbose, inplace=args.inplace)
        except KeyboardInterrupt:
            # Backup handler if signal handler didn't catch it
            print("\n[SHUTDOWN] KeyboardInterrupt caught")
        except Exception as e:
            print(f"[FATAL] Unhandled exception: {e}")
            sys.exit(1)
    
    sys.exit(0)


if __name__ == "__main__":
    main()
