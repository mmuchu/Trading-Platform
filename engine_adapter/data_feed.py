import logging
import time
from typing import Dict, List, Optional, Callable
from datetime import datetime
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class FeedState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"


@dataclass
class FeedConfig:
    symbols: List[str]
    timeframe: str = "1m"
    include_volume: bool = True
    max_history_candles: int = 500
    on_tick_callback: Optional[Callable] = None
    on_bar_callback: Optional[Callable] = None
    source: str = "paper"


class DataFeed:

    def __init__(self, config: FeedConfig, engine=None):
        self.config = config
        self._engine = engine
        self._state = FeedState.STOPPED
        self._tick_counter = 0
        self._bar_counter = 0
        self._candle_cache: Dict[str, List[Dict]] = {}
        self._current_bar: Dict[str, Dict] = {}
        self._subscribers: Dict[str, List[Callable]] = {}
        self._latest_tickers: Dict[str, Dict] = {}
        logger.info(f"DataFeed initialized for {len(config.symbols)} symbols ({config.timeframe})")

    def start(self):
        self._state = FeedState.RUNNING
        for sym in self.config.symbols:
            self._current_bar[sym] = {"open": 0, "high": 0, "low": float("inf"), "close": 0, "volume": 0, "timestamp": ""}
            self._candle_cache[sym] = []
        logger.info(f"DataFeed started: {self.config.symbols}")

    def stop(self):
        self._state = FeedState.STOPPED
        logger.info("DataFeed stopped")

    def simulate_tick(self, symbol: str, price: float, volume: float = 0):
        if self._state != FeedState.RUNNING:
            return
        self._tick_counter += 1
        self._latest_tickers[symbol] = {"symbol": symbol, "price": price, "volume": volume, "timestamp": datetime.now().isoformat()}
        bar = self._current_bar.get(symbol)
        if bar:
            if bar["open"] == 0:
                bar["open"] = price
                bar["timestamp"] = datetime.now().isoformat()
            bar["close"] = price
            bar["high"] = max(bar["high"], price)
            bar["low"] = min(bar["low"], price)
            bar["volume"] += volume
        for cb in self._subscribers.get("tick", []):
            try:
                cb(symbol, price, volume)
            except Exception as e:
                logger.error(f"Tick callback error: {e}")
        if self.config.on_tick_callback:
            try:
                self.config.on_tick_callback(symbol, price)
            except Exception as e:
                logger.error(f"on_tick_callback error: {e}")

    def complete_bar(self, symbol: str):
        bar = self._current_bar.get(symbol)
        if not bar or bar["open"] == 0:
            return
        completed = dict(bar)
        completed["timestamp"] = datetime.now().isoformat()
        self._candle_cache.setdefault(symbol, []).append(completed)
        max_candles = self.config.max_history_candles
        if len(self._candle_cache[symbol]) > max_candles:
            self._candle_cache[symbol] = self._candle_cache[symbol][-max_candles:]
        self._bar_counter += 1
        self._current_bar[symbol] = {"open": 0, "high": 0, "low": float("inf"), "close": 0, "volume": 0, "timestamp": ""}
        for cb in self._subscribers.get("bar", []):
            try:
                cb(symbol, completed)
            except Exception as e:
                logger.error(f"Bar callback error: {e}")
        if self.config.on_bar_callback:
            try:
                self.config.on_bar_callback(symbol, completed)
            except Exception as e:
                logger.error(f"on_bar_callback error: {e}")

    def get_candles(self, symbol: str, limit: int = 0) -> List[Dict]:
        candles = self._candle_cache.get(symbol, [])
        if limit > 0:
            return candles[-limit:]
        return candles

    def get_all_candles(self) -> Dict[str, List[Dict]]:
        return {sym: list(candles) for sym, candles in self._candle_cache.items()}

    def get_current_bar(self, symbol: str) -> Optional[Dict]:
        bar = self._current_bar.get(symbol)
        if bar and bar["open"] > 0:
            return dict(bar)
        return None

    def get_latest_price(self, symbol: str) -> Optional[float]:
        ticker = self._latest_tickers.get(symbol)
        return ticker["price"] if ticker else None

    def get_all_latest_prices(self) -> Dict[str, float]:
        return {sym: t["price"] for sym, t in self._latest_tickers.items()}

    def subscribe(self, event: str, callback: Callable):
        self._subscribers.setdefault(event, []).append(callback)

    def unsubscribe(self, event: str, callback: Callable):
        if event in self._subscribers:
            self._subscribers[event] = [cb for cb in self._subscribers[event] if cb != callback]

    def fetch_historical(self, symbol: str, limit: int = 100) -> List[Dict]:
        if self._engine and hasattr(self._engine, "fetch_ohlcv"):
            try:
                raw = self._engine.fetch_ohlcv(symbol, self.config.timeframe, limit=limit)
                candles = []
                for c in raw:
                    candles.append({
                        "timestamp": datetime.fromtimestamp(c[0] / 1000).isoformat() if c[0] > 1e12 else str(c[0]),
                        "open": c[1], "high": c[2], "low": c[3], "close": c[4], "volume": c[5] if len(c) > 5 else 0,
                    })
                self._candle_cache[symbol] = candles
                return candles
            except Exception as e:
                logger.error(f"fetch_historical error: {e}")
        return self._candle_cache.get(symbol, [])[-limit:]

    def get_status(self) -> Dict:
        return {
            "state": self._state.value,
            "symbols": self.config.symbols,
            "total_ticks": self._tick_counter,
            "total_bars": self._bar_counter,
            "candles_cached": {sym: len(c) for sym, c in self._candle_cache.items()},
            "subscribers": {k: len(v) for k, v in self._subscribers.items()},
        }
