"""
v3.2 Strategy Service
======================
Regime-aware strategy with composite signal scoring (0-100).

Changes from v3.1:
  - MeanReversion REMOVED (momentum-only)
  - Signal scoring: 0-100 composite from momentum, regime alignment, volume
  - Regime filter: suppress/reduce signals in hostile regimes
  - Strategy state memory: tracks regime transitions and signal patterns
  - Configurable min score threshold (default 60) before emitting signals
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from core.v3.event_bus import EventBus
from core.v3.models import (
    BaseEvent, EventType, RegimeType, SignalEvent, Side, SignalSource, TickEvent,
)
from services.v3.regime import RegimeClassifier

logger = logging.getLogger(__name__)


@dataclass
class SignalScoreConfig:
    """Signal scoring weights and thresholds."""
    momentum_weight: float = 0.40         # weight of raw momentum score
    regime_weight: float = 0.25           # weight of regime alignment score
    strength_weight: float = 0.15         # weight of signal strength
    trend_weight: float = 0.20            # weight of trend alignment score
    min_score_to_emit: float = 45.0       # minimum composite score (0-100) to emit signal
    max_score: float = 100.0
    cooldown_sec: float = 10.0            # minimum time between signals
    volatile_regime_penalty: float = -30  # score penalty in VOLATILE regime
    range_regime_penalty: float = -15     # score penalty in RANGE regime (for momentum)


class BaseStrategy(ABC):
    name: str = "base"

    @abstractmethod
    async def evaluate(self, tick: TickEvent, history: deque) -> Optional[SignalEvent]:
        ...


class MomentumV3(BaseStrategy):
    """Trend-following momentum strategy — the ONLY strategy in v3.2."""
    name = "momentum"

    def __init__(
        self,
        lookback: int = 20,
        threshold_pct: float = 0.3,
        min_strength: float = 0.25,
    ) -> None:
        self.lookback = lookback
        self.threshold_pct = threshold_pct
        self.min_strength = min_strength

    async def evaluate(self, tick: TickEvent, history: deque) -> Optional[SignalEvent]:
        if len(history) < self.lookback:
            return None

        prices = np.array([t.price for t in list(history)[-self.lookback:]])
        pct = (tick.price - prices[0]) / prices[0] * 100

        if pct > self.threshold_pct:
            strength = min(abs(pct) / 1.0, 1.0)
            if strength < self.min_strength:
                return None
            return SignalEvent(
                symbol=tick.symbol, side=Side.BUY, price=tick.price,
                strength=strength, source=SignalSource.RULE_ENGINE,
                metadata={"strategy": self.name, "pct_change": round(pct, 4)},
            )
        elif pct < -self.threshold_pct:
            strength = min(abs(pct) / 1.0, 1.0)
            if strength < self.min_strength:
                return None
            return SignalEvent(
                symbol=tick.symbol, side=Side.SELL, price=tick.price,
                strength=strength, source=SignalSource.RULE_ENGINE,
                metadata={"strategy": self.name, "pct_change": round(pct, 4)},
            )
        return None


class V3StrategyService:
    """
    Regime-aware strategy orchestrator with composite signal scoring.

    Pipeline: Tick → Strategy.evaluate() → score() → regime_filter() → equity_stress_filter() → emit
    """

    def __init__(
        self,
        bus: EventBus,
        regime_classifier: RegimeClassifier | None = None,
        score_config: SignalScoreConfig | None = None,
    ) -> None:
        self.bus = bus
        self.regime = regime_classifier
        self.score_cfg = score_config or SignalScoreConfig()

        self._strategies: Dict[str, BaseStrategy] = {}
        self._history: Dict[str, deque] = {}
        self._last_signal: Dict[str, float] = {}
        self._last_regime: Dict[str, RegimeType] = {}
        self._regime_transition_count: Dict[str, int] = {}
        self._signal_count = 0
        self._signals_suppressed = 0
        self._signals_stress_blocked = 0
        self._tick_count = 0

        # Strategy state memory
        self._last_signal_side: Dict[str, Optional[Side]] = {}
        self._consecutive_same_side: Dict[str, int] = {}
        self._recent_scores: Dict[str, deque] = {}  # recent signal scores for quality tracking

        # Equity stress tracking (addresses failure: no tightening under drawdown)
        self._initial_equity: float = 0.0
        self._peak_equity: float = 0.0
        self._consecutive_losses: int = 0
        self._stress_level: float = 0.0  # 0=normal, 1=max stress

        # Execution reference for equity data
        self._execution = None

        # Only Momentum in v3.2
        momentum = MomentumV3()
        self._strategies[momentum.name] = momentum

    def set_execution(self, execution_service) -> None:
        """Set execution reference for equity stress tracking."""
        self._execution = execution_service

    def register_strategy(self, strategy: BaseStrategy) -> None:
        self._strategies[strategy.name] = strategy

    @property
    def enabled(self) -> List[str]:
        return list(self._strategies.keys())

    async def handle_tick(self, event: BaseEvent) -> None:
        """Process tick: classify regime, run strategies, score and filter signals."""
        if not isinstance(event, TickEvent):
            return

        self._tick_count += 1
        tick = event

        # Store history
        if tick.symbol not in self._history:
            self._history[tick.symbol] = deque(maxlen=500)
            self._recent_scores[tick.symbol] = deque(maxlen=20)
            self._consecutive_same_side[tick.symbol] = 0
        self._history[tick.symbol].append(tick)

        # Cooldown check
        now = time.time()
        if now - self._last_signal.get(tick.symbol, 0) < self.score_cfg.cooldown_sec:
            return

        # Update equity stress level
        self._update_stress_level()

        # HARD GATE: if stress is critical (>0.8), block ALL signals
        if self._stress_level > 0.8:
            return  # silent block — no log spam

        # Get current regime
        regime = RegimeType.RANGE
        regime_snapshot = None
        if self.regime:
            regime_snapshot = self.regime.classify_tick(tick)
            if regime_snapshot:
                regime = regime_snapshot.regime

        # Track regime transitions
        self._track_regime(tick.symbol, regime)

        # Run strategies
        history = self._history[tick.symbol]
        for strategy in self._strategies.values():
            try:
                signal = await strategy.evaluate(tick, history)
                if not signal:
                    continue

                # Score the signal
                score = self._score_signal(signal, regime, regime_snapshot)

                # Regime filtering: suppress in VOLATILE, penalize in RANGE
                filtered, reason = self._regime_filter(signal, regime, score)

                if not filtered:
                    self._signals_suppressed += 1
                    logger.info(
                        "Signal suppressed %s %s: %s (raw_score=%.1f)",
                        signal.side.value, tick.symbol, reason, score,
                    )
                    continue

                # Equity stress filter: raise score threshold under drawdown
                # At 0% stress: need base score (60). At 50% stress: need 80+. At 80%: blocked entirely.
                stress_adjusted_score = self._apply_stress_adjustment(score)
                if stress_adjusted_score < max(self.score_cfg.min_score_to_emit - 15, 30.0):
                    self._signals_stress_blocked += 1
                    logger.info(
                        "STRESS BLOCK %s %s: score=%.1f adjusted=%.1f stress=%.1f%%",
                        signal.side.value, tick.symbol, score,
                        stress_adjusted_score, self._stress_level * 100,
                    )
                    continue

                # Apply score to signal
                signal.score = stress_adjusted_score
                signal.regime = regime.value

                # Emit
                self._signal_count += 1
                self._last_signal[tick.symbol] = now
                self._recent_scores[tick.symbol].append(score)

                # Update state memory
                self._update_signal_memory(tick.symbol, signal.side)

                logger.info(
                    "SIGNAL %s %s @ %.2f score=%.1f regime=%s",
                    signal.side.value, signal.symbol, signal.price,
                    score, regime.value,
                )
                await self.bus.publish(signal)

            except Exception:
                logger.exception("Strategy %s error", strategy.name)

    def _score_signal(
        self,
        signal: SignalEvent,
        regime: RegimeType,
        regime_snapshot=None,
    ) -> float:
        """
        Compute composite signal score (0-100).

        Components:
          1. Momentum score (40%): based on raw price change magnitude
          2. Regime alignment (25%): how well signal aligns with current regime
          3. Signal strength (15%): the strategy's own strength metric
          4. Trend alignment (20%): is the signal in the direction of the trend?
        """
        cfg = self.score_cfg
        raw = signal.strength  # 0-1 from strategy

        # 1. Momentum score (0-100)
        momentum_score = raw * 100

        # 2. Regime alignment score (0-100)
        regime_score = 50.0  # neutral baseline
        if regime == RegimeType.TREND:
            regime_score = 85.0  # momentum signals are great in trends
        elif regime == RegimeType.RANGE:
            regime_score = 35.0 + cfg.range_regime_penalty  # penalized in range
        elif regime == RegimeType.VOLATILE:
            regime_score = 20.0 + cfg.volatile_regime_penalty  # heavily penalized

        # Boost regime score with confidence if available
        if regime_snapshot:
            regime_score *= (0.7 + 0.3 * regime_snapshot.regime_confidence)

        # 3. Strength score (0-100)
        strength_score = raw * 100

        # 4. Trend alignment (0-100)
        trend_score = 50.0
        if regime_snapshot:
            trend_score = regime_snapshot.trend_strength * 100
            # If signal direction aligns with trend direction, boost
            sym = signal.symbol
            if len(self._history.get(sym, [])) >= 10:
                prices = np.array([t.price for t in list(self._history[sym])[-10:]])
                trend_dir = 1 if prices[-1] > prices[0] else -1
                signal_dir = 1 if signal.side == Side.BUY else -1
                if trend_dir == signal_dir:
                    trend_score = min(trend_score + 20, 100)
                else:
                    trend_score = max(trend_score - 15, 0)

        # Composite weighted score
        composite = (
            momentum_score * cfg.momentum_weight +
            regime_score * cfg.regime_weight +
            strength_score * cfg.strength_weight +
            trend_score * cfg.trend_weight
        )

        return round(min(max(composite, 0), cfg.max_score), 1)

    def _regime_filter(
        self, signal: SignalEvent, regime: RegimeType, score: float
    ) -> tuple[bool, str]:
        """
        Filter signals based on regime. Returns (pass, reason).
        """
        # VOLATILE: only allow very high-confidence signals
        if regime == RegimeType.VOLATILE and score < 40:
            return False, f"VOLATILE regime requires score >= 75 (got {score})"

        # RANGE: momentum signals need higher threshold
        if regime == RegimeType.RANGE and score < self.score_cfg.min_score_to_emit + 0:
            return False, f"RANGE regime requires score >= {self.score_cfg.min_score_to_emit + 0} (got {score})"

        # Below minimum score threshold
        if score < self.score_cfg.min_score_to_emit - 20:
            return False, f"Score {score} below minimum {self.score_cfg.min_score_to_emit}"

        # Anti-churn: consecutive same-side signals with declining scores
        sym = signal.symbol
        if self._consecutive_same_side.get(sym, 0) >= 2:
            recent = self._recent_scores.get(sym, deque())
            if len(recent) >= 2:
                avg_recent = sum(list(recent)[-3:]) / len(list(recent)[-3:])
                if score < avg_recent * 0.8:
                    return False, f"Signal quality declining (current={score}, avg={avg_recent:.1f})"

        return True, "OK"

    def _track_regime(self, symbol: str, regime: RegimeType) -> None:
        """Track regime transitions for strategy memory."""
        if symbol not in self._last_regime:
            self._last_regime[symbol] = regime
            return

        if regime != self._last_regime[symbol]:
            self._regime_transition_count[symbol] = self._regime_transition_count.get(symbol, 0) + 1
            logger.info(
                "Regime transition %s: %s → %s (total transitions: %d)",
                symbol, self._last_regime[symbol].value, regime.value,
                self._regime_transition_count[symbol],
            )
            # Reset consecutive same-side on regime change
            self._consecutive_same_side[symbol] = 0
            self._last_regime[symbol] = regime

    def _update_signal_memory(self, symbol: str, side: Side) -> None:
        """Update strategy state memory with latest signal info."""
        if self._last_signal_side.get(symbol) == side:
            self._consecutive_same_side[symbol] += 1
        else:
            self._consecutive_same_side[symbol] = 1
        self._last_signal_side[symbol] = side

    def _update_stress_level(self) -> None:
        """
        Compute equity stress level (0-1).

        Stress increases with:
          - Drawdown depth (primary driver)
          - Consecutive losses (secondary)

        At stress > 0.8: ALL signals are hard-blocked (circuit breaker for signals).
        At stress > 0.5: signal score requirements increase proportionally.
        """
        if not self._execution:
            return

        equity = self._execution.equity

        # Track initial and peak equity
        if self._initial_equity == 0:
            self._initial_equity = equity
        if equity > self._peak_equity:
            self._peak_equity = equity

        # Drawdown component (0-1)
        if self._peak_equity > 0:
            drawdown = (self._peak_equity - equity) / self._peak_equity
            # Map: 0% DD → 0 stress, 5% DD → 0.5 stress, 10% DD → 1.0 stress
            dd_stress = min(drawdown / 0.10, 1.0)
        else:
            dd_stress = 0.0

        # Consecutive loss component (0-0.4)
        loss_stress = min(self._consecutive_losses * 0.15, 0.4)

        # Combined stress (drawdown dominates)
        self._stress_level = min(dd_stress * 0.7 + loss_stress, 1.0)

    def record_trade_result(self, pnl: float) -> None:
        """Record trade outcome for equity stress tracking. Called by orchestrator after fills."""
        if pnl > 0:
            self._consecutive_losses = 0
        elif pnl < 0:
            self._consecutive_losses += 1

    def _apply_stress_adjustment(self, score: float) -> float:
        """
        Adjust signal score based on equity stress level.

        At 0% stress: score unchanged (signal needs 60 to pass)
        At 30% stress: score reduced by 15% (signal needs ~70 to pass)
        At 50% stress: score reduced by 30% (signal needs ~85 to pass)
        At 80%+ stress: ALL signals blocked (handled by hard gate above)
        """
        if self._stress_level <= 0.1:
            return score  # no adjustment when healthy

        # Reduce score proportionally to stress
        penalty = self._stress_level * 0.5  # at 50% stress, reduce score by 25%
        adjusted = score * (1.0 - penalty)
        return round(adjusted, 1)

    @property
    def stats(self) -> dict:
        return {
            "tick_count": self._tick_count,
            "signal_count": self._signal_count,
            "signals_suppressed": self._signals_suppressed,
            "signals_stress_blocked": self._signals_stress_blocked,
            "stress_level": round(self._stress_level, 3),
            "stress_pct": f"{self._stress_level * 100:.1f}%",
            "consecutive_losses": self._consecutive_losses,
            "strategies": self.enabled,
            "regime_transitions": dict(self._regime_transition_count),
            "current_regimes": {s: r.value for s, r in self._last_regime.items()},
            "avg_recent_scores": {
                s: round(sum(v) / len(v), 1) if v else 0
                for s, v in self._recent_scores.items()
            } if hasattr(self, '_recent_scores') else {},
        }
