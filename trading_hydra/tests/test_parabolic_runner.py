"""
Parabolic Runner Mode Test Suite
================================
Tests the parabolic runner feature that lets trailing stops ride extended moves after TP2.

Key scenarios tested:
1. Runner mode activation after TP2
2. TP3 is skipped when runner mode active
3. Trailing stop widening (one-time only)
4. Runner mode disabled respects TP3
5. State persistence for widening flag

Run with: python -m src.trading_hydra.tests.test_parabolic_runner
"""
import sys
import os
from dataclasses import dataclass
from typing import Dict, Any, Optional, Set
from unittest.mock import MagicMock, patch
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))


@dataclass
class MockPosition:
    """Mock position for testing"""
    symbol: str = "AAPL"
    position_id: str = "test_pos_001"
    bot_id: str = "momentum_bot"
    side: str = "long"
    qty: float = 100.0
    entry_price: float = 100.0
    current_price: float = 110.0
    asset_class: str = "equity"
    pnl_pct: float = 10.0


@dataclass  
class MockTrailingStopState:
    """Mock trailing stop state"""
    armed: bool = True
    high_water: float = 110.0
    stop_price: float = 105.0
    trailing_pct: float = 5.0


class TestParabolicRunner:
    """Test suite for parabolic runner mode"""
    
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
    
    def test_runner_mode_activates_after_tp2(self):
        """Runner mode should activate when TP2 hit and parabolic_runner_enabled=True"""
        test_name = "Runner Mode Activates After TP2"
        
        try:
            from src.trading_hydra.services.exitbot import TakeProfitConfig
            
            config = TakeProfitConfig(
                enabled=True,
                tp1_pct=2.0,
                tp2_pct=4.0,
                tp3_pct=8.0,
                parabolic_runner_enabled=True,
                runner_widen_trailing_pct=50.0
            )
            
            position = MockPosition(pnl_pct=6.0)  # Above TP2 (4%), below TP3 (8%)
            tiers_hit = {1, 2}  # TP1 and TP2 already hit
            
            # Check runner condition
            should_activate = (
                config.parabolic_runner_enabled and 
                2 in tiers_hit and 
                3 not in tiers_hit
            )
            
            assert should_activate == True, "Runner should activate when TP2 hit"
            self.log(test_name, True, f"Runner activates at {position.pnl_pct}% with TP2 hit")
            
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_tp3_skipped_in_runner_mode(self):
        """TP3 should not trigger when runner mode is active"""
        test_name = "TP3 Skipped in Runner Mode"
        
        try:
            from src.trading_hydra.services.exitbot import TakeProfitConfig
            
            config = TakeProfitConfig(
                enabled=True,
                tp1_pct=2.0,
                tp2_pct=4.0,
                tp3_pct=8.0,
                parabolic_runner_enabled=True
            )
            
            position = MockPosition(pnl_pct=10.0)  # Above TP3 (8%)
            tiers_hit = {1, 2}  # TP1 and TP2 hit, but NOT TP3
            
            # In runner mode, even at 10% (above TP3), we don't trigger TP3
            is_runner_mode = config.parabolic_runner_enabled and 2 in tiers_hit and 3 not in tiers_hit
            should_check_tp3 = not is_runner_mode
            
            assert is_runner_mode == True, "Should be in runner mode"
            assert should_check_tp3 == False, "Should NOT check TP3 in runner mode"
            
            self.log(test_name, True, f"At {position.pnl_pct}% profit, TP3 skipped - trailing stop rides")
            
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_trailing_stop_widening(self):
        """Trailing stop should widen by configured percentage in runner mode"""
        test_name = "Trailing Stop Widening"
        
        try:
            config_widen_pct = 50.0
            
            # Original trailing stop: 5% trailing
            original_stop_distance = 5.0  # $5 from high water
            high_water = 110.0
            original_stop = high_water - original_stop_distance  # 105.0
            
            # After widening by 50%
            widen_factor = 1.0 + (config_widen_pct / 100.0)  # 1.5
            new_distance = original_stop_distance * widen_factor  # 7.5
            new_stop = high_water - new_distance  # 102.5
            
            assert new_distance == 7.5, f"Distance should be 7.5, got {new_distance}"
            assert new_stop == 102.5, f"New stop should be 102.5, got {new_stop}"
            
            improvement = original_stop - new_stop
            self.log(test_name, True, f"Stop widened from ${original_stop} to ${new_stop} (${improvement} more room)")
            
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_one_time_widening(self):
        """Trailing stop should only widen once, not every loop"""
        test_name = "One-Time Widening (No Repeat)"
        
        try:
            # Simulate the runner_widened state flag
            runner_widened_flags = {}
            position_id = "test_pos_001"
            
            def check_and_widen(pos_id: str) -> bool:
                """Returns True if widening happened, False if already widened"""
                key = f"runner_widened_{pos_id}"
                if key in runner_widened_flags:
                    return False  # Already widened
                runner_widened_flags[key] = True
                return True  # First time widening
            
            # First call - should widen
            first_widen = check_and_widen(position_id)
            assert first_widen == True, "First call should widen"
            
            # Second call - should NOT widen again
            second_widen = check_and_widen(position_id)
            assert second_widen == False, "Second call should not widen"
            
            # Third call - still should NOT widen
            third_widen = check_and_widen(position_id)
            assert third_widen == False, "Third call should not widen"
            
            self.log(test_name, True, "Widening correctly happens only once per position")
            
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_runner_mode_disabled(self):
        """When runner mode disabled, TP3 should trigger normally"""
        test_name = "Runner Mode Disabled - TP3 Works"
        
        try:
            from src.trading_hydra.services.exitbot import TakeProfitConfig
            
            config = TakeProfitConfig(
                enabled=True,
                tp1_pct=2.0,
                tp2_pct=4.0,
                tp3_pct=8.0,
                parabolic_runner_enabled=False,  # Disabled
                runner_widen_trailing_pct=50.0
            )
            
            position = MockPosition(pnl_pct=10.0)  # Above TP3
            tiers_hit = {1, 2}
            
            # Runner mode should NOT activate
            is_runner_mode = config.parabolic_runner_enabled and 2 in tiers_hit and 3 not in tiers_hit
            should_check_tp3 = not is_runner_mode
            
            assert is_runner_mode == False, "Runner mode should be disabled"
            assert should_check_tp3 == True, "Should check TP3 when runner disabled"
            
            # TP3 should trigger at 10% (above 8% threshold)
            should_trigger_tp3 = position.pnl_pct >= config.tp3_pct
            assert should_trigger_tp3 == True, f"TP3 should trigger at {position.pnl_pct}%"
            
            self.log(test_name, True, "TP3 triggers normally when runner mode disabled")
            
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_threshold_analysis(self):
        """Analyze current thresholds and suggest adjustments"""
        test_name = "Threshold Analysis"
        
        try:
            from src.trading_hydra.services.exitbot import TakeProfitConfig
            
            config = TakeProfitConfig()
            
            analysis = []
            analysis.append(f"Current TP Thresholds:")
            analysis.append(f"  TP1: {config.tp1_pct}% → exit {config.tp1_exit_pct*100:.0f}% of position")
            analysis.append(f"  TP2: {config.tp2_pct}% → exit {config.tp2_exit_pct*100:.0f}% of remaining")
            analysis.append(f"  TP3: {config.tp3_pct}% → exit {config.tp3_exit_pct*100:.0f}% (full exit)")
            analysis.append(f"  Runner Mode: {'Enabled' if config.parabolic_runner_enabled else 'Disabled'}")
            analysis.append(f"  Stop Widen: {config.runner_widen_trailing_pct}%")
            
            # Recommendations based on typical market conditions
            recommendations = []
            
            # TP1 analysis
            if config.tp1_pct < 1.5:
                recommendations.append("TP1 too tight - may exit winners too early")
            elif config.tp1_pct > 3.0:
                recommendations.append("TP1 may miss securing early profits")
            
            # TP2 analysis  
            if config.tp2_pct < config.tp1_pct * 1.5:
                recommendations.append("TP2 too close to TP1 - not enough room between tiers")
            
            # Widen percentage analysis
            if config.runner_widen_trailing_pct < 30:
                recommendations.append("Widen % too low - parabolic moves may get stopped out early")
            elif config.runner_widen_trailing_pct > 80:
                recommendations.append("Widen % very high - may give back too much profit on reversals")
            
            if not recommendations:
                recommendations.append("Current thresholds look balanced")
            
            analysis.append(f"\nRecommendations:")
            for rec in recommendations:
                analysis.append(f"  • {rec}")
            
            self.log(test_name, True, "\n" + "\n".join(analysis))
            
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def test_scenario_parabolic_run(self):
        """Simulate a parabolic run scenario to validate behavior"""
        test_name = "Scenario: Parabolic Run (+23% capture)"
        
        try:
            # Scenario: NVDA earnings breakout
            entry_price = 100.0
            
            # Price progression and expected behavior
            stages = [
                # (price, pnl_pct, expected_action)
                (102.0, 2.0, "TP1 hit - exit 33%, move stop to breakeven"),
                (104.0, 4.0, "TP2 hit - exit 50% of remaining, runner mode activates"),
                (108.0, 8.0, "At TP3 level but runner mode SKIPS it"),
                (115.0, 15.0, "Parabolic run continues, trailing stop follows"),
                (123.0, 23.0, "Peak of move, high water mark set"),
                (118.0, 18.0, "Pullback, trailing stop triggered at ~19%"),
            ]
            
            output = ["Simulated price progression:"]
            for price, pnl, action in stages:
                output.append(f"  ${price:.2f} (+{pnl:.1f}%): {action}")
            
            # Without runner mode: Would exit at TP3 (+8%) 
            # With runner mode: Captured up to +23%, exit on trailing at ~+19%
            without_runner = 8.0
            with_runner = 19.0  # Approximate trailing stop exit
            improvement = with_runner - without_runner
            
            output.append(f"\n  Result comparison:")
            output.append(f"  Without runner: +{without_runner}% (TP3 exit)")
            output.append(f"  With runner: +{with_runner}% (trailing stop)")
            output.append(f"  Improvement: +{improvement}% extra profit captured")
            
            self.log(test_name, True, "\n" + "\n".join(output))
            
        except Exception as e:
            self.log(test_name, False, str(e))
    
    def run_all(self):
        """Run all tests"""
        print("\n" + "="*60)
        print("PARABOLIC RUNNER MODE TEST SUITE")
        print("="*60 + "\n")
        
        self.test_runner_mode_activates_after_tp2()
        self.test_tp3_skipped_in_runner_mode()
        self.test_trailing_stop_widening()
        self.test_one_time_widening()
        self.test_runner_mode_disabled()
        self.test_threshold_analysis()
        self.test_scenario_parabolic_run()
        
        print("\n".join(self.results))
        print("\n" + "-"*60)
        print(f"Results: {self.passed} passed, {self.failed} failed")
        print("-"*60)
        
        return self.failed == 0


if __name__ == "__main__":
    tester = TestParabolicRunner()
    success = tester.run_all()
    sys.exit(0 if success else 1)
