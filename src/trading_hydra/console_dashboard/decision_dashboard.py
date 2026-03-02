"""
Console-based Decision Dashboard - View AI reasoning in the terminal.

Displays recent AI decisions with full reasoning:
- ML Signal scores and feature importance
- News sentiment analysis
- Risk gate evaluations
- Trade execution decisions
- P&L attribution breakdowns

Usage:
    python -m trading_hydra.console_dashboard

Or:
    from trading_hydra.console_dashboard import run_dashboard
    run_dashboard()
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from collections import defaultdict


DECISION_EVENT_TYPES = {
    "ml_signal",
    "ml_score", 
    "ml_score_entry",
    "ml_signal_service_score",
    "ml_model_loaded",
    "decision_signal_update",
    "news_intelligence_result",
    "news_exit_check",
    "sentiment_analysis",
    "risk_gate_check",
    "risk_orchestrator_evaluation",
    "options_risk_gate_blocked",
    "options_risk_gate_reduce",
    "options_bot_strategy_system_enabled",
    "twentymin_trade_decision",
    "trade_execution",
    "entry_decision",
    "exit_decision",
    "exit_signal",
    "entry_veto",
    "pnl_attribution_exit",
    "pnl_attribution_entry",
    "greek_delta_warning",
    "greek_gamma_warning",
    "greek_limit_blocked_entry",
    "iv_gate_blocked",
    "iv_gate_passed",
    "correlation_guard_alert",
    "vol_of_vol_alert",
    "macro_intel_update",
    "smart_money_signal",
    "halt_triggered",
    "pnl_monitor_fat_tail_detected",
    "pnl_monitor_halt",
    "enhanced_options_bot_execution_complete",
}


class Colors:
    """ANSI color codes for terminal output."""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'
    
    @classmethod
    def colorize(cls, text: str, color: str) -> str:
        return f"{color}{text}{cls.END}"


class DecisionDashboard:
    """
    Console dashboard for viewing AI decision reasoning.
    
    Parses JSONL logs and displays recent AI decisions in an
    organized, readable format with sections for different
    decision types.
    """
    
    def __init__(self, log_path: str = "./logs/app.jsonl"):
        self.log_path = log_path
        self.max_events = 50
    
    def load_recent_events(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Load decision events from the last N hours."""
        events = []
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        
        if not os.path.exists(self.log_path):
            return events
        
        try:
            with open(self.log_path, "r") as f:
                for line in f:
                    try:
                        event = json.loads(line.strip())
                        event_type = event.get("event", "")
                        
                        if event_type not in DECISION_EVENT_TYPES:
                            continue
                        
                        ts_str = event.get("ts", event.get("timestamp", ""))
                        if ts_str:
                            try:
                                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                                if ts.replace(tzinfo=None) < cutoff:
                                    continue
                            except (ValueError, AttributeError):
                                pass  # Malformed timestamp; include event regardless
                        
                        events.append(event)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            print(f"Error reading logs: {e}")
        
        return events[-self.max_events:]
    
    def categorize_events(self, events: List[Dict[str, Any]]) -> Dict[str, List[Dict]]:
        """Categorize events into display sections."""
        categories = {
            "ml_signals": [],
            "news_sentiment": [],
            "risk_gates": [],
            "trade_decisions": [],
            "pnl_attribution": [],
            "alerts": [],
        }
        
        for event in events:
            event_type = event.get("event", "")
            
            if event_type in {"ml_signal", "ml_score", "ml_score_entry", "ml_signal_service_score", 
                            "ml_model_loaded", "decision_signal_update"}:
                categories["ml_signals"].append(event)
            elif event_type in {"news_intelligence_result", "news_exit_check", "sentiment_analysis"}:
                categories["news_sentiment"].append(event)
            elif event_type in {"risk_gate_check", "risk_orchestrator_evaluation", 
                              "options_risk_gate_blocked", "options_risk_gate_reduce",
                              "iv_gate_blocked", "iv_gate_passed",
                              "greek_delta_warning", "greek_gamma_warning", "greek_limit_blocked_entry",
                              "options_bot_strategy_system_enabled"}:
                categories["risk_gates"].append(event)
            elif event_type in {"twentymin_trade_decision", "trade_execution",
                              "entry_decision", "exit_decision", "exit_signal", "entry_veto",
                              "enhanced_options_bot_execution_complete"}:
                categories["trade_decisions"].append(event)
            elif event_type in {"pnl_attribution_exit", "pnl_attribution_entry"}:
                categories["pnl_attribution"].append(event)
            elif event_type in {"halt_triggered", "correlation_guard_alert",
                              "vol_of_vol_alert", "pnl_monitor_fat_tail_detected", "pnl_monitor_halt",
                              "macro_intel_update", "smart_money_signal"}:
                categories["alerts"].append(event)
        
        return categories
    
    def format_timestamp(self, ts_str: str) -> str:
        """Format timestamp for display."""
        if not ts_str:
            return "??:??"
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return ts.strftime("%H:%M:%S")
        except (ValueError, AttributeError):
            return ts_str[:8] if len(ts_str) >= 8 else ts_str
    
    def format_ml_signal(self, event: Dict) -> str:
        """Format ML signal event for display."""
        data = event.get("data", event)
        symbol = data.get("symbol", "?")
        score = data.get("score", data.get("ml_score", 0))
        confidence = data.get("confidence", 0)
        features = data.get("top_features", data.get("features", {}))
        reason = data.get("reason", "")
        
        c = Colors
        lines = []
        lines.append(f"  {c.colorize(symbol, c.CYAN)}: Score {c.colorize(f'{score:.2f}', c.YELLOW if score > 0.5 else c.DIM)} | Confidence {confidence:.0%}")
        
        if features and isinstance(features, dict):
            feat_str = ", ".join([f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}" 
                                 for k, v in list(features.items())[:5]])
            lines.append(f"    {c.colorize('Features:', c.DIM)} {feat_str}")
        
        if reason:
            lines.append(f"    {c.colorize('Reason:', c.DIM)} {reason}")
        
        return "\n".join(lines)
    
    def format_news_sentiment(self, event: Dict) -> str:
        """Format news sentiment event for display."""
        data = event.get("data", event)
        symbol = data.get("symbol", "?")
        sentiment = data.get("sentiment", data.get("sentiment_score", 0))
        confidence = data.get("confidence", 0)
        action = data.get("action", "")
        headline = data.get("headline", data.get("summary", ""))[:60]
        
        c = Colors
        
        if sentiment > 0.3:
            sent_color = c.GREEN
            sent_label = "BULLISH"
        elif sentiment < -0.3:
            sent_color = c.RED
            sent_label = "BEARISH"
        else:
            sent_color = c.DIM
            sent_label = "NEUTRAL"
        
        lines = []
        lines.append(f"  {c.colorize(symbol, c.CYAN)}: {c.colorize(sent_label, sent_color)} ({sentiment:+.2f}) | Confidence {confidence:.0%}")
        
        if action:
            action_color = c.GREEN if action == "HOLD" else c.RED if action in ("EXIT", "BLOCK") else c.YELLOW
            lines.append(f"    {c.colorize('Action:', c.DIM)} {c.colorize(action, action_color)}")
        
        if headline:
            lines.append(f"    {c.colorize('News:', c.DIM)} {headline}...")
        
        return "\n".join(lines)
    
    def format_risk_gate(self, event: Dict) -> str:
        """Format risk gate event for display."""
        data = event.get("data", event)
        event_type = event.get("event", "")
        symbol = data.get("symbol", "?")
        
        c = Colors
        lines = []
        
        if event_type == "iv_gate_blocked":
            iv_pct = data.get("iv_percentile", 0)
            strategy = data.get("strategy", "?")
            lines.append(f"  {c.colorize('IV GATE BLOCKED', c.RED)}: {symbol} | {strategy}")
            lines.append(f"    IV Percentile: {iv_pct:.0f}% - outside acceptable range")
            
        elif event_type == "iv_gate_passed":
            iv_pct = data.get("iv_percentile", 0)
            lines.append(f"  {c.colorize('IV GATE OK', c.GREEN)}: {symbol} | IV: {iv_pct:.0f}%")
            
        elif event_type in {"greek_delta_warning", "greek_gamma_warning"}:
            utilization = data.get("utilization_pct", data.get("delta_util", data.get("gamma_util", 0)))
            status = data.get("status", "?")
            greek = "Delta" if "delta" in event_type else "Gamma"
            status_color = c.YELLOW if status == "near_limit" else c.RED
            lines.append(f"  {c.colorize(f'{greek.upper()} WARNING', status_color)}: {utilization:.0f}% of limit")
            
        elif event_type == "options_risk_gate_blocked":
            reason = data.get("reason", "?")
            lines.append(f"  {c.colorize('RISK BLOCKED', c.RED)}: {symbol}")
            lines.append(f"    {c.colorize('Reason:', c.DIM)} {reason}")
            
        elif event_type == "risk_orchestrator_evaluation":
            can_trade = data.get("can_trade", True)
            gates = data.get("gates_passed", {})
            failed = [k for k, v in gates.items() if not v]
            
            if can_trade:
                lines.append(f"  {c.colorize('RISK OK', c.GREEN)}: {symbol} - all gates passed")
            else:
                lines.append(f"  {c.colorize('RISK BLOCKED', c.RED)}: {symbol}")
                if failed:
                    lines.append(f"    {c.colorize('Failed gates:', c.DIM)} {', '.join(failed)}")
        else:
            lines.append(f"  {event_type}: {json.dumps(data)[:80]}")
        
        return "\n".join(lines)
    
    def format_trade_decision(self, event: Dict) -> str:
        """Format trade decision event for display."""
        data = event.get("data", event)
        event_type = event.get("event", "")
        symbol = data.get("symbol", "?")
        
        c = Colors
        lines = []
        
        if event_type == "twentymin_trade_decision":
            action = data.get("action", "?")
            strategy = data.get("strategy", "?")
            reason = data.get("reason", "")
            score = data.get("score", 0)
            
            action_color = c.GREEN if action == "TRADE" else c.YELLOW if action == "SKIP" else c.DIM
            lines.append(f"  {c.colorize(action, action_color)}: {c.colorize(symbol, c.CYAN)} | {strategy} | Score: {score:.2f}")
            if reason:
                lines.append(f"    {c.colorize('Reason:', c.DIM)} {reason}")
                
        elif event_type == "trade_execution":
            side = data.get("side", "?")
            qty = data.get("qty", data.get("quantity", 0))
            price = data.get("price", 0)
            side_color = c.GREEN if side.lower() == "buy" else c.RED
            lines.append(f"  {c.colorize(side.upper(), side_color)}: {c.colorize(symbol, c.CYAN)} x{qty} @ ${price:.2f}")
            
        elif event_type == "entry_veto":
            reason = data.get("reason", "?")
            lines.append(f"  {c.colorize('VETO', c.RED)}: {symbol}")
            lines.append(f"    {c.colorize('Reason:', c.DIM)} {reason}")
            
        elif event_type in {"exit_decision", "exit_signal"}:
            reason = data.get("reason", data.get("exit_reason", "?"))
            pnl = data.get("pnl", data.get("pnl_pct", 0))
            lines.append(f"  {c.colorize('EXIT', c.YELLOW)}: {c.colorize(symbol, c.CYAN)} | P&L: {pnl:+.1f}%")
            lines.append(f"    {c.colorize('Reason:', c.DIM)} {reason}")
        else:
            lines.append(f"  {event_type}: {symbol}")
        
        return "\n".join(lines)
    
    def format_pnl_attribution(self, event: Dict) -> str:
        """Format P&L attribution event for display."""
        data = event.get("data", event)
        symbol = data.get("symbol", "?")
        total = data.get("total_pnl", 0)
        delta = data.get("delta_pnl", 0)
        gamma = data.get("gamma_pnl", 0)
        theta = data.get("theta_pnl", 0)
        vega = data.get("vega_pnl", 0)
        residual = data.get("residual_pnl", 0)
        explained = data.get("explained_pct", 0)
        
        c = Colors
        total_color = c.GREEN if total > 0 else c.RED
        
        lines = []
        lines.append(f"  {c.colorize(symbol, c.CYAN)}: Total {c.colorize(f'${total:+.2f}', total_color)} | Explained: {explained:.0f}%")
        
        components = []
        if abs(delta) > 0.01:
            components.append(f"Delta: ${delta:+.2f}")
        if abs(gamma) > 0.01:
            components.append(f"Gamma: ${gamma:+.2f}")
        if abs(theta) > 0.01:
            components.append(f"Theta: ${theta:+.2f}")
        if abs(vega) > 0.01:
            components.append(f"Vega: ${vega:+.2f}")
        if abs(residual) > 0.01:
            components.append(f"Residual: ${residual:+.2f}")
        
        if components:
            lines.append(f"    {c.colorize('Breakdown:', c.DIM)} {' | '.join(components)}")
        
        return "\n".join(lines)
    
    def format_alert(self, event: Dict) -> str:
        """Format alert event for display."""
        data = event.get("data", event)
        event_type = event.get("event", "")
        
        c = Colors
        lines = []
        
        if event_type == "halt_triggered":
            reason = data.get("reason", "?")
            lines.append(f"  {c.colorize('HALT TRIGGERED', c.RED + c.BOLD)}")
            lines.append(f"    {c.colorize('Reason:', c.DIM)} {reason}")
            
        elif event_type == "pnl_monitor_fat_tail_detected":
            symbol = data.get("symbol", "?")
            loss = data.get("loss_pct", 0)
            multiple = data.get("multiple_of_median", 0)
            lines.append(f"  {c.colorize('FAT TAIL DETECTED', c.RED)}: {symbol}")
            lines.append(f"    Loss: {loss:.1f}% ({multiple:.1f}x median)")
            
        elif event_type == "macro_intel_update":
            regime = data.get("regime", "?")
            score = data.get("score", 0)
            regime_color = c.GREEN if regime == "NORMAL" else c.YELLOW if regime == "CAUTION" else c.RED
            lines.append(f"  {c.colorize('MACRO INTEL', c.BLUE)}: {c.colorize(regime, regime_color)} | Score: {score:+.2f}")
            
        elif event_type == "smart_money_signal":
            symbol = data.get("symbol", "?")
            signal = data.get("signal", "?")
            source = data.get("source", "?")
            lines.append(f"  {c.colorize('SMART MONEY', c.BLUE)}: {symbol} | {signal} via {source}")
        else:
            lines.append(f"  {event_type}: {json.dumps(data)[:80]}")
        
        return "\n".join(lines)
    
    def render(self) -> str:
        """Render the full dashboard as a string."""
        events = self.load_recent_events()
        categories = self.categorize_events(events)
        
        c = Colors
        lines = []
        
        lines.append("")
        lines.append(c.colorize("=" * 70, c.BOLD))
        lines.append(c.colorize("  AI DECISION SUMMARY DASHBOARD", c.BOLD + c.HEADER))
        lines.append(c.colorize(f"  Last 24 hours | {len(events)} decisions", c.DIM))
        lines.append(c.colorize("=" * 70, c.BOLD))
        
        if categories["ml_signals"]:
            lines.append("")
            lines.append(c.colorize("  ML SIGNAL SCORES", c.BOLD + c.BLUE))
            lines.append(c.colorize("  " + "-" * 40, c.DIM))
            for event in categories["ml_signals"][-10:]:
                lines.append(self.format_ml_signal(event))
        
        if categories["news_sentiment"]:
            lines.append("")
            lines.append(c.colorize("  NEWS & SENTIMENT ANALYSIS", c.BOLD + c.BLUE))
            lines.append(c.colorize("  " + "-" * 40, c.DIM))
            for event in categories["news_sentiment"][-10:]:
                lines.append(self.format_news_sentiment(event))
        
        if categories["risk_gates"]:
            lines.append("")
            lines.append(c.colorize("  RISK GATE EVALUATIONS", c.BOLD + c.BLUE))
            lines.append(c.colorize("  " + "-" * 40, c.DIM))
            for event in categories["risk_gates"][-10:]:
                lines.append(self.format_risk_gate(event))
        
        if categories["trade_decisions"]:
            lines.append("")
            lines.append(c.colorize("  TRADE DECISIONS", c.BOLD + c.BLUE))
            lines.append(c.colorize("  " + "-" * 40, c.DIM))
            for event in categories["trade_decisions"][-15:]:
                lines.append(self.format_trade_decision(event))
        
        if categories["pnl_attribution"]:
            lines.append("")
            lines.append(c.colorize("  P&L ATTRIBUTION (Greek Breakdown)", c.BOLD + c.BLUE))
            lines.append(c.colorize("  " + "-" * 40, c.DIM))
            for event in categories["pnl_attribution"][-10:]:
                lines.append(self.format_pnl_attribution(event))
        
        if categories["alerts"]:
            lines.append("")
            lines.append(c.colorize("  ALERTS & SIGNALS", c.BOLD + c.RED))
            lines.append(c.colorize("  " + "-" * 40, c.DIM))
            for event in categories["alerts"][-10:]:
                lines.append(self.format_alert(event))
        
        if not events:
            lines.append("")
            lines.append(c.colorize("  No decision events found in the last 24 hours.", c.DIM))
            lines.append(c.colorize("  Run the trading system to generate decision logs.", c.DIM))
        
        lines.append("")
        lines.append(c.colorize("=" * 70, c.BOLD))
        lines.append(c.colorize(f"  Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", c.DIM))
        lines.append(c.colorize("=" * 70, c.BOLD))
        lines.append("")
        
        return "\n".join(lines)
    
    def display(self):
        """Print the dashboard to console."""
        print(self.render())


def run_dashboard(log_path: str = "./logs/app.jsonl"):
    """Run the decision dashboard."""
    dashboard = DecisionDashboard(log_path=log_path)
    dashboard.display()


if __name__ == "__main__":
    run_dashboard()
