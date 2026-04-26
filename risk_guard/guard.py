"""
Portfolio Risk Guard — 5-gate pre-trade controller.

This is the central integration point.  The V3ExecutionService calls
``guard.evaluate(signal)`` instead of its own ``_check_risk()``.  All 5
gates must pass before a trade is approved.

Gate pipeline:
  1. FeedMonitor    — Is market data alive?
  2. SignalValidator — Is the signal structurally valid?
  3. RiskChecker    — Does the trade pass risk limits?
  4. PositionSync   — Is position state consistent?
  5. CooldownManager — Has enough time elapsed?

Any gate failure short-circuits and returns a rejection.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from core.v3.event_bus import EventBus
from core.v3.models import (
    BaseEvent,
    EventType,
    FillEvent,
    RiskRejectedEvent,
    SignalEvent,
)

from risk_guard.feed_monitor import FeedMonitor
from risk_guard.signal_validator import SignalValidator
from risk_guard.risk_checker import RiskChecker
from risk_guard.position_sync import PositionSyncGuard
from risk_guard.cooldown_manager import CooldownManager, CooldownConfig
from risk_guard.sl_tp_manager import SLTPManager

logger = logging.getLogger(__name__)


@dataclass
class GateResult:
    """Result of the 5-gate evaluation."""

    approved: bool = True
    rejection_reason: str = ""
    rejection_gate: str = ""  # Which gate rejected
    gate_results: Dict[str, bool] = field(default_factory=dict)
    gate_reasons: Dict[str, str] = field(default_factory=dict)
    evaluation_time_ms: float = 0.0

    # Risk context (always populated, even on rejection)
    risk_score: float = 0.0
    drawdown_pct: float = 0.0
    feed_alive: bool = True
    position_qty: float = 0.0


class PortfolioRiskGuard:
    """5-gate pre-trade risk controller.

    Parameters
    ----------
    bus : EventBus
        Shared event bus.
    execution_service : V3ExecutionService
        Reference to the execution service for position/equity queries.
    stale_threshold_sec : float
        Feed staleness threshold (Gate 1).
    cooldown_config : CooldownConfig
        Cooldown timing (Gate 5).
    """

    def __init__(
        self,
        bus: EventBus,
        execution_service: Any,
        stale_threshold_sec: float = 15.0,
        cooldown_config: Optional[CooldownConfig] = None,
    ) -> None:
        self.bus = bus
        self._execution = execution_service

        # Gate instances
        self.feed_monitor = FeedMonitor(
            bus, stale_threshold_sec=stale_threshold_sec
        )
        self.signal_validator = SignalValidator()
        self.risk_checker = RiskChecker()
        self.position_sync = PositionSyncGuard()
        self.cooldown = CooldownManager(
            config=cooldown_config or CooldownConfig()
        )
        self.sl_tp = SLTPManager(bus)

        # Stats
        self._total_evaluations: int = 0
        self._total_approved: int = 0
        self._total_rejected: int = 0
        self._gate_rejections: Dict[str, int] = {}
        self._last_result: Optional[GateResult] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start all gate monitors."""
        await self.feed_monitor.start()
        await self.sl_tp.start()
        # Subscribe to FILL events for trade result tracking
        self.bus.subscribe(EventType.FILL, self._on_fill)
        # Subscribe to TICK events for SL/TP monitoring
        self.bus.subscribe(EventType.TICK, self._on_tick_for_sltp)
        logger.info("PortfolioRiskGuard started (5 gates + SL/TP)")

    async def stop(self) -> None:
        """Stop all gate monitors."""
        await self.feed_monitor.stop()
        await self.sl_tp.stop()
        self.bus.unsubscribe(EventType.FILL, self._on_fill)
        self.bus.unsubscribe(EventType.TICK, self._on_tick_for_sltp)
        logger.info("PortfolioRiskGuard stopped")

    # ------------------------------------------------------------------
    # Main evaluation (called by execution service)
    # ------------------------------------------------------------------

    async def evaluate(self, signal: SignalEvent) -> GateResult:
        """Run all 5 gates on a signal.

        Returns
        -------
        GateResult
            ``approved`` is True only when ALL gates pass.
        """
        t0 = time.time()
        self._total_evaluations += 1

        result = GateResult()
        gates = self._build_gate_pipeline(signal)

        for gate_name, gate_fn in gates:
            try:
                passed, reason = await gate_fn(signal, result)
                result.gate_results[gate_name] = passed
                result.gate_reasons[gate_name] = reason

                if not passed:
                    result.approved = False
                    result.rejection_gate = gate_name
                    result.rejection_reason = f"[{gate_name}] {reason}"
                    self._total_rejected += 1
                    self._gate_rejections[gate_name] = (
                        self._gate_rejections.get(gate_name, 0) + 1
                    )
                    logger.warning(
                        "Gate BLOCKED: %s — %s (signal %s %s @ %.2f)",
                        gate_name, reason,
                        signal.side.value, signal.symbol, signal.price,
                    )
                    break
            except Exception:
                logger.exception("Gate %s raised exception", gate_name)
                result.gate_results[gate_name] = False
                result.gate_reasons[gate_name] = f"Gate error: {gate_name}"
                result.approved = False
                result.rejection_gate = gate_name
                result.rejection_reason = f"[{gate_name}] Exception during check"
                self._total_rejected += 1
                self._gate_rejections[gate_name] = (
                    self._gate_rejections.get(gate_name, 0) + 1
                )
                break

        if result.approved:
            self._total_approved += 1
            # Record cooldown
            self.cooldown.record_trade(signal.symbol, signal.side.value)

        result.evaluation_time_ms = (time.time() - t0) * 1000
        self._last_result = result
        return result

    # ------------------------------------------------------------------
    # Gate definitions
    # ------------------------------------------------------------------

    def _build_gate_pipeline(
        self, signal: SignalEvent
    ) -> List[tuple]:
        """Build the ordered list of (gate_name, gate_fn) pairs."""
        return [
            ("feed_alive", self._gate_feed),
            ("signal_valid", self._gate_signal),
            ("risk_check", self._gate_risk),
            ("position_sync", self._gate_position),
            ("cooldown", self._gate_cooldown),
        ]

    async def _gate_feed(
        self, signal: SignalEvent, result: GateResult
    ) -> tuple[bool, str]:
        """Gate 1: Is the feed alive?"""
        alive, reason = self.feed_monitor.is_alive(signal.symbol)
        result.feed_alive = alive

        # Allow SL/TP to bypass feed gate (emergency exits)
        if not alive and signal.metadata.get("trigger_type"):
            logger.warning(
                "SL/TP bypassing feed gate (feed dead, but %s trigger active)",
                signal.metadata["trigger_type"],
            )
            return True, "Bypassed for SL/TP"

        return alive, reason

    async def _gate_signal(
        self, signal: SignalEvent, result: GateResult
    ) -> tuple[bool, str]:
        """Gate 2: Is the signal valid?"""
        # Skip validation for SL/TP signals (they're system-generated)
        if signal.metadata.get("trigger_type"):
            return True, "SL/TP signal (system-generated)"

        # Get current position for direction check
        pos = self._execution.get_position(signal.symbol)
        exec_stats = self._execution.stats
        max_pos = exec_stats.get("max_position", 10.0)

        validation = self.signal_validator.validate(
            signal,
            current_position_qty=pos.quantity,
            max_position_size=max_pos,
        )

        return validation.valid, validation.reason

    async def _gate_risk(
        self, signal: SignalEvent, result: GateResult
    ) -> tuple[bool, str]:
        """Gate 3: Does the trade pass risk limits?"""
        # SL/TP signals bypass risk limits (emergency exits)
        if signal.metadata.get("trigger_type"):
            logger.info("SL/TP bypassing risk gate (%s)", signal.metadata["trigger_type"])
            return True, "SL/TP bypass"

        exec_stats = self._execution.stats
        pos = self._execution.get_position(signal.symbol)

        check = self.risk_checker.check(
            signal=signal,
            equity=exec_stats.get("equity", 0.0),
            cash=exec_stats.get("cash", 0.0),
            position_qty=pos.quantity,
        )

        result.risk_score = check.risk_score
        result.drawdown_pct = check.current_drawdown_pct

        return check.approved, check.reason

    async def _gate_position(
        self, signal: SignalEvent, result: GateResult
    ) -> tuple[bool, str]:
        """Gate 4: Is position state consistent?"""
        pos = self._execution.get_position(signal.symbol)
        # Get current market price
        feed_health = self.feed_monitor.health(signal.symbol)

        sync = self.position_sync.check(
            signal=signal,
            current_qty=pos.quantity,
            avg_entry=pos.avg_entry,
            current_price=feed_health.last_tick_price,
        )

        result.position_qty = pos.quantity

        return sync.synced, sync.reason

    async def _gate_cooldown(
        self, signal: SignalEvent, result: GateResult
    ) -> tuple[bool, str]:
        """Gate 5: Has enough time elapsed?"""
        # SL/TP bypasses cooldown
        if signal.metadata.get("bypass_cooldown"):
            return True, "SL/TP bypass"

        allowed, reason = self.cooldown.check(
            signal.symbol, signal.side.value
        )

        return allowed, reason

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    async def _on_fill(self, event: BaseEvent) -> None:
        """Track fill results for risk checker and SL/TP manager."""
        if not isinstance(event, FillEvent):
            return

        # Update SL/TP position tracking
        pos = self._execution.get_position(event.symbol)
        self.sl_tp.update_position(
            symbol=event.symbol,
            qty=pos.quantity,
            avg_entry=pos.avg_entry,
            side=event.side.value,
        )

        # Track PnL for circuit breaker (use simple approximation)
        # The analytics service will provide accurate PnL later

    async def _on_tick_for_sltp(self, event: BaseEvent) -> None:
        """Check SL/TP on every tick."""
        from core.v3.models import TickEvent
        if not isinstance(event, TickEvent):
            return

        # Also update signal validator's price
        self.signal_validator.update_price(event.symbol, event.price)

        # Check SL/TP
        sltp_signal = await self.sl_tp.on_price_update(event.symbol, event.price)
        if sltp_signal is not None:
            # Publish SL/TP signal — it will go through the normal pipeline
            # but with bypass flags in metadata
            logger.info(
                "SL/TP signal generated: %s %s @ %.2f",
                sltp_signal.side.value,
                sltp_signal.symbol,
                sltp_signal.price,
            )
            await self.bus.publish(sltp_signal)

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "total_evaluations": self._total_evaluations,
            "total_approved": self._total_approved,
            "total_rejected": self._total_rejected,
            "approval_rate": (
                self._total_approved / self._total_evaluations
                if self._total_evaluations > 0
                else 0.0
            ),
            "gate_rejections": dict(self._gate_rejections),
            "feed": self.feed_monitor.health_dict(),
            "signal_validator": self.signal_validator.stats,
            "risk_checker": self.risk_checker.stats,
            "position_sync": self.position_sync.stats,
            "cooldown": self.cooldown.stats,
            "sl_tp": self.sl_tp.stats,
        }

    def get_system_status(self) -> Dict[str, Any]:
        """Return comprehensive risk guard status for dashboard."""
        return {
            "gates_active": True,
            "feed_alive": self.feed_monitor.health_dict(),
            "risk": {
                "kill_switch": self.risk_checker.stats["kill_switch"],
                "circuit_breaker": self.risk_checker.stats["circuit_breaker"],
                "drawdown_pct": self.risk_checker.stats["current_drawdown_pct"],
                "risk_score": self.risk_checker.stats.get("current_drawdown_pct", 0) / self.risk_checker.max_drawdown_pct if self.risk_checker.max_drawdown_pct > 0 else 0,
            },
            "sl_tp": self.sl_tp.stats,
            "cooldown": self.cooldown.stats,
            "stats": {
                "total_evaluations": self._total_evaluations,
                "total_approved": self._total_approved,
                "total_rejected": self._total_rejected,
                "approval_rate": (
                    round(
                        self._total_approved / self._total_evaluations * 100, 1
                    )
                    if self._total_evaluations > 0
                    else 0.0
                ),
                "gate_rejections": dict(self._gate_rejections),
            },
            "last_evaluation": (
                asdict(self._last_result) if self._last_result else None
            ),
        }

    def reset(self) -> None:
        """Reset all gates to initial state."""
        self.signal_validator.reset()
        self.risk_checker.reset()
        self.position_sync.reset()
        self.cooldown.reset()
        self.sl_tp.reset()
        self._total_evaluations = 0
        self._total_approved = 0
        self._total_rejected = 0
        self._gate_rejections.clear()
        logger.info("PortfolioRiskGuard fully reset")
