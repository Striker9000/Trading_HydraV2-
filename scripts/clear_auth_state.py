#!/usr/bin/env python3
"""Clear critical auth failure state from the database."""
import sys
sys.path.insert(0, 'src')

from trading_hydra.core.state import set_state, get_state

print("Current auth failure state:")
print(f"  critical_auth_failure: {get_state('health.critical_auth_failure')}")
print(f"  critical_auth_error: {get_state('health.critical_auth_error')}")

print("\nClearing auth failure state...")
set_state('health.critical_auth_failure', False)
set_state('health.critical_auth_error', '')
set_state('health.critical_auth_timestamp', '')

print("\nAfter clearing:")
print(f"  critical_auth_failure: {get_state('health.critical_auth_failure')}")
print("Done!")
