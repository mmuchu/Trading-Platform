"""V3 Execution Service — receives SignalEvents, runs 5-gate risk check,
and simulates fills with slippage and commission.

v3.1: Delegates all pre-trade risk checks to PortfolioRiskGuard instead of
the old inline _check_risk().  The risk guard handles:
  - Feed alive check
  - Signal validation
  - Drawdown / circuit breaker / kill switch
  - Position sync verification
  - Cooldown enforcement
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from config.settings import settings
from core.v3.event_bus import EventBus
from core.v3.models import (
    BaseEvent,
    FillEvent,
    RiskRejectedEvent,
    Side,
    SignalEvent,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Position tracker
# ---------------------------------------------------------------------------

@dataclass
class PositionTracker:
    """Tracks a single-symbol position."""

    quantity: float = 0.0
    avg_entry: float = 0.0
    realized_pnl: float = 0.0

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0.0


# ---------------------------------------------------------------------------
# Execution service
# ---------------------------------------------------------------------------

class V3ExecutionService:
    """Simulated broker that listens for SIGNAL events, delegates risk
    checking to PortfolioRiskGuard, and publishes FILL or RISK_REJECTED events.

    v3.1: Risk checks are delegated to ``risk_guard``.  If no guard is set,
    falls back to a basic internal check (backward compatible).

    Parameters
    ----------
    bus:
        The shared v3 :class:`EventBus`.
    """

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus

        # Risk guard reference (set by orchestrator)
        self._risk_guard: Optional[Any] = None

        # Capital -----------------------------------------------------------
        self._cash: float = settings.broker.initial_cash
        self._initial_cash: float = self._cash

        # Fee / slippage model ----------------------------------------------
        self._commission_pct: float = settings.broker.commission_pct
        self._slippage_pct: float = settings.broker.slippage_pct

        # Risk limits -------------------------------------------------------
        self._max_position_size: int = settings.risk.max_position_size

        # Position tracking (symbol -> PositionTracker) ----------------------
        self._positions: Dict[str, PositionTracker] = {}

        # Counters ----------------------------------------------------------
        self._orders_placed: int = 0
        self._orders_rejected: int = 0
        self._fills_total: int = 0

    # ------------------------------------------------------------------
    # Wire risk guard
    # ------------------------------------------------------------------

    def set_risk_guard(self, risk_guard: Any) -> None:
        """Set the PortfolioRiskGuard instance for pre-trade checks."""
        self._risk_guard = risk_guard
        logger.info("V3ExecutionService: risk guard wired")

    # ------------------------------------------------------------------
    # Bus subscription helpers
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """No-op start — orchestrator wires subscriptions."""
        logger.info(
            "V3ExecutionService started (cash=%.2f, commission=%.4f, slippage=%.4f, risk_guard=%s)",
            self._cash,
            self._commission_pct,
            self._slippage_pct,
            "ACTIVE" if self._risk_guard else "NONE",
        )

    async def stop(self) -> None:
        """No-op stop."""
        logger.info("V3ExecutionService stopped")

    # ------------------------------------------------------------------
    # Signal handler
    # ------------------------------------------------------------------

    async def handle_signal(self, event: BaseEvent) -> None:
        signal = event  # routed from SIGNAL topic
        if not isinstance(signal, SignalEvent):
            return

        order_id = f"ord-{uuid.uuid4().hex[:12]}"
        self._orders_placed += 1

        # --- Risk gate (delegated to PortfolioRiskGuard) --------------------
        if self._risk_guard is not None:
            gate_result = await self._risk_guard.evaluate(signal)
            if not gate_result.approved:
                rejection = RiskRejectedEvent(
                    order_id=order_id,
                    reason=gate_result.rejection_reason,
                )
                await self.bus.publish(rejection)
                self._orders_rejected += 1
                logger.warning(
                    "Order %s REJECTED by %s: %s",
                    order_id,
                    gate_result.rejection_gate,
                    gate_result.rejection_reason,
                )
                return
        else:
            # Fallback: basic risk check (no risk guard)
            rejection = self._check_risk_fallback(signal, order_id)
            if rejection is not None:
                await self.bus.publish(rejection)
                self._orders_rejected += 1
                logger.warning("Order %s rejected (fallback): %s", order_id, rejection.reason)
                return

        # --- Fill simulation -----------------------------------------------
        fill = self._simulate_fill(signal, order_id)
        if fill.quantity <= 0: return
        await self.bus.publish(fill)
        self._fills_total += 1
        logger.info(
            "Fill %s: %s %s qty=%.4f @ %.2f (commission=%.4f)",
            fill.fill_id,
            fill.side.value,
            fill.symbol,
            fill.quantity,
            fill.price,
            fill.commission,
        )

    # ------------------------------------------------------------------
    # Fallback risk check (when no risk guard is set)
    # ------------------------------------------------------------------

    def _check_risk_fallback(
        self,
        signal: SignalEvent,
        order_id: str,
    ) -> Optional[RiskRejectedEvent]:
        """Basic risk check — used only when PortfolioRiskGuard is not set."""
        pos = self._positions.setdefault(signal.symbol, PositionTracker())

        max_qty = self._max_position_size * signal.strength
        max_qty = max(max_qty, 0.01)

        if signal.side == Side.BUY:
            notional = max_qty * signal.price * (1 + self._commission_pct)
            if notional > self._cash:
                return RiskRejectedEvent(
                    order_id=order_id,
                    reason=f"Insufficient cash: need ${notional:.2f}, have ${self._cash:.2f}",
                )

        if signal.side == Side.BUY and pos.is_long:
            if pos.quantity + max_qty > self._max_position_size:
                return RiskRejectedEvent(
                    order_id=order_id,
                    reason=f"Position limit: {pos.quantity:.4f} + {max_qty:.4f} exceeds max {self._max_position_size}",
                )

        return None

    # ------------------------------------------------------------------
    # Fill simulation
    # ------------------------------------------------------------------

    def _simulate_fill(
        self,
        signal: SignalEvent,
        order_id: str,
    ) -> FillEvent:
        """Apply slippage, compute quantity, deduct/add cash, update position."""
        pos = self._positions.setdefault(signal.symbol, PositionTracker())

        # Slippage-adjusted price
        if signal.side == Side.BUY:
            exec_price = signal.price * (1 + self._slippage_pct)
        else:
            exec_price = signal.price * (1 - self._slippage_pct)

        # Determine trade quantity based on strength & risk limits
        max_qty = self._max_position_size * max(signal.strength, 0.1)
        if signal.side == Side.BUY:
            affordable = self._cash / (exec_price * (1 + self._commission_pct))
            qty = min(max_qty, affordable)
        else:
            qty = min(max_qty, abs(pos.quantity)) if pos.is_long else max_qty
            if pos.is_flat or pos.is_short:
                qty = max_qty

        mx=self._max_position_size
        if signal.side==Side.SELL and pos.quantity<0:
            gap=mx-abs(pos.quantity)
            if gap<qty:qty=max(gap,0)
        if signal.side==Side.BUY and pos.quantity>0:
            gap=mx-pos.quantity
            if gap<qty:qty=max(gap,0)
        qty = round(qty, 8)
        if qty <= 0:
            qty = 0.0

        notional = qty * exec_price
        commission = notional * self._commission_pct

        # Update cash
        if signal.side == Side.BUY:
            self._cash -= (notional + commission)
        else:
            self._cash += (notional - commission)

        # Update position & realize PnL on closes
        self._update_position(pos, signal.side, qty, exec_price)

        fill_id = f"fill-{uuid.uuid4().hex[:12]}"
        return FillEvent(
            fill_id=fill_id,
            order_id=order_id,
            symbol=signal.symbol,
            side=signal.side,
            quantity=qty,
            price=round(exec_price, 2),
            commission=round(commission, 6),
        )

    # ------------------------------------------------------------------
    # Position accounting
    # ------------------------------------------------------------------

    def _update_position(
        self,
        pos: PositionTracker,
        side: Side,
        qty: float,
        price: float,
    ) -> None:
        """Update average entry and realise PnL for partial / full closes."""
        if qty == 0:
            return

        if side == Side.BUY:
            if pos.quantity <= 0:
                if pos.quantity < 0:
                    close_qty = min(qty, abs(pos.quantity))
                    trade_pnl = (pos.avg_entry - price) * close_qty
                    pos.realized_pnl += trade_pnl
                    pos.quantity += close_qty
                    leftover = qty - close_qty
                    if leftover > 0:
                        pos.quantity = leftover
                        pos.avg_entry = price
                    elif abs(pos.quantity) < 1e-8:
                        pos.quantity = 0.0
                        pos.avg_entry = 0.0
                else:
                    pos.quantity = qty
                    pos.avg_entry = price
            else:
                total_cost = pos.quantity * pos.avg_entry + qty * price
                pos.quantity += qty
                pos.avg_entry = total_cost / pos.quantity if pos.quantity else 0.0
        else:
            if pos.quantity >= 0:
                if pos.quantity > 0:
                    close_qty = min(qty, pos.quantity)
                    trade_pnl = (price - pos.avg_entry) * close_qty
                    pos.realized_pnl += trade_pnl
                    remaining = pos.quantity - close_qty
                    if remaining > 0:
                        pos.quantity = remaining
                    else:
                        pos.quantity = 0.0
                        pos.avg_entry = 0.0
                else:
                    pos.quantity = -qty
                    pos.avg_entry = price
            else:
                total_cost = abs(pos.quantity) * pos.avg_entry + qty * price
                pos.quantity -= qty
                pos.avg_entry = total_cost / abs(pos.quantity) if pos.quantity else 0.0
        pos.quantity=round(pos.quantity,8)
        if abs(pos.quantity)<1e-8:pos.quantity=0.0;pos.avg_entry=0.0

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    @property
    def equity(self) -> float:
        """Current total equity (cash + positions at last known prices)."""
        total_realized = sum(p.realized_pnl for p in self._positions.values())
        total_unrealized=sum(p.quantity*p.avg_entry for p in self._positions.values())
        return self._cash+total_realized+total_unrealized

    @property
    def stats(self) -> Dict[str, Any]:
        """Return a snapshot dict of execution statistics."""
        positions_summary = {}
        for sym, pos in self._positions.items():
            positions_summary[sym] = {
                "quantity": pos.quantity,
                "avg_entry": pos.avg_entry,
                "realized_pnl": pos.realized_pnl,
            }

        total_realized = sum(p.realized_pnl for p in self._positions.values())
        equity = self.equity

        return {
            "cash": round(self._cash, 2),
            "equity": round(equity, 2),
            "initial_cash": self._initial_cash,
            "total_realized_pnl": round(total_realized, 2),
            "positions": positions_summary,
            "max_position": self._max_position_size,
            "orders_submitted": self._orders_placed,
            "orders_rejected": self._orders_rejected,
            "fills_total": self._fills_total,
            "risk_guard": self._risk_guard is not None,
        }

    def get_position(self, symbol: str) -> PositionTracker:
        """Return the position tracker for *symbol*, creating one if needed."""
        return self._positions.setdefault(symbol, PositionTracker())