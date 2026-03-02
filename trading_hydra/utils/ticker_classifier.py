"""
=============================================================================
Ticker Classifier - Auto-detect asset type and route to appropriate bot
=============================================================================

Classifies tickers into: CRYPTO, STOCK, ETF
Routes optimized parameters to the correct bot configuration.
"""

import re
from enum import Enum
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass


class AssetType(Enum):
    """Asset classification types."""
    CRYPTO = "crypto"
    STOCK = "stock"
    ETF = "etf"
    UNKNOWN = "unknown"


# Known ETF symbols
KNOWN_ETFS = {
    # Major index ETFs
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "IVV",
    # Sector ETFs
    "XLF", "XLE", "XLK", "XLV", "XLI", "XLP", "XLU", "XLY", "XLB", "XLRE",
    # Leveraged ETFs
    "TQQQ", "SQQQ", "UPRO", "SPXU", "TNA", "TZA", "SOXL", "SOXS",
    # Commodity ETFs
    "GLD", "SLV", "USO", "UNG", "WEAT", "CORN",
    # Bond ETFs
    "TLT", "IEF", "SHY", "LQD", "HYG", "JNK",
    # International ETFs
    "EFA", "EEM", "VEU", "VWO", "FXI", "EWJ", "EWZ",
    # Volatility ETFs
    "VXX", "UVXY", "SVXY",
    # Thematic ETFs
    "ARKK", "ARKG", "ARKW", "ARKF", "ARKQ",
    "XBI", "IBB", "HACK", "BOTZ", "ROBO",
    # Regional bank/financial
    "KRE", "KBE", "IAT",
}

# Known crypto pairs (format: BASE/QUOTE)
CRYPTO_QUOTE_CURRENCIES = {"USD", "USDT", "USDC", "BTC", "ETH", "EUR", "GBP"}


@dataclass
class TickerInfo:
    """Information about a ticker."""
    symbol: str
    asset_type: AssetType
    target_bot: str
    config_section: str
    
    def __repr__(self):
        return f"TickerInfo({self.symbol}, {self.asset_type.value}, bot={self.target_bot})"


def classify_ticker(symbol: str) -> TickerInfo:
    """
    Classify a ticker symbol into its asset type and determine target bot.
    
    Args:
        symbol: Ticker symbol (e.g., "BTC/USD", "AAPL", "SPY")
    
    Returns:
        TickerInfo with classification and routing info
    """
    symbol_upper = symbol.upper().strip()
    
    # Check for crypto pairs (contain "/" like BTC/USD)
    if "/" in symbol_upper:
        parts = symbol_upper.split("/")
        if len(parts) == 2 and parts[1] in CRYPTO_QUOTE_CURRENCIES:
            return TickerInfo(
                symbol=symbol_upper,
                asset_type=AssetType.CRYPTO,
                target_bot="cryptobot",
                config_section="cryptobot"
            )
    
    # Check for known ETFs
    base_symbol = symbol_upper.split("/")[0] if "/" in symbol_upper else symbol_upper
    if base_symbol in KNOWN_ETFS:
        return TickerInfo(
            symbol=symbol_upper,
            asset_type=AssetType.ETF,
            target_bot="twentyminute_bot",
            config_section="twentyminute_bot"
        )
    
    # Default: treat as stock
    return TickerInfo(
        symbol=symbol_upper,
        asset_type=AssetType.STOCK,
        target_bot="twentyminute_bot",
        config_section="twentyminute_bot"
    )


def classify_symbols(symbols: List[str]) -> Dict[AssetType, List[TickerInfo]]:
    """
    Classify multiple symbols and group by asset type.
    
    Args:
        symbols: List of ticker symbols
    
    Returns:
        Dictionary mapping asset type to list of TickerInfo
    """
    result = {
        AssetType.CRYPTO: [],
        AssetType.STOCK: [],
        AssetType.ETF: [],
        AssetType.UNKNOWN: []
    }
    
    for symbol in symbols:
        info = classify_ticker(symbol)
        result[info.asset_type].append(info)
    
    return result


def get_target_bot(symbols: List[str]) -> Tuple[str, str]:
    """
    Determine the primary target bot for a list of symbols.
    
    Returns the bot that handles the majority of the symbols.
    
    Args:
        symbols: List of ticker symbols
    
    Returns:
        Tuple of (bot_name, config_section)
    """
    classified = classify_symbols(symbols)
    
    # Count by target bot
    crypto_count = len(classified[AssetType.CRYPTO])
    stock_etf_count = len(classified[AssetType.STOCK]) + len(classified[AssetType.ETF])
    
    if crypto_count >= stock_etf_count:
        return "cryptobot", "cryptobot"
    else:
        return "twentyminute_bot", "twentyminute_bot"


def get_param_map_for_bot(bot_name: str) -> Dict[str, Tuple[str, ...]]:
    """
    Get the parameter mapping for a specific bot.
    
    Maps optimization parameter names to their config paths.
    
    Args:
        bot_name: Name of the bot (cryptobot, twentyminute_bot, momentum_bot)
    
    Returns:
        Dictionary mapping param names to config paths
    """
    if bot_name == "cryptobot":
        return {
            "entry_lookback": ("cryptobot", "turtle", "entry_lookback"),
            "exit_lookback": ("cryptobot", "turtle", "exit_lookback"),
            "atr_period": ("cryptobot", "turtle", "atr_period"),
            "stop_loss_pct": ("cryptobot", "exits", "stop_loss_pct"),
            "take_profit_pct": ("cryptobot", "exits", "take_profit_pct"),
            "trailing_stop_pct": ("cryptobot", "risk", "trailing_stop", "value"),
            "trailing_activation_pct": ("cryptobot", "risk", "trailing_stop", "activation_profit_pct")
        }
    elif bot_name == "twentyminute_bot":
        return {
            "stop_loss_pct": ("twentyminute_bot", "exits", "stop_loss_pct"),
            "take_profit_pct": ("twentyminute_bot", "exits", "take_profit_pct"),
            "trailing_stop_pct": ("twentyminute_bot", "exits", "trailing_stop", "value"),
            "trailing_activation_pct": ("twentyminute_bot", "exits", "trailing_stop", "activation_profit_pct"),
            "min_gap_pct": ("twentyminute_bot", "gap", "min_gap_pct"),
            "max_hold_minutes": ("twentyminute_bot", "exits", "max_hold_minutes")
        }
    elif bot_name == "momentum_bot":
        return {
            "stop_loss_pct": ("momentum_bot", "risk", "stop_loss_pct"),
            "take_profit_pct": ("momentum_bot", "risk", "take_profit_pct"),
            "trailing_stop_pct": ("momentum_bot", "risk", "trailing_stop_pct"),
            "short_sma": ("momentum_bot", "strategy", "short_sma_period"),
            "long_sma": ("momentum_bot", "strategy", "long_sma_period")
        }
    else:
        return {}


def get_symbol_profile_path(symbol: str) -> Tuple[str, ...]:
    """
    Get the config path for a symbol's profile.
    
    Args:
        symbol: Ticker symbol
    
    Returns:
        Config path tuple for this symbol's profile
    """
    # Normalize symbol for config key (replace / with _)
    safe_symbol = symbol.replace("/", "_").upper()
    return ("symbol_profiles", safe_symbol)


# Default parameters by asset type (used as starting points for optimization)
DEFAULT_PARAMS_BY_TYPE = {
    AssetType.CRYPTO: {
        "entry_lookback": 96,  # 4 days of hourly bars
        "exit_lookback": 48,
        "atr_period": 14,
        "stop_loss_pct": 2.5,
        "take_profit_pct": 5.0,
        "trailing_stop_pct": 1.5,
        "trailing_activation_pct": 1.0
    },
    AssetType.STOCK: {
        "stop_loss_pct": 0.5,
        "take_profit_pct": 1.0,
        "trailing_stop_pct": 0.3,
        "trailing_activation_pct": 0.3,
        "min_gap_pct": 0.2,
        "max_hold_minutes": 15
    },
    AssetType.ETF: {
        "stop_loss_pct": 0.4,
        "take_profit_pct": 0.8,
        "trailing_stop_pct": 0.25,
        "trailing_activation_pct": 0.25,
        "min_gap_pct": 0.15,
        "max_hold_minutes": 20
    }
}


def get_optimization_grid_for_type(asset_type: AssetType) -> Dict[str, List]:
    """
    Get appropriate optimization parameter grid for an asset type.
    
    Different asset types have different optimal parameter ranges.
    
    Args:
        asset_type: The asset classification
    
    Returns:
        Parameter grid for optimization
    """
    if asset_type == AssetType.CRYPTO:
        return {
            "entry_lookback": [48, 96, 144, 192],  # 2-8 days of hourly bars
            "exit_lookback": [24, 48, 72, 96],
            "stop_loss_pct": [1.5, 2.0, 2.5, 3.0],
            "take_profit_pct": [3.0, 4.0, 5.0, 6.0],
            "trailing_stop_pct": [1.0, 1.5, 2.0]
        }
    elif asset_type == AssetType.ETF:
        return {
            "stop_loss_pct": [0.3, 0.4, 0.5, 0.6],
            "take_profit_pct": [0.6, 0.8, 1.0, 1.2],
            "trailing_stop_pct": [0.2, 0.25, 0.3],
            "min_gap_pct": [0.1, 0.15, 0.2]
        }
    else:  # STOCK
        return {
            "stop_loss_pct": [0.4, 0.5, 0.6, 0.75],
            "take_profit_pct": [0.75, 1.0, 1.25, 1.5],
            "trailing_stop_pct": [0.25, 0.3, 0.4],
            "min_gap_pct": [0.15, 0.2, 0.25, 0.3]
        }


_OCC_PATTERN = re.compile(r'^([A-Z]{1,5})(\d{6})([CP])(\d{8})$')


@dataclass
class OptionSymbolInfo:
    """Parsed OCC option symbol components."""
    underlying: str
    expiry: str
    option_type: str
    strike: float
    raw_symbol: str


def parse_option_symbol(symbol: str) -> Optional[OptionSymbolInfo]:
    """
    Parse an OCC-format option symbol into its components.
    
    OCC format: SYMBOL + YYMMDD + C/P + strike*1000 (8 digits)
    Example: IWM260226C00262000 -> underlying=IWM, expiry=2026-02-26, type=call, strike=262.0
    
    Returns None if the symbol is not a valid option symbol.
    """
    m = _OCC_PATTERN.match(symbol.upper().strip())
    if not m:
        return None
    
    underlying = m.group(1)
    date_str = m.group(2)
    cp = m.group(3)
    strike_raw = m.group(4)
    
    try:
        yy, mm, dd = date_str[:2], date_str[2:4], date_str[4:6]
        expiry = f"20{yy}-{mm}-{dd}"
        option_type = "call" if cp == "C" else "put"
        strike = int(strike_raw) / 1000.0
    except (ValueError, IndexError):
        return None
    
    return OptionSymbolInfo(
        underlying=underlying,
        expiry=expiry,
        option_type=option_type,
        strike=strike,
        raw_symbol=symbol
    )


def extract_underlying(symbol: str) -> str:
    """
    Extract the underlying ticker from an option symbol, or return as-is for equities.
    Works for both OCC option symbols and plain tickers.
    """
    parsed = parse_option_symbol(symbol)
    if parsed:
        return parsed.underlying
    return symbol
