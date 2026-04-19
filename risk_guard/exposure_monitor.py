import logging
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from collections import defaultdict

logger = logging.getLogger(__name__)


class ExposureType(Enum):
    LONG = "long"
    SHORT = "short"
    TOTAL = "total"
    BY_SYMBOL = "by_symbol"
    BY_SECTOR = "by_sector"
    BY_CORRELATION = "by_correlation"


@dataclass
class PositionExposure:
    symbol: str
    side: str
    quantity: float
    entry_price: float
    current_price: float
    position_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    weight_pct: float
    sector: str = ""


@dataclass
class ExposureSnapshot:
    total_exposure: float
    total_exposure_pct: float
    long_exposure: float
    long_exposure_pct: float
    short_exposure: float
    short_exposure_pct: float
    net_exposure: float
    net_exposure_pct: float
    num_positions: int
    num_long: int
    num_short: int
    largest_position_pct: float
    largest_position_symbol: str
    sector_exposure: Dict[str, float]
    timestamp: str


@dataclass
class ExposureConfig:
    max_total_exposure_pct: float = 80.0
    max_long_exposure_pct: float = 60.0
    max_short_exposure_pct: float = 40.0
    max_single_position_pct: float = 15.0
    max_sector_exposure_pct: float = 30.0
    max_correlated_exposure_pct: float = 25.0
    correlation_threshold: float = 0.7
    warn_exposure_pct: float = 70.0


class ExposureMonitor:

    def __init__(self, config: ExposureConfig = None):
        self.config = config or ExposureConfig()
        self._positions: Dict[str, PositionExposure] = {}
        self._sector_map: Dict[str, str] = {}
        self._correlation_cache: Dict[Tuple[str, str], float] = {}
        self._exposure_history: List[ExposureSnapshot] = []
        self._peak_exposure_pct: float = 0.0
        logger.info("ExposureMonitor initialized")

    def update_position(self, symbol: str, side: str, quantity: float, entry_price: float, current_price: float, sector: str = ""):
        if quantity <= 0:
            self._positions.pop(symbol, None)
            logger.debug(f"Removed position: {symbol}")
            return
        pos_value = quantity * current_price
        unrealized_pnl = (current_price - entry_price) * quantity if side == "buy" else (entry_price - current_price) * quantity
        pnl_pct = unrealized_pnl / (entry_price * quantity) * 100 if entry_price * quantity > 0 else 0
        self._positions[symbol] = PositionExposure(
            symbol=symbol, side=side, quantity=quantity,
            entry_price=entry_price, current_price=current_price,
            position_value=round(pos_value, 2),
            unrealized_pnl=round(unrealized_pnl, 2),
            unrealized_pnl_pct=round(pnl_pct, 4),
            weight_pct=0.0, sector=sector,
        )
        if sector:
            self._sector_map[symbol] = sector

    def remove_position(self, symbol: str):
        self._positions.pop(symbol, None)
        self._sector_map.pop(symbol, None)

    def update_prices(self, price_map: Dict[str, float]):
        for symbol, pos in self._positions.items():
            if symbol in price_map:
                pos.current_price = price_map[symbol]
                pos.position_value = round(pos.quantity * pos.current_price, 2)
                pnl = (pos.current_price - pos.entry_price) * pos.quantity if pos.side == "buy" else (pos.entry_price - pos.current_price) * pos.quantity
                pos.unrealized_pnl = round(pnl, 2)
                pos.unrealized_pnl_pct = round(pnl / (pos.entry_price * pos.quantity) * 100, 4) if pos.entry_price * pos.quantity > 0 else 0

    def get_snapshot(self, portfolio_value: float) -> ExposureSnapshot:
        if portfolio_value <= 0:
            return self._empty_snapshot()
        long_val = sum(p.position_value for p in self._positions.values() if p.side == "buy")
        short_val = sum(p.position_value for p in self._positions.values() if p.side == "sell")
        total_val = long_val + short_val
        for p in self._positions.values():
            p.weight_pct = round(p.position_value / portfolio_value * 100, 4)
        long_pct = long_val / portfolio_value * 100
        short_pct = short_val / portfolio_value * 100
        total_pct = total_val / portfolio_value * 100
        net_val = long_val - short_val
        net_pct = net_val / portfolio_value * 100
        num_long = sum(1 for p in self._positions.values() if p.side == "buy")
        num_short = sum(1 for p in self._positions.values() if p.side == "sell")
        largest_pct = 0.0
        largest_sym = ""
        for p in self._positions.values():
            if p.weight_pct > largest_pct:
                largest_pct = p.weight_pct
                largest_sym = p.symbol
        sector_exp = defaultdict(float)
        for p in self._positions.values():
            sec = self._sector_map.get(p.symbol, "unknown")
            sector_exp[sec] += p.position_value
        sector_pct = {k: round(v / portfolio_value * 100, 4) for k, v in sector_exp.items()}
        snap = ExposureSnapshot(
            total_exposure=round(total_val, 2), total_exposure_pct=round(total_pct, 4),
            long_exposure=round(long_val, 2), long_exposure_pct=round(long_pct, 4),
            short_exposure=round(short_val, 2), short_exposure_pct=round(short_pct, 4),
            net_exposure=round(net_val, 2), net_exposure_pct=round(net_pct, 4),
            num_positions=len(self._positions), num_long=num_long, num_short=num_short,
            largest_position_pct=round(largest_pct, 4), largest_position_symbol=largest_sym,
            sector_exposure=sector_pct, timestamp=datetime.now().isoformat(),
        )
        self._exposure_history.append(snap)
        if total_pct > self._peak_exposure_pct:
            self._peak_exposure_pct = total_pct
        return snap

    def check_limits(self, portfolio_value: float) -> Dict:
        snap = self.get_snapshot(portfolio_value)
        warnings = []
        violations = []
        if snap.total_exposure_pct > self.config.max_total_exposure_pct:
            violations.append(f"Total exposure {snap.total_exposure_pct:.1f}% > {self.config.max_total_exposure_pct}%")
        elif snap.total_exposure_pct > self.config.warn_exposure_pct:
            warnings.append(f"Total exposure {snap.total_exposure_pct:.1f}% near limit")
        if snap.long_exposure_pct > self.config.max_long_exposure_pct:
            violations.append(f"Long exposure {snap.long_exposure_pct:.1f}% > {self.config.max_long_exposure_pct}%")
        if snap.short_exposure_pct > self.config.max_short_exposure_pct:
            violations.append(f"Short exposure {snap.short_exposure_pct:.1f}% > {self.config.max_short_exposure_pct}%")
        if snap.largest_position_pct > self.config.max_single_position_pct:
            violations.append(f"{snap.largest_position_symbol} = {snap.largest_position_pct:.1f}% > {self.config.max_single_position_pct}%")
        for sector, pct in snap.sector_exposure.items():
            if pct > self.config.max_sector_exposure_pct:
                violations.append(f"Sector {sector}: {pct:.1f}% > {self.config.max_sector_exposure_pct}%")
        correlated_groups = self._find_correlated_groups()
        for group_symbols in correlated_groups:
            group_val = sum(self._positions[s].position_value for s in group_symbols if s in self._positions)
            group_pct = group_val / portfolio_value * 100 if portfolio_value > 0 else 0
            if group_pct > self.config.max_correlated_exposure_pct:
                violations.append(f"Correlated group {group_symbols}: {group_pct:.1f}%")
        return {"warnings": warnings, "violations": violations, "snapshot": snap, "is_safe": len(violations) == 0}

    def can_add_position(self, symbol: str, side: str, value: float, portfolio_value: float, sector: str = "") -> Tuple[bool, str]:
        if portfolio_value <= 0:
            return False, "Portfolio value is zero"
        sim = dict(self._positions)
        existing = sim.get(symbol)
        new_val = (existing.position_value if existing else 0) + value
        current_total = sum(p.position_value for p in sim.values() if p.symbol != symbol) + new_val
        new_pct = current_total / portfolio_value * 100
        if new_pct > self.config.max_total_exposure_pct:
            return False, f"Would exceed total exposure: {new_pct:.1f}% > {self.config.max_total_exposure_pct}%"
        if side == "buy":
            current_long = sum(p.position_value for p in sim.values() if p.side == "buy" and p.symbol != symbol) + new_val
            long_pct = current_long / portfolio_value * 100
            if long_pct > self.config.max_long_exposure_pct:
                return False, f"Would exceed long exposure: {long_pct:.1f}%"
        single_pct = new_val / portfolio_value * 100
        if single_pct > self.config.max_single_position_pct:
            return False, f"Would exceed single position limit: {single_pct:.1f}%"
        if sector:
            sector_val = sum(p.position_value for p in sim.values() if self._sector_map.get(p.symbol) == sector and p.symbol != symbol) + new_val
            sector_pct = sector_val / portfolio_value * 100
            if sector_pct > self.config.max_sector_exposure_pct:
                return False, f"Would exceed sector {sector} limit: {sector_pct:.1f}%"
        return True, "OK"

    def set_correlation(self, symbol_a: str, symbol_b: str, corr: float):
        self._correlation_cache[(symbol_a, symbol_b)] = corr
        self._correlation_cache[(symbol_b, symbol_a)] = corr

    def _find_correlated_groups(self) -> List[List[str]]:
        groups = []
        visited: Set[str] = set()
        symbols = list(self._positions.keys())
        for sym in symbols:
            if sym in visited:
                continue
            group = [sym]
            queue = [sym]
            visited.add(sym)
            while queue:
                current = queue.pop(0)
                for other in symbols:
                    if other in visited:
                        continue
                    corr = self._correlation_cache.get((current, other), 0)
                    if abs(corr) >= self.config.correlation_threshold:
                        group.append(other)
                        visited.add(other)
                        queue.append(other)
            if len(group) > 1:
                groups.append(group)
        return groups

    def _empty_snapshot(self) -> ExposureSnapshot:
        return ExposureSnapshot(
            total_exposure=0, total_exposure_pct=0, long_exposure=0, long_exposure_pct=0,
            short_exposure=0, short_exposure_pct=0, net_exposure=0, net_exposure_pct=0,
            num_positions=0, num_long=0, num_short=0, largest_position_pct=0,
            largest_position_symbol="", sector_exposure={}, timestamp=datetime.now().isoformat(),
        )

    def get_status(self) -> Dict:
        return {
            "num_positions": len(self._positions),
            "symbols": list(self._positions.keys()),
            "peak_exposure_pct": round(self._peak_exposure_pct, 4),
            "correlation_cache_size": len(self._correlation_cache),
            "snapshots_recorded": len(self._exposure_history),
        }
