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