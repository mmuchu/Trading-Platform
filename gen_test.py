"""Tests for Smart Order Router and Execution Analytics."""

import time
import pytest

from core.execution.order import (
    OrderRequest, OrderSide, OrderType,
    ChildOrderStatus, ExecutionFill, ChildOrder, ExecutionReport,
)
from core.execution.order_router import SmartOrderRouter, RouterConfig, SplitAlgorithm
from core.execution.execution_analytics import ExecutionAnalytics, AlgorithmStats


def _make_request(symbol="AAPL", side=OrderSide.BUY, order_type=OrderType.MARKET,
                  quantity=100.0, price=None, client_id=None):
    return OrderRequest(symbol=symbol, side=side, order_type=order_type,
                        quantity=quantity, price=price,
                        client_order_id=client_id or "test-001")


def _mock_broker(symbol, side, price, quantity):
    return {"fill_price": price * 1.001, "fill_qty": quantity,
            "commission": price * quantity * 0.001}


# ============================================================
# Order Models
# ============================================================
class TestOrderModels:

    def test_child_order_remaining(self):
        co = ChildOrder(parent_id="p1", quantity=100.0, filled_quantity=30.0)
        assert co.remaining == 70.0

    def test_child_order_fill_pct(self):
        co = ChildOrder(parent_id="p1", quantity=100.0, filled_quantity=25.0)
        assert co.fill_pct == 25.0

    def test_child_order_add_fill_updates_state(self):
        co = ChildOrder(parent_id="p1", quantity=100.0)
        fill = ExecutionFill(price=150.0, quantity=60.0, commission=9.0, slippage_bps=1.5)
        co.add_fill(fill)
        assert co.filled_quantity == 60.0
        assert co.fill_pct == 60.0
        assert co.avg_fill_price == 150.0

    def test_child_order_add_fill_marks_complete(self):
        co = ChildOrder(parent_id="p1", quantity=100.0)
        co.add_fill(ExecutionFill(price=100.0, quantity=100.0))
        assert co.is_complete
        assert co.status == ChildOrderStatus.FILLED

    def test_execution_report_fill_pct(self):
        r = ExecutionReport(requested_quantity=200.0, filled_quantity=150.0)
        assert r.fill_pct == 75.0

    def test_execution_report_is_complete(self):
        r = ExecutionReport(requested_quantity=100.0, filled_quantity=100.0)
        assert r.is_complete

    def test_execution_report_to_dict_keys(self):
        r = ExecutionReport(parent_id="p1", symbol="AAPL", side="BUY",
                            algorithm="slice", requested_quantity=100.0)
        d = r.to_dict()
        for key in ["parent_id", "symbol", "side", "algorithm",
                     "requested_quantity", "filled_quantity", "fill_pct",
                     "avg_fill_price", "slippage_bps", "status"]:
            assert key in d


# ============================================================
# ExecutionAnalytics
# ============================================================
class TestExecutionAnalytics:

    def _report(self, **kw):
        defaults = dict(parent_id="p", symbol="X", side="BUY", algorithm="slice",
                        requested_quantity=100.0, filled_quantity=100.0,
                        avg_fill_price=150.0, benchmark_price=150.0,
                        total_commission=0.5, total_slippage_bps=2.0,
                        child_count=1, start_time=time.time(),
                        end_time=time.time() + 1, status="COMPLETE")
        defaults.update(kw)
        return ExecutionReport(**defaults)

    def test_empty_summary(self):
        a = ExecutionAnalytics()
        assert a.get_summary()["total_orders"] == 0

    def test_record_single_order(self):
        a = ExecutionAnalytics()
        a.record(self._report(algorithm="slice"))
        s = a.get_summary()
        assert s["total_orders"] == 1
        assert s["completed_orders"] == 1

    def test_algorithm_breakdown(self):
        a = ExecutionAnalytics()
        a.record(self._report(algorithm="slice"))
        a.record(self._report(algorithm="twap"))
        a.record(self._report(algorithm="slice"))
        assert a.get_summary()["algorithm_breakdown"] == {"slice": 2, "twap": 1}

    def test_algorithm_comparison(self):
        a = ExecutionAnalytics()
        a.record(self._report(algorithm="slice", total_slippage_bps=1.0))
        a.record(self._report(algorithm="twap", total_slippage_bps=3.0))
        comp = a.get_algorithm_comparison()
        assert len(comp) == 2
        m = {c["algorithm"]: c for c in comp}
        assert m["slice"]["avg_slippage_bps"] == 1.0
        assert m["twap"]["avg_slippage_bps"] == 3.0

    def test_slippage_distribution(self):
        a = ExecutionAnalytics()
        for bps in [1.0, 2.0, 3.0, 4.0, 5.0]:
            a.record(self._report(total_slippage_bps=bps))
        d = a.get_slippage_distribution()
        assert d["min"] == 1.0
        assert d["max"] == 5.0
        assert d["mean"] == 3.0

    def test_get_recent_reports(self):
        a = ExecutionAnalytics()
        for i in range(5):
            a.record(self._report(parent_id=f"p{i}"))
        recent = a.get_recent_reports(limit=3)
        assert len(recent) == 3
        assert recent[0]["parent_id"] == "p4"

    def test_reset_clears_all(self):
        a = ExecutionAnalytics()
        a.record(self._report())
        a.reset()
        assert a.get_summary()["total_orders"] == 0


# ============================================================
# Router - Algorithm Selection
# ============================================================
class TestRouterAlgorithmSelection:

    def test_limit_uses_pass(self):
        r = SmartOrderRouter(RouterConfig(), broker_execute=_mock_broker)
        report = r.route(_make_request(order_type=OrderType.LIMIT, price=150.0), 150.0)
        assert report.algorithm == SplitAlgorithm.PASS

    def test_twap_order_type(self):
        r = SmartOrderRouter(RouterConfig(), broker_execute=_mock_broker)
        report = r.route(_make_request(order_type=OrderType.TWAP), 150.0)
        assert report.algorithm == SplitAlgorithm.TWAP

    def test_vwap_order_type(self):
        r = SmartOrderRouter(RouterConfig(), broker_execute=_mock_broker)
        report = r.route(_make_request(order_type=OrderType.VWAP), 150.0)
        assert report.algorithm == SplitAlgorithm.VWAP

    def test_market_uses_default(self):
        r = SmartOrderRouter(RouterConfig(default_algorithm="slice"),
                             broker_execute=_mock_broker)
        report = r.route(_make_request(order_type=OrderType.MARKET), 150.0)
        assert report.algorithm == "slice"


# ============================================================
# Router - Slice Execution
# ============================================================
class TestRouterSlice:

    def _router(self, max_children=5):
        return SmartOrderRouter(
            RouterConfig(default_algorithm="slice", max_child_orders=max_children),
            broker_execute=_mock_broker)

    def test_slice_splits_into_children(self):
        report = self._router(5).route(_make_request(quantity=100.0), 150.0)
        assert report.child_count == 5

    def test_slice_fills_completely(self):
        report = self._router(3).route(_make_request(quantity=300.0), 150.0)
        assert report.is_complete
        assert report.filled_quantity == 300.0

    def test_slice_child_quantities_sum(self):
        report = self._router(4).route(_make_request(quantity=200.0), 150.0)
        assert sum(c.quantity for c in report.child_orders) == pytest.approx(200.0, abs=0.01)

    def test_slice_sell_side(self):
        report = self._router(2).route(
            _make_request(side=OrderSide.SELL, quantity=100.0), 150.0)
        assert report.side == "SELL"
        assert all(c.side == OrderSide.SELL for c in report.child_orders)


# ============================================================
# Router - TWAP Execution
# ============================================================
class TestRouterTWAP:

    def _router(self, duration=60.0, interval=10.0):
        return SmartOrderRouter(
            RouterConfig(twap_duration_secs=duration, twap_interval_secs=interval,
                         randomize_timing=False),
            broker_execute=_mock_broker)

    def test_twap_creates_timed_children(self):
        report = self._router(60.0, 20.0).route(
            _make_request(order_type=OrderType.TWAP, quantity=300.0), 150.0)
        assert report.child_count == 3
        assert all(c.scheduled_time is not None for c in report.child_orders)

    def test_twap_tick_executes_due_children(self):
        r = self._router(1.0, 1.0)
        report = r.route(
            _make_request(order_type=OrderType.TWAP, quantity=100.0, client_id="tw-01"), 150.0)
        for child in report.child_orders:
            child.scheduled_time = time.time() - 1
        fills = r.tick({"AAPL": 150.0})
        assert len(fills) > 0
        assert fills[0]["parent_id"] == "tw-01"


# ============================================================
# Router - VWAP Execution
# ============================================================
class TestRouterVWAP:

    def test_vwap_creates_children(self):
        r = SmartOrderRouter(
            RouterConfig(vwap_duration_secs=300, twap_interval_secs=60,
                         randomize_timing=False),
            broker_execute=_mock_broker)
        report = r.route(
            _make_request(order_type=OrderType.VWAP, quantity=500.0), 150.0)
        assert report.child_count >= 1
        assert all(c.scheduled_time is not None for c in report.child_orders)


# ============================================================
# Router - Pass-Through
# ============================================================
class TestRouterPass:

    def test_pass_creates_single_child(self):
        r = SmartOrderRouter(RouterConfig(), broker_execute=_mock_broker)
        report = r.route(
            _make_request(order_type=OrderType.LIMIT, price=150.0, quantity=50.0), 150.0)
        assert report.child_count == 1
        assert report.child_orders[0].quantity == 50.0

    def test_pass_fills_completely(self):
        r = SmartOrderRouter(RouterConfig(), broker_execute=_mock_broker)
        report = r.route(
            _make_request(order_type=OrderType.LIMIT, price=150.0, quantity=50.0), 150.0)
        assert report.is_complete


# ============================================================
# Router - Cancel
# ============================================================
class TestRouterCancel:

    def test_cancel_twap_order(self):
        r = SmartOrderRouter(
            RouterConfig(randomize_timing=False), broker_execute=_mock_broker)
        report = r.route(
            _make_request(order_type=OrderType.TWAP, quantity=100.0, client_id="cx-01"), 150.0)
        assert r.cancel_order("cx-01") is True
        assert all(c.status == ChildOrderStatus.CANCELLED for c in report.child_orders)

    def test_cancel_nonexistent_returns_false(self):
        r = SmartOrderRouter(RouterConfig())
        assert r.cancel_order("no-such-id") is False


# ============================================================
# Router - Adverse Price
# ============================================================
class TestRouterAdversePrice:

    def test_adverse_price_cancels_buy(self):
        r = SmartOrderRouter(
            RouterConfig(cancel_on_drawdown_pct=0.5, randomize_timing=False),
            broker_execute=_mock_broker)
        report = r.route(
            _make_request(order_type=OrderType.TWAP, quantity=100.0,
                          price=150.0, client_id="adv-01"), 150.0)
        for child in report.child_orders:
            child.scheduled_time = time.time() - 1
        fills = r.tick({"AAPL": 240.0})
        assert len(fills) == 0


# ============================================================
# Router - Stats and Reports
# ============================================================
class TestRouterStats:

    def test_stats_after_slice(self):
        r = SmartOrderRouter(
            RouterConfig(max_child_orders=2), broker_execute=_mock_broker)
        r.route(_make_request(quantity=100.0), 150.0)
        s
@'
content = r'''"""Tests for Smart Order Router and Execution Analytics."""
import time
import pytest
from core.execution.order import (
    OrderRequest, OrderSide, OrderType,
    ChildOrderStatus, ExecutionFill, ChildOrder, ExecutionReport,
)
from core.execution.order_router import SmartOrderRouter, RouterConfig, SplitAlgorithm
from core.execution.execution_analytics import ExecutionAnalytics, AlgorithmStats

def _make_request(symbol="AAPL", side=OrderSide.BUY, order_type=OrderType.MARKET,
                  quantity=100.0, price=None, client_id=None):
    return OrderRequest(symbol=symbol, side=side, order_type=order_type,
                        quantity=quantity, price=price,
                        client_order_id=client_id or "test-001")

def _mock_broker(symbol, side, price, quantity):
    return {"fill_price": price * 1.001, "fill_qty": quantity,
            "commission": price * quantity * 0.001}

class TestOrderModels:
    def test_child_order_remaining(self):
        co = ChildOrder(parent_id="p1", quantity=100.0, filled_quantity=30.0)
        assert co.remaining == 70.0
    def test_child_order_fill_pct(self):
        co = ChildOrder(parent_id="p1", quantity=100.0, filled_quantity=25.0)
        assert co.fill_pct == 25.0
    def test_child_order_add_fill_updates_state(self):
        co = ChildOrder(parent_id="p1", quantity=100.0)
        fill = ExecutionFill(price=150.0, quantity=60.0, commission=9.0, slippage_bps=1.5)
        co.add_fill(fill)
        assert co.filled_quantity == 60.0
        assert co.fill_pct == 60.0
        assert co.avg_fill_price == 150.0
    def test_child_order_add_fill_marks_complete(self):
        co = ChildOrder(parent_id="p1", quantity=100.0)
        co.add_fill(ExecutionFill(price=100.0, quantity=100.0))
        assert co.is_complete
        assert co.status == ChildOrderStatus.FILLED
    def test_execution_report_fill_pct(self):
        r = ExecutionReport(requested_quantity=200.0, filled_quantity=150.0)
        assert r.fill_pct == 75.0
    def test_execution_report_is_complete(self):
        r = ExecutionReport(requested_quantity=100.0, filled_quantity=100.0)
        assert r.is_complete
    def test_execution_report_to_dict_keys(self):
        r = ExecutionReport(parent_id="p1", symbol="AAPL", side="BUY",
                            algorithm="slice", requested_quantity=100.0)
        d = r.to_dict()
        for key in ["parent_id","symbol","side","algorithm","requested_quantity",
                     "filled_quantity","fill_pct","avg_fill_price","slippage_bps","status"]:
            assert key in d

class TestExecutionAnalytics:
    def _report(self, **kw):
        defaults = dict(parent_id="p", symbol="X", side="BUY", algorithm="slice",
                        requested_quantity=100.0, filled_quantity=100.0,
                        avg_fill_price=150.0, benchmark_price=150.0,
                        total_commission=0.5, total_slippage_bps=2.0,
                        child_count=1, start_time=time.time(),
                        end_time=time.time()+1, status="COMPLETE")
        defaults.update(kw)
        return ExecutionReport(**defaults)
    def test_empty_summary(self):
        a = ExecutionAnalytics()
        assert a.get_summary()["total_orders"] == 0
    def test_record_single_order(self):
        a = ExecutionAnalytics()
        a.record(self._report(algorithm="slice"))
        s = a.get_summary()
        assert s["total_orders"] == 1
        assert s["completed_orders"] == 1
    def test_algorithm_breakdown(self):
        a = ExecutionAnalytics()
        a.record(self._report(algorithm="slice"))
        a.record(self._report(algorithm="twap"))
        a.record(self._report(algorithm="slice"))
        assert a.get_summary()["algorithm_breakdown"] == {"slice": 2, "twap": 1}
    def test_algorithm_comparison(self):
        a = ExecutionAnalytics()
        a.record(self._report(algorithm="slice", total_slippage_bps=1.0))
        a.record(self._report(algorithm="twap", total_slippage_bps=3.0))
        comp = a.get_algorithm_comparison()
        assert len(comp) == 2
        m = {c["algorithm"]: c for c in comp}
        assert m["slice"]["avg_slippage_bps"] == 1.0
        assert m["twap"]["avg_slippage_bps"] == 3.0
    def test_slippage_distribution(self):
        a = ExecutionAnalytics()
        for bps in [1.0, 2.0, 3.0, 4.0, 5.0]:
            a.record(self._report(total_slippage_bps=bps))
        d = a.get_slippage_distribution()
        assert d["min"] == 1.0
        assert d["max"] == 5.0
        assert d["mean"] == 3.0
    def test_get_recent_reports(self):
        a = ExecutionAnalytics()
        for i in range(5):
            a.record(self._report(parent_id=f"p{i}"))
        recent = a.get_recent_reports(limit=3)
        assert len(recent) == 3
        assert recent[0]["parent_id"] == "p4"
    def test_reset_clears_all(self):
        a = ExecutionAnalytics()
        a.record(self._report())
        a.reset()
        assert a.get_summary()["total_orders"] == 0

class TestRouterAlgorithmSelection:
    def test_limit_uses_pass(self):
        r = SmartOrderRouter(RouterConfig(), broker_execute=_mock_broker)
        report = r.route(_make_request(order_type=OrderType.LIMIT, price=150.0), 150.0)
        assert report.algorithm == SplitAlgorithm.PASS
    def test_twap_order_type(self):
        r = SmartOrderRouter(RouterConfig(), broker_execute=_mock_broker)
        report = r.route(_make_request(order_type=OrderType.TWAP), 150.0)
        assert report.algorithm == SplitAlgorithm.TWAP
    def test_vwap_order_type(self):
        r = SmartOrderRouter(RouterConfig(), broker_execute=_mock_broker)
        report = r.route(_make_request(order_type=OrderType.VWAP), 150.0)
        assert report.algorithm == SplitAlgorithm.VWAP
    def test_market_uses_default(self):
        r = SmartOrderRouter(RouterConfig(default_algorithm="slice"), broker_execute=_mock_broker)
        report = r.route(_make_request(order_type=OrderType.MARKET), 150.0)
        assert report.algorithm == "slice"

class TestRouterSlice:
    def _router(self, max_children=5):
        return SmartOrderRouter(RouterConfig(default_algorithm="slice", max_child_orders=max_children), broker_execute=_mock_broker)
    def test_slice_splits_into_children(self):
        report = self._router(5).route(_make_request(quantity=100.0), 150.0)
        assert report.child_count == 5
    def test_slice_fills_completely(self):
        report = self._router(3).route(_make_request(quantity=300.0), 150.0)
        assert report.is_complete
        assert report.filled_quantity == 300.0
    def test_slice_child_quantities_sum(self):
        report = self._router(4).route(_make_request(quantity=200.0), 150.0)
        assert sum(c.quantity for c in report.child_orders) == pytest.approx(200.0, abs=0.01)
    def test_slice_sell_side(self):
        report = self._router(2).route(_make_request(side=OrderSide.SELL, quantity=100.0), 150.0)
        assert report.side == "SELL"
        assert all(c.side == OrderSide.SELL for c in report.child_orders)

class TestRouterTWAP:
    def _router(self, duration=60.0, interval=10.0):
        return SmartOrderRouter(RouterConfig(twap_duration_secs=duration, twap_interval_secs=interval, randomize_timing=False), broker_execute=_mock_broker)
    def test_twap_creates_timed_children(self):
        report = self._router(60.0, 20.0).route(_make_request(order_type=OrderType.TWAP, quantity=300.0), 150.0)
        assert report.child_count == 3
        assert all(c.scheduled_time is not None for c in report.child_orders)
    def test_twap_tick_executes_due_children(self):
        r = self._router(1.0, 1.0)
        report = r.route(_make_request(order_type=OrderType.TWAP, quantity=100.0, client_id="tw-01"), 150.0)
        for child in report.child_orders:
            child.scheduled_time = time.time() - 1
        fills = r.tick({"AAPL": 150.0})
        assert len(fills) > 0
        assert fills[0]["parent_id"] == "tw-01"

class TestRouterVWAP:
    def test_vwap_creates_children(self):
        r = SmartOrderRouter(RouterConfig(vwap_duration_secs=300, twap_interval_secs=60, randomize_timing=False), broker_execute=_mock_broker)
        report = r.route(_make_request(order_type=OrderType.VWAP, quantity=500.0), 150.0)
        assert report.child_count >= 1
        assert all(c.scheduled_time is not None for c in report.child_orders)

class TestRouterPass:
    def test_pass_creates_single_child(self):
        r = SmartOrderRouter(RouterConfig(), broker_execute=_mock_broker)
        report = r.route(_make_request(order_type=OrderType.LIMIT, price=150.0, quantity=50.0), 150.0)
        assert report.child_count == 1
        assert report.child_orders[0].quantity == 50.0
    def test_pass_fills_completely(self):
        r = SmartOrderRouter(RouterConfig(), broker_execute=_mock_broker)
        report = r.route(_make_request(order_type=OrderType.LIMIT, price=150.0, quantity=50.0), 150.0)
        assert report.is_complete

class TestRouterCancel:
    def test_cancel_twap_order(self):
        r = SmartOrderRouter(RouterConfig(randomize_timing=False), broker_execute=_mock_broker)
        report = r.route(_make_request(order_type=OrderType.TWAP, quantity=100.0, client_id="cx-01"), 150.0)
        assert r.cancel_order("cx-01") is True
        assert all(c.status == ChildOrderStatus.CANCELLED for c in report.child_orders)
    def test_cancel_nonexistent_returns_false(self):
        r = SmartOrderRouter(RouterConfig())
        assert r.cancel_order("no-such-id") is False

class TestRouterAdversePrice:
    def test_adverse_price_cancels_buy(self):
        r = SmartOrderRouter(RouterConfig(cancel_on_drawdown_pct=0.5, randomize_timing=False), broker_execute=_mock_broker)
        report = r.route(_make_request(order_type=OrderType.TWAP, quantity=100.0, price=150.0, client_id="adv-01"), 150.0)
        for child in report.child_orders:
            child.scheduled_time = time.time() - 1
        fills = r.tick({"AAPL": 240.0})
        assert len(fills) == 0

class TestRouterStats:
    def test_stats_after_slice(self):
        r = SmartOrderRouter(RouterConfig(max_child_orders=2), broker_execute=_mock_broker)
        r.route(_make_request(quantity=100.0), 150.0)
        s = r.get_stats()
        assert s["total_orders_routed"] == 1
        assert s["active_orders"] == 1
    def test_get_report_by_id(self):
        r = SmartOrderRouter(RouterConfig(max_child_orders=2), broker_execute=_mock_broker)
        report = r.route(_make_request(client_id="rp-01", quantity=100.0), 150.0)
        assert r.get_report("rp-01") is not None
        assert r.get_report("rp-01").symbol == "AAPL"
    def test_get_report_nonexistent(self):
        r = SmartOrderRouter(RouterConfig())
        assert r.get_report("nope") is None
    def test_reset_clears_router(self):
        r = SmartOrderRouter(RouterConfig(max_child_orders=2), broker_execute=_mock_broker)
        r.route(_make_request(quantity=100.0), 150.0)
        r.reset()
        s = r.get_stats()
        assert s["total_orders_routed"] == 0
        assert s["active_orders"] == 0

class TestRouterAnalyticsIntegration:
    def test_analytics_records_completed_report(self):
        r = SmartOrderRouter(RouterConfig(max_child_orders=2), broker_execute=_mock_broker)
        a = ExecutionAnalytics()
        report = r.route(_make_request(client_id="int-01", quantity=100.0), 150.0)
        a.record(report)
        s = a.get_summary()
        assert s["total_orders"] == 1
        assert "slice" in s["algorithm_breakdown"]
'''
with open(r"tests\test_order_router.py", "w", encoding="utf-8") as f:
    f.write(content)
print("[OK] tests/test_order_router.py written")
