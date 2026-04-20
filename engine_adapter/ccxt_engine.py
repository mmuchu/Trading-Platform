import logging
import time
from typing import Dict, List, Optional, Any
from datetime import datetime

from .base_engine import BaseEngine, EngineConfig, Ticker, Balance, OrderBook, OrderBookEntry, ConnectionStatus

logger = logging.getLogger(__name__)


class CCXTEngine(BaseEngine):

    def __init__(self, config: EngineConfig):
        super().__init__(config)
        self._exchange = None
        self._markets = None
        self._initialized_markets = False

    def connect(self) -> bool:
        self._status = ConnectionStatus.CONNECTING
        try:
            import ccxt
            exchange_class = getattr(ccxt, self.config.exchange_id.lower(), None)
            if not exchange_class:
                available = [e for e in dir(ccxt) if not e.startswith("_") and e[0].islower()]
                self.logger.error(f"Exchange '{self.config.exchange_id}' not found. Available: {available}")
                self._status = ConnectionStatus.ERROR
                return False
            ex_config = {
                "apiKey": self.config.api_key,
                "secret": self.config.api_secret,
                "enableRateLimit": True,
                "timeout": self.config.timeout_seconds * 1000,
                "options": {"defaultType": "spot"},
            }
            if self.config.passphrase:
                ex_config["password"] = self.config.passphrase
            if self.config.sandbox or self.config.testnet_url:
                ex_config["sandbox"] = True
                if self.config.testnet_url:
                    ex_config["urls"] = {"api": {"public": self.config.testnet_url, "private": self.config.testnet_url}}
            self._exchange = exchange_class(ex_config)
            if self.config.api_key:
                self._exchange.load_markets()
                self._markets = self._exchange.markets
                self._initialized_markets = True
            self._status = ConnectionStatus.CONNECTED
            self.logger.info(f"Connected to {self.config.exchange_id} (sandbox={self.config.sandbox})")
            return True
        except ImportError:
            self.logger.error("ccxt not installed. Run: pip install ccxt")
            self._status = ConnectionStatus.ERROR
            return False
        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            self._status = ConnectionStatus.ERROR
            return False

    def disconnect(self) -> bool:
        try:
            if self._exchange:
                self._exchange.close()
            self._exchange = None
            self._status = ConnectionStatus.DISCONNECTED
            self.logger.info("Disconnected")
            return True
        except Exception as e:
            self.logger.error(f"Disconnect error: {e}")
            return False

    def _retry(self, func, *args, **kwargs):
        last_error = None
        for attempt in range(self.config.max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < self.config.max_retries - 1:
                    time.sleep(self.config.retry_delay_ms / 1000)
        raise last_error

    def fetch_ticker(self, symbol: str) -> Optional[Ticker]:
        if not self._exchange:
            return None
        try:
            data = self._retry(self._exchange.fetch_ticker, symbol)
            ticker = Ticker(
                symbol=symbol, bid=data.get("bid", 0), ask=data.get("ask", 0),
                last=data.get("last", 0), high=data.get("high", 0),
                low=data.get("low", 0), volume=data.get("baseVolume", data.get("quoteVolume", 0)),
            )
            self._tickers[symbol] = ticker
            return ticker
        except Exception as e:
            self.logger.error(f"fetch_ticker error for {symbol}: {e}")
            return None

    def fetch_balance(self) -> Dict[str, Balance]:
        if not self._exchange:
            return {}
        try:
            data = self._retry(self._exchange.fetch_balance)
            self._balances.clear()
            for asset, info in data.get("total", {}).items():
                if info and info > 0:
                    self._balances[asset] = Balance(
                        asset=asset,
                        free=data.get("free", {}).get(asset, 0),
                        used=data.get("used", {}).get(asset, 0),
                        total=info,
                    )
            return dict(self._balances)
        except Exception as e:
            self.logger.error(f"fetch_balance error: {e}")
            return {}

    def fetch_order_book(self, symbol: str, limit: int = 20) -> Optional[OrderBook]:
        if not self._exchange:
            return None
        try:
            data = self._retry(self._exchange.fetch_order_book, symbol, limit)
            bids = [OrderBookEntry(price=b[0], quantity=b[1]) for b in data.get("bids", [])]
            asks = [OrderBookEntry(price=a[0], quantity=a[1]) for a in data.get("asks", [])]
            return OrderBook(symbol=symbol, bids=bids, asks=asks)
        except Exception as e:
            self.logger.error(f"fetch_order_book error: {e}")
            return None

    def create_market_order(self, symbol: str, side: str, quantity: float) -> Optional[Dict]:
        if not self._exchange:
            return None
        try:
            result = self._retry(self._exchange.create_market_order, symbol, side, quantity)
            self.logger.info(f"Market order: {side} {quantity} {symbol} -> {result.get('id', 'unknown')}")
            return self._normalize_order(result)
        except Exception as e:
            self.logger.error(f"create_market_order error: {e}")
            return {"error": str(e)}

    def create_limit_order(self, symbol: str, side: str, quantity: float, price: float) -> Optional[Dict]:
        if not self._exchange:
            return None
        try:
            result = self._retry(self._exchange.create_limit_order, symbol, side, quantity, price)
            self.logger.info(f"Limit order: {side} {quantity} {symbol} @ {price} -> {result.get('id', 'unknown')}")
            return self._normalize_order(result)
        except Exception as e:
            self.logger.error(f"create_limit_order error: {e}")
            return {"error": str(e)}

    def cancel_order(self, order_id: str, symbol: str) -> bool:
        if not self._exchange:
            return False
        try:
            self._retry(self._exchange.cancel_order, order_id, symbol)
            self.logger.info(f"Order cancelled: {order_id}")
            return True
        except Exception as e:
            self.logger.error(f"cancel_order error: {e}")
            return False

    def fetch_open_orders(self, symbol: str = "") -> List[Dict]:
        if not self._exchange:
            return []
        try:
            if symbol:
                data = self._retry(self._exchange.fetch_open_orders, symbol)
            else:
                data = self._retry(self._exchange.fetch_open_orders)
            return [self._normalize_order(o) for o in data]
        except Exception as e:
            self.logger.error(f"fetch_open_orders error: {e}")
            return []

    def fetch_my_trades(self, symbol: str = "", limit: int = 50) -> List[Dict]:
        if not self._exchange:
            return []
        try:
            params = {"limit": limit}
            if symbol:
                data = self._retry(self._exchange.fetch_my_trades, symbol, params=params)
            else:
                data = self._retry(self._exchange.fetch_my_trades, params=params)
            return [{
                "id": t.get("id"), "symbol": t.get("symbol"), "side": t.get("side"),
                "price": t.get("price"), "quantity": t.get("amount"),
                "cost": t.get("cost"), "fee": t.get("fee"),
                "timestamp": t.get("datetime", ""),
            } for t in data]
        except Exception as e:
            self.logger.error(f"fetch_my_trades error: {e}")
            return []

    def _normalize_order(self, order: Dict) -> Dict:
        return {
            "id": order.get("id"), "symbol": order.get("symbol"),
            "side": order.get("side"), "type": order.get("type"),
            "price": order.get("price", 0), "quantity": order.get("amount", 0),
            "filled": order.get("filled", 0), "remaining": order.get("remaining", 0),
            "status": order.get("status"), "timestamp": order.get("datetime", ""),
            "fee": order.get("fee"),
        }

    def get_status(self) -> ConnectionStatus:
        return self._status
