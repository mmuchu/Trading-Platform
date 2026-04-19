"""Trading Bot v1.0 - Production Baseline
Single-file: Feed + Strategy + Risk + Execution + Dashboard
"""
import asyncio, json, logging, random, time, uuid, threading, argparse
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(name)-8s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("bot")

# ── Config ──────────────────────────────────────────────
@dataclass
class Config:
    symbol: str = "BTCUSDT"
    interval: float = 5.0
    max_position: int = 3
    initial_cash: float = 100000.0
    commission: float = 0.001
    ma_period: int = 5
    host: str = "0.0.0.0"
    port: int = 8002

# ── Events ──────────────────────────────────────────────
class EventType(str, Enum):
    TICK = "TICK"; SIGNAL = "SIGNAL"; FILL = "FILL"

@dataclass
class Event:
    type: EventType; timestamp: float = 0.0; data: dict = field(default_factory=dict)
    def __post_init__(self):
        if not self.timestamp: self.timestamp = time.time()
    def to_dict(self): return {"type": self.type.value, "ts": self.timestamp, **self.data}

# ── Event Bus ───────────────────────────────────────────
class EventBus:
    def __init__(self):
        self._handlers = {e: [] for e in EventType}
    def subscribe(self, etype, handler):
        self._handlers[etype].append(handler)
    async def publish(self, event):
        for h in self._handlers.get(event.type, []):
            try: await h(event)
            except: pass

# ── Market Feed (live Binance REST) ─────────────────────
class Feed:
    def __init__(self, bus, cfg):
        self.bus = bus; self.cfg = cfg
        self.price = None; self.ticks = 0; self.running = False
    async def start(self):
        self.running = True; log.info("Feed started (%s)", self.cfg.symbol)
        while self.running:
            try:
                import requests
                r = requests.get(
                    f"https://api.binance.com/api/v3/ticker/price?symbol={self.cfg.symbol}",
                    timeout=10)
                self.price = float(r.json()["price"]); self.ticks += 1
                await self.bus.publish(Event(EventType.TICK, data={
                    "symbol": self.cfg.symbol, "price": self.price}))
                if self.ticks % 10 == 0:
                    log.info("TICK #%d  %s @ $%.2f", self.ticks, self.cfg.symbol, self.price)
            except asyncio.CancelledError: break
            except Exception as e: log.error("Feed error: %s", e)
            await asyncio.sleep(self.cfg.interval)
    async def stop(self): self.running = False

# ── Strategy (MA Crossover) ────────────────────────────
class Strategy:
    def __init__(self, bus, cfg):
        self.bus = bus; self.cfg = cfg
        self.prices = deque(maxlen=1000); self.signals = 0
    async def on_tick(self, event):
        p = event.data.get("price"); self.prices.append(p)
        if len(self.prices) < self.cfg.ma_period: return
        avg = sum(list(self.prices)[-self.cfg.ma_period:]) / self.cfg.ma_period
        if p > avg * 1.001:
            self.signals += 1
            await self.bus.publish(Event(EventType.SIGNAL, data={
                "symbol": event.data["symbol"], "side": "BUY", "price": p, "strength": min((p - avg) / avg * 100, 1)}))
        elif p < avg * 0.999:
            self.signals += 1
            await self.bus.publish(Event(EventType.SIGNAL, data={
                "symbol": event.data["symbol"], "side": "SELL", "price": p, "strength": min((avg - p) / avg * 100, 1)}))

# ── Risk Engine ─────────────────────────────────────────
class RiskEngine:
    def __init__(self, cfg):
        self.cfg = cfg; self.rejected = 0
    def check(self, signal, position, cash, price):
        if abs(position) >= self.cfg.max_position:
            self.rejected += 1; return None
        if signal == "BUY" and cash < price * 1.002:
            self.rejected += 1; return None
        return signal

# ── Execution Engine (paper trading) ────────────────────
class Execution:
    def __init__(self, bus, cfg):
        self.bus = bus; self.cfg = cfg
        self.cash = cfg.initial_cash; self.initial = cfg.initial_cash
        self.position = 0; self.avg_entry = 0.0; self.realized = 0.0
        self.trades = []; self.orders = 0; self.risk = RiskEngine(cfg)
    async def on_signal(self, event):
        s = event.data; safe = self.risk.check(s["side"], self.position, self.cash, s["price"])
        if not safe: return
        self.orders += 1; price = s["price"]; comm = price * self.cfg.commission
        if safe == "BUY":
            self.cash -= price + comm
            if self.position >= 0:
                tot = abs(self.position) + 1; self.avg_entry = (self.avg_entry * abs(self.position) + price) / tot
            else:
                self.realized += (self.avg_entry - price) * abs(self.position) - comm; self.avg_entry = price
            self.position += 1
        elif safe == "SELL":
            self.cash += price - comm
            if self.position <= 0:
                tot = abs(self.position) + 1; self.avg_entry = (self.avg_entry * abs(self.position) + price) / tot
            else:
                self.realized += (price - self.avg_entry) * abs(self.position) - comm; self.avg_entry = price
            self.position -= 1
        trade = {"id": uuid.uuid4().hex[:10], "ts": round(time.time(), 2),
            "side": safe, "price": round(price, 2), "comm": round(comm, 2),
            "pos": self.position, "cash": round(self.cash, 2), "rpnl": round(self.realized, 2)}
        self.trades.append(trade)
        log.info("FILL %s %s @ $%.2f  pos=%d  cash=%.2f", safe, s["symbol"], price, self.position, self.cash)
        await self.bus.publish(Event(EventType.FILL, data=trade))
    @property
    def equity(self): return self.cash + self.position * self.avg_entry
    @property
    def total_pnl(self): return self.equity - self.initial
    def status(self):
        return {"position": self.position, "avg_entry": round(self.avg_entry, 2),
            "cash": round(self.cash, 2), "equity": round(self.equity, 2),
            "realized_pnl": round(self.realized, 2), "total_pnl": round(self.total_pnl, 2),
            "orders": self.orders, "trades": len(self.trades)}

# ── Engine (orchestrator) ───────────────────────────────
class Engine:
    def __init__(self, cfg=None):
        self.cfg = cfg or Config(); self.bus = EventBus()
        self.feed = Feed(self.bus, self.cfg)
        self.strategy = Strategy(self.bus, self.cfg)
        self.execution = Execution(self.bus, self.cfg)
        self.running = False; self._tasks = []
    async def start(self):
        self.running = True
        log.info("=" * 50); log.info("TRADING BOT v1.0  %s", self.cfg.symbol)
        log.info("Cash: $%.2f  MaxPos: %d", self.cfg.initial_cash, self.cfg.max_position)
        log.info("=" * 50)
        self.bus.subscribe(EventType.TICK, self.strategy.on_tick)
        self.bus.subscribe(EventType.SIGNAL, self.execution.on_signal)
        self._tasks.append(asyncio.create_task(self.feed.start()))
        try: await asyncio.gather(*self._tasks)
        except asyncio.CancelledError: pass
        finally: await self.stop()
    async def stop(self):
        self.running = False; await self.feed.stop()
        for t in self._tasks:
            if not t.done(): t.cancel()
        s = self.execution.status()
        log.info("STOPPED  %d ticks  %d trades  PnL: $%.2f",
            self.feed.ticks, s["trades"], s["total_pnl"])

# ── Dashboard (optional) ────────────────────────────────
def create_dashboard(engine):
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse
    from fastapi.middleware.cors import CORSMiddleware
    app = FastAPI(title="Trading Bot v1.0")
    app.add_middleware(CORSMiddleware, allow_origins=["*"],
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    HTML = HTMLResponse(content='<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Bot v1.0</title>'
    '<style>*{margin:0;padding:0;box-sizing:border-box}:root{--bg:#0a0e17;--card:#1a2332;--bdr:#1e3a5f;'
    '--txt:#e2e8f0;--dim:#94a3b8;--g:#10b981;--r:#ef4444;--b:#3b82f6;--y:#f59e0b}'
    'body{font-family:"Courier New",monospace;background:var(--bg);color:var(--txt);min-height:100vh}'
    '.h{background:#111827;border-bottom:1px solid var(--bdr);padding:14px 20px;display:flex;'
    'justify-content:space-between;align-items:center}.h h1{font-size:16px;letter-spacing:1px}'
    '.g{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;padding:18px 20px}'
    '.c{background:var(--card);border:1px solid var(--bdr);border-radius:8px;padding:16px}'
    '.ch{font-size:10px;text-transform:uppercase;letter-spacing:1.5px;color:var(--dim);margin-bottom:8px}'
    '.cv{font-size:26px;font-weight:700}.cv.g{color:var(--g)}.cv.r{color:var(--r)}.cv.b{color:var(--b)}.cv.y{color:var(--y)}'
    '.cs{font-size:11px;color:var(--dim);margin-top:3px}'
    'table{width:100%;border-collapse:collapse;font-size:12px}'
    'th{text-align:left;padding:6px 10px;font-size:10px;text-transform:uppercase;color:var(--dim);'
    'border-bottom:1px solid var(--bdr)}td{padding:6px 10px;border-bottom:1px solid rgba(30,58,95,.3)}'
    '.bu{color:var(--g);font-weight:600}.se{color:var(--r);font-weight:600}'
    '.s{padding:0 20px 16px}.badge{padding:3px 10px;border-radius:16px;font-size:11px;font-weight:600}'
    '.on{background:rgba(16,185,129,.15);color:var(--g);border:1px solid var(--g)}'
    '.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;animation:p 2s infinite}'
    '.dot.on{background:var(--g)}.dot.off{background:var(--r);animation:none}@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}'
    '</style></head><body><div class="h"><h1>TRADING BOT <span style="color:var(--b)">v1.0</span></h1>'
    '<div><span class="dot off" id="d"></span><span class="badge on" id="b">INIT</span></div></div>'
    '<div class="g"><div class="c"><div class="ch">Equity</div><div class="cv b" id="eq">$100,000</div>'
    '<div class="cs" id="ret">Return: 0%</div></div><div class="c"><div class="ch">Realized PnL</div>'
    '<div class="cv" id="rpnl">$0</div><div class="cs" id="trades">Trades: 0</div></div>'
    '<div class="c"><div class="ch">Position</div><div class="cv y" id="pos">0</div>'
    '<div class="cs" id="avg">Entry: $0</div></div><div class="c"><div class="ch">Price</div>'
    '<div class="cv" id="prc">--</div><div class="cs" id="stats">Signals: 0</div></div></div>'
    '<div class="s"><div class="c"><div class="ch">Trade Log</div><table><thead><tr>'
    '<th>Time</th><th>Side</th><th>Price</th><th>Comm</th><th>Pos</th><th>Cash</th></tr></thead>'
    '<tbody id="tl"></tbody></table></div></div><script>'
    'var $=function(id){return document.getElementById(id)};'
    'function fmt(n){return(n>=0?"+":"")+"$"+Math.abs(n).toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2})}'
    'function ft(t){var d=new Date(t*1000);return d.toLocaleTimeString("en-US",{hour12:false})}'
    'var ws=null;function connect(){try{ws=new WebSocket("ws://"+location.host+"/ws");'
    'ws.onopen=function(){$("d").className="dot on";$("b").textContent="LIVE";$("b").className="badge on"};'
    'ws.onclose=function(){$("d").className="dot off";$("b").textContent="OFF";$("b").className="badge off";setTimeout(connect,3000)};'
    'ws.onmessage=function(e){try{var d=JSON.parse(e.data);if(d.equity!=null){$("eq").textContent="$"+d.equity.toLocaleString("en-US",{minimumFractionDigits:2});'
    '$("eq").className="cv "+(d.equity>=100000?"g":"r");$("rpnl").textContent=fmt(d.realized_pnl);'
    '$("rpnl").className="cv "+(d.realized_pnl>=0?"g":"r");$("trades").textContent="Trades: "+d.orders;'
    '$("pos").textContent=d.position;$("avg").textContent="Entry: "+fmt(d.avg_entry)}'
    'if(d.price!=null){$("prc").textContent="$"+d.price.toLocaleString("en-US",{minimumFractionDigits:2})}'
    'if(d.signals!=null){$("stats").textContent="Signals: "+d.signals+" | Rejected: "+d.rejected}}catch(x){}}}'
    'connect();setInterval(function(){fetch("/api/trades").then(function(r){return r.json()}).then(function(t){'
    'var h="";t.forEach(function(x){h+="<tr><td>"+ft(x.ts)+"</td><td class=\\""+(x.side==="BUY"?"bu":"se")+"\\">"+x.side+"</td><td>$"+x.price+"</td><td>$"+x.comm+"</td><td>"+x.pos+"</td><td>$"+x.cash+"</td></tr>"});'
    '$("tl").innerHTML=h}).catch(function(){})},5000);</script></body></html>')
    @app.get("/", response_class=HTMLResponse)
    async def idx(): return HTML
    @app.get("/api/status")
    async def status():
        if not engine: return {"error": "no engine"}
        s = engine.execution.status()
        s["price"] = engine.feed.price; s["ticks"] = engine.feed.ticks
        s["signals"] = engine.strategy.signals; s["rejected"] = engine.execution.risk.rejected
        return s
    @app.get("/api/trades")
    async def trades():
        return engine.execution.trades[-50:] if engine else []
    @app.websocket("/ws")
    async def ws_live(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                if engine:
                    s = engine.execution.status()
                    await ws.send_json({"price": engine.feed.price, "ticks": engine.feed.ticks,
                        "signals": engine.strategy.signals, "rejected": engine.execution.risk.rejected, **s})
                await asyncio.sleep(2)
        except (WebSocketDisconnect, Exception): pass
    return app

# ── Entry Point ─────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Trading Bot v1.0")
    p.add_argument("--symbol", default=None); p.add_argument("--interval", type=float, default=None)
    p.add_argument("--cash", type=float, default=None); p.add_argument("--max-pos", type=int, default=None)
    p.add_argument("--dashboard", action="store_true"); p.add_argument("--port", type=int, default=None)
    args = p.parse_args()
    cfg = Config()
    if args.symbol: cfg.symbol = args.symbol
    if args.interval: cfg.interval = args.interval
    if args.cash: cfg.initial_cash = args.cash
    if args.max_pos: cfg.max_position = args.max_pos
    if args.port: cfg.port = args.port
    engine = Engine(cfg)
    if args.dashboard:
        import uvicorn
        app = create_dashboard(engine)
        t = threading.Thread(target=asyncio.run, args=(engine.start(),), daemon=True)
        t.start()
        log.info("Dashboard: http://%s:%d", cfg.host, cfg.port)
        uvicorn.run(app, host=cfg.host, port=cfg.port)
    else:
        asyncio.run(engine.start())

if __name__ == "__main__":
    main()