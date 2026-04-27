"""
V3.2 Dashboard — FastAPI Application
======================================
Real-time monitoring for the v3.2 regime-aware state machine architecture.

New in v3.2:
  - Regime indicator (TREND / RANGE / VOLATILE)
  - Position FSM state display
  - Signal score display
  - SL/TP exit counters
  - Risk checker status (circuit breaker, kill switch)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

_orchestrator = None


def create_v3_app(orchestrator=None) -> FastAPI:
    """Factory — pass the V3Orchestrator to wire endpoints."""
    global _orchestrator
    _orchestrator = orchestrator

    app = FastAPI(title="Trading Platform v3.2 Dashboard", version="3.2")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", response_class=HTMLResponse)
    async def dashboard_html():
        return HTML_RESPONSE

    @app.get("/api/v3/status")
    async def v3_status():
        if not _orchestrator:
            return {"error": "Not initialized"}
        return _orchestrator.system_status

    @app.get("/api/v3/trades")
    async def v3_trades(limit: int = 100):
        if not _orchestrator:
            return []
        return _orchestrator.analytics.trade_log[-limit:]

    @app.get("/api/v3/equity-curve")
    async def v3_equity():
        if not _orchestrator:
            return []
        return _orchestrator.analytics.equity_curve_data

    @app.get("/api/v3/performance")
    async def v3_perf():
        if not _orchestrator:
            return {}
        return _orchestrator.analytics.performance_summary

    @app.get("/api/v3/positions")
    async def v3_positions():
        if not _orchestrator:
            return {}
        return _orchestrator.execution.stats.get("positions", {})

    @app.get("/api/v3/ticks")
    async def v3_ticks(limit: int = 50):
        if not _orchestrator:
            return []
        ticks = _orchestrator.market_data.recent_ticks(limit)
        return [t.to_dict() for t in ticks]

    @app.get("/api/v3/regime")
    async def v3_regime():
        if not _orchestrator:
            return {}
        return _orchestrator.regime_classifier.stats

    @app.get("/api/v3/fsm")
    async def v3_fsm():
        if not _orchestrator:
            return {}
        return _orchestrator.position_fsm.stats

    @app.get("/api/v3/risk")
    async def v3_risk():
        if not _orchestrator:
            return {}
        return _orchestrator.risk_checker.stats

    @app.websocket("/ws/v3")
    async def websocket_v3(websocket: WebSocket):
        await websocket.accept()
        logger.info("V3.2 WebSocket client connected")
        queue = None
        if _orchestrator:
            queue = _orchestrator.analytics.subscribe_dashboard()
        try:
            while True:
                if queue:
                    try:
                        data = await asyncio.wait_for(queue.get(), timeout=1.0)
                        await websocket.send_json(data)
                    except asyncio.TimeoutError:
                        await websocket.send_json({"type": "HEARTBEAT", "ts": time.time()})
                else:
                    await asyncio.sleep(1)
        except WebSocketDisconnect:
            logger.info("V3.2 WebSocket client disconnected")
        finally:
            if _orchestrator and queue:
                _orchestrator.analytics.unsubscribe_dashboard(queue)

    return app


# ─── Embedded Dashboard HTML ──────────────────────────────────────

HTML_RESPONSE = HTMLResponse(content="""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trading Platform v3.2</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        :root {
            --bg-primary: #0a0e17;
            --bg-secondary: #111827;
            --bg-card: #1a2332;
            --border: #1e3a5f;
            --text-primary: #e2e8f0;
            --text-secondary: #94a3b8;
            --green: #10b981;
            --red: #ef4444;
            --blue: #3b82f6;
            --yellow: #f59e0b;
            --purple: #8b5cf6;
            --cyan: #06b6d4;
        }
        body {
            font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
        }
        .header {
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border);
            padding: 16px 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 { font-size: 18px; font-weight: 700; letter-spacing: 1px; }
        .header .badge {
            padding: 4px 12px; border-radius: 20px;
            font-size: 12px; font-weight: 600; margin-left: 8px;
        }
        .badge-live { background: rgba(16,185,129,0.15); color: var(--green); border: 1px solid var(--green); }
        .badge-off { background: rgba(239,68,68,0.15); color: var(--red); border: 1px solid var(--red); }
        .badge-regime {
            font-size: 11px; padding: 3px 10px; border-radius: 12px;
            font-weight: 700; letter-spacing: 1px;
        }
        .regime-trend { background: rgba(16,185,129,0.2); color: var(--green); border: 1px solid var(--green); }
        .regime-range { background: rgba(245,158,11,0.2); color: var(--yellow); border: 1px solid var(--yellow); }
        .regime-volatile { background: rgba(239,68,68,0.2); color: var(--red); border: 1px solid var(--red); }
        .grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 16px;
            padding: 20px 24px;
        }
        .grid-5 { grid-template-columns: repeat(5, 1fr); }
        .grid-6 { grid-template-columns: repeat(6, 1fr); }
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px 20px;
        }
        .card-header {
            font-size: 10px; text-transform: uppercase;
            letter-spacing: 1.5px; color: var(--text-secondary);
            margin-bottom: 8px;
        }
        .card-value { font-size: 24px; font-weight: 700; }
        .green { color: var(--green); }
        .red { color: var(--red); }
        .blue { color: var(--blue); }
        .yellow { color: var(--yellow); }
        .purple { color: var(--purple); }
        .cyan { color: var(--cyan); }
        .card-sub { font-size: 11px; color: var(--text-secondary); margin-top: 4px; }
        .section { padding: 0 24px 20px; }
        .panel-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th {
            text-align: left; padding: 8px 12px;
            font-size: 10px; text-transform: uppercase;
            letter-spacing: 1px; color: var(--text-secondary);
            border-bottom: 1px solid var(--border);
        }
        td { padding: 6px 12px; border-bottom: 1px solid rgba(30,58,95,0.4); }
        tr:hover { background: rgba(59,130,246,0.05); }
        .side-buy { color: var(--green); font-weight: 600; }
        .side-sell { color: var(--red); font-weight: 600; }
        .log-container {
            background: var(--bg-card); border: 1px solid var(--border);
            border-radius: 8px; max-height: 400px;
            overflow-y: auto; font-size: 11px; padding: 10px;
        }
        .log-entry { padding: 2px 0; }
        .log-time { color: var(--text-secondary); }
        .log-tick { color: var(--blue); }
        .log-signal { color: var(--yellow); }
        .log-fill { color: var(--green); }
        .log-risk { color: var(--red); }
        .log-regime { color: var(--purple); }
        .log-fsm { color: var(--cyan); }
        .ws-dot {
            display: inline-block; width: 8px; height: 8px;
            border-radius: 50%; margin-right: 8px;
            animation: pulse 2s infinite;
        }
        .ws-on { background: var(--green); }
        .ws-off { background: var(--red); animation: none; }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
        .fsm-state {
            display: inline-block; padding: 2px 8px; border-radius: 4px;
            font-size: 11px; font-weight: 700; letter-spacing: 0.5px;
            background: rgba(6,182,212,0.15); color: var(--cyan); border: 1px solid var(--cyan);
        }
        .kill-active { color: var(--red); font-weight: 700; }
        .cb-active { color: var(--yellow); font-weight: 700; }
        @media (max-width: 900px) {
            .grid, .grid-5, .grid-6 { grid-template-columns: repeat(2, 1fr); }
            .panel-grid { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>

<div class="header">
    <h1>TRADING PLATFORM <span style="color:var(--blue)">v3.2</span>
        <span style="font-size:10px;color:var(--text-secondary)">REGIME-AWARE STATE MACHINE</span>
    </h1>
    <div style="display:flex;align-items:center;gap:12px;">
        <span class="badge-regime regime-range" id="regimeBadge">RANGE</span>
        <span class="ws-dot ws-off" id="wsDot"></span>
        <span class="badge badge-off" id="statusBadge">INITIALIZING</span>
    </div>
</div>

<div class="grid">
    <div class="card">
        <div class="card-header">Equity</div>
        <div class="card-value blue" id="equity">$10,000.00</div>
        <div class="card-sub" id="retPct">Return: 0.00%</div>
    </div>
    <div class="card">
        <div class="card-header">Unrealized PnL</div>
        <div class="card-value green" id="unPnl">$0.00</div>
        <div class="card-sub" id="rePnl">Realized: $0.00</div>
    </div>
    <div class="card">
        <div class="card-header">Position</div>
        <div class="card-value yellow" id="pos">0</div>
        <div class="card-sub" id="avgE">Avg Entry: $0.00</div>
    </div>
    <div class="card">
        <div class="card-header">Latest Price</div>
        <div class="card-value" id="price">$65,000.00</div>
        <div class="card-sub" id="counts">Ticks: 0 | Signals: 0</div>
    </div>
</div>

<div class="grid grid-6">
    <div class="card">
        <div class="card-header">FSM State</div>
        <div class="card-value cyan" id="fsmState"><span class="fsm-state">FLAT</span></div>
        <div class="card-sub" id="fsmCycles">Cycles: 0</div>
    </div>
    <div class="card">
        <div class="card-header">Regime</div>
        <div class="card-value purple" id="regimeVal">RANGE</div>
        <div class="card-sub" id="regimeTrans">Transitions: 0</div>
    </div>
    <div class="card">
        <div class="card-header">Circuit Breaker</div>
        <div class="card-value" id="cbStatus">OK</div>
        <div class="card-sub" id="cbLosses">Consec. losses: 0</div>
    </div>
    <div class="card">
        <div class="card-header">Kill Switch</div>
        <div class="card-value green" id="killStatus">OFF</div>
        <div class="card-sub" id="ddPct">Drawdown: 0.00%</div>
    </div>
    <div class="card">
        <div class="card-header">SL / TP Exits</div>
        <div class="card-value yellow" id="sltpExits">0 / 0</div>
        <div class="card-sub" id="winRate">Win Rate: 0%</div>
    </div>
</div>
<div class="grid grid-6" style="margin-top:16px;">
    <div class="card">
        <div class="card-header">Signals Suppressed</div>
        <div class="card-value red" id="suppressed">0</div>
        <div class="card-sub" id="avgScore">Avg Score: 0 | Stress: <span id="stressVal">0%</span></div>
    </div>
    <div class="card">
        <div class="card-header">Equity Stress</div>
        <div class="card-value" id="stressLevel">0%</div>
        <div class="card-sub" id="stressBlocks">Stress-blocked: 0</div>
    </div>
</div>

<div class="section">
    <div class="panel-grid">
        <div class="card">
            <div class="card-header">Trade Log</div>
            <table>
                <thead><tr><th>Time</th><th>Side</th><th>Qty</th><th>Price</th><th>Comm</th></tr></thead>
                <tbody id="tradeLog"></tbody>
            </table>
        </div>
        <div class="card">
            <div class="card-header">Live Event Stream</div>
            <div class="log-container" id="eventLog"></div>
        </div>
    </div>
</div>

<script>
const $ = id => document.getElementById(id);
const ws = new WebSocket('ws://' + location.host + '/ws/v3');
const MAX_LOG = 100;
const INIT_EQ = 10000;
let trades = [];

function fmt(n, p='$') {
    const s = n >= 0 ? '' : '-';
    return s + p + Math.abs(n).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
}
function ts(t) {
    const d = new Date(t*1000);
    return d.toLocaleTimeString('en-US',{hour12:false})+'.'+String(d.getMilliseconds()).padStart(3,'0');
}

ws.onopen = () => {
    $('wsDot').className = 'ws-dot ws-on';
    $('statusBadge').textContent = 'LIVE';
    $('statusBadge').className = 'badge badge-live';
};
ws.onclose = () => {
    $('wsDot').className = 'ws-dot ws-off';
    $('statusBadge').textContent = 'DISCONNECTED';
    $('statusBadge').className = 'badge badge-off';
};

ws.onmessage = evt => {
    try {
        const d = JSON.parse(evt.data);
        if (d.type === 'PNL_SNAPSHOT') {
            $('equity').textContent = fmt(d.equity);
            $('equity').className = 'card-value ' + (d.equity >= INIT_EQ ? 'green' : 'red');
            $('unPnl').textContent = fmt(d.unrealized_pnl);
            $('unPnl').className = 'card-value ' + (d.unrealized_pnl >= 0 ? 'green' : 'red');
            $('rePnl').textContent = 'Realized: ' + fmt(d.realized_pnl);
            const ret = ((d.equity - INIT_EQ) / INIT_EQ * 100).toFixed(2);
            $('retPct').textContent = 'Return: ' + (ret >= 0 ? '+' : '') + ret + '%';
            $('pos').textContent = d.position;
            $('pos').className = 'card-value ' + (d.position > 0 ? 'green' : d.position < 0 ? 'red' : 'yellow');
            $('avgE').textContent = 'Avg Entry: ' + fmt(d.avg_entry);
        }
        if (d.type === 'FILL') {
            trades.unshift(d);
            if (trades.length > 20) trades.pop();
            renderTrades();
            addLog('fill', 'FILL ' + d.side + ' ' + d.symbol + ' x' + d.quantity + ' @ ' + d.price + (d.metadata?.trigger ? ' ['+d.metadata.trigger+']' : ''));
        }
        if (d.type === 'SIGNAL') {
            addLog('signal', 'SIGNAL ' + d.side + ' ' + d.symbol + ' @ ' + d.price + ' score=' + (d.score||0).toFixed(1) + ' regime=' + (d.regime||'?'));
        }
        if (d.type === 'HEARTBEAT') refreshStatus();
    } catch(e) { console.error(e); }
};

function renderTrades() {
    $('tradeLog').innerHTML = trades.map(t =>
        '<tr><td>'+ts(t.timestamp)+'</td><td class="side-'+t.side.toLowerCase()+'">'+t.side+'</td><td>'+t.quantity+'</td><td>'+fmt(t.price)+'</td><td>'+fmt(t.commission)+'</td></tr>'
    ).join('');
}

function addLog(cls, msg) {
    const el = $('eventLog');
    const div = document.createElement('div');
    div.className = 'log-entry';
    div.innerHTML = '<span class="log-time">'+ts(Date.now()/1000)+'</span> <span class="log-'+cls+'">'+msg+'</span>';
    el.prepend(div);
    while (el.children.length > MAX_LOG) el.removeChild(el.lastChild);
}

async function refreshStatus() {
    try {
        const r = await fetch('/api/v3/status');
        const s = await r.json();

        // Market data
        if (s.market_data) {
            $('price').textContent = fmt(s.market_data.latest_price || 0);
            $('counts').textContent = 'Ticks: ' + s.market_data.tick_count;
        }

        // Strategy
        if (s.strategy) {
            $('counts').textContent += ' | Signals: ' + s.strategy.signal_count;
            $('suppressed').textContent = s.strategy.signals_suppressed || 0;
            const stressBlocked = s.strategy.signals_stress_blocked || 0;
            $('stressBlocks').textContent = 'Stress-blocked: ' + stressBlocked;
            const stressPct = parseFloat(s.strategy.stress_pct) || 0;
            $('stressLevel').textContent = stressPct.toFixed(1) + '%';
            $('stressVal').textContent = stressPct.toFixed(1) + '%';
            $('stressLevel').className = 'card-value ' + (stressPct > 80 ? 'red' : stressPct > 50 ? 'yellow' : 'green');
            const avgScores = s.strategy.avg_recent_scores || {};
            const scores = Object.values(avgScores);
            $('avgScore').textContent = 'Avg Score: ' + (scores.length ? (scores.reduce((a,b)=>a+b,0)/scores.length).toFixed(1) : '0');
        }

        // Regime
        if (s.regime) {
            const regimes = s.regime.current_regimes || {};
            const reg = Object.values(regimes)[0] || 'RANGE';
            $('regimeVal').textContent = reg;
            $('regimeTrans').textContent = 'Transitions: ' + (s.regime.regime_changes || 0);
            $('regimeBadge').textContent = reg;
            $('regimeBadge').className = 'badge-regime regime-' + reg.toLowerCase();
        }

        // FSM
        if (s.position_fsm) {
            const positions = s.position_fsm.positions || {};
            const sym = Object.keys(positions)[0];
            if (sym) {
                const p = positions[sym];
                $('fsmState').innerHTML = '<span class="fsm-state">' + p.state + '</span>';
            } else {
                $('fsmState').innerHTML = '<span class="fsm-state">FLAT</span>';
            }
            $('fsmCycles').textContent = 'Cycles: ' + (s.position_fsm.total_cycles || 0);
        }

        // Execution
        if (s.execution) {
            $('sltpExits').textContent = (s.execution.sl_exits||0) + ' / ' + (s.execution.tp_exits||0);
            $('winRate').textContent = 'Win Rate: ' + (s.execution.win_rate||0) + '%';
        }

        // Risk
        if (s.risk) {
            const r = s.risk;
            $('cbStatus').textContent = r.circuit_breaker ? 'ACTIVE' : 'OK';
            $('cbStatus').className = 'card-value ' + (r.circuit_breaker ? 'yellow cb-active' : 'green');
            $('cbLosses').textContent = 'Consec. losses: ' + (r.consecutive_losses || 0);
            $('killStatus').textContent = r.kill_switch ? 'ACTIVE' : 'OFF';
            $('killStatus').className = 'card-value ' + (r.kill_switch ? 'red kill-active' : 'green');
            $('ddPct').textContent = 'Drawdown: ' + ((r.current_drawdown_pct||0)*100).toFixed(2) + '%';
        }

        // Analytics
        if (s.analytics) {
            $('maxDD') && ($('maxDD').textContent = (s.analytics.max_drawdown_pct||0).toFixed(2) + '%');
        }

    } catch(e) {}
}

refreshStatus();
setInterval(refreshStatus, 3000);
</script>
</body>
</html>
""")
