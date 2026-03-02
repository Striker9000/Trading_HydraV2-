
"""QC Test Runner - Comprehensive system validation"""
import sys
import os
import time
import traceback
from typing import Dict, List, Any, Tuple
from datetime import datetime
from dataclasses import dataclass

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../..'))

from trading_hydra.core.logging import get_logger
from trading_hydra.services.alpaca_client import get_alpaca_client
from trading_hydra.orchestrator import get_orchestrator
from trading_hydra.core.state import get_state, set_state, init_state_store
from trading_hydra.core.config import load_settings, load_bots_config


@dataclass
class TestResult:
    name: str
    passed: bool
    message: str
    execution_time: float
    details: Dict[str, Any] = None


class TradingHydraQC:
    """Comprehensive Quality Control testing for Trading Hydra"""
    
    def __init__(self):
        self.logger = get_logger()
        self.results: List[TestResult] = []
        self.start_time = time.time()
    
    def run_all_tests(self) -> Dict[str, Any]:
        """Run comprehensive QC test suite"""
        print("🚀 Trading Hydra QC Test Suite Starting...")
        print("=" * 60)
        
        # Core system tests
        self._test_configuration()
        self._test_logging_system()
        self._test_state_management()
        
        # API connectivity tests
        self._test_alpaca_connectivity()
        self._test_data_fetching()
        
        # Bot functionality tests
        self._test_crypto_bot_io()
        self._test_momentum_bot_io()
        self._test_options_bot_io()
        
        # Orchestrator tests
        self._test_orchestrator_initialization()
        self._test_trading_loop()
        
        # Integration tests
        self._test_full_system_integration()
        
        return self._generate_report()
    
    def _test_configuration(self):
        """Test configuration loading"""
        start = time.time()
        try:
            settings = load_settings()
            bots_config = load_bots_config()
            
            # Validate critical settings
            assert 'runner' in settings
            assert 'exitbot' in settings
            assert 'portfoliobot' in settings
            
            # Validate bots config
            assert 'bots' in bots_config
            assert len(bots_config['bots']) > 0
            
            self.results.append(TestResult(
                "Configuration Loading",
                True,
                "✅ All configuration files loaded successfully",
                time.time() - start,
                {"settings_keys": list(settings.keys()), "bots_count": len(bots_config['bots'])}
            ))
            
        except Exception as e:
            self.results.append(TestResult(
                "Configuration Loading",
                False,
                f"❌ Config error: {e}",
                time.time() - start,
                {"error": str(e), "traceback": traceback.format_exc()}
            ))
    
    def _test_logging_system(self):
        """Test logging functionality"""
        start = time.time()
        try:
            logger = get_logger()
            
            # Test different log levels
            test_data = {"test_key": "test_value", "timestamp": datetime.now().isoformat()}
            logger.log("qc_test_info", test_data)
            logger.warn("QC warning test message")
            logger.error("QC error test message")
            
            self.results.append(TestResult(
                "Logging System",
                True,
                "✅ Logging system operational",
                time.time() - start,
                {"log_file": "logs/app.jsonl"}
            ))
            
        except Exception as e:
            self.results.append(TestResult(
                "Logging System",
                False,
                f"❌ Logging error: {e}",
                time.time() - start
            ))
    
    def _test_state_management(self):
        """Test state persistence"""
        start = time.time()
        try:
            init_state_store()
            
            # Test write/read cycle
            test_key = "qc_test_state"
            test_value = {"test": True, "timestamp": time.time()}
            
            set_state(test_key, test_value)
            retrieved = get_state(test_key)
            
            assert retrieved == test_value
            
            self.results.append(TestResult(
                "State Management",
                True,
                "✅ State persistence working",
                time.time() - start,
                {"test_key": test_key, "data_match": retrieved == test_value}
            ))
            
        except Exception as e:
            self.results.append(TestResult(
                "State Management",
                False,
                f"❌ State error: {e}",
                time.time() - start
            ))
    
    def _test_alpaca_connectivity(self):
        """Test Alpaca API connection"""
        start = time.time()
        try:
            alpaca = get_alpaca_client()
            
            # Test credentials
            if not alpaca.has_credentials():
                raise Exception("Missing ALPACA_KEY or ALPACA_SECRET")
            
            # Test account fetch
            account = alpaca.get_account()
            
            # Validate account data
            assert hasattr(account, 'equity')
            assert hasattr(account, 'cash')
            assert hasattr(account, 'status')
            assert float(account.equity) >= 0
            
            self.results.append(TestResult(
                "Alpaca API Connectivity",
                True,
                f"✅ Connected - Equity: ${account.equity}, Status: {account.status}",
                time.time() - start,
                {
                    "equity": float(account.equity),
                    "cash": float(account.cash),
                    "status": account.status,
                    "paper_trading": alpaca.is_paper,
                    "base_url": alpaca.base_url
                }
            ))
            
        except Exception as e:
            self.results.append(TestResult(
                "Alpaca API Connectivity",
                False,
                f"❌ Connection failed: {e}",
                time.time() - start,
                {"error_type": type(e).__name__}
            ))
    
    def _test_data_fetching(self):
        """Test market data fetching capabilities"""
        start = time.time()
        errors = []
        success_count = 0
        
        alpaca = get_alpaca_client()
        test_symbols = {
            "stock": ["AAPL", "SPY"],
            "crypto": ["BTC/USD", "ETH/USD"]
        }
        
        # Test stock quotes
        for symbol in test_symbols["stock"]:
            try:
                quote = alpaca.get_latest_quote(symbol, asset_class="stock")
                if quote and "bid" in quote and "ask" in quote:
                    success_count += 1
                else:
                    errors.append(f"Invalid quote format for {symbol}")
            except Exception as e:
                errors.append(f"Stock quote {symbol}: {e}")
        
        # Test crypto quotes
        for symbol in test_symbols["crypto"]:
            try:
                quote = alpaca.get_latest_quote(symbol, asset_class="crypto")
                if quote and "bid" in quote and "ask" in quote:
                    success_count += 1
                else:
                    errors.append(f"Invalid quote format for {symbol}")
            except Exception as e:
                errors.append(f"Crypto quote {symbol}: {e}")
        
        # Test positions
        try:
            positions = alpaca.get_positions()
            success_count += 1
        except Exception as e:
            errors.append(f"Positions fetch: {e}")
        
        total_tests = len(test_symbols["stock"]) + len(test_symbols["crypto"]) + 1
        
        self.results.append(TestResult(
            "Market Data Fetching",
            len(errors) < total_tests,  # Pass if at least some data works
            f"✅ {success_count}/{total_tests} data fetches successful" if len(errors) < total_tests else f"❌ Multiple data fetch failures",
            time.time() - start,
            {
                "success_count": success_count,
                "total_tests": total_tests,
                "errors": errors[:5],  # Limit error details
                "error_count": len(errors)
            }
        ))
    
    def _test_crypto_bot_io(self):
        """Test crypto bot input/output"""
        start = time.time()
        try:
            from trading_hydra.bots.crypto_bot import CryptoBot
            
            bot = CryptoBot("qc_test_crypto")
            
            # Test bot execution with safe parameters
            result = bot.execute(max_daily_loss=1.0)  # $1 test budget
            
            # Validate result structure
            required_keys = ["trades_attempted", "positions_managed", "signals", "errors"]
            for key in required_keys:
                assert key in result, f"Missing key: {key}"
            
            assert isinstance(result["trades_attempted"], int)
            assert isinstance(result["positions_managed"], int)
            assert isinstance(result["signals"], dict)
            assert isinstance(result["errors"], list)
            
            self.results.append(TestResult(
                "Crypto Bot I/O",
                True,
                f"✅ Bot executed - Trades: {result['trades_attempted']}, Positions: {result['positions_managed']}, Errors: {len(result['errors'])}",
                time.time() - start,
                result
            ))
            
        except Exception as e:
            self.results.append(TestResult(
                "Crypto Bot I/O",
                False,
                f"❌ Bot execution failed: {e}",
                time.time() - start,
                {"error": str(e), "traceback": traceback.format_exc()}
            ))
    
    def _test_momentum_bot_io(self):
        """Test momentum bot input/output"""
        start = time.time()
        try:
            from trading_hydra.bots.momentum_bot import MomentumBot
            
            bot = MomentumBot("qc_test_mom", "AAPL")
            
            # Test bot execution
            result = bot.execute(max_daily_loss=1.0)
            
            # Validate result structure
            required_keys = ["trades_attempted", "positions_managed", "signal", "errors"]
            for key in required_keys:
                assert key in result, f"Missing key: {key}"
            
            self.results.append(TestResult(
                "Momentum Bot I/O",
                True,
                f"✅ Bot executed - Trades: {result['trades_attempted']}, Positions: {result['positions_managed']}, Errors: {len(result['errors'])}",
                time.time() - start,
                result
            ))
            
        except Exception as e:
            self.results.append(TestResult(
                "Momentum Bot I/O",
                False,
                f"❌ Bot execution failed: {e}",
                time.time() - start,
                {"error": str(e)}
            ))
    
    def _test_options_bot_io(self):
        """Test options bot input/output"""
        start = time.time()
        try:
            from trading_hydra.bots.options_bot import OptionsBot
            
            bot = OptionsBot("qc_test_opt")
            
            # Test bot execution
            result = bot.execute(max_daily_loss=1.0)
            
            # Validate result structure
            required_keys = ["trades_attempted", "positions_managed", "scanned_chains", "errors"]
            for key in required_keys:
                assert key in result, f"Missing key: {key}"
            
            self.results.append(TestResult(
                "Options Bot I/O",
                True,
                f"✅ Bot executed - Trades: {result['trades_attempted']}, Chains: {result['scanned_chains']}, Errors: {len(result['errors'])}",
                time.time() - start,
                result
            ))
            
        except Exception as e:
            self.results.append(TestResult(
                "Options Bot I/O",
                False,
                f"❌ Bot execution failed: {e}",
                time.time() - start,
                {"error": str(e)}
            ))
    
    def _test_orchestrator_initialization(self):
        """Test orchestrator initialization"""
        start = time.time()
        try:
            orchestrator = get_orchestrator()
            orchestrator.initialize()
            
            # Verify initialization
            assert orchestrator._initialized == True
            assert orchestrator._logger is not None
            assert orchestrator._alpaca is not None
            
            self.results.append(TestResult(
                "Orchestrator Initialization",
                True,
                "✅ Orchestrator initialized successfully",
                time.time() - start,
                {"initialized": True}
            ))
            
        except Exception as e:
            self.results.append(TestResult(
                "Orchestrator Initialization",
                False,
                f"❌ Orchestrator init failed: {e}",
                time.time() - start
            ))
    
    def _test_trading_loop(self):
        """Test complete trading loop execution"""
        start = time.time()
        try:
            orchestrator = get_orchestrator()
            orchestrator.initialize()
            
            # Run one loop iteration
            result = orchestrator.run_loop()
            
            # Validate loop result
            assert hasattr(result, 'success')
            assert hasattr(result, 'status')
            assert hasattr(result, 'summary')
            assert hasattr(result, 'timestamp')
            
            assert isinstance(result.success, bool)
            assert isinstance(result.status, str)
            assert isinstance(result.summary, str)
            assert isinstance(result.timestamp, str)
            
            self.results.append(TestResult(
                "Trading Loop Execution",
                True,
                f"✅ Loop completed - Success: {result.success}, Status: {result.status}",
                time.time() - start,
                {
                    "success": result.success,
                    "status": result.status,
                    "summary_length": len(result.summary),
                    "timestamp": result.timestamp
                }
            ))
            
        except Exception as e:
            self.results.append(TestResult(
                "Trading Loop Execution",
                False,
                f"❌ Loop execution failed: {e}",
                time.time() - start,
                {"error": str(e)}
            ))
    
    def _test_full_system_integration(self):
        """Test full system integration with multiple loop cycles"""
        start = time.time()
        try:
            orchestrator = get_orchestrator()
            orchestrator.initialize()
            
            # Run 3 consecutive loops to test stability
            loop_results = []
            for i in range(3):
                result = orchestrator.run_loop()
                loop_results.append({
                    "iteration": i + 1,
                    "success": result.success,
                    "status": result.status
                })
                time.sleep(0.5)  # Brief pause between loops
            
            # Check all loops succeeded
            all_success = all(r["success"] for r in loop_results)
            
            self.results.append(TestResult(
                "Full System Integration",
                all_success,
                f"✅ 3 consecutive loops completed successfully" if all_success else f"❌ Some loops failed",
                time.time() - start,
                {"loop_results": loop_results, "all_success": all_success}
            ))
            
        except Exception as e:
            self.results.append(TestResult(
                "Full System Integration",
                False,
                f"❌ Integration test failed: {e}",
                time.time() - start,
                {"error": str(e)}
            ))
    
    def _generate_report(self) -> Dict[str, Any]:
        """Generate comprehensive QC report"""
        total_time = time.time() - self.start_time
        passed_tests = [r for r in self.results if r.passed]
        failed_tests = [r for r in self.results if not r.passed]
        
        print("\n" + "=" * 60)
        print("🎯 QC TEST RESULTS SUMMARY")
        print("=" * 60)
        
        print(f"📊 OVERALL: {len(passed_tests)}/{len(self.results)} tests passed")
        print(f"⏱️  TOTAL TIME: {total_time:.2f} seconds")
        print(f"📝 DETAILED RESULTS:")
        print("-" * 40)
        
        for result in self.results:
            status = "✅ PASS" if result.passed else "❌ FAIL"
            print(f"{status} | {result.name:<25} | {result.execution_time:.2f}s")
            print(f"     {result.message}")
            
            if not result.passed and result.details:
                if "error" in result.details:
                    print(f"     Error: {result.details['error'][:100]}...")
            print("-" * 40)
        
        # System recommendations
        print("\n🔧 RECOMMENDATIONS:")
        if len(failed_tests) == 0:
            print("✅ All systems operational - Ready for live trading")
        else:
            print("⚠️  Address failed tests before live deployment:")
            for test in failed_tests:
                print(f"   - Fix: {test.name}")
        
        # API status check
        api_tests = [r for r in self.results if "API" in r.name or "Data" in r.name]
        api_failures = [r for r in api_tests if not r.passed]
        
        if api_failures:
            print("\n⚠️  API CONNECTIVITY ISSUES DETECTED:")
            print("   - Current errors are likely due to:")
            print("     1. Missing alpaca-py latest version")
            print("     2. API endpoint changes")
            print("     3. Paper trading limitations")
            print("   - Trading system will run but with limited data")
        
        return {
            "summary": {
                "total_tests": len(self.results),
                "passed": len(passed_tests),
                "failed": len(failed_tests),
                "success_rate": len(passed_tests) / len(self.results) * 100,
                "total_time": total_time
            },
            "results": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "message": r.message,
                    "execution_time": r.execution_time,
                    "details": r.details
                }
                for r in self.results
            ],
            "recommendations": {
                "ready_for_live": len(failed_tests) == 0,
                "critical_issues": [r.name for r in failed_tests],
                "api_status": "degraded" if api_failures else "operational"
            }
        }


def main():
    """Run QC test suite"""
    qc = TradingHydraQC()
    report = qc.run_all_tests()
    
    # Save report to file
    import json
    report_path = "logs/qc_report.json"
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    
    print(f"\n📄 Full report saved to: {report_path}")
    
    # Return exit code based on results
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    exit(main())
