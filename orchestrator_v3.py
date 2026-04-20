"""
V3 Orchestrator — Lifecycle management for the hybrid architecture.
Wires the event bus, starts/stops all services, handles graceful shutdown.

v3.1: Integrated 5-gate risk guard into the pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import signal as _signal

from core.v3.event_bus import EventBus
from core.v3.models import EventType
from services.v3.market_data import V3MarketDataService
from services.v3.strategy import V3StrategyService
from services.v3.execution import V3ExecutionService
from services.v3.analytics import V3AnalyticsService
from risk_guard.guard import PortfolioRiskGuard

logger = logging.getLogger(__name__)


class V3Orchestrator:

    def __init__(self, mode: str = "sim") -> None:
        self.mode = mode
        self.bus = EventBus()
        self.market_data = V3MarketDataService(self.bus, mode=mode)
        self.strategy = V3StrategyService(self.bus)
        self.execution = V3ExecutionService(self.bus)
        self.analytics = V3AnalyticsService(self.bus)
        self.analytics.set_execution(self.execution)
        self.risk_guard = PortfolioRiskGuard(
            bus=self.bus,
            execution_service=self.execution,
        )
        self.execution.set_risk_guard(self.risk_guard)
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        self._running = True
        logger.info("=" * 55)
        logger.info("  TRADING PLATFORM v3.1 — Hybrid + Risk Guard")
        logger.info("  Mode: %s", self.mode)
        logger.info("=" * 55)
        await self.risk_guard.start()
        self.bus.subscribe(EventType.TICK, self.strategy.handle_tick)
        self.bus.subscribe(EventType.TICK, self.analytics.handle_tick)
        self.bus.subscribe(EventType.SIGNAL, self.execution.handle_signal)
        self.bus.subscribe(EventType.FILL, self.analytics.handle_fill)
        logger.info("Event bus wired: %d handlers", self.bus.handler_count)
        await self.analytics.start()
        await self.market_data.start()
        md_task = self.market_data._task
        if md_task:
            self._tasks.append(md_task)
        def _shutdown(sig, frame):
            logger.info("Signal %s received", sig)
            asyncio.create_task(self.shutdown())
        try:
            _signal.signal(_signal.SIGINT, _shutdown)
            _signal.signal(_signal.SIGTERM, _shutdown)
        except (ValueError, OSError):
            pass  # signal not available in non-main thread
        logger.info("V3.1 system operational — 5-gate risk guard active")
        logger.info("  Gates: Feed -> Signal -> Risk -> Position -> Cooldown")
        logger.info("  SL/TP: monitoring active")
        logger.info("Waiting for ticks...")
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        if not self._running:
            return
        self._running = False
        logger.info("V3.1 shutting down...")
        await self.risk_guard.stop()
        await self.market_data.stop()
        await self.analytics.stop()
        for t in self._tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        rg = self.risk_guard.stats
        logger.info(
            "V3.1 stopped | ticks=%d signals=%d fills=%d equity=%.2f "
            "| risk: %d approved, %d rejected",
            self.market_data.tick_count,
            self.strategy.stats["signal_count"],
            self.execution.stats["fills_total"],
            self.execution.equity,
            rg["total_approved"],
            rg["total_rejected"],
        )

    @property
    def system_status(self) -> dict:
        return {
            "running": self._running,
            "architecture": "v3.1_risk_guard",
            "mode": self.mode,
            "market_data": {
                "mode": self.market_data.mode,
                "tick_count": self.market_data.tick_count,
                "latest_price": self.market_data.latest_tick.price if self.market_data.latest_tick else None,
            },
            "strategy": self.strategy.stats,
            "execution": self.execution.stats,
            "analytics": self.analytics.performance_summary,
            "risk_guard": self.risk_guard.get_system_status(),
            "event_bus": {"handler_count": self.bus.handler_count},
        }