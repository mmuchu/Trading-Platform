"""Hedge Trigger - autonomous hedge emission."""
import logging
from dataclasses import dataclass
from typing import Dict, List, Tuple
from core.portfolio.portfolio_state import PortfolioState
from core.portfolio.correlation import CorrelationMatrix

logger = logging.getLogger(__name__)

@dataclass
class HedgeOrder:
    symbol: str
    side: str
    quantity: float
    notional_reduction: float
    reason: str

class HedgeTrigger:
    def __init__(self, portfolio, correlation, max_hedge_pct=0.5):
        self.portfolio = portfolio
        self.correlation = correlation
        self.max_hedge_pct = max_hedge_pct
        self._hedge_count = 0
        self._last_hedges = []

    def compute_hedges(self, excess_exposure, current_prices):
        if excess_exposure <= 0:
            return []
        hedges = []
        remaining_excess = excess_exposure
        directional_positions = []
        with self.portfolio._lock:
            for sym, pos in self.portfolio._positions.items():
                if pos.engine_type == "directional" and pos.quantity != 0 and sym in current_prices:
                    directional_positions.append((sym, pos.quantity, pos.notional_value))
        directional_positions.sort(key=lambda x: abs(x[2]), reverse=True)
        if not directional_positions:
            return []
        correlated = self.correlation.highest_correlated_pairs(0.6)
        correlated_symbols = set()
        for sym_a, sym_b, _ in correlated:
            correlated_symbols.add(sym_a)
            correlated_symbols.add(sym_b)
        for symbol, quantity, notional in directional_positions:
            if remaining_excess <= 0:
                break
            max_reduce_notional = min(abs(notional) * self.max_hedge_pct, remaining_excess)
            if max_reduce_notional <= 0:
                continue
            price = current_prices.get(symbol, 0)
            if price <= 0:
                continue
            hedge_qty = max_reduce_notional / price
            if quantity > 0:
                side = "SELL"
                reason = f"Close long to reduce net long exposure by ${max_reduce_notional:,.0f}"
            else:
                side = "BUY"
                reason = f"Close short to reduce net short exposure by ${max_reduce_notional:,.0f}"
            if symbol in correlated_symbols:
                reason += " (high-correlation cluster)"
            hedge = HedgeOrder(symbol=symbol, side=side, quantity=round(hedge_qty, 6),
                notional_reduction=max_reduce_notional, reason=reason)
            hedges.append(hedge)
            remaining_excess -= max_reduce_notional
        if hedges:
            self._hedge_count += 1
            self._last_hedges = hedges
        return hedges

    def get_status(self):
        return {"total_hedges_triggered": self._hedge_count,
            "last_hedges": [{"symbol": h.symbol, "side": h.side, "quantity": h.quantity,
                "notional_reduction": round(h.notional_reduction, 2), "reason": h.reason}
                for h in self._last_hedges] if self._last_hedges else []}

    def reset(self):
        self._hedge_count = 0
        self._last_hedges = []
