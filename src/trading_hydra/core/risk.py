"""Risk calculation utilities"""
from typing import Optional


def dollars_from_pct(base_amount: float, pct: float) -> float:
    return base_amount * (pct / 100.0)


def pct_from_dollars(base_amount: float, dollars: float) -> float:
    if base_amount == 0:
        return 0.0
    return (dollars / base_amount) * 100.0


def valid_budget(budget: Optional[float]) -> bool:
    if budget is None:
        return False
    return budget > 0


def position_size_from_risk(
    risk_dollars: float,
    entry_price: float,
    stop_price: float
) -> int:
    if entry_price <= 0 or stop_price <= 0:
        return 0
    
    risk_per_share = abs(entry_price - stop_price)
    if risk_per_share == 0:
        return 0
    
    shares = int(risk_dollars / risk_per_share)
    return max(0, shares)
