"""Exposure Allocator - pre-trade hard gate."""
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional
from core.portfolio.portfolio_state import PortfolioState, AllocatorSnapshot
from core.portfolio.correlation import CorrelationMatrix
from config.settings import settings

logger = logging.getLogger(__name__)

@dataclass
class AllocationDecision:
    allowed: bool = True
    reason: str = ""
    hedge_required: bool = False
    hedge_size: float = 0.0
    effective_exposure: float = 0.0
    exposure_limit: float = 0.0
    exposure_utilization: float = 0.0
    projected_exposure: float = 0.0

@dataclass
class AllocatorStats:
    total_checks: int = 0
    allowed: int = 0
    blocked: int = 0
    hedges_emitted: int = 0
    inventory_reductions_fast_tracked: int = 0
    last_block_reason: str = ""

class ExposureAllocator:
    def __init__(self, portfolio, correlation=None, max_ratio=None, min_mm_inventory=None):
        self.portfolio = portfolio
        self.correlation = correlation or CorrelationMatrix()
        allocator_cfg = getattr(settings, 'allocator', None)
        self.max_ratio = max_ratio or (allocator_cfg.max_ratio if allocator_cfg else 2.0)
        self.min_mm_inventory = min_mm_inventory or (allocator_cfg.min_mm_inventory if allocator_cfg else 1000.0)
        self.stats = AllocatorStats()
        self._last_decision = None

    def allow(self, symbol, side, price, quantity=1.0, engine_type="directional"):
        self.stats.total_checks += 1
        if self._is_inventory_reduction(symbol, side, engine_type):
            self.stats.inventory_reductions_fast_tracked += 1
            self.stats.allowed += 1
            d = AllocationDecision(allowed=True, reason="Inventory reduction - fast tracked")
            self._last_decision = d
            return d
        current_snap = self.portfolio.snapshot()
        current_effective = self._calc_effective_exposure(current_snap)
        mm_base = current_snap.mm_inventory_total
        effective_mm_base = max(mm_base, self.min_mm_inventory)
        exposure_limit = self.max_ratio * effective_mm_base
        projected = self.portfolio.project(symbol, side, price, quantity, engine_type)
        projected_effective = self._calc_effective_exposure(projected)
        utilization = (current_effective / exposure_limit) if exposure_limit > 0 else float('inf')
        d = AllocationDecision(allowed=True, effective_exposure=current_effective,
            exposure_limit=exposure_limit, exposure_utilization=utilization,
            projected_exposure=projected_effective)
        if projected_effective > exposure_limit:
            d.allowed = False
            excess = projected_effective - exposure_limit
            d.reason = f"Exposure breach: projected ${projected_effective:,.0f} > limit ${exposure_limit:,.0f} (excess: ${excess:,.0f})"
            self.stats.blocked += 1
            self.stats.last_block_reason = d.reason
        if current_effective > exposure_limit:
            d.hedge_required = True
            d.hedge_size = current_effective - exposure_limit
            d.allowed = False
            d.reason = f"Current exposure ${current_effective:,.0f} already exceeds limit ${exposure_limit:,.0f} - hedge required"
            self.stats.blocked += 1
            self.stats.last_block_reason = d.reason
        if d.allowed:
            self.stats.allowed += 1
        self._last_decision = d
        return d

    def get_status(self):
        snap = self.portfolio.snapshot()
        effective = self._calc_effective_exposure(snap)
        mm_base = snap.mm_inventory_total
        eff_mm = max(mm_base, self.min_mm_inventory)
        limit = self.max_ratio * eff_mm
        return {
            "effective_exposure": round(effective, 2),
            "exposure_limit": round(limit, 2),
            "exposure_utilization_pct": round((effective / limit * 100) if limit > 0 else 0, 2),
            "mm_inventory": round(mm_base, 2),
            "directional_exposure_raw": round(snap.directional_exposure_raw, 2),
            "net_delta": round(snap.net_delta, 2),
            "max_ratio": self.max_ratio,
            "hedge_required": self._last_decision.hedge_required if self._last_decision else False,
            "hedge_size": round(self._last_decision.hedge_size, 2) if self._last_decision else 0,
            "stats": {"total_checks": self.stats.total_checks, "allowed": self.stats.allowed,
                "blocked": self.stats.blocked, "hedges_emitted": self.stats.hedges_emitted,
                "last_block_reason": self.stats.last_block_reason},
            "correlated_pairs": self.correlation.highest_correlated_pairs(0.7)}

    def reset(self):
        self.stats = AllocatorStats()
        self._last_decision = None

    def _calc_effective_exposure(self, snap):
        positions = {}
        for sym, pos in snap.positions.items():
            if pos.engine_type == "directional" and pos.quantity != 0:
                positions[sym] = pos.notional_value
        if not positions:
            return 0.0
        symbols_with_data = [s for s in positions if len(self.correlation._returns.get(s, [])) >= 30]
        if len(symbols_with_data) >= 2:
            effective = self.correlation.effective_exposure(positions)
            snap.directional_exposure_effective = effective
            return effective
        else:
            return snap.directional_exposure_raw

    def _is_inventory_reduction(self, symbol, side, engine_type):
        if engine_type != "mm":
            return False
        pos = self.portfolio._positions.get(symbol)
        if pos is None or pos.quantity <= 0:
            return False
        if side == "SELL" and pos.quantity > 0:
            return True
        return False
