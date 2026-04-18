"""Smart Order Router - TWAP/VWAP/slice execution algorithms."""
import logging, math, threading, time, random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from core.execution.order import (
    OrderRequest, OrderSide, OrderType,
    ChildOrder, ChildOrderStatus, ExecutionFill, ExecutionReport,
)
from config.settings import settings
logger = logging.getLogger(__name__)
class SplitAlgorithm:
    TWAP = "twap"
    VWAP = "vwap"
    SLICE = "slice"
    PASS = "pass"
@dataclass
class RouterConfig:
    default_algorithm: str = "slice"
    max_child_orders: int = 10
    twap_duration_secs: float = 300.0
    twap_interval_secs: float = 30.0
    vwap_duration_secs: float = 600.0
    participation_rate_max: float = 0.1
    min_slice_quantity: float = 0.001
    randomize_timing: bool = True
    cancel_on_drawdown_pct: float = 0.5
class SmartOrderRouter:
    def __init__(self, config=None, broker_execute=None):
        self.config = config or self._load_config()
        self._broker_execute = broker_execute
        self._lock = threading.RLock()
        self._active_orders = {}
        self._reports = []
        self._max_reports = 500
        self._total_routed = 0
        self._total_filled = 0
        self._total_cancelled = 0
        self._total_slippage_bps = 0.0
        self._slippage_count = 0
    def route(self, request, current_price):
        self._total_routed += 1
        algo = self._select_algorithm(request)
        children = self._create_children(request, algo, current_price)
        report = ExecutionReport(
            parent_id=request.client_order_id, symbol=request.symbol,
            side=request.side.value, algorithm=algo,
            requested_quantity=request.quantity, benchmark_price=current_price,
            child_count=len(children), child_orders=children,
            start_time=time.time(), status="ACTIVE",
        )
        self._active_orders[request.client_order_id] = {
            "request": request, "children": children,
            "report": report, "cancel_flag": False, "algo": algo,
        }
        if algo in (SplitAlgorithm.PASS, SplitAlgorithm.SLICE):
            self._execute_immediate(request, children, current_price)
            for child in children:
                for fill in child.fills:
                    self._update_report(report, child, fill)
            report.end_time = time.time()
            report.status = "COMPLETE" if report.is_complete else "PARTIAL"
        elif algo in (SplitAlgorithm.TWAP, SplitAlgorithm.VWAP):
            self._schedule_children(request.client_order_id, children)
        return report
    def get_active_orders(self):
        with self._lock:
            result = []
            for pid, od in self._active_orders.items():
                r = od["report"]
                result.append({"parent_id": pid, "symbol": r.symbol, "side": r.side,
                    "algorithm": r.algorithm, "fill_pct": round(r.fill_pct, 2),
                    "avg_fill_price": round(r.avg_fill_price, 2), "status": r.status,
                    "children_pending": sum(1 for c in r.child_orders
                        if c.status in (ChildOrderStatus.WAITING, ChildOrderStatus.ACTIVE)),
                    "children_total": r.child_count})
            return result
    def get_report(self, parent_id):
        with self._lock:
            if parent_id in self._active_orders:
                return self._active_orders[parent_id]["report"]
            for r in self._reports:
                if r.parent_id == parent_id:
                    return r
        return None
    def get_reports(self, limit=20):
        with self._lock:
            return [r.to_dict() for r in reversed(self._reports[-limit:])]
    def cancel_order(self, parent_id):
        with self._lock:
            if parent_id not in self._active_orders:
                return False
            self._active_orders[parent_id]["cancel_flag"] = True
            od = self._active_orders[parent_id]
            cc = 0
            for child in od["children"]:
                if child.status in (ChildOrderStatus.WAITING, ChildOrderStatus.ACTIVE):
                    child.status = ChildOrderStatus.CANCELLED
                    cc += 1
            report = od["report"]
            report.end_time = time.time()
            report.status = "CANCELLED" if cc == len(report.child_orders) else "PARTIAL"
            self._total_cancelled += cc
            if report.status != "ACTIVE":
                self._archive_report(report)
                del self._active_orders[parent_id]
            return True
    def tick(self, current_prices):
        now = time.time()
        executed_fills = []
        with self._lock:
            for pid, od in list(self._active_orders.items()):
                if od["cancel_flag"]:
                    continue
                req = od["request"]
                price = current_prices.get(req.symbol, 0)
                if price <= 0:
                    continue
                report = od["report"]
                for child in od["children"]:
                    if child.status != ChildOrderStatus.WAITING:
                        continue
                    if child.scheduled_time and now >= child.scheduled_time:
                        child.status = ChildOrderStatus.ACTIVE
                        if self._check_adverse_price(child, req, price):
                            child.status = ChildOrderStatus.CANCELLED
                            continue
                        fill = self._execute_child(child, req, price)
                        if fill:
                            child.add_fill(fill)
                            executed_fills.append({"parent_id": pid,
                                "child_id": child.child_id,
                                "slice": child.slice_index + 1,
                                "total_slices": child.total_slices,
                                "fill_qty": fill.quantity,
                                "fill_price": fill.price,
                                "slippage_bps": fill.slippage_bps})
                            self._update_report(report, child, fill)
                all_done = all(c.status in (ChildOrderStatus.FILLED, ChildOrderStatus.CANCELLED,
                    ChildOrderStatus.PARTIAL) for c in od["children"])
                if all_done:
                    report.end_time = now
                    report.status = "COMPLETE" if report.is_complete else "PARTIAL"
                    self._archive_report(report)
                    del self._active_orders[pid]
        return executed_fills
    def get_stats(self):
        avg_slip = self._total_slippage_bps / self._slippage_count if self._slippage_count > 0 else 0
        return {"total_orders_routed": self._total_routed,
            "total_orders_completed": self._total_filled,
            "total_orders_cancelled": self._total_cancelled,
            "active_orders": len(self._active_orders),
            "avg_slippage_bps": round(avg_slip, 2),
            "reports_count": len(self._reports)}
    def reset(self):
        with self._lock:
            self._active_orders.clear()
            self._reports.clear()
            self._total_routed = 0
            self._total_filled = 0
            self._total_cancelled = 0
            self._total_slippage_bps = 0
            self._slippage_count = 0
    def _select_algorithm(self, request):
        if request.order_type == OrderType.TWAP:
            return SplitAlgorithm.TWAP
        if request.order_type == OrderType.VWAP:
            return SplitAlgorithm.VWAP
        if request.order_type == OrderType.MARKET:
            return self.config.default_algorithm
        return SplitAlgorithm.PASS
    def _create_children(self, request, algorithm, current_price):
        if algorithm == SplitAlgorithm.PASS:
            return self._create_single_child(request, 0, 1)
        if algorithm == SplitAlgorithm.SLICE:
            return self._create_equal_slices(request)
        if algorithm == SplitAlgorithm.TWAP:
            return self._create_timed_slices(request,
                self.config.twap_duration_secs, self.config.twap_interval_secs)
        if algorithm == SplitAlgorithm.VWAP:
            return self._create_vwap_slices(request, current_price)
        return self._create_single_child(request, 0, 1)
    def _create_single_child(self, request, index, total):
        return [ChildOrder(parent_id=request.client_order_id, symbol=request.symbol,
            side=request.side, order_type=request.order_type, quantity=request.quantity,
            target_price=request.price, slice_index=index, total_slices=total)]
    def _create_equal_slices(self, request):
        n = min(self.config.max_child_orders,
            max(1, int(request.quantity / self.config.min_slice_quantity)))
        qty_per = request.quantity / n
        return [ChildOrder(parent_id=request.client_order_id, symbol=request.symbol,
            side=request.side, order_type=OrderType.MARKET,
            quantity=round(qty_per, 8), target_price=request.price,
            slice_index=i, total_slices=n) for i in range(n)]
    def _create_timed_slices(self, request, duration, interval):
        n = max(1, min(self.config.max_child_orders, int(duration / interval)))
        qty_per = request.quantity / n
        now = time.time()
        children = []
        for i in range(n):
            scheduled = now + (i * (duration / n))
            if self.config.randomize_timing:
                jitter = (duration / n) * 0.1
                scheduled += random.uniform(-jitter, jitter)
            children.append(ChildOrder(parent_id=request.client_order_id,
                symbol=request.symbol, side=request.side, order_type=OrderType.MARKET,
                quantity=round(qty_per, 8), target_price=request.price,
                slice_index=i, total_slices=n, scheduled_time=scheduled))
        return children
    def _create_vwap_slices(self, request, current_price):
        n = min(self.config.max_child_orders,
            max(1, int(self.config.vwap_duration_secs / self.config.twap_interval_secs)))
        weights = [1.0 + 2.0 * (i / max(1, n - 1) - 0.5) ** 2 for i in range(n)]
        total_w = sum(weights)
        weights = [w / total_w for w in weights]
        now = time.time()
        interval = self.config.vwap_duration_secs / n
        children = []
        for i in range(n):
            scheduled = now + (i * interval)
            if self.config.randomize_timing:
                scheduled += random.uniform(-interval * 0.1, interval * 0.1)
            children.append(ChildOrder(parent_id=request.client_order_id,
                symbol=request.symbol, side=request.side, order_type=OrderType.MARKET,
                quantity=round(request.quantity * weights[i], 8), target_price=request.price,
                slice_index=i, total_slices=n, scheduled_time=scheduled))
        return children
    def _execute_immediate(self, request, children, current_price):
        for child in children:
            child.status = ChildOrderStatus.ACTIVE
            fill = self._execute_child(child, request, current_price)
            if fill:
                child.add_fill(fill)
    def _execute_child(self, child, request, current_price):
        if child.remaining <= 0:
            return None
        if self._broker_execute:
            try:
                result = self._broker_execute(request.symbol, child.side.value,
                    current_price, child.remaining)
                fill_price = result.get("fill_price", current_price)
                fill_qty = result.get("fill_qty", child.remaining)
                commission = result.get("commission", 0.0)
            except Exception as exc:
                logger.error("Broker execution failed: %s", exc)
                return None
        else:
            slippage_pct = random.gauss(0.0003, 0.0002)
            if child.side == OrderSide.BUY:
                fill_price = current_price * (1 + slippage_pct)
            else:
                fill_price = current_price * (1 - slippage_pct)
            fill_qty = child.remaining
            commission = fill_price * fill_qty * 0.001
        slippage_bps = ((fill_price - current_price) / current_price) * 10000
        if child.side == OrderSide.SELL:
            slippage_bps = -slippage_bps
        return ExecutionFill(child_order_id=child.child_id, price=fill_price,
            quantity=fill_qty, commission=commission,
            slippage_bps=slippage_bps, timestamp=time.time())
    def _schedule_children(self, parent_id, children):
        pass
    def _check_adverse_price(self, child, request, current_price):
        if request.price is None or self.config.cancel_on_drawdown_pct <= 0:
            return False
        threshold = self.config.cancel_on_drawdown_pct
        if child.side == OrderSide.BUY:
            return current_price > request.price * (1 + threshold)
        else:
            return current_price < request.price * (1 - threshold)
    def _update_report(self, report, child, fill):
        total_qty = sum(c.filled_quantity for c in report.child_orders)
        total_notional = sum(c.avg_fill_price * c.filled_quantity
            for c in report.child_orders if c.filled_quantity > 0)
        report.filled_quantity = total_qty
        report.avg_fill_price = total_notional / total_qty if total_qty > 0 else 0
        report.total_commission = sum(f.commission for c in report.child_orders for f in c.fills)
        total_slip = total_qty_for_slip = 0
        for c in report.child_orders:
            for f in c.fills:
                total_slip += f.slippage_bps * f.quantity
                total_qty_for_slip += f.quantity
        report.total_slippage_bps = total_slip / total_qty_for_slip if total_qty_for_slip > 0 else 0
    def _archive_report(self, report):
        self._reports.append(report)
        if len(self._reports) > self._max_reports:
            self._reports = self._reports[-self._max_reports:]
        self._total_filled += 1
        self._total_slippage_bps += report.total_slippage_bps
        self._slippage_count += 1
    def _load_config(self):
        sor_cfg = getattr(settings, 'order_router', None)
        if sor_cfg:
            return RouterConfig(
                default_algorithm=getattr(sor_cfg, 'default_algorithm', 'slice'),
                max_child_orders=getattr(sor_cfg, 'max_child_orders', 10),
                twap_duration_secs=getattr(sor_cfg, 'twap_duration_secs', 300),
                twap_interval_secs=getattr(sor_cfg, 'twap_interval_secs', 30),
                vwap_duration_secs=getattr(sor_cfg, 'vwap_duration_secs', 600),
            )
        return RouterConfig()
