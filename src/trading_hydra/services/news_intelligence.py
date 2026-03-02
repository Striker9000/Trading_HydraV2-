"""
NewsIntelligenceService - Real-time news fetching and caching for trading decisions

This service provides:
1. News fetching via OpenAI web search (no external API keys required)
2. Per-symbol caching with configurable TTL
3. Fail-closed behavior - if news unavailable, trading continues without intel
4. Thread-safe singleton pattern for use across the trading system

Usage:
    from trading_hydra.services.news_intelligence import get_news_intelligence
    
    intel = get_news_intelligence()
    news_items = intel.get_news_for_symbol("AAPL")
"""

import os
import time
import threading
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone
from abc import ABC, abstractmethod

from ..core.logging import get_logger
from ..core.config import load_bots_config
from ..core.staleness import get_data_staleness, DataType


@dataclass
class NewsItem:
    """Normalized news item from any provider"""
    symbol: str
    headline: str
    summary: str
    source: str
    url: str
    published_at: str  # ISO format
    provider_id: str
    fetched_at: float = field(default_factory=time.time)  # Unix timestamp
    
    def age_seconds(self) -> float:
        """How old is this news item since it was fetched"""
        return time.time() - self.fetched_at


@dataclass
class NewsCacheEntry:
    """Cache entry for a symbol's news"""
    symbol: str
    items: List[NewsItem]
    last_fetch_ts: float
    fetch_success: bool
    error_message: str = ""
    
    def is_stale(self, ttl_seconds: float) -> bool:
        """Check if cache entry is stale based on TTL"""
        return (time.time() - self.last_fetch_ts) > ttl_seconds
    
    def age_seconds(self) -> float:
        """How old is this cache entry"""
        return time.time() - self.last_fetch_ts


class NewsProvider(ABC):
    """Abstract base class for news providers"""
    
    @abstractmethod
    def fetch_headlines(self, symbols: List[str]) -> Dict[str, List[NewsItem]]:
        """
        Fetch headlines for given symbols
        
        Args:
            symbols: List of ticker symbols
            
        Returns:
            Dict mapping symbol -> list of NewsItem
        """
        pass
    
    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique identifier for this provider"""
        pass


class OpenAINewsProvider(NewsProvider):
    """
    News provider using OpenAI's web search capability
    
    Uses the Replit AI Integration for OpenAI - no API key needed
    """
    
    def __init__(self, timeout_seconds: int = 10, max_items: int = 10):
        self._timeout = timeout_seconds
        self._max_items = max_items
        self._logger = get_logger()
        self._client = None
        self._model = None
    
    def _ensure_client(self):
        """Lazy-initialize OpenAI client"""
        if self._client is None:
            try:
                from openai import OpenAI
                base_url = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
                api_key = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
                
                if not base_url or not api_key:
                    self._logger.warn("OpenAI env vars not set, news provider disabled")
                    return False
                
                self._client = OpenAI(base_url=base_url, api_key=api_key)
                self._model = "gpt-4o"  # Use gpt-4o for web search
                return True
            except Exception as e:
                self._logger.error(f"Failed to init OpenAI client: {e}")
                return False
        return True
    
    @property
    def provider_id(self) -> str:
        return "openai_web_search"
    
    def fetch_headlines(self, symbols: List[str]) -> Dict[str, List[NewsItem]]:
        """
        Fetch news headlines using OpenAI web search
        
        Args:
            symbols: List of ticker symbols to fetch news for
            
        Returns:
            Dict mapping symbol -> list of NewsItem
        """
        results: Dict[str, List[NewsItem]] = {}
        
        if not self._ensure_client():
            return results
        
        for symbol in symbols:
            try:
                news_items = self._fetch_for_symbol(symbol)
                results[symbol] = news_items
                self._logger.info(f"[NewsIntel] Fetched {len(news_items)} items for {symbol}")
            except Exception as e:
                self._logger.error(f"[NewsIntel] Failed to fetch news for {symbol}: {e}")
                results[symbol] = []
        
        return results
    
    def _fetch_for_symbol(self, symbol: str) -> List[NewsItem]:
        """Fetch news for a single symbol"""
        if not self._client:
            return []
        
        prompt = f"""Search for the latest financial news about stock ticker {symbol} from the past 24 hours.

Return the top {self._max_items} most relevant news headlines in this exact JSON format:
{{
    "news": [
        {{
            "headline": "Short headline text",
            "summary": "1-2 sentence summary",
            "source": "News source name",
            "url": "URL if available or empty string",
            "published": "ISO timestamp or approximate time"
        }}
    ]
}}

Focus on:
- Earnings announcements
- FDA/regulatory decisions
- Lawsuits or legal issues
- Analyst upgrades/downgrades
- Major deals or partnerships
- Executive changes
- Guidance changes

If no recent news found, return {{"news": []}}"""

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                timeout=self._timeout,
                temperature=0.1,
                max_tokens=2000
            )
            
            content = response.choices[0].message.content
            return self._parse_news_response(symbol, content)
            
        except Exception as e:
            self._logger.error(f"[NewsIntel] OpenAI request failed for {symbol}: {e}")
            return []
    
    def _parse_news_response(self, symbol: str, content: str) -> List[NewsItem]:
        """Parse OpenAI response into NewsItem list"""
        import json
        
        try:
            # Extract JSON from response (may be wrapped in markdown)
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            data = json.loads(content.strip())
            news_list = data.get("news", [])
            
            items = []
            for n in news_list[:self._max_items]:
                item = NewsItem(
                    symbol=symbol,
                    headline=n.get("headline", ""),
                    summary=n.get("summary", ""),
                    source=n.get("source", "Unknown"),
                    url=n.get("url", ""),
                    published_at=n.get("published", datetime.now(timezone.utc).isoformat()),
                    provider_id=self.provider_id
                )
                items.append(item)
            
            return items
            
        except json.JSONDecodeError as e:
            self._logger.warn(f"[NewsIntel] Failed to parse JSON for {symbol}: {e}")
            return []
        except Exception as e:
            self._logger.error(f"[NewsIntel] Parse error for {symbol}: {e}")
            return []


class YahooFinanceNewsProvider(NewsProvider):
    """
    News provider using Yahoo Finance (via yfinance library).
    
    No API key required. Provides news from Yahoo Finance sources.
    """
    
    def __init__(self, max_items: int = 10):
        self._max_items = max_items
        self._logger = get_logger()
    
    @property
    def provider_id(self) -> str:
        return "yahoo_finance"
    
    def fetch_headlines(self, symbols: List[str]) -> Dict[str, List[NewsItem]]:
        """Fetch news headlines using yfinance"""
        results: Dict[str, List[NewsItem]] = {}
        
        for symbol in symbols:
            try:
                news_items = self._fetch_for_symbol(symbol)
                results[symbol] = news_items
                self._logger.info(f"[NewsIntel] Yahoo fetched {len(news_items)} items for {symbol}")
            except Exception as e:
                self._logger.error(f"[NewsIntel] Yahoo failed for {symbol}: {e}")
                results[symbol] = []
        
        return results
    
    def _fetch_for_symbol(self, symbol: str) -> List[NewsItem]:
        """Fetch news for a single symbol using yfinance"""
        try:
            import yfinance as yf
            
            # Handle crypto symbols (BTC/USD -> BTC-USD for yfinance)
            yf_symbol = symbol.replace("/", "-")
            
            ticker = yf.Ticker(yf_symbol)
            news_list = ticker.news or []
            
            items = []
            for n in news_list[:self._max_items]:
                # yfinance returns nested structure with 'content' dict
                content = n.get('content', {}) if isinstance(n, dict) else {}
                
                headline = content.get('title', '') or ''
                summary = content.get('summary', '') or ''
                pub_date = content.get('pubDate', '') or ''
                
                # Get URL from canonicalUrl or clickThroughUrl
                canonical = content.get('canonicalUrl', {}) or {}
                url = canonical.get('url', '') or ''
                
                # Get source/provider
                provider = content.get('provider', {}) or {}
                source = provider.get('displayName', 'Yahoo Finance') or 'Yahoo Finance'
                
                if headline:  # Only add if we have a headline
                    item = NewsItem(
                        symbol=symbol,
                        headline=headline,
                        summary=summary,
                        source=source,
                        url=url,
                        published_at=pub_date or datetime.now(timezone.utc).isoformat(),
                        provider_id=self.provider_id
                    )
                    items.append(item)
            
            self._logger.info(
                "[news_intel_fetch]",
                symbol=symbol,
                items_count=len(items),
                provider=self.provider_id
            )
            
            return items
            
        except Exception as e:
            self._logger.error(f"[NewsIntel] Yahoo request failed for {symbol}: {e}")
            return []


class AlpacaNewsProvider(NewsProvider):
    """
    News provider using Alpaca's free news API.
    
    This is the preferred provider as it returns real, up-to-date news
    without requiring additional API keys (uses existing Alpaca credentials).
    """
    
    def __init__(self, timeout_seconds: int = 10, max_items: int = 10):
        self._timeout = timeout_seconds
        self._max_items = max_items
        self._logger = get_logger()
        self._client = None
    
    def _ensure_client(self):
        """Lazy-initialize Alpaca news client"""
        if self._client is None:
            try:
                from alpaca.data.historical.news import NewsClient
                api_key = os.environ.get("ALPACA_KEY")
                secret_key = os.environ.get("ALPACA_SECRET")
                
                if not api_key or not secret_key:
                    self._logger.warn("[NewsIntel] Alpaca credentials not set, provider disabled")
                    return False
                
                self._client = NewsClient(api_key=api_key, secret_key=secret_key)
                return True
            except Exception as e:
                self._logger.error(f"[NewsIntel] Failed to init Alpaca client: {e}")
                return False
        return True
    
    @property
    def provider_id(self) -> str:
        return "alpaca"
    
    def fetch_headlines(self, symbols: List[str]) -> Dict[str, List[NewsItem]]:
        """Fetch news headlines using Alpaca API"""
        results: Dict[str, List[NewsItem]] = {}
        
        if not self._ensure_client():
            return results
        
        for symbol in symbols:
            try:
                news_items = self._fetch_for_symbol(symbol)
                results[symbol] = news_items
                self._logger.info(f"[NewsIntel] Fetched {len(news_items)} items for {symbol}")
            except Exception as e:
                self._logger.error(f"[NewsIntel] Failed to fetch for {symbol}: {e}")
                results[symbol] = []
        
        return results
    
    def _fetch_for_symbol(self, symbol: str) -> List[NewsItem]:
        """Fetch news for a single symbol using Alpaca API"""
        if not self._ensure_client():
            return []
        
        try:
            from alpaca.data.requests import NewsRequest
            
            # Handle crypto symbols (BTC/USD -> BTCUSD for Alpaca)
            alpaca_symbol = symbol.replace("/", "")
            
            request = NewsRequest(symbols=alpaca_symbol, limit=self._max_items)
            news_set = self._client.get_news(request)
            
            # Extract news items from the response
            items = []
            news_data = news_set.data.get("news", []) if hasattr(news_set, 'data') else []
            
            for n in news_data[:self._max_items]:
                # Alpaca returns News objects with attributes, not dicts
                item = NewsItem(
                    symbol=symbol,
                    headline=getattr(n, 'headline', '') or '',
                    summary=getattr(n, 'summary', '') or '',
                    source=getattr(n, 'source', 'Unknown') or 'Unknown',
                    url=getattr(n, 'url', '') or '',
                    published_at=str(getattr(n, 'created_at', datetime.now(timezone.utc).isoformat())),
                    provider_id=self.provider_id
                )
                items.append(item)
            
            self._logger.info(
                "[news_intel_fetch]",
                symbol=symbol,
                items_count=len(items),
                provider=self.provider_id
            )
            
            return items
            
        except Exception as e:
            self._logger.error(f"[NewsIntel] Alpaca request failed for {symbol}: {e}")
            return []


class CombinedNewsProvider(NewsProvider):
    """
    Combined news provider using both Alpaca and Yahoo Finance.
    
    Fetches news from both sources and deduplicates based on headline similarity.
    This provides broader coverage and more reliable news data.
    """
    
    def __init__(self, timeout_seconds: int = 10, max_items: int = 10):
        self._max_items = max_items
        self._logger = get_logger()
        self._alpaca = AlpacaNewsProvider(timeout_seconds=timeout_seconds, max_items=max_items)
        self._yahoo = YahooFinanceNewsProvider(max_items=max_items)
    
    @property
    def provider_id(self) -> str:
        return "combined_alpaca_yahoo"
    
    def fetch_headlines(self, symbols: List[str]) -> Dict[str, List[NewsItem]]:
        """Fetch news from both Alpaca and Yahoo Finance"""
        results: Dict[str, List[NewsItem]] = {}
        
        for symbol in symbols:
            try:
                combined = self._fetch_for_symbol(symbol)
                results[symbol] = combined
                self._logger.info(f"[NewsIntel] Combined fetched {len(combined)} items for {symbol}")
            except Exception as e:
                self._logger.error(f"[NewsIntel] Combined failed for {symbol}: {e}")
                results[symbol] = []
        
        return results
    
    def _fetch_for_symbol(self, symbol: str) -> List[NewsItem]:
        """Fetch and combine news from both sources"""
        alpaca_news = []
        yahoo_news = []
        
        # Fetch from Alpaca
        try:
            alpaca_results = self._alpaca.fetch_headlines([symbol])
            alpaca_news = alpaca_results.get(symbol, [])
        except Exception as e:
            self._logger.warn(f"[NewsIntel] Alpaca fetch failed for {symbol}: {e}")
        
        # Fetch from Yahoo
        try:
            yahoo_results = self._yahoo.fetch_headlines([symbol])
            yahoo_news = yahoo_results.get(symbol, [])
        except Exception as e:
            self._logger.warn(f"[NewsIntel] Yahoo fetch failed for {symbol}: {e}")
        
        # Combine and deduplicate by headline similarity
        combined = list(alpaca_news)  # Start with Alpaca news
        seen_headlines = {n.headline.lower()[:50] for n in combined}
        
        for item in yahoo_news:
            # Check for duplicates using first 50 chars of headline
            headline_key = item.headline.lower()[:50]
            if headline_key not in seen_headlines:
                combined.append(item)
                seen_headlines.add(headline_key)
        
        # Sort by published date (most recent first) and limit
        combined.sort(key=lambda x: x.published_at, reverse=True)
        
        self._logger.info(
            "[news_intel_combined]",
            symbol=symbol,
            alpaca_count=len(alpaca_news),
            yahoo_count=len(yahoo_news),
            combined_count=len(combined[:self._max_items])
        )
        
        return combined[:self._max_items]


class SimulationNewsProvider(NewsProvider):
    """
    Mock news provider for simulation/testing
    
    Generates deterministic news based on seed for reproducible tests
    """
    
    def __init__(self, seed: int = 42, activity_level: str = "normal"):
        self._seed = seed
        self._activity_level = activity_level  # "normal", "high", "extreme"
        self._logger = get_logger()
        self._call_count = 0
    
    @property
    def provider_id(self) -> str:
        return "simulation"
    
    def fetch_headlines(self, symbols: List[str]) -> Dict[str, List[NewsItem]]:
        """Generate simulated news for testing"""
        import random
        random.seed(self._seed + self._call_count)
        self._call_count += 1
        
        results: Dict[str, List[NewsItem]] = {}
        
        # Predefined news templates for simulation
        templates = {
            "bullish": [
                ("beats earnings expectations by 15%", "earnings", 0.8),
                ("receives FDA approval for new drug", "fda", 0.9),
                ("announces major partnership with tech giant", "deal", 0.7),
                ("analyst upgrades to buy with higher target", "upgrade", 0.6),
                ("reports record revenue growth", "earnings", 0.7),
            ],
            "bearish": [
                ("misses earnings estimates, stock plunges", "earnings", -0.8),
                ("faces class action lawsuit over fraud allegations", "lawsuit", -0.9),
                ("FDA rejects drug application", "fda", -0.85),
                ("analyst downgrades citing weak outlook", "downgrade", -0.6),
                ("announces layoffs amid restructuring", "restructuring", -0.5),
                ("CFO resigns amid accounting probe", "fraud", -0.95),
            ],
            "neutral": [
                ("hosts investor day, reiterates guidance", "guidance", 0.1),
                ("appoints new board member", "executive", 0.05),
                ("stock trades flat ahead of earnings", "market", 0.0),
            ]
        }
        
        # Determine how many news items to generate based on activity level
        items_per_symbol = {
            "normal": (0, 3),
            "high": (2, 5),
            "extreme": (4, 8)
        }.get(self._activity_level, (0, 3))
        
        for symbol in symbols:
            num_items = random.randint(items_per_symbol[0], items_per_symbol[1])
            items = []
            
            for i in range(num_items):
                # Randomly select sentiment category with weighted probabilities
                category = random.choices(
                    ["bullish", "bearish", "neutral"],
                    weights=[0.35, 0.35, 0.30]
                )[0]
                
                template = random.choice(templates[category])
                headline, flag, sentiment = template
                
                item = NewsItem(
                    symbol=symbol,
                    headline=f"{symbol} {headline}",
                    summary=f"Simulated news: {symbol} {headline}. Generated for testing.",
                    source="SimulationNews",
                    url="",
                    published_at=datetime.now(timezone.utc).isoformat(),
                    provider_id=self.provider_id
                )
                items.append(item)
            
            results[symbol] = items
        
        return results
    
    def set_seed(self, seed: int):
        """Update seed for next fetch"""
        self._seed = seed
        self._call_count = 0
    
    def set_activity_level(self, level: str):
        """Set activity level: normal, high, extreme"""
        self._activity_level = level


class NewsIntelligenceService:
    """
    Main news intelligence service with caching and fail-closed behavior
    
    Features:
    - Per-symbol caching with configurable TTL
    - Automatic refresh on stale cache
    - Fail-closed: returns empty results on error, never blocks trading
    - Thread-safe for concurrent access
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._logger = get_logger()
        self._cache: Dict[str, NewsCacheEntry] = {}
        self._cache_lock = threading.Lock()
        self._provider: Optional[NewsProvider] = None
        self._simulation_mode = False
        
        # Load config
        self._load_config()
        
        # Initialize provider
        self._init_provider()
        
        self._initialized = True
        self._logger.info("[NewsIntel] Service initialized")
    
    def _load_config(self):
        """Load configuration from bots.yaml"""
        try:
            config = load_bots_config()
            intel_config = config.get("intelligence", {}).get("news", {})
            
            self._enabled = intel_config.get("enabled", False)
            self._provider_name = intel_config.get("provider", "openai")
            self._refresh_seconds = intel_config.get("refresh_seconds", 60)
            self._max_items = intel_config.get("max_items_per_symbol", 10)
            self._timeout_seconds = intel_config.get("timeout_seconds", 10)
            self._dry_run = intel_config.get("dry_run", False)
            
            # Debug mode settings (only for simulation)
            debug_config = config.get("intelligence", {}).get("debug", {})
            self._force_active = debug_config.get("force_active", False)
            self._simulation_mode = debug_config.get("simulation_mode", False)
            
        except Exception as e:
            self._logger.warn(f"[NewsIntel] Config load failed, using defaults: {e}")
            self._enabled = False
            self._provider_name = "openai"
            self._refresh_seconds = 60
            self._max_items = 10
            self._timeout_seconds = 10
            self._dry_run = False
            self._force_active = False
            self._simulation_mode = False
    
    def _init_provider(self):
        """Initialize the news provider based on config"""
        if self._simulation_mode:
            self._provider = SimulationNewsProvider()
            self._logger.info("[NewsIntel] Using SIMULATION provider")
        elif self._provider_name == "openai":
            self._provider = OpenAINewsProvider(
                timeout_seconds=self._timeout_seconds,
                max_items=self._max_items
            )
            self._logger.info("[NewsIntel] Using OpenAI provider")
        elif self._provider_name == "alpaca":
            self._provider = AlpacaNewsProvider(
                timeout_seconds=self._timeout_seconds,
                max_items=self._max_items
            )
            self._logger.info("[NewsIntel] Using Alpaca provider")
        elif self._provider_name == "yahoo":
            self._provider = YahooFinanceNewsProvider(
                max_items=self._max_items
            )
            self._logger.info("[NewsIntel] Using Yahoo Finance provider")
        else:
            # Default to Combined (Alpaca + Yahoo) for broadest coverage
            self._provider = CombinedNewsProvider(
                timeout_seconds=self._timeout_seconds,
                max_items=self._max_items
            )
            self._logger.info("[NewsIntel] Using Combined (Alpaca + Yahoo) provider")
    
    def is_enabled(self) -> bool:
        """Check if news intelligence is enabled"""
        return self._enabled or self._force_active
    
    def reload_config(self):
        """Reload configuration (call after config file changes)"""
        self._load_config()
        self._init_provider()
        self._logger.info("[NewsIntel] Config reloaded")
    
    def get_news_for_symbol(self, symbol: str, force_refresh: bool = False) -> List[NewsItem]:
        """
        Get news for a symbol, using cache if available
        
        Args:
            symbol: Ticker symbol
            force_refresh: Force fetch even if cache is fresh
            
        Returns:
            List of NewsItem (empty if unavailable - fail-closed)
        """
        if not self.is_enabled():
            return []
        
        with self._cache_lock:
            cache_entry = self._cache.get(symbol)
            
            # Use cache if fresh - use context-aware TTL based on market hours
            staleness = get_data_staleness()
            ttl = staleness.get_ttl(DataType.NEWS)
            cache_age = cache_entry.age_seconds() if cache_entry else 0
            
            if cache_entry and not staleness.is_stale(DataType.NEWS, cache_age) and not force_refresh:
                self._logger.info(f"[NewsIntel] Cache hit for {symbol}, age={cache_age:.1f}s, ttl={ttl:.0f}s")
                return cache_entry.items
        
        # Fetch fresh news
        try:
            results = self._provider.fetch_headlines([symbol])
            items = results.get(symbol, [])
            
            with self._cache_lock:
                self._cache[symbol] = NewsCacheEntry(
                    symbol=symbol,
                    items=items,
                    last_fetch_ts=time.time(),
                    fetch_success=True
                )
            
            self._logger.log("news_intel_fetch", {
                "symbol": symbol,
                "items_count": len(items),
                "provider": self._provider.provider_id
            })
            
            return items
            
        except Exception as e:
            self._logger.error(f"[NewsIntel] Fetch failed for {symbol}: {e}")
            
            # Update cache with failure
            with self._cache_lock:
                self._cache[symbol] = NewsCacheEntry(
                    symbol=symbol,
                    items=[],
                    last_fetch_ts=time.time(),
                    fetch_success=False,
                    error_message=str(e)
                )
            
            return []  # Fail-closed
    
    def get_news_for_symbols(self, symbols: List[str], force_refresh: bool = False) -> Dict[str, List[NewsItem]]:
        """
        Get news for multiple symbols
        
        Args:
            symbols: List of ticker symbols
            force_refresh: Force fetch even if cache is fresh
            
        Returns:
            Dict mapping symbol -> list of NewsItem
        """
        results = {}
        symbols_to_fetch = []
        
        # Check cache first
        with self._cache_lock:
            for symbol in symbols:
                cache_entry = self._cache.get(symbol)
                if cache_entry and not cache_entry.is_stale(self._refresh_seconds) and not force_refresh:
                    results[symbol] = cache_entry.items
                else:
                    symbols_to_fetch.append(symbol)
        
        # Fetch missing symbols
        if symbols_to_fetch and self.is_enabled():
            try:
                fetched = self._provider.fetch_headlines(symbols_to_fetch)
                
                with self._cache_lock:
                    for symbol, items in fetched.items():
                        self._cache[symbol] = NewsCacheEntry(
                            symbol=symbol,
                            items=items,
                            last_fetch_ts=time.time(),
                            fetch_success=True
                        )
                        results[symbol] = items
                
            except Exception as e:
                self._logger.error(f"[NewsIntel] Batch fetch failed: {e}")
                # Fail-closed: return empty for failed symbols
                for symbol in symbols_to_fetch:
                    if symbol not in results:
                        results[symbol] = []
        
        return results
    
    def get_cache_status(self, symbol: str) -> Optional[NewsCacheEntry]:
        """Get cache entry for debugging/monitoring"""
        with self._cache_lock:
            return self._cache.get(symbol)
    
    def get_all_cache_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all cached symbols for monitoring"""
        with self._cache_lock:
            status = {}
            for symbol, entry in self._cache.items():
                status[symbol] = {
                    "items_count": len(entry.items),
                    "age_seconds": entry.age_seconds(),
                    "is_stale": entry.is_stale(self._refresh_seconds),
                    "fetch_success": entry.fetch_success,
                    "error": entry.error_message
                }
            return status
    
    def get_last_updated(self) -> Optional[float]:
        """Get timestamp of most recent fetch across all symbols"""
        with self._cache_lock:
            if not self._cache:
                return None
            return max(entry.last_fetch_ts for entry in self._cache.values())
    
    def clear_cache(self):
        """Clear all cached news (for testing)"""
        with self._cache_lock:
            self._cache.clear()
        self._logger.info("[NewsIntel] Cache cleared")
    
    def set_simulation_mode(self, enabled: bool, seed: int = 42, activity_level: str = "normal"):
        """Enable simulation mode for testing"""
        self._simulation_mode = enabled
        if enabled:
            self._provider = SimulationNewsProvider(seed=seed, activity_level=activity_level)
            self._logger.info(f"[NewsIntel] Simulation mode enabled (seed={seed}, activity={activity_level})")
        else:
            self._init_provider()
            self._logger.info("[NewsIntel] Simulation mode disabled")


# Singleton accessor
_news_intel_instance: Optional[NewsIntelligenceService] = None
_news_intel_lock = threading.Lock()


def get_news_intelligence() -> NewsIntelligenceService:
    """Get the singleton NewsIntelligenceService instance"""
    global _news_intel_instance
    with _news_intel_lock:
        if _news_intel_instance is None:
            _news_intel_instance = NewsIntelligenceService()
        return _news_intel_instance


def reset_news_intelligence():
    """Reset singleton for testing"""
    global _news_intel_instance
    with _news_intel_lock:
        if _news_intel_instance:
            _news_intel_instance.clear_cache()
        _news_intel_instance = None
