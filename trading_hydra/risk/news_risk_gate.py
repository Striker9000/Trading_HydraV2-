"""News Risk Gate - Unified News Sentiment Gate for All Bots.

Single point of control for news-based entry/exit decisions.
All bots consult this gate before taking action.

Rules:
- Severe negative sentiment → block entries, force exits
- Moderate negative → reduce size or skip
- Neutral/positive → proceed normally

Safe defaults for live trading.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional, Any, List
from enum import Enum

from ..core.logging import get_logger
from ..core.config import load_settings


class NewsAction(Enum):
    """Recommended action based on news."""
    ALLOW = "allow"
    REDUCE_SIZE = "reduce_size"
    SKIP_ENTRY = "skip_entry"
    FORCE_EXIT = "force_exit"


@dataclass
class NewsGateResult:
    """Result of news gate evaluation."""
    action: NewsAction
    reason: str
    sentiment_score: float
    confidence: float
    size_multiplier: float
    symbol: str
    evaluated_at: datetime
    cache_stale: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "sentiment_score": round(self.sentiment_score, 3),
            "confidence": round(self.confidence, 3),
            "size_multiplier": self.size_multiplier,
            "symbol": self.symbol,
            "evaluated_at": self.evaluated_at.isoformat(),
            "cache_stale": self.cache_stale
        }


class NewsRiskGate:
    """
    Unified news risk gate for all trading bots.
    
    Philosophy:
    - One gate, consistent rules across all bots
    - Fail-closed: if news service unavailable, proceed cautiously
    - Always explain decisions
    
    Thresholds:
    - SEVERE: sentiment < -0.85 → force exit
    - NEGATIVE: sentiment < -0.70 → skip entry
    - CAUTIOUS: sentiment < -0.40 → reduce size 50%
    - NEUTRAL: -0.40 to +0.40 → proceed normally
    - POSITIVE: > +0.40 → proceed (no boost)
    """
    
    # Sentiment thresholds
    SEVERE_THRESHOLD = -0.85
    NEGATIVE_THRESHOLD = -0.70
    CAUTIOUS_THRESHOLD = -0.40
    
    # Size multipliers
    CAUTIOUS_SIZE_MULT = 0.5
    
    # Confidence requirements
    MIN_CONFIDENCE_EXIT = 0.60
    MIN_CONFIDENCE_ENTRY = 0.50
    
    # Cache staleness
    CACHE_STALE_SECONDS = 300  # 5 minutes
    
    def __init__(self):
        self._logger = get_logger()
        self._settings = load_settings()
        
        config = self._settings.get("news_risk_gate", {})
        self._enabled = config.get("enabled", True)
        
        # Override thresholds from config
        self._severe = config.get("severe_threshold", self.SEVERE_THRESHOLD)
        self._negative = config.get("negative_threshold", self.NEGATIVE_THRESHOLD)
        self._cautious = config.get("cautious_threshold", self.CAUTIOUS_THRESHOLD)
        self._min_conf_exit = config.get("min_confidence_exit", self.MIN_CONFIDENCE_EXIT)
        self._min_conf_entry = config.get("min_confidence_entry", self.MIN_CONFIDENCE_ENTRY)
        
        self._logger.log("news_risk_gate_init", {
            "enabled": self._enabled,
            "severe_threshold": self._severe,
            "negative_threshold": self._negative,
            "cautious_threshold": self._cautious
        })
    
    def evaluate_entry(
        self,
        symbol: str,
        sentiment_score: float,
        confidence: float,
        is_bullish_trade: bool = True,
        cache_age_seconds: Optional[float] = None
    ) -> NewsGateResult:
        """
        Evaluate whether to allow entry based on news sentiment.
        
        Args:
            symbol: Trading symbol
            sentiment_score: Sentiment from -1.0 to +1.0
            confidence: Confidence from 0.0 to 1.0
            is_bullish_trade: True for long/call, False for short/put
            cache_age_seconds: Age of sentiment data
            
        Returns:
            NewsGateResult with action and reasoning
        """
        now = datetime.utcnow()
        
        if not self._enabled:
            return NewsGateResult(
                action=NewsAction.ALLOW,
                reason="news_gate_disabled",
                sentiment_score=sentiment_score,
                confidence=confidence,
                size_multiplier=1.0,
                symbol=symbol,
                evaluated_at=now
            )
        
        # Check cache staleness
        cache_stale = False
        if cache_age_seconds and cache_age_seconds > self.CACHE_STALE_SECONDS:
            cache_stale = True
        
        # Low confidence → proceed with caution
        if confidence < self._min_conf_entry:
            return NewsGateResult(
                action=NewsAction.ALLOW,
                reason=f"low_confidence_{confidence:.2f}_proceed_cautiously",
                sentiment_score=sentiment_score,
                confidence=confidence,
                size_multiplier=0.75,  # Slight reduction for uncertainty
                symbol=symbol,
                evaluated_at=now,
                cache_stale=cache_stale
            )
        
        # Evaluate sentiment
        if sentiment_score <= self._severe:
            return NewsGateResult(
                action=NewsAction.SKIP_ENTRY,
                reason=f"severe_negative_sentiment_{sentiment_score:.2f}",
                sentiment_score=sentiment_score,
                confidence=confidence,
                size_multiplier=0.0,
                symbol=symbol,
                evaluated_at=now,
                cache_stale=cache_stale
            )
        
        if sentiment_score <= self._negative:
            return NewsGateResult(
                action=NewsAction.SKIP_ENTRY,
                reason=f"negative_sentiment_{sentiment_score:.2f}",
                sentiment_score=sentiment_score,
                confidence=confidence,
                size_multiplier=0.0,
                symbol=symbol,
                evaluated_at=now,
                cache_stale=cache_stale
            )
        
        if sentiment_score <= self._cautious:
            return NewsGateResult(
                action=NewsAction.REDUCE_SIZE,
                reason=f"cautious_sentiment_{sentiment_score:.2f}",
                sentiment_score=sentiment_score,
                confidence=confidence,
                size_multiplier=self.CAUTIOUS_SIZE_MULT,
                symbol=symbol,
                evaluated_at=now,
                cache_stale=cache_stale
            )
        
        # Neutral or positive → allow
        return NewsGateResult(
            action=NewsAction.ALLOW,
            reason=f"sentiment_acceptable_{sentiment_score:.2f}",
            sentiment_score=sentiment_score,
            confidence=confidence,
            size_multiplier=1.0,
            symbol=symbol,
            evaluated_at=now,
            cache_stale=cache_stale
        )
    
    def evaluate_exit(
        self,
        symbol: str,
        sentiment_score: float,
        confidence: float,
        current_pnl_pct: float,
        cache_age_seconds: Optional[float] = None
    ) -> NewsGateResult:
        """
        Evaluate whether to force exit based on news sentiment.
        
        Args:
            symbol: Trading symbol
            sentiment_score: Sentiment from -1.0 to +1.0
            confidence: Confidence from 0.0 to 1.0
            current_pnl_pct: Current position P&L percentage
            cache_age_seconds: Age of sentiment data
            
        Returns:
            NewsGateResult with action and reasoning
        """
        now = datetime.utcnow()
        
        if not self._enabled:
            return NewsGateResult(
                action=NewsAction.ALLOW,
                reason="news_gate_disabled",
                sentiment_score=sentiment_score,
                confidence=confidence,
                size_multiplier=1.0,
                symbol=symbol,
                evaluated_at=now
            )
        
        # Check cache staleness
        cache_stale = False
        if cache_age_seconds and cache_age_seconds > self.CACHE_STALE_SECONDS:
            cache_stale = True
            # Don't force exit on stale data
            return NewsGateResult(
                action=NewsAction.ALLOW,
                reason="cache_stale_no_exit_action",
                sentiment_score=sentiment_score,
                confidence=confidence,
                size_multiplier=1.0,
                symbol=symbol,
                evaluated_at=now,
                cache_stale=True
            )
        
        # Low confidence → no action
        if confidence < self._min_conf_exit:
            return NewsGateResult(
                action=NewsAction.ALLOW,
                reason=f"low_confidence_{confidence:.2f}_no_exit_action",
                sentiment_score=sentiment_score,
                confidence=confidence,
                size_multiplier=1.0,
                symbol=symbol,
                evaluated_at=now,
                cache_stale=cache_stale
            )
        
        # Severe negative → force exit regardless of P&L
        if sentiment_score <= self._severe:
            return NewsGateResult(
                action=NewsAction.FORCE_EXIT,
                reason=f"severe_negative_{sentiment_score:.2f}_force_exit",
                sentiment_score=sentiment_score,
                confidence=confidence,
                size_multiplier=0.0,
                symbol=symbol,
                evaluated_at=now,
                cache_stale=cache_stale
            )
        
        # Negative + losing position → exit to cut losses
        if sentiment_score <= self._negative and current_pnl_pct < 0:
            return NewsGateResult(
                action=NewsAction.FORCE_EXIT,
                reason=f"negative_{sentiment_score:.2f}_losing_position_{current_pnl_pct:.1f}pct",
                sentiment_score=sentiment_score,
                confidence=confidence,
                size_multiplier=0.0,
                symbol=symbol,
                evaluated_at=now,
                cache_stale=cache_stale
            )
        
        # Otherwise allow position to continue
        return NewsGateResult(
            action=NewsAction.ALLOW,
            reason=f"sentiment_{sentiment_score:.2f}_position_continues",
            sentiment_score=sentiment_score,
            confidence=confidence,
            size_multiplier=1.0,
            symbol=symbol,
            evaluated_at=now,
            cache_stale=cache_stale
        )
    
    def log_decision(self, result: NewsGateResult, context: str):
        """Log gate decision for audit."""
        self._logger.log("news_risk_gate_decision", {
            "context": context,
            **result.to_dict()
        })


# Singleton
_news_risk_gate: Optional[NewsRiskGate] = None


def get_news_risk_gate() -> NewsRiskGate:
    """Get or create NewsRiskGate singleton."""
    global _news_risk_gate
    if _news_risk_gate is None:
        _news_risk_gate = NewsRiskGate()
    return _news_risk_gate
