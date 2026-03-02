
"""Unit tests for all core Trading Hydra modules"""
import pytest
import os
import json
import time
import sqlite3
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta

# Test infrastructure setup
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../..'))

from trading_hydra.core.state import init_state_store, get_state, set_state, delete_state
from trading_hydra.core.logging import get_logger
from trading_hydra.core.halt import HaltManager
from trading_hydra.core.health import HealthMonitor
from trading_hydra.core.risk import valid_budget, pct_from_dollars
from trading_hydra.risk.trailing_stop import TrailingStopManager, TrailingStopConfig, TrailingStopState


class TestStateStore:
    """Test state persistence and SQLite operations"""
    
    def setup_method(self):
        """Setup test environment"""
        self.test_db = "./test_state.db"
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
        # Use the actual init_state_store function
        init_state_store()
    
    def teardown_method(self):
        """Cleanup test files"""
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
    
    def test_set_get_state(self):
        """Test basic state operations"""
        set_state("test_key", {"value": 123, "timestamp": "2024-01-01"})
        result = get_state("test_key")
        assert result["value"] == 123
        assert result["timestamp"] == "2024-01-01"
    
    def test_state_persistence(self):
        """Test state survives process restart"""
        # Set state
        set_state("persistent_key", {"data": "important"})
        
        # Simulate new process by creating new connection
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM state WHERE key = ?", ("persistent_key",))
        row = cursor.fetchone()
        conn.close()
        
        assert row is not None
        stored_data = json.loads(row[0])
        assert stored_data["data"] == "important"
    
    def test_delete_state(self):
        """Test state deletion"""
        set_state("delete_me", {"temp": True})
        assert get_state("delete_me") is not None
        
        success = delete_state("delete_me")
        assert success is True
        assert get_state("delete_me") is None
    
    def test_default_values(self):
        """Test default value handling"""
        assert get_state("nonexistent") is None
        assert get_state("nonexistent", "default") == "default"
        assert get_state("nonexistent", {"default": "object"}) == {"default": "object"}


class TestLogging:
    """Test JSONL logging functionality"""
    
    def setup_method(self):
        """Setup test environment"""
        self.test_log = "./test.jsonl"
        if os.path.exists(self.test_log):
            os.remove(self.test_log)
    
    def teardown_method(self):
        """Cleanup test files"""
        if os.path.exists(self.test_log):
            os.remove(self.test_log)
    
    def test_jsonl_output(self):
        """Test logger writes proper JSONL format"""
        logger = get_logger()
        
        # Mock the log file path for testing
        with patch('trading_hydra.core.logging._ensure_log_directory'):
            with patch('builtins.open', create=True) as mock_open:
                mock_file = MagicMock()
                mock_open.return_value.__enter__.return_value = mock_file
                
                logger.log("test_event", {"key": "value", "number": 42})
                
                # Verify file was opened for append
                mock_open.assert_called()
                # Verify write was called
                mock_file.write.assert_called()
                
                # Get the written data
                written_data = mock_file.write.call_args[0][0]
                log_entry = json.loads(written_data.strip())
                
                assert log_entry["event"] == "test_event"
                assert log_entry["key"] == "value"
                assert log_entry["number"] == 42
                assert "timestamp" in log_entry
    
    def test_log_structure(self):
        """Test log entries have required fields"""
        logger = get_logger()
        
        with patch('trading_hydra.core.logging._ensure_log_directory'):
            with patch('builtins.open', create=True) as mock_open:
                mock_file = MagicMock()
                mock_open.return_value.__enter__.return_value = mock_file
                
                logger.log("structure_test", {"data": "test"})
                
                written_data = mock_file.write.call_args[0][0]
                log_entry = json.loads(written_data.strip())
                
                # Required fields
                assert "timestamp" in log_entry
                assert "event" in log_entry
                assert "data" in log_entry
                
                # Timestamp format
                assert log_entry["timestamp"].endswith("Z")


class TestHaltManager:
    """Test halt management and GLOBAL_TRADING_HALT doctrine"""
    
    def setup_method(self):
        """Setup test environment"""
        self.test_db = "./test_halt_state.db"
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
        init_state_store(db_path=self.test_db)
        self.halt_manager = HaltManager()
    
    def teardown_method(self):
        """Cleanup"""
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
    
    def test_set_halt(self):
        """Test setting GLOBAL_TRADING_HALT"""
        assert not self.halt_manager.is_halted()
        
        self.halt_manager.set_halt("Test halt", duration_seconds=30)
        assert self.halt_manager.is_halted()
        
        status = self.halt_manager.get_status()
        assert status.active is True
        assert status.reason == "Test halt"
    
    def test_halt_persistence(self):
        """Test halt survives manager restart"""
        self.halt_manager.set_halt("Persistent halt", duration_seconds=60)
        
        # Create new manager instance (simulate restart)
        new_manager = HaltManager()
        assert new_manager.is_halted()
        
        status = new_manager.get_status()
        assert status.reason == "Persistent halt"
    
    def test_halt_expiry(self):
        """Test automatic halt expiry"""
        self.halt_manager.set_halt("Short halt", duration_seconds=1)
        assert self.halt_manager.is_halted()
        
        # Wait for expiry
        time.sleep(1.1)
        assert not self.halt_manager.is_halted()
    
    def test_manual_clear(self):
        """Test manual halt clearing"""
        self.halt_manager.set_halt("Manual halt", duration_seconds=3600)
        assert self.halt_manager.is_halted()
        
        self.halt_manager.clear_halt()
        assert not self.halt_manager.is_halted()
    
    def test_config_override_forces_halt(self):
        """Test config override forces halt but cannot auto-clear"""
        with patch('trading_hydra.core.config.load_settings') as mock_config:
            mock_config.return_value = {
                "trading": {"global_halt": True}
            }
            
            # Config override should force halt
            manager = HaltManager()
            assert manager.is_halted()
            
            # Should not be able to clear when config override is active
            manager.clear_halt()
            assert manager.is_halted()


class TestHealthMonitor:
    """Test health monitoring and failure tracking"""
    
    def setup_method(self):
        self.health = HealthMonitor()
    
    def test_api_failure_tracking(self):
        """Test API failure counting"""
        # Should start healthy
        assert self.health.is_healthy()
        
        # Record some failures
        for i in range(3):
            self.health.record_api_failure(f"Error {i}")
        
        # Should still be healthy (under threshold)
        assert self.health.is_healthy()
        
        # Add more failures to cross threshold
        for i in range(5):
            self.health.record_api_failure(f"Error {i+3}")
        
        # Should now be unhealthy
        assert not self.health.is_healthy()
    
    def test_price_tick_recovery(self):
        """Test recovery through successful price ticks"""
        # Make unhealthy
        for i in range(10):
            self.health.record_api_failure(f"Error {i}")
        assert not self.health.is_healthy()
        
        # Record successful ticks
        for i in range(20):
            self.health.record_price_tick()
        
        # Should recover
        assert self.health.is_healthy()


class TestRiskHelpers:
    """Test risk calculation utilities"""
    
    def test_valid_budget(self):
        """Test budget validation"""
        assert valid_budget(100.0) is True
        assert valid_budget(0.0) is False
        assert valid_budget(-10.0) is False
        assert valid_budget(None) is False
    
    def test_calculate_max_loss_pct(self):
        """Test max loss percentage calculation"""
        # 5% of $10,000 = $500
        max_loss = calculate_max_loss_pct(10000.0, 5.0)
        assert max_loss == 500.0
        
        # Edge cases
        assert calculate_max_loss_pct(0.0, 5.0) == 0.0
        assert calculate_max_loss_pct(1000.0, 0.0) == 0.0


class TestTrailingStopManager:
    """Test trailing stop logic and persistence"""
    
    def setup_method(self):
        self.test_db = "./test_trailing_state.db"
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
        init_state_store(db_path=self.test_db)
        
        self.trailing_manager = TrailingStopManager()
        self.config = TrailingStopConfig(
            enabled=True,
            mode="percent",
            value=1.0,  # 1% trail
            activation_profit_pct=0.3,  # Activate after 0.3% profit
            update_only_if_improves=True
        )
    
    def teardown_method(self):
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
    
    def test_long_trailing_stop_logic(self):
        """Test long position trailing stop"""
        # Initialize at $100
        state = self.trailing_manager.init_for_position(
            "test_bot", "pos123", "TEST", "equity", 
            100.0, "long", self.config
        )
        
        # Move to $100.50 - should arm
        state = self.trailing_manager.update_state(
            "test_bot", "pos123", "TEST", "equity", 100.50, state
        )
        assert state.armed is True
        assert state.high_water == 100.50
        
        # Should trigger exit below stop
        should_exit = self.trailing_manager.should_exit(state, 99.0)
        assert should_exit is True
    
    def test_short_trailing_stop_logic(self):
        """Test short position trailing stop"""
        # Initialize short at $100
        state = self.trailing_manager.init_for_position(
            "test_bot", "pos456", "TEST", "equity",
            100.0, "short", self.config
        )
        
        # Move to $99.50 - should arm
        state = self.trailing_manager.update_state(
            "test_bot", "pos456", "TEST", "equity", 99.50, state
        )
        assert state.armed is True
        assert state.low_water == 99.50
        
        # Should trigger exit above stop
        should_exit = self.trailing_manager.should_exit(state, 101.0)
        assert should_exit is True
    
    def test_state_persistence(self):
        """Test trailing stop state persistence"""
        # Create and save state
        state = self.trailing_manager.init_for_position(
            "test_bot", "pos_persist", "TEST", "equity",
            100.0, "long", self.config
        )
        
        # Update state
        updated_state = self.trailing_manager.update_state(
            "test_bot", "pos_persist", "TEST", "equity", 101.0, state
        )
        
        # Load from new manager (simulate restart)
        new_manager = TrailingStopManager()
        loaded_state = new_manager.load_state(
            "test_bot", "pos_persist", "TEST", "equity"
        )
        
        assert loaded_state is not None
        assert loaded_state.high_water == updated_state.high_water
        assert loaded_state.armed == updated_state.armed


if __name__ == "__main__":
    pytest.main([__file__])
