import logging
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from collections import deque

logger = logging.getLogger(__name__)


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


class AlertCategory(Enum):
    DRAWDOWN = "drawdown"
    EXPOSURE = "exposure"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    CIRCUIT_BREAKER = "circuit_breaker"
    VOLATILITY = "volatility"
    DAILY_LIMIT = "daily_limit"
    CONCENTRATION = "concentration"
    SYSTEM = "system"


@dataclass
class RiskAlert:
    alert_id: str
    category: AlertCategory
    severity: AlertSeverity
    title: str
    message: str
    symbol: str = ""
    value: float = 0.0
    threshold: float = 0.0
    timestamp: str = ""
    acknowledged: bool = False
    metadata: Dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class AlertRule:
    name: str
    category: AlertCategory
    severity: AlertSeverity
    condition_fn: Callable
    cooldown_minutes: int = 30
    enabled: bool = True
    last_triggered: Optional[str] = None


@dataclass
class AlertStats:
    total_alerts: int = 0
    by_category: Dict[str, int] = field(default_factory=dict)
    by_severity: Dict[str, int] = field(default_factory=dict)
    unacknowledged: int = 0
    last_alert_time: str = ""
    most_frequent_category: str = ""


class RiskAlertManager:

    def __init__(self, max_history: int = 500):
        self._alerts: List[RiskAlert] = []
        self._rules: Dict[str, AlertRule] = {}
        self._callbacks: List[Callable] = []
        self._max_history = max_history
        self._alert_counter = 0
        self._setup_default_rules()
        logger.info("RiskAlertManager initialized")

    def _setup_default_rules(self):
        self.add_rule(AlertRule(
            name="high_exposure", category=AlertCategory.EXPOSURE,
            severity=AlertSeverity.WARNING,
            condition_fn=lambda ctx: ctx.get("total_exposure_pct", 0) > 70,
            cooldown_minutes=30,
        ))
        self.add_rule(AlertRule(
            name="max_exposure", category=AlertCategory.EXPOSURE,
            severity=AlertSeverity.CRITICAL,
            condition_fn=lambda ctx: ctx.get("total_exposure_pct", 0) > 85,
            cooldown_minutes=15,
        ))
        self.add_rule(AlertRule(
            name="drawdown_warning", category=AlertCategory.DRAWDOWN,
            severity=AlertSeverity.WARNING,
            condition_fn=lambda ctx: ctx.get("drawdown_pct", 0) > 8,
            cooldown_minutes=60,
        ))
        self.add_rule(AlertRule(
            name="drawdown_critical", category=AlertCategory.DRAWDOWN,
            severity=AlertSeverity.EMERGENCY,
            condition_fn=lambda ctx: ctx.get("drawdown_pct", 0) > 15,
            cooldown_minutes=10,
        ))
        self.add_rule(AlertRule(
            name="high_concentration", category=AlertCategory.CONCENTRATION,
            severity=AlertSeverity.WARNING,
            condition_fn=lambda ctx: ctx.get("largest_position_pct", 0) > 15,
            cooldown_minutes=30,
        ))
        self.add_rule(AlertRule(
            name="daily_loss_limit", category=AlertCategory.DAILY_LIMIT,
            severity=AlertSeverity.CRITICAL,
            condition_fn=lambda ctx: ctx.get("daily_loss_pct", 0) > 3,
            cooldown_minutes=60,
        ))

    def add_rule(self, rule: AlertRule):
        self._rules[rule.name] = rule
        logger.debug(f"Alert rule added: {rule.name}")

    def remove_rule(self, name: str) -> bool:
        if name in self._rules:
            del self._rules[name]
            return True
        return False

    def register_callback(self, callback: Callable):
        self._callbacks.append(callback)
        logger.debug(f"Alert callback registered: {callback.__name__ if hasattr(callback, '__name__') else 'callback'}")

    def evaluate(self, context: Dict) -> List[RiskAlert]:
        triggered = []
        for name, rule in self._rules.items():
            if not rule.enabled:
                continue
            if rule.last_triggered:
                last = datetime.fromisoformat(rule.last_triggered)
                if (datetime.now() - last).total_seconds() < rule.cooldown_minutes * 60:
                    continue
            try:
                if rule.condition_fn(context):
                    alert = self._create_alert(rule, context)
                    self._alerts.append(alert)
                    self._alert_counter += 1
                    rule.last_triggered = datetime.now().isoformat()
                    triggered.append(alert)
                    self._dispatch(alert)
            except Exception as e:
                logger.error(f"Rule {name} evaluation failed: {e}")
        self._trim_history()
        return triggered

    def fire_manual_alert(self, category: AlertCategory, severity: AlertSeverity, title: str, message: str, symbol: str = "", metadata: Dict = None) -> RiskAlert:
        alert = RiskAlert(
            alert_id=self._next_id(), category=category, severity=severity,
            title=title, message=message, symbol=symbol, metadata=metadata or {},
        )
        self._alerts.append(alert)
        self._alert_counter += 1
        self._dispatch(alert)
        self._trim_history()
        logger.info(f"Manual alert: [{severity.value}] {title}")
        return alert

    def _create_alert(self, rule: AlertRule, context: Dict) -> RiskAlert:
        return RiskAlert(
            alert_id=self._next_id(), category=rule.category, severity=rule.severity,
            title=f"Rule: {rule.name}", message=f"{rule.category.value} threshold triggered",
            symbol=context.get("symbol", ""),
            value=context.get(rule.category.value + "_pct", 0) if rule.category.value + "_pct" in context else context.get("drawdown_pct", 0),
            threshold=rule.condition_fn.__code__.co_consts if hasattr(rule.condition_fn, '__code__') else 0,
            metadata={"rule": rule.name, "context": {k: v for k, v in context.items() if isinstance(v, (int, float, str, bool))}},
        )

    def _dispatch(self, alert: RiskAlert):
        for cb in self._callbacks:
            try:
                cb(alert)
            except Exception as e:
                logger.error(f"Alert callback error: {e}")

    def _next_id(self) -> str:
        self._alert_counter += 1
        return f"ALT-{self._alert_counter:06d}"

    def _trim_history(self):
        if len(self._alerts) > self._max_history:
            self._alerts = self._alerts[-self._max_history:]

    def acknowledge(self, alert_id: str) -> bool:
        for alert in self._alerts:
            if alert.alert_id == alert_id:
                alert.acknowledged = True
                return True
        return False

    def acknowledge_all(self):
        for alert in self._alerts:
            alert.acknowledged = True

    def get_alerts(self, category: Optional[AlertCategory] = None, severity: Optional[AlertSeverity] = None, acknowledged: Optional[bool] = None, limit: int = 50) -> List[RiskAlert]:
        filtered = self._alerts
        if category:
            filtered = [a for a in filtered if a.category == category]
        if severity:
            filtered = [a for a in filtered if a.severity == severity]
        if acknowledged is not None:
            filtered = [a for a in filtered if a.acknowledged == acknowledged]
        return filtered[-limit:]

    def get_unacknowledged(self) -> List[RiskAlert]:
        return [a for a in self._alerts if not a.acknowledged]

    def get_stats(self) -> AlertStats:
        by_cat: Dict[str, int] = {}
        by_sev: Dict[str, int] = {}
        for a in self._alerts:
            by_cat[a.category.value] = by_cat.get(a.category.value, 0) + 1
            by_sev[a.severity.value] = by_sev.get(a.severity.value, 0) + 1
        unack = sum(1 for a in self._alerts if not a.acknowledged)
        most_freq = max(by_cat, key=by_cat.get) if by_cat else ""
        return AlertStats(
            total_alerts=len(self._alerts),
            by_category=by_cat, by_severity=by_sev,
            unacknowledged=unack,
            last_alert_time=self._alerts[-1].timestamp if self._alerts else "",
            most_frequent_category=most_freq,
        )

    def clear_history(self):
        self._alerts.clear()
        self._alert_counter = 0
        logger.info("Alert history cleared")

    def get_status(self) -> Dict:
        stats = self.get_stats()
        return {
            "total_alerts": stats.total_alerts,
            "unacknowledged": stats.unacknowledged,
            "active_rules": sum(1 for r in self._rules.values() if r.enabled),
            "registered_callbacks": len(self._callbacks),
            "most_frequent_category": stats.most_frequent_category,
        }
