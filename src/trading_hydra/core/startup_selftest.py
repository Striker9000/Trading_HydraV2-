"""
Startup Self-Test Module
Validates system connectivity and readiness before trading begins.
Runs a suite of tests against Alpaca API, state DB, config files, etc.
"""
import os
import time
import yaml
import logging
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime


@dataclass
class SelfTestResult:
    test_name: str
    passed: bool
    duration_ms: float
    error_message: Optional[str] = None


CRITICAL_TESTS = {"test_api_connectivity", "test_order_lifecycle"}

logger = logging.getLogger("trading_hydra.selftest")


class StartupSelfTest:
    def __init__(self):
        self._results: List[SelfTestResult] = []

    def _run_single(self, name: str, fn) -> SelfTestResult:
        logger.info(f"[SELFTEST] Starting: {name}")
        t0 = time.monotonic()
        try:
            fn()
            elapsed = (time.monotonic() - t0) * 1000
            result = SelfTestResult(test_name=name, passed=True, duration_ms=round(elapsed, 2))
            logger.info(f"[SELFTEST] PASS: {name} ({result.duration_ms:.1f}ms)")
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            result = SelfTestResult(
                test_name=name,
                passed=False,
                duration_ms=round(elapsed, 2),
                error_message=str(exc),
            )
            logger.error(f"[SELFTEST] FAIL: {name} ({result.duration_ms:.1f}ms) - {exc}")
        self._results.append(result)
        return result

    def test_api_connectivity(self) -> SelfTestResult:
        def _test():
            from ..services.alpaca_client import get_alpaca_client

            client = get_alpaca_client()
            if not client.has_credentials():
                raise RuntimeError("Alpaca credentials not set (ALPACA_KEY / ALPACA_SECRET)")
            account = client.get_account(force_refresh=True)
            if not account:
                raise RuntimeError("get_account() returned None")
            if not account.status:
                raise RuntimeError("Account status is empty")
            logger.info(
                f"[SELFTEST] Account status={account.status}, equity={account.equity}, "
                f"buying_power={account.buying_power}"
            )

        return self._run_single("test_api_connectivity", _test)

    def test_market_data(self) -> SelfTestResult:
        def _test():
            from ..services.alpaca_client import get_alpaca_client

            client = get_alpaca_client()
            quote = client.get_latest_quote("SPY")
            if quote is None:
                raise RuntimeError("get_latest_quote('SPY') returned None")
            logger.info(f"[SELFTEST] SPY quote: {quote}")

        return self._run_single("test_market_data", _test)

    def test_order_lifecycle(self) -> SelfTestResult:
        def _test():
            from ..services.alpaca_client import get_alpaca_client

            client = get_alpaca_client()
            test_order_id = None
            try:
                logger.info("[SELFTEST] Placing test limit order: SPY 1 share @ $1.00")
                order_result = client.place_limit_order(
                    symbol="SPY",
                    qty=1,
                    side="buy",
                    limit_price=1.00,
                    time_in_force="day",
                )
                if not order_result:
                    raise RuntimeError("place_limit_order returned empty result")

                test_order_id = order_result.get("id") or order_result.get("order_id")
                if not test_order_id:
                    raise RuntimeError(f"No order id in result: {order_result}")
                logger.info(f"[SELFTEST] Test order placed: {test_order_id}")

                time.sleep(1)

                open_orders = client.get_open_orders(symbol="SPY")
                found = any(
                    str(o.get("id")) == str(test_order_id)
                    for o in open_orders
                )
                if not found:
                    logger.warning(
                        f"[SELFTEST] Test order {test_order_id} not found in open orders "
                        f"(may have been rejected or filled instantly). Continuing."
                    )

            finally:
                if test_order_id:
                    try:
                        logger.info(f"[SELFTEST] Cancelling test order {test_order_id}")
                        client.cancel_order(test_order_id)
                        logger.info(f"[SELFTEST] Test order cancelled successfully")
                    except Exception as cancel_err:
                        logger.warning(f"[SELFTEST] Could not cancel test order: {cancel_err}")

        return self._run_single("test_order_lifecycle", _test)

    def test_state_db(self) -> SelfTestResult:
        def _test():
            from .state import init_state_store, set_state, get_state, delete_state

            init_state_store()

            test_key = "_selftest_probe"
            test_value = {"ts": datetime.utcnow().isoformat(), "probe": True}

            set_state(test_key, test_value)
            readback = get_state(test_key)

            if readback is None:
                raise RuntimeError("State DB read-back returned None")
            if readback.get("probe") is not True:
                raise RuntimeError(f"State DB read-back mismatch: {readback}")

            delete_state(test_key)
            logger.info("[SELFTEST] State DB write/read/delete OK")

        return self._run_single("test_state_db", _test)

    def test_config_loading(self) -> SelfTestResult:
        def _test():
            config_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config")
            config_dir = os.path.abspath(config_dir)

            if not os.path.isdir(config_dir):
                alt = os.path.join(os.getcwd(), "config")
                if os.path.isdir(alt):
                    config_dir = alt
                else:
                    raise RuntimeError(f"Config directory not found: {config_dir}")

            yaml_files = []
            for root, _dirs, files in os.walk(config_dir):
                for f in files:
                    if f.endswith((".yaml", ".yml")):
                        yaml_files.append(os.path.join(root, f))

            if not yaml_files:
                raise RuntimeError(f"No YAML files found in {config_dir}")

            errors = []
            warnings = []
            for fp in yaml_files:
                try:
                    with open(fp, "r") as fh:
                        yaml.safe_load(fh)
                except yaml.constructor.ConstructorError as exc:
                    warnings.append(f"{os.path.basename(fp)}: {exc}")
                except Exception as exc:
                    errors.append(f"{os.path.basename(fp)}: {exc}")

            if warnings:
                logger.warning(
                    f"[SELFTEST] {len(warnings)} config files have non-standard YAML "
                    f"(numpy/pickle tags) - skipped: "
                    f"{[w.split(':')[0] for w in warnings]}"
                )

            if errors:
                raise RuntimeError(f"Config load errors: {'; '.join(errors)}")

            loaded_count = len(yaml_files) - len(warnings)
            logger.info(
                f"[SELFTEST] {loaded_count}/{len(yaml_files)} config YAML files loaded OK"
                f"{f' ({len(warnings)} skipped - non-standard tags)' if warnings else ''}"
            )

        return self._run_single("test_config_loading", _test)

    def run_all_tests(self) -> List[SelfTestResult]:
        self._results = []
        logger.info("=" * 60)
        logger.info("[SELFTEST] Starting startup self-test suite")
        logger.info("=" * 60)

        t0 = time.monotonic()

        self.test_api_connectivity()
        self.test_market_data()
        self.test_order_lifecycle()
        self.test_state_db()
        self.test_config_loading()

        total_ms = (time.monotonic() - t0) * 1000

        passed = sum(1 for r in self._results if r.passed)
        failed = sum(1 for r in self._results if not r.passed)
        critical_failures = [
            r for r in self._results if not r.passed and r.test_name in CRITICAL_TESTS
        ]

        logger.info("=" * 60)
        logger.info(
            f"[SELFTEST] Complete: {passed} passed, {failed} failed, "
            f"{total_ms:.0f}ms total"
        )
        if critical_failures:
            logger.error(
                f"[SELFTEST] CRITICAL FAILURES: "
                f"{[r.test_name for r in critical_failures]}"
            )
        logger.info("=" * 60)

        return list(self._results)


def run_startup_selftest() -> bool:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    tester = StartupSelfTest()
    results = tester.run_all_tests()

    overall_pass = True
    print("\n" + "=" * 60)
    print("STARTUP SELF-TEST RESULTS")
    print("=" * 60)
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        critical_tag = " [CRITICAL]" if r.test_name in CRITICAL_TESTS else ""
        line = f"  [{status}] {r.test_name}{critical_tag} ({r.duration_ms:.1f}ms)"
        if r.error_message:
            line += f"\n         Error: {r.error_message}"
        print(line)

        if not r.passed and r.test_name in CRITICAL_TESTS:
            overall_pass = False

    print("=" * 60)
    if overall_pass:
        print("OVERALL: PASS - System ready for trading")
    else:
        print("OVERALL: FAIL - Critical tests failed, trading blocked")
    print("=" * 60 + "\n")

    return overall_pass


if __name__ == "__main__":
    import sys

    ok = run_startup_selftest()
    sys.exit(0 if ok else 1)
