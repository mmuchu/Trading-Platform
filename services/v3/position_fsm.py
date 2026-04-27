"""
v3.2 Position State Machine
============================
Manages the position lifecycle through strict state transitions:

  FLAT → ENTERING → ACTIVE → EXIT → COOLDOWN → FLAT

The FSM prevents illegal transitions (e.g., can't EXIT from FLAT)
and enforces minimum hold times, cooldown periods, and max positions.

This replaces the ad-hoc position tracking in execution.py with
a proper state machine that all other modules query.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Tuple

from core.v3.models import PositionState, RegimeType, Side

logger = logging.getLogger(__name__)


# Legal state transitions: (current_state, trigger) → next_state
_TRANSITIONS: dict[PositionState, dict[str, PositionState]] = {
    PositionState.FLAT: {
        "open": PositionState.ENTERING,
    },
    PositionState.ENTERING: {
        "fill_confirmed": PositionState.ACTIVE,
        "timeout": PositionState.FLAT,
        "cancel": PositionState.FLAT,
    },
    PositionState.ACTIVE: {
        "sl_hit": PositionState.EXIT,
        "tp_hit": PositionState.EXIT,
        "signal_reverse": PositionState.EXIT,
        "manual_close": PositionState.EXIT,
        "regime_hostile": PositionState.EXIT,
    },
    PositionState.EXIT: {
        "fill_confirmed": PositionState.COOLDOWN,
        "timeout": PositionState.COOLDOWN,
    },
    PositionState.COOLDOWN: {
        "cooldown_elapsed": PositionState.FLAT,
        "force_reset": PositionState.FLAT,
    },
}


@dataclass
class PositionConfig:
    """Position lifecycle tuning parameters."""
    entering_timeout_sec: float = 10.0       # max time to wait for fill after entry signal
    exit_timeout_sec: float = 10.0            # max time to wait for exit fill
    cooldown_sec: float = 5.0                 # mandatory cooldown after closing
    max_hold_time_sec: float = 3600.0         # max time in ACTIVE before forced exit
    min_hold_time_sec: float = 2.0            # min time before allowing exit (anti-churn)


@dataclass
class PositionRecord:
    """Tracks a single position through its lifecycle."""
    symbol: str = ""
    side: Side = Side.BUY
    state: PositionState = PositionState.FLAT
    quantity: float = 0.0
    avg_entry: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    entry_time: float = 0.0
    active_time: float = 0.0
    exit_time: float = 0.0
    regime_at_entry: str = ""
    signal_score_at_entry: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = 0.0


class PositionStateMachine:
    """
    Enforces the position lifecycle state machine.

    Usage:
        fsm = PositionStateMachine()
        fsm.try_transition(symbol, "open", side=Side.BUY, quantity=0.01)
        fsm.try_transition(symbol, "fill_confirmed")
        # ... later
        fsm.try_transition(symbol, "tp_hit")
    """

    def __init__(self, config: PositionConfig | None = None) -> None:
        self.cfg = config or PositionConfig()
        self._positions: Dict[str, PositionRecord] = {}
        self._last_state_change: Dict[str, float] = {}

        # Stats
        self._total_cycles = 0
        self._rejected_transitions = 0

    def get_position(self, symbol: str) -> PositionRecord:
        """Get or create position record for symbol."""
        if symbol not in self._positions:
            self._positions[symbol] = PositionRecord(symbol=symbol)
        return self._positions[symbol]

    @property
    def state(self, symbol: str = "") -> PositionState:
        """Get current state for symbol. If no symbol, returns first symbol's state."""
        if symbol:
            return self.get_position(symbol).state
        if self._positions:
            return list(self._positions.values())[0].state
        return PositionState.FLAT

    def can_accept_signal(self, symbol: str, side: Side) -> Tuple[bool, str]:
        """
        Check if the FSM can accept a new trading signal.
        Returns (can_accept, reason).
        """
        pos = self.get_position(symbol)
        now = time.time()

        if pos.state == PositionState.FLAT:
            return True, "OK"

        if pos.state == PositionState.ENTERING:
            elapsed = now - self._last_state_change.get(symbol, now)
            if elapsed > self.cfg.entering_timeout_sec:
                return True, "Entering timeout — allowing new signal"
            return False, f"In ENTERING state ({elapsed:.1f}s/{self.cfg.entering_timeout_sec}s)"

        if pos.state == PositionState.ACTIVE:
            hold_time = now - pos.active_time
            # Same-direction signal: ignore (already in position)
            if (pos.side == Side.BUY and side == Side.BUY) or \
               (pos.side == Side.SELL and side == Side.SELL):
                if hold_time>120:
                  logger.warning("FSM %s stale %.0fs reset",symbol,hold_time)
                  self._positions.pop(sym,None)
                if hold_time>120:
                  logger.warning("FSM %s stale %.0fs reset",symbol,hold_time)
                  self._positions.pop(sym,None)
                return False, f"Already in {pos.side.value} position (hold {hold_time:.0f}s)"
            # Reverse signal: allow if min hold time met
            if hold_time < self.cfg.min_hold_time_sec:
                return False, f"Min hold time not met ({hold_time:.1f}s/{self.cfg.min_hold_time_sec}s)"
            return True, "Reverse signal — will trigger EXIT"

        if pos.state == PositionState.EXIT:
            return False, "Position EXIT in progress"

        if pos.state == PositionState.COOLDOWN:
            elapsed = now - self._last_state_change.get(symbol, now)
            remaining = self.cfg.cooldown_sec - elapsed
            if remaining > 0:
                return False, f"In COOLDOWN ({remaining:.1f}s remaining)"
            return True, "Cooldown elapsed"

        return False, f"Unknown state: {pos.state}"

    def try_transition(
        self,
        symbol: str,
        trigger: str,
        side: Side | None = None,
        quantity: float = 0.0,
        price: float = 0.0,
        regime: str = "",
        score: float = 0.0,
    ) -> Tuple[bool, PositionState]:
        """
        Attempt a state transition. Returns (success, new_state).
        """
        pos = self.get_position(symbol)
        current = pos.state
        now = time.time()

        # Validate transition
        allowed = _TRANSITIONS.get(current, {})
        next_state = allowed.get(trigger)

        if next_state is None:
            self._rejected_transitions += 1
            logger.warning(
                "FSM rejected: %s %s → trigger '%s' (illegal)",
                symbol, current.value, trigger,
            )
            return False, current

        # Transition
        prev = pos.state
        pos.state = next_state
        self._last_state_change[symbol] = now

        logger.info(
            "FSM %s: %s → %s (trigger=%s)",
            symbol, prev.value, next_state.value, trigger,
        )

        # State entry actions
        if next_state == PositionState.ENTERING:
            if side:
                pos.side = side
            if quantity:
                pos.quantity = quantity
            if price:
                pos.avg_entry = price
            pos.entry_time = now
            pos.regime_at_entry = regime
            pos.signal_score_at_entry = score

        elif next_state == PositionState.ACTIVE:
            pos.active_time = now
            if price:
                pos.avg_entry = price  # update with actual fill price
            pos.highest_price = price
            pos.lowest_price = price

        elif next_state == PositionState.COOLDOWN:
            pos.exit_time = now
            self._total_cycles += 1

        elif next_state == PositionState.FLAT:
            # Reset position record
            pos.quantity = 0.0
            pos.avg_entry = 0.0
            pos.unrealized_pnl = 0.0
            pos.entry_time = 0.0
            pos.active_time = 0.0
            pos.exit_time = 0.0
            pos.highest_price = 0.0
            pos.lowest_price = 0.0

        return True, next_state

    def update_price(self, symbol: str, price: float) -> None:
        """Update current price for unrealized PnL and SL/TP tracking."""
        pos = self.get_position(symbol)
        if pos.state != PositionState.ACTIVE:
            return

        if pos.quantity > 0:
            pos.unrealized_pnl = (price - pos.avg_entry) * pos.quantity
        elif pos.quantity < 0:
            pos.unrealized_pnl = (pos.avg_entry - price) * abs(pos.quantity)

        pos.highest_price = max(pos.highest_price, price)
        pos.lowest_price = min(pos.lowest_price, price) if pos.lowest_price > 0 else price

    def check_timeouts(self, symbol: str) -> list[str]:
        """
        Check for timed-out states. Returns list of triggers to fire.
        Call periodically from the orchestrator tick handler.
        """
        triggers = []
        now = time.time()
        pos = self.get_position(symbol)

        if pos.state == PositionState.ENTERING:
            elapsed = now - self._last_state_change.get(symbol, now)
            if elapsed > self.cfg.entering_timeout_sec:
                triggers.append("timeout")
                logger.warning("FSM %s: ENTERING timeout (%.1fs)", symbol, elapsed)

        elif pos.state == PositionState.ACTIVE:
            hold_time = now - pos.active_time
            if hold_time > self.cfg.max_hold_time_sec:
                triggers.append("manual_close")
                logger.warning("FSM %s: Max hold time exceeded (%.0fs)", symbol, hold_time)

        elif pos.state == PositionState.EXIT:
            elapsed = now - self._last_state_change.get(symbol, now)
            if elapsed > self.cfg.exit_timeout_sec:
                triggers.append("timeout")
                logger.warning("FSM %s: EXIT timeout (%.1fs)", symbol, elapsed)

        elif pos.state == PositionState.COOLDOWN:
            elapsed = now - self._last_state_change.get(symbol, now)
            if elapsed >= self.cfg.cooldown_sec:
                triggers.append("cooldown_elapsed")
                logger.info("FSM %s: Cooldown elapsed", symbol)

        return triggers

    def check_regime_conflict(self, symbol: str, current_regime: RegimeType) -> Optional[str]:
        """
        Check if current regime is hostile to the open position.
        Returns a trigger string if position should be closed, None otherwise.
        """
        pos = self.get_position(symbol)
        if pos.state != PositionState.ACTIVE:
            return None

        # Trend-following positions should exit in RANGE or VOLATILE
        if pos.regime_at_entry == RegimeType.TREND.value:
            if current_regime in (RegimeType.VOLATILE,):
                return "regime_hostile"

        return None

    @property
    def stats(self) -> dict:
        positions_summary = {}
        for sym, pos in self._positions.items():
            positions_summary[sym] = {
                "state": pos.state.value,
                "side": pos.side.value if pos.state != PositionState.FLAT else "NONE",
                "quantity": pos.quantity,
                "avg_entry": round(pos.avg_entry, 2),
                "unrealized_pnl": round(pos.unrealized_pnl, 2),
                "realized_pnl": round(pos.realized_pnl, 2),
                "hold_time": round(time.time() - pos.active_time, 1) if pos.state == PositionState.ACTIVE else 0,
            }

        return {
            "total_cycles": self._total_cycles,
            "rejected_transitions": self._rejected_transitions,
            "positions": positions_summary,
        }
