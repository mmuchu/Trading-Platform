import asyncio, json, logging
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse
from core.engine.live import LiveEngine
from core.engine.backtest import run_backtest
from config.settings import settings
logger = logging.getLogger(__name__)
app = FastAPI(title="Trading Platform Dashboard", version="1.0.0")
_engine = None

def get_engine():
    global _engine
    if _engine is None: _engine = LiveEngine(strategy_name="momentum", mode="PAPER")
    return _engine

class ConnectionManager:
    def __init__(self): self.connections = []
    async def connect(self, ws): await ws.accept(); self.connections.append(ws)
    def disconnect(self, ws): self.connections.remove(ws) if ws in self.connections else None
    async def broadcast(self, data):
        dead = []
        for ws in self.connections:
            try: await ws.send_json(data)
            except: dead.append(ws)
        for ws in dead:
            if ws in self.connections: self.connections.remove(ws)

manager = ConnectionManager()

def _on_tick(data):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running(): asyncio.run_coroutine_threadsafe(manager.broadcast(data), loop)
    except: pass

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    with open("dashboard/templates/index.html","r",encoding="utf-8") as f: return HTMLResponse(f.read())

@app.get("/api/status")
async def status(): return get_engine().get_status()

@app.get("/api/backtest")
async def backtest_endpoint(strategy=Query(default="momentum"), bars=Query(default=500)):
    try: return {"success":True,"data":run_backtest(strategy_name=strategy,kline_limit=bars).to_dict()}
    except Exception as e: return {"success":False,"error":str(e)}

@app.get("/api/trades")
async def trades(): return {"trades":get_engine().broker.get_trades()}

@app.get("/api/portfolio")
async def portfolio():
    e=get_engine(); p=e.feed.get_price()
    return e.broker.snapshot(p) if p else {"error":"No price data yet"}

@app.post("/api/start")
async def start_engine(strategy=Query(default="momentum"), mode=Query(default="PAPER")):
    global _engine; _engine=LiveEngine(strategy_name=strategy,mode=mode,on_tick=_on_tick); _engine.start()
    return {"status":"started","strategy":strategy,"mode":mode}

@app.post("/api/stop")
async def stop_engine():
    global _engine
    if _engine: _engine.stop(); return {"status":"stopped"}
    return {"status":"not_running"}

@app.post("/api/reset")
async def reset_engine(): global _engine; _engine=None; return {"status":"reset"}

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
                if msg.get("action")=="start": await start_engine(msg.get("strategy","momentum"),msg.get("mode","PAPER"))
                elif msg.get("action")=="stop": await stop_engine()
            except: pass
    except WebSocketDisconnect: manager.disconnect(ws)

if __name__=="__main__":
    import uvicorn; cfg=settings.dashboard
    uvicorn.run("app:app",host=cfg.host,port=cfg.port,reload=cfg.reload)