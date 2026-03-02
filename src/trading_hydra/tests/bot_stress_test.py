
"""Bot Stress Testing - High-frequency I/O validation"""
import time
import threading
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any

from trading_hydra.bots.crypto_bot import CryptoBot
from trading_hydra.bots.momentum_bot import MomentumBot
from trading_hydra.bots.options_bot import OptionsBot


class BotStressTester:
    """Stress test all bots for performance and reliability"""
    
    def __init__(self):
        self.results = []
    
    def stress_test_crypto_bot(self, iterations: int = 10, max_concurrent: int = 3) -> Dict[str, Any]:
        """Stress test crypto bot with concurrent executions"""
        print(f"🔥 Stress testing Crypto Bot - {iterations} iterations, {max_concurrent} concurrent")
        
        execution_times = []
        errors = []
        
        def single_execution(iteration: int) -> Dict[str, Any]:
            start_time = time.time()
            try:
                bot = CryptoBot(f"stress_crypto_{iteration}")
                result = bot.execute(max_daily_loss=0.10)  # Very small test budget
                
                execution_time = time.time() - start_time
                execution_times.append(execution_time)
                
                return {
                    "iteration": iteration,
                    "success": True,
                    "execution_time": execution_time,
                    "trades_attempted": result.get("trades_attempted", 0),
                    "errors": len(result.get("errors", []))
                }
            except Exception as e:
                execution_time = time.time() - start_time
                errors.append(f"Iteration {iteration}: {e}")
                return {
                    "iteration": iteration,
                    "success": False,
                    "execution_time": execution_time,
                    "error": str(e)
                }
        
        # Execute with thread pool
        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = [executor.submit(single_execution, i) for i in range(iterations)]
            results = [future.result() for future in as_completed(futures)]
        
        # Calculate statistics
        successful_results = [r for r in results if r["success"]]
        success_rate = len(successful_results) / iterations * 100
        
        if execution_times:
            avg_time = statistics.mean(execution_times)
            median_time = statistics.median(execution_times)
            max_time = max(execution_times)
            min_time = min(execution_times)
        else:
            avg_time = median_time = max_time = min_time = 0
        
        print(f"✅ Crypto Bot Results: {success_rate:.1f}% success, avg: {avg_time:.2f}s")
        
        return {
            "bot_type": "crypto",
            "iterations": iterations,
            "success_rate": success_rate,
            "execution_stats": {
                "average_time": avg_time,
                "median_time": median_time,
                "max_time": max_time,
                "min_time": min_time
            },
            "errors": errors[:5],  # First 5 errors
            "total_errors": len(errors)
        }
    
    def stress_test_momentum_bot(self, iterations: int = 10, tickers: List[str] = None) -> Dict[str, Any]:
        """Stress test momentum bot with different tickers"""
        if tickers is None:
            tickers = ["AAPL", "MSFT", "GOOGL", "TSLA"]
        
        print(f"🔥 Stress testing Momentum Bot - {iterations} iterations across {len(tickers)} tickers")
        
        results_by_ticker = {}
        overall_times = []
        overall_errors = []
        
        for ticker in tickers:
            ticker_times = []
            ticker_errors = []
            
            for i in range(iterations):
                start_time = time.time()
                try:
                    bot = MomentumBot(f"stress_mom_{ticker}_{i}", ticker)
                    result = bot.execute(max_daily_loss=0.10)
                    
                    execution_time = time.time() - start_time
                    ticker_times.append(execution_time)
                    overall_times.append(execution_time)
                    
                except Exception as e:
                    execution_time = time.time() - start_time
                    error_msg = f"{ticker}[{i}]: {e}"
                    ticker_errors.append(error_msg)
                    overall_errors.append(error_msg)
                    overall_times.append(execution_time)
            
            results_by_ticker[ticker] = {
                "success_rate": (iterations - len(ticker_errors)) / iterations * 100,
                "avg_time": statistics.mean(ticker_times) if ticker_times else 0,
                "errors": len(ticker_errors)
            }
        
        overall_success = (len(tickers) * iterations - len(overall_errors)) / (len(tickers) * iterations) * 100
        
        print(f"✅ Momentum Bot Results: {overall_success:.1f}% success across all tickers")
        
        return {
            "bot_type": "momentum",
            "iterations_per_ticker": iterations,
            "tickers_tested": len(tickers),
            "overall_success_rate": overall_success,
            "results_by_ticker": results_by_ticker,
            "overall_stats": {
                "total_executions": len(tickers) * iterations,
                "total_errors": len(overall_errors),
                "avg_time": statistics.mean(overall_times) if overall_times else 0
            }
        }
    
    def stress_test_options_bot(self, iterations: int = 5) -> Dict[str, Any]:
        """Stress test options bot (fewer iterations due to complexity)"""
        print(f"🔥 Stress testing Options Bot - {iterations} iterations")
        
        execution_times = []
        errors = []
        successful_scans = 0
        
        for i in range(iterations):
            start_time = time.time()
            try:
                bot = OptionsBot(f"stress_opt_{i}")
                result = bot.execute(max_daily_loss=0.10)
                
                execution_time = time.time() - start_time
                execution_times.append(execution_time)
                
                if result.get("scanned_chains", 0) > 0:
                    successful_scans += 1
                    
            except Exception as e:
                execution_time = time.time() - start_time
                execution_times.append(execution_time)
                errors.append(f"Iteration {i}: {e}")
        
        success_rate = (iterations - len(errors)) / iterations * 100
        scan_success_rate = successful_scans / iterations * 100
        
        print(f"✅ Options Bot Results: {success_rate:.1f}% execution success, {scan_success_rate:.1f}% scan success")
        
        return {
            "bot_type": "options",
            "iterations": iterations,
            "execution_success_rate": success_rate,
            "scan_success_rate": scan_success_rate,
            "avg_execution_time": statistics.mean(execution_times) if execution_times else 0,
            "errors": errors,
            "total_errors": len(errors)
        }
    
    def run_full_stress_test(self) -> Dict[str, Any]:
        """Run comprehensive stress test on all bots"""
        print("🚀 Starting Full Bot Stress Test Suite")
        print("=" * 50)
        
        start_time = time.time()
        
        # Test each bot type
        crypto_results = self.stress_test_crypto_bot(iterations=8, max_concurrent=2)
        momentum_results = self.stress_test_momentum_bot(iterations=5, tickers=["AAPL", "SPY"])
        options_results = self.stress_test_options_bot(iterations=3)
        
        total_time = time.time() - start_time
        
        # Overall analysis
        all_results = [crypto_results, momentum_results, options_results]
        overall_success = statistics.mean([r.get("success_rate", r.get("execution_success_rate", 0)) for r in all_results])
        
        print("\n" + "=" * 50)
        print(f"🎯 STRESS TEST SUMMARY")
        print(f"📊 Overall Success Rate: {overall_success:.1f}%")
        print(f"⏱️  Total Test Time: {total_time:.2f}s")
        print("=" * 50)
        
        report = {
            "summary": {
                "overall_success_rate": overall_success,
                "total_time": total_time,
                "bots_tested": 3
            },
            "detailed_results": {
                "crypto_bot": crypto_results,
                "momentum_bot": momentum_results,
                "options_bot": options_results
            },
            "recommendations": self._generate_stress_recommendations(all_results)
        }
        
        return report
    
    def _generate_stress_recommendations(self, results: List[Dict[str, Any]]) -> List[str]:
        """Generate recommendations based on stress test results"""
        recommendations = []
        
        for result in results:
            bot_type = result["bot_type"]
            success_rate = result.get("success_rate", result.get("execution_success_rate", 0))
            
            if success_rate < 80:
                recommendations.append(f"⚠️  {bot_type.title()} bot needs stability improvements (success rate: {success_rate:.1f}%)")
            elif success_rate < 95:
                recommendations.append(f"🔧 {bot_type.title()} bot has minor reliability issues (success rate: {success_rate:.1f}%)")
            else:
                recommendations.append(f"✅ {bot_type.title()} bot passed stress test (success rate: {success_rate:.1f}%)")
        
        if all(r.get("success_rate", r.get("execution_success_rate", 0)) > 90 for r in results):
            recommendations.append("🚀 All bots ready for production load")
        else:
            recommendations.append("⚠️  Address bot reliability issues before scaling up")
        
        return recommendations


def main():
    """Run bot stress test suite"""
    tester = BotStressTester()
    report = tester.run_full_stress_test()
    
    # Save report
    import json
    with open("logs/stress_test_report.json", 'w') as f:
        json.dump(report, f, indent=2)
    
    print(f"\n📄 Stress test report saved to: logs/stress_test_report.json")
    
    return 0 if report["summary"]["overall_success_rate"] > 80 else 1


if __name__ == "__main__":
    exit(main())
