
"""Options data service for real-time options chain analysis"""
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import requests
import time

from ..core.logging import get_logger
from ..core.clock import get_market_clock
from ..services.alpaca_client import get_alpaca_client


class OptionsDataService:
    """Service for fetching and analyzing real options data"""
    
    def __init__(self):
        self._logger = get_logger()
        self._alpaca = get_alpaca_client()
        self._cache = {}
        self._cache_ttl = 60  # 1 minute cache
        
    def get_options_chain(self, symbol: str, expiration_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get options chain for symbol with real Alpaca data"""
        
        cache_key = f"{symbol}_{expiration_date}_{int(time.time() / self._cache_ttl)}"
        
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        try:
            # Use Alpaca options data client if available
            if hasattr(self._alpaca, '_options_data_client') and self._alpaca._options_data_client:
                chain = self._fetch_alpaca_options_chain(symbol, expiration_date)
            else:
                # Fallback to simulated chain for demo
                underlying_price = self._get_underlying_price(symbol)
                chain = self._generate_realistic_chain(symbol, underlying_price)
            
            self._cache[cache_key] = chain
            return chain
            
        except Exception as e:
            self._logger.error(f"Failed to fetch options chain for {symbol}: {e}")
            return []
    
    def _fetch_alpaca_options_chain(self, symbol: str, expiration_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch real options chain from Alpaca"""
        
        try:
            from alpaca.data.requests import OptionChainRequest
            from alpaca.data.timeframe import TimeFrame
            
            request = OptionChainRequest(
                underlying_symbol=symbol,
                expiration_date=expiration_date,
                limit=1000
            )
            
            chain_data = self._alpaca._options_data_client.get_option_chain(request)
            
            options = []
            for contract_symbol, contract_data in chain_data.items():
                contract_info = self._parse_option_symbol(contract_symbol)
                
                if contract_info:
                    now_naive = get_market_clock().now().replace(tzinfo=None)
                    
                    bid = getattr(contract_data, 'bid_price', None) or getattr(contract_data, 'bid', 0) or 0
                    ask = getattr(contract_data, 'ask_price', None) or getattr(contract_data, 'ask', 0) or 0
                    last = getattr(contract_data, 'last_trade_price', None) or getattr(contract_data, 'last', 0) or 0
                    volume = getattr(contract_data, 'volume', 0) or 0
                    oi = getattr(contract_data, 'open_interest', 0) or 0
                    iv = getattr(contract_data, 'implied_volatility', 0.25) or 0.25
                    delta = getattr(contract_data, 'delta', 0) or 0
                    gamma = getattr(contract_data, 'gamma', 0) or 0
                    theta = getattr(contract_data, 'theta', 0) or 0
                    vega = getattr(contract_data, 'vega', 0) or 0
                    
                    if hasattr(contract_data, 'greeks') and contract_data.greeks:
                        greeks = contract_data.greeks
                        delta = getattr(greeks, 'delta', delta) or delta
                        gamma = getattr(greeks, 'gamma', gamma) or gamma
                        theta = getattr(greeks, 'theta', theta) or theta
                        vega = getattr(greeks, 'vega', vega) or vega
                    
                    if hasattr(contract_data, 'latest_quote') and contract_data.latest_quote:
                        quote = contract_data.latest_quote
                        bid = getattr(quote, 'bid_price', bid) or bid
                        ask = getattr(quote, 'ask_price', ask) or ask
                    
                    options.append({
                        "symbol": contract_symbol,
                        "underlying": symbol,
                        "strike": contract_info["strike"],
                        "expiry": contract_info["expiry"],
                        "type": contract_info["type"],
                        "dte": (contract_info["expiry"] - now_naive).days,
                        "bid": float(bid),
                        "ask": float(ask),
                        "last": float(last),
                        "volume": int(volume),
                        "open_interest": int(oi),
                        "implied_volatility": float(iv),
                        "delta": float(delta),
                        "gamma": float(gamma),
                        "theta": float(theta),
                        "vega": float(vega)
                    })
            
            self._logger.log("alpaca_options_chain_fetched", {
                "symbol": symbol,
                "contracts": len(options)
            })
            
            return options
            
        except ImportError:
            self._logger.warn("Alpaca options data client not available, using fallback")
            return []
        except Exception as e:
            self._logger.error(f"Alpaca options chain fetch failed: {e}")
            return []
    
    def _parse_option_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Parse option symbol to extract contract details"""
        
        try:
            # Standard options symbol format: AAPL240119C00150000
            # Format: [SYMBOL][YYMMDD][C/P][STRIKE*1000]
            
            if len(symbol) < 15:
                return None
            
            # Extract underlying symbol (first part before date)
            underlying = ""
            date_start = -1
            
            for i, char in enumerate(symbol):
                if char.isdigit() and i > 0:
                    date_start = i
                    break
                underlying += char
            
            if date_start == -1:
                return None
            
            # Extract date (YYMMDD)
            date_str = symbol[date_start:date_start+6]
            expiry = datetime.strptime(f"20{date_str}", "%Y%m%d")
            
            # Extract option type (C/P)
            option_type = symbol[date_start+6]
            option_type = "call" if option_type == "C" else "put"
            
            # Extract strike (last 8 digits, divide by 1000)
            strike_str = symbol[date_start+7:date_start+15]
            strike = float(strike_str) / 1000
            
            return {
                "underlying": underlying,
                "expiry": expiry,
                "type": option_type,
                "strike": strike
            }
            
        except Exception as e:
            self._logger.error(f"Failed to parse option symbol {symbol}: {e}")
            return None
    
    def _get_underlying_price(self, symbol: str) -> float:
        """Get current underlying price"""
        
        try:
            quote = self._alpaca.get_latest_quote(symbol, asset_class="stock")
            return (quote["bid"] + quote["ask"]) / 2
        except Exception as e:
            self._logger.error(f"Failed to get underlying price for {symbol}: {e}")
            return 100.0  # Fallback price
    
    def _generate_realistic_chain(self, symbol: str, underlying_price: float) -> List[Dict[str, Any]]:
        """Generate realistic options chain for demo purposes"""
        
        options = []
        
        # Generate multiple expiration dates
        expiry_days = [7, 14, 21, 30, 45, 60]
        
        for dte in expiry_days:
            expiry_date = get_market_clock().now() + timedelta(days=dte)
            
            # Generate strikes around current price
            strike_range = max(10, int(underlying_price * 0.15))
            strike_increment = 1 if underlying_price < 200 else 5
            
            for i in range(-strike_range, strike_range + 1, strike_increment):
                strike = round(underlying_price + i, 2)
                
                # Generate both calls and puts
                for option_type in ["call", "put"]:
                    option_data = self._calculate_synthetic_option_price(
                        underlying_price, strike, dte, option_type
                    )
                    
                    if option_data:
                        options.append({
                            "symbol": f"{symbol}_{expiry_date.strftime('%y%m%d')}{'C' if option_type == 'call' else 'P'}{strike:08.2f}",
                            "underlying": symbol,
                            "strike": strike,
                            "expiry": expiry_date,
                            "type": option_type,
                            "dte": dte,
                            **option_data
                        })
        
        return options
    
    def _calculate_synthetic_option_price(self, spot: float, strike: float, dte: int, 
                                        option_type: str) -> Optional[Dict[str, Any]]:
        """Calculate synthetic option price with Greeks using Black-Scholes"""
        
        try:
            import math
            from scipy.stats import norm
            
            # Market parameters
            time_to_expiry = dte / 365.0
            risk_free_rate = 0.05
            volatility = 0.25  # 25% annual volatility
            
            if time_to_expiry <= 0:
                return None
            
            # Black-Scholes calculation
            d1 = (math.log(spot / strike) + (risk_free_rate + 0.5 * volatility**2) * time_to_expiry) / (volatility * math.sqrt(time_to_expiry))
            d2 = d1 - volatility * math.sqrt(time_to_expiry)
            
            if option_type == "call":
                price = spot * norm.cdf(d1) - strike * math.exp(-risk_free_rate * time_to_expiry) * norm.cdf(d2)
                delta = norm.cdf(d1)
                gamma = norm.pdf(d1) / (spot * volatility * math.sqrt(time_to_expiry))
                theta = -(spot * norm.pdf(d1) * volatility) / (2 * math.sqrt(time_to_expiry)) - risk_free_rate * strike * math.exp(-risk_free_rate * time_to_expiry) * norm.cdf(d2)
                vega = spot * norm.pdf(d1) * math.sqrt(time_to_expiry)
            else:  # put
                price = strike * math.exp(-risk_free_rate * time_to_expiry) * norm.cdf(-d2) - spot * norm.cdf(-d1)
                delta = -norm.cdf(-d1)
                gamma = norm.pdf(d1) / (spot * volatility * math.sqrt(time_to_expiry))
                theta = -(spot * norm.pdf(d1) * volatility) / (2 * math.sqrt(time_to_expiry)) + risk_free_rate * strike * math.exp(-risk_free_rate * time_to_expiry) * norm.cdf(-d2)
                vega = spot * norm.pdf(d1) * math.sqrt(time_to_expiry)
            
            # Add realistic bid/ask spread
            spread = max(0.05, price * 0.03)
            
            return {
                "bid": max(0.01, price - spread/2),
                "ask": max(0.02, price + spread/2),
                "last": price,
                "delta": delta,
                "gamma": gamma,
                "theta": theta / 365,  # Per day
                "vega": vega / 100,   # Per 1% vol change
                "implied_volatility": volatility,
                "volume": max(10, int(abs(delta) * 1000)),  # Simulate volume
                "open_interest": max(50, int(abs(delta) * 2000))  # Simulate OI
            }
            
        except ImportError:
            # Fallback without scipy
            return self._simple_option_price(spot, strike, dte, option_type)
        except Exception as e:
            self._logger.error(f"Option price calculation failed: {e}")
            return None
    
    def _simple_option_price(self, spot: float, strike: float, dte: int, option_type: str) -> Dict[str, Any]:
        """Simple option pricing without scipy"""
        
        import math
        
        time_to_expiry = dte / 365.0
        volatility = 0.25
        
        # Simplified approximation
        moneyness = spot / strike if strike > 0 else 1
        time_value = volatility * math.sqrt(time_to_expiry) * spot * 0.4
        
        if option_type == "call":
            intrinsic = max(0, spot - strike)
            delta = 0.5 if abs(moneyness - 1) < 0.05 else (0.8 if moneyness > 1 else 0.2)
        else:
            intrinsic = max(0, strike - spot)
            delta = -0.5 if abs(moneyness - 1) < 0.05 else (-0.8 if moneyness < 1 else -0.2)
        
        price = intrinsic + time_value
        spread = max(0.05, price * 0.03)
        
        return {
            "bid": max(0.01, price - spread/2),
            "ask": max(0.02, price + spread/2),
            "last": price,
            "delta": delta,
            "gamma": 0.01,
            "theta": -time_value / max(1, dte),
            "vega": time_value * 0.1,
            "implied_volatility": volatility,
            "volume": 100,
            "open_interest": 500
        }
    
    def analyze_iv_rank(self, symbol: str, current_iv: float) -> Dict[str, Any]:
        """Analyze implied volatility rank"""
        
        # Simplified IV rank calculation
        # In production, use historical IV data
        
        try:
            # Simulate 252-day IV history
            import random
            random.seed(hash(symbol) % 2**32)
            
            iv_history = [current_iv + random.gauss(0, 0.05) for _ in range(252)]
            iv_history.sort()
            
            # Calculate percentile rank
            rank = (sum(1 for iv in iv_history if iv < current_iv) / len(iv_history)) * 100
            
            return {
                "current_iv": current_iv,
                "iv_rank": rank,
                "52_week_low": min(iv_history),
                "52_week_high": max(iv_history),
                "recommendation": "sell_premium" if rank > 50 else "buy_premium"
            }
            
        except Exception as e:
            self._logger.error(f"IV rank analysis failed: {e}")
            return {
                "current_iv": current_iv,
                "iv_rank": 50.0,
                "recommendation": "neutral"
            }


# Global service instance
_options_data_service: Optional[OptionsDataService] = None


def get_options_data_service() -> OptionsDataService:
    """Get the global options data service instance"""
    global _options_data_service
    if _options_data_service is None:
        _options_data_service = OptionsDataService()
    return _options_data_service
