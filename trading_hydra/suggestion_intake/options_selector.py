"""
Options Selector - Filter and rank options contracts.

Filters option chain by spread, volume, OI, and Greeks.
Returns best matching contract for the trade intent.
"""
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, date, timedelta
from dataclasses import dataclass
import os

from .tradeintent_schema import OptionsContract, TradeDirection
from .data_provider import DataProviderInterface


@dataclass
class GreeksFilter:
    """Greeks filtering parameters."""
    delta_min: float = 0.30
    delta_max: float = 0.70
    theta_max_pct: float = -5.0
    gamma_min: float = 0.01
    vega_max: float = 0.50


@dataclass
class LiquidityFilter:
    """Liquidity filtering parameters."""
    max_spread_dollars: float = 0.05
    min_spread_dollars: float = 0.02
    max_spread_pct: float = 5.0
    min_volume: int = 100
    min_open_interest: int = 500


@dataclass
class OptionCandidate:
    """Candidate option with score for ranking."""
    contract: OptionsContract
    score: float
    rejection_reasons: List[str]


def get_option_greeks(
    provider: DataProviderInterface,
    option_symbol: str,
    underlying_price: float
) -> Dict[str, float]:
    """
    Get Greeks for an option contract.
    
    Uses Alpaca options data if available, otherwise estimates.
    """
    try:
        from alpaca.data.historical import OptionHistoricalDataClient
        from alpaca.data.requests import OptionLatestQuoteRequest, OptionSnapshotRequest
        
        api_key = os.environ.get("ALPACA_API_KEY", os.environ.get("APCA_API_KEY_ID"))
        secret_key = os.environ.get("ALPACA_SECRET_KEY", os.environ.get("APCA_API_SECRET_KEY"))
        
        if api_key and secret_key:
            client = OptionHistoricalDataClient(api_key, secret_key)
            
            req = OptionSnapshotRequest(symbol_or_symbols=[option_symbol])
            snapshot = client.get_option_snapshot(req)
            
            if option_symbol in snapshot:
                snap = snapshot[option_symbol]
                greeks = snap.greeks if hasattr(snap, 'greeks') and snap.greeks else None
                
                if greeks:
                    return {
                        "delta": float(greeks.delta) if greeks.delta else 0.5,
                        "gamma": float(greeks.gamma) if greeks.gamma else 0.05,
                        "theta": float(greeks.theta) if greeks.theta else -0.05,
                        "vega": float(greeks.vega) if greeks.vega else 0.1,
                        "iv": float(snap.implied_volatility) if hasattr(snap, 'implied_volatility') and snap.implied_volatility else 0.3
                    }
    except Exception as e:
        print(f"Could not fetch Greeks for {option_symbol}: {e}")
    
    return {
        "delta": 0.50,
        "gamma": 0.05,
        "theta": -0.05,
        "vega": 0.10,
        "iv": 0.30
    }


def get_option_quote(
    provider: DataProviderInterface,
    option_symbol: str
) -> Tuple[float, float, int, int]:
    """
    Get bid/ask and volume/OI for option.
    
    Returns (bid, ask, volume, open_interest)
    """
    try:
        from alpaca.data.historical import OptionHistoricalDataClient
        from alpaca.data.requests import OptionLatestQuoteRequest, OptionSnapshotRequest
        
        api_key = os.environ.get("ALPACA_API_KEY", os.environ.get("APCA_API_KEY_ID"))
        secret_key = os.environ.get("ALPACA_SECRET_KEY", os.environ.get("APCA_API_SECRET_KEY"))
        
        if api_key and secret_key:
            client = OptionHistoricalDataClient(api_key, secret_key)
            
            req = OptionSnapshotRequest(symbol_or_symbols=[option_symbol])
            snapshot = client.get_option_snapshot(req)
            
            if option_symbol in snapshot:
                snap = snapshot[option_symbol]
                quote = snap.latest_quote if hasattr(snap, 'latest_quote') else None
                trade = snap.latest_trade if hasattr(snap, 'latest_trade') else None
                
                bid = float(quote.bid_price) if quote and quote.bid_price else 0.0
                ask = float(quote.ask_price) if quote and quote.ask_price else 0.0
                volume = int(trade.size) if trade and trade.size else 0
                oi = int(snap.open_interest) if hasattr(snap, 'open_interest') and snap.open_interest else 0
                
                return (bid, ask, volume, oi)
    except Exception as e:
        print(f"Could not fetch quote for {option_symbol}: {e}")
    
    return (0.0, 0.0, 0, 0)


def filter_options(
    provider: DataProviderInterface,
    underlying: str,
    underlying_price: float,
    direction: TradeDirection,
    expiration_start: date,
    expiration_end: date,
    greeks_filter: GreeksFilter,
    liquidity_filter: LiquidityFilter
) -> List[OptionCandidate]:
    """
    Filter option chain and return scored candidates.
    
    Applies liquidity, spread, and Greeks filters.
    """
    chain = provider.get_option_chain(underlying, expiration_start, expiration_end)
    
    if not chain:
        print(f"No option chain found for {underlying}")
        return []
    
    option_type = "call" if direction == TradeDirection.LONG else "put"
    
    if direction == TradeDirection.LONG:
        strike_min = underlying_price * 0.95
        strike_max = underlying_price * 1.10
    else:
        strike_min = underlying_price * 0.90
        strike_max = underlying_price * 1.05
    
    candidates = []
    
    for contract in chain:
        rejection_reasons = []
        
        if contract["option_type"] != option_type:
            continue
        
        strike = contract["strike"]
        if strike < strike_min or strike > strike_max:
            rejection_reasons.append(f"Strike {strike} outside range [{strike_min:.2f}, {strike_max:.2f}]")
            continue
        
        bid, ask, volume, oi = get_option_quote(provider, contract["symbol"])
        
        if bid <= 0 or ask <= 0:
            rejection_reasons.append("No valid bid/ask")
            continue
        
        spread = ask - bid
        mid = (bid + ask) / 2
        spread_pct = (spread / mid * 100) if mid > 0 else 100
        
        if spread > liquidity_filter.max_spread_dollars:
            rejection_reasons.append(f"Spread ${spread:.2f} > max ${liquidity_filter.max_spread_dollars}")
        
        if spread_pct > liquidity_filter.max_spread_pct:
            rejection_reasons.append(f"Spread {spread_pct:.1f}% > max {liquidity_filter.max_spread_pct}%")
        
        if volume < liquidity_filter.min_volume:
            rejection_reasons.append(f"Volume {volume} < min {liquidity_filter.min_volume}")
        
        if oi < liquidity_filter.min_open_interest:
            rejection_reasons.append(f"OI {oi} < min {liquidity_filter.min_open_interest}")
        
        greeks = get_option_greeks(provider, contract["symbol"], underlying_price)
        
        delta = abs(greeks["delta"])
        if delta < greeks_filter.delta_min:
            rejection_reasons.append(f"Delta {delta:.2f} < min {greeks_filter.delta_min}")
        if delta > greeks_filter.delta_max:
            rejection_reasons.append(f"Delta {delta:.2f} > max {greeks_filter.delta_max}")
        
        if greeks["gamma"] < greeks_filter.gamma_min:
            rejection_reasons.append(f"Gamma {greeks['gamma']:.3f} < min {greeks_filter.gamma_min}")
        
        theta_pct = (greeks["theta"] / mid * 100) if mid > 0 else 0
        if theta_pct < greeks_filter.theta_max_pct:
            rejection_reasons.append(f"Theta decay {theta_pct:.1f}% < max {greeks_filter.theta_max_pct}%")
        
        expiration = contract["expiration"]
        if isinstance(expiration, str):
            expiration = date.fromisoformat(expiration)
        
        dte = (expiration - date.today()).days
        expected_theta_decay = abs(greeks["theta"]) * min(dte, 1)
        
        options_contract = OptionsContract(
            symbol=contract["symbol"],
            underlying=underlying,
            strike=strike,
            expiration=expiration,
            option_type=option_type,
            bid=bid,
            ask=ask,
            spread=spread,
            spread_pct=spread_pct,
            volume=volume,
            open_interest=oi,
            delta=greeks["delta"],
            gamma=greeks["gamma"],
            theta=greeks["theta"],
            vega=greeks["vega"],
            iv=greeks["iv"],
            expected_theta_decay_1d=expected_theta_decay
        )
        
        if not rejection_reasons:
            score = calculate_option_score(options_contract, underlying_price, liquidity_filter)
        else:
            score = 0.0
        
        candidates.append(OptionCandidate(
            contract=options_contract,
            score=score,
            rejection_reasons=rejection_reasons
        ))
    
    valid_candidates = [c for c in candidates if not c.rejection_reasons]
    valid_candidates.sort(key=lambda x: x.score, reverse=True)
    
    return valid_candidates


def calculate_option_score(
    contract: OptionsContract,
    underlying_price: float,
    liquidity_filter: LiquidityFilter
) -> float:
    """
    Score an option contract (0-100).
    
    Higher scores are better. Factors:
    - Tight spreads (normalized)
    - High volume/OI
    - Delta near 0.50 (balanced risk/reward)
    - Low theta decay relative to premium
    """
    score = 100.0
    
    spread_score = max(0, 20 - (contract.spread / liquidity_filter.max_spread_dollars) * 20)
    score += spread_score
    
    volume_score = min(20, contract.volume / liquidity_filter.min_volume * 5)
    oi_score = min(20, contract.open_interest / liquidity_filter.min_open_interest * 5)
    score += volume_score + oi_score
    
    delta_optimal = 0.50
    delta_penalty = abs(abs(contract.delta) - delta_optimal) * 40
    score -= delta_penalty
    
    mid_price = (contract.bid + contract.ask) / 2
    theta_pct = abs(contract.theta) / mid_price * 100 if mid_price > 0 else 10
    theta_penalty = min(20, theta_pct * 2)
    score -= theta_penalty
    
    moneyness = contract.strike / underlying_price
    if 0.97 <= moneyness <= 1.03:
        score += 10
    
    return max(0, score)


def select_best_option(
    provider: DataProviderInterface,
    underlying: str,
    underlying_price: float,
    direction: TradeDirection,
    dte_range: Tuple[int, int],
    greeks_filter: Optional[GreeksFilter] = None,
    liquidity_filter: Optional[LiquidityFilter] = None
) -> Optional[OptionsContract]:
    """
    Select the best option contract for the trade.
    
    Returns the highest-scored valid contract or None if none pass filters.
    """
    if greeks_filter is None:
        greeks_filter = GreeksFilter()
    if liquidity_filter is None:
        liquidity_filter = LiquidityFilter()
    
    today = date.today()
    expiration_start = today + timedelta(days=dte_range[0])
    expiration_end = today + timedelta(days=dte_range[1] + 1)
    
    candidates = filter_options(
        provider=provider,
        underlying=underlying,
        underlying_price=underlying_price,
        direction=direction,
        expiration_start=expiration_start,
        expiration_end=expiration_end,
        greeks_filter=greeks_filter,
        liquidity_filter=liquidity_filter
    )
    
    if candidates:
        return candidates[0].contract
    
    return None
