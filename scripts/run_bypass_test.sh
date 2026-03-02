#!/bin/bash
# Bypass Order Test Runner
# Stops main trading loop, runs bypass test, then confirms positions

echo "=== BYPASS ORDER TEST ==="
echo "This script will:"
echo "1. Run bypass orders for TwentyMinuteBot and OptionsBot"
echo "2. Register positions with ExitBot BEFORE placing orders"
echo ""

# Stop main loop if running
echo "Checking if main loop is running..."
pkill -f "python main.py" 2>/dev/null && echo "Stopped main trading loop" || echo "Main loop not running"
sleep 2

# Run bypass for both bots
echo ""
echo "=== EXECUTING TWENTYMINUTEBOT ==="
python scripts/bypass_order_test.py --bot twentymin

echo ""
echo "=== EXECUTING OPTIONSBOT ==="
python scripts/bypass_order_test.py --bot options

echo ""
echo "=== CURRENT POSITIONS ==="
python -c "
from src.trading_hydra.services.alpaca_client import get_alpaca_client
alpaca = get_alpaca_client()
positions = alpaca.get_positions()
print('Positions in Alpaca:')
for p in positions:
    print(f'  {p.symbol}: {p.qty} @ \${float(p.current_price):.2f}')
if not positions:
    print('  No positions found')
print(f'Total: {len(positions)} positions')
"

echo ""
echo "=== TEST COMPLETE ==="
echo "You can now restart main.py to continue trading."
echo "ExitBot will recognize these positions because entry intents are saved to state."
