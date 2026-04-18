"""
Execution Engine - Paper trading order execution.

Clean model:
  - Cash in USD
  - Position in BTC (fractional, can be positive or negative)
  - BUY when short: close short fully, then open long
  - BUY when flat: open long with SIZE_FRACTION of cash
  - BUY when long: add to long with SIZE_FRACTION of cash
  - SELL when long: close long fully, then open short
  - SELL when flat: open short with SIZE_FRACTION of cash
  - SELL when short: add to short with SIZE_FRACTION of cash

  - Equity = cash + position * current_price
"""

import logging
import time
from typing import Optional

from trading_bot.config import Config

logger = logging.getLogger("execution")


class ExecutionEngine:
    """Paper trading with proper position management."""

    def __init__(self, config: Config):
        self.config = config
        self.cash: float = config.INITIAL_CASH
        self.position: float = 0.0          # BTC quantity (+long, -short)
        self.avg_entry: float = 0.0         # weighted average entry price
        self.realized_pnl: float = 0.0
        self.total_commission: float = 0.0
        self.trade_count: int = 0
        self._trade_log: list[dict] = []
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
                # Step 1: Close existing short fully (buy back borrowed BTC)
                close_value = abs(self.position) * exec_price  # cost to buy back
                close_pnl = (self.avg_entry - exec_price) * abs(self.position)
                close_comm = close_value * commission_rate
                self.realized_pnl += close_pnl
                self.cash -= close_value + close_comm  # pay to buy back
                self.total_commission += close_comm
                self._log_trade("CLOSE_SHORT", price, exec_price, abs(old_pos), close_pnl, close_comm, old_pos, 0.0)
                self.position = 0.0
                self.avg_entry = 0.0

            if self.position >= 0:
                # Step 2: Open or add to long
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
                # Step 1: Close existing long fully
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
                # Step 2: Open or add to short
                notional = self.cash * self.config.SIZE_PER_UNIT
                btc_qty = notional / exec_price
                comm = notional * commission_rate

                if self.position == 0:
                    self.avg_entry = exec_price
                else:
                    total = abs(self.position) + btc_qty
                    self.avg_entry = (self.avg_entry * abs(self.position) + exec_price * btc_qty) / total

                self.position -= btc_qty
                self.cash += notional - comm  # receive proceeds from short sale
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
                "TRADE #%d %s %.6f BTC @ $%.2f pnl=$%+.2f cash=$%.0f",
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

    def trade_log(self) -> list[dict]:
        return list(self._trade_log)
