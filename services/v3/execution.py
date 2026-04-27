"""
v3.2 Execution Service
=======================
Regime-aware, state-machine-gated execution with:
  - Hard SL/TP at tick level (not just at signal time)
  - Volatility-adjusted position sizing (ATR-based)
  - Position FSM integration (all fills go through state machine)
  - Cash reserve enforcement
  - Per-trade risk budgeting

This replaces the v3.1 execution.py which had passive SL/TP
and no structural position lifecycle control.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Dict, Optional, Tuple

from core.v3.event_bus import EventBus
from core.v3.models import (
    BaseEvent, EventType, FillEvent, RiskRejectedEvent,
    SignalEvent, Side, TickEvent,
)
from config.settings import settings
from services.v3.position_fsm import PositionStateMachine, PositionConfig

logger = logging.getLogger(__name__)


class PositionTracker:
    """Tracks fills, PnL, and cash per symbol."""

    def __init__(self) -> None:
        self.quantity: float = 0.0
        self.avg_entry: float = 0.0
        self.realized_pnl: float = 0.0
        self.total_commission: float = 0.0
        self.trade_count: int = 0
        self.win_count: int = 0
        self.loss_count: int = 0


class V3ExecutionService:
    """
    State-machine-gated execution with hard SL/TP and vol-adjusted sizing.
    """

    def __init__(
        self,
        bus: EventBus,
        fsm: PositionStateMachine | None = None,
        paper_broker=None,
    ) -> None:
        self.bus = bus
        self.fsm = fsm  # injected from orchestrator
        self._broker = paper_broker

        cfg = settings.broker
        self._cash: float = cfg.initial_cash
        self._max_position: float = float(settings.risk.max_position_size)
        self._commission_pct: float = cfg.commission_pct
        self._slippage_pct: float = cfg.slippage_pct
        self._initial_cash: float = cfg.initial_cash

        # SL/TP settings
        self._sl_pct: float = settings.risk.stop_loss_pct
        self._tp_pct: float = settings.risk.take_profit_pct

        # Cash reserve
        self._cash_reserve_pct: float = getattr(settings.risk, 'cash_reserve_pct', 0.30)

        # Current prices (updated via tick)
        self._prices: Dict[str, float] = {}

        # Position tracking
        self._positions: Dict[str, PositionTracker] = {}
        self._fills: list[FillEvent] = []
        self._submitted = 0
        self._rejected = 0
        self._sl_exits = 0
        self._tp_exits = 0
        self._start = time.time()

        # Recent trade results for risk checker
        self._recent_pnls: list[float] = []  # last N trade PnLs

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def equity(self) -> float:
        """Equity = cash + position_value + realized_pnl."""
        position_value = sum(
            pos.quantity * self._prices.get(sym, pos.avg_entry)
            for sym, pos in self._positions.items()
        )
        realized = sum(p.realized_pnl for p in self._positions.values())
        return self._cash + position_value + realized

    @property
    def total_realized_pnl(self) -> float:
        return sum(p.realized_pnl for p in self._positions.values())

    @property
    def available_cash(self) -> float:
        """Cash available for new positions (after reserve)."""
        reserve = self._initial_cash * self._cash_reserve_pct
        return max(self._cash - reserve, 0)

    def get_position(self, symbol: str) -> PositionTracker:
        if symbol not in self._positions:
            self._positions[symbol] = PositionTracker()
        return self._positions[symbol]

    def update_price(self, symbol: str, price: float) -> None:
        """Update current price — called on every tick for SL/TP monitoring."""
        self._prices[symbol] = price

        # Also update FSM for unrealized PnL tracking
        if self.fsm:
            self.fsm.update_price(symbol, price)

    def check_sl_tp(self, symbol: str) -> Optional[str]:
        """
        Check if SL or TP has been hit for the current position.
        Returns trigger string ('sl_hit', 'tp_hit') or None.
        Call on every tick for hard enforcement.
        """
        price = self._prices.get(symbol)
        if not price:
            return None

        pos = self.get_position(symbol)
        if pos.quantity == 0 or pos.avg_entry == 0:
            return None

        if pos.quantity > 0:  # Long position
            pnl_pct = (price - pos.avg_entry) / pos.avg_entry
            if pnl_pct <= -self._sl_pct:
                self._sl_exits += 1
                logger.warning(
                    "SL HIT %s: entry=%.2f current=%.2f loss=%.2f%%",
                    symbol, pos.avg_entry, price, pnl_pct * 100,
                )
                return "sl_hit"
            if pnl_pct >= self._tp_pct:
                self._tp_exits += 1
                logger.info(
                    "TP HIT %s: entry=%.2f current=%.2f gain=%.2f%%",
                    symbol, pos.avg_entry, price, pnl_pct * 100,
                )
                return "tp_hit"

        elif pos.quantity < 0:  # Short position
            pnl_pct = (pos.avg_entry - price) / pos.avg_entry
            if pnl_pct <= -self._sl_pct:
                self._sl_exits += 1
                logger.warning(
                    "SL HIT %s (short): entry=%.2f current=%.2f loss=%.2f%%",
                    symbol, pos.avg_entry, price, pnl_pct * 100,
                )
                return "sl_hit"
            if pnl_pct >= self._tp_pct:
                self._tp_exits += 1
                logger.info(
                    "TP HIT %s (short): entry=%.2f current=%.2f gain=%.2f%%",
                    symbol, pos.avg_entry, price, pnl_pct * 100,
                )
                return "tp_hit"

        return None

    def compute_position_size(
        self,
        signal: SignalEvent,
        atr_pct: float = 0.0,
    ) -> float:
        """
        Compute volatility-adjusted position size.

        Base: risk_per_trade_pct of equity / (ATR-based stop distance)
        Capped by: max_position_size, available_cash
        """
        equity = self.equity
        risk_budget = equity * settings.risk.risk_per_trade_pct  # e.g., $100 on $10K at 1%

        # Stop distance: use ATR if available, else use SL percentage
        if atr_pct > 0:
            stop_distance = atr_pct * 2  # 2x ATR stop
        else:
            stop_distance = self._sl_pct

        if stop_distance == 0:
            return 0.0

        # Target size based on risk budget and stop distance
        target_value = risk_budget / stop_distance
        target_qty = target_value / signal.price if signal.price > 0 else 0

        # Cap by max position
        target_qty = min(target_qty, self._max_position)

        # Cap by available cash
        cost = target_qty * signal.price * (1 + self._commission_pct + self._slippage_pct)
        if cost > self.available_cash:
            target_qty = self.available_cash / (signal.price * (1 + self._commission_pct + self._slippage_pct))

        return round(max(target_qty, 0.001), 6)  # minimum 0.001 BTC

    async def handle_signal(self, event: BaseEvent) -> None:
        """Process a scored signal through risk gate and state machine."""
        if not isinstance(event, SignalEvent):
            return

        signal = event
        sym = signal.symbol

        # FSM gate: can we accept this signal?
        if self.fsm:
            can_accept, reason = self.fsm.can_accept_signal(sym, signal.side)

            if not can_accept:
                # Check if it's a reverse signal that should trigger EXIT
                if "Reverse" in reason:
                    # Trigger exit through FSM
                    success, new_state = self.fsm.try_transition(sym, "signal_reverse")
                    if success and new_state.value == "EXIT":
                        # Execute exit order
                        await self._execute_exit(sym, signal.price, "signal_reverse")
                    return
                else:
                    self._rejected += 1
                    logger.info("FSM rejected %s %s: %s", signal.side.value, sym, reason)
                    return

            # Accept signal → transition to ENTERING
            atr = signal.metadata.get('atr_pct',0.0)
            qty = self.compute_position_size(signal, atr_pct=atr)
            if self.fsm and sym in self.fsm._positions and self.get_position(sym).quantity==0:
              self.fsm._positions.pop(sym)

            success, new_state = self.fsm.try_transition(
                sym, "open", side=signal.side, quantity=qty,
                price=signal.price, regime=signal.regime, score=signal.score,
            )
            if not success:
                self._rejected += 1
                return

        # Risk checks
        rejection = self._check_risk(signal)
        if rejection:
            self._rejected += 1
            # Revert FSM on risk rejection
            if self.fsm:
                self.fsm.try_transition(sym, "cancel")
            await self.bus.publish(rejection)
            logger.warning("Risk rejected %s: %s", signal.symbol, rejection.reason)
            return

        # Execute fill
        fill = self._simulate_fill(signal)
        self._fills.append(fill)
        self._update_position(fill)
        self._submitted += 1

        # Confirm fill to FSM
        if self.fsm:
            self.fsm.try_transition(sym, "fill_confirmed", price=fill.price)

        # Bridge to existing PaperBroker if available
        if self._broker:
            try:
                self._broker.execute(signal.side.value, fill.price)
            except Exception:
                logger.exception("PaperBroker bridge error")

        await self.bus.publish(fill)
        logger.info(
            "FILL %s %s %.6f @ %.2f comm=%.4f score=%.1f",
            fill.side.value, fill.symbol, fill.quantity, fill.price,
            fill.commission, signal.score,
        )

    async def execute_sl_tp(self, symbol: str, trigger: str, price: float) -> None:
        """Execute SL or TP exit triggered by tick-level monitoring."""
        pos = self.get_position(symbol)
        if pos.quantity == 0:
            return

        side = Side.SELL if pos.quantity > 0 else Side.BUY
        exit_signal = SignalEvent(
            symbol=symbol, side=side, price=price, strength=1.0, score=100.0,
            metadata={"trigger": trigger},
        )

        await self._execute_exit(symbol, price, trigger)

    async def _execute_exit(self, symbol: str, price: float, trigger: str) -> None:
        """Execute a position exit (SL/TP/reverse)."""
        pos = self.get_position(symbol)

        if pos.quantity == 0:
            if self.fsm:
                self.fsm.try_transition(symbol, "cooldown_elapsed")
            return

        side = Side.SELL if pos.quantity > 0 else Side.BUY

        # Simulate exit fill
        slip = self._slippage_pct
        fill_price = price * (1 - slip) if side == Side.SELL else price * (1 + slip)
        commission = fill_price * abs(pos.quantity) * self._commission_pct

        fill = FillEvent(
            fill_id=uuid.uuid4().hex[:12], order_id=uuid.uuid4().hex[:12],
            symbol=symbol, side=side, quantity=abs(pos.quantity),
            price=round(fill_price, 2), commission=round(commission, 4),
        )

        self._fills.append(fill)
        self._update_position(fill)
        self._submitted += 1

        # Confirm exit to FSM
        if self.fsm:
            self.fsm.try_transition(symbol, "fill_confirmed", price=fill.price)

        # Record trade result for risk checker
        if fill.quantity > 0:
            trade_pnl = pos.realized_pnl  # latest realized portion
            self._recent_pnls.append(trade_pnl)

        await self.bus.publish(fill)
        logger.info(
            "EXIT %s %s %.6f @ %.2f trigger=%s",
            side.value, symbol, fill.quantity, fill.price, trigger,
        )

    def _check_risk(self, signal: SignalEvent) -> Optional[RiskRejectedEvent]:
        """Pre-trade risk checks."""
        pos = self.get_position(signal.symbol)

        # Max position size
        if signal.side == Side.BUY and pos.quantity >= self._max_position:
            return RiskRejectedEvent(reason=f"Max long position {self._max_position}")
        if signal.side == Side.SELL and pos.quantity <= -self._max_position:
            return RiskRejectedEvent(reason=f"Max short position -{self._max_position}")

        # Cash check
        atr2 = signal.metadata.get('atr_pct',0.0)
          estimated_cost = signal.price * self.compute_position_size(signal, atr_pct=atr2) * (1 + self._commission_pct + self._slippage_pct)
        if signal.side == Side.BUY and self._cash < estimated_cost:
            return RiskRejectedEvent(reason=f"Insufficient cash (have ${self._cash:.2f}, need ${estimated_cost:.2f})")

        # Cash reserve check
        if signal.side == Side.BUY:
            max_spendable = self.available_cash
            if estimated_cost > max_spendable:
                return RiskRejectedEvent(
                    reason=f"Would breach cash reserve (max spendable: ${max_spendable:.2f})"
                )

        return None

    def _simulate_fill(self, signal: SignalEvent) -> FillEvent:
        """Simulate order fill with slippage and commission."""
        slip = self._slippage_pct
        if signal.side == Side.BUY:
            fill_price = signal.price * (1 + slip)
        else:
            fill_price = signal.price * (1 - slip)

        atr = signal.metadata.get('atr_pct',0.0)
            qty = self.compute_position_size(signal, atr_pct=atr)
        commission = fill_price * qty * self._commission_pct

        return FillEvent(
            fill_id=uuid.uuid4().hex[:12], order_id=uuid.uuid4().hex[:12],
            symbol=signal.symbol, side=signal.side, quantity=qty,
            price=round(fill_price, 2), commission=round(commission, 4),
        )

    def _update_position(self, fill: FillEvent) -> None:
        """Update position tracker after fill."""
        pos = self.get_position(fill.symbol)
        qty = fill.quantity if fill.side == Side.BUY else -fill.quantity
        cost = fill.price * abs(qty)

        if (pos.quantity > 0 and qty > 0) or (pos.quantity < 0 and qty < 0):
            # Adding to position
            total = pos.quantity + qty
            pos.avg_entry = (
                (pos.avg_entry * abs(pos.quantity) + fill.price * abs(qty)) / abs(total)
                if total != 0 else 0
            )
            pos.quantity = total
        elif (pos.quantity > 0 and qty < 0) or (pos.quantity < 0 and qty > 0):
            # Reducing / closing position
            close = min(abs(qty), abs(pos.quantity))
            pnl = (fill.price - pos.avg_entry) * close
            if pos.quantity < 0:
                pnl = -pnl
            pos.realized_pnl += pnl
            if pnl > 0:
                pos.win_count += 1
            elif pnl < 0:
                pos.loss_count += 1
            pos.quantity += qty
            if abs(pos.quantity) < 1e-8:
                pos.quantity = 0
                pos.avg_entry = 0
        else:
            # Opening new position from flat
            pos.quantity = qty
            pos.avg_entry = fill.price

        # Update cash
        if fill.side == Side.BUY:
            self._cash -= cost + fill.commission
        else:
            self._cash += cost - fill.commission

        pos.total_commission += fill.commission
        pos.trade_count += 1

    @property
    def stats(self) -> dict:
        total_wins = sum(p.win_count for p in self._positions.values())
        total_losses = sum(p.loss_count for p in self._positions.values())
        total_trades = total_wins + total_losses
        win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0

        return {
            "cash": round(self._cash, 2),
            "equity": round(self.equity, 2),
            "available_cash": round(self.available_cash, 2),
            "realized_pnl": round(self.total_realized_pnl, 2),
            "orders_submitted": self._submitted,
            "orders_rejected": self._rejected,
            "sl_exits": self._sl_exits,
            "tp_exits": self._tp_exits,
            "win_rate": round(win_rate, 1),
            "positions": {
                s: {
                    "quantity": round(p.quantity, 6),
                    "avg_entry": round(p.avg_entry, 2),
                    "realized_pnl": round(p.realized_pnl, 2),
                    "trades": p.trade_count,
                    "wins": p.win_count,
                    "losses": p.loss_count,
                }
                for s, p in self._positions.items()
            },
            "uptime": round(time.time() - self._start, 1),
        }
