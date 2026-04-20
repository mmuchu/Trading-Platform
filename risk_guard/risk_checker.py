"""
Gate 3 — Risk Checker.

Comprehensive risk gate that replaces the basic _check_risk() in v3.
Checks drawdown, equity, exposure limits, and circuit-breaker conditions.

This gate uses the existing PortfolioState and ExposureAllocator from
core/portfolio/ but adds its own circuit-breaker and kill-switch logic.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from config.settings import settings
from core.v3.models import Side, SignalEvent

logger = logging.getLogger(__name__)


@dataclass
class RiskCheckResult:
    """Result of the risk gate check."""

    approved: bool = True
    reason: str = ""
    checks: Dict[str, bool] = field(default_factory=dict)
    current_drawdown_pct: float = 0.0
    current_equity: float = 0.0
    risk_score: float = 0.0  # 0 = safe, 1 = max risk


class RiskChecker:
    """Pre-trade risk gate with drawdown, equity, and circuit-breaker checks.

    Parameters
    ----------
    max_drawdown_pct : float
        Halt all trading when drawdown exceeds this threshold.
    risk_per_trade_pct : float
        Maximum equity percentage to risk on any single trade.
    circuit_breaker_trades : int
        After this many consecutive losses, trigger circuit breaker.
    circuit_breaker_cooldown_sec : float
        Seconds to wait after circuit breaker triggers.
    """

    def __init__(
        self,
        max_drawdown_pct: float = 0.0,
        risk_per_trade_pct: float = 0.0,
        circuit_breaker_trades: int = 5,
        circuit_breaker_cooldown_sec: float = 60.0,
    ) -> None:
        # Use settings defaults if not specified
        self.max_drawdown_pct = max_drawdown_pct or settings.risk.max_drawdown_pct
        self.risk_per_trade_pct = risk_per_trade_pct or settings.risk.risk_per_trade_pct
        self.circuit_breaker_trades = circuit_breaker_trades
        self.circuit_breaker_cooldown_sec = circuit_breaker_cooldown_sec

        # Equity tracking
        self._initial_equity: float = settings.broker.initial_cash
        self._peak_equity: float = self._initial_equity
        self._current_drawdown_pct: float = 0.0

        # Circuit breaker
        self._circuit_breaker_active: bool = False
        self._circuit_breaker_time: float = 0.0
        self._consecutive_losses: int = 0
        self._total_losses: int = 0
        self._total_wins: int = 0

        # Kill switch
        self._kill_switch: bool = False

        # Stats
        self._total_checks: int = 0
        self._total_approved: int = 0
        self._total_blocked: int = 0
        self._last_block_reason: str = ""

    # ------------------------------------------------------------------
    # Risk check
    # ------------------------------------------------------------------

    def check(
        self,
        signal: SignalEvent,
        equity: float,
        cash: float,
        position_qty: float = 0.0,
        unrealized_pnl: float = 0.0,
    ) -> RiskCheckResult:
        """Run all risk checks.

        Returns
        -------
        RiskCheckResult
            ``approved`` is True if the trade passes all checks.
        """
        self._total_checks += 1
        result = RiskCheckResult(current_equity=equity)
        checks: Dict[str, bool] = {}

        # --- Check 1: Kill switch ---
        if self._kill_switch:
            checks["kill_switch"] = False
            result.approved = False
            result.reason = "Kill switch active — all trading halted"
            self._record_block(result.reason)
            result.checks = checks
            return result
        checks["kill_switch"] = True

        # --- Check 2: Circuit breaker ---
        if self._circuit_breaker_active:
            elapsed = time.time() - self._circuit_breaker_time
            if elapsed < self.circuit_breaker_cooldown_sec:
                checks["circuit_breaker"] = False
                remaining = self.circuit_breaker_cooldown_sec - elapsed
                result.approved = False
                result.reason = (
                    f"Circuit breaker active: {remaining:.0f}s remaining "
                    f"({self._consecutive_losses} consecutive losses)"
                )
                self._record_block(result.reason)
                result.checks = checks
                return result
            else:
                self._circuit_breaker_active = False
                self._consecutive_losses = 0
                logger.info("Circuit breaker cooldown elapsed — resuming trading")
        checks["circuit_breaker"] = True

        # --- Check 3: Drawdown ---
        if equity > self._peak_equity:
            self._peak_equity = equity

        if self._peak_equity > 0:
            dd = (self._peak_equity - equity) / self._peak_equity
            self._current_drawdown_pct = max(dd, 0.0)
            result.current_drawdown_pct = self._current_drawdown_pct

        if self._current_drawdown_pct > self.max_drawdown_pct:
            checks["drawdown"] = False
            result.approved = False
            result.reason = (
                f"Drawdown {self._current_drawdown_pct:.2%} exceeds "
                f"limit {self.max_drawdown_pct:.2%}"
            )
            # Auto-activate kill switch on severe drawdown
            if self._current_drawdown_pct > self.max_drawdown_pct * 2:
                self._kill_switch = True
                result.reason += " — KILL SWITCH ACTIVATED"
                logger.critical(
                    "KILL SWITCH activated: drawdown %.2f%%",
                    self._current_drawdown_pct * 100,
                )
            self._record_block(result.reason)
            result.checks = checks
            return result
        checks["drawdown"] = True

        # --- Check 4: Drawdown velocity ---
        if self._current_drawdown_pct > self.max_drawdown_pct * 0.8:
            checks["drawdown_velocity"] = False
            result.approved = False
            result.reason = (
                f"Drawdown approaching limit: {self._current_drawdown_pct:.2%} "
                f"(>{self.max_drawdown_pct * 0.8:.2%} of {self.max_drawdown_pct:.2%})"
            )
            self._record_block(result.reason)
            result.checks = checks
            return result
        checks["drawdown_velocity"] = True

        # --- Check 5: Equity floor ---
        equity_floor = self._initial_equity * 0.5
        if equity < equity_floor:
            checks["equity_floor"] = False
            result.approved = False
            result.reason = (
                f"Equity ${equity:.2f} below floor ${equity_floor:.2f} "
                f"(50% of initial ${self._initial_equity:.2f})"
            )
            self._record_block(result.reason)
            result.checks = checks
            return result
        checks["equity_floor"] = True

        # All checks passed
        result.checks = checks
        self._total_approved += 1

        # Compute risk score (0-1, higher = more risky)
        dd_score = self._current_drawdown_pct / self.max_drawdown_pct if self.max_drawdown_pct > 0 else 0
        loss_score = self._consecutive_losses / self.circuit_breaker_trades if self.circuit_breaker_trades > 0 else 0
        result.risk_score = round(min(dd_score + loss_score * 0.5, 1.0), 4)

        return result

    # ------------------------------------------------------------------
    # Trade result tracking (call after each fill resolves)
    # ------------------------------------------------------------------

    def record_trade_result(self, pnl: float) -> None:
        """Record the outcome of a completed trade for circuit breaker."""
        if pnl > 0:
            self._total_wins += 1
            self._consecutive_losses = 0
        elif pnl < 0:
            self._total_losses += 1
            self._consecutive_losses += 1
            if self._consecutive_losses >= self.circuit_breaker_trades:
                self._circuit_breaker_active = True
                self._circuit_breaker_time = time.time()
                logger.warning(
                    "Circuit breaker triggered: %d consecutive losses",
                    self._consecutive_losses,
                )

    # ------------------------------------------------------------------
    # Kill switch
    # ------------------------------------------------------------------

    def activate_kill_switch(self) -> None:
        """Manually activate the kill switch (flatten all + halt)."""
        self._kill_switch = True
        logger.critical("Kill switch MANUALLY activated")

    def deactivate_kill_switch(self) -> None:
        """Manually deactivate (use with caution)."""
        self._kill_switch = False
        self._consecutive_losses = 0
        self._circuit_breaker_active = False
        self._peak_equity = self._initial_equity  # reset peak
        logger.info("Kill switch deactivated — trading resumed")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _record_block(self, reason: str) -> None:
        self._total_blocked += 1
        self._last_block_reason = reason

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "total_checks": self._total_checks,
            "total_approved": self._total_approved,
            "total_blocked": self._total_blocked,
            "kill_switch": self._kill_switch,
            "circuit_breaker": self._circuit_breaker_active,
            "consecutive_losses": self._consecutive_losses,
            "total_wins": self._total_wins,
            "total_losses": self._total_losses,
            "current_drawdown_pct": round(self._current_drawdown_pct, 4),
            "peak_equity": round(self._peak_equity, 2),
            "last_block_reason": self._last_block_reason,
        }

    def reset(self) -> None:
        self._peak_equity = self._initial_equity
        self._current_drawdown_pct = 0.0
        self._circuit_breaker_active = False
        self._circuit_breaker_time = 0.0
        self._consecutive_losses = 0
        self._kill_switch = False
        self._total_checks = 0
        self._total_approved = 0
        self._total_blocked = 0
        self._last_block_reason = ""
        self._total_wins = 0
        self._total_losses = 0
        logger.info("RiskChecker reset")