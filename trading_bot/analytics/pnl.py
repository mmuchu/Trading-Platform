"""
PnL Tracker - Performance analytics and metrics.

Tracks:
  - Equity curve over time
  - Max drawdown
  - Win/loss statistics
  - Return percentage
"""

import logging
import time
from collections import deque
from typing import Optional

from trading_bot.config import Config

logger = logging.getLogger("pnl")


class PnLTracker:
    """Track trading performance metrics."""

    def __init__(self, config: Config):
        self.config = config
        self._initial_equity: float = config.INITIAL_CASH
        self._equity_curve: deque = deque(maxlen=10000)
        self._trade_pnls: list[float] = []
        self._last_equity: float = self._initial_equity
        self._peak: float = self._initial_equity
        self._max_drawdown: float = 0.0
        self._total_trades: int = 0

    def record(self, execution_result: dict):
        """Record a trade result for analytics."""
        equity = execution_result.get("equity", self._last_equity)
        self._equity_curve.append((time.time(), equity))

        if execution_result.get("trade_count", 0) > self._total_trades:
            # New trade happened
            self._total_trades = execution_result["trade_count"]
            realized = execution_result.get("realized_pnl", 0)
            # Track PnL delta (difference between this trade and last)
            if self._trade_pnls:
                pnl_delta = realized - self._trade_pnls[-1] if self._trade_pnls else realized
                self._trade_pnls.append(pnl_delta)
            else:
                self._trade_pnls.append(realized)

        self._last_equity = equity

        # Update peak and drawdown
        if equity > self._peak:
            self._peak = equity
        dd = (self._peak - equity) / self._peak * 100 if self._peak > 0 else 0
        if dd > self._max_drawdown:
            self._max_drawdown = dd

    def stats(self) -> dict:
        """Return current performance statistics."""
        return_pct = 0.0
        if self._initial_equity > 0:
            return_pct = (self._last_equity - self._initial_equity) / self._initial_equity * 100

        wins = sum(1 for p in self._trade_pnls if p > 0)
        losses = sum(1 for p in self._trade_pnls if p < 0)
        total = len(self._trade_pnls)

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
            "avg_win": round(sum(p for p in self._trade_pnls if p > 0) / wins, 2) if wins > 0 else 0.0,
            "avg_loss": round(sum(p for p in self._trade_pnls if p < 0) / losses, 2) if losses > 0 else 0.0,
            "profit_factor": self._profit_factor(),
        }

    def _profit_factor(self) -> float:
        """Gross profit / gross loss."""
        gross_profit = sum(p for p in self._trade_pnls if p > 0)
        gross_loss = abs(sum(p for p in self._trade_pnls if p < 0))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return round(gross_profit / gross_loss, 2)

    def equity_history(self) -> list[dict]:
        """Return equity curve as list of (timestamp, equity) dicts."""
        return [{"ts": t, "equity": round(e, 2)} for t, e in self._equity_curve]
