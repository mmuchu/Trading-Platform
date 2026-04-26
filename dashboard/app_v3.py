"""
V3 Dashboard — FastAPI app with real-time WebSocket feed.
Separate from dashboard/app.py (v1) so both coexist.

v3.1: Added risk guard status panel, SL/TP indicators, and gate visualization.
"""

from __future__ import annotations

import asyncio
import logging
import time

import json
_o=json.JSONEncoder.default
def _sd(s,o):
 if isinstance(o,float)and(o!=o or abs(o)>1e308):return None
 return _o(s,o)
json.JSONEncoder.default=_sd

import math
_je=json.JSONEncoder.default
json.JSONEncoder.default=lambda s,o:None if isinstance(o,float)and(math.isnan(o)or math.isinf(o))else _je(s,o)
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

_orchestrator = None


def create_v3_app(orchestrator=None) -> FastAPI:
    """Factory — pass V3Orchestrator to wire endpoints to live data."""
    global _orchestrator
    _orchestrator = orchestrator

    app = FastAPI(title="Trading Platform v3.1", version="3.1")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTML_RESPONSE

    @app.get("/api/v3/status")
    async def status():
        return _orchestrator.system_status if _orchestrator else {"error": "not initialized"}

    @app.get("/api/v3/trades")
    async def trades(limit: int = 100):
        if not _orchestrator:
            return []
        return _orchestrator.analytics.trade_log[-limit:]

    @app.get("/api/v3/equity-curve")
    async def equity_curve():
        return _orchestrator.analytics.equity_curve_data if _orchestrator else []

    @app.get("/api/v3/performance")
    async def performance():
        return _orchestrator.analytics.performance_summary if _orchestrator else {}

    @app.get("/api/v3/positions")
    async def positions():
        return _orchestrator.execution.stats.get("positions", {}) if _orchestrator else {}

    @app.get("/api/v3/ticks")
    async def ticks(limit: int = 50):
        if not _orchestrator:
            return []
        return [t.to_dict() for t in _orchestrator.market_data.recent_ticks(limit)]

    @app.get("/api/v3/risk-guard")
    async def risk_guard_status():
        if not _orchestrator:
            return {"error": "not initialized"}
        return _orchestrator.risk_guard.get_system_status()

    @app.get("/api/v3/sltp-triggers")
    async def sltp_triggers(limit: int = 20):
        if not _orchestrator:
            return []
        return _orchestrator.risk_guard.sl_tp.get_triggers(limit)

    @app.websocket("/ws")
    async def ws_v3(websocket: WebSocket):
        await websocket.accept()
        logger.info("V3 WebSocket client connected")
        queue = None
        if _orchestrator:
            queue = _orchestrator.analytics.subscribe_dashboard()
        try:
            while True:
                if queue:
                    try:
                        data = await asyncio.to_thread(queue.get, timeout=1.0)
                        await websocket.send_json(data)
                    except Exception:
                        await websocket.send_json({"type": "HEARTBEAT", "ts": time.time()})
                else:
                    await asyncio.sleep(1)
        except WebSocketDisconnect:
            logger.info("V3 WebSocket client disconnected")
        finally:
            if _orchestrator and queue:
                _orchestrator.analytics.unsubscribe_dashboard(queue)

    return app


HTML_RESPONSE = HTMLResponse(content="""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta http-equiv="Cache-Control" content="no-cache,no-store,must-revalidate">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Trading Platform v3.1 — Risk Guard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0a0e17;--bg2:#111827;--card:#1a2332;--border:#1e3a5f;--txt:#e2e8f0;--dim:#94a3b8;--g:#10b981;--r:#ef4444;--b:#3b82f6;--y:#f59e0b;--p:#a855f7;--o:#f97316}
body{font-family:'JetBrains Mono','Fira Code',monospace;background:var(--bg);color:var(--txt);min-height:100vh}
.hdr{background:var(--bg2);border-bottom:1px solid var(--border);padding:14px 24px;display:flex;justify-content:space-between;align-items:center}
.hdr h1{font-size:17px;font-weight:700;letter-spacing:1px}
.badge{padding:4px 12px;border-radius:20px;font-size:11px;font-weight:600}
.b-on{background:rgba(16,185,129,.15);color:var(--g);border:1px solid var(--g)}
.b-off{background:rgba(239,68,68,.15);color:var(--r);border:1px solid var(--r)}
.b-warn{background:rgba(245,158,11,.15);color:var(--y);border:1px solid var(--y)}
.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;padding:18px 24px}
.card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:18px}
.ch{font-size:10px;text-transform:uppercase;letter-spacing:1.5px;color:var(--dim);margin-bottom:10px}
.cv{font-size:26px;font-weight:700}
.cs{font-size:11px;color:var(--dim);margin-top:4px}
.g{color:var(--g)}.r{color:var(--r)}.b{color:var(--b)}.y{color:var(--y)}.p{color:var(--p)}.o{color:var(--o)}
.sec{padding:0 24px 18px}
.pg{display:grid;grid-template-columns:1fr 1fr;gap:14px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:7px 10px;font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);border-bottom:1px solid var(--border)}
td{padding:7px 10px;border-bottom:1px solid rgba(30,58,95,.4)}
tr:hover{background:rgba(59,130,246,.05)}
.sb{color:var(--g);font-weight:600}.ss{color:var(--r);font-weight:600}
.logc{background:var(--card);border:1px solid var(--border);border-radius:8px;max-height:380px;overflow-y:auto;font-size:11px;padding:10px}
.le{padding:2px 0}.lt{color:var(--dim)}.lk{color:var(--b)}.ls{color:var(--y)}.lf{color:var(--g)}.lr{color:var(--r)}.lp{color:var(--p)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px;animation:p 2s infinite}
.d-on{background:var(--g)}.d-off{background:var(--r);animation:none}
@keyframes p{0%,100%{opacity:1}50%{opacity:.4}}
.gates{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.gate{padding:3px 10px;border-radius:12px;font-size:10px;font-weight:600;border:1px solid var(--border);background:rgba(30,58,95,.3);color:var(--dim)}
.gate.pass{border-color:var(--g);color:var(--g);background:rgba(16,185,129,.1)}
.gate.fail{border-color:var(--r);color:var(--r);background:rgba(239,68,68,.1)}
.risk-bar{width:100%;height:6px;background:rgba(30,58,95,.5);border-radius:3px;margin-top:8px;overflow:hidden}
.risk-fill{height:100%;border-radius:3px;transition:width .5s,background .5s}
@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}.pg{grid-template-columns:1fr}}
</style></head><body>
<div class="hdr"><h1>TRADING PLATFORM <span class="badge" style="background:rgba(168,85,247,.15);color:var(--p);border:1px solid var(--p)">v3.1</span> <span style="font-size:10px;color:var(--dim)">RISK GUARD</span></h1><div><span class="dot d-off" id="dot"></span><span class="badge b-off" id="badge">INITIALIZING</span></div></div>

<div class="grid">
<div class="card"><div class="ch">Equity</div><div class="cv b" id="eq">$10,000.00</div><div class="cs" id="ret">Return: 0.00%</div></div>
<div class="card"><div class="ch">Unrealized PnL</div><div class="cv g" id="unpnl">$0.00</div><div class="cs" id="repnl">Realized: $0.00</div></div>
<div class="card"><div class="ch">Position</div><div class="cv y" id="pos">0</div><div class="cs" id="avgE">Avg Entry: $0.00</div></div>
<div class="card"><div class="ch">Latest Price</div><div class="cv" id="px">$65,000.00</div><div class="cs" id="cnt">Ticks: 0 | Signals: 0</div></div>
<div class="card"><div class="ch">Win Rate</div><div class="cv b" id="wr">--</div><div class="cs" id="rts">W: 0 | L: 0 | Trips: 0</div></div>
</div>

<div class="sec" style="margin-top:4px">
<div class="card">
<div class="ch">5-Gate Risk Guard</div>
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
<div class="gates" id="gates">
<span class="gate" id="g-feed">Feed</span>
<span class="gate" id="g-signal">Signal</span>
<span class="gate" id="g-risk">Risk</span>
<span class="gate" id="g-pos">Position</span>
<span class="gate" id="g-cd">Cooldown</span>
</div>
<div style="text-align:right">
<div style="font-size:11px;color:var(--dim)">Risk Score</div>
<div style="font-size:20px;font-weight:700" id="riskScore">0%</div>
</div>
</div>
<div class="risk-bar"><div class="risk-fill" id="riskFill" style="width:0%;background:var(--g)"></div></div>
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-top:12px">
<div><div style="font-size:10px;color:var(--dim)">Feed</div><div style="font-size:13px" id="feedSt">--</div></div>
<div><div style="font-size:10px;color:var(--dim)">Drawdown</div><div style="font-size:13px" id="ddSt">--</div></div>
<div><div style="font-size:10px;color:var(--dim)">Circuit Brk</div><div style="font-size:13px" id="cbSt">--</div></div>
<div><div style="font-size:10px;color:var(--dim)">Kill Switch</div><div style="font-size:13px" id="ksSt">--</div></div>
<div><div style="font-size:10px;color:var(--dim)">SL/TP Active</div><div style="font-size:13px" id="sltpSt">--</div></div>
</div>
<div style="margin-top:10px;font-size:11px;color:var(--dim)" id="rgStats"></div>
</div>
</div>

<div class="sec"><div class="pg">
<div class="card"><div class="ch">Trade Log</div><table><thead><tr><th>Time</th><th>Side</th><th>Qty</th><th>Price</th><th>Comm</th></tr></thead><tbody id="tl"></tbody></table></div>
<div class="card"><div class="ch">Live Event Stream</div><div class="logc" id="el"></div></div>
</div></div>

<div class="sec"><div class="grid">
<div class="card"><div class="ch">Total Trades</div><div class="cv b" id="tt">0</div></div>
<div class="card"><div class="ch">Max Drawdown</div><div class="cv r" id="dd">0.00%</div></div>
<div class="card"><div class="ch">Approved</div><div class="cv g" id="ap">0</div></div>
<div class="card"><div class="ch">Rejected</div><div class="cv r" id="rj">0</div></div>
<div class="card"><div class="ch">SL/TP Fires</div><div class="cv o" id="slt">0</div></div>
</div></div>

<script>
const $ = (id) => document.getElementById(id);
const wsProtocol = location.protocol === 'https:' ? 'wss://' : 'ws://';
const ws = new WebSocket(wsProtocol + location.host + '/ws?v=' + Date.now());
const ML = 80;
const IE = 10000;
let trades = [];

function fmt(value, prefix = '$') {
  const n = Number(value ?? 0);
  const sign = n >= 0 ? '' : '-';
  return sign + prefix + Math.abs(n).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });
}

function pct(value) {
  return (Number(value ?? 0) * 100).toFixed(2) + '%';
}

function ts(value) {
  const d = new Date(Number(value ?? 0) * 1000);
  return d.toLocaleTimeString('en-US', { hour12: false }) + '.' + String(d.getMilliseconds()).padStart(3, '0');
}

function setBadge(live) {
  $('dot').className = live ? 'dot d-on' : 'dot d-off';
  $('badge').textContent = live ? 'LIVE' : 'DISCONNECTED';
  $('badge').className = live ? 'badge b-on' : 'badge b-off';
}

function setGateState(id, passed) {
  const el = $(id);
  if (!el) return;
  el.className = 'gate' + (passed === true ? ' pass' : passed === false ? ' fail' : '');
}

function rtl() {
  $('tl').innerHTML = trades.map((t) => (
    '<tr><td>' + ts(t.timestamp) + '</td><td class="s' + String(t.side || '').slice(0, 1).toLowerCase() + '">' +
    t.side + '</td><td>' + Number(t.quantity ?? 0).toFixed(4) + '</td><td>' + fmt(t.price) + '</td><td>' +
    fmt(t.commission) + '</td></tr>'
  )).join('');
}

function alog(cssClass, message, timestamp = Date.now() / 1000) {
  const el = $('el');
  const row = document.createElement('div');
  row.className = 'le';
  row.innerHTML = '<span class="lt">' + ts(timestamp) + '</span> <span class="' + cssClass + '">' + message + '</span>';
  el.prepend(row);
  while (el.children.length > ML) {
    el.removeChild(el.lastChild);
  }
}

function updateSnapshot(d) {
  $('eq').textContent = fmt(d.equity);
  $('eq').className = 'cv ' + (Number(d.equity ?? 0) >= IE ? 'g' : 'r');
  $('unpnl').textContent = fmt(d.unrealized_pnl);
  $('unpnl').className = 'cv ' + (Number(d.unrealized_pnl ?? 0) >= 0 ? 'g' : 'r');
  $('repnl').textContent = 'Realized: ' + fmt(d.realized_pnl);
  const ret = ((Number(d.equity ?? IE) - IE) / IE) * 100;
  $('ret').textContent = 'Return: ' + (ret >= 0 ? '+' : '') + ret.toFixed(2) + '%';
  $('pos').textContent = Number(d.position ?? 0).toFixed(4).replace(/\\.?0+$/, '');
  $('pos').className = 'cv ' + (d.position > 0 ? 'g' : d.position < 0 ? 'r' : 'y');
  $('avgE').textContent = 'Avg Entry: ' + fmt(d.avg_entry);
}

function updateRiskGuard(rg) {
  const feed = rg.feed_alive || {};
  const risk = rg.risk || {};
  const stats = rg.stats || {};
  const sltp = rg.sl_tp || {};
  const last = rg.last_evaluation || {};
  const gateResults = last.gate_results || {};
  const riskScore = Number(risk.risk_score ?? 0);

  $('feedSt').innerHTML = feed.alive
    ? '<span class="g">Alive</span>'
    : '<span class="r">DEAD</span>' + (Number.isFinite(feed.stale_seconds) ? ' ' + Number(feed.stale_seconds).toFixed(1) + 's' : '');
  $('ddSt').textContent = pct(risk.drawdown_pct);
  $('cbSt').innerHTML = risk.circuit_breaker ? '<span class="r">ACTIVE</span>' : '<span class="g">OK</span>';
  $('ksSt').innerHTML = risk.kill_switch ? '<span class="r">KILL</span>' : '<span class="g">OK</span>';
  $('sltpSt').innerHTML = (sltp.positions_monitored || 0) > 0 ? '<span class="g">ACTIVE</span>' : '<span class="y">STANDBY</span>';

  $('riskScore').textContent = (riskScore * 100).toFixed(0) + '%';
  $('riskScore').className = 'cv ' + (riskScore < 0.5 ? 'g' : riskScore < 0.8 ? 'y' : 'r');
  $('riskFill').style.width = (riskScore * 100).toFixed(1) + '%';
  $('riskFill').style.background = riskScore < 0.5 ? 'var(--g)' : riskScore < 0.8 ? 'var(--y)' : 'var(--r)';

  $('rgStats').textContent = 'Evaluations: ' + (stats.total_evaluations || 0) +
    ' | Approved: ' + (stats.total_approved || 0) +
    ' | Rejected: ' + (stats.total_rejected || 0) +
    ' | Approval: ' + Number(stats.approval_rate || 0).toFixed(1) + '%';
  $('slt').textContent = (sltp.sl_triggers || 0) + (sltp.tp_triggers || 0);

  setGateState('g-feed', feed.alive);
  setGateState('g-signal', gateResults.signal_valid);
  setGateState('g-risk', gateResults.risk_check);
  setGateState('g-pos', gateResults.position_sync);
  setGateState('g-cd', gateResults.cooldown);
}

async function loadTrades() {
  try {
    const response = await fetch('/api/v3/trades?limit=20', { cache: 'no-store' });
    if (!response.ok) return;
    trades = await response.json();
    rtl();
  } catch (ex) {
    console.error(ex);
  }
}

async function rs() {
  try {
    const response = await fetch('/api/v3/status', { cache: 'no-store' });
    if (!response.ok) return;

    const s = await response.json();
    if (!s.market_data) return;

    $('cnt').textContent = 'Ticks: ' + (s.market_data.tick_count || 0) + ' | Signals: ' + (s.strategy?.signal_count || 0);
    $('tt').textContent = s.execution?.fills_total ?? s.analytics?.total_trades ?? 0;
    $('dd').textContent = pct(s.analytics?.max_drawdown_pct);
    $('ap').textContent = s.risk_guard?.stats?.total_approved ?? 0;
    $('rj').textContent = s.risk_guard?.stats?.total_rejected ?? s.execution?.orders_rejected ?? 0;

    if (s.market_data.latest_price !== null && s.market_data.latest_price !== undefined) {
      $('px').textContent = fmt(s.market_data.latest_price);
    }

    const roundTrips = s.analytics?.round_trips || 0;
    const winRate = Number(s.analytics?.win_rate ?? 0);
    if (roundTrips > 0) {
      $('wr').textContent = (winRate * 100).toFixed(1) + '%';
      $('wr').className = 'cv ' + (winRate >= 0.5 ? 'g' : winRate > 0 ? 'y' : 'r');
    } else {
      $('wr').textContent = '--';
      $('wr').className = 'cv b';
    }
    $('rts').textContent = 'W: ' + (s.analytics?.wins || 0) + ' | L: ' + (s.analytics?.losses || 0) + ' | Trips: ' + roundTrips;

    if (s.risk_guard) {
      updateRiskGuard(s.risk_guard);
    }
  } catch (ex) {
    console.error(ex);
  }
}

ws.onopen = () => setBadge(true);
ws.onclose = () => setBadge(false);
ws.onmessage = (e) => {
  try {
    const d = JSON.parse(e.data);
    switch (d.type) {
      case 'PNL_SNAPSHOT':
        updateSnapshot(d);
        alog('lf', 'SNAPSHOT eq=' + d.equity + ' pos=' + d.position, d.timestamp);
        break;
      case 'TICK':
        $('px').textContent = fmt(d.price);
        alog('lk', 'TICK ' + d.symbol + ' @ ' + d.price, d.timestamp);
        break;
      case 'FILL':
        trades.unshift(d);
        if (trades.length > 20) {
          trades.pop();
        }
        rtl();
        alog('lf', 'FILL ' + d.side + ' ' + d.symbol + ' x' + d.quantity + ' @ ' + d.price, d.timestamp);
        rs();
        break;
      case 'SIGNAL':
        alog(
          'ls',
          'SIGNAL ' + d.side + ' ' + d.symbol + ' @ ' + d.price + ' str=' + Number(d.strength ?? 0).toFixed(2) +
            (d.metadata && d.metadata.trigger_type ? ' [' + d.metadata.trigger_type + ']' : ''),
          d.timestamp
        );
        break;
      case 'RISK_REJECTED':
        alog('lr', 'REJECTED ' + d.reason, d.timestamp);
        rs();
        break;
      case 'HEARTBEAT':
        rs();
        break;
      default:
        break;
    }
  } catch (ex) {
    console.error(ex);
  }
};

loadTrades();
rs();
setInterval(rs, 3000);
</script></body></html>
""")



