#!/usr/bin/env python3
"""
Trading Hydra System Launcher
Convenience script to start the trading system

Usage: python -m src.test.main
       (This redirects to the actual runner)
"""
import sys
import os

def main():
    print("Starting Trading Hydra System...")
    print("Redirecting to: python -m src.runner.main")
    
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.chdir(project_root)
    
    try:
        import subprocess
        result = subprocess.run([sys.executable, "-m", "src.runner.main"], check=True)
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        print("\nShutdown requested by user")
        sys.exit(0)
    except Exception as e:
        print(f"System error: {e}")
        print("Try running directly: python -m src.runner.main")
        sys.exit(1)

if __name__ == "__main__":
    main()
