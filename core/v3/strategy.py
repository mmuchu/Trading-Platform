"""V3 Strategy Service — evaluates built-in strategies on incoming ticks
and publishes SignalEvents when entry conditions are met."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

import numpy as np

from core.v3.event_bus import EventBus
from core.v3.models import (
    BaseEvent,
    SignalEvent,
    SignalSource,
    Side,
    TickEvent,
)

logger = logging.getLogger(__name__)


class BaseStrategy(ABC):
    """Abstract base for every v3 strategy."""

    name: str = ""

    @abstractmethod
    def evaluate(
        self,
        tick: TickEvent,
        history: Deque[TickEvent],
    ) -> Optional[SignalEvent]:
        """Return a signal if conditions are met, else None."""


class MomentumV3(BaseStrategy):
    """Simple price-momentum strategy over a configurable lookback window."""

    name = "MomentumV3"

    def __init__(
        self,
        lookback: int = 20,
        buy_threshold_pct: float = 0.001,
        sell_threshold_pct: float = -0.001,
    ) -> None:
        self.lookback = lookback
        self.buy_threshold_pct = buy_threshold_pct
        self.sell_threshold_pct = sell_threshold_pct

    def evaluate(
        self,
        tick: TickEvent,
        history: Deque[TickEvent],
    ) -> Optional[SignalEvent]:
        if len(history) < self.lookback:
            return None

        prices = np.array([t.price for t in history], dtype=np.float64)
        pct_change = (prices[-1] - prices[-self.lookback]) / prices[-self.lookback]

        side: Optional[Side] = None
        if pct_change > self.buy_threshold_pct:
            side = Side.BUY
        elif pct_change < self.sell_threshold_pct:
            side = Side.SELL

        if side is None:
            return None

        strength = min(abs(pct_change) / 0.005, 1.0)
        return SignalEvent(
            symbol=tick.symbol,
            side=side,
            price=tick.price,
            strength=round(strength, 4),
            source=SignalSource.RULE_ENGINE,
            metadata={"strategy": self.name, "pct_change": round(pct_change, 6)},
        )


class MeanReversionV3(BaseStrategy):
    """Z-score mean-reversion strategy."""

    name = "MeanReversionV3"

    def __init__(
        self,
        lookback: int = 50,
        entry_z: float = 2.0,
    ) -> None:
        self.lookback = lookback
        self.entry_z = entry_z

    def evaluate(
        self,
        tick: TickEvent,
        history: Deque[TickEvent],
    ) -> Optional[SignalEvent]:
        if len(history) < self.lookback:
            return None

        prices = np.array([t.price for t in history], dtype=np.float64)
        mean = np.mean(prices)
        std = np.std(prices)

        if std == 0:
            return None

        z = (tick.price - mean) / std

        side: Optional[Side] = None
        if z < -self.entry_z:
            side = Side.BUY
        elif z > self.entry_z:
            side = Side.SELL

        if side is None:
            return None

        strength = min(abs(z) / (self.entry_z * 2), 1.0)
        return SignalEvent(
            symbol=tick.symbol,
            side=side,
            price=tick.price,
            strength=round(strength, 4),
            source=SignalSource.RULE_ENGINE,
            metadata={"strategy": self.name, "z_score": round(z, 4)},
        )


class V3StrategyService:
    """Subscribes to TICK events, runs every registered strategy, and
    publishes the first qualifying SignalEvent (respecting a cooldown)."""

    def __init__(self, bus: EventBus, cooldown: float = 1.0) -> None:
        self.bus = bus
        self.cooldown = cooldown

        self.strategies: List[BaseStrategy] = [
            MomentumV3(),
            MeanReversionV3(),
        ]

        self._history: Dict[str, Deque[TickEvent]] = {}
        self._last_signal_time: float = 0.0
        self._signals_emitted: int = 0
        self._tick_count: int = 0

    @property
    def stats(self) -> dict:
        return {"tick_count": self._tick_count, "signal_count": self._signals_emitted,
                "strategies": [s.name for s in self.strategies]}

    async def start(self) -> None:
        logger.info(
            "V3StrategyService started with %d strategies",
            len(self.strategies),
        )

    async def stop(self) -> None:
        logger.info("V3StrategyService stopped")

    async def handle_tick(self, event: BaseEvent) -> None:
        tick = event
        if not isinstance(tick, TickEvent):
            return

        self._tick_count += 1

        history = self._history.setdefault(tick.symbol, deque(maxlen=500))
        history.append(tick)

        now = time.time()
        if now - self._last_signal_time < self.cooldown:
            return

        for strategy in self.strategies:
            signal = strategy.evaluate(tick, history)
            if signal is not None:
                await self.bus.publish(signal)
                self._last_signal_time = now
                self._signals_emitted += 1
                logger.info(
                    "Signal emitted: %s %s @ %.2f (strength=%.2f, strategy=%s)",
                    signal.side.value,
                    signal.symbol,
                    signal.price,
                    signal.strength,
                    strategy.name,
                )
                return