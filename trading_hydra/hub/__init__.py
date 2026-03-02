# src/trading_hydra/hub/__init__.py
"""
Hub module for MySQL-based inter-bot communication.

Bots communicate ONLY through MySQL tables:
- MarketData writes market_snapshots
- Strategy reads snapshots, writes trade_intents
- Execution leases intents, writes order_events
- Exit tracks positions, writes pnl_events, updates kill-switches
"""

from trading_hydra.hub.hub_store_mysql import HubStoreMySQL, LeaseResult

__all__ = ["HubStoreMySQL", "LeaseResult"]
