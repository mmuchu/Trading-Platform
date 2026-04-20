"""V3 Analytics Service."""
from __future__ import annotations
import asyncio, logging, time
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from core.v3.event_bus import EventBus
from core.v3.models import BaseEvent, FillEvent, PnLSnapshot, TickEvent
logger = logging.getLogger(__name__)

@dataclass
class TradeRecord:
    fill_id: str; order_id: str; symbol: str; side: str; quantity: float; price: float; commission: float; timestamp: float

class V3AnalyticsService:
    def __init__(self, bus: EventBus, snapshot_interval: float = 2.0):
        self.bus = bus; self.snapshot_interval = snapshot_interval
        self._execution = None; self._latest_prices = {}; self._trade_log = []
        self._equity_curve = deque(maxlen=10000); self._max_equity = 0.0; self._max_drawdown = 0.0; self._max_drawdown_pct = 0.0
        self._task = None; self._running = False; self._dashboard_queues = []
    def set_execution(self, svc): self._execution = svc
    async def start(self):
        self._running = True; self._task = asyncio.create_task(self._loop(), name="analytics"); logger.info("Analytics started")
    async def stop(self):
        self._running = False
        if self._task: self._task.cancel()
        logger.info("Analytics stopped")
    async def handle_fill(self, event):
        if not isinstance(event, FillEvent): return
        self._trade_log.append(TradeRecord(event.fill_id, event.order_id, event.symbol, event.side.value, event.quantity, event.price, event.commission, event.timestamp))
    async def handle_tick(self, event):
        if isinstance(event, TickEvent): self._latest_prices[event.symbol] = event.price
    async def _loop(self):
        while self._running:
            try:
                snap = self._snapshot()
                if snap:
                    await self.bus.publish(snap); self._update_dd(snap.equity)
                    for q in self._dashboard_queues:
                        try: q.put_nowait(snap.to_dict())
                        except asyncio.QueueFull: pass
            except: logger.exception("Snapshot error")
            await asyncio.sleep(self.snapshot_interval)
    def _snapshot(self):
        if not self._execution: return None
        s = self._execution.stats; cash = s["cash"]; tu = 0.0; tr = s.get("total_realized_pnl", 0.0); tp = 0.0; ae = 0.0; eq = cash + tr
        for sym, p in s.get("positions", {}).items():
            qty, entry = p["quantity"], p["avg_entry"]; cp = self._latest_prices.get(sym, entry)
            u = (cp - entry) * qty if qty > 0 else ((entry - cp) * abs(qty) if qty < 0 else 0.0)
            tu += u; tp += qty
            if abs(qty) > 0: ae = (ae*(tp-qty)+entry*qty)/tp if tp != 0 else entry
        eq += tu
        return PnLSnapshot(symbol=next(iter(s.get("positions",{})),""), position=tp, avg_entry=round(ae,2), unrealized_pnl=round(tu,2), realized_pnl=round(tr,2), total_pnl=round(tr+tu,2), equity=round(eq,2))
    def _update_dd(self, eq):
        self._equity_curve.append(eq)
        if eq > self._max_equity: self._max_equity = eq
        if self._max_equity > 0:
            dd = self._max_equity - eq
            if dd > self._max_drawdown: self._max_drawdown = dd; self._max_drawdown_pct = dd / self._max_equity
    def subscribe_dashboard(self): q = asyncio.Queue(maxsize=200); self._dashboard_queues.append(q); return q
    def unsubscribe_dashboard(self, q): self._dashboard_queues = [x for x in self._dashboard_queues if x is not q]
    @property
    def trade_log(self): return [{"fill_id":r.fill_id,"timestamp":r.timestamp,"symbol":r.symbol,"side":r.side,"quantity":r.quantity,"price":r.price,"commission":r.commission} for r in self._trade_log]
    @property
    def equity_curve_data(self): return list(self._equity_curve)
    @property
    def performance_summary(self):
        tf = len(self._trade_log); tc = sum(r.commission for r in self._trade_log)
        wins = losses = 0; bp = []
        for r in self._trade_log:
            if r.side == "BUY": bp.append(r.price)
            elif r.side == "SELL" and bp:
                if r.price > sum(bp)/len(bp): wins += 1
                else: losses += 1
                bp.clear()
        rt = wins + losses; wr = wins/rt if rt > 0 else 0.0
        return {"total_trades":tf,"buy_trades":len([r for r in self._trade_log if r.side=="BUY"]),"sell_trades":len([r for r in self._trade_log if r.side=="SELL"]),"total_commission":round(tc,6),"max_drawdown":round(self._max_drawdown,2),"max_drawdown_pct":round(self._max_drawdown_pct,4),"equity_curve_len":len(self._equity_curve),"latest_equity":self._equity_curve[-1] if self._equity_curve else 0.0,"peak_equity":round(self._max_equity,2),"win_rate":round(wr,4),"wins":wins,"losses":losses,"round_trips":rt}
