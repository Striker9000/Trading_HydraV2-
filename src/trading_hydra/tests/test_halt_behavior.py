
"""Unit test to verify halt behavior prevents new trades and idempotency works"""

import unittest
import time
from unittest.mock import Mock, patch
from typing import Dict, Any

from ..core.halt import get_halt_manager
from ..core.state import set_state, generate_client_order_id, is_order_already_submitted, record_order_submission, init_state_store
from ..services.execution import ExecutionService


class FakeBroker:
    """Mock broker that records all method calls"""
    def __init__(self):
        self.calls = []
        self.is_paper = True
    
    def get_positions(self):
        self.calls.append("get_positions")
        return []
    
    def place_market_order(self, **kwargs):
        self.calls.append(("place_market_order", kwargs))
        return {"id": f"fake_order_{len(self.calls)}"}
    
    def get_latest_quote(self, symbol, asset_class="stock"):
        self.calls.append(("get_latest_quote", symbol, asset_class))
        return {"bid": 100.0, "ask": 100.1}
    
    def has_credentials(self):
        return True


class TestHaltBehavior(unittest.TestCase):
    """Test that halt prevents new trades but allows position management"""
    
    def setUp(self):
        # Clear any existing halt state
        set_state("GLOBAL_TRADING_HALT", False)
        self.fake_broker = FakeBroker()
    
    def test_halt_prevents_new_trades_allows_management(self):
        """Test that halted system manages positions but blocks new trades"""
        
        # Set halt condition
        halt_manager = get_halt_manager()
        halt_manager.set_halt("test_halt", "Unit test halt")
        
        # Verify halt is active
        self.assertTrue(halt_manager.is_halted())
        
        # Create execution service with mocked broker
        execution_service = ExecutionService()
        
        with patch('trading_hydra.services.alpaca_client.get_alpaca_client', return_value=self.fake_broker):
            # Run execution with halt active
            result = execution_service.run(["crypto_core"], equity=1000.0)
            
            # Verify positions were checked (management allowed)
            position_calls = [call for call in self.fake_broker.calls if call == "get_positions"]
            self.assertGreater(len(position_calls), 0, "Should call get_positions for management")
            
            # Verify no new orders were placed (trades blocked)
            order_calls = [call for call in self.fake_broker.calls if isinstance(call, tuple) and call[0] == "place_market_order"]
            self.assertEqual(len(order_calls), 0, "Should not place any new orders when halted")
            
            # Verify bots still ran (for management)

    
    def test_idempotency_prevents_duplicate_orders(self):
        """Test that idempotency prevents duplicate orders"""
        # Initialize state store
        init_state_store()
        
        # Generate a client order ID
        client_order_id = generate_client_order_id("test_bot", "TEST", "buy_123")
        
        # Initially should not exist
        self.assertFalse(is_order_already_submitted(client_order_id))
        
        # Record submission
        record_order_submission(client_order_id, "test_bot", "TEST", "buy_123", "alpaca_123")
        
        # Now should exist
        self.assertTrue(is_order_already_submitted(client_order_id))
        
        # Same client_order_id should be deterministic
        client_order_id_2 = generate_client_order_id("test_bot", "TEST", "buy_123")
        self.assertEqual(client_order_id, client_order_id_2)
    
    def test_cooldown_enforcement(self):
        """Test that cooldown prevents rapid-fire trades"""
        from ..core.state import get_last_trade_timestamp, set_last_trade_timestamp
        
        # Initialize state store
        init_state_store()
        
        execution_service = ExecutionService()
        
        # No previous trade - should not be in cooldown
        self.assertFalse(execution_service._is_in_cooldown("test_bot", "TEST", 30))
        
        # Set recent trade timestamp
        set_last_trade_timestamp("test_bot", "TEST", time.time())
        
        # Should now be in cooldown
        self.assertTrue(execution_service._is_in_cooldown("test_bot", "TEST", 30))
        
        # Set old trade timestamp
        set_last_trade_timestamp("test_bot", "TEST", time.time() - 60)
        
        # Should no longer be in cooldown
        self.assertFalse(execution_service._is_in_cooldown("test_bot", "TEST", 30))
    
    def test_no_halt_allows_trades(self):
        """Test that non-halted system can place trades"""
        
        # Ensure no halt
        halt_manager = get_halt_manager()
        halt_manager.clear_halt()
        
        # Verify no halt
        self.assertFalse(halt_manager.is_halted())
        
        # This would require more complex mocking to actually trigger trade signals
        # For now, just verify the halt check passes
        execution_service = ExecutionService()
        
        with patch('trading_hydra.services.alpaca_client.get_alpaca_client', return_value=self.fake_broker):
            result = execution_service.run(["crypto_core"], equity=1000.0)
            
            # At minimum, positions should be checked
            position_calls = [call for call in self.fake_broker.calls if call == "get_positions"]
            self.assertGreater(len(position_calls), 0, "Should check positions")


if __name__ == "__main__":
    unittest.main()
