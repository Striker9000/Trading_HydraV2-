
"""Paper safety lock to prevent accidental live trading"""
import os
from typing import Dict, Any

from .logging import get_logger
from .config import load_settings


class PaperSafetyLock:
    """Enforces paper trading unless explicitly overridden"""
    
    def __init__(self):
        self._logger = get_logger()
    
    def check_safety_requirements(self) -> Dict[str, Any]:
        """Check if system is safe to run with current configuration"""
        result = {
            "safe": False,
            "mode": "unknown",
            "errors": [],
            "warnings": []
        }
        
        try:
            # Check ALPACA_PAPER environment variable
            alpaca_paper = os.environ.get("ALPACA_PAPER", "true").lower()
            is_paper_mode = alpaca_paper in ("true", "1", "yes")
            
            if is_paper_mode:
                result["safe"] = True
                result["mode"] = "paper"
                self._logger.log("safety_check_paper_mode", {"safe": True})
            else:
                # Live mode - check for explicit override
                settings = load_settings()
                trading_config = settings.get("trading", {})
                allow_live = trading_config.get("allow_live", False)
                
                if allow_live:
                    result["safe"] = True
                    result["mode"] = "live"
                    result["warnings"].append("LIVE TRADING ENABLED - Real money at risk!")
                    self._logger.log("safety_check_live_mode", {
                        "safe": True,
                        "warning": "Live trading explicitly enabled"
                    })
                else:
                    result["safe"] = False
                    result["mode"] = "live_blocked"
                    result["errors"].append(
                        "Live trading blocked: Set trading.allow_live = true in settings.yaml "
                        "to enable live trading with real money"
                    )
                    self._logger.log("safety_check_live_blocked", {
                        "safe": False,
                        "reason": "allow_live not set"
                    })
            
        except Exception as e:
            result["errors"].append(f"Safety check failed: {e}")
            self._logger.error(f"Paper safety check error: {e}")
        
        return result
    
    def enforce_safety_or_exit(self) -> None:
        """Enforce safety requirements or exit the application"""
        safety_result = self.check_safety_requirements()
        
        if not safety_result["safe"]:
            print("🚨 TRADING SAFETY LOCK ENGAGED 🚨")
            print()
            for error in safety_result["errors"]:
                print(f"❌ {error}")
            print()
            print("System will not start until safety requirements are met.")
            print("This protects against accidental live trading.")
            print()
            exit(1)
        
        # Safe to proceed - log mode
        if safety_result["mode"] == "paper":
            print("✅ PAPER TRADING MODE - Safe to proceed")
        elif safety_result["mode"] == "live":
            print("⚠️  LIVE TRADING MODE - Real money at risk!")
            for warning in safety_result["warnings"]:
                print(f"⚠️  {warning}")
            print()
        
        self._logger.log("safety_lock_passed", safety_result)


def get_paper_safety_lock() -> PaperSafetyLock:
    """Get the paper safety lock instance"""
    return PaperSafetyLock()
