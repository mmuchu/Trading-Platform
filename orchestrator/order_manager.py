import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TRAILING_STOP = "trailing_stop"


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass
class Order:
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float = 0.0
    stop_price: float = 0.0
    filled_quantity: float = 0.0
    filled_price: float = 0.0
    avg_fill_price: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    signal_id: str = ""
    strategy_name: str = ""
    commission: float = 0.0
    timestamp: str = ""
    filled_timestamp: str = ""
    error_message: str = ""
    metadata: Dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class OrderResult:
    success: bool
    order_id: str
    filled_quantity: float
    filled_price: float
    message: str
    order: Optional[Order] = None


class OrderManager:

    def __init__(self):
        self._orders: Dict[str, Order] = {}
        self._open_orders: Dict[str, Order] = {}
        self._counter = 0
        self._submit_callback: Optional[callable] = None
        self._cancel_callback: Optional[callable] = None
        logger.info("OrderManager initialized")

    def set_submit_callback(self, callback: callable):
        self._submit_callback = callback

    def set_cancel_callback(self, callback: callable):
        self._cancel_callback = callback

    def create_order(self, symbol: str, side: OrderSide, order_type: OrderType, quantity: float, price: float = 0.0, stop_price: float = 0.0, signal_id: str = "", strategy_name: str = "", metadata: Dict = None) -> Order:
        self._counter += 1
        order = Order(
            order_id=f"ORD-{self._counter:06d}",
            symbol=symbol, side=side, order_type=order_type,
            quantity=quantity, price=price, stop_price=stop_price,
            signal_id=signal_id, strategy_name=strategy_name,
            metadata=metadata or {},
        )
        self._orders[order.order_id] = order
        self._open_orders[order.order_id] = order
        logger.info(f"Order created: {order.order_id} {side.value} {quantity} {symbol} @ {price or 'MARKET'}")
        return order

    def submit_order(self, order_id: str) -> OrderResult:
        order = self._orders.get(order_id)
        if not order:
            return OrderResult(False, order_id, 0, 0, "Order not found")
        if order.status not in (OrderStatus.PENDING,):
            return OrderResult(False, order_id, 0, 0, f"Cannot submit order in {order.status.value} status")
        order.status = OrderStatus.SUBMITTED
        if self._submit_callback:
            try:
                result = self._submit_callback(order)
                if result.success:
                    order.status = OrderStatus.FILLED
                    order.filled_quantity = result.filled_quantity
                    order.filled_price = result.filled_price
                    order.avg_fill_price = result.filled_price
                    order.filled_timestamp = datetime.now().isoformat()
                    order.commission = result.filled_quantity * result.filled_price * 0.001
                    del self._open_orders[order_id]
                    logger.info(f"Order filled: {order_id} qty={result.filled_quantity} @ {result.filled_price}")
                    return result
                else:
                    order.status = OrderStatus.REJECTED
                    order.error_message = result.message
                    del self._open_orders[order_id]
                    return result
            except Exception as e:
                order.status = OrderStatus.FAILED
                order.error_message = str(e)
                del self._open_orders[order_id]
                return OrderResult(False, order_id, 0, 0, str(e))
        order.status = OrderStatus.SUBMITTED
        logger.info(f"Order submitted (no callback): {order_id}")
        return OrderResult(True, order_id, 0, 0, "Submitted (pending fill)")

    def cancel_order(self, order_id: str) -> OrderResult:
        order = self._open_orders.get(order_id)
        if not order:
            return OrderResult(False, order_id, 0, 0, "Open order not found")
        if self._cancel_callback:
            try:
                result = self._cancel_callback(order)
            except Exception as e:
                logger.error(f"Cancel callback error: {e}")
        order.status = OrderStatus.CANCELLED
        self._open_orders.pop(order_id, None)
        logger.info(f"Order cancelled: {order_id}")
        return OrderResult(True, order_id, order.filled_quantity, order.avg_fill_price, "Cancelled")

    def cancel_all_orders(self, symbol: str = "") -> int:
        to_cancel = []
        for oid, o in list(self._open_orders.items()):
            if not symbol or o.symbol == symbol:
                to_cancel.append(oid)
        for oid in to_cancel:
            self.cancel_order(oid)
        return len(to_cancel)

    def update_fill(self, order_id: str, filled_qty: float, filled_price: float):
        order = self._orders.get(order_id)
        if not order:
            return
        total_cost = order.avg_fill_price * order.filled_quantity + filled_price * filled_qty
        order.filled_quantity += filled_qty
        order.avg_fill_price = total_cost / order.filled_quantity if order.filled_quantity > 0 else 0
        if order.filled_quantity >= order.quantity:
            order.status = OrderStatus.FILLED
            order.filled_timestamp = datetime.now().isoformat()
            self._open_orders.pop(order_id, None)
            logger.info(f"Order fully filled: {order_id} avg={order.avg_fill_price:.2f}")
        else:
            order.status = OrderStatus.PARTIAL
            logger.info(f"Order partial fill: {order_id} {order.filled_quantity}/{order.quantity}")

    def get_order(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)

    def get_open_orders(self, symbol: str = "") -> List[Order]:
        orders = list(self._open_orders.values())
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        return orders

    def get_filled_orders(self, symbol: str = "", limit: int = 100) -> List[Order]:
        orders = [o for o in self._orders.values() if o.status == OrderStatus.FILLED]
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        return sorted(orders, key=lambda o: o.filled_timestamp or o.timestamp, reverse=True)[:limit]

    def get_total_commission(self) -> float:
        return round(sum(o.commission for o in self._orders.values()), 2)

    def get_status(self) -> Dict:
        by_status = {}
        for o in self._orders.values():
            by_status[o.status.value] = by_status.get(o.status.value, 0) + 1
        return {
            "total_orders": len(self._orders),
            "open_orders": len(self._open_orders),
            "by_status": by_status,
            "total_commission": self.get_total_commission(),
        }
