"""
SentimentScorerService - AI-powered sentiment analysis for trading decisions

This service provides:
1. OpenAI-powered sentiment scoring for news items
2. Flag detection (earnings, lawsuit, FDA, etc.)
3. Confidence scoring and fail-closed behavior
4. Caching to minimize API calls

Usage:
    from trading_hydra.services.sentiment_scorer import get_sentiment_scorer
    from trading_hydra.services.news_intelligence import get_news_intelligence
    
    intel = get_news_intelligence()
    scorer = get_sentiment_scorer()
    
    news = intel.get_news_for_symbol("AAPL")
    sentiment = scorer.score_news(news)
"""

import os
import time
import json
import threading
from typing import Dict, List, Optional, Any, Set
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..core.logging import get_logger
from ..core.config import load_bots_config
from .news_intelligence import NewsItem


@dataclass
class SentimentResult:
    """Result of sentiment analysis for a symbol"""
    symbol: str
    sentiment_score: float  # -1.0 (very negative) to +1.0 (very positive)
    confidence: float  # 0.0 to 1.0
    reason_short: str  # <= 140 chars
    flags: List[str]  # e.g., ["earnings", "lawsuit", "fda"]
    news_count: int  # Number of news items analyzed
    timestamp: float = field(default_factory=time.time)
    is_stale: bool = False
    error: str = ""
    
    @property
    def is_severe_negative(self) -> bool:
        """Check if this is a severe negative signal (lawsuit, fraud, FDA rejection)"""
        severe_flags = {"lawsuit", "fraud", "fda_rejection", "sec_investigation", "bankruptcy"}
        return bool(set(self.flags) & severe_flags)
    
    @property
    def is_positive(self) -> bool:
        return self.sentiment_score > 0.15
    
    @property
    def is_negative(self) -> bool:
        return self.sentiment_score < -0.15
    
    @property
    def is_neutral(self) -> bool:
        return -0.15 <= self.sentiment_score <= 0.15
    
    def age_seconds(self) -> float:
        return time.time() - self.timestamp


# Unknown/failed sentiment result
UNKNOWN_SENTIMENT = SentimentResult(
    symbol="",
    sentiment_score=0.0,
    confidence=0.0,
    reason_short="Unable to determine sentiment",
    flags=[],
    news_count=0,
    is_stale=True,
    error="No sentiment available"
)


@dataclass
class SentimentCacheEntry:
    """Cache entry for sentiment results"""
    symbol: str
    result: SentimentResult
    news_hash: str  # Hash of news items to detect changes
    cached_at: float = field(default_factory=time.time)
    
    def is_stale(self, ttl_seconds: float) -> bool:
        return (time.time() - self.cached_at) > ttl_seconds


class SentimentScorerService:
    """
    AI-powered sentiment scoring service
    
    Features:
    - OpenAI integration for sentiment analysis
    - Hard timeout with fail-closed behavior
    - Caching to minimize API calls
    - Flag detection for specific event types
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
        self._cache: Dict[str, SentimentCacheEntry] = {}
        self._cache_lock = threading.Lock()
        self._client = None
        self._simulation_mode = False
        
        # Load config
        self._load_config()
        
        # Initialize OpenAI client
        self._init_client()
        
        self._initialized = True
        self._logger.info("[SentimentScorer] Service initialized")
    
    def _load_config(self):
        """Load configuration from bots.yaml"""
        try:
            config = load_bots_config()
            intel_config = config.get("intelligence", {}).get("news", {})
            openai_config = intel_config.get("openai_sentiment", {})
            
            self._enabled = intel_config.get("enabled", False) and openai_config.get("enabled", True)
            self._timeout_seconds = openai_config.get("timeout_seconds", 5)
            self._cache_ttl = intel_config.get("refresh_seconds", 60)
            self._model = openai_config.get("model", "gpt-4o-mini")
            self._dry_run = intel_config.get("dry_run", False)
            
            # Debug mode
            debug_config = config.get("intelligence", {}).get("debug", {})
            self._simulation_mode = debug_config.get("simulation_mode", False)
            self._lower_confidence_floor = debug_config.get("lower_confidence_floor", 0.0)
            
        except Exception as e:
            self._logger.warn(f"[SentimentScorer] Config load failed: {e}")
            self._enabled = False
            self._timeout_seconds = 5
            self._cache_ttl = 60
            self._model = "gpt-4o-mini"
            self._dry_run = False
            self._simulation_mode = False
            self._lower_confidence_floor = 0.0
    
    def _init_client(self):
        """Initialize OpenAI client"""
        if self._simulation_mode:
            self._logger.info("[SentimentScorer] Running in simulation mode")
            return
        
        try:
            from openai import OpenAI
            base_url = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
            api_key = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
            
            if base_url and api_key:
                self._client = OpenAI(base_url=base_url, api_key=api_key)
                self._logger.info("[SentimentScorer] OpenAI client initialized")
            else:
                self._logger.warn("[SentimentScorer] OpenAI env vars not set")
                
        except Exception as e:
            self._logger.error(f"[SentimentScorer] Failed to init client: {e}")
    
    def is_enabled(self) -> bool:
        """Check if sentiment scoring is enabled"""
        return self._enabled
    
    def reload_config(self):
        """Reload configuration"""
        self._load_config()
        self._init_client()
    
    def _compute_news_hash(self, news_items: List[NewsItem]) -> str:
        """Compute hash of news items to detect changes"""
        import hashlib
        content = "|".join(sorted(n.headline for n in news_items))
        return hashlib.md5(content.encode()).hexdigest()[:16]
    
    def score_news(self, news_items: List[NewsItem], force_refresh: bool = False) -> SentimentResult:
        """
        Score sentiment of news items for a symbol
        
        Args:
            news_items: List of NewsItem for a single symbol
            force_refresh: Force new scoring even if cached
            
        Returns:
            SentimentResult (UNKNOWN_SENTIMENT on failure - fail-closed)
        """
        if not news_items:
            return SentimentResult(
                symbol="",
                sentiment_score=0.0,
                confidence=0.0,
                reason_short="No news to analyze",
                flags=[],
                news_count=0
            )
        
        symbol = news_items[0].symbol
        news_hash = self._compute_news_hash(news_items)
        
        # Check cache
        with self._cache_lock:
            cached = self._cache.get(symbol)
            if cached and not force_refresh:
                if cached.news_hash == news_hash and not cached.is_stale(self._cache_ttl):
                    self._logger.info(f"[SentimentScorer] Cache hit for {symbol}")
                    return cached.result
        
        # Simulation mode
        if self._simulation_mode:
            return self._score_simulated(symbol, news_items)
        
        # Real scoring
        if not self._client:
            self._logger.warn(f"[SentimentScorer] No client, returning UNKNOWN for {symbol}")
            return self._create_unknown_result(symbol, "OpenAI client not available")
        
        try:
            result = self._score_with_openai(symbol, news_items)
            
            # Cache result
            with self._cache_lock:
                self._cache[symbol] = SentimentCacheEntry(
                    symbol=symbol,
                    result=result,
                    news_hash=news_hash
                )
            
            self._logger.log("sentiment_scored", {
                "symbol": symbol,
                "score": result.sentiment_score,
                "confidence": result.confidence,
                "flags": result.flags,
                "news_count": len(news_items)
            })
            
            return result
            
        except Exception as e:
            self._logger.error(f"[SentimentScorer] Scoring failed for {symbol}: {e}")
            return self._create_unknown_result(symbol, str(e))
    
    def _create_unknown_result(self, symbol: str, error: str) -> SentimentResult:
        """Create an UNKNOWN result (fail-closed)"""
        return SentimentResult(
            symbol=symbol,
            sentiment_score=0.0,
            confidence=0.0,
            reason_short="Sentiment unavailable - fail-closed",
            flags=[],
            news_count=0,
            is_stale=True,
            error=error
        )
    
    def _score_with_openai(self, symbol: str, news_items: List[NewsItem]) -> SentimentResult:
        """Score news using OpenAI API"""
        # Build compact prompt
        headlines = "\n".join([f"- {n.headline}" for n in news_items[:10]])
        
        prompt = f"""Analyze the sentiment of these news headlines for stock {symbol}:

{headlines}

Respond with JSON only:
{{
    "sentiment_score": <float -1.0 to +1.0>,
    "confidence": <float 0.0 to 1.0>,
    "reason": "<140 char explanation>",
    "flags": [<list of applicable flags>]
}}

Flags to consider: earnings, lawsuit, fda, guidance, downgrade, upgrade, fraud, sec, bankruptcy, deal, partnership, executive, layoffs, recall, hack, regulatory, macro, geopolitical

-1.0 = extremely negative (bankruptcy, fraud)
-0.5 = moderately negative (earnings miss)
0.0 = neutral
+0.5 = moderately positive (beat earnings)
+1.0 = extremely positive (major deal)"""

        try:
            # Use temperature=0 for fully deterministic sentiment decisions
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                timeout=self._timeout_seconds,
                temperature=0,
                max_tokens=200
            )
            
            content = response.choices[0].message.content
            return self._parse_sentiment_response(symbol, content, len(news_items))
            
        except Exception as e:
            raise RuntimeError(f"OpenAI API error: {e}")
    
    def _parse_sentiment_response(self, symbol: str, content: str, news_count: int) -> SentimentResult:
        """Parse OpenAI response into SentimentResult"""
        try:
            # Extract JSON
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            data = json.loads(content.strip())
            
            score = float(data.get("sentiment_score", 0.0))
            score = max(-1.0, min(1.0, score))  # Clamp to range
            
            confidence = float(data.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))
            
            reason = str(data.get("reason", ""))[:140]
            
            flags = data.get("flags", [])
            if isinstance(flags, list):
                flags = [str(f).lower() for f in flags]
            else:
                flags = []
            
            return SentimentResult(
                symbol=symbol,
                sentiment_score=score,
                confidence=confidence,
                reason_short=reason,
                flags=flags,
                news_count=news_count
            )
            
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            self._logger.warn(f"[SentimentScorer] Parse error: {e}")
            return self._create_unknown_result(symbol, f"Parse error: {e}")
    
    def _score_simulated(self, symbol: str, news_items: List[NewsItem]) -> SentimentResult:
        """Generate simulated sentiment for testing"""
        import random
        import hashlib
        
        # Use news content to generate deterministic but varied results
        seed = int(hashlib.md5(symbol.encode()).hexdigest()[:8], 16)
        random.seed(seed + len(news_items))
        
        # Analyze headlines for keywords
        all_text = " ".join([n.headline.lower() for n in news_items])
        
        flags = []
        base_score = 0.0
        
        # Keyword detection
        if "earnings" in all_text or "beat" in all_text:
            flags.append("earnings")
            base_score += 0.3 if "beat" in all_text else -0.3
        if "lawsuit" in all_text or "sued" in all_text:
            flags.append("lawsuit")
            base_score -= 0.5
        if "fda" in all_text:
            flags.append("fda")
            base_score += 0.4 if "approved" in all_text else -0.4
        if "downgrade" in all_text:
            flags.append("downgrade")
            base_score -= 0.3
        if "upgrade" in all_text:
            flags.append("upgrade")
            base_score += 0.3
        if "fraud" in all_text:
            flags.append("fraud")
            base_score -= 0.8
        if "plunge" in all_text or "crash" in all_text:
            base_score -= 0.4
        if "surge" in all_text or "soar" in all_text:
            base_score += 0.4
        
        # Add some randomness
        score = base_score + random.uniform(-0.2, 0.2)
        score = max(-1.0, min(1.0, score))
        
        confidence = 0.6 + random.uniform(0.0, 0.35)
        
        # Apply debug floor if set
        if self._lower_confidence_floor > 0:
            confidence = max(confidence, self._lower_confidence_floor)
        
        return SentimentResult(
            symbol=symbol,
            sentiment_score=score,
            confidence=confidence,
            reason_short=f"Simulated: {len(news_items)} headlines analyzed for {symbol}",
            flags=flags,
            news_count=len(news_items)
        )
    
    def score_symbols(self, symbol_news: Dict[str, List[NewsItem]]) -> Dict[str, SentimentResult]:
        """
        Score sentiment for multiple symbols
        
        Args:
            symbol_news: Dict mapping symbol -> list of NewsItem
            
        Returns:
            Dict mapping symbol -> SentimentResult
        """
        results = {}
        for symbol, news_items in symbol_news.items():
            results[symbol] = self.score_news(news_items)
        return results
    
    def get_cached_sentiment(self, symbol: str) -> Optional[SentimentResult]:
        """Get cached sentiment without fetching"""
        with self._cache_lock:
            cached = self._cache.get(symbol)
            if cached:
                result = cached.result
                result.is_stale = cached.is_stale(self._cache_ttl)
                return result
        return None
    
    def clear_cache(self):
        """Clear all cached sentiments"""
        with self._cache_lock:
            self._cache.clear()
        self._logger.info("[SentimentScorer] Cache cleared")
    
    def set_simulation_mode(self, enabled: bool):
        """Enable/disable simulation mode"""
        self._simulation_mode = enabled
        self._logger.info(f"[SentimentScorer] Simulation mode: {enabled}")


# Singleton accessor
_scorer_instance: Optional[SentimentScorerService] = None
_scorer_lock = threading.Lock()


def get_sentiment_scorer() -> SentimentScorerService:
    """Get the singleton SentimentScorerService instance"""
    global _scorer_instance
    with _scorer_lock:
        if _scorer_instance is None:
            _scorer_instance = SentimentScorerService()
        return _scorer_instance


def reset_sentiment_scorer():
    """Reset singleton for testing"""
    global _scorer_instance
    with _scorer_lock:
        if _scorer_instance:
            _scorer_instance.clear_cache()
        _scorer_instance = None
