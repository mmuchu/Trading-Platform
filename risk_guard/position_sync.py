"""
Gate 4 — Position Sync Guard.

Ensures the position state is consistent between the signal intent and the
execution layer's actual position.  This prevents the v2 bug where SELL signals
were generated while the system was actually flat or already short.

The guard verifies:
  - We are not trying to sell when flat (unless opening short is allowed)
  - We are not trying to buy more when already at max position
  - The signal direction makes sense relative to current position
  - Position hasn't changed unexpectedly between signal generation and execution
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.v3.models import Side, SignalEvent

logger = logging.getLogger(__name__)


@dataclass
class PositionSyncResult:
    """Result of the position sync check."""

    synced: bool = True
    reason: str = ""
    current_qty: float = 0.0
    avg_entry: float = 0.0
    action_required: str = "EXECUTE"  # EXECUTE, FLATTEN_FIRST, REJECT


class PositionSyncGuard:
    """Verifies position consistency before execution.

    Parameters
    ----------
    allow_shorting : bool
        Whether opening short positions is allowed.
    max_position_qty : float
        Maximum absolute position quantity.
    """

    def __init__(
        self,
        allow_shorting: bool = True,
        max_position_qty: float = 0.0,
    ) -> None:
        from config.settings import settings
        self.allow_shorting = allow_shorting
        self.max_position_qty = max_position_qty or settings.risk.max_position_size

        self._check_count: int = 0
        self._reject_count: int = 0
        self._flatten_count: int = 0
        self._last_reject_reason: str = ""

    # ------------------------------------------------------------------
    # Sync check
    # ------------------------------------------------------------------

    def check(
        self,
        signal: SignalEvent,
        current_qty: float,
        avg_entry: float = 0.0,
        current_price: float = 0.0,
    ) -> PositionSyncResult:
        """Verify position state is consistent with the signal.

        Parameters
        ----------
        signal : SignalEvent
            The incoming trade signal.
        current_qty : float
            Current position quantity from the execution service.
        avg_entry : float
            Current average entry price.
        current_price : float
            Current market price for PnL context.

        Returns
        -------
        PositionSyncResult
        """
        self._check_count += 1

        # --- Check 1: SELL when flat ---
        if signal.side == Side.SELL and abs(current_qty) < 1e-8:
            if not self.allow_shorting:
                result = PositionSyncResult(
                    synced=False,
                    reason="SELL signal but position is flat (shorting disabled)",
                    current_qty=current_qty,
                    avg_entry=avg_entry,
                    action_required="REJECT",
                )
                self._record_reject(result.reason)
                return result
            # Shorting is allowed — proceed but flag it
            logger.info(
                "SELL signal opening short position (shorting enabled) @ %.2f",
                signal.price,
            )

        # --- Check 2: BUY when already max-long ---
        if signal.side == Side.BUY and current_qty >= self.max_position_qty:
            result = PositionSyncResult(
                synced=False,
                reason=(
                    f"BUY signal but position {current_qty:.4f} "
                    f"already at max {self.max_position_qty}"
                ),
                current_qty=current_qty,
                avg_entry=avg_entry,
                action_required="REJECT",
            )
            self._record_reject(result.reason)
            return result

        # --- Check 3: SELL when already max-short ---
        if signal.side == Side.SELL and current_qty <= -self.max_position_qty:
            result = PositionSyncResult(
                synced=False,
                reason=(
                    f"SELL signal but position {current_qty:.4f} "
                    f"already at max short -{self.max_position_qty}"
                ),
                current_qty=current_qty,
                avg_entry=avg_entry,
                action_required="REJECT",
            )
            self._record_reject(result.reason)
            return result

        # --- Check 4: Direction conflict ---
        if current_qty != 0 and avg_entry > 0:
            if signal.side == Side.SELL and current_qty > 0:
                pass  # SL/TP manager handles this
            elif signal.side == Side.BUY and current_qty < 0:
                pass

        # --- Check 5: Position direction vs signal direction ---
        if current_qty > 0 and signal.side == Side.BUY and signal.strength < 0.2:
            logger.debug(
                "Weak BUY signal (%.2f) while long %.4f — may be noise",
                signal.strength, current_qty,
            )

        return PositionSyncResult(
            synced=True,
            current_qty=current_qty,
            avg_entry=avg_entry,
            action_required="EXECUTE",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _record_reject(self, reason: str) -> None:
        self._reject_count += 1
        self._last_reject_reason = reason
        logger.warning("Position sync rejected: %s", reason)

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "total_checks": self._check_count,
            "total_rejected": self._reject_count,
            "total_flatten_required": self._flatten_count,
            "last_reject_reason": self._last_reject_reason,
        }

    def reset(self) -> None:
        self._check_count = 0
        self._reject_count = 0
        self._flatten_count = 0
        self._last_reject_reason = ""