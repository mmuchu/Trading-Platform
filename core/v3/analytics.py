"""V3 Analytics Service — tracks fills & ticks, computes periodic PnL
snapshots, maintains equity curve, drawdown, and pushes updates to
connected dashboards via WebSocket."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Set

from core.v3.event_bus import EventBus
from core.v3.models import (
    BaseEvent,
    FillEvent,
    PnLSnapshot,
    TickEvent,
)

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """Immutable record of a single fill for the trade log."""

    fill_id: str
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    commission: float
    timestamp: float


class V3AnalyticsService:
    """Periodically computes PnL snapshots, tracks the equity curve, and
    optionally streams snapshots to connected WebSocket dashboards."""

    def __init__(self, bus: EventBus, snapshot_interval: float = 2.0) -> None:
        self.bus = bus
        self.snapshot_interval = snapshot_interval

        self._execution: Optional[Any] = None

        self._latest_prices: Dict[str, float] = {}
        self._trade_log: List[TradeRecord] = []

        self._equity_curve: Deque[float] = deque(maxlen=10_000)
        self._max_equity: float = 0.0
        self._max_drawdown: float = 0.0
        self._max_drawdown_pct: float = 0.0

        self._task: Optional[asyncio.Task] = None
        self._running: bool = False

        self._dashboard_queues: List[asyncio.Queue] = []

    def set_execution(self, execution_service: Any) -> None:
        self._execution = execution_service

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(
            self._snapshot_loop(), name="analytics_snapshot",
        )
        logger.info("V3AnalyticsService started (interval=%.1fs)", self.snapshot_interval)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("V3AnalyticsService stopped")

    async def handle_fill(self, event: BaseEvent) -> None:
        fill = event
        if not isinstance(fill, FillEvent):
            return

        record = TradeRecord(
            fill_id=fill.fill_id,
            order_id=fill.order_id,
            symbol=fill.symbol,
            side=fill.side.value,
            quantity=fill.quantity,
            price=fill.price,
            commission=fill.commission,
            timestamp=fill.timestamp,
        )
        self._trade_log.append(record)
        logger.debug(
            "Analytics: recorded fill %s %s qty=%.4f @ %.2f",
            fill.side.value, fill.symbol, fill.quantity, fill.price,
        )

    async def handle_tick(self, event: BaseEvent) -> None:
        tick = event
        if not isinstance(tick, TickEvent):
            return
        self._latest_prices[tick.symbol] = tick.price

    async def _snapshot_loop(self) -> None:
        while self._running:
            try:
                snapshot = self._compute_snapshot()
                if snapshot is not None:
                    await self.bus.publish(snapshot)
                    self._update_drawdown(snapshot.equity)
                    await self._push_to_dashboards(snapshot.to_dict())
            except Exception:
                logger.exception("Error computing PnL snapshot")

            await asyncio.sleep(self.snapshot_interval)

    def _compute_snapshot(self) -> Optional[PnLSnapshot]:
        if self._execution is None:
            return None

        stats = self._execution.stats
        cash = stats["cash"]

        total_unrealized = 0.0
        total_realized = stats.get("total_realized_pnl", 0.0)
        total_position = 0.0
        avg_entry = 0.0
        equity = cash + total_realized

        for symbol, pos_info in stats.get("positions", {}).items():
            qty = pos_info["quantity"]
            entry = pos_info["avg_entry"]
            current_price = self._latest_prices.get(symbol, entry)

            if qty > 0:
                unrealized = (current_price - entry) * qty
            elif qty < 0:
                unrealized = (entry - current_price) * abs(qty)
            else:
                unrealized = 0.0

            total_unrealized += unrealized
            total_position += qty
            if abs(qty) > 0:
                avg_entry = (avg_entry * (total_position - qty) + entry * qty) / total_position if total_position != 0 else entry

        equity += total_unrealized
        total_pnl = total_realized + total_unrealized

        symbol = next(iter(stats.get("positions", {})), "")

        return PnLSnapshot(
            symbol=symbol,
            position=total_position,
            avg_entry=round(avg_entry, 2),
            unrealized_pnl=round(total_unrealized, 2),
            realized_pnl=round(total_realized, 2),
            total_pnl=round(total_pnl, 2),
            equity=round(equity, 2),
        )

    def _update_drawdown(self, equity: float) -> None:
        self._equity_curve.append(equity)

        if equity > self._max_equity:
            self._max_equity = equity

        if self._max_equity > 0:
            dd = self._max_equity - equity
            if dd > self._max_drawdown:
                self._max_drawdown = dd
                self._max_drawdown_pct = dd / self._max_equity

    def subscribe_dashboard(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._dashboard_queues.append(q)
        return q

    def unsubscribe_dashboard(self, q: asyncio.Queue) -> None:
        self._dashboard_queues = [sq for sq in self._dashboard_queues if sq is not q]

    async def _push_to_dashboards(self, snapshot_dict: Dict[str, Any]) -> None:
        stale = []
        for q in self._dashboard_queues:
            try:
                q.put_nowait(snapshot_dict)
            except asyncio.QueueFull:
                stale.append(q)
        for q in stale:
            self.unsubscribe_dashboard(q)

    @property
    def trade_log(self) -> List[dict]:
        return [{"fill_id": r.fill_id, "timestamp": r.timestamp, "symbol": r.symbol,
                 "side": r.side, "quantity": r.quantity, "price": r.price,
                 "commission": r.commission} for r in self._trade_log]

    @property
    def equity_curve_data(self) -> List[float]:
        return list(self._equity_curve)

    @property
    def performance_summary(self) -> Dict[str, Any]:
        total_fills = len(self._trade_log)
        total_commission = sum(r.commission for r in self._trade_log)

        buys = [r for r in self._trade_log if r.side == "BUY"]
        sells = [r for r in self._trade_log if r.side == "SELL"]

        wins = 0
        losses = 0
        buy_prices: List[float] = []
        for record in self._trade_log:
            if record.side == "BUY":
                buy_prices.append(record.price)
            elif record.side == "SELL" and buy_prices:
                avg_buy = sum(buy_prices) / len(buy_prices)
                if record.price > avg_buy:
                    wins += 1
                else:
                    losses += 1
                buy_prices.clear()

        total_round_trips = wins + losses
        win_rate = wins / total_round_trips if total_round_trips > 0 else 0.0

        return {
            "total_trades": total_fills,
            "buy_trades": len(buys),
            "sell_trades": len(sells),
            "total_commission": round(total_commission, 6),
            "max_drawdown": round(self._max_drawdown, 2),
            "max_drawdown_pct": round(self._max_drawdown_pct, 4),
            "equity_curve_len": len(self._equity_curve),
            "latest_equity": self._equity_curve[-1] if self._equity_curve else 0.0,
            "peak_equity": round(self._max_equity, 2),
            "win_rate": round(win_rate, 4),
            "wins": wins,
            "losses": losses,
            "round_trips": total_round_trips,
        }