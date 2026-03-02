#!/bin/bash
# Trading Hydra Run Script for Linux/macOS

# Activate virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Load environment variables
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Run the trading system
echo "Starting Trading Hydra..."
python3 main.py
