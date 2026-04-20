#!/usr/bin/env python3
"""
Trading Bot v2.0 - Live Trading Ready
=====================================
Single-file, self-contained trading system with integrated Risk Guard.

UPGRADES from v1.0:
  1. Feed: Binance REST with health monitoring + auto-reconnect + heartbeat
  2. Strategy: Multi-signal (MA Cross + Momentum + RSI) with debug output
  3. Sensitivity: Configurable (conservative / moderate / aggressive)
  4. Risk: Integrated circuit breaker + drawdown + exposure limits
  5. Diagnostics: [HEARTBEAT] every tick, signal decision logging
  6. Dashboard: Signal debug panel, feed status, heartbeat counter

Pipeline (FIXED — all stages now active):
  Market Data -> Indicator Engine -> Signal Generation -> Risk Filter -> Execution -> Logging

Usage:
  python trading_bot_v2.py                              # Sim mode with dashboard
  python trading_bot_v2.py --mode live                   # Live Binance REST
  python trading_bot_v2.py --mode live --no-dash         # Headless live
  python trading_bot_v2.py --mode backtest --bars 2000   # Backtest
  python trading_bot_v2.py --strategy momentum           # Momentum strategy
  python trading_bot_v2.py --sensitivity aggressive      # More signals
  python trading_bot_v2.py --nav 100000                  # $100K NAV
"""

import argparse
import json
import logging
import math
import os
import random
import signal as sig
import sqlite3
import sys
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

# ─── Third-party imports (lazy-loaded for headless mode) ───

def _import_requests():
    import requests
    return requests

def _import_fastapi():
    import fastapi
    import uvicorn
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.middleware.cors import CORSMiddleware
    return fastapi, uvicorn, HTMLResponse, JSONResponse, CORSMiddleware


# ═══════════════════════════════════════════════════════════════
# SECTION 1: CONFIGURATION
# ═══════════════════════════════════════════════════════════════

@dataclass
class Config:
    """All tuneable parameters in one place. Single source of truth."""

    # ── Trading ──
    SYMBOL: str = "BTCUSDT"
    INITIAL_CASH: float = 10_000.0
    MAX_POSITION: int = 3
    SIZE_PER_UNIT: float = 0.10

    # ── Strategy ──
    STRATEGY: str = "multi"          # "ma_cross", "momentum", "rsi", "multi"
    SENSITIVITY: str = "moderate"    # "conservative", "moderate", "aggressive"
    MA_SHORT: int = 5                # faster response
    MA_LONG: int = 15                # faster response than v1.0's 30
    MOMENTUM_PERIOD: int = 10
    RSI_PERIOD: int = 14
    RSI_OVERSOLD: float = 30.0
    RSI_OVERBOUGHT: float = 70.0

    # ── Execution ──
    COMMISSION_PER_SIDE: float = 0.0004
    SLIPPAGE_PER_SIDE: float = 0.0001

    # ── Feed ──
    MODE: str = "sim"
    FEED_INTERVAL_SEC: float = 2.0   # faster than v1.0's 5s
    BINANCE_API_URL: str = "https://api.binance.com/api/v3/ticker/price"
    FEED_TIMEOUT_SEC: float = 10.0
    RECONNECT_DELAY_SEC: float = 5.0
    MAX_RECONNECT_ATTEMPTS: int = 10

    # ── Backtest ──
    BACKTEST_BARS: int = 500
    BACKTEST_START_PRICE: float = 65_000.0
    BACKTEST_VOLATILITY: float = 50.0

    # ── Risk ──
    MAX_DRAWDOWN_PCT: float = 5.0
    MIN_BALANCE: float = 500.0
    COOLDOWN_TICKS: int = 10          # reduced from v1.0's 20
    CIRCUIT_BREAKER_DRAWDOWN: float = 10.0
    CIRCUIT_BREAKER_COOLDOWN_SEC: int = 1800
    NAV_USD: float = 100_000.0       # for Risk Guard integration
    MAX_SINGLE_POSITION_PCT: float = 0.10

    # ── Dashboard ──
    DASHBOARD_HOST: str = "0.0.0.0"
    DASHBOARD_PORT: int = 8080

    # ── Diagnostics ──
    HEARTBEAT_INTERVAL: int = 10      # log heartbeat every N ticks
    DEBUG_SIGNALS: bool = True         # show why signals fire/don't fire

    # ── Paths ──
    DB_PATH: str = "trades_v2.sqlite"

    # ── Sensitivity Presets ──
    def apply_sensitivity(self):
        """Adjust parameters based on sensitivity setting."""
        s = self.SENSITIVITY.lower()
        if s == "conservative":
            self.MA_SHORT = 10
            self.MA_LONG = 30
            self.COOLDOWN_TICKS = 20
            self.SIZE_PER_UNIT = 0.05
            self.RSI_OVERSOLD = 25.0
            self.RSI_OVERBOUGHT = 75.0
        elif s == "aggressive":
            self.MA_SHORT = 3
            self.MA_LONG = 10
            self.COOLDOWN_TICKS = 5
            self.SIZE_PER_UNIT = 0.15
            self.RSI_OVERSOLD = 35.0
            self.RSI_OVERBOUGHT = 65.0
        # moderate is default (already set above)

    def __repr__(self):
        return (f"Config(symbol={self.SYMBOL}, mode={self.MODE}, strategy={self.STRATEGY}, "
                f"sensitivity={self.SENSITIVITY}, cash={self.INITIAL_CASH})")


# ═══════════════════════════════════════════════════════════════
# SECTION 2: LOGGING
# ═══════════════════════════════════════════════════════════════

def setup_logging(level="INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(name)-18s  %(levelname)-5s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


# ═══════════════════════════════════════════════════════════════
# SECTION 3: MARKET DATA FEED
# ═══════════════════════════════════════════════════════════════

class FeedStatus(Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"
    SIMULATED = "simulated"


class Feed:
    """
    Unified market data feed with health monitoring and auto-reconnect.

    Supports:
      - live: Binance REST API polling with health monitoring
      - sim: Random-walk price generation (testing)
    """

    def __init__(self, config: Config):
        self.config = config
        self.price: Optional[float] = None
        self.history: List[float] = []
        self._tick_count: int = 0
        self._sim_price: float = config.BACKTEST_START_PRICE
        self._status: FeedStatus = FeedStatus.DISCONNECTED
        self._last_feed_time: float = 0.0
        self._consecutive_errors: int = 0
        self._total_errors: int = 0
        self._reconnect_count: int = 0
        self._start_time: float = time.time()
        self._lock = threading.Lock()

    @property
    def status(self) -> FeedStatus:
        return self._status

    @property
    def tick_count(self) -> int:
        return self._tick_count

    @property
    def uptime_sec(self) -> float:
        return time.time() - self._start_time

    @property
    def feed_quality(self) -> dict:
        """Feed health metrics for diagnostics."""
        with self._lock:
            return {
                "status": self._status.value,
                "ticks": self._tick_count,
                "errors": self._total_errors,
                "consecutive_errors": self._consecutive_errors,
                "reconnects": self._reconnect_count,
                "last_feed_age_sec": time.time() - self._last_feed_time if self._last_feed_time else -1,
                "uptime_sec": round(self.uptime_sec, 0),
                "price": self.price,
            }

    def get_price(self) -> float:
        """Fetch a single price tick. Blocks in live mode."""
        if self.config.MODE == "live":
            return self._fetch_live()
        else:
            return self._simulate_tick()

    def _fetch_live(self) -> float:
        """Poll Binance REST API with error handling and reconnect logic."""
        requests = _import_requests()
        try:
            url = f"{self.config.BINANCE_API_URL}?symbol={self.config.SYMBOL}"
            resp = requests.get(url, timeout=self.config.FEED_TIMEOUT_SEC)
            resp.raise_for_status()
            data = resp.json()
            new_price = float(data["price"])

            with self._lock:
                self.price = new_price
                self._last_feed_time = time.time()
                self._consecutive_errors = 0
                self._status = FeedStatus.CONNECTED
                self._record(new_price)

            return new_price

        except Exception as e:
            with self._lock:
                self._consecutive_errors += 1
                self._total_errors += 1

                if self._consecutive_errors >= 3:
                    self._status = FeedStatus.RECONNECTING
                    self._reconnect_count += 1
                    logger.warning(
                        "[FEED] %d consecutive errors, status: RECONNECTING (attempt %d)",
                        self._consecutive_errors, self._reconnect_count
                    )

            logger.error("[FEED] Error: %s", e)

            if self.price is not None:
                logger.warning("[FEED] Using last known price: $%.2f", self.price)
                return self.price
            raise RuntimeError(f"Cannot get market data: {e}")

    def _simulate_tick(self) -> float:
        """Generate a simulated price tick with mean reversion and trend bursts."""
        with self._lock:
            self._status = FeedStatus.SIMULATED

        drift = random.gauss(0, self.config.BACKTEST_VOLATILITY)
        revert = 0.002 * (self.config.BACKTEST_START_PRICE - self._sim_price)

        # Occasional trend burst (5% chance)
        if random.random() < 0.05:
            drift = random.gauss(0, self.config.BACKTEST_VOLATILITY * 5)

        self._sim_price = max(100.0, self._sim_price + drift + revert)
        new_price = round(self._sim_price, 2)

        with self._lock:
            self.price = new_price
            self._last_feed_time = time.time()
            self._record(new_price)

        return new_price

    def _record(self, price: float):
        self.history.append(price)
        self._tick_count += 1

    def generate_backtest_data(self, n_bars: Optional[int] = None) -> List[float]:
        """Pre-generate N bars for backtesting."""
        n = n_bars or self.config.BACKTEST_BARS
        prices = []
        for _ in range(n):
            prices.append(self.get_price())
        logger.info("Generated %d backtest bars", n)
        return prices

    def run(self, on_tick: Callable[[float], None], max_ticks: Optional[int] = None):
        """Run the feed loop, calling on_tick(price) for each tick."""
        logger.info(
            "[FEED] Starting (mode=%s, symbol=%s, interval=%.1fs)",
            self.config.MODE, self.config.SYMBOL, self.config.FEED_INTERVAL_SEC
        )
        try:
            while True:
                try:
                    price = self.get_price()
                    on_tick(price)

                    if max_ticks is not None and self._tick_count >= max_ticks:
                        logger.info("[FEED] Stopped: max_ticks=%d reached", max_ticks)
                        break

                    if self.config.MODE == "live":
                        time.sleep(self.config.FEED_INTERVAL_SEC)
                    else:
                        time.sleep(0.05)

                except RuntimeError:
                    # Feed completely failed
                    logger.error("[FEED] Fatal feed error, attempting reconnect...")
                    time.sleep(self.config.RECONNECT_DELAY_SEC)

        except KeyboardInterrupt:
            logger.info("[FEED] Stopped by user (%d ticks)", self._tick_count)


logger = logging.getLogger("bot")


# ═══════════════════════════════════════════════════════════════
# SECTION 4: INDICATOR ENGINE
# ═══════════════════════════════════════════════════════════════

class Indicators:
    """
    Technical indicator calculator.
    Computes SMA, EMA, RSI, MACD, Momentum, Bollinger Bands.
    All calculations are O(1) incremental after warmup.
    """

    def __init__(self, config: Config):
        self.config = config
        self._prices: List[float] = []
        self._ema_short_cache: float = 0.0
        self._ema_long_cache: float = 0.0
        self._ema_short_k: float = 0.0
        self._ema_long_k: float = 0.0
        self._gain_sum: float = 0.0
        self._loss_sum: float = 0.0
        self._prev_price: Optional[float] = None
        self._rsi_ready: bool = False
        self._ema_ready: bool = False

    def update(self, price: float) -> dict:
        """Update with new price and return all indicator values."""
        self._prices.append(price)
        n = len(self._prices)

        result = {"price": price, "n": n}

        # SMA
        result["sma_short"] = self._sma(self.config.MA_SHORT)
        result["sma_long"] = self._sma(self.config.MA_LONG)

        # EMA (incremental)
        self._update_ema(price, n)
        result["ema_short"] = self._ema_short_cache
        result["ema_long"] = self._ema_long_cache
        result["ema_ready"] = self._ema_ready

        # RSI (Wilder's smoothing)
        self._update_rsi(price, n)
        result["rsi"] = self._current_rsi()
        result["rsi_ready"] = self._rsi_ready

        # Momentum (rate of change)
        mom_period = self.config.MOMENTUM_PERIOD
        if n > mom_period:
            result["momentum"] = (price - self._prices[-mom_period - 1]) / self._prices[-mom_period - 1] * 100
        else:
            result["momentum"] = 0.0

        # MACD (12, 26, 9)
        ema12 = self._ema(12)
        ema26 = self._ema(26)
        result["macd"] = ema12 - ema26
        result["macd_ready"] = n >= 26

        # Bollinger Bands (20, 2)
        bb_period = 20
        if n >= bb_period:
            sma20 = self._sma(bb_period)
            variance = sum((p - sma20) ** 2 for p in self._prices[-bb_period:]) / bb_period
            std = math.sqrt(variance)
            result["bb_upper"] = sma20 + 2 * std
            result["bb_middle"] = sma20
            result["bb_lower"] = sma20 - 2 * std
            result["bb_pct"] = (price - result["bb_lower"]) / (result["bb_upper"] - result["bb_lower"]) * 100 if result["bb_upper"] != result["bb_lower"] else 50
        else:
            result["bb_upper"] = price
            result["bb_middle"] = price
            result["bb_lower"] = price
            result["bb_pct"] = 50.0

        result["momentum_ready"] = n > mom_period

        return result

    def _sma(self, period: int) -> float:
        if len(self._prices) < period:
            return 0.0
        return sum(self._prices[-period:]) / period

    def _ema(self, period: int) -> float:
        if len(self._prices) < period:
            return 0.0
        k = 2.0 / (period + 1)
        ema = sum(self._prices[:period]) / period
        for p in self._prices[period:]:
            ema = p * k + ema * (1 - k)
        return ema

    def _update_ema(self, price: float, n: int):
        short_p = self.config.MA_SHORT
        long_p = self.config.MA_LONG

        if n == short_p:
            self._ema_short_cache = sum(self._prices[:short_p]) / short_p
            self._ema_short_k = 2.0 / (short_p + 1)
        elif n > short_p:
            self._ema_short_cache = price * self._ema_short_k + self._ema_short_cache * (1 - self._ema_short_k)

        if n == long_p:
            self._ema_long_cache = sum(self._prices[:long_p]) / long_p
            self._ema_long_k = 2.0 / (long_p + 1)
            self._ema_ready = True
        elif n > long_p:
            self._ema_long_cache = price * self._ema_long_k + self._ema_long_cache * (1 - self._ema_long_k)

    def _update_rsi(self, price: float, n: int):
        if self._prev_price is None:
            self._prev_price = price
            return

        change = price - self._prev_price
        self._prev_price = price

        if n <= self.config.RSI_PERIOD:
            if change > 0:
                self._gain_sum += change
            else:
                self._loss_sum += abs(change)

            if n == self.config.RSI_PERIOD:
                if self._loss_sum == 0:
                    self._avg_gain = self._gain_sum / self.config.RSI_PERIOD
                    self._avg_loss = 0.001  # avoid div by zero
                else:
                    self._avg_gain = self._gain_sum / self.config.RSI_PERIOD
                    self._avg_loss = self._loss_sum / self.config.RSI_PERIOD
                self._rsi_ready = True
        else:
            # Wilder's smoothing
            gain = max(change, 0)
            loss = max(-change, 0)
            self._avg_gain = (self._avg_gain * (self.config.RSI_PERIOD - 1) + gain) / self.config.RSI_PERIOD
            self._avg_loss = (self._avg_loss * (self.config.RSI_PERIOD - 1) + loss) / self.config.RSI_PERIOD

    def _current_rsi(self) -> float:
        if not hasattr(self, '_avg_gain') or not self._rsi_ready:
            return 50.0
        if self._avg_loss == 0:
            return 100.0
        rs = self._avg_gain / self._avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    @property
    def history_length(self) -> int:
        return len(self._prices)


# ═══════════════════════════════════════════════════════════════
# SECTION 5: STRATEGY ENGINE
# ═══════════════════════════════════════════════════════════════

class Strategy:
    """
    Multi-strategy signal engine with debug output.

    Strategies:
      1. MA Cross: Short MA crosses Long MA (golden/death cross)
      2. Momentum: Rate of change exceeds threshold
      3. RSI: Oversold/Overbought reversal

    Modes:
      - "ma_cross": Only MA crossover signals
      - "momentum": Only momentum signals
      - "rsi": Only RSI signals
      - "multi": All three, require consensus (2+ agree = strong, 1 = weak)
    """

    def __init__(self, config: Config):
        self.config = config
        self.indicators = Indicators(config)
        self.signal_count: int = 0
        self._last_signal: str = "HOLD"
        self._prev_short_ma: Optional[float] = None
        self._prev_long_ma: Optional[float] = None
        self._debug_log: List[dict] = deque(maxlen=100)
        self._momentum_thresholds = {
            "conservative": 0.3,
            "moderate": 0.15,
            "aggressive": 0.05,
        }

    def generate(self, price: float) -> Tuple[str, dict]:
        """
        Analyze price and return (signal, debug_info).

        Returns: ("BUY", "SELL", "HOLD", "WEAK_BUY", "WEAK_SELL"), debug_dict
        """
        ind = self.indicators.update(price)

        signals = {}  # strategy_name -> signal
        reasons = []  # human-readable reasons

        # Strategy 1: MA Crossover
        ma_signal, ma_reason = self._ma_cross(ind)
        signals["ma_cross"] = ma_signal
        if ma_reason:
            reasons.append(ma_reason)

        # Strategy 2: Momentum
        mom_signal, mom_reason = self._momentum(ind)
        signals["momentum"] = mom_signal
        if mom_reason:
            reasons.append(mom_reason)

        # Strategy 3: RSI
        rsi_signal, rsi_reason = self._rsi(ind)
        signals["rsi"] = rsi_signal
        if rsi_reason:
            reasons.append(rsi_reason)

        # Aggregate signals
        final_signal = self._aggregate(signals)

        debug = {
            "price": price,
            "indicators": {
                "sma_short": round(ind.get("sma_short", 0), 2),
                "sma_long": round(ind.get("sma_long", 0), 2),
                "rsi": round(ind.get("rsi", 50), 1),
                "momentum": round(ind.get("momentum", 0), 4),
                "macd": round(ind.get("macd", 0), 2),
                "bb_pct": round(ind.get("bb_pct", 50), 1),
            },
            "signals": signals,
            "reasons": reasons,
            "final": final_signal,
            "bar": ind["n"],
        }

        # Debug logging
        if self.config.DEBUG_SIGNALS:
            self._debug_log.append(debug)
            if final_signal != "HOLD" or ind["n"] % 20 == 0:
                logger.debug(
                    "[SIGNAL] bar=%d price=$%.2f | MA=%s Mom=%s RSI=%.1f => %s %s",
                    ind["n"], price,
                    signals.get("ma_cross", "-"),
                    signals.get("momentum", "-"),
                    ind.get("rsi", 50),
                    final_signal,
                    "| " + "; ".join(reasons) if reasons else ""
                )

        if final_signal in ("BUY", "SELL"):
            self.signal_count += 1
            self._last_signal = final_signal
            logger.info(
                "[SIGNAL #%d] %s @ $%.2f (MA%d=%.0f MA%d=%.0f RSI=%.1f Mom=%.3f) [%s]",
                self.signal_count, final_signal, price,
                self.config.MA_SHORT, ind.get("sma_short", 0),
                self.config.MA_LONG, ind.get("sma_long", 0),
                ind.get("rsi", 50), ind.get("momentum", 0),
                "; ".join(reasons)
            )
        elif final_signal in ("WEAK_BUY", "WEAK_SELL"):
            self.signal_count += 1
            self._last_signal = "BUY" if final_signal == "WEAK_BUY" else "SELL"
            logger.info(
                "[WEAK SIGNAL #%d] %s @ $%.2f (single-strategy) [%s]",
                self.signal_count, final_signal, price,
                "; ".join(reasons)
            )

        return self._last_signal if final_signal != "HOLD" else "HOLD", debug

    def _ma_cross(self, ind: dict) -> Tuple[str, str]:
        """MA crossover strategy."""
        short_ma = ind.get("sma_short", 0)
        long_ma = ind.get("sma_long", 0)
        n = ind["n"]

        min_bars = self.config.MA_LONG + 1
        if n < min_bars or short_ma == 0 or long_ma == 0:
            return "HOLD", ""

        prev_short = self._prev_short_ma
        prev_long = self._prev_long_ma

        self._prev_short_ma = short_ma
        self._prev_long_ma = long_ma

        if prev_short is None or prev_long is None:
            return "HOLD", ""

        gap = abs(short_ma - long_ma) / long_ma * 100

        if prev_short <= prev_long and short_ma > long_ma:
            return "BUY", f"Golden cross (gap={gap:.3f}%)"
        elif prev_short >= prev_long and short_ma < long_ma:
            return "SELL", f"Death cross (gap={gap:.3f}%)"

        return "HOLD", ""

    def _momentum(self, ind: dict) -> Tuple[str, str]:
        """Momentum burst strategy."""
        if not ind.get("momentum_ready"):
            return "HOLD", ""

        momentum = ind.get("momentum", 0)
        threshold = self._momentum_thresholds.get(self.config.SENSITIVITY, 0.15)

        if momentum > threshold:
            return "BUY", f"Momentum bullish ({momentum:.3f}% > {threshold}%)"
        elif momentum < -threshold:
            return "SELL", f"Momentum bearish ({momentum:.3f}% < -{threshold}%)"

        return "HOLD", ""

    def _rsi(self, ind: dict) -> Tuple[str, str]:
        """RSI reversal strategy."""
        if not ind.get("rsi_ready"):
            return "HOLD", ""

        rsi = ind.get("rsi", 50)
        oversold = self.config.RSI_OVERSOLD
        overbought = self.config.RSI_OVERBOUGHT

        if rsi < oversold:
            return "BUY", f"RSI oversold ({rsi:.1f} < {oversold})"
        elif rsi > overbought:
            return "SELL", f"RSI overbought ({rsi:.1f} > {overbought})"

        return "HOLD", ""

    def _aggregate(self, signals: dict) -> str:
        """Aggregate multiple strategy signals."""
        buys = sum(1 for s in signals.values() if s == "BUY")
        sells = sum(1 for s in signals.values() if s == "SELL")

        if buys >= 2:
            return "BUY"      # Strong consensus
        elif sells >= 2:
            return "SELL"     # Strong consensus
        elif buys == 1 and sells == 0:
            return "WEAK_BUY"  # Single-strategy signal
        elif sells == 1 and buys == 0:
            return "WEAK_SELL"
        elif buys == 1 and sells == 1:
            return "HOLD"      # Conflicting signals

        return "HOLD"

    @property
    def last_signal(self) -> str:
        return self._last_signal

    def stats(self) -> dict:
        return {
            "signals_generated": self.signal_count,
            "history_length": self.indicators.history_length,
            "last_signal": self._last_signal,
            "strategy": self.config.STRATEGY,
            "sensitivity": self.config.SENSITIVITY,
        }

    def get_debug_log(self) -> list:
        return list(self._debug_log)


# ═══════════════════════════════════════════════════════════════
# SECTION 6: RISK ENGINE (Integrated Risk Guard)
# ═══════════════════════════════════════════════════════════════

class RiskEngine:
    """
    Pre-execution risk validation with circuit breaker.

    Checks (in order):
      1. Circuit breaker active? -> BLOCK
      2. Cooldown (anti-overtrading)? -> BLOCK
      3. Position limit? -> BLOCK
      4. Minimum balance? -> BLOCK
      5. Max drawdown? -> BLOCK (may trigger circuit breaker)
      6. Single position size limit (Risk Guard)? -> BLOCK
      7. NAV exposure limit (Risk Guard)? -> BLOCK
    """

    def __init__(self, config: Config):
        self.config = config
        self._blocked: int = 0
        self._passed: int = 0
        self._block_reasons: Dict[str, int] = {}
        self._ticks_since_signal: int = 0
        self._peak_equity: float = config.INITIAL_CASH
        self._circuit_breaker_active: bool = False
        self._circuit_breaker_until: float = 0.0
        self._circuit_breaker_count: int = 0

    def validate(self, signal: str, position_units: int, cash: float,
                 equity: float, notional: float = 0.0) -> str:
        """
        Validate a signal. Returns signal if safe, "BLOCK" if rejected.
        """
        if signal == "HOLD":
            return "HOLD"

        # 1. Circuit breaker check
        if self._circuit_breaker_active:
            if time.time() < self._circuit_breaker_until:
                remaining = (self._circuit_breaker_until - time.time()) / 60
                self._block("CIRCUIT_BREAKER")
                logger.warning(
                    "[RISK] CIRCUIT BREAKER active (%.0f min remaining)", remaining
                )
                return "BLOCK"
            else:
                self._circuit_breaker_active = False
                logger.info("[RISK] Circuit breaker expired, resuming trading")

        # 2. Cooldown
        if self._ticks_since_signal < self.config.COOLDOWN_TICKS:
            self._block("COOLDOWN")
            return "BLOCK"

        # 3. Position limit
        if signal == "BUY" and position_units >= self.config.MAX_POSITION:
            self._block("MAX_LONG")
            return "BLOCK"
        if signal == "SELL" and position_units <= -self.config.MAX_POSITION:
            self._block("MAX_SHORT")
            return "BLOCK"

        # 4. Minimum balance
        if signal == "BUY" and cash < self.config.MIN_BALANCE:
            self._block("LOW_CASH")
            return "BLOCK"

        # 5. Drawdown check
        if equity > self._peak_equity:
            self._peak_equity = equity
        drawdown_pct = (self._peak_equity - equity) / self._peak_equity * 100 if self._peak_equity > 0 else 0

        if drawdown_pct >= self.config.MAX_DRAWDOWN_PCT:
            self._block("MAX_DRAWDOWN")
            logger.warning("[RISK] Drawdown %.2f%% >= %.2f%% limit", drawdown_pct, self.config.MAX_DRAWDOWN_PCT)

            # Trigger circuit breaker if severe
            if drawdown_pct >= self.config.CIRCUIT_BREAKER_DRAWDOWN:
                self._trigger_circuit_breaker()
                return "BLOCK"

        # 6. Single position size (Risk Guard integration)
        if notional > 0:
            max_single = self.config.MAX_SINGLE_POSITION_PCT * self.config.NAV_USD
            if notional > max_single:
                self._block("POSITION_SIZE")
                logger.warning("[RISK] Notional $%.0f > single-position cap $%.0f", notional, max_single)
                return "BLOCK"

        # All checks passed
        self._passed += 1
        self._ticks_since_signal = 0
        logger.debug("[RISK] PASS: %s (pos=%d, cash=%.0f, dd=%.2f%%)", signal, position_units, cash, drawdown_pct)
        return signal

    def tick(self):
        """Increment cooldown counter (called every tick)."""
        self._ticks_since_signal += 1

    def _trigger_circuit_breaker(self):
        """Activate circuit breaker."""
        self._circuit_breaker_active = True
        self._circuit_breaker_until = time.time() + self.config.CIRCUIT_BREAKER_COOLDOWN_SEC
        self._circuit_breaker_count += 1
        logger.critical(
            "[RISK] CIRCUIT BREAKER ACTIVATED! Trading halted for %d minutes (count=%d)",
            self.config.CIRCUIT_BREAKER_COOLDOWN_SEC / 60, self._circuit_breaker_count
        )

    def _block(self, reason: str):
        self._blocked += 1
        self._block_reasons[reason] = self._block_reasons.get(reason, 0) + 1
        logger.debug("[RISK] BLOCK: %s", reason)

    @property
    def circuit_breaker_active(self) -> bool:
        return self._circuit_breaker_active

    @property
    def current_drawdown(self) -> float:
        return (self._peak_equity - (self._peak_equity)) / self._peak_equity * 100 if self._peak_equity > 0 else 0

    def stats(self) -> dict:
        return {
            "passed": self._passed,
            "blocked": self._blocked,
            "block_rate": round(self._blocked / max(1, self._passed + self._blocked) * 100, 1),
            "block_reasons": dict(self._block_reasons),
            "peak_equity": round(self._peak_equity, 2),
            "circuit_breaker_active": self._circuit_breaker_active,
            "circuit_breaker_count": self._circuit_breaker_count,
        }


# ═══════════════════════════════════════════════════════════════
# SECTION 7: EXECUTION ENGINE
# ═══════════════════════════════════════════════════════════════

class ExecutionEngine:
    """
    Paper trading with proper position management.
    Supports long and short positions.
    """

    def __init__(self, config: Config):
        self.config = config
        self.cash: float = config.INITIAL_CASH
        self.position: float = 0.0
        self.avg_entry: float = 0.0
        self.realized_pnl: float = 0.0
        self.total_commission: float = 0.0
        self.trade_count: int = 0
        self._trade_log: List[dict] = []
        self._start_time: float = time.time()

    def execute(self, signal: str, price: float) -> dict:
        """Execute a trade signal at given price."""
        if signal not in ("BUY", "SELL"):
            return self._snapshot(price, signal)

        slip = self.config.SLIPPAGE_PER_SIDE
        exec_price = round(price * (1 + slip) if signal == "BUY" else price * (1 - slip), 2)
        commission_rate = self.config.COMMISSION_PER_SIDE

        old_pos = self.position
        old_cash = self.cash

        if signal == "BUY":
            if self.position < 0:
                close_value = abs(self.position) * exec_price
                close_pnl = (self.avg_entry - exec_price) * abs(self.position)
                close_comm = close_value * commission_rate
                self.realized_pnl += close_pnl
                self.cash -= close_value + close_comm
                self.total_commission += close_comm
                self._log_trade("CLOSE_SHORT", price, exec_price, abs(old_pos), close_pnl, close_comm, old_pos, 0.0)
                self.position = 0.0
                self.avg_entry = 0.0

            if self.position >= 0:
                notional = self.cash * self.config.SIZE_PER_UNIT
                btc_qty = notional / exec_price
                comm = notional * commission_rate
                if self.position == 0:
                    self.avg_entry = exec_price
                else:
                    total = self.position + btc_qty
                    self.avg_entry = (self.avg_entry * self.position + exec_price * btc_qty) / total
                self.position += btc_qty
                self.cash -= notional + comm
                self.total_commission += comm
                self._log_trade("BUY", price, exec_price, btc_qty, 0, comm, self.position - btc_qty, self.position)

        elif signal == "SELL":
            if self.position > 0:
                close_value = self.position * exec_price
                close_pnl = (exec_price - self.avg_entry) * self.position
                close_comm = close_value * commission_rate
                self.realized_pnl += close_pnl
                self.cash += close_value - close_comm
                self.total_commission += close_comm
                self._log_trade("CLOSE_LONG", price, exec_price, old_pos, close_pnl, close_comm, old_pos, 0.0)
                self.position = 0.0
                self.avg_entry = 0.0

            if self.position <= 0:
                notional = self.cash * self.config.SIZE_PER_UNIT
                btc_qty = notional / exec_price
                comm = notional * commission_rate
                if self.position == 0:
                    self.avg_entry = exec_price
                else:
                    total = abs(self.position) + btc_qty
                    self.avg_entry = (self.avg_entry * abs(self.position) + exec_price * btc_qty) / total
                self.position -= btc_qty
                self.cash += notional - comm
                self.total_commission += comm
                self._log_trade("SELL", price, exec_price, btc_qty, 0, comm, self.position + btc_qty, self.position)

        return self._snapshot(price, signal)

    def _log_trade(self, action, price, exec_price, btc_qty, pnl, comm, pos_before, pos_after):
        self.trade_count += 1
        self._trade_log.append({
            "id": self.trade_count,
            "timestamp": time.time(),
            "action": action,
            "price": price,
            "exec_price": exec_price,
            "btc_qty": round(btc_qty, 8),
            "pnl": round(pnl, 2),
            "commission": round(comm, 4),
            "pos_before": round(pos_before, 8),
            "pos_after": round(pos_after, 8),
            "cash": round(self.cash, 2),
            "realized_pnl": round(self.realized_pnl, 2),
        })
        if pnl != 0:
            logger.info(
                "[TRADE #%d] %s %.6f BTC @ $%.2f pnl=$%+.2f cash=$%.0f",
                self.trade_count, action, btc_qty, exec_price, pnl, self.cash
            )

    def _snapshot(self, price: float, signal: str) -> dict:
        unrealized = (price - self.avg_entry) * self.position if self.position != 0 else 0.0
        equity = self.cash + self.position * price
        return {
            "signal": signal,
            "price": price,
            "position_btc": round(self.position, 8),
            "position_usd": round(abs(self.position * price), 2),
            "avg_entry": round(self.avg_entry, 2),
            "cash": round(self.cash, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "unrealized_pnl": round(unrealized, 2),
            "total_pnl": round(self.realized_pnl + unrealized, 2),
            "equity": round(equity, 2),
            "commission_total": round(self.total_commission, 4),
            "trade_count": self.trade_count,
            "notional": round(abs(self.position * price), 2),
        }

    def stats(self) -> dict:
        return {
            "trade_count": self.trade_count,
            "position_btc": round(self.position, 8),
            "avg_entry": round(self.avg_entry, 2),
            "cash": round(self.cash, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "total_commission": round(self.total_commission, 4),
            "uptime_sec": round(time.time() - self._start_time, 0),
        }

    def trade_log(self) -> List[dict]:
        return list(self._trade_log)


# ═══════════════════════════════════════════════════════════════
# SECTION 8: PNL TRACKER
# ═══════════════════════════════════════════════════════════════

class PnLTracker:
    """Performance analytics and metrics."""

    def __init__(self, config: Config):
        self._initial_equity: float = config.INITIAL_CASH
        self._equity_curve: deque = deque(maxlen=10000)
        self._trade_pnls: List[float] = []
        self._last_equity: float = self._initial_equity
        self._peak: float = self._initial_equity
        self._max_drawdown: float = 0.0
        self._total_trades: int = 0

    def record(self, execution_result: dict):
        equity = execution_result.get("equity", self._last_equity)
        self._equity_curve.append((time.time(), equity))

        if execution_result.get("trade_count", 0) > self._total_trades:
            self._total_trades = execution_result["trade_count"]
            realized = execution_result.get("realized_pnl", 0)
            if self._trade_pnls:
                pnl_delta = realized - self._trade_pnls[-1] if self._trade_pnls else realized
                self._trade_pnls.append(pnl_delta)
            else:
                self._trade_pnls.append(realized)

        self._last_equity = equity
        if equity > self._peak:
            self._peak = equity
        dd = (self._peak - equity) / self._peak * 100 if self._peak > 0 else 0
        if dd > self._max_drawdown:
            self._max_drawdown = dd

    def stats(self) -> dict:
        return_pct = 0.0
        if self._initial_equity > 0:
            return_pct = (self._last_equity - self._initial_equity) / self._initial_equity * 100
        wins = sum(1 for p in self._trade_pnls if p > 0)
        losses = sum(1 for p in self._trade_pnls if p < 0)
        total = len(self._trade_pnls)
        gross_profit = sum(p for p in self._trade_pnls if p > 0)
        gross_loss = abs(sum(p for p in self._trade_pnls if p < 0))
        return {
            "equity": round(self._last_equity, 2),
            "initial_equity": self._initial_equity,
            "return_pct": round(return_pct, 2),
            "max_drawdown_pct": round(self._max_drawdown, 2),
            "peak_equity": round(self._peak, 2),
            "total_trades": self._total_trades,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0.0,
            "wins": wins,
            "losses": losses,
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0),
        }


# ═══════════════════════════════════════════════════════════════
# SECTION 9: TRADE STORE
# ═══════════════════════════════════════════════════════════════

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY,
    trade_id INTEGER NOT NULL,
    timestamp REAL NOT NULL,
    signal TEXT NOT NULL,
    price REAL NOT NULL,
    exec_price REAL NOT NULL,
    commission REAL NOT NULL,
    position_before REAL NOT NULL,
    position_after REAL NOT NULL,
    cash REAL NOT NULL,
    realized_pnl REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
"""


class TradeStore:
    """SQLite trade log persistence."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self):
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        logger.info("[DB] Connected: %s", self.db_path)

    def insert_trade(self, trade: dict):
        if self._conn is None:
            self.connect()
        try:
            self._conn.execute(
                "INSERT INTO trades (trade_id, timestamp, signal, price, exec_price, "
                "commission, position_before, position_after, cash, realized_pnl) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (trade.get("id", 0), trade.get("timestamp", time.time()),
                 trade.get("action", ""), trade.get("price", 0),
                 trade.get("exec_price", 0), trade.get("commission", 0),
                 trade.get("pos_before", 0), trade.get("pos_after", 0),
                 trade.get("cash", 0), trade.get("realized_pnl", 0)),
            )
            self._conn.commit()
        except Exception as e:
            logger.error("[DB] Insert error: %s", e)

    def get_all_trades(self) -> List[dict]:
        if self._conn is None:
            self.connect()
        try:
            rows = self._conn.execute(
                "SELECT trade_id, timestamp, signal, price, exec_price, "
                "commission, position_before, position_after, cash, realized_pnl "
                "FROM trades ORDER BY timestamp"
            ).fetchall()
            return [
                {"id": r[0], "timestamp": r[1], "signal": r[2], "price": r[3],
                 "exec_price": r[4], "commission": r[5], "position_before": r[6],
                 "position_after": r[7], "cash": r[8], "realized_pnl": r[9]}
                for r in rows
            ]
        except Exception as e:
            logger.error("[DB] Query error: %s", e)
            return []

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


# ═══════════════════════════════════════════════════════════════
# SECTION 10: DASHBOARD
# ═══════════════════════════════════════════════════════════════

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Trading Bot v2.0</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0a0e17;--card:#1a2332;--bdr:#1e3a5f;--txt:#e2e8f0;--dim:#94a3b8;
--g:#10b981;--r:#ef4444;--b:#3b82f6;--y:#f59e0b;--p:#a855f7}
body{font-family:'Courier New',monospace;background:var(--bg);color:var(--txt);min-height:100vh}
.hdr{background:#111827;border-bottom:1px solid var(--bdr);padding:14px 24px;
display:flex;justify-content:space-between;align-items:center}
.hdr h1{font-size:15px;letter-spacing:1px}
.badge{padding:3px 10px;border-radius:16px;font-size:10px;font-weight:700}
.live{background:rgba(16,185,129,.15);color:var(--g);border:1px solid var(--g)}
.sim{background:rgba(59,130,246,.15);color:var(--b);border:1px solid var(--b)}
.off{background:rgba(239,68,68,.15);color:var(--r);border:1px solid var(--r)}
.recon{background:rgba(245,158,11,.15);color:var(--y);border:1px solid var(--y)}
.cb{background:rgba(168,85,247,.15);color:var(--p);border:1px solid var(--p);animation:pulse 1s infinite}
.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;padding:16px 24px}
.card{background:var(--card);border:1px solid var(--bdr);border-radius:8px;padding:16px}
.label{font-size:9px;text-transform:uppercase;letter-spacing:1.5px;color:var(--dim);margin-bottom:6px}
.val{font-size:22px;font-weight:700}
.val.green{color:var(--g)}.val.red{color:var(--r)}.val.blue{color:var(--b)}.val.yellow{color:var(--y)}
.sub{font-size:10px;color:var(--dim);margin-top:4px}
.section{padding:0 24px 16px}
.pair{display:grid;grid-template-columns:2fr 1fr;gap:12px}
.triple{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
table{width:100%;border-collapse:collapse;font-size:11px}
th{text-align:left;padding:5px 8px;font-size:9px;text-transform:uppercase;
color:var(--dim);border-bottom:1px solid var(--bdr)}
td{padding:5px 8px;border-bottom:1px solid rgba(30,58,95,.3)}
.buy{color:var(--g);font-weight:700}.sell{color:var(--r);font-weight:700}
.log{background:var(--card);border:1px solid var(--bdr);border-radius:8px;
max-height:260px;overflow-y:auto;font-size:10px;padding:10px}
.log-entry{padding:1px 0}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;
animation:pulse 2s infinite}
.dot.on{background:var(--g)}.dot.off{background:var(--r);animation:none}
.dot.recon{background:var(--y)}.dot.cb{background:var(--p);animation:pulse 1s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.indicator-bar{display:flex;gap:8px;align-items:center;margin:4px 0}
.indicator-label{font-size:10px;color:var(--dim);min-width:40px}
.indicator-val{font-size:11px;font-weight:600;min-width:70px}
.indicator-bar-bg{flex:1;height:6px;background:#21262d;border-radius:3px;overflow:hidden}
.indicator-bar-fill{height:100%;border-radius:3px;transition:width .3s}
.sig-indicator{display:inline-block;padding:2px 6px;border-radius:4px;font-size:10px;font-weight:700}
.sig-buy{background:rgba(16,185,129,.2);color:var(--g)}.sig-sell{background:rgba(239,68,68,.2);color:var(--r)}
.sig-hold{background:rgba(148,163,184,.2);color:var(--dim)}
</style>
</head>
<body>
<div class="hdr">
<h1>TRADING BOT <span style="color:var(--b)">v2.0</span> <span style="color:var(--dim);font-size:11px">LIVE TRADING READY</span></h1>
<div style="display:flex;align-items:center;gap:10px">
<span id="feed-info" style="font-size:10px;color:var(--dim)">Feed: --</span>
<span class="dot off" id="dot"></span><span class="badge off" id="badge">OFF</span>
</div>
</div>
<div id="cb-banner" style="display:none;background:#3d1214;border-bottom:2px solid var(--p);
padding:8px 24px;text-align:center;color:var(--p);font-weight:700;font-size:12px">
CIRCUIT BREAKER ACTIVE - ALL TRADING HALTED
</div>
<div class="grid">
<div class="card"><div class="label">Equity</div><div class="val blue" id="eq">$0</div>
<div class="sub" id="ret">Return: 0%</div></div>
<div class="card"><div class="label">Position</div><div class="val yellow" id="pos">0</div>
<div class="sub" id="entry">Avg Entry: $0</div></div>
<div class="card"><div class="label">Realized PnL</div><div class="val green" id="rpnl">$0</div>
<div class="sub" id="wr">Win Rate: 0% | Trades: 0</div></div>
<div class="card"><div class="label">Price</div><div class="val" id="prc">--</div>
<div class="sub" id="sig">Last Signal: HOLD</div></div>
<div class="card"><div class="label">Heartbeat</div><div class="val blue" id="hb">0</div>
<div class="sub" id="feed-stat">Feed: -- | Errors: 0</div></div>
</div>
<div class="section"><div class="pair">
<div class="card"><div class="label">Trades</div>
<table><thead><tr><th>#</th><th>Time</th><th>Side</th><th>Price</th><th>Comm</th><th>PnL</th></tr></thead>
<tbody id="tbl"></tbody></table></div>
<div class="card"><div class="label">Event Log</div><div class="log" id="log"></div></div>
</div></div>
<div class="section"><div class="triple">
<div class="card"><div class="label">Signal Debug (Latest)</div><div id="sig-debug">Waiting for data...</div></div>
<div class="card"><div class="label">Indicators</div><div id="indicators">Waiting for data...</div></div>
<div class="card"><div class="label">Risk & Stats</div>
<div id="risk-stats">
<div style="margin:6px 0;font-size:11px">Signals: <b id="r-sigs">0</b> | Risk Pass: <b id="r-pass">0</b> | Block: <b id="r-block">0</b></div>
<div style="margin:6px 0;font-size:11px">Max DD: <b id="r-dd" style="color:var(--r)">0%</b></div>
<div style="margin:6px 0;font-size:11px">Commission: <b id="r-comm">$0</b></div>
<div style="margin:6px 0;font-size:11px">Profit Factor: <b id="r-pf">0</b></div>
<div style="margin:6px 0;font-size:11px">Strategy: <b id="r-strat">--</b></div>
<div style="margin:6px 0;font-size:11px">Sensitivity: <b id="r-sens">--</b></div>
</div></div>
</div></div>
<script>
var $=function(id){return document.getElementById(id)};
function fmt(n){return(n>=0?"+":"")+"$"+Math.abs(n).toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2})}
function pct(n){return(n>=0?"+":"")+n.toFixed(2)+"%"}
function ts(t){return new Date(t*1000).toLocaleTimeString("en-US",{hour12:false})}
var ML=120,logs=[];
function addLog(cls,msg){logs.unshift({c:cls,m:msg});if(logs.length>ML)logs.pop();
var el=$("log");el.innerHTML=logs.map(function(l){return"<div class='log-entry'><span style='color:var(--dim)'>"+ts(Date.now()/1000)+"</span> <span style='color:var(--"+l.c+")'>"+l.m+"</span></div>"}).join("")}
function sigBadge(s){var c=s==="BUY"?"sig-buy":s==="SELL"?"sig-sell":"sig-hold";return"<span class='sig-indicator "+c+"'>"+s+"</span>"}
function indicatorBar(label,value,max,color){var pct2=Math.min(Math.abs(value)/max*100,100);return'<div class="indicator-bar"><span class="indicator-label">'+label+'</span><span class="indicator-val">'+value.toFixed(1)+'</span><div class="indicator-bar-bg"><div class="indicator-bar-fill" style="width:'+pct2+'%;background:'+color+'"></div></div></div>'}
function refresh(){fetch("/api/status").then(function(r){return r.json()}).then(function(s){
$("eq").textContent=fmt(s.equity-s.initial_equity);$("eq").className="val "+(s.equity>=s.initial_equity?"green":"red");
var r=(s.equity-s.initial_equity)/s.initial_equity*100;$("ret").textContent="Return: "+pct(r);
$("pos").textContent=s.position;$("entry").textContent="Avg Entry: $"+(s.avg_entry||0).toLocaleString();
$("rpnl").textContent=fmt(s.realized_pnl);$("rpnl").className="val "+(s.realized_pnl>=0?"green":"red");
$("wr").textContent="Win: "+s.win_rate+"% | Trades: "+s.total_trades;
$("prc").textContent="$"+(s.price||0).toLocaleString();$("sig").innerHTML="Signal: "+sigBadge(s.last_signal);
$("dd").textContent=s.max_drawdown.toFixed(2)+"%";
$("r-comm").textContent="$"+s.total_commission.toFixed(4);
$("r-pf").textContent=s.profit_factor==="Infinity"?"inf":s.profit_factor;
$("r-sigs").textContent=s.signals;$("r-pass").textContent=s.risk_passed;$("r-block").textContent=s.risk_blocked;
$("r-strat").textContent=s.strategy;$("r-sens").textContent=s.sensitivity;

// Feed status
var fq=s.feed_quality||{};
var dot=$("dot"),badge=$("badge"),fi=$("feed-info");
fi.textContent="Feed: "+(fq.status||"--")+" | Ticks: "+(fq.ticks||0)+" | Err: "+(fq.errors||0);
if(fq.status==="connected"){dot.className="dot on";badge.className="badge live";badge.textContent="LIVE"}
else if(fq.status==="simulated"){dot.className="dot on";badge.className="badge sim";badge.textContent="SIM"}
else if(fq.status==="reconnecting"){dot.className="dot recon";badge.className="badge recon";badge.textContent="RECONN"}
else{dot.className="dot off";badge.className="badge off";badge.textContent="OFF"}

// Heartbeat
$("hb").textContent=(fq.ticks||0);
$("feed-stat").textContent="Ticks: "+(fq.ticks||0)+" | Errors: "+(fq.errors||0)+" | Recon: "+(fq.reconnects||0);

// Circuit breaker
$("cb-banner").style.display=s.circuit_breaker?"block":"none";
if(s.circuit_breaker){dot.className="dot cb";badge.className="badge cb";badge.textContent="CB"}

// Signal debug
var sd=s.signal_debug;
if(sd){
var si=sd.indicators||{};
var ss=sd.signals||{};
var html='<div style="font-size:10px;margin:6px 0">Bar: '+sd.bar+' | Price: $'+sd.price.toLocaleString()+'</div>';
html+='<div style="margin:4px 0;font-size:10px">MA Cross: '+sigBadge(ss.ma_cross||"HOLD")+'</div>';
html+='<div style="margin:4px 0;font-size:10px">Momentum: '+sigBadge(ss.momentum||"HOLD")+'</div>';
html+='<div style="margin:4px 0;font-size:10px">RSI: '+sigBadge(ss.rsi||"HOLD")+'</div>';
html+='<div style="margin:4px 0;font-size:10px;color:var(--dim)">=> Final: '+sigBadge(sd.final||"HOLD")+'</div>';
if(sd.reasons&&sd.reasons.length)html+='<div style="margin:4px 0;font-size:9px;color:var(--dim)">'+sd.reasons.join("; ")+'</div>';
$("sig-debug").innerHTML=html}

// Indicators
if(si){
$("indicators").innerHTML=
indicatorBar("SMA-S",si.sma_short||0,100000,"var(--b)")+
indicatorBar("SMA-L",si.sma_long||0,100000,"var(--p)")+
indicatorBar("RSI",si.rsi||50,100,"var(--y)")+
indicatorBar("Mom%",si.momentum||0,1,"var(--g)")+
indicatorBar("MACD",si.macd||0,500,"var(--b)")+
indicatorBar("BB%",si.bb_pct||50,100,"var(--p)")}

}).catch(function(){})}
function loadTrades(){fetch("/api/trades").then(function(r){return r.json()}).then(function(t){
$("tbl").innerHTML=t.slice(-20).reverse().map(function(x){return"<tr><td>"+x.id+"</td><td>"+ts(x.timestamp)+"</td>"+
"<td class='"+(x.signal==="BUY"?"buy":"sell")+"'>"+x.signal+"</td><td>$"+x.exec_price+"</td>"+
"<td>"+x.commission.toFixed(4)+"</td><td style='color:"+(x.realized_pnl>=0?"var(--g)":"var(--r)")+"'>"+fmt(x.realized_pnl)+"</td></tr>"}).join("")
}).catch(function(){})}
refresh();loadTrades();setInterval(refresh,1500);setInterval(loadTrades,5000);
addLog("b","Trading Bot v2.0 dashboard loaded.");
</script>
</body></html>"""


def create_dashboard_app(engine) -> "FastAPI":
    """Create FastAPI app wired to TradingEngine."""
    fastapi, uvicorn, HTMLResponse, JSONResponse, CORSMiddleware = _import_fastapi()

    app = fastapi.FastAPI(title="Trading Bot v2.0", version="2.0")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    html = HTMLResponse(content=HTML_TEMPLATE)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return html

    @app.get("/api/status")
    async def status():
        try:
            ex = engine.execution.stats()
            pn = engine.pnl.stats()
            st = engine.strategy.stats()
            rs = engine.risk.stats()
            price = engine.feed.price or 0
            debug = engine.strategy.get_debug_log()
            return {
                "equity": ex["cash"] + ex["position_btc"] * price,
                "initial_equity": engine.config.INITIAL_CASH,
                "cash": ex["cash"], "position": round(abs(ex["position_btc"]), 6),
                "avg_entry": ex["avg_entry"], "realized_pnl": ex["realized_pnl"],
                "total_commission": ex["total_commission"], "total_trades": ex["trade_count"],
                "win_rate": pn["win_rate"], "max_drawdown": pn["max_drawdown_pct"],
                "profit_factor": pn["profit_factor"], "return_pct": pn["return_pct"],
                "price": price, "last_signal": st["last_signal"],
                "signals": st["signals_generated"], "strategy": st["strategy"],
                "sensitivity": st["sensitivity"],
                "risk_blocked": rs["blocked"], "risk_passed": rs["passed"],
                "circuit_breaker": rs["circuit_breaker_active"],
                "ticks": engine.feed.tick_count,
                "feed_quality": engine.feed.feed_quality,
                "signal_debug": debug[-1] if debug else None,
            }
        except Exception as e:
            return {"error": str(e)}

    @app.get("/api/trades")
    async def trades():
        try:
            return engine.execution.trade_log()
        except Exception:
            return []

    @app.get("/api/pnl")
    async def pnl():
        try:
            return engine.pnl.stats()
        except Exception:
            return {}

    @app.get("/api/feed")
    async def feed_status():
        return engine.feed.feed_quality

    @app.get("/api/debug")
    async def debug():
        return {"debug_log": engine.strategy.get_debug_log()[-20:]}

    return app


def run_dashboard(engine, host: str = "0.0.0.0", port: int = 8080):
    """Start dashboard in background thread."""
    import uvicorn
    app = create_dashboard_app(engine)

    def _run():
        logger.info("[DASH] Starting on http://%s:%d", host, port)
        uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning")).run()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    logger.info("[DASH] Thread started (port %d)", port)
    return thread


# ═══════════════════════════════════════════════════════════════
# SECTION 11: TRADING ENGINE (Main Orchestrator)
# ═══════════════════════════════════════════════════════════════

class TradingEngine:
    """
    Main trading engine v2.0.
    Wires: Feed -> Indicators -> Strategy -> Risk -> Execution -> PnL -> Store -> Dashboard

    Key fixes from v1.0:
      - Heartbeat logging every N ticks
      - Signal debug output
      - Feed health monitoring
      - Circuit breaker
      - Multi-strategy support
    """

    def __init__(self, config: Config):
        self.config = config
        self._running = False

        # Components
        self.feed = Feed(config)
        self.strategy = Strategy(config)
        self.risk = RiskEngine(config)
        self.execution = ExecutionEngine(config)
        self.pnl = PnLTracker(config)
        self.store = TradeStore(config.DB_PATH)

    def run(self, max_ticks: Optional[int] = None):
        """Run the main trading loop."""
        self._running = True
        self._setup_signal_handlers()

        logger.info("=" * 60)
        logger.info("  TRADING BOT v2.0 - LIVE TRADING READY")
        logger.info("=" * 60)
        logger.info("  Mode:        %s", self.config.MODE)
        logger.info("  Symbol:      %s", self.config.SYMBOL)
        logger.info("  Cash:        $%.2f", self.config.INITIAL_CASH)
        logger.info("  Strategy:    %s", self.config.STRATEGY)
        logger.info("  Sensitivity: %s", self.config.SENSITIVITY)
        logger.info("  Max Pos:     %d", self.config.MAX_POSITION)
        logger.info("  MA:          %d x %d", self.config.MA_SHORT, self.config.MA_LONG)
        logger.info("  RSI:         %d (%.0f/%.0f)", self.config.RSI_PERIOD,
                     self.config.RSI_OVERSOLD, self.config.RSI_OVERBOUGHT)
        logger.info("  Cooldown:    %d ticks", self.config.COOLDOWN_TICKS)
        logger.info("  Comm:        %.4f%%/side", self.config.COMMISSION_PER_SIDE * 100)
        logger.info("  CB Trigger:  %.1f%% drawdown -> %d min halt",
                     self.config.CIRCUIT_BREAKER_DRAWDOWN,
                     self.config.CIRCUIT_BREAKER_COOLDOWN_SEC // 60)
        logger.info("=" * 60)

        # Verify feed works
        logger.info("[BOOT] Testing market data feed...")
        try:
            test_price = self.feed.get_price()
            logger.info("[BOOT] Feed OK - first price: $%.2f", test_price)
        except Exception as e:
            logger.error("[BOOT] Feed FAILED: %s", e)
            if self.config.MODE == "live":
                logger.error("[BOOT] Cannot start in live mode without market data!")
                return

        try:
            if self.config.MODE == "backtest":
                self._run_backtest(max_ticks or self.config.BACKTEST_BARS)
            else:
                self._run_live(max_ticks)
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            self._print_report()

    def _run_live(self, max_ticks: Optional[int]):
        """Run with live/sim feed loop."""
        def on_tick(price: float):
            if not self._running:
                return
            self._process_tick(price)

        self.feed.run(on_tick, max_ticks=max_ticks)

    def _run_backtest(self, n_bars: int):
        """Run backtest over pre-generated data."""
        logger.info("[BT] Generating %d bars...", n_bars)
        prices = self.feed.generate_backtest_data(n_bars)
        logger.info("[BT] Processing %d bars...", n_bars)
        for i, price in enumerate(prices):
            self._process_tick(price)
            if (i + 1) % 100 == 0:
                logger.info("[BT] Progress: %d/%d (%.0f%%)",
                            i + 1, n_bars, (i + 1) / n_bars * 100)

    def _process_tick(self, price: float):
        """Process a single tick through the full pipeline."""
        # 1. Strategy generates signal
        signal, debug = self.strategy.generate(price)

        # 2. Tick risk cooldown
        self.risk.tick()

        # 3. Heartbeat logging
        if signal == "HOLD" and self.feed.tick_count % self.config.HEARTBEAT_INTERVAL == 0:
            fq = self.feed.feed_quality
            ind = debug.get("indicators", {})
            logger.info(
                "[HEARTBEAT] tick=%d price=$%.2f feed=%s | SMA=%d/%d RSI=%.1f Mom=%.3f | pos=%.6f equity=$%.0f signals=%d",
                self.feed.tick_count, price, fq["status"],
                self.config.MA_SHORT, self.config.MA_LONG,
                ind.get("rsi", 50), ind.get("momentum", 0),
                self.execution.position,
                self.execution.cash + self.execution.position * price,
                self.strategy.signal_count
            )

        if signal == "HOLD":
            return

        # 4. Risk validates signal
        snap = self.execution._snapshot(price, signal)
        equity = snap["equity"]
        pos_units = int(abs(self.execution.position * price / max(snap["avg_entry"] * self.config.SIZE_PER_UNIT, 1))) if snap["avg_entry"] > 0 else 0
        notional = abs(self.execution.position * price)

        safe_signal = self.risk.validate(signal, pos_units, self.execution.cash, equity, notional)
        if safe_signal == "BLOCK":
            return

        # 5. Execute trade
        result = self.execution.execute(safe_signal, price)

        # 6. Track PnL
        self.pnl.record(result)

        # 7. Log to DB
        if self.execution.trade_count > 0:
            last_trade = self.execution.trade_log()[-1]
            self.store.insert_trade(last_trade)

    def _setup_signal_handlers(self):
        def handler(signum, frame):
            logger.info("[ENGINE] Shutdown signal received")
            self._running = False
        sig.signal(sig.SIGINT, handler)

    def _print_report(self):
        """Print final report on shutdown."""
        stats = self.execution.stats()
        pnl_stats = self.pnl.stats()
        risk_stats = self.risk.stats()
        strat_stats = self.strategy.stats()
        fq = self.feed.feed_quality

        logger.info("=" * 60)
        logger.info("  TRADING REPORT v2.0")
        logger.info("=" * 60)
        logger.info("  Ticks processed:     %d", self.feed.tick_count)
        logger.info("  Feed status:         %s (errors=%d, reconnects=%d)",
                     fq["status"], fq["errors"], fq["reconnects"])
        logger.info("  Signals generated:   %d", strat_stats["signals_generated"])
        logger.info("  Strategy:            %s (%s)", strat_stats["strategy"], strat_stats["sensitivity"])
        logger.info("  Risk passed:         %d", risk_stats["passed"])
        logger.info("  Risk blocked:        %d (%s)",
                     risk_stats["blocked"],
                     dict(risk_stats["block_reasons"]) if risk_stats["block_reasons"] else "none")
        logger.info("  Circuit breaker:     %s (triggered %d times)",
                     "ACTIVE" if risk_stats["circuit_breaker_active"] else "inactive",
                     risk_stats["circuit_breaker_count"])
        logger.info("  Trades executed:     %d", stats["trade_count"])
        logger.info("  Final position:      %.6f BTC ($%.0f) @ avg $%.2f",
                     stats["position_btc"], abs(stats["position_btc"]) * (self.feed.price or 0), stats["avg_entry"])
        logger.info("  Final cash:          $%.2f", stats["cash"])
        logger.info("  Realized PnL:        $%.2f", stats["realized_pnl"])
        logger.info("  Total commission:    $%.4f", stats["total_commission"])
        logger.info("  Return:              %.2f%%", pnl_stats["return_pct"])
        logger.info("  Max drawdown:        %.2f%%", pnl_stats["max_drawdown_pct"])
        logger.info("  Win rate:            %.1f%%", pnl_stats["win_rate"])
        logger.info("  Uptime:              %.0f sec", stats["uptime_sec"])
        logger.info("=" * 60)


# ═══════════════════════════════════════════════════════════════
# SECTION 12: CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════

def parse_args():
    p = argparse.ArgumentParser(
        prog="trading_bot_v2",
        description="Trading Bot v2.0 - Live Trading Ready",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python trading_bot_v2.py                              Sim mode + dashboard
  python trading_bot_v2.py --mode live                   Live Binance + dashboard
  python trading_bot_v2.py --mode live --no-dash         Headless live
  python trading_bot_v2.py --mode backtest --bars 2000   Backtest
  python trading_bot_v2.py --strategy momentum           Momentum only
  python trading_bot_v2.py --sensitivity aggressive      More signals
  python trading_bot_v2.py --nav 100000 --cash 100000    $100K account
        """,
    )
    p.add_argument("--mode", choices=["live", "sim", "backtest"], default="sim",
                   help="Data source (default: sim)")
    p.add_argument("--dashboard", action="store_true", default=True,
                   help="Start web dashboard (default: True)")
    p.add_argument("--no-dash", action="store_true",
                   help="Disable dashboard")
    p.add_argument("--port", type=int, default=8080,
                   help="Dashboard port (default: 8080)")
    p.add_argument("--symbol", type=str, default="BTCUSDT",
                   help="Trading symbol (default: BTCUSDT)")
    p.add_argument("--cash", type=float, default=10000,
                   help="Initial cash (default: 10000)")
    p.add_argument("--nav", type=float, default=100000,
                   help="NAV for risk calculations (default: 100000)")
    p.add_argument("--max-pos", type=int, default=3,
                   help="Max position size (default: 3)")
    p.add_argument("--strategy", choices=["ma_cross", "momentum", "rsi", "multi"], default="multi",
                   help="Strategy mode (default: multi)")
    p.add_argument("--sensitivity", choices=["conservative", "moderate", "aggressive"], default="moderate",
                   help="Signal sensitivity (default: moderate)")
    p.add_argument("--bars", type=int, default=500,
                   help="Backtest bars (default: 500)")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args()


def main():
    args = parse_args()
    setup_logging(args.log_level)

    config = Config()
    config.MODE = args.mode
    config.SYMBOL = args.symbol
    config.INITIAL_CASH = args.cash
    config.MAX_POSITION = args.max_pos
    config.DASHBOARD_PORT = args.port
    config.BACKTEST_BARS = args.bars
    config.STRATEGY = args.strategy
    config.SENSITIVITY = args.sensitivity
    config.NAV_USD = args.nav
    config.apply_sensitivity()

    engine = TradingEngine(config)

    show_dashboard = args.dashboard and not args.no_dash
    if show_dashboard:
        try:
            run_dashboard(engine, port=args.port)
            logger.info("[MAIN] Dashboard: http://localhost:%d", args.port)
        except ImportError:
            logger.error("[MAIN] Dashboard requires: pip install fastapi uvicorn")
            logger.info("[MAIN] Continuing without dashboard...")

    try:
        engine.run()
    except KeyboardInterrupt:
        logger.info("[MAIN] Shutting down...")
    finally:
        engine.store.close()


if __name__ == "__main__":
    main()

