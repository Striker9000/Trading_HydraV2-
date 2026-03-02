"""
Liquidity Filter - Bid-Ask Spread and Volume Filters for Options.

Ensures we only trade options with sufficient liquidity to avoid
getting trapped in wide-spread positions.

Key filters:
- Bid-ask spread as % of mid price
- Dollar volume (not just contract count)
- Open interest thresholds
- Real-time spread check before order
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, List, Optional

from ..core.logging import get_logger
from ..core.config import load_bots_config


@dataclass
class LiquidityCheck:
    """Result of liquidity check."""
    symbol: str
    passed: bool
    spread_pct: float
    spread_limit: float
    dollar_volume: float
    volume_limit: float
    open_interest: int
    oi_limit: int
    rejection_reason: Optional[str] = None


class LiquidityFilter:
    """
    Filter options by liquidity metrics.
    
    Philosophy:
    - Wide spreads eat into profits
    - Low volume = hard to exit
    - Even if OI is high, check current bid-ask
    
    Default limits:
    - Max spread: 10% of mid price
    - Min dollar volume: $50,000/day
    - Min open interest: 100 contracts
    """
    
    # Default thresholds
    DEFAULT_MAX_SPREAD_PCT = 10.0  # 10% of mid price
    DEFAULT_MIN_DOLLAR_VOLUME = 50000  # $50k daily
    DEFAULT_MIN_OPEN_INTEREST = 100
    
    # Relaxed thresholds for first 20 minutes of session
    EARLY_SESSION_MAX_SPREAD_PCT = 15.0
    EARLY_SESSION_MIN_OI = 50
    
    # Outside market hours - very relaxed spread (configurable)
    DEFAULT_OUTSIDE_HOURS_MAX_SPREAD_PCT = 50.0
    
    def __init__(self):
        self._logger = get_logger()
        self._config = self._load_config()
    
    def _load_config(self) -> Dict[str, Any]:
        """Load liquidity filter config from bots.yaml."""
        try:
            bots_config = load_bots_config()
            liquidity = bots_config.get("liquidity_filter", {})
            return {
                "max_spread_pct": liquidity.get("max_spread_pct", self.DEFAULT_MAX_SPREAD_PCT),
                "min_dollar_volume": liquidity.get("min_dollar_volume", self.DEFAULT_MIN_DOLLAR_VOLUME),
                "min_open_interest": liquidity.get("min_open_interest", self.DEFAULT_MIN_OPEN_INTEREST),
                "outside_hours_max_spread_pct": liquidity.get("outside_hours_max_spread_pct", self.DEFAULT_OUTSIDE_HOURS_MAX_SPREAD_PCT),
                "enabled": liquidity.get("enabled", True)
            }
        except Exception as e:
            self._logger.error(f"Failed to load liquidity config: {e}")
            return {
                "max_spread_pct": self.DEFAULT_MAX_SPREAD_PCT,
                "min_dollar_volume": self.DEFAULT_MIN_DOLLAR_VOLUME,
                "min_open_interest": self.DEFAULT_MIN_OPEN_INTEREST,
                "outside_hours_max_spread_pct": self.DEFAULT_OUTSIDE_HOURS_MAX_SPREAD_PCT,
                "enabled": True
            }
    
    def _is_early_session(self) -> bool:
        """Check if we're in first 20 minutes of market session."""
        try:
            from ..core.clock import get_market_clock
            clock = get_market_clock()
            now = clock.now_naive()
            
            # PST: Market opens at 6:30 AM
            if now.hour == 6 and 30 <= now.minute < 50:
                return True
            return False
        except Exception:
            return False
    
    def _is_market_hours(self) -> bool:
        """Check if we're during regular market hours (6:30 AM - 1:00 PM PST)."""
        try:
            from ..core.clock import get_market_clock
            clock = get_market_clock()
            now = clock.now_naive()
            
            # PST market hours: 6:30 AM to 1:00 PM
            if now.hour < 6 or (now.hour == 6 and now.minute < 30):
                return False  # Before market open
            if now.hour >= 13:
                return False  # After market close
            return True
        except Exception:
            return True  # Fail-safe: assume market hours
    
    def check_option_liquidity(
        self,
        symbol: str,
        bid: float,
        ask: float,
        volume: int,
        open_interest: int,
        underlying_price: Optional[float] = None
    ) -> LiquidityCheck:
        """
        Check if an option meets liquidity requirements.
        
        Args:
            symbol: Option symbol
            bid: Current bid price
            ask: Current ask price
            volume: Today's trading volume
            open_interest: Open interest
            underlying_price: Underlying stock price (for dollar volume calc)
            
        Returns:
            LiquidityCheck with pass/fail and details
        """
        if not self._config.get("enabled", True):
            return LiquidityCheck(
                symbol=symbol,
                passed=True,
                spread_pct=0,
                spread_limit=100,
                dollar_volume=0,
                volume_limit=0,
                open_interest=open_interest,
                oi_limit=0
            )
        
        # Track if we're outside market hours for relaxed spread thresholds
        # Outside hours: relax spread check only, keep OI/volume checks for safety
        is_outside_hours = not self._is_market_hours()
        
        # Calculate mid price and spread
        mid_price = (bid + ask) / 2 if (bid > 0 and ask > 0) else 0
        spread = ask - bid
        spread_pct = (spread / mid_price * 100) if mid_price > 0 else 100
        
        # Calculate dollar volume (contracts * 100 * mid price)
        dollar_volume = volume * 100 * mid_price if mid_price > 0 else 0
        
        # Get thresholds (relaxed during early session or outside hours)
        is_early = self._is_early_session()
        
        # Outside market hours: very relaxed spread (50%), keep OI/volume checks
        # Early session: moderately relaxed spread
        # Normal hours: strict thresholds
        if is_outside_hours:
            max_spread = self._config.get("outside_hours_max_spread_pct", 50.0)
            min_oi = self._config["min_open_interest"]  # Keep OI check
            min_volume = self._config["min_dollar_volume"]  # Keep volume check
        elif is_early:
            max_spread = self.EARLY_SESSION_MAX_SPREAD_PCT
            min_oi = self.EARLY_SESSION_MIN_OI
            min_volume = self._config["min_dollar_volume"]
        else:
            max_spread = self._config["max_spread_pct"]
            min_oi = self._config["min_open_interest"]
            min_volume = self._config["min_dollar_volume"]
        
        # Check each criterion
        rejection_reason = None
        passed = True
        
        if spread_pct > max_spread:
            passed = False
            rejection_reason = f"spread_too_wide_{spread_pct:.1f}%_limit_{max_spread:.1f}%"
        elif dollar_volume < min_volume and volume > 0:
            # Only fail on volume if there's some trading happening
            passed = False
            rejection_reason = f"dollar_volume_too_low_${dollar_volume:.0f}_limit_${min_volume:.0f}"
        elif open_interest < min_oi:
            passed = False
            rejection_reason = f"open_interest_too_low_{open_interest}_limit_{min_oi}"
        
        result = LiquidityCheck(
            symbol=symbol,
            passed=passed,
            spread_pct=round(spread_pct, 2),
            spread_limit=max_spread,
            dollar_volume=round(dollar_volume, 2),
            volume_limit=min_volume,
            open_interest=open_interest,
            oi_limit=min_oi,
            rejection_reason=rejection_reason
        )
        
        # Log
        if not passed:
            self._logger.log("liquidity_filter_rejected", {
                "symbol": symbol,
                "spread_pct": result.spread_pct,
                "dollar_volume": result.dollar_volume,
                "open_interest": open_interest,
                "reason": rejection_reason,
                "early_session": is_early,
                "outside_hours": is_outside_hours
            })
        
        return result
    
    def check_before_order(
        self,
        symbol: str,
        alpaca_client: Any,
        original_spread_pct: Optional[float] = None
    ) -> bool:
        """
        Real-time spread check before placing order.
        
        Fetches current quote to ensure spread hasn't widened
        since we decided to trade.
        
        Args:
            symbol: Option symbol to check
            alpaca_client: Alpaca client for quote fetch
            original_spread_pct: Spread when we decided to trade
            
        Returns:
            True if still OK to trade
        """
        try:
            quote = alpaca_client.get_option_quote(symbol)
            if not quote:
                self._logger.log("liquidity_preorder_check_failed", {
                    "symbol": symbol,
                    "reason": "no_quote"
                })
                return False
            
            bid = float(quote.get("bid", 0))
            ask = float(quote.get("ask", 0))
            mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
            spread_pct = ((ask - bid) / mid * 100) if mid > 0 else 100
            
            max_spread = self._config["max_spread_pct"]
            
            if spread_pct > max_spread:
                self._logger.log("liquidity_preorder_check_rejected", {
                    "symbol": symbol,
                    "current_spread_pct": round(spread_pct, 2),
                    "original_spread_pct": original_spread_pct,
                    "limit": max_spread,
                    "reason": "spread_widened"
                })
                return False
            
            # Warn if spread widened significantly
            if original_spread_pct and spread_pct > original_spread_pct * 1.5:
                self._logger.log("liquidity_preorder_check_warning", {
                    "symbol": symbol,
                    "current_spread_pct": round(spread_pct, 2),
                    "original_spread_pct": original_spread_pct,
                    "reason": "spread_increased_50pct"
                })
            
            return True
            
        except Exception as e:
            self._logger.error(f"Pre-order liquidity check failed for {symbol}: {e}")
            return False
    
    def filter_options_list(
        self,
        options: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Filter a list of options by liquidity.
        
        Args:
            options: List of option dicts with bid, ask, volume, open_interest
            
        Returns:
            Filtered list of liquid options
        """
        filtered = []
        
        for opt in options:
            check = self.check_option_liquidity(
                symbol=opt.get("symbol", ""),
                bid=float(opt.get("bid", 0)),
                ask=float(opt.get("ask", 0)),
                volume=int(opt.get("volume", 0)),
                open_interest=int(opt.get("open_interest", 0))
            )
            
            if check.passed:
                # Add liquidity metrics to option
                opt["liquidity_spread_pct"] = check.spread_pct
                opt["liquidity_dollar_volume"] = check.dollar_volume
                filtered.append(opt)
        
        self._logger.log("liquidity_filter_batch", {
            "input_count": len(options),
            "output_count": len(filtered),
            "rejected_count": len(options) - len(filtered)
        })
        
        return filtered


# Singleton
_liquidity_filter: Optional[LiquidityFilter] = None


def get_liquidity_filter() -> LiquidityFilter:
    """Get or create LiquidityFilter singleton."""
    global _liquidity_filter
    if _liquidity_filter is None:
        _liquidity_filter = LiquidityFilter()
    return _liquidity_filter
