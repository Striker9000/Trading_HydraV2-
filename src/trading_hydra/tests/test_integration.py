
"""Integration tests for Trading Hydra system behavior"""
import pytest
import os
import time
from unittest.mock import patch, Mock

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../..'))

from trading_hydra.core.state import init_state_store, get_state, set_state
from trading_hydra.core.halt import get_halt_manager
from trading_hydra.orchestrator import get_orchestrator
from trading_hydra.services.fake_broker import get_fake_broker
from trading_hydra.services.execution import get_execution_service
from trading_hydra.risk.trailing_stop import get_trailing_stop_manager, TrailingStopConfig


class TestHaltBehavior:
    """Test that halt doctrine is properly enforced"""
    
    def setup_method(self):
        """Setup test environment with fake broker"""
        self.test_db = "./test_integration_state.db"
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
        init_state_store(db_path=self.test_db)
        
        self.fake_broker = get_fake_broker(initial_equity=10000.0)
        self.halt_manager = get_halt_manager()
        
        # Mock Alpaca client to use fake broker
        self.alpaca_patcher = patch('trading_hydra.services.alpaca_client.get_alpaca_client')
        self.mock_alpaca = self.alpaca_patcher.start()
        self.mock_alpaca.return_value = self.fake_broker
    
    def teardown_method(self):
        """Cleanup"""
        self.alpaca_patcher.stop()
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
    
    def test_halt_blocks_new_trades_allows_position_management(self):
        """Test: Under halt, manage positions but no new entries"""
        # Set up existing position
        self.fake_broker.set_price("AAPL", 150.0)
        self.fake_broker.place_market_order("AAPL", "buy", notional=1000.0)
        
        # Verify position exists
        positions = self.fake_broker.get_positions()
        assert len(positions) == 1
        initial_order_count = len(self.fake_broker.get_order_history())
        
        # Set GLOBAL_TRADING_HALT
        self.halt_manager.set_halt("Test halt for integration test", 60)
        assert self.halt_manager.is_halted()
        
        # Enable a bot for testing (consolidated dict format)
        set_state("bots.mom_AAPL", {"enabled": True, "allowed": True})
        set_state("budgets.mom_AAPL", {"max_daily_loss": 500.0, "max_open_risk": 1000.0})
        
        # Execute one orchestrator loop
        orchestrator = get_orchestrator()
        result = orchestrator.run_loop()
        
        # Verify halt was respected
        assert "HALT" in result.status or "halt" in result.status
        
        # Check that position management occurred (no new entry orders)
        final_order_count = len(self.fake_broker.get_order_history())
        new_orders = final_order_count - initial_order_count
        
        # Should have managed positions but not placed new entry orders
        # (May have exit orders from position management)
        assert new_orders == 0 or all(
            "exit" in order.client_order_id.lower() 
            for order in self.fake_broker.get_order_history()[-new_orders:]
            if order.client_order_id
        )
        
        self.halt_manager.clear_halt()
    
    def test_no_halt_allows_new_trades(self):
        """Test: Without halt, new trades are allowed"""
        # Ensure no halt
        assert not self.halt_manager.is_halted()
        
        # Set up favorable conditions for trading
        self.fake_broker.set_price("AAPL", 150.0)
        
        # Enable bot (consolidated dict format)
        set_state("bots.mom_AAPL", {"enabled": True, "allowed": True})
        set_state("budgets.mom_AAPL", {"max_daily_loss": 500.0, "max_open_risk": 1000.0})
        
        initial_order_count = len(self.fake_broker.get_order_history())
        
        # Mock signal generation to force a trade
        with patch('trading_hydra.services.execution.ExecutionService._generate_momentum_signal') as mock_signal:
            mock_signal.return_value = "buy"
            
            # Execute orchestrator loop
            orchestrator = get_orchestrator()
            result = orchestrator.run_loop()
            
            # Should be successful (no halt)
            assert result.success
            
            # Should have placed at least one order
            final_order_count = len(self.fake_broker.get_order_history())
            assert final_order_count > initial_order_count


class TestIdempotency:
    """Test order idempotency prevents duplicate submissions"""
    
    def setup_method(self):
        """Setup test environment"""
        self.test_db = "./test_idempotency_state.db"
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
        init_state_store(db_path=self.test_db)
        
        self.fake_broker = get_fake_broker(initial_equity=10000.0)
        
        # Mock Alpaca client
        self.alpaca_patcher = patch('trading_hydra.services.alpaca_client.get_alpaca_client')
        self.mock_alpaca = self.alpaca_patcher.start()
        self.mock_alpaca.return_value = self.fake_broker
    
    def teardown_method(self):
        """Cleanup"""
        self.alpaca_patcher.stop()
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
    
    def test_duplicate_client_order_id_prevention(self):
        """Test that duplicate client_order_id is prevented"""
        from trading_hydra.core.state import generate_client_order_id, is_order_already_submitted, record_order_submission
        
        # Generate deterministic order ID
        client_order_id = generate_client_order_id("test_bot", "AAPL", "signal_123")
        
        # First submission should succeed
        assert not is_order_already_submitted(client_order_id)
        record_order_submission(client_order_id, "test_bot", "AAPL", "signal_123")
        
        # Second submission should be blocked
        assert is_order_already_submitted(client_order_id)
        
        # Different signal should get different ID
        client_order_id_2 = generate_client_order_id("test_bot", "AAPL", "signal_456")
        assert client_order_id != client_order_id_2
        assert not is_order_already_submitted(client_order_id_2)


class TestCooldownEnforcement:
    """Test trading cooldown periods"""
    
    def setup_method(self):
        """Setup test environment"""
        self.test_db = "./test_cooldown_state.db"
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
        init_state_store(db_path=self.test_db)
        
        self.fake_broker = get_fake_broker(initial_equity=10000.0)
        
        # Mock Alpaca client
        self.alpaca_patcher = patch('trading_hydra.services.alpaca_client.get_alpaca_client')
        self.mock_alpaca = self.alpaca_patcher.start()
        self.mock_alpaca.return_value = self.fake_broker
        
        self.execution_service = get_execution_service()
    
    def teardown_method(self):
        """Cleanup"""
        self.alpaca_patcher.stop()
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
    
    def test_cooldown_blocks_rapid_trades(self):
        """Test that cooldown prevents rapid successive trades"""
        from trading_hydra.core.state import set_last_trade_timestamp
        
        bot_id = "test_bot"
        symbol = "AAPL"
        
        # Set recent trade timestamp
        set_last_trade_timestamp(bot_id, symbol, time.time())
        
        # Should be in cooldown
        assert self.execution_service._is_in_cooldown(bot_id, symbol, cooldown_seconds=30)
        
        # Should not be in cooldown after sufficient time
        set_last_trade_timestamp(bot_id, symbol, time.time() - 60)  # 60 seconds ago
        assert not self.execution_service._is_in_cooldown(bot_id, symbol, cooldown_seconds=30)


class TestTrailingStopExecution:
    """Test trailing stop exit behavior"""
    
    def setup_method(self):
        """Setup test environment"""
        self.test_db = "./test_trailing_exit_state.db"
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
        init_state_store(db_path=self.test_db)
        
        self.fake_broker = get_fake_broker(initial_equity=10000.0)
        
        # Mock Alpaca client
        self.alpaca_patcher = patch('trading_hydra.services.alpaca_client.get_alpaca_client')
        self.mock_alpaca = self.alpaca_patcher.start()
        self.mock_alpaca.return_value = self.fake_broker
        
        self.trailing_manager = get_trailing_stop_manager()
        self.execution_service = get_execution_service()
    
    def teardown_method(self):
        """Cleanup"""
        self.alpaca_patcher.stop()
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
    
    def test_trailing_stop_exit_no_spam(self):
        """Test trailing stop triggers exit order only once"""
        # Set up position
        self.fake_broker.set_price("AAPL", 100.0)
        self.fake_broker.place_market_order("AAPL", "buy", notional=1000.0, client_order_id="entry_123")
        
        initial_order_count = len(self.fake_broker.get_order_history())
        
        # Initialize trailing stop
        config = TrailingStopConfig(
            enabled=True,
            mode="percent",
            value=1.0,  # 1% trail
            activation_profit_pct=0.3  # Arm after 0.3% profit
        )
        
        bot_id = "test_bot"
        position_id = "pos_123"
        symbol = "AAPL"
        asset_class = "equity"
        
        # Initialize trailing stop state
        trailing_state = self.trailing_manager.init_for_position(
            bot_id, position_id, symbol, asset_class, 100.0, "long", config
        )
        
        # Move price up to arm trailing stop
        self.fake_broker.set_price("AAPL", 100.50)  # +0.5% should arm
        trailing_state = self.trailing_manager.update_state(
            bot_id, position_id, symbol, asset_class, 100.50, trailing_state
        )
        assert trailing_state.armed
        
        # Move price down to trigger exit
        self.fake_broker.set_price("AAPL", 99.0)  # Should trigger stop
        should_exit = self.trailing_manager.should_exit(trailing_state, 99.0)
        assert should_exit
        
        # Submit exit order
        exit_result = self.execution_service.submit_exit_order(
            bot_id, position_id, symbol, asset_class, "sell", 10.0, "market", 99.0
        )
        assert exit_result["success"]
        
        # Verify exactly one exit order was placed
        final_order_count = len(self.fake_broker.get_order_history())
        exit_orders = final_order_count - initial_order_count
        assert exit_orders == 1
        
        # Try to submit again - should be blocked by exit lock
        exit_result_2 = self.execution_service.submit_exit_order(
            bot_id, position_id, symbol, asset_class, "sell", 10.0, "market", 99.0
        )
        assert exit_result_2["skipped"]  # Should be skipped due to lock
        
        # No additional orders should be placed
        assert len(self.fake_broker.get_order_history()) == final_order_count


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
