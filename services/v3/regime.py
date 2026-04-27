"""
v3.2 Regime Classifier
=======================
Classifies market into TREND / RANGE / VOLATILE using:
  - ATR (Average True Range) for volatility measurement
  - ADX-like trend strength from directional movement
  - Price action analysis (higher highs / lower lows)
  - Volatility percentile against rolling history

The regime classifier runs on every tick and provides context
to both the strategy (for signal quality) and risk gate (for sizing).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np

from core.v3.models import BaseEvent, EventType, RegimeType, RegimeSnapshot, TickEvent

logger = logging.getLogger(__name__)


@dataclass
class RegimeConfig:
    """Tuning parameters for regime classification."""
    atr_period: int = 14                  # ATR lookback window
    trend_lookback: int = 30              # bars for trend direction
    trend_strength_period: int = 20       # bars for ADX-like calculation
    volatility_history: int = 100         # rolling window for vol percentile
    trend_adx_threshold: float = 0.35     # ADX > this = TREND
    volatile_percentile: float = 0.80     # vol percentile > this = VOLATILE
    min_bars: int = 20                    # minimum bars before classifying
    regime_stability_bars: int = 10        # consecutive bars needed to confirm change


class RegimeClassifier:
    """
    Real-time market regime classifier.

    Produces RegimeType (TREND / RANGE / VOLATILE) with confidence scores
    and supporting metrics (ATR, trend strength, volatility percentile).
    """

    def __init__(self, config: RegimeConfig | None = None) -> None:
        self.cfg = config or RegimeConfig()

        # Per-symbol state
        self._bars: dict[str, deque] = {}           # price bars (close, high, low)
        self._atr_values: dict[str, deque] = {}      # rolling ATR history
        self._current_regime: dict[str, RegimeType] = {}
        self._prev_regime: dict[str, RegimeType] = {}
        self._consecutive: dict[str, int] = {}       # consecutive bars in same regime
        self._pending_regime: dict[str, RegimeType] = {}  # unconfirmed regime change
        self._pending_count: dict[str, int] = {}

        # Stats
        self._classification_count = 0
        self._regime_changes = 0

    def classify_tick(self, tick: TickEvent) -> Optional[RegimeSnapshot]:
        """
        Process a tick and return current regime classification.
        Returns None if not enough data yet.
        """
        sym = tick.symbol

        # Initialize per-symbol state
        if sym not in self._bars:
            self._bars[sym] = deque(maxlen=200)
            self._atr_values[sym] = deque(maxlen=self.cfg.volatility_history)
            self._current_regime[sym] = RegimeType.RANGE
            self._prev_regime[sym] = RegimeType.RANGE
            self._consecutive[sym] = 0
            self._pending_regime[sym] = RegimeType.RANGE
            self._pending_count[sym] = 0

        # Store bar data (using tick as 1-tick bar)
        self._bars[sym].append({
            "close": tick.price,
            "high": tick.price,
            "low": tick.price,
            "timestamp": tick.timestamp,
        })

        bars = self._bars[sym]
        if len(bars) < self.cfg.min_bars:
            return None

        self._classification_count += 1

        # Compute metrics
        atr, atr_pct = self._compute_atr(sym, bars)
        trend_strength = self._compute_trend_strength(bars)
        vol_percentile = self._compute_vol_percentile(sym, atr)

        # Classify
        raw_regime = self._classify(atr_pct, trend_strength, vol_percentile)

        # Regime stability filter: require N consecutive bars before confirming change
        confirmed_regime = self._apply_stability(sym, raw_regime)

        # Track changes
        if confirmed_regime != self._current_regime[sym]:
            self._prev_regime[sym] = self._current_regime[sym]
            self._current_regime[sym] = confirmed_regime
            self._regime_changes += 1
            logger.info(
                "REGIME CHANGE %s: %s → %s (atr_pct=%.4f, trend=%.3f, vol_pct=%.3f)",
                sym, self._prev_regime[sym].value, confirmed_regime.value,
                atr_pct, trend_strength, vol_percentile,
            )

        return RegimeSnapshot(
            symbol=sym,
            regime=confirmed_regime,
            atr=atr,
            atr_pct=atr_pct,
            trend_strength=round(trend_strength, 4),
            volatility_percentile=round(vol_percentile, 4),
            regime_confidence=self._compute_confidence(trend_strength, vol_percentile),
            prev_regime=self._prev_regime[sym],
            consecutive_regime_bars=self._consecutive[sym],
        )

    def _compute_atr(self, sym: str, bars: deque) -> tuple[float, float]:
        """Compute Average True Range and ATR as percentage."""
        if len(bars) < 2:
            return 0.0, 0.0

        period = min(self.cfg.atr_period, len(bars) - 1)
        tr_values = []

        for i in range(1, min(period + 1, len(bars))):
            high = bars[-i]["high"]
            low = bars[-i]["low"]
            prev_close = bars[-i - 1]["close"]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_values.append(tr)

        if not tr_values:
            return 0.0, 0.0

        atr = float(np.mean(tr_values))
        current_price = bars[-1]["close"]
        atr_pct = atr / current_price if current_price > 0 else 0.0

        # Store for percentile
        self._atr_values[sym].append(atr_pct)

        return atr, atr_pct

    def _compute_trend_strength(self, bars: deque) -> float:
        """
        ADX-like trend strength: measures how directional price movement is.
        Returns 0.0 (no trend) to 1.0 (strong trend).
        """
        n = min(self.cfg.trend_strength_period, len(bars) - 1)
        if n < 5:
            return 0.0

        prices = np.array([bars[-i]["close"] for i in range(n + 1)][::-1])

        # +DM / -DM (Directional Movement)
        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)
        tr = np.zeros(n)

        for i in range(1, n + 1):
            high_diff = prices[i] - prices[i - 1]
            low_diff = prices[i - 1] - prices[i]
            plus_dm[i - 1] = max(high_diff, 0) if high_diff > low_diff and high_diff > 0 else 0
            minus_dm[i - 1] = max(low_diff, 0) if low_diff > high_diff and low_diff > 0 else 0
            tr[i - 1] = max(
                prices[i] - prices[i - 1],
                abs(prices[i] - prices[i - 1]),
                0.001,  # floor to avoid div-by-zero
            )

        # Smoothed averages
        atr_sum = np.sum(tr)
        if atr_sum == 0:
            return 0.0

        plus_di = np.sum(plus_dm) / atr_sum
        minus_di = np.sum(minus_dm) / atr_sum

        # DX = |+DI - (-DI)| / (+DI + (-DI))
        di_sum = plus_di + minus_di
        dx = abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0

        return float(min(dx, 1.0))

    def _compute_vol_percentile(self, sym: str, current_atr_pct: float) -> float:
        """Where current ATR% sits vs historical values (0-1).

        Uses relative z-score approach instead of raw percentile to avoid
        the 'always 1.0' problem when all values are similar.
        """
        history = self._atr_values[sym]
        if len(history) < 20:
            return 0.5  # neutral when insufficient history

        arr = np.array(history)
        mean_val = np.mean(arr)
        std_val = np.std(arr)

        if std_val < 1e-8:
            # All values are identical — neutral
            return 0.5

        # Z-score based percentile approximation
        z = (current_atr_pct - mean_val) / std_val
        # Map z-score to 0-1 range using sigmoid-like transform
        percentile = 1.0 / (1.0 + np.exp(-z * 1.5))
        return float(min(max(percentile, 0.0), 1.0))

    def _classify(
        self, atr_pct: float, trend_strength: float, vol_percentile: float
    ) -> RegimeType:
        """Core classification logic."""
        # VOLATILE takes priority: extreme volatility overrides trend detection
        # Require both high vol_percentile AND elevated absolute ATR
        if vol_percentile > self.cfg.volatile_percentile and atr_pct > 0.0003:
            return RegimeType.VOLATILE

        # TREND: strong directional movement (ATR threshold is low to avoid
        # blocking trends in low-volatility environments)
        if trend_strength > self.cfg.trend_adx_threshold:
            return RegimeType.TREND

        # Default: RANGE
        return RegimeType.RANGE

    def _apply_stability(self, sym: str, raw_regime: RegimeType) -> RegimeType:
        """Require N consecutive bars of same regime before confirming change."""
        current = self._current_regime[sym]

        if raw_regime == current:
            # Still in same regime, reset pending
            self._consecutive[sym] += 1
            self._pending_regime[sym] = raw_regime
            self._pending_count[sym] = 0
            return current

        # Different regime detected
        if raw_regime == self._pending_regime[sym]:
            self._pending_count[sym] += 1
        else:
            # New direction, reset pending
            self._pending_regime[sym] = raw_regime
            self._pending_count[sym] = 1

        # Confirm change if enough consecutive bars
        if self._pending_count[sym] >= self.cfg.regime_stability_bars:
            return raw_regime

        return current

    def _compute_confidence(self, trend_strength: float, vol_percentile: float) -> float:
        """How confident we are in the current classification (0-1)."""
        # High confidence when metrics are far from thresholds
        trend_dist = abs(trend_strength - self.cfg.trend_adx_threshold)
        vol_dist = abs(vol_percentile - self.cfg.volatile_percentile)
        return float(min(max((trend_dist + vol_dist) * 2, 0.1), 1.0))

    def get_regime(self, symbol: str) -> RegimeType:
        """Get current regime for a symbol (default RANGE if unknown)."""
        return self._current_regime.get(symbol, RegimeType.RANGE)

    @property
    def stats(self) -> dict:
        return {
            "classifications": self._classification_count,
            "regime_changes": self._regime_changes,
            "current_regimes": {s: r.value for s, r in self._current_regime.items()},
        }
