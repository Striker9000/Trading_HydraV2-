"""
=============================================================================
AI Strategy Advisor - OpenAI-powered trade reasoning and config optimization
=============================================================================

Uses OpenAI to provide:
1. Trade entry/exit reasoning
2. Config optimization recommendations
3. Market condition analysis
4. Risk assessment

Philosophy:
- AI augments human judgment, doesn't replace it
- All recommendations come with confidence and reasoning
- Fail-closed: if AI unavailable, provide conservative fallback
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any
import os
import json
import time
import threading

from ..core.logging import get_logger
from ..core.config import load_bots_config, load_settings
from ..risk.circuit_breaker import get_circuit_registry


@dataclass
class TradeReasoning:
    """AI-generated reasoning for a trade decision."""
    symbol: str
    decision: str  # enter_long, enter_short, hold, exit
    reasoning: str
    confidence: float
    key_factors: List[str]
    risks: List[str]
    suggested_size_pct: float
    generated_at: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "decision": self.decision,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "key_factors": self.key_factors,
            "risks": self.risks,
            "suggested_size_pct": self.suggested_size_pct,
            "generated_at": self.generated_at
        }


@dataclass
class ConfigAdvice:
    """AI-generated config optimization advice."""
    bot_id: str
    current_performance: Dict[str, Any]
    recommendations: List[Dict[str, Any]]
    priority_changes: List[str]
    expected_improvement: str
    confidence: float
    generated_at: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "bot_id": self.bot_id,
            "current_performance": self.current_performance,
            "recommendations": self.recommendations,
            "priority_changes": self.priority_changes,
            "expected_improvement": self.expected_improvement,
            "confidence": self.confidence,
            "generated_at": self.generated_at
        }


class AIStrategyAdvisor:
    """
    OpenAI-powered strategy advisor for trading decisions.
    
    Uses Replit AI Integration for OpenAI - no API key needed.
    """
    
    MODEL = "gpt-4o-mini"  # Fast and cost-effective
    MAX_TOKENS = 1000
    TIMEOUT_SECONDS = 15
    
    CACHE_TTL_SECONDS = 300  # 5 minutes for trade reasoning
    
    def __init__(self):
        self._logger = get_logger()
        self._client = None
        self._lock = threading.Lock()
        self._reasoning_cache: Dict[str, TradeReasoning] = {}
        
        self._init_client()
        
        self._logger.log("ai_strategy_advisor_init", {
            "model": self.MODEL,
            "timeout": self.TIMEOUT_SECONDS
        })
    
    def _init_client(self) -> None:
        """Initialize OpenAI client using Replit AI Integration."""
        try:
            from openai import OpenAI
            
            base_url = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
            api_key = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
            
            if not base_url or not api_key:
                self._logger.warn("AI Strategy Advisor: OpenAI credentials not found")
                return
            
            self._client = OpenAI(base_url=base_url, api_key=api_key)
            
        except Exception as e:
            self._logger.error(f"Failed to initialize OpenAI client: {e}")
    
    def is_available(self) -> bool:
        """Check if AI advisor is available."""
        if not self._client:
            return False
        
        registry = get_circuit_registry()
        return registry.is_available("openai_analysis")
    
    def get_trade_reasoning(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        sentiment_data: Optional[Dict[str, Any]] = None,
        position: Optional[Dict[str, Any]] = None
    ) -> TradeReasoning:
        """
        Get AI reasoning for a trade decision.
        
        Args:
            symbol: Ticker symbol
            market_data: Current price, volume, technicals
            sentiment_data: News/social sentiment (optional)
            position: Current position if any (optional)
        
        Returns:
            TradeReasoning with decision and explanation
        """
        cache_key = f"{symbol}:{hash(json.dumps(market_data, sort_keys=True, default=str))}"
        
        with self._lock:
            if cache_key in self._reasoning_cache:
                cached = self._reasoning_cache[cache_key]
                if time.time() - cached.generated_at < self.CACHE_TTL_SECONDS:
                    return cached
        
        if not self.is_available():
            return self._fallback_reasoning(symbol, market_data)
        
        try:
            reasoning = self._call_openai_for_reasoning(symbol, market_data, sentiment_data, position)
            
            with self._lock:
                self._reasoning_cache[cache_key] = reasoning
            
            registry = get_circuit_registry()
            registry.record_success("openai_analysis")
            
            return reasoning
            
        except Exception as e:
            self._logger.error(f"AI reasoning failed for {symbol}: {e}")
            
            registry = get_circuit_registry()
            registry.record_failure("openai_analysis", error=str(e))
            
            return self._fallback_reasoning(symbol, market_data)
    
    def _call_openai_for_reasoning(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        sentiment_data: Optional[Dict[str, Any]],
        position: Optional[Dict[str, Any]]
    ) -> TradeReasoning:
        """Call OpenAI for trade reasoning."""
        context = f"""You are a professional trading analyst. Analyze this opportunity and provide a trading recommendation.

Symbol: {symbol}

Market Data:
- Current Price: ${market_data.get('close', 'N/A')}
- Change: {market_data.get('change_pct', 'N/A')}%
- Volume: {market_data.get('volume', 'N/A')}
- RSI: {market_data.get('rsi', 'N/A')}
- 20-day SMA: ${market_data.get('sma_20', 'N/A')}
- VIX: {market_data.get('vix', 'N/A')}

"""
        
        if sentiment_data:
            context += f"""Sentiment:
- News Sentiment: {sentiment_data.get('news_sentiment', 'N/A')}
- Social Sentiment: {sentiment_data.get('social_sentiment', 'N/A')}
- Key Headlines: {', '.join(sentiment_data.get('headlines', [])[:3])}

"""
        
        if position:
            context += f"""Current Position:
- Side: {position.get('side', 'N/A')}
- Entry Price: ${position.get('entry_price', 'N/A')}
- Current P&L: {position.get('pnl_pct', 'N/A')}%
- Hold Time: {position.get('hold_minutes', 'N/A')} minutes

"""
        
        context += """Provide a JSON response with:
{
    "decision": "enter_long" | "enter_short" | "hold" | "exit",
    "reasoning": "2-3 sentence explanation",
    "confidence": 0.0-1.0,
    "key_factors": ["factor1", "factor2", "factor3"],
    "risks": ["risk1", "risk2"],
    "suggested_size_pct": 0.5-2.0
}

Be conservative. Only recommend entries with clear setups. Consider risk first."""
        
        response = self._client.chat.completions.create(
            model=self.MODEL,
            messages=[{"role": "user", "content": context}],
            max_tokens=self.MAX_TOKENS,
            timeout=self.TIMEOUT_SECONDS
        )
        
        text = response.choices[0].message.content.strip()
        
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        
        data = json.loads(text)
        
        return TradeReasoning(
            symbol=symbol,
            decision=data.get("decision", "hold"),
            reasoning=data.get("reasoning", "Unable to determine"),
            confidence=float(data.get("confidence", 0.5)),
            key_factors=data.get("key_factors", []),
            risks=data.get("risks", []),
            suggested_size_pct=float(data.get("suggested_size_pct", 1.0))
        )
    
    def _fallback_reasoning(self, symbol: str, market_data: Dict[str, Any]) -> TradeReasoning:
        """Provide conservative fallback when AI unavailable."""
        rsi = market_data.get("rsi", 50)
        change_pct = market_data.get("change_pct", 0)
        
        if rsi < 30 and change_pct < -2:
            decision = "enter_long"
            reasoning = "RSI oversold with significant drawdown - potential bounce"
        elif rsi > 70 and change_pct > 2:
            decision = "enter_short"
            reasoning = "RSI overbought with extended move - potential pullback"
        else:
            decision = "hold"
            reasoning = "No clear setup - waiting for better entry"
        
        return TradeReasoning(
            symbol=symbol,
            decision=decision,
            reasoning=f"[Fallback] {reasoning}",
            confidence=0.3,
            key_factors=["rules-based fallback"],
            risks=["AI unavailable - reduced confidence"],
            suggested_size_pct=0.5
        )
    
    def get_config_advice(
        self,
        bot_id: str,
        performance_stats: Dict[str, Any],
        current_config: Dict[str, Any]
    ) -> ConfigAdvice:
        """
        Get AI advice for config optimization.
        
        Args:
            bot_id: Bot identifier
            performance_stats: Win rate, P&L, Sharpe, etc.
            current_config: Current bot configuration
        
        Returns:
            ConfigAdvice with recommendations
        """
        if not self.is_available():
            return self._fallback_config_advice(bot_id, performance_stats)
        
        try:
            return self._call_openai_for_config(bot_id, performance_stats, current_config)
        except Exception as e:
            self._logger.error(f"AI config advice failed for {bot_id}: {e}")
            return self._fallback_config_advice(bot_id, performance_stats)
    
    def _call_openai_for_config(
        self,
        bot_id: str,
        performance_stats: Dict[str, Any],
        current_config: Dict[str, Any]
    ) -> ConfigAdvice:
        """Call OpenAI for config optimization advice."""
        context = f"""You are a trading systems engineer. Analyze this bot's performance and suggest config optimizations.

Bot: {bot_id}

Performance (last 30 days):
- Win Rate: {performance_stats.get('win_rate', 'N/A')}
- Total P&L: ${performance_stats.get('total_pnl_usd', 'N/A')}
- Trade Count: {performance_stats.get('trade_count', 'N/A')}
- Avg Win: ${performance_stats.get('avg_win_usd', 'N/A')}
- Avg Loss: ${performance_stats.get('avg_loss_usd', 'N/A')}
- Profit Factor: {performance_stats.get('profit_factor', 'N/A')}

Current Config:
{json.dumps(current_config, indent=2, default=str)[:2000]}

Provide a JSON response with optimization recommendations:
{{
    "recommendations": [
        {{"param": "exits.stop_loss_pct", "current": 0.5, "suggested": 0.8, "reason": "..."}},
        {{"param": "exits.take_profit_pct", "current": 1.0, "suggested": 1.5, "reason": "..."}}
    ],
    "priority_changes": ["First change to make", "Second change"],
    "expected_improvement": "Description of expected impact",
    "confidence": 0.0-1.0
}}

Focus on risk-adjusted returns. Suggest conservative changes."""
        
        response = self._client.chat.completions.create(
            model=self.MODEL,
            messages=[{"role": "user", "content": context}],
            max_tokens=self.MAX_TOKENS,
            timeout=self.TIMEOUT_SECONDS
        )
        
        text = response.choices[0].message.content.strip()
        
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        
        data = json.loads(text)
        
        return ConfigAdvice(
            bot_id=bot_id,
            current_performance=performance_stats,
            recommendations=data.get("recommendations", []),
            priority_changes=data.get("priority_changes", []),
            expected_improvement=data.get("expected_improvement", "Unknown"),
            confidence=float(data.get("confidence", 0.5))
        )
    
    def _fallback_config_advice(
        self,
        bot_id: str,
        performance_stats: Dict[str, Any]
    ) -> ConfigAdvice:
        """Provide fallback config advice when AI unavailable."""
        win_rate = performance_stats.get("win_rate", 0.5)
        profit_factor = performance_stats.get("profit_factor", 1.0)
        
        recommendations = []
        
        if win_rate < 0.4:
            recommendations.append({
                "param": "exits.take_profit_pct",
                "suggestion": "reduce by 20%",
                "reason": "Low win rate suggests targets too aggressive"
            })
        
        if profit_factor < 1.0:
            recommendations.append({
                "param": "exits.stop_loss_pct",
                "suggestion": "reduce by 15%",
                "reason": "Negative profit factor - tighten stops"
            })
        
        return ConfigAdvice(
            bot_id=bot_id,
            current_performance=performance_stats,
            recommendations=recommendations,
            priority_changes=["[Fallback] Review stop-loss settings"],
            expected_improvement="Unknown - AI unavailable",
            confidence=0.3
        )
    
    def analyze_market_conditions(self, indicators: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get AI analysis of current market conditions.
        
        Args:
            indicators: VIX, sector performance, economic data
        
        Returns:
            Market regime assessment and recommendations
        """
        if not self.is_available():
            return {
                "regime": "unknown",
                "recommendation": "Proceed with caution - AI unavailable",
                "confidence": 0.3
            }
        
        try:
            context = f"""Analyze current market conditions:

VIX: {indicators.get('vix', 'N/A')}
S&P 500 Change: {indicators.get('spy_change', 'N/A')}%
10Y Treasury: {indicators.get('tlt_change', 'N/A')}%
Dollar Index: {indicators.get('dxy_change', 'N/A')}%

Provide a JSON response:
{{
    "regime": "risk_on" | "risk_off" | "neutral" | "volatile",
    "recommendation": "Brief trading recommendation",
    "confidence": 0.0-1.0,
    "key_factors": ["factor1", "factor2"]
}}"""
            
            response = self._client.chat.completions.create(
                model=self.MODEL,
                messages=[{"role": "user", "content": context}],
                max_tokens=500,
                timeout=10
            )
            
            text = response.choices[0].message.content.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            
            return json.loads(text)
            
        except Exception as e:
            self._logger.error(f"Market analysis failed: {e}")
            return {
                "regime": "unknown",
                "recommendation": "Analysis failed - use technical indicators",
                "confidence": 0.0
            }


_ai_advisor: Optional[AIStrategyAdvisor] = None


def get_ai_advisor() -> AIStrategyAdvisor:
    """Get or create AIStrategyAdvisor singleton."""
    global _ai_advisor
    if _ai_advisor is None:
        _ai_advisor = AIStrategyAdvisor()
    return _ai_advisor
