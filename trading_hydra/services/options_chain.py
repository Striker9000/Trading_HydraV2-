"""
=============================================================================
Options Chain Provider - Options Chain Data for Strategy System
=============================================================================
Provides options chain data for strategy option selection.
Implements the OptionsChainProvider protocol required by OptionSelector.

Fetches option contracts from Alpaca with:
- Strike prices
- Expiration dates
- Greeks (delta, gamma, theta, vega)
- Bid/ask quotes
=============================================================================
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

from ..core.logging import get_logger
from ..strategy.options_selector import OptionContract


class OptionsChainProvider:
    """
    Options chain data provider using Alpaca API.
    
    Implements the protocol required by OptionSelector for
    fetching and filtering option contracts.
    """
    
    def __init__(self, alpaca_client: Any):
        """
        Initialize with Alpaca client.
        
        Args:
            alpaca_client: Alpaca client instance from alpaca_client.py
        """
        self._alpaca = alpaca_client
        self._logger = get_logger()
        self._chain_cache: Dict[str, List[OptionContract]] = {}
        self._cache_ts: Dict[str, float] = {}
        self._cache_duration = 60  # 1 minute cache for options (more volatile)
    
    def chain(self, symbol: str) -> List[OptionContract]:
        """
        Get options chain for a symbol.
        
        Args:
            symbol: Underlying stock symbol
            
        Returns:
            List of OptionContract objects
        """
        import time
        now = time.time()
        
        if symbol in self._chain_cache:
            if now - self._cache_ts.get(symbol, 0) < self._cache_duration:
                return self._chain_cache[symbol]
        
        try:
            raw_chain = self._alpaca.get_options_chain(symbol)
            contracts = self._parse_chain(raw_chain, symbol)
            
            self._chain_cache[symbol] = contracts
            self._cache_ts[symbol] = now
            
            return contracts
            
        except Exception as e:
            self._logger.error(f"Failed to get options chain for {symbol}: {e}")
            return []
    
    def dte(self, expiry_str: str) -> int:
        """
        Calculate days to expiration from expiry string.
        
        Args:
            expiry_str: Expiration date string (YYYY-MM-DD)
            
        Returns:
            Number of days until expiration
        """
        try:
            expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            today = datetime.now().date()
            delta = expiry_date - today
            return delta.days
        except Exception:
            return 0
    
    def _parse_chain(self, raw_chain: List[Dict[str, Any]], underlying: str) -> List[OptionContract]:
        """
        Parse raw Alpaca chain data into OptionContract objects.
        
        Args:
            raw_chain: Raw chain data from Alpaca
            underlying: Underlying symbol
            
        Returns:
            List of OptionContract objects
        """
        contracts = []
        
        for raw in raw_chain:
            try:
                contract = OptionContract(
                    symbol=raw.get("symbol", ""),
                    expiry=raw.get("expiry", raw.get("expiration_date", "")),
                    strike=float(raw.get("strike", raw.get("strike_price", 0))),
                    right=raw.get("right", raw.get("type", "call")).lower(),
                    delta=float(raw.get("delta", raw.get("greeks", {}).get("delta", 0))),
                    bid=float(raw.get("bid", raw.get("quote", {}).get("bid", 0))),
                    ask=float(raw.get("ask", raw.get("quote", {}).get("ask", 0))),
                    volume=int(raw.get("volume", 0)),
                    open_interest=int(raw.get("open_interest", 0))
                )
                contracts.append(contract)
            except Exception as e:
                self._logger.warn(f"Failed to parse option contract: {e}")
                continue
        
        return contracts
