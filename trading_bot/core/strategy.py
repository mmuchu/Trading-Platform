"""
Strategy Engine - Generates trading signals from price data.

Uses dual MA crossover: short MA (10) crosses long MA (30).
Only fires on actual crossovers, not every tick.
Extensible: Replace generate() with ML/RL model output.
"""

import logging
from typing import Optional

from trading_bot.config import Config

logger = logging.getLogger("strategy")


class Strategy:
    """Moving average crossover strategy."""

    def __init__(self, config: Config):
        self.config = config
        self.history: list[float] = []
        self.signal_count: int = 0
        self._last_signal: str = "HOLD"
        self._prev_short_ma: Optional[float] = None
        self._prev_long_ma: Optional[float] = None

    def generate(self, price: float) -> str:
        """
        Analyze price and return signal: BUY, SELL, or HOLD.

        Uses dual MA crossover:
          BUY  = short MA crosses ABOVE long MA (golden cross)
          SELL = short MA crosses BELOW long MA (death cross)
          HOLD = no crossover detected

        Requires at least MA_LONG + 1 data points to start.
        """
        self.history.append(price)

        short_period = self.config.MA_SHORT
        long_period = self.config.MA_LONG

        # Need enough data for both MAs + one previous bar
        min_bars = long_period + 1
        if len(self.history) < min_bars:
            return "HOLD"

        # Current MAs
        short_ma = sum(self.history[-short_period:]) / short_period
        long_ma = sum(self.history[-long_period:]) / long_period

        # Previous MAs (shifted by 1 bar)
        prev_short_ma = sum(self.history[-(short_period + 1):-1]) / short_period
        prev_long_ma = sum(self.history[-(long_period + 1):-1]) / long_period

        # Detect crossovers
        signal = "HOLD"
        if prev_short_ma <= prev_long_ma and short_ma > long_ma:
            signal = "BUY"
        elif prev_short_ma >= prev_long_ma and short_ma < long_ma:
            signal = "SELL"

        if signal != "HOLD":
            return self._emit(signal, price, short_ma, long_ma)

        return "HOLD"

    def _emit(self, signal: str, price: float, short_ma: float, long_ma: float) -> str:
        """Emit and log a signal."""
        self.signal_count += 1
        self._last_signal = signal
        gap = abs(short_ma - long_ma) / long_ma * 100
        logger.info("SIGNAL %s @ $%.2f (MA%d=%.2f MA%d=%.2f gap=%.3f%% total: %d)",
                     signal, price, self.config.MA_SHORT, short_ma,
                     self.config.MA_LONG, long_ma, gap, self.signal_count)
        return signal

    @property
    def last_signal(self) -> str:
        return self._last_signal

    def stats(self) -> dict:
        return {
            "signals_generated": self.signal_count,
            "history_length": len(self.history),
            "last_signal": self._last_signal,
            "short_ma_period": self.config.MA_SHORT,
            "long_ma_period": self.config.MA_LONG,
        }
