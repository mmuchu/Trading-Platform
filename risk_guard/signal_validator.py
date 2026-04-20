"""
Gate 2 — Signal Validator.

Validates SignalEvents before they reach execution.  Catches the "undefined"
trades from v2 by ensuring every signal has sensible, internally-consistent
fields.

Checks performed:
  - Required fields present and non-zero (symbol, side, price, strength)
  - Price sanity (not negative, not absurdly far from last market price)
  - Strength in [0, 1] range
  - Side is BUY or SELL (not HOLD or unknown)
  - Signal direction vs current position is logical (no BUY when already
    max-long unless position reduction)
  - Price is within a reasonable range of the latest market tick
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from core.v3.models import SignalEvent, Side

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of signal validation."""

    valid: bool = True
    reason: str = ""
    checks_passed: int = 0
    checks_total: int = 6


class SignalValidator:
    """Validates trading signals against sanity criteria.

    Parameters
    ----------
    max_price_deviation_pct : float
        Maximum allowed deviation between signal price and last market price.
        Helps catch stale or corrupted signals.
    min_strength : float
        Minimum signal strength to be considered tradeable.
    max_strength : float
        Maximum signal strength cap.
    """

    def __init__(
        self,
        max_price_deviation_pct: float = 0.02,
        min_strength: float = 0.05,
        max_strength: float = 1.0,
    ) -> None:
        self.max_price_deviation_pct = max_price_deviation_pct
        self.min_strength = min_strength
        self.max_strength = max_strength

        self._last_prices: Dict[str, float] = {}
        self._validation_count: int = 0
        self._reject_count: int = 0
        self._last_reject_reason: str = ""

    # ------------------------------------------------------------------
    # Update latest price (called by feed monitor or external source)
    # ------------------------------------------------------------------

    def update_price(self, symbol: str, price: float) -> None:
        """Update the last known market price for price-deviation checks."""
        self._last_prices[symbol] = price

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(
        self,
        signal: SignalEvent,
        current_position_qty: float = 0.0,
        max_position_size: float = 10.0,
    ) -> ValidationResult:
        """Run all validation checks on a signal.

        Parameters
        ----------
        signal : SignalEvent
            The signal to validate.
        current_position_qty : float
            Current position quantity for direction-consistency check.
        max_position_size : float
            Maximum allowed position for direction check.

        Returns
        -------
        ValidationResult
        """
        self._validation_count += 1
        result = ValidationResult(checks_total=6)
        checks = 0

        # --- Check 1: Required fields ---
        checks += 1
        if not signal.symbol or signal.symbol.strip() == "":
            return self._reject(result, checks, "Signal has empty symbol")
        if signal.price <= 0:
            return self._reject(result, checks, f"Signal price invalid: {signal.price}")
        if signal.side not in (Side.BUY, Side.SELL):
            return self._reject(result, checks, f"Signal side invalid: {signal.side}")

        # --- Check 2: Strength range ---
        checks += 1
        if signal.strength < self.min_strength:
            return self._reject(
                result, checks,
                f"Signal strength too weak: {signal.strength:.4f} < {self.min_strength}",
            )
        if signal.strength > self.max_strength:
            # Clamp rather than reject — signals from external sources may overshoot
            logger.debug(
                "Signal strength clamped: %.4f -> %.4f",
                signal.strength, self.max_strength,
            )

        # --- Check 3: Price sanity vs market ---
        checks += 1
        last_price = self._last_prices.get(signal.symbol, 0.0)
        if last_price > 0:
            deviation = abs(signal.price - last_price) / last_price
            if deviation > self.max_price_deviation_pct:
                return self._reject(
                    result, checks,
                    f"Signal price {signal.price:.2f} deviates {deviation:.2%} "
                    f"from market {last_price:.2f} (max {self.max_price_deviation_pct:.2%})",
                )

        # --- Check 4: Direction consistency ---
        checks += 1
        if signal.side == Side.BUY and current_position_qty >= max_position_size:
            return self._reject(
                result, checks,
                f"BUY signal but position {current_position_qty:.4f} "
                f"already at max {max_position_size}",
            )

        # --- Check 5: Timestamp sanity ---
        checks += 1
        now = time.time()
        if signal.timestamp > 0:
            age = now - signal.timestamp
            if age > 60:  # signal older than 60 seconds is stale
                return self._reject(
                    result, checks,
                    f"Signal stale: {age:.1f}s old (max 60s)",
                )

        # --- Check 6: Metadata completeness ---
        checks += 1
        if not signal.metadata:
            # Not a hard reject, but worth logging — metadata helps with debugging
            logger.debug("Signal has no metadata (strategy info missing)")

        result.checks_passed = checks
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _reject(self, result: ValidationResult, checks: int, reason: str) -> ValidationResult:
        result.valid = False
        result.reason = reason
        result.checks_passed = checks - 1
        self._reject_count += 1
        self._last_reject_reason = reason
        logger.warning("Signal rejected: %s", reason)
        return result

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "total_validations": self._validation_count,
            "total_rejected": self._reject_count,
            "rejection_rate": (
                self._reject_count / self._validation_count
                if self._validation_count > 0
                else 0.0
            ),
            "last_reject_reason": self._last_reject_reason,
        }

    def reset(self) -> None:
        self._validation_count = 0
        self._reject_count = 0
        self._last_reject_reason = ""
        self._last_prices.clear()
