"""
v3 Market Data Service
======================
Wraps the existing BinanceFeed (or sim mode) and emits TickEvents
onto the event bus.  Bridges v1 BinanceFeed → v3 EventBus.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from typing import Optional

from core.v3.event_bus import EventBus
from core.v3.models import EventType, TickEvent

logger = logging.getLogger(__name__)


class V3MarketDataService:
    """
    Market data provider that emits normalized TickEvents.

    Modes:
      live  — wraps existing BinanceFeed (reads from shared state)
      sim   — generates synthetic random-walk ticks
    """

    def __init__(self, bus: EventBus, mode: str = "sim", binance_feed=None) -> None:
        self.bus = bus
        self.mode = mode
        self._binance_feed = binance_feed  # existing BinanceFeed instance

        self._tick_buffer: deque[TickEvent] = deque(maxlen=10_000)
        self._latest: Optional[TickEvent] = None
        self._sim_price: float = 65_000.0
        self._running = False
        self._tick_count = 0

    @property
    def latest_tick(self) -> Optional[TickEvent]:
        return self._latest

    @property
    def tick_count(self) -> int:
        return self._tick_count

    def recent_ticks(self, n: int = 100) -> list[TickEvent]:
        return list(self._tick_buffer)[-n:]

    async def start(self) -> None:
        self._running = True
        logger.info("V3 MarketData starting (mode=%s)", self.mode)
        if self.mode == "live" and self._binance_feed:
            await self._live_loop()
        else:
            await self._sim_loop()

    async def stop(self) -> None:
        self._running = False
        logger.info("V3 MarketData stopped after %d ticks", self._tick_count)

    async def _sim_loop(self) -> None:
        # Sim with occasional momentum bursts to test regime classification
        # Base drift: ~0.005% per tick (realistic BTC noise)
        # Occasional bursts: ~0.05% per tick (simulates momentum events)
        burst_counter = 0
        burst_direction = 1
        while self._running:
            burst_counter += 1
            # Every 40-80 ticks, switch to a momentum burst direction
            if burst_counter > random.randint(40, 80):
                burst_counter = 0
                burst_direction = random.choice([-1, 1])

            # Base noise + occasional momentum burst
            base_drift = random.gauss(0, 5.0)  # ~0.008% noise
            burst_drift = burst_direction * random.uniform(5, 18) if burst_counter < 25 else 0  # momentum burst (0.3-1% cumulative)
            revert = 0.0003 * (65_000.0 - self._sim_price)  # gentle mean reversion
            self._sim_price = max(100.0, self._sim_price + base_drift + burst_drift + revert)

            spread = random.uniform(0.5, 2.0)
            tick = TickEvent(
                symbol="BTCUSDT",
                price=round(self._sim_price, 2),
                volume=random.randint(10, 500) + (200 if burst_counter < 20 else 0),
                bid=round(self._sim_price - spread / 2, 2),
                ask=round(self._sim_price + spread / 2, 2),
                exchange="SIM",
            )
            await self._emit(tick)
            await asyncio.sleep(random.uniform(0.05, 0.15))

    async def _live_loop(self) -> None:
        """
        Poll the existing BinanceFeed for price updates.
        The v1 BinanceFeed runs in its own thread — we poll it async.
        """
        while self._running:
            price = self._binance_feed.get_price()
            if price is not None:
                tick = TickEvent(
                    symbol=self._binance_feed.symbol,
                    price=price,
                    exchange="LIVE",
                )
                await self._emit(tick)
            await asyncio.sleep(0.5)

    async def _emit(self, tick: TickEvent) -> None:
        self._tick_buffer.append(tick)
        self._latest = tick
        self._tick_count += 1
        await self.bus.publish(tick)
