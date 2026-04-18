"""Order Models for Smart Order Router."""
import logging, uuid, time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional
logger = logging.getLogger(__name__)
class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"
class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    TWAP = "TWAP"
    VWAP = "VWAP"
class OrderStatus(Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
class ChildOrderStatus(Enum):
    WAITING = "WAITING"
    ACTIVE = "ACTIVE"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
@dataclass
class OrderRequest:
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: Optional[float] = None
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: str = "GTC"
    engine_type: str = "directional"
    client_order_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    created_at: float = field(default_factory=time.time)
    metadata: Dict = field(default_factory=dict)
    @property
    def notional(self) -> float:
        return self.price * self.quantity if self.price else 0.0
    @property
    def is_buy(self) -> bool:
        return self.side == OrderSide.BUY
@dataclass
class ExecutionFill:
    fill_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    child_order_id: str = ""
    price: float = 0.0
    quantity: float = 0.0
    commission: float = 0.0
    slippage_bps: float = 0.0
    timestamp: float = field(default_factory=time.time)
    venue: str = "default"
@dataclass
class ChildOrder:
    parent_id: str
    child_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    order_type: OrderType = OrderType.MARKET
    quantity: float = 0.0
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    target_price: Optional[float] = None
    status: ChildOrderStatus = ChildOrderStatus.WAITING
    scheduled_time: Optional[float] = None
    slice_index: int = 0
    total_slices: int = 1
    fills: List[ExecutionFill] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    @property
    def remaining(self) -> float:
        return max(0.0, self.quantity - self.filled_quantity)
    @property
    def fill_pct(self) -> float:
        return (self.filled_quantity / self.quantity * 100.0) if self.quantity > 0 else 0.0
    @property
    def is_complete(self) -> bool:
        return self.filled_quantity >= self.quantity
    def add_fill(self, fill: ExecutionFill) -> None:
        fill.child_order_id = self.child_id
        self.fills.append(fill)
        self.filled_quantity += fill.quantity
        if self.filled_quantity > 0:
            total_notional = sum(f.price * f.quantity for f in self.fills)
            self.avg_fill_price = total_notional / self.filled_quantity
        if self.is_complete:
            self.status = ChildOrderStatus.FILLED
        else:
            self.status = ChildOrderStatus.PARTIAL
@dataclass
class ExecutionReport:
    parent_id: str = ""
    symbol: str = ""
    side: str = ""
    algorithm: str = "none"
    requested_quantity: float = 0.0
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    benchmark_price: float = 0.0
    total_commission: float = 0.0
    total_slippage_bps: float = 0.0
    child_count: int = 0
    child_orders: List[ChildOrder] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0
    status: str = "PENDING"
    @property
    def fill_pct(self) -> float:
        return (self.filled_quantity / self.requested_quantity * 100.0) if self.requested_quantity > 0 else 0.0
    @property
    def implementation_shortfall_bps(self) -> float:
        if self.benchmark_price <= 0 or self.avg_fill_price <= 0:
            return 0.0
        raw = (self.avg_fill_price - self.benchmark_price) / self.benchmark_price * 10000
        if self.side == "SELL":
            raw = -raw
        return raw
    @property
    def total_notional(self) -> float:
        return self.avg_fill_price * self.filled_quantity
    @property
    def duration_secs(self) -> float:
        if self.end_time > 0 and self.start_time > 0:
            return self.end_time - self.start_time
        return 0.0
    @property
    def is_complete(self) -> bool:
        return self.filled_quantity >= self.requested_quantity
    def to_dict(self) -> dict:
        return {
            "parent_id": self.parent_id, "symbol": self.symbol, "side": self.side,
            "algorithm": self.algorithm, "requested_quantity": self.requested_quantity,
            "filled_quantity": round(self.filled_quantity, 6),
            "fill_pct": round(self.fill_pct, 2),
            "avg_fill_price": round(self.avg_fill_price, 2),
            "benchmark_price": round(self.benchmark_price, 2),
            "slippage_bps": round(self.total_slippage_bps, 2),
            "implementation_shortfall_bps": round(self.implementation_shortfall_bps, 2),
            "total_commission": round(self.total_commission, 4),
            "total_notional": round(self.total_notional, 2),
            "child_count": self.child_count,
            "duration_secs": round(self.duration_secs, 2), "status": self.status,
        }
