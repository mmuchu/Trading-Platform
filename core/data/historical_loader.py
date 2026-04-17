import logging
from datetime import datetime, timezone
from typing import Optional
import requests
from config.settings import settings
logger = logging.getLogger(__name__)

class HistoricalLoader:
    BASE_URL = settings.binance.rest_base
    def __init__(self, symbol=None):
        self.symbol = symbol or settings.binance.symbol

    def fetch_klines(self, interval="1m", limit=1000, start_time=None, end_time=None):
        params = {"symbol": self.symbol.upper(), "interval": interval, "limit": limit}
        if start_time: params["startTime"] = start_time
        if end_time: params["endTime"] = end_time
        try:
            resp = requests.get(f"{self.BASE_URL}/klines", params=params, timeout=10)
            resp.raise_for_status()
            raw = resp.json()
        except requests.RequestException as exc:
            logger.error("Failed to fetch klines: %s", exc); return []
        klines = []
        for row in raw:
            klines.append({"open_time":row[0],"open":float(row[1]),"high":float(row[2]),"low":float(row[3]),"close":float(row[4]),"volume":float(row[5]),"close_time":row[6]})
        logger.info("Fetched %d klines  symbol=%s  interval=%s", len(klines), self.symbol, interval)
        return klines

    def fetch_close_prices(self, interval="1m", limit=1000, start_time=None, end_time=None):
        return [k["close"] for k in self.fetch_klines(interval, limit, start_time, end_time)]

    @staticmethod
    def date_to_ms(date_str):
        fmt = "%Y-%m-%d" if len(date_str) == 10 else "%Y-%m-%d %H:%M:%S"
        dt = datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)