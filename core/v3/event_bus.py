"""
v3 Core - Async Event Bus
==========================
Lightweight in-process pub/sub — no Kafka needed.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Awaitable, Callable, Dict, List, Set

from core.v3.models import BaseEvent, EventType

logger = logging.getLogger(__name__)
EventHandler = Callable[[BaseEvent], Awaitable[None]]


class EventBus:
    """Async event bus with topic-based routing."""

    def __init__(self) -> None:
        self._handlers: Dict[EventType, List[EventHandler]] = defaultdict(list)
        self._wildcards: List[EventHandler] = []

    def subscribe(self, topic: EventType | str | None, handler: EventHandler) -> None:
        if topic is None:
            self._wildcards.append(handler)
        else:
            etype = EventType(topic) if isinstance(topic, str) else topic
            self._handlers[etype].append(handler)

    def unsubscribe(self, topic: EventType | str | None, handler: EventHandler) -> None:
        if topic is None:
            self._wildcards = [h for h in self._wildcards if h is not handler]
        else:
            etype = EventType(topic) if isinstance(topic, str) else topic
            self._handlers[etype] = [h for h in self._handlers.get(etype, []) if h is not handler]

    async def publish(self, event: BaseEvent) -> None:
        topic = EventType(event.type) if isinstance(event.type, str) else event.type
        handlers = list(self._handlers.get(topic, [])) + list(self._wildcards)
        if not handlers:
            return
        tasks = [self._safe_call(h, event) for h in handlers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for handler, result in zip(handlers, results):
            if isinstance(result, Exception):
                logger.error("Handler %s failed: %s", handler.__qualname__, result)

    async def _safe_call(self, handler: EventHandler, event: BaseEvent) -> None:
        try:
            await handler(event)
        except Exception:
            logger.exception("Error in handler %s", handler.__qualname__)
            raise

    @property
    def handler_count(self) -> int:
        return len(self._wildcards) + sum(len(v) for v in self._handlers.values())
