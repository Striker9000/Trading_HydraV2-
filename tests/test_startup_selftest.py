"""
Tests for the startup self-test module.
Run with:  cd export && python tests/test_startup_selftest.py
"""
import sys
import os
import logging
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

passed = 0
failed = 0


def run_test(name, fn):
    global passed, failed
    try:
        fn()
        print(f"  [PASS] {name}")
        passed += 1
    except Exception as exc:
        print(f"  [FAIL] {name}: {exc}")
        traceback.print_exc()
        failed += 1


def test_import():
    from src.trading_hydra.core.startup_selftest import (
        StartupSelfTest,
        SelfTestResult,
        run_startup_selftest,
        CRITICAL_TESTS,
    )
    assert StartupSelfTest is not None
    assert SelfTestResult is not None
    assert callable(run_startup_selftest)
    assert "test_api_connectivity" in CRITICAL_TESTS
    assert "test_order_lifecycle" in CRITICAL_TESTS


def test_selftest_result_dataclass():
    from src.trading_hydra.core.startup_selftest import SelfTestResult

    r = SelfTestResult(test_name="example", passed=True, duration_ms=42.0)
    assert r.test_name == "example"
    assert r.passed is True
    assert r.duration_ms == 42.0
    assert r.error_message is None

    r2 = SelfTestResult(test_name="bad", passed=False, duration_ms=1.0, error_message="boom")
    assert r2.error_message == "boom"


def test_api_connectivity():
    from src.trading_hydra.core.startup_selftest import StartupSelfTest

    tester = StartupSelfTest()
    result = tester.test_api_connectivity()
    assert result.passed, f"API connectivity failed: {result.error_message}"
    assert result.duration_ms >= 0


def test_config_loading():
    from src.trading_hydra.core.startup_selftest import StartupSelfTest

    tester = StartupSelfTest()
    result = tester.test_config_loading()
    assert result.passed, f"Config loading failed: {result.error_message}"


def test_state_db():
    from src.trading_hydra.core.startup_selftest import StartupSelfTest

    tester = StartupSelfTest()
    result = tester.test_state_db()
    assert result.passed, f"State DB failed: {result.error_message}"


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("test_startup_selftest.py")
    print("=" * 60)

    run_test("test_import", test_import)
    run_test("test_selftest_result_dataclass", test_selftest_result_dataclass)
    run_test("test_api_connectivity", test_api_connectivity)
    run_test("test_config_loading", test_config_loading)
    run_test("test_state_db", test_state_db)

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60 + "\n")

    sys.exit(0 if failed == 0 else 1)
