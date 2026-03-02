"""
HailMary Bot — Standalone OTM Options Entry Bot
=================================================

Buys cheap near-term OTM options with massive upside potential.
Separated from OptionsBot for independent operation.

Key Design:
- ENTRY ONLY by default (use_exitbot: false → self-managed tiered exits)
- Premium IS the stop-loss — no stop needed
- Tiered profit taking: 50% at 3x, 25% at 5x, 25% runner at 10x
- Dynamic universe from PreMarketIntel/OptionsScreener/DynamicUniverse
- Earnings IV crush protection, VWAP posture confirmation
- Kill switch enforcement (blocks entries on global freeze)
- Sweep-optimized: $7 max premium, 0DTE, 0.5% OTM, 5 trades/day

Usage:
    from .hail_mary_bot import get_hail_mary_bot
    bot = get_hail_mary_bot("hm_core")
    result = bot.execute(max_daily_loss=500.0)
"""

import json
from dataclasses import dataclass, field
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from uuid import uuid4

from ..core.logging import get_logger
from ..core.state import get_state, set_state, get_keys_by_prefix
from ..core.config import load_bots_config
from ..core.clock import get_market_clock
from ..services.alpaca_client import get_alpaca_client
from ..services.exitbot import get_exitbot
from ..risk.killswitch import get_killswitch_service
from ..risk.risk_integration import get_risk_integration, RiskAction
from ..indicators.vwap_posture import (
    VWAPPosture, get_vwap_posture_manager, PostureDecision
)


@dataclass
class HailMaryConfig:
    enabled: bool = False
    dry_run: bool = False
    bot_id: str = "hm_core"
    max_trades_per_day: int = 5
    max_risk_usd: float = 500.0
    min_risk_usd: float = 100.0
    max_premium: float = 7.00
    min_premium: float = 0.05
    max_spread: float = 0.50
    max_spread_pct: float = 15.0
    dte_min: int = 0
    dte_max: int = 0
    strike_otm_pct: float = 0.5
    min_delta: float = 0.01
    max_delta: float = 0.30
    require_momentum: bool = True
    min_stock_change: float = 0.3
    tickers: Optional[List[str]] = None
    use_dynamic_universe: bool = True
    dynamic_max_tickers: int = 20
    dynamic_min_score: float = 30.0
    profit_target_mult: float = 25.0
    time_exit_days: int = 1
    use_exitbot: bool = False
    tiered_exits: bool = True
    tier1_mult: float = 3.0
    tier1_pct: float = 50.0
    tier2_mult: float = 5.0
    tier2_pct: float = 25.0
    runner_mult: float = 10.0
    block_near_earnings: bool = True
    earnings_buffer_days: int = 3
    use_vwap_posture: bool = True
    delegate_exits_to_exitbot: bool = False
    min_entry_spacing_seconds: int = 120


_instances: Dict[str, "HailMaryBot"] = {}


def get_hail_mary_bot(bot_id: str = "hm_core") -> "HailMaryBot":
    if bot_id not in _instances:
        _instances[bot_id] = HailMaryBot(bot_id)
    return _instances[bot_id]


class HailMaryBot:
    def __init__(self, bot_id: str = "hm_core"):
        self.bot_id = bot_id
        self._logger = get_logger()
        self._alpaca = get_alpaca_client()
        self._config: Optional[HailMaryConfig] = None
        self._last_entry_time: float = 0.0
        self._load_config()

    def _load_config(self) -> None:
        try:
            bots_config = load_bots_config()
            cfg = bots_config.get("hailmary_bot", {})

            if not cfg:
                self._logger.warn("HailMaryBot: no 'hailmary_bot' section in bots.yaml")
                return

            hm = cfg.get("hail_mary", cfg)

            self._config = HailMaryConfig(
                enabled=cfg.get("enabled", False),
                dry_run=cfg.get("dry_run", False),
                bot_id=cfg.get("bot_id", self.bot_id),
                max_trades_per_day=hm.get("max_trades_per_day", 5),
                max_risk_usd=hm.get("max_risk_per_trade_usd", 500.0),
                min_risk_usd=hm.get("min_risk_per_trade_usd", 100.0),
                max_premium=hm.get("max_premium", 7.00),
                min_premium=hm.get("min_premium", 0.05),
                max_spread=hm.get("max_spread", 0.50),
                max_spread_pct=hm.get("max_spread_pct", 15.0),
                dte_min=hm.get("dte_min", 0),
                dte_max=hm.get("dte_max", 0),
                strike_otm_pct=hm.get("strike_otm_pct", 0.5),
                min_delta=hm.get("min_delta", 0.01),
                max_delta=hm.get("max_delta", 0.30),
                require_momentum=hm.get("require_momentum_alignment", True),
                min_stock_change=hm.get("min_stock_change_pct", 0.3),
                tickers=hm.get("tickers", None),
                use_dynamic_universe=hm.get("use_dynamic_universe", True),
                dynamic_max_tickers=hm.get("dynamic_max_tickers", 20),
                dynamic_min_score=hm.get("dynamic_min_score", 30.0),
                profit_target_mult=hm.get("profit_target_multiplier", 25.0),
                time_exit_days=hm.get("time_exit_days_before_expiry", 1),
                use_exitbot=hm.get("use_exitbot", False),
                tiered_exits=hm.get("tiered_exits", True),
                tier1_mult=hm.get("tier1_multiplier", 3.0),
                tier1_pct=hm.get("tier1_sell_pct", 50.0),
                tier2_mult=hm.get("tier2_multiplier", 5.0),
                tier2_pct=hm.get("tier2_sell_pct", 25.0),
                runner_mult=hm.get("runner_multiplier", 10.0),
                block_near_earnings=hm.get("block_near_earnings", True),
                earnings_buffer_days=hm.get("earnings_buffer_days", 3),
                use_vwap_posture=hm.get("use_vwap_posture", True),
                delegate_exits_to_exitbot=cfg.get("delegate_exits_to_exitbot", False),
                min_entry_spacing_seconds=hm.get("min_entry_spacing_seconds", 120),
            )

            self._logger.log("hailmary_bot_config_loaded", {
                "bot_id": self.bot_id,
                "enabled": self._config.enabled,
                "max_trades_per_day": self._config.max_trades_per_day,
                "max_risk_usd": self._config.max_risk_usd,
                "max_premium": self._config.max_premium,
                "dte_range": f"{self._config.dte_min}-{self._config.dte_max}",
                "strike_otm_pct": self._config.strike_otm_pct,
                "tiered_exits": self._config.tiered_exits,
                "use_exitbot": self._config.use_exitbot,
                "use_dynamic_universe": self._config.use_dynamic_universe,
                "dry_run": self._config.dry_run
            })

        except Exception as e:
            self._logger.error(f"HailMaryBot config load failed: {e}")
            self._config = None

    # =========================================================================
    # MAIN EXECUTE — called by orchestrator/runner every loop
    # =========================================================================

    def execute(self, max_daily_loss: float = 500.0, halt_new_trades: bool = False) -> Dict[str, Any]:
        results = {
            "trades_attempted": 0,
            "positions_managed": 0,
            "errors": []
        }

        if not self._config or not self._config.enabled:
            return results

        # Manage existing HailMary exits first (always runs even if halted)
        if not self._config.use_exitbot and not self._config.delegate_exits_to_exitbot:
            self._manage_hail_mary_exits(results)

        # Check kill switch before new entries
        if halt_new_trades:
            self._logger.log("hailmary_bot_halted", {"reason": "halt_new_trades flag"})
            return results

        try:
            killswitch = get_killswitch_service()
            ks_allowed, ks_reason = killswitch.is_entry_allowed("hailmary")
            if not ks_allowed:
                self._logger.log("hailmary_bot_killswitch_blocked", {"reason": ks_reason})
                return results
        except Exception as ks_err:
            self._logger.warn(f"HailMaryBot killswitch check failed (fail-open): {ks_err}")

        # Session protection check — block entries if target locked
        # Freeroll eligibility is checked later per-opportunity with actual quality score
        try:
            from ..risk.session_protection import get_session_protection
            sp = get_session_protection()
            if sp.is_target_locked() and (not sp.is_freeroll_available()):
                sp_block, sp_reason = sp.should_block_new_trade(quality_score=0.0)
                if sp_block:
                    if not sp.should_throttle_message("hailmary_block"):
                        self._logger.log("hailmary_session_protection_block", {"reason": sp_reason})
                        print(f"  [HAILMARY] Entry blocked: {sp_reason}")
                    return results
        except Exception as sp_err:
            self._logger.warn(f"HailMaryBot session protection check failed (fail-open): {sp_err}")

        # Run entry scan
        self._execute_hail_mary(results)

        return results

    # =========================================================================
    # TRADE COUNTER
    # =========================================================================

    def _get_trades_today(self) -> int:
        today_key = f"hailmary_bot_trades_{get_market_clock().now().strftime('%Y%m%d')}"
        return get_state(today_key, 0)

    def _increment_trades_today(self) -> None:
        today_key = f"hailmary_bot_trades_{get_market_clock().now().strftime('%Y%m%d')}"
        current = get_state(today_key, 0)
        set_state(today_key, current + 1)
    
    def _get_traded_underlyings_today(self) -> set:
        """Get set of underlyings already traded today (persisted in state DB)."""
        today_key = f"hailmary_traded_underlyings_{get_market_clock().now().strftime('%Y%m%d')}"
        return set(get_state(today_key, []))
    
    def _add_traded_underlying(self, underlying: str) -> None:
        """Record that we traded this underlying today (persisted in state DB)."""
        today_key = f"hailmary_traded_underlyings_{get_market_clock().now().strftime('%Y%m%d')}"
        current = set(get_state(today_key, []))
        current.add(underlying.upper())
        set_state(today_key, list(current))

    # =========================================================================
    # EARNINGS IV CRUSH PROTECTION
    # =========================================================================

    def _is_near_earnings(self, ticker: str, buffer_days: int = 3) -> bool:
        try:
            from ..services.earnings_calendar import get_earnings_calendar
            calendar = get_earnings_calendar()
            info = calendar.get_earnings_info(ticker)

            if info is None or info.report_date is None:
                return False

            days_until = info.days_until
            if days_until is None:
                return False

            is_near = 0 <= days_until <= buffer_days

            if is_near:
                self._logger.log("hailmary_earnings_blocked", {
                    "ticker": ticker,
                    "earnings_date": info.report_date,
                    "days_until": days_until,
                    "buffer_days": buffer_days,
                    "action": "BLOCK_ENTRY"
                })

            return is_near

        except Exception as e:
            self._logger.warn(f"HM earnings check failed for {ticker} (fail-open): {e}")
            return False

    # =========================================================================
    # DYNAMIC UNIVERSE BUILDER
    # =========================================================================

    def _build_universe(self) -> List[str]:
        seen = set()
        universe = []
        source_counts = {}
        min_score = self._config.dynamic_min_score
        max_tickers = self._config.dynamic_max_tickers

        def _add_tickers(tickers: List[str], source: str):
            added = 0
            for t in tickers:
                t_upper = t.upper().strip()
                if t_upper and t_upper not in seen and len(universe) < max_tickers:
                    seen.add(t_upper)
                    universe.append(t_upper)
                    added += 1
            source_counts[source] = added

        # SOURCE 1: PreMarket Intelligence ranked opportunities
        try:
            from ..services.premarket_intelligence import PreMarketIntelligenceService
            intel_service = PreMarketIntelligenceService()
            cached = intel_service.get_cached_intelligence()

            if cached and cached.ranked_opportunities:
                ranked_tickers = []
                for ticker in cached.ranked_opportunities:
                    intel = cached.tickers.get(ticker)
                    if intel and intel.opportunity_score >= min_score:
                        ranked_tickers.append(ticker)
                    elif not intel:
                        ranked_tickers.append(ticker)
                _add_tickers(ranked_tickers, "premarket_intel")
        except Exception as e:
            self._logger.warn(f"HM universe: premarket intel failed (continuing): {e}")

        # SOURCE 2: Options Screener selected underlyings
        try:
            from ..services.options_screener import get_options_screener
            screener = get_options_screener()
            result = screener.screen()
            if result and result.selected_underlyings:
                _add_tickers(result.selected_underlyings, "options_screener")
        except Exception as e:
            self._logger.warn(f"HM universe: options screener failed (continuing): {e}")

        # SOURCE 3: Static fallback list from config
        static_tickers = self._config.tickers or []
        if static_tickers:
            _add_tickers(static_tickers, "static_config")

        # If all sources failed, use default liquid names
        if not universe:
            _add_tickers(["SPY", "QQQ", "TSLA", "NVDA", "AAPL", "AMD", "META", "AMZN"], "fallback")

        self._logger.log("hailmary_universe_built", {
            "total_tickers": len(universe),
            "source_counts": source_counts,
            "tickers": universe,
            "max_tickers": max_tickers,
            "min_score": min_score
        })

        return universe

    # =========================================================================
    # VWAP POSTURE CHECK
    # =========================================================================

    def _get_vwap_posture(self, ticker: str, price: float) -> Optional[PostureDecision]:
        try:
            bars = self._alpaca.get_bars(ticker, days=2, timeframe="5Min")
            if not bars or len(bars) < 10:
                return None

            vwap_bars = [
                {
                    "high": float(b.high) if hasattr(b, 'high') else float(b.get("high", 0)),
                    "low": float(b.low) if hasattr(b, 'low') else float(b.get("low", 0)),
                    "close": float(b.close) if hasattr(b, 'close') else float(b.get("close", 0)),
                    "volume": float(b.volume) if hasattr(b, 'volume') else float(b.get("volume", 0))
                }
                for b in bars
            ]

            vwap_manager = get_vwap_posture_manager(ticker)
            posture = vwap_manager.evaluate(
                bars=vwap_bars,
                current_price=price,
                intraday_bars=vwap_bars[-50:],
                bar_index=len(vwap_bars)
            )

            self._logger.log("hailmary_vwap_posture", {
                "ticker": ticker,
                "posture": posture.posture.value,
                "allow_long": posture.allow_long,
                "allow_short": posture.allow_short,
                "distance_pct": round(posture.distance_from_vwap_pct, 3),
                "is_retest": posture.is_vwap_retest
            })

            return posture

        except Exception as e:
            self._logger.error(f"HM VWAP posture check failed for {ticker}: {e}")
            return None

    # =========================================================================
    # BUYING POWER CHECK
    # =========================================================================

    def _check_buying_power(self, total_cost: float) -> Dict[str, Any]:
        result = {
            "approved": False,
            "required": total_cost,
            "available": 0.0,
            "reason": ""
        }

        try:
            account = self._alpaca.get_account()

            def safe_float(val, default=0.0) -> float:
                if val is None:
                    return default
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return default

            options_bp = getattr(account, 'options_buying_power', None)
            if options_bp is not None:
                available = safe_float(options_bp)
            else:
                available = safe_float(account.buying_power)

            result["available"] = available

            required_with_buffer = total_cost * 1.05

            if available >= required_with_buffer:
                result["approved"] = True
                result["reason"] = f"Sufficient buying power: ${available:.2f} >= ${required_with_buffer:.2f}"
            else:
                result["reason"] = f"Need ${required_with_buffer:.2f}, have ${available:.2f}"

            self._logger.log("hailmary_buying_power_check", result)

        except Exception as e:
            result["reason"] = f"Buying power check failed: {e}"
            self._logger.error(result["reason"])

        return result

    # =========================================================================
    # SPREAD GATE
    # =========================================================================

    def _check_spread_gate(self, bid: float, ask: float, symbol: str) -> Dict[str, Any]:
        if ask <= 0 or bid <= 0:
            return {"approved": False, "spread_pct": 999.0, "reason": "Invalid bid/ask quotes"}

        mid = (bid + ask) / 2
        spread_pct = ((ask - bid) / mid) * 100 if mid > 0 else 999.0

        max_spread_pct = self._config.max_spread_pct if self._config else 15.0
        approved = spread_pct <= max_spread_pct

        self._logger.log("hailmary_spread_gate", {
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "spread_pct": round(spread_pct, 2),
            "max_spread_pct": max_spread_pct,
            "approved": approved
        })

        if not approved:
            return {
                "approved": False,
                "spread_pct": spread_pct,
                "reason": f"Spread {spread_pct:.2f}% exceeds max {max_spread_pct}%"
            }

        return {"approved": True, "spread_pct": spread_pct}

    # =========================================================================
    # ENTRY SCAN — find and execute best opportunity
    # =========================================================================

    def _execute_hail_mary(self, results: Dict[str, Any]) -> Dict[str, Any]:
        if not self._config or not self._config.enabled:
            return results

        # RATE LIMITING: Enforce minimum spacing between entries (forensic fix 02/17)
        now = time.time()
        spacing = self._config.min_entry_spacing_seconds
        elapsed = now - self._last_entry_time
        if self._last_entry_time > 0 and elapsed < spacing:
            self._logger.log("hailmary_rate_limited", {
                "seconds_since_last": round(elapsed, 1),
                "min_spacing": spacing,
                "wait_remaining": round(spacing - elapsed, 1)
            })
            return results

        # Risk integration gate — downgrade macro_stress from hard block to size reduction
        # Hailmary trades are small, high-conviction plays that should still scan during stress
        risk_size_multiplier = 1.0
        try:
            risk_integration = get_risk_integration()
            risk_eval = risk_integration.evaluate_entry(
                symbol="MARKET",
                bot_name=f"{self.bot_id}_hail_mary",
                proposed_size_usd=self._config.max_risk_usd,
                is_bullish=True
            )
            if risk_eval and risk_eval.action == RiskAction.HALT_TRADING:
                # Only hard-block on HALT (PnL halt, circuit breaker) — not macro stress
                self._logger.log("hailmary_risk_gate_blocked", {
                    "action": risk_eval.action.value,
                    "reason": risk_eval.reason,
                    "gate_details": risk_eval.gate_details
                })
                return results
            elif risk_eval and risk_eval.action == RiskAction.SKIP_ENTRY:
                # Macro stress or VIX crisis: downgrade to 50% size instead of blocking
                risk_size_multiplier = 0.5
                self._logger.log("hailmary_risk_gate_softened", {
                    "action": risk_eval.action.value,
                    "reason": risk_eval.reason,
                    "gate_details": risk_eval.gate_details,
                    "size_multiplier": risk_size_multiplier,
                    "note": "Downgraded from block to size reduction for hailmary"
                })
            elif risk_eval and risk_eval.action == RiskAction.REDUCE_SIZE:
                risk_size_multiplier = risk_eval.size_multiplier or 0.75
        except Exception as ri_err:
            self._logger.warn(f"HailMary risk integration check failed: {ri_err}")

        # Check daily limit
        hm_today = self._get_trades_today()
        if hm_today >= self._config.max_trades_per_day:
            self._logger.log("hailmary_daily_limit_reached", {
                "trades_today": hm_today,
                "max_per_day": self._config.max_trades_per_day
            })
            return results

        # CONCURRENT POSITION LIMIT + DUPLICATE PREVENTION (forensic fix 02/17, 02/23, 02/25)
        existing_underlyings = set()
        try:
            positions = self._alpaca.get_all_positions()
            option_positions = [p for p in positions if hasattr(p, 'asset_class') and str(getattr(p, 'asset_class', '')) in ('us_option', 'option')]
            if len(option_positions) >= 3:
                self._logger.log("hailmary_max_positions_reached", {
                    "current_option_positions": len(option_positions),
                    "max_concurrent": 3,
                    "reason": "Capital preservation — max 3 concurrent option positions"
                })
                return results
            # Build set of underlyings we already have positions in to prevent duplicates
            for pos in option_positions:
                sym = str(getattr(pos, 'symbol', ''))
                underlying = ''
                for i, ch in enumerate(sym):
                    if ch.isdigit():
                        underlying = sym[:i]
                        break
                if underlying:
                    existing_underlyings.add(underlying)
        except Exception as pos_err:
            self._logger.warn(f"HailMary position check failed (fail-open): {pos_err}")
        
        # Merge with state-persisted traded underlyings (catches orders not yet in positions)
        traded_today = self._get_traded_underlyings_today()
        existing_underlyings |= traded_today

        # Build ticker universe
        if self._config.use_dynamic_universe:
            hm_tickers = self._build_universe()
        else:
            hm_tickers = self._config.tickers or ["SPY", "QQQ", "TSLA", "NVDA", "AAPL"]

        self._logger.log("hailmary_scan_start", {
            "tickers": hm_tickers,
            "universe_mode": "dynamic" if self._config.use_dynamic_universe else "static",
            "max_premium": self._config.max_premium,
            "max_spread": self._config.max_spread,
            "dte_range": f"{self._config.dte_min}-{self._config.dte_max}",
            "trades_today": hm_today,
            "max_trades": self._config.max_trades_per_day,
            "earnings_protection": self._config.block_near_earnings,
            "vwap_posture": self._config.use_vwap_posture,
            "tiered_exits": self._config.tiered_exits
        })

        all_opportunities = []

        for ticker in hm_tickers:
            try:
                # Skip tickers where we already hold an option position (prevent duplicate entries)
                if ticker in existing_underlyings:
                    self._logger.log("hailmary_skip_existing_position", {
                        "ticker": ticker,
                        "reason": "Already have option position in this underlying"
                    })
                    continue

                # Earnings IV crush protection
                if self._config.block_near_earnings:
                    if self._is_near_earnings(ticker, self._config.earnings_buffer_days):
                        continue

                opportunities = self._scan_opportunities(ticker)
                all_opportunities.extend(opportunities)
            except Exception as e:
                self._logger.warn(f"HailMary scan failed for {ticker}: {e}")

        if not all_opportunities:
            self._logger.log("hailmary_no_opportunities", {
                "tickers_scanned": len(hm_tickers)
            })
            return results

        # Sort by score and execute the best
        all_opportunities.sort(key=lambda x: x["score"], reverse=True)

        self._logger.log("hailmary_opportunities_found", {
            "total_opportunities": len(all_opportunities),
            "top_score": all_opportunities[0]["score"],
            "top_symbol": all_opportunities[0]["symbol"],
            "top_3": [{
                "symbol": o["symbol"],
                "underlying": o["underlying"],
                "type": o["type"],
                "strike": o["strike"],
                "mid": o["mid"],
                "spread": o["spread"],
                "score": o["score"]
            } for o in all_opportunities[:3]]
        })

        best = all_opportunities[0]
        trade_result = self._execute_trade(best, risk_size_multiplier=risk_size_multiplier)

        if trade_result.get("success"):
            results["trades_attempted"] += 1
            self._increment_trades_today()
            self._add_traded_underlying(best["underlying"])
            self._last_entry_time = time.time()

            self._logger.log("hailmary_trade_executed", {
                "symbol": best["symbol"],
                "underlying": best["underlying"],
                "type": best["type"],
                "strike": best["strike"],
                "contracts": trade_result.get("contracts", 0),
                "cost_per_contract": best["mid"],
                "total_risk": trade_result.get("total_risk", 0),
                "score": best["score"],
                "stock_price": best["stock_price"],
                "stock_change_pct": best["stock_change_pct"]
            })
        else:
            self._logger.log("hailmary_trade_failed", {
                "symbol": best["symbol"],
                "error": trade_result.get("error", "Unknown")
            })

        return results

    # =========================================================================
    # SCAN OPPORTUNITIES — scan single ticker's options chain
    # =========================================================================

    def _scan_opportunities(self, ticker: str) -> List[Dict[str, Any]]:
        opportunities = []

        if not self._config:
            return opportunities

        try:
            # Get current stock price and momentum
            stock_data = self._alpaca.get_latest_quote(ticker)
            if not stock_data:
                return opportunities

            bid_price = float(stock_data.get("bid", stock_data.get("bid_price", 0)) or 0)
            ask_price = float(stock_data.get("ask", stock_data.get("ask_price", 0)) or 0)
            stock_price = (bid_price + ask_price) / 2 if bid_price > 0 and ask_price > 0 else 0

            # Get recent bars for momentum calculation
            bars = self._alpaca.get_bars(ticker, timeframe="1Day", limit=3)
            if not bars or len(bars) < 2:
                return opportunities

            prev_close = float(getattr(bars[-2], 'close', 0) or 0)
            today_close = float(getattr(bars[-1], 'close', 0) or 0)

            if stock_price <= 0:
                stock_price = today_close

            if prev_close <= 0 or stock_price <= 0:
                return opportunities

            change_pct = ((today_close - prev_close) / prev_close) * 100

            # Determine direction
            is_bullish = change_pct > 0

            # Apply momentum filter
            if self._config.require_momentum:
                if abs(change_pct) < self._config.min_stock_change:
                    self._logger.log("hailmary_weak_momentum", {
                        "ticker": ticker,
                        "change_pct": round(change_pct, 2),
                        "min_required": self._config.min_stock_change
                    })
                    return opportunities

            # VWAP posture confirmation
            if self._config.use_vwap_posture:
                try:
                    posture = self._get_vwap_posture(ticker, stock_price)

                    if posture and posture.posture != VWAPPosture.NEUTRAL:
                        vwap_bullish = posture.posture == VWAPPosture.BULLISH
                        vwap_bearish = posture.posture == VWAPPosture.BEARISH

                        if is_bullish and vwap_bearish:
                            self._logger.log("hailmary_vwap_conflict_skip", {
                                "ticker": ticker,
                                "momentum": "bullish",
                                "vwap_posture": "BEARISH",
                                "action": "skipping_ticker"
                            })
                            return opportunities
                        elif not is_bullish and vwap_bullish:
                            self._logger.log("hailmary_vwap_conflict_skip", {
                                "ticker": ticker,
                                "momentum": "bearish",
                                "vwap_posture": "BULLISH",
                                "action": "skipping_ticker"
                            })
                            return opportunities
                        else:
                            self._logger.log("hailmary_vwap_confirmed", {
                                "ticker": ticker,
                                "momentum": "bullish" if is_bullish else "bearish",
                                "vwap_posture": posture.posture.value
                            })
                    elif posture and posture.posture == VWAPPosture.NEUTRAL:
                        self._logger.log("hailmary_vwap_neutral", {
                            "ticker": ticker,
                            "action": "allowing_momentum_direction",
                            "reason": "VWAP neutral, falling back to price momentum"
                        })
                except Exception as vwap_err:
                    self._logger.warn(f"VWAP posture check failed for {ticker} (fail-open): {vwap_err}")

            # Calculate strike range for OTM options
            otm_range = stock_price * (self._config.strike_otm_pct / 100)

            exp_start = get_market_clock().now()
            exp_end = exp_start + timedelta(days=max(self._config.dte_max, 1))

            # Scan calls (OTM = above current price)
            call_chain = self._alpaca.get_options_chain(
                underlying_symbol=ticker,
                expiration_date_gte=exp_start.strftime("%Y-%m-%d"),
                expiration_date_lte=exp_end.strftime("%Y-%m-%d"),
                strike_price_gte=round(stock_price, 0),
                strike_price_lte=round(stock_price + otm_range, 0),
                option_type="call"
            )

            # Scan puts (OTM = below current price)
            put_chain = self._alpaca.get_options_chain(
                underlying_symbol=ticker,
                expiration_date_gte=exp_start.strftime("%Y-%m-%d"),
                expiration_date_lte=exp_end.strftime("%Y-%m-%d"),
                strike_price_gte=round(stock_price - otm_range, 0),
                strike_price_lte=round(stock_price, 0),
                option_type="put"
            )

            # Combine chains
            all_contracts = []
            if call_chain:
                for c in call_chain:
                    c["_option_type"] = "call"
                    all_contracts.append(c)
            if put_chain:
                for p in put_chain:
                    p["_option_type"] = "put"
                    all_contracts.append(p)

            # Filter and score
            for contract in all_contracts:
                opt_bid = float(contract.get("bid", 0) or 0)
                opt_ask = float(contract.get("ask", 0) or 0)

                if opt_bid <= 0 or opt_ask <= 0:
                    continue

                opt_mid = (opt_bid + opt_ask) / 2
                spread = opt_ask - opt_bid

                if opt_mid < self._config.min_premium:
                    continue
                if opt_mid > self._config.max_premium:
                    continue
                if spread > self._config.max_spread:
                    continue

                delta = abs(float(contract.get("delta", 0) or 0))
                if delta < self._config.min_delta:
                    continue
                if delta > self._config.max_delta:
                    continue

                symbol = contract.get("symbol", "")
                
                # Derive option type from actual OCC symbol (C/P in symbol)
                # NOT from the chain tag — chain tags can mismatch when
                # strike ranges overlap between call/put chain queries
                from ..utils.ticker_classifier import parse_option_symbol as _parse_opt
                _parsed = _parse_opt(symbol) if symbol else None
                if _parsed:
                    option_type = _parsed.option_type
                else:
                    option_type = contract.get("_option_type", contract.get("type", "call"))

                # Momentum alignment
                if self._config.require_momentum:
                    if option_type == "call" and not is_bullish:
                        continue
                    if option_type == "put" and is_bullish:
                        continue

                # Score the opportunity
                spread_score = max(0, 1 - (spread / self._config.max_spread))
                price_score = max(0, 1 - (opt_mid / self._config.max_premium))
                delta_score = min(delta / self._config.max_delta, 1.0)

                total_score = (
                    spread_score * 0.40 +
                    price_score * 0.30 +
                    delta_score * 0.30
                )

                strike = float(contract.get("strike", 0) or 0)
                expiry = contract.get("expiry", "unknown")
                iv = float(contract.get("iv", 0) or 0)

                opportunities.append({
                    "symbol": symbol,
                    "underlying": ticker,
                    "type": option_type.upper(),
                    "strike": strike,
                    "expiry": expiry,
                    "bid": opt_bid,
                    "ask": opt_ask,
                    "mid": round(opt_mid, 2),
                    "spread": round(spread, 2),
                    "delta": round(delta, 4),
                    "iv": round(iv * 100, 1) if iv < 10 else round(iv, 1),
                    "stock_price": round(stock_price, 2),
                    "stock_change_pct": round(change_pct, 2),
                    "score": round(total_score, 3),
                    "cost_per_contract": round(opt_mid * 100, 2)
                })

            self._logger.log("hailmary_ticker_scan_complete", {
                "ticker": ticker,
                "stock_price": round(stock_price, 2),
                "change_pct": round(change_pct, 2),
                "contracts_scanned": len(all_contracts),
                "opportunities_found": len(opportunities)
            })

        except Exception as e:
            self._logger.warn(f"HailMary scan error for {ticker}: {e}")

        return opportunities

    # =========================================================================
    # EXECUTE TRADE — place limit order for best opportunity
    # =========================================================================

    def _execute_trade(self, opportunity: Dict[str, Any], risk_size_multiplier: float = 1.0) -> Dict[str, Any]:
        result = {"success": False, "error": None, "contracts": 0, "total_risk": 0}

        if not self._config:
            result["error"] = "No configuration"
            return result

        if self._config.dry_run:
            self._logger.log("hailmary_dry_run_skip", {
                "symbol": opportunity["symbol"],
                "mid": opportunity["mid"],
                "score": opportunity["score"]
            })
            result["error"] = "Dry run mode - trade not executed"
            return result

        try:
            symbol = opportunity["symbol"]
            ask_price = opportunity["ask"]
            ticker = opportunity["underlying"]

            # Session protection freeroll check (quality_score known here)
            freeroll_budget = None
            try:
                from ..risk.session_protection import get_session_protection as _get_sp
                _sp = _get_sp()
                if _sp.is_target_locked():
                    opp_score = opportunity.get("score", 0)
                    quality_100 = min(100, opp_score * 100)
                    sp_block, sp_reason = _sp.should_block_new_trade(quality_score=quality_100)
                    if sp_block:
                        result["error"] = f"Target locked: {sp_reason}"
                        return result
                    elif sp_reason.startswith("FREEROLL:"):
                        freeroll_budget = float(sp_reason.replace("FREEROLL:$", ""))
                        print(f"  [HAILMARY] FREEROLL eligible: score={quality_100:.0f}, house money=${freeroll_budget:.0f}")
            except Exception as _sp_err:
                pass

            # Position sizing (capped by freeroll_budget if in freeroll mode, scaled by risk gate)
            max_risk = self._config.max_risk_usd * risk_size_multiplier
            if freeroll_budget is not None:
                max_risk = min(max_risk, freeroll_budget)
                print(f"  [HAILMARY] Freeroll sizing: max_risk capped to ${max_risk:.0f} (house money)")
            min_risk = self._config.min_risk_usd
            cost_per_contract = ask_price * 100

            if cost_per_contract <= 0:
                result["error"] = "Zero cost per contract"
                return result

            # For freeroll: do NOT force min 1 contract if house money < contract cost
            if freeroll_budget is not None and cost_per_contract > freeroll_budget:
                result["error"] = f"Freeroll: house money ${freeroll_budget:.0f} < contract cost ${cost_per_contract:.0f}"
                return result

            contracts = int(max_risk / cost_per_contract)
            contracts = max(1, contracts)

            total_risk = contracts * cost_per_contract

            if total_risk < min_risk and contracts == 1 and freeroll_budget is None:
                if cost_per_contract < 5.0:
                    result["error"] = f"Trade too small: ${total_risk:.2f} < ${min_risk:.2f} minimum"
                    return result

            # Buying power check
            bp_check = self._check_buying_power(total_risk)
            if not bp_check.get("approved", False):
                result["error"] = f"Insufficient buying power: {bp_check.get('reason')}"
                return result

            # Spread gate check
            spread_check = self._check_spread_gate(
                opportunity["bid"], opportunity["ask"], symbol
            )
            if not spread_check.get("approved", False):
                result["error"] = f"Spread gate rejected: {spread_check.get('reason')}"
                return result

            # ExitBot health check (fail-closed)
            exitbot = get_exitbot()
            if not exitbot.is_healthy():
                self._logger.warn(f"ExitBot unhealthy - blocking hail mary entry for {symbol}")
                result["error"] = "ExitBot unhealthy - entry blocked"
                return result

            # Generate signal identity
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            signal_id = f"HM_{ticker}_standalone_{ts}_{uuid4().hex[:6]}"
            client_order_id = f"hailmary_bot:{ticker}:hm:{signal_id}"

            limit_price = round(ask_price, 2)

            self._logger.log("hailmary_order_attempt", {
                "symbol": symbol,
                "ticker": ticker,
                "strike": opportunity["strike"],
                "type": opportunity["type"],
                "expiry": opportunity["expiry"],
                "contracts": contracts,
                "limit_price": limit_price,
                "total_risk": round(total_risk, 2),
                "signal_id": signal_id,
                "score": opportunity["score"],
                "stock_price": opportunity["stock_price"],
                "stock_change_pct": opportunity["stock_change_pct"]
            })

            order = self._alpaca.place_options_order(
                symbol=symbol,
                qty=contracts,
                side="buy",
                order_type="limit",
                limit_price=limit_price,
                client_order_id=client_order_id,
                position_intent="buy_to_open"
            )

            order_id = order.get("id", "unknown")

            self._logger.log("hailmary_order_placed", {
                "symbol": symbol,
                "order_id": order_id,
                "contracts": contracts,
                "limit_price": limit_price,
                "total_risk": round(total_risk, 2),
                "signal_id": signal_id
            })

            # Register with ExitBot only if configured
            if order_id and order_id != "unknown":
                if self._config.use_exitbot or self._config.delegate_exits_to_exitbot:
                    options_context = {
                        "underlying": ticker,
                        "expiry": opportunity.get("expiry", ""),
                        "strike": opportunity.get("strike", 0),
                        "right": opportunity.get("type", "CALL").lower(),
                        "bid": opportunity.get("bid", 0),
                        "ask": opportunity.get("ask", 0),
                        "strategy": "hail_mary"
                    }

                    if opportunity.get("delta"):
                        options_context["greeks"] = {
                            "delta": opportunity.get("delta", 0)
                        }

                    exitbot.register_entry_intent(
                        bot_id="hailmary_bot",
                        symbol=symbol,
                        side="long",
                        qty=contracts,
                        entry_price=limit_price,
                        signal_id=signal_id,
                        client_order_id=client_order_id,
                        alpaca_order_id=order_id,
                        asset_class="option",
                        options=options_context
                    )
                    self._logger.log("hailmary_exitbot_registered", {"symbol": symbol, "order_id": order_id})
                else:
                    self._logger.log("hailmary_self_managed_exit", {
                        "symbol": symbol,
                        "order_id": order_id,
                        "profit_target_mult": self._config.profit_target_mult,
                        "time_exit_days": self._config.time_exit_days,
                        "reason": "HailMary manages own exits: premium IS the stop, target is Nx profit"
                    })

            # Persist trade record
            trade_record = {
                "strategy": "hail_mary",
                "strategy_type": "debit",
                "bot_id": self.bot_id,
                "ticker": ticker,
                "contracts": contracts,
                "symbol": symbol,
                "strike": opportunity["strike"],
                "type": opportunity["type"],
                "expiry": opportunity["expiry"],
                "entry_price": limit_price,
                "total_risk": round(total_risk, 2),
                "order_id": order_id,
                "signal_id": signal_id,
                "timestamp": get_market_clock().now().isoformat(),
                "stock_price": opportunity["stock_price"],
                "stock_change_pct": opportunity["stock_change_pct"],
                "score": opportunity["score"],
                "spread": opportunity["spread"],
                "delta": opportunity["delta"]
            }

            trade_key = f"hailmary_bot_trade_{signal_id}"
            set_state(trade_key, json.dumps(trade_record))

            result["success"] = True
            result["contracts"] = contracts
            result["total_risk"] = round(total_risk, 2)
            result["order_id"] = order_id

            # Mark freeroll as used after successful freeroll entry
            if freeroll_budget is not None:
                try:
                    from ..risk.session_protection import get_session_protection
                    sp = get_session_protection()
                    sp.mark_freeroll_used(signal_id)
                    self._logger.log("hailmary_freeroll_entry", {
                        "signal_id": signal_id,
                        "symbol": symbol,
                        "contracts": contracts,
                        "total_risk": round(total_risk, 2),
                        "freeroll_budget": freeroll_budget,
                    })
                    print(f"  [HAILMARY] FREEROLL entry placed: {symbol} x{contracts} for ${total_risk:.0f}")
                except Exception as fr_err:
                    self._logger.error(f"Freeroll marking failed: {fr_err}")

        except Exception as e:
            result["error"] = f"HailMary execution failed: {e}"
            self._logger.error(f"HailMary trade error: {e}")

        return result

    # =========================================================================
    # EXIT MANAGEMENT — Tiered profit taking (self-managed, no ExitBot)
    # Premium IS the stop-loss. Target is Nx profit with tiered exits.
    # =========================================================================

    def _manage_hail_mary_exits(self, results: Dict[str, Any]) -> Dict[str, Any]:
        if not self._config or not self._config.enabled:
            return results

        use_tiered = self._config.tiered_exits
        profit_mult = self._config.profit_target_mult
        time_exit_days = self._config.time_exit_days

        try:
            positions = self._alpaca.get_positions()
        except Exception as e:
            self._logger.warn(f"HailMary exit check: failed to fetch positions: {e}")
            return results

        options_positions = [
            p for p in positions
            if p.asset_class == "us_option" or (len(p.symbol) > 10 and any(c in p.symbol for c in "CP"))
        ]

        if not options_positions:
            return results

        hm_records = {}
        try:
            hm_keys = get_keys_by_prefix("hailmary_bot_trade_")
            for key in hm_keys:
                try:
                    raw = get_state(key)
                    if not raw:
                        continue
                    record = json.loads(raw) if isinstance(raw, str) else raw
                    if not isinstance(record, dict):
                        continue
                    record_symbol = record.get("symbol", "")
                    if record_symbol and record.get("entry_price") and not record.get("exit_price"):
                        hm_records[record_symbol.upper().strip()] = record
                except Exception as rec_err:
                    self._logger.warn(f"HailMary exit: skipping bad record {key}: {rec_err}")
                    continue
        except Exception as e:
            self._logger.warn(f"HailMary exit: failed to load trade records: {e}")
            return results

        if not hm_records:
            return results

        self._logger.log("hailmary_exit_check_start", {
            "options_positions": len(options_positions),
            "hm_records": len(hm_records),
            "tiered_exits": use_tiered,
            "tiers": f"{self._config.tier1_mult}x/{self._config.tier2_mult}x/{self._config.runner_mult}x" if use_tiered else f"{profit_mult}x",
            "time_exit_days": time_exit_days
        })

        today = get_market_clock().now().date()

        for pos in options_positions:
            symbol = pos.symbol.upper().strip()

            if symbol not in hm_records:
                continue

            record = hm_records[symbol]
            entry_price = record.get("entry_price", 0)
            expiry_str = record.get("expiry", "")
            current_price = pos.current_price
            qty = abs(int(pos.qty))
            original_contracts = record.get("contracts", qty)

            if entry_price <= 0 or qty <= 0:
                continue

            profit_multiple = current_price / entry_price if entry_price > 0 else 0

            try:
                expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
                days_to_expiry = (expiry_date - today).days
            except (ValueError, TypeError):
                days_to_expiry = 999

            # TIME EXIT — always takes priority
            if days_to_expiry <= time_exit_days and current_price > 0.01:
                exit_reason = f"TIME_EXIT_{days_to_expiry}d_to_expiry"
                self._place_exit(symbol, qty, entry_price, current_price,
                                 profit_multiple, days_to_expiry, exit_reason,
                                 record, results, is_full_exit=True)
                continue

            if use_tiered and original_contracts >= 2:
                tiers_hit = record.get("tiers_hit", [])

                # Tier 1: sell portion at tier1_mult
                if profit_multiple >= self._config.tier1_mult and "tier1" not in tiers_hit:
                    tier1_qty = max(1, int(original_contracts * (self._config.tier1_pct / 100.0)))
                    tier1_qty = min(tier1_qty, qty)

                    if tier1_qty > 0:
                        exit_reason = f"TIER1_{self._config.tier1_mult}x_PROFIT"
                        self._place_exit(symbol, tier1_qty, entry_price, current_price,
                                         profit_multiple, days_to_expiry, exit_reason,
                                         record, results, is_full_exit=False)
                        tiers_hit.append("tier1")
                        record["tiers_hit"] = tiers_hit
                        trade_key = f"hailmary_bot_trade_{record.get('signal_id', symbol)}"
                        set_state(trade_key, json.dumps(record))

                        self._logger.log("hailmary_tier1_exit", {
                            "symbol": symbol,
                            "sold_qty": tier1_qty,
                            "remaining_qty": qty - tier1_qty,
                            "profit_multiple": round(profit_multiple, 2),
                            "tier_mult": self._config.tier1_mult,
                            "pct_sold": self._config.tier1_pct
                        })
                        continue

                # Tier 2: sell portion at tier2_mult
                if profit_multiple >= self._config.tier2_mult and "tier2" not in tiers_hit:
                    tier2_qty = max(1, int(original_contracts * (self._config.tier2_pct / 100.0)))
                    tier2_qty = min(tier2_qty, qty)

                    if tier2_qty > 0:
                        exit_reason = f"TIER2_{self._config.tier2_mult}x_PROFIT"
                        self._place_exit(symbol, tier2_qty, entry_price, current_price,
                                         profit_multiple, days_to_expiry, exit_reason,
                                         record, results, is_full_exit=False)
                        tiers_hit.append("tier2")
                        record["tiers_hit"] = tiers_hit
                        trade_key = f"hailmary_bot_trade_{record.get('signal_id', symbol)}"
                        set_state(trade_key, json.dumps(record))

                        self._logger.log("hailmary_tier2_exit", {
                            "symbol": symbol,
                            "sold_qty": tier2_qty,
                            "remaining_qty": qty - tier2_qty,
                            "profit_multiple": round(profit_multiple, 2),
                            "tier_mult": self._config.tier2_mult,
                            "pct_sold": self._config.tier2_pct
                        })
                        continue

                # Runner: sell all remaining at runner_mult
                if profit_multiple >= self._config.runner_mult:
                    exit_reason = f"RUNNER_{self._config.runner_mult}x_MOONSHOT"
                    self._place_exit(symbol, qty, entry_price, current_price,
                                     profit_multiple, days_to_expiry, exit_reason,
                                     record, results, is_full_exit=True)

                    self._logger.log("hailmary_runner_exit", {
                        "symbol": symbol,
                        "sold_qty": qty,
                        "profit_multiple": round(profit_multiple, 2),
                        "runner_mult": self._config.runner_mult,
                        "tiers_completed": tiers_hit
                    })
                    continue

                # Holding
                tier_status = "RUNNER" if "tier2" in tiers_hit else ("POST_TIER1" if "tier1" in tiers_hit else "PRE_TIER1")
                next_target = (self._config.tier1_mult if "tier1" not in tiers_hit
                              else self._config.tier2_mult if "tier2" not in tiers_hit
                              else self._config.runner_mult)

                self._logger.log("hailmary_position_holding", {
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "profit_multiple": round(profit_multiple, 2),
                    "days_to_expiry": days_to_expiry,
                    "tier_status": tier_status,
                    "next_target": f"{next_target}x",
                    "qty_remaining": qty,
                    "tiers_hit": tiers_hit,
                    "status": "HOLDING"
                })
            else:
                # Non-tiered: all-or-nothing
                if profit_multiple >= profit_mult:
                    exit_reason = f"PROFIT_TARGET_{profit_mult}x"
                    self._place_exit(symbol, qty, entry_price, current_price,
                                     profit_multiple, days_to_expiry, exit_reason,
                                     record, results, is_full_exit=True)
                else:
                    self._logger.log("hailmary_position_holding", {
                        "symbol": symbol,
                        "entry_price": entry_price,
                        "current_price": current_price,
                        "profit_multiple": round(profit_multiple, 2),
                        "days_to_expiry": days_to_expiry,
                        "target_mult": profit_mult,
                        "status": "HOLDING"
                    })

        return results

    # =========================================================================
    # PLACE EXIT ORDER
    # =========================================================================

    def _place_exit(self, symbol: str, qty: int, entry_price: float,
                    current_price: float, profit_multiple: float,
                    days_to_expiry: int, exit_reason: str,
                    record: Dict[str, Any], results: Dict[str, Any],
                    is_full_exit: bool = True) -> None:
        try:
            self._logger.log("hailmary_exit_triggered", {
                "symbol": symbol,
                "ticker": record.get("ticker", ""),
                "entry_price": entry_price,
                "current_price": current_price,
                "profit_multiple": round(profit_multiple, 2),
                "days_to_expiry": days_to_expiry,
                "qty": qty,
                "is_full_exit": is_full_exit,
                "reason": exit_reason
            })

            exit_order = self._alpaca.place_options_order(
                symbol=symbol,
                qty=qty,
                side="sell",
                order_type="market",
                position_intent="sell_to_close",
                client_order_id=f"hm_exit_{symbol}_{exit_reason[:20]}_{int(get_market_clock().now().timestamp())}"
            )

            exit_order_id = exit_order.get("id", "unknown")
            pnl_per_contract = (current_price - entry_price) * 100
            total_pnl = pnl_per_contract * qty

            self._logger.log("hailmary_exit_placed", {
                "symbol": symbol,
                "exit_order_id": exit_order_id,
                "qty": qty,
                "entry_price": entry_price,
                "exit_price": current_price,
                "profit_multiple": round(profit_multiple, 2),
                "pnl_per_contract": round(pnl_per_contract, 2),
                "total_pnl": round(total_pnl, 2),
                "is_full_exit": is_full_exit,
                "reason": exit_reason
            })

            try:
                from ..risk.session_protection import get_session_protection
                sp = get_session_protection()
                sp.record_trade_pnl(total_pnl, symbol, f"hailmary_{exit_reason}")
            except Exception as sp_err:
                self._logger.error(f"SessionProtection record failed in HailMary exit (fail-open): {sp_err}")

            partial_exits = record.get("partial_exits", [])
            partial_exits.append({
                "qty": qty,
                "price": current_price,
                "pnl": round(total_pnl, 2),
                "multiple": round(profit_multiple, 2),
                "reason": exit_reason,
                "timestamp": get_market_clock().now().isoformat()
            })
            record["partial_exits"] = partial_exits

            if is_full_exit:
                record["exit_price"] = current_price
                record["exit_reason"] = exit_reason
                record["exit_timestamp"] = get_market_clock().now().isoformat()
                total_realized = sum(pe.get("pnl", 0) for pe in partial_exits)
                record["pnl"] = round(total_realized, 2)
                record["profit_multiple"] = round(profit_multiple, 2)
                record["exit_order_id"] = exit_order_id

            trade_key = f"hailmary_bot_trade_{record.get('signal_id', symbol)}"
            set_state(trade_key, json.dumps(record))

            results["positions_managed"] = results.get("positions_managed", 0) + 1

        except Exception as exit_err:
            self._logger.error(f"HailMary exit failed for {symbol}: {exit_err}")
            results["errors"].append(f"HM exit failed {symbol}: {exit_err}")
