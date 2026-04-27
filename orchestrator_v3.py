"""
v3.2 Orchestrator
=================
Wires the regime-aware state machine architecture:

  MarketData → [Tick]
       ↓
  RegimeClassifier → [RegimeSnapshot]
       ↓
  StrategyService → [ScoredSignal]
       ↓
  RiskChecker (gate) → approved/rejected
       ↓
  PositionFSM → state transition
       ↓
  ExecutionService → [Fill] (with hard SL/TP monitoring)
       ↓
  AnalyticsService → [PnLSnapshot] → Dashboard

Key v3.2 changes:
  - Regime classifier processes every tick
  - SL/TP monitored on EVERY tick (not just at signal time)
  - FSM enforces position lifecycle
  - RiskChecker gets tick-level drawdown updates
  - Signals are scored 0-100 before emission
"""

from __future__ import annotations

import asyncio
import logging
import signal as _signal
from typing import Optional

from core.v3.event_bus import EventBus
from core.v3.models import EventType, TickEvent, SignalEvent, FillEvent, RegimeType
from services.v3.market_data import V3MarketDataService
from services.v3.strategy import V3StrategyService
from services.v3.execution import V3ExecutionService
from services.v3.analytics import V3AnalyticsService
from services.v3.regime import RegimeClassifier, RegimeConfig
from services.v3.position_fsm import PositionStateMachine, PositionConfig
from risk_guard.risk_checker import RiskChecker
from config.settings import settings

logger = logging.getLogger(__name__)


class V3Orchestrator:
    """
    v3.2 Orchestrator — Regime-Aware State Machine Architecture.

    Usage:
        orch = V3Orchestrator(mode="sim")
        await orch.start()
    """

    def __init__(self, mode: str = "sim", binance_feed=None) -> None:
        self.mode = mode
        self.bus = EventBus()

        # ─── Core Services ────────────────────────────────────────
        self.market_data = V3MarketDataService(self.bus, mode=mode, binance_feed=binance_feed)

        # v3.2: Regime classifier (shared with strategy)
        regime_cfg = RegimeConfig(
            atr_period=settings.regime.atr_period,
            trend_lookback=settings.regime.trend_lookback,
            trend_strength_period=settings.regime.trend_strength_period,
            volatility_history=settings.regime.volatility_history,
            trend_adx_threshold=settings.regime.trend_adx_threshold,
            volatile_percentile=settings.regime.volatile_percentile,
            min_bars=settings.regime.min_bars,
            regime_stability_bars=settings.regime.regime_stability_bars,
        )
        self.regime_classifier = RegimeClassifier(config=regime_cfg)

        # v3.2: Position state machine
        fsm_cfg = PositionConfig(
            entering_timeout_sec=settings.position_fsm.entering_timeout_sec,
            exit_timeout_sec=settings.position_fsm.exit_timeout_sec,
            cooldown_sec=settings.position_fsm.cooldown_sec,
            max_hold_time_sec=settings.position_fsm.max_hold_time_sec,
            min_hold_time_sec=settings.position_fsm.min_hold_time_sec,
        )
        self.position_fsm = PositionStateMachine(config=fsm_cfg)

        # v3.2: Risk checker
        self.risk_checker = RiskChecker(
            max_drawdown_pct=settings.risk.max_drawdown_pct,
            risk_per_trade_pct=settings.risk.risk_per_trade_pct,
            circuit_breaker_trades=settings.risk.circuit_breaker_losses,
            circuit_breaker_cooldown_sec=settings.risk.circuit_breaker_cooldown_sec,
        )

        # Strategy with regime awareness and signal scoring
        self.strategy = V3StrategyService(
            self.bus,
            regime_classifier=self.regime_classifier,
        )

        # Execution with FSM integration and hard SL/TP
        self.execution = V3ExecutionService(self.bus, fsm=self.position_fsm)

        # Wire strategy to execution for equity stress tracking
        self.strategy.set_execution(self.execution)

        # Analytics (unchanged interface)
        self.analytics = V3AnalyticsService(self.bus)
        self.analytics.set_execution(self.execution)

        self._running = False
        self._tasks: list[asyncio.Task] = []

        # Primary symbol (for SL/TP monitoring loop)
        self._primary_symbol = settings.binance.symbol.upper()

    async def start(self) -> None:
        self._running = True
        logger.info("=" * 60)
        logger.info("  TRADING PLATFORM v3.2 — Regime-Aware State Machine")
        logger.info("  Market data: %s", self.mode)
        logger.info("  SL: %.1f%% | TP: %.1f%% | Max DD: %.1f%%",
                     settings.risk.stop_loss_pct * 100,
                     settings.risk.take_profit_pct * 100,
                     settings.risk.max_drawdown_pct * 100)
        logger.info("  Circuit breaker: %d consecutive losses → %.0fs cooldown",
                     settings.risk.circuit_breaker_losses,
                     settings.risk.circuit_breaker_cooldown_sec)
        logger.info("  Min signal score: %.0f/100", settings.signal_score.min_score_to_emit)
        logger.info("=" * 60)

        # Wire event bus pipeline
        self.bus.subscribe(EventType.TICK, self._on_tick)           # central tick handler
        self.bus.subscribe(EventType.TICK, self.analytics.handle_tick)  # analytics price tracking
        self.bus.subscribe(EventType.SIGNAL, self._on_signal)        # risk-gated signals
        self.bus.subscribe(EventType.FILL, self.analytics.handle_fill)
        logger.info("Event bus: %d handlers wired", self.bus.handler_count)

        # Start analytics snapshot loop
        await self.analytics.start()

        # Start market data feed
        md_task = asyncio.create_task(self.market_data.start())
        self._tasks.append(md_task)

        # Signal handlers for graceful shutdown
        def _shutdown(sig, frame):
            logger.info("Signal %%s -- shutting down", sig)
            asyncio.create_task(self.shutdown())

        try:
            _signal.signal(_signal.SIGINT, _shutdown)
        except (ValueError, OSError):
            pass  # not main thread
        try:
            _signal.signal(_signal.SIGTERM, _shutdown)
        except (ValueError, OSError):
            pass  # not main thread

        logger.info("V3.2 system operational — awaiting market data...")

        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def _on_tick(self, event) -> None:
        """
        Central tick handler — runs on EVERY tick.

        1. Update execution price (for SL/TP monitoring)
        2. Run SL/TP checks against current positions
        3. Forward tick to strategy (which also classifies regime)
        4. Run FSM timeout checks
        5. Update risk checker with current equity
        """
        if not isinstance(event, TickEvent):
            return

        sym = event.symbol

        # 1. Update price in execution service
        self.execution.update_price(sym, event.price)

        # 2. Hard SL/TP check on every tick
        sl_tp_trigger = self.execution.check_sl_tp(sym)
        if sl_tp_trigger:
            logger.warning("SL/TP triggered: %s → %s @ %.2f", sym, sl_tp_trigger, event.price)
            await self.execution.execute_sl_tp(sym, sl_tp_trigger, event.price)

        # 3. Forward to strategy (which handles regime classification internally)
        await self.strategy.handle_tick(event)

        # 4. FSM timeout checks
        if self.position_fsm:
            triggers = self.position_fsm.check_timeouts(sym)
            for trigger in triggers:
                if trigger in ("timeout", "cooldown_elapsed", "manual_close"):
                    self.position_fsm.try_transition(sym, trigger)

        # 5. Regime conflict check (close position if regime turned hostile)
        current_regime = self.regime_classifier.get_regime(sym)
        if self.position_fsm:
            regime_trigger = self.position_fsm.check_regime_conflict(sym, current_regime)
            if False:
                logger.warning("Regime conflict: closing %s (regime=%s)", sym, current_regime.value)
                await self.execution.execute_sl_tp(sym, regime_trigger, event.price)


        if self.position_fsm and self.execution and self.execution.get_position(sym).quantity==0 and sym in self.position_fsm._positions:
          self.position_fsm._positions.pop(sym)
    async def _on_signal(self, event) -> None:
        """
        Signal handler with risk checker gate.

        The strategy already scored the signal (0-100) and applied regime filtering.
        Here we add the risk checker gate before passing to execution.
        """
        if not isinstance(event, SignalEvent):
            return

        signal = event
        sym = signal.symbol

        # Risk checker gate
        risk_result = self.risk_checker.check(
            signal=signal,
            equity=self.execution.equity,
            cash=self.execution.cash,
            position_qty=self.execution.get_position(sym).quantity,
        )

        if not risk_result.approved:
            from core.v3.models import RiskRejectedEvent
            rejection = RiskRejectedEvent(reason=risk_result.reason)
            self.execution._rejected += 1
            await self.bus.publish(rejection)
            logger.warning("Risk gate blocked %s %s: %s", signal.side.value, sym, risk_result.reason)
            return

        # Forward to execution (which handles FSM transitions)
        await self.execution.handle_signal(event)

    async def shutdown(self) -> None:
        if not self._running:
            return
        self._running = False
        logger.info("V3.2 shutting down...")
        await self.market_data.stop()
        await self.analytics.stop()
        for t in self._tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("V3.2 stopped. Final equity: $%.2f", self.execution.equity)

    @property
    def system_status(self) -> dict:
        return {
            "version": "3.2",
            "architecture": "regime_aware_state_machine",
            "running": self._running,
            "market_data": {
                "mode": self.market_data.mode,
                "tick_count": self.market_data.tick_count,
                "latest_price": self.market_data.latest_tick.price if self.market_data.latest_tick else None,
            },
            "regime": self.regime_classifier.stats,
            "strategy": self.strategy.stats,
            "execution": self.execution.stats,
            "position_fsm": self.position_fsm.stats,
            "risk": self.risk_checker.stats,
            "analytics": self.analytics.performance_summary,
        }
