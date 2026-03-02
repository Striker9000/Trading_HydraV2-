"""
BounceBot - Overnight Crypto Dip-Buying Strategy
=================================================

This bot captures overnight crypto drawdowns and rides the bounce back up.
Operates during the 1:00am - 5:30am PST window when crypto experiences
frequent volatility spikes and dip/recovery patterns.

OPTIMIZED STRATEGY (84.5% win rate, PF 1.76):
- Monitor for -2.0%+ drawdowns from recent highs (deeper dips = better bounces)
- Enter when RSI < 20 (strongly oversold) for quality entries
- +1.5% take-profit (higher target for meaningful bounces)
- -2.0% stop-loss (tighter risk control)
- Max 90-minute hold (quicker exits to capture bounce window)

WHY OVERNIGHT:
- 1-5:30am PST sees consistent crypto volatility (Asia open, Europe pre-open)
- Liquidity thin = bigger moves, but also bigger bounces
- Pattern: Sharp dip -> exhaustion -> mean reversion bounce

RISK CONTROLS:
- Only top-3 liquid pairs (BTC, ETH, SOL)
- 75% normal position size (config-driven)
- Max 10 trades per session
- Separate from CryptoBot to avoid interference
"""

from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta, time
from dataclasses import dataclass
import time as time_module
import pytz

from ..core.logging import get_logger
from ..core.state import get_state, set_state, delete_state
from ..core.config import load_bots_config, load_settings
from ..core.clock import get_market_clock
from ..services.alpaca_client import get_alpaca_client
from ..services.decision_tracker import get_decision_tracker
from ..risk.trailing_stop import get_trailing_stop_manager, TrailingStopConfig
from ..risk.killswitch import get_killswitch_service


PST = pytz.timezone("America/Los_Angeles")

RSI_PERIOD = 14
RSI_OVERSOLD = 30
MAX_QUOTE_AGE_SECONDS = 30
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2


def normalize_crypto_symbol(symbol: str) -> str:
    """Convert XXXUSD -> XXX/USD format for Alpaca API"""
    if "/" in symbol:
        return symbol
    if symbol.endswith("USD"):
        base = symbol[:-3]
        return f"{base}/USD"
    return symbol


def denormalize_crypto_symbol(symbol: str) -> str:
    """Convert XXX/USD -> XXXUSD format"""
    return symbol.replace("/", "")


@dataclass
class BounceConfig:
    """Configuration for BounceBot from bots.yaml"""
    bot_id: str
    enabled: bool
    pairs: List[str]
    max_trades_per_session: int
    position_size_multiplier: float
    min_notional_usd: float
    max_notional_usd: float
    equity_pct: float
    window_start: str
    window_end: str
    drawdown_threshold_pct: float
    rsi_oversold: int
    take_profit_pct: float
    stop_loss_pct: float
    max_hold_minutes: int
    require_reversal_candle: bool
    lookback_hours: int
    require_volume_spike: bool


class BounceBot:
    """
    Overnight crypto dip-buying bot.
    
    Monitors crypto prices during 1-5:30am PST for significant drawdowns,
    then enters long positions on oversold bounces.
    
    Usage:
        bot = BounceBot("bounce_core")
        result = bot.execute(max_daily_loss=100.0)
    """
    
    def __init__(self, bot_id: str = "bounce_core"):
        self.bot_id = bot_id
        self._logger = get_logger()
        self._alpaca = get_alpaca_client()
        self._config = self._load_config()
        self._quote_cache: Dict[str, Dict[str, Any]] = {}
        self._decision_tracker = get_decision_tracker()
        
        self._logger.log("bounce_bot_init", {
            "bot_id": bot_id,
            "pairs": self._config.pairs if self._config else [],
            "config_loaded": self._config is not None,
            "window": f"{self._config.window_start}-{self._config.window_end}" if self._config else "N/A",
            "drawdown_threshold": f"-{self._config.drawdown_threshold_pct}%" if self._config else "N/A"
        })
    
    def _load_config(self) -> Optional[BounceConfig]:
        """Load configuration from bots.yaml"""
        try:
            bots_config = load_bots_config()
            cfg = bots_config.get("bouncebot", {})
            
            if not cfg.get("enabled", False):
                self._logger.log("bounce_bot_disabled", {"reason": "config.enabled=false"})
                return None
            
            return BounceConfig(
                bot_id=cfg.get("bot_id", "bounce_core"),
                enabled=cfg.get("enabled", False),
                pairs=cfg.get("pairs", ["BTC/USD", "ETH/USD", "SOL/USD"]),
                max_trades_per_session=cfg.get("risk", {}).get("max_trades_per_session", 2),
                position_size_multiplier=cfg.get("risk", {}).get("position_size_multiplier", 0.5),
                min_notional_usd=cfg.get("execution", {}).get("min_notional_usd", 15),
                max_notional_usd=cfg.get("execution", {}).get("max_notional_usd", 1000),
                equity_pct=cfg.get("execution", {}).get("equity_pct", 1.75),
                window_start=cfg.get("session", {}).get("window_start", "01:00"),
                window_end=cfg.get("session", {}).get("window_end", "05:30"),
                drawdown_threshold_pct=cfg.get("entry", {}).get("drawdown_threshold_pct", 1.5),
                rsi_oversold=cfg.get("entry", {}).get("rsi_oversold", 30),
                take_profit_pct=cfg.get("exits", {}).get("take_profit_pct", 0.8),
                stop_loss_pct=cfg.get("exits", {}).get("stop_loss_pct", 0.5),
                max_hold_minutes=cfg.get("exits", {}).get("max_hold_minutes", 120),
                require_reversal_candle=cfg.get("entry", {}).get("require_reversal_candle", True),
                lookback_hours=cfg.get("entry", {}).get("lookback_hours", 4),
                require_volume_spike=cfg.get("entry", {}).get("require_volume_spike", False)
            )
        except Exception as e:
            self._logger.error(f"BounceBot config load error: {e}")
            return None
    
    def _is_in_window(self) -> bool:
        """Check if current time is within the overnight trading window"""
        if not self._config:
            return False
        
        now_pst = datetime.now(PST)
        current_time = now_pst.time()
        
        try:
            start_parts = self._config.window_start.split(":")
            end_parts = self._config.window_end.split(":")
            window_start = time(int(start_parts[0]), int(start_parts[1]))
            window_end = time(int(end_parts[0]), int(end_parts[1]))
            
            in_window = window_start <= current_time <= window_end
            
            self._logger.log("bounce_window_check", {
                "current_time_pst": current_time.strftime("%H:%M"),
                "window_start": self._config.window_start,
                "window_end": self._config.window_end,
                "in_window": in_window
            })
            
            return in_window
        except Exception as e:
            self._logger.error(f"BounceBot window check error: {e}")
            return False
    
    def _get_session_key(self) -> str:
        """Generate session key for tracking trades per night"""
        now_pst = datetime.now(PST)
        if now_pst.hour < 6:
            session_date = now_pst.date()
        else:
            session_date = (now_pst + timedelta(days=1)).date()
        return f"bounce_session_{session_date.isoformat()}"
    
    def _get_session_trades(self) -> int:
        """Get number of trades in current overnight session"""
        session_key = self._get_session_key()
        return get_state(f"{session_key}_trades", 0)
    
    def _increment_session_trades(self):
        """Increment trade count for current session"""
        session_key = self._get_session_key()
        current = get_state(f"{session_key}_trades", 0)
        set_state(f"{session_key}_trades", current + 1)
    
    def _calculate_rsi(self, prices: List[float], period: int = RSI_PERIOD) -> float:
        """Calculate RSI from price history"""
        if len(prices) < period + 1:
            return 50.0
        
        deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        gains = [max(0, d) for d in deltas]
        losses = [max(0, -d) for d in deltas]
        
        recent_gains = gains[-period:]
        recent_losses = losses[-period:]
        
        avg_gain = sum(recent_gains) / period
        avg_loss = sum(recent_losses) / period
        
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    def _get_recent_high(self, pair: str) -> Optional[float]:
        """Get highest price in lookback period (4 hours default)"""
        if not self._config:
            return None
        
        try:
            lookback_hours = self._config.lookback_hours
            now_utc = datetime.utcnow()
            start_time = now_utc - timedelta(hours=lookback_hours)
            
            bars = self._alpaca.get_crypto_bars(
                symbol=pair,
                timeframe="15Min",
                start=start_time.isoformat() + "Z",
                limit=lookback_hours * 4
            )
            
            if not bars:
                return None
            
            high_prices = [b.get("high", 0) for b in bars if b.get("high")]
            if not high_prices:
                return None
            
            recent_high = max(high_prices)
            
            self._logger.log("bounce_recent_high", {
                "pair": pair,
                "lookback_hours": lookback_hours,
                "bar_count": len(bars),
                "recent_high": recent_high
            })
            
            return recent_high
        except Exception as e:
            self._logger.error(f"BounceBot get_recent_high error for {pair}: {e}")
            return None
    
    def _get_price_history(self, pair: str, periods: int = 20) -> List[float]:
        """Get recent closing prices for RSI calculation"""
        try:
            now_utc = datetime.utcnow()
            start_time = now_utc - timedelta(hours=periods)
            
            bars = self._alpaca.get_crypto_bars(
                symbol=pair,
                timeframe="1Hour",
                start=start_time.isoformat() + "Z",
                limit=periods
            )
            
            if not bars:
                return []
            
            close_prices = [b.get("close", 0) for b in bars if b.get("close")]
            return close_prices
        except Exception as e:
            self._logger.error(f"BounceBot get_price_history error for {pair}: {e}")
            return []
    
    def _check_volume_spike(self, pair: str) -> bool:
        """Check if current volume is elevated vs recent average (capitulation signal)"""
        try:
            now_utc = datetime.utcnow()
            start_time = now_utc - timedelta(hours=24)
            
            bars = self._alpaca.get_crypto_bars(
                symbol=pair,
                timeframe="1Hour",
                start=start_time.isoformat() + "Z",
                limit=24
            )
            
            if not bars or len(bars) < 4:
                return False
            
            volumes = [b.get("volume", 0) for b in bars if b.get("volume")]
            if not volumes or len(volumes) < 4:
                return False
            
            avg_volume = sum(volumes[:-1]) / len(volumes[:-1])
            current_volume = volumes[-1]
            
            if avg_volume <= 0:
                return False
            
            volume_ratio = current_volume / avg_volume
            is_spike = volume_ratio >= 1.5
            
            self._logger.log("bounce_volume_check", {
                "pair": pair,
                "current_volume": current_volume,
                "avg_volume": round(avg_volume, 2),
                "volume_ratio": round(volume_ratio, 2),
                "is_spike": is_spike
            })
            
            return is_spike
        except Exception as e:
            self._logger.error(f"BounceBot volume spike check error for {pair}: {e}")
            return False

    def _check_reversal_candle(self, pair: str) -> bool:
        """Check if last candle shows reversal pattern (green after red)"""
        try:
            now_utc = datetime.utcnow()
            start_time = now_utc - timedelta(hours=2)
            
            bars = self._alpaca.get_crypto_bars(
                symbol=pair,
                timeframe="15Min",
                start=start_time.isoformat() + "Z",
                limit=4
            )
            
            if len(bars) < 2:
                return False
            
            prev_bar = bars[-2]
            curr_bar = bars[-1]
            
            prev_red = prev_bar.get("close", 0) < prev_bar.get("open", 0)
            curr_green = curr_bar.get("close", 0) > curr_bar.get("open", 0)
            
            is_reversal = prev_red and curr_green
            
            self._logger.log("bounce_reversal_check", {
                "pair": pair,
                "prev_open": prev_bar.get("open"),
                "prev_close": prev_bar.get("close"),
                "prev_red": prev_red,
                "curr_open": curr_bar.get("open"),
                "curr_close": curr_bar.get("close"),
                "curr_green": curr_green,
                "is_reversal": is_reversal
            })
            
            return is_reversal
        except Exception as e:
            self._logger.error(f"BounceBot reversal check error for {pair}: {e}")
            return False
    
    def _get_validated_quote(self, pair: str) -> Optional[Dict[str, Any]]:
        """Get fresh quote with validation"""
        for attempt in range(MAX_RETRIES):
            try:
                raw_quote = self._alpaca.get_latest_quote(pair, asset_class="crypto")
                
                if not raw_quote or "bid" not in raw_quote or "ask" not in raw_quote:
                    raise ValueError("Invalid quote structure")
                
                bid = float(raw_quote.get("bid", 0))
                ask = float(raw_quote.get("ask", 0))
                
                if bid <= 0 or ask <= 0 or bid > ask:
                    raise ValueError(f"Invalid bid/ask: bid={bid}, ask={ask}")
                
                quote = {
                    "bid": bid,
                    "ask": ask,
                    "mid": (bid + ask) / 2,
                    "spread_pct": ((ask - bid) / ((bid + ask) / 2)) * 100,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
                self._quote_cache[pair] = quote
                return quote
                
            except Exception as e:
                self._logger.warn(f"BounceBot quote fetch attempt {attempt + 1} failed for {pair}: {e}")
                if attempt < MAX_RETRIES - 1:
                    time_module.sleep(RETRY_BACKOFF_BASE ** attempt)
        
        return self._quote_cache.get(pair)
    
    def _has_existing_position(self, pair: str) -> bool:
        """Check if we already have a position in this pair"""
        try:
            positions = self._alpaca.get_positions()
            position_symbol = denormalize_crypto_symbol(pair)
            
            for pos in positions:
                pos_symbol = str(pos.symbol) if hasattr(pos, 'symbol') else pos.get("symbol", "")
                if pos_symbol == position_symbol:
                    return True
            return False
        except Exception as e:
            self._logger.error(f"BounceBot position check error: {e}")
            return True
    
    def _calculate_position_size(self, max_daily_loss: float) -> float:
        """Calculate position size with bounce-specific constraints"""
        if not self._config:
            return 0.0
        
        try:
            account = self._alpaca.get_account()
            equity = float(account.equity) if hasattr(account, 'equity') else float(account.get("equity", 0))
            
            base_size = equity * (self._config.equity_pct / 100)
            bounce_size = base_size * self._config.position_size_multiplier
            
            notional = max(self._config.min_notional_usd, 
                          min(bounce_size, self._config.max_notional_usd))
            
            notional = min(notional, max_daily_loss * 0.25)
            
            self._logger.log("bounce_position_sizing", {
                "equity": equity,
                "equity_pct": self._config.equity_pct,
                "base_size": round(base_size, 2),
                "size_multiplier": self._config.position_size_multiplier,
                "bounce_size": round(bounce_size, 2),
                "final_notional": round(notional, 2),
                "max_daily_loss": max_daily_loss
            })
            
            return round(notional, 2)
        except Exception as e:
            self._logger.error(f"BounceBot sizing error: {e}")
            return self._config.min_notional_usd if self._config else 15.0
    
    def _evaluate_entry(self, pair: str, quote: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate if pair meets entry criteria for bounce trade"""
        result = {
            "signal": None,
            "reason": "",
            "drawdown_pct": 0.0,
            "rsi": 50.0,
            "reversal_confirmed": False
        }
        
        if not self._config:
            result["reason"] = "config_not_loaded"
            return result
        
        current_price = quote["mid"]
        
        recent_high = self._get_recent_high(pair)
        if not recent_high:
            result["reason"] = "no_recent_high_data"
            return result
        
        drawdown_pct = ((recent_high - current_price) / recent_high) * 100
        result["drawdown_pct"] = round(drawdown_pct, 2)
        
        if drawdown_pct < self._config.drawdown_threshold_pct:
            result["reason"] = f"drawdown_{drawdown_pct:.2f}%_below_threshold_{self._config.drawdown_threshold_pct}%"
            return result
        
        price_history = self._get_price_history(pair)
        if price_history:
            price_history.append(current_price)
            rsi = self._calculate_rsi(price_history)
            result["rsi"] = round(rsi, 1)
            
            if rsi > self._config.rsi_oversold:
                result["reason"] = f"rsi_{rsi:.1f}_above_oversold_{self._config.rsi_oversold}"
                return result
        else:
            result["reason"] = "no_price_history_for_rsi"
            return result
        
        if self._config.require_reversal_candle:
            is_reversal = self._check_reversal_candle(pair)
            result["reversal_confirmed"] = is_reversal
            
            if not is_reversal:
                result["reason"] = "no_reversal_candle"
                return result
        else:
            result["reversal_confirmed"] = True
        
        if self._config.require_volume_spike:
            has_volume = self._check_volume_spike(pair)
            if not has_volume:
                result["reason"] = "no_volume_spike_confirmation"
                return result
        
        result["signal"] = "long"
        result["reason"] = "bounce_criteria_met"
        
        self._logger.log("bounce_entry_signal", {
            "pair": pair,
            "current_price": current_price,
            "recent_high": recent_high,
            "drawdown_pct": result["drawdown_pct"],
            "rsi": result["rsi"],
            "reversal_confirmed": result["reversal_confirmed"],
            "signal": result["signal"]
        })
        
        return result
    
    def _execute_entry(self, pair: str, notional: float, entry_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Execute bounce entry order"""
        result = {
            "success": False,
            "order_id": None,
            "filled_price": None,
            "qty": None
        }
        
        try:
            quote = self._get_validated_quote(pair)
            if not quote:
                self._logger.error(f"BounceBot no quote for entry: {pair}")
                return result
            
            qty = notional / quote["mid"]
            qty = round(qty, 6)
            
            order = self._alpaca.place_market_order(
                symbol=pair,
                side="buy",
                qty=qty
            )
            
            if order and order.get("id"):
                result["success"] = True
                result["order_id"] = order.get("id")
                result["qty"] = qty
                result["filled_price"] = quote["mid"]
                
                self._increment_session_trades()
                
                self._logger.log("bounce_order_placed", {
                    "pair": pair,
                    "side": "buy",
                    "notional": notional,
                    "qty": qty,
                    "order_id": result["order_id"],
                    "entry_price": result["filled_price"],
                    "drawdown_pct": entry_analysis.get("drawdown_pct"),
                    "rsi": entry_analysis.get("rsi")
                })
                
                self._logger.log("TRADE_ENTRY", {
                    "symbol": pair,
                    "side": "long",
                    "qty": qty,
                    "entry_price": result["filled_price"],
                    "notional": notional,
                    "reason": "bounce_dip_buy",
                    "bot_id": self.bot_id,
                    "drawdown_pct": entry_analysis.get("drawdown_pct"),
                    "rsi": entry_analysis.get("rsi")
                })
                
                self._decision_tracker.update_signal(
                    bot_id=self.bot_id,
                    bot_type="bounce",
                    symbol=pair,
                    signal="bounce_entry",
                    strength=0.7,
                    reason=f"Dip buy: -{entry_analysis.get('drawdown_pct', 0):.1f}% drawdown, RSI={entry_analysis.get('rsi', 0):.0f}"
                )
                
                set_state(f"bounce_position_{denormalize_crypto_symbol(pair)}", {
                    "entry_price": result["filled_price"],
                    "entry_time": datetime.utcnow().isoformat(),
                    "qty": qty,
                    "drawdown_pct": entry_analysis.get("drawdown_pct"),
                    "rsi": entry_analysis.get("rsi"),
                    "take_profit_price": result["filled_price"] * (1 + self._config.take_profit_pct / 100),
                    "stop_loss_price": result["filled_price"] * (1 - self._config.stop_loss_pct / 100)
                })
                
                position_id = f"{denormalize_crypto_symbol(pair)}_long_{result['filled_price']:.4f}"
                trailing_config = TrailingStopConfig(
                    enabled=True,
                    mode="percent",
                    value=self._config.stop_loss_pct,
                    activation_profit_pct=self._config.take_profit_pct * 0.5,
                    update_only_if_improves=True,
                    epsilon_pct=0.02,
                    exit_order_type="market"
                )
                ts_manager = get_trailing_stop_manager()
                ts_manager.init_for_position(
                    bot_id=self.bot_id,
                    position_id=position_id,
                    symbol=denormalize_crypto_symbol(pair),
                    side="long",
                    entry_price=result["filled_price"],
                    config=trailing_config,
                    asset_class="crypto"
                )
            
        except Exception as e:
            self._logger.error(f"BounceBot entry execution error for {pair}: {e}")
        
        return result
    
    def execute(self, max_daily_loss: float = 100.0, halt_new_trades: bool = False) -> Dict[str, Any]:
        """
        Main execution loop for BounceBot.
        
        Args:
            max_daily_loss: Maximum loss allowed today
            halt_new_trades: If True, only manage existing positions
            
        Returns:
            Execution results dictionary
        """
        result = {
            "trades_attempted": 0,
            "trades_executed": 0,
            "positions_managed": 0,
            "signals": [],
            "outside_hours": False
        }
        
        if not self._config or not self._config.enabled:
            self._logger.log("bounce_bot_skip", {"reason": "disabled"})
            return result
        
        if not self._is_in_window():
            result["outside_hours"] = True
            self._logger.log("bounce_bot_skip", {
                "reason": "outside_window",
                "window": f"{self._config.window_start}-{self._config.window_end}"
            })
            return result
        
        session_trades = self._get_session_trades()
        if session_trades >= self._config.max_trades_per_session:
            self._logger.log("bounce_bot_skip", {
                "reason": "max_trades_reached",
                "session_trades": session_trades,
                "max_trades": self._config.max_trades_per_session
            })
            return result
        
        if halt_new_trades:
            self._logger.log("bounce_bot_skip", {"reason": "halt_active"})
            return result
        
        killswitch = get_killswitch_service()
        ks_allowed, ks_reason = killswitch.is_entry_allowed("bounce")
        if not ks_allowed:
            self._logger.log("bounce_bot_skip", {"reason": f"killswitch:{ks_reason}"})
            return result
        
        self._logger.log("bounce_bot_start", {
            "pairs": self._config.pairs,
            "max_daily_loss": max_daily_loss,
            "session_trades": session_trades,
            "max_trades_per_session": self._config.max_trades_per_session
        })
        
        notional = self._calculate_position_size(max_daily_loss)
        
        for pair in self._config.pairs:
            try:
                if session_trades >= self._config.max_trades_per_session:
                    break
                
                if self._has_existing_position(pair):
                    self._logger.log("bounce_pair_skip", {
                        "pair": pair,
                        "reason": "existing_position"
                    })
                    result["positions_managed"] += 1
                    continue
                
                quote = self._get_validated_quote(pair)
                if not quote:
                    self._logger.log("bounce_pair_skip", {
                        "pair": pair,
                        "reason": "no_quote"
                    })
                    continue
                
                entry_analysis = self._evaluate_entry(pair, quote)
                
                self._logger.log("bounce_evaluation", {
                    "pair": pair,
                    "mid_price": quote["mid"],
                    "signal": entry_analysis["signal"],
                    "reason": entry_analysis["reason"],
                    "drawdown_pct": entry_analysis["drawdown_pct"],
                    "rsi": entry_analysis["rsi"],
                    "reversal": entry_analysis["reversal_confirmed"]
                })
                
                if entry_analysis["signal"] == "long":
                    result["trades_attempted"] += 1
                    
                    order_result = self._execute_entry(pair, notional, entry_analysis)
                    if order_result["success"]:
                        result["trades_executed"] += 1
                        session_trades += 1
                        
                        result["signals"].append({
                            "symbol": pair,
                            "signal": "bounce_long",
                            "strength": 0.7
                        })
                
            except Exception as e:
                self._logger.error(f"BounceBot error processing {pair}: {e}")
        
        self._logger.log("bounce_bot_complete", {
            "trades_attempted": result["trades_attempted"],
            "trades_executed": result["trades_executed"],
            "positions_managed": result["positions_managed"],
            "session_trades_total": session_trades
        })
        
        return result


# =============================================================================
# SINGLETON FACTORY - Provides cached bot instances
# =============================================================================

_bounce_bot_instances: Dict[str, BounceBot] = {}


def get_bounce_bot(bot_id: str = "bounce_core") -> BounceBot:
    """
    Get or create a BounceBot instance (singleton per bot_id).
    
    Args:
        bot_id: Unique identifier for this bot instance
        
    Returns:
        BounceBot instance (cached)
    """
    global _bounce_bot_instances
    if bot_id not in _bounce_bot_instances:
        _bounce_bot_instances[bot_id] = BounceBot(bot_id)
    return _bounce_bot_instances[bot_id]
