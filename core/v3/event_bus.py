"""Async event bus with topic-based routing.

Subscribe is synchronous (registration is instant).
Publish is async (dispatches to handlers concurrently).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Dict, List, Optional

from .models import BaseEvent, EventType

logger = logging.getLogger(__name__)

Handler = Callable[[BaseEvent], Awaitable[None]]


class EventBus:
    """Lightweight async publish / subscribe event bus.

    Usage::

        bus = EventBus()

        async def on_tick(event: BaseEvent) -> None:
            print(event)

        bus.subscribe(EventType.TICK, on_tick)
        await bus.publish(TickEvent(symbol="BTCUSDT", price=65000.0))
    """

    def __init__(self) -> None:
        self._handlers: Dict[EventType, List[Handler]] = {}
        self._wildcards: List[Handler] = []

    def subscribe(self, topic: EventType | str | None, handler: Handler) -> None:
        if topic is None:
            self._wildcards.append(handler)
        else:
            etype = EventType(topic) if isinstance(topic, str) else topic
            if etype not in self._handlers:
                self._handlers[etype] = []
            self._handlers[etype].append(handler)

    def unsubscribe(self, topic: EventType | str | None, handler: Handler) -> None:
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

        results = await asyncio.gather(
            *(self._safe_call(h, event) for h in handlers),
            return_exceptions=True,
        )
        for handler, result in zip(handlers, results):
            if isinstance(result, Exception):
                logger.error("Handler %s failed on %s: %s", handler.__qualname__, topic.value, result)

    @staticmethod
    async def _safe_call(handler: Handler, event: BaseEvent) -> None:
        try:
            await handler(event)
        except Exception:
            logger.exception("Error in handler %s", handler.__qualname__)
            raise

    @property
    def handler_count(self) -> int:
        return len(self._wildcards) + sum(len(v) for v in self._handlers.values())

    def topics(self) -> list[str]:
        return [t.value for t in self._handlers.keys()]