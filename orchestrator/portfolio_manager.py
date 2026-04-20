import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


@dataclass
class Holding:
    symbol: str
    side: str
    quantity: float
    entry_price: float
    current_price: float
    avg_entry_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    realized_pnl: float
    total_commission: float
    open_time: str
    updated_time: str
    metadata: Dict = field(default_factory=dict)

    @property
    def position_value(self) -> float:
        return round(self.quantity * self.current_price, 2)


@dataclass
class PortfolioSnapshot:
    total_value: float
    cash_balance: float
    positions_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    realized_pnl: float
    total_pnl: float
    total_pnl_pct: float
    total_commission: float
    num_positions: int
    timestamp: str


@dataclass
class PortfolioConfig:
    initial_capital: float = 100000.0
    reserve_cash_pct: float = 10.0
    max_position_value_pct: float = 20.0
    default_slippage_pct: float = 0.05
    commission_rate: float = 0.001


class PortfolioManager:

    def __init__(self, config: PortfolioConfig = None):
        self.config = config or PortfolioConfig()
        self._cash = self.config.initial_capital
        self._holdings: Dict[str, Holding] = {}
        self._realized_pnl = 0.0
        self._total_commission = 0.0
        self._snapshots: List[PortfolioSnapshot] = []
        self._closed_trades: List[Dict] = []
        logger.info(f"PortfolioManager initialized (capital={self._cash:.2f})")

    def open_position(self, symbol: str, side: str, quantity: float, price: float, commission: float = 0.0) -> Optional[Holding]:
        cost = quantity * price + commission
        if cost > self._cash:
            affordable = (self._cash - commission) / price if price > 0 else 0
            logger.warning(f"Insufficient cash for {symbol}: need {cost:.2f}, have {self._cash:.2f}. Max qty: {affordable:.6f}")
            return None
        self._cash -= cost
        self._total_commission += commission
        now = datetime.now().isoformat()
        holding = Holding(
            symbol=symbol, side=side, quantity=quantity,
            entry_price=price, current_price=price, avg_entry_price=price,
            unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
            realized_pnl=0.0, total_commission=commission,
            open_time=now, updated_time=now,
        )
        self._holdings[symbol] = holding
        logger.info(f"Position opened: {side} {quantity} {symbol} @ {price:.2f}, cost={cost:.2f}")
        return holding

    def close_position(self, symbol: str, quantity: float = 0, price: float = 0.0) -> Tuple[bool, float, float]:
        holding = self._holdings.get(symbol)
        if not holding:
            logger.warning(f"No position found for {symbol}")
            return False, 0.0, 0.0
        close_qty = quantity if quantity > 0 else holding.quantity
        if close_qty > holding.quantity:
            close_qty = holding.quantity
        if price <= 0:
            price = holding.current_price
        commission = close_qty * price * self.config.commission_rate
        if holding.side == "buy":
            pnl = (price - holding.avg_entry_price) * close_qty - commission
        else:
            pnl = (holding.avg_entry_price - price) * close_qty - commission
        proceeds = close_qty * price - commission
        self._cash += proceeds
        self._realized_pnl += pnl
        self._total_commission += commission
        holding.realized_pnl += pnl
        holding.total_commission += commission
        self._closed_trades.append({
            "symbol": symbol, "side": holding.side, "quantity": close_qty,
            "entry_price": holding.avg_entry_price, "exit_price": price,
            "pnl": round(pnl, 2), "commission": round(commission, 2),
            "open_time": holding.open_time, "close_time": datetime.now().isoformat(),
        })
        holding.quantity -= close_qty
        if holding.quantity < 1e-10:
            del self._holdings[symbol]
            logger.info(f"Position closed: {symbol} pnl={pnl:.2f}")
        else:
            holding.updated_time = datetime.now().isoformat()
            logger.info(f"Partial close: {symbol} {close_qty}/{holding.quantity + close_qty}, pnl={pnl:.2f}")
        return True, pnl, commission

    def update_prices(self, price_map: Dict[str, float]):
        for symbol, holding in self._holdings.items():
            if symbol in price_map:
                holding.current_price = price_map[symbol]
                if holding.side == "buy":
                    holding.unrealized_pnl = round((holding.current_price - holding.avg_entry_price) * holding.quantity, 2)
                else:
                    holding.unrealized_pnl = round((holding.avg_entry_price - holding.current_price) * holding.quantity, 2)
                cost_basis = holding.avg_entry_price * holding.quantity
                holding.unrealized_pnl_pct = round(holding.unrealized_pnl / cost_basis * 100, 4) if cost_basis > 0 else 0
                holding.updated_time = datetime.now().isoformat()

    def add_cash(self, amount: float):
        self._cash += amount
        logger.info(f"Cash added: {amount:.2f}, balance={self._cash:.2f}")

    def get_snapshot(self) -> PortfolioSnapshot:
        positions_value = sum(h.position_value for h in self._holdings.values())
        unrealized_pnl = sum(h.unrealized_pnl for h in self._holdings.values())
        total_value = self._cash + positions_value
        initial = self.config.initial_capital
        unrealized_pct = (unrealized_pnl / (total_value - unrealized_pnl)) * 100 if (total_value - unrealized_pnl) > 0 else 0
        total_pnl = self._realized_pnl + unrealized_pnl
        total_pct = (total_pnl / initial) * 100 if initial > 0 else 0
        snap = PortfolioSnapshot(
            total_value=round(total_value, 2), cash_balance=round(self._cash, 2),
            positions_value=round(positions_value, 2), unrealized_pnl=round(unrealized_pnl, 2),
            unrealized_pnl_pct=round(unrealized_pct, 4), realized_pnl=round(self._realized_pnl, 2),
            total_pnl=round(total_pnl, 2), total_pnl_pct=round(total_pct, 4),
            total_commission=round(self._total_commission, 2),
            num_positions=len(self._holdings),
            timestamp=datetime.now().isoformat(),
        )
        self._snapshots.append(snap)
        return snap

    def get_holding(self, symbol: str) -> Optional[Holding]:
        return self._holdings.get(symbol)

    def get_all_holdings(self) -> Dict[str, Holding]:
        return dict(self._holdings)

    def has_position(self, symbol: str) -> bool:
        return symbol in self._holdings

    def get_available_cash(self) -> float:
        reserve = self.config.initial_capital * self.config.reserve_cash_pct / 100
        return max(0, self._cash - reserve)

    def get_position_symbols(self) -> List[str]:
        return list(self._holdings.keys())

    def get_closed_trades(self, limit: int = 50) -> List[Dict]:
        return self._closed_trades[-limit:]

    def get_status(self) -> Dict:
        snap = self.get_snapshot()
        return {
            "total_value": snap.total_value, "cash": snap.cash_balance,
            "positions_value": snap.positions_value, "num_positions": snap.num_positions,
            "unrealized_pnl": snap.unrealized_pnl, "realized_pnl": snap.realized_pnl,
            "total_pnl": snap.total_pnl, "total_commission": snap.total_commission,
            "symbols": list(self._holdings.keys()),
        }
