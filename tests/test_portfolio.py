"""Unit tests for the Portfolio Control Layer. Run: python -m pytest tests/test_portfolio.py -v"""
import math, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import numpy as np
from core.portfolio.portfolio_state import PortfolioState, SymbolPosition, AllocatorSnapshot
from core.portfolio.correlation import CorrelationMatrix
from core.portfolio.exposure_allocator import ExposureAllocator, AllocationDecision
from core.portfolio.hedge_trigger import HedgeTrigger

class TestPortfolioState:
    def setup_method(self):
        self.portfolio = PortfolioState(initial_cash=100000.0)
    def test_initial_state(self):
        snap = self.portfolio.snapshot()
        assert snap.total_cash == 100000.0 and snap.total_equity == 100000.0
        assert len(snap.positions) == 0 and snap.mm_inventory_total == 0.0
    def test_open_long_directional(self):
        self.portfolio.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        snap = self.portfolio.snapshot()
        assert snap.positions["BTCUSDT"].quantity == 1.0
        assert snap.directional_exposure_raw == 50000.0 and snap.total_cash == 50000.0
    def test_open_long_mm(self):
        self.portfolio.update_position("BTCUSDT", "BUY", 50000.0, 0.5, "mm")
        snap = self.portfolio.snapshot()
        assert snap.mm_inventory_total == 25000.0 and snap.directional_exposure_raw == 0.0
    def test_close_position_resets(self):
        self.portfolio.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        self.portfolio.update_position("BTCUSDT", "SELL", 51000.0, 1.0, "directional")
        snap = self.portfolio.snapshot()
        assert snap.positions["BTCUSDT"].quantity == 0.0
    def test_realised_pnl(self):
        self.portfolio.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        self.portfolio.update_position("BTCUSDT", "SELL", 55000.0, 1.0, "directional")
        assert self.portfolio._positions["BTCUSDT"].realised_pnl == 5000.0
    def test_weighted_average_entry(self):
        self.portfolio.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        self.portfolio.update_position("BTCUSDT", "BUY", 60000.0, 1.0, "directional")
        pos = self.portfolio._positions["BTCUSDT"]
        assert pos.quantity == 2.0 and pos.entry_price == 55000.0
    def test_mark_to_market(self):
        self.portfolio.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        self.portfolio.update_mark_to_market({"BTCUSDT": 55000.0})
        pos = self.portfolio._positions["BTCUSDT"]
        assert pos.notional_value == 55000.0 and pos.unrealised_pnl == 5000.0
    def test_project_no_mutate(self):
        self.portfolio.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        oc = self.portfolio._cash; oq = self.portfolio._positions["BTCUSDT"].quantity
        proj = self.portfolio.project("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        assert self.portfolio._cash == oc and self.portfolio._positions["BTCUSDT"].quantity == oq
        assert proj.positions["BTCUSDT"].quantity == 2.0
    def test_multi_symbol(self):
        self.portfolio.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        self.portfolio.update_position("ETHUSDT", "BUY", 3000.0, 2.0, "directional")
        assert self.portfolio.snapshot().directional_exposure_raw == 56000.0
    def test_net_delta(self):
        self.portfolio.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        assert self.portfolio.net_delta() == 50000.0
    def test_active_symbols(self):
        self.portfolio.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        self.portfolio.update_position("ETHUSDT", "BUY", 3000.0, 2.0, "directional")
        self.portfolio.update_position("ETHUSDT", "SELL", 3000.0, 2.0, "directional")
        active = self.portfolio.active_symbols()
        assert "BTCUSDT" in active and "ETHUSDT" not in active
    def test_reset(self):
        self.portfolio.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        self.portfolio.reset()
        assert self.portfolio.snapshot().total_cash == 100000.0

class TestCorrelationMatrix:
    def setup_method(self):
        self.corr = CorrelationMatrix(window=50)
    def test_initial_state(self):
        assert self.corr.get_matrix() == {}
    def test_self_correlation(self):
        assert self.corr.get_correlation("BTCUSDT", "BTCUSDT") == 1.0
    def test_insufficient_data(self):
        for i in range(20):
            self.corr.update({"BTCUSDT": 50000.0 + i * 100, "ETHUSDT": 3000.0 + i * 10})
        assert self.corr.get_correlation("BTCUSDT", "ETHUSDT") == 0.0
    def test_perfect_correlation(self):
        np.random.seed(42)
        btc_base = 50000.0
        for i in range(50):
            btc_base *= 1.0 + np.random.normal(0.001, 0.01)
            eth_price = 3000.0 * (btc_base / 50000.0)
            self.corr.update({"BTCUSDT": btc_base, "ETHUSDT": eth_price})
        assert self.corr.get_correlation("BTCUSDT", "ETHUSDT") > 0.95
    def test_window_trim(self):
        for i in range(100):
            self.corr.update({"BTCUSDT": 50000.0 + i})
            self.corr.update({"ETHUSDT": 3000.0 + i * 0.1})
        assert len(self.corr._returns["BTCUSDT"]) == 50
    def test_effective_single(self):
        assert self.corr.effective_exposure({"BTCUSDT": 50000.0}) == 50000.0
    def test_effective_empty(self):
        assert self.corr.effective_exposure({}) == 0.0
    def test_effective_uncorrelated(self):
        result = self.corr.effective_exposure({"BTCUSDT": 3000.0, "ETHUSDT": 4000.0})
        expected = math.sqrt(3000.0**2 + 4000.0**2)
        assert abs(result - expected) < 0.01
    def test_no_pairs(self):
        assert self.corr.highest_correlated_pairs(0.5) == []
    def test_zero_std(self):
        for i in range(50):
            self.corr.update({"BTCUSDT": 50000.0, "ETHUSDT": 3000.0})
        assert self.corr.get_correlation("BTCUSDT", "ETHUSDT") == 0.0
    def test_cache_invalidation(self):
        self.corr.update({"BTCUSDT": 50000.0})
        self.corr.get_matrix()
        assert self.corr._cached_matrix is not None
        self.corr.update({"BTCUSDT": 50100.0})
        assert self.corr._cached_matrix is None

class TestExposureAllocator:
    def setup_method(self):
        self.portfolio = PortfolioState(initial_cash=100000.0)
        self.corr = CorrelationMatrix(window=100)
        self.allocator = ExposureAllocator(self.portfolio, self.corr, 2.0, 1000.0)
    def test_small_ok(self):
        assert self.allocator.allow("BTCUSDT", "BUY", 15000.0, 0.1, "directional").allowed
    def test_big_blocked(self):
        assert not self.allocator.allow("BTCUSDT", "BUY", 50000.0, 1.0, "directional").allowed
    def test_block_reason(self):
        self.portfolio.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        d = self.allocator.allow("ETHUSDT", "BUY", 3000.0, 1.0, "directional")
        assert not d.allowed and ("Exposure breach" in d.reason or "exceeds" in d.reason)
    def test_mm_headroom(self):
        self.portfolio.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "mm")
        assert self.allocator.allow("ETHUSDT", "BUY", 3000.0, 1.0, "directional").allowed
    def test_mm_fasttrack(self):
        self.portfolio.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "mm")
        d = self.allocator.allow("BTCUSDT", "SELL", 50000.0, 1.0, "mm")
        assert d.allowed and "Inventory reduction" in d.reason
    def test_stats_allowed(self):
        self.allocator.allow("BTCUSDT", "BUY", 15000.0, 0.1, "directional")
        assert self.allocator.stats.allowed == 1
    def test_stats_blocked(self):
        self.allocator.allow("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        assert self.allocator.stats.blocked == 1
    def test_utilization(self):
        d = self.allocator.allow("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        assert d.exposure_limit > 0
    def test_status(self):
        s = self.allocator.get_status()
        assert "effective_exposure" in s and "stats" in s
    def test_reset(self):
        self.allocator.allow("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        self.allocator.reset()
        assert self.allocator.stats.total_checks == 0
    def test_non_mm_not_fasttracked(self):
        self.portfolio.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        d = self.allocator.allow("BTCUSDT", "SELL", 50000.0, 1.0, "directional")
        assert "Inventory reduction" not in (d.reason or "")
    def test_block_records_reason(self):
        self.portfolio.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        self.allocator.allow("ETHUSDT", "BUY", 3000.0, 1.0, "directional")
        assert self.allocator.stats.last_block_reason != ""

class TestHedgeTrigger:
    def setup_method(self):
        self.p = PortfolioState(100000.0)
        self.ht = HedgeTrigger(self.p, CorrelationMatrix(100), 0.5)
    def test_no_excess(self):
        assert self.ht.compute_hedges(0, {"BTCUSDT": 50000.0}) == []
    def test_hedge_long(self):
        self.p.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        h = self.ht.compute_hedges(20000.0, {"BTCUSDT": 50000.0})
        assert len(h) >= 1 and h[0].side == "SELL"
    def test_max_pct(self):
        self.p.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        h = self.ht.compute_hedges(100000.0, {"BTCUSDT": 50000.0})
        assert sum(x.notional_reduction for x in h) <= 25001.0
    def test_priority(self):
        self.p.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        self.p.update_position("ETHUSDT", "BUY", 3000.0, 1.0, "directional")
        h = self.ht.compute_hedges(10000.0, {"BTCUSDT": 50000.0, "ETHUSDT": 3000.0})
        assert h[0].symbol == "BTCUSDT"
    def test_no_positions(self):
        assert self.ht.compute_hedges(10000.0, {"BTCUSDT": 50000.0}) == []
    def test_hedge_status(self):
        assert self.ht.get_status()["total_hedges_triggered"] == 0
    def test_reset(self):
        self.p.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        self.ht.compute_hedges(10000.0, {"BTCUSDT": 50000.0})
        self.ht.reset()
        assert self.ht._hedge_count == 0

class TestIntegrationFlow:
    def test_under_limit(self):
        p = PortfolioState(200000.0); a = ExposureAllocator(p, CorrelationMatrix(100), 2.0, 1000.0)
        p.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "mm")
        assert a.allow("ETHUSDT", "BUY", 40000.0, 2.0, "directional").allowed
    def test_over_limit(self):
        p = PortfolioState(300000.0); a = ExposureAllocator(p, CorrelationMatrix(100), 2.0, 1000.0)
        p.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "mm")
        assert not a.allow("ETHUSDT", "BUY", 40000.0, 3.0, "directional").allowed
    def test_corr_amplify(self):
        p = PortfolioState(200000.0); c = CorrelationMatrix(100)
        np.random.seed(42)
        for i in range(50):
            r = np.random.normal(0.001, 0.02)
            c.update({"BTCUSDT": 50000.0*(1+r), "ETHUSDT": 3000.0*(1+r*0.9)})
        p.update_position("BTCUSDT", "BUY", 50000.0, 1.0, "directional")
        p.update_position("ETHUSDT", "BUY", 3000.0, 2.0, "directional")
        assert ExposureAllocator(p, c, 2.0, 1000.0).get_status()["effective_exposure"] > 50000.0
    def test_hedge_flow(self):
        p = PortfolioState(200000.0); c = CorrelationMatrix(100)
        a = ExposureAllocator(p, c, 2.0, 1000.0); ht = HedgeTrigger(p, c, 1.0)
        p.update_position("BTCUSDT", "BUY", 50000.0, 0.2, "mm")
        p.update_position("ETHUSDT", "BUY", 50000.0, 2.0, "directional")
        d = a.allow("BTCUSDT", "BUY", 50000.0, 0.1, "mm")
        if d.hedge_required:
            h = ht.compute_hedges(d.hedge_size, {"ETHUSDT": 50000.0})
            assert len(h) > 0 and h[0].side == "SELL"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
