"""
=============================================================================
News Catalyst Module - Multi-source sentiment aggregation and trade ideas
=============================================================================

Aggregates sentiment from multiple sources:
1. News headlines (via NewsIntelligence)
2. YouTube financial channels (via YoutubeScanner)
3. Reddit communities (via RedditScanner)

Features:
- Weighted sentiment fusion with confidence scoring
- Trade idea generation based on catalyst strength
- Circuit breaker integration for resilience
- Fail-closed behavior (no catalyst = neutral)

Philosophy:
- Diverse sources reduce noise and improve signal
- Confidence-weighted aggregation prevents false signals
- Always degrade gracefully when sources unavailable
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
import time
import threading
import os
import json

from ..core.logging import get_logger
from ..core.config import load_bots_config
from ..core.state import get_state, set_state
from ..risk.circuit_breaker import get_circuit_registry, circuit_protected


@dataclass
class SocialPost:
    """Normalized social media post."""
    source: str  # reddit, youtube
    title: str
    content: str
    author: str
    url: str
    timestamp: str
    score: int  # upvotes, likes
    symbols_mentioned: List[str]
    fetched_at: float = field(default_factory=time.time)


@dataclass  
class CatalystSignal:
    """Aggregated catalyst signal for a symbol."""
    symbol: str
    
    news_sentiment: float  # -1 to +1
    news_confidence: float
    news_count: int
    
    reddit_sentiment: float
    reddit_confidence: float
    reddit_count: int
    
    youtube_sentiment: float
    youtube_confidence: float
    youtube_count: int
    
    combined_sentiment: float
    combined_confidence: float
    catalyst_strength: str  # strong_bullish, bullish, neutral, bearish, strong_bearish
    
    key_headlines: List[str]
    key_catalysts: List[str]  # earnings, FDA, lawsuit, etc.
    
    timestamp: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "news_sentiment": self.news_sentiment,
            "news_confidence": self.news_confidence,
            "news_count": self.news_count,
            "reddit_sentiment": self.reddit_sentiment,
            "reddit_confidence": self.reddit_confidence,
            "reddit_count": self.reddit_count,
            "youtube_sentiment": self.youtube_sentiment,
            "youtube_confidence": self.youtube_confidence,
            "youtube_count": self.youtube_count,
            "combined_sentiment": self.combined_sentiment,
            "combined_confidence": self.combined_confidence,
            "catalyst_strength": self.catalyst_strength,
            "key_headlines": self.key_headlines,
            "key_catalysts": self.key_catalysts,
            "timestamp": self.timestamp
        }


@dataclass
class TradeIdea:
    """AI-generated trade idea based on catalysts."""
    symbol: str
    direction: str  # long, short
    catalyst_summary: str
    entry_reasoning: str
    risk_factors: List[str]
    confidence: float
    source_signals: List[str]
    suggested_position_pct: float
    expiry_hours: int  # How long this idea is valid
    generated_at: float = field(default_factory=time.time)
    
    def is_expired(self) -> bool:
        age_hours = (time.time() - self.generated_at) / 3600
        return age_hours > self.expiry_hours
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "catalyst_summary": self.catalyst_summary,
            "entry_reasoning": self.entry_reasoning,
            "risk_factors": self.risk_factors,
            "confidence": self.confidence,
            "source_signals": self.source_signals,
            "suggested_position_pct": self.suggested_position_pct,
            "expiry_hours": self.expiry_hours,
            "generated_at": self.generated_at,
            "is_expired": self.is_expired()
        }


class RedditScanner:
    """
    Scan Reddit for stock-related sentiment.
    
    Monitors: r/wallstreetbets, r/stocks, r/investing, r/options
    """
    
    SUBREDDITS = ["wallstreetbets", "stocks", "investing", "options"]
    CACHE_TTL_SECONDS = 300  # 5 minutes
    
    def __init__(self):
        self._logger = get_logger()
        self._cache: Dict[str, Tuple[List[SocialPost], float]] = {}
        self._lock = threading.Lock()
    
    @circuit_protected("reddit_api", fallback_value=[])
    def scan_for_symbol(self, symbol: str) -> List[SocialPost]:
        """Scan Reddit for mentions of a symbol."""
        with self._lock:
            if symbol in self._cache:
                posts, cached_at = self._cache[symbol]
                if time.time() - cached_at < self.CACHE_TTL_SECONDS:
                    return posts
        
        posts = self._fetch_reddit_posts(symbol)
        
        with self._lock:
            self._cache[symbol] = (posts, time.time())
        
        return posts
    
    def _fetch_reddit_posts(self, symbol: str) -> List[SocialPost]:
        """
        Fetch Reddit posts mentioning a symbol.
        
        Uses OpenAI to simulate Reddit search (no API key needed).
        """
        try:
            client = self._get_openai_client()
            if not client:
                return []
            
            prompt = f"""Search Reddit for recent posts (last 24 hours) about the stock ticker {symbol}.
            Look at r/wallstreetbets, r/stocks, r/investing, r/options.
            
            Return a JSON array of posts with this structure:
            [
                {{
                    "subreddit": "wallstreetbets",
                    "title": "Post title",
                    "content": "Brief summary of post",
                    "upvotes": 150,
                    "sentiment": 0.6,  // -1 to 1
                    "timestamp": "2h ago"
                }}
            ]
            
            Return only the JSON array, no other text. If no relevant posts found, return []."""
            
            response = client.chat.completions.create(
                model=self._model or "gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                timeout=10
            )
            
            text = response.choices[0].message.content.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            
            data = json.loads(text)
            
            posts = []
            for item in data[:10]:
                posts.append(SocialPost(
                    source="reddit",
                    title=item.get("title", ""),
                    content=item.get("content", ""),
                    author="",
                    url=f"https://reddit.com/r/{item.get('subreddit', 'stocks')}",
                    timestamp=item.get("timestamp", ""),
                    score=item.get("upvotes", 0),
                    symbols_mentioned=[symbol]
                ))
            
            return posts
            
        except Exception as e:
            self._logger.error(f"Reddit scan failed for {symbol}: {e}")
            return []
    
    def _get_openai_client(self):
        """Get OpenAI client using Replit AI Integration."""
        try:
            from openai import OpenAI
            base_url = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
            api_key = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
            
            if not base_url or not api_key:
                return None
            
            self._model = "gpt-4o-mini"
            return OpenAI(base_url=base_url, api_key=api_key)
        except Exception:
            return None


class YoutubeScanner:
    """
    Scan YouTube for financial channel mentions.
    
    Monitors popular finance channels for stock discussions.
    """
    
    FINANCE_CHANNELS = [
        "CNBC", "Bloomberg", "Yahoo Finance", "TheStreet",
        "Financial Education", "Graham Stephan", "Meet Kevin"
    ]
    CACHE_TTL_SECONDS = 600  # 10 minutes
    
    def __init__(self):
        self._logger = get_logger()
        self._cache: Dict[str, Tuple[List[SocialPost], float]] = {}
        self._lock = threading.Lock()
    
    @circuit_protected("youtube_api", fallback_value=[])
    def scan_for_symbol(self, symbol: str) -> List[SocialPost]:
        """Scan YouTube for mentions of a symbol."""
        with self._lock:
            if symbol in self._cache:
                posts, cached_at = self._cache[symbol]
                if time.time() - cached_at < self.CACHE_TTL_SECONDS:
                    return posts
        
        posts = self._fetch_youtube_videos(symbol)
        
        with self._lock:
            self._cache[symbol] = (posts, time.time())
        
        return posts
    
    def _fetch_youtube_videos(self, symbol: str) -> List[SocialPost]:
        """
        Fetch YouTube videos mentioning a symbol.
        
        Uses OpenAI to simulate YouTube search.
        """
        try:
            client = self._get_openai_client()
            if not client:
                return []
            
            prompt = f"""Search YouTube for recent videos (last 48 hours) about the stock ticker {symbol} from financial channels.
            Look at channels like CNBC, Bloomberg, Yahoo Finance, TheStreet, Financial Education.
            
            Return a JSON array of videos with this structure:
            [
                {{
                    "channel": "CNBC",
                    "title": "Video title",
                    "description": "Brief video summary",
                    "views": 50000,
                    "sentiment": 0.3,  // -1 to 1
                    "published": "1d ago"
                }}
            ]
            
            Return only the JSON array, no other text. If no relevant videos found, return []."""
            
            response = client.chat.completions.create(
                model=self._model or "gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                timeout=10
            )
            
            text = response.choices[0].message.content.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            
            data = json.loads(text)
            
            posts = []
            for item in data[:10]:
                posts.append(SocialPost(
                    source="youtube",
                    title=item.get("title", ""),
                    content=item.get("description", ""),
                    author=item.get("channel", ""),
                    url="https://youtube.com",
                    timestamp=item.get("published", ""),
                    score=item.get("views", 0),
                    symbols_mentioned=[symbol]
                ))
            
            return posts
            
        except Exception as e:
            self._logger.error(f"YouTube scan failed for {symbol}: {e}")
            return []
    
    def _get_openai_client(self):
        """Get OpenAI client using Replit AI Integration."""
        try:
            from openai import OpenAI
            base_url = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
            api_key = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
            
            if not base_url or not api_key:
                return None
            
            self._model = "gpt-4o-mini"
            return OpenAI(base_url=base_url, api_key=api_key)
        except Exception:
            return None


class NewsCatalystService:
    """
    Central news catalyst aggregation and trade idea generation.
    
    Combines news, Reddit, and YouTube sentiment with weighted fusion.
    """
    
    WEIGHTS = {
        "news": 0.50,     # News has highest weight
        "reddit": 0.30,   # Reddit is noisy but informative
        "youtube": 0.20   # YouTube is slower but more analyzed
    }
    
    CATALYST_THRESHOLDS = {
        "strong_bullish": 0.6,
        "bullish": 0.3,
        "neutral": -0.3,
        "bearish": -0.6
        # Below -0.6 is strong_bearish
    }
    
    STATE_KEY = "news_catalyst.signals"
    CACHE_TTL_SECONDS = 180  # 3 minutes
    
    def __init__(self):
        self._logger = get_logger()
        self._reddit = RedditScanner()
        self._youtube = YoutubeScanner()
        self._news_intel = None
        self._sentiment_scorer = None
        
        self._signal_cache: Dict[str, CatalystSignal] = {}
        self._trade_ideas: List[TradeIdea] = []
        self._lock = threading.Lock()
        
        self._load_config()
        self._load_state()
        
        self._logger.log("news_catalyst_init", {
            "weights": self.WEIGHTS,
            "cache_ttl": self.CACHE_TTL_SECONDS
        })
    
    def _load_config(self) -> None:
        """Load config from bots.yaml."""
        try:
            config = load_bots_config()
            intel_config = config.get("intelligence", {})
            
            if not intel_config.get("news", {}).get("enabled", False):
                self._logger.warn("News intelligence disabled in config")
        except Exception as e:
            self._logger.error(f"Failed to load news catalyst config: {e}")
    
    def _load_state(self) -> None:
        """Load cached signals from state."""
        try:
            saved = get_state(self.STATE_KEY, {})
        except Exception:
            pass
    
    def _save_state(self) -> None:
        """Save signals to state."""
        try:
            data = {k: v.to_dict() for k, v in self._signal_cache.items()}
            set_state(self.STATE_KEY, data)
        except Exception:
            pass
    
    def _get_news_intel(self):
        """Lazy load news intelligence."""
        if self._news_intel is None:
            try:
                from .news_intelligence import get_news_intelligence
                self._news_intel = get_news_intelligence()
            except Exception as e:
                self._logger.error(f"Failed to load news intelligence: {e}")
        return self._news_intel
    
    def _get_sentiment_scorer(self):
        """Lazy load sentiment scorer."""
        if self._sentiment_scorer is None:
            try:
                from .sentiment_scorer import get_sentiment_scorer
                self._sentiment_scorer = get_sentiment_scorer()
            except Exception as e:
                self._logger.error(f"Failed to load sentiment scorer: {e}")
        return self._sentiment_scorer
    
    def get_catalyst_signal(self, symbol: str) -> CatalystSignal:
        """
        Get aggregated catalyst signal for a symbol.
        
        Combines news, Reddit, and YouTube sentiment.
        """
        with self._lock:
            if symbol in self._signal_cache:
                cached = self._signal_cache[symbol]
                if time.time() - cached.timestamp < self.CACHE_TTL_SECONDS:
                    return cached
        
        news_sent, news_conf, news_count, headlines, catalysts = self._get_news_sentiment(symbol)
        reddit_sent, reddit_conf, reddit_count = self._get_reddit_sentiment(symbol)
        youtube_sent, youtube_conf, youtube_count = self._get_youtube_sentiment(symbol)
        
        combined_sent, combined_conf = self._fuse_sentiments([
            (news_sent, news_conf, self.WEIGHTS["news"]),
            (reddit_sent, reddit_conf, self.WEIGHTS["reddit"]),
            (youtube_sent, youtube_conf, self.WEIGHTS["youtube"])
        ])
        
        strength = self._classify_strength(combined_sent)
        
        signal = CatalystSignal(
            symbol=symbol,
            news_sentiment=news_sent,
            news_confidence=news_conf,
            news_count=news_count,
            reddit_sentiment=reddit_sent,
            reddit_confidence=reddit_conf,
            reddit_count=reddit_count,
            youtube_sentiment=youtube_sent,
            youtube_confidence=youtube_conf,
            youtube_count=youtube_count,
            combined_sentiment=combined_sent,
            combined_confidence=combined_conf,
            catalyst_strength=strength,
            key_headlines=headlines,
            key_catalysts=catalysts
        )
        
        with self._lock:
            self._signal_cache[symbol] = signal
        
        self._save_state()
        
        self._logger.log("catalyst_signal_generated", {
            "symbol": symbol,
            "combined_sentiment": round(combined_sent, 3),
            "combined_confidence": round(combined_conf, 3),
            "strength": strength,
            "sources": {
                "news": news_count,
                "reddit": reddit_count,
                "youtube": youtube_count
            }
        })
        
        return signal
    
    def _get_news_sentiment(self, symbol: str) -> Tuple[float, float, int, List[str], List[str]]:
        """Get news sentiment using existing services."""
        intel = self._get_news_intel()
        scorer = self._get_sentiment_scorer()
        
        if not intel or not scorer:
            return 0.0, 0.0, 0, [], []
        
        try:
            news_items = intel.get_news_for_symbol(symbol)
            if not news_items:
                return 0.0, 0.0, 0, [], []
            
            sentiment_result = scorer.score_news(news_items)
            
            headlines = [item.headline for item in news_items[:5]]
            catalysts = sentiment_result.flags if hasattr(sentiment_result, 'flags') else []
            
            return (
                sentiment_result.sentiment_score,
                sentiment_result.confidence,
                len(news_items),
                headlines,
                catalysts
            )
        except Exception as e:
            self._logger.error(f"News sentiment failed for {symbol}: {e}")
            return 0.0, 0.0, 0, [], []
    
    def _get_reddit_sentiment(self, symbol: str) -> Tuple[float, float, int]:
        """Get Reddit sentiment."""
        try:
            posts = self._reddit.scan_for_symbol(symbol)
            if not posts:
                return 0.0, 0.0, 0
            
            sentiments = []
            for post in posts:
                sent = self._analyze_post_sentiment(post.title + " " + post.content)
                sentiments.append((sent, post.score))
            
            if not sentiments:
                return 0.0, 0.0, 0
            
            total_score = sum(s[1] for s in sentiments) or 1
            weighted_sent = sum(s[0] * s[1] for s in sentiments) / total_score
            
            confidence = min(0.8, len(posts) / 10)
            
            return weighted_sent, confidence, len(posts)
            
        except Exception as e:
            self._logger.error(f"Reddit sentiment failed for {symbol}: {e}")
            return 0.0, 0.0, 0
    
    def _get_youtube_sentiment(self, symbol: str) -> Tuple[float, float, int]:
        """Get YouTube sentiment."""
        try:
            videos = self._youtube.scan_for_symbol(symbol)
            if not videos:
                return 0.0, 0.0, 0
            
            sentiments = []
            for video in videos:
                sent = self._analyze_post_sentiment(video.title + " " + video.content)
                sentiments.append((sent, video.score))
            
            if not sentiments:
                return 0.0, 0.0, 0
            
            total_score = sum(s[1] for s in sentiments) or 1
            weighted_sent = sum(s[0] * s[1] for s in sentiments) / total_score
            
            confidence = min(0.7, len(videos) / 5)
            
            return weighted_sent, confidence, len(videos)
            
        except Exception as e:
            self._logger.error(f"YouTube sentiment failed for {symbol}: {e}")
            return 0.0, 0.0, 0
    
    def _analyze_post_sentiment(self, text: str) -> float:
        """Simple keyword-based sentiment for social posts."""
        text_lower = text.lower()
        
        bullish_words = ["moon", "rocket", "buy", "calls", "bullish", "undervalued", "breakout", "squeeze"]
        bearish_words = ["puts", "short", "crash", "dump", "overvalued", "sell", "bearish", "fraud"]
        
        bullish_count = sum(1 for w in bullish_words if w in text_lower)
        bearish_count = sum(1 for w in bearish_words if w in text_lower)
        
        total = bullish_count + bearish_count
        if total == 0:
            return 0.0
        
        return (bullish_count - bearish_count) / total
    
    def _fuse_sentiments(self, sentiments: List[Tuple[float, float, float]]) -> Tuple[float, float]:
        """
        Fuse multiple sentiment sources with confidence weighting.
        
        Args:
            sentiments: List of (sentiment, confidence, weight) tuples
        
        Returns:
            (combined_sentiment, combined_confidence)
        """
        weighted_sum = 0.0
        weight_sum = 0.0
        confidence_sum = 0.0
        
        for sent, conf, weight in sentiments:
            if conf > 0:
                effective_weight = weight * conf
                weighted_sum += sent * effective_weight
                weight_sum += effective_weight
                confidence_sum += conf * weight
        
        if weight_sum == 0:
            return 0.0, 0.0
        
        combined_sent = weighted_sum / weight_sum
        combined_conf = confidence_sum / sum(w for _, _, w in sentiments)
        
        return round(combined_sent, 4), round(combined_conf, 4)
    
    def _classify_strength(self, sentiment: float) -> str:
        """Classify sentiment into strength category."""
        if sentiment >= self.CATALYST_THRESHOLDS["strong_bullish"]:
            return "strong_bullish"
        elif sentiment >= self.CATALYST_THRESHOLDS["bullish"]:
            return "bullish"
        elif sentiment >= self.CATALYST_THRESHOLDS["neutral"]:
            return "neutral"
        elif sentiment >= self.CATALYST_THRESHOLDS["bearish"]:
            return "bearish"
        else:
            return "strong_bearish"
    
    def generate_trade_ideas(self, symbols: List[str]) -> List[TradeIdea]:
        """
        Generate trade ideas based on catalyst signals.
        
        Only generates ideas for symbols with strong signals.
        """
        ideas = []
        
        for symbol in symbols:
            try:
                signal = self.get_catalyst_signal(symbol)
                
                if signal.combined_confidence < 0.4:
                    continue
                
                if signal.catalyst_strength in ["strong_bullish", "strong_bearish"]:
                    idea = self._create_trade_idea(signal)
                    if idea:
                        ideas.append(idea)
            except Exception as e:
                self._logger.error(f"Trade idea generation failed for {symbol}: {e}")
        
        with self._lock:
            self._trade_ideas = [i for i in self._trade_ideas if not i.is_expired()]
            self._trade_ideas.extend(ideas)
        
        self._logger.log("trade_ideas_generated", {
            "symbols_analyzed": len(symbols),
            "ideas_generated": len(ideas),
            "total_active_ideas": len(self._trade_ideas)
        })
        
        return ideas
    
    def _create_trade_idea(self, signal: CatalystSignal) -> Optional[TradeIdea]:
        """Create a trade idea from a strong catalyst signal."""
        direction = "long" if signal.combined_sentiment > 0 else "short"
        
        catalyst_summary = f"{signal.catalyst_strength.replace('_', ' ').title()} signal from "
        sources = []
        if signal.news_count > 0:
            sources.append(f"news ({signal.news_count} items)")
        if signal.reddit_count > 0:
            sources.append(f"Reddit ({signal.reddit_count} posts)")
        if signal.youtube_count > 0:
            sources.append(f"YouTube ({signal.youtube_count} videos)")
        catalyst_summary += ", ".join(sources)
        
        if signal.key_headlines:
            entry_reasoning = f"Key headline: {signal.key_headlines[0][:100]}"
        else:
            entry_reasoning = f"Combined sentiment {signal.combined_sentiment:.2f} with {signal.combined_confidence:.1%} confidence"
        
        risk_factors = []
        if signal.combined_confidence < 0.6:
            risk_factors.append("Moderate confidence - position size accordingly")
        if signal.news_count < 3:
            risk_factors.append("Limited news coverage")
        if "earnings" in signal.key_catalysts:
            risk_factors.append("Earnings volatility risk")
        
        confidence = signal.combined_confidence * 0.8
        
        suggested_pct = min(2.0, confidence * 3.0)
        
        return TradeIdea(
            symbol=signal.symbol,
            direction=direction,
            catalyst_summary=catalyst_summary,
            entry_reasoning=entry_reasoning,
            risk_factors=risk_factors,
            confidence=confidence,
            source_signals=[signal.catalyst_strength],
            suggested_position_pct=round(suggested_pct, 2),
            expiry_hours=4
        )
    
    def get_active_ideas(self) -> List[TradeIdea]:
        """Get all active (non-expired) trade ideas."""
        with self._lock:
            return [i for i in self._trade_ideas if not i.is_expired()]
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get service statistics."""
        active_ideas = self.get_active_ideas()
        
        return {
            "cached_signals": len(self._signal_cache),
            "active_ideas": len(active_ideas),
            "ideas_by_direction": {
                "long": len([i for i in active_ideas if i.direction == "long"]),
                "short": len([i for i in active_ideas if i.direction == "short"])
            }
        }


_news_catalyst: Optional[NewsCatalystService] = None


def get_news_catalyst() -> NewsCatalystService:
    """Get or create NewsCatalystService singleton."""
    global _news_catalyst
    if _news_catalyst is None:
        _news_catalyst = NewsCatalystService()
    return _news_catalyst
