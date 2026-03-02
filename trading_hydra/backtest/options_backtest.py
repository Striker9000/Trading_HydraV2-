"""
Options Strategy Backtester
===========================

Simulates options spread strategies using underlying price data.
Approximates option P&L via delta exposure and time decay.

Supported Strategies:
- Long Call / Long Put
- Bull Call Spread / Bear Put Spread (debit spreads)
- Bull Put Spread / Bear Call Spread (credit spreads)
- Iron Condor

This is an approximation - real options have path-dependent Greeks,
IV changes, and liquidity issues not captured here.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum

import numpy as np


class OptionType(Enum):
    CALL = "call"
    PUT = "put"


class SpreadType(Enum):
    LONG_CALL = "long_call"
    LONG_PUT = "long_put"
    BULL_CALL_SPREAD = "bull_call_spread"
    BEAR_PUT_SPREAD = "bear_put_spread"
    BULL_PUT_SPREAD = "bull_put_spread"
    BEAR_CALL_SPREAD = "bear_call_spread"
    IRON_CONDOR = "iron_condor"
    HAIL_MARY = "hail_mary"
    GAP_AND_GO = "gap_and_go"
    GAP_AND_GO_OPTIONS = "gap_and_go_options"


@dataclass
class OptionLeg:
    """Single option leg."""
    option_type: OptionType
    strike: float
    is_long: bool
    delta: float = 0.0
    premium: float = 0.0
    quantity: int = 1


@dataclass
class SpreadPosition:
    """An options spread position."""
    spread_type: SpreadType
    underlying: str
    entry_price: float
    entry_time: datetime
    legs: List[OptionLeg]
    dte: int
    net_premium: float
    max_profit: float
    max_loss: float
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_reason: Optional[str] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0


@dataclass
class OptionsBacktestResult:
    """Results from options backtest."""
    strategy: str
    symbols: List[str]
    start_date: datetime
    end_date: datetime
    config: Dict[str, Any]
    trades: List[SpreadPosition]
    num_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    avg_trade_pnl: float = 0.0
    avg_winner: float = 0.0
    avg_loser: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0


class BlackScholes:
    """Black-Scholes option pricing for approximations."""
    
    @staticmethod
    def d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
        if T <= 0 or sigma <= 0:
            return 0.0
        return (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    
    @staticmethod
    def d2(S: float, K: float, T: float, r: float, sigma: float) -> float:
        return BlackScholes.d1(S, K, T, r, sigma) - sigma * math.sqrt(T)
    
    @staticmethod
    def norm_cdf(x: float) -> float:
        return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0
    
    @staticmethod
    def call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
        if T <= 0:
            return max(0, S - K)
        d1 = BlackScholes.d1(S, K, T, r, sigma)
        d2 = BlackScholes.d2(S, K, T, r, sigma)
        return S * BlackScholes.norm_cdf(d1) - K * math.exp(-r * T) * BlackScholes.norm_cdf(d2)
    
    @staticmethod
    def put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
        if T <= 0:
            return max(0, K - S)
        d1 = BlackScholes.d1(S, K, T, r, sigma)
        d2 = BlackScholes.d2(S, K, T, r, sigma)
        return K * math.exp(-r * T) * BlackScholes.norm_cdf(-d2) - S * BlackScholes.norm_cdf(-d1)
    
    @staticmethod
    def call_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
        if T <= 0:
            return 1.0 if S > K else 0.0
        return BlackScholes.norm_cdf(BlackScholes.d1(S, K, T, r, sigma))
    
    @staticmethod
    def put_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
        return BlackScholes.call_delta(S, K, T, r, sigma) - 1.0
    
    @staticmethod
    def theta(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
        if T <= 0:
            return 0.0
        d1 = BlackScholes.d1(S, K, T, r, sigma)
        d2 = BlackScholes.d2(S, K, T, r, sigma)
        term1 = -(S * sigma * math.exp(-d1**2 / 2) / math.sqrt(2 * math.pi)) / (2 * math.sqrt(T))
        if is_call:
            term2 = -r * K * math.exp(-r * T) * BlackScholes.norm_cdf(d2)
        else:
            term2 = r * K * math.exp(-r * T) * BlackScholes.norm_cdf(-d2)
        return (term1 + term2) / 365


class OptionsBacktester:
    """
    Backtests options strategies using underlying price data.
    
    Uses Black-Scholes for pricing approximations and delta hedging simulation.
    """
    
    def __init__(self, risk_free_rate: float = 0.05):
        self.risk_free_rate = risk_free_rate
        self.iv_default = 0.30
    
    def run_hail_mary_backtest(
        self,
        price_data: List[Dict[str, Any]],
        symbol: str,
        config: Optional[Dict[str, Any]] = None
    ) -> OptionsBacktestResult:
        """
        Backtest the hail mary strategy: buy cheap OTM options on momentum days,
        hold for profit target or let expire.
        
        Hail mary rules:
        - Entry: Buy OTM call on green days (>min_move%), put on red days
        - Strike: OTM by otm_pct of current price
        - DTE: Short-dated (configurable, default 5 days)
        - Premium: Approximated via Black-Scholes for OTM options
        - Exit: Sell at profit_target_mult × entry, or time_exit before expiry, or expire worthless
        - No stop-loss: premium paid IS the max loss
        - Max trades per day: configurable
        - Max premium filter: only enter if BS premium < max_premium
        """
        config = config or {}
        
        dte = config.get('dte', 5)
        otm_pct = config.get('otm_pct', 3.0)
        min_move_pct = config.get('min_move_pct', 0.3)
        profit_target_mult = config.get('profit_target_mult', 5.0)
        time_exit_days = config.get('time_exit_days', 1)
        max_premium = config.get('max_premium', 3.00)
        min_premium = config.get('min_premium', 0.05)
        max_trades_per_day = config.get('max_trades_per_day', 2)
        contracts_per_trade = config.get('contracts', 2)
        iv_estimate = config.get('iv', 0.35)
        lookback = config.get('entry_lookback', 3)
        
        trades: List[SpreadPosition] = []
        open_positions: List[SpreadPosition] = []
        daily_trade_count: Dict[str, int] = {}
        
        for i in range(max(1, lookback), len(price_data)):
            bar = price_data[i]
            timestamp = bar.get('timestamp', datetime.now())
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            
            price = bar['close']
            prev_price = price_data[i-1]['close']
            day_key = timestamp.strftime('%Y-%m-%d') if hasattr(timestamp, 'strftime') else str(timestamp)[:10]
            
            daily_change_pct = ((price - prev_price) / prev_price) * 100
            
            # Check and exit open positions
            positions_to_close = []
            for pos_idx, pos in enumerate(open_positions):
                days_held = (timestamp - pos.entry_time).days
                remaining_dte = max(0, pos.dte - days_held)
                
                # _calculate_position_pnl returns per-share P&L, scale by contracts * 100
                per_share_pnl = self._calculate_position_pnl(pos, price, remaining_dte, iv_estimate)
                pos.pnl = per_share_pnl * contracts_per_trade * 100
                
                entry_premium = abs(pos.net_premium)
                if entry_premium > 0:
                    # Current option value = entry premium + per-share P&L
                    current_value = entry_premium + per_share_pnl
                    profit_multiple = current_value / entry_premium
                else:
                    profit_multiple = 0
                
                exit_reason = None
                
                # EXIT 1: Profit target hit
                if profit_multiple >= profit_target_mult:
                    exit_reason = f"profit_target_{profit_target_mult}x"
                # EXIT 2: Time exit before expiry
                elif remaining_dte <= time_exit_days and remaining_dte > 0:
                    exit_reason = f"time_exit_{remaining_dte}d"
                # EXIT 3: Expired
                elif remaining_dte <= 0:
                    exit_reason = "expired"
                    # At expiry: intrinsic value only
                    for leg in pos.legs:
                        if leg.option_type == OptionType.CALL:
                            intrinsic = max(0, price - leg.strike)
                        else:
                            intrinsic = max(0, leg.strike - price)
                        pos.pnl = (intrinsic - leg.premium) * contracts_per_trade * 100
                
                if exit_reason:
                    pos.exit_price = price
                    pos.exit_time = timestamp
                    pos.exit_reason = exit_reason
                    max_loss = abs(pos.max_loss) if pos.max_loss else entry_premium
                    pos.pnl_pct = (pos.pnl / max_loss) * 100 if max_loss > 0 else 0
                    trades.append(pos)
                    positions_to_close.append(pos_idx)
            
            # Remove closed positions (reverse order to preserve indices)
            for idx in sorted(positions_to_close, reverse=True):
                open_positions.pop(idx)
            
            # Check entry conditions
            daily_count = daily_trade_count.get(day_key, 0)
            if daily_count >= max_trades_per_day:
                continue
            
            abs_move = abs(daily_change_pct)
            if abs_move < min_move_pct:
                continue
            
            # Determine direction based on momentum
            T = dte / 365.0
            r = self.risk_free_rate
            
            if daily_change_pct > 0:
                # Green day: buy OTM call
                strike = price * (1 + otm_pct / 100)
                premium = BlackScholes.call_price(price, strike, T, r, iv_estimate)
                delta = BlackScholes.call_delta(price, strike, T, r, iv_estimate)
                opt_type = OptionType.CALL
            else:
                # Red day: buy OTM put
                strike = price * (1 - otm_pct / 100)
                premium = BlackScholes.put_price(price, strike, T, r, iv_estimate)
                delta = abs(BlackScholes.put_delta(price, strike, T, r, iv_estimate))
                opt_type = OptionType.PUT
            
            # Premium filter
            if premium < min_premium or premium > max_premium:
                continue
            
            # Scale premium by contracts (each contract = 100 shares)
            total_cost = premium * contracts_per_trade * 100
            
            position = SpreadPosition(
                spread_type=SpreadType.HAIL_MARY,
                underlying=symbol,
                entry_price=price,
                entry_time=timestamp,
                legs=[OptionLeg(opt_type, strike, True, delta, premium, contracts_per_trade)],
                dte=dte,
                net_premium=-premium,
                max_profit=float('inf') if opt_type == OptionType.CALL else (strike * contracts_per_trade * 100),
                max_loss=-total_cost
            )
            
            open_positions.append(position)
            daily_trade_count[day_key] = daily_count + 1
        
        # Close any remaining open positions at end of data
        for pos in open_positions:
            pos.exit_price = price_data[-1]['close']
            pos.exit_time = price_data[-1].get('timestamp', datetime.now())
            pos.exit_reason = "end_of_data"
            trades.append(pos)
        
        return self._compile_results(
            trades, symbol, SpreadType.HAIL_MARY,
            price_data[0].get('timestamp', datetime.now()),
            price_data[-1].get('timestamp', datetime.now()),
            config
        )
    
    def run_gap_backtest(
        self,
        price_data: List[Dict[str, Any]],
        symbol: str,
        config: Optional[Dict[str, Any]] = None
    ) -> OptionsBacktestResult:
        """
        Backtest the TwentyMinuteBot gap-and-go strategy using daily OHLC bars.
        
        Simulates intraday gap continuation trades:
        - Entry: Buy at open on days with significant overnight gap
        - Direction: Trade in gap direction (continuation) or against (reversal)
        - Stop loss: Checked against daily low (longs) or high (shorts)
        - Trailing stop: Ratchets from daily high (longs) or low (shorts)
        - Exit: End of day (close price) if not stopped out
        - Options mode: Amplifies returns via near-ATM options leverage
        
        Uses daily high/low to approximate intraday stop hits.
        Conservative assumption: if stop and target both could trigger,
        assumes stop hit first (worst case).
        """
        config = config or {}
        
        min_gap_pct = config.get('min_gap_pct', 1.0)
        max_gap_pct = config.get('max_gap_pct', 15.0)
        stop_loss_pct = config.get('stop_loss_pct', 0.0)
        trailing_stop_pct = config.get('trailing_stop_pct', 0.0)
        position_size_usd = config.get('position_size_usd', 2000)
        max_trades_per_day = config.get('max_trades_per_day', 1)
        direction_mode = config.get('direction', 'continuation')
        use_options = config.get('use_options', False)
        options_delta = config.get('options_delta', 0.50)
        options_dte = config.get('options_dte', 1)
        iv_estimate = config.get('iv', 0.35)
        require_volume_spike = config.get('require_volume_spike', False)
        volume_spike_mult = config.get('volume_spike_mult', 1.5)
        volume_lookback = config.get('volume_lookback', 20)
        
        trades: List[SpreadPosition] = []
        daily_trade_count: Dict[str, int] = {}
        
        for i in range(max(1, volume_lookback), len(price_data)):
            bar = price_data[i]
            prev_bar = price_data[i - 1]
            
            timestamp = bar.get('timestamp', datetime.now())
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            
            open_price = bar['open']
            high_price = bar['high']
            low_price = bar['low']
            close_price = bar['close']
            volume = bar.get('volume', 0)
            prev_close = prev_bar['close']
            
            day_key = timestamp.strftime('%Y-%m-%d') if hasattr(timestamp, 'strftime') else str(timestamp)[:10]
            
            gap_pct = ((open_price - prev_close) / prev_close) * 100
            abs_gap = abs(gap_pct)
            
            if abs_gap < min_gap_pct or abs_gap > max_gap_pct:
                continue
            
            daily_count = daily_trade_count.get(day_key, 0)
            if daily_count >= max_trades_per_day:
                continue
            
            if require_volume_spike and i >= volume_lookback:
                avg_vol = np.mean([price_data[j].get('volume', 0) for j in range(i - volume_lookback, i)])
                if avg_vol > 0 and volume < avg_vol * volume_spike_mult:
                    continue
            
            if direction_mode == 'continuation':
                is_long = gap_pct > 0
            elif direction_mode == 'reversal':
                is_long = gap_pct < 0
            else:
                is_long = gap_pct > 0
            
            entry_price = open_price
            
            if use_options:
                T = options_dte / 365.0
                r = self.risk_free_rate
                if is_long:
                    strike = entry_price * (1 + 0.01)
                    premium = BlackScholes.call_price(entry_price, strike, T, r, iv_estimate)
                    delta = BlackScholes.call_delta(entry_price, strike, T, r, iv_estimate)
                    opt_type = OptionType.CALL
                else:
                    strike = entry_price * (1 - 0.01)
                    premium = BlackScholes.put_price(entry_price, strike, T, r, iv_estimate)
                    delta = abs(BlackScholes.put_delta(entry_price, strike, T, r, iv_estimate))
                    opt_type = OptionType.PUT
                
                if premium <= 0.01:
                    continue
                
                contracts = max(1, int(position_size_usd / (premium * 100)))
                total_cost = premium * contracts * 100
                
                intraday_move_pct = 0
                if is_long:
                    intraday_move_pct = ((close_price - open_price) / open_price) * 100
                    max_favorable = ((high_price - open_price) / open_price) * 100
                    max_adverse = ((open_price - low_price) / open_price) * 100
                else:
                    intraday_move_pct = ((open_price - close_price) / open_price) * 100
                    max_favorable = ((open_price - low_price) / open_price) * 100
                    max_adverse = ((high_price - open_price) / open_price) * 100
                
                stopped_out = False
                exit_reason = "eod_close"
                
                if stop_loss_pct > 0 and max_adverse >= stop_loss_pct:
                    stopped_out = True
                    exit_reason = f"stop_loss_{stop_loss_pct}%"
                
                remaining_T = max(0.001, T - 1/365)
                
                if stopped_out:
                    if is_long:
                        stop_underlying = open_price * (1 - stop_loss_pct / 100)
                    else:
                        stop_underlying = open_price * (1 + stop_loss_pct / 100)
                    if is_long:
                        eod_option_value = BlackScholes.call_price(stop_underlying, strike, remaining_T, r, iv_estimate)
                    else:
                        eod_option_value = BlackScholes.put_price(stop_underlying, strike, remaining_T, r, iv_estimate)
                else:
                    price_at_exit = close_price
                    if is_long:
                        eod_option_value = BlackScholes.call_price(price_at_exit, strike, remaining_T, r, iv_estimate)
                    else:
                        eod_option_value = BlackScholes.put_price(price_at_exit, strike, remaining_T, r, iv_estimate)
                
                pnl = (eod_option_value - premium) * contracts * 100
                
                position = SpreadPosition(
                    spread_type=SpreadType.GAP_AND_GO_OPTIONS,
                    underlying=symbol,
                    entry_price=entry_price,
                    entry_time=timestamp,
                    legs=[OptionLeg(opt_type, strike, True, delta, premium, contracts)],
                    dte=options_dte,
                    net_premium=-premium,
                    max_profit=float('inf'),
                    max_loss=-total_cost,
                    exit_price=close_price,
                    exit_time=timestamp,
                    exit_reason=exit_reason,
                    pnl=pnl,
                    pnl_pct=(pnl / total_cost) * 100 if total_cost > 0 else 0
                )
            else:
                shares = max(1, int(position_size_usd / entry_price))
                total_cost = shares * entry_price
                
                if is_long:
                    max_adverse = ((open_price - low_price) / open_price) * 100
                    max_favorable = ((high_price - open_price) / open_price) * 100
                else:
                    max_adverse = ((high_price - open_price) / open_price) * 100
                    max_favorable = ((open_price - low_price) / open_price) * 100
                
                exit_price = close_price
                exit_reason = "eod_close"
                
                if stop_loss_pct > 0 and max_adverse >= stop_loss_pct:
                    if is_long:
                        exit_price = open_price * (1 - stop_loss_pct / 100)
                    else:
                        exit_price = open_price * (1 + stop_loss_pct / 100)
                    exit_reason = f"stop_loss_{stop_loss_pct}%"
                elif trailing_stop_pct > 0:
                    if is_long:
                        trail_from = high_price
                        trail_stop = trail_from * (1 - trailing_stop_pct / 100)
                        if low_price <= trail_stop and trail_stop > open_price:
                            exit_price = trail_stop
                            exit_reason = f"trailing_stop_{trailing_stop_pct}%"
                    else:
                        trail_from = low_price
                        trail_stop = trail_from * (1 + trailing_stop_pct / 100)
                        if high_price >= trail_stop and trail_stop < open_price:
                            exit_price = trail_stop
                            exit_reason = f"trailing_stop_{trailing_stop_pct}%"
                
                if is_long:
                    pnl = (exit_price - entry_price) * shares
                else:
                    pnl = (entry_price - exit_price) * shares
                
                pnl_pct = (pnl / total_cost) * 100 if total_cost > 0 else 0
                
                opt_type = OptionType.CALL if is_long else OptionType.PUT
                position = SpreadPosition(
                    spread_type=SpreadType.GAP_AND_GO,
                    underlying=symbol,
                    entry_price=entry_price,
                    entry_time=timestamp,
                    legs=[OptionLeg(opt_type, entry_price, True, 1.0, entry_price, shares)],
                    dte=0,
                    net_premium=-entry_price,
                    max_profit=float('inf'),
                    max_loss=-total_cost,
                    exit_price=exit_price,
                    exit_time=timestamp,
                    exit_reason=exit_reason,
                    pnl=pnl,
                    pnl_pct=pnl_pct
                )
            
            trades.append(position)
            daily_trade_count[day_key] = daily_count + 1
        
        strategy_type = SpreadType.GAP_AND_GO_OPTIONS if use_options else SpreadType.GAP_AND_GO
        return self._compile_results(
            trades, symbol, strategy_type,
            price_data[0].get('timestamp', datetime.now()),
            price_data[-1].get('timestamp', datetime.now()),
            config
        )
    
    def run_backtest(
        self,
        price_data: List[Dict[str, Any]],
        symbol: str,
        strategy: SpreadType,
        config: Optional[Dict[str, Any]] = None
    ) -> OptionsBacktestResult:
        """
        Run options backtest on price data.
        
        Args:
            price_data: List of OHLCV bars with 'timestamp', 'open', 'high', 'low', 'close'
            symbol: Underlying symbol
            strategy: Type of spread to simulate
            config: Strategy configuration
            
        Returns:
            OptionsBacktestResult with trades and metrics
        """
        config = config or {}
        
        dte = config.get('dte', 30)
        delta_target = config.get('delta_target', 0.30)
        stop_loss_pct = config.get('stop_loss_pct', 50.0)
        take_profit_pct = config.get('take_profit_pct', 50.0)
        spread_width_pct = config.get('spread_width_pct', 2.0)
        entry_lookback = config.get('entry_lookback', 20)
        iv_estimate = config.get('iv', self.iv_default)
        
        trades: List[SpreadPosition] = []
        current_position: Optional[SpreadPosition] = None
        
        for i in range(entry_lookback, len(price_data)):
            bar = price_data[i]
            timestamp = bar.get('timestamp', datetime.now())
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            
            price = bar['close']
            
            if current_position:
                days_held = (timestamp - current_position.entry_time).days
                remaining_dte = max(0, current_position.dte - days_held)
                
                current_pnl = self._calculate_position_pnl(
                    current_position, price, remaining_dte, iv_estimate
                )
                current_position.pnl = current_pnl
                
                max_loss = abs(current_position.max_loss) if current_position.max_loss else abs(current_position.net_premium)
                if max_loss > 0:
                    pnl_pct = (current_pnl / max_loss) * 100
                else:
                    pnl_pct = 0
                
                exit_reason = None
                
                if pnl_pct <= -stop_loss_pct:
                    exit_reason = "stop_loss"
                elif pnl_pct >= take_profit_pct:
                    exit_reason = "take_profit"
                elif remaining_dte <= 1:
                    exit_reason = "expiration"
                
                if exit_reason:
                    current_position.exit_price = price
                    current_position.exit_time = timestamp
                    current_position.exit_reason = exit_reason
                    current_position.pnl_pct = pnl_pct
                    trades.append(current_position)
                    current_position = None
            
            else:
                if self._should_enter(price_data, i, entry_lookback, strategy):
                    position = self._create_position(
                        symbol, price, timestamp, strategy,
                        dte, delta_target, spread_width_pct, iv_estimate
                    )
                    if position:
                        current_position = position
        
        if current_position:
            current_position.exit_reason = "end_of_data"
            current_position.exit_time = price_data[-1].get('timestamp', datetime.now())
            current_position.exit_price = price_data[-1]['close']
            trades.append(current_position)
        
        return self._compile_results(
            trades, symbol, strategy, 
            price_data[0].get('timestamp', datetime.now()),
            price_data[-1].get('timestamp', datetime.now()),
            config
        )
    
    def _should_enter(
        self, 
        price_data: List[Dict], 
        idx: int, 
        lookback: int,
        strategy: SpreadType
    ) -> bool:
        """Determine if entry conditions are met."""
        if idx < lookback:
            return False
        
        closes = [price_data[i]['close'] for i in range(idx - lookback, idx)]
        current = price_data[idx]['close']
        
        sma = sum(closes) / len(closes)
        high = max(closes)
        low = min(closes)
        
        if strategy in (SpreadType.LONG_CALL, SpreadType.BULL_CALL_SPREAD, SpreadType.BULL_PUT_SPREAD):
            return current > sma and current > closes[-1]
        
        elif strategy in (SpreadType.LONG_PUT, SpreadType.BEAR_PUT_SPREAD, SpreadType.BEAR_CALL_SPREAD):
            return current < sma and current < closes[-1]
        
        elif strategy == SpreadType.IRON_CONDOR:
            range_pct = (high - low) / low * 100
            return range_pct < 5.0
        
        return False
    
    def _create_position(
        self,
        symbol: str,
        price: float,
        timestamp: datetime,
        strategy: SpreadType,
        dte: int,
        delta_target: float,
        spread_width_pct: float,
        iv: float
    ) -> Optional[SpreadPosition]:
        """Create a spread position."""
        T = dte / 365.0
        r = self.risk_free_rate
        spread_width = price * (spread_width_pct / 100)
        
        if strategy == SpreadType.LONG_CALL:
            strike = price * (1 + 0.02)
            premium = BlackScholes.call_price(price, strike, T, r, iv)
            delta = BlackScholes.call_delta(price, strike, T, r, iv)
            
            return SpreadPosition(
                spread_type=strategy,
                underlying=symbol,
                entry_price=price,
                entry_time=timestamp,
                legs=[OptionLeg(OptionType.CALL, strike, True, delta, premium)],
                dte=dte,
                net_premium=-premium,
                max_profit=float('inf'),
                max_loss=-premium
            )
        
        elif strategy == SpreadType.LONG_PUT:
            strike = price * (1 - 0.02)
            premium = BlackScholes.put_price(price, strike, T, r, iv)
            delta = BlackScholes.put_delta(price, strike, T, r, iv)
            
            return SpreadPosition(
                spread_type=strategy,
                underlying=symbol,
                entry_price=price,
                entry_time=timestamp,
                legs=[OptionLeg(OptionType.PUT, strike, True, delta, premium)],
                dte=dte,
                net_premium=-premium,
                max_profit=strike - premium,
                max_loss=-premium
            )
        
        elif strategy == SpreadType.BULL_CALL_SPREAD:
            long_strike = price * 1.01
            short_strike = long_strike + spread_width
            
            long_premium = BlackScholes.call_price(price, long_strike, T, r, iv)
            short_premium = BlackScholes.call_price(price, short_strike, T, r, iv)
            net_debit = long_premium - short_premium
            
            return SpreadPosition(
                spread_type=strategy,
                underlying=symbol,
                entry_price=price,
                entry_time=timestamp,
                legs=[
                    OptionLeg(OptionType.CALL, long_strike, True, 
                             BlackScholes.call_delta(price, long_strike, T, r, iv), long_premium),
                    OptionLeg(OptionType.CALL, short_strike, False,
                             BlackScholes.call_delta(price, short_strike, T, r, iv), short_premium)
                ],
                dte=dte,
                net_premium=-net_debit,
                max_profit=spread_width - net_debit,
                max_loss=-net_debit
            )
        
        elif strategy == SpreadType.BEAR_PUT_SPREAD:
            long_strike = price * 0.99
            short_strike = long_strike - spread_width
            
            long_premium = BlackScholes.put_price(price, long_strike, T, r, iv)
            short_premium = BlackScholes.put_price(price, short_strike, T, r, iv)
            net_debit = long_premium - short_premium
            
            return SpreadPosition(
                spread_type=strategy,
                underlying=symbol,
                entry_price=price,
                entry_time=timestamp,
                legs=[
                    OptionLeg(OptionType.PUT, long_strike, True,
                             BlackScholes.put_delta(price, long_strike, T, r, iv), long_premium),
                    OptionLeg(OptionType.PUT, short_strike, False,
                             BlackScholes.put_delta(price, short_strike, T, r, iv), short_premium)
                ],
                dte=dte,
                net_premium=-net_debit,
                max_profit=spread_width - net_debit,
                max_loss=-net_debit
            )
        
        elif strategy == SpreadType.BULL_PUT_SPREAD:
            short_strike = price * 0.97
            long_strike = short_strike - spread_width
            
            short_premium = BlackScholes.put_price(price, short_strike, T, r, iv)
            long_premium = BlackScholes.put_price(price, long_strike, T, r, iv)
            net_credit = short_premium - long_premium
            
            return SpreadPosition(
                spread_type=strategy,
                underlying=symbol,
                entry_price=price,
                entry_time=timestamp,
                legs=[
                    OptionLeg(OptionType.PUT, short_strike, False,
                             BlackScholes.put_delta(price, short_strike, T, r, iv), short_premium),
                    OptionLeg(OptionType.PUT, long_strike, True,
                             BlackScholes.put_delta(price, long_strike, T, r, iv), long_premium)
                ],
                dte=dte,
                net_premium=net_credit,
                max_profit=net_credit,
                max_loss=-(spread_width - net_credit)
            )
        
        elif strategy == SpreadType.BEAR_CALL_SPREAD:
            short_strike = price * 1.03
            long_strike = short_strike + spread_width
            
            short_premium = BlackScholes.call_price(price, short_strike, T, r, iv)
            long_premium = BlackScholes.call_price(price, long_strike, T, r, iv)
            net_credit = short_premium - long_premium
            
            return SpreadPosition(
                spread_type=strategy,
                underlying=symbol,
                entry_price=price,
                entry_time=timestamp,
                legs=[
                    OptionLeg(OptionType.CALL, short_strike, False,
                             BlackScholes.call_delta(price, short_strike, T, r, iv), short_premium),
                    OptionLeg(OptionType.CALL, long_strike, True,
                             BlackScholes.call_delta(price, long_strike, T, r, iv), long_premium)
                ],
                dte=dte,
                net_premium=net_credit,
                max_profit=net_credit,
                max_loss=-(spread_width - net_credit)
            )
        
        elif strategy == SpreadType.IRON_CONDOR:
            put_short = price * 0.95
            put_long = put_short - spread_width
            call_short = price * 1.05
            call_long = call_short + spread_width
            
            put_short_prem = BlackScholes.put_price(price, put_short, T, r, iv)
            put_long_prem = BlackScholes.put_price(price, put_long, T, r, iv)
            call_short_prem = BlackScholes.call_price(price, call_short, T, r, iv)
            call_long_prem = BlackScholes.call_price(price, call_long, T, r, iv)
            
            net_credit = (put_short_prem - put_long_prem) + (call_short_prem - call_long_prem)
            
            return SpreadPosition(
                spread_type=strategy,
                underlying=symbol,
                entry_price=price,
                entry_time=timestamp,
                legs=[
                    OptionLeg(OptionType.PUT, put_short, False,
                             BlackScholes.put_delta(price, put_short, T, r, iv), put_short_prem),
                    OptionLeg(OptionType.PUT, put_long, True,
                             BlackScholes.put_delta(price, put_long, T, r, iv), put_long_prem),
                    OptionLeg(OptionType.CALL, call_short, False,
                             BlackScholes.call_delta(price, call_short, T, r, iv), call_short_prem),
                    OptionLeg(OptionType.CALL, call_long, True,
                             BlackScholes.call_delta(price, call_long, T, r, iv), call_long_prem)
                ],
                dte=dte,
                net_premium=net_credit,
                max_profit=net_credit,
                max_loss=-(spread_width - net_credit)
            )
        
        return None
    
    def _calculate_position_pnl(
        self,
        position: SpreadPosition,
        current_price: float,
        remaining_dte: int,
        iv: float
    ) -> float:
        """Calculate current P&L of position."""
        T = max(0.001, remaining_dte / 365.0)
        r = self.risk_free_rate
        
        current_value = 0.0
        
        for leg in position.legs:
            if leg.option_type == OptionType.CALL:
                leg_value = BlackScholes.call_price(current_price, leg.strike, T, r, iv)
            else:
                leg_value = BlackScholes.put_price(current_price, leg.strike, T, r, iv)
            
            if leg.is_long:
                current_value += leg_value
            else:
                current_value -= leg_value
        
        entry_value = position.net_premium
        
        pnl = current_value + entry_value
        
        return pnl
    
    def _compile_results(
        self,
        trades: List[SpreadPosition],
        symbol: str,
        strategy: SpreadType,
        start_date: datetime,
        end_date: datetime,
        config: Dict[str, Any]
    ) -> OptionsBacktestResult:
        """Compile backtest statistics."""
        if not trades:
            return OptionsBacktestResult(
                strategy=strategy.value,
                symbols=[symbol],
                start_date=start_date,
                end_date=end_date,
                config=config,
                trades=[]
            )
        
        num_trades = len(trades)
        winners = [t for t in trades if t.pnl > 0]
        losers = [t for t in trades if t.pnl <= 0]
        
        win_rate = len(winners) / num_trades if num_trades > 0 else 0
        total_pnl = sum(t.pnl for t in trades)
        avg_trade = total_pnl / num_trades if num_trades > 0 else 0
        avg_winner = sum(t.pnl for t in winners) / len(winners) if winners else 0
        avg_loser = sum(t.pnl for t in losers) / len(losers) if losers else 0
        
        gross_profit = sum(t.pnl for t in winners)
        gross_loss = abs(sum(t.pnl for t in losers))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        cumulative = 0
        peak = 0
        max_dd = 0
        for t in trades:
            cumulative += t.pnl
            peak = max(peak, cumulative)
            dd = (peak - cumulative) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
        
        returns = [t.pnl_pct / 100 for t in trades if t.pnl_pct != 0]
        if len(returns) > 1:
            sharpe = (np.mean(returns) / np.std(returns)) * np.sqrt(252 / 30) if np.std(returns) > 0 else 0
        else:
            sharpe = 0
        
        initial_capital = 10000
        total_pnl_pct = (total_pnl / initial_capital) * 100
        
        return OptionsBacktestResult(
            strategy=strategy.value,
            symbols=[symbol],
            start_date=start_date,
            end_date=end_date,
            config=config,
            trades=trades,
            num_trades=num_trades,
            win_rate=win_rate,
            total_pnl=total_pnl,
            total_pnl_pct=total_pnl_pct,
            avg_trade_pnl=avg_trade,
            avg_winner=avg_winner,
            avg_loser=avg_loser,
            profit_factor=profit_factor,
            max_drawdown_pct=max_dd * 100,
            sharpe_ratio=sharpe
        )
