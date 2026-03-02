#!/usr/bin/env python3
"""Dashboard entry point"""
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.dirname(current_dir)
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from dashboard.app import run_dashboard

if __name__ == "__main__":
    print("Starting Trading Hydra Dashboard on http://0.0.0.0:5000")
    run_dashboard(host="0.0.0.0", port=5000, debug=False)
