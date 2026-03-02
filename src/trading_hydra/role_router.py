"""
Role Router for Multi-Container Deployment
===========================================

This module provides role-based execution gating for running Trading Hydra
as multiple Proxmox LXC containers. Each container runs one role.

Roles:
    all        - Full trading loop (current behavior unchanged)
    marketdata - Only market data collection/snapshot publishing
    strategy   - Only signal generation (NO broker orders)
    execution  - Only intent consumption and order placement
    exit       - Only exit/position management (stops/TPs/close logic)

Safety Rules:
    - Strategy role MUST NOT import or initialize broker keys if possible
    - Strategy role MUST NOT place orders, ever
    - Execution role is the ONLY one that submits entries
    - Exit role can submit exits if needed

Usage:
    python main.py --role marketdata --no-dashboard
    HYDRA_ROLE=strategy HYDRA_NO_DASHBOARD=1 python main.py
"""

import os
import sys
import socket
import subprocess
from datetime import datetime
from typing import Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum


def _try_create_hub_store():
    """
    Try to create HubStoreMySQL from env vars.
    Returns None if env vars are missing (graceful fallback to state-based).
    """
    required = ["HUB_DB_HOST", "HUB_DB_PORT", "HUB_DB_NAME", "HUB_DB_USER", "HUB_DB_PASS"]
    if not all(os.environ.get(k) for k in required):
        return None
    
    try:
        from trading_hydra.hub.hub_store_mysql import HubStoreMySQL
        return HubStoreMySQL(
            host=os.environ["HUB_DB_HOST"],
            port=int(os.environ["HUB_DB_PORT"]),
            user=os.environ["HUB_DB_USER"],
            password=os.environ["HUB_DB_PASS"],
            database=os.environ["HUB_DB_NAME"],
        )
    except Exception as e:
        print(f"[WARNING] Hub store creation failed: {e}")
        return None


class HydraRole(Enum):
    """Valid roles for Trading Hydra containers"""
    ALL = "all"
    MARKETDATA = "marketdata"
    STRATEGY = "strategy"
    EXECUTION = "execution"
    EXIT = "exit"
    
    @classmethod
    def from_string(cls, value: str) -> 'HydraRole':
        """Parse role from string, case-insensitive"""
        try:
            return cls(value.lower().strip())
        except ValueError:
            valid = ", ".join([r.value for r in cls])
            raise ValueError(f"Invalid role '{value}'. Valid roles: {valid}")


@dataclass
class RoleConfig:
    """Configuration resolved from CLI and environment"""
    role: HydraRole
    no_dashboard: bool
    hostname: str
    pid: int
    git_commit: Optional[str]
    config_files: list


def get_git_commit() -> Optional[str]:
    """Get current git commit hash, or None if not available"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        pass
    return None


def get_loaded_config_files() -> list:
    """Return list of config files that will be loaded"""
    config_files = []
    base_path = "config"
    
    candidates = [
        "settings.yaml",
        "bots.yaml", 
        "watchlist.yaml",
        "regime.yaml",
        "symbols.yaml"
    ]
    
    for filename in candidates:
        full_path = os.path.join(base_path, filename)
        if os.path.exists(full_path):
            config_files.append(full_path)
    
    return config_files


def resolve_role_config(cli_role: Optional[str], cli_no_dashboard: bool) -> RoleConfig:
    """
    Resolve final role configuration from CLI args and environment variables.
    
    Priority:
        1. CLI arguments (highest)
        2. Environment variables
        3. Defaults (role=all, no_dashboard=False)
    
    Args:
        cli_role: Role from --role argument, or None
        cli_no_dashboard: --no-dashboard flag from CLI
    
    Returns:
        RoleConfig with resolved values
    """
    # Resolve role: CLI > env > default
    if cli_role:
        role = HydraRole.from_string(cli_role)
    elif os.environ.get("HYDRA_ROLE"):
        role = HydraRole.from_string(os.environ["HYDRA_ROLE"])
    else:
        role = HydraRole.ALL
    
    # Resolve no_dashboard: CLI > env > default
    if cli_no_dashboard:
        no_dashboard = True
    elif os.environ.get("HYDRA_NO_DASHBOARD", "").strip() in ("1", "true", "yes"):
        no_dashboard = True
    else:
        no_dashboard = False
    
    return RoleConfig(
        role=role,
        no_dashboard=no_dashboard,
        hostname=socket.gethostname(),
        pid=os.getpid(),
        git_commit=get_git_commit(),
        config_files=get_loaded_config_files()
    )


def log_startup_banner(config: RoleConfig) -> dict:
    """
    Generate and print deterministic startup banner.
    
    Returns the banner as a dict for JSONL logging.
    """
    banner = {
        "event": "hydra_startup",
        "role": config.role.value,
        "no_dashboard": config.no_dashboard,
        "hostname": config.hostname,
        "pid": config.pid,
        "git_commit": config.git_commit,
        "config_files": config.config_files,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }
    
    # Print single-line JSON for machine parsing
    import json
    print(json.dumps(banner))
    
    return banner


def run_role(role: str, no_dashboard: bool) -> None:
    """
    Initialize config and logging once, then run the correct loop based on role.
    
    This is the main entry point for role-based execution.
    
    Args:
        role: Role string (all, marketdata, strategy, execution, exit)
        no_dashboard: If True, skip Flask dashboard startup
    """
    # Resolve full config
    config = resolve_role_config(role, no_dashboard)
    
    # Log startup banner (single deterministic JSON line)
    banner = log_startup_banner(config)
    
    # Import core modules after banner (fail-fast if imports broken)
    from trading_hydra.core.logging import get_logger
    from trading_hydra.core.config import load_settings
    from trading_hydra.core.state import init_state_store
    
    # Initialize state store once
    init_state_store()
    
    # Get logger and log startup
    logger = get_logger()
    logger.log("role_router_start", banner)
    
    # Load settings
    settings = load_settings()
    loop_interval = settings.get("runner", {}).get("loop_interval_seconds", 5)
    
    # Route to appropriate role handler
    if config.role == HydraRole.ALL:
        _run_role_all(config, no_dashboard, loop_interval, logger)
    elif config.role == HydraRole.MARKETDATA:
        _run_role_marketdata(config, loop_interval, logger)
    elif config.role == HydraRole.STRATEGY:
        _run_role_strategy(config, loop_interval, logger)
    elif config.role == HydraRole.EXECUTION:
        _run_role_execution(config, loop_interval, logger)
    elif config.role == HydraRole.EXIT:
        _run_role_exit(config, loop_interval, logger)
    else:
        raise ValueError(f"Unknown role: {config.role}")


def _run_role_all(config: RoleConfig, no_dashboard: bool, loop_interval: int, logger) -> None:
    """
    Role: ALL - Full trading loop with dedicated bot threads
    
    This preserves the existing main.py behavior but adds dedicated threads
    for time-sensitive bots: ExitBot, CryptoBot, TwentyMinBot, BounceBot, Options.
    
    Main loop runs momentum bots only, while dedicated threads run other bots
    at 5-second intervals for faster response times.
    """
    import time
    import signal
    import threading
    from datetime import datetime
    from trading_hydra.orchestrator import TradingOrchestrator
    from trading_hydra.core.console import get_console_formatter
    from trading_hydra.services.dedicated_threads import (
        get_dedicated_thread_manager, shutdown_dedicated_threads
    )
    from trading_hydra.services.bot_runners import create_bot_runners
    from trading_hydra.services.signal_queue import (
        start_execution_worker, stop_execution_worker, get_signal_queue
    )
    
    # Shutdown handling
    shutdown_requested = False
    
    def signal_handler(signum, frame):
        nonlocal shutdown_requested
        shutdown_requested = True
        logger.log("shutdown_requested", {"signal": signum, "role": "all"})
        print(f"\n[SHUTDOWN] Signal {signum} received, completing current loop...")
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start dashboard unless disabled
    if not no_dashboard:
        dashboard_thread = threading.Thread(
            target=_start_dashboard_thread,
            name="DashboardThread",
            daemon=True
        )
        dashboard_thread.start()
        logger.log("dashboard_started", {"port": 5000})
        time.sleep(1)
    else:
        logger.log("dashboard_skipped", {"reason": "no_dashboard flag"})
    
    # Initialize dedicated bot threads
    try:
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
        print(f"[INFO] Started {len(bot_runners)} dedicated bot threads")
    except Exception as e:
        logger.error(f"Failed to start dedicated threads: {e}")
        print(f"[WARN] Dedicated threads failed, falling back to main loop only: {e}")
        thread_manager = None
    
    # Start execution worker (processes signal queue)
    try:
        start_execution_worker()
        logger.log("execution_worker_started", {})
        print("[INFO] Execution worker started (signal queue processing)")
    except Exception as e:
        logger.error(f"Failed to start execution worker: {e}")
        print(f"[WARN] Execution worker failed: {e}")
    
    # Initialize orchestrator
    orchestrator = TradingOrchestrator()
    formatter = get_console_formatter(quiet=True)
    
    loop_count = 0
    
    logger.log("role_all_loop_starting", {"interval": loop_interval})
    print(f"[INFO] Role ALL: Full trading loop, interval={loop_interval}s")
    
    while not shutdown_requested:
        loop_count += 1
        loop_start = time.time()
        
        try:
            result = orchestrator.run_loop()
            
            if result.display_data:
                result.display_data.loop_number = loop_count
                output = formatter.format_loop(result.display_data)
                print(output)
            else:
                status = "OK" if result.success else "FAIL"
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Loop #{loop_count} {status}")
                
        except Exception as e:
            logger.error(f"Loop #{loop_count} error: {e}")
            print(f"[ERROR] Loop #{loop_count}: {e}")
        
        # Sleep with shutdown check
        elapsed = time.time() - loop_start
        sleep_time = max(0, loop_interval - elapsed)
        sleep_remaining = sleep_time
        while sleep_remaining > 0 and not shutdown_requested:
            time.sleep(min(0.5, sleep_remaining))
            sleep_remaining -= 0.5
    
    # Shutdown execution worker (signal queue processor)
    try:
        stop_execution_worker()
        queue = get_signal_queue()
        logger.log("execution_worker_shutdown", queue.get_stats())
        print("[INFO] Execution worker stopped")
    except Exception as e:
        logger.error(f"Error stopping execution worker: {e}")
    
    # Shutdown dedicated threads
    if thread_manager is not None:
        try:
            thread_manager.log_stats()
            thread_manager.stop_all()
            logger.log("dedicated_threads_shutdown", {})
            print("[INFO] Dedicated bot threads stopped")
        except Exception as e:
            logger.error(f"Error stopping dedicated threads: {e}")
    
    logger.log("role_all_stopped", {"total_loops": loop_count})
    print(f"[INFO] Role ALL stopped after {loop_count} loops")


def _run_role_marketdata(config: RoleConfig, loop_interval: int, logger) -> None:
    """
    Role: MARKETDATA - Only market data collection/snapshot publishing
    
    This role:
    - Fetches quotes, bars, and market data from Alpaca
    - Updates market regime indicators (VIX, VVIX, etc.)
    - Publishes snapshots to state for other roles to consume
    - Writes snapshots to hub MySQL if configured
    - Does NOT make trading decisions or place orders
    """
    import time
    import signal
    from datetime import datetime
    
    shutdown_requested = False
    
    def signal_handler(signum, frame):
        nonlocal shutdown_requested
        shutdown_requested = True
        logger.log("shutdown_requested", {"signal": signum, "role": "marketdata"})
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Import only what we need - NO execution imports
    from trading_hydra.services.alpaca_client import get_alpaca_client
    from trading_hydra.services.market_regime import get_current_regime
    from trading_hydra.core.health import get_health_monitor
    from trading_hydra.core.state import set_state
    
    alpaca = get_alpaca_client()
    health = get_health_monitor()
    hub = _try_create_hub_store()
    
    if hub:
        print("[INFO] Hub MySQL connected - snapshots will be written to hub")
    else:
        print("[INFO] Hub MySQL not configured - using local state only")
    
    loop_count = 0
    
    logger.log("role_marketdata_starting", {"interval": loop_interval, "hub_enabled": hub is not None})
    print(f"[INFO] Role MARKETDATA: Data collection only, interval={loop_interval}s")
    
    while not shutdown_requested:
        loop_count += 1
        loop_start = time.time()
        
        try:
            # Step 1: Get account info (for equity snapshot)
            account = alpaca.get_account()
            set_state("marketdata.account_equity", float(account.equity))
            set_state("marketdata.account_cash", float(account.cash))
            set_state("marketdata.snapshot_ts", datetime.utcnow().isoformat() + "Z")
            
            # Step 2: Update market regime indicators
            regime = get_current_regime()
            if regime:
                set_state("marketdata.vix", regime.vix)
                set_state("marketdata.vvix", regime.vvix)
                set_state("marketdata.sentiment", regime.sentiment.value if regime.sentiment else "unknown")
            
            # Step 3: Record health tick
            health.record_price_tick()
            
            # Step 4: Write to hub MySQL if configured
            if hub:
                try:
                    hub.upsert_heartbeat(
                        bot_name=config.hostname,
                        role="marketdata",
                        host=config.hostname,
                        pid=config.pid,
                        meta={"loop": loop_count}
                    )
                    
                    # Determine market session
                    from trading_hydra.core.market_clock import is_market_open
                    market_open = is_market_open()
                    session = "OPEN" if market_open else "CLOSED"
                    
                    # Build snapshot payload
                    payload = {
                        "equity": float(account.equity),
                        "cash": float(account.cash),
                        "vix": regime.vix if regime else None,
                        "vvix": regime.vvix if regime else None,
                        "sentiment": regime.sentiment.value if regime and regime.sentiment else None,
                        "symbols_count": 0,
                        "timestamp": datetime.utcnow().isoformat() + "Z"
                    }
                    
                    hub.insert_snapshot(
                        source_bot=config.hostname,
                        market_session=session,
                        payload=payload,
                        vix=regime.vix if regime else None,
                        regime="NORMAL"
                    )
                except Exception as hub_err:
                    logger.error(f"Hub write error: {hub_err}")
            
            logger.log("marketdata_snapshot", {
                "loop": loop_count,
                "equity": float(account.equity),
                "vix": regime.vix if regime else None
            })
            
            print(f"[{datetime.now().strftime('%H:%M:%S')}] MARKETDATA #{loop_count}: equity=${account.equity}, VIX={regime.vix if regime else 'N/A'}")
            
        except Exception as e:
            logger.error(f"Marketdata loop #{loop_count} error: {e}")
            print(f"[ERROR] MARKETDATA #{loop_count}: {e}")
        
        # Sleep
        elapsed = time.time() - loop_start
        sleep_time = max(0, loop_interval - elapsed)
        sleep_remaining = sleep_time
        while sleep_remaining > 0 and not shutdown_requested:
            time.sleep(min(0.5, sleep_remaining))
            sleep_remaining -= 0.5
    
    if hub:
        hub.close()
    logger.log("role_marketdata_stopped", {"total_loops": loop_count})
    print(f"[INFO] Role MARKETDATA stopped after {loop_count} loops")


def _run_role_strategy(config: RoleConfig, loop_interval: int, logger) -> None:
    """
    Role: STRATEGY - Only signal generation (NO broker orders)
    
    SAFETY: This role MUST NOT place orders, ever.
    
    This role:
    - Reads market data from state or hub (published by marketdata role)
    - Runs signal generation logic from bots
    - Publishes trading intents to state/hub for execution role
    - Does NOT submit orders to broker
    """
    import time
    import signal
    import json
    import hashlib
    from datetime import datetime
    
    shutdown_requested = False
    
    def signal_handler(signum, frame):
        nonlocal shutdown_requested
        shutdown_requested = True
        logger.log("shutdown_requested", {"signal": signum, "role": "strategy"})
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # SAFETY: Do NOT import alpaca_client or execution service
    # Only import signal generation components
    from trading_hydra.core.state import get_state, set_state
    from trading_hydra.core.config import load_bots_config
    from trading_hydra.services.market_regime import get_current_regime
    
    hub = _try_create_hub_store()
    
    if hub:
        print("[INFO] Hub MySQL connected - intents will be written to hub")
    else:
        print("[INFO] Hub MySQL not configured - using local state only")
    
    loop_count = 0
    
    logger.log("role_strategy_starting", {"interval": loop_interval, "hub_enabled": hub is not None})
    print(f"[INFO] Role STRATEGY: Signal generation only (NO ORDERS), interval={loop_interval}s")
    print(f"[SAFETY] Strategy role will NEVER place broker orders")
    
    while not shutdown_requested:
        loop_count += 1
        loop_start = time.time()
        
        try:
            # Read market data snapshot from hub or state
            snapshot = None
            snapshot_id = None
            if hub:
                try:
                    hub.upsert_heartbeat(
                        bot_name=config.hostname,
                        role="strategy",
                        host=config.hostname,
                        pid=config.pid,
                        meta={"loop": loop_count}
                    )
                    snapshot = hub.get_latest_snapshot()
                    if snapshot:
                        snapshot_id = snapshot.get("snapshot_id")
                        equity = snapshot.get("payload", {}).get("equity", 0.0)
                        vix = snapshot.get("vix", 0.0)
                except Exception as hub_err:
                    logger.error(f"Hub read error: {hub_err}")
            
            if not snapshot:
                equity = get_state("marketdata.account_equity", 0.0)
                vix = get_state("marketdata.vix", 0.0)
            
            # Check posture before generating signals
            posture = "STANDARD"
            if hub:
                try:
                    posture = hub.get_posture()
                except Exception:
                    pass
            
            if posture == "HALT":
                logger.log("strategy_halted", {"posture": posture})
                print(f"[{datetime.now().strftime('%H:%M:%S')}] STRATEGY #{loop_count}: HALTED (posture={posture})")
            else:
                # Get market regime for signal decisions
                regime = get_current_regime()
                
                # Generate signals (placeholder - actual bot signal logic would go here)
                intents = []
                
                # For each intent, write to hub if configured
                if hub and snapshot_id and intents:
                    for intent in intents:
                        try:
                            strategy_id = intent.get("strategy_id", "unknown")
                            symbol = intent.get("symbol", "")
                            
                            # Check kill-switch for this strategy
                            kill_state = hub.get_kill_state(strategy_id)
                            if kill_state.get("is_killed"):
                                killed_until = kill_state.get("killed_until_ts")
                                logger.log("strategy_killed", {"strategy_id": strategy_id, "until": str(killed_until)})
                                continue
                            
                            # Create idempotency key
                            date_str = datetime.utcnow().strftime("%Y%m%d")
                            signal_hash = hashlib.md5(json.dumps(intent, sort_keys=True).encode()).hexdigest()[:8]
                            idempotency_key = f"{strategy_id}:{symbol}:{date_str}:{signal_hash}"
                            
                            hub.create_intent(
                                snapshot_id=snapshot_id,
                                strategy_id=strategy_id,
                                symbol=symbol,
                                side=intent.get("side", "BUY").upper(),
                                instrument=intent.get("instrument", "EQUITY").upper(),
                                idempotency_key=idempotency_key,
                                receipt=intent,
                                qty=intent.get("qty"),
                                limit_price=intent.get("limit_price"),
                            )
                        except Exception as intent_err:
                            logger.error(f"Hub intent write error: {intent_err}")
                
                # Also publish to state for local consumption
                set_state("strategy.intents", json.dumps(intents))
                set_state("strategy.intent_count", len(intents))
                set_state("strategy.last_run_ts", datetime.utcnow().isoformat() + "Z")
                
                logger.log("strategy_signals_generated", {
                    "loop": loop_count,
                    "intent_count": len(intents),
                    "equity": equity,
                    "vix": vix
                })
                
                print(f"[{datetime.now().strftime('%H:%M:%S')}] STRATEGY #{loop_count}: {len(intents)} intents, equity=${equity}")
            
        except Exception as e:
            logger.error(f"Strategy loop #{loop_count} error: {e}")
            print(f"[ERROR] STRATEGY #{loop_count}: {e}")
        
        # Sleep
        elapsed = time.time() - loop_start
        sleep_time = max(0, loop_interval - elapsed)
        sleep_remaining = sleep_time
        while sleep_remaining > 0 and not shutdown_requested:
            time.sleep(min(0.5, sleep_remaining))
            sleep_remaining -= 0.5
    
    if hub:
        hub.close()
    logger.log("role_strategy_stopped", {"total_loops": loop_count})
    print(f"[INFO] Role STRATEGY stopped after {loop_count} loops")


def _run_role_execution(config: RoleConfig, loop_interval: int, logger) -> None:
    """
    Role: EXECUTION - Only intent consumption and order placement
    
    This role is the ONLY one that submits entry orders.
    
    This role:
    - Leases intents from hub or reads from state
    - Validates intents against risk rules
    - Submits orders to broker
    - Acks/fails intents in hub
    - Records order events
    """
    import time
    import signal
    import json
    from datetime import datetime
    
    shutdown_requested = False
    
    def signal_handler(signum, frame):
        nonlocal shutdown_requested
        shutdown_requested = True
        logger.log("shutdown_requested", {"signal": signum, "role": "execution"})
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Import execution components
    from trading_hydra.core.state import get_state, set_state
    from trading_hydra.core.halt import get_halt_manager
    from trading_hydra.services.alpaca_client import get_alpaca_client
    
    alpaca = get_alpaca_client()
    halt = get_halt_manager()
    hub = _try_create_hub_store()
    
    if hub:
        from trading_hydra.hub.hub_store_mysql import HubStoreMySQL
        worker_id = HubStoreMySQL.make_worker_id("execution")
        print(f"[INFO] Hub MySQL connected - leasing intents as {worker_id}")
    else:
        worker_id = None
        print("[INFO] Hub MySQL not configured - using local state only")
    
    loop_count = 0
    
    logger.log("role_execution_starting", {"interval": loop_interval, "hub_enabled": hub is not None})
    print(f"[INFO] Role EXECUTION: Order placement only, interval={loop_interval}s")
    
    while not shutdown_requested:
        loop_count += 1
        loop_start = time.time()
        
        try:
            # Check halt status
            if halt.is_halted():
                status = halt.get_status()
                logger.log("execution_halted", {"reason": status.reason})
                print(f"[{datetime.now().strftime('%H:%M:%S')}] EXECUTION #{loop_count}: HALTED - {status.reason}")
                time.sleep(loop_interval)
                continue
            
            # Check hub posture
            if hub:
                try:
                    hub.upsert_heartbeat(
                        bot_name=config.hostname,
                        role="execution",
                        host=config.hostname,
                        pid=config.pid,
                        meta={"loop": loop_count}
                    )
                    posture = hub.get_posture()
                    if posture == "HALT":
                        logger.log("execution_posture_halt", {"posture": posture})
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] EXECUTION #{loop_count}: HALTED (posture={posture})")
                        time.sleep(loop_interval)
                        continue
                except Exception as hub_err:
                    logger.error(f"Hub posture check error: {hub_err}")
            
            # Lease intents from hub or read from state
            intents = []
            leased_ids = []
            
            if hub and worker_id:
                try:
                    lease_result = hub.lease_intents(leased_by=worker_id, limit=5, lease_seconds=45)
                    leased_ids = lease_result.leased_intent_ids
                    if leased_ids:
                        intents = hub.get_intents_by_ids(leased_ids)
                except Exception as lease_err:
                    logger.error(f"Hub lease error: {lease_err}")
            
            if not intents:
                intents_json = get_state("strategy.intents", "[]")
                intents = json.loads(intents_json) if intents_json else []
            
            orders_placed = 0
            
            for intent in intents:
                intent_id = intent.get("intent_id")
                try:
                    symbol = intent.get("symbol", "")
                    side = intent.get("side", "")
                    qty = intent.get("qty")
                    
                    # Validate intent has required fields
                    if not symbol or not side:
                        logger.log("intent_invalid", {"intent": intent})
                        if hub and worker_id and intent_id:
                            hub.fail_intent(intent_id=intent_id, leased_by=worker_id, error_text="Missing required fields")
                        continue
                    
                    # Mark submitted in hub
                    if hub and worker_id and intent_id:
                        hub.mark_intent_submitted(intent_id=intent_id, leased_by=worker_id)
                    
                    # Place order (placeholder - production would submit to broker)
                    # order = alpaca.submit_order(symbol=symbol, side=side, qty=qty, ...)
                    # orders_placed += 1
                    
                    # Record order event in hub
                    if hub and intent_id:
                        hub.insert_order_event(
                            intent_id=intent_id,
                            strategy_id=intent.get("strategy_id"),
                            symbol=symbol,
                            broker_order_id=None,
                            event_type="SUBMIT",
                            qty=qty
                        )
                    
                    # Ack intent in hub
                    if hub and worker_id and intent_id:
                        hub.ack_intent(intent_id=intent_id, leased_by=worker_id)
                    
                    logger.log("intent_processed", {
                        "symbol": symbol,
                        "side": side,
                        "qty": qty,
                        "intent_id": intent_id
                    })
                    
                except Exception as order_error:
                    logger.error(f"Order placement failed: {order_error}")
                    if hub and worker_id and intent_id:
                        hub.fail_intent(intent_id=intent_id, leased_by=worker_id, error_text=str(order_error)[:512])
            
            # Clear processed intents from local state
            set_state("strategy.intents", "[]")
            set_state("execution.last_run_ts", datetime.utcnow().isoformat() + "Z")
            set_state("execution.orders_placed", orders_placed)
            
            logger.log("execution_loop_complete", {
                "loop": loop_count,
                "intents_processed": len(intents),
                "orders_placed": orders_placed
            })
            
            print(f"[{datetime.now().strftime('%H:%M:%S')}] EXECUTION #{loop_count}: {len(intents)} intents, {orders_placed} orders")
            
        except Exception as e:
            logger.error(f"Execution loop #{loop_count} error: {e}")
            print(f"[ERROR] EXECUTION #{loop_count}: {e}")
        
        # Sleep
        elapsed = time.time() - loop_start
        sleep_time = max(0, loop_interval - elapsed)
        sleep_remaining = sleep_time
        while sleep_remaining > 0 and not shutdown_requested:
            time.sleep(min(0.5, sleep_remaining))
            sleep_remaining -= 0.5
    
    if hub:
        hub.close()
    logger.log("role_execution_stopped", {"total_loops": loop_count})
    print(f"[INFO] Role EXECUTION stopped after {loop_count} loops")


def _run_role_exit(config: RoleConfig, loop_interval: int, logger) -> None:
    """
    Role: EXIT - Only exit/position management (stops/TPs/close logic)
    
    This role can submit exit orders if needed.
    
    This role:
    - Monitors existing positions
    - Updates trailing stops
    - Triggers exits when stops/TPs hit
    - Manages position closures
    - Upserts positions to hub
    - Writes pnl events and enforces kill-switches
    """
    import time
    import signal
    from datetime import datetime
    
    shutdown_requested = False
    
    def signal_handler(signum, frame):
        nonlocal shutdown_requested
        shutdown_requested = True
        logger.log("shutdown_requested", {"signal": signum, "role": "exit"})
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Import exit management components
    from trading_hydra.core.state import get_state, set_state
    from trading_hydra.core.halt import get_halt_manager
    from trading_hydra.services.exitbot import get_exitbot
    from trading_hydra.services.alpaca_client import get_alpaca_client
    
    exitbot = get_exitbot()
    alpaca = get_alpaca_client()
    halt = get_halt_manager()
    hub = _try_create_hub_store()
    
    if hub:
        print("[INFO] Hub MySQL connected - positions will be synced to hub")
    else:
        print("[INFO] Hub MySQL not configured - using local state only")
    
    loop_count = 0
    
    logger.log("role_exit_starting", {"interval": loop_interval, "hub_enabled": hub is not None})
    print(f"[INFO] Role EXIT: Position management only, interval={loop_interval}s")
    
    while not shutdown_requested:
        loop_count += 1
        loop_start = time.time()
        
        try:
            # Write heartbeat
            if hub:
                try:
                    hub.upsert_heartbeat(
                        bot_name=config.hostname,
                        role="exit",
                        host=config.hostname,
                        pid=config.pid,
                        meta={"loop": loop_count}
                    )
                except Exception as hub_err:
                    logger.error(f"Hub heartbeat error: {hub_err}")
            
            # Get current equity for P&L calculations
            account = alpaca.get_account()
            equity = float(account.equity)
            
            # Get day start equity
            day_start_equity = get_state("day_start_equity", equity)
            
            # Sync positions to hub
            if hub:
                try:
                    positions = alpaca.get_positions()
                    for pos in positions:
                        hub.upsert_position(
                            position_id=str(pos.asset_id),
                            symbol=pos.symbol,
                            instrument="OPTION" if hasattr(pos, 'contract_type') else "EQUITY",
                            qty=int(pos.qty),
                            avg_price=float(pos.avg_entry_price) if pos.avg_entry_price else None,
                            market_price=float(pos.current_price) if pos.current_price else None,
                            unrealized_pnl=float(pos.unrealized_pl) if pos.unrealized_pl else None,
                            realized_pnl=None,
                            status="OPEN",
                            meta={"side": pos.side}
                        )
                except Exception as pos_err:
                    logger.error(f"Hub position sync error: {pos_err}")
            
            # Run exitbot - this handles:
            # - Trailing stop updates
            # - Stop-loss triggers
            # - Take-profit triggers  
            # - Daily P&L limit checks
            # - Health monitoring
            exitbot_result = exitbot.run(equity, day_start_equity)
            
            # Check if any positions were closed, record pnl events
            if hub and hasattr(exitbot_result, 'closed_positions'):
                for closed in getattr(exitbot_result, 'closed_positions', []):
                    try:
                        position_id = closed.get("position_id", "unknown")
                        strategy_id = closed.get("strategy_id")
                        symbol = closed.get("symbol", "")
                        realized_pnl = closed.get("realized_pnl", 0.0)
                        
                        # Record PnL event
                        hub.insert_pnl_event(
                            position_id=position_id,
                            symbol=symbol,
                            realized_pnl=realized_pnl,
                            strategy_id=strategy_id,
                            notes=f"Closed by exitbot: {closed.get('reason', 'unknown')}"
                        )
                        
                        # Enforce kill-switch if strategy_id is known
                        if strategy_id:
                            hub.enforce_kill_switch_from_recent_pnl(
                                strategy_id=strategy_id,
                                rolling_trades=20,
                                max_drawdown=250.0,
                                cooloff_minutes=120
                            )
                        
                        # Update position status in hub
                        hub.upsert_position(
                            position_id=position_id,
                            symbol=symbol,
                            instrument=closed.get("instrument", "EQUITY"),
                            qty=0,
                            avg_price=None,
                            market_price=None,
                            unrealized_pnl=None,
                            realized_pnl=realized_pnl,
                            status="CLOSED"
                        )
                    except Exception as pnl_err:
                        logger.error(f"Hub PnL event error: {pnl_err}")
            
            set_state("exit.last_run_ts", datetime.utcnow().isoformat() + "Z")
            set_state("exit.trailing_stops_active", exitbot_result.trailing_stops_active)
            
            logger.log("exit_loop_complete", {
                "loop": loop_count,
                "equity": equity,
                "daily_pnl": equity - day_start_equity,
                "trailing_stops_active": exitbot_result.trailing_stops_active,
                "should_continue": exitbot_result.should_continue
            })
            
            status = "OK" if exitbot_result.should_continue else f"HALT: {exitbot_result.halt_reason}"
            print(f"[{datetime.now().strftime('%H:%M:%S')}] EXIT #{loop_count}: {status}, stops={exitbot_result.trailing_stops_active}")
            
            # If exitbot triggers halt, log it prominently
            if not exitbot_result.should_continue:
                logger.log("exit_triggered_halt", {"reason": exitbot_result.halt_reason})
                print(f"[HALT] Exit role triggered halt: {exitbot_result.halt_reason}")
            
        except Exception as e:
            logger.error(f"Exit loop #{loop_count} error: {e}")
            print(f"[ERROR] EXIT #{loop_count}: {e}")
        
        # Sleep
        elapsed = time.time() - loop_start
        sleep_time = max(0, loop_interval - elapsed)
        sleep_remaining = sleep_time
        while sleep_remaining > 0 and not shutdown_requested:
            time.sleep(min(0.5, sleep_remaining))
            sleep_remaining -= 0.5
    
    if hub:
        hub.close()
    logger.log("role_exit_stopped", {"total_loops": loop_count})
    print(f"[INFO] Role EXIT stopped after {loop_count} loops")


def _start_dashboard_thread():
    """Start Flask dashboard in thread (for role=all with dashboard enabled)"""
    try:
        from trading_hydra.dashboard import create_app
        import logging as flask_logging
        
        flask_logging.getLogger('werkzeug').setLevel(flask_logging.ERROR)
        
        app = create_app()
        app.run(
            host='0.0.0.0',
            port=5000,
            threaded=True,
            use_reloader=False,
            debug=False
        )
    except ImportError as e:
        print(f"[WARNING] Dashboard not available: {e}")
    except Exception as e:
        print(f"[ERROR] Dashboard failed: {e}")
