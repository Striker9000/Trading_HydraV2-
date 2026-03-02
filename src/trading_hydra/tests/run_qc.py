
"""Unified QC Test Runner - Run all quality control tests"""
import sys
import os
import json
from datetime import datetime

# Add project paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../..'))

from qc_runner import TradingHydraQC
from bot_stress_test import BotStressTester


def run_complete_qc_suite():
    """Run complete QC test suite including functional and stress tests"""
    
    print("🎯 TRADING HYDRA COMPLETE QC SUITE")
    print("=" * 60)
    print(f"📅 Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # Phase 1: Functional QC Tests
    print("\n🔧 PHASE 1: FUNCTIONAL TESTING")
    print("-" * 40)
    
    qc_tester = TradingHydraQC()
    functional_report = qc_tester.run_all_tests()
    
    # Phase 2: Stress Testing
    print("\n🔥 PHASE 2: STRESS TESTING")
    print("-" * 40)
    
    stress_tester = BotStressTester()
    stress_report = stress_tester.run_full_stress_test()
    
    # Combined Analysis
    print("\n📊 COMBINED QC ANALYSIS")
    print("=" * 60)
    
    functional_success = functional_report["summary"]["success_rate"]
    stress_success = stress_report["summary"]["overall_success_rate"]
    overall_score = (functional_success + stress_success) / 2
    
    print(f"🎯 Functional Tests: {functional_success:.1f}% ({functional_report['summary']['passed']}/{functional_report['summary']['total_tests']})")
    print(f"🔥 Stress Tests: {stress_success:.1f}%")
    print(f"📈 Overall QC Score: {overall_score:.1f}%")
    
    # System Status Assessment
    if overall_score >= 95:
        status = "🟢 EXCELLENT - Production Ready"
    elif overall_score >= 85:
        status = "🟡 GOOD - Minor Issues to Address"
    elif overall_score >= 70:
        status = "🟠 FAIR - Several Issues Need Fixing"
    else:
        status = "🔴 POOR - Major Issues Require Attention"
    
    print(f"🎖️  System Status: {status}")
    
    # Critical Issues Summary
    critical_issues = functional_report["recommendations"]["critical_issues"]
    if critical_issues:
        print(f"\n⚠️  CRITICAL ISSUES TO FIX:")
        for issue in critical_issues:
            print(f"   • {issue}")
    
    # Generate comprehensive report
    combined_report = {
        "qc_suite_summary": {
            "timestamp": datetime.now().isoformat(),
            "functional_score": functional_success,
            "stress_score": stress_success,
            "overall_score": overall_score,
            "status": status,
            "ready_for_production": overall_score >= 85 and len(critical_issues) == 0
        },
        "functional_testing": functional_report,
        "stress_testing": stress_report,
        "recommendations": {
            "immediate_actions": critical_issues,
            "performance_notes": stress_report["recommendations"],
            "next_steps": generate_next_steps(overall_score, critical_issues)
        }
    }
    
    # Save comprehensive report
    report_filename = f"logs/complete_qc_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_filename, 'w') as f:
        json.dump(combined_report, f, indent=2)
    
    print(f"\n📄 Complete QC report saved to: {report_filename}")
    
    # Print final recommendations
    print("\n🎯 NEXT STEPS:")
    for step in combined_report["recommendations"]["next_steps"]:
        print(f"   {step}")
    
    print("\n" + "=" * 60)
    print("QC SUITE COMPLETED")
    print("=" * 60)
    
    return combined_report


def generate_next_steps(overall_score: float, critical_issues: list) -> list:
    """Generate next steps based on QC results"""
    steps = []
    
    if critical_issues:
        steps.append("🔧 Fix all critical functional issues first")
        steps.append("🔄 Re-run functional tests after fixes")
    
    if overall_score < 85:
        steps.append("🔍 Review failed tests and improve bot reliability")
        steps.append("🧪 Run additional targeted tests on problem areas")
    
    if overall_score >= 85:
        steps.append("✅ System ready for paper trading validation")
        steps.append("📈 Monitor system performance in live paper trading")
        
    if overall_score >= 95:
        steps.append("🚀 Consider enabling live trading (with small position sizes)")
        steps.append("📊 Set up production monitoring and alerts")
    
    steps.append("⏰ Schedule regular QC checks (daily/weekly)")
    steps.append("📈 Monitor trading performance and bot behavior")
    
    return steps


if __name__ == "__main__":
    try:
        report = run_complete_qc_suite()
        
        # Exit code based on overall system health
        if report["qc_suite_summary"]["ready_for_production"]:
            sys.exit(0)  # Success
        elif report["qc_suite_summary"]["overall_score"] >= 70:
            sys.exit(1)  # Issues but functional
        else:
            sys.exit(2)  # Major problems
            
    except Exception as e:
        print(f"❌ QC Suite failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(3)  # Critical failure
