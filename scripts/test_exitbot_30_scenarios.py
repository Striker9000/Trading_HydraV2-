#!/usr/bin/env python3
"""
ExitBot 30-Scenario Stress Test

Tests ExitBot's position closing logic across 30 different scenarios:
- Various asset classes (stocks, options, crypto)
- Different profit/loss levels relative to activation thresholds
- Price movements that should/shouldn't trigger exits
- Long and short positions
- Different bot owners
- Edge cases and boundary conditions

This test uses mocking to simulate positions without placing real orders.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime
import time

from src.trading_hydra.core.logging import get_logger
from src.trading_hydra.core.config import load_bots_config, load_settings
from src.trading_hydra.services.exitbot import ExitBot, PositionInfo, ExitBotResult
from src.trading_hydra.risk.trailing_stop import (
    TrailingStopManager, TrailingStopConfig, TrailingStopState,
    get_trailing_stop_manager
)


@dataclass
class TestScenario:
    """A single test scenario for ExitBot"""
    id: int
    name: str
    description: str
    position: PositionInfo
    price_sequence: List[float]  # Sequence of prices to simulate
    expected_exit: bool  # Should ExitBot trigger an exit?
    expected_armed: bool  # Should trailing stop become armed?
    activation_threshold: float  # Expected activation threshold
    trailing_pct: float  # Expected trailing stop percentage
    notes: str = ""


@dataclass 
class ScenarioResult:
    """Result from running a scenario"""
    scenario_id: int
    scenario_name: str
    passed: bool
    armed: bool
    exit_triggered: bool
    expected_exit: bool
    expected_armed: bool
    stop_price: float
    high_water: float
    final_price: float
    pnl_pct: float
    activation_threshold: float
    error: str = ""
    log_events: List[Dict] = field(default_factory=list)


class ExitBotScenarioTester:
    """Runs 30 scenarios against ExitBot logic"""
    
    def __init__(self):
        self._logger = get_logger()
        self._ts_mgr = get_trailing_stop_manager()
        self._config = load_bots_config()
        self._settings = load_settings()
        self._results: List[ScenarioResult] = []
        
    def build_scenarios(self) -> List[TestScenario]:
        """Build all 30 test scenarios"""
        scenarios = []
        ts = time.time()
        
        # =========================================
        # STOCK SCENARIOS (1-10)
        # =========================================
        
        # Scenario 1: Stock long - profit hits activation, then stop triggered
        scenarios.append(TestScenario(
            id=1,
            name="stock_long_profit_exit",
            description="Stock long position reaches +35% profit, trailing stop arms, price drops to trigger exit",
            position=PositionInfo(
                symbol="AAPL", qty=10, side="long", entry_price=100.0,
                current_price=100.0, market_value=1000.0, unrealized_pnl=0.0,
                asset_class="us_equity", position_id="AAPL_long_100.0", 
                first_seen_ts=ts, bot_id="momentum_core"
            ),
            price_sequence=[100.0, 125.0, 135.0, 140.0, 132.0, 125.0],  # +35% -> drop
            expected_exit=True,
            expected_armed=True,
            activation_threshold=30,  # Stocks: 30% activation
            trailing_pct=0.8,
            notes="Should arm at +30%, exit when price drops below trailing stop"
        ))
        
        # Scenario 2: Stock long - profit below activation threshold
        scenarios.append(TestScenario(
            id=2,
            name="stock_long_below_activation",
            description="Stock long position only reaches +20% profit, below 30% activation",
            position=PositionInfo(
                symbol="MSFT", qty=5, side="long", entry_price=200.0,
                current_price=200.0, market_value=1000.0, unrealized_pnl=0.0,
                asset_class="us_equity", position_id="MSFT_long_200.0",
                first_seen_ts=ts, bot_id="momentum_core"
            ),
            price_sequence=[200.0, 220.0, 240.0, 230.0, 210.0],  # +20% max
            expected_exit=False,  # Never arms, so no exit
            expected_armed=False,
            activation_threshold=30.0,
            trailing_pct=0.8,
            notes="Should NOT arm because profit never hits 30%"
        ))
        
        # Scenario 3: Stock long - large profit, price recovers before stop
        scenarios.append(TestScenario(
            id=3,
            name="stock_long_profit_recovery",
            description="Stock reaches +50%, dips but recovers before hitting stop",
            position=PositionInfo(
                symbol="GOOGL", qty=3, side="long", entry_price=150.0,
                current_price=150.0, market_value=450.0, unrealized_pnl=0.0,
                asset_class="us_equity", position_id="GOOGL_long_150.0",
                first_seen_ts=ts, bot_id="momentum_core"
            ),
            price_sequence=[150.0, 200.0, 225.0, 222.0, 223.0, 225.0],  # +50% peak, tiny dip within 0.8%
            expected_exit=False,  # Price doesn't drop enough (0.8% trail = 223.2 stop, lowest is 222)
            expected_armed=True,
            activation_threshold=30.0,
            trailing_pct=1.5,  # 1.5% trailing - drop from 225 to 222 is 1.3%, within tolerance
            notes="Should arm but NOT exit because price stays above stop"
        ))
        
        # Scenario 4: Stock short - profit hits activation, stop triggered
        scenarios.append(TestScenario(
            id=4,
            name="stock_short_profit_exit",
            description="Stock short position reaches +35% profit when price drops",
            position=PositionInfo(
                symbol="NFLX", qty=5, side="short", entry_price=100.0,
                current_price=100.0, market_value=500.0, unrealized_pnl=0.0,
                asset_class="us_equity", position_id="NFLX_short_100.0",
                first_seen_ts=ts, bot_id="momentum_core"
            ),
            price_sequence=[100.0, 80.0, 65.0, 70.0, 75.0, 80.0],  # Price drops 35%, then bounces
            expected_exit=True,  # Stop triggered on bounce
            expected_armed=True,
            activation_threshold=30.0,
            trailing_pct=0.8,
            notes="Short: profit when price drops, exit when price rises through stop"
        ))
        
        # Scenario 5: Stock - loss position (should not arm or exit)
        scenarios.append(TestScenario(
            id=5,
            name="stock_long_loss_no_action",
            description="Stock in loss territory - no trailing stop action",
            position=PositionInfo(
                symbol="META", qty=10, side="long", entry_price=300.0,
                current_price=300.0, market_value=3000.0, unrealized_pnl=0.0,
                asset_class="us_equity", position_id="META_long_300.0",
                first_seen_ts=ts, bot_id="manual"
            ),
            price_sequence=[300.0, 290.0, 280.0, 270.0, 265.0],  # Losing position
            expected_exit=False,
            expected_armed=False,
            activation_threshold=50.0,  # Manual trade: 50% activation
            trailing_pct=1.0,
            notes="Position in loss - trailing stop should not arm"
        ))
        
        # Scenario 6: Stock manual trade - higher activation threshold
        scenarios.append(TestScenario(
            id=6,
            name="stock_manual_high_activation",
            description="Manual stock trade with 50% activation threshold",
            position=PositionInfo(
                symbol="AMZN", qty=2, side="long", entry_price=150.0,
                current_price=150.0, market_value=300.0, unrealized_pnl=0.0,
                asset_class="us_equity", position_id="AMZN_long_150.0",
                first_seen_ts=ts, bot_id="manual"
            ),
            price_sequence=[150.0, 200.0, 225.0, 240.0, 200.0],  # +60% then crash
            expected_exit=True,
            expected_armed=True,
            activation_threshold=50.0,  # Manual trades have higher threshold
            trailing_pct=1.0,
            notes="Manual trade arms at 50%, not 30%"
        ))
        
        # Scenario 7: Stock - exactly at activation threshold
        scenarios.append(TestScenario(
            id=7,
            name="stock_exact_activation",
            description="Stock reaches exactly 30% profit",
            position=PositionInfo(
                symbol="TSLA", qty=5, side="long", entry_price=200.0,
                current_price=200.0, market_value=1000.0, unrealized_pnl=0.0,
                asset_class="us_equity", position_id="TSLA_long_200.0",
                first_seen_ts=ts, bot_id="twentymin_core"
            ),
            price_sequence=[200.0, 250.0, 260.0, 258.5, 259.0],  # +30% peak, tiny dip within 0.8%
            expected_exit=False,  # Arms but doesn't hit stop (0.8% of 260 = 257.92, lowest is 258.5)
            expected_armed=True,
            activation_threshold=30.0,
            trailing_pct=0.8,
            notes="Arms exactly at threshold, stays above stop"
        ))
        
        # Scenario 8: Stock - rapid price movement
        scenarios.append(TestScenario(
            id=8,
            name="stock_rapid_movement",
            description="Stock with rapid price swings",
            position=PositionInfo(
                symbol="AMD", qty=20, side="long", entry_price=100.0,
                current_price=100.0, market_value=2000.0, unrealized_pnl=0.0,
                asset_class="us_equity", position_id="AMD_long_100.0",
                first_seen_ts=ts, bot_id="twentymin_core"
            ),
            price_sequence=[100.0, 140.0, 145.0, 130.0, 125.0, 120.0],  # +45% then rapid drop
            expected_exit=True,
            expected_armed=True,
            activation_threshold=30.0,
            trailing_pct=0.8,
            notes="High volatility scenario - should exit on rapid drop"
        ))
        
        # Scenario 9: Stock - gradual profit growth
        scenarios.append(TestScenario(
            id=9,
            name="stock_gradual_growth",
            description="Stock with slow steady profit growth",
            position=PositionInfo(
                symbol="JNJ", qty=15, side="long", entry_price=150.0,
                current_price=150.0, market_value=2250.0, unrealized_pnl=0.0,
                asset_class="us_equity", position_id="JNJ_long_150.0",
                first_seen_ts=ts, bot_id="momentum_core"
            ),
            price_sequence=[150.0, 155.0, 165.0, 180.0, 195.0, 200.0, 210.0],  # +40%
            expected_exit=False,  # No pullback
            expected_armed=True,
            activation_threshold=30.0,
            trailing_pct=0.8,
            notes="Steady growth - should arm but no exit trigger"
        ))
        
        # Scenario 10: Stock short - never reaches profit
        scenarios.append(TestScenario(
            id=10,
            name="stock_short_no_profit",
            description="Short position where price keeps rising (loss)",
            position=PositionInfo(
                symbol="NVDA", qty=3, side="short", entry_price=500.0,
                current_price=500.0, market_value=1500.0, unrealized_pnl=0.0,
                asset_class="us_equity", position_id="NVDA_short_500.0",
                first_seen_ts=ts, bot_id="momentum_core"
            ),
            price_sequence=[500.0, 520.0, 550.0, 580.0, 600.0],  # Price rises (short loss)
            expected_exit=False,
            expected_armed=False,
            activation_threshold=30.0,
            trailing_pct=0.8,
            notes="Short losing money - should not arm"
        ))
        
        # =========================================
        # OPTIONS SCENARIOS (11-18)
        # =========================================
        
        # Scenario 11: Option long - hits 50% activation, exit triggered
        scenarios.append(TestScenario(
            id=11,
            name="option_long_profit_exit",
            description="Option reaches +55% profit, trailing stop triggers",
            position=PositionInfo(
                symbol="SPY260321C00580000", qty=2, side="long", entry_price=5.0,
                current_price=5.0, market_value=1000.0, unrealized_pnl=0.0,
                asset_class="option", position_id="SPY260321C00580000_long_5.0",
                first_seen_ts=ts, bot_id="opt_core"
            ),
            price_sequence=[5.0, 6.5, 7.75, 8.0, 7.0, 6.0],  # +60% -> drop
            expected_exit=True,
            expected_armed=True,
            activation_threshold=50.0,  # Options: 50% activation
            trailing_pct=2.0,
            notes="Option arms at +50%, exits on pullback"
        ))
        
        # Scenario 12: Option - below 50% activation
        scenarios.append(TestScenario(
            id=12,
            name="option_below_activation",
            description="Option only reaches +40% profit",
            position=PositionInfo(
                symbol="QQQ260321P00450000", qty=3, side="long", entry_price=3.0,
                current_price=3.0, market_value=900.0, unrealized_pnl=0.0,
                asset_class="option", position_id="QQQ260321P00450000_long_3.0",
                first_seen_ts=ts, bot_id="opt_core"
            ),
            price_sequence=[3.0, 3.5, 4.0, 4.2, 3.8, 3.5],  # +40% max
            expected_exit=False,
            expected_armed=False,
            activation_threshold=50.0,
            trailing_pct=2.0,
            notes="Should NOT arm - never hits 50%"
        ))
        
        # Scenario 13: Option - massive profit, then crash
        scenarios.append(TestScenario(
            id=13,
            name="option_massive_profit_crash",
            description="Option goes +200% then crashes",
            position=PositionInfo(
                symbol="TSLA260321C00400000", qty=5, side="long", entry_price=2.0,
                current_price=2.0, market_value=1000.0, unrealized_pnl=0.0,
                asset_class="option", position_id="TSLA260321C00400000_long_2.0",
                first_seen_ts=ts, bot_id="opt_0dte"
            ),
            price_sequence=[2.0, 4.0, 6.0, 5.0, 3.5, 2.5],  # +200% -> crash
            expected_exit=True,
            expected_armed=True,
            activation_threshold=50.0,
            trailing_pct=2.0,
            notes="Massive move - trailing stop should protect gains"
        ))
        
        # Scenario 14: Option - 0DTE rapid expiry simulation
        scenarios.append(TestScenario(
            id=14,
            name="option_0dte_rapid",
            description="0DTE option with rapid price decay",
            position=PositionInfo(
                symbol="SPY260121C00580000", qty=10, side="long", entry_price=1.0,
                current_price=1.0, market_value=1000.0, unrealized_pnl=0.0,
                asset_class="option", position_id="SPY260121C00580000_long_1.0",
                first_seen_ts=ts, bot_id="opt_0dte"
            ),
            price_sequence=[1.0, 1.8, 1.6, 1.3, 1.0, 0.5],  # +80% then decay
            expected_exit=True,
            expected_armed=True,
            activation_threshold=50.0,
            trailing_pct=2.0,
            notes="0DTE time decay - should exit fast"
        ))
        
        # Scenario 15: Option put - profit on underlying drop
        scenarios.append(TestScenario(
            id=15,
            name="option_put_profit",
            description="Put option profits as underlying drops",
            position=PositionInfo(
                symbol="IWM260321P00200000", qty=4, side="long", entry_price=4.0,
                current_price=4.0, market_value=1600.0, unrealized_pnl=0.0,
                asset_class="option", position_id="IWM260321P00200000_long_4.0",
                first_seen_ts=ts, bot_id="opt_core"
            ),
            price_sequence=[4.0, 5.0, 6.5, 7.0, 5.5, 4.5],  # +75% then pullback
            expected_exit=True,
            expected_armed=True,
            activation_threshold=50.0,
            trailing_pct=2.0,
            notes="Put profit scenario"
        ))
        
        # Scenario 16: Option - slow grind higher
        scenarios.append(TestScenario(
            id=16,
            name="option_slow_grind",
            description="Option slowly grinds to +60%",
            position=PositionInfo(
                symbol="AAPL260321C00200000", qty=2, side="long", entry_price=10.0,
                current_price=10.0, market_value=2000.0, unrealized_pnl=0.0,
                asset_class="option", position_id="AAPL260321C00200000_long_10.0",
                first_seen_ts=ts, bot_id="opt_core"
            ),
            price_sequence=[10.0, 11.0, 12.0, 13.5, 15.0, 16.0, 15.7],  # +60%, tiny dip within 2% trail
            expected_exit=False,  # 2% of 16 = 15.68 stop, lowest is 15.7 - no exit
            expected_armed=True,
            activation_threshold=50.0,
            trailing_pct=2.0,
            notes="Slow move - arms but doesn't exit"
        ))
        
        # Scenario 17: Option - theta burn scenario
        scenarios.append(TestScenario(
            id=17,
            name="option_theta_burn",
            description="Option loses value due to time decay",
            position=PositionInfo(
                symbol="META260321C00550000", qty=3, side="long", entry_price=8.0,
                current_price=8.0, market_value=2400.0, unrealized_pnl=0.0,
                asset_class="option", position_id="META260321C00550000_long_8.0",
                first_seen_ts=ts, bot_id="opt_core"
            ),
            price_sequence=[8.0, 7.5, 7.0, 6.5, 6.0, 5.5],  # Slow decay
            expected_exit=False,  # Never profitable, never arms
            expected_armed=False,
            activation_threshold=50.0,
            trailing_pct=2.0,
            notes="Time decay - never arms (always losing)"
        ))
        
        # Scenario 18: Option - just above activation
        scenarios.append(TestScenario(
            id=18,
            name="option_just_above_activation",
            description="Option reaches exactly 51% profit",
            position=PositionInfo(
                symbol="NVDA260321C00800000", qty=1, side="long", entry_price=20.0,
                current_price=20.0, market_value=2000.0, unrealized_pnl=0.0,
                asset_class="option", position_id="NVDA260321C00800000_long_20.0",
                first_seen_ts=ts, bot_id="opt_core"
            ),
            price_sequence=[20.0, 25.0, 30.2, 29.7, 29.8, 30.0],  # +51% -> tiny dip within 2%
            expected_exit=False,  # Arms but dip within 2% trail (2% of 30.2 = 29.6 stop, lowest is 29.7)
            expected_armed=True,
            activation_threshold=50.0,
            trailing_pct=2.0,
            notes="Just crosses activation, should arm"
        ))
        
        # =========================================
        # CRYPTO SCENARIOS (19-26)
        # =========================================
        
        # Scenario 19: Crypto BTC - hits 15% activation, exit triggered
        scenarios.append(TestScenario(
            id=19,
            name="crypto_btc_profit_exit",
            description="BTC reaches +18% profit, trailing stop triggers",
            position=PositionInfo(
                symbol="BTCUSD", qty=0.01, side="long", entry_price=90000.0,
                current_price=90000.0, market_value=900.0, unrealized_pnl=0.0,
                asset_class="crypto", position_id="BTCUSD_long_90000.0",
                first_seen_ts=ts, bot_id="crypto_core"
            ),
            price_sequence=[90000, 100000, 106200, 103000, 98000, 95000],  # +18% -> drop
            expected_exit=True,
            expected_armed=True,
            activation_threshold=15.0,  # Crypto: 15% activation
            trailing_pct=1.5,
            notes="BTC arms at +15%, exits on pullback"
        ))
        
        # Scenario 20: Crypto ETH - below activation
        scenarios.append(TestScenario(
            id=20,
            name="crypto_eth_below_activation",
            description="ETH only reaches +10% profit",
            position=PositionInfo(
                symbol="ETHUSD", qty=0.5, side="long", entry_price=3000.0,
                current_price=3000.0, market_value=1500.0, unrealized_pnl=0.0,
                asset_class="crypto", position_id="ETHUSD_long_3000.0",
                first_seen_ts=ts, bot_id="crypto_core"
            ),
            price_sequence=[3000, 3200, 3300, 3250, 3100],  # +10% max
            expected_exit=False,
            expected_armed=False,
            activation_threshold=15.0,
            trailing_pct=1.5,
            notes="Should NOT arm - never hits 15%"
        ))
        
        # Scenario 21: Crypto SOL - volatile movement
        scenarios.append(TestScenario(
            id=21,
            name="crypto_sol_volatile",
            description="SOL with high volatility",
            position=PositionInfo(
                symbol="SOLUSD", qty=10, side="long", entry_price=150.0,
                current_price=150.0, market_value=1500.0, unrealized_pnl=0.0,
                asset_class="crypto", position_id="SOLUSD_long_150.0",
                first_seen_ts=ts, bot_id="crypto_core"
            ),
            price_sequence=[150, 180, 200, 175, 160, 145],  # +33% then crash
            expected_exit=True,
            expected_armed=True,
            activation_threshold=15.0,
            trailing_pct=1.5,
            notes="High vol crypto - trailing stop protects"
        ))
        
        # Scenario 22: Crypto DOGE - meme pump and dump
        scenarios.append(TestScenario(
            id=22,
            name="crypto_doge_pump_dump",
            description="DOGE pump and dump pattern",
            position=PositionInfo(
                symbol="DOGEUSD", qty=1000, side="long", entry_price=0.15,
                current_price=0.15, market_value=150.0, unrealized_pnl=0.0,
                asset_class="crypto", position_id="DOGEUSD_long_0.15",
                first_seen_ts=ts, bot_id="crypto_core"
            ),
            price_sequence=[0.15, 0.20, 0.25, 0.22, 0.18, 0.14],  # +67% then dump
            expected_exit=True,
            expected_armed=True,
            activation_threshold=15.0,
            trailing_pct=1.5,
            notes="Meme coin volatility"
        ))
        
        # Scenario 23: Crypto short (if supported)
        scenarios.append(TestScenario(
            id=23,
            name="crypto_short_profit",
            description="Crypto short position profits",
            position=PositionInfo(
                symbol="BTCUSD", qty=0.01, side="short", entry_price=100000.0,
                current_price=100000.0, market_value=1000.0, unrealized_pnl=0.0,
                asset_class="crypto", position_id="BTCUSD_short_100000.0",
                first_seen_ts=ts, bot_id="crypto_core"
            ),
            price_sequence=[100000, 90000, 85000, 88000, 92000, 95000],  # Price drops then bounces
            expected_exit=True,
            expected_armed=True,
            activation_threshold=15.0,
            trailing_pct=1.5,
            notes="Crypto short - exit on bounce"
        ))
        
        # Scenario 24: Crypto - sideways movement
        scenarios.append(TestScenario(
            id=24,
            name="crypto_sideways",
            description="Crypto consolidates sideways",
            position=PositionInfo(
                symbol="LTCUSD", qty=5, side="long", entry_price=100.0,
                current_price=100.0, market_value=500.0, unrealized_pnl=0.0,
                asset_class="crypto", position_id="LTCUSD_long_100.0",
                first_seen_ts=ts, bot_id="crypto_core"
            ),
            price_sequence=[100, 102, 98, 101, 99, 100],  # Sideways
            expected_exit=False,
            expected_armed=False,
            activation_threshold=15.0,
            trailing_pct=1.5,
            notes="Range-bound - no action"
        ))
        
        # Scenario 25: Crypto AVAX - steady climb
        scenarios.append(TestScenario(
            id=25,
            name="crypto_avax_climb",
            description="AVAX steady climb no pullback",
            position=PositionInfo(
                symbol="AVAXUSD", qty=20, side="long", entry_price=40.0,
                current_price=40.0, market_value=800.0, unrealized_pnl=0.0,
                asset_class="crypto", position_id="AVAXUSD_long_40.0",
                first_seen_ts=ts, bot_id="crypto_core"
            ),
            price_sequence=[40, 44, 48, 52, 56, 60],  # +50% climb
            expected_exit=False,  # No pullback
            expected_armed=True,
            activation_threshold=15.0,
            trailing_pct=1.5,
            notes="Steady climb - arms but no exit"
        ))
        
        # Scenario 26: Crypto - flash crash recovery
        scenarios.append(TestScenario(
            id=26,
            name="crypto_flash_crash",
            description="Crypto flash crash then recovery",
            position=PositionInfo(
                symbol="LINKUSD", qty=50, side="long", entry_price=20.0,
                current_price=20.0, market_value=1000.0, unrealized_pnl=0.0,
                asset_class="crypto", position_id="LINKUSD_long_20.0",
                first_seen_ts=ts, bot_id="crypto_core"
            ),
            price_sequence=[20, 25, 26, 15, 22, 24],  # +30% then flash to -25%
            expected_exit=True,  # Flash crash triggers exit
            expected_armed=True,
            activation_threshold=15.0,
            trailing_pct=1.5,
            notes="Flash crash should trigger immediate exit"
        ))
        
        # =========================================
        # EDGE CASES (27-30)
        # =========================================
        
        # Scenario 27: Zero quantity edge case
        scenarios.append(TestScenario(
            id=27,
            name="edge_zero_qty",
            description="Position with near-zero quantity",
            position=PositionInfo(
                symbol="AAPL", qty=0.001, side="long", entry_price=200.0,
                current_price=200.0, market_value=0.2, unrealized_pnl=0.0,
                asset_class="us_equity", position_id="AAPL_long_200.0_micro",
                first_seen_ts=ts, bot_id="manual"
            ),
            price_sequence=[200, 280, 300, 260, 220],  # +50% then drop
            expected_exit=True,
            expected_armed=True,
            activation_threshold=50.0,
            trailing_pct=1.0,
            notes="Micro position - should still work"
        ))
        
        # Scenario 28: Very high price asset
        scenarios.append(TestScenario(
            id=28,
            name="edge_high_price",
            description="BRK.A class - very high price",
            position=PositionInfo(
                symbol="BRK.A", qty=0.1, side="long", entry_price=700000.0,
                current_price=700000.0, market_value=70000.0, unrealized_pnl=0.0,
                asset_class="us_equity", position_id="BRK.A_long_700000.0",
                first_seen_ts=ts, bot_id="manual"
            ),
            price_sequence=[700000, 800000, 900000, 1100000, 950000, 850000],  # +57% then drop
            expected_exit=True,
            expected_armed=True,
            activation_threshold=50.0,
            trailing_pct=1.0,
            notes="High price stock - math should handle"
        ))
        
        # Scenario 29: Penny stock
        scenarios.append(TestScenario(
            id=29,
            name="edge_penny_stock",
            description="Sub-dollar penny stock",
            position=PositionInfo(
                symbol="PENNY", qty=10000, side="long", entry_price=0.05,
                current_price=0.05, market_value=500.0, unrealized_pnl=0.0,
                asset_class="us_equity", position_id="PENNY_long_0.05",
                first_seen_ts=ts, bot_id="manual"
            ),
            price_sequence=[0.05, 0.06, 0.08, 0.10, 0.08, 0.06],  # +100% then crash
            expected_exit=True,
            expected_armed=True,
            activation_threshold=50.0,
            trailing_pct=1.0,
            notes="Penny stock - high pct moves"
        ))
        
        # Scenario 30: Multiple bot owner types
        scenarios.append(TestScenario(
            id=30,
            name="edge_unknown_bot",
            description="Position with unknown bot owner",
            position=PositionInfo(
                symbol="XYZ", qty=100, side="long", entry_price=50.0,
                current_price=50.0, market_value=5000.0, unrealized_pnl=0.0,
                asset_class="us_equity", position_id="XYZ_long_50.0",
                first_seen_ts=ts, bot_id="unknown_bot"
            ),
            price_sequence=[50, 65, 80, 85, 70, 60],  # +70% then drop
            expected_exit=True,
            expected_armed=True,
            activation_threshold=50.0,  # Falls back to default (manual)
            trailing_pct=1.0,
            notes="Unknown bot - uses default config"
        ))
        
        return scenarios
    
    def run_scenario(self, scenario: TestScenario) -> ScenarioResult:
        """Run a single scenario and collect results"""
        self._logger.log("exitbot_scenario_start", {
            "id": scenario.id,
            "name": scenario.name,
            "symbol": scenario.position.symbol,
            "asset_class": scenario.position.asset_class,
            "side": scenario.position.side,
            "entry_price": scenario.position.entry_price
        })
        
        try:
            # Get appropriate trailing stop config
            config = self._get_config_for_scenario(scenario)
            
            # Initialize trailing stop state
            # Note: activation_profit_pct is compared directly with profit_pct (0-100 scale)
            state = TrailingStopState(
                side=scenario.position.side,
                entry_price=scenario.position.entry_price,
                armed=False,
                high_water=scenario.position.entry_price,
                low_water=scenario.position.entry_price,
                stop_price=0.0,
                config={
                    "mode": "percent",
                    "value": scenario.trailing_pct,
                    "activation_profit_pct": scenario.activation_threshold,  # Use raw percentage value
                    "update_only_if_improves": True,
                    "epsilon_pct": 0.02
                }
            )
            
            ts_config = TrailingStopConfig(
                enabled=True,
                mode="percent",
                value=scenario.trailing_pct,
                activation_profit_pct=scenario.activation_threshold,  # Use raw percentage value
                update_only_if_improves=True,
                epsilon_pct=0.02
            )
            
            # Process price sequence
            exit_triggered = False
            log_events = []
            
            for i, price in enumerate(scenario.price_sequence):
                # Calculate P&L
                if scenario.position.side == "long":
                    pnl_pct = ((price - scenario.position.entry_price) / scenario.position.entry_price) * 100
                else:
                    pnl_pct = ((scenario.position.entry_price - price) / scenario.position.entry_price) * 100
                
                # Update trailing stop state
                state = self._ts_mgr.update_state(
                    bot_id=scenario.position.bot_id,
                    position_id=scenario.position.position_id,
                    symbol=scenario.position.symbol,
                    asset_class=scenario.position.asset_class,
                    current_price=price,
                    state=state
                )
                
                # Log state
                log_event = {
                    "step": i,
                    "price": price,
                    "pnl_pct": round(pnl_pct, 2),
                    "armed": state.armed,
                    "stop_price": round(state.stop_price, 4) if state.stop_price else 0,
                    "high_water": round(state.high_water, 4),
                    "low_water": round(state.low_water, 4)
                }
                log_events.append(log_event)
                
                self._logger.log("exitbot_scenario_price_update", {
                    "scenario_id": scenario.id,
                    **log_event
                })
                
                # Check for exit
                if state.armed and state.stop_price > 0:
                    should_exit = self._ts_mgr.should_exit(state, price)
                    if should_exit:
                        exit_triggered = True
                        self._logger.log("exitbot_scenario_exit_triggered", {
                            "scenario_id": scenario.id,
                            "step": i,
                            "price": price,
                            "stop_price": state.stop_price,
                            "pnl_pct": round(pnl_pct, 2)
                        })
                        break
            
            # Final state
            final_price = scenario.price_sequence[-1]
            if scenario.position.side == "long":
                final_pnl_pct = ((final_price - scenario.position.entry_price) / scenario.position.entry_price) * 100
            else:
                final_pnl_pct = ((scenario.position.entry_price - final_price) / scenario.position.entry_price) * 100
            
            # Check if scenario passed
            passed = (exit_triggered == scenario.expected_exit) and (state.armed == scenario.expected_armed)
            
            result = ScenarioResult(
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                passed=passed,
                armed=state.armed,
                exit_triggered=exit_triggered,
                expected_exit=scenario.expected_exit,
                expected_armed=scenario.expected_armed,
                stop_price=state.stop_price,
                high_water=state.high_water,
                final_price=final_price,
                pnl_pct=final_pnl_pct,
                activation_threshold=scenario.activation_threshold,
                log_events=log_events
            )
            
            self._logger.log("exitbot_scenario_complete", {
                "scenario_id": scenario.id,
                "passed": passed,
                "armed": state.armed,
                "expected_armed": scenario.expected_armed,
                "exit_triggered": exit_triggered,
                "expected_exit": scenario.expected_exit,
                "stop_price": round(state.stop_price, 4),
                "final_pnl_pct": round(final_pnl_pct, 2)
            })
            
            return result
            
        except Exception as e:
            self._logger.error(f"Scenario {scenario.id} error: {e}")
            return ScenarioResult(
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                passed=False,
                armed=False,
                exit_triggered=False,
                expected_exit=scenario.expected_exit,
                expected_armed=scenario.expected_armed,
                stop_price=0,
                high_water=0,
                final_price=0,
                pnl_pct=0,
                activation_threshold=scenario.activation_threshold,
                error=str(e)
            )
    
    def _get_config_for_scenario(self, scenario: TestScenario) -> Dict:
        """Get trailing stop config for a scenario based on asset class/bot"""
        # This returns the expected config values for logging
        return {
            "activation_pct": scenario.activation_threshold,
            "trailing_pct": scenario.trailing_pct
        }
    
    def run_all(self) -> List[ScenarioResult]:
        """Run all 30 scenarios"""
        scenarios = self.build_scenarios()
        
        print("=" * 80)
        print("EXITBOT 30-SCENARIO STRESS TEST")
        print("=" * 80)
        print(f"Testing {len(scenarios)} scenarios...\n")
        
        self._logger.log("exitbot_scenario_test_start", {
            "total_scenarios": len(scenarios)
        })
        
        results = []
        for scenario in scenarios:
            result = self.run_scenario(scenario)
            results.append(result)
            
            # Print result
            status = "✅ PASS" if result.passed else "❌ FAIL"
            print(f"[{scenario.id:02d}] {status} {scenario.name}")
            print(f"     {scenario.description}")
            print(f"     Armed: {result.armed} (expected: {result.expected_armed})")
            print(f"     Exit: {result.exit_triggered} (expected: {result.expected_exit})")
            print(f"     Final P&L: {result.pnl_pct:+.2f}%, Stop: {result.stop_price:.4f}")
            if result.error:
                print(f"     ERROR: {result.error}")
            print()
        
        # Summary
        passed = sum(1 for r in results if r.passed)
        failed = sum(1 for r in results if not r.passed)
        
        print("=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"Total: {len(results)} | Passed: {passed} | Failed: {failed}")
        print(f"Pass Rate: {100*passed/len(results):.1f}%")
        
        if failed > 0:
            print("\nFailed Scenarios:")
            for r in results:
                if not r.passed:
                    print(f"  - [{r.scenario_id:02d}] {r.scenario_name}")
                    print(f"    Armed: {r.armed} vs expected {r.expected_armed}")
                    print(f"    Exit: {r.exit_triggered} vs expected {r.expected_exit}")
        
        self._logger.log("exitbot_scenario_test_complete", {
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "pass_rate": 100*passed/len(results)
        })
        
        print("\nDetailed logs written to logs/app.jsonl")
        print("Search for: exitbot_scenario_*")
        
        return results


def main():
    """Run the 30-scenario test"""
    tester = ExitBotScenarioTester()
    results = tester.run_all()
    
    # Return exit code based on results
    failed = sum(1 for r in results if not r.passed)
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
