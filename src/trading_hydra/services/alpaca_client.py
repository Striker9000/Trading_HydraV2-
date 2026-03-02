"""Alpaca API client for trading operations"""
import os
import time
import threading
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import requests
from decimal import Decimal, ROUND_HALF_UP

from ..core.logging import get_logger
from ..core.health import get_health_monitor
from ..core.clock import get_market_clock
# Safe import of mock_data - optional dependency
try:
    from .mock_data import get_mock_provider, is_development_mode
    MOCK_DATA_AVAILABLE = True
except (ImportError, Exception) as e:
    print(f"Warning: mock_data not available: {e}")
    MOCK_DATA_AVAILABLE = False

    def get_mock_provider():
        return None

    def is_development_mode():
        return False

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        MarketOrderRequest, LimitOrderRequest, GetOrdersRequest,
        StopOrderRequest, StopLimitOrderRequest, ReplaceOrderRequest,
        TakeProfitRequest, StopLossRequest
    )
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, AssetClass, PositionIntent, OrderType
    from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient, OptionHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest, CryptoLatestQuoteRequest, OptionChainRequest, OptionSnapshotRequest
    ALPACA_SDK_AVAILABLE = True
    ALPACA_OPTIONS_AVAILABLE = True
except ImportError:
    print("Warning: alpaca-py not installed. Using fallback HTTP client.")
    ALPACA_SDK_AVAILABLE = False
    ALPACA_OPTIONS_AVAILABLE = False


@dataclass
class AlpacaAccount:
    equity: float
    cash: float
    buying_power: float
    status: str


@dataclass
class AlpacaPosition:
    symbol: str
    qty: float
    market_value: float
    unrealized_pl: float
    side: str
    avg_entry_price: float = 0.0  # The fill price when position was opened
    current_price: float = 0.0    # The current market price
    asset_class: str = "us_equity"  # us_equity, crypto, option
    cost_basis: float = 0.0  # Total cost basis (qty * avg_entry_price)


class AlpacaClient:
    def __init__(self):
        self.api_key = os.environ.get("ALPACA_KEY")
        self.api_secret = os.environ.get("ALPACA_SECRET")
        self.is_paper = os.environ.get("ALPACA_PAPER", "true").lower() != "false"

        self.base_url = (
            "https://paper-api.alpaca.markets" if self.is_paper
            else "https://api.alpaca.markets"
        )

        self._logger = get_logger()
        self._health = get_health_monitor()
        
        # API rate limiter — sliding 60-second window + minimum interval
        # Alpaca free tier: 200 req/min per bucket (trading and market-data share one pool here)
        # We cap at 190/min to leave a 10-request safety buffer
        self._rate_lock = threading.Lock()
        self._last_request_time: float = 0.0
        self._min_request_interval: float = 0.32  # 320ms minimum between consecutive calls
        self._request_window: list = []            # sliding window of request timestamps (60s)
        
        # Caching for API call optimization
        self._account_cache: Optional[Dict[str, Any]] = None
        self._account_cache_ts: float = 0.0
        self._account_cache_ttl: float = 60.0  # 60 seconds default
        
        self._quote_cache: Dict[str, Dict[str, Any]] = {}
        self._quote_cache_ttl: float = 200.0  # 200 seconds default
        
        self._positions_cache: Optional[List] = None
        self._positions_cache_ts: float = 0.0
        self._positions_cache_ttl: float = 5.0  # 5 seconds default
        
        self._bars_cache: Dict[str, Dict[str, Any]] = {}
        self._bars_cache_ttl: float = 60.0  # 60 seconds for bar data
        
        # Load TTL settings from config if available
        try:
            from ..core.config import load_settings
            settings = load_settings()
            caching = settings.get("caching", {})
            self._account_cache_ttl = float(caching.get("account_ttl_seconds", 60))
            self._quote_cache_ttl = float(caching.get("quote_ttl_seconds", 200))
            self._positions_cache_ttl = float(caching.get("positions_ttl_seconds", 5))
        except Exception:
            pass  # Use defaults

        # Initialize Alpaca SDK clients
        if ALPACA_SDK_AVAILABLE and self.has_credentials():
            self._trading_client = TradingClient(
                api_key=self.api_key,
                secret_key=self.api_secret,
                paper=self.is_paper
            )
            self._stock_data_client = StockHistoricalDataClient(
                api_key=self.api_key,
                secret_key=self.api_secret
            )
            self._crypto_data_client = CryptoHistoricalDataClient(
                api_key=self.api_key,
                secret_key=self.api_secret
            )
            # Initialize options data client for real options chain/Greeks
            if ALPACA_OPTIONS_AVAILABLE:
                self._options_data_client = OptionHistoricalDataClient(
                    api_key=self.api_key,
                    secret_key=self.api_secret
                )
            else:
                self._options_data_client = None
        else:
            self._trading_client = None
            self._stock_data_client = None
            self._crypto_data_client = None
            self._options_data_client = None

    def _throttle(self):
        """Rate limiter: sliding 60-second window (cap 190/min) + min interval between calls.

        Two-layer defense:
          1. Minimum interval (320ms) prevents micro-bursts of back-to-back calls.
          2. Sliding window (190 req/60s) prevents sustained over-rate across many calls,
             which is the main source of 429 errors from Alpaca.
        """
        with self._rate_lock:
            now = time.time()

            # Layer 1: prune expired entries from the 60-second window
            self._request_window = [t for t in self._request_window if now - t < 60.0]

            # Layer 2: if sliding window is full, wait until oldest entry ages out
            if len(self._request_window) >= 190:
                sleep_needed = 60.0 - (now - self._request_window[0]) + 0.05
                if sleep_needed > 0:
                    time.sleep(sleep_needed)
                now = time.time()
                self._request_window = [t for t in self._request_window if now - t < 60.0]

            # Layer 1: enforce minimum interval between consecutive calls
            elapsed = now - self._last_request_time
            if elapsed < self._min_request_interval:
                time.sleep(self._min_request_interval - elapsed)

            self._last_request_time = time.time()
            self._request_window.append(self._last_request_time)

    def _headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key or "",
            "APCA-API-SECRET-KEY": self.api_secret or "",
        }

    def has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _request(
        self, 
        method: str, 
        endpoint: str, 
        data: Optional[Dict] = None,
        retries: int = 3
    ) -> Dict[str, Any]:
        if not self.has_credentials():
            raise RuntimeError("ALPACA_KEY and ALPACA_SECRET required")

        url = f"{self.base_url}{endpoint}"

        for attempt in range(retries + 1):
            # Rate limiting: enforce minimum interval between API calls
            self._throttle()
            
            try:
                resp = requests.request(
                    method, 
                    url, 
                    headers=self._headers(),
                    json=data if method != "GET" else None,
                    params=data if method == "GET" else None,
                    timeout=30
                )

                if resp.status_code >= 400:
                    error_text = resp.text
                    
                    # Handle 429 rate limit with exponential backoff
                    if resp.status_code == 429:
                        if attempt < retries:
                            backoff = min(2 ** attempt, 30)  # 1s, 2s, 4s, max 30s
                            self._logger.log("alpaca_rate_limit", {
                                "attempt": attempt + 1,
                                "backoff_seconds": backoff,
                                "endpoint": endpoint
                            })
                            time.sleep(backoff)
                            continue
                        # After all retries, record failure but don't halt system
                        self._logger.log("alpaca_rate_limit_exhausted", {
                            "endpoint": endpoint,
                            "retries": retries
                        })
                    
                    self._health.record_api_failure(f"{resp.status_code}: {error_text}")
                    
                    # Auth failures (401/403) can be CRITICAL - but NOT for certain expected errors
                    # "uncovered option contracts" is a capability restriction, not an auth failure
                    if resp.status_code in (401, 403):
                        non_critical_errors = [
                            "uncovered option contracts",  # Account capability, not auth
                            "option trading not enabled",  # Account feature, not auth
                            "trading is not allowed",      # Account restriction, not auth
                            "insufficient qty available",  # Position held for orders, not auth
                            "insufficient qty",            # Alpaca short form (code 40310000)
                            "insufficient quantity",       # Alternate phrasing
                            "40310000",                    # Alpaca error code: insufficient qty
                            "position intent mismatch",    # Wrong side/already closed, not auth
                            "wash trade",                  # Trading rule violation, not auth
                            "potential wash trade",        # Trading rule violation, not auth
                        ]
                        is_non_critical = any(err in error_text.lower() for err in non_critical_errors)
                        
                        if not is_non_critical:
                            self._health.record_critical_auth_failure(
                                f"AUTH_FAILURE_{resp.status_code}: {error_text}"
                            )
                    
                    raise RuntimeError(f"Alpaca API error {resp.status_code}: {error_text}")

                self._health.record_price_tick()

                # Proactive back-pressure: if Alpaca's own rate-limit header shows
                # we're close to the ceiling, push the next call's earliest start time
                # forward rather than waiting synchronously right now.
                try:
                    remaining = int(resp.headers.get("X-RateLimit-Remaining", 999))
                    if remaining < 10:
                        with self._rate_lock:
                            self._last_request_time = time.time() + 2.0
                    elif remaining < 20:
                        with self._rate_lock:
                            self._last_request_time = time.time() + 0.5
                except (ValueError, TypeError):
                    pass

                if resp.status_code == 204:
                    return {}
                return resp.json()

            except requests.RequestException as e:
                self._health.record_api_failure(str(e))
                
                # Connection errors indicate network/broker issues - may be critical
                error_str = str(e).lower()
                if "connection" in error_str or "timeout" in error_str:
                    self._health.record_connection_failure(str(e))
                
                if attempt < retries:
                    backoff = min(2 ** attempt, 30)
                    time.sleep(backoff)
                    continue
                raise RuntimeError(f"Alpaca request failed: {e}")

        raise RuntimeError("Alpaca request failed after retries")

    def get_account(self, force_refresh: bool = False) -> AlpacaAccount:
        # Check cache first (60 second TTL by default)
        now = time.time()
        if not force_refresh and self._account_cache is not None:
            cache_age = now - self._account_cache_ts
            if cache_age < self._account_cache_ttl:
                self._logger.log("alpaca_account_cache_hit", {"cache_age_sec": round(cache_age, 1)})
                return self._account_cache["account"]
        
        self._logger.log("alpaca_get_account", {"cache_miss": True})
        data = self._request("GET", "/v2/account")

        # Validate API response structure
        required_fields = ["equity", "cash", "buying_power", "status"]
        for field in required_fields:
            if field not in data:
                raise ValueError(f"Missing required field '{field}' in Alpaca account response")

        # Validate and convert numeric fields
        try:
            equity = float(data["equity"])
            cash = float(data["cash"]) 
            buying_power = float(data["buying_power"])
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid numeric data in Alpaca account response: {e}")

        # Validate equity is reasonable
        if equity < 0:
            raise ValueError(f"Invalid negative equity: {equity}")

        status = str(data["status"])
        if status not in ["ACTIVE", "ACCOUNT_CLOSED", "ACCOUNT_FROZEN", "PENDING_APPROVAL"]:
            self._logger.warn(f"Unknown account status: {status}")

        account = AlpacaAccount(
            equity=equity,
            cash=cash,
            buying_power=buying_power,
            status=status
        )
        
        # Cache the result
        self._account_cache = {"account": account}
        self._account_cache_ts = now

        self._logger.log("alpaca_account", {
            "equity": account.equity,
            "cash": account.cash,
            "status": account.status,
            "validated": True,
            "cached": True
        })

        return account

    def invalidate_positions_cache(self):
        """Force invalidate positions cache (call after placing/closing orders)"""
        self._positions_cache = None
        self._positions_cache_ts = 0.0

    def get_positions(self) -> List[AlpacaPosition]:
        now = time.time()
        if (self._positions_cache is not None and
            now - self._positions_cache_ts < self._positions_cache_ttl):
            self._logger.log("alpaca_positions_cache_hit", {
                "count": len(self._positions_cache),
                "cache_age_sec": round(now - self._positions_cache_ts, 1)
            })
            return self._positions_cache
        
        self._logger.log("alpaca_get_positions", {})

        # Use mock data during development/after hours
        if MOCK_DATA_AVAILABLE and is_development_mode():
            mock_provider = get_mock_provider()
            if mock_provider:
                mock_positions_data = mock_provider.get_mock_positions()

            positions = []
            for p in mock_positions_data:
                positions.append(AlpacaPosition(
                    symbol=p["symbol"],
                    qty=float(p["qty"]),
                    market_value=float(p["market_value"]),
                    unrealized_pl=float(p["unrealized_pl"]),
                    side=p["side"]
                ))

            self._logger.log("alpaca_positions", {
                "count": len(positions),
                "symbols": [p.symbol for p in positions],
                "total_value": sum(p.market_value for p in positions),
                "validated": True,
                "mock_data": True
            })
            self._positions_cache = positions
            self._positions_cache_ts = now
            return positions

        data = self._request("GET", "/v2/positions")

        # Validate response is a list
        if not isinstance(data, list):
            raise ValueError("Alpaca positions response must be a list")

        positions = []
        for i, p in enumerate(data):
            if not isinstance(p, dict):
                raise ValueError(f"Position {i} must be a dictionary")

            # Validate required fields
            required_fields = ["symbol", "qty", "market_value", "unrealized_pl", "side"]
            for field in required_fields:
                if field not in p:
                    raise ValueError(f"Missing field '{field}' in position {i}")

            try:
                symbol = str(p["symbol"]).strip().upper()
                qty = float(p["qty"])
                market_value = float(p["market_value"])
                unrealized_pl = float(p["unrealized_pl"])
                side = str(p["side"]).lower()
                # Extract avg_entry_price and current_price from API response
                avg_entry_price = float(p.get("avg_entry_price", 0))
                current_price = float(p.get("current_price", 0))
                asset_class = str(p.get("asset_class", "us_equity"))
            except (ValueError, TypeError) as e:
                raise ValueError(f"Invalid data in position {i}: {e}")

            # Validate symbol (options symbols can be up to 21 chars in OCC format)
            if not symbol or len(symbol) > 21:
                raise ValueError(f"Invalid symbol in position {i}: '{symbol}'")

            # Validate side
            if side not in ["long", "short"]:
                raise ValueError(f"Invalid side in position {i}: '{side}'")

            cost_basis = float(p.get("cost_basis", abs(qty) * avg_entry_price))
            positions.append(AlpacaPosition(
                symbol=symbol,
                qty=qty,
                market_value=float(market_value),
                unrealized_pl=float(unrealized_pl),
                side=side,
                avg_entry_price=avg_entry_price,
                current_price=current_price,
                asset_class=asset_class,
                cost_basis=cost_basis
            ))

        self._logger.log("alpaca_positions", {
            "count": len(positions),
            "symbols": [p.symbol for p in positions],
            "total_value": sum(p.market_value for p in positions),
            "validated": True
        })
        self._positions_cache = positions
        self._positions_cache_ts = time.time()
        return positions

    def cancel_all_orders(self) -> int:
        self._logger.log("alpaca_cancel_all_orders", {})
        self._request("DELETE", "/v2/orders")
        self._logger.log("alpaca_orders_cancelled", {})
        return 0

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all open/pending orders, optionally filtered by symbol.
        
        Used to prevent duplicate order placement when orders are pending
        but not yet filled (position not yet created).
        
        Args:
            symbol: Optional symbol to filter orders (e.g., "LINK/USD")
            
        Returns:
            List of order dicts with status, symbol, side, qty, etc.
        """
        try:
            query_params = {"status": "open"}
            if symbol:
                query_params["symbols"] = symbol.replace("/", "")
            
            orders_data = self._request("GET", "/v2/orders", data=query_params)
            
            if not orders_data:
                return []
            
            orders = []
            for o in orders_data:
                orders.append({
                    "id": o.get("id"),
                    "symbol": o.get("symbol"),
                    "side": o.get("side"),
                    "qty": o.get("qty"),
                    "status": o.get("status"),
                    "created_at": o.get("created_at"),
                    "client_order_id": o.get("client_order_id")
                })
            
            return orders
        except Exception as e:
            self._logger.error(f"Failed to get open orders: {e}")
            return []

    def close_position(self, symbol: str, qty: float = None, percentage: float = None) -> Dict[str, Any]:
        params = {}
        if qty is not None:
            params["qty"] = str(qty)
        if percentage is not None:
            params["percentage"] = str(percentage)
        self._logger.log("alpaca_close_position", {"symbol": symbol, "qty": qty, "percentage": percentage})
        if params:
            query_string = "&".join(f"{k}={v}" for k, v in params.items())
            result = self._request("DELETE", f"/v2/positions/{symbol}?{query_string}")
        else:
            result = self._request("DELETE", f"/v2/positions/{symbol}")
        self._logger.log("alpaca_position_closed", {"symbol": symbol, "qty": qty})
        self.invalidate_positions_cache()
        return result

    def close_all_positions(self) -> Dict[str, Any]:
        self._logger.log("alpaca_close_all_positions", {})
        result = self._request("DELETE", "/v2/positions")
        self._logger.log("alpaca_positions_closed", {})
        self.invalidate_positions_cache()
        return result

    def flatten(self) -> Dict[str, Any]:
        self._logger.log("alpaca_flatten_start", {})

        if not self.has_credentials():
            return {"success": False, "error": "No credentials"}

        try:
            self.cancel_all_orders()
            self.close_all_positions()
            self._logger.log("alpaca_flatten_complete", {})
            return {"success": True}
        except Exception as e:
            self._logger.error(f"Flatten failed: {e}")
            return {"success": False, "error": str(e)}

    # ==========================================================================
    # PRE-STAGED EXIT ORDERS - Broker-side SL/TP protection
    # Places orders on Alpaca servers so positions are protected even if system goes down
    # ==========================================================================

    def place_oco_exit_orders(
        self, symbol: str, qty: float, side: str,
        stop_price: float, take_profit_price: float,
        client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Place OCO (One-Cancels-Other) exit orders: stop-loss + take-profit.
        
        When one fills, the other is automatically cancelled by Alpaca.
        This gives broker-side protection even if our system goes down.
        
        Args:
            symbol: Ticker symbol (e.g., "AAPL", options symbol, or "BTC/USD")
            qty: Number of shares/contracts to exit
            side: "sell" for long positions, "buy" for short positions
            stop_price: Stop-loss price
            take_profit_price: Take-profit limit price
            client_order_id: Optional custom order ID for tracking
            
        Returns:
            Dict with order IDs: {success, parent_order_id, stop_order_id, tp_order_id}
        """
        if not self.has_credentials():
            return {"success": False, "error": "No credentials"}
        
        try:
            is_crypto = self._is_crypto_symbol(symbol)
            is_option = self._is_option_symbol(symbol)
            # Options MUST use DAY TIF on Alpaca (GTC not supported for options)
            tif = TimeInForce.DAY if is_option else TimeInForce.GTC
            order_side = OrderSide.SELL if side.lower() == "sell" else OrderSide.BUY
            
            # Round prices to 2 decimal places
            stop_price = round(stop_price, 2)
            take_profit_price = round(take_profit_price, 2)
            
            self._logger.log("alpaca_oco_exit_request", {
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "stop_price": stop_price,
                "take_profit_price": take_profit_price,
                "is_crypto": is_crypto,
                "client_order_id": client_order_id
            })
            
            # Use Alpaca SDK OCO order class
            order_request = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=order_side,
                time_in_force=tif,
                order_class=OrderClass.OCO,
                take_profit=TakeProfitRequest(limit_price=take_profit_price),
                stop_loss=StopLossRequest(stop_price=stop_price),
                limit_price=take_profit_price,
                client_order_id=client_order_id
            )
            
            order = self._trading_client.submit_order(order_request)
            
            # Extract leg order IDs
            parent_id = str(order.id)
            stop_id = None
            tp_id = None
            
            if hasattr(order, 'legs') and order.legs:
                for leg in order.legs:
                    leg_type = getattr(leg, 'order_type', None)
                    if leg_type and str(leg_type).lower() in ('stop', 'stop_limit'):
                        stop_id = str(leg.id)
                    elif leg_type and str(leg_type).lower() in ('limit',):
                        tp_id = str(leg.id)
                    elif not stop_id and getattr(leg, 'stop_price', None):
                        stop_id = str(leg.id)
                    elif not tp_id:
                        tp_id = str(leg.id)
            
            result = {
                "success": True,
                "parent_order_id": parent_id,
                "stop_order_id": stop_id,
                "tp_order_id": tp_id,
                "symbol": symbol,
                "qty": qty,
                "stop_price": stop_price,
                "take_profit_price": take_profit_price
            }
            
            self._logger.log("alpaca_oco_exit_placed", result)
            return result
            
        except Exception as e:
            self._logger.error(f"OCO exit order failed for {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def place_stop_order(
        self, symbol: str, qty: float, side: str,
        stop_price: float, client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Place a standalone stop order (stop-loss protection).
        
        Used as fallback when OCO isn't supported (e.g., some options).
        
        Args:
            symbol: Ticker symbol
            qty: Number of shares/contracts
            side: "sell" for long positions, "buy" for short positions
            stop_price: Stop trigger price
            client_order_id: Optional custom order ID
            
        Returns:
            Dict with {success, order_id, stop_price}
        """
        if not self.has_credentials():
            return {"success": False, "error": "No credentials"}
        
        try:
            is_crypto = self._is_crypto_symbol(symbol)
            is_option = self._is_option_symbol(symbol)
            # Options MUST use DAY TIF on Alpaca (GTC not supported for options)
            tif = TimeInForce.DAY if is_option else TimeInForce.GTC
            order_side = OrderSide.SELL if side.lower() == "sell" else OrderSide.BUY
            stop_price = round(stop_price, 2)
            
            self._logger.log("alpaca_stop_order_request", {
                "symbol": symbol, "side": side, "qty": qty,
                "stop_price": stop_price, "client_order_id": client_order_id
            })
            
            order_request = StopOrderRequest(
                symbol=symbol,
                qty=qty,
                side=order_side,
                time_in_force=tif,
                stop_price=stop_price,
                client_order_id=client_order_id
            )
            
            order = self._trading_client.submit_order(order_request)
            
            result = {
                "success": True,
                "order_id": str(order.id),
                "symbol": symbol,
                "stop_price": stop_price
            }
            
            self._logger.log("alpaca_stop_order_placed", result)
            return result
            
        except Exception as e:
            self._logger.error(f"Stop order failed for {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def replace_order(
        self, order_id: str, 
        qty: Optional[float] = None,
        stop_price: Optional[float] = None,
        limit_price: Optional[float] = None,
        client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Replace/modify an existing order (e.g., update trailing stop price).
        
        Only specified fields are changed; others remain the same.
        
        Args:
            order_id: Alpaca order UUID to modify
            qty: New quantity (optional)
            stop_price: New stop price (optional)
            limit_price: New limit price (optional)
            client_order_id: New client order ID (optional)
            
        Returns:
            Dict with {success, new_order_id} or error
        """
        if not self.has_credentials():
            return {"success": False, "error": "No credentials"}
        
        try:
            self._logger.log("alpaca_replace_order_request", {
                "order_id": order_id,
                "new_qty": qty,
                "new_stop_price": stop_price,
                "new_limit_price": limit_price
            })
            
            replace_params = {}
            if qty is not None:
                replace_params["qty"] = qty
            if stop_price is not None:
                replace_params["stop_price"] = round(stop_price, 2)
            if limit_price is not None:
                replace_params["limit_price"] = round(limit_price, 2)
            if client_order_id is not None:
                replace_params["client_order_id"] = client_order_id
            
            replace_request = ReplaceOrderRequest(**replace_params)
            new_order = self._trading_client.replace_order_by_id(
                order_id=order_id,
                order_data=replace_request
            )
            
            result = {
                "success": True,
                "old_order_id": order_id,
                "new_order_id": str(new_order.id),
                "symbol": new_order.symbol,
                "status": str(new_order.status)
            }
            
            self._logger.log("alpaca_order_replaced", result)
            return result
            
        except Exception as e:
            self._logger.error(f"Replace order failed for {order_id}: {e}")
            return {"success": False, "error": str(e)}

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """
        Cancel an existing order by ID.
        
        Used to cancel pre-staged SL/TP when manually exiting a position.
        
        Args:
            order_id: Alpaca order UUID to cancel
            
        Returns:
            Dict with {success} or error
        """
        if not self.has_credentials():
            return {"success": False, "error": "No credentials"}
        
        try:
            self._logger.log("alpaca_cancel_order_request", {"order_id": order_id})
            self._trading_client.cancel_order_by_id(order_id)
            
            self._logger.log("alpaca_order_cancelled", {"order_id": order_id})
            return {"success": True, "order_id": order_id}
            
        except Exception as e:
            self._logger.error(f"Cancel order failed for {order_id}: {e}")
            return {"success": False, "error": str(e)}

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Get order details by ID.
        
        Used to check if pre-staged orders are still open or have filled.
        
        Args:
            order_id: Alpaca order UUID
            
        Returns:
            Order dict with status, filled info, or None on failure
        """
        if not self.has_credentials():
            return None
        
        try:
            order = self._trading_client.get_order_by_id(order_id)
            
            return {
                "id": str(order.id),
                "symbol": order.symbol,
                "side": str(order.side),
                "qty": str(order.qty),
                "status": str(order.status),
                "order_type": str(order.order_type),
                "stop_price": str(order.stop_price) if order.stop_price else None,
                "limit_price": str(order.limit_price) if order.limit_price else None,
                "filled_qty": str(order.filled_qty or 0),
                "filled_avg_price": str(order.filled_avg_price or 0),
                "created_at": str(order.created_at),
                "client_order_id": getattr(order, 'client_order_id', None)
            }
            
        except Exception as e:
            self._logger.error(f"Get order failed for {order_id}: {e}")
            return None

    def _round_notional(self, value: float) -> float:
        """Round notional to 2 decimal places as required by Alpaca API"""
        return float(Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

    def _round_qty(self, value: float) -> float:
        """Round quantity to appropriate decimal places"""
        return float(Decimal(str(value)).quantize(Decimal('0.000000001'), rounding=ROUND_HALF_UP))

    def _is_crypto_symbol(self, symbol: str) -> bool:
        """Check if symbol is a crypto trading pair (e.g., BTC/USD, ETH/USD)"""
        return "/" in symbol or symbol.upper() in ["BTCUSD", "ETHUSD"]

    def place_market_order(self, symbol: str, side: str, qty: Optional[float] = None, 
                          notional: Optional[float] = None, client_order_id: Optional[str] = None) -> Dict[str, Any]:
        """Place market order with proper validation.
        
        DTBP workaround: When notional is specified for non-crypto stocks,
        convert to whole share qty so we can use GTC time_in_force.
        Fractional/notional orders require DAY TIF which needs DTBP.
        """

        if not self.has_credentials():
            self._logger.error("No Alpaca credentials available")
            return {"error": "No credentials", "success": False}

        if not qty and not notional:
            raise ValueError("Must specify either qty or notional")

        try:
            is_crypto = self._is_crypto_symbol(symbol)
            is_option = self._is_option_symbol(symbol)
            
            if not is_option and not is_crypto and side.lower() == "buy":
                self._logger.log("STOCK_ENTRY_BLOCKED", {
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "notional": notional,
                    "reason": "OPTIONS_ONLY_MODE: Stock buys disabled at broker level"
                })
                return {"success": False, "error": "OPTIONS_ONLY_MODE: Stock buys blocked"}
            # Options MUST use DAY TIF on Alpaca (GTC not supported for options)
            tif = TimeInForce.DAY if is_option else TimeInForce.GTC
            
            # DTBP workaround: convert notional to whole share qty for non-crypto
            # Fractional/notional orders MUST use DAY TIF on Alpaca, but DAY needs DTBP.
            # Whole share qty with GTC bypasses the DTBP requirement.
            # CRITICAL: Alpaca does NOT support notional for sell/short orders at all.
            # For sells, we MUST always convert to qty.
            actual_qty = qty
            actual_notional = notional
            is_sell = side.lower() == "sell"
            if notional is not None and not is_crypto and qty is None:
                try:
                    quote = self.get_latest_quote(symbol, "stock")
                    price = quote.get("price", 0) or ((quote.get("bid", 0) + quote.get("ask", 0)) / 2) or quote.get("ask", 0) or quote.get("bid", 0)
                    if price > 0:
                        whole_shares = int(notional / price)
                        if whole_shares >= 1:
                            actual_qty = float(whole_shares)
                            actual_notional = None
                            self._logger.log("notional_to_qty_conversion", {
                                "symbol": symbol,
                                "notional": notional,
                                "price": price,
                                "whole_shares": whole_shares,
                                "reason": "DTBP_workaround_fractional_requires_DAY"
                            })
                        elif is_sell:
                            actual_qty = 1.0
                            actual_notional = None
                            self._logger.log("notional_sell_min_1_share", {
                                "symbol": symbol,
                                "notional": notional,
                                "price": price,
                                "reason": "sell_orders_require_qty_minimum_1_share"
                            })
                        else:
                            tif = TimeInForce.DAY
                            self._logger.log("notional_kept_expensive_stock", {
                                "symbol": symbol,
                                "notional": notional,
                                "price": price,
                                "reason": "cannot_afford_1_whole_share"
                            })
                except Exception as e:
                    if is_sell:
                        actual_qty = 1.0
                        actual_notional = None
                        self._logger.log("notional_sell_conversion_failed_fallback", {
                            "symbol": symbol,
                            "notional": notional,
                            "error": str(e),
                            "fallback": "using_1_share_for_sell"
                        })
                    else:
                        tif = TimeInForce.DAY
                        self._logger.log("notional_conversion_failed", {
                            "symbol": symbol,
                            "notional": notional,
                            "error": str(e),
                            "fallback": "using_DAY_tif"
                        })
            
            # DTBP workaround: fractional qty requires DAY TIF, so round to whole shares for GTC
            # This handles cases where ExitBot passes fractional position qty directly
            if actual_qty is not None and not is_crypto and not is_option and tif == TimeInForce.GTC:
                import math
                if actual_qty != int(actual_qty):
                    if is_sell:
                        rounded_qty = math.floor(actual_qty)
                        if rounded_qty < 1:
                            rounded_qty = 1
                    else:
                        rounded_qty = int(actual_qty)
                        if rounded_qty < 1:
                            tif = TimeInForce.DAY
                            rounded_qty = None
                            actual_notional = actual_qty  # fallback to original as notional-ish
                    if rounded_qty is not None:
                        self._logger.log("fractional_qty_rounded", {
                            "symbol": symbol,
                            "original_qty": actual_qty,
                            "rounded_qty": rounded_qty,
                            "side": side,
                            "reason": "fractional_requires_DAY_TIF_using_whole_shares_for_GTC"
                        })
                        actual_qty = float(rounded_qty)

            if actual_notional is not None and tif == TimeInForce.GTC and not is_crypto:
                tif = TimeInForce.DAY
                self._logger.log("notional_force_day_tif", {
                    "symbol": symbol,
                    "notional": actual_notional,
                    "reason": "notional_orders_require_DAY_TIF_safety_net"
                })

            self._logger.log("alpaca_order_request", {
                "symbol": symbol,
                "side": side,
                "type": "market",
                "qty": actual_qty,
                "notional": actual_notional,
                "paper_trading": self.is_paper,
                "client_order_id": client_order_id,
                "is_crypto": is_crypto,
                "time_in_force": "gtc" if tif == TimeInForce.GTC else "day"
            })

            order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
            
            rounded_notional = self._round_notional(actual_notional) if actual_notional is not None else None
            
            order_request = MarketOrderRequest(
                symbol=symbol,
                side=order_side,
                time_in_force=tif,
                qty=actual_qty if actual_qty is not None else None,
                notional=rounded_notional,
                client_order_id=client_order_id
            )

            order = self._trading_client.submit_order(order_request)

            # Convert to dict for consistent return format
            order_dict = {
                "id": str(order.id),
                "symbol": order.symbol,
                "side": order.side,
                "qty": str(order.qty),
                "notional": str(getattr(order, 'notional', 'N/A')),
                "status": order.status,
                "filled_qty": str(order.filled_qty or 0),
                "filled_avg_price": str(order.filled_avg_price or 0),
                "created_at": str(order.created_at),
                "client_order_id": getattr(order, 'client_order_id', None)
            }

            self._logger.log("alpaca_order_submitted", order_dict)
            return order_dict

        except Exception as e:
            self._logger.error(f"Alpaca order submission failed: {e}")
            return {"error": str(e), "success": False}

    def place_limit_order(self, symbol: str, side: str, qty: float, limit_price: float,
                         client_order_id: Optional[str] = None, time_in_force: str = "gtc") -> Dict[str, Any]:
        """Place limit order with proper validation"""

        if not self.has_credentials():
            self._logger.error("No Alpaca credentials available")
            return {"error": "No credentials", "success": False}

        try:
            is_crypto = self._is_crypto_symbol(symbol)
            is_option = self._is_option_symbol(symbol)
            if is_crypto:
                time_in_force = "gtc"
            # Options MUST use DAY TIF on Alpaca (GTC not supported for options)
            if is_option:
                time_in_force = "day"
            
            self._logger.log("alpaca_limit_order_request", {
                "symbol": symbol,
                "side": side,
                "type": "limit",
                "qty": qty,
                "limit_price": limit_price,
                "time_in_force": time_in_force,
                "paper_trading": self.is_paper,
                "client_order_id": client_order_id,
                "is_crypto": is_crypto
            })

            order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
            # Options MUST use DAY TIF on Alpaca (GTC not supported for options)
            tif = TimeInForce.DAY if is_option else TimeInForce.GTC
            
            order_request = LimitOrderRequest(
                symbol=symbol,
                side=order_side,
                time_in_force=tif,
                qty=qty,
                limit_price=limit_price,
                client_order_id=client_order_id
            )

            order = self._trading_client.submit_order(order_request)

            # Convert to dict for consistent return format
            order_dict = {
                "id": str(order.id),
                "symbol": order.symbol,
                "side": order.side,
                "qty": str(order.qty),
                "limit_price": str(order.limit_price),
                "status": order.status,
                "filled_qty": str(order.filled_qty or 0),
                "filled_avg_price": str(order.filled_avg_price or 0),
                "created_at": str(order.created_at),
                "client_order_id": getattr(order, 'client_order_id', None)
            }

            self._logger.log("alpaca_limit_order_submitted", order_dict)
            return order_dict

        except Exception as e:
            self._logger.error(f"Alpaca limit order submission failed: {e}")
            return {"error": str(e), "success": False}

    def get_latest_quote(self, symbol: str, asset_class: str = "stock") -> Dict[str, float]:
        """Get latest bid/ask quote for symbol with caching (200 second TTL default)"""
        # Check quote cache first
        cache_key = f"{symbol}:{asset_class}"
        now = time.time()
        
        if cache_key in self._quote_cache:
            cached = self._quote_cache[cache_key]
            cache_age = now - cached["ts"]
            if cache_age < self._quote_cache_ttl:
                self._logger.log("alpaca_quote_cache_hit", {
                    "symbol": symbol, 
                    "asset_class": asset_class,
                    "cache_age_sec": round(cache_age, 1)
                })
                return cached["quote"]
        
        self._logger.log("alpaca_get_quote", {"symbol": symbol, "asset_class": asset_class, "cache_miss": True})

        # Use mock data during development/after hours
        if MOCK_DATA_AVAILABLE and is_development_mode():
            mock_provider = get_mock_provider()
            if mock_provider:
                mock_quote = mock_provider.get_mock_quote(symbol, asset_class)
            self._logger.log("using_mock_quote", {
                "symbol": symbol,
                "mock_bid": mock_quote["bid"],
                "mock_ask": mock_quote["ask"],
                "reason": "development_mode"
            })
            return mock_quote

        try:
            if asset_class == "stock":
                # Use the correct method name for stock quotes
                try:
                    quotes_request = StockLatestQuoteRequest(symbol_or_symbols=[symbol])
                    self._throttle()
                    quotes = self._stock_data_client.get_stock_latest_quote(quotes_request)
                    quotes_data = quotes.data if hasattr(quotes, 'data') else quotes

                    if symbol in quotes_data:
                        quote_obj = quotes_data[symbol]
                        result = {
                            "bid": float(quote_obj.bid_price),
                            "ask": float(quote_obj.ask_price),
                            "timestamp": quote_obj.timestamp.isoformat()
                        }
                        self._cache_quote(symbol, asset_class, result)
                        return result
                    else:
                        raise Exception(f"No quote data for {symbol}")
                except AttributeError:
                    # Fallback: try to get latest bar as proxy for quote
                    from alpaca.data.requests import StockBarsRequest
                    from alpaca.data.timeframe import TimeFrame

                    bars_request = StockBarsRequest(
                        symbol_or_symbols=[symbol],
                        timeframe=TimeFrame.Minute,
                        limit=1
                    )
                    self._throttle()
                    bars = self._stock_data_client.get_stock_bars(bars_request)
                    bars_data = bars.data if hasattr(bars, 'data') else bars

                    if symbol in bars_data:
                        bar = bars_data[symbol][-1] if bars_data[symbol] else None
                        if bar:
                            # Use close price as both bid and ask (spread approximation)
                            close_price = float(bar.close)
                            result = {
                                "bid": close_price * 0.999,  # Slight spread simulation
                                "ask": close_price * 1.001,
                                "timestamp": bar.timestamp.isoformat()
                            }
                            self._cache_quote(symbol, asset_class, result)
                            return result
                    raise Exception(f"No bar data available for {symbol}")

            elif asset_class == "crypto":
                try:
                    from alpaca.data.requests import CryptoLatestQuoteRequest
                    quotes_request = CryptoLatestQuoteRequest(symbol_or_symbols=[symbol])
                    quotes = self._crypto_data_client.get_crypto_latest_quote(quotes_request)
                    quotes_data = quotes.data if hasattr(quotes, 'data') else quotes

                    if symbol in quotes_data:
                        quote_obj = quotes_data[symbol]
                        result = {
                            "bid": float(quote_obj.bid_price),
                            "ask": float(quote_obj.ask_price),
                            "timestamp": quote_obj.timestamp.isoformat()
                        }
                        self._cache_quote(symbol, asset_class, result)
                        return result
                    else:
                        raise Exception(f"No crypto quote data for {symbol}")
                except (AttributeError, ImportError):
                    # Fallback: try crypto bars
                    from alpaca.data.requests import CryptoBarsRequest
                    from alpaca.data.timeframe import TimeFrame

                    bars_request = CryptoBarsRequest(
                        symbol_or_symbols=[symbol],
                        timeframe=TimeFrame.Minute,
                        limit=1
                    )
                    bars = self._crypto_data_client.get_crypto_bars(bars_request)
                    bars_data = bars.data if hasattr(bars, 'data') else bars

                    if symbol in bars_data:
                        bar = bars_data[symbol][-1] if bars_data[symbol] else None
                        if bar:
                            close_price = float(bar.close)
                            result = {
                                "bid": close_price * 0.999,
                                "ask": close_price * 1.001,
                                "timestamp": bar.timestamp.isoformat()
                            }
                            self._cache_quote(symbol, asset_class, result)
                            return result
                    raise Exception(f"No crypto bar data available for {symbol}")
            else:
                raise ValueError(f"Unsupported asset class: {asset_class}")

        except Exception as e:
            error_msg = f"Quote fetch failed for {symbol}: {e}"
            self._logger.warn(error_msg)
            self._health.record_api_failure(str(e))
            raise Exception(str(e))
    
    def _cache_quote(self, symbol: str, asset_class: str, quote: Dict[str, float]) -> None:
        """Cache a quote result with timestamp."""
        cache_key = f"{symbol}:{asset_class}"
        self._quote_cache[cache_key] = {
            "quote": quote,
            "ts": time.time()
        }

    # ==========================================================================
    # HISTORICAL BARS - For Turtle Traders strategy (Donchian channels, ATR)
    # ==========================================================================

    def get_stock_bars(
        self,
        symbol: str,
        timeframe: str = "1Day",
        limit: int = 30
    ) -> List[Any]:
        """
        Fetch historical bars for a stock symbol.
        
        Used by Turtle Traders strategy for:
        - Donchian Channel calculation (20/55-day highs/lows)
        - ATR calculation (Average True Range)
        
        Args:
            symbol: Stock symbol (e.g., "AAPL")
            timeframe: Bar timeframe ("1Day", "1Hour", "1Min")
            limit: Number of bars to fetch
            
        Returns:
            List of bar objects with open, high, low, close, volume
        """
        bar_cache_key = f"{symbol}:{timeframe}:{limit}"
        now = time.time()
        if bar_cache_key in self._bars_cache:
            cached = self._bars_cache[bar_cache_key]
            if now - cached["ts"] < self._bars_cache_ttl:
                self._logger.log("alpaca_stock_bars_cache_hit", {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "count": len(cached["bars"]),
                    "cache_age_sec": round(now - cached["ts"], 1)
                })
                return cached["bars"]
        
        self._logger.log("alpaca_get_stock_bars", {
            "symbol": symbol,
            "timeframe": timeframe,
            "limit": limit
        })
        
        try:
            if not self._stock_data_client:
                self._logger.warn(f"Stock data client not available for {symbol}")
                return []
            
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            from datetime import datetime, timedelta
            
            tf_map = {
                "1Day": TimeFrame.Day,
                "1Hour": TimeFrame.Hour,
                "1Min": TimeFrame.Minute,
                "5Min": TimeFrame.Minute
            }
            tf = tf_map.get(timeframe, TimeFrame.Day)
            
            if timeframe == "1Day":
                days_back = max(limit * 2, 60)
            elif timeframe == "1Hour":
                days_back = max(limit // 6, 7)
            else:
                days_back = 5
            
            start_date = datetime.now() - timedelta(days=days_back)
            
            bars_request = StockBarsRequest(
                symbol_or_symbols=[symbol],
                timeframe=tf,
                start=start_date,
                limit=limit
            )
            
            bars_response = self._stock_data_client.get_stock_bars(bars_request)
            
            bars_data = bars_response.data if hasattr(bars_response, 'data') else bars_response
            
            if symbol in bars_data:
                bars = list(bars_data[symbol])
                self._logger.log("alpaca_stock_bars_fetched", {
                    "symbol": symbol,
                    "count": len(bars),
                    "timeframe": timeframe
                })
                self._bars_cache[bar_cache_key] = {"bars": bars, "ts": time.time()}
                return bars
            
            self._logger.warn(f"No bars data for {symbol}")
            return []
            
        except Exception as e:
            self._logger.error(f"Failed to fetch stock bars for {symbol}: {e}")
            self._health.record_api_failure(str(e))
            return []

    def get_crypto_bars(
        self,
        symbol: str,
        timeframe: str = "1Hour",
        limit: int = 30,
        start: Optional[str] = None
    ) -> List[Any]:
        """
        Fetch historical bars for a crypto symbol.
        
        Used by CryptoBot Turtle strategy for 24/7 trading.
        
        Args:
            symbol: Crypto symbol (e.g., "BTC/USD")
            timeframe: Bar timeframe ("1Day", "1Hour", "1Min", "15Min")
            limit: Number of bars to fetch
            start: Optional ISO timestamp for start time
            
        Returns:
            List of bar objects with open, high, low, close, volume
        """
        self._logger.log("alpaca_get_crypto_bars", {
            "symbol": symbol,
            "timeframe": timeframe,
            "limit": limit,
            "start": start
        })
        
        try:
            if not self._crypto_data_client:
                self._logger.warn(f"Crypto data client not available for {symbol}")
                return []
            
            from alpaca.data.requests import CryptoBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
            from datetime import datetime, timedelta
            
            tf_map = {
                "1Day": TimeFrame.Day,
                "1Hour": TimeFrame.Hour,
                "15Min": TimeFrame(15, TimeFrameUnit.Minute),
                "1Min": TimeFrame.Minute
            }
            tf = tf_map.get(timeframe, TimeFrame.Hour)
            
            if start:
                from dateutil.parser import parse as parse_date
                start_date = parse_date(start.rstrip("Z"))
            else:
                if timeframe == "1Day":
                    days_back = max(limit * 2, 60)
                elif timeframe == "1Hour":
                    days_back = max(limit // 24 + 1, 7)
                elif timeframe == "15Min":
                    days_back = max(limit // 96 + 1, 2)
                else:
                    days_back = 2
                start_date = datetime.now() - timedelta(days=days_back)
            
            bars_request = CryptoBarsRequest(
                symbol_or_symbols=[symbol],
                timeframe=tf,
                start=start_date,
                limit=limit
            )
            
            bars_response = self._crypto_data_client.get_crypto_bars(bars_request)
            
            bars_data = bars_response.data if hasattr(bars_response, 'data') else bars_response
            
            if symbol in bars_data:
                bars = list(bars_data[symbol])
                self._logger.log("alpaca_crypto_bars_fetched", {
                    "symbol": symbol,
                    "count": len(bars),
                    "timeframe": timeframe
                })
                return bars
            
            self._logger.warn(f"No crypto bars data for {symbol}")
            return []
            
        except Exception as e:
            self._logger.error(f"Failed to fetch crypto bars for {symbol}: {e}")
            self._health.record_api_failure(str(e))
            return []

    def get_bars(
        self,
        symbol: str,
        timeframe: str = "1Day",
        limit: int = 30,
        days: int = None
    ) -> List[Any]:
        """
        Generic get_bars wrapper that delegates to get_stock_bars or get_crypto_bars.
        
        This provides a unified interface for services that don't need to know
        whether they're fetching stock or crypto data.
        
        Args:
            symbol: Asset symbol (e.g., "AAPL" or "BTC/USD")
            timeframe: Bar timeframe ("1Day", "1Hour", "1Min")
            limit: Number of bars to fetch
            days: Alternative to limit - number of days of data
            
        Returns:
            List of bar objects with open, high, low, close, volume
        """
        if days is not None:
            limit = days
        
        if self._is_option_symbol(symbol):
            self._logger.warn(f"get_bars called for option symbol {symbol}, use get_option_snapshot instead")
            return []
        elif self._is_crypto_symbol(symbol):
            return self.get_crypto_bars(symbol, timeframe, limit)
        else:
            return self.get_stock_bars(symbol, timeframe, limit)
    
    def _is_option_symbol(self, symbol: str) -> bool:
        """Detect if a symbol is an options contract (OCC format)."""
        import re
        if len(symbol) >= 15 and re.match(r'^[A-Z]{1,6}\d{6}[CP]\d{8}$', symbol):
            return True
        if any(x in symbol for x in ['SPXW', 'SPX', 'VIX']) and len(symbol) > 10:
            return True
        return False
    
    def _is_crypto_symbol(self, symbol: str) -> bool:
        """Detect if a symbol is a crypto pair."""
        if "/" in symbol:
            return True
        crypto_suffixes = ['USD', 'USDT', 'USDC', 'BTC', 'ETH']
        for suffix in crypto_suffixes:
            if symbol.endswith(suffix) and len(symbol) > len(suffix):
                base = symbol[:-len(suffix)]
                if base.isupper() and len(base) >= 2 and len(base) <= 5:
                    return True
        return False

    # ==========================================================================
    # OPTIONS DATA METHODS - Production-level options chain with real Greeks
    # ==========================================================================

    def get_options_chain(
        self,
        underlying_symbol: str,
        expiration_date_gte: Optional[str] = None,
        expiration_date_lte: Optional[str] = None,
        strike_price_gte: Optional[float] = None,
        strike_price_lte: Optional[float] = None,
        option_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch real options chain from Alpaca API with Greeks and IV.
        
        Production-level: Attempts to fetch real market data, with robust
        error handling and automatic fallback. Returns empty list on failure
        so caller can use Black-Scholes simulation as backup.
        
        Args:
            underlying_symbol: Stock symbol (e.g., "SPY", "QQQ")
            expiration_date_gte: Minimum expiration date (YYYY-MM-DD)
            expiration_date_lte: Maximum expiration date (YYYY-MM-DD)
            strike_price_gte: Minimum strike price
            strike_price_lte: Maximum strike price
            option_type: "call" or "put" (None for both)
            
        Returns:
            List of option contracts with market data, or empty list on failure
        """
        options = []
        
        # GUARD: Need options data client
        if not self._options_data_client:
            self._logger.warn("Options data client not available")
            return options
        
        try:
            # Try to use Alpaca SDK's option chain functionality
            # Note: SDK versions may vary, so we handle multiple approaches
            
            # Approach 1: Try OptionChainRequest if available
            try:
                from alpaca.data.requests import OptionChainRequest as ChainReq
                
                request_params = {"underlying_symbol": underlying_symbol}
                if expiration_date_gte:
                    request_params["expiration_date_gte"] = expiration_date_gte
                if expiration_date_lte:
                    request_params["expiration_date_lte"] = expiration_date_lte
                if strike_price_gte:
                    request_params["strike_price_gte"] = strike_price_gte
                if strike_price_lte:
                    request_params["strike_price_lte"] = strike_price_lte
                
                chain_request = ChainReq(**request_params)
                self._throttle()
                chain_data = self._options_data_client.get_option_chain(chain_request)
                
                # Handle different response formats from SDK
                if hasattr(chain_data, 'items'):
                    # Dict-like response
                    items = chain_data.items()
                elif hasattr(chain_data, '__iter__'):
                    # Iterable response
                    items = [(getattr(c, 'symbol', str(c)), c) for c in chain_data]
                else:
                    items = []
                
                for symbol, snapshot in items:
                    option_contract = self._parse_option_snapshot(symbol, underlying_symbol, snapshot)
                    if option_contract:
                        options.append(option_contract)
                        
            except (ImportError, AttributeError) as e:
                self._logger.warn(f"OptionChainRequest not available: {e}")
                # Will return empty list, caller uses simulation
                
        except Exception as e:
            self._logger.warn(f"Options chain fetch failed for {underlying_symbol}: {e}")
            self._health.record_api_failure(str(e))
        
        if options:
            self._logger.log("options_chain_fetched", {
                "underlying": underlying_symbol,
                "contracts_found": len(options)
            })
        
        return options
    
    def _parse_option_snapshot(self, symbol: str, underlying: str, snapshot) -> Optional[Dict[str, Any]]:
        """
        Parse an option snapshot into a standardized dictionary.
        
        Handles different SDK response formats robustly.
        
        Args:
            symbol: Option contract symbol
            underlying: Underlying stock symbol
            snapshot: Raw snapshot object from SDK
            
        Returns:
            Standardized option contract dictionary or None
        """
        try:
            # Determine option type from symbol
            # Standard format: {UNDERLYING}{YYMMDD}{C|P}{STRIKE*1000}
            # The C/P indicator is between the date and strike
            option_type = "call"
            symbol_upper = symbol.upper()
            if "P" in symbol_upper[len(underlying):len(underlying)+7]:
                option_type = "put"
            elif "C" in symbol_upper[len(underlying):len(underlying)+7]:
                option_type = "call"
            
            option_contract = {
                "symbol": symbol,
                "underlying": underlying,
                "type": option_type,
            }
            
            # Extract quote data (bid/ask) - handle various attribute names
            bid = 0.0
            ask = 0.0
            if hasattr(snapshot, 'latest_quote') and snapshot.latest_quote:
                quote = snapshot.latest_quote
                bid = float(getattr(quote, 'bid_price', 0) or 0)
                ask = float(getattr(quote, 'ask_price', 0) or 0)
            elif hasattr(snapshot, 'bid_price'):
                bid = float(snapshot.bid_price or 0)
                ask = float(getattr(snapshot, 'ask_price', 0) or 0)
            
            option_contract["bid"] = bid
            option_contract["ask"] = ask
            
            # Extract Greeks - handle both nested and flat structures
            delta = gamma = theta = vega = rho = 0.0
            if hasattr(snapshot, 'greeks') and snapshot.greeks:
                g = snapshot.greeks
                delta = float(getattr(g, 'delta', 0) or 0)
                gamma = float(getattr(g, 'gamma', 0) or 0)
                theta = float(getattr(g, 'theta', 0) or 0)
                vega = float(getattr(g, 'vega', 0) or 0)
                rho = float(getattr(g, 'rho', 0) or 0)
            else:
                # Try flat structure
                delta = float(getattr(snapshot, 'delta', 0) or 0)
                gamma = float(getattr(snapshot, 'gamma', 0) or 0)
                theta = float(getattr(snapshot, 'theta', 0) or 0)
                vega = float(getattr(snapshot, 'vega', 0) or 0)
                rho = float(getattr(snapshot, 'rho', 0) or 0)
            
            option_contract.update({
                "delta": delta,
                "gamma": gamma,
                "theta": theta,
                "vega": vega,
                "rho": rho
            })
            
            # Extract IV
            iv = float(getattr(snapshot, 'implied_volatility', 0) or 0)
            option_contract["iv"] = iv if iv > 0 else 0.25  # Default 25%
            
            # Parse strike and expiry from symbol
            try:
                # Remove underlying prefix
                suffix = symbol.upper().replace(underlying.upper(), "", 1)
                # Extract date (first 6 chars)
                if len(suffix) >= 7:
                    expiry_str = suffix[:6]
                    opt_type_char = suffix[6]
                    strike_str = suffix[7:]
                    
                    from datetime import datetime
                    expiry = datetime.strptime(f"20{expiry_str}", "%Y%m%d")
                    option_contract["expiry"] = expiry.strftime("%Y-%m-%d")
                    now_naive = get_market_clock().now().replace(tzinfo=None)
                    option_contract["dte"] = (expiry - now_naive).days
                    option_contract["strike"] = float(strike_str) / 1000
                    option_contract["type"] = "call" if opt_type_char == "C" else "put"
            except Exception:
                option_contract["strike"] = 0.0
                option_contract["expiry"] = ""
                option_contract["dte"] = 0
            
            # Volume/OI estimation based on bid-ask spread
            spread = ask - bid if ask > bid else 0.05
            liquidity_score = 1.0 / (1 + spread)  # Tighter spread = more liquid
            option_contract["volume"] = int(500 * liquidity_score)
            option_contract["open_interest"] = int(2000 * liquidity_score)
            
            return option_contract
            
        except Exception as e:
            self._logger.warn(f"Failed to parse option snapshot {symbol}: {e}")
            return None

    def get_option_snapshot(self, option_symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get snapshot for a specific option contract with Greeks and IV.
        
        Args:
            option_symbol: Full option symbol (e.g., "SPY250127C00608000")
            
        Returns:
            Dictionary with option data or None if not available
        """
        if not self._options_data_client:
            return None
        
        try:
            # Try to get snapshot using SDK
            try:
                from alpaca.data.requests import OptionSnapshotRequest as SnapReq
                snapshot_request = SnapReq(symbol_or_symbols=[option_symbol])
                snapshots = self._options_data_client.get_option_snapshot(snapshot_request)
            except (ImportError, AttributeError):
                # Fallback: try direct method call
                snapshots = self._options_data_client.get_option_snapshot([option_symbol])
            
            # Handle different response formats
            if hasattr(snapshots, 'get'):
                snapshot = snapshots.get(option_symbol)
            elif hasattr(snapshots, option_symbol):
                snapshot = getattr(snapshots, option_symbol)
            elif isinstance(snapshots, list) and len(snapshots) > 0:
                snapshot = snapshots[0]
            else:
                return None
            
            if snapshot:
                # Parse the underlying from symbol (first 1-5 chars before date)
                underlying = option_symbol[:3]  # Default to first 3 chars
                for i in range(1, min(6, len(option_symbol))):
                    if option_symbol[i].isdigit():
                        underlying = option_symbol[:i]
                        break
                
                return self._parse_option_snapshot(option_symbol, underlying, snapshot)
                
        except Exception as e:
            self._logger.warn(f"Failed to get option snapshot for {option_symbol}: {e}")
        
        return None

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an order by ID.
        
        Args:
            order_id: The order ID to cancel
            
        Returns:
            True if cancelled successfully, False otherwise
        """
        if not self._trading_client:
            self._logger.warn("Trading client not available for cancel")
            return False
        
        try:
            self._trading_client.cancel_order_by_id(order_id)
            self._logger.log("order_cancelled", {"order_id": order_id})
            return True
        except Exception as e:
            self._logger.warn(f"Failed to cancel order {order_id}: {e}")
            return False
    
    def place_options_bracket_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        limit_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        time_in_force: str = "gtc"
    ) -> Dict[str, Any]:
        """
        Place an options order with manual OCO bracket (stop loss + take profit).
        
        Since Alpaca's bracket orders are primarily for stocks, we implement
        options brackets manually:
        1. Place limit entry order
        2. Store bracket prices for later leg placement after fill
        
        The caller is responsible for monitoring fill and placing OCO legs.
        
        Args:
            symbol: Option contract symbol (e.g., "SPY250117C00585000")
            qty: Number of contracts
            side: "buy" or "sell"
            limit_price: Entry limit price
            stop_loss_price: Stop loss price
            take_profit_price: Take profit price
            time_in_force: "day" or "gtc"
            
        Returns:
            Order response with bracket metadata
        """
        self._logger.log("place_options_bracket_order", {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "limit_price": limit_price,
            "stop_loss": stop_loss_price,
            "take_profit": take_profit_price
        })
        
        if not self._trading_client:
            raise RuntimeError("Trading client not available")
        
        try:
            tif = TimeInForce.DAY  # Options require DAY time_in_force on Alpaca
            order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
            
            # Place entry limit order
            entry_request = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=order_side,
                time_in_force=tif,
                limit_price=round(limit_price, 2)
            )
            
            order = self._trading_client.submit_order(entry_request)
            
            result = {
                "success": True,
                "order_id": str(order.id),
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "entry_price": limit_price,
                "stop_loss": stop_loss_price,
                "take_profit": take_profit_price,
                "status": str(order.status),
                "bracket_pending": True  # Indicates OCO legs need placement after fill
            }
            
            self._logger.log("options_bracket_entry_placed", result)
            return result
                
        except Exception as e:
            self._logger.error(f"Failed to place options bracket order: {e}")
            return {"success": False, "error": str(e)}
    
    def place_options_exit_orders(
        self,
        symbol: str,
        qty: int,
        stop_loss_price: float,
        take_profit_price: float,
        time_in_force: str = "gtc"
    ) -> Dict[str, Any]:
        """
        Place independent exit orders for options positions.
        
        Since Alpaca doesn't support OCO/bracket orders for options via SDK,
        we place independent stop and take-profit orders. The caller must
        monitor fills and cancel the remaining order when one triggers.
        
        Args:
            symbol: Option contract symbol
            qty: Number of contracts to exit
            stop_loss_price: Stop loss trigger price
            take_profit_price: Take profit limit price
            time_in_force: "day" or "gtc"
            
        Returns:
            Dict with exit order IDs, per-leg status, and overall success
            - success=True if at least one protective leg is live
            - both_legs=True if both legs were placed successfully
        """
        self._logger.log("place_options_exit_orders", {
            "symbol": symbol,
            "qty": qty,
            "stop_loss": stop_loss_price,
            "take_profit": take_profit_price
        })
        
        result = {
            "success": False,
            "both_legs": False,
            "tp_order_id": None,
            "sl_order_id": None,
            "tp_error": None,
            "sl_error": None,
            "symbol": symbol
        }
        
        # Place take-profit limit order
        try:
            tp_result = self.place_options_order(
                symbol=symbol,
                qty=qty,
                side="sell",
                order_type="limit",
                limit_price=take_profit_price,
                time_in_force=time_in_force
            )
            
            if tp_result and tp_result.get("id"):
                result["tp_order_id"] = tp_result.get("id")
                self._logger.log("options_tp_order_placed", {
                    "symbol": symbol,
                    "order_id": result["tp_order_id"],
                    "price": take_profit_price
                })
        except Exception as tp_err:
            result["tp_error"] = str(tp_err)
            self._logger.error(f"Failed to place TP order: {tp_err}")
        
        # Place stop-loss order (use limit at stop price for options)
        try:
            sl_result = self.place_options_order(
                symbol=symbol,
                qty=qty,
                side="sell",
                order_type="limit",
                limit_price=stop_loss_price,
                time_in_force=time_in_force
            )
            
            if sl_result and sl_result.get("id"):
                result["sl_order_id"] = sl_result.get("id")
                self._logger.log("options_sl_order_placed", {
                    "symbol": symbol,
                    "order_id": result["sl_order_id"],
                    "price": stop_loss_price
                })
        except Exception as sl_err:
            result["sl_error"] = str(sl_err)
            self._logger.error(f"Failed to place SL order: {sl_err}")
        
        # Success if at least one leg is placed (provides some protection)
        if result["tp_order_id"] or result["sl_order_id"]:
            result["success"] = True
            if result["tp_order_id"] and result["sl_order_id"]:
                result["both_legs"] = True
            self._logger.log("options_exit_orders_placed", result)
        else:
            result["error"] = "Failed to place any exit orders"
            self._logger.error(f"No exit orders placed for {symbol}: TP={result['tp_error']}, SL={result['sl_error']}")
        
        return result
    
    def get_order_status(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the status of an order by ID.
        
        Args:
            order_id: The order ID to check
            
        Returns:
            Order status dict or None if not found
        """
        if not self._trading_client:
            return None
        
        try:
            order = self._trading_client.get_order_by_id(order_id)
            return {
                "id": str(order.id),
                "status": str(order.status),
                "filled_qty": float(order.filled_qty or 0),
                "filled_avg_price": float(order.filled_avg_price or 0),
                "symbol": order.symbol
            }
        except Exception as e:
            self._logger.warn(f"Failed to get order {order_id}: {e}")
            return None

    def place_options_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        time_in_force: str = "gtc",
        client_order_id: Optional[str] = None,
        position_intent: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Place an options order via Alpaca.
        
        Args:
            symbol: Option contract symbol (e.g., "SPY250127C00608000")
            qty: Number of contracts
            side: "buy" or "sell"
            order_type: "market" or "limit"
            limit_price: Required for limit orders
            time_in_force: "day", "gtc", "ioc", "fok"
            client_order_id: Optional idempotency key for order correlation
            position_intent: "buy_to_open", "buy_to_close", "sell_to_open", "sell_to_close"
                            If not specified, defaults to buy_to_open for buys, sell_to_close for sells
            
        Returns:
            Order response dictionary
        """
        # Default position intents for Long Calls/Puts (not uncovered writing)
        if position_intent is None:
            position_intent = "buy_to_open" if side.lower() == "buy" else "sell_to_close"
        
        self._logger.log("place_options_order", {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "order_type": order_type,
            "limit_price": limit_price,
            "client_order_id": client_order_id,
            "position_intent": position_intent
        })
        
        if not self._trading_client:
            raise RuntimeError("Trading client not available")
        
        try:
            # Options require DAY time_in_force on Alpaca (GTC not supported)
            tif = TimeInForce.DAY
            if time_in_force.lower() == "ioc":
                tif = TimeInForce.IOC
            elif time_in_force.lower() == "fok":
                tif = TimeInForce.FOK
            
            # Build order request based on type
            order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
            
            # Convert position_intent string to enum for Long Calls/Puts
            intent_map = {
                "buy_to_open": PositionIntent.BUY_TO_OPEN,
                "buy_to_close": PositionIntent.BUY_TO_CLOSE,
                "sell_to_open": PositionIntent.SELL_TO_OPEN,
                "sell_to_close": PositionIntent.SELL_TO_CLOSE
            }
            pos_intent = intent_map.get(position_intent, PositionIntent.BUY_TO_OPEN)
            
            if order_type == "limit" and limit_price:
                order_request = LimitOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=order_side,
                    time_in_force=tif,
                    limit_price=limit_price,
                    client_order_id=client_order_id,
                    position_intent=pos_intent
                )
            else:
                order_request = MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=order_side,
                    time_in_force=tif,
                    client_order_id=client_order_id,
                    position_intent=pos_intent
                )
            
            # Submit the order
            order = self._trading_client.submit_order(order_request)
            
            result = {
                "id": str(order.id),
                "client_order_id": str(order.client_order_id) if order.client_order_id else client_order_id,
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "status": str(order.status),
                "filled_qty": float(order.filled_qty or 0),
                "filled_avg_price": float(order.filled_avg_price or 0)
            }
            
            self._logger.log("options_order_placed", result)
            return result
            
        except Exception as e:
            self._logger.error(f"Failed to place options order: {e}")
            raise


_alpaca_client: Optional[AlpacaClient] = None


def get_alpaca_client() -> AlpacaClient:
    global _alpaca_client
    if _alpaca_client is None:
        _alpaca_client = AlpacaClient()
    return _alpaca_client