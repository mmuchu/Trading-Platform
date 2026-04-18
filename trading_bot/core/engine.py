"""
Engine - Main trading loop.

Wires together: Feed -> Strategy -> Risk -> Execution -> Trade Log

Runs synchronously. Simple. Debuggable. Stable.
"""

import logging
import signal as sig
import sys
import time
from typing import Optional

from trading_bot.config import Config
from trading_bot.core.feed import Feed
from trading_bot.core.strategy import Strategy
from trading_bot.core.risk import RiskEngine
from trading_bot.core.execution import ExecutionEngine
from trading_bot.analytics.pnl import PnLTracker
from trading_bot.db.store import TradeStore

logger = logging.getLogger("engine")


class TradingEngine:
    """
    Main trading engine. Wires all components.

    Usage:
        engine = TradingEngine(mode="sim")
        engine.run()
    """

    def __init__(self, config: Optional[Config] = None, mode: str = "live"):
        self.config = config or Config()
        self.mode = mode
        self._running = False

        # Components
        self.feed = Feed(self.config, mode=mode)
        self.strategy = Strategy(self.config)
        self.risk = RiskEngine(self.config)
        self.execution = ExecutionEngine(self.config)
        self.pnl = PnLTracker(self.config)
        self.store = TradeStore(self.config)

        # Callbacks for dashboard
        self._on_tick_callbacks: list = []

    def register_tick_callback(self, callback):
        """Register a callback for every tick (used by dashboard)."""
        self._on_tick_callbacks.append(callback)

    def _fire_callbacks(self, data: dict):
        """Fire all registered callbacks."""
        for cb in self._on_tick_callbacks:
            try:
                cb(data)
            except Exception as e:
                logger.debug("Callback error: %s", e)

    def run(self, max_ticks: Optional[int] = None):
        """
        Run the main trading loop.

        Args:
            max_ticks: Stop after N ticks (for backtesting). None = run forever.
        """
        self._running = True
        self._setup_signal_handlers()

        logger.info("=" * 55)
        logger.info("TRADING BOT v1.0 - Production Baseline")
        logger.info("=" * 55)
        logger.info("Mode:      %s", self.mode)
        logger.info("Symbol:    %s", self.config.SYMBOL)
        logger.info("Cash:      $%.2f", self.config.INITIAL_CASH)
        logger.info("Max Pos:   %d", self.config.MAX_POSITION)
        logger.info("Strategy:  MA(%d) x MA(%d)",
                     self.config.MA_SHORT, self.config.MA_LONG)
        logger.info("Comm:      %.4f%%/side", self.config.COMMISSION_PER_SIDE * 100)
        logger.info("=" * 55)

        try:
            if self.mode == "backtest":
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
        logger.info("Backtest: generating %d bars...", n_bars)
        prices = self.feed.generate_backtest_data(n_bars)
        logger.info("Backtest: processing %d bars...", n_bars)

        for i, price in enumerate(prices):
            self._process_tick(price)
            if (i + 1) % 100 == 0:
                logger.info("Backtest progress: %d/%d (%.0f%%)",
                            i + 1, n_bars, (i + 1) / n_bars * 100)

    def _process_tick(self, price: float):
        """Process a single price tick through the full pipeline."""
        # 1. Strategy generates signal
        signal = self.strategy.generate(price)

        # Tick the risk cooldown counter on every bar
        self.risk._ticks_since_signal += 1

        if signal == "HOLD":
            return

        # 2. Risk validates signal
        snap = self.execution._snapshot(price, signal)
        equity = snap["equity"]
        # Position units: round to int for risk limit (1 BTC unit ≈ SIZE_PER_UNIT of cash)
        pos_units = int(abs(self.execution.position * price / max(snap["avg_entry"] * self.config.SIZE_PER_UNIT, 1))) if snap["avg_entry"] > 0 else 0
        safe_signal = self.risk.validate(
            signal, pos_units, self.execution.cash, equity
        )
        if safe_signal == "BLOCK":
            return

        # 3. Execute trade
        result = self.execution.execute(safe_signal, price)

        # 4. Track PnL
        self.pnl.record(result)

        # 5. Log to DB
        if self.execution.trade_count > 0:
            last_trade = self.execution.trade_log()[-1]
            self.store.insert_trade(last_trade)

        # 6. Fire callbacks (dashboard, etc.)
        self._fire_callbacks(result)

    def _setup_signal_handlers(self):
        """Graceful shutdown on Ctrl+C."""
        def handler(signum, frame):
            logger.info("Shutdown signal received")
            self._running = False
        sig.signal(sig.SIGINT, handler)

    def _print_report(self):
        """Print final report on shutdown."""
        stats = self.execution.stats()
        pnl_stats = self.pnl.stats()
        risk_stats = self.risk.stats()
        strat_stats = self.strategy.stats()

        logger.info("=" * 55)
        logger.info("TRADING REPORT")
        logger.info("=" * 55)
        logger.info("Ticks processed:     %d", self.feed.tick_count)
        logger.info("Signals generated:   %d", strat_stats["signals_generated"])
        logger.info("Risk passed:         %d", risk_stats["passed"])
        logger.info("Risk blocked:        %d (%s)",
                     risk_stats["blocked"],
                     dict(risk_stats["block_reasons"]) if risk_stats["blocked_reasons"] else "none")
        logger.info("Trades executed:     %d", stats["trade_count"])
        logger.info("Final position:      %.6f BTC ($%.0f) @ avg $%.2f",
                     stats["position_btc"], abs(stats["position_btc"]) * (engine.feed.price or 0), stats["avg_entry"])
        logger.info("Final cash:          $%.2f", stats["cash"])
        logger.info("Realized PnL:        $%.2f", stats["realized_pnl"])
        logger.info("Total commission:    $%.4f", stats["total_commission"])
        logger.info("Return:              %.2f%%", pnl_stats["return_pct"])
        logger.info("Max drawdown:        %.2f%%", pnl_stats["max_drawdown_pct"])
        logger.info("Win rate:            %.1f%%", pnl_stats["win_rate"])
        logger.info("=" * 55)
