"""
Configuration for Trading Bot v1.0
All tuneable parameters in one place.
"""

import os

class Config:
    # ── Trading ──
    SYMBOL: str = "BTCUSDT"
    INITIAL_CASH: float = 10_000.0
    MAX_POSITION: int = 3              # max absolute position size

    # ── Position Sizing ──
    # Each "unit" is worth this fraction of current cash.
    # 0.1 = use 10% of cash per trade, so max 3 units = 30% allocation
    SIZE_PER_UNIT: float = 0.1         # fraction of cash per position unit

    # ── Strategy ──
    MA_SHORT: int = 10               # short moving average period
    MA_LONG: int = 30                 # long moving average period (dual MA crossover)

    # ── Execution ──
    COMMISSION_PER_SIDE: float = 0.0004   # 0.04% per side (Binance maker)
    SLIPPAGE_PER_SIDE: float = 0.0001     # 0.01% slippage estimate

    # ── Feed ──
    FEED_INTERVAL_SEC: float = 5.0    # seconds between price polls (live mode)
    Binance_API_URL: str = "https://api.binance.com/api/v3/ticker/price"

    # ── Backtest ──
    BACKTEST_BARS: int = 500
    BACKTEST_START_PRICE: float = 65_000.0
    BACKTEST_VOLATILITY: float = 50.0  # standard deviation per tick

    # ── Dashboard ──
    DASHBOARD_HOST: str = "0.0.0.0"
    DASHBOARD_PORT: int = 8080

    # ── Paths ──
    DB_PATH: str = os.path.join(os.path.dirname(__file__), "db", "trades.sqlite")

    # ── Risk ──
    MAX_DRAWDOWN_PCT: float = 5.0     # halt trading if drawdown exceeds this
    MIN_BALANCE: float = 500.0        # halt if cash drops below this

    def __repr__(self):
        return (f"Config(symbol={self.SYMBOL}, cash={self.INITIAL_CASH}, "
                f"max_pos={self.MAX_POSITION}, size={self.SIZE_PER_UNIT}, "
                f"ma_short={self.MA_SHORT}, ma_long={self.MA_LONG})")
