"""Main runner for Trading Hydra - long-running process with config-driven loop"""
import os
import sys
import signal
import time
from datetime import datetime

# Ensure we can import trading_hydra from the src directory
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.dirname(current_dir)
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from dotenv import load_dotenv
load_dotenv()

from trading_hydra.core.config import load_settings
from trading_hydra.core.logging import get_logger
from trading_hydra.core.state import close_state_store
from trading_hydra.orchestrator import get_orchestrator

_running = True


def signal_handler(signum, frame):
    global _running
    logger = get_logger()
    logger.log("shutdown_requested", {"signal": signum, "graceful": True})
    print(f"🛑 Shutdown requested (signal {signum}), exiting gracefully...")
    _running = False


def boot_self_test():
    """Run startup self-tests to ensure system is ready"""
    print("=== TRADING HYDRA BOOT SELF-TEST ===")
    
    # Test 0: Paper Safety Lock (CRITICAL)
    try:
        from trading_hydra.core.safety import get_paper_safety_lock
        safety_lock = get_paper_safety_lock()
        safety_lock.enforce_safety_or_exit()
        print("✅ Paper safety lock: OK")
    except SystemExit:
        # Safety lock triggered exit - let it happen
        raise
    except Exception as e:
        print(f"❌ Paper safety lock: FAILED - {e}")
        return False
    
    # Test 1: Config loading
    try:
        settings = load_settings()
        print("✅ Config loading: OK")
    except Exception as e:
        print(f"❌ Config loading: FAILED - {e}")
        return False
    
    # Test 2: State directory and SQLite
    try:
        state_dir = "./state"
        os.makedirs(state_dir, exist_ok=True)
        
        # Test SQLite creation
        import sqlite3
        db_path = os.path.join(state_dir, "trading_state.db")
        with sqlite3.connect(db_path) as conn:
            conn.execute("SELECT 1")
        print("✅ SQLite state: OK")
    except Exception as e:
        print(f"❌ SQLite state: FAILED - {e}")
        return False
    
    # Test 3: Log directory
    try:
        log_dir = "./logs"
        os.makedirs(log_dir, exist_ok=True)
        
        # Test log file write
        log_path = os.path.join(log_dir, "app.jsonl")
        with open(log_path, "a") as f:
            f.write("")
        print("✅ Log file access: OK")
    except Exception as e:
        print(f"❌ Log file access: FAILED - {e}")
        return False
    
    # Test 4: Core imports
    try:
        from trading_hydra.orchestrator import get_orchestrator
        print("✅ Core imports: OK")
    except Exception as e:
        print(f"❌ Core imports: FAILED - {e}")
        return False
    
    print("✅ BOOT_OK: All self-tests passed")
    print("=====================================")
    
    # Startup audit
    from trading_hydra.core.halt import get_halt_manager
    
    try:
        halt_manager = get_halt_manager()
        is_halted = halt_manager.is_halted()
        status = halt_manager.get_status()
        
        audit_settings = load_settings()
        loop_interval = audit_settings.get("runner", {}).get("loop_interval_seconds", 5)
        
        print("🔍 STARTUP AUDIT:")
        print(f"   - Loop interval: {loop_interval} seconds")
        print(f"   - Trading halt: {'ACTIVE' if is_halted else 'INACTIVE'}")
        if is_halted:
            print(f"     Reason: {status.reason}")
            if status.expires_at:
                print(f"     Expires: {status.expires_at}")
        print("=====================================")
    except Exception as e:
        print(f"⚠️ Startup audit failed: {e}")
    
    return True


def main():
    global _running
    
    # Run boot self-test first
    if not boot_self_test():
        print("❌ BOOT FAILED: Exiting due to self-test failures")
        sys.exit(1)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logger = get_logger()
    logger.log("runner_start", {"pid": os.getpid()})
    
    try:
        settings = load_settings()
    except Exception as e:
        logger.error(f"Failed to load settings: {e}")
        sys.exit(1)
    
    loop_interval = settings.get("runner", {}).get("loop_interval_seconds", 5)
    logger.log("runner_config", {"loop_interval_seconds": loop_interval})
    
    orchestrator = get_orchestrator()
    
    try:
        orchestrator.initialize()
    except Exception as e:
        logger.error(f"Failed to initialize orchestrator: {e}")
        sys.exit(1)
    
    loop_count = 0
    
    logger.log("runner_loop_starting", {"interval": loop_interval})
    
    print("\n" + "=" * 50)
    print("  TRADING HYDRA - RUNNING")
    print(f"  Loop interval: {loop_interval} seconds")
    print("  Press Ctrl+C to stop")
    print("=" * 50 + "\n")
    
    while _running:
        loop_count += 1
        loop_start = time.time()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        print(f"[{now}] Loop #{loop_count} starting...")
        
        logger.log("runner_loop_iteration", {
            "loop_count": loop_count,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        })
        
        try:
            result = orchestrator.run_loop()
            
            status_icon = "✅" if result.success else "❌"
            print(f"[{now}] Loop #{loop_count} complete: {status_icon} {result.status}")
            
            logger.log("runner_loop_result", {
                "loop_count": loop_count,
                "success": result.success,
                "status": result.status
            })
        except Exception as e:
            print(f"[{now}] Loop #{loop_count} ERROR: {e}")
            logger.error(f"Loop error: {e}", loop_count=loop_count)
        
        elapsed = time.time() - loop_start
        sleep_time = max(0, loop_interval - elapsed)
        
        if sleep_time > 0 and _running:
            logger.log("runner_sleeping", {"seconds": round(sleep_time, 2)})
            time.sleep(sleep_time)
    
    print("\n" + "=" * 50)
    print(f"  TRADING HYDRA - STOPPED")
    print(f"  Total loops completed: {loop_count}")
    print("=" * 50 + "\n")
    
    logger.log("runner_shutdown", {"total_loops": loop_count})
    close_state_store()
    logger.log("runner_stopped", {})


if __name__ == "__main__":
    main()
