"""
Dashboard - FastAPI web dashboard for Trading Bot v1.0.

Optional. Requires: pip install fastapi uvicorn

Run:
    python -m trading_bot --dashboard

Endpoints:
    GET /              - HTML dashboard
    GET /api/status    - System status JSON
    GET /api/trades    - Trade log JSON
    GET /api/pnl       - PnL metrics JSON
    WS  /ws/live       - Real-time tick feed
"""

import asyncio
import logging
import time
from collections import deque
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("dashboard")

# HTML Dashboard (single-page, dark theme, auto-refresh)
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Trading Bot v1.0</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0a0e17;--card:#1a2332;--bdr:#1e3a5f;--txt:#e2e8f0;--dim:#94a3b8;
--g:#10b981;--r:#ef4444;--b:#3b82f6;--y:#f59e0b}
body{font-family:'Courier New',monospace;background:var(--bg);color:var(--txt);min-height:100vh}
.hdr{background:#111827;border-bottom:1px solid var(--bdr);padding:14px 24px;
display:flex;justify-content:space-between;align-items:center}
.hdr h1{font-size:15px;letter-spacing:1px}
.badge{padding:3px 10px;border-radius:16px;font-size:10px;font-weight:700}
.live{background:rgba(16,185,129,.15);color:var(--g);border:1px solid var(--g)}
.off{background:rgba(239,68,68,.15);color:var(--r);border:1px solid var(--r)}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:16px 24px}
.card{background:var(--card);border:1px solid var(--bdr);border-radius:8px;padding:16px}
.label{font-size:9px;text-transform:uppercase;letter-spacing:1.5px;color:var(--dim);margin-bottom:6px}
.val{font-size:24px;font-weight:700}
.val.green{color:var(--g)}.val.red{color:var(--r)}.val.blue{color:var(--b)}.val.yellow{color:var(--y)}
.sub{font-size:10px;color:var(--dim);margin-top:4px}
.section{padding:0 24px 16px}
.pair{display:grid;grid-template-columns:1.5fr 1fr;gap:12px}
table{width:100%;border-collapse:collapse;font-size:11px}
th{text-align:left;padding:5px 8px;font-size:9px;text-transform:uppercase;
color:var(--dim);border-bottom:1px solid var(--bdr)}
td{padding:5px 8px;border-bottom:1px solid rgba(30,58,95,.3)}
.buy{color:var(--g);font-weight:700}.sell{color:var(--r);font-weight:700}
.log{background:var(--card);border:1px solid var(--bdr);border-radius:8px;
max-height:280px;overflow-y:auto;font-size:10px;padding:10px}
.log-entry{padding:1px 0}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;
animation:pulse 2s infinite}
.dot.on{background:var(--g)}.dot.off{background:var(--r);animation:none}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
</style>
</head>
<body>
<div class="hdr">
<h1>TRADING BOT <span style="color:var(--b)">v1.0</span> <span style="color:var(--dim);font-size:11px">PRODUCTION BASELINE</span></h1>
<div><span class="dot off" id="dot"></span><span class="badge off" id="badge">OFF</span></div>
</div>
<div class="grid">
<div class="card"><div class="label">Equity</div><div class="val blue" id="eq">$10,000</div>
<div class="sub" id="ret">Return: 0%</div></div>
<div class="card"><div class="label">Position</div><div class="val yellow" id="pos">0</div>
<div class="sub" id="entry">Avg Entry: $0</div></div>
<div class="card"><div class="label">Realized PnL</div><div class="val green" id="rpnl">$0</div>
<div class="sub" id="wr">Win Rate: 0% | Trades: 0</div></div>
<div class="card"><div class="label">Price</div><div class="val" id="prc">$65,000</div>
<div class="sub" id="sig">Last Signal: HOLD</div></div>
</div>
<div class="section"><div class="pair">
<div class="card"><div class="label">Trades</div>
<table><thead><tr><th>#</th><th>Time</th><th>Side</th><th>Price</th><th>Comm</th><th>PnL</th></tr></thead>
<tbody id="tbl"></tbody></table></div>
<div class="card"><div class="label">Event Log</div><div class="log" id="log"></div></div>
</div></div>
<div class="section"><div class="grid">
<div class="card"><div class="label">Max Drawdown</div><div class="val red" id="dd">0%</div></div>
<div class="card"><div class="label">Commission</div><div class="val yellow" id="comm">$0</div></div>
<div class="card"><div class="label">Profit Factor</div><div class="val blue" id="pf">0</div></div>
<div class="card"><div class="label">Signals</div><div class="val" id="sigs">0</div></div>
</div></div>
<script>
var $=function(id){return document.getElementById(id)};
function fmt(n){return(n>=0?"+":"")+"$"+Math.abs(n).toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2})}
function pct(n){return(n>=0?"+":"")+n.toFixed(2)+"%"}
function ts(t){return new Date(t*1000).toLocaleTimeString("en-US",{hour12:false})}
var ML=80,logs=[];
function addLog(cls,msg){logs.unshift({c:cls,m:msg});if(logs.length>ML)logs.pop();
var el=$("log");el.innerHTML=logs.map(function(l){return"<div class='log-entry'><span style='color:var(--dim)'>"+ts(Date.now()/1000)+"</span> <span style='color:var(--"+l.c+")'>"+l.m+"</span></div>"}).join("")}
function refresh(){fetch("/api/status").then(function(r){return r.json()}).then(function(s){
$("eq").textContent=fmt(s.equity-s.initial_equity);$("eq").className="val "+(s.equity>=s.initial_equity?"green":"red");
var r=(s.equity-s.initial_equity)/s.initial_equity*100;$("ret").textContent="Return: "+(r>=0?"+":"")+r.toFixed(2)+"%";
$("pos").textContent=s.position;$("entry").textContent="Avg Entry: $"+(s.avg_entry||0).toLocaleString();
$("rpnl").textContent=fmt(s.realized_pnl);$("rpnl").className="val "+(s.realized_pnl>=0?"green":"red");
$("wr").textContent="Win: "+s.win_rate+"% | Trades: "+s.total_trades;
$("prc").textContent="$"+(s.price||0).toLocaleString();$("sig").textContent="Signal: "+s.last_signal;
$("dd").textContent=s.max_drawdown.toFixed(2)+"%";
$("comm").textContent="$"+s.total_commission.toFixed(4);
$("pf").textContent=s.profit_factor;$("sigs").textContent=s.signals;
}).catch(function(){})}
function loadTrades(){fetch("/api/trades").then(function(r){return r.json()}).then(function(t){
$("tbl").innerHTML=t.slice(-20).reverse().map(function(x){return"<tr><td>"+x.id+"</td><td>"+ts(x.timestamp)+"</td>"+
"<td class='"+(x.signal==="BUY"?"buy":"sell")+"'>"+x.signal+"</td><td>$"+x.exec_price+"</td>"+
"<td>"+x.commission.toFixed(4)+"</td><td style='color:"+(x.realized_pnl>=0?"var(--g)":"var(--r)")+"'>"+fmt(x.realized_pnl)+"</td></tr>"}).join("")
}).catch(function(){})}
refresh();loadTrades();setInterval(refresh,2000);setInterval(loadTrades,5000);
addLog("b","Dashboard loaded. Refreshing every 2s.");
</script>
</body></html>"""


def create_app(engine) -> FastAPI:
    """Create FastAPI app wired to a TradingEngine instance."""
    app = FastAPI(title="Trading Bot v1.0", version="1.0")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    html = HTMLResponse(content=HTML_TEMPLATE)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return html

    @app.get("/api/status")
    async def status():
        try:
            ex = engine.execution.stats()
            pn = engine.pnl.stats()
            st = engine.strategy.stats()
            rs = engine.risk.stats()
            price = engine.feed.price or 0
            return {
                "equity": ex["cash"] + ex["position_btc"] * price,
                "initial_equity": engine.config.INITIAL_CASH,
                "cash": ex["cash"],
                "position": round(abs(ex["position_btc"]), 6),
                "avg_entry": ex["avg_entry"],
                "realized_pnl": ex["realized_pnl"],
                "total_commission": ex["total_commission"],
                "total_trades": ex["trade_count"],
                "win_rate": pn["win_rate"],
                "max_drawdown": pn["max_drawdown_pct"],
                "profit_factor": pn["profit_factor"],
                "return_pct": pn["return_pct"],
                "price": price,
                "last_signal": st["last_signal"],
                "signals": st["signals_generated"],
                "risk_blocked": rs["blocked"],
                "risk_passed": rs["passed"],
                "ticks": engine.feed.tick_count,
            }
        except Exception as e:
            return {"error": str(e)}

    @app.get("/api/trades")
    async def trades():
        try:
            return engine.execution.trade_log()
        except Exception:
            return []

    @app.get("/api/pnl")
    async def pnl():
        try:
            return engine.pnl.stats()
        except Exception:
            return {}

    return app


def run_dashboard(engine, host: str = "0.0.0.0", port: int = 8080):
    """Start the dashboard in a background thread."""
    import threading
    import uvicorn

    app = create_app(engine)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")

    def _run():
        logger.info("Dashboard starting on http://%s:%d", host, port)
        uvicorn.Server(config).run()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    logger.info("Dashboard thread started (port %d)", port)
    return thread
