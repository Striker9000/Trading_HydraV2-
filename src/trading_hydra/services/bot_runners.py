"""
Bot Runners for Dedicated Threads
==================================

Provides run functions for each bot that can be executed by dedicated threads.
These are thin wrappers that handle setup and call the actual bot execute methods.

Each runner:
1. Gets necessary context (equity, positions, config)
2. Calls the bot's main execute method
3. Returns results for logging

Usage:
    from .bot_runners import create_bot_runners
    
    runners = create_bot_runners()
    result = runners['exitbot']()  # Run ExitBot once
"""

from typing import Dict, Any, Callable, Optional
from datetime import datetime

from ..core.logging import get_logger
from ..core.state import get_state, set_state
from ..core.config import load_bots_config, load_settings
from ..core.halt import get_halt_manager
from .alpaca_client import get_alpaca_client


def create_exitbot_runner() -> Callable[[], Dict[str, Any]]:
    """Create a runner function for ExitBot."""
    from .exitbot import get_exitbot
    
    logger = get_logger()
    exitbot = get_exitbot()
    alpaca = get_alpaca_client()
    
    def run() -> Dict[str, Any]:
        try:
            # Get account equity
            account = alpaca.get_account()
            equity = float(account.equity)
            day_start_equity = get_state("day_start_equity", equity)
            
            # Run exitbot
            result = exitbot.run(equity, day_start_equity)
            
            return {
                "success": True,
                "should_continue": result.should_continue,
                "trailing_stops_active": result.trailing_stops_active,
                "equity": equity
            }
        except Exception as e:
            logger.error(f"ExitBot runner error: {e}")
            return {"success": False, "error": str(e)}
    
    return run


def create_crypto_runner() -> Callable[[], Dict[str, Any]]:
    """Create a runner function for CryptoBot."""
    from ..bots.crypto_bot import get_crypto_bot
    
    logger = get_logger()
    alpaca = get_alpaca_client()
    
    def run() -> Dict[str, Any]:
        try:
            # Get crypto bot instance
            crypto_bot = get_crypto_bot("crypto_core")
            
            # Get account equity for budget
            account = alpaca.get_account()
            equity = float(account.equity)
            
            # Load budget from state (PortfolioBot sets budgets.{bot_id} as dict with max_daily_loss)
            budget_state = get_state("budgets.crypto_core", {})
            crypto_budget = budget_state.get("max_daily_loss", 600.0) if isinstance(budget_state, dict) else 600.0
            
            # Execute crypto bot
            result = crypto_bot.execute(max_daily_loss=crypto_budget)
            
            return {
                "success": True,
                "trades_attempted": result.get("trades_attempted", 0),
                "positions_managed": result.get("positions_managed", 0),
                "errors": result.get("errors", [])
            }
        except Exception as e:
            logger.error(f"CryptoBot runner error: {e}")
            return {"success": False, "error": str(e)}
    
    return run


def create_twentymin_runner() -> Callable[[], Dict[str, Any]]:
    """Create a runner function for TwentyMinBot."""
    from ..bots.twenty_minute_bot import get_twenty_minute_bot
    
    logger = get_logger()
    alpaca = get_alpaca_client()
    
    def run() -> Dict[str, Any]:
        try:
            # Get twentymin bot instance
            bot = get_twenty_minute_bot("twentymin_core")
            
            # Get budget from state (PortfolioBot sets budgets.{bot_id} as dict with max_daily_loss)
            budget_state = get_state("budgets.twentymin_core", {})
            twentymin_budget = budget_state.get("max_daily_loss", 450.0) if isinstance(budget_state, dict) else 450.0
            
            # Execute bot
            result = bot.execute(budget=twentymin_budget)
            
            return {
                "success": True,
                "trades_attempted": result.get("trades_attempted", 0),
                "positions_managed": result.get("positions_managed", 0),
                "patterns_detected": result.get("patterns_detected", 0),
                "errors": result.get("errors", [])
            }
        except Exception as e:
            logger.error(f"TwentyMinBot runner error: {e}")
            return {"success": False, "error": str(e)}
    
    return run


def create_bounce_runner() -> Callable[[], Dict[str, Any]]:
    """Create a runner function for BounceBot."""
    from ..bots.bounce_bot import get_bounce_bot
    
    logger = get_logger()
    
    def run() -> Dict[str, Any]:
        try:
            # Get bounce bot instance
            bot = get_bounce_bot("bounce_core")
            
            # Get budget from state (PortfolioBot sets budgets.{bot_id} as dict with max_daily_loss)
            budget_state = get_state("budgets.bounce_core", {})
            bounce_budget = budget_state.get("max_daily_loss", 300.0) if isinstance(budget_state, dict) else 300.0
            
            # Execute bot
            result = bot.execute(max_daily_loss=bounce_budget)
            
            return {
                "success": True,
                "trades_attempted": result.get("trades_attempted", 0),
                "positions_managed": result.get("positions_managed", 0),
                "errors": result.get("errors", [])
            }
        except Exception as e:
            logger.error(f"BounceBot runner error: {e}")
            return {"success": False, "error": str(e)}
    
    return run


def create_options_core_runner() -> Callable[[], Dict[str, Any]]:
    """Create a runner function for OptionsBot Core."""
    from ..bots.options_bot import get_options_bot
    
    logger = get_logger()
    
    def run() -> Dict[str, Any]:
        try:
            # Get options bot instance
            bot = get_options_bot("opt_core")
            
            # Get budget from state (PortfolioBot sets budgets.{bot_id} as dict with max_daily_loss)
            budget_state = get_state("budgets.opt_core", {})
            options_budget = budget_state.get("max_daily_loss", 630.0) if isinstance(budget_state, dict) else 630.0
            
            # Execute bot
            result = bot.execute(max_daily_loss=options_budget)
            
            return {
                "success": True,
                "trades_attempted": result.get("trades_attempted", 0),
                "positions_managed": result.get("positions_managed", 0),
                "strategies_analyzed": result.get("strategies_analyzed", 0),
                "errors": result.get("errors", [])
            }
        except Exception as e:
            logger.error(f"OptionsBot Core runner error: {e}")
            return {"success": False, "error": str(e)}
    
    return run


def create_options_0dte_runner() -> Callable[[], Dict[str, Any]]:
    """Create a runner function for OptionsBot 0DTE."""
    from ..bots.options_bot import get_options_bot
    
    logger = get_logger()
    
    def run() -> Dict[str, Any]:
        try:
            # Get options 0dte bot instance
            bot = get_options_bot("opt_0dte")
            
            # Get budget from state (PortfolioBot sets budgets.{bot_id} as dict with max_daily_loss)
            budget_state = get_state("budgets.opt_0dte", {})
            options_budget = budget_state.get("max_daily_loss", 420.0) if isinstance(budget_state, dict) else 420.0
            
            # Execute bot
            result = bot.execute(max_daily_loss=options_budget)
            
            return {
                "success": True,
                "trades_attempted": result.get("trades_attempted", 0),
                "positions_managed": result.get("positions_managed", 0),
                "strategies_analyzed": result.get("strategies_analyzed", 0),
                "errors": result.get("errors", [])
            }
        except Exception as e:
            logger.error(f"OptionsBot 0DTE runner error: {e}")
            return {"success": False, "error": str(e)}
    
    return run


def create_hailmary_runner() -> Callable[[], Dict[str, Any]]:
    """Create a runner function for standalone HailMary Bot."""
    from ..bots.hail_mary_bot import get_hail_mary_bot

    logger = get_logger()

    def run() -> Dict[str, Any]:
        try:
            bot = get_hail_mary_bot("hm_core")

            budget_state = get_state("budgets.hm_core", {})
            hm_budget = budget_state.get("max_daily_loss", 500.0) if isinstance(budget_state, dict) else 500.0

            result = bot.execute(max_daily_loss=hm_budget)

            return {
                "success": True,
                "trades_attempted": result.get("trades_attempted", 0),
                "positions_managed": result.get("positions_managed", 0),
                "errors": result.get("errors", [])
            }
        except Exception as e:
            logger.error(f"HailMary Bot runner error: {e}")
            return {"success": False, "error": str(e)}

    return run


def create_bot_runners() -> Dict[str, Callable[[], Dict[str, Any]]]:
    """
    Create all bot runner functions.
    
    Returns:
        Dict mapping bot_id to runner function
    """
    return {
        "exitbot": create_exitbot_runner(),
        "crypto_core": create_crypto_runner(),
        "twentymin_core": create_twentymin_runner(),
        "bounce_core": create_bounce_runner(),
        "opt_core": create_options_core_runner(),
        "opt_0dte": create_options_0dte_runner(),
        "hm_core": create_hailmary_runner(),
    }


# List of bot_ids that run in dedicated threads (excluded from main loop)
DEDICATED_THREAD_BOTS = {
    "exitbot",
}


def is_dedicated_thread_bot(bot_id: str) -> bool:
    """Check if a bot runs in a dedicated thread."""
    return bot_id in DEDICATED_THREAD_BOTS
