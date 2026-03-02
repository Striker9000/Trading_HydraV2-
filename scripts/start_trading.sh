#!/bin/bash
# Start Trading Hydra with cleared health state

echo "=== Clearing health state ==="
python scripts/clear_auth_state.py

echo ""
echo "=== Starting Trading Hydra ==="
exec python main.py --inplace
