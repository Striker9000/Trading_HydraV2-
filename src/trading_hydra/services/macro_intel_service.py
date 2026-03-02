"""
MacroIntelService - Fed and White House macro intelligence

This service provides:
1. Fed/FOMC news and sentiment tracking
2. White House policy and statement monitoring
3. Hawkish/dovish scoring
4. Regime modifier (NORMAL/CAUTION/STRESS) for risk management

Usage:
    from trading_hydra.services.macro_intel_service import get_macro_intel_service
    
    service = get_macro_intel_service()
    regime = service.get_current_regime_modifier()
"""

import os
import time
import json
import threading
from typing import Dict, Optional, Any, List
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from ..core.logging import get_logger
from ..core.config import load_bots_config


class RegimeModifier(Enum):
    """Market regime based on macro intelligence"""
    NORMAL = "NORMAL"      # Business as usual
    CAUTION = "CAUTION"    # Reduce risk exposure
    STRESS = "STRESS"      # Minimal exposure, defensive posture


class MacroEventType(Enum):
    """Types of macro events that can affect markets"""
    FED_RATE_DECISION = "fed_rate_decision"
    FED_SPEECH = "fed_speech"
    FOMC_MINUTES = "fomc_minutes"
    TARIFF_ANNOUNCEMENT = "tariff_announcement"
    TRADE_WAR_ESCALATION = "trade_war_escalation"
    TRADE_DEAL = "trade_deal"
    EXECUTIVE_ORDER = "executive_order"
    SANCTIONS = "sanctions"
    GEOPOLITICAL_TENSION = "geopolitical_tension"
    GOVERNMENT_SHUTDOWN = "government_shutdown"
    DEBT_CEILING = "debt_ceiling"
    REGULATION_CHANGE = "regulation_change"
    ELECTION_NEWS = "election_news"
    OTHER = "other"


@dataclass
class MacroEvent:
    """Individual macro event with impact assessment"""
    event_type: MacroEventType
    headline: str
    source: str
    impact_score: float  # -1.0 (bearish) to +1.0 (bullish)
    urgency: str  # "immediate", "short_term", "medium_term"
    affected_sectors: List[str]
    timestamp: str


@dataclass
class MacroIntelResult:
    """Result of macro intelligence analysis"""
    hawkish_dovish_score: float  # -1.0 (dovish) to +1.0 (hawkish)
    impact_probability: float  # 0.0 to 1.0
    regime_modifier: RegimeModifier
    affected_sectors: List[str]
    fed_sentiment: str  # "hawkish", "dovish", "neutral"
    white_house_sentiment: str
    reason_short: str
    last_fed_event: str
    last_wh_event: str
    timestamp: float = field(default_factory=time.time)
    tariff_alert: bool = False
    tariff_details: str = ""
    trade_war_status: str = "none"  # "none", "escalating", "de-escalating", "active"
    active_events: List[Dict[str, Any]] = field(default_factory=list)
    
    def age_seconds(self) -> float:
        return time.time() - self.timestamp
    
    def has_tariff_risk(self) -> bool:
        return self.tariff_alert or self.trade_war_status in ["escalating", "active"]
    
    def get_trade_policy_impact(self) -> float:
        """Returns impact score from trade policy events (-1 to +1)"""
        if self.trade_war_status == "escalating":
            return -0.7
        elif self.trade_war_status == "active":
            return -0.4
        elif self.trade_war_status == "de-escalating":
            return 0.3
        return 0.0


@dataclass
class MacroCacheEntry:
    """Cache entry for macro intel"""
    result: MacroIntelResult
    last_fetch_ts: float
    fetch_success: bool
    
    def is_stale(self, ttl_seconds: float) -> bool:
        return (time.time() - self.last_fetch_ts) > ttl_seconds


class MacroIntelService:
    """
    Service for tracking Fed and White House macro signals
    
    Provides regime modifiers that adjust risk posture across all bots.
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
        self._cache: Optional[MacroCacheEntry] = None
        self._cache_lock = threading.Lock()
        self._client = None
        self._simulation_mode = False
        
        self._load_config()
        self._init_client()
        
        self._initialized = True
        self._logger.info("[MacroIntel] Service initialized")
    
    def _load_config(self):
        """Load configuration from bots.yaml"""
        try:
            config = load_bots_config()
            macro_config = config.get("intelligence", {}).get("macro", {})
            
            self._enabled = macro_config.get("enabled", False)
            self._refresh_seconds = macro_config.get("refresh_seconds", 300)
            self._hawkish_caution_threshold = macro_config.get("hawkish_caution_threshold", 0.40)
            self._hawkish_stress_threshold = macro_config.get("hawkish_stress_threshold", 0.70)
            self._stress_size_multiplier = macro_config.get("stress_size_multiplier", 0.5)
            self._caution_size_multiplier = macro_config.get("caution_size_multiplier", 0.75)
            
            debug_config = config.get("intelligence", {}).get("debug", {})
            self._simulation_mode = debug_config.get("simulation_mode", False)
            
        except Exception as e:
            self._logger.warn(f"[MacroIntel] Config load failed: {e}")
            self._enabled = False
            self._refresh_seconds = 300
            self._hawkish_caution_threshold = 0.40
            self._hawkish_stress_threshold = 0.70
            self._stress_size_multiplier = 0.5
            self._caution_size_multiplier = 0.75
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
                self._logger.info("[MacroIntel] OpenAI client initialized")
            else:
                self._logger.warn("[MacroIntel] OpenAI env vars not set")
                
        except Exception as e:
            self._logger.error(f"[MacroIntel] Failed to init client: {e}")
    
    def is_enabled(self) -> bool:
        return self._enabled
    
    def get_macro_intel(self, force_refresh: bool = False) -> MacroIntelResult:
        """
        Get current macro intelligence
        
        Returns:
            MacroIntelResult with regime modifier
        """
        if not self._enabled:
            return self._get_neutral_result()
        
        # Check cache
        with self._cache_lock:
            if self._cache and not self._cache.is_stale(self._refresh_seconds) and not force_refresh:
                return self._cache.result
        
        # Fetch fresh data
        if self._simulation_mode:
            result = self._fetch_simulated()
        else:
            result = self._fetch_from_openai()
        
        # Cache result
        with self._cache_lock:
            self._cache = MacroCacheEntry(
                result=result,
                last_fetch_ts=time.time(),
                fetch_success=True
            )
        
        self._logger.log("macro_intel_update", {
            "regime": result.regime_modifier.value,
            "hawkish_dovish": round(result.hawkish_dovish_score, 2),
            "impact_prob": round(result.impact_probability, 2),
            "fed_sentiment": result.fed_sentiment,
            "wh_sentiment": result.white_house_sentiment
        })
        
        return result
    
    def get_current_regime_modifier(self) -> RegimeModifier:
        """Get just the regime modifier"""
        intel = self.get_macro_intel()
        return intel.regime_modifier
    
    def get_size_multiplier(self) -> float:
        """
        Get position size multiplier based on regime
        
        Returns:
            Multiplier <= 1.0 (1.0 = normal, 0.5 = stress)
        """
        regime = self.get_current_regime_modifier()
        
        if regime == RegimeModifier.STRESS:
            return self._stress_size_multiplier
        elif regime == RegimeModifier.CAUTION:
            return self._caution_size_multiplier
        else:
            return 1.0
    
    def has_tariff_alert(self) -> bool:
        """Check if there's an active tariff alert"""
        intel = self.get_macro_intel()
        return intel.tariff_alert
    
    def get_tariff_details(self) -> str:
        """Get current tariff alert details"""
        intel = self.get_macro_intel()
        return intel.tariff_details
    
    def get_trade_war_status(self) -> str:
        """Get current trade war status: none, escalating, de-escalating, active"""
        intel = self.get_macro_intel()
        return intel.trade_war_status
    
    def has_trade_policy_risk(self) -> bool:
        """Check if trade policy creates elevated risk"""
        intel = self.get_macro_intel()
        return intel.has_tariff_risk()
    
    def get_active_macro_events(self) -> List[Dict[str, Any]]:
        """Get list of active macro events with impact scores"""
        intel = self.get_macro_intel()
        return intel.active_events
    
    def should_reduce_exposure(self) -> tuple:
        """
        Check if macro conditions warrant reduced exposure
        
        Returns:
            (should_reduce: bool, reason: str, multiplier: float)
        """
        intel = self.get_macro_intel()
        
        if intel.regime_modifier == RegimeModifier.STRESS:
            reason = f"STRESS regime - {intel.reason_short[:80]}"
            if intel.tariff_alert:
                reason = f"TARIFF ALERT: {intel.tariff_details[:60]}"
            elif intel.trade_war_status == "escalating":
                reason = "Trade war escalating"
            return (True, reason, self._stress_size_multiplier)
        
        if intel.regime_modifier == RegimeModifier.CAUTION:
            reason = f"CAUTION regime - {intel.reason_short[:80]}"
            return (True, reason, self._caution_size_multiplier)
        
        return (False, "", 1.0)
    
    def get_sector_risk(self, sector: str) -> float:
        """
        Get risk level for a specific sector based on current macro events
        
        Returns:
            Risk multiplier (1.0 = normal, 2.0 = elevated, 0.5 = reduced)
        """
        intel = self.get_macro_intel()
        
        # Check if sector is in affected sectors
        affected = [s.lower() for s in intel.affected_sectors]
        sector_lower = sector.lower()
        
        if sector_lower in affected:
            if intel.regime_modifier == RegimeModifier.STRESS:
                return 2.0  # Double risk for affected sectors in stress
            elif intel.regime_modifier == RegimeModifier.CAUTION:
                return 1.5
        
        return 1.0
    
    def _get_neutral_result(self) -> MacroIntelResult:
        """Return neutral result when disabled"""
        return MacroIntelResult(
            hawkish_dovish_score=0.0,
            impact_probability=0.0,
            regime_modifier=RegimeModifier.NORMAL,
            affected_sectors=[],
            fed_sentiment="neutral",
            white_house_sentiment="neutral",
            reason_short="Macro intel disabled",
            last_fed_event="",
            last_wh_event=""
        )
    
    def _fetch_macro_headlines(self) -> List[str]:
        """
        Fetch macro headlines from multiple sources.
        Uses yfinance for general market news as primary source.
        Falls back to neutral if unavailable.
        """
        headlines = []
        
        try:
            import yfinance as yf
        except ImportError:
            self._logger.warn("[MacroIntel] yfinance not installed, returning empty headlines")
            return []
        
        try:
            # SPY for general market news, TLT for rates, GLD for risk-off
            macro_tickers = ["SPY", "^VIX", "TLT", "GLD", "DX-Y.NYB"]
            
            for ticker in macro_tickers[:3]:  # Limit to 3 to save time
                try:
                    t = yf.Ticker(ticker)
                    news = t.news or []
                    for n in news[:3]:
                        content = n.get('content', {}) if isinstance(n, dict) else {}
                        title = content.get('title', '') or n.get('title', '')
                        if title:
                            headlines.append(title)
                except Exception:
                    continue
            
            self._logger.info(f"[MacroIntel] Fetched {len(headlines)} macro headlines")
            
        except Exception as e:
            self._logger.warn(f"[MacroIntel] Failed to fetch headlines: {e}")
        
        return headlines[:15]  # Limit to 15 headlines
    
    def _has_macro_keywords(self, headlines: List[str]) -> bool:
        """Check if headlines contain macro-relevant keywords"""
        macro_keywords = [
            "fed", "fomc", "rate", "inflation", "powell", "tariff", "trade war",
            "china", "tariffs", "white house", "executive order", "sanctions",
            "treasury", "gdp", "jobs", "employment", "recession", "stimulus",
            "russia", "ukraine", "geopolitical", "oil", "opec", "bank"
        ]
        headlines_lower = " ".join(headlines).lower()
        return any(kw in headlines_lower for kw in macro_keywords)
    
    def _analyze_headlines_for_macro(self, headlines: List[str]) -> MacroIntelResult:
        """
        Analyze headlines using OpenAI to extract macro intel.
        This is more reliable than asking the model to search for news.
        """
        if not self._client or not headlines:
            return self._get_neutral_result()
        
        # Format headlines for analysis
        headlines_text = "\n".join([f"- {h}" for h in headlines])
        
        prompt = f"""Analyze these recent financial news headlines for macro/political market impact:

{headlines_text}

Based on these headlines, extract:
1. Federal Reserve sentiment and policy direction
2. Any tariff or trade policy news
3. White House/government policy impact
4. Geopolitical stress level

Return this EXACT JSON format:
{{
    "fed": {{
        "sentiment": "hawkish" or "dovish" or "neutral",
        "latest_event": "Brief description from headlines or empty",
        "hawkish_score": 0.0 to 1.0
    }},
    "white_house": {{
        "sentiment": "positive" or "negative" or "neutral",
        "latest_event": "Brief description or empty",
        "market_impact": "high" or "medium" or "low"
    }},
    "tariffs": {{
        "alert": true or false,
        "status": "none" or "new_announced" or "escalating" or "de-escalating" or "active",
        "details": "Brief description or empty",
        "affected_countries": [],
        "affected_products": [],
        "market_impact": "high" or "medium" or "low"
    }},
    "trade_war": {{
        "status": "none" or "escalating" or "de-escalating" or "active",
        "parties": [],
        "latest_development": ""
    }},
    "geopolitical": {{
        "stress_level": "low" or "medium" or "high",
        "events": [],
        "sanctions_news": null
    }},
    "active_events": [],
    "affected_sectors": [],
    "overall_assessment": "Brief 100 char summary"
}}

If no relevant macro news in headlines, return neutral values."""

        try:
            response = self._client.chat.completions.create(
                model="gpt-4o-mini",  # Use faster model for headline analysis
                messages=[{"role": "user", "content": prompt}],
                timeout=15,
                temperature=0.1,
                max_tokens=800
            )
            
            content = response.choices[0].message.content
            return self._parse_macro_response(content)
            
        except Exception as e:
            self._logger.error(f"[MacroIntel] Headline analysis failed: {e}")
            return self._get_neutral_result()
    
    def _fetch_from_openai(self) -> MacroIntelResult:
        """
        Fetch macro intel using headline analysis approach.
        Falls back to simulation if headlines unavailable.
        """
        if not self._client:
            return self._get_neutral_result()
        
        # Step 1: Fetch headlines from real sources
        headlines = self._fetch_macro_headlines()
        
        if headlines:
            # Check if headlines contain macro-relevant keywords
            if not self._has_macro_keywords(headlines):
                self._logger.info("[MacroIntel] No macro keywords in headlines, returning neutral")
                return self._get_neutral_result()
            
            # Step 2: Analyze headlines with OpenAI
            return self._analyze_headlines_for_macro(headlines)
        
        # Fallback: Return neutral if no headlines available
        self._logger.warn("[MacroIntel] No headlines available, returning neutral")
        return self._get_neutral_result()
    
    def _parse_macro_response(self, content: str) -> MacroIntelResult:
        """Parse macro intel response with tariff/trade policy support"""
        try:
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            data = json.loads(content.strip())
            
            fed = data.get("fed", {})
            wh = data.get("white_house", {})
            geo = data.get("geopolitical", {})
            tariffs = data.get("tariffs", {})
            trade_war = data.get("trade_war", {})
            active_events = data.get("active_events", [])
            
            # Calculate hawkish/dovish score
            hawkish_score = float(fed.get("hawkish_score", 0.5))
            
            # Extract tariff info
            tariff_alert = tariffs.get("alert", False)
            tariff_status = tariffs.get("status", "none")
            tariff_details = tariffs.get("details", "")
            tariff_impact = tariffs.get("market_impact", "low")
            
            # Extract trade war status
            trade_war_status = trade_war.get("status", "none")
            
            # Determine regime modifier with tariff/trade policy weighting
            geo_stress = geo.get("stress_level", "low")
            wh_impact = wh.get("market_impact", "low")
            
            stress_points = 0
            
            # Fed policy stress
            if hawkish_score > self._hawkish_stress_threshold:
                stress_points += 2
            elif hawkish_score > self._hawkish_caution_threshold:
                stress_points += 1
            
            # Geopolitical stress
            if geo_stress == "high":
                stress_points += 2
            elif geo_stress == "medium":
                stress_points += 1
            
            # White House negative impact
            if wh_impact == "high" and wh.get("sentiment") == "negative":
                stress_points += 1
            
            # TARIFF/TRADE POLICY STRESS (Unified scoring - avoid double counting)
            trade_policy_points = 0
            
            # Tariff-specific scoring
            if tariff_alert:
                if tariff_impact == "high":
                    trade_policy_points = 3  # Major tariff announcement
                    self._logger.warn(f"[MacroIntel] HIGH TARIFF ALERT: {tariff_details[:100]}")
                elif tariff_impact == "medium":
                    trade_policy_points = 2
                else:
                    trade_policy_points = 1
            elif tariff_status == "escalating":
                trade_policy_points = max(trade_policy_points, 2)
            elif tariff_status == "active":
                trade_policy_points = max(trade_policy_points, 1)
            elif tariff_status == "new_announced":
                trade_policy_points = max(trade_policy_points, 2)
            
            # Trade war - only add if worse than tariff score (avoid double counting)
            if trade_war_status == "escalating":
                trade_policy_points = max(trade_policy_points, 3)
            elif trade_war_status == "active":
                trade_policy_points = max(trade_policy_points, 2)
            
            # Cap trade policy contribution to prevent over-weighting
            stress_points += min(trade_policy_points, 3)
            
            # Determine regime
            if stress_points >= 4:
                regime = RegimeModifier.STRESS
            elif stress_points >= 2:
                regime = RegimeModifier.CAUTION
            else:
                regime = RegimeModifier.NORMAL
            
            # Calculate impact probability
            impact_prob = min(1.0, stress_points * 0.15 + 0.2)
            
            # Log tariff/trade alerts
            if tariff_alert or trade_war_status != "none":
                self._logger.log("macro_trade_policy_alert", {
                    "tariff_alert": tariff_alert,
                    "tariff_status": tariff_status,
                    "trade_war_status": trade_war_status,
                    "details": tariff_details[:100] if tariff_details else "",
                    "regime": regime.value
                })
            
            return MacroIntelResult(
                hawkish_dovish_score=hawkish_score * 2 - 1,  # Convert to -1 to +1
                impact_probability=impact_prob,
                regime_modifier=regime,
                affected_sectors=data.get("affected_sectors", []),
                fed_sentiment=fed.get("sentiment", "neutral"),
                white_house_sentiment=wh.get("sentiment", "neutral"),
                reason_short=data.get("overall_assessment", "")[:150],
                last_fed_event=fed.get("latest_event", ""),
                last_wh_event=wh.get("latest_event", ""),
                tariff_alert=tariff_alert,
                tariff_details=tariff_details,
                trade_war_status=trade_war_status,
                active_events=active_events
            )
            
        except Exception as e:
            self._logger.warn(f"[MacroIntel] Parse error: {e}")
            return self._get_neutral_result()
    
    def _fetch_simulated(self) -> MacroIntelResult:
        """Generate simulated macro intel for testing"""
        import random
        
        # Use current time as seed for some variation
        random.seed(int(time.time() / 3600))  # Changes hourly
        
        hawkish = random.uniform(-0.5, 0.8)
        
        if hawkish > 0.6:
            regime = RegimeModifier.STRESS
            fed_sent = "hawkish"
        elif hawkish > 0.3:
            regime = RegimeModifier.CAUTION
            fed_sent = "hawkish" if hawkish > 0.45 else "neutral"
        else:
            regime = RegimeModifier.NORMAL
            fed_sent = "dovish" if hawkish < 0 else "neutral"
        
        return MacroIntelResult(
            hawkish_dovish_score=hawkish,
            impact_probability=random.uniform(0.2, 0.7),
            regime_modifier=regime,
            affected_sectors=random.sample(["tech", "financials", "energy", "utilities", "reits"], 2),
            fed_sentiment=fed_sent,
            white_house_sentiment=random.choice(["positive", "neutral", "negative"]),
            reason_short="Simulated macro intel for testing",
            last_fed_event="Simulated Fed event",
            last_wh_event="Simulated WH event"
        )
    
    def clear_cache(self):
        """Clear cached data"""
        with self._cache_lock:
            self._cache = None
    
    def set_simulation_mode(self, enabled: bool):
        """Enable/disable simulation mode"""
        self._simulation_mode = enabled


# Singleton accessor
_macro_intel_instance: Optional[MacroIntelService] = None
_macro_intel_lock = threading.Lock()


def get_macro_intel_service() -> MacroIntelService:
    """Get the singleton MacroIntelService instance"""
    global _macro_intel_instance
    with _macro_intel_lock:
        if _macro_intel_instance is None:
            _macro_intel_instance = MacroIntelService()
        return _macro_intel_instance


def reset_macro_intel_service():
    """Reset singleton for testing"""
    global _macro_intel_instance
    with _macro_intel_lock:
        if _macro_intel_instance:
            _macro_intel_instance.clear_cache()
        _macro_intel_instance = None
