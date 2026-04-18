"""
Risk Engine - Safety guard between strategy signals and execution.

Non-negotiable checks before any order:
  1. Position limit (max_position)
  2. Minimum balance (don't over-leverage)
  3. Max drawdown (stop the bleeding)
  4. Anti-overtrading (cooldown between signals)
"""

import logging
import time
from typing import Optional

from trading_bot.config import Config

logger = logging.getLogger("risk")


class RiskEngine:
    """Pre-execution risk validation."""

    def __init__(self, config: Config):
        self.config = config
        self.max_position = config.MAX_POSITION
        self.max_drawdown_pct = config.MAX_DRAWDOWN_PCT
        self.min_balance = config.MIN_BALANCE
        self._blocked: int = 0
        self._passed: int = 0
        self._block_reasons: dict[str, int] = {}
        self._last_signal_time: float = 0.0
        self._peak_equity: float = config.INITIAL_CASH
        self._cooldown_ticks: int = 20     # min ticks between signals (not time-based)
        self._ticks_since_signal: int = 0

    def validate(self, signal: str, position: int, cash: float,
                 equity: float) -> str:
        """
        Validate a signal. Returns the signal if safe, or "BLOCK" if rejected.

        Args:
            signal: BUY, SELL, or HOLD
            position: current absolute position size
            cash: available cash
            equity: total account equity (cash + position value)

        Returns:
            signal if safe, "BLOCK" if rejected
        """
        if signal == "HOLD":
            return "HOLD"

        self._ticks_since_signal += 1

        # Check 1: Cooldown (anti-overtrading, tick-based)
        if self._ticks_since_signal < self._cooldown_ticks:
            self._block("COOLDOWN")
            return "BLOCK"

        # Check 2: Position limit
        if signal == "BUY" and position >= self.max_position:
            self._block("MAX_LONG")
            return "BLOCK"
        if signal == "SELL" and position <= -self.max_position:
            self._block("MAX_SHORT")
            return "BLOCK"

        # Check 3: Minimum balance for buys
        if signal == "BUY" and cash < self.min_balance:
            self._block("LOW_CASH")
            return "BLOCK"

        # Check 4: Max drawdown
        if equity > self._peak_equity:
            self._peak_equity = equity
        drawdown_pct = (self._peak_equity - equity) / self._peak_equity * 100
        if drawdown_pct >= self.max_drawdown_pct:
            self._block("MAX_DRAWDOWN")
            logger.warning("MAX DRAWDOWN %.2f%% >= %.2f%% - trading halted",
                           drawdown_pct, self.max_drawdown_pct)
            return "BLOCK"

        # Signal is safe
        self._passed += 1
        self._ticks_since_signal = 0
        logger.debug("Risk PASS: %s (pos=%d, cash=%.0f, dd=%.2f%%)",
                      signal, position, cash, drawdown_pct)
        return signal

    def _block(self, reason: str):
        """Record a blocked signal."""
        self._blocked += 1
        self._block_reasons[reason] = self._block_reasons.get(reason, 0) + 1
        logger.debug("Risk BLOCK: %s", reason)

    def stats(self) -> dict:
        return {
            "passed": self._passed,
            "blocked": self._blocked,
            "block_rate": round(self._blocked / max(1, self._passed + self._blocked) * 100, 1),
            "block_reasons": dict(self._block_reasons),
            "peak_equity": round(self._peak_equity, 2),
        }
