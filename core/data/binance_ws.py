import json, threading, time, logging
from typing import Callable, Optional
import websocket
from config.settings import settings
logger = logging.getLogger(__name__)

class BinanceFeed:
    def __init__(self, symbol=None, on_price=None):
        self.symbol = symbol or settings.binance.symbol
        self.url = f"{settings.binance.ws_base}/{self.symbol}@trade"
        self.price = None
        self.history = []
        self.max_history = 500
        self._on_price = on_price
        self._ws = None
        self._running = False
        self._lock = threading.Lock()

    def get_price(self):
        with self._lock: return self.price

    def get_history(self):
        with self._lock: return list(self.history)

    def start(self):
        if self._running: return
        self._running = True
        self._ws = websocket.WebSocketApp(self.url, on_open=self._on_open, on_message=self._on_message, on_error=self._on_error, on_close=self._on_close)
        threading.Thread(target=self._ws.run_forever, daemon=True).start()
        logger.info("BinanceFeed started  symbol=%s", self.symbol)

    def stop(self):
        self._running = False
        if self._ws: self._ws.close()

    def _on_open(self, ws): logger.info("Connected to Binance stream for %s", self.symbol)
    def _on_error(self, ws, error): logger.error("WebSocket error: %s", error)
    def _on_close(self, ws, cs, cm):
        logger.info("Disconnected from Binance stream")
        if self._running:
            time.sleep(3); self.start()

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            price = float(data["p"])
            with self._lock:
                self.price = price
                self.history.append(price)
                if len(self.history) > self.max_history: self.history = self.history[-self.max_history:]
            if self._on_price: self._on_price(price)
        except Exception as exc: logger.warning("Bad trade message: %s", exc)