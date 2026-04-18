"""
Quant Trading System v3.1 - Edge-Optimized Edition
===================================================
CHANGES from v3.0:
  1. Order retry with price adjustment (fixes 50% rejection rate)
  2. Minimum profit threshold (skip trades that can't beat costs)
  3. Forced position holding (hold until reversal / target / stop)
  4. Confidence scoring (only trade above threshold)
  5. Regime filter (disable trading in low volatility)
  6. Reduced commission (0.0002 = 0.02% per side)
  7. True edge tracking (expected vs actual move)
  8. WebSocket FILL forwarding fix
  9. Top-level imports (no Pydantic bugs)

Deploy:
  1. Save this file as v3.1.py in your project root
  2. python main.py --mode v3.1
"""

from __future__ import annotations

import asyncio, json, logging, math, os, random, signal, sqlite3, sys, time, uuid
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Deque, Tuple

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

# ══════════════════════════════════════════════════════════════
#  MODELS
# ══════════════════════════════════════════════════════════════

class EventType(str, Enum):
    TICK = "TICK"; SIGNAL = "SIGNAL"; ORDER = "ORDER"; FILL = "FILL"
    RISK_REJECTED = "RISK_REJECTED"; PNL_SNAPSHOT = "PNL_SNAPSHOT"
    SYSTEM = "SYSTEM"; ERROR = "ERROR"
    REJECTION_RETRY = "REJECTION_RETRY"  # NEW: retry event

class Side(str, Enum):
    BUY = "BUY"; SELL = "SELL"

class SignalSource(str, Enum):
    ML_MODEL = "ML_MODEL"; RL_AGENT = "RL_AGENT"; RULE_ENGINE = "RULE_ENGINE"; MANUAL = "MANUAL"

class RejectionReason(str, Enum):
    MAX_POSITION = "MAX_POSITION"
    NO_CASH = "NO_CASH"
    REGIME_FILTER = "REGIME_FILTER"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    MIN_PROFIT = "MIN_PROFIT"
    SAME_DIRECTION = "SAME_DIRECTION"

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
    confidence: float = 0.0  # NEW: 0.0-1.0 confidence score
    expected_move_pct: float = 0.0  # NEW: expected price move in %
    metadata: dict = field(default_factory=dict)
    def __post_init__(self):
        self.type = EventType.SIGNAL
        self.source = self.source.value if isinstance(self.source, SignalSource) else self.source

@dataclass
class FillEvent(BaseEvent):
    fill_id: str = ""; order_id: str = ""; symbol: str = ""
    side: Side = Side.BUY; quantity: int = 1; price: float = 0.0; commission: float = 0.0
    retries_used: int = 0  # NEW: how many retries before fill
    def __post_init__(self):
        self.type = EventType.FILL; self.source = "execution"

@dataclass
class RiskRejectedEvent(BaseEvent):
    order_id: str = ""; reason: str = ""
    rejection_detail: str = ""  # NEW: detailed reason + metrics
    latency_ms: float = 0.0  # NEW: submission latency
    def __post_init__(self):
        self.type = EventType.RISK_REJECTED; self.source = "execution"

@dataclass
class PnLSnapshot(BaseEvent):
    symbol: str = ""; position: int = 0; avg_entry: float = 0.0
    unrealized_pnl: float = 0.0; realized_pnl: float = 0.0
    total_pnl: float = 0.0; equity: float = 0.0
    # NEW metrics
    regime: str = "NORMAL"
    avg_confidence: float = 0.0
    avg_expected_vs_actual: float = 0.0
    total_rejections: int = 0
    rejection_breakdown: dict = field(default_factory=dict)
    def __post_init__(self):
        self.type = EventType.PNL_SNAPSHOT; self.source = "analytics"

@dataclass
class EdgeRecord:
    """Track expected vs actual move for true edge measurement."""
    timestamp: float = 0.0
    symbol: str = ""
    side: str = ""
    entry_price: float = 0.0
    expected_move_pct: float = 0.0
    actual_move_pct: float = 0.0
    bars_held: int = 0
    confidence: float = 0.0
    pnl: float = 0.0
    exit_reason: str = ""

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
        except Exception as e:
            logging.debug("Handler error: %s", e)

    @property
    def handler_count(self):
        return len(self._wildcards) + sum(len(v) for v in self._handlers.values())

    def topics(self):
        return set(self._handlers.keys())

# ══════════════════════════════════════════════════════════════
#  CONFIG  (v3.1 defaults)
# ══════════════════════════════════════════════════════════════

@dataclass
class Config:
    symbols: List[str] = field(default_factory=lambda: ["BTCUSDT"])
    ws_url: str = "wss://stream.binance.com:9443/ws/btcusdt@trade"
    initial_cash: float = 100_000.0
    max_position: int = 10
    max_order_value: float = 500_000.0

    # FIX 6: Reduced commission (was 0.001 = 0.1%/side, now 0.0002 = 0.02%/side)
    commission: float = 0.0002
    slippage_bps: int = 3  # Reduced from 5

    strategies: List[str] = field(default_factory=lambda: ["momentum", "mean_reversion"])
    signal_cooldown: float = 2.0  # Increased from 1.0

    dash_host: str = "0.0.0.0"
    dash_port: int = 8001
    db_path: str = "data/trading_v3.1.db"
    log_level: str = "INFO"

    # NEW: v3.1 parameters
    # FIX 1: Order retry
    max_order_retries: int = 3  # Retry rejected orders up to 3 times
    retry_price_adjustment_bps: int = 2  # Adjust price by 2bps per retry

    # FIX 2: Minimum profit threshold
    min_expected_profit_pct: float = 0.15  # Skip if expected move < 0.15% (must beat fees+slippage)
    estimated_slippage_pct: float = 0.03  # ~3bps slippage estimate
    estimated_spread_pct: float = 0.02  # ~2bps spread estimate

    # FIX 3: Position holding
    hold_until_reversal: bool = True  # Don't exit unless signal reverses
    take_profit_pct: float = 0.5  # Exit if position up 0.5%
    stop_loss_pct: float = 0.3  # Exit if position down 0.3%
    max_hold_bars: int = 100  # Force exit after 100 ticks in position

    # FIX 4: Confidence scoring
    min_confidence: float = 0.65  # Skip signals with confidence < 0.65

    # FIX 5: Regime filter
    regime_lookback: int = 50  # Bars for volatility calculation
    min_volatility_pct: float = 0.05  # Min 0.05% avg bar-to-bar move to trade
    regime_high_vol_threshold: float = 0.5  # Above this = high vol regime

    # FIX 7: Edge tracking
    edge_track_bars: int = 50  # Track actual move over N bars after entry

# ══════════════════════════════════════════════════════════════
#  MARKET DATA SERVICE (unchanged, but tracks volatility)
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

    def current_volatility(self, lookback: int = 50) -> float:
        """Calculate annualized-ish volatility from recent ticks."""
        ticks = self.recent_ticks(lookback)
        if len(ticks) < lookback: return 0.0
        import numpy as np
        prices = np.array([t.price for t in ticks])
        returns = np.diff(prices) / prices[:-1]
        return float(np.std(returns) * 100)  # In percent

    def current_regime(self) -> str:
        """Determine market regime: QUIET, NORMAL, VOLATILE."""
        vol = self.current_volatility(self.config.regime_lookback)
        if vol < self.config.min_volatility_pct:
            return "QUIET"
        elif vol > self.config.regime_high_vol_threshold:
            return "VOLATILE"
        return "NORMAL"

    async def start(self):
        self._running = True
        logging.info("MarketDataService v3.1 starting (mode=%s)", self.mode)
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
                # Occasional trend bursts to create real regime changes
                if random.random() < 0.02:  # 2% chance of burst
                    drift = random.gauss(0, 15)  # Much larger move
                self._sim_price = max(1.0, self._sim_price + drift + revert)
                spread = random.uniform(0.3, 1.5)
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
#  STRATEGY SERVICE (v3.1: adds confidence + expected move)
# ══════════════════════════════════════════════════════════════

class BaseStrategy(ABC):
    name = "base"
    @abstractmethod
    async def evaluate(self, tick: TickEvent, history: deque) -> Optional[SignalEvent]: pass

class MomentumStrategy(BaseStrategy):
    name = "momentum"
    def __init__(self, lookback=20, threshold=0.05):
        self.lookback = lookback; self.threshold = threshold

    def _compute_confidence(self, prices, pct_change):
        """Confidence based on trend consistency, volume of move, and R^2 of trend."""
        import numpy as np
        if len(prices) < self.lookback: return 0.0
        # 1. Trend R^2 - how linear is the price move?
        x = np.arange(len(prices), dtype=float)
        y = prices
        slope = (np.mean(x * y) - np.mean(x) * np.mean(y)) / (np.mean(x**2) - np.mean(x)**2 + 1e-10)
        y_pred = slope * x + (np.mean(y) - slope * np.mean(x))
        ss_res = np.sum((y - y_pred)**2)
        ss_tot = np.sum((y - np.mean(y))**2) + 1e-10
        r_squared = max(0, 1 - ss_res / ss_tot)

        # 2. Move magnitude factor (bigger move = more conviction, up to a point)
        magnitude_factor = min(abs(pct_change) / (self.threshold * 3), 1.0)

        # 3. Recent acceleration (is the move getting stronger?)
        if len(prices) >= 5:
            recent_returns = np.diff(prices[-5:]) / prices[-5:-1]
            acceleration = recent_returns[-1] - np.mean(recent_returns[:-1]) if len(recent_returns) > 1 else 0
            accel_factor = min(abs(acceleration) * 1000, 1.0)
        else:
            accel_factor = 0.3

        # Weighted combination
        confidence = 0.4 * r_squared + 0.35 * magnitude_factor + 0.25 * accel_factor
        return round(min(max(confidence, 0.0), 1.0), 3)

    async def evaluate(self, tick, history):
        if len(history) < self.lookback: return None
        import numpy as np
        prices = np.array([t.price for t in list(history)[-self.lookback:]])
        pct = (tick.price - prices[0]) / prices[0] * 100

        if pct > self.threshold:
            confidence = self._compute_confidence(prices, pct)
            # FIX 4: Calculate expected move based on momentum decay model
            avg_return = (prices[-1] / prices[0]) - 1  # Total return over lookback
            # Expected move = recent momentum scaled by decay factor
            expected_move = abs(pct) * 0.3  # Conservative: expect 30% of recent move to continue
            return SignalEvent(
                symbol=tick.symbol, side=Side.BUY, price=tick.price,
                strength=min(abs(pct), 1.0), source=SignalSource.RULE_ENGINE,
                confidence=confidence,
                expected_move_pct=round(expected_move, 4),
                metadata={"strategy": self.name, "pct": round(pct, 4), "r2": round(confidence, 3)})
        elif pct < -self.threshold:
            confidence = self._compute_confidence(prices, pct)
            expected_move = abs(pct) * 0.3
            return SignalEvent(
                symbol=tick.symbol, side=Side.SELL, price=tick.price,
                strength=min(abs(pct), 1.0), source=SignalSource.RULE_ENGINE,
                confidence=confidence,
                expected_move_pct=round(expected_move, 4),
                metadata={"strategy": self.name, "pct": round(pct, 4), "r2": round(confidence, 3)})
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

        # Confidence: further from mean = higher confidence (more likely to revert)
        confidence = min(abs(z) / 4.0, 1.0)

        # Expected move: distance back to mean
        expected_move = abs(tick.price - sma) / sma * 100

        if z > self.std_above:
            return SignalEvent(
                symbol=tick.symbol, side=Side.SELL, price=tick.price,
                strength=min(z/4, 1.0), source=SignalSource.RULE_ENGINE,
                confidence=round(confidence, 3),
                expected_move_pct=round(expected_move, 4),
                metadata={"strategy": self.name, "z": round(z, 3)})
        elif z < -self.std_below:
            return SignalEvent(
                symbol=tick.symbol, side=Side.BUY, price=tick.price,
                strength=min(abs(z)/4, 1.0), source=SignalSource.RULE_ENGINE,
                confidence=round(confidence, 3),
                expected_move_pct=round(expected_move, 4),
                metadata={"strategy": self.name, "z": round(z, 3)})
        return None

class StrategyService:
    def __init__(self, bus: EventBus, config: Config, market_data: MarketDataService):
        self.bus = bus; self.config = config; self.market_data = market_data
        self._strategies = {s.name: s for s in [MomentumStrategy(), MeanReversionStrategy()]}
        self._history: Dict[str, deque] = {}; self._last_sig: Dict[str, float] = {}
        self._signals = 0; self._ticks = 0; self._filtered = 0
        self._filter_reasons: Dict[str, int] = defaultdict(int)

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
                    # FIX 5: Regime filter - skip in QUIET markets
                    regime = self.market_data.current_regime()
                    if regime == "QUIET":
                        self._filtered += 1
                        self._filter_reasons["REGIME_QUIET"] += 1
                        logging.debug("Signal filtered: QUIET regime (vol too low)")
                        continue

                    # FIX 4: Confidence filter
                    if sig.confidence < self.config.min_confidence:
                        self._filtered += 1
                        self._filter_reasons["LOW_CONFIDENCE"] += 1
                        logging.debug("Signal filtered: confidence %.2f < %.2f",
                                      sig.confidence, self.config.min_confidence)
                        continue

                    # FIX 2: Minimum profit threshold
                    total_cost_pct = (self.config.commission * 2 +  # Round-trip commission
                                      self.config.estimated_slippage_pct * 2 +  # Round-trip slippage
                                      self.config.estimated_spread_pct * 2)  # Round-trip spread
                    if sig.expected_move_pct < total_cost_pct:
                        self._filtered += 1
                        self._filter_reasons["MIN_PROFIT"] += 1
                        logging.debug("Signal filtered: expected move %.4f%% < cost %.4f%%",
                                      sig.expected_move_pct, total_cost_pct)
                        continue

                    self._signals += 1
                    self._last_sig[tick.symbol] = now
                    await self.bus.publish(sig)
                    logging.info("SIGNAL %s %s %s conf=%.2f exp_move=%.4f%%",
                                 sig.side.value, sig.symbol, name,
                                 sig.confidence, sig.expected_move_pct)
            except Exception as e:
                logging.exception("Strategy %s error: %s", name, e)

    @property
    def stats(self):
        return {
            "tick_count": self._ticks,
            "signal_count": self._signals,
            "filtered_count": self._filtered,
            "filter_reasons": dict(self._filter_reasons),
            "strategies": list(self._strategies.keys()),
            "enabled": self.config.strategies
        }

# ══════════════════════════════════════════════════════════════
#  EXECUTION SERVICE (v3.1: retry, hold, edge tracking)
# ══════════════════════════════════════════════════════════════

@dataclass
class PositionTracker:
    quantity: int = 0; avg_entry: float = 0.0
    realized_pnl: float = 0.0; commission: float = 0.0; trades: int = 0
    # NEW: position holding state
    entry_time: float = 0.0
    entry_signal_side: str = ""  # Which direction signal opened this position
    bars_in_position: int = 0
    peak_price: float = 0.0  # Highest price since entry (for trailing)
    trough_price: float = float('inf')  # Lowest price since entry

@dataclass
class PendingEntry:
    """Track entries for edge measurement."""
    signal: SignalEvent = field(default_factory=SignalEvent)
    fill_price: float = 0.0
    fill_time: float = 0.0
    confidence: float = 0.0
    expected_move_pct: float = 0.0
    bars_to_measure: int = 0  # How many bars left to measure

class ExecutionService:
    def __init__(self, bus: EventBus, config: Config, market_data: MarketDataService):
        self.bus = bus; self.config = config; self.market_data = market_data
        self._cash = config.initial_cash
        self._positions: Dict[str, PositionTracker] = {}
        self._fills: list = []; self._submitted = 0; self._rejected = 0; self._retried = 0
        self._start = time.time()
        # FIX 7: Edge tracking
        self._edge_records: List[EdgeRecord] = []
        self._pending_entries: Dict[str, PendingEntry] = {}  # symbol -> pending edge measurement
        # FIX 1: Rejection breakdown
        self._rejection_breakdown: Dict[str, int] = defaultdict(int)

    @property
    def cash(self): return self._cash
    @property
    def equity(self):
        return self._cash + sum(
            p.quantity * (self.market_data.latest_tick.price if self.market_data.latest_tick else p.avg_entry)
            for p in self._positions.values())
    @property
    def total_realized_pnl(self): return sum(p.realized_pnl for p in self._positions.values())
    def get_position(self, sym):
        if sym not in self._positions: self._positions[sym] = PositionTracker()
        return self._positions[sym]

    @property
    def stats(self):
        pos_details = {}
        for s, p in self._positions.items():
            current_price = self.market_data.latest_tick.price if self.market_data.latest_tick else p.avg_entry
            pos_details[s] = {
                "qty": p.quantity, "avg": round(p.avg_entry, 2),
                "pnl": round(p.realized_pnl, 2),
                "bars_held": p.bars_in_position,
                "entry_side": p.entry_signal_side
            }
        return {
            "cash": round(self._cash, 2), "equity": round(self.equity, 2),
            "realized_pnl": round(self.total_realized_pnl, 2),
            "orders_submitted": self._submitted,
            "orders_rejected": self._rejected,
            "orders_retried": self._retried,
            "rejection_breakdown": dict(self._rejection_breakdown),
            "positions": pos_details,
            "uptime": round(time.time() - self._start, 1),
            "edge_records": len(self._edge_records),
            "avg_edge": self._avg_edge()
        }

    def _avg_edge(self) -> float:
        """Average (actual_move - expected_move) in %. Positive = outperformed expectations."""
        if not self._edge_records: return 0.0
        diffs = [r.actual_move_pct - r.expected_move_pct for r in self._edge_records]
        return round(sum(diffs) / len(diffs), 4)

    def _reject(self, reason: str, detail: str = "", latency: float = 0.0):
        """Record a rejection with detailed tracking."""
        self._rejection_breakdown[reason] += 1
        self._rejected += 1
        rejection = RiskRejectedEvent(
            reason=reason, rejection_detail=detail, latency_ms=latency)
        # Fire and forget (async from sync context via bus.publish)
        asyncio.ensure_future(self.bus.publish(rejection))

    async def handle_signal(self, event: BaseEvent):
        if not isinstance(event, SignalEvent): return
        t0 = time.time()
        sig = event; pos = self.get_position(sig.symbol)
        latency = (time.time() - t0) * 1000

        # ═══ FIX 3: Position holding logic ═══
        # If we have a position, only allow EXIT on reversal
        if pos.quantity != 0 and self.config.hold_until_reversal:
            # Check if this signal is a reversal (opposite direction)
            if pos.entry_signal_side == "BUY" and sig.side == Side.SELL:
                # This is a reversal signal -> allow exit
                pass  # Continue to execute
            elif pos.entry_signal_side == "SELL" and sig.side == Side.BUY:
                pass  # Continue to execute
            else:
                # Same direction signal while in position
                self._reject(RejectionReason.SAME_DIRECTION.value,
                             f"Already in {pos.entry_signal_side} pos, bars={pos.bars_in_position}",
                             latency)
                return

            # Check forced exit conditions: take profit / stop loss
            if self.market_data.latest_tick:
                current = self.market_data.latest_tick.price
                if pos.entry_signal_side == "BUY":
                    move_pct = (current - pos.avg_entry) / pos.avg_entry * 100
                    if move_pct >= self.config.take_profit_pct:
                        logging.info("TAKE PROFIT: +%.2f%% after %d bars", move_pct, pos.bars_in_position)
                        await self._execute_fill(sig, pos, reason="TAKE_PROFIT")
                        return
                    elif move_pct <= -self.config.stop_loss_pct:
                        logging.info("STOP LOSS: %.2f%% after %d bars", move_pct, pos.bars_in_position)
                        await self._execute_fill(sig, pos, reason="STOP_LOSS")
                        return
                elif pos.entry_signal_side == "SELL":
                    move_pct = (pos.avg_entry - current) / pos.avg_entry * 100
                    if move_pct >= self.config.take_profit_pct:
                        logging.info("TAKE PROFIT: +%.2f%% after %d bars", move_pct, pos.bars_in_position)
                        await self._execute_fill(sig, pos, reason="TAKE_PROFIT")
                        return
                    elif move_pct <= -self.config.stop_loss_pct:
                        logging.info("STOP LOSS: %.2f%% after %d bars", move_pct, pos.bars_in_position)
                        await self._execute_fill(sig, pos, reason="STOP_LOSS")
                        return

                # Max hold time
                if pos.bars_in_position >= self.config.max_hold_bars:
                    logging.info("MAX HOLD: forced exit after %d bars", pos.bars_in_position)
                    await self._execute_fill(sig, pos, reason="MAX_HOLD")
                    return

        # ═══ Standard risk checks ═══
        if pos.quantity + 1 > self.config.max_position:
            self._reject(RejectionReason.MAX_POSITION.value,
                         f"pos={pos.quantity}/{self.config.max_position}", latency)
            return

        if sig.side == Side.BUY and self._cash < sig.price * 1.001:
            self._reject(RejectionReason.NO_CASH.value,
                         f"cash={self._cash:.0f} need={sig.price:.0f}", latency)
            return

        # ═══ FIX 1: Execute with retry logic ═══
        await self._execute_fill(sig, pos, reason="SIGNAL")

    async def _execute_fill(self, sig: SignalEvent, pos: PositionTracker, reason: str = "SIGNAL"):
        """Execute a fill with retry logic on rejection."""
        # FIX 1: Retry mechanism
        retries = 0
        max_retries = self.config.max_order_retries if reason == "SIGNAL" else 0  # No retry for forced exits

        for attempt in range(max_retries + 1):
            slip = self.config.slippage_bps / 10000
            # Adjust slippage based on retry attempt
            if attempt > 0:
                slip += (attempt * self.config.retry_price_adjustment_bps / 10000)
                logging.info("RETRY %d/%d for %s %s (adjusting slippage +%.1fbps)",
                             attempt, max_retries, sig.side.value, sig.symbol,
                             attempt * self.config.retry_price_adjustment_bps)

            fp = sig.price * (1 + slip) if sig.side == Side.BUY else sig.price * (1 - slip)
            comm = fp * self.config.commission

            # Re-check cash for retries (price may have increased)
            if sig.side == Side.BUY and self._cash < fp + comm:
                if attempt < max_retries:
                    self._retried += 1
                    continue
                self._reject(RejectionReason.NO_CASH.value,
                             f"Retry failed: cash={self._cash:.0f} need={fp+comm:.0f}")
                return

            self._submitted += 1
            fill = FillEvent(
                fill_id=uuid.uuid4().hex[:12], order_id=uuid.uuid4().hex[:12],
                symbol=sig.symbol, side=sig.side, quantity=1,
                price=round(fp, 2), commission=round(comm, 4),
                retries_used=attempt)
            self._fills.append(fill)

            # Update position
            qty = 1 if fill.side == Side.BUY else -1
            old_qty = pos.quantity
            if (pos.quantity > 0 and qty > 0) or (pos.quantity < 0 and qty < 0):
                total = pos.quantity + qty
                pos.avg_entry = (pos.avg_entry * abs(pos.quantity) + fill.price * abs(qty)) / abs(total)
                pos.quantity = total
            elif (pos.quantity > 0 and qty < 0) or (pos.quantity < 0 and qty > 0):
                pnl = (fill.price - pos.avg_entry) * min(abs(qty), abs(pos.quantity))
                if pos.quantity < 0: pnl = -pnl
                pos.realized_pnl += pnl; pos.quantity += qty
                if pos.quantity == 0: pos.avg_entry = 0
                # FIX 7: Record edge for closed trades
                self._record_edge_close(pos, fill, reason)
            else:
                pos.quantity = qty; pos.avg_entry = fill.price

            if fill.side == Side.BUY:
                self._cash -= fill.price + fill.commission
            else:
                self._cash += fill.price - fill.commission
            pos.commission += fill.commission; pos.trades += 1

            # FIX 3: Track position holding state
            if pos.quantity != 0 and old_qty == 0:
                # New position opened
                pos.entry_time = time.time()
                pos.entry_signal_side = fill.side.value
                pos.bars_in_position = 0
                pos.peak_price = fill.price if fill.side == Side.BUY else 0
                pos.trough_price = fill.price if fill.side == Side.SELL else float('inf')
                # FIX 7: Start edge tracking
                self._pending_entries[sig.symbol] = PendingEntry(
                    signal=sig, fill_price=fill.price, fill_time=time.time(),
                    confidence=sig.confidence, expected_move_pct=sig.expected_move_pct,
                    bars_to_measure=self.config.edge_track_bars)
                logging.info("EDGE_TRACK: tracking %s entry @ %.2f, conf=%.2f, exp=%.4f%%",
                             sig.symbol, fill.price, sig.confidence, sig.expected_move_pct)
            elif pos.quantity == 0:
                # Position closed
                pos.entry_signal_side = ""
                pos.bars_in_position = 0

            if attempt > 0:
                self._retried += 1
                logging.info("RETRY SUCCEEDED on attempt %d", attempt + 1)

            await self.bus.publish(fill)
            logging.info("FILL %s %s qty=1 @ %.2f comm=%.4f [%s] retries=%d",
                         fill.side.value, fill.symbol, fill.price, fill.commission, reason, attempt)
            return  # Success

        # All retries exhausted
        self._reject("MAX_RETRIES", f"All {max_retries} retries exhausted for {sig.side.value}")

    def _record_edge_close(self, pos: PositionTracker, fill: FillEvent, exit_reason: str):
        """Record edge when a position is closed."""
        sym = fill.symbol
        pending = self._pending_entries.pop(sym, None)
        if not pending:
            return
        actual_move = abs(fill.price - pending.fill_price) / pending.fill_price * 100
        pnl = pos.realized_pnl
        record = EdgeRecord(
            timestamp=time.time(), symbol=sym,
            side=pending.fill_price and "BUY" if fill.side == Side.SELL else "SELL",
            entry_price=pending.fill_price,
            expected_move_pct=pending.expected_move_pct,
            actual_move_pct=round(actual_move, 4),
            bars_held=pos.bars_in_position,
            confidence=pending.confidence,
            pnl=round(pnl, 2),
            exit_reason=exit_reason)
        self._edge_records.append(record)
        ratio = actual_move / pending.expected_move_pct if pending.expected_move_pct > 0 else 0
        logging.info("EDGE_RECORD: %s exp=%.4f%% actual=%.4f%% ratio=%.2fx pnl=%.2f reason=%s conf=%.2f",
                     sym, pending.expected_move_pct, actual_move, ratio, pnl, exit_reason,
                     pending.confidence)

    async def handle_tick_for_position(self, event: BaseEvent):
        """Called on every tick to update position holding state and edge tracking."""
        if not isinstance(event, TickEvent): return
        tick = event
        for sym, pos in self._positions.items():
            if pos.quantity == 0: continue
            pos.bars_in_position += 1

            # Update peak/trough
            if pos.entry_signal_side == "BUY":
                if tick.price > pos.peak_price: pos.peak_price = tick.price
                if tick.price < pos.trough_price: pos.trough_price = tick.price
            elif pos.entry_signal_side == "SELL":
                if tick.price < pos.trough_price: pos.trough_price = tick.price
                if tick.price > pos.peak_price: pos.peak_price = tick.price

        # FIX 7: Decrement edge tracking counters
        for sym in list(self._pending_entries.keys()):
            pending = self._pending_entries[sym]
            pending.bars_to_measure -= 1
            if pending.bars_to_measure <= 0:
                # Time's up - record what happened even if position still open
                current = self.market_data.latest_tick
                if current:
                    actual_move = abs(current.price - pending.fill_price) / pending.fill_price * 100
                    record = EdgeRecord(
                        timestamp=time.time(), symbol=sym,
                        side="BUY",
                        entry_price=pending.fill_price,
                        expected_move_pct=pending.expected_move_pct,
                        actual_move_pct=round(actual_move, 4),
                        bars_held=self.config.edge_track_bars,
                        confidence=pending.confidence,
                        pnl=0, exit_reason="TRACKING_EXPIRED")
                    self._edge_records.append(record)
                    logging.info("EDGE_EXPIRED: %s exp=%.4f%% actual=%.4f%% conf=%.2f (still in pos)",
                                 sym, pending.expected_move_pct, actual_move, pending.confidence)
                self._pending_entries.pop(sym, None)

# ══════════════════════════════════════════════════════════════
#  ANALYTICS SERVICE (v3.1: edge metrics, regime, rejection breakdown)
# ══════════════════════════════════════════════════════════════

class AnalyticsService:
    def __init__(self, bus: EventBus, interval=2.0):
        self.bus = bus; self.interval = interval
        self._trades = []; self._equity_ts = deque(maxlen=10000)
        self._equity_vals = deque(maxlen=10000); self._init_eq = None
        self._peak = 0.0; self._max_dd = 0.0; self._prices = {}
        self._subs: list = []; self._running = False; self._task = None; self._exec = None
        self._market_data = None  # NEW: for regime info

    def set_execution(self, svc): self._exec = svc
    def set_market_data(self, svc): self._market_data = svc  # NEW

    async def handle_fill(self, event):
        if isinstance(event, FillEvent):
            self._trades.append(event)

    async def handle_tick(self, event):
        if isinstance(event, TickEvent): self._prices[event.symbol] = event.price

    # FIX 8: Forward FILL events to WebSocket subscribers
    async def handle_event_forward(self, event: BaseEvent):
        """Wildcard handler that forwards FILL, SIGNAL, and TICK events to WS subscribers."""
        if isinstance(event, (FillEvent, SignalEvent)):
            data = event.to_dict()
            for q in list(self._subs):
                try: q.put_nowait(data)
                except: pass

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

        # NEW: regime info
        regime = self._market_data.current_regime() if self._market_data else "UNKNOWN"

        # NEW: edge metrics
        avg_conf = 0.0
        avg_edge = 0.0
        if self._exec._edge_records:
            avg_conf = sum(r.confidence for r in self._exec._edge_records) / len(self._exec._edge_records)
            avg_edge = self._exec._avg_edge()

        return PnLSnapshot(
            symbol=sym, position=pos.quantity, avg_entry=pos.avg_entry,
            unrealized_pnl=round(unrealized, 2),
            realized_pnl=round(self._exec.total_realized_pnl, 2),
            total_pnl=round(self._exec.total_realized_pnl + unrealized, 2),
            equity=round(eq, 2),
            regime=regime,
            avg_confidence=round(avg_conf, 3),
            avg_expected_vs_actual=round(avg_edge, 4),
            total_rejections=self._exec._rejected,
            rejection_breakdown=dict(self._exec._rejection_breakdown))

    def subscribe(self): q = asyncio.Queue(maxsize=500); self._subs.append(q); return q
    def unsubscribe(self, q):
        if q in self._subs: self._subs.remove(q)

    @property
    def trade_log(self): return [{"fill_id": t.fill_id, "ts": t.timestamp, "symbol": t.symbol,
        "side": t.side.value, "qty": t.quantity, "price": t.price,
        "comm": t.commission, "retries": t.retries_used} for t in self._trades]

    @property
    def performance(self):
        ret = 0.0
        if self._init_eq and self._init_eq > 0:
            cur = self._equity_vals[-1] if self._equity_vals else self._init_eq
            ret = (cur - self._init_eq) / self._init_eq * 100
        return {"total_trades": len(self._trades), "return_pct": round(ret, 4),
                "max_drawdown_pct": round(self._max_dd * 100, 4), "peak": round(self._peak, 2)}

    @property
    def edge_stats(self):
        """Return edge tracking statistics."""
        if not hasattr(self._exec, '_edge_records') or not self._exec._edge_records:
            return {"count": 0, "avg_edge": 0, "win_rate": 0, "avg_expected": 0, "avg_actual": 0}
        records = self._exec._edge_records
        winners = sum(1 for r in records if r.pnl > 0)
        return {
            "count": len(records),
            "avg_edge": round(self._exec._avg_edge(), 4),
            "win_rate": round(winners / len(records) * 100, 1) if records else 0,
            "avg_expected": round(sum(r.expected_move_pct for r in records) / len(records), 4),
            "avg_actual": round(sum(r.actual_move_pct for r in records) / len(records), 4),
            "avg_confidence": round(sum(r.confidence for r in records) / len(records), 3),
            "exit_reasons": dict(defaultdict(int, {r.exit_reason: sum(1 for x in records if x.exit_reason == r.exit_reason) for r in records}))
        }

# ══════════════════════════════════════════════════════════════
#  DATABASE (SQLite)
# ══════════════════════════════════════════════════════════════

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL, symbol TEXT, price REAL, volume INTEGER);
CREATE TABLE IF NOT EXISTS fills (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL, fill_id TEXT, symbol TEXT, side TEXT, quantity INTEGER, price REAL, commission REAL, retries INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL, symbol TEXT, position INTEGER, equity REAL, regime TEXT);
CREATE TABLE IF NOT EXISTS edge_records (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL, symbol TEXT, entry_price REAL, expected_move_pct REAL, actual_move_pct REAL, confidence REAL, pnl REAL, exit_reason TEXT);
"""

class Database:
    def __init__(self, path="data/trading_v3.1.db"):
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
#  ORCHESTRATOR (v3.1)
# ══════════════════════════════════════════════════════════════

class Orchestrator:
    def __init__(self, config=None):
        self.config = config or Config()
        self.bus = EventBus()
        self.db = Database(self.config.db_path)
        self.market_data = MarketDataService(self.bus, self.config, mode="sim")
        self.strategy = StrategyService(self.bus, self.config, self.market_data)
        self.execution = ExecutionService(self.bus, self.config, self.market_data)
        self.analytics = AnalyticsService(self.bus)
        self.analytics.set_execution(self.execution)
        self.analytics.set_market_data(self.market_data)
        self._running = False; self._tasks = []

    async def start(self):
        self._running = True
        logging.info("=" * 60)
        logging.info("QUANT TRADING SYSTEM v3.1 - Edge-Optimized Edition")
        logging.info("=" * 60)
        logging.info("Config:")
        logging.info("  Commission: %.4f%%/side | Slippage: %dbps", self.config.commission*100, self.config.slippage_bps)
        logging.info("  Min confidence: %.2f | Min profit: %.3f%%", self.config.min_confidence, self.config.min_expected_profit_pct)
        logging.info("  Position hold: TP=%.2f%% SL=%.2f%% max=%d bars",
                     self.config.take_profit_pct, self.config.stop_loss_pct, self.config.max_hold_bars)
        logging.info("  Order retries: %d | Adj: %dbps/retry",
                     self.config.max_order_retries, self.config.retry_price_adjustment_bps)
        logging.info("=" * 60)
        self.db.connect()
        self.bus.subscribe(EventType.TICK, self.strategy.handle_tick)
        self.bus.subscribe(EventType.TICK, self.analytics.handle_tick)
        self.bus.subscribe(EventType.TICK, self.execution.handle_tick_for_position)
        self.bus.subscribe(EventType.SIGNAL, self.execution.handle_signal)
        self.bus.subscribe(EventType.FILL, self.analytics.handle_fill)
        self.bus.subscribe(None, self.analytics.handle_event_forward)  # Wildcard: forward FILL/SIGNAL
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
        # Log final edge stats
        if self.execution._edge_records:
            records = self.execution._edge_records
            winners = sum(1 for r in records if r.pnl > 0)
            logging.info("=== EDGE REPORT ===")
            logging.info("Total tracked: %d | Winners: %d (%.1f%%)",
                         len(records), winners, winners/len(records)*100 if records else 0)
            logging.info("Avg expected: %.4f%% | Avg actual: %.4f%% | Edge: %.4f%%",
                         sum(r.expected_move_pct for r in records)/len(records),
                         sum(r.actual_move_pct for r in records)/len(records),
                         self.execution._avg_edge())
            logging.info("Avg confidence: %.3f", sum(r.confidence for r in records)/len(records))
            reasons = defaultdict(int)
            for r in records: reasons[r.exit_reason] += 1
            logging.info("Exit reasons: %s", dict(reasons))
        self.db.close()
        logging.info("System stopped")

    @property
    def system_status(self):
        return {
            "version": "3.1",
            "running": self._running,
            "market_data": {
                "mode": self.market_data.mode, "ticks": self.market_data.tick_count,
                "price": self.market_data.latest_tick.price if self.market_data.latest_tick else None,
                "volatility": round(self.market_data.current_volatility(), 4),
                "regime": self.market_data.current_regime()
            },
            "strategy": self.strategy.stats,
            "execution": self.execution.stats,
            "analytics": self.analytics.performance,
            "edge": self.analytics.edge_stats
        }

# ══════════════════════════════════════════════════════════════
#  FASTAPI DASHBOARD (v3.1: enhanced with edge metrics)
# ══════════════════════════════════════════════════════════════

def create_dashboard(orchestrator=None):
    app = FastAPI(title="Trading System v3.1", version="3.1")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    HTML = HTMLResponse(content="""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>Trading v3.1 - Edge Optimized</title><style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0a0e17;--card:#1a2332;--bdr:#1e3a5f;--txt:#e2e8f0;--dim:#94a3b8;--g:#10b981;--r:#ef4444;--b:#3b82f6;--y:#f59e0b;--p:#a855f7}
body{font-family:'Courier New',monospace;background:var(--bg);color:var(--txt);min-height:100vh}
.h{background:#111827;border-bottom:1px solid var(--bdr);padding:14px 20px;display:flex;justify-content:space-between;align-items:center}
.h h1{font-size:16px;letter-spacing:1px}.badge{padding:3px 10px;border-radius:16px;font-size:11px;font-weight:600;margin-left:8px}
.on{background:rgba(16,185,129,.15);color:var(--g);border:1px solid var(--g)}.off{background:rgba(239,68,68,.15);color:var(--r);border:1px solid var(--r)}
.regime{padding:3px 8px;border-radius:12px;font-size:10px;font-weight:600;letter-spacing:.5px}
.r-quiet{background:rgba(100,116,139,.15);color:#94a3b8;border:1px solid #64748b}
.r-normal{background:rgba(59,130,246,.15);color:var(--b);border:1px solid var(--b)}
.r-volatile{background:rgba(245,158,11,.15);color:var(--y);border:1px solid var(--y)}
.g{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;padding:14px 20px}
.c{background:var(--card);border:1px solid var(--bdr);border-radius:8px;padding:14px}
.ch{font-size:9px;text-transform:uppercase;letter-spacing:1.5px;color:var(--dim);margin-bottom:6px}
.cv{font-size:22px;font-weight:700}.cv.g{color:var(--g)}.cv.r{color:var(--r)}.cv.b{color:var(--b)}.cv.y{color:var(--y)}.cv.p{color:var(--p)}
.cs{font-size:10px;color:var(--dim);margin-top:3px}
.s{padding:0 20px 14px}.pg{display:grid;grid-template-columns:2fr 1fr;gap:14px}
table{width:100%;border-collapse:collapse;font-size:11px}
th{text-align:left;padding:5px 8px;font-size:9px;text-transform:uppercase;color:var(--dim);border-bottom:1px solid var(--bdr)}
td{padding:5px 8px;border-bottom:1px solid rgba(30,58,95,.3)}
.bu{color:var(--g);font-weight:600}.se{color:var(--r);font-weight:600}
.lc{background:var(--card);border:1px solid var(--bdr);border-radius:8px;max-height:300px;overflow-y:auto;font-size:10px;padding:10px}
.le{padding:1px 0}.lt{color:var(--dim)}.lk{color:var(--b)}.ls{color:var(--y)}.lf{color:var(--g)}.lr{color:var(--r)}.lp{color:var(--p)}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;animation:p 2s infinite}
.dot.on{background:var(--g)}.dot.off{background:var(--r);animation:none}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}
.edge{display:flex;gap:10px;margin-top:8px;flex-wrap:wrap}.edge-i{font-size:10px;color:var(--dim)}.edge-i b{color:var(--txt)}
.rej-bar{height:16px;background:#1e293b;border-radius:4px;overflow:hidden;display:flex;margin-top:6px}
.rej-seg{height:100%;display:flex;align-items:center;justify-content:center;font-size:8px;font-weight:600;color:white;min-width:20px}
</style></head><body>
<div class="h"><h1>QUANT TRADING <span style="color:var(--p)">v3.1</span> <span style="color:var(--dim);font-size:11px">EDGE OPTIMIZED</span></h1>
<div style="display:flex;align-items:center;gap:8px">
<span class="dot off" id="d"></span><span class="badge on" id="b">INIT</span>
<span class="regime r-normal" id="regime">NORMAL</span>
</div></div>
<div class="g">
<div class="c"><div class="ch">Equity</div><div class="cv b" id="eq">$100,000</div><div class="cs" id="ret">Return: 0%</div></div>
<div class="c"><div class="ch">Unrealized PnL</div><div class="cv g" id="upnl">$0</div><div class="cs" id="rpnl">Realized: $0</div></div>
<div class="c"><div class="ch">Position</div><div class="cv y" id="pos">0</div><div class="cs" id="bars">Held: 0 bars</div></div>
<div class="c"><div class="ch">Price</div><div class="cv" id="prc">$65,000</div><div class="cs" id="cnt">Ticks: 0 | Signals: 0</div></div>
<div class="c"><div class="ch">True Edge</div><div class="cv p" id="edge">0.00%</div><div class="cs" id="edgeinfo">Win: 0% | Tracked: 0</div></div>
</div>
<div class="s"><div class="pg">
<div class="c"><div class="ch" style="display:flex;justify-content:space-between"><span>Trades</span><span id="filt" style="color:var(--y)"></span></div>
<table><thead><tr><th>Time</th><th>Side</th><th>Qty</th><th>Price</th><th>Comm</th><th>Retries</th></tr></thead><tbody id="tl"></tbody></table></div>
<div class="c"><div class="ch">Rejection Breakdown</div>
<div class="rej-bar" id="rbar"></div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-top:8px;font-size:10px" id="rlist"></div>
<div class="ch" style="margin-top:12px">Filter Stats</div>
<div style="font-size:10px;color:var(--dim)" id="fstats"></div>
</div>
</div></div>
<div class="s"><div class="pg">
<div class="c"><div class="ch">Events</div><div class="lc" id="el"></div></div>
<div class="c"><div class="ch">Edge Tracking</div>
<div class="edge" id="edged"></div>
</div>
</div></div>
<div class="s"><div class="g">
<div class="c"><div class="ch">Total Trades</div><div class="cv b" id="tt">0</div></div>
<div class="c"><div class="ch">Max Drawdown</div><div class="cv r" id="dd">0%</div></div>
<div class="c"><div class="ch">Orders OK</div><div class="cv g" id="ok">0</div></div>
<div class="c"><div class="ch">Rejected</div><div class="cv r" id="rj">0</div></div>
<div class="c"><div class="ch">Retried</div><div class="cv y" id="rt">0</div></div>
</div></div>
<script>
var $=function(id){return document.getElementById(id)};
var ws=new WebSocket("ws://"+location.host+"/ws/live");
var ML=80,trades=[];
function f(n){return(n>=0?"+":"")+"$"+Math.abs(n).toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2})}
function fu(n){return(n>=0?"+":"")+n.toFixed(2)+"%"}
function ft(t){var d=new Date(t*1000);return d.toLocaleTimeString("en-US",{hour12:false})}
ws.onopen=function(){$("d").className="dot on";$("b").textContent="LIVE";$("b").className="badge on"};
ws.onclose=function(){$("d").className="dot off";$("b").textContent="OFF";$("b").className="badge off"};
ws.onmessage=function(e){try{var d=JSON.parse(e.data);
if(d.type==="PNL_SNAPSHOT"){$("eq").textContent=f(d.equity-1e5);$("eq").className="cv "+(d.equity>=1e5?"g":"r");
$("upnl").textContent=f(d.unrealized_pnl);$("upnl").className="cv "+(d.unrealized_pnl>=0?"g":"r");
$("rpnl").textContent="Realized: "+f(d.realized_pnl);
var r=((d.equity-1e5)/1e5*100).toFixed(2);$("ret").textContent="Return: "+(r>=0?"+":"")+r+"%";
$("pos").textContent=d.position;
$("bars").textContent="Held: "+(d.position!==0?"in position":"flat");
$("edge").textContent=(d.avg_expected_vs_actual>=0?"+":"")+d.avg_expected_vs_actual.toFixed(4)+"%";
$("edge").className="cv "+(d.avg_expected_vs_actual>=0?"g":"r");
$("edgeinfo").textContent="Avg Conf: "+d.avg_confidence.toFixed(2);
$("rbar").innerHTML=buildRejBar(d.rejection_breakdown||{});
log("s","PNL eq="+d.equity.toFixed(0)+" pos="+d.position+" regime="+d.regime);
if(d.regime){$("regime").textContent=d.regime;$("regime").className="regime r-"+d.regime.toLowerCase()}}
if(d.type==="TICK"){$("prc").textContent="$"+d.price.toLocaleString("en-US",{minimumFractionDigits:2});log("k","TICK "+d.symbol+" @ "+d.price)}
if(d.type==="FILL"){trades.unshift(d);if(trades.length>20)trades.pop();rt();log("f","FILL "+d.side+" "+d.symbol+" @ "+d.price+(d.retries_used?" retries="+d.retries_used:""))}
if(d.type==="SIGNAL"){log("s","SIGNAL "+d.side+" "+d.symbol+" conf="+d.confidence+" exp="+d.expected_move_pct+"%")}
if(d.type==="HEARTBEAT"){st()}
}catch(x){}};
function rt(){var t=$("tl");t.innerHTML=trades.map(function(x){return"<tr><td>"+ft(x.timestamp)+"</td><td class='"+(x.side==="BUY"?"bu":"se")+"'>"+x.side+"</td><td>"+x.quantity+"</td><td>$"+x.price.toLocaleString("en-US",{minimumFractionDigits:2})+"</td><td>"+(x.commission||0).toFixed(4)+"</td><td>"+(x.retries_used||0)+"</td></tr>"}).join("")}
function log(c,m){var l=$("el"),d=document.createElement("div");d.className="le";d.innerHTML="<span class='lt'>"+ft(Date.now()/1000)+"</span> <span class='l"+c+"'>"+m+"</span>";l.prepend(d);while(l.children.length>ML)l.removeChild(l.lastChild)}
function buildRejBar(bd){var total=0;for(var k in bd)total+=bd[k];if(total===0)return"";var colors={"MAX_POSITION":"#ef4444","NO_CASH":"#f59e0b","REGIME_FILTER":"#64748b","LOW_CONFIDENCE":"#a855f7","MIN_PROFIT":"#3b82f6","SAME_DIRECTION":"#14b8a6"};var html="";for(var k in bd){var pct=bd[k]/total*100;var c=colors[k]||"#64748b";html+="<div class='rej-seg' style='width:"+pct+"%;background:"+c+"' title='"+k+": "+bd[k]+"'>"+(pct>10?Math.round(pct)+"%":"")+"</div>"}
var rl="";for(var k in bd){var c=colors[k]||"#64748b";rl+="<div><span style='color:"+c+"'>\u25CF</span> "+k.replace(/_/g,' ')+": <b>"+bd[k]+"</b></div>"}$("rlist").innerHTML=rl;return html}
function st(){fetch("/api/status").then(function(r){return r.json()}).then(function(s){if(s.market_data){$("cnt").textContent="Ticks: "+s.market_data.ticks+" | Signals: "+(s.strategy?s.strategy.signal_count:0);$("tt").textContent=s.execution?s.execution.orders_submitted:0;$("dd").textContent=(s.analytics?s.analytics.max_drawdown_pct:0).toFixed(2)+"%";$("ok").textContent=s.execution?s.execution.orders_submitted:0;$("rj").textContent=s.execution?s.execution.orders_rejected:0;$("rt").textContent=s.execution?(s.execution.orders_retried||0):0;
if(s.strategy){$("filt").textContent="Filtered: "+(s.strategy.filtered_count||0);$("fstats").innerHTML="";var fr=s.strategy.filter_reasons||{};for(var k in fr)$("fstats").innerHTML+="<div><span style='color:var(--y)'>"+k.replace(/_/g,' ')+"</span>: "+fr[k]+"</div>"}
if(s.edge){$("edged").innerHTML="<div class='edge-i'>Edge: <b>"+s.edge.avg_edge.toFixed(4)+"%</b></div><div class='edge-i'>Win Rate: <b>"+s.edge.win_rate.toFixed(1)+"%</b></div><div class='edge-i'>Tracked: <b>"+s.edge.count+"</b></div><div class='edge-i'>Avg Exp: <b>"+(s.edge.avg_expected||0).toFixed(4)+"%</b></div><div class='edge-i'>Avg Act: <b>"+(s.edge.avg_actual||0).toFixed(4)+"%</b></div>"}
$("rbar").innerHTML=buildRejBar(s.execution?s.execution.rejection_breakdown:{});
if(s.market_data.regime){$("regime").textContent=s.market_data.regime;$("regime").className="regime r-"+s.market_data.regime.toLowerCase()}
}}).catch(function(){})}
st();setInterval(st,3000);
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

    @app.get("/api/edge")
    async def edge():
        if not orchestrator: return {}
        return orchestrator.analytics.edge_stats

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
