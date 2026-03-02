
"""Unit tests for trailing stop functionality"""

import unittest
import time
from dataclasses import asdict
from unittest.mock import Mock, patch

from ..risk.trailing_stop import TrailingStopManager, TrailingStopConfig, TrailingStopState
from ..core.state import init_state_store


class TestTrailingStops(unittest.TestCase):
    """Test trailing stop logic and persistence"""
    
    def setUp(self):
        """Set up test environment"""
        init_state_store()
        self.trailing_manager = TrailingStopManager()
        
        # Standard config for testing
        self.config = TrailingStopConfig(
            enabled=True,
            mode="percent",
            value=1.0,  # 1% trail
            activation_profit_pct=0.3,  # Activate after 0.3% profit
            update_only_if_improves=True,
            epsilon_pct=0.02  # 0.02% buffer
        )
    
    def test_long_trailing_stop_basic(self):
        """Test basic long position trailing stop logic"""
        
        # Initialize long position at $100
        entry_price = 100.0
        state = self.trailing_manager.init_for_position(
            "test_bot", "pos123", "TEST", "equity", 
            entry_price, "long", self.config
        )
        
        # Price moves up to $100.50 - should arm trailing stop
        state = self.trailing_manager.update_state(
            "test_bot", "pos123", "TEST", "equity", 100.50, state
        )
        self.assertTrue(state.armed, "Should arm after 0.5% profit")
        self.assertEqual(state.high_water, 100.50)
        expected_stop = 100.50 * (1 - 1.0/100)  # 1% below high water
        self.assertAlmostEqual(state.stop_price, expected_stop, places=4)
        
        # Price continues up to $101 - stop should move up
        state = self.trailing_manager.update_state(
            "test_bot", "pos123", "TEST", "equity", 101.0, state
        )
        self.assertEqual(state.high_water, 101.0)
        new_expected_stop = 101.0 * (1 - 1.0/100)
        self.assertAlmostEqual(state.stop_price, new_expected_stop, places=4)
        self.assertGreater(state.stop_price, expected_stop, "Stop should move up")
        
        # Price drops to $100.80 - should NOT trigger (above stop)
        should_exit = self.trailing_manager.should_exit(state, 100.80)
        self.assertFalse(should_exit, "Should not trigger above stop price")
        
        # Price drops to $99.80 - should trigger (below stop with buffer)
        should_exit = self.trailing_manager.should_exit(state, 99.80)
        self.assertTrue(should_exit, "Should trigger below stop price")
    
    def test_short_trailing_stop_basic(self):
        """Test basic short position trailing stop logic"""
        
        # Initialize short position at $100
        entry_price = 100.0
        state = self.trailing_manager.init_for_position(
            "test_bot", "pos456", "TEST", "equity",
            entry_price, "short", self.config
        )
        
        # Price moves down to $99.50 - should arm trailing stop
        state = self.trailing_manager.update_state(
            "test_bot", "pos456", "TEST", "equity", 99.50, state
        )
        self.assertTrue(state.armed, "Should arm after 0.5% profit on short")
        self.assertEqual(state.low_water, 99.50)
        expected_stop = 99.50 * (1 + 1.0/100)  # 1% above low water
        self.assertAlmostEqual(state.stop_price, expected_stop, places=4)
        
        # Price continues down to $99 - stop should move down
        state = self.trailing_manager.update_state(
            "test_bot", "pos456", "TEST", "equity", 99.0, state
        )
        self.assertEqual(state.low_water, 99.0)
        new_expected_stop = 99.0 * (1 + 1.0/100)
        self.assertAlmostEqual(state.stop_price, new_expected_stop, places=4)
        self.assertLess(state.stop_price, expected_stop, "Stop should move down for short")
        
        # Price rises to $99.20 - should NOT trigger (below stop)
        should_exit = self.trailing_manager.should_exit(state, 99.20)
        self.assertFalse(should_exit, "Should not trigger below stop price for short")
        
        # Price rises to $100.20 - should trigger (above stop with buffer)
        should_exit = self.trailing_manager.should_exit(state, 100.20)
        self.assertTrue(should_exit, "Should trigger above stop price for short")
    
    def test_activation_threshold(self):
        """Test that trailing stop only arms after activation threshold"""
        
        entry_price = 100.0
        state = self.trailing_manager.init_for_position(
            "test_bot", "pos789", "TEST", "equity",
            entry_price, "long", self.config
        )
        
        # Price moves up slightly (0.2%) - should NOT arm
        state = self.trailing_manager.update_state(
            "test_bot", "pos789", "TEST", "equity", 100.20, state
        )
        self.assertFalse(state.armed, "Should not arm below activation threshold")
        
        # Price moves up to 0.3% - should ARM
        state = self.trailing_manager.update_state(
            "test_bot", "pos789", "TEST", "equity", 100.30, state
        )
        self.assertTrue(state.armed, "Should arm at activation threshold")
    
    def test_price_mode_trailing(self):
        """Test trailing stop with price mode (dollar amounts)"""
        
        config = TrailingStopConfig(
            enabled=True,
            mode="price",  # Dollar mode
            value=0.50,    # $0.50 trail
            activation_profit_pct=0.2,
            update_only_if_improves=True
        )
        
        entry_price = 100.0
        state = self.trailing_manager.init_for_position(
            "test_bot", "pos_price", "TEST", "equity",
            entry_price, "long", config
        )
        
        # Move up to $100.25 to arm
        state = self.trailing_manager.update_state(
            "test_bot", "pos_price", "TEST", "equity", 100.25, state
        )
        self.assertTrue(state.armed)
        
        # Stop should be $0.50 below high water
        expected_stop = 100.25 - 0.50
        self.assertAlmostEqual(state.stop_price, expected_stop, places=2)
        
        # Move up to $101
        state = self.trailing_manager.update_state(
            "test_bot", "pos_price", "TEST", "equity", 101.0, state
        )
        new_expected_stop = 101.0 - 0.50
        self.assertAlmostEqual(state.stop_price, new_expected_stop, places=2)
    
    def test_exit_lock_prevents_duplicate_orders(self):
        """Test that exit locks prevent duplicate order submission"""
        
        # Set exit lock
        self.trailing_manager.set_exit_lock(
            "test_bot", "pos123", "TEST", "equity", "order_123"
        )
        
        # Check that lock is active
        has_lock = self.trailing_manager.has_exit_lock(
            "test_bot", "pos123", "TEST", "equity"
        )
        self.assertTrue(has_lock, "Should have active exit lock")
        
        # Clear lock
        self.trailing_manager.clear_exit_lock(
            "test_bot", "pos123", "TEST", "equity"
        )
        
        # Check that lock is cleared
        has_lock = self.trailing_manager.has_exit_lock(
            "test_bot", "pos123", "TEST", "equity"
        )
        self.assertFalse(has_lock, "Should not have exit lock after clearing")
    
    def test_state_persistence(self):
        """Test that trailing stop state persists across restarts"""
        
        # Create and save state
        entry_price = 100.0
        original_state = self.trailing_manager.init_for_position(
            "test_bot", "pos_persist", "TEST", "equity",
            entry_price, "long", self.config
        )
        
        # Update state
        updated_state = self.trailing_manager.update_state(
            "test_bot", "pos_persist", "TEST", "equity", 101.0, original_state
        )
        
        # Create new manager instance (simulate restart)
        new_manager = TrailingStopManager()
        
        # Load state
        loaded_state = new_manager.load_state(
            "test_bot", "pos_persist", "TEST", "equity"
        )
        
        self.assertIsNotNone(loaded_state, "State should persist")
        self.assertEqual(loaded_state.high_water, updated_state.high_water)
        self.assertEqual(loaded_state.stop_price, updated_state.stop_price)
        self.assertEqual(loaded_state.armed, updated_state.armed)
    
    def test_halt_mode_still_manages_positions(self):
        """Test that trailing stops work during halt mode"""
        from ..core.halt import get_halt_manager
        
        # Set system halt
        halt_manager = get_halt_manager()
        halt_manager.set_halt("Test halt for trailing stop test", 60)
        
        # Trailing stop logic should still work
        entry_price = 100.0
        state = self.trailing_manager.init_for_position(
            "test_bot", "pos_halt", "TEST", "equity",
            entry_price, "long", self.config
        )
        
        # Should still update and trigger normally
        state = self.trailing_manager.update_state(
            "test_bot", "pos_halt", "TEST", "equity", 100.50, state
        )
        self.assertTrue(state.armed, "Trailing stops should work during halt")
        
        # Should still trigger exit
        should_exit = self.trailing_manager.should_exit(state, 99.0)
        self.assertTrue(should_exit, "Should still trigger exits during halt")
        
        # Clear halt
        halt_manager.clear_halt()


if __name__ == "__main__":
    unittest.main()
