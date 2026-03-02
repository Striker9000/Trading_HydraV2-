"""PortfolioBot service for dynamic budget allocation"""
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from ..core.logging import get_logger
from ..core.config import load_settings, load_bots_config
from ..core.state import get_state, set_state
from ..core.risk import dollars_from_pct


@dataclass
class PortfolioBotResult:
    budgets_set: bool
    daily_risk: float
    enabled_bots: List[str]
    error: str


class PortfolioBot:
    def __init__(self):
        self._logger = get_logger()
    
    def run(self, equity: float) -> PortfolioBotResult:
        self._logger.log("portfoliobot_start", {"equity": equity})
        
        try:
            config = load_bots_config()
            settings = load_settings()
        except Exception as e:
            self._logger.error(f"PortfolioBot config load failed: {e}")
            return PortfolioBotResult(
                budgets_set=False,
                daily_risk=0,
                enabled_bots=[],
                error=str(e)
            )
        
        portfolio_config = config.get("portfoliobot", {})
        
        if not portfolio_config.get("enabled", True):
            self._logger.log("portfoliobot_disabled", {})
            return PortfolioBotResult(
                budgets_set=False,
                daily_risk=0,
                enabled_bots=[],
                error=""
            )
        
        day_start_equity = get_state("day_start_equity", equity) or equity
        risk_config = settings.get("risk", {})
        max_loss_pct = risk_config.get("global_max_daily_loss_pct", 1.0)
        daily_risk = dollars_from_pct(day_start_equity, max_loss_pct)
        
        buckets = portfolio_config.get("buckets", {})
        mom_bucket = daily_risk * (buckets.get("momentum_bucket_pct_of_daily_risk", 50) / 100)
        opt_bucket = daily_risk * (buckets.get("options_bucket_pct_of_daily_risk", 50) / 100)
        cry_bucket = daily_risk * (buckets.get("crypto_bucket_pct_of_daily_risk", 25) / 100)
        bounce_bucket = daily_risk * (buckets.get("bounce_bucket_pct_of_daily_risk", 10) / 100)
        twentymin_bucket = daily_risk * (buckets.get("twentymin_bucket_pct_of_daily_risk", 15) / 100)
        hailmary_bucket = daily_risk * (buckets.get("hailmary_bucket_pct_of_daily_risk", 30) / 100)
        
        guardrails = portfolio_config.get("guardrails", {})
        per_min = daily_risk * (guardrails.get("per_bot_min_pct_of_daily_risk", 10) / 100)
        per_max = daily_risk * (guardrails.get("per_bot_max_pct_of_daily_risk", 50) / 100)
        
        enabled_bots = []
        
        momentum_bots = config.get("momentum_bots", [])
        enabled_momentum = [b for b in momentum_bots if b.get("enabled", False)]
        num_mom = max(1, len(enabled_momentum))
        mom_each = max(per_min, min(per_max, mom_bucket / num_mom))
        
        for bot in momentum_bots:
            bot_id = bot.get("bot_id", "")
            risk_cfg = bot.get("risk", {})
            
            set_state(f"budgets.{bot_id}", {
                "max_daily_loss": mom_each,
                "max_open_risk": mom_each * 2,
                "max_trades_per_day": risk_cfg.get("max_trades_per_day", 5),
                "max_concurrent_positions": risk_cfg.get("max_concurrent_positions", 2)
            })
            set_state(f"bots.{bot_id}", {
                "allowed": True,
                "enabled": bot.get("enabled", False)
            })
            
            if bot.get("enabled", False):
                enabled_bots.append(bot_id)
        
        # Count enabled options bots for budget splitting
        optionsbot = config.get("optionsbot", {})
        optionsbot_0dte = config.get("optionsbot_0dte", {})
        opt_core_enabled = optionsbot.get("enabled", False)
        opt_0dte_enabled = optionsbot_0dte.get("enabled", False)
        num_opts = (1 if opt_core_enabled else 0) + (1 if opt_0dte_enabled else 0)
        
        # Split options bucket between enabled options bots (60/40 if both, 100% if one)
        if num_opts == 2:
            opt_core_share = opt_bucket * 0.6
            opt_0dte_share = opt_bucket * 0.4
        else:
            opt_core_share = opt_bucket if opt_core_enabled else 0
            opt_0dte_share = opt_bucket if opt_0dte_enabled else 0
        
        # Standard options bot (multi-day DTE)
        if opt_core_enabled:
            bot_id = optionsbot.get("bot_id", "opt_core")
            risk_cfg = optionsbot.get("risk", {})
            
            # Clamp to guardrails
            opt_core_budget = max(per_min, min(per_max, opt_core_share))
            
            set_state(f"budgets.{bot_id}", {
                "max_daily_loss": opt_core_budget,
                "max_open_risk": opt_core_budget * 2,
                "max_trades_per_day": risk_cfg.get("max_trades_per_day", 3),
                "max_concurrent_positions": risk_cfg.get("max_concurrent_positions", 2)
            })
            set_state(f"bots.{bot_id}", {"allowed": True, "enabled": True})
            enabled_bots.append(bot_id)
        
        # 0DTE options bot (same-day expiration)
        if opt_0dte_enabled:
            bot_id = optionsbot_0dte.get("bot_id", "opt_0dte")
            risk_cfg = optionsbot_0dte.get("risk", {})
            
            # Clamp to guardrails (0DTE gets smaller allocation)
            opt_0dte_budget = max(per_min, min(per_max, opt_0dte_share))
            
            set_state(f"budgets.{bot_id}", {
                "max_daily_loss": opt_0dte_budget,
                "max_open_risk": opt_0dte_budget * 1.5,
                "max_trades_per_day": risk_cfg.get("max_trades_per_day", 5),
                "max_concurrent_positions": risk_cfg.get("max_concurrent_positions", 2)
            })
            set_state(f"bots.{bot_id}", {"allowed": True, "enabled": True})
            enabled_bots.append(bot_id)
        
        cryptobot = config.get("cryptobot", {})
        if cryptobot.get("enabled", False):
            bot_id = cryptobot.get("bot_id", "crypto_core")
            risk_cfg = cryptobot.get("risk", {})
            
            set_state(f"budgets.{bot_id}", {
                "max_daily_loss": cry_bucket,
                "max_open_risk": cry_bucket * 2,
                "max_trades_per_day": risk_cfg.get("max_trades_per_day", 5),
                "max_concurrent_positions": risk_cfg.get("max_concurrent_positions", 3)
            })
            set_state(f"bots.{bot_id}", {"allowed": True, "enabled": True})
            enabled_bots.append(bot_id)
        
        bouncebot = config.get("bouncebot", {})
        if bouncebot.get("enabled", False):
            bot_id = bouncebot.get("bot_id", "bounce_core")
            risk_cfg = bouncebot.get("risk", {})
            
            bounce_budget = max(per_min, min(per_max, bounce_bucket))
            
            set_state(f"budgets.{bot_id}", {
                "max_daily_loss": bounce_budget,
                "max_open_risk": bounce_budget * 2,
                "max_trades_per_day": risk_cfg.get("max_trades_per_session", 2),
                "max_concurrent_positions": 2
            })
            set_state(f"bots.{bot_id}", {"allowed": True, "enabled": True})
            enabled_bots.append(bot_id)
        
        twentymin_bot = config.get("twentyminute_bot", {})
        if twentymin_bot.get("enabled", False):
            bot_id = twentymin_bot.get("bot_id", "twentymin_core")
            risk_cfg = twentymin_bot.get("risk", {})
            
            twentymin_budget = max(per_min, min(per_max, twentymin_bucket))
            
            set_state(f"budgets.{bot_id}", {
                "max_daily_loss": twentymin_budget,
                "max_open_risk": twentymin_budget * 2,
                "max_trades_per_day": risk_cfg.get("max_trades_per_day", 5),
                "max_concurrent_positions": risk_cfg.get("max_concurrent_positions", 3)
            })
            set_state(f"bots.{bot_id}", {"allowed": True, "enabled": True})
            enabled_bots.append(bot_id)
        
        hailmary_bot_cfg = config.get("hailmary_bot", {})
        if hailmary_bot_cfg.get("enabled", False):
            bot_id = hailmary_bot_cfg.get("bot_id", "hm_core")
            
            hm_budget = max(per_min, min(per_max, hailmary_bucket))
            
            set_state(f"budgets.{bot_id}", {
                "max_daily_loss": hm_budget,
                "max_open_risk": hm_budget * 2,
                "max_trades_per_day": hailmary_bot_cfg.get("max_trades_per_day", 5),
                "max_concurrent_positions": 5
            })
            set_state(f"bots.{bot_id}", {"allowed": True, "enabled": True})
            enabled_bots.append(bot_id)
        
        self._logger.log("portfoliobot_budgets", {
            "equity": equity,
            "day_start_equity": day_start_equity,
            "daily_risk": round(daily_risk, 2),
            "mom_bucket": round(mom_bucket, 2),
            "opt_bucket": round(opt_bucket, 2),
            "cry_bucket": round(cry_bucket, 2),
            "bounce_bucket": round(bounce_bucket, 2),
            "twentymin_bucket": round(twentymin_bucket, 2),
            "hailmary_bucket": round(hailmary_bucket, 2),
            "mom_each": round(mom_each, 2),
            "enabled_bots": enabled_bots
        })
        
        return PortfolioBotResult(
            budgets_set=True,
            daily_risk=daily_risk,
            enabled_bots=enabled_bots,
            error=""
        )


_portfoliobot: Optional[PortfolioBot] = None


def get_portfoliobot() -> PortfolioBot:
    global _portfoliobot
    if _portfoliobot is None:
        _portfoliobot = PortfolioBot()
    return _portfoliobot
