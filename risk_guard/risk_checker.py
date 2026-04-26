"""
Gate 3 - Risk Checker (v3.1 FIX)
=================================
Handles equity=0 at startup gracefully.
When no trades have executed yet, equity defaults to 0.0 which would
compute 100% drawdown and permanently trigger kill switch.

Fix: Skip drawdown + equity floor checks when equity <= 0.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict

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
    risk_score: float = 0.0


class RiskChecker:
    """Pre-trade risk gate with drawdown, equity, and circuit-breaker checks."""

    def __init__(
        self,
        max_drawdown_pct: float = 0.0,
        risk_per_trade_pct: float = 0.0,
        circuit_breaker_trades: int = 5,
        circuit_breaker_cooldown_sec: float = 60.0,
    ) -> None:
        self.max_drawdown_pct = max_drawdown_pct or settings.risk.max_drawdown_pct
        self.risk_per_trade_pct = risk_per_trade_pct or settings.risk.risk_per_trade_pct
        self.circuit_breaker_trades = circuit_breaker_trades
        self.circuit_breaker_cooldown_sec = circuit_breaker_cooldown_sec

        self._initial_equity: float = settings.broker.initial_cash
        self._peak_equity: float = self._initial_equity
        self._current_drawdown_pct: float = 0.0

        self._circuit_breaker_active: bool = False
        self._circuit_breaker_time: float = 0.0
        self._consecutive_losses: int = 0
        self._total_losses: int = 0
        self._total_wins: int = 0

        self._kill_switch: bool = False
        self._kill_switch_time: float = 0.0

        self._total_checks: int = 0
        self._total_approved: int = 0
        self._total_blocked: int = 0
        self._last_block_reason: str = ""

        print(f"[RC] INIT  max_dd={self.max_drawdown_pct:.2%}  "
              f"initial_equity={self._initial_equity:.0f}")

    def check(
        self,
        signal: SignalEvent,
        equity: float,
        cash: float,
        position_qty: float = 0.0,
        unrealized_pnl: float = 0.0,
    ) -> RiskCheckResult:
        self._total_checks += 1
        result = RiskCheckResult(current_equity=equity)
        checks: Dict[str, bool] = {}

        print(f"[RC] CHECK #{self._total_checks}  equity={equity:.2f}  "
              f"cash={cash:.2f}  pos={position_qty}  "
              f"kill={self._kill_switch}")

        # --- Check 1: Kill switch (auto-reset 300s) ---
        if self._kill_switch:
            _kse = time.time() - self._kill_switch_time
            if _kse > 300.0:
                self._kill_switch = False
                self._consecutive_losses = 0
                self._circuit_breaker_active = False
                print("[RC] Kill switch AUTO-RESET after %.0fs" % _kse)
        if self._kill_switch:
            checks["kill_switch"] = False
            result.approved = False
            result.reason = "Kill switch active - all trading halted"
            self._record_block(result.reason)
            result.checks = checks
            print("[RC] BLOCKED: kill switch active")
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
                print(f"[RC] BLOCKED: circuit breaker ({remaining:.0f}s)")
                return result
            else:
                self._circuit_breaker_active = False
                self._consecutive_losses = 0
                print("[RC] Circuit breaker cooldown elapsed")
        checks["circuit_breaker"] = True

        # --- Check 3: Drawdown ---
        # CRITICAL FIX: When equity=0, position tracking is not initialized.
        # Skip drawdown check to avoid false 100% drawdown triggering kill switch.
        if equity <= 0:
            print("[RC] equity <= 0 (no position tracking) - "
                  "SKIPPING drawdown check")
            checks["drawdown"] = True
        else:
            if equity > self._peak_equity:
                self._peak_equity = equity
                print(f"[RC] New peak equity: {equity:.2f}")

            if self._peak_equity > 0:
                dd = (self._peak_equity - equity) / self._peak_equity
                self._current_drawdown_pct = max(dd, 0.0)
                result.current_drawdown_pct = self._current_drawdown_pct

            print(f"[RC] drawdown={self._current_drawdown_pct:.2%}  "
                  f"limit={self.max_drawdown_pct:.2%}")

            if self._current_drawdown_pct > self.max_drawdown_pct:
                checks["drawdown"] = False
                result.approved = False
                result.reason = (
                    f"Drawdown {self._current_drawdown_pct:.2%} exceeds "
                    f"limit {self.max_drawdown_pct:.2%}"
                )
                if self._current_drawdown_pct > self.max_drawdown_pct * 2:
                    self._kill_switch = True
                    result.reason += " - KILL SWITCH ACTIVATED"
                    self._kill_switch_time = time.time()
                    print(f"[RC] KILL SWITCH: dd={self._current_drawdown_pct:.2%}")
                self._record_block(result.reason)
                result.checks = checks
                return result
            checks["drawdown"] = True

        # --- Check 4: Drawdown velocity (soft) ---
        if self._current_drawdown_pct > self.max_drawdown_pct * 0.8:
            checks["drawdown_velocity"] = False
            result.approved = False
            result.reason = (
                f"Drawdown approaching limit: {self._current_drawdown_pct:.2%} "
                f"(>{self.max_drawdown_pct * 0.8:.2%} of "
                f"{self.max_drawdown_pct:.2%})"
            )
            self._record_block(result.reason)
            result.checks = checks
            print("[RC] BLOCKED: drawdown velocity warning")
            return result
        checks["drawdown_velocity"] = True

        # --- Check 5: Equity floor ---
        if equity <= 0:
            print("[RC] equity <= 0 - SKIPPING equity floor check")
            checks["equity_floor"] = True
        else:
            equity_floor = self._initial_equity * 0.5
            if equity < equity_floor:
                checks["equity_floor"] = False
                result.approved = False
                result.reason = (
                    f"Equity ${equity:.2f} below floor "
                    f"${equity_floor:.2f} "
                    f"(50% of initial ${self._initial_equity:.2f})"
                )
                self._record_block(result.reason)
                result.checks = checks
                print("[RC] BLOCKED: equity below floor")
                return result
            checks["equity_floor"] = True

        # All checks passed
        result.checks = checks
        self._total_approved += 1

        dd_score = (
            self._current_drawdown_pct / self.max_drawdown_pct
            if self.max_drawdown_pct > 0 else 0
        )
        loss_score = (
            self._consecutive_losses / self.circuit_breaker_trades
            if self.circuit_breaker_trades > 0 else 0
        )
        result.risk_score = round(min(dd_score + loss_score * 0.5, 1.0), 4)

        print("[RC] APPROVED")
        return result

    def record_trade_result(self, pnl: float) -> None:
        if pnl > 0:
            self._total_wins += 1
            self._consecutive_losses = 0
        elif pnl < 0:
            self._total_losses += 1
            self._consecutive_losses += 1
            if self._consecutive_losses >= self.circuit_breaker_trades:
                self._circuit_breaker_active = True
                self._circuit_breaker_time = time.time()
                print(f"[RC] Circuit breaker: "
                      f"{self._consecutive_losses} consecutive losses")

    def activate_kill_switch(self) -> None:
        self._kill_switch = True
        self._kill_switch_time = time.time()
        print("[RC] Kill switch MANUALLY activated")

    def deactivate_kill_switch(self) -> None:
        self._kill_switch = False
        self._kill_switch_time = 0.0
        self._consecutive_losses = 0
        self._circuit_breaker_active = False
        self._peak_equity = self._initial_equity
        print("[RC] Kill switch deactivated")

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
        print("[RC] RiskChecker reset")
