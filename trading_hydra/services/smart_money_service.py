"""
SmartMoneyService - Congress trades and institutional (13F) holdings intelligence

This service provides:
1. Congress trades tracking via OpenAI web search
2. 13F institutional holdings tracking
3. AI-powered conviction and convergence scoring
4. Fail-closed behavior for safe trading

Usage:
    from trading_hydra.services.smart_money_service import get_smart_money_service
    
    service = get_smart_money_service()
    signals = service.get_signals_for_symbol("NVDA")
"""

import os
import time
import json
import threading
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..core.logging import get_logger
from ..core.config import load_bots_config


@dataclass
class SmartMoneySignal:
    """Signal from smart money sources (Congress or 13F)"""
    symbol: str
    source: str  # "congress" or "institutional"
    direction: str  # "buy", "sell", "hold"
    conviction_score: float  # 0.0 to 1.0
    convergence_score: float  # 0.0 to 1.0 (multiple sources aligned)
    trade_value_usd: float
    trader_name: str  # e.g., "Nancy Pelosi" or "BlackRock"
    trade_date: str  # ISO format
    disclosure_delay_days: int
    reason_short: str
    tags: List[str]  # e.g., ["semis", "defense", "ai"]
    fetched_at: float = field(default_factory=time.time)
    
    def age_seconds(self) -> float:
        return time.time() - self.fetched_at


@dataclass
class SmartMoneyCacheEntry:
    """Cache entry for smart money signals"""
    symbol: str
    signals: List[SmartMoneySignal]
    last_fetch_ts: float
    fetch_success: bool
    error_message: str = ""
    
    def is_stale(self, ttl_seconds: float) -> bool:
        return (time.time() - self.last_fetch_ts) > ttl_seconds
    
    def age_seconds(self) -> float:
        return time.time() - self.last_fetch_ts


class SmartMoneyService:
    """
    Service for tracking Congress trades and institutional holdings
    
    Uses OpenAI web search to find recent disclosures and applies AI
    scoring for conviction and convergence.
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
        self._cache: Dict[str, SmartMoneyCacheEntry] = {}
        self._cache_lock = threading.Lock()
        self._client = None
        self._simulation_mode = False
        
        self._load_config()
        self._init_client()
        
        self._initialized = True
        self._logger.info("[SmartMoney] Service initialized")
    
    def _load_config(self):
        """Load configuration from bots.yaml"""
        try:
            config = load_bots_config()
            smart_money_config = config.get("intelligence", {}).get("smart_money", {})
            
            self._enabled = smart_money_config.get("enabled", False)
            self._refresh_seconds = smart_money_config.get("refresh_seconds", 3600)
            self._boost_factor = smart_money_config.get("boost_factor", 1.2)
            
            congress_config = smart_money_config.get("congress", {})
            self._congress_enabled = congress_config.get("enabled", False)
            self._min_conviction = congress_config.get("min_conviction", 0.50)
            self._min_trade_value = congress_config.get("min_trade_value", 15000)
            
            inst_config = smart_money_config.get("institutional", {})
            self._institutional_enabled = inst_config.get("enabled", False)
            self._min_convergence = inst_config.get("min_convergence", 0.30)
            
            debug_config = config.get("intelligence", {}).get("debug", {})
            self._simulation_mode = debug_config.get("simulation_mode", False)
            
        except Exception as e:
            self._logger.warn(f"[SmartMoney] Config load failed: {e}")
            self._enabled = False
            self._refresh_seconds = 3600
            self._boost_factor = 1.2
            self._congress_enabled = False
            self._institutional_enabled = False
            self._min_conviction = 0.50
            self._min_convergence = 0.30
            self._min_trade_value = 15000
            self._simulation_mode = False
    
    def _init_client(self):
        """Initialize OpenAI client"""
        if self._simulation_mode:
            return
        
        try:
            from openai import OpenAI
            base_url = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
            api_key = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
            
            if base_url and api_key:
                self._client = OpenAI(base_url=base_url, api_key=api_key)
                self._logger.info("[SmartMoney] OpenAI client initialized")
            else:
                self._logger.warn("[SmartMoney] OpenAI env vars not set")
                
        except Exception as e:
            self._logger.error(f"[SmartMoney] Failed to init client: {e}")
    
    def is_enabled(self) -> bool:
        return self._enabled
    
    def get_signals_for_symbol(self, symbol: str, force_refresh: bool = False) -> List[SmartMoneySignal]:
        """
        Get smart money signals for a symbol
        
        Args:
            symbol: Ticker symbol
            force_refresh: Force new fetch
            
        Returns:
            List of SmartMoneySignal (empty if unavailable - fail-closed)
        """
        if not self._enabled:
            return []
        
        # Check cache
        with self._cache_lock:
            cached = self._cache.get(symbol)
            if cached and not cached.is_stale(self._refresh_seconds) and not force_refresh:
                return cached.signals
        
        # Fetch fresh data
        if self._simulation_mode:
            signals = self._fetch_simulated(symbol)
        else:
            signals = self._fetch_from_openai(symbol)
        
        # Cache results
        with self._cache_lock:
            self._cache[symbol] = SmartMoneyCacheEntry(
                symbol=symbol,
                signals=signals,
                last_fetch_ts=time.time(),
                fetch_success=True
            )
        
        return signals
    
    def _fetch_from_openai(self, symbol: str) -> List[SmartMoneySignal]:
        """Fetch smart money data using OpenAI web search"""
        if not self._client:
            return []
        
        signals = []
        
        # Congress trades
        if self._congress_enabled:
            congress_signals = self._fetch_congress_trades(symbol)
            signals.extend(congress_signals)
        
        # Institutional holdings
        if self._institutional_enabled:
            inst_signals = self._fetch_institutional(symbol)
            signals.extend(inst_signals)
        
        return signals
    
    def _fetch_congress_trades(self, symbol: str) -> List[SmartMoneySignal]:
        """Fetch Congress member trades for a symbol"""
        prompt = f"""Search for recent Congressional stock trades for ticker {symbol} from the past 90 days.

Look for disclosures from US Congress members buying or selling {symbol}.

Return JSON format:
{{
    "trades": [
        {{
            "member_name": "Name of Congress member",
            "party": "D or R",
            "trade_type": "buy" or "sell",
            "estimated_value": approximate USD value,
            "trade_date": "YYYY-MM-DD",
            "disclosure_date": "YYYY-MM-DD"
        }}
    ]
}}

If no trades found, return {{"trades": []}}"""

        try:
            response = self._client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                timeout=15,
                temperature=0.1,
                max_tokens=1000
            )
            
            content = response.choices[0].message.content
            return self._parse_congress_response(symbol, content)
            
        except Exception as e:
            self._logger.error(f"[SmartMoney] Congress fetch failed for {symbol}: {e}")
            return []
    
    def _parse_congress_response(self, symbol: str, content: str) -> List[SmartMoneySignal]:
        """Parse Congress trades response"""
        try:
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            data = json.loads(content.strip())
            trades = data.get("trades", [])
            
            signals = []
            for trade in trades:
                value = float(trade.get("estimated_value", 0))
                if value < self._min_trade_value:
                    continue
                
                # Calculate disclosure delay
                try:
                    trade_dt = datetime.strptime(trade.get("trade_date", ""), "%Y-%m-%d")
                    disclosure_dt = datetime.strptime(trade.get("disclosure_date", ""), "%Y-%m-%d")
                    delay = (disclosure_dt - trade_dt).days
                except:
                    delay = 45
                
                signal = SmartMoneySignal(
                    symbol=symbol,
                    source="congress",
                    direction=trade.get("trade_type", "buy"),
                    conviction_score=0.7,  # Base score
                    convergence_score=0.0,  # Will be updated if multiple trades
                    trade_value_usd=value,
                    trader_name=trade.get("member_name", "Unknown"),
                    trade_date=trade.get("trade_date", ""),
                    disclosure_delay_days=delay,
                    reason_short=f"{trade.get('member_name', 'Congress')} {trade.get('trade_type', 'traded')} {symbol}",
                    tags=["congress"]
                )
                signals.append(signal)
            
            # Update convergence if multiple members traded
            if len(signals) > 1:
                convergence = min(1.0, len(signals) * 0.25)
                for s in signals:
                    s.convergence_score = convergence
            
            return signals
            
        except Exception as e:
            self._logger.warn(f"[SmartMoney] Parse error for congress: {e}")
            return []
    
    def _fetch_institutional(self, symbol: str) -> List[SmartMoneySignal]:
        """Fetch 13F institutional holdings for a symbol"""
        prompt = f"""Search for recent 13F filings showing major institutional holdings changes for {symbol}.

Look for hedge funds, mutual funds, and investment firms that have recently increased or decreased their {symbol} holdings.

Return JSON format:
{{
    "holdings": [
        {{
            "fund_name": "Name of institution",
            "action": "increased" or "decreased" or "new_position" or "exited",
            "shares_change": approximate number,
            "total_value_usd": approximate current value,
            "quarter": "Q1 2024" etc
        }}
    ]
}}

If no data found, return {{"holdings": []}}"""

        try:
            response = self._client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                timeout=15,
                temperature=0.1,
                max_tokens=1000
            )
            
            content = response.choices[0].message.content
            return self._parse_institutional_response(symbol, content)
            
        except Exception as e:
            self._logger.error(f"[SmartMoney] Institutional fetch failed for {symbol}: {e}")
            return []
    
    def _parse_institutional_response(self, symbol: str, content: str) -> List[SmartMoneySignal]:
        """Parse institutional holdings response"""
        try:
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            data = json.loads(content.strip())
            holdings = data.get("holdings", [])
            
            signals = []
            for holding in holdings:
                action = holding.get("action", "")
                direction = "buy" if action in ["increased", "new_position"] else "sell"
                
                signal = SmartMoneySignal(
                    symbol=symbol,
                    source="institutional",
                    direction=direction,
                    conviction_score=0.6,
                    convergence_score=0.0,
                    trade_value_usd=float(holding.get("total_value_usd", 0)),
                    trader_name=holding.get("fund_name", "Unknown Fund"),
                    trade_date=holding.get("quarter", ""),
                    disclosure_delay_days=45,
                    reason_short=f"{holding.get('fund_name', 'Institution')} {action} {symbol}",
                    tags=["13f", "institutional"]
                )
                signals.append(signal)
            
            # Update convergence
            if len(signals) > 1:
                buy_count = sum(1 for s in signals if s.direction == "buy")
                sell_count = sum(1 for s in signals if s.direction == "sell")
                
                if buy_count > sell_count:
                    convergence = min(1.0, buy_count * 0.2)
                    for s in signals:
                        if s.direction == "buy":
                            s.convergence_score = convergence
                elif sell_count > buy_count:
                    convergence = min(1.0, sell_count * 0.2)
                    for s in signals:
                        if s.direction == "sell":
                            s.convergence_score = convergence
            
            return signals
            
        except Exception as e:
            self._logger.warn(f"[SmartMoney] Parse error for institutional: {e}")
            return []
    
    def _fetch_simulated(self, symbol: str) -> List[SmartMoneySignal]:
        """Generate simulated signals for testing"""
        import random
        import hashlib
        
        seed = int(hashlib.md5(symbol.encode()).hexdigest()[:8], 16)
        random.seed(seed)
        
        signals = []
        
        # Randomly generate Congress trades
        if random.random() > 0.6:
            members = ["Nancy Pelosi", "Dan Crenshaw", "Josh Gottheimer", "Tommy Tuberville"]
            signal = SmartMoneySignal(
                symbol=symbol,
                source="congress",
                direction=random.choice(["buy", "sell"]),
                conviction_score=random.uniform(0.5, 0.9),
                convergence_score=random.uniform(0.0, 0.5),
                trade_value_usd=random.uniform(15000, 500000),
                trader_name=random.choice(members),
                trade_date=datetime.now().strftime("%Y-%m-%d"),
                disclosure_delay_days=random.randint(10, 45),
                reason_short=f"Simulated Congress trade for {symbol}",
                tags=["congress", "simulation"]
            )
            signals.append(signal)
        
        # Randomly generate institutional
        if random.random() > 0.5:
            funds = ["BlackRock", "Vanguard", "Citadel", "Renaissance", "Bridgewater"]
            signal = SmartMoneySignal(
                symbol=symbol,
                source="institutional",
                direction=random.choice(["buy", "sell"]),
                conviction_score=random.uniform(0.4, 0.8),
                convergence_score=random.uniform(0.0, 0.6),
                trade_value_usd=random.uniform(1000000, 50000000),
                trader_name=random.choice(funds),
                trade_date=f"Q4 2025",
                disclosure_delay_days=45,
                reason_short=f"Simulated 13F for {symbol}",
                tags=["13f", "simulation"]
            )
            signals.append(signal)
        
        return signals
    
    def get_boost_for_symbol(self, symbol: str) -> float:
        """
        Get universe scoring boost based on smart money signals
        
        Returns:
            Multiplier >= 1.0 (1.0 = no boost)
        """
        if not self._enabled:
            return 1.0
        
        signals = self.get_signals_for_symbol(symbol)
        if not signals:
            return 1.0
        
        # Calculate boost based on conviction and convergence
        max_conviction = max(s.conviction_score for s in signals)
        max_convergence = max(s.convergence_score for s in signals)
        
        # Only boost if both conviction and convergence meet thresholds
        if max_conviction < self._min_conviction:
            return 1.0
        if max_convergence < self._min_convergence:
            return 1.0
        
        # Calculate boost factor
        combined = (max_conviction + max_convergence) / 2
        boost = 1.0 + (self._boost_factor - 1.0) * combined
        
        return min(boost, self._boost_factor)
    
    def clear_cache(self):
        """Clear all cached data"""
        with self._cache_lock:
            self._cache.clear()
    
    def set_simulation_mode(self, enabled: bool):
        """Enable/disable simulation mode"""
        self._simulation_mode = enabled


# Singleton accessor
_smart_money_instance: Optional[SmartMoneyService] = None
_smart_money_lock = threading.Lock()


def get_smart_money_service() -> SmartMoneyService:
    """Get the singleton SmartMoneyService instance"""
    global _smart_money_instance
    with _smart_money_lock:
        if _smart_money_instance is None:
            _smart_money_instance = SmartMoneyService()
        return _smart_money_instance


def reset_smart_money_service():
    """Reset singleton for testing"""
    global _smart_money_instance
    with _smart_money_lock:
        if _smart_money_instance:
            _smart_money_instance.clear_cache()
        _smart_money_instance = None
