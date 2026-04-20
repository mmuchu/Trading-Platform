import logging
import time
from typing import Dict, List, Optional, Callable, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class ExecutionType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"


class ExecutionStatus(Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass
class ExecutionRequest:
    request_id: str
    symbol: str
    side: str
    exec_type: ExecutionType
    quantity: float
    price: float = 0.0
    stop_price: float = 0.0
    time_in_force: str = "GTC"
    reduce_only: bool = False
    metadata: Dict = field(default_factory=dict)


@dataclass
class ExecutionResult:
    request_id: str
    symbol: str
    side: str
    exec_type: ExecutionType
    status: ExecutionStatus
    order_id: str = ""
    filled_quantity: float = 0.0
    filled_price: float = 0.0
    avg_price: float = 0.0
    commission: float = 0.0
    message: str = ""
    timestamp: str = ""
    latency_ms: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class ExecutorConfig:
    default_type: ExecutionType = ExecutionType.MARKET
    slippage_pct: float = 0.1
    max_slippage_pct: float = 1.0
    timeout_seconds: float = 30.0
    retry_on_failure: bool = True
    max_retries: int = 3
    retry_delay_ms: int = 500
    validate_before_submit: bool = True
    partial_fill_wait_seconds: float = 5.0


class OrderExecutor:

    def __init__(self, engine=None, config: ExecutorConfig = None):
        self._engine = engine
        self.config = config or ExecutorConfig()
        self._pending: Dict[str, ExecutionRequest] = {}
        self._results: List[ExecutionResult] = []
        self._counter = 0
        self._pre_submit_hooks: List[Callable] = []
        self._post_fill_hooks: List[Callable] = []
        self._total_commission = 0.0
        logger.info(f"OrderExecutor initialized (type={self.config.default_type.value})")

    def add_pre_submit_hook(self, hook: Callable):
        self._pre_submit_hooks.append(hook)

    def add_post_fill_hook(self, hook: Callable):
        self._post_fill_hooks.append(hook)

    def execute(self, symbol: str, side: str, quantity: float, exec_type: ExecutionType = None, price: float = 0.0, stop_price: float = 0.0, metadata: Dict = None) -> ExecutionResult:
        self._counter += 1
        req = ExecutionRequest(
            request_id=f"EXE-{self._counter:06d}", symbol=symbol, side=side,
            exec_type=exec_type or self.config.default_type, quantity=quantity,
            price=price, stop_price=stop_price, metadata=metadata or {},
        )
        if self.config.validate_before_submit:
            valid, reason = self._validate(req)
            if not valid:
                result = ExecutionResult(
                    request_id=req.request_id, symbol=symbol, side=side,
                    exec_type=req.exec_type, status=ExecutionStatus.FAILED, message=reason,
                )
                self._results.append(result)
                return result
        for hook in self._pre_submit_hooks:
            try:
                modified = hook(req)
                if modified is False:
                    result = ExecutionResult(
                        request_id=req.request_id, symbol=symbol, side=side,
                        exec_type=req.exec_type, status=ExecutionStatus.CANCELLED, message="Rejected by pre-submit hook",
                    )
                    self._results.append(result)
                    return result
            except Exception as e:
                logger.error(f"Pre-submit hook error: {e}")
        self._pending[req.request_id] = req
        result = self._submit(req)
        self._results.append(result)
        if result.status == ExecutionStatus.FILLED and result.commission > 0:
            self._total_commission += result.commission
        for hook in self._post_fill_hooks:
            try:
                hook(result)
            except Exception as e:
                logger.error(f"Post-fill hook error: {e}")
        self._pending.pop(req.request_id, None)
        return result

    def _validate(self, req: ExecutionRequest) -> Tuple[bool, str]:
        if not req.symbol:
            return False, "Symbol is required"
        if req.quantity <= 0:
            return False, "Quantity must be positive"
        if req.side not in ("buy", "sell"):
            return False, "Side must be 'buy' or 'sell'"
        if req.exec_type in (ExecutionType.LIMIT, ExecutionType.STOP_LIMIT) and req.price <= 0:
            return False, "Price required for limit orders"
        if req.exec_type in (ExecutionType.STOP_MARKET, ExecutionType.STOP_LIMIT) and req.stop_price <= 0:
            return False, "Stop price required for stop orders"
        return True, "OK"

    def _submit(self, req: ExecutionRequest) -> ExecutionResult:
        start_time = time.time()
        if not self._engine:
            return ExecutionResult(
                request_id=req.request_id, symbol=req.symbol, side=req.side,
                exec_type=req.exec_type, status=ExecutionStatus.FAILED,
                message="No engine connected", latency_ms=0,
            )
        last_error = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                if req.exec_type == ExecutionType.MARKET:
                    order = self._engine.create_market_order(req.symbol, req.side, req.quantity)
                elif req.exec_type == ExecutionType.LIMIT:
                    order = self._engine.create_limit_order(req.symbol, req.side, req.quantity, req.price)
                elif req.exec_type == ExecutionType.STOP_MARKET:
                    order = self._engine.create_market_order(req.symbol, req.side, req.quantity)
                elif req.exec_type == ExecutionType.STOP_LIMIT:
                    order = self._engine.create_limit_order(req.symbol, req.side, req.quantity, req.stop_price or req.price)
                else:
                    order = self._engine.create_market_order(req.symbol, req.side, req.quantity)
                if order and order.get("error"):
                    last_error = order["error"]
                    if attempt < self.config.max_retries and self.config.retry_on_failure:
                        time.sleep(self.config.retry_delay_ms / 1000)
                        continue
                    return ExecutionResult(
                        request_id=req.request_id, symbol=req.symbol, side=req.side,
                        exec_type=req.exec_type, status=ExecutionStatus.FAILED,
                        message=last_error, latency_ms=round((time.time() - start_time) * 1000),
                    )
                latency = round((time.time() - start_time) * 1000)
                filled_qty = order.get("filled", order.get("quantity", 0)) if order else 0
                filled_price = order.get("price", 0) if order else 0
                commission = order.get("commission", filled_price * filled_qty * 0.001) if order else 0
                status = ExecutionStatus.FILLED if filled_qty >= req.quantity else ExecutionStatus.PARTIAL
                logger.info(f"Executed: {req.side} {filled_qty} {req.symbol} @ {filled_price} ({latency}ms)")
                return ExecutionResult(
                    request_id=req.request_id, symbol=req.symbol, side=req.side,
                    exec_type=req.exec_type, status=status,
                    order_id=order.get("id", "") if order else "",
                    filled_quantity=filled_qty, filled_price=filled_price,
                    avg_price=filled_price, commission=commission,
                    latency_ms=latency,
                )
            except Exception as e:
                last_error = str(e)
                logger.error(f"Execution attempt {attempt} failed: {e}")
                if attempt < self.config.max_retries and self.config.retry_on_failure:
                    time.sleep(self.config.retry_delay_ms / 1000)
        return ExecutionResult(
            request_id=req.request_id, symbol=req.symbol, side=req.side,
            exec_type=req.exec_type, status=ExecutionStatus.FAILED,
            message=f"All retries failed: {last_error}",
            latency_ms=round((time.time() - start_time) * 1000),
        )

    def cancel(self, order_id: str, symbol: str = "") -> bool:
        if not self._engine:
            return False
        return self._engine.cancel_order(order_id, symbol)

    def get_result(self, request_id: str) -> Optional[ExecutionResult]:
        for r in self._results:
            if r.request_id == request_id:
                return r
        return None

    def get_results(self, symbol: str = "", limit: int = 50) -> List[ExecutionResult]:
        results = self._results
        if symbol:
            results = [r for r in results if r.symbol == symbol]
        return results[-limit:]

    def get_total_commission(self) -> float:
        return round(self._total_commission, 2)

    def get_pending_count(self) -> int:
        return len(self._pending)

    def get_stats(self) -> Dict:
        total = len(self._results)
        filled = sum(1 for r in self._results if r.status == ExecutionStatus.FILLED)
        failed = sum(1 for r in self._results if r.status == ExecutionStatus.FAILED)
        latencies = [r.latency_ms for r in self._results if r.latency_ms > 0]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        return {
            "total_executions": total, "filled": filled, "failed": failed,
            "success_rate": round(filled / total, 4) if total > 0 else 0,
            "avg_latency_ms": round(avg_latency, 2),
            "total_commission": self.get_total_commission(),
            "pending": len(self._pending),
        }

    def get_status(self) -> Dict:
        return self.get_stats()
