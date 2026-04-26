"""Regression tests for v3 orchestrator event-bus wiring."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.v3.models import EventType, FillEvent, Side
from orchestrator_v3 import V3Orchestrator
import orchestrator_v3 as orchestrator_module


def test_single_fill_creates_single_trade_log_entry(monkeypatch):
    async def scenario():
        orch = V3Orchestrator(mode="sim")
        release = asyncio.Event()

        async def fake_market_start():
            orch.market_data._task = asyncio.create_task(release.wait())

        async def fake_market_stop():
            if orch.market_data._task and not orch.market_data._task.done():
                orch.market_data._task.cancel()
                await asyncio.gather(orch.market_data._task, return_exceptions=True)
            orch.market_data._task = None

        monkeypatch.setattr(orch.market_data, "start", fake_market_start)
        monkeypatch.setattr(orch.market_data, "stop", fake_market_stop)
        monkeypatch.setattr(
            orchestrator_module._signal,
            "signal",
            lambda *args, **kwargs: None,
        )

        task = asyncio.create_task(orch.start())
        try:
            for _ in range(50):
                if orch.bus.handler_count > 0:
                    break
                await asyncio.sleep(0.01)

            await orch.bus.publish(
                FillEvent(
                    fill_id="fill-1",
                    order_id="ord-1",
                    symbol="BTCUSDT",
                    side=Side.SELL,
                    quantity=0.5,
                    price=78000.0,
                    commission=7.8,
                )
            )

            fill_handlers = orch.bus._handlers.get(EventType.FILL, [])
            analytics_fill_handlers = [
                handler
                for handler in fill_handlers
                if getattr(handler, "__self__", None) is orch.analytics
                and getattr(handler, "__func__", None)
                is orch.analytics.handle_fill.__func__
            ]

            assert len(analytics_fill_handlers) == 1
            assert len(orch.analytics.trade_log) == 1
        finally:
            release.set()
            await orch.shutdown()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
