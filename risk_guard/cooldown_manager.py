"""
Gate 5 — Cooldown Manager.

Prevents rapid-fire trading on the same symbol.  The v2 bot executed
multiple trades per second because there was no effective cooldown between
signal generation and execution.

This module tracks per-symbol cooldowns and enforces minimum intervals
between trades.  Different cooldowns can be set for:
  - Same-direction trades (BUY after BUY)
  - Direction-change trades (BUY after SELL)
  - Global cooldown (any trade on any symbol)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class CooldownConfig:
    """Cooldown configuration."""

    same_direction_sec: float = 5.0      # Min time between same-direction trades
    direction_change_sec: float = 2.0    # Min time between direction changes
    global_sec: float = 1.0              # Min time between any trades
    per_symbol_sec: float = 3.0          # Min time between trades on same symbol


@dataclass
class CooldownState:
    """Per-symbol cooldown tracking."""

    last_trade_time: float = 0.0
    last_side: str = ""                  # "BUY" or "SELL"
    trade_count: int = 0


class CooldownManager:
    """Manages trade cooldowns to prevent rapid-fire execution.

    Parameters
    ----------
    config : CooldownConfig
        Cooldown timing configuration.
    """

    def __init__(self, config: Optional[CooldownConfig] = None) -> None:
        self.config = config or CooldownConfig()

        # Per-symbol state
        self._symbols: Dict[str, CooldownState] = {}

        # Global state
        self._global_last_trade_time: float = 0.0
        self._global_trade_count: int = 0

        # Stats
        self._total_checks: int = 0
        self._total_blocked: int = 0
        self._last_block_reason: str = ""

    # ------------------------------------------------------------------
    # Cooldown check
    # ------------------------------------------------------------------

    def check(self, symbol: str, side: str) -> tuple[bool, str]:
        """Check if a trade on *symbol* with *side* is allowed by cooldown.

        Returns
        -------
        (allowed, reason) : tuple[bool, str]
            ``allowed`` is True when the cooldown has elapsed.
        """
        self._total_checks += 1
        now = time.time()
        state = self._symbols.setdefault(
            symbol, CooldownState()
        )

        # --- Global cooldown ---
        if self._global_last_trade_time > 0:
            elapsed_global = now - self._global_last_trade_time
            if elapsed_global < self.config.global_sec:
                remaining = self.config.global_sec - elapsed_global
                reason = (
                    f"Global cooldown: {remaining:.1f}s remaining "
                    f"(last trade {elapsed_global:.1f}s ago)"
                )
                self._total_blocked += 1
                self._last_block_reason = reason
                return False, reason

        # --- Per-symbol cooldown ---
        if state.last_trade_time > 0:
            elapsed_symbol = now - state.last_trade_time

            # Same direction?
            if state.last_side == side and side in ("BUY", "SELL"):
                min_interval = self.config.same_direction_sec
            else:
                min_interval = self.config.direction_change_sec

            min_interval = max(min_interval, self.config.per_symbol_sec)

            if elapsed_symbol < min_interval:
                remaining = min_interval - elapsed_symbol
                reason = (
                    f"Symbol cooldown ({symbol}): {remaining:.1f}s remaining "
                    f"(last {state.last_side} {elapsed_symbol:.1f}s ago, "
                    f"min interval {min_interval:.1f}s)"
                )
                self._total_blocked += 1
                self._last_block_reason = reason
                return False, reason

        return True, ""

    # ------------------------------------------------------------------
    # Record trade
    # ------------------------------------------------------------------

    def record_trade(self, symbol: str, side: str) -> None:
        """Record that a trade was executed (resets cooldown timer)."""
        now = time.time()
        state = self._symbols.setdefault(
            symbol, CooldownState()
        )
        state.last_trade_time = now
        state.last_side = side
        state.trade_count += 1

        self._global_last_trade_time = now
        self._global_trade_count += 1

    # ------------------------------------------------------------------
    # Time remaining
    # ------------------------------------------------------------------

    def time_remaining(self, symbol: str, side: str = "") -> float:
        """Return seconds remaining until next allowed trade, or 0."""
        now = time.time()
        state = self._symbols.get(symbol)
        if not state or state.last_trade_time == 0:
            return 0.0

        elapsed = now - state.last_trade_time
        if side and state.last_side == side:
            remaining = self.config.same_direction_sec - elapsed
        else:
            remaining = self.config.direction_change_sec - elapsed

        remaining = max(remaining, self.config.per_symbol_sec - elapsed)
        remaining = max(remaining, self.config.global_sec - (now - self._global_last_trade_time) if self._global_last_trade_time else 0)
        return max(remaining, 0.0)

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "total_checks": self._total_checks,
            "total_blocked": self._total_blocked,
            "global_trade_count": self._global_trade_count,
            "symbols_tracked": len(self._symbols),
            "last_block_reason": self._last_block_reason,
        }

    def symbol_stats(self, symbol: str) -> Dict[str, Any]:
        state = self._symbols.get(symbol)
        if not state:
            return {
                "symbol": symbol,
                "trade_count": 0,
                "last_side": None,
                "last_trade_time": 0.0,
            }
        return {
            "symbol": symbol,
            "trade_count": state.trade_count,
            "last_side": state.last_side or None,
            "last_trade_time": state.last_trade_time,
        }

    def reset(self) -> None:
        self._symbols.clear()
        self._global_last_trade_time = 0.0
        self._global_trade_count = 0
        self._total_checks = 0
        self._total_blocked = 0
        self._last_block_reason = ""
        logger.info("CooldownManager reset")