"""V3 Strategy Service.""" 
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
