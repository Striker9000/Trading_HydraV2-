#!/usr/bin/env python3
"""
Main QC Test Runner - Execute complete quality control suite

Usage: python -m src.test.run_qc_tests
"""
import os
import sys
import subprocess

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(project_root)

sys.path.insert(0, project_root)

def main():
    print("🚀 TRADING HYDRA QC SUITE LAUNCHER")
    print("=" * 50)
    
    tests_dir = os.path.join(project_root, 'src', 'trading_hydra', 'tests')
    
    if os.path.exists(os.path.join(tests_dir, 'run_qc.py')):
        os.chdir(tests_dir)
        try:
            result = subprocess.run([sys.executable, 'run_qc.py'], 
                                  capture_output=False, 
                                  text=True)
            
            print(f"\n🏁 QC Suite completed with exit code: {result.returncode}")
            
            if result.returncode == 0:
                print("✅ System passed all QC checks - Ready for production!")
            elif result.returncode == 1:
                print("⚠️  System has minor issues but is functional")
            elif result.returncode == 2:
                print("🔴 System has major issues requiring attention")
            else:
                print("❌ QC Suite encountered critical errors")
            
            return result.returncode
            
        except Exception as e:
            print(f"❌ Failed to run QC suite: {e}")
            return 3
    else:
        print("Running comprehensive QC from src.test module...")
        from src.test.run_comprehensive_qc import main as run_qc
        return run_qc()

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
