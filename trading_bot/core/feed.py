"""
Market Feed - Price ingestion from Binance REST API or simulated data.

Supports two modes:
  - live:  Polls Binance REST API every N seconds
  - sim:   Generates random-walk price ticks locally (for testing/backtest)
"""

import logging
import random
import time
from typing import Optional, Callable

from trading_bot.config import Config

logger = logging.getLogger("feed")


class Feed:
    """Unified market data feed (live or simulated)."""

    def __init__(self, config: Config, mode: str = "live"):
        self.config = config
        self.mode = mode
        self.price: Optional[float] = None
        self.history: list[float] = []
        self._tick_count: int = 0
        self._sim_price: float = config.BACKTEST_START_PRICE

    def get_price(self) -> float:
        """Fetch a single price tick. Blocks in live mode."""
        if self.mode == "live":
            return self._fetch_live()
        else:
            return self._simulate_tick()

    def _fetch_live(self) -> float:
        """Poll Binance REST API for current price."""
        import requests
        try:
            url = f"{self.config.Binance_API_URL}?symbol={self.config.SYMBOL}"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            self.price = float(data["price"])
            self._record(self.price)
            logger.debug("LIVE %s @ %.2f", self.config.SYMBOL, self.price)
            return self.price
        except Exception as e:
            logger.error("Feed error: %s", e)
            if self.price is not None:
                logger.warning("Using last known price: %.2f", self.price)
                return self.price
            raise

    def _simulate_tick(self) -> float:
        """Generate a simulated price tick with mean reversion and occasional trends."""
        drift = random.gauss(0, self.config.BACKTEST_VOLATILITY)
        # Mean reversion toward start price (weak pull)
        revert = 0.002 * (self.config.BACKTEST_START_PRICE - self._sim_price)
        # Occasional trend burst (3% chance)
        if random.random() < 0.03:
            drift = random.gauss(0, self.config.BACKTEST_VOLATILITY * 5)

        self._sim_price = max(100.0, self._sim_price + drift + revert)
        self.price = round(self._sim_price, 2)
        self._record(self.price)
        return self.price

    def _record(self, price: float):
        """Append price to history and increment counter."""
        self.history.append(price)
        self._tick_count += 1

    @property
    def tick_count(self) -> int:
        return self._tick_count

    def generate_backtest_data(self, n_bars: Optional[int] = None) -> list[float]:
        """Pre-generate N bars of simulated price data (for backtesting)."""
        n = n_bars or self.config.BACKTEST_BARS
        prices = []
        for _ in range(n):
            prices.append(self.get_price())
        logger.info("Generated %d backtest bars", n)
        return prices

    def run(self, on_tick: Callable[[float], None], max_ticks: Optional[int] = None):
        """Run the feed in a loop, calling on_tick(price) for each tick."""
        logger.info("Feed starting (mode=%s, symbol=%s, interval=%.1fs)",
                     self.mode, self.config.SYMBOL, self.config.FEED_INTERVAL_SEC)
        try:
            while True:
                price = self.get_price()
                on_tick(price)
                if max_ticks is not None and self._tick_count >= max_ticks:
                    logger.info("Feed stopped: max_ticks=%d reached", max_ticks)
                    break
                if self.mode == "live":
                    time.sleep(self.config.FEED_INTERVAL_SEC)
                else:
                    time.sleep(0.05)  # fast sim
        except KeyboardInterrupt:
            logger.info("Feed stopped by user (%d ticks)", self._tick_count)
