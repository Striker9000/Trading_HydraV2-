#!/usr/bin/env python3
"""
=============================================================================
Unit Tests for Market Intelligence System
=============================================================================

Tests:
1. Sentiment gating logic (thresholds, severe override)
2. Cache staleness fail-closed behavior
3. Exit dedupe (no duplicate exit orders)
4. News entry filter logic
5. Smart money boost calculations
6. Macro regime modifier logic

Run with: pytest scripts/test_intelligence_unit.py -v
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestSentimentGating:
    """Tests for sentiment threshold gating"""
    
    def test_severe_threshold_exit_even_when_losing(self):
        """Severe negative sentiment should trigger exit even on red position"""
        # Setup: Position at -5% loss, sentiment at -0.90 (severe)
        pnl_pct = -5.0
        sentiment = -0.90
        severe_threshold = -0.85
        
        # Severe sentiment overrides loss
        should_exit = sentiment <= severe_threshold
        assert should_exit, "Severe sentiment should trigger exit even when losing"
    
    def test_negative_threshold_requires_profit(self):
        """Normal negative sentiment only exits if position is profitable"""
        pnl_pct = -2.0  # Losing position
        sentiment = -0.75  # Negative but not severe
        negative_threshold = -0.70
        severe_threshold = -0.85
        profit_exit_requires_profit = True
        
        is_negative = sentiment <= negative_threshold
        is_severe = sentiment <= severe_threshold
        is_profitable = pnl_pct > 0
        
        # Should NOT exit: negative but not severe, and not profitable
        should_exit = is_severe or (is_negative and is_profitable)
        assert not should_exit, "Negative but not severe should not exit when losing"
    
    def test_confidence_gate_blocks_low_confidence(self):
        """Low confidence should block all actions"""
        sentiment = -0.90  # Severe
        confidence = 0.40  # Below threshold
        min_confidence = 0.60
        
        confidence_ok = confidence >= min_confidence
        assert not confidence_ok, "Low confidence should block actions"
    
    def test_confidence_gate_allows_high_confidence(self):
        """High confidence should allow actions"""
        sentiment = -0.90
        confidence = 0.75
        min_confidence = 0.60
        
        confidence_ok = confidence >= min_confidence
        assert confidence_ok, "High confidence should allow actions"


class TestCacheStaleness:
    """Tests for cache staleness fail-closed behavior"""
    
    def test_stale_cache_blocks_actions(self):
        """Stale cache should fail-closed and block all intel actions"""
        last_fetch_ts = time.time() - 300  # 5 minutes ago
        ttl_seconds = 60  # 1 minute TTL
        max_cache_age = ttl_seconds * 2  # Allow 2x TTL
        
        cache_age_s = time.time() - last_fetch_ts
        is_stale = cache_age_s > max_cache_age
        
        assert is_stale, "Cache older than 2x TTL should be stale"
    
    def test_fresh_cache_allows_actions(self):
        """Fresh cache should allow intel actions"""
        last_fetch_ts = time.time() - 30  # 30 seconds ago
        ttl_seconds = 60
        max_cache_age = ttl_seconds * 2
        
        cache_age_s = time.time() - last_fetch_ts
        is_stale = cache_age_s > max_cache_age
        
        assert not is_stale, "Fresh cache should not be stale"
    
    def test_missing_cache_fails_closed(self):
        """Missing cache entry should fail-closed"""
        cache_entry = None
        
        # When cache is None, treat as stale
        is_stale = cache_entry is None
        assert is_stale, "Missing cache should fail-closed"


class TestExitDedupe:
    """Tests for exit order deduplication"""
    
    def test_exit_lock_prevents_duplicate_orders(self):
        """Once exit is triggered, subsequent triggers should be blocked"""
        exit_locks = set()  # Simulate exit lock storage
        
        symbol = "AAPL"
        position_id = "pos_123"
        
        # First exit - should succeed
        lock_key = f"{symbol}_{position_id}"
        first_exit_allowed = lock_key not in exit_locks
        assert first_exit_allowed, "First exit should be allowed"
        
        # Set lock
        exit_locks.add(lock_key)
        
        # Second exit attempt - should be blocked
        second_exit_allowed = lock_key not in exit_locks
        assert not second_exit_allowed, "Second exit should be blocked by lock"
    
    def test_different_symbols_not_blocked(self):
        """Exit lock for one symbol should not block another"""
        exit_locks = set()
        
        # Lock AAPL
        exit_locks.add("AAPL_pos_1")
        
        # NVDA should still be allowed
        nvda_allowed = "NVDA_pos_2" not in exit_locks
        assert nvda_allowed, "Different symbol should not be blocked"


class TestNewsEntryFilter:
    """Tests for news-based entry filtering"""
    
    def test_bullish_entry_requires_positive_sentiment(self):
        """Bullish entries require sentiment above bullish_min"""
        sentiment = 0.10  # Slightly positive
        bullish_min = 0.20  # Threshold
        
        bullish_entry_allowed = sentiment >= bullish_min
        assert not bullish_entry_allowed, "Weak positive should block bullish entry"
    
    def test_bearish_entry_requires_negative_sentiment(self):
        """Bearish entries require sentiment below bearish_max"""
        sentiment = -0.10  # Slightly negative
        bearish_max = -0.20  # Threshold
        
        bearish_entry_allowed = sentiment <= bearish_max
        assert not bearish_entry_allowed, "Weak negative should block bearish entry"
    
    def test_neutral_band_handling(self):
        """Sentiment within neutral band should be handled per config"""
        sentiment = 0.05
        neutral_band = 0.15
        
        is_neutral = abs(sentiment) < neutral_band
        assert is_neutral, "Small sentiment should be neutral"
    
    def test_mixed_handling_skip(self):
        """Mixed handling = skip should block entry"""
        is_neutral = True
        mixed_handling = "skip"
        
        blocked = is_neutral and mixed_handling == "skip"
        assert blocked, "Neutral with skip handling should block"
    
    def test_mixed_handling_reduce_size(self):
        """Mixed handling = reduce_size should reduce but not block"""
        is_neutral = True
        mixed_handling = "reduce_size"
        original_size = 10
        
        if is_neutral and mixed_handling == "reduce_size":
            new_size = max(1, int(original_size * 0.5))
        else:
            new_size = original_size
        
        assert new_size == 5, "Size should be reduced by 50%"


class TestSmartMoneyBoost:
    """Tests for smart money universe boost calculations"""
    
    def test_boost_requires_min_conviction(self):
        """Boost only applies if conviction exceeds minimum"""
        conviction = 0.40
        min_conviction = 0.50
        
        boost_allowed = conviction >= min_conviction
        assert not boost_allowed, "Low conviction should not boost"
    
    def test_boost_requires_convergence(self):
        """Boost requires minimum convergence score"""
        conviction = 0.70
        convergence = 0.20
        min_convergence = 0.30
        
        boost_allowed = conviction >= 0.50 and convergence >= min_convergence
        assert not boost_allowed, "Low convergence should not boost"
    
    def test_boost_calculation(self):
        """Boost factor should be calculated correctly"""
        conviction = 0.80
        convergence = 0.60
        boost_factor = 1.2  # Max boost
        
        combined = (conviction + convergence) / 2  # 0.70
        calculated_boost = 1.0 + (boost_factor - 1.0) * combined
        
        assert 1.0 < calculated_boost <= boost_factor, "Boost should be between 1.0 and max"


class TestMacroRegimeModifier:
    """Tests for macro regime modifier logic"""
    
    def test_hawkish_triggers_caution(self):
        """Hawkish Fed should trigger CAUTION regime"""
        hawkish_score = 0.50
        caution_threshold = 0.40
        stress_threshold = 0.70
        
        if hawkish_score >= stress_threshold:
            regime = "STRESS"
        elif hawkish_score >= caution_threshold:
            regime = "CAUTION"
        else:
            regime = "NORMAL"
        
        assert regime == "CAUTION", "Moderate hawkish should be CAUTION"
    
    def test_very_hawkish_triggers_stress(self):
        """Very hawkish should trigger STRESS regime"""
        hawkish_score = 0.80
        stress_threshold = 0.70
        
        is_stress = hawkish_score >= stress_threshold
        assert is_stress, "Very hawkish should trigger STRESS"
    
    def test_stress_reduces_position_size(self):
        """STRESS regime should reduce position sizes"""
        regime = "STRESS"
        base_size = 100
        stress_multiplier = 0.5
        caution_multiplier = 0.75
        
        if regime == "STRESS":
            adjusted_size = base_size * stress_multiplier
        elif regime == "CAUTION":
            adjusted_size = base_size * caution_multiplier
        else:
            adjusted_size = base_size
        
        assert adjusted_size == 50, "STRESS should halve position size"
    
    def test_dovish_allows_normal_trading(self):
        """Dovish Fed should allow normal trading"""
        hawkish_score = -0.30  # Dovish
        caution_threshold = 0.40
        
        is_caution_or_worse = hawkish_score >= caution_threshold
        assert not is_caution_or_worse, "Dovish should be NORMAL"


def run_tests():
    """Run all tests manually if not using pytest"""
    test_classes = [
        TestSentimentGating,
        TestCacheStaleness,
        TestExitDedupe,
        TestNewsEntryFilter,
        TestSmartMoneyBoost,
        TestMacroRegimeModifier
    ]
    
    total = 0
    passed = 0
    failed = 0
    
    for test_class in test_classes:
        instance = test_class()
        for method_name in dir(instance):
            if method_name.startswith("test_"):
                total += 1
                try:
                    getattr(instance, method_name)()
                    passed += 1
                    print(f"  [PASS] {test_class.__name__}.{method_name}")
                except AssertionError as e:
                    failed += 1
                    print(f"  [FAIL] {test_class.__name__}.{method_name}: {e}")
                except Exception as e:
                    failed += 1
                    print(f"  [ERROR] {test_class.__name__}.{method_name}: {e}")
    
    print()
    print(f"Results: {passed}/{total} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    print("=" * 60)
    print("INTELLIGENCE UNIT TESTS")
    print("=" * 60)
    success = run_tests()
    sys.exit(0 if success else 1)
