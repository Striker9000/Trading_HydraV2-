
#!/usr/bin/env python3
"""CLI utility to check halt status"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from trading_hydra.core.halt import get_halt_manager

def main():
    halt_manager = get_halt_manager()
    status = halt_manager.get_status()
    
    print(f"GLOBAL_TRADING_HALT: {status.is_halted}")
    print(f"HALT_REASON: {status.reason}")
    print(f"HALT_UNTIL: {status.halt_until}")
    
    if status.is_halted:
        print("🛑 TRADING HALTED - Manage-only mode")
    else:
        print("✅ TRADING ACTIVE")

if __name__ == "__main__":
    main()
