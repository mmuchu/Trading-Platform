"""
Stop Loss / Take Profit Manager.

Automates SL/TP execution for open positions.  Monitors positions against
configured stop-loss and take-profit thresholds and generates signals to
close positions when thresholds are breached.

This module publishes SIGNAL events with special metadata markers so the
execution layer knows these are SL/TP-triggered, not strategy-generated.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings
from core.v3.event_bus import EventBus
from core.v3.models import (
    BaseEvent,
    EventType,
    FillEvent,
    SignalEvent,
    Side,
)

logger = logging.getLogger(__name__)


@dataclass
class SLTPConfig:
    """Stop-loss and take-profit configuration."""

    stop_loss_pct: float = 0.0      # e.g. 0.02 = 2%
    take_profit_pct: float = 0.0    # e.g. 0.04 = 4%
    trailing_stop_pct: float = 0.0  # e.g. 0.015 = 1.5% trailing
    trailing_stop_active: bool = False
    check_interval_sec: float = 1.0  # How often to check SL/TP


@dataclass
class SLTPTrigger:
    """Record of an SL/TP trigger event."""

    trigger_id: str = ""
    symbol: str = ""
    trigger_type: str = ""  # "STOP_LOSS" or "TAKE_PROFIT"
    side: str = ""
    entry_price: float = 0.0
    trigger_price: float = 0.0
    pnl_at_trigger: float = 0.0
    timestamp: float = 0.0


class SLTPManager:
    """Monitors positions and triggers SL/TP signals when thresholds are hit.

    Parameters
    ----------
    bus : EventBus
        Shared event bus to publish SL/TP signals.
    config : SLTPConfig
        Stop-loss and take-profit configuration.
    """

    def __init__(
        self,
        bus: EventBus,
        config: Optional[SLTPConfig] = None,
    ) -> None:
        self.bus = bus
        self.config = config or SLTPConfig(
            stop_loss_pct=settings.risk.stop_loss_pct,
            take_profit_pct=settings.risk.take_profit_pct,
        )

        # Per-position tracking: symbol -> {entry_price, qty, side, high_water_mark}
        self._positions: Dict[str, Dict[str, Any]] = {}

        # Trigger history
        self._triggers: List[SLTPTrigger] = []

        # Stats
        self._sl_count: int = 0
        self._tp_count: int = 0
        self._check_count: int = 0

        # Lifecycle
        self._task = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        logger.info(
            "SLTPManager started (SL=%.2f%%, TP=%.2f%%, trailing=%s)",
            self.config.stop_loss_pct * 100,
            self.config.take_profit_pct * 100,
            self.config.trailing_stop_active,
        )

    async def stop(self) -> None:
        self._running = False
        logger.info("SLTPManager stopped")

    # ------------------------------------------------------------------
    # Position tracking
    # ------------------------------------------------------------------

    def update_position(
        self,
        symbol: str,
        qty: float,
        avg_entry: float,
        side: str = "",
    ) -> None:
        """Update position state for SL/TP monitoring.

        Call this after every fill to keep SL/TP state in sync.
        """
        if abs(qty) < 1e-8:
            if symbol in self._positions:
                logger.debug(
                    "SLTP: position %s closed, removing tracking", symbol
                )
                del self._positions[symbol]
            return

        actual_side = "LONG" if qty > 0 else "SHORT"

        self._positions[symbol] = {
            "qty": qty,
            "avg_entry": avg_entry,
            "side": actual_side,
            "high_water_mark": avg_entry,
            "low_water_mark": avg_entry,
            "opened_at": time.time(),
        }

    # ------------------------------------------------------------------
    # Price update (called on every tick)
    # ------------------------------------------------------------------

    async def on_price_update(self, symbol: str, price: float) -> Optional[SignalEvent]:
        """Check SL/TP conditions on price update.

        Returns a SignalEvent if an SL/TP should be triggered, else None.
        """
        self._check_count += 1
        pos = self._positions.get(symbol)
        if not pos:
            return None

        signal = None

        if pos["side"] == "LONG":
            signal = self._check_long(symbol, pos, price)
        elif pos["side"] == "SHORT":
            signal = self._check_short(symbol, pos, price)

        # Update water marks for trailing stop
        if pos["side"] == "LONG" and price > pos["high_water_mark"]:
            pos["high_water_mark"] = price
        elif pos["side"] == "SHORT" and (price < pos["low_water_mark"] or pos["low_water_mark"] == pos["avg_entry"]):
            pos["low_water_mark"] = price

        return signal

    def _check_long(
        self, symbol: str, pos: Dict[str, Any], price: float
    ) -> Optional[SignalEvent]:
        """Check SL/TP for a long position."""
        entry = pos["avg_entry"]
        pnl_pct = (price - entry) / entry if entry > 0 else 0.0

        sl_threshold = self.config.stop_loss_pct
        if self.config.trailing_stop_active and self.config.trailing_stop_pct > 0:
            trailing_sl = (pos["high_water_mark"] - price) / pos["high_water_mark"]
            if trailing_sl >= self.config.trailing_stop_pct:
                return self._create_trigger(
                    symbol, "STOP_LOSS", "SELL", entry, price, pnl_pct,
                    reason=f"Trailing stop hit: {trailing_sl:.2%}",
                )
            effective_sl_level = pos["high_water_mark"] * (1 - self.config.trailing_stop_pct)
            if price <= effective_sl_level:
                return self._create_trigger(
                    symbol, "STOP_LOSS", "SELL", entry, price, pnl_pct,
                    reason=f"Trailing stop price level: {effective_sl_level:.2f}",
                )
        elif pnl_pct <= -sl_threshold:
            return self._create_trigger(
                symbol, "STOP_LOSS", "SELL", entry, price, pnl_pct,
                reason=f"Fixed stop loss: {pnl_pct:.2%} <= -{sl_threshold:.2%}",
            )

        tp_threshold = self.config.take_profit_pct
        if pnl_pct >= tp_threshold:
            return self._create_trigger(
                symbol, "TAKE_PROFIT", "SELL", entry, price, pnl_pct,
                reason=f"Take profit: {pnl_pct:.2%} >= {tp_threshold:.2%}",
            )

        return None

    def _check_short(
        self, symbol: str, pos: Dict[str, Any], price: float
    ) -> Optional[SignalEvent]:
        """Check SL/TP for a short position."""
        entry = pos["avg_entry"]
        pnl_pct = (entry - price) / entry if entry > 0 else 0.0

        sl_threshold = self.config.stop_loss_pct
        if pnl_pct <= -sl_threshold:
            return self._create_trigger(
                symbol, "STOP_LOSS", "BUY", entry, price, pnl_pct,
                reason=f"Short stop loss: {pnl_pct:.2%} <= -{sl_threshold:.2%}",
            )

        tp_threshold = self.config.take_profit_pct
        if pnl_pct >= tp_threshold:
            return self._create_trigger(
                symbol, "TAKE_PROFIT", "BUY", entry, price, pnl_pct,
                reason=f"Short take profit: {pnl_pct:.2%} >= {tp_threshold:.2%}",
            )

        return None

    def _create_trigger(
        self,
        symbol: str,
        trigger_type: str,
        side: str,
        entry: float,
        price: float,
        pnl_pct: float,
        reason: str = "",
    ) -> SignalEvent:
        """Create an SL/TP trigger signal and record it."""
        trigger = SLTPTrigger(
            trigger_id=f"sltp-{uuid.uuid4().hex[:8]}",
            symbol=symbol,
            trigger_type=trigger_type,
            side=side,
            entry_price=entry,
            trigger_price=price,
            pnl_at_trigger=pnl_pct,
            timestamp=time.time(),
        )
        self._triggers.append(trigger)

        if trigger_type == "STOP_LOSS":
            self._sl_count += 1
        else:
            self._tp_count += 1

        logger.warning(
            "SL/TP TRIGGER: %s on %s @ %.2f (entry=%.2f, pnl=%.2%%) — %s",
            trigger_type, symbol, price, entry, pnl_pct * 100, reason,
        )

        signal = SignalEvent(
            symbol=symbol,
            side=Side.BUY if side == "BUY" else Side.SELL,
            price=price,
            strength=1.0,
            source=__import__("core.v3.models", fromlist=["SignalSource"]).SignalSource.RULE_ENGINE,
            metadata={
                "trigger_type": trigger_type,
                "trigger_reason": reason,
                "entry_price": entry,
                "pnl_pct": round(pnl_pct, 6),
                "bypass_cooldown": True,
            },
        )
        return signal

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "sl_triggers": self._sl_count,
            "tp_triggers": self._tp_count,
            "total_checks": self._check_count,
            "positions_monitored": len(self._positions),
            "config": {
                "stop_loss_pct": self.config.stop_loss_pct,
                "take_profit_pct": self.config.take_profit_pct,
                "trailing_stop": self.config.trailing_stop_active,
                "trailing_stop_pct": self.config.trailing_stop_pct,
            },
        }

    def get_triggers(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent SL/TP triggers."""
        recent = self._triggers[-limit:]
        return [
            {
                "trigger_id": t.trigger_id,
                "symbol": t.symbol,
                "type": t.trigger_type,
                "side": t.side,
                "entry": t.entry_price,
                "trigger_price": t.trigger_price,
                "pnl_pct": round(t.pnl_at_trigger * 100, 2),
                "timestamp": t.timestamp,
            }
            for t in recent
        ]

    def reset(self) -> None:
        self._positions.clear()
        self._triggers.clear()
        self._sl_count = 0
        self._tp_count = 0
        self._check_count = 0
        logger.info("SLTPManager reset")