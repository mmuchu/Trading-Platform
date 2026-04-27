"""
v3 Analytics Service
=====================
Consumes fills and ticks, computes PnL, equity curve, pushes
snapshots to dashboard WebSocket clients.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

from core.v3.event_bus import EventBus
from core.v3.models import BaseEvent, EventType, FillEvent, PnLSnapshot, TickEvent

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    fill_id: str; timestamp: float; symbol: str
    side: str; quantity: float; price: float; commission: float


@dataclass
class EquityCurve:
    timestamps: Deque[float] = field(default_factory=lambda: deque(maxlen=10_000))
    values: Deque[float] = field(default_factory=lambda: deque(maxlen=10_000))
    def append(self, ts: float, val: float) -> None:
        self.timestamps.append(ts); self.values.append(val)
    def to_list(self) -> List[dict]:
        return [{"timestamp": t, "equity": v} for t, v in zip(self.timestamps, self.values)]


class V3AnalyticsService:
    """
    Computes real-time analytics and feeds the dashboard.
    """

    def __init__(self, bus: EventBus, snapshot_interval: float = 2.0) -> None:
        self.bus = bus
        self.snapshot_interval = snapshot_interval
        self._trades: List[TradeRecord] = []
        self._equity_curve = EquityCurve()
        self._initial_equity: Optional[float] = None
        self._peak_equity: float = 0.0
        self._max_drawdown: float = 0.0
        self._prices: Dict[str, float] = {}
        self._dashboard_queues: list[asyncio.Queue] = []
        self._running = False
        self._execution = None

    def set_execution(self, execution_service) -> None:
        self._execution = execution_service

    async def handle_fill(self, event: BaseEvent) -> None:
        if not isinstance(event, FillEvent):
            return
        self._trades.append(TradeRecord(
            fill_id=event.fill_id, timestamp=event.timestamp,
            symbol=event.symbol, side=event.side.value,
            quantity=event.quantity, price=event.price, commission=event.commission,
        ))

    async def handle_tick(self, event: BaseEvent) -> None:
        if isinstance(event, TickEvent):
            self._prices[event.symbol] = event.price

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._snapshot_loop())

    async def stop(self) -> None:
        self._running = False

    async def _snapshot_loop(self) -> None:
        while self._running:
            try:
                snap = self._compute_snapshot()
                if snap:
                    await self.bus.publish(snap)
                    await self._push_to_dashboards(snap)
            except Exception:
                logger.exception("Snapshot error")
            await asyncio.sleep(self.snapshot_interval)

    def _compute_snapshot(self) -> Optional[PnLSnapshot]:
        if not self._execution:
            return None
        eq = self._execution.equity
        realized = self._execution.total_realized_pnl
        now = time.time()
        if self._initial_equity is None:
            self._initial_equity = eq
        self._equity_curve.append(now, eq)
        if eq > self._peak_equity:
            self._peak_equity = eq
        dd = (self._peak_equity - eq) / self._peak_equity if self._peak_equity else 0
        if dd > self._max_drawdown:
            self._max_drawdown = dd

        total_unrealized = 0.0
        for sym, pos in self._execution._positions.items():
            if pos.quantity != 0 and sym in self._prices:
                total_unrealized += (self._prices[sym] - pos.avg_entry) * pos.quantity

        primary = list(self._execution._positions.keys())[0] if self._execution._positions else ""
        pos = self._execution.get_position(primary)

        return PnLSnapshot(
            symbol=primary, position=pos.quantity, avg_entry=pos.avg_entry,
            unrealized_pnl=round(total_unrealized, 2),
            realized_pnl=round(realized, 2),
            total_pnl=round(realized + total_unrealized, 2),
            equity=round(eq, 2),
        )

    def subscribe_dashboard(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._dashboard_queues.append(q)
        return q

    def unsubscribe_dashboard(self, q: asyncio.Queue) -> None:
        self._dashboard_queues = [sq for sq in self._dashboard_queues if sq is not q]

    async def _push_to_dashboards(self, snapshot: PnLSnapshot) -> None:
        payload = snapshot.to_dict()
        stale = []
        for q in self._dashboard_queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                stale.append(q)
        for q in stale:
            self.unsubscribe_dashboard(q)

    @property
    def trade_log(self) -> List[dict]:
        return [{"fill_id": t.fill_id, "timestamp": t.timestamp, "symbol": t.symbol,
                 "side": t.side, "quantity": t.quantity, "price": t.price,
                 "commission": t.commission} for t in self._trades]

    @property
    def equity_curve_data(self) -> List[dict]:
        return self._equity_curve.to_list()

    @property
    def performance_summary(self) -> dict:
        total_ret = 0.0
        if self._initial_equity and self._initial_equity > 0:
            cur = self._equity_curve.values[-1] if self._equity_curve.values else self._initial_equity
            total_ret = (cur - self._initial_equity) / self._initial_equity * 100
        return {"total_trades": len(self._trades), "total_return_pct": round(total_ret, 4),
                "max_drawdown_pct": round(self._max_drawdown * 100, 4),
                "peak_equity": round(self._peak_equity, 2),
                "current_equity": round(
                    self._equity_curve.values[-1] if self._equity_curve.values else 0, 2)}
