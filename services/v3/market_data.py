"""V3 Market Data Service — produces synthetic or live TickEvents.

v3.2: Added live Binance REST polling with exponential backoff on failures.
The feed automatically recovers from DNS errors and connection timeouts without
spamming logs.  When the feed is dead, Gate 1 (FeedMonitor) blocks all trades.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from typing import Optional

import aiohttp

from config.settings import settings
from core.v3.event_bus import EventBus
from core.v3.models import TickEvent

logger = logging.getLogger(__name__)

# Default simulated parameters
_DEFAULT_SYMBOL = "BTCUSDT"
_BASE_PRICE = 65_000.0
_MEAN_REVERSION = 0.01
_VOLATILITY = 50.0
_HALF_SPREAD = 2.5
_TICK_INTERVAL = 0.5

# Live mode parameters
_LIVE_POLL_INTERVAL = 5.0
_BACKOFF_INITIAL = 5.0
_BACKOFF_MAX = 120.0
_BACKOFF_MULTIPLIER = 2.0
_LOG_SUPPRESS_INTERVAL = 30.0


class V3MarketDataService:
    """Async market-data feed backed by either a random-walk simulator or
    a live Binance REST polling connection.

    Live mode uses exponential backoff on failures to avoid log spam and
    allow automatic recovery from DNS errors / timeouts.

    Parameters
    ----------
    bus:
        The shared v3 :class:`EventBus`.
    mode:
        ``"sim"`` for synthetic ticks, ``"live"`` for a real feed.
    """

    def __init__(self, bus: EventBus, mode: str = "sim") -> None:
        self.bus = bus
        self.mode = mode

        self.tick_buffer: deque[TickEvent] = deque(maxlen=10_000)
        self.latest_tick: Optional[TickEvent] = None
        self._tick_count: int = 0

        self._price: float = _BASE_PRICE
        self._task: Optional[asyncio.Task] = None
        self._running: bool = False

        self._backoff_current: float = _BACKOFF_INITIAL
        self._consecutive_errors: int = 0
        self._last_error_log: float = 0.0
        self._feed_dead: bool = False
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def tick_count(self) -> int:
        return self._tick_count

    @property
    def feed_alive(self) -> bool:
        if self.mode == "sim":
            return True
        return not self._feed_dead

    def recent_ticks(self, n: int = 100) -> list[TickEvent]:
        return list(self.tick_buffer)[-n:]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            logger.warning("V3MarketDataService already running")
            return

        self._running = True

        if self.mode == "sim":
            logger.info("Starting V3MarketDataService in SIM mode (symbol=%s)", _DEFAULT_SYMBOL)
            self._task = asyncio.create_task(self._sim_loop(), name="market_data_sim")
        else:
            logger.info(
                "Starting V3MarketDataService in LIVE mode (symbol=%s, poll=%.1fs)",
                settings.binance.symbol.upper(),
                _LIVE_POLL_INTERVAL,
            )
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
            )
            self._task = asyncio.create_task(self._live_loop(), name="market_data_live")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("V3MarketDataService stopped")

    # ------------------------------------------------------------------
    # Simulation loop
    # ------------------------------------------------------------------

    async def _sim_loop(self) -> None:
        while self._running:
            tick = self._next_sim_tick()
            self.tick_buffer.append(tick)
            self.latest_tick = tick
            self._tick_count += 1
            await self.bus.publish(tick)
            await asyncio.sleep(_TICK_INTERVAL)

    def _next_sim_tick(self) -> TickEvent:
        drift = _MEAN_REVERSION * (_BASE_PRICE - self._price)
        noise = random.gauss(0, _VOLATILITY)
        self._price += drift + noise
        self._price = max(self._price, _BASE_PRICE * 0.5)
        self._price = min(self._price, _BASE_PRICE * 1.5)

        mid = self._price
        bid = round(mid - _HALF_SPREAD, 2)
        ask = round(mid + _HALF_SPREAD, 2)
        return TickEvent(
            symbol=_DEFAULT_SYMBOL,
            price=mid,
            volume=random.randint(1, 500),
            bid=bid,
            ask=ask,
        )

    # ------------------------------------------------------------------
    # Live loop with exponential backoff
    # ------------------------------------------------------------------

    async def _live_loop(self) -> None:
        while self._running:
            success = await self._fetch_live_price()
            if success:
                if self._feed_dead:
                    logger.info(
                        "Feed RECOVERED after %d consecutive errors — resuming normal polling",
                        self._consecutive_errors,
                    )
                self._consecutive_errors = 0
                self._backoff_current = _BACKOFF_INITIAL
                self._feed_dead = False
                await asyncio.sleep(_LIVE_POLL_INTERVAL)
            else:
                self._consecutive_errors += 1
                self._feed_dead = True

                now = time.time()
                if now - self._last_error_log >= _LOG_SUPPRESS_INTERVAL:
                    logger.error(
                        "Feed DEAD (error #%d) — backing off %.1fs. "
                        "Gate 1 will block all trades until recovery.",
                        self._consecutive_errors,
                        self._backoff_current,
                    )
                    self._last_error_log = now

                await asyncio.sleep(self._backoff_current)

                self._backoff_current = min(
                    self._backoff_current * _BACKOFF_MULTIPLIER,
                    _BACKOFF_MAX,
                )

    async def _fetch_live_price(self) -> bool:
        symbol = settings.binance.symbol.upper()
        url = f"{settings.binance.rest_base}/ticker/price"
        params = {"symbol": symbol}

        try:
            async with self._session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    price = float(data["price"])
                    tick = TickEvent(
                        symbol=symbol,
                        price=price,
                        volume=0,
                        bid=round(price - _HALF_SPREAD, 2),
                        ask=round(price + _HALF_SPREAD, 2),
                    )
                    self.tick_buffer.append(tick)
                    self.latest_tick = tick
                    self._tick_count += 1
                    await self.bus.publish(tick)
                    return True
                else:
                    logger.warning(
                        "Binance API returned status %d for %s",
                        resp.status, symbol,
                    )
                    return False
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            return False