"""Quant Trading System v3.0"""
from __future__ import annotations

import asyncio, json, logging, os, random, signal, sqlite3, sys, time, uuid
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Deque

# ══════════════════════════════════════════════════════════════
#  MODELS
# ══════════════════════════════════════════════════════════════

class EventType(str, Enum):
    TICK = "TICK"; SIGNAL = "SIGNAL"; ORDER = "ORDER"; FILL = "FILL"
    RISK_REJECTED = "RISK_REJECTED"; PNL_SNAPSHOT = "PNL_SNAPSHOT"
    SYSTEM = "SYSTEM"; ERROR = "ERROR"

class Side(str, Enum):
    BUY = "BUY"; SELL = "SELL"

class SignalSource(str, Enum):
    ML_MODEL = "ML_MODEL"; RL_AGENT = "RL_AGENT"; RULE_ENGINE = "RULE_ENGINE"; MANUAL = "MANUAL"

@dataclass
class BaseEvent:
    type: EventType = EventType.SYSTEM
    timestamp: float = field(default_factory=time.time)
    source: str = ""
    def to_dict(self) -> dict[str, Any]:
        import dataclasses
        d = dataclasses.asdict(self)
        for k, v in d.items():
            if isinstance(v, Enum): d[k] = v.value
        return d

@dataclass
class TickEvent(BaseEvent):
    symbol: str = ""; price: float = 0.0; volume: int = 0
    bid: float = 0.0; ask: float = 0.0; exchange: str = ""
    def __post_init__(self):
        self.type = EventType.TICK; self.source = "market_data"

@dataclass
class SignalEvent(BaseEvent):
    symbol: str = ""; side: Side = Side.BUY; price: float = 0.0
    strength: float = 1.0; source: SignalSource = SignalSource.RULE_ENGINE
    metadata: dict = field(default_factory=dict)
    def __post_init__(self):
        self.type = EventType.SIGNAL
        self.source = self.source.value if isinstance(self.source, SignalSource) else self.source

@dataclass
class OrderEvent(BaseEvent):
    order_id: str = ""; symbol: str = ""; side: Side = Side.BUY
    quantity: int = 1; price: Optional[float] = None; strategy_id: str = ""
    def __post_init__(self):
        self.type = EventType.ORDER; self.source = "execution"

@dataclass
class FillEvent(BaseEvent):
    fill_id: str = ""; order_id: str = ""; symbol: str = ""
    side: Side = Side.BUY; quantity: int = 1; price: float = 0.0; commission: float = 0.0
    def __post_init__(self):
        self.type = EventType.FILL; self.source = "execution"

@dataclass
class RiskRejectedEvent(BaseEvent):
    order_id: str = ""; reason: str = ""
    def __post_init__(self):
        self.type = EventType.RISK_REJECTED; self.source = "execution"

@dataclass
class PnLSnapshot(BaseEvent):
    symbol: str = ""; position: int = 0; avg_entry: float = 0.0
    unrealized_pnl: float = 0.0; realized_pnl: float = 0.0
    total_pnl: float = 0.0; equity: float = 0.0
    def __post_init__(self):
        self.type = EventType.PNL_SNAPSHOT; self.source = "analytics"

# ══════════════════════════════════════════════════════════════
#  EVENT BUS
# ══════════════════════════════════════════════════════════════

EventHandler = Callable[[BaseEvent], Awaitable[None]]

class EventBus:
    def __init__(self):
        self._handlers: Dict[EventType, List[EventHandler]] = defaultdict(list)
        self._wildcards: List[EventHandler] = []

    def subscribe(self, topic, handler):
        if topic is None:
            self._wildcards.append(handler)
        else:
            etype = EventType(topic) if isinstance(topic, str) else topic
            self._handlers[etype].append(handler)

    async def publish(self, event: BaseEvent):
        topic = EventType(event.type) if isinstance(event.type, str) else event.type
        handlers = list(self._handlers.get(topic, [])) + list(self._wildcards)
        if not handlers: return
        tasks = [self._safe(h, event) for h in handlers]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe(self, handler, event):
        try: await handler(event)
        except: pass

    @property
    def handler_count(self):
        return len(self._wildcards) + sum(len(v) for v in self._handlers.values())

    def topics(self):
        return set(self._handlers.keys())

# ══════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════

@dataclass
class Config:
    symbols: List[str] = field(default_factory=lambda: ["BTCUSDT"])
    ws_url: str = "wss://stream.binance.com:9443/ws/btcusdt@trade"
    initial_cash: float = 100_000.0
    max_position: int = 10
    max_order_value: float = 500_000.0
    commission: float = 0.001
    slippage_bps: int = 5
    strategies: List[str] = field(default_factory=lambda: ["momentum", "mean_reversion"])
    signal_cooldown: float = 1.0
    dash_host: str = "0.0.0.0"
    dash_port: int = 8001
    db_path: str = "data/trading_v3.db"
    log_level: str = "INFO"

# ══════════════════════════════════════════════════════════════
#  MARKET DATA SERVICE
# ══════════════════════════════════════════════════════════════

class MarketDataService:
    def __init__(self, bus: EventBus, config: Config, mode: str = "sim"):
        self.bus = bus; self.config = config; self.mode = mode
        self._buffer: deque[TickEvent] = deque(maxlen=10000)
        self._latest = None; self._sim_price = 65000.0; self._running = False; self._count = 0

    @property
    def latest_tick(self): return self._latest
    @property
    def tick_count(self): return self._count
    def recent_ticks(self, n=100): return list(self._buffer)[-n:]

    async def start(self):
        self._running = True
        logging.info("MarketDataService v3 starting (mode=%s)", self.mode)
        if self.mode == "live":
            await self._live_loop()
        else:
            await self._sim_loop()

    async def stop(self):
        self._running = False
        logging.info("MarketDataService stopped (%d ticks)", self._count)

    async def _sim_loop(self):
        while self._running:
            try:
                drift = random.gauss(0, 2.5)
                revert = 0.001 * (65000.0 - self._sim_price)
                self._sim_price = max(1.0, self._sim_price + drift + revert)
                spread = random.uniform(0.5, 2.0)
                tick = TickEvent(
                    symbol=self.config.symbols[0],
                    price=round(self._sim_price, 2), volume=random.randint(1, 500),
                    bid=round(self._sim_price - spread/2, 2),
                    ask=round(self._sim_price + spread/2, 2), exchange="SIM")
                self._buffer.append(tick); self._latest = tick; self._count += 1
                await self.bus.publish(tick)
                await asyncio.sleep(random.uniform(0.05, 0.3))
            except asyncio.CancelledError: break
            except: await asyncio.sleep(0.5)

    async def _live_loop(self):
        try:
            import websockets
            attempt = 0
            while self._running and attempt < 50:
                try:
                    async with websockets.connect(self.config.ws_url) as ws:
                        attempt = 0
                        async for raw in ws:
                            if not self._running: break
                            try:
                                d = json.loads(raw)
                                tick = TickEvent(symbol=d.get("s",""), price=float(d.get("p",0)),
                                    volume=int(float(d.get("q",0))), exchange="LIVE")
                                self._buffer.append(tick); self._latest = tick; self._count += 1
                                await self.bus.publish(tick)
                            except: pass
                except Exception as e:
                    attempt += 1
                    logging.warning("WS error: %s, retry %d", e, attempt)
                    await asyncio.sleep(min(2**attempt, 60))
        except ImportError:
            logging.error("websockets not installed. Run: pip install websockets")

# ══════════════════════════════════════════════════════════════
#  STRATEGY SERVICE
# ══════════════════════════════════════════════════════════════

class BaseStrategy(ABC):
    name = "base"
    @abstractmethod
    async def evaluate(self, tick: TickEvent, history: deque) -> Optional[SignalEvent]: pass

class MomentumStrategy(BaseStrategy):
    name = "momentum"
    def __init__(self, lookback=20, threshold=0.05):
        self.lookback = lookback; self.threshold = threshold
    async def evaluate(self, tick, history):
        if len(history) < self.lookback: return None
        import numpy as np
        prices = np.array([t.price for t in list(history)[-self.lookback:]])
        pct = (tick.price - prices[0]) / prices[0] * 100
        if pct > self.threshold:
            return SignalEvent(symbol=tick.symbol, side=Side.BUY, price=tick.price,
                strength=min(abs(pct), 1.0), source=SignalSource.RULE_ENGINE,
                metadata={"strategy": self.name, "pct": round(pct, 4)})
        elif pct < -self.threshold:
            return SignalEvent(symbol=tick.symbol, side=Side.SELL, price=tick.price,
                strength=min(abs(pct), 1.0), source=SignalSource.RULE_ENGINE,
                metadata={"strategy": self.name, "pct": round(pct, 4)})
        return None

class MeanReversionStrategy(BaseStrategy):
    name = "mean_reversion"
    def __init__(self, window=30, std_above=2.0, std_below=2.0):
        self.window = window; self.std_above = std_above; self.std_below = std_below
    async def evaluate(self, tick, history):
        if len(history) < self.window: return None
        import numpy as np
        prices = np.array([t.price for t in list(history)[-self.window:]])
        sma, std = np.mean(prices), np.std(prices)
        if std == 0: return None
        z = (tick.price - sma) / std
        if z > self.std_above:
            return SignalEvent(symbol=tick.symbol, side=Side.SELL, price=tick.price,
                strength=min(z/4, 1.0), source=SignalSource.RULE_ENGINE,
                metadata={"strategy": self.name, "z": round(z, 3)})
        elif z < -self.std_below:
            return SignalEvent(symbol=tick.symbol, side=Side.BUY, price=tick.price,
                strength=min(abs(z)/4, 1.0), source=SignalSource.RULE_ENGINE,
                metadata={"strategy": self.name, "z": round(z, 3)})
        return None

class StrategyService:
    def __init__(self, bus: EventBus, config: Config):
        self.bus = bus; self.config = config
        self._strategies = {s.name: s for s in [MomentumStrategy(), MeanReversionStrategy()]}
        self._history: Dict[str, deque] = {}; self._last_sig: Dict[str, float] = {}
        self._signals = 0; self._ticks = 0

    async def handle_tick(self, event: BaseEvent):
        if not isinstance(event, TickEvent): return
        self._ticks += 1; tick = event
        if tick.symbol not in self._history:
            self._history[tick.symbol] = deque(maxlen=500)
        self._history[tick.symbol].append(tick)
        now = time.time()
        if now - self._last_sig.get(tick.symbol, 0) < self.config.signal_cooldown: return
        history = self._history[tick.symbol]
        for name, strat in self._strategies.items():
            if name not in self.config.strategies: continue
            try:
                sig = await strat.evaluate(tick, history)
                if sig:
                    self._signals += 1; self._last_sig[tick.symbol] = now
                    await self.bus.publish(sig)
            except: logging.exception("Strategy %s error", name)

    @property
    def stats(self):
        return {"tick_count": self._ticks, "signal_count": self._signals,
                "strategies": list(self._strategies.keys()), "enabled": self.config.strategies}

# ══════════════════════════════════════════════════════════════
#  EXECUTION SERVICE
# ══════════════════════════════════════════════════════════════

@dataclass
class PositionTracker:
    quantity: int = 0; avg_entry: float = 0.0
    realized_pnl: float = 0.0; commission: float = 0.0; trades: int = 0

class ExecutionService:
    def __init__(self, bus: EventBus, config: Config):
        self.bus = bus; self.config = config
        self._cash = config.initial_cash
        self._positions: Dict[str, PositionTracker] = {}
        self._fills: list = []; self._submitted = 0; self._rejected = 0
        self._start = time.time()

    @property
    def cash(self): return self._cash
    @property
    def equity(self): return self._cash + sum(p.quantity * p.avg_entry for p in self._positions.values())
    @property
    def total_realized_pnl(self): return sum(p.realized_pnl for p in self._positions.values())
    def get_position(self, sym):
        if sym not in self._positions: self._positions[sym] = PositionTracker()
        return self._positions[sym]

    @property
    def stats(self):
        return {"cash": round(self._cash, 2), "equity": round(self.equity, 2),
                "realized_pnl": round(self.total_realized_pnl, 2),
                "orders_submitted": self._submitted, "orders_rejected": self._rejected,
                "positions": {s: {"qty": p.quantity, "avg": round(p.avg_entry, 2), "pnl": round(p.realized_pnl, 2)}
                             for s, p in self._positions.items()},
                "uptime": round(time.time() - self._start, 1)}

    async def handle_signal(self, event: BaseEvent):
        if not isinstance(event, SignalEvent): return
        sig = event; pos = self.get_position(sig.symbol)
        # Risk check
        if pos.quantity + 1 > self.config.max_position:
            await self.bus.publish(RiskRejectedEvent(reason="Max position")); self._rejected += 1; return
        if sig.side == Side.BUY and self._cash < sig.price * 1.001:
            await self.bus.publish(RiskRejectedEvent(reason="No cash")); self._rejected += 1; return
        # Fill
        self._submitted += 1
        slip = self.config.slippage_bps / 10000
        fp = sig.price * (1 + slip) if sig.side == Side.BUY else sig.price * (1 - slip)
        comm = fp * self.config.commission
        fill = FillEvent(fill_id=uuid.uuid4().hex[:12], order_id=uuid.uuid4().hex[:12],
            symbol=sig.symbol, side=sig.side, quantity=1, price=round(fp, 2), commission=round(comm, 4))
        self._fills.append(fill)
        # Update position
        qty = 1 if fill.side == Side.BUY else -1
        if (pos.quantity > 0 and qty > 0) or (pos.quantity < 0 and qty < 0):
            total = pos.quantity + qty
            pos.avg_entry = (pos.avg_entry * abs(pos.quantity) + fill.price * abs(qty)) / abs(total)
            pos.quantity = total
        elif (pos.quantity > 0 and qty < 0) or (pos.quantity < 0 and qty > 0):
            pnl = (fill.price - pos.avg_entry) * min(abs(qty), abs(pos.quantity))
            if pos.quantity < 0: pnl = -pnl
            pos.realized_pnl += pnl; pos.quantity += qty
            if pos.quantity == 0: pos.avg_entry = 0
        else:
            pos.quantity = qty; pos.avg_entry = fill.price
        if fill.side == Side.BUY: self._cash -= fill.price + fill.commission
        else: self._cash += fill.price - fill.commission
        pos.commission += fill.commission; pos.trades += 1
        await self.bus.publish(fill)
        logging.info("FILL %s %s qty=1 @ %.2f comm=%.4f", fill.side.value, fill.symbol, fill.price, fill.commission)

# ══════════════════════════════════════════════════════════════
#  ANALYTICS SERVICE
# ══════════════════════════════════════════════════════════════

class AnalyticsService:
    def __init__(self, bus: EventBus, interval=2.0):
        self.bus = bus; self.interval = interval
        self._trades = []; self._equity_ts = deque(maxlen=10000)
        self._equity_vals = deque(maxlen=10000); self._init_eq = None
        self._peak = 0.0; self._max_dd = 0.0; self._prices = {}
        self._subs: list = []; self._running = False; self._task = None; self._exec = None

    def set_execution(self, svc): self._exec = svc

    async def handle_fill(self, event):
        if isinstance(event, FillEvent):
            self._trades.append(event)

    async def handle_tick(self, event):
        if isinstance(event, TickEvent): self._prices[event.symbol] = event.price

    async def start(self):
        self._running = True; self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._running = False
        if self._task: self._task.cancel()

    async def _loop(self):
        while self._running:
            try:
                snap = self._snapshot()
                if snap:
                    await self.bus.publish(snap)
                    for q in list(self._subs):
                        try: q.put_nowait(snap.to_dict())
                        except: self._subs.remove(q)
            except asyncio.CancelledError: break
            except: pass
            await asyncio.sleep(self.interval)

    def _snapshot(self):
        if not self._exec: return None
        eq = self._exec.equity; now = time.time()
        if not self._init_eq: self._init_eq = eq
        self._equity_ts.append(now); self._equity_vals.append(eq)
        if eq > self._peak: self._peak = eq
        dd = (self._peak - eq) / self._peak if self._peak else 0
        if dd > self._max_dd: self._max_dd = dd
        unrealized = sum((self._prices.get(s, 0) - p.avg_entry) * p.quantity
                        for s, p in self._exec._positions.items() if p.quantity != 0)
        sym = list(self._exec._positions.keys())[0] if self._exec._positions else ""
        pos = self._exec.get_position(sym)
        return PnLSnapshot(symbol=sym, position=pos.quantity, avg_entry=pos.avg_entry,
            unrealized_pnl=round(unrealized, 2), realized_pnl=round(self._exec.total_realized_pnl, 2),
            total_pnl=round(self._exec.total_realized_pnl + unrealized, 2), equity=round(eq, 2))

    def subscribe(self): q = asyncio.Queue(maxsize=200); self._subs.append(q); return q
    def unsubscribe(self, q):
        if q in self._subs: self._subs.remove(q)

    @property
    def trade_log(self): return [{"fill_id": t.fill_id, "ts": t.timestamp, "symbol": t.symbol,
        "side": t.side.value, "qty": t.quantity, "price": t.price, "comm": t.commission} for t in self._trades]

    @property
    def performance(self):
        ret = 0.0
        if self._init_eq and self._init_eq > 0:
            cur = self._equity_vals[-1] if self._equity_vals else self._init_eq
            ret = (cur - self._init_eq) / self._init_eq * 100
        return {"total_trades": len(self._trades), "return_pct": round(ret, 4),
                "max_drawdown_pct": round(self._max_dd * 100, 4), "peak": round(self._peak, 2)}

# ══════════════════════════════════════════════════════════════
#  DATABASE (SQLite)
# ══════════════════════════════════════════════════════════════

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL, symbol TEXT, price REAL, volume INTEGER);
CREATE TABLE IF NOT EXISTS fills (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL, fill_id TEXT, symbol TEXT, side TEXT, quantity INTEGER, price REAL, commission REAL);
CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL, symbol TEXT, position INTEGER, equity REAL);
"""

class Database:
    def __init__(self, path="data/trading_v3.db"):
        self.path = path; self._conn = None
    def connect(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.executescript(_SCHEMA); self._conn.commit()
    def close(self):
        if self._conn: self._conn.close()
    @property
    def conn(self): return self._conn

# ══════════════════════════════════════════════════════════════
#  ORCHESTRATOR
# ══════════════════════════════════════════════════════════════

class Orchestrator:
    def __init__(self, config=None):
        self.config = config or Config()
        self.bus = EventBus()
        self.db = Database(self.config.db_path)
        self.market_data = MarketDataService(self.bus, self.config, mode="sim")
        self.strategy = StrategyService(self.bus, self.config)
        self.execution = ExecutionService(self.bus, self.config)
        self.analytics = AnalyticsService(self.bus)
        self.analytics.set_execution(self.execution)
        self._running = False; self._tasks = []

    async def start(self):
        self._running = True
        logging.info("=" * 50)
        logging.info("QUANT TRADING SYSTEM v3.0 - Starting")
        logging.info("=" * 50)
        self.db.connect()
        self.bus.subscribe(EventType.TICK, self.strategy.handle_tick)
        self.bus.subscribe(EventType.TICK, self.analytics.handle_tick)
        self.bus.subscribe(EventType.SIGNAL, self.execution.handle_signal)
        self.bus.subscribe(EventType.FILL, self.analytics.handle_fill)
        logging.info("Event bus wired (%d handlers)", self.bus.handler_count)
        await self.analytics.start()
        task = asyncio.create_task(self.market_data.start())
        self._tasks.append(task)
        logging.info("Dashboard: http://%s:%d", self.config.dash_host, self.config.dash_port)
        logging.info("Press Ctrl+C to stop")
        try: await asyncio.gather(*self._tasks)
        except asyncio.CancelledError: pass
        finally: await self.shutdown()

    async def shutdown(self):
        if not self._running: return
        self._running = False
        await self.market_data.stop(); await self.analytics.stop()
        for t in self._tasks:
            if not t.done(): t.cancel()
        self.db.close()
        logging.info("System stopped")

    @property
    def system_status(self):
        return {"version": "3.0", "running": self._running,
            "market_data": {"mode": self.market_data.mode, "ticks": self.market_data.tick_count,
                           "price": self.market_data.latest_tick.price if self.market_data.latest_tick else None},
            "strategy": self.strategy.stats, "execution": self.execution.stats,
            "analytics": self.analytics.performance}

# ══════════════════════════════════════════════════════════════
#  FASTAPI DASHBOARD
# ══════════════════════════════════════════════════════════════

def create_dashboard(orchestrator=None):
    from fastapi import FastAPI, WebSocket
    from fastapi.responses import HTMLResponse
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(title="Trading System v3.0", version="3.0")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    HTML = HTMLResponse(content="""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>Trading v3.0</title><style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0a0e17;--card:#1a2332;--bdr:#1e3a5f;--txt:#e2e8f0;--dim:#94a3b8;--g:#10b981;--r:#ef4444;--b:#3b82f6;--y:#f59e0b}
body{font-family:'Courier New',monospace;background:var(--bg);color:var(--txt);min-height:100vh}
.h{background:#111827;border-bottom:1px solid var(--bdr);padding:14px 20px;display:flex;justify-content:space-between;align-items:center}
.h h1{font-size:16px;letter-spacing:1px}.badge{padding:3px 10px;border-radius:16px;font-size:11px;font-weight:600}
.on{background:rgba(16,185,129,.15);color:var(--g);border:1px solid var(--g)}.off{background:rgba(239,68,68,.15);color:var(--r);border:1px solid var(--r)}
.g{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;padding:18px 20px}
.c{background:var(--card);border:1px solid var(--bdr);border-radius:8px;padding:16px}
.ch{font-size:10px;text-transform:uppercase;letter-spacing:1.5px;color:var(--dim);margin-bottom:8px}
.cv{font-size:26px;font-weight:700}.cv.g{color:var(--g)}.cv.r{color:var(--r)}.cv.b{color:var(--b)}.cv.y{color:var(--y)}
.cs{font-size:11px;color:var(--dim);margin-top:3px}
.s{padding:0 20px 16px}.pg{display:grid;grid-template-columns:1fr 1fr;gap:14px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:6px 10px;font-size:10px;text-transform:uppercase;color:var(--dim);border-bottom:1px solid var(--bdr)}
td{padding:6px 10px;border-bottom:1px solid rgba(30,58,95,.3)}
.bu{color:var(--g);font-weight:600}.se{color:var(--r);font-weight:600}
.lc{background:var(--card);border:1px solid var(--bdr);border-radius:8px;max-height:350px;overflow-y:auto;font-size:11px;padding:10px}
.le{padding:1px 0}.lt{color:var(--dim)}.lk{color:var(--b)}.ls{color:var(--y)}.lf{color:var(--g)}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;animation:p 2s infinite}
.dot.on{background:var(--g)}.dot.off{background:var(--r);animation:none}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}
</style></head><body>
<div class="h"><h1>QUANT TRADING <span style="color:var(--b)">v3.0</span></h1>
<div><span class="dot off" id="d"></span><span class="badge on" id="b">INIT</span></div></div>
<div class="g">
<div class="c"><div class="ch">Equity</div><div class="cv b" id="eq">$100,000</div><div class="cs" id="ret">Return: 0%</div></div>
<div class="c"><div class="ch">Unrealized PnL</div><div class="cv g" id="upnl">$0</div><div class="cs" id="rpnl">Realized: $0</div></div>
<div class="c"><div class="ch">Position</div><div class="cv y" id="pos">0</div><div class="cs" id="avg">Entry: $0</div></div>
<div class="c"><div class="ch">Price</div><div class="cv" id="prc">$65,000</div><div class="cs" id="cnt">Ticks: 0 | Signals: 0</div></div>
</div>
<div class="s"><div class="pg">
<div class="c"><div class="ch">Trades</div><table><thead><tr><th>Time</th><th>Side</th><th>Qty</th><th>Price</th></tr></thead><tbody id="tl"></tbody></table></div>
<div class="c"><div class="ch">Events</div><div class="lc" id="el"></div></div>
</div></div>
<div class="s"><div class="g">
<div class="c"><div class="ch">Total Trades</div><div class="cv b" id="tt">0</div></div>
<div class="c"><div class="ch">Max Drawdown</div><div class="cv r" id="dd">0%</div></div>
<div class="c"><div class="ch">Orders OK</div><div class="cv y" id="ok">0</div></div>
<div class="c"><div class="ch">Rejected</div><div class="cv r" id="rj">0</div></div>
</div></div>
<script>
var $=function(id){return document.getElementById(id)};
var ws=new WebSocket("ws://"+location.host+"/ws/live");
var ML=60,trades=[];
function f(n){return(n>=0?"":"-")+"$"+Math.abs(n).toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2})}
function ft(t){var d=new Date(t*1000);return d.toLocaleTimeString("en-US",{hour12:false})}
ws.onopen=function(){$("d").className="dot on";$("b").textContent="LIVE";$("b").className="badge on"};
ws.onclose=function(){$("d").className="dot off";$("b").textContent="OFF";$("b").className="badge off"};
ws.onmessage=function(e){try{var d=JSON.parse(e.data);
if(d.type==="PNL_SNAPSHOT"){$("eq").textContent=f(d.equity);$("eq").className="cv "+(d.equity>=1e5?"g":"r");
$("upnl").textContent=f(d.unrealized_pnl);$("upnl").className="cv "+(d.unrealized_pnl>=0?"g":"r");
$("rpnl").textContent="Realized: "+f(d.realized_pnl);
var r=((d.equity-1e5)/1e5*100).toFixed(2);$("ret").textContent="Return: "+(r>=0?"+":"")+r+"%";
$("pos").textContent=d.position;$("avg").textContent="Entry: "+f(d.avg_entry);log("s","PNL eq="+d.equity+" pos="+d.position)}
if(d.type==="TICK"){$("prc").textContent=f(d.price);log("k","TICK "+d.symbol+" @ "+d.price)}
if(d.type==="FILL"){trades.unshift(d);if(trades.length>15)trades.pop();rt();log("f","FILL "+d.side+" "+d.symbol+" @ "+d.price)}
if(d.type==="SIGNAL"){log("s","SIGNAL "+d.side+" "+d.symbol+" @ "+d.price)}
if(d.type==="HEARTBEAT"){st()}
}catch(x){}};
function rt(){var t=$("tl");t.innerHTML=trades.map(function(x){return"<tr><td>"+ft(x.timestamp)+"</td><td class='"+(x.side==="BUY"?"bu":"se")+"'>"+x.side+"</td><td>"+x.quantity+"</td><td>"+f(x.price)+"</td></tr>"}).join("")}
function log(c,m){var l=$("el"),d=document.createElement("div");d.className="le";d.innerHTML="<span class='lt'>"+ft(Date.now()/1000)+"</span> <span class='l"+c+"'>"+m+"</span>";l.prepend(d);while(l.children.length>ML)l.removeChild(l.lastChild)}
function st(){fetch("/api/status").then(function(r){return r.json()}).then(function(s){if(s.market_data){$("cnt").textContent="Ticks: "+s.market_data.ticks+" | Signals: "+(s.strategy?s.strategy.signal_count:0);$("tt").textContent=s.execution?s.execution.orders_submitted:0;$("dd").textContent=(s.analytics?s.analytics.max_drawdown_pct:0).toFixed(2)+"%";$("ok").textContent=s.execution?s.execution.orders_submitted:0;$("rj").textContent=s.execution?s.execution.orders_rejected:0}}).catch(function(){})}
st();setInterval(st,5000);
</script></body></html>""")

    @app.get("/", response_class=HTMLResponse)
    async def index(): return HTML

    @app.get("/api/status")
    async def status():
        if not orchestrator: return {"error": "not init"}
        return orchestrator.system_status

    @app.get("/api/trades")
    async def trades():
        if not orchestrator: return []
        return orchestrator.analytics.trade_log

    @app.get("/api/performance")
    async def perf():
        if not orchestrator: return {}
        return orchestrator.analytics.performance

    @app.websocket("/ws/live")
    async def ws_live(websocket: WebSocket):
        await websocket.accept()
        q = orchestrator.analytics.subscribe() if orchestrator else None
        try:
            while True:
                if q:
                    try:
                        data = await asyncio.wait_for(q.get(), timeout=1.0)
                        await websocket.send_json(data)
                    except asyncio.TimeoutError:
                        await websocket.send_json({"type": "HEARTBEAT", "ts": time.time()})
                else:
                    await asyncio.sleep(1)
        except WebSocketDisconnect: pass
        finally:
            if orchestrator and q: orchestrator.analytics.unsubscribe(q)

    return app

from fastapi import WebSocket

from fastapi import WebSocketDisconnect

from fastapi import WebSocketDisconnect
