"""
Jeremy Bracket - Video Rules Options Execution Module
======================================================

Pure functions implementing the video rules for options execution:
- Compute underlying move from first bar
- Select liquid contract by spread/volume/OI/delta
- Compute option bracket (stop/TP) with 4-8% clamping
- Size position by daily risk budget

These functions are pure (no side effects) and can be unit tested independently.
"""

from typing import Dict, Any, Optional, List, Tuple, Callable
from dataclasses import dataclass
import math

# Optional logger type for dependency injection
LogFunc = Optional[Callable[[str, Dict[str, Any]], None]]


@dataclass
class SelectedContract:
    """Selected option contract with all relevant details."""
    symbol: str               # OCC symbol (e.g., "SPY250117C00585000")
    underlying: str           # Underlying ticker
    expiry: str               # Expiration date (YYYY-MM-DD)
    strike: float             # Strike price
    right: str                # "call" or "put"
    delta: float              # Contract delta
    bid: float                # Current bid
    ask: float                # Current ask
    mid: float                # Mid price
    spread: float             # Bid-ask spread
    volume: int               # Daily volume
    open_interest: int        # Open interest
    
    def __str__(self) -> str:
        return f"{self.underlying} {self.expiry} {self.strike}{self.right[0].upper()} Δ{self.delta:.2f}"


@dataclass
class BracketResult:
    """Computed bracket prices and sizing."""
    entry_mid: float          # Entry mid price
    stop_price: float         # Stop loss price
    tp_price: float           # Take profit price
    pct: float                # Actual percent move (clamped)
    underlying_move: float    # Underlying move in dollars
    option_move: float        # Option move in dollars
    
    def __str__(self) -> str:
        return f"Entry={self.entry_mid:.2f} SL={self.stop_price:.2f} TP={self.tp_price:.2f} ({self.pct*100:.1f}%)"


def compute_underlying_move(
    first_bar_high: float,
    first_bar_low: float,
    current_price: float,
    k_first_bar: float = 0.75,
    min_move_pct: float = 0.0008,
    max_move_pct: float = 0.0035
) -> float:
    """
    Compute expected underlying move based on first bar range.
    
    Rules (from video):
    1. first_bar_range = high_1m - low_1m
    2. raw = first_bar_range * k_first_bar
    3. clamp(raw, price*min_move_pct, price*max_move_pct)
    
    Args:
        first_bar_high: High of first 1-minute bar
        first_bar_low: Low of first 1-minute bar
        current_price: Current underlying price
        k_first_bar: Multiplier for first bar range (default 0.75)
        min_move_pct: Minimum move as percent of price (default 0.08%)
        max_move_pct: Maximum move as percent of price (default 0.35%)
    
    Returns:
        Expected underlying move in dollars
        
    Example:
        >>> compute_underlying_move(150.50, 150.00, 150.25)
        0.375  # (0.50 range * 0.75)
    """
    if first_bar_high <= first_bar_low:
        # Fallback: use minimum move
        return current_price * min_move_pct
    
    first_bar_range = first_bar_high - first_bar_low
    raw_move = first_bar_range * k_first_bar
    
    min_move = current_price * min_move_pct
    max_move = current_price * max_move_pct
    
    # Clamp to min/max bounds
    clamped_move = max(min_move, min(raw_move, max_move))
    
    return clamped_move


def select_liquid_contract(
    chain: List[Dict[str, Any]],
    direction: str,
    max_spread: float = 0.50,
    prefer_spread: float = 0.30,
    min_volume: int = 500,
    min_open_interest: int = 500,
    prefer_delta_min: float = 0.45,
    prefer_delta_max: float = 0.60,
    max_spread_pct: float = 15.0,
    expected_move_pct: float = 1.5,
    logger: LogFunc = None
) -> Optional[SelectedContract]:
    """
    Select the most liquid AND profitable contract meeting quality criteria.
    
    Filter criteria:
    1. spread <= max_spread (absolute $) AND spread_pct <= max_spread_pct (percentage)
    2. volume >= min_volume
    3. open_interest >= min_open_interest
    4. delta within prefer range (0.45-0.60 abs)
    5. Profitability: Expected gain from move must exceed spread cost
    
    Ranking (best to worst):
    1. Best profitability ratio (expected gain / cost)
    2. Lowest spread percentage
    3. Highest volume/OI
    4. Delta closest to 0.50
    
    Args:
        chain: List of option contracts from broker/data source
        direction: "LONG" or "SHORT" (determines call vs put)
        max_spread: Maximum allowed bid-ask spread in dollars
        prefer_spread: Preferred spread (tighter is better)
        min_volume: Minimum required volume
        min_open_interest: Minimum required open interest
        prefer_delta_min: Minimum preferred delta (absolute)
        prefer_delta_max: Maximum preferred delta (absolute)
        max_spread_pct: Maximum spread as percentage of mid price
        expected_move_pct: Expected underlying move percentage for profitability calc
        logger: Optional logging function (event_name, data_dict) for detailed tracking
    
    Returns:
        SelectedContract if found, None if no suitable contract
        
    Example:
        >>> chain = [{"symbol": "SPY...", "delta": 0.50, "bid": 2.45, "ask": 2.48, ...}]
        >>> contract = select_liquid_contract(chain, "LONG", max_spread_pct=10.0)
    """
    def log(event: str, data: Dict[str, Any]) -> None:
        """Internal log helper."""
        if logger:
            logger(event, data)
    
    log("options_selection_start", {
        "direction": direction,
        "chain_size": len(chain) if chain else 0,
        "filters": {
            "max_spread": max_spread,
            "max_spread_pct": max_spread_pct,
            "min_volume": min_volume,
            "min_open_interest": min_open_interest,
            "delta_range": f"{prefer_delta_min}-{prefer_delta_max}",
            "expected_move_pct": expected_move_pct
        }
    })
    
    if not chain:
        log("options_selection_empty_chain", {"reason": "No contracts in chain"})
        return None
    
    # Determine contract type based on direction
    # LONG signal -> buy call, SHORT signal -> buy put
    want_call = direction.upper() == "LONG"
    
    candidates = []
    rejected_contracts = []  # Track rejections for logging
    
    for c in chain:
        # Extract fields (handle various naming conventions)
        right = c.get("right", c.get("type", c.get("option_type", "")))
        if isinstance(right, str):
            right = right.lower()
        
        is_call = right in ("call", "c", "C")
        is_put = right in ("put", "p", "P")
        
        symbol = c.get("symbol", c.get("option_symbol", ""))[:25]
        strike = float(c.get("strike", c.get("strike_price", 0)) or 0)
        
        # Filter by contract type
        if want_call and not is_call:
            continue
        if not want_call and not is_put:
            continue
        
        # Extract prices
        bid = float(c.get("bid", c.get("bid_price", 0)) or 0)
        ask = float(c.get("ask", c.get("ask_price", 0)) or 0)
        
        if bid <= 0 or ask <= 0:
            rejected_contracts.append({"symbol": symbol, "strike": strike, "reason": "no_bid_ask", "bid": bid, "ask": ask})
            continue
        
        spread = ask - bid
        mid = (bid + ask) / 2
        spread_pct = (spread / mid) * 100 if mid > 0 else 100
        
        # Extract volume and OI early for logging
        volume = int(c.get("volume", c.get("total_volume", 0)) or 0)
        oi = int(c.get("open_interest", c.get("openInterest", c.get("oi", 0))) or 0)
        delta = float(c.get("delta", c.get("greeks", {}).get("delta", 0)) or 0)
        abs_delta = abs(delta)
        
        # Filter by absolute spread
        if spread > max_spread:
            rejected_contracts.append({
                "symbol": symbol, "strike": strike, "reason": "spread_too_wide",
                "spread": round(spread, 2), "max_spread": max_spread
            })
            continue
        
        # Filter by percentage spread (more important for high-priced options)
        if spread_pct > max_spread_pct:
            rejected_contracts.append({
                "symbol": symbol, "strike": strike, "reason": "spread_pct_too_high",
                "spread_pct": round(spread_pct, 1), "max_spread_pct": max_spread_pct
            })
            continue
        
        # Filter by liquidity
        if volume < min_volume:
            rejected_contracts.append({
                "symbol": symbol, "strike": strike, "reason": "low_volume",
                "volume": volume, "min_volume": min_volume
            })
            continue
        if oi < min_open_interest:
            rejected_contracts.append({
                "symbol": symbol, "strike": strike, "reason": "low_open_interest",
                "oi": oi, "min_oi": min_open_interest
            })
            continue
        
        # Filter by delta range
        if abs_delta < prefer_delta_min or abs_delta > prefer_delta_max:
            rejected_contracts.append({
                "symbol": symbol, "strike": strike, "reason": "delta_out_of_range",
                "delta": round(delta, 3), "range": f"{prefer_delta_min}-{prefer_delta_max}"
            })
            continue
        
        # PROFITABILITY CHECK: Expected gain must exceed spread cost
        underlying_price = float(c.get("underlying_price", c.get("stock_price", 0)) or 0)
        if underlying_price <= 0:
            underlying_price = float(c.get("strike", c.get("strike_price", mid * 10)) or mid * 10)
        
        expected_underlying_move = underlying_price * (expected_move_pct / 100)
        expected_option_gain = abs_delta * expected_underlying_move
        net_expected_profit = expected_option_gain - spread
        profit_ratio = net_expected_profit / mid if mid > 0 else 0
        
        # Skip if not profitable (spread eats more than expected gain)
        if net_expected_profit <= 0:
            rejected_contracts.append({
                "symbol": symbol, "strike": strike, "reason": "unprofitable",
                "expected_gain": round(expected_option_gain, 2),
                "spread_cost": round(spread, 2),
                "net_profit": round(net_expected_profit, 2)
            })
            continue
        
        # Extract other fields
        full_symbol = c.get("symbol", c.get("option_symbol", ""))
        underlying = c.get("underlying", c.get("underlying_symbol", ""))
        expiry = c.get("expiration_date", c.get("expiry", c.get("expiration", "")))
        
        candidates.append({
            "contract": c,
            "symbol": full_symbol,
            "underlying": underlying,
            "expiry": str(expiry),
            "strike": strike,
            "right": "call" if is_call else "put",
            "delta": delta,
            "abs_delta": abs_delta,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "spread": spread,
            "spread_pct": spread_pct,
            "volume": volume,
            "oi": oi,
            "delta_from_50": abs(abs_delta - 0.50),
            "expected_option_gain": expected_option_gain,
            "net_expected_profit": net_expected_profit,
            "profit_ratio": profit_ratio
        })
        
        # Log each passing candidate
        log("options_candidate_passed", {
            "symbol": full_symbol,
            "strike": strike,
            "expiry": str(expiry),
            "delta": round(delta, 3),
            "bid_ask": f"${bid:.2f}/${ask:.2f}",
            "spread": f"${spread:.2f} ({spread_pct:.1f}%)",
            "volume": volume,
            "oi": oi,
            "expected_gain": round(expected_option_gain, 2),
            "net_profit": round(net_expected_profit, 2),
            "profit_ratio_pct": round(profit_ratio * 100, 1)
        })
    
    # Log rejection summary
    rejection_summary = {}
    if rejected_contracts:
        # Group by reason
        for r in rejected_contracts:
            reason = r["reason"]
            if reason not in rejection_summary:
                rejection_summary[reason] = 0
            rejection_summary[reason] += 1
        
        log("options_rejection_summary", {
            "total_rejected": len(rejected_contracts),
            "by_reason": rejection_summary,
            "sample_rejections": rejected_contracts[:5]  # First 5 for debugging
        })
    
    if not candidates:
        log("options_selection_no_candidates", {
            "chain_size": len(chain),
            "all_rejected": len(rejected_contracts),
            "top_rejection_reasons": rejection_summary
        })
        return None
    
    # Rank candidates (lower score = better)
    # Weights: profitability (35%), spread_pct (25%), volume (20%), OI (10%), delta (10%)
    max_profit_ratio = max(x["profit_ratio"] for x in candidates) or 1
    max_spread_pct_seen = max(x["spread_pct"] for x in candidates) or 1
    
    for c in candidates:
        # Profitability: higher is better, invert for scoring
        profit_score = 1 - (c["profit_ratio"] / max_profit_ratio) if max_profit_ratio > 0 else 1
        
        # Spread percentage: lower is better
        spread_pct_score = c["spread_pct"] / max_spread_pct_seen
        
        # Volume/OI: higher is better, so invert
        max_vol = max(x["volume"] for x in candidates) or 1
        max_oi = max(x["oi"] for x in candidates) or 1
        volume_score = 1 - (c["volume"] / max_vol)
        oi_score = 1 - (c["oi"] / max_oi)
        
        # Delta: closer to 0.50 is better
        delta_score = c["delta_from_50"] / 0.15  # Allow wider delta range
        
        c["rank_score"] = (
            profit_score * 0.35 +
            spread_pct_score * 0.25 +
            volume_score * 0.20 +
            oi_score * 0.10 +
            delta_score * 0.10
        )
    
    # Sort by rank score (lowest = best)
    candidates.sort(key=lambda x: x["rank_score"])
    
    best = candidates[0]
    
    # Log the final selection with ranking details
    log("options_selection_complete", {
        "selected": {
            "symbol": best["symbol"],
            "strike": best["strike"],
            "expiry": best["expiry"],
            "delta": round(best["delta"], 3),
            "bid_ask": f"${best['bid']:.2f}/${best['ask']:.2f}",
            "spread": f"${best['spread']:.2f} ({best['spread_pct']:.1f}%)",
            "volume": best["volume"],
            "oi": best["oi"],
            "net_profit": round(best["net_expected_profit"], 2),
            "profit_ratio_pct": round(best["profit_ratio"] * 100, 1),
            "rank_score": round(best["rank_score"], 4)
        },
        "candidates_count": len(candidates),
        "rejected_count": len(rejected_contracts),
        "runner_ups": [
            {"symbol": c["symbol"], "strike": c["strike"], "score": round(c["rank_score"], 4)}
            for c in candidates[1:4]  # Top 3 alternatives
        ] if len(candidates) > 1 else []
    })
    
    return SelectedContract(
        symbol=best["symbol"],
        underlying=best["underlying"],
        expiry=best["expiry"],
        strike=best["strike"],
        right=best["right"],
        delta=best["delta"],
        bid=best["bid"],
        ask=best["ask"],
        mid=best["mid"],
        spread=best["spread"],
        volume=best["volume"],
        open_interest=best["oi"]
    )


def compute_option_bracket(
    entry_mid: float,
    delta: float,
    underlying_move: float,
    stop_pct_min: float = 0.04,
    stop_pct_max: float = 0.08,
    tp_pct_min: float = 0.04,
    tp_pct_max: float = 0.08
) -> BracketResult:
    """
    Compute option bracket (stop and take-profit) based on underlying move.
    
    Rules (from video):
    1. option_move = abs(delta) * underlying_move
    2. pct = option_move / entry_mid
    3. clamp pct into 4-8% range
    4. stop_price = entry_mid * (1 - pct)
    5. tp_price = entry_mid * (1 + pct)
    
    Args:
        entry_mid: Option mid price at entry
        delta: Contract delta
        underlying_move: Expected underlying move in dollars
        stop_pct_min: Minimum stop loss percent (default 4%)
        stop_pct_max: Maximum stop loss percent (default 8%)
        tp_pct_min: Minimum take profit percent (default 4%)
        tp_pct_max: Maximum take profit percent (default 8%)
    
    Returns:
        BracketResult with stop/TP prices and computed percentages
        
    Example:
        >>> result = compute_option_bracket(2.50, 0.50, 1.00)
        >>> print(result)
        Entry=2.50 SL=2.38 TP=2.62 (5.0%)
    """
    if entry_mid <= 0:
        # Fail-closed: return zeros
        return BracketResult(
            entry_mid=0,
            stop_price=0,
            tp_price=0,
            pct=0,
            underlying_move=0,
            option_move=0
        )
    
    # Compute raw option move from delta translation
    abs_delta = abs(delta)
    option_move = abs_delta * underlying_move
    
    # Convert to percent of option premium
    raw_pct = option_move / entry_mid
    
    # Clamp into 4-8% band
    clamped_pct = max(stop_pct_min, min(raw_pct, stop_pct_max))
    
    # Compute stop and TP prices
    stop_price = entry_mid * (1 - clamped_pct)
    tp_price = entry_mid * (1 + clamped_pct)
    
    return BracketResult(
        entry_mid=entry_mid,
        stop_price=round(stop_price, 2),
        tp_price=round(tp_price, 2),
        pct=clamped_pct,
        underlying_move=underlying_move,
        option_move=option_move
    )


def compute_contract_qty(
    daily_budget_usd: float,
    entry_mid: float,
    stop_price: float,
    max_contracts: int = 10
) -> int:
    """
    Compute number of contracts to trade based on risk budget.
    
    Rules:
    1. risk_per_contract = (entry_mid - stop_price) * 100
    2. qty = floor(daily_budget_usd / risk_per_contract)
    3. qty = min(qty, max_contracts)
    4. If qty < 1, return 0 (skip trade)
    
    Args:
        daily_budget_usd: Daily risk budget in dollars
        entry_mid: Option entry price (mid)
        stop_price: Stop loss price
        max_contracts: Maximum contracts allowed (default 10)
    
    Returns:
        Number of contracts (0 if trade should be skipped)
        
    Example:
        >>> compute_contract_qty(200, 2.50, 2.38, 10)
        16  # But capped at 10, so returns 10
    """
    if entry_mid <= stop_price:
        # Invalid: stop above entry
        return 0
    
    if daily_budget_usd <= 0:
        return 0
    
    # Risk per contract = (entry - stop) * 100 shares per contract
    risk_per_contract = (entry_mid - stop_price) * 100
    
    if risk_per_contract <= 0:
        return 0
    
    # Compute quantity
    raw_qty = daily_budget_usd / risk_per_contract
    qty = int(math.floor(raw_qty))
    
    # Cap at max contracts
    qty = min(qty, max_contracts)
    
    # If less than 1 contract, skip trade
    if qty < 1:
        return 0
    
    return qty


# ============================================================================
# SANITY TESTS (can be run with: python -c "from services.jeremy_bracket import *; run_sanity_tests()")
# ============================================================================

def run_sanity_tests():
    """Run quick sanity tests for all functions."""
    print("Running jeremy_bracket sanity tests...")
    
    # Test 1: compute_underlying_move
    move = compute_underlying_move(150.50, 150.00, 150.25)
    expected = 0.375  # 0.50 * 0.75
    assert abs(move - expected) < 0.01, f"compute_underlying_move: expected {expected}, got {move}"
    print(f"  ✓ compute_underlying_move: {move:.3f} (expected ~{expected})")
    
    # Test 2: compute_option_bracket with entry=2.50, pct=5%
    bracket = compute_option_bracket(2.50, 0.50, 0.25)
    assert abs(bracket.stop_price - 2.38) < 0.01, f"stop_price expected ~2.38, got {bracket.stop_price}"
    assert abs(bracket.tp_price - 2.62) < 0.02, f"tp_price expected ~2.62, got {bracket.tp_price}"
    print(f"  ✓ compute_option_bracket: {bracket}")
    
    # Test 3: compute_contract_qty
    qty = compute_contract_qty(200, 2.50, 2.38, 10)
    # risk_per_contract = (2.50 - 2.38) * 100 = $12
    # raw_qty = 200 / 12 = 16.67 -> floor = 16, capped at 10
    assert qty == 10, f"compute_contract_qty: expected 10, got {qty}"
    print(f"  ✓ compute_contract_qty: {qty} contracts")
    
    # Test 4: qty = 0 when budget insufficient
    qty_zero = compute_contract_qty(5, 2.50, 2.38, 10)
    # risk = $12, budget = $5, can't afford 1 contract
    assert qty_zero == 0, f"compute_contract_qty should return 0 for insufficient budget"
    print(f"  ✓ compute_contract_qty (insufficient): {qty_zero} (correctly 0)")
    
    print("\nAll sanity tests passed!")
    return True


if __name__ == "__main__":
    run_sanity_tests()
