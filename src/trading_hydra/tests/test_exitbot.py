"""
ExitBot Test Suite
==================
Comprehensive tests to guarantee ExitBot behaves correctly under all conditions.

Test Scenarios:
1. Normal operation - trading continues when healthy
2. Max daily loss halt - stops trading when loss limit exceeded
3. API health failure halt - stops trading when API fails
4. Existing halt respected - doesn't override existing halts
5. Disabled mode - bypasses all checks when disabled
6. Flatten on halt - attempts to close positions on halt
7. Config load failure - fails closed (halts trading)

Run with: python -m src.trading_hydra.tests.test_exitbot
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from dataclasses import dataclass
from typing import Dict, Any, Optional
from unittest.mock import MagicMock, patch


class MockHealthSnapshot:
    def __init__(self, ok: bool, reason: str = ""):
        self.ok = ok
        self.reason = reason


class MockHaltStatus:
    def __init__(self, active: bool, reason: str = ""):
        self.active = active
        self.reason = reason


class TestExitBot:
    """Test suite for ExitBot safety mechanisms"""
    
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
    
    def test_normal_operation(self):
        """ExitBot allows trading when health OK and P&L within limits"""
        test_name = "Normal Operation - Trading Continues"
        
        try:
            from src.trading_hydra.services.exitbot import ExitBot
            
            with patch.multiple(
                'src.trading_hydra.services.exitbot',
                get_alpaca_client=MagicMock(return_value=MagicMock(has_credentials=MagicMock(return_value=True))),
                load_bots_config=MagicMock(return_value={
                    "exitbot": {"enabled": True, "kill_conditions": {"max_daily_loss_halt": True, "api_failure_halt": True}}
                }),
                load_settings=MagicMock(return_value={
                    "risk": {"global_max_daily_loss_pct": 2.0}
                }),
                get_health_monitor=MagicMock(return_value=MagicMock(
                    get_snapshot=MagicMock(return_value=MockHealthSnapshot(ok=True))
                )),
                get_halt_manager=MagicMock(return_value=MagicMock(
                    is_halted=MagicMock(return_value=False),
                    get_status=MagicMock(return_value=MockHaltStatus(active=False))
                )),
                get_logger=MagicMock(return_value=MagicMock(log=MagicMock(), error=MagicMock()))
            ):
                bot = ExitBot()
                result = bot.run(equity=10000.0, day_start_equity=10000.0)
                
                assert result.should_continue == True, "Should continue trading"
                assert result.is_halted == False, "Should not be halted"
                assert result.pnl == 0.0, "P&L should be 0"
                
                self.log(test_name, True, "Trading continues when healthy with no losses")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_max_daily_loss_halt(self):
        """ExitBot halts trading when max daily loss is exceeded"""
        test_name = "Max Daily Loss Halt"
        
        try:
            from src.trading_hydra.services.exitbot import ExitBot
            
            mock_halt_manager = MagicMock()
            mock_halt_manager.is_halted = MagicMock(return_value=False)
            mock_halt_manager.set_halt = MagicMock()
            mock_halt_manager.get_status = MagicMock(return_value=MockHaltStatus(active=False))
            
            mock_alpaca = MagicMock()
            mock_alpaca.has_credentials = MagicMock(return_value=True)
            mock_alpaca.flatten = MagicMock(return_value={"success": True})
            
            with patch.multiple(
                'src.trading_hydra.services.exitbot',
                get_alpaca_client=MagicMock(return_value=mock_alpaca),
                load_bots_config=MagicMock(return_value={
                    "exitbot": {"enabled": True, "cooloff_minutes": 60, "kill_conditions": {"max_daily_loss_halt": True}}
                }),
                load_settings=MagicMock(return_value={
                    "risk": {"global_max_daily_loss_pct": 2.0}
                }),
                get_health_monitor=MagicMock(return_value=MagicMock(
                    get_snapshot=MagicMock(return_value=MockHealthSnapshot(ok=True))
                )),
                get_halt_manager=MagicMock(return_value=mock_halt_manager),
                get_logger=MagicMock(return_value=MagicMock(log=MagicMock(), error=MagicMock()))
            ):
                bot = ExitBot()
                result = bot.run(equity=9700.0, day_start_equity=10000.0)
                
                assert result.should_continue == False, "Should stop trading"
                assert result.is_halted == True, "Should be halted"
                assert "MAX_DAILY_LOSS" in result.halt_reason, "Should mention max loss"
                assert result.pnl == -300.0, "P&L should be -300"
                
                mock_halt_manager.set_halt.assert_called_once()
                mock_alpaca.flatten.assert_called_once()
                
                self.log(test_name, True, "Halts and flattens when loss exceeds 2% ($200 limit, $300 loss)")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_api_health_failure_halt(self):
        """ExitBot halts trading when API health check fails"""
        test_name = "API Health Failure Halt"
        
        try:
            from src.trading_hydra.services.exitbot import ExitBot
            
            mock_halt_manager = MagicMock()
            mock_halt_manager.is_halted = MagicMock(return_value=False)
            mock_halt_manager.set_halt = MagicMock()
            
            mock_alpaca = MagicMock()
            mock_alpaca.has_credentials = MagicMock(return_value=True)
            mock_alpaca.flatten = MagicMock(return_value={"success": True})
            
            with patch.multiple(
                'src.trading_hydra.services.exitbot',
                get_alpaca_client=MagicMock(return_value=mock_alpaca),
                load_bots_config=MagicMock(return_value={
                    "exitbot": {"enabled": True, "cooloff_minutes": 60, "kill_conditions": {"api_failure_halt": True}}
                }),
                load_settings=MagicMock(return_value={"risk": {}}),
                get_health_monitor=MagicMock(return_value=MagicMock(
                    get_snapshot=MagicMock(return_value=MockHealthSnapshot(ok=False, reason="5 consecutive API failures"))
                )),
                get_halt_manager=MagicMock(return_value=mock_halt_manager),
                get_logger=MagicMock(return_value=MagicMock(log=MagicMock(), error=MagicMock()))
            ):
                bot = ExitBot()
                result = bot.run(equity=10000.0, day_start_equity=10000.0)
                
                assert result.should_continue == False, "Should stop trading"
                assert result.is_halted == True, "Should be halted"
                assert "HEALTH_FAIL" in result.halt_reason, "Should mention health failure"
                
                mock_halt_manager.set_halt.assert_called_once()
                mock_alpaca.flatten.assert_called_once()
                
                self.log(test_name, True, "Halts and flattens on API health failure")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_existing_halt_respected(self):
        """ExitBot respects existing halt state and doesn't override"""
        test_name = "Existing Halt Respected"
        
        try:
            from src.trading_hydra.services.exitbot import ExitBot
            
            mock_halt_manager = MagicMock()
            mock_halt_manager.is_halted = MagicMock(return_value=True)
            mock_halt_manager.get_status = MagicMock(return_value=MockHaltStatus(
                active=True, reason="PREVIOUS_HALT: Manual stop"
            ))
            
            with patch.multiple(
                'src.trading_hydra.services.exitbot',
                get_alpaca_client=MagicMock(return_value=MagicMock()),
                load_bots_config=MagicMock(return_value={"exitbot": {"enabled": True}}),
                load_settings=MagicMock(return_value={}),
                get_health_monitor=MagicMock(return_value=MagicMock()),
                get_halt_manager=MagicMock(return_value=mock_halt_manager),
                get_logger=MagicMock(return_value=MagicMock(log=MagicMock(), error=MagicMock()))
            ):
                bot = ExitBot()
                result = bot.run(equity=10000.0, day_start_equity=10000.0)
                
                assert result.should_continue == False, "Should not continue"
                assert result.is_halted == True, "Should remain halted"
                assert "PREVIOUS_HALT" in result.halt_reason, "Should preserve original reason"
                
                self.log(test_name, True, "Preserves existing halt state")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_disabled_mode(self):
        """ExitBot bypasses all checks when disabled"""
        test_name = "Disabled Mode Bypass"
        
        try:
            from src.trading_hydra.services.exitbot import ExitBot
            
            with patch.multiple(
                'src.trading_hydra.services.exitbot',
                get_alpaca_client=MagicMock(return_value=MagicMock()),
                load_bots_config=MagicMock(return_value={"exitbot": {"enabled": False}}),
                load_settings=MagicMock(return_value={}),
                get_health_monitor=MagicMock(return_value=MagicMock(
                    get_snapshot=MagicMock(return_value=MockHealthSnapshot(ok=False))
                )),
                get_halt_manager=MagicMock(return_value=MagicMock(
                    is_halted=MagicMock(return_value=False)
                )),
                get_logger=MagicMock(return_value=MagicMock(log=MagicMock(), error=MagicMock()))
            ):
                bot = ExitBot()
                result = bot.run(equity=9000.0, day_start_equity=10000.0)
                
                assert result.should_continue == True, "Should continue even with losses"
                assert result.is_halted == False, "Should not halt"
                assert result.pnl == -1000.0, "Should report actual P&L"
                
                self.log(test_name, True, "Bypasses safety checks when disabled (dangerous but allowed)")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_config_load_failure(self):
        """ExitBot fails closed when config cannot be loaded"""
        test_name = "Config Load Failure - Fail Closed"
        
        try:
            from src.trading_hydra.services.exitbot import ExitBot
            
            with patch.multiple(
                'src.trading_hydra.services.exitbot',
                get_alpaca_client=MagicMock(return_value=MagicMock()),
                load_bots_config=MagicMock(side_effect=Exception("Config file corrupted")),
                load_settings=MagicMock(return_value={}),
                get_health_monitor=MagicMock(return_value=MagicMock()),
                get_halt_manager=MagicMock(return_value=MagicMock()),
                get_logger=MagicMock(return_value=MagicMock(log=MagicMock(), error=MagicMock()))
            ):
                bot = ExitBot()
                result = bot.run(equity=10000.0, day_start_equity=10000.0)
                
                assert result.should_continue == False, "Should stop trading"
                assert result.is_halted == True, "Should halt"
                assert "Config load failed" in result.halt_reason, "Should mention config failure"
                
                self.log(test_name, True, "Halts trading when config cannot be loaded (fail-closed)")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_flatten_failure_still_halts(self):
        """ExitBot still halts even if flatten fails"""
        test_name = "Flatten Failure Still Halts"
        
        try:
            from src.trading_hydra.services.exitbot import ExitBot
            
            mock_halt_manager = MagicMock()
            mock_halt_manager.is_halted = MagicMock(return_value=False)
            mock_halt_manager.set_halt = MagicMock()
            
            mock_alpaca = MagicMock()
            mock_alpaca.has_credentials = MagicMock(return_value=True)
            mock_alpaca.flatten = MagicMock(return_value={"success": False, "error": "Connection refused"})
            
            with patch.multiple(
                'src.trading_hydra.services.exitbot',
                get_alpaca_client=MagicMock(return_value=mock_alpaca),
                load_bots_config=MagicMock(return_value={
                    "exitbot": {"enabled": True, "cooloff_minutes": 60, "kill_conditions": {"max_daily_loss_halt": True}}
                }),
                load_settings=MagicMock(return_value={
                    "risk": {"global_max_daily_loss_pct": 2.0}
                }),
                get_health_monitor=MagicMock(return_value=MagicMock(
                    get_snapshot=MagicMock(return_value=MockHealthSnapshot(ok=True))
                )),
                get_halt_manager=MagicMock(return_value=mock_halt_manager),
                get_logger=MagicMock(return_value=MagicMock(log=MagicMock(), error=MagicMock()))
            ):
                bot = ExitBot()
                result = bot.run(equity=9700.0, day_start_equity=10000.0)
                
                assert result.should_continue == False, "Should stop trading"
                assert result.is_halted == True, "Should be halted"
                assert "FLATTEN_FAILED" in result.halt_reason, "Should mention flatten failure"
                
                self.log(test_name, True, "Still halts even when flatten fails")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_pnl_within_limits(self):
        """ExitBot allows trading when loss is within limits"""
        test_name = "P&L Within Limits - Continue Trading"
        
        try:
            from src.trading_hydra.services.exitbot import ExitBot
            
            with patch.multiple(
                'src.trading_hydra.services.exitbot',
                get_alpaca_client=MagicMock(return_value=MagicMock(has_credentials=MagicMock(return_value=True))),
                load_bots_config=MagicMock(return_value={
                    "exitbot": {"enabled": True, "kill_conditions": {"max_daily_loss_halt": True}}
                }),
                load_settings=MagicMock(return_value={
                    "risk": {"global_max_daily_loss_pct": 2.0}
                }),
                get_health_monitor=MagicMock(return_value=MagicMock(
                    get_snapshot=MagicMock(return_value=MockHealthSnapshot(ok=True))
                )),
                get_halt_manager=MagicMock(return_value=MagicMock(
                    is_halted=MagicMock(return_value=False),
                    get_status=MagicMock(return_value=MockHaltStatus(active=False))
                )),
                get_logger=MagicMock(return_value=MagicMock(log=MagicMock(), error=MagicMock()))
            ):
                bot = ExitBot()
                result = bot.run(equity=9900.0, day_start_equity=10000.0)
                
                assert result.should_continue == True, "Should continue"
                assert result.is_halted == False, "Should not halt"
                assert result.pnl == -100.0, "P&L should be -100"
                
                self.log(test_name, True, "Continues trading with $100 loss (within 2% = $200 limit)")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_position_monitoring_returns_metrics(self):
        """ExitBot returns position monitoring metrics"""
        test_name = "Position Monitoring Returns Metrics"
        
        try:
            from src.trading_hydra.services.exitbot import ExitBot
            
            mock_alpaca = MagicMock()
            mock_alpaca.has_credentials = MagicMock(return_value=True)
            mock_alpaca.get_positions = MagicMock(return_value=[])
            
            with patch.multiple(
                'src.trading_hydra.services.exitbot',
                get_alpaca_client=MagicMock(return_value=mock_alpaca),
                load_bots_config=MagicMock(return_value={
                    "exitbot": {"enabled": True, "kill_conditions": {"max_daily_loss_halt": True}},
                    "default_trailing_stop": {"enabled": True, "value": 1.0}
                }),
                load_settings=MagicMock(return_value={
                    "risk": {"global_max_daily_loss_pct": 2.0}
                }),
                get_health_monitor=MagicMock(return_value=MagicMock(
                    get_snapshot=MagicMock(return_value=MockHealthSnapshot(ok=True))
                )),
                get_halt_manager=MagicMock(return_value=MagicMock(
                    is_halted=MagicMock(return_value=False),
                    get_status=MagicMock(return_value=MockHaltStatus(active=False))
                )),
                get_logger=MagicMock(return_value=MagicMock(log=MagicMock(), error=MagicMock())),
                get_trailing_stop_manager=MagicMock(return_value=MagicMock())
            ):
                bot = ExitBot()
                result = bot.run(equity=10000.0, day_start_equity=10000.0)
                
                assert result.should_continue == True, "Should continue trading"
                assert hasattr(result, 'positions_monitored'), "Should have positions_monitored"
                assert hasattr(result, 'trailing_stops_active'), "Should have trailing_stops_active"
                assert hasattr(result, 'exits_triggered'), "Should have exits_triggered"
                
                self.log(test_name, True, "ExitBot returns position monitoring metrics")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_detects_new_position(self):
        """ExitBot detects new positions and tracks them"""
        test_name = "Detects New Positions"
        
        try:
            from src.trading_hydra.services.exitbot import ExitBot, PositionInfo
            
            # Create mock position (simulating AlpacaPosition dataclass)
            mock_position = MagicMock()
            mock_position.symbol = "AAPL"
            mock_position.qty = 10.0
            mock_position.market_value = 1500.0
            mock_position.unrealized_pl = 50.0
            mock_position.side = "long"
            
            mock_alpaca = MagicMock()
            mock_alpaca.has_credentials = MagicMock(return_value=True)
            mock_alpaca.get_positions = MagicMock(return_value=[mock_position])
            
            mock_ts_mgr = MagicMock()
            mock_ts_mgr.init_for_position = MagicMock()
            mock_ts_mgr.load_state = MagicMock(return_value=None)
            
            with patch.multiple(
                'src.trading_hydra.services.exitbot',
                get_alpaca_client=MagicMock(return_value=mock_alpaca),
                load_bots_config=MagicMock(return_value={
                    "exitbot": {
                        "enabled": True, 
                        "kill_conditions": {"max_daily_loss_halt": True},
                        "default_trailing_stop": {"enabled": True, "value": 1.0}
                    },
                    "momentum_bots": []
                }),
                load_settings=MagicMock(return_value={
                    "risk": {"global_max_daily_loss_pct": 2.0}
                }),
                get_health_monitor=MagicMock(return_value=MagicMock(
                    get_snapshot=MagicMock(return_value=MockHealthSnapshot(ok=True))
                )),
                get_halt_manager=MagicMock(return_value=MagicMock(
                    is_halted=MagicMock(return_value=False),
                    get_status=MagicMock(return_value=MockHaltStatus(active=False))
                )),
                get_logger=MagicMock(return_value=MagicMock(log=MagicMock(), error=MagicMock())),
                get_trailing_stop_manager=MagicMock(return_value=mock_ts_mgr),
                get_state=MagicMock(return_value=[]),
                set_state=MagicMock()
            ):
                bot = ExitBot()
                result = bot.run(equity=10000.0, day_start_equity=10000.0)
                
                assert result.should_continue == True, "Should continue trading"
                assert result.positions_monitored == 1, "Should monitor 1 position"
                
                mock_ts_mgr.init_for_position.assert_called_once()
                
                self.log(test_name, True, "ExitBot detects and registers new positions")
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def run_all(self):
        """Run all ExitBot tests"""
        print("\n" + "=" * 60)
        print("EXITBOT TEST SUITE")
        print("=" * 60)
        print("Testing all ExitBot safety scenarios...\n")
        
        self.test_normal_operation()
        self.test_max_daily_loss_halt()
        self.test_api_health_failure_halt()
        self.test_existing_halt_respected()
        self.test_disabled_mode()
        self.test_config_load_failure()
        self.test_flatten_failure_still_halts()
        self.test_pnl_within_limits()
        self.test_position_monitoring_returns_metrics()
        self.test_detects_new_position()
        
        print("\n".join(self.results))
        print("\n" + "-" * 60)
        print(f"RESULTS: {self.passed} passed, {self.failed} failed")
        print("-" * 60)
        
        if self.failed == 0:
            print("✅ ALL EXITBOT TESTS PASSED - Safety mechanisms verified!")
        else:
            print("❌ SOME TESTS FAILED - ExitBot may not be safe for production!")
        
        return self.failed == 0


if __name__ == "__main__":
    tester = TestExitBot()
    success = tester.run_all()
    sys.exit(0 if success else 1)
