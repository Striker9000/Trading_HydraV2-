#!/usr/bin/env python3
"""
Comprehensive QC Check for Trading Hydra
Validates all systems before live trading deployment

Usage: python -m src.test.run_comprehensive_qc
"""

import sys
import os
import time
from datetime import datetime
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from trading_hydra.core.logging import get_logger
from trading_hydra.services.alpaca_client import get_alpaca_client
from trading_hydra.orchestrator import get_orchestrator
from trading_hydra.core.state import init_state_store, get_state, set_state
from trading_hydra.core.config import load_settings, load_bots_config
from trading_hydra.services.mock_data import get_mock_data_service, is_development_mode


class QCValidator:
    def __init__(self):
        self.logger = get_logger()
        self.results = []
        self.critical_issues = []
        self.warnings = []

    def run_full_qc_check(self):
        """Run comprehensive QC validation"""
        print("🔍 TRADING HYDRA COMPREHENSIVE QC CHECK")
        print("=" * 60)
        print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S EST')}")
        print()

        self._test_alpaca_connectivity()
        self._test_real_vs_mock_data()
        self._test_configuration_integrity()
        self._test_risk_parameters()
        self._test_bot_implementations()
        self._test_signal_generation()
        self._test_safety_mechanisms()
        self._test_budget_calculations()
        self._test_state_persistence()
        self._test_logging_system()
        self._test_trading_readiness()
        
        return self._generate_final_report()

    def _test_alpaca_connectivity(self):
        """Test Alpaca API connection and account status"""
        print("📡 TESTING ALPACA CONNECTIVITY")
        print("-" * 40)
        
        try:
            client = get_alpaca_client()
            
            if not client.has_credentials():
                self.critical_issues.append("Missing ALPACA_KEY or ALPACA_SECRET")
                print("❌ CRITICAL: Missing API credentials")
                return
            
            print(f"✅ API Credentials: Present")
            print(f"✅ Environment: {'Paper Trading' if client.is_paper else 'LIVE TRADING'}")
            print(f"✅ Base URL: {client.base_url}")
            
            account = client.get_account()
            print(f"✅ Account Status: {account.status}")
            print(f"✅ Current Equity: ${account.equity:,.2f}")
            print(f"✅ Buying Power: ${account.buying_power:,.2f}")
            
            if account.equity < 1000:
                self.warnings.append(f"Low account equity: ${account.equity:,.2f}")
            if account.status != "ACTIVE":
                self.critical_issues.append(f"Account not active: {account.status}")
            
            positions = client.get_positions()
            print(f"✅ Positions Retrieved: {len(positions)}")
            
            if positions:
                total_value = sum(p.market_value for p in positions)
                print(f"   Total Position Value: ${total_value:,.2f}")
                for pos in positions[:3]:
                    print(f"   - {pos.symbol}: {pos.qty} shares, P&L: ${pos.unrealized_pl:,.2f}")
            
        except Exception as e:
            self.critical_issues.append(f"Alpaca connectivity: {e}")
            print(f"❌ CONNECTION FAILED: {e}")
        
        print()

    def _test_real_vs_mock_data(self):
        """Test data source configuration during/outside trading hours"""
        print("📊 TESTING DATA SOURCE CONFIGURATION")
        print("-" * 40)
        
        try:
            mock_service = get_mock_data_service()
            dev_mode = is_development_mode()
            
            print(f"Development Mode: {'ON' if dev_mode else 'OFF'}")
            
            current_time = datetime.now().time()
            market_hours = (current_time.hour >= 9 and current_time.hour < 16)
            
            if market_hours:
                print("🏛️ MARKET HOURS DETECTED")
                if dev_mode:
                    self.warnings.append("Development mode ON during market hours - will use mock data")
                    print("⚠️  WARNING: Mock data enabled during market hours")
                else:
                    print("✅ Using REAL market data during trading hours")
            else:
                print("🌙 AFTER HOURS DETECTED")
                if dev_mode:
                    print("✅ Using mock data for after-hours development")
                else:
                    print("✅ Real data mode (limited after-hours data)")
            
            test_symbols = ["AAPL", "BTC/USD"]
            for symbol in test_symbols:
                try:
                    asset_class = "crypto" if "/" in symbol else "stock"
                    quote = get_alpaca_client().get_latest_quote(symbol, asset_class)
                    print(f"✅ {symbol} Quote: Bid ${quote['bid']:.2f}, Ask ${quote['ask']:.2f}")
                except Exception as e:
                    print(f"⚠️  {symbol} Quote Error: {e}")
                    
        except Exception as e:
            self.critical_issues.append(f"Data source configuration: {e}")
            print(f"❌ DATA SOURCE ERROR: {e}")
        
        print()

    def _test_configuration_integrity(self):
        """Test configuration file integrity"""
        print("⚙️ TESTING CONFIGURATION INTEGRITY")
        print("-" * 40)
        
        try:
            settings = load_settings()
            bots_config = load_bots_config()
            
            print("✅ Settings.yaml loaded successfully")
            print("✅ Bots.yaml loaded successfully")
            
            risk_config = settings.get("risk", {})
            max_loss_pct = risk_config.get("global_max_daily_loss_pct", 0)
            
            if max_loss_pct <= 0 or max_loss_pct > 5:
                self.warnings.append(f"Unusual max daily loss: {max_loss_pct}%")
            
            print(f"✅ Global Max Daily Loss: {max_loss_pct}%")
            print(f"✅ Loop Interval: {settings.get('runner', {}).get('loop_interval_seconds', 5)}s")
            
            enabled_bots = []
            for bot_type in ['momentum_bots', 'optionsbot', 'cryptobot']:
                if bot_type in bots_config:
                    if bot_type == 'momentum_bots':
                        for bot in bots_config[bot_type]:
                            if bot.get('enabled', False):
                                enabled_bots.append(bot.get('bot_id', 'unknown'))
                    else:
                        if bots_config[bot_type].get('enabled', False):
                            enabled_bots.append(bots_config[bot_type].get('bot_id', bot_type))
            
            print(f"✅ Enabled Bots: {', '.join(enabled_bots)}")
            
        except Exception as e:
            self.critical_issues.append(f"Configuration integrity: {e}")
            print(f"❌ CONFIG ERROR: {e}")
        
        print()

    def _test_risk_parameters(self):
        """Test risk management parameters"""
        print("🛡️ TESTING RISK PARAMETERS")
        print("-" * 40)
        
        try:
            client = get_alpaca_client()
            account = client.get_account()
            equity = account.equity
            
            settings = load_settings()
            max_loss_pct = settings.get("risk", {}).get("global_max_daily_loss_pct", 2.0)
            daily_risk = equity * (max_loss_pct / 100.0)
            
            print(f"✅ Account Equity: ${equity:,.2f}")
            print(f"✅ Max Daily Loss %: {max_loss_pct}%")
            print(f"✅ Daily Risk Budget: ${daily_risk:,.2f}")
            
            mom_budget = daily_risk * 0.25
            opt_budget = daily_risk * 0.50
            cry_budget = daily_risk * 0.25
            
            print(f"✅ Momentum Budget: ${mom_budget:,.2f}")
            print(f"✅ Options Budget: ${opt_budget:,.2f}")
            print(f"✅ Crypto Budget: ${cry_budget:,.2f}")
            
            if cry_budget < 15.0:
                self.warnings.append(f"Crypto budget ${cry_budget:.2f} below $15 minimum")
                print(f"⚠️  Crypto budget may be too small for meaningful trades")
            
        except Exception as e:
            self.critical_issues.append(f"Risk parameters: {e}")
            print(f"❌ RISK CALC ERROR: {e}")
        
        print()

    def _test_bot_implementations(self):
        """Test bot implementations for errors"""
        print("🤖 TESTING BOT IMPLEMENTATIONS")
        print("-" * 40)
        
        try:
            from trading_hydra.bots.momentum_bot import MomentumBot
            mom_bot = MomentumBot("test_mom", "AAPL")
            print("✅ MomentumBot class loads successfully")
            
            from trading_hydra.bots.crypto_bot import CryptoBot
            crypto_bot = CryptoBot("test_crypto")
            print("✅ CryptoBot class loads successfully")
            
            from trading_hydra.bots.options_bot import OptionsBot
            options_bot = OptionsBot("test_opt")
            print("✅ OptionsBot class loads successfully")
            
            if hasattr(get_mock_data_service(), 'should_generate_signal'):
                print("✅ Mock signal generation methods available")
            else:
                self.critical_issues.append("Missing mock signal generation methods")
                print("❌ Mock signal generation methods missing")
                
        except Exception as e:
            self.critical_issues.append(f"Bot implementations: {e}")
            print(f"❌ BOT IMPLEMENTATION ERROR: {e}")
            print(f"Stack trace: {traceback.format_exc()}")
        
        print()

    def _test_signal_generation(self):
        """Test signal generation without trading"""
        print("📈 TESTING SIGNAL GENERATION")
        print("-" * 40)
        
        try:
            from trading_hydra.bots.momentum_bot import MomentumBot
            mom_bot = MomentumBot("test_mom", "AAPL")
            
            signal = mom_bot._generate_momentum_signal()
            print(f"✅ Momentum signal generated: {signal.get('action', 'unknown')}")
            
            from trading_hydra.bots.crypto_bot import CryptoBot
            crypto_bot = CryptoBot("test_crypto")
            
            crypto_signal = crypto_bot._generate_signal("BTC/USD")
            print(f"✅ Crypto signal generated: {crypto_signal.get('action', 'unknown')}")
            
        except Exception as e:
            self.critical_issues.append(f"Signal generation: {e}")
            print(f"❌ SIGNAL GENERATION ERROR: {e}")
            print(f"This is likely the string concatenation bug!")
        
        print()

    def _test_safety_mechanisms(self):
        """Test safety and exit mechanisms"""
        print("🚨 TESTING SAFETY MECHANISMS")
        print("-" * 40)
        
        try:
            from trading_hydra.services.exitbot import get_exitbot
            exitbot = get_exitbot()
            
            client = get_alpaca_client()
            account = client.get_account()
            
            result = exitbot.run(account.equity, account.equity)
            print(f"✅ ExitBot Status: {result.halt_reason if result.is_halted else 'Active'}")
            print(f"✅ Should Continue: {result.should_continue}")
            
            from trading_hydra.core.halt import get_halt_manager
            halt_manager = get_halt_manager()
            
            is_halted = halt_manager.is_halted()
            print(f"✅ System Halt Status: {'HALTED' if is_halted else 'ACTIVE'}")
            
        except Exception as e:
            self.critical_issues.append(f"Safety mechanisms: {e}")
            print(f"❌ SAFETY MECHANISM ERROR: {e}")
        
        print()

    def _test_budget_calculations(self):
        """Test dynamic budget allocation"""
        print("💰 TESTING BUDGET ALLOCATION")
        print("-" * 40)
        
        try:
            from trading_hydra.services.portfolio import get_portfoliobot
            portfoliobot = get_portfoliobot()
            
            client = get_alpaca_client()
            account = client.get_account()
            
            result = portfoliobot.run(account.equity)
            print(f"✅ Budget Allocation: {'Success' if result.budgets_set else 'Failed'}")
            print(f"✅ Daily Risk: ${result.daily_risk:.2f}")
            print(f"✅ Enabled Bots: {', '.join(result.enabled_bots)}")
            
        except Exception as e:
            self.critical_issues.append(f"Budget calculations: {e}")
            print(f"❌ BUDGET CALCULATION ERROR: {e}")
        
        print()

    def _test_state_persistence(self):
        """Test state management"""
        print("💾 TESTING STATE PERSISTENCE")
        print("-" * 40)
        
        try:
            init_state_store()
            
            test_key = "qc_test_state"
            test_value = {"timestamp": time.time(), "test": True}
            
            set_state(test_key, test_value)
            retrieved = get_state(test_key)
            
            if retrieved == test_value:
                print("✅ State persistence working correctly")
            else:
                self.critical_issues.append("State persistence data mismatch")
                print("❌ State persistence failed")
            
        except Exception as e:
            self.critical_issues.append(f"State persistence: {e}")
            print(f"❌ STATE PERSISTENCE ERROR: {e}")
        
        print()

    def _test_logging_system(self):
        """Test logging system"""
        print("📝 TESTING LOGGING SYSTEM")
        print("-" * 40)
        
        try:
            logger = get_logger()
            
            test_data = {
                "test_type": "qc_validation",
                "timestamp": datetime.now().isoformat(),
                "status": "testing"
            }
            
            logger.log("qc_test_log", test_data)
            print("✅ Structured logging working")
            
            if os.path.exists("logs/app.jsonl"):
                file_size = os.path.getsize("logs/app.jsonl")
                print(f"✅ Log file size: {file_size} bytes")
            else:
                self.warnings.append("Log file not found")
                print("⚠️  Log file not found")
                
        except Exception as e:
            self.critical_issues.append(f"Logging system: {e}")
            print(f"❌ LOGGING ERROR: {e}")
        
        print()

    def _test_trading_readiness(self):
        """Test overall trading readiness"""
        print("🎯 TESTING TRADING READINESS")
        print("-" * 40)
        
        try:
            orchestrator = get_orchestrator()
            orchestrator.initialize()
            print("✅ Orchestrator initializes successfully")
            
            result = orchestrator.run_loop()
            print(f"✅ Loop Execution: {'Success' if result.success else 'Partial/Failed'}")
            print(f"✅ Loop Status: {result.status}")
            
            if not result.success:
                self.warnings.append(f"Loop execution issues: {result.status}")
            
        except Exception as e:
            self.critical_issues.append(f"Trading readiness: {e}")
            print(f"❌ TRADING READINESS ERROR: {e}")
        
        print()

    def _generate_final_report(self):
        """Generate final QC report"""
        print("📋 FINAL QC REPORT")
        print("=" * 60)
        
        critical_count = len(self.critical_issues)
        warning_count = len(self.warnings)
        
        print(f"Critical Issues: {critical_count}")
        print(f"Warnings: {warning_count}")
        print()
        
        if critical_count > 0:
            print("🚨 CRITICAL ISSUES (Must Fix Before Live Trading):")
            for i, issue in enumerate(self.critical_issues, 1):
                print(f"   {i}. {issue}")
            print()
        
        if warning_count > 0:
            print("⚠️  WARNINGS (Recommended to Address):")
            for i, warning in enumerate(self.warnings, 1):
                print(f"   {i}. {warning}")
            print()
        
        if critical_count == 0:
            if warning_count == 0:
                print("🎉 SYSTEM READY FOR LIVE TRADING")
                print("✅ All QC checks passed - Deploy with confidence")
                return 0
            else:
                print("⚠️  SYSTEM READY WITH MINOR ISSUES")
                print("✅ Core systems operational, warnings noted")
                return 1
        else:
            print("🔴 SYSTEM NOT READY FOR LIVE TRADING")
            print("❌ Critical issues must be resolved first")
            return 2


def main():
    """Main QC execution"""
    print("Starting comprehensive QC validation...")
    
    validator = QCValidator()
    exit_code = validator.run_full_qc_check()
    
    print(f"\n🏁 QC Complete - Exit Code: {exit_code}")
    
    if exit_code == 0:
        print("✅ System validated and ready for production")
    elif exit_code == 1:
        print("⚠️  System functional with minor issues")
    else:
        print("❌ System requires fixes before deployment")
    
    return exit_code


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
