"""
Historical Data - Fetch and cache historical price data from Binance.

Used for backtesting with real market data.
"""

import logging
import csv
import os
from typing import Optional

from trading_bot.config import Config

logger = logging.getLogger("data.historical")

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"


def fetch_binance_klines(symbol: str = "BTCUSDT",
                         interval: str = "1m",
                         limit: int = 1000) -> list[float]:
    """
    Fetch historical klines from Binance public API.

    Args:
        symbol: Trading pair (e.g., BTCUSDT)
        interval: Kline interval (1m, 5m, 15m, 1h, 1d)
        limit: Number of klines (max 1000)

    Returns:
        List of close prices
    """
    import requests
    try:
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        prices = [float(k[4]) for k in data]  # k[4] = close price
        logger.info("Fetched %d historical prices for %s (%s)",
                     len(prices), symbol, interval)
        return prices
    except Exception as e:
        logger.error("Failed to fetch historical data: %s", e)
        return []


def fetch_and_cache(symbol: str = "BTCUSDT",
                    interval: str = "1m",
                    limit: int = 1000,
                    cache_path: Optional[str] = None) -> list[float]:
    """
    Fetch historical data and cache to CSV for offline use.

    Args:
        cache_path: Path to save CSV. Defaults to data/historical_{symbol}_{interval}.csv

    Returns:
        List of close prices
    """
    if cache_path is None:
        data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
        os.makedirs(data_dir, exist_ok=True)
        cache_path = os.path.join(data_dir,
                                  f"historical_{symbol}_{interval}.csv")

    # Try loading from cache first
    if os.path.exists(cache_path):
        try:
            prices = []
            with open(cache_path, "r") as f:
                reader = csv.reader(f)
                for row in reader:
                    if row and row[0] != "timestamp":
                        prices.append(float(row[1]))
            if len(prices) >= limit:
                logger.info("Loaded %d prices from cache: %s", len(prices), cache_path)
                return prices[:limit]
        except Exception as e:
            logger.warning("Cache read error: %s", e)

    # Fetch from Binance
    prices = fetch_binance_klines(symbol, interval, limit)

    # Save to cache
    if prices:
        try:
            with open(cache_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "close"])
                for p in prices:
                    writer.writerow(["", p])
            logger.info("Cached %d prices to %s", len(prices), cache_path)
        except Exception as e:
            logger.warning("Cache write error: %s", e)

    return prices
