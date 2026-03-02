#!/usr/bin/env python3
"""Run comprehensive tests for Trading Hydra"""

import sys
import os
import unittest

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

def run_all_tests():
    """Run all Trading Hydra tests"""

    # Discover and run tests
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add test modules
    from trading_hydra.tests.test_halt_behavior import TestHaltBehavior
    from trading_hydra.tests.test_trailing_stops import TestTrailingStops

    suite.addTests(loader.loadTestsFromTestCase(TestHaltBehavior))
    suite.addTests(loader.loadTestsFromTestCase(TestTrailingStops))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return result.wasSuccessful()

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)