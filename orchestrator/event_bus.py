import logging
import threading
from typing import Dict, List, Callable, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict
from enum import Enum

logger = logging.getLogger(__name__)


class EventType(Enum):
    SIGNAL_GENERATED = "signal_generated"
    SIGNAL_VALIDATED = "signal_validated"
    SIGNAL_REJECTED = "signal_rejected"
    ORDER_CREATED = "order_created"
    ORDER_SUBMITTED = "order_submitted"
    ORDER_FILLED = "order_filled"
    ORDER_PARTIAL = "order_partial"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_FAILED = "order_failed"
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    POSITION_MODIFIED = "position_modified"
    STOP_LOSS_TRIGGERED = "stop_loss_triggered"
    TAKE_PROFIT_TRIGGERED = "take_profit_triggered"
    DRAWDOWN_ALERT = "drawdown_alert"
    EXPOSURE_WARNING = "exposure_warning"
    CIRCUIT_BREAKER = "circuit_breaker"
    SYSTEM_ERROR = "system_error"
    SYSTEM_INFO = "system_info"
    PORTFOLIO_UPDATED = "portfolio_updated"
    TICK_UPDATE = "tick_update"
    BAR_CLOSE = "bar_close"
    CUSTOM = "custom"


@dataclass
class Event:
    event_type: EventType
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    source: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class Subscription:
    event_type: EventType
    callback: Callable
    name: str = ""
    once: bool = False


class EventBus:

    def __init__(self):
        self._subscriptions: Dict[EventType, List[Subscription]] = defaultdict(list)
        self._history: List[Event] = []
        self._max_history: int = 1000
        self._lock = threading.Lock()
        self._global_handlers: List[Callable] = []
        self._event_counts: Dict[str, int] = defaultdict(int)
        logger.info("EventBus initialized")

    def subscribe(self, event_type: EventType, callback: Callable, name: str = "", once: bool = False) -> Subscription:
        with self._lock:
            sub = Subscription(event_type=event_type, callback=callback, name=name or callback.__name__, once=once)
            self._subscriptions[event_type].append(sub)
            logger.debug(f"Subscribed: {name or callback.__name__} -> {event_type.value}")
            return sub

    def unsubscribe(self, event_type: EventType, callback: Callable) -> bool:
        with self._lock:
            subs = self._subscriptions.get(event_type, [])
            for i, sub in enumerate(subs):
                if sub.callback == callback:
                    subs.pop(i)
                    return True
            return False

    def publish(self, event_type: EventType, data: Dict = None, source: str = "") -> int:
        event = Event(event_type=event_type, data=data or {}, source=source)
        self._record(event)
        count = 0
        with self._lock:
            subs = list(self._subscriptions.get(event_type, []))
        for sub in subs:
            try:
                sub.callback(event)
                count += 1
                if sub.once:
                    self.unsubscribe(event_type, sub.callback)
            except Exception as e:
                logger.error(f"Event handler {sub.name} error on {event_type.value}: {e}")
        for handler in self._global_handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error(f"Global handler error: {e}")
        return count

    def on_all_events(self, handler: Callable):
        self._global_handlers.append(handler)

    def _record(self, event: Event):
        self._event_counts[event.event_type.value] += 1
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

    def get_history(self, event_type: Optional[EventType] = None, limit: int = 100) -> List[Event]:
        if event_type:
            return [e for e in self._history if e.event_type == event_type][-limit:]
        return self._history[-limit:]

    def get_event_counts(self) -> Dict[str, int]:
        return dict(self._event_counts)

    def clear_history(self):
        self._history.clear()
        self._event_counts.clear()

    def get_status(self) -> Dict:
        total_subs = sum(len(s) for s in self._subscriptions.values())
        return {
            "total_subscriptions": total_subs,
            "global_handlers": len(self._global_handlers),
            "event_types": len(self._subscriptions),
            "total_events": sum(self._event_counts.values()),
            "history_size": len(self._history),
            "event_counts": dict(self._event_counts),
        }
