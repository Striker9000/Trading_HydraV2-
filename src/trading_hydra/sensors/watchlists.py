"""
Watchlist management for HydraSensors.

Loads ticker universe from YAML config with:
- Ticker definitions with tags and priority
- Named watchlists (explicit or tag-based)
- Tag-to-ticker reverse lookups
"""

import os
from typing import Dict, List, Optional, Set
import yaml

from ..core.logging import get_logger


def load_watchlists_config(config_path: str = "config/watchlists.yaml") -> Dict:
    """Load watchlists configuration from YAML."""
    logger = get_logger()
    
    if not os.path.exists(config_path):
        logger.error(f"Watchlists config not found: {config_path}")
        return {}
    
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        logger.log("watchlists_config_loaded", {"path": config_path})
        return config or {}
    except Exception as e:
        logger.error(f"Failed to load watchlists config: {e}")
        return {}


class WatchlistManager:
    """
    Manages watchlists and ticker tags.
    
    Provides:
    - All tickers in universe
    - Tag-to-ticker lookups
    - Named watchlists (static or dynamic from tags)
    - Ticker metadata (priority, notes)
    """
    
    def __init__(self, config_path: str = "config/watchlists.yaml"):
        self.logger = get_logger()
        self.config_path = config_path
        
        # Ticker universe
        self._tickers: Dict[str, Dict] = {}  # ticker -> {tags, priority, notes}
        
        # Reverse lookups
        self._tag_to_tickers: Dict[str, Set[str]] = {}
        
        # Named watchlists
        self._watchlists: Dict[str, List[str]] = {}
        
        # Tag descriptions
        self._tag_groups: Dict[str, str] = {}
        
        # Load config
        self._load()
    
    def _load(self) -> None:
        """Load configuration from YAML."""
        config = load_watchlists_config(self.config_path)
        
        if not config:
            self.logger.log("watchlists_empty_config", {})
            return
        
        # Load tickers
        tickers_config = config.get("tickers", {})
        for ticker, data in tickers_config.items():
            if data is None:
                data = {}
            
            tags = data.get("tags", [])
            priority = data.get("priority", 3)
            notes = data.get("notes", "")
            
            self._tickers[ticker] = {
                "tags": tags,
                "priority": priority,
                "notes": notes,
            }
            
            # Build reverse lookup
            for tag in tags:
                if tag not in self._tag_to_tickers:
                    self._tag_to_tickers[tag] = set()
                self._tag_to_tickers[tag].add(ticker)
        
        # Load tag descriptions
        self._tag_groups = config.get("tag_groups", {})
        
        # Load named watchlists
        watchlists_config = config.get("watchlists", {})
        for name, wl_data in watchlists_config.items():
            if wl_data is None:
                continue
            
            tickers = []
            
            # Explicit ticker list
            if "tickers" in wl_data:
                tickers.extend(wl_data["tickers"])
            
            # Dynamic from tags
            if "from_tags" in wl_data:
                min_priority = wl_data.get("min_priority", 999)
                for tag in wl_data["from_tags"]:
                    for ticker in self.get_tickers_by_tag(tag):
                        ticker_data = self._tickers.get(ticker, {})
                        if ticker_data.get("priority", 3) <= min_priority:
                            if ticker not in tickers:
                                tickers.append(ticker)
            
            self._watchlists[name] = tickers
        
        self.logger.log("watchlists_loaded", {
            "ticker_count": len(self._tickers),
            "tag_count": len(self._tag_to_tickers),
            "watchlist_count": len(self._watchlists),
        })
    
    def reload(self) -> None:
        """Reload configuration from disk."""
        self._tickers.clear()
        self._tag_to_tickers.clear()
        self._watchlists.clear()
        self._tag_groups.clear()
        self._load()
    
    def get_all_tickers(self) -> List[str]:
        """Get all tickers in universe."""
        return list(self._tickers.keys())
    
    def get_ticker_info(self, ticker: str) -> Optional[Dict]:
        """Get ticker metadata (tags, priority, notes)."""
        return self._tickers.get(ticker)
    
    def get_ticker_tags(self, ticker: str) -> List[str]:
        """Get tags for a ticker."""
        info = self._tickers.get(ticker, {})
        return info.get("tags", [])
    
    def get_ticker_priority(self, ticker: str) -> int:
        """Get priority for a ticker (lower = higher priority)."""
        info = self._tickers.get(ticker, {})
        return info.get("priority", 3)
    
    def get_tickers_by_tag(self, tag: str) -> List[str]:
        """Get all tickers with a specific tag."""
        return list(self._tag_to_tickers.get(tag, set()))
    
    def get_all_tags(self) -> List[str]:
        """Get all known tags."""
        return list(self._tag_to_tickers.keys())
    
    def get_tag_description(self, tag: str) -> str:
        """Get description for a tag."""
        return self._tag_groups.get(tag, "")
    
    def get_watchlist(self, name: str) -> List[str]:
        """Get tickers in a named watchlist."""
        return self._watchlists.get(name, []).copy()
    
    def get_all_watchlists(self) -> Dict[str, List[str]]:
        """Get all named watchlists."""
        return {k: v.copy() for k, v in self._watchlists.items()}
    
    def get_ticker_tags_map(self) -> Dict[str, List[str]]:
        """Get mapping of ticker -> tags for all tickers."""
        return {
            ticker: data.get("tags", [])
            for ticker, data in self._tickers.items()
        }
    
    def filter_tickers(
        self,
        tags: List[str] = None,
        max_priority: int = None,
        exclude_tags: List[str] = None,
    ) -> List[str]:
        """
        Filter tickers by criteria.
        
        Args:
            tags: Required tags (ticker must have at least one)
            max_priority: Maximum priority (lower = higher priority)
            exclude_tags: Tags to exclude
        
        Returns:
            List of matching tickers sorted by priority
        """
        results = []
        
        for ticker, data in self._tickers.items():
            ticker_tags = data.get("tags", [])
            ticker_priority = data.get("priority", 3)
            
            # Check priority
            if max_priority is not None and ticker_priority > max_priority:
                continue
            
            # Check required tags
            if tags:
                if not any(t in ticker_tags for t in tags):
                    continue
            
            # Check excluded tags
            if exclude_tags:
                if any(t in ticker_tags for t in exclude_tags):
                    continue
            
            results.append((ticker, ticker_priority))
        
        # Sort by priority
        results.sort(key=lambda x: x[1])
        
        return [ticker for ticker, _ in results]
    
    def is_equity(self, ticker: str) -> bool:
        """Check if ticker is an equity (not crypto)."""
        tags = self.get_ticker_tags(ticker)
        return "CRYPTO" not in tags
    
    def is_crypto(self, ticker: str) -> bool:
        """Check if ticker is crypto."""
        tags = self.get_ticker_tags(ticker)
        return "CRYPTO" in tags
    
    def is_etf(self, ticker: str) -> bool:
        """Check if ticker is an ETF."""
        tags = self.get_ticker_tags(ticker)
        return "ETF" in tags
