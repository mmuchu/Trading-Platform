import os
base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "services", "v3")

open(os.path.join(base, "__init__.py"), "w").write("# v3 service layer\n")
print("[OK] __init__.py")

# strategy.py
open(os.path.join(base, "strategy.py"), "w").write('''\"\"\"V3 Strategy Service.\"\"\" 
from __future__ import annotations
import logging, time
from abc import ABC, abstractmethod
from collections import deque
from typing import Any, Deque, Dict, List, Optional
import numpy as np
from core.v3.event_bus import EventBus
from core.v3.models import BaseEvent, SignalEvent, SignalSource, Side, TickEvent
logger = logging.getLogger(__name__)

class BaseStrategy(ABC):
    name: str = ""
    @abstractmethod
    def evaluate(self, tick: TickEvent, history: Deque[TickEvent]) -> Optional[SignalEvent]: ...

class MomentumV3(BaseStrategy):
    name = "MomentumV3"
    def __init__(self, lookback=20, buy_threshold_pct=0.001, sell_threshold_pct=-0.001):
        self.lookback, self.buy_threshold_pct, self.sell_threshold_pct = lookback, buy_threshold_pct, sell_threshold_pct
    def evaluate(self, tick, history):
        if len(history) < self.lookback: return None
        prices = np.array([t.price for t in history], dtype=np.float64)
        pct = (prices[-1] - prices[-self.lookback]) / prices[-self.lookback]
        side = Side.BUY if pct > self.buy_threshold_pct else (Side.SELL if pct < self.sell_threshold_pct else None)
        if side is None: return None
        return SignalEvent(symbol=tick.symbol, side=side, price=tick.price, strength=round(min(abs(pct)/0.005, 1.0), 4), source=SignalSource.RULE_ENGINE, metadata={"strategy": self.name, "pct_change": round(pct, 6)})

class MeanReversionV3(BaseStrategy):
    name = "MeanReversionV3"
    def __init__(self, lookback=50, entry_z=2.0):
        self.lookback, self.entry_z = lookback, entry_z
    def evaluate(self, tick, history):
        if len(history) < self.lookback: return None
        prices = np.array([t.price for t in history], dtype=np.float64)
        mean, std = np.mean(prices), np.std(prices)
        if std == 0: return None
        z = (tick.price - mean) / std
        side = Side.BUY if z < -self.entry_z else (Side.SELL if z > self.entry_z else None)
        if side is None: return None
        return SignalEvent(symbol=tick.symbol, side=side, price=tick.price, strength=round(min(abs(z)/(self.entry_z*2), 1.0), 4), source=SignalSource.RULE_ENGINE, metadata={"strategy": self.name, "z_score": round(z, 4)})

class V3StrategyService:
    def __init__(self, bus: EventBus, cooldown: float = 1.0):
        self.bus, self.cooldown = bus, cooldown
        self.strategies = [MomentumV3(), MeanReversionV3()]
        self._history: Dict[str, Deque[TickEvent]] = {}
        self._last_signal_time = 0.0
        self._signals_emitted = 0
        self._tick_count = 0
    @property
    def stats(self): return {"tick_count": self._tick_count, "signal_count": self._signals_emitted, "strategies": [s.name for s in self.strategies]}
    async def start(self): logger.info("V3StrategyService started with %d strategies", len(self.strategies))
    async def stop(self): logger.info("V3StrategyService stopped")
    async def handle_tick(self, event):
        if not isinstance(event, TickEvent): return
        self._tick_count += 1
        history = self._history.setdefault(event.symbol, deque(maxlen=500))
        history.append(event)
        now = time.time()
        if now - self._last_signal_time < self.cooldown: return
        for strategy in self.strategies:
            signal = strategy.evaluate(event, history)
            if signal is not None:
                await self.bus.publish(signal)
                self._last_signal_time = now
                self._signals_emitted += 1
                logger.info("Signal: %s %s @ %.2f str=%.2f [%s]", signal.side.value, signal.symbol, signal.price, signal.strength, strategy.name)
                return
''')
print("[OK] strategy.py")

# analytics.py
open(os.path.join(base, "analytics.py"), "w").write('''\"\"\"V3 Analytics Service.\"\"\"
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
''')
print("[OK] analytics.py")
print("DONE - now run: python main.py --mode v3")