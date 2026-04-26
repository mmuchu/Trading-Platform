"""
Gate 1 — Feed Monitor.

Tracks the health of the market data feed.  A feed is considered *alive* when
ticks arrive within a configurable timeout window.  If the feed dies, the
monitor blocks all new executions until ticks resume.

This directly addresses the v2 bug where trades continued even though the
Binance heartbeat was 0 and the feed showed "--".
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from core.v3.event_bus import EventBus
from core.v3.models import BaseEvent, EventType, TickEvent

logger = logging.getLogger(__name__)


@dataclass
class FeedHealth:
    """Snapshot of the feed's current health."""

    alive: bool = False
    last_tick_time: float = 0.0
    last_tick_price: float = 0.0
    tick_count: int = 0
    stale_seconds: float = 0.0
    symbol: str = ""
    gaps_detected: int = 0
    consecutive_gaps: int = 0


class FeedMonitor:
    """Monitors market-data feed health and can block trading when the feed
    is stale.

    Parameters
    ----------
    bus : EventBus
        Shared event bus to subscribe to TICK events.
    stale_threshold_sec : float
        Maximum seconds without a tick before the feed is considered dead.
    gap_threshold_sec : float
        Maximum expected interval between consecutive ticks.  If the gap
        exceeds this, a "gap" is recorded (potential data loss).
    """

    def __init__(
        self,
        bus: EventBus,
        stale_threshold_sec: float = 15.0,
        gap_threshold_sec: float = 10.0,
    ) -> None:
        self.bus = bus
        self.stale_threshold_sec = stale_threshold_sec
        self.gap_threshold_sec = gap_threshold_sec

        # Per-symbol state
        self._last_tick_time: Dict[str, float] = {}
        self._last_tick_price: Dict[str, float] = {}
        self._prev_tick_time: Dict[str, float] = {}
        self._tick_counts: Dict[str, int] = {}
        self._gaps: Dict[str, int] = {}
        self._consecutive_gaps: Dict[str, int] = {}

        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        self.bus.subscribe(EventType.TICK, self._on_tick)
        self._started = True
        logger.info(
            "FeedMonitor started (stale=%.1fs, gap=%.1fs)",
            self.stale_threshold_sec,
            self.gap_threshold_sec,
        )

    async def stop(self) -> None:
        if not self._started:
            return
        self.bus.unsubscribe(EventType.TICK, self._on_tick)
        self._started = False
        logger.info("FeedMonitor stopped")

    # ------------------------------------------------------------------
    # Tick handler
    # ------------------------------------------------------------------

    async def _on_tick(self, event: BaseEvent) -> None:
        if not isinstance(event, TickEvent):
            return
        sym = event.symbol
        now = event.timestamp

        # Detect gaps
        if sym in self._prev_tick_time:
            gap = now - self._prev_tick_time[sym]
            if gap > self.gap_threshold_sec:
                self._gaps[sym] = self._gaps.get(sym, 0) + 1
                self._consecutive_gaps[sym] = self._consecutive_gaps.get(sym, 0) + 1
                if gap > 10 * self.gap_threshold_sec:
                    logger.warning(
                        "Feed gap on %s: %.1fs without data (gap #%d)",
                        sym, gap, self._gaps[sym],
                    )
            else:
                self._consecutive_gaps[sym] = 0
        else:
            self._consecutive_gaps[sym] = 0

        self._prev_tick_time[sym] = self._last_tick_time.get(sym, now)
        self._last_tick_time[sym] = now
        self._last_tick_price[sym] = event.price
        self._tick_counts[sym] = self._tick_counts.get(sym, 0) + 1

    # ------------------------------------------------------------------
    # Gate check
    # ------------------------------------------------------------------

    def is_alive(self, symbol: str = "") -> tuple[bool, str]:
        """Check if the feed is alive for *symbol*.

        Returns
        -------
        (alive, reason) : tuple[bool, str]
            ``alive`` is True when the feed is healthy.  ``reason`` explains
            any failure.
        """
        sym = symbol or next(iter(self._last_tick_time), "")
        if not sym:
            return False, "No feed data received yet"

        last = self._last_tick_time.get(sym, 0.0)
        if last == 0.0:
            return False, f"No ticks received for {sym}"

        stale = time.time() - last
        if stale > self.stale_threshold_sec:
            return False, (
                f"Feed stale for {sym}: {stale:.1f}s > {self.stale_threshold_sec:.1f}s"
            )

        # Check for excessive consecutive gaps (data stream instability)
        consec = self._consecutive_gaps.get(sym, 0)
        if consec >= 5:
            return False, f"Feed unstable: {consec} consecutive gaps on {sym}"

        return True, ""

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def health(self, symbol: str = "") -> FeedHealth:
        """Return a FeedHealth snapshot."""
        sym = symbol or next(iter(self._last_tick_time), "")
        now = time.time()
        last = self._last_tick_time.get(sym, 0.0)
        alive, _ = self.is_alive(sym)
        return FeedHealth(
            alive=alive,
            last_tick_time=last,
            last_tick_price=self._last_tick_price.get(sym, 0.0),
            tick_count=self._tick_counts.get(sym, 0),
            stale_seconds=now - last if last else float("inf"),
            symbol=sym,
            gaps_detected=self._gaps.get(sym, 0),
            consecutive_gaps=self._consecutive_gaps.get(sym, 0),
        )

    def health_dict(self, symbol: str = "") -> Dict[str, Any]:
        h = self.health(symbol)
        return {
            "alive": h.alive,
            "last_tick_time": h.last_tick_time,
            "last_tick_price": h.last_tick_price,
            "tick_count": h.tick_count,
            "stale_seconds": round(h.stale_seconds, 2),
            "symbol": h.symbol,
            "gaps_detected": h.gaps_detected,
            "consecutive_gaps": h.consecutive_gaps,
        }
