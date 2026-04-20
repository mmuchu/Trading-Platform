import logging
import random
import time
from typing import Dict, List, Optional
from datetime import datetime

from .base_engine import BaseEngine, EngineConfig, Ticker, Balance, OrderBook, OrderBookEntry, ConnectionStatus

logger = logging.getLogger(__name__)


class PaperEngine(BaseEngine):

    def __init__(self, config: EngineConfig = None, initial_balance: Dict[str, float] = None):
        config = config or EngineConfig(engine_type=self._get_type())
        super().__init__(config)
        self._paper_balances: Dict[str, Balance] = {}
        self._paper_orders: List[Dict] = []
        self._paper_trades: List[Dict] = []
        self._paper_tickers: Dict[str, Ticker] = {}
        self._order_counter = 0
        self._slippage_pct = 0.05
        self._commission_rate = 0.001
        if initial_balance:
            for asset, amount in initial_balance.items():
                self._paper_balances[asset] = Balance(asset=asset, free=amount, used=0, total=amount)
        self._setup_default_tickers()
        self.logger.info(f"PaperEngine ready (balance: {self._format_balances()})")

    @staticmethod
    def _get_type():
        from .base_engine import EngineType
        return EngineType.PAPER

    def _setup_default_tickers(self):
        defaults = {
            "BTC/USDT": 50000.0, "ETH/USDT": 3000.0, "SOL/USDT": 100.0,
            "BNB/USDT": 300.0, "ADA/USDT": 0.5, "DOT/USDT": 7.0,
            "AVAX/USDT": 20.0, "MATIC/USDT": 0.8, "LINK/USDT": 15.0,
            "XRP/USDT": 0.6,
        }
        for sym, price in defaults.items():
            spread = price * 0.001
            self._paper_tickers[sym] = Ticker(
                symbol=sym, bid=price - spread / 2, ask=price + spread / 2,
                last=price, high=price * 1.02, low=price * 0.98, volume=random.uniform(100, 10000),
            )

    def set_ticker(self, symbol: str, price: float):
        spread = price * 0.001
        self._paper_tickers[symbol] = Ticker(
            symbol=symbol, bid=price - spread / 2, ask=price + spread / 2,
            last=price, high=price * 1.01, low=price * 0.99,
            volume=random.uniform(100, 5000),
        )

    def set_balance(self, asset: str, amount: float):
        existing = self._paper_balances.get(asset)
        self._paper_balances[asset] = Balance(
            asset=asset, free=amount,
            used=existing.used if existing else 0,
            total=amount + (existing.used if existing else 0),
        )

    def connect(self) -> bool:
        self._status = ConnectionStatus.CONNECTED
        self.logger.info("PaperEngine connected")
        return True

    def disconnect(self) -> bool:
        self._status = ConnectionStatus.DISCONNECTED
        self.logger.info("PaperEngine disconnected")
        return True

    def fetch_ticker(self, symbol: str) -> Optional[Ticker]:
        ticker = self._paper_tickers.get(symbol)
        if ticker:
            jitter = random.uniform(-0.0005, 0.0005)
            new_last = ticker.last * (1 + jitter)
            spread = new_last * 0.001
            updated = Ticker(
                symbol=symbol, bid=new_last - spread / 2, ask=new_last + spread / 2,
                last=round(new_last, 8), high=max(ticker.high, new_last),
                low=min(ticker.low, new_last), volume=ticker.volume + random.uniform(0, 10),
            )
            self._paper_tickers[symbol] = updated
            self._tickers[symbol] = updated
            return updated
        self.logger.warning(f"No paper ticker for {symbol}")
        return None

    def fetch_balance(self) -> Dict[str, Balance]:
        self._balances = dict(self._paper_balances)
        return dict(self._paper_balances)

    def fetch_order_book(self, symbol: str, limit: int = 20) -> Optional[OrderBook]:
        ticker = self._paper_tickers.get(symbol)
        if not ticker:
            return None
        bids, asks = [], []
        for i in range(limit):
            bid_price = ticker.bid * (1 - i * 0.0002)
            ask_price = ticker.ask * (1 + i * 0.0002)
            bids.append(OrderBookEntry(price=round(bid_price, 8), quantity=round(random.uniform(0.1, 5), 4)))
            asks.append(OrderBookEntry(price=round(ask_price, 8), quantity=round(random.uniform(0.1, 5), 4)))
        return OrderBook(symbol=symbol, bids=bids, asks=asks)

    def create_market_order(self, symbol: str, side: str, quantity: float) -> Optional[Dict]:
        if not self._check_balance(symbol, side, quantity):
            return {"error": "Insufficient balance"}
        ticker = self._paper_tickers.get(symbol)
        if not ticker:
            return {"error": f"No ticker for {symbol}"}
        slippage = 1 + (self._slippage_pct / 100 * (1 if side == "buy" else -1))
        fill_price = ticker.ask * slippage if side == "buy" else ticker.bid * slippage
        fill_price = round(fill_price, 8)
        commission = fill_price * quantity * self._commission_rate
        self._update_balance(symbol, side, quantity, fill_price, commission)
        self._order_counter += 1
        order = {
            "id": f"PAPER-{self._order_counter:06d}", "symbol": symbol, "side": side,
            "type": "market", "price": fill_price, "quantity": quantity,
            "filled": quantity, "remaining": 0, "status": "closed",
            "timestamp": datetime.now().isoformat(), "commission": round(commission, 8),
        }
        self._paper_orders.append(order)
        trade = {"order_id": order["id"], "symbol": symbol, "side": side,
                 "price": fill_price, "quantity": quantity, "commission": commission,
                 "timestamp": datetime.now().isoformat()}
        self._paper_trades.append(trade)
        self.logger.info(f"[PAPER] Market order: {side} {quantity} {symbol} @ {fill_price}")
        return order

    def create_limit_order(self, symbol: str, side: str, quantity: float, price: float) -> Optional[Dict]:
        if not self._check_balance(symbol, side, quantity):
            return {"error": "Insufficient balance"}
        self._order_counter += 1
        order = {
            "id": f"PAPER-{self._order_counter:06d}", "symbol": symbol, "side": side,
            "type": "limit", "price": price, "quantity": quantity,
            "filled": 0, "remaining": quantity, "status": "open",
            "timestamp": datetime.now().isoformat(), "commission": 0,
        }
        self._paper_orders.append(order)
        self.logger.info(f"[PAPER] Limit order placed: {side} {quantity} {symbol} @ {price}")
        return order

    def cancel_order(self, order_id: str, symbol: str) -> bool:
        for o in self._paper_orders:
            if o["id"] == order_id and o["status"] == "open":
                o["status"] = "canceled"
                self.logger.info(f"[PAPER] Order cancelled: {order_id}")
                return True
        return False

    def fetch_open_orders(self, symbol: str = "") -> List[Dict]:
        orders = [o for o in self._paper_orders if o["status"] == "open"]
        if symbol:
            orders = [o for o in orders if o["symbol"] == symbol]
        return orders

    def fetch_my_trades(self, symbol: str = "", limit: int = 50) -> List[Dict]:
        trades = self._paper_trades[-limit:]
        if symbol:
            trades = [t for t in trades if t["symbol"] == symbol]
        return trades

    def simulate_fill(self, order_id: str, fill_price: float = 0):
        for o in self._paper_orders:
            if o["id"] == order_id and o["status"] == "open":
                fp = fill_price or o["price"]
                commission = fp * o["quantity"] * self._commission_rate
                self._update_balance(o["symbol"], o["side"], o["quantity"], fp, commission)
                o["filled"] = o["quantity"]
                o["remaining"] = 0
                o["status"] = "closed"
                o["commission"] = round(commission, 8)
                self._paper_trades.append({
                    "order_id": order_id, "symbol": o["symbol"], "side": o["side"],
                    "price": fp, "quantity": o["quantity"], "commission": commission,
                    "timestamp": datetime.now().isoformat(),
                })
                self.logger.info(f"[PAPER] Order filled: {order_id} @ {fp}")
                return

    def _check_balance(self, symbol: str, side: str, quantity: float) -> bool:
        if side == "buy":
            quote = symbol.split("/")[1] if "/" in symbol else "USDT"
            cost = quantity * (self._paper_tickers.get(symbol, Ticker(symbol, 0, 0, 0, 0, 0, 0)).ask if symbol in self._paper_tickers else 0)
            bal = self._paper_balances.get(quote)
            return bal and bal.free >= cost
        else:
            base = symbol.split("/")[0] if "/" in symbol else symbol
            bal = self._paper_balances.get(base)
            return bal and bal.free >= quantity
        return False

    def _update_balance(self, symbol: str, side: str, quantity: float, price: float, commission: float):
        parts = symbol.split("/") if "/" in symbol else [symbol, "USDT"]
        base, quote = parts[0], parts[1]
        if side == "buy":
            self._adj_balance(base, quantity, add=True)
            self._adj_balance(quote, quantity * price + commission, add=False)
        else:
            self._adj_balance(base, quantity, add=False)
            self._adj_balance(quote, quantity * price - commission, add=True)

    def _adj_balance(self, asset: str, amount: float, add: bool):
        bal = self._paper_balances.get(asset)
        if not bal:
            self._paper_balances[asset] = Balance(asset=asset, free=amount if add else 0, used=0, total=amount if add else 0)
        else:
            if add:
                bal.free = round(bal.free + amount, 8)
            else:
                bal.free = max(0, round(bal.free - amount, 8))
            bal.total = round(bal.free + bal.used, 8)

    def _format_balances(self) -> str:
        return ", ".join(f"{a}:{b.free:.2f}" for a, b in self._paper_balances.items() if b.total > 0)
