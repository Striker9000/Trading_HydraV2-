"""
Alert Service for Trading Hydra - Production Monitoring
========================================================

Provides configurable alerting for critical trading events:
- System halts
- Daily loss limits hit
- Position exits
- API errors
- Bot failures

Supports multiple notification channels:
- Console logging (always on)
- File alerts (stored in alerts.jsonl)
- Webhook (configurable for Slack, Discord, etc.)

Usage:
    from trading_hydra.services.alerts import get_alert_service, AlertLevel
    
    alerts = get_alert_service()
    alerts.send_alert(
        level=AlertLevel.CRITICAL,
        category="halt",
        title="Trading Halted",
        message="Daily loss limit exceeded: -$500",
        data={"equity": 43000, "daily_pnl": -500}
    )
"""

import os
import json
import requests
from enum import Enum
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional, List
from pathlib import Path

from ..core.logging import get_logger
from ..core.config import load_settings


class AlertLevel(Enum):
    """Alert severity levels"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class Alert:
    """Represents a single alert event"""
    timestamp: str
    level: str
    category: str
    title: str
    message: str
    data: Dict[str, Any]
    acknowledged: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class AlertService:
    """
    Centralized alert service for production monitoring.
    
    Features:
    - Multi-channel notifications (console, file, webhook)
    - Alert deduplication (avoid spamming same alert)
    - Severity filtering
    - Rate limiting
    """
    
    def __init__(self):
        self._logger = get_logger()
        self._settings = load_settings()
        
        self._alerts_file = Path("logs/alerts.jsonl")
        self._alerts_file.parent.mkdir(exist_ok=True)
        
        self._recent_alerts: List[Alert] = []
        self._max_recent = 100
        
        self._webhook_url = os.environ.get("ALERT_WEBHOOK_URL")
        
        self._last_alert_times: Dict[str, datetime] = {}
        self._rate_limit_seconds = 60
        
        self._load_recent_alerts()
        
        self._logger.info("[alerts_service_init] Alert service initialized", 
                         webhook_configured=bool(self._webhook_url))
    
    def _load_recent_alerts(self):
        """Load recent alerts from file on startup"""
        try:
            if self._alerts_file.exists():
                with open(self._alerts_file, 'r') as f:
                    lines = f.readlines()[-self._max_recent:]
                    for line in lines:
                        try:
                            data = json.loads(line.strip())
                            alert = Alert(**data)
                            self._recent_alerts.append(alert)
                        except:
                            continue
        except Exception as e:
            self._logger.warn(f"[alerts_load_failed] Could not load alerts: {e}")
    
    def send_alert(
        self,
        level: AlertLevel,
        category: str,
        title: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
        force: bool = False
    ) -> bool:
        """
        Send an alert through all configured channels.
        
        Args:
            level: Severity level (INFO, WARNING, ERROR, CRITICAL)
            category: Alert category (halt, exit, error, performance)
            title: Short alert title
            message: Detailed alert message
            data: Additional context data
            force: Bypass rate limiting
        
        Returns:
            True if alert was sent, False if rate-limited
        """
        alert_key = f"{category}:{title}"
        now = datetime.now()
        
        if not force and alert_key in self._last_alert_times:
            time_since = (now - self._last_alert_times[alert_key]).total_seconds()
            if time_since < self._rate_limit_seconds:
                return False
        
        self._last_alert_times[alert_key] = now
        
        alert = Alert(
            timestamp=now.isoformat(),
            level=level.value,
            category=category,
            title=title,
            message=message,
            data=data or {}
        )
        
        self._log_alert(alert)
        self._save_alert(alert)
        self._send_webhook(alert)
        
        self._recent_alerts.append(alert)
        if len(self._recent_alerts) > self._max_recent:
            self._recent_alerts = self._recent_alerts[-self._max_recent:]
        
        return True
    
    def _log_alert(self, alert: Alert):
        """Log alert to console with appropriate level"""
        log_data = {
            "category": alert.category,
            "title": alert.title,
            "message": alert.message,
            **alert.data
        }
        
        if alert.level == "critical":
            self._logger.error(f"[alert_{alert.category}] CRITICAL: {alert.title}", **log_data)
        elif alert.level == "error":
            self._logger.error(f"[alert_{alert.category}] {alert.title}", **log_data)
        elif alert.level == "warning":
            self._logger.warn(f"[alert_{alert.category}] {alert.title}", **log_data)
        else:
            self._logger.info(f"[alert_{alert.category}] {alert.title}", **log_data)
    
    def _save_alert(self, alert: Alert):
        """Persist alert to JSONL file"""
        try:
            with open(self._alerts_file, 'a') as f:
                f.write(alert.to_json() + "\n")
        except Exception as e:
            self._logger.error(f"[alert_save_failed] {e}")
    
    def _send_webhook(self, alert: Alert):
        """Send alert to configured webhook (Slack, Discord, etc.)"""
        if not self._webhook_url:
            return
        
        try:
            emoji_map = {
                "critical": "🚨",
                "error": "❌",
                "warning": "⚠️",
                "info": "ℹ️"
            }
            emoji = emoji_map.get(alert.level, "📢")
            
            payload = {
                "text": f"{emoji} *{alert.title}*\n{alert.message}",
                "attachments": [{
                    "color": self._get_color(alert.level),
                    "fields": [
                        {"title": k, "value": str(v), "short": True}
                        for k, v in alert.data.items()
                    ][:10]
                }]
            }
            
            response = requests.post(
                self._webhook_url,
                json=payload,
                timeout=5
            )
            
            if response.status_code != 200:
                self._logger.warn(f"[webhook_failed] Status {response.status_code}")
                
        except Exception as e:
            self._logger.warn(f"[webhook_error] {e}")
    
    def _get_color(self, level: str) -> str:
        """Get color for webhook attachment"""
        colors = {
            "critical": "#FF0000",
            "error": "#FF6B6B",
            "warning": "#FFB347",
            "info": "#4ECDC4"
        }
        return colors.get(level, "#808080")
    
    def get_recent_alerts(
        self,
        limit: int = 50,
        level: Optional[AlertLevel] = None,
        category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get recent alerts with optional filtering"""
        alerts = self._recent_alerts[-limit:]
        
        if level:
            alerts = [a for a in alerts if a.level == level.value]
        if category:
            alerts = [a for a in alerts if a.category == category]
        
        return [a.to_dict() for a in reversed(alerts)]
    
    def get_unacknowledged_count(self) -> int:
        """Count unacknowledged alerts"""
        return sum(1 for a in self._recent_alerts if not a.acknowledged)
    
    def acknowledge_all(self):
        """Mark all alerts as acknowledged"""
        for alert in self._recent_alerts:
            alert.acknowledged = True
    
    def alert_halt(self, reason: str, equity: float, daily_pnl: float):
        """Convenience method for halt alerts"""
        self.send_alert(
            level=AlertLevel.CRITICAL,
            category="halt",
            title="Trading Halted",
            message=reason,
            data={"equity": equity, "daily_pnl": daily_pnl},
            force=True
        )
    
    def alert_exit(
        self,
        symbol: str,
        side: str,
        reason: str,
        pnl: float,
        pnl_pct: float
    ):
        """Convenience method for position exit alerts"""
        level = AlertLevel.INFO if pnl >= 0 else AlertLevel.WARNING
        self.send_alert(
            level=level,
            category="exit",
            title=f"Position Closed: {symbol}",
            message=f"{side.upper()} {symbol} closed via {reason}",
            data={
                "symbol": symbol,
                "side": side,
                "reason": reason,
                "pnl": pnl,
                "pnl_pct": pnl_pct
            }
        )
    
    def alert_error(self, component: str, error: str, context: Optional[Dict[str, Any]] = None):
        """Convenience method for error alerts"""
        self.send_alert(
            level=AlertLevel.ERROR,
            category="error",
            title=f"Error in {component}",
            message=error,
            data=context or {}
        )
    
    def alert_daily_summary(
        self,
        equity: float,
        daily_pnl: float,
        trades: int,
        exits: int,
        positions: int
    ):
        """Send daily performance summary"""
        self.send_alert(
            level=AlertLevel.INFO,
            category="performance",
            title="Daily Summary",
            message=f"P&L: ${daily_pnl:+.2f} | Trades: {trades} | Exits: {exits}",
            data={
                "equity": equity,
                "daily_pnl": daily_pnl,
                "trades": trades,
                "exits": exits,
                "open_positions": positions
            },
            force=True
        )


_alert_service: Optional[AlertService] = None

def get_alert_service() -> AlertService:
    """Get singleton instance of AlertService"""
    global _alert_service
    if _alert_service is None:
        _alert_service = AlertService()
    return _alert_service
