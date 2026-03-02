"""Execution service for running trading bots with real Alpaca integration"""
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
import time
from datetime import datetime, timedelta

from ..core.logging import get_logger
from ..core.config import load_bots_config
from ..core.state import get_state, set_state, generate_client_order_id, is_order_already_submitted, record_order_submission, get_last_trade_timestamp, set_last_trade_timestamp
from ..risk.trailing_stop import get_trailing_stop_manager, TrailingStopConfig
from ..risk.slippage_tracker import get_slippage_tracker
from ..core.health import get_health_monitor
from ..core.risk import valid_budget
from ..core.console import TickerSignal
from .decision_tracker import get_decision_tracker


@dataclass
class ExecutionResult:
    bots_run: List[str]
    trades_attempted: int
    positions_managed: int
    errors: List[str]
    signals: List[TickerSignal] = field(default_factory=list)
    bots_outside_hours: List[str] = field(default_factory=list)


class ExecutionService:
    def __init__(self):
        self._logger = get_logger()
        self._health = get_health_monitor()
        self._slippage_tracker = get_slippage_tracker()
        self._policy_gate = None
        self._order_state_machine = None
    
    def _get_policy_gate(self):
        """Lazy load PolicyGate to avoid circular imports."""
        if self._policy_gate is None:
            try:
                from ..risk.policy_gate import get_policy_gate
                self._policy_gate = get_policy_gate()
            except Exception as e:
                self._logger.error(f"Failed to load PolicyGate: {e}")
        return self._policy_gate
    
    def _get_order_state_machine(self):
        """Lazy load OrderStateMachine to avoid circular imports."""
        if self._order_state_machine is None:
            try:
                from ..risk.order_state_machine import get_order_state_machine
                self._order_state_machine = get_order_state_machine()
            except Exception as e:
                self._logger.error(f"Failed to load OrderStateMachine: {e}")
        return self._order_state_machine
    
    def _validate_and_place_order(
        self,
        alpaca,
        symbol: str,
        side: str,
        qty: float,
        bot_id: str,
        order_type: str = "market",
        asset_class: str = "equity",
        expected_price: Optional[float] = None,
        limit_price: Optional[float] = None,
        client_order_id: Optional[str] = None,
        skip_policy_gate: bool = False,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Centralized order placement with PolicyGate validation and OSM tracking.
        
        This is the ONLY path for placing orders - ensures all trades go through
        unified pre-trade validation with fail-closed semantics.
        
        Args:
            alpaca: Alpaca client instance
            symbol: Trading symbol
            side: 'buy' or 'sell'
            qty: Quantity to trade
            bot_id: ID of the requesting bot
            order_type: 'market' or 'limit'
            asset_class: 'equity', 'option', or 'crypto'
            expected_price: Expected fill price (for slippage tracking)
            limit_price: Limit price (for limit orders)
            client_order_id: Optional custom order ID
            skip_policy_gate: If True, skip PolicyGate (for exits/safety orders only)
            metadata: Additional order metadata
            
        Returns:
            {"success": bool, "order_id": str, "order_response": dict, "blocked_reason": str}
        """
        from ..core.state import generate_client_order_id, record_order_submission
        
        if client_order_id is None:
            signal_id = f"{side}_{symbol}_{int(time.time())}"
            client_order_id = generate_client_order_id(bot_id, symbol, signal_id)
        
        result = {
            "success": False,
            "order_id": None,
            "order_response": None,
            "blocked_reason": None
        }
        
        policy_gate = self._get_policy_gate()
        osm = self._get_order_state_machine()
        
        if policy_gate and not skip_policy_gate:
            try:
                from ..risk.policy_gate import OrderRequest
                
                order_request = OrderRequest(
                    symbol=symbol,
                    bot_id=bot_id,
                    side=side,
                    asset_class=asset_class,
                    size_usd=qty * (expected_price or 1.0),
                    is_entry=(side == "buy"),
                    expected_fill_price=expected_price
                )
                
                verdict = policy_gate.validate_order(order_request)
                
                if not verdict.approved:
                    self._logger.log("policy_gate_order_blocked", {
                        "symbol": symbol,
                        "side": side,
                        "qty": qty,
                        "bot_id": bot_id,
                        "decision": verdict.decision.value,
                        "block_reason": verdict.block_reason.value if verdict.block_reason else None,
                        "block_details": verdict.block_details
                    })
                    result["blocked_reason"] = f"PolicyGate: {verdict.block_details}"
                    return result
                
                self._logger.log("policy_gate_order_approved", {
                    "symbol": symbol,
                    "side": side,
                    "bot_id": bot_id,
                    "decision": verdict.decision.value,
                    "size_multiplier": verdict.size_multiplier,
                    "gates_passed": list(verdict.gates_passed.keys())
                })
            except Exception as e:
                self._logger.error(f"[FAIL_CLOSED] PolicyGate error - blocking order: {e}")
                result["blocked_reason"] = f"PolicyGate FAIL_CLOSED: {e}"
                return result
        
        if osm:
            internal_order_id = osm.create_order(
                symbol=symbol,
                bot_id=bot_id,
                side=side,
                asset_class=asset_class,
                qty=qty,
                order_type=order_type,
                limit_price=limit_price,
                expected_price=expected_price,
                metadata=metadata or {}
            )
        else:
            internal_order_id = client_order_id
        
        if side == "sell":
            try:
                from .exitbot import get_exitbot
                exitbot = get_exitbot()
                cancel_result = exitbot.cancel_staged_orders_for_symbol(
                    symbol, reason=f"wash_trade_prevention_{bot_id}"
                )
                if cancel_result.get("cancelled_count", 0) > 0:
                    self._logger.log("wash_trade_staged_orders_cleared", {
                        "symbol": symbol,
                        "bot_id": bot_id,
                        "cancelled_count": cancel_result["cancelled_count"],
                        "position_ids": cancel_result["position_ids"]
                    })
            except Exception as e:
                self._logger.error(f"[WASH_TRADE_PREVENTION] Failed to cancel staged orders for {symbol}: {e}")

            try:
                from .alpaca_client import get_alpaca_client
                alpaca_client = get_alpaca_client()
                open_orders = alpaca_client.get_open_orders(symbol=symbol)
                sell_orders = [o for o in open_orders if o.get("side") == "sell"]
                for order in sell_orders:
                    try:
                        alpaca_client.cancel_order(order["id"])
                        self._logger.log("wash_trade_alpaca_order_cancelled", {
                            "symbol": symbol,
                            "bot_id": bot_id,
                            "cancelled_order_id": order["id"],
                            "cancelled_order_side": order.get("side")
                        })
                    except Exception as cancel_err:
                        self._logger.error(f"[WASH_TRADE_PREVENTION] Failed to cancel Alpaca order {order.get('id')}: {cancel_err}")
                if sell_orders:
                    import time as _time
                    _time.sleep(0.5)
            except Exception as e:
                self._logger.error(f"[WASH_TRADE_PREVENTION] Failed to cancel open Alpaca sell orders for {symbol}: {e}")

        if side == "sell":
            try:
                from .alpaca_client import get_alpaca_client
                alpaca_client = get_alpaca_client()
                position_symbol = symbol.replace("/", "")
                pos_data = alpaca_client._request("GET", f"/v2/positions/{position_symbol}")
                position_qty = abs(float(pos_data.get("qty", 0)))

                if position_qty <= 0:
                    self._logger.log("sell_qty_capped", {
                        "symbol": symbol,
                        "bot_id": bot_id,
                        "original_qty": qty,
                        "position_qty": 0,
                        "action": "skip_order_no_position"
                    })
                    result["blocked_reason"] = f"No position found for {symbol} (qty=0), skipping sell"
                    return result

                if qty > position_qty:
                    self._logger.log("sell_qty_capped", {
                        "symbol": symbol,
                        "bot_id": bot_id,
                        "original_qty": qty,
                        "capped_qty": position_qty,
                        "reason": "qty_exceeds_position"
                    })
                    qty = position_qty
            except RuntimeError as pos_err:
                if "404" in str(pos_err):
                    self._logger.log("sell_qty_capped", {
                        "symbol": symbol,
                        "bot_id": bot_id,
                        "original_qty": qty,
                        "position_qty": 0,
                        "action": "skip_order_position_not_found"
                    })
                    result["blocked_reason"] = f"Position not found for {symbol}, skipping sell"
                    return result
                self._logger.error(f"[QTY_CAP] Failed to query position for {symbol}: {pos_err}")
            except Exception as pos_err:
                self._logger.error(f"[QTY_CAP] Failed to query position for {symbol}: {pos_err}")

        def _attempt_order_placement():
            record_order_submission(client_order_id, bot_id, symbol, f"{side}_{symbol}")

            if order_type == "limit" and limit_price:
                return alpaca.place_limit_order(
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    limit_price=limit_price,
                    client_order_id=client_order_id
                )
            else:
                return alpaca.place_market_order(
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    client_order_id=client_order_id
                )

        try:
            order_response = _attempt_order_placement()

            if order_response.get("error") or order_response.get("success") is False:
                error_msg = order_response.get("error", "Unknown order error")
                raise RuntimeError(f"Order rejected by broker: {error_msg}")

            broker_order_id = order_response.get("id")
            if not broker_order_id:
                self._logger.error(f"[ORDER_INTEGRITY] Broker returned no order ID for {symbol} {side} {qty}: {order_response}")
                raise RuntimeError(f"Broker returned no order ID for {symbol} - response malformed")
            
            if osm:
                osm.mark_submitted(internal_order_id, broker_order_id)
            
            record_order_submission(client_order_id, bot_id, symbol, f"{side}_{symbol}", broker_order_id)
            
            result["success"] = True
            result["order_id"] = broker_order_id
            result["order_response"] = order_response
            
            self._logger.log("validated_order_placed", {
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "bot_id": bot_id,
                "order_id": broker_order_id,
                "order_type": order_type,
                "internal_order_id": internal_order_id
            })
            
        except Exception as e:
            error_str = str(e)
            is_retryable = "40310000" in error_str or "insufficient qty" in error_str.lower() or "wash trade" in error_str.lower() or "sold short" in error_str.lower()

            if is_retryable:
                self._logger.log("order_placement_retry", {
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "bot_id": bot_id,
                    "original_error": error_str,
                    "action": "cancel_all_and_retry"
                })

                try:
                    from .alpaca_client import get_alpaca_client
                    alpaca_client = get_alpaca_client()
                    all_orders = alpaca_client.get_open_orders(symbol=symbol)
                    for order in all_orders:
                        try:
                            alpaca_client.cancel_order(order["id"])
                        except Exception:
                            pass
                    if all_orders:
                        import time as _time
                        _time.sleep(0.5)

                    if side == "sell":
                        try:
                            position_symbol = symbol.replace("/", "")
                            pos_data = alpaca_client._request("GET", f"/v2/positions/{position_symbol}")
                            fresh_qty = abs(float(pos_data.get("qty", 0)))
                            if fresh_qty <= 0:
                                result["blocked_reason"] = f"Retry: no position for {symbol} after cancel"
                                return result
                            qty = fresh_qty
                        except Exception:
                            result["blocked_reason"] = f"Retry: position query failed for {symbol}"
                            return result

                    client_order_id = generate_client_order_id(bot_id, symbol, f"retry_{side}_{symbol}_{int(time.time())}")
                    order_response = _attempt_order_placement()

                    if order_response.get("error") or order_response.get("success") is False:
                        error_msg = order_response.get("error", "Unknown order error")
                        raise RuntimeError(f"Retry also rejected: {error_msg}")

                    broker_order_id = order_response.get("id")
                    if not broker_order_id:
                        raise RuntimeError(f"Retry returned no order ID for {symbol}")
                    if osm:
                        osm.mark_submitted(internal_order_id, broker_order_id)
                    record_order_submission(client_order_id, bot_id, symbol, f"{side}_{symbol}", broker_order_id)

                    result["success"] = True
                    result["order_id"] = broker_order_id
                    result["order_response"] = order_response

                    self._logger.log("validated_order_placed_on_retry", {
                        "symbol": symbol,
                        "side": side,
                        "qty": qty,
                        "bot_id": bot_id,
                        "order_id": broker_order_id,
                        "order_type": order_type,
                        "internal_order_id": internal_order_id
                    })
                except Exception as retry_err:
                    self._logger.error(f"Order placement retry failed: {symbol} {side} {qty}: {retry_err}")
                    if osm:
                        osm.mark_rejected(internal_order_id, str(retry_err))
                    result["blocked_reason"] = f"Execution error (after retry): {retry_err}"
            else:
                self._logger.error(f"Order placement failed: {symbol} {side} {qty}: {e}")
                if osm:
                    osm.mark_rejected(internal_order_id, str(e))
                result["blocked_reason"] = f"Execution error: {e}"
        
        return result

    def _track_slippage(
        self,
        symbol: str,
        expected_price: float,
        actual_price: float,
        qty: float,
        side: str,
        order_type: str = "market"
    ) -> None:
        """
        Track slippage for an executed order.
        
        Args:
            symbol: Traded symbol
            expected_price: Price expected when order was placed (mid/limit)
            actual_price: Actual fill price
            qty: Quantity traded
            side: 'buy' or 'sell'
            order_type: 'market' or 'limit'
        """
        try:
            # Fixed: method is record_fill, not record_slippage
            self._slippage_tracker.record_fill(
                symbol=symbol,
                expected_price=expected_price,
                fill_price=actual_price,
                qty=qty,
                side=side,
                order_type=order_type
            )
        except Exception as e:
            self._logger.error(f"Slippage tracking failed (non-fatal): {e}")

    def _is_in_cooldown(self, bot_id: str, symbol: str, cooldown_seconds: int = 30) -> bool:
        """Check if bot is in cooldown for this symbol"""
        cooldown_key = f"cooldown_{bot_id}_{symbol}"
        last_trade_time = get_state(cooldown_key)

        if not last_trade_time:
            return False

        import time
        return (time.time() - last_trade_time) < cooldown_seconds


    def run(self, enabled_bots: List[str], equity: float,
            selected_stocks: List[str] = None,
            selected_options: List[str] = None) -> ExecutionResult:
        from ..core.halt import get_halt_manager
        from ..core.clock import get_market_clock
        from .bot_runners import DEDICATED_THREAD_BOTS
        
        # Filter out bots running in dedicated threads (they run at 5s intervals)
        # The main loop should only run momentum bots
        original_count = len(enabled_bots)
        enabled_bots = [b for b in enabled_bots if b not in DEDICATED_THREAD_BOTS]
        skipped_dedicated = original_count - len(enabled_bots)
        
        if skipped_dedicated > 0:
            self._logger.log("execution_dedicated_bots_skipped", {
                "skipped_count": skipped_dedicated,
                "reason": "running_in_dedicated_threads",
                "remaining_bots": enabled_bots
            })
        
        # Check market hours for bot skipping optimization
        clock = get_market_clock()
        skip_stock_bots = clock.should_skip_stock_bots()
        
        # Store selected tickers for bots to use
        if selected_stocks is not None:
            set_state("screener.active_stocks", selected_stocks)
        if selected_options is not None:
            set_state("screener.active_options", selected_options)

        self._logger.log("execution_start", {
            "enabled_bots": enabled_bots, 
            "equity": equity,
            "selected_stocks": selected_stocks or [],
            "selected_options": selected_options or [],
            "skip_stock_bots": skip_stock_bots,
            "is_weekend": clock.is_weekend(),
            "is_market_hours": clock.is_market_hours()
        })

        prestaged_triggered = self._check_prestaged_entries(clock)

        bots_run = []
        errors = []
        trades_attempted = 0
        positions_managed = 0
        all_signals = []
        bots_outside_hours = []

        halt_manager = get_halt_manager()
        is_halted = halt_manager.is_halted()

        if is_halted:
            self._logger.log("execution_halted", {
                "reason": "GLOBAL_TRADING_HALT active",
                "halt_status": halt_manager.get_status().reason,
                "action": "manage_positions_only"
            })

        for bot_id in enabled_bots:
            try:
                bot_state = get_state(f"bots.{bot_id}", {})
                is_enabled = bot_state.get("enabled", False) if isinstance(bot_state, dict) else False
                is_allowed = bot_state.get("allowed", False) if isinstance(bot_state, dict) else False
                budget_state = get_state(f"budgets.{bot_id}", {})
                max_daily_loss = budget_state.get("max_daily_loss", 0) if isinstance(budget_state, dict) else 0

                if not is_enabled or not is_allowed:
                    self._logger.log("execution_bot_skipped", {
                        "bot_id": bot_id,
                        "reason": "not_enabled_or_allowed"
                    })
                    continue

                if not valid_budget(max_daily_loss):
                    self._logger.log("execution_bot_skipped", {
                        "bot_id": bot_id,
                        "reason": "invalid_budget",
                        "max_daily_loss": max_daily_loss
                    })
                    continue

                self._logger.log("execution_bot_preflight_ok", {
                    "bot_id": bot_id,
                    "max_daily_loss": round(max_daily_loss, 2),
                    "halted": is_halted
                })

                # Execute bot-specific trading logic (always manage positions, new trades only if not halted)
                if bot_id == "crypto_core":
                    # Crypto runs 24/7
                    result = self._execute_crypto_bot(bot_id, max_daily_loss, halt_new_trades=is_halted)
                    trades_attempted += result.get("trades_attempted", 0)
                    positions_managed += result.get("positions_managed", 0)
                    all_signals.extend(result.get("signals", []))
                elif bot_id.startswith("mom_") or bot_id.startswith("session_"):
                    # Skip momentum/session bots on weekends/outside hours to save API calls
                    if skip_stock_bots:
                        self._logger.log("execution_bot_skipped", {
                            "bot_id": bot_id,
                            "reason": "market_closed_or_weekend"
                        })
                        bots_outside_hours.append(bot_id)
                        continue
                    # session_ bots are dynamically selected tickers from SessionSelectorBot
                    # They use the same momentum execution logic, extracting ticker from bot_id
                    effective_bot_id = bot_id
                    if bot_id.startswith("session_"):
                        effective_bot_id = bot_id  # _execute_momentum_bot handles both prefixes
                    result = self._execute_momentum_bot(effective_bot_id, max_daily_loss, halt_new_trades=is_halted)
                    trades_attempted += result.get("trades_attempted", 0)
                    positions_managed += result.get("positions_managed", 0)
                    all_signals.extend(result.get("signals", []))
                elif bot_id == "opt_core" or bot_id == "opt_0dte":
                    # Skip options bots on weekends/outside hours to save API calls
                    if skip_stock_bots:
                        self._logger.log("execution_bot_skipped", {
                            "bot_id": bot_id,
                            "reason": "market_closed_or_weekend"
                        })
                        bots_outside_hours.append(bot_id)
                        continue
                    result = self._execute_options_bot(bot_id, max_daily_loss, halt_new_trades=is_halted)
                    trades_attempted += result.get("trades_attempted", 0)
                    positions_managed += result.get("positions_managed", 0)
                    # Track if options bot is outside trading hours
                    if result.get("outside_hours", False):
                        bots_outside_hours.append(bot_id)
                    all_signals.extend(result.get("signals", []))
                elif bot_id == "twentymin_core":
                    # 20-Minute Trader - session times configurable in bots.yaml (pre-session, in-session, post-session)
                    if skip_stock_bots:
                        self._logger.log("execution_bot_skipped", {
                            "bot_id": bot_id,
                            "reason": "market_closed_or_weekend"
                        })
                        bots_outside_hours.append(bot_id)
                        continue
                    result = self._execute_twentymin_bot(bot_id, max_daily_loss, halt_new_trades=is_halted)
                    trades_attempted += result.get("trades_attempted", 0)
                    positions_managed += result.get("positions_managed", 0)
                    # TwentyMinuteBot properly returns outside_hours based on its 6:00-7:00 window
                    if result.get("outside_hours", False):
                        bots_outside_hours.append(bot_id)
                    all_signals.extend(result.get("signals", []))
                elif bot_id == "bounce_core":
                    # BounceBot - Overnight crypto dip-buying (1-5:30am PST)
                    result = self._execute_bounce_bot(bot_id, max_daily_loss, halt_new_trades=is_halted)
                    trades_attempted += result.get("trades_attempted", 0)
                    positions_managed += result.get("positions_managed", 0)
                    if result.get("outside_hours", False):
                        bots_outside_hours.append(bot_id)
                    all_signals.extend(result.get("signals", []))
                elif bot_id == "hm_core":
                    # HailMary runs in dedicated thread — skip in main loop
                    self._logger.log("execution_bot_skipped", {
                        "bot_id": bot_id,
                        "reason": "runs_in_dedicated_thread"
                    })
                    continue

                bots_run.append(bot_id)

            except Exception as e:
                self._logger.error(f"Bot {bot_id} execution error: {e}")
                errors.append(f"{bot_id}: {e}")

        self._health.record_price_tick()

        self._logger.log("execution_complete", {
            "bots_run": bots_run,
            "trades_attempted": trades_attempted,
            "positions_managed": positions_managed,
            "errors": errors
        })

        return ExecutionResult(
            bots_run=bots_run,
            trades_attempted=trades_attempted,
            positions_managed=positions_managed,
            errors=errors,
            signals=all_signals,
            bots_outside_hours=bots_outside_hours
        )

    def _execute_crypto_bot(self, bot_id: str, max_daily_loss: float, halt_new_trades: bool = False) -> Dict[str, Any]:
        """Execute crypto trading logic using dynamic universe selection"""
        from ..services.alpaca_client import get_alpaca_client
        from ..services.crypto_universe import get_crypto_universe
        from ..core.config import load_bots_config

        alpaca = get_alpaca_client()
        
        # Get pairs from dynamic universe or fall back to static list
        bots_config = load_bots_config()
        crypto_config = bots_config.get("cryptobot", {})
        universe_config = crypto_config.get("universe", {})
        
        if universe_config.get("enabled", False):
            universe = get_crypto_universe()
            max_coins = universe_config.get("max_coins", 5)
            pairs = universe.get_top_coins(n=max_coins)
            self._logger.log("crypto_universe_selected", {
                "pairs": pairs,
                "count": len(pairs)
            })
        else:
            pairs = crypto_config.get("pairs", ["BTC/USD", "ETH/USD"])
        trades_attempted = 0
        positions_managed = 0
        signals = []  # Collect signals for human-readable display

        # Get current positions to avoid over-trading
        current_positions = alpaca.get_positions()
        # Detect ALL crypto positions - they end with "USD" and don't have "/" in symbol
        all_crypto_positions = [
            p for p in current_positions 
            if str(p.symbol).endswith("USD") and "/" not in str(p.symbol)
        ]
        # Build set of all crypto symbols for duplicate prevention
        all_crypto_symbols = {str(p.symbol) for p in all_crypto_positions}
        
        # Filter to positions in current trading pairs
        crypto_positions = [p for p in all_crypto_positions if any(pair.replace("/", "") in str(p.symbol) for pair in pairs)]
        positions_managed = len(crypto_positions)

        self._logger.log("crypto_bot_start", {
            "pairs": pairs,
            "max_daily_loss": round(max_daily_loss, 2),
            "existing_positions": positions_managed,
            "total_crypto_positions": len(all_crypto_positions),
            "all_crypto_symbols": list(all_crypto_symbols)
        })

        # Use all crypto positions for risk management
        total_crypto_value = sum(abs(float(p.market_value)) for p in all_crypto_positions)

        # Manage existing positions first (stop losses, take profits)
        for position in crypto_positions:
            try:
                self._manage_crypto_position(position, bot_id)
                positions_managed += 1
            except Exception as e:
                self._logger.error(f"Position management error for {position.symbol}: {e}")

        # Look for new entries if under position limit and not halted
        # Use total crypto positions count, not just current pairs
        # SAFETY: Read max_concurrent_positions from config instead of hardcoding
        max_positions = crypto_config.get("risk", {}).get("max_concurrent_positions", 5)
        if len(all_crypto_positions) < max_positions and not halt_new_trades:
            for pair in pairs:
                if trades_attempted >= 1:  # Max 1 new trade per cycle to prevent rapid orders
                    break

                try:
                    # Check if we already have this position - use ALL crypto symbols
                    symbol_clean = pair.replace("/", "")
                    has_position = symbol_clean in all_crypto_symbols

                    if has_position:
                        self._logger.log("crypto_skip_existing_position", {
                            "symbol": pair,
                            "symbol_clean": symbol_clean,
                            "all_crypto_symbols": list(all_crypto_symbols),
                            "reason": "already_have_position"
                        })
                        continue

                    # Get current market data
                    try:
                        quote = alpaca.get_latest_quote(pair, asset_class="crypto")
                        bid = quote["bid"]
                        ask = quote["ask"]
                        mid_price = (bid + ask) / 2
                        
                        # EXECUTION-TIME SPREAD GATE: Reject if spread too wide
                        max_spread_pct = crypto_config.get("risk", {}).get("max_spread_pct", 0.5)
                        spread_pct = ((ask - bid) / mid_price) * 100 if mid_price > 0 else 999
                        if spread_pct > max_spread_pct:
                            self._logger.log("crypto_spread_gate_blocked", {
                                "symbol": pair,
                                "spread_pct": round(spread_pct, 3),
                                "max_spread_pct": max_spread_pct,
                                "bid": bid,
                                "ask": ask,
                                "action": "skip_trade"
                            })
                            continue
                    except Exception as e:
                        self._logger.warn(f"Could not get quote for {pair}: {e}")
                        continue

                    # Simple momentum signal (replace with your strategy)
                    signal = self._generate_crypto_signal(pair, mid_price, bot_id)

                    if signal == "buy":
                        # Check cooldown
                        if self._is_in_cooldown(bot_id, pair):
                            self._logger.log("crypto_cooldown_active", {
                                "symbol": pair,
                                "bot_id": bot_id,
                                "action": "skip_trade"
                            })
                            continue

                        # Generate deterministic order ID
                        signal_id = f"buy_{int(time.time())}"
                        client_order_id = generate_client_order_id(bot_id, pair, signal_id)

                        # Check idempotency
                        if is_order_already_submitted(client_order_id):
                            self._logger.log("crypto_order_duplicate", {
                                "symbol": pair,
                                "client_order_id": client_order_id,
                                "action": "skip_duplicate"
                            })
                            continue

                        # Dynamic position sizing based on config
                        # Use equity_pct from crypto config (default 0.5% of account)
                        execution_config = crypto_config.get("execution", {})
                        equity_pct = execution_config.get("equity_pct", 0.5)
                        min_notional = execution_config.get("min_notional_usd", 25.0)
                        max_notional = execution_config.get("max_notional_usd", 500.0)
                        
                        try:
                            account = alpaca.get_account()
                            equity = float(account.equity)
                            dollar_amount = equity * (equity_pct / 100)
                            dollar_amount = max(min_notional, min(max_notional, dollar_amount))
                        except Exception:
                            dollar_amount = execution_config.get("default_notional_usd", 50.0)

                        # Use qty instead of notional for crypto to avoid time_in_force issues
                        # Estimate qty based on current price
                        estimated_qty = dollar_amount / mid_price

                        # Use centralized order placement with PolicyGate validation
                        order_result = self._validate_and_place_order(
                            alpaca=alpaca,
                            symbol=pair,
                            side="buy",
                            qty=round(estimated_qty, 6),
                            bot_id=bot_id,
                            order_type="market",
                            asset_class="crypto",
                            expected_price=mid_price,
                            client_order_id=client_order_id,
                            metadata={"notional": dollar_amount, "signal": "momentum"}
                        )
                        
                        if not order_result["success"]:
                            self._logger.log("crypto_order_blocked", {
                                "symbol": pair,
                                "reason": order_result["blocked_reason"]
                            })
                            continue
                        
                        order_response = order_result["order_response"]

                        # Set cooldown timestamp
                        set_last_trade_timestamp(bot_id, pair, time.time())

                        # Get regime context for decision logging
                        from .market_regime import get_current_regime
                        regime = get_current_regime()
                        
                        self._logger.log("crypto_order_placed", {
                            "symbol": pair,
                            "side": "buy",
                            "notional": dollar_amount,
                            "qty": round(estimated_qty, 6),
                            "order_id": order_response.get("id"),
                            "status": order_response.get("status"),
                            "paper_trading": alpaca.is_paper,
                            "mid_price": mid_price,
                            "regime_vix": round(regime.vix, 2) if regime else None,
                            "regime_label": regime.volatility_regime.value if regime else "unknown",
                            "policy_gate_validated": True
                        })
                        
                        # Record expected price at order placement for slippage baseline
                        # NOTE: This records the expected mid_price. Actual fill price comes
                        # from order fill events which are processed separately. The slippage
                        # tracker compares this expected price against fills when they arrive.
                        self._track_slippage(
                            symbol=pair,
                            expected_price=mid_price,
                            actual_price=mid_price,  # Placeholder - updated on fill
                            qty=round(estimated_qty, 6),
                            side="buy",
                            order_type="market"
                        )
                        
                        # Add BUY signal for human-readable display
                        signals.append(TickerSignal(
                            symbol=pair,
                            price=mid_price,
                            signal="BUY",
                            reason=f"Momentum confirmed - entering long ${dollar_amount:.0f}",
                            asset_type="crypto"
                        ))

                        trades_attempted += 1

                        # Update state tracking
                        trade_key = f"trades.{bot_id}.{int(time.time())}"
                        set_state(trade_key, {
                            "symbol": pair,
                            "side": "buy",
                            "notional": dollar_amount,
                            "qty": round(estimated_qty, 6),
                            "timestamp": time.time(),
                            "order_id": order_response.get("id"),
                            "entry_price": mid_price
                        })

                    elif signal == "short":
                        # Check cooldown for shorts too
                        if self._is_in_cooldown(bot_id, pair):
                            self._logger.log("crypto_cooldown_active", {
                                "symbol": pair,
                                "bot_id": bot_id,
                                "action": "skip_short"
                            })
                            continue

                        # Generate deterministic order ID for short
                        signal_id = f"short_{int(time.time())}"
                        client_order_id = generate_client_order_id(bot_id, pair, signal_id)

                        # Check idempotency
                        if is_order_already_submitted(client_order_id):
                            continue

                        # Dynamic position sizing for shorts (same as longs)
                        execution_config = crypto_config.get("execution", {})
                        equity_pct = execution_config.get("equity_pct", 0.5)
                        min_notional = execution_config.get("min_notional_usd", 25.0)
                        max_notional = execution_config.get("max_notional_usd", 500.0)
                        
                        try:
                            account = alpaca.get_account()
                            equity = float(account.equity)
                            dollar_amount = equity * (equity_pct / 100)
                            dollar_amount = max(min_notional, min(max_notional, dollar_amount))
                        except Exception:
                            dollar_amount = execution_config.get("default_notional_usd", 50.0)
                        
                        estimated_qty = dollar_amount / mid_price

                        # Use centralized order placement with PolicyGate validation
                        order_result = self._validate_and_place_order(
                            alpaca=alpaca,
                            symbol=pair,
                            side="sell",
                            qty=round(estimated_qty, 6),
                            bot_id=bot_id,
                            order_type="market",
                            asset_class="crypto",
                            expected_price=mid_price,
                            client_order_id=client_order_id,
                            metadata={"notional": dollar_amount, "signal": "short_momentum"}
                        )
                        
                        if not order_result["success"]:
                            self._logger.log("crypto_order_blocked", {
                                "symbol": pair,
                                "reason": order_result["blocked_reason"]
                            })
                            continue
                        
                        order_response = order_result["order_response"]
                        set_last_trade_timestamp(bot_id, pair, time.time())

                        # Get regime context for decision logging
                        from .market_regime import get_current_regime
                        regime = get_current_regime()
                        
                        self._logger.log("crypto_order_placed", {
                            "symbol": pair,
                            "side": "short",
                            "notional": dollar_amount,
                            "qty": round(estimated_qty, 6),
                            "order_id": order_response.get("id"),
                            "status": order_response.get("status"),
                            "paper_trading": alpaca.is_paper,
                            "mid_price": mid_price,
                            "regime_vix": round(regime.vix, 2) if regime else None,
                            "regime_label": regime.volatility_regime.value if regime else "unknown",
                            "policy_gate_validated": True
                        })
                        
                        # Record expected price at order placement for slippage baseline
                        # NOTE: Actual fill price tracked separately when order fills arrive
                        self._track_slippage(
                            symbol=pair,
                            expected_price=mid_price,
                            actual_price=mid_price,  # Placeholder - updated on fill
                            qty=round(estimated_qty, 6),
                            side="sell",
                            order_type="market"
                        )
                        
                        signals.append(TickerSignal(
                            symbol=pair,
                            price=mid_price,
                            signal="SHORT",
                            reason=f"Bearish momentum - entering short ${dollar_amount:.0f}",
                            asset_type="crypto"
                        ))

                        trades_attempted += 1

                        trade_key = f"trades.{bot_id}.{int(time.time())}"
                        set_state(trade_key, {
                            "symbol": pair,
                            "side": "short",
                            "notional": dollar_amount,
                            "qty": round(estimated_qty, 6),
                            "timestamp": time.time(),
                            "order_id": order_response.get("id"),
                            "entry_price": mid_price
                        })

                    else:
                        self._logger.log("crypto_signal_hold", {
                            "symbol": pair,
                            "signal": signal,
                            "action": "no_trade",
                            "mid_price": mid_price
                        })
                        # Add signal for human-readable display
                        signals.append(TickerSignal(
                            symbol=pair,
                            price=mid_price,
                            signal="HOLD",
                            reason="5-period SMA flat, waiting for momentum",
                            asset_type="crypto"
                        ))
                    
                    # Update decision tracker for dashboard visibility
                    try:
                        tracker = get_decision_tracker()
                        if signal == "buy":
                            action = "buy"
                            reason = "SMA bullish, entering long"
                            strength = 0.5
                        elif signal == "short":
                            action = "short"
                            reason = "SMA bearish, entering short"
                            strength = 0.5
                        else:
                            action = "hold"
                            reason = "Waiting for momentum"
                            strength = 0.0
                        tracker.update_signal(
                            bot_id=bot_id,
                            bot_type="crypto",
                            symbol=pair,
                            signal=action,
                            strength=strength,
                            reason=reason
                        )
                    except Exception as track_err:
                        self._logger.error(f"Decision tracker update failed: {track_err}")

                except Exception as e:
                    self._logger.error(f"Crypto trading error for {pair}: {e}")
        elif halt_new_trades:
            self._logger.log("crypto_new_trades_halted", {
                "pairs": pairs,
                "reason": "GLOBAL_TRADING_HALT active",
                "action": "manage_positions_only"
            })

        self._logger.log("crypto_bot_complete", {
            "trades_attempted": trades_attempted,
            "positions_managed": positions_managed,
            "pairs_analyzed": len(pairs),
            "max_daily_loss": round(max_daily_loss, 2),
            "paper_trading": alpaca.is_paper
        })

        return {
            "trades_attempted": trades_attempted, 
            "positions_managed": positions_managed,
            "signals": signals
        }

    def _execute_momentum_bot(self, bot_id: str, max_daily_loss: float, halt_new_trades: bool = False) -> Dict[str, Any]:
        """Execute momentum trading logic for stocks like AAPL"""
        from ..services.alpaca_client import get_alpaca_client

        alpaca = get_alpaca_client()
        trades_attempted = 0
        positions_managed = 0
        signals = []  # Collect signals for human-readable display

        # Get ticker from bot_id (e.g., mom_AAPL -> AAPL, session_AAPL -> AAPL)
        if bot_id.startswith("session_"):
            ticker = bot_id.replace("session_", "")
        else:
            ticker = bot_id.replace("mom_", "")

        # Get current positions
        current_positions = alpaca.get_positions()
        stock_positions = [p for p in current_positions if str(p.symbol) == ticker]
        positions_managed = len(stock_positions)

        self._logger.log("momentum_bot_start", {
            "ticker": ticker,
            "max_daily_loss": round(max_daily_loss, 2),
            "existing_positions": positions_managed
        })

        # Check existing positions for risk management
        existing_positions = [p for p in current_positions if ticker in p.symbol]
        total_position_value = sum(float(p.market_value) for p in existing_positions)

        # Manage existing positions
        for position in stock_positions:
            try:
                self._manage_stock_position(position, bot_id, max_daily_loss)
                positions_managed += 1
            except Exception as e:
                self._logger.error(f"Position management error for {position.symbol}: {e}")

        # Look for new entries if no position exists and not halted
        # Load bot config to get max_concurrent_positions
        bots_config = load_bots_config()
        if bot_id.startswith("session_"):
            # Session bots use session_selector config
            session_cfg = bots_config.get("session_selector", {})
            bot_cfg = {
                "bot_id": bot_id,
                "risk": session_cfg.get("risk", {}),
                "turtle": session_cfg.get("turtle", {}),
                "exits": session_cfg.get("exits", {}),
                "session": session_cfg.get("session", {}),
            }
        else:
            mom_bots = bots_config.get("momentum_bots", [])
            bot_cfg = next((b for b in mom_bots if b.get("bot_id") == bot_id), {})
        max_positions = bot_cfg.get("risk", {}).get("max_concurrent_positions", 1)
        
        # Load global settings for spread gate
        from ..core.config import load_settings
        settings = load_settings()
        max_spread_pct = settings.get("smart_execution", {}).get("max_spread_pct", 0.5)
        
        if len(stock_positions) < max_positions and not halt_new_trades:
            try:
                # Get current market data
                quote = alpaca.get_latest_quote(ticker, asset_class="stock")
                bid = quote["bid"]
                ask = quote["ask"]
                mid_price = (bid + ask) / 2
                
                # EXECUTION-TIME SPREAD GATE: Reject if spread too wide
                spread_pct = ((ask - bid) / mid_price) * 100 if mid_price > 0 else 999
                if spread_pct > max_spread_pct:
                    self._logger.log("momentum_spread_gate_blocked", {
                        "ticker": ticker,
                        "spread_pct": round(spread_pct, 3),
                        "max_spread_pct": max_spread_pct,
                        "bid": bid,
                        "ask": ask,
                        "action": "skip_trade"
                    })
                    return {"trades_attempted": trades_attempted, "positions_managed": positions_managed, "signals": signals}

                # Generate momentum signal
                signal = self._generate_momentum_signal(ticker, mid_price, bot_id)

                if signal in ["buy", "sell"]:
                    # Check cooldown
                    if self._is_in_cooldown(bot_id, ticker):
                        self._logger.log("momentum_cooldown_active", {
                            "ticker": ticker,
                            "bot_id": bot_id,
                            "action": "skip_trade"
                        })
                        return {"trades_attempted": trades_attempted, "positions_managed": positions_managed, "signals": signals}

                    # Generate deterministic order ID
                    signal_id = f"{signal}_{int(time.time())}"
                    client_order_id = generate_client_order_id(bot_id, ticker, signal_id)

                    # Check idempotency
                    if is_order_already_submitted(client_order_id):
                        self._logger.log("momentum_order_duplicate", {
                            "ticker": ticker,
                            "client_order_id": client_order_id,
                            "action": "skip_duplicate"
                        })
                        return {"trades_attempted": trades_attempted, "positions_managed": positions_managed, "signals": signals}

                    # Position sizing: 75% of allocated budget, capped by config max_notional
                    # max_daily_loss comes from PortfolioBot budget allocation per ticker
                    mom_cfg = load_bots_config().get("momentumbot", {})
                    max_notional = mom_cfg.get("position_sizing", {}).get("max_notional_usd", 5000)
                    default_notional = mom_cfg.get("position_sizing", {}).get("default_notional_usd", 500)
                    min_order = mom_cfg.get("risk", {}).get("min_order_size", 10.0)
                    dollar_amount = max(min_order, min(max_notional, max_daily_loss * 0.75))
                    estimated_qty = dollar_amount / mid_price if mid_price > 0 else 0

                    # Use centralized order placement with PolicyGate validation
                    order_result = self._validate_and_place_order(
                        alpaca=alpaca,
                        symbol=ticker,
                        side=signal,
                        qty=estimated_qty,
                        bot_id=bot_id,
                        order_type="market",
                        asset_class="equity",
                        expected_price=mid_price,
                        client_order_id=client_order_id,
                        metadata={"notional": dollar_amount, "signal": "momentum"}
                    )
                    
                    if not order_result["success"]:
                        self._logger.log("momentum_order_blocked", {
                            "ticker": ticker,
                            "reason": order_result["blocked_reason"]
                        })
                        return {"trades_attempted": trades_attempted, "positions_managed": positions_managed, "signals": signals}
                    
                    order_response = order_result["order_response"]

                    # Set cooldown timestamp
                    set_last_trade_timestamp(bot_id, ticker, time.time())

                    # Get regime context for decision logging
                    from .market_regime import get_current_regime
                    regime = get_current_regime()
                    
                    self._logger.log("momentum_order_placed", {
                        "ticker": ticker,
                        "side": signal,
                        "notional": dollar_amount,
                        "order_id": order_response.get("id"),
                        "status": order_response.get("status"),
                        "paper_trading": alpaca.is_paper,
                        "mid_price": mid_price,
                        "regime_vix": round(regime.vix, 2) if regime else None,
                        "regime_label": regime.volatility_regime.value if regime else "unknown",
                        "policy_gate_validated": True
                    })
                    
                    # Record expected price at order placement for slippage baseline
                    # NOTE: Actual fill price tracked separately when order fills arrive
                    self._track_slippage(
                        symbol=ticker,
                        expected_price=mid_price,
                        actual_price=mid_price,  # Placeholder - updated on fill
                        qty=dollar_amount / mid_price if mid_price > 0 else 0,
                        side=signal,
                        order_type="market"
                    )
                    
                    # Add BUY/SELL signal for human-readable display
                    signal_type = "BUY" if signal == "buy" else "SHORT"
                    signals.append(TickerSignal(
                        symbol=ticker,
                        price=mid_price,
                        signal=signal_type,
                        reason=f"Momentum confirmed - entering {signal_type.lower()} ${dollar_amount:.0f}",
                        asset_type="stock"
                    ))

                    trades_attempted += 1

                    # Track trade
                    trade_key = f"trades.{bot_id}.{int(time.time())}"
                    set_state(trade_key, {
                        "symbol": ticker,
                        "side": signal,
                        "notional": dollar_amount,
                        "timestamp": time.time(),
                        "order_id": order_response.get("id"),
                        "entry_price": mid_price
                    })

                else:
                    # Enhanced hold signal logging with regime context
                    from .market_regime import get_current_regime
                    regime = get_current_regime()
                    self._logger.log("momentum_signal_hold", {
                        "ticker": ticker,
                        "signal": signal,
                        "action": "no_trade",
                        "mid_price": mid_price,
                        "regime_vix": round(regime.vix, 2) if regime else None,
                        "regime_label": regime.volatility_regime.value if regime else "unknown",
                        "reason": "Awaiting momentum confirmation"
                    })
                    # Add signal for human-readable display
                    signals.append(TickerSignal(
                        symbol=ticker,
                        price=mid_price,
                        signal="HOLD",
                        reason="Waiting for momentum confirmation (consecutive moves)",
                        asset_type="stock"
                    ))
                
                # Update decision tracker for dashboard visibility
                try:
                    tracker = get_decision_tracker()
                    action = signal if signal in ("buy", "short") else "hold"
                    reason = "Momentum confirmed" if action != "hold" else "Waiting for momentum"
                    tracker.update_signal(
                        bot_id=bot_id,
                        bot_type="momentum",
                        symbol=ticker,
                        signal=action,
                        strength=0.5 if action != "hold" else 0.0,
                        reason=reason
                    )
                except Exception as track_err:
                    self._logger.error(f"Decision tracker update failed: {track_err}")

            except Exception as e:
                self._logger.error(f"Momentum trading error for {ticker}: {e}")
        elif halt_new_trades:
            self._logger.log("momentum_new_trades_halted", {
                "ticker": ticker,
                "reason": "GLOBAL_TRADING_HALT active",
                "action": "manage_positions_only"
            })

        self._logger.log("momentum_bot_complete", {
            "ticker": ticker,
            "trades_attempted": trades_attempted,
            "positions_managed": positions_managed,
            "max_daily_loss": round(max_daily_loss, 2),
            "paper_trading": alpaca.is_paper
        })

        return {
            "trades_attempted": trades_attempted, 
            "positions_managed": positions_managed,
            "signals": signals
        }

    def _execute_options_bot(self, bot_id: str, max_daily_loss: float, halt_new_trades: bool = False) -> Dict[str, Any]:
        """Execute enhanced options trading with multiple strategies"""
        from ..bots.options_bot import OptionsBot

        try:
            # Use the enhanced options bot
            options_bot = OptionsBot(bot_id)
            
            # Prepare session: loads pre-market intelligence and updates dynamic universe
            # This runs early in market hours and selects tickers based on gap/IV/volume analysis
            prep_result = options_bot.prepare_session()
            if prep_result.get("intel_loaded"):
                self._logger.log("options_bot_session_prepared", {
                    "bot_id": bot_id,
                    "universe_size": prep_result.get("universe_size", 0),
                    "top_opportunities": prep_result.get("top_opportunities", [])[:3],
                    "regime": prep_result.get("regime", "normal")
                })
            
            result = options_bot.execute(max_daily_loss, halt_new_trades=halt_new_trades)

            self._logger.log("enhanced_options_bot_execution_complete", {
                "bot_id": bot_id,
                "trades_attempted": result.get("trades_attempted", 0),
                "positions_managed": result.get("positions_managed", 0),
                "strategies_analyzed": result.get("strategies_analyzed", 0),
                "errors": result.get("errors", []),
                "max_daily_loss": round(max_daily_loss, 2)
            })
            
            # Update decision tracker for options bot
            try:
                tracker = get_decision_tracker()
                strategies = result.get("strategies_analyzed", 0)
                outside_hours = result.get("outside_hours", False)
                
                if outside_hours:
                    tracker.update_blocker(
                        bot_id=bot_id,
                        bot_type="options",
                        blocker_type="outside_session",
                        description="Outside trading hours"
                    )
                    tracker.update_signal(
                        bot_id=bot_id,
                        bot_type="options",
                        symbol="OPTIONS",
                        signal="wait",
                        strength=0.0,
                        reason="Outside trading session"
                    )
                else:
                    tracker.clear_blocker(bot_id, "outside_session")
                    signal = "hold" if result.get("trades_attempted", 0) == 0 else "active"
                    reason = f"Analyzed {strategies} strategies" if strategies > 0 else "No strategies matched"
                    tracker.update_signal(
                        bot_id=bot_id,
                        bot_type="options",
                        symbol="OPTIONS",
                        signal=signal,
                        strength=0.3 if strategies > 0 else 0.0,
                        reason=reason
                    )
            except Exception as track_err:
                self._logger.error(f"Decision tracker update failed for options: {track_err}")

            return {
                "trades_attempted": result.get("trades_attempted", 0),
                "positions_managed": result.get("positions_managed", 0),
                "outside_hours": result.get("outside_hours", False),
                "signals": result.get("signals", [])
            }

        except Exception as e:
            self._logger.error(f"Enhanced options bot execution failed: {e}")
            return {"trades_attempted": 0, "positions_managed": 0, "signals": []}

    def _generate_crypto_signal(self, pair: str, price: float, bot_id: str) -> str:
        """Generate trading signal for crypto pair using momentum strategy with shorts"""
        from ..core.config import load_bots_config

        try:
            # Get price history for momentum calculation
            price_history_key = f"price_history.{pair.replace('/', '')}"
            price_history = get_state(price_history_key, [])

            # Add current price
            price_history.append({
                "price": price,
                "timestamp": time.time()
            })

            # Keep last 50 prices for indicator calculations
            if len(price_history) > 50:
                price_history = price_history[-50:]

            set_state(price_history_key, price_history)

            # Require at least 20 data points for signal (need SMA5 and SMA20)
            if len(price_history) < 20:
                return "hold"

            # Calculate SMAs
            prices = [p["price"] for p in price_history]
            sma5 = sum(prices[-5:]) / 5
            sma20 = sum(prices[-20:]) / 20

            # Bullish: price > SMA5 > SMA20 (uptrend)
            if price > sma5 and sma5 > sma20:
                return "buy"
            # Bearish: price < SMA5 < SMA20 (downtrend)
            elif price < sma5 and sma5 < sma20:
                return "short"
            else:
                return "hold"

        except Exception as e:
            self._logger.error(f"Crypto signal generation error: {e}")
            return "hold"

    def _generate_momentum_signal(self, ticker: str, price: float, bot_id: str) -> str:
        """Generate momentum signal for stock with time window enforcement"""
        from ..core.config import load_bots_config
        from ..core.clock import get_market_clock

        try:
            # Get bot config for time windows
            bots_config = load_bots_config()
            momentum_bots = bots_config.get("momentum_bots", [])

            bot_config = None
            for bot in momentum_bots:
                if bot.get("bot_id") == bot_id:
                    bot_config = bot
                    break

            if not bot_config:
                return "hold"

            # Check trading time window
            session = bot_config.get("session", {})
            trade_start = session.get("trade_start", "06:35")
            trade_end = session.get("trade_end", "09:30")

            current_time = get_market_clock().now().time()
            start_time = datetime.strptime(trade_start, "%H:%M").time()
            end_time = datetime.strptime(trade_end, "%H:%M").time()

            # Only trade within configured window
            if not (start_time <= current_time <= end_time):
                return "hold"

            # Get price history
            price_history_key = f"momentum_history.{ticker}"
            price_history = get_state(price_history_key, [])

            # Add current price
            price_history.append({
                "price": price,
                "timestamp": time.time()
            })

            # Keep last 10 prices
            if len(price_history) > 10:
                price_history = price_history[-10:]

            set_state(price_history_key, price_history)

            # Require at least 3 data points
            if len(price_history) < 3:
                return "hold"

            # Simple trend detection
            prices = [p["price"] for p in price_history[-3:]]

            # Uptrend: each price higher than previous
            if prices[-1] > prices[-2] > prices[-3]:
                avg_price = sum(prices) / len(prices)
                # Additional confirmation: current price > 1.002 * average
                if price > avg_price * 1.002:
                    return "buy"

            # Downtrend: each price lower than previous
            elif prices[-1] < prices[-2] < prices[-3]:
                avg_price = sum(prices) / len(prices)
                # Additional confirmation: current price < 0.998 * average
                if price < avg_price * 0.998:
                    return "sell"

            return "hold"

        except Exception as e:
            self._logger.error(f"Momentum signal generation error: {e}")
            return "hold"

    def _generate_options_signal(self, ticker: str, price: float, bot_id: str) -> str:
        """
        Generate options signal based on price trend and volatility analysis.
        Uses momentum detection similar to stock signals but adapted for options.
        
        Strategy:
        - Bullish trend (3 higher prices + above MA) -> buy_call or bull_put_spread
        - Bearish trend (3 lower prices + below MA) -> buy_put or bear_call_spread
        - No clear trend -> hold
        
        Args:
            ticker: Underlying symbol (e.g., SPY, QQQ)
            price: Current price of the underlying
            bot_id: Bot identifier for state tracking
            
        Returns:
            Signal string: "buy_call", "buy_put", "bull_put_spread", "bear_call_spread", or "hold"
        """
        try:
            # STEP 1: Get historical price data from state
            price_history_key = f"options_prices:{bot_id}:{ticker}"
            price_history = get_state(price_history_key, [])
            
            # STEP 2: Add current price to history
            from ..core.clock import get_market_clock
            price_history.append({
                "price": price,
                "timestamp": get_market_clock().now().isoformat()
            })
            
            # Keep last 10 prices for analysis
            if len(price_history) > 10:
                price_history = price_history[-10:]
            
            # Save updated history
            set_state(price_history_key, price_history)
            
            # STEP 3: Need minimum 5 prices for signal generation
            if len(price_history) < 5:
                return "hold"
            
            # STEP 4: Extract recent prices for trend analysis
            prices = [p["price"] for p in price_history[-5:]]
            avg_price = sum(prices) / len(prices)
            
            # Calculate simple volatility (standard deviation proxy)
            price_range = max(prices) - min(prices)
            volatility_pct = (price_range / avg_price) * 100
            
            # STEP 5: Trend detection - check last 3 prices
            recent_prices = prices[-3:]
            
            # Bullish: 3 consecutive higher prices and price above average
            if recent_prices[-1] > recent_prices[-2] > recent_prices[-3]:
                if price > avg_price * 1.001:  # 0.1% above average
                    # Low volatility -> credit spread (bull put spread)
                    # High volatility -> directional (buy call)
                    if volatility_pct < 0.5:
                        return "bull_put_spread"
                    else:
                        return "buy_call"
            
            # Bearish: 3 consecutive lower prices and price below average
            elif recent_prices[-1] < recent_prices[-2] < recent_prices[-3]:
                if price < avg_price * 0.999:  # 0.1% below average
                    # Low volatility -> credit spread (bear call spread)
                    # High volatility -> directional (buy put)
                    if volatility_pct < 0.5:
                        return "bear_call_spread"
                    else:
                        return "buy_put"
            
            # STEP 6: No clear trend - hold
            return "hold"
            
        except Exception as e:
            self._logger.error(f"Options signal generation error for {ticker}: {e}")
            return "hold"  # Fail-safe: hold on error

    def _manage_crypto_position(self, position, bot_id: str):
        """Manage existing crypto position with stops and targets"""
        from ..services.alpaca_client import get_alpaca_client
        from ..bots.crypto_bot import normalize_crypto_symbol

        alpaca = get_alpaca_client()

        # Get current quote for position
        # Normalize symbol: Alpaca returns "BTCUSD" but API needs "BTC/USD"
        try:
            normalized_symbol = normalize_crypto_symbol(position.symbol)
            quote = alpaca.get_latest_quote(normalized_symbol, asset_class="crypto")
            current_price = (quote["bid"] + quote["ask"]) / 2
        except:
            return  # Skip if can't get quote

        # Calculate P&L
        unrealized_pnl_pct = (position.unrealized_pl / abs(position.market_value)) * 100

        # Exit conditions - options have tighter stops
        if unrealized_pnl_pct <= -25:  # Stop loss at -25% for options
            self._close_position(position, "stop_loss", alpaca)
        elif unrealized_pnl_pct >= 50:  # Take profit at +50% for options
            self._close_position(position, "take_profit", alpaca)

        # Time-based exit (4 hours from config: time_stop_minutes: 240)
        # This would require tracking entry time from state

    def _manage_stock_position(self, position, bot_id: str, max_daily_loss: float):
        """Manage existing stock position"""
        from ..services.alpaca_client import get_alpaca_client

        alpaca = get_alpaca_client()

        # Similar position management logic for stocks
        unrealized_pnl_pct = (position.unrealized_pl / abs(position.market_value)) * 100

        # Exit conditions - stocks have wider stops
        if unrealized_pnl_pct <= -2:  # Stop loss at -2% for stocks
            self._close_position(position, "stop_loss", alpaca)
        elif unrealized_pnl_pct >= 5:  # Take profit at +5% for stocks
            self._close_position(position, "take_profit", alpaca)

    def generate_exit_order_id(self, bot_id: str, asset_class: str, symbol: str, position_id: str) -> str:
        """Generate deterministic client_order_id for exit orders"""
        from datetime import datetime
        day_key = datetime.utcnow().strftime("%Y%m%d")
        return f"EXIT:{bot_id}:{asset_class}:{symbol}:{position_id}:{day_key}"

    def submit_exit_order(self, bot_id: str, position_id: str, symbol: str,
                         asset_class: str, side: str, qty: float,
                         order_type: str = "market", current_price: float = 0.0) -> Dict[str, Any]:
        """Submit exit order with idempotency and exit lock protection"""

        result = {"success": False, "error": None, "order_id": None, "skipped": False}
        trailing_manager = None  # Initialize to avoid unbound variable in exception handler

        try:
            trailing_manager = get_trailing_stop_manager()

            # Check if exit already in progress
            if trailing_manager.has_exit_lock(bot_id, position_id, symbol, asset_class):
                result["skipped"] = True
                result["error"] = "Exit order already active for position"
                self._logger.log("exit_order_skipped", {
                    "bot_id": bot_id,
                    "symbol": symbol,
                    "position_id": position_id,
                    "reason": "exit_lock_active"
                })
                return result

            # Generate client order ID for idempotency
            client_order_id = self.generate_exit_order_id(bot_id, asset_class, symbol, position_id)

            # Check if order already submitted
            if is_order_already_submitted(client_order_id):
                result["skipped"] = True
                result["error"] = "Exit order already submitted"
                self._logger.log("exit_order_duplicate", {
                    "bot_id": bot_id,
                    "symbol": symbol,
                    "client_order_id": client_order_id
                })
                return result

            # Set exit lock
            trailing_manager.set_exit_lock(bot_id, position_id, symbol, asset_class, client_order_id)

            # Record order submission for idempotency
            record_order_submission(client_order_id, bot_id, symbol, f"exit_{int(time.time())}")

            from ..services.alpaca_client import get_alpaca_client
            alpaca = get_alpaca_client()

            # Submit appropriate order type by asset class
            if asset_class == "crypto":
                order_response = alpaca.place_market_order(
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    client_order_id=client_order_id
                )
            elif asset_class == "option":
                # Options: market or limit with slippage protection
                if order_type == "limit" and current_price > 0:
                    slippage_pct = 0.002  # 0.2% slippage allowance
                    if side == "sell":
                        limit_price = current_price * (1 - slippage_pct)
                    else:
                        limit_price = current_price * (1 + slippage_pct)

                    order_response = alpaca.place_limit_order(
                        symbol=symbol,
                        side=side,
                        qty=qty,
                        limit_price=limit_price,
                        client_order_id=client_order_id
                    )
                else:
                    order_response = alpaca.place_market_order(
                        symbol=symbol,
                        side=side,
                        qty=qty,
                        client_order_id=client_order_id
                    )
            else:  # equity
                order_response = alpaca.place_market_order(
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    client_order_id=client_order_id
                )

            # Update order record with Alpaca ID
            record_order_submission(client_order_id, bot_id, symbol, f"exit_{int(time.time())}",
                                   order_response.get("id"))

            result["success"] = True
            result["order_id"] = order_response.get("id")

            self._logger.log("trailing_exit_submitted", {
                "bot_id": bot_id,
                "symbol": symbol,
                "asset_class": asset_class,
                "position_id": position_id,
                "side": side,
                "qty": qty,
                "order_type": order_type,
                "order_id": order_response.get("id"),
                "client_order_id": client_order_id
            })

        except Exception as e:
            # Clear exit lock on failure (only if trailing_manager was initialized)
            if trailing_manager is not None:
                trailing_manager.clear_exit_lock(bot_id, position_id, symbol, asset_class)
            result["error"] = str(e)
            self._logger.error(f"Exit order submission failed: {e}")

        return result

    def _execute_twentymin_bot(self, bot_id: str, max_daily_loss: float, halt_new_trades: bool = False) -> Dict[str, Any]:
        """
        Execute 20-Minute Trader bot.
        
        This bot focuses on the first 20 minutes after market open,
        exploiting predictable patterns from overnight gap resolution.
        """
        from ..bots.twenty_minute_bot import TwentyMinuteBot
        from ..core.config import load_bots_config
        
        trades_attempted = 0
        positions_managed = 0
        signals = []
        outside_hours = False
        
        try:
            bots_config = load_bots_config()
            twentymin_config = bots_config.get("twentyminute_bot", {})
            
            if not twentymin_config.get("enabled", False):
                self._logger.log("twentymin_bot_disabled", {"bot_id": bot_id})
                return {
                    "trades_attempted": 0,
                    "positions_managed": 0,
                    "signals": [],
                    "outside_hours": True
                }
            
            bot = TwentyMinuteBot()
            result = bot.execute(budget=max_daily_loss, halt_new_trades=halt_new_trades)
            
            trades_attempted = result.get("trades_attempted", 0)
            positions_managed = result.get("positions_managed", 0)
            
            self._logger.log("twentymin_bot_complete", {
                "bot_id": bot_id,
                "trades_attempted": trades_attempted,
                "positions_managed": positions_managed,
                "patterns_detected": result.get("patterns_detected", 0),
                "gaps_analyzed": result.get("gaps_analyzed", 0),
                "errors": result.get("errors", [])
            })
            
        except Exception as e:
            self._logger.error(f"TwentyMinuteBot execution error: {e}")
        
        return {
            "trades_attempted": trades_attempted,
            "positions_managed": positions_managed,
            "signals": signals,
            "outside_hours": outside_hours
        }

    def _close_position(self, position, reason: str, alpaca):
        """Close a position"""
        try:
            if position.side == "long":
                side = "sell"
            else:
                side = "buy"

            # Cancel any pending orders first so shares aren't locked by staged exits
            try:
                open_orders = alpaca.get_open_orders(symbol=position.symbol)
                for order in open_orders:
                    if order.get("id"):
                        alpaca.cancel_order(order["id"])
                        self._logger.log("cancelled_pending_before_close", {
                            "symbol": position.symbol,
                            "order_id": order["id"],
                            "reason": reason
                        })
            except Exception:
                pass

            order_response = alpaca.place_market_order(
                symbol=position.symbol,
                side=side,
                qty=abs(float(position.qty))
            )

            self._logger.log("position_closed", {
                "symbol": position.symbol,
                "reason": reason,
                "side": side,
                "qty": abs(float(position.qty)),
                "unrealized_pl": position.unrealized_pl,
                "order_id": order_response.get("id")
            })

        except Exception as e:
            self._logger.error(f"Failed to close position {position.symbol}: {e}")

    def _check_prestaged_entries(self, clock) -> List[Dict[str, Any]]:
        """
        Check and execute pre-staged entries.
        
        Runs at 6:00 AM to scan for setups, and during 6:30-7:30 to check triggers.
        Returns list of triggered entries.
        """
        from .prestaged_entries import get_prestaged_service
        from zoneinfo import ZoneInfo
        from datetime import datetime
        
        triggered = []
        pst = ZoneInfo("America/Los_Angeles")
        now_pst = datetime.now(pst)
        current_time = now_pst.strftime("%H:%M")
        
        try:
            prestaged = get_prestaged_service()
            
            if "06:00" <= current_time < "06:15":
                new_entries = prestaged.run_premarket_scan()
                if new_entries:
                    self._logger.log("prestaged_scan_ran", {
                        "time": current_time,
                        "new_entries": len(new_entries),
                        "symbols": [e.symbol for e in new_entries]
                    })
            
            if "06:30" <= current_time < "07:30":
                triggered_entries = prestaged.check_triggers()
                for entry in triggered_entries:
                    triggered.append({
                        "id": entry.id,
                        "symbol": entry.symbol,
                        "setup_type": entry.setup_type.value,
                        "direction": entry.direction.value,
                        "trigger_price": entry.trigger_price,
                        "gap_pct": entry.gap_pct,
                        "reasoning": entry.reasoning
                    })
                    
                    self._logger.log("prestaged_entry_ready", {
                        "id": entry.id,
                        "symbol": entry.symbol,
                        "direction": entry.direction.value,
                        "setup_type": entry.setup_type.value,
                        "trigger_price": entry.trigger_price,
                        "action": "ready_for_options_bot"
                    })
            
            summary = prestaged.get_entry_summary()
            if summary["staged_count"] > 0 or summary["triggered_count"] > 0:
                self._logger.log("prestaged_status", {
                    "time": current_time,
                    "staged": summary["staged_count"],
                    "triggered": summary["triggered_count"]
                })
        
        except Exception as e:
            self._logger.error(f"PreStaged entries check error: {e}")
        
        return triggered

    def _execute_bounce_bot(self, bot_id: str, max_daily_loss: float, halt_new_trades: bool = False) -> Dict[str, Any]:
        """Execute BounceBot - overnight crypto dip-buying strategy (1-5:30am PST)"""
        from ..bots.bounce_bot import BounceBot
        
        try:
            bot = BounceBot(bot_id)
            result = bot.execute(max_daily_loss=max_daily_loss, halt_new_trades=halt_new_trades)
            
            self._logger.log("bounce_bot_execution_complete", {
                "bot_id": bot_id,
                "trades_attempted": result.get("trades_attempted", 0),
                "trades_executed": result.get("trades_executed", 0),
                "positions_managed": result.get("positions_managed", 0),
                "outside_hours": result.get("outside_hours", False)
            })
            
            return result
        except Exception as e:
            self._logger.error(f"BounceBot execution error: {e}")
            return {
                "trades_attempted": 0,
                "positions_managed": 0,
                "signals": [],
                "outside_hours": False
            }


_execution_service: Optional[ExecutionService] = None


def get_execution_service() -> ExecutionService:
    global _execution_service
    if _execution_service is None:
        _execution_service = ExecutionService()
    return _execution_service