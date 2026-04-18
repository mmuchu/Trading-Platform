"""Portfolio State - single source of truth for all positions and exposure."""
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class SymbolPosition:
    symbol: str
    quantity: float = 0.0
    entry_price: float = 0.0
    unrealised_pnl: float = 0.0
    realised_pnl: float = 0.0
    total_trades: int = 0
    engine_type: str = "directional"
    notional_value: float = 0.0

@dataclass
class AllocatorSnapshot:
    timestamp: float
    total_equity: float
    total_cash: float
    positions: Dict[str, SymbolPosition]
    mm_inventory_total: float
    directional_exposure_raw: float
    directional_exposure_effective: float
    net_delta: float
    symbols_active: List[str]
    risk_score: float = 0.0

class PortfolioState:
    def __init__(self, initial_cash: float = 100000.0):
        self.initial_cash = initial_cash
        self._cash = initial_cash
        self._positions: Dict[str, SymbolPosition] = {}
        self._peak_equity = initial_cash
        self._lock = threading.RLock()
        self._snapshots: List[AllocatorSnapshot] = []
        self._max_snapshots = 1000

    def update_position(self, symbol: str, side: str, price: float,
                        quantity: float, engine_type: str = "directional") -> SymbolPosition:
        with self._lock:
            pos = self._get_or_create(symbol)
            pos.engine_type = engine_type
            if side == "BUY":
                gross_cost = price * quantity
                if pos.quantity > 0:
                    total_basis = pos.entry_price * pos.quantity + price * quantity
                    pos.entry_price = total_basis / (pos.quantity + quantity)
                else:
                    pos.entry_price = price
                pos.quantity += quantity
                self._cash -= gross_cost
            elif side == "SELL":
                gross_proceeds = price * quantity
                trade_pnl = (price - pos.entry_price) * quantity
                pos.realised_pnl += trade_pnl
                pos.quantity -= quantity
                self._cash += gross_proceeds
                if pos.quantity <= 0:
                    pos.quantity = 0.0
                    pos.entry_price = 0.0
            pos.total_trades += 1
            pos.notional_value = pos.quantity * price
            pos.unrealised_pnl = (price - pos.entry_price) * pos.quantity if pos.quantity > 0 else 0.0
            return pos

    def update_mark_to_market(self, prices: Dict[str, float]) -> None:
        with self._lock:
            for symbol, price in prices.items():
                if symbol in self._positions:
                    pos = self._positions[symbol]
                    pos.notional_value = pos.quantity * price
                    pos.unrealised_pnl = (price - pos.entry_price) * pos.quantity if pos.quantity > 0 else 0.0

    def mm_inventory_total(self) -> float:
        with self._lock:
            return sum(p.notional_value for p in self._positions.values()
                       if p.engine_type == "mm" and p.quantity > 0)

    def directional_exposure_raw(self) -> float:
        with self._lock:
            return sum(abs(p.notional_value) for p in self._positions.values()
                       if p.engine_type == "directional" and p.quantity != 0)

    def net_delta(self) -> float:
        with self._lock:
            return sum(p.notional_value for p in self._positions.values() if p.quantity != 0)

    def active_symbols(self) -> List[str]:
        with self._lock:
            return [s for s, p in self._positions.items() if p.quantity != 0]

    def snapshot(self, correlation_adjusted_exposure: float = 0.0,
                 risk_score: float = 0.0) -> AllocatorSnapshot:
        with self._lock:
            total_equity = self._cash + sum(p.notional_value for p in self._positions.values())
            if total_equity > self._peak_equity:
                self._peak_equity = total_equity
            snap = AllocatorSnapshot(
                timestamp=time.time(), total_equity=total_equity, total_cash=self._cash,
                positions=dict(self._positions), mm_inventory_total=self.mm_inventory_total(),
                directional_exposure_raw=self.directional_exposure_raw(),
                directional_exposure_effective=correlation_adjusted_exposure,
                net_delta=self.net_delta(), symbols_active=self.active_symbols(),
                risk_score=risk_score)
            self._snapshots.append(snap)
            if len(self._snapshots) > self._max_snapshots:
                self._snapshots = self._snapshots[-self._max_snapshots:]
            return snap

    def project(self, symbol: str, side: str, price: float,
                quantity: float, engine_type: str = "directional") -> AllocatorSnapshot:
        with self._lock:
            original_cash = self._cash
            original_positions = {
                s: SymbolPosition(symbol=p.symbol, quantity=p.quantity,
                    entry_price=p.entry_price, unrealised_pnl=p.unrealised_pnl,
                    realised_pnl=p.realised_pnl, total_trades=p.total_trades,
                    engine_type=p.engine_type, notional_value=p.notional_value)
                for s, p in self._positions.items()}
        self.update_position(symbol, side, price, quantity, engine_type)
        projected = self.snapshot()
        with self._lock:
            self._cash = original_cash
            self._positions = original_positions
        return projected

    def reset(self, cash: Optional[float] = None) -> None:
        with self._lock:
            self._cash = cash if cash is not None else self.initial_cash
            self._positions.clear()
            self._peak_equity = self._cash
            self._snapshots.clear()

    def _get_or_create(self, symbol: str) -> SymbolPosition:
        if symbol not in self._positions:
            self._positions[symbol] = SymbolPosition(symbol=symbol)
        return self._positions[symbol]
