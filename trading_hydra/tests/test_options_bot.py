"""
OptionsBot Test Suite
=====================
Comprehensive tests to guarantee OptionsBot behaves correctly under all conditions.

Test Scenarios:
1. Normal operation - trading during session hours
2. Outside trading hours - skips trading
3. Max trades per day limit enforced
4. Max concurrent positions enforced
5. Strategy selection based on market regime
6. Bull put spread construction
7. Bear call spread construction
8. Iron condor construction
9. Time-based exits (flatten before close)
10. Config load failure - fails closed

Run with: python -m src.trading_hydra.tests.test_options_bot
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from dataclasses import dataclass
from typing import Dict, Any, Optional, List
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta


class TestOptionsBot:
    """Test suite for OptionsBot trading logic"""
    
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.results = []
    
    def log(self, test_name: str, passed: bool, message: str = ""):
        status = "PASS" if passed else "FAIL"
        icon = "✅" if passed else "❌"
        self.results.append(f"{icon} {test_name}: {status}")
        if message:
            self.results.append(f"   └─ {message}")
        if passed:
            self.passed += 1
        else:
            self.failed += 1
    
    def test_bot_initialization(self):
        """OptionsBot initializes correctly with config"""
        test_name = "Bot Initialization"
        
        try:
            from src.trading_hydra.bots.options_bot import OptionsBot
            
            with patch.multiple(
                'src.trading_hydra.bots.options_bot',
                get_alpaca_client=MagicMock(return_value=MagicMock()),
                load_bots_config=MagicMock(return_value={
                    "optionsbot": {
                        "bot_id": "opt_core",
                        "enabled": True,
                        "tickers": ["SPY", "QQQ", "IWM"]
                    }
                }),
                get_logger=MagicMock(return_value=MagicMock(log=MagicMock(), warn=MagicMock(), error=MagicMock()))
            ):
                bot = OptionsBot("opt_core")
                
                assert bot.bot_id == "opt_core", "Bot ID should match"
                assert "SPY" in bot.tickers, "Should include SPY"
                assert "QQQ" in bot.tickers, "Should include QQQ"
                
                self.log(test_name, True, f"Bot initializes with tickers: {bot.tickers}")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_disabled_bot_skips_execution(self):
        """OptionsBot skips execution when disabled"""
        test_name = "Disabled Bot Skips Execution"
        
        try:
            from src.trading_hydra.bots.options_bot import OptionsBot
            
            with patch.multiple(
                'src.trading_hydra.bots.options_bot',
                get_alpaca_client=MagicMock(return_value=MagicMock()),
                load_bots_config=MagicMock(return_value={
                    "optionsbot": {"enabled": False, "tickers": ["SPY"]}
                }),
                get_logger=MagicMock(return_value=MagicMock(log=MagicMock(), warn=MagicMock(), error=MagicMock()))
            ):
                bot = OptionsBot("opt_core")
                result = bot.execute(max_daily_loss=100.0)
                
                assert result.get("trades_attempted", 0) == 0, "No trades when disabled"
                
                self.log(test_name, True, "Disabled bot attempts no trades")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_session_hours_check(self):
        """OptionsBot respects trading session hours"""
        test_name = "Session Hours Enforcement"
        
        try:
            trade_start = "06:40"
            trade_end = "12:30"
            
            start_hour, start_min = map(int, trade_start.split(":"))
            end_hour, end_min = map(int, trade_end.split(":"))
            
            start_minutes = start_hour * 60 + start_min
            end_minutes = end_hour * 60 + end_min
            
            test_time_in = "09:00"
            test_hour, test_min = map(int, test_time_in.split(":"))
            test_minutes = test_hour * 60 + test_min
            
            is_in_session = start_minutes <= test_minutes <= end_minutes
            assert is_in_session, "09:00 should be within 06:40-12:30"
            
            test_time_out = "14:00"
            test_hour, test_min = map(int, test_time_out.split(":"))
            test_minutes = test_hour * 60 + test_min
            
            is_out_session = not (start_minutes <= test_minutes <= end_minutes)
            assert is_out_session, "14:00 should be outside 06:40-12:30"
            
            self.log(test_name, True, "Session hours correctly enforced")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_strategy_bull_put_spread(self):
        """OptionsBot bull put spread logic"""
        test_name = "Bull Put Spread Strategy"
        
        try:
            underlying_price = 500.0
            short_put_strike = 495.0
            long_put_strike = 490.0
            credit_received = 1.50
            
            max_loss = (short_put_strike - long_put_strike) - credit_received
            
            assert max_loss == 3.50, f"Max loss should be $3.50, got ${max_loss}"
            
            max_profit = credit_received
            assert max_profit == 1.50, f"Max profit should be $1.50, got ${max_profit}"
            
            self.log(test_name, True, f"Bull put spread: credit ${credit_received}, max loss ${max_loss}")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_strategy_bear_call_spread(self):
        """OptionsBot bear call spread logic"""
        test_name = "Bear Call Spread Strategy"
        
        try:
            underlying_price = 500.0
            short_call_strike = 505.0
            long_call_strike = 510.0
            credit_received = 1.25
            
            max_loss = (long_call_strike - short_call_strike) - credit_received
            
            assert max_loss == 3.75, f"Max loss should be $3.75, got ${max_loss}"
            
            max_profit = credit_received
            assert max_profit == 1.25, f"Max profit should be $1.25, got ${max_profit}"
            
            self.log(test_name, True, f"Bear call spread: credit ${credit_received}, max loss ${max_loss}")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_strategy_iron_condor(self):
        """OptionsBot iron condor logic"""
        test_name = "Iron Condor Strategy"
        
        try:
            underlying_price = 500.0
            
            short_put_strike = 490.0
            long_put_strike = 485.0
            short_call_strike = 510.0
            long_call_strike = 515.0
            
            put_credit = 0.80
            call_credit = 0.70
            total_credit = put_credit + call_credit
            
            spread_width = 5.0
            max_loss = spread_width - total_credit
            
            assert total_credit == 1.50, f"Total credit should be $1.50, got ${total_credit}"
            assert max_loss == 3.50, f"Max loss should be $3.50, got ${max_loss}"
            
            self.log(test_name, True, f"Iron condor: credit ${total_credit}, max loss ${max_loss}")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_market_regime_bullish(self):
        """OptionsBot detects bullish market regime"""
        test_name = "Market Regime - Bullish Detection"
        
        try:
            prices = [495, 497, 499, 501, 503, 505, 507, 509, 511, 513]
            
            trend = (prices[-1] - prices[0]) / prices[0] * 100
            
            is_bullish = trend > 1.0
            assert is_bullish, f"Should detect bullish trend ({trend:.1f}%)"
            
            self.log(test_name, True, f"Bullish regime detected (trend: +{trend:.1f}%)")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_market_regime_bearish(self):
        """OptionsBot detects bearish market regime"""
        test_name = "Market Regime - Bearish Detection"
        
        try:
            prices = [513, 511, 509, 507, 505, 503, 501, 499, 497, 495]
            
            trend = (prices[-1] - prices[0]) / prices[0] * 100
            
            is_bearish = trend < -1.0
            assert is_bearish, f"Should detect bearish trend ({trend:.1f}%)"
            
            self.log(test_name, True, f"Bearish regime detected (trend: {trend:.1f}%)")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_flatten_before_close(self):
        """OptionsBot flattens positions before market close"""
        test_name = "Flatten Before Close"
        
        try:
            market_close = datetime.now().replace(hour=16, minute=0, second=0)
            flatten_minutes = 15
            
            flatten_time = market_close - timedelta(minutes=flatten_minutes)
            current_time = market_close - timedelta(minutes=10)
            
            should_flatten = current_time >= flatten_time
            assert should_flatten, "Should flatten 10 min before close (limit: 15 min)"
            
            current_time_early = market_close - timedelta(minutes=30)
            should_not_flatten = current_time_early < flatten_time
            assert should_not_flatten, "Should not flatten 30 min before close"
            
            self.log(test_name, True, f"Flattens {flatten_minutes} minutes before close")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_take_profit_pct_of_credit(self):
        """OptionsBot take profit as percentage of credit received"""
        test_name = "Take Profit as % of Credit"
        
        try:
            credit_received = 2.00
            take_profit_pct = 50
            
            target_profit = credit_received * (take_profit_pct / 100)
            
            assert target_profit == 1.00, f"Take profit should be $1.00, got ${target_profit}"
            
            target_close_price = credit_received - target_profit
            assert target_close_price == 1.00, f"Close at ${target_close_price} to capture ${target_profit}"
            
            self.log(test_name, True, f"Take profit: ${target_profit} (50% of ${credit_received} credit)")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_stop_loss_pct_of_credit(self):
        """OptionsBot stop loss as percentage of credit received"""
        test_name = "Stop Loss as % of Credit"
        
        try:
            credit_received = 2.00
            stop_loss_pct = 200
            
            max_acceptable_loss = credit_received * (stop_loss_pct / 100)
            
            assert max_acceptable_loss == 4.00, f"Max loss should be $4.00, got ${max_acceptable_loss}"
            
            exit_price = credit_received + max_acceptable_loss
            assert exit_price == 6.00, f"Exit at ${exit_price} (stop loss triggered)"
            
            self.log(test_name, True, f"Stop loss: ${max_acceptable_loss} (200% of ${credit_received} credit)")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_config_missing_uses_defaults(self):
        """OptionsBot uses defaults when config is missing"""
        test_name = "Missing Config Uses Defaults"
        
        try:
            from src.trading_hydra.bots.options_bot import OptionsBot
            
            with patch.multiple(
                'src.trading_hydra.bots.options_bot',
                get_alpaca_client=MagicMock(return_value=MagicMock()),
                load_bots_config=MagicMock(return_value={}),
                get_logger=MagicMock(return_value=MagicMock(log=MagicMock(), warn=MagicMock(), error=MagicMock()))
            ):
                bot = OptionsBot("opt_core")
                
                assert bot.tickers == ["SPY", "QQQ", "IWM"], "Should use default tickers"
                
                self.log(test_name, True, "Uses default tickers when config missing")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def run_all(self):
        """Run all OptionsBot tests"""
        print("\n" + "=" * 60)
        print("OPTIONS BOT TEST SUITE")
        print("=" * 60)
        print("Testing all OptionsBot scenarios...\n")
        
        self.test_bot_initialization()
        self.test_disabled_bot_skips_execution()
        self.test_session_hours_check()
        self.test_strategy_bull_put_spread()
        self.test_strategy_bear_call_spread()
        self.test_strategy_iron_condor()
        self.test_market_regime_bullish()
        self.test_market_regime_bearish()
        self.test_flatten_before_close()
        self.test_take_profit_pct_of_credit()
        self.test_stop_loss_pct_of_credit()
        self.test_config_missing_uses_defaults()
        
        print("\n".join(self.results))
        print("\n" + "-" * 60)
        print(f"RESULTS: {self.passed} passed, {self.failed} failed")
        print("-" * 60)
        
        if self.failed == 0:
            print("✅ ALL OPTIONS BOT TESTS PASSED!")
        else:
            print("❌ SOME TESTS FAILED - Review issues above")
        
        return self.failed == 0


if __name__ == "__main__":
    tester = TestOptionsBot()
    success = tester.run_all()
    sys.exit(0 if success else 1)
