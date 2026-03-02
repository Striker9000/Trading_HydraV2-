"""
Risk Engine - Pre-trade validation for trade suggestions.

Validates liquidity, spread, market regime, and risk limits.
Rejects ideas that don't meet criteria.
"""
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

from .tradeintent_schema import (
    MarketContext,
    OptionsContract,
    ValidationResult,
    ValidationStatus,
    TradeDirection,
    RouteType
)


@dataclass
class RiskLimits:
    """Risk parameters for validation."""
    max_loss_per_trade_pct: float = 1.0
    max_loss_per_trade_usd: float = 100.0
    max_position_size_pct: float = 5.0
    max_daily_loss_pct: float = 3.0
    choppy_regime_vwap_crosses: int = 5
    choppy_regime_window_minutes: int = 30
    min_relative_volume: float = 0.5
    max_spread_pct: float = 5.0


def check_liquidity(
    market_context: MarketContext,
    options_contract: Optional[OptionsContract],
    limits: RiskLimits
) -> Tuple[bool, List[str], List[str]]:
    """
    Check liquidity requirements.
    
    Returns (passed, passed_checks, failed_checks)
    """
    passed_checks = []
    failed_checks = []
    
    if market_context.relative_volume >= limits.min_relative_volume:
        passed_checks.append(
            f"Relative volume {market_context.relative_volume:.2f}x >= {limits.min_relative_volume}x"
        )
    else:
        failed_checks.append(
            f"Low relative volume {market_context.relative_volume:.2f}x < {limits.min_relative_volume}x"
        )
    
    if market_context.volume > 0:
        passed_checks.append(f"Active volume: {market_context.volume:,}")
    else:
        failed_checks.append("No trading volume detected")
    
    if options_contract:
        if options_contract.spread_pct <= limits.max_spread_pct:
            passed_checks.append(
                f"Option spread {options_contract.spread_pct:.1f}% <= {limits.max_spread_pct}%"
            )
        else:
            failed_checks.append(
                f"Option spread too wide: {options_contract.spread_pct:.1f}% > {limits.max_spread_pct}%"
            )
        
        if options_contract.volume >= 100:
            passed_checks.append(f"Option volume: {options_contract.volume}")
        else:
            failed_checks.append(f"Low option volume: {options_contract.volume} < 100")
        
        if options_contract.open_interest >= 500:
            passed_checks.append(f"Option OI: {options_contract.open_interest}")
        else:
            failed_checks.append(f"Low option OI: {options_contract.open_interest} < 500")
    
    passed = len(failed_checks) == 0
    return passed, passed_checks, failed_checks


def check_market_regime(
    market_context: MarketContext,
    limits: RiskLimits
) -> Tuple[bool, List[str], List[str], List[str]]:
    """
    Check for choppy/unfavorable market conditions.
    
    Returns (passed, passed_checks, failed_checks, warnings)
    """
    passed_checks = []
    failed_checks = []
    warnings = []
    
    if market_context.trend_bias != "neutral":
        passed_checks.append(f"Clear trend bias: {market_context.trend_bias}")
    else:
        warnings.append("Neutral trend bias - may be choppy")
    
    if market_context.atr_pct > 0.5:
        passed_checks.append(f"Adequate volatility: ATR {market_context.atr_pct:.2f}%")
    else:
        warnings.append(f"Low volatility: ATR {market_context.atr_pct:.2f}% - reduced profit potential")
    
    if market_context.atr_pct > 5.0:
        warnings.append(f"High volatility: ATR {market_context.atr_pct:.2f}% - consider reduced size")
    
    if abs(market_context.gap_pct) > 5.0:
        warnings.append(f"Large gap: {market_context.gap_pct:+.1f}% - elevated risk")
    
    if market_context.vwap:
        price_to_vwap = (market_context.current_price - market_context.vwap) / market_context.vwap * 100
        if abs(price_to_vwap) < 0.1:
            warnings.append("Price at VWAP - wait for directional confirmation")
    
    passed = len(failed_checks) == 0
    return passed, passed_checks, failed_checks, warnings


def check_risk_limits(
    market_context: MarketContext,
    direction: TradeDirection,
    position_size_pct: float,
    stop_loss_pct: float,
    account_equity: float,
    limits: RiskLimits
) -> Tuple[bool, List[str], List[str]]:
    """
    Check risk limits are not exceeded.
    
    Returns (passed, passed_checks, failed_checks)
    """
    passed_checks = []
    failed_checks = []
    
    max_risk_usd = (position_size_pct / 100) * account_equity * (stop_loss_pct / 100)
    
    if max_risk_usd <= limits.max_loss_per_trade_usd:
        passed_checks.append(f"Trade risk ${max_risk_usd:.2f} <= ${limits.max_loss_per_trade_usd}")
    else:
        failed_checks.append(
            f"Trade risk ${max_risk_usd:.2f} > max ${limits.max_loss_per_trade_usd}"
        )
    
    risk_pct = (max_risk_usd / account_equity) * 100
    if risk_pct <= limits.max_loss_per_trade_pct:
        passed_checks.append(f"Risk {risk_pct:.2f}% <= {limits.max_loss_per_trade_pct}%")
    else:
        failed_checks.append(
            f"Risk {risk_pct:.2f}% > max {limits.max_loss_per_trade_pct}%"
        )
    
    if position_size_pct <= limits.max_position_size_pct:
        passed_checks.append(f"Position size {position_size_pct}% <= {limits.max_position_size_pct}%")
    else:
        failed_checks.append(
            f"Position size {position_size_pct}% > max {limits.max_position_size_pct}%"
        )
    
    passed = len(failed_checks) == 0
    return passed, passed_checks, failed_checks


def check_options_suitability(
    options_contract: Optional[OptionsContract],
    route: RouteType
) -> Tuple[bool, List[str], List[str], List[str]]:
    """
    Check if options contract is suitable for the route.
    
    Returns (passed, passed_checks, failed_checks, warnings)
    """
    passed_checks = []
    failed_checks = []
    warnings = []
    
    if not options_contract:
        return True, [], [], []
    
    mid_price = (options_contract.bid + options_contract.ask) / 2
    theta_decay_pct = abs(options_contract.theta) / mid_price * 100 if mid_price > 0 else 0
    
    if route == RouteType.ZERO_DTE:
        if theta_decay_pct > 10:
            warnings.append(
                f"0DTE theta decay: {theta_decay_pct:.1f}% of premium per day - expect rapid decay"
            )
        if abs(options_contract.delta) < 0.3:
            warnings.append(
                f"Low delta {options_contract.delta:.2f} - reduced price sensitivity"
            )
    else:
        if theta_decay_pct > 5:
            warnings.append(
                f"High theta decay: {theta_decay_pct:.1f}% of premium per day"
            )
    
    if options_contract.iv > 0.8:
        warnings.append(f"High IV: {options_contract.iv*100:.0f}% - premium is expensive")
    elif options_contract.iv < 0.15:
        warnings.append(f"Low IV: {options_contract.iv*100:.0f}% - limited profit potential")
    
    passed = len(failed_checks) == 0
    return passed, passed_checks, failed_checks, warnings


def validate_trade_idea(
    market_context: MarketContext,
    direction: TradeDirection,
    route: RouteType,
    options_contract: Optional[OptionsContract],
    position_size_pct: float,
    stop_loss_pct: float,
    account_equity: float = 10000.0,
    risk_config: Optional[Dict[str, Any]] = None
) -> ValidationResult:
    """
    Run full pre-trade validation.
    
    Checks liquidity, regime, risk limits, and options suitability.
    """
    if risk_config:
        limits = RiskLimits(
            max_loss_per_trade_pct=risk_config.get("max_loss_per_trade_pct", 1.0),
            max_loss_per_trade_usd=risk_config.get("max_loss_per_trade_usd", 100.0),
            max_position_size_pct=risk_config.get("max_position_size_pct", 5.0),
            max_daily_loss_pct=risk_config.get("max_daily_loss_pct", 3.0),
            choppy_regime_vwap_crosses=risk_config.get("choppy_regime_vwap_crosses", 5),
            choppy_regime_window_minutes=risk_config.get("choppy_regime_window_minutes", 30)
        )
    else:
        limits = RiskLimits()
    
    all_passed = []
    all_failed = []
    all_warnings = []
    
    liq_pass, liq_passed, liq_failed = check_liquidity(
        market_context, options_contract, limits
    )
    all_passed.extend(liq_passed)
    all_failed.extend(liq_failed)
    
    reg_pass, reg_passed, reg_failed, reg_warnings = check_market_regime(
        market_context, limits
    )
    all_passed.extend(reg_passed)
    all_failed.extend(reg_failed)
    all_warnings.extend(reg_warnings)
    
    risk_pass, risk_passed, risk_failed = check_risk_limits(
        market_context, direction, position_size_pct, stop_loss_pct,
        account_equity, limits
    )
    all_passed.extend(risk_passed)
    all_failed.extend(risk_failed)
    
    opt_pass, opt_passed, opt_failed, opt_warnings = check_options_suitability(
        options_contract, route
    )
    all_passed.extend(opt_passed)
    all_failed.extend(opt_failed)
    all_warnings.extend(opt_warnings)
    
    if all_failed:
        status = ValidationStatus.REJECTED
        rejection_reason = "; ".join(all_failed[:3])
    else:
        status = ValidationStatus.APPROVED
        rejection_reason = None
    
    return ValidationResult(
        status=status,
        passed_checks=all_passed,
        failed_checks=all_failed,
        warnings=all_warnings,
        rejection_reason=rejection_reason
    )


def calculate_position_size(
    market_context: MarketContext,
    stop_loss_pct: float,
    max_risk_usd: float,
    account_equity: float
) -> float:
    """
    Calculate position size based on risk parameters.
    
    Uses fixed fractional position sizing.
    """
    if stop_loss_pct <= 0:
        return 1.0
    
    max_position_value = max_risk_usd / (stop_loss_pct / 100)
    
    position_size_pct = (max_position_value / account_equity) * 100
    
    position_size_pct = min(position_size_pct, 5.0)
    
    return round(position_size_pct, 2)
