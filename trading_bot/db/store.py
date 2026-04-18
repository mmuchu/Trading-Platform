"""
Trade Store - SQLite persistence for trade logs.

Lightweight, no ORM, no dependencies. Just SQLite.
"""

import logging
import os
import sqlite3
import time
from typing import Optional

logger = logging.getLogger("db.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY,
    trade_id INTEGER NOT NULL,
    timestamp REAL NOT NULL,
    signal TEXT NOT NULL,
    price REAL NOT NULL,
    exec_price REAL NOT NULL,
    commission REAL NOT NULL,
    slippage REAL NOT NULL,
    position_before INTEGER NOT NULL,
    position_after INTEGER NOT NULL,
    cash REAL NOT NULL,
    realized_pnl REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_signal ON trades(signal);
"""


class TradeStore:
    """SQLite trade log persistence."""

    def __init__(self, config):
        self.db_path = config.DB_PATH
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self):
        """Open database connection and create tables."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        logger.info("TradeStore connected: %s", self.db_path)

    def insert_trade(self, trade: dict):
        """Insert a trade record into the database."""
        if self._conn is None:
            self.connect()
        try:
            self._conn.execute(
                "INSERT INTO trades (trade_id, timestamp, signal, price, exec_price, "
                "commission, slippage, position_before, position_after, cash, realized_pnl) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    trade.get("id", 0),
                    trade.get("timestamp", time.time()),
                    trade.get("signal", ""),
                    trade.get("price", 0),
                    trade.get("exec_price", 0),
                    trade.get("commission", 0),
                    trade.get("slippage", 0),
                    trade.get("position_before", 0),
                    trade.get("position_after", 0),
                    trade.get("cash", 0),
                    trade.get("realized_pnl", 0),
                )
            )
            self._conn.commit()
        except Exception as e:
            logger.error("DB insert error: %s", e)

    def get_all_trades(self) -> list[dict]:
        """Fetch all trades from the database."""
        if self._conn is None:
            self.connect()
        try:
            rows = self._conn.execute(
                "SELECT trade_id, timestamp, signal, price, exec_price, "
                "commission, slippage, position_before, position_after, cash, realized_pnl "
                "FROM trades ORDER BY timestamp"
            ).fetchall()
            return [
                {
                    "id": r[0], "timestamp": r[1], "signal": r[2],
                    "price": r[3], "exec_price": r[4], "commission": r[5],
                    "slippage": r[6], "position_before": r[7],
                    "position_after": r[8], "cash": r[9], "realized_pnl": r[10],
                }
                for r in rows
            ]
        except Exception as e:
            logger.error("DB query error: %s", e)
            return []

    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("TradeStore closed")

    def __del__(self):
        self.close()
