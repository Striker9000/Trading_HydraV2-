#!/usr/bin/env python3
"""
Trading Hydra Watchdog
Monitors the main trading bot and automatically restarts it if it crashes.
Features:
- Subprocess monitoring with periodic health checks
- Exponential backoff with max attempts before extended cooloff
- Stale PID detection and cleanup
- Crash logging to JSONL
- Graceful shutdown handling
"""

import subprocess
import sys
import time
import signal
import json
import os
from datetime import datetime
from pathlib import Path

# Configuration
MAIN_SCRIPT = "main.py"
LOG_FILE = Path("logs/watchdog.jsonl")
HEALTH_CHECK_INTERVAL = 5  # Check child every 5 seconds
MAX_BACKOFF_SECONDS = 60  # 1 minute max between restarts
INITIAL_BACKOFF_SECONDS = 5
BACKOFF_MULTIPLIER = 2
RESET_BACKOFF_AFTER_SECONDS = 300  # Reset backoff if bot runs for 5+ minutes
MAX_RAPID_RESTARTS = 5  # After 5 rapid failures, enter extended cooloff
EXTENDED_COOLOFF_SECONDS = 300  # 5 minute extended cooloff after repeated failures
RAPID_FAILURE_WINDOW_SECONDS = 120  # Failures within 2 minutes count as "rapid"

# Global state
child_process = None
shutdown_requested = False


def log_event(event: str, **kwargs):
    """Log an event to the watchdog JSONL log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    entry = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "event": event,
        **kwargs
    }
    
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    
    # Also print to console
    level = "ERROR" if "error" in event or "failed" in event else "INFO"
    print(f"[WATCHDOG][{level}] {event}: {kwargs}")


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested, child_process
    
    sig_name = signal.Signals(signum).name
    log_event("shutdown_signal_received", signal=sig_name)
    shutdown_requested = True
    
    if child_process and child_process.poll() is None:
        log_event("stopping_child_process", pid=child_process.pid)
        child_process.terminate()
        try:
            child_process.wait(timeout=10)
            log_event("child_process_stopped", pid=child_process.pid)
        except subprocess.TimeoutExpired:
            log_event("child_process_force_kill", pid=child_process.pid)
            child_process.kill()
            child_process.wait()
    
    log_event("watchdog_shutdown_complete")
    sys.exit(0)


def is_process_alive(process):
    """Check if subprocess is still running."""
    if process is None:
        return False
    return process.poll() is None


def cleanup_stale_processes():
    """Kill any stale main.py processes from previous runs (cross-platform)."""
    try:
        import psutil
        current_pid = os.getpid()
        killed = 0
        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                if proc.pid == current_pid:
                    continue
                cmdline = proc.info.get('cmdline') or []
                if any(MAIN_SCRIPT in arg for arg in cmdline):
                    proc.terminate()
                    killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if killed > 0:
            log_event("cleaned_stale_processes", count=killed)
            time.sleep(1)
    except ImportError:
        log_event("stale_cleanup_warning", error="psutil not available, skipping stale process cleanup")
    except Exception as e:
        log_event("stale_cleanup_warning", error=str(e))


def start_bot():
    """Start the trading bot as a subprocess."""
    global child_process
    
    cleanup_stale_processes()
    
    log_event("starting_bot", script=MAIN_SCRIPT)
    
    child_process = subprocess.Popen(
        [sys.executable, MAIN_SCRIPT],
        stdout=sys.stdout,
        stderr=sys.stderr,
        cwd=os.getcwd()
    )
    
    log_event("bot_started", pid=child_process.pid)
    return child_process


def wait_with_health_checks(process):
    """Wait for process with periodic health checks."""
    while not shutdown_requested:
        # Check if process exited
        return_code = process.poll()
        if return_code is not None:
            return return_code
        
        # Process still running, wait before next check
        time.sleep(HEALTH_CHECK_INTERVAL)
    
    return None


def interruptible_sleep(seconds):
    """Sleep in small increments to allow shutdown."""
    end_time = time.time() + seconds
    while time.time() < end_time and not shutdown_requested:
        time.sleep(1)
    return not shutdown_requested


def run_watchdog():
    """Main watchdog loop with hardened restart logic."""
    global shutdown_requested
    
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    log_event("watchdog_started", 
              main_script=MAIN_SCRIPT,
              max_backoff=MAX_BACKOFF_SECONDS,
              initial_backoff=INITIAL_BACKOFF_SECONDS,
              max_rapid_restarts=MAX_RAPID_RESTARTS,
              extended_cooloff=EXTENDED_COOLOFF_SECONDS)
    
    backoff_seconds = INITIAL_BACKOFF_SECONDS
    total_restarts = 0
    rapid_failure_times = []  # Track recent failure timestamps
    
    while not shutdown_requested:
        # Start the bot
        process = start_bot()
        start_time = time.time()
        
        # Wait for the process with health checks
        return_code = wait_with_health_checks(process)
        run_duration = time.time() - start_time
        
        if shutdown_requested:
            break
        
        # Bot exited unexpectedly
        total_restarts += 1
        log_event("bot_exited",
                  return_code=return_code,
                  run_duration_seconds=round(run_duration, 1),
                  total_restarts=total_restarts)
        
        # Track rapid failures (failures within the rapid window)
        current_time = time.time()
        rapid_failure_times.append(current_time)
        # Remove old failures outside the window
        rapid_failure_times = [
            t for t in rapid_failure_times 
            if current_time - t < RAPID_FAILURE_WINDOW_SECONDS
        ]
        
        # Check if we've hit too many rapid failures
        if len(rapid_failure_times) >= MAX_RAPID_RESTARTS:
            log_event("extended_cooloff_triggered",
                      rapid_failures=len(rapid_failure_times),
                      cooloff_seconds=EXTENDED_COOLOFF_SECONDS,
                      reason="too_many_rapid_failures")
            
            if not interruptible_sleep(EXTENDED_COOLOFF_SECONDS):
                break
            
            # Reset after extended cooloff
            rapid_failure_times = []
            backoff_seconds = INITIAL_BACKOFF_SECONDS
            log_event("extended_cooloff_complete", resuming=True)
            continue
        
        # Reset backoff if bot ran successfully for a while
        if run_duration >= RESET_BACKOFF_AFTER_SECONDS:
            log_event("backoff_reset", 
                      reason="bot_ran_successfully",
                      run_duration=round(run_duration, 1))
            backoff_seconds = INITIAL_BACKOFF_SECONDS
            rapid_failure_times = []  # Also clear rapid failure tracking
        
        # Wait before restarting with exponential backoff
        log_event("waiting_before_restart", wait_seconds=backoff_seconds)
        
        if not interruptible_sleep(backoff_seconds):
            break
        
        # Increase backoff for next potential failure
        backoff_seconds = min(backoff_seconds * BACKOFF_MULTIPLIER, MAX_BACKOFF_SECONDS)
    
    log_event("watchdog_loop_ended", total_restarts=total_restarts)


if __name__ == "__main__":
    print("=" * 60)
    print("  TRADING HYDRA WATCHDOG")
    print("  Monitoring and auto-restart enabled")
    print("  Extended cooloff after repeated failures")
    print("=" * 60)
    run_watchdog()
