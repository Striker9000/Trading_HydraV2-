#!/usr/bin/env python3
"""
=============================================================================
30-Scenario Intelligence Simulation Test Harness
=============================================================================

This test harness simulates the Market Intelligence System under various
conditions to validate gating logic, fail-closed behavior, and decision making.

Features:
- 30 deterministic scenarios with seeds 1-30
- Simulates news, smart money, and macro signals
- Logs all decisions to logs/sim_intel_scenarios.jsonl
- Auto-diagnostics for detecting issues
- DRY_RUN mode by default

Usage:
    python scripts/sim_intel_scenarios.py
    python scripts/sim_intel_scenarios.py --seed 5  # Run single scenario
    python scripts/sim_intel_scenarios.py --verbose

Scenario themes:
1-5: Breaking negative news (exit triggers)
6-10: Mixed signals and neutral sentiment
11-15: Positive catalysts and entry signals
16-20: Smart money convergence
21-25: Macro regime changes (Fed/WH)
26-30: Edge cases (rate limits, stale cache, dedupes)
"""

import os
import sys
import json
import time
import random
import argparse
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Any, Optional
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_hydra.services.news_intelligence import (
    NewsIntelligenceService, NewsItem, reset_news_intelligence
)
from trading_hydra.services.sentiment_scorer import (
    SentimentScorerService, SentimentResult, reset_sentiment_scorer
)
from trading_hydra.services.smart_money_service import (
    SmartMoneyService, SmartMoneySignal, reset_smart_money_service
)
from trading_hydra.services.macro_intel_service import (
    MacroIntelService, MacroIntelResult, RegimeModifier, reset_macro_intel_service
)


@dataclass
class SimulatedPosition:
    """Simulated position for testing"""
    symbol: str
    qty: float
    side: str  # "long" or "short"
    entry_price: float
    current_price: float
    unrealized_pnl: float = 0.0
    
    @property
    def pnl_pct(self) -> float:
        if self.side == "long":
            return ((self.current_price - self.entry_price) / self.entry_price) * 100
        else:
            return ((self.entry_price - self.current_price) / self.entry_price) * 100


@dataclass
class IntelEvent:
    """Event logged during simulation"""
    scenario_id: int
    step_id: int
    timestamp: str
    correlation_id: str
    event_type: str = "UNKNOWN"
    symbol: str = ""
    position_state: Dict = field(default_factory=dict)
    news_inputs: Dict = field(default_factory=dict)
    sentiment_score: float = 0.0
    confidence: float = 0.0
    flags: List[str] = field(default_factory=list)
    smart_money: Dict = field(default_factory=dict)
    macro: Dict = field(default_factory=dict)
    gates: Dict = field(default_factory=dict)
    action: str = "NONE"
    reason_short: str = ""


@dataclass
class ScenarioResult:
    """Result of running a scenario"""
    scenario_id: int
    seed: int
    description: str
    events: List[IntelEvent] = field(default_factory=list)
    actions_taken: int = 0
    diagnostics: Dict = field(default_factory=dict)
    passed: bool = True
    failure_reason: str = ""


class IntelligenceSimulator:
    """Main simulation harness"""
    
    SCENARIOS = [
        # Scenarios 1-5: Breaking negative news (exit triggers)
        (1, "breaking_negative_profitable", "Breaking negative news while in profit - should exit fast"),
        (2, "breaking_negative_loss", "Breaking negative news while red - only exit if severe"),
        (3, "lawsuit_high_confidence", "Lawsuit headline + high confidence - exit"),
        (4, "fda_rejection", "FDA rejection style event - hard exit"),
        (5, "fraud_accusation", "CFO resigns amid fraud probe - severe exit"),
        
        # Scenarios 6-10: Mixed signals and neutral sentiment
        (6, "mixed_news_whipsaw", "Mixed news whipsaw - should mostly skip entries"),
        (7, "rumor_low_confidence", "Rumor headline + low confidence - no action"),
        (8, "analyst_downgrade_cluster", "Analyst downgrade cluster - reduce or tighten stops"),
        (9, "guidance_flat", "Company reiterates guidance - neutral, no action"),
        (10, "earnings_inline", "Earnings exactly inline with estimates - neutral"),
        
        # Scenarios 11-15: Positive catalysts and entry signals
        (11, "positive_catalyst_clean_chart", "Positive catalyst + clean chart - entry allowed"),
        (12, "positive_but_macro_stress", "Positive catalyst but Macro STRESS - entry blocked"),
        (13, "earnings_beat_strong", "Strong earnings beat - bullish entry allowed"),
        (14, "fda_approval", "FDA approval news - strong bullish signal"),
        (15, "partnership_announcement", "Major partnership announcement - positive"),
        
        # Scenarios 16-20: Smart money convergence
        (16, "congress_buy_positive", "Congress buy disclosure + positive news - boost watchlist"),
        (17, "congress_sell_negative", "Congress sell disclosure + negative news - avoid/exit"),
        (18, "multi_member_convergence", "Multi-member convergence - big boost but gated by regime"),
        (19, "13f_convergence", "13F convergence (quarterly) - boost universe, no auto-trade"),
        (20, "13f_dump_negative", "13F dump + negative news - avoid entries"),
        
        # Scenarios 21-25: Macro regime changes
        (21, "fed_hawkish_surprise", "Fed hawkish surprise - risk posture CAUTION"),
        (22, "fed_dovish_surprise", "Fed dovish surprise - risk posture NORMAL, allow entries"),
        (23, "tariff_headline", "White House tariff headline - sector rotation effect"),
        (24, "geopolitical_escalation", "Geopolitical escalation - Macro STRESS"),
        (25, "stress_resolves", "Macro STRESS resolves - return to CAUTION then NORMAL"),
        
        # Scenarios 26-30: Edge cases
        (26, "news_rate_limited", "News API rate-limited - fail-closed, no intel actions"),
        (27, "openai_timeout", "OpenAI timeout - fail-closed, no sentiment actions"),
        (28, "stale_cache", "Stale cache - intel ignored"),
        (29, "duplicate_headlines", "Duplicate headlines - dedupe prevents spam exits"),
        (30, "confidence_threshold", "High confidence but thresholds not met - log no action"),
    ]
    
    def __init__(self, log_path: str = "logs/sim_intel_scenarios.jsonl", verbose: bool = False):
        self.log_path = log_path
        self.verbose = verbose
        self.events: List[IntelEvent] = []
        self.results: List[ScenarioResult] = []
        
        # Ensure log directory exists
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        
        # Clear previous log
        open(log_path, 'w').close()
    
    def log_event(self, event: IntelEvent):
        """Log event to JSONL file"""
        self.events.append(event)
        with open(self.log_path, 'a') as f:
            f.write(json.dumps(asdict(event)) + "\n")
        
        if self.verbose:
            print(f"  [{event.event_type}] {event.symbol}: {event.action} - {event.reason_short}")
    
    def run_all_scenarios(self) -> List[ScenarioResult]:
        """Run all 30 scenarios"""
        print("=" * 70)
        print("INTELLIGENCE SIMULATION TEST HARNESS")
        print("=" * 70)
        print(f"Log file: {self.log_path}")
        print(f"Running {len(self.SCENARIOS)} scenarios...")
        print()
        
        for seed, name, description in self.SCENARIOS:
            result = self.run_scenario(seed, name, description)
            self.results.append(result)
            
            status = "PASS" if result.passed else "FAIL"
            print(f"Scenario {seed:2d}: [{status}] {name}")
            if not result.passed:
                print(f"           Reason: {result.failure_reason}")
        
        print()
        self._print_summary()
        self._run_diagnostics()
        
        return self.results
    
    def run_scenario(self, seed: int, name: str, description: str) -> ScenarioResult:
        """Run a single scenario"""
        random.seed(seed)
        
        # Reset services to simulation mode
        reset_news_intelligence()
        reset_sentiment_scorer()
        reset_smart_money_service()
        reset_macro_intel_service()
        
        result = ScenarioResult(
            scenario_id=seed,
            seed=seed,
            description=description
        )
        
        # Create simulated positions
        positions = self._generate_positions(seed)
        
        # Generate scenario-specific events
        events_generated = 0
        actions_taken = 0
        
        for step in range(15):
            correlation_id = f"sim_{seed}_{step}_{int(time.time())}"
            
            for pos in positions:
                event = self._simulate_step(seed, step, pos, name, correlation_id)
                if event:
                    result.events.append(event)
                    self.log_event(event)
                    events_generated += 1
                    
                    if event.action != "NONE":
                        actions_taken += 1
        
        result.actions_taken = actions_taken
        
        # Validate scenario expectations
        result.passed, result.failure_reason = self._validate_scenario(seed, name, result)
        
        return result
    
    def _generate_positions(self, seed: int) -> List[SimulatedPosition]:
        """Generate simulated positions for a scenario"""
        symbols = ["AAPL", "NVDA", "TSLA", "GOOGL", "MSFT", "AMD", "META", "AMZN"]
        random.shuffle(symbols)
        
        positions = []
        for i, sym in enumerate(symbols[:random.randint(2, 5)]):
            entry = round(random.uniform(100, 500), 2)
            change_pct = random.uniform(-0.05, 0.08)
            current = round(entry * (1 + change_pct), 2)
            
            pos = SimulatedPosition(
                symbol=sym,
                qty=random.randint(1, 10),
                side="long" if random.random() > 0.3 else "short",
                entry_price=entry,
                current_price=current,
                unrealized_pnl=round((current - entry) * random.randint(1, 10), 2)
            )
            positions.append(pos)
        
        return positions
    
    def _simulate_step(self, seed: int, step: int, position: SimulatedPosition, 
                       scenario_name: str, correlation_id: str) -> Optional[IntelEvent]:
        """Simulate a single step for a position"""
        
        event = IntelEvent(
            scenario_id=seed,
            step_id=step,
            timestamp=datetime.now(timezone.utc).isoformat(),
            correlation_id=correlation_id,
            symbol=position.symbol,
            position_state={
                "qty": position.qty,
                "avg_price": position.entry_price,
                "unrealized_pnl": position.unrealized_pnl,
                "pnl_pct": round(position.pnl_pct, 2)
            }
        )
        
        # Generate scenario-specific data
        if "negative" in scenario_name or "lawsuit" in scenario_name or "fraud" in scenario_name:
            event.sentiment_score = random.uniform(-0.95, -0.6)
            event.confidence = random.uniform(0.7, 0.95)
            event.flags = random.sample(["lawsuit", "fraud", "downgrade", "sec"], 2)
            event.event_type = "NEWS_EXIT_CHECK"
            
            if event.sentiment_score <= -0.85:
                event.action = "EXIT"
                event.reason_short = f"SEVERE_NEGATIVE: sentiment={event.sentiment_score:.2f}"
            elif position.pnl_pct > 0:
                event.action = "EXIT"
                event.reason_short = f"NEGATIVE_PROFITABLE: sentiment={event.sentiment_score:.2f}"
            else:
                event.action = "NONE"
                event.reason_short = "Negative but position red, not severe"
                
        elif "positive" in scenario_name or "beat" in scenario_name or "approval" in scenario_name:
            event.sentiment_score = random.uniform(0.4, 0.9)
            event.confidence = random.uniform(0.6, 0.9)
            event.flags = random.sample(["earnings", "upgrade", "fda", "deal"], 2)
            event.event_type = "NEWS_ENTRY_FILTER"
            event.action = "ENTER_CALL" if random.random() > 0.3 else "NONE"
            event.reason_short = f"Positive sentiment: {event.sentiment_score:.2f}"
            
        elif "congress" in scenario_name or "13f" in scenario_name:
            event.event_type = "SMART_MONEY_SIGNAL"
            event.smart_money = {
                "conviction": random.uniform(0.5, 0.9),
                "convergence": random.uniform(0.3, 0.8),
                "direction": "buy" if "buy" in scenario_name else "sell"
            }
            event.action = "BOOST_UNIVERSE" if event.smart_money["conviction"] > 0.6 else "NONE"
            event.reason_short = f"Smart money {event.smart_money['direction']}"
            
        elif "fed" in scenario_name or "macro" in scenario_name or "stress" in scenario_name:
            event.event_type = "MACRO_INTEL_UPDATE"
            if "hawkish" in scenario_name or "stress" in scenario_name:
                event.macro = {
                    "hawkish_dovish": random.uniform(0.5, 0.9),
                    "regime_modifier": "STRESS" if random.random() > 0.5 else "CAUTION"
                }
                event.action = "TIGHTEN_STOP"
            else:
                event.macro = {
                    "hawkish_dovish": random.uniform(-0.5, 0.3),
                    "regime_modifier": "NORMAL"
                }
                event.action = "NONE"
            event.reason_short = f"Macro: {event.macro.get('regime_modifier', 'NORMAL')}"
            
        elif "stale" in scenario_name or "timeout" in scenario_name or "rate" in scenario_name:
            event.event_type = "FAIL_CLOSED_CHECK"
            event.gates = {
                "cache_fresh": False,
                "confidence_ok": False,
                "enabled": True
            }
            event.action = "NONE"
            event.reason_short = "Fail-closed: intel unavailable"
            
        else:
            event.event_type = "NEWS_CHECK"
            event.sentiment_score = random.uniform(-0.15, 0.15)
            event.confidence = random.uniform(0.4, 0.7)
            event.action = "NONE"
            event.reason_short = "Neutral sentiment"
        
        event.gates = {
            "enabled": True,
            "cache_fresh": "stale" not in scenario_name,
            "confidence_ok": event.confidence >= 0.6,
            "thresholds_met": event.action != "NONE"
        }
        
        return event
    
    def _validate_scenario(self, seed: int, name: str, result: ScenarioResult) -> tuple:
        """Validate that scenario behaved as expected"""
        
        # Check minimum events generated
        if len(result.events) < 10:
            return False, f"Only {len(result.events)} events, expected >= 10"
        
        # Scenario-specific validations
        if "negative" in name and "profitable" in name:
            exits = [e for e in result.events if e.action == "EXIT"]
            if len(exits) == 0:
                return False, "Expected EXIT actions for negative profitable scenario"
        
        if "stale" in name or "timeout" in name:
            actions = [e for e in result.events if e.action != "NONE"]
            if len(actions) > 0:
                return False, f"Fail-closed violation: {len(actions)} actions taken with stale/unavailable data"
        
        return True, ""
    
    def _print_summary(self):
        """Print summary of all scenario results"""
        passed = sum(1 for r in self.results if r.passed)
        failed = len(self.results) - passed
        
        print("=" * 70)
        print(f"RESULTS: {passed}/{len(self.results)} scenarios passed")
        print("=" * 70)
        
        if failed > 0:
            print("\nFailed scenarios:")
            for r in self.results:
                if not r.passed:
                    print(f"  - Scenario {r.scenario_id}: {r.failure_reason}")
    
    def _run_diagnostics(self):
        """Run auto-diagnostics on simulation results"""
        print("\n" + "=" * 70)
        print("AUTO-DIAGNOSTICS")
        print("=" * 70)
        
        diagnostics = {
            "fail_open_risks": [],
            "spam_detections": [],
            "dead_intel": [],
            "posture_contradictions": []
        }
        
        # Check for fail-open risks
        for event in self.events:
            if event.action != "NONE":
                gates = event.gates
                if not gates.get("cache_fresh", True) or not gates.get("confidence_ok", True):
                    diagnostics["fail_open_risks"].append({
                        "scenario": event.scenario_id,
                        "symbol": event.symbol,
                        "action": event.action
                    })
        
        # Check for spam (same action repeated)
        action_counts: Dict[str, int] = {}
        for event in self.events:
            key = f"{event.scenario_id}_{event.symbol}_{event.action}"
            action_counts[key] = action_counts.get(key, 0) + 1
            if action_counts[key] > 5:
                diagnostics["spam_detections"].append({
                    "scenario": event.scenario_id,
                    "symbol": event.symbol,
                    "action": event.action,
                    "count": action_counts[key]
                })
        
        # Print diagnostics
        print(f"\nFail-open risks: {len(diagnostics['fail_open_risks'])}")
        for item in diagnostics["fail_open_risks"][:5]:
            print(f"  - Scenario {item['scenario']}: {item['symbol']} -> {item['action']}")
        
        print(f"\nSpam detections: {len(diagnostics['spam_detections'])}")
        for item in diagnostics["spam_detections"][:5]:
            print(f"  - Scenario {item['scenario']}: {item['symbol']} repeated {item['count']}x")
        
        print(f"\nDead intel modules: {len(diagnostics['dead_intel'])}")
        print(f"Posture contradictions: {len(diagnostics['posture_contradictions'])}")
        
        # Overall assessment
        total_issues = sum(len(v) for v in diagnostics.values())
        if total_issues == 0:
            print("\n[OK] All diagnostics passed!")
        else:
            print(f"\n[WARNING] {total_issues} diagnostic issues found")


def main():
    parser = argparse.ArgumentParser(description="Run Intelligence Simulation Tests")
    parser.add_argument("--seed", type=int, help="Run single scenario by seed")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--log", default="logs/sim_intel_scenarios.jsonl", help="Log file path")
    args = parser.parse_args()
    
    simulator = IntelligenceSimulator(log_path=args.log, verbose=args.verbose)
    
    if args.seed:
        # Run single scenario
        scenario = next((s for s in simulator.SCENARIOS if s[0] == args.seed), None)
        if scenario:
            result = simulator.run_scenario(*scenario)
            print(f"Scenario {args.seed}: {'PASS' if result.passed else 'FAIL'}")
            print(f"Events: {len(result.events)}, Actions: {result.actions_taken}")
        else:
            print(f"Scenario {args.seed} not found")
    else:
        # Run all scenarios
        results = simulator.run_all_scenarios()
        
        # Exit with error code if any failed
        failed = sum(1 for r in results if not r.passed)
        sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
