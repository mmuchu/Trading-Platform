import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class TakeProfitType(Enum):
    FIXED_PERCENT = "fixed_percent"
    FIXED_PRICE = "fixed_price"
    TIERED = "tiered"
    TRAILING = "trailing"


@dataclass
class TierLevel:
    level: int
    percent: float
    close_fraction: float


@dataclass
class TakeProfitOrder:
    symbol: str
    side: str
    entry_price: float
    tp_type: TakeProfitType
    quantity: float
    tp_price: float = 0.0
    tiers: List[TierLevel] = None
    is_trailing: bool = False
    trail_offset: float = 0.0
    highest_since_tp: float = 0.0
    lowest_since_tp: float = 0.0
    triggered_tiers: List[int] = None
    original_tp: float = 0.0

    def __post_init__(self):
        if self.tiers is None:
            self.tiers = []
        if self.triggered_tiers is None:
            self.triggered_tiers = []


class TakeProfitManager:

    def __init__(self):
        self._active_tps: Dict[str, TakeProfitOrder] = {}
        logger.info("TakeProfitManager initialized")

    def set_fixed_tp(self, symbol, side, entry_price, tp_pct, quantity):
        if side == 'buy':
            tp_price = entry_price * (1 + tp_pct / 100)
        else:
            tp_price = entry_price * (1 - tp_pct / 100)
        order = TakeProfitOrder(symbol=symbol, side=side, entry_price=entry_price,
            tp_type=TakeProfitType.FIXED_PERCENT, quantity=quantity,
            tp_price=round(tp_price, 2), original_tp=round(tp_price, 2))
        self._active_tps[symbol] = order
        logger.info(f"Fixed TP set for {symbol}: {tp_price:.2f}")
        return order

    def set_fixed_price_tp(self, symbol, side, entry_price, tp_price, quantity):
        order = TakeProfitOrder(symbol=symbol, side=side, entry_price=entry_price,
            tp_type=TakeProfitType.FIXED_PRICE, quantity=quantity,
            tp_price=tp_price, original_tp=tp_price)
        self._active_tps[symbol] = order
        return order

    def set_tiered_tp(self, symbol, side, entry_price, quantity, tiers):
        tier_levels = []
        for idx, (pct, close_frac) in enumerate(tiers):
            if side == 'buy':
                price = entry_price * (1 + pct / 100)
            else: price = entry_price * (1 - pct / 100)
            tier_levels.append(TierLevel(level=idx+1, percent=pct, close_fraction=close_frac))
        order = TakeProfitOrder(symbol=symbol, side=side, entry_price=entry_price,
            tp_type=TakeProfitType.TIERED, quantity=quantity, tiers=tier_levels)
        self._active_tps[symbol] = order
        return order

    def set_trailing_tp(self, symbol, side, entry_price, trail_pct, quantity, activation_pct=0.5):
        act = entry_price*(1+activation_pct/100) if side=='buy' else entry_price*(1-activation_pct/100)
        order = TakeProfitOrder(symbol=symbol, side=side, entry_price=entry_price,
            tp_type=TakeProfitType.TRAILING, quantity=quantity,
            is_trailing=True, trail_offset=trail_pct,
            highest_since_tp=entry_price, lowest_since_tp=entry_price,
            original_tp=round(act, 2))
        self._active_tps[symbol] = order
        return order

    def check_tp_triggered(self, symbol, current_price):
        order = self._active_tps.get(symbol)
        if not order: return False, None
        if order.tp_type == TakeProfitType.TIERED:
            return self._check_tiered(symbol, current_price, order)
        if order.tp_type == TakeProfitType.TRAILING:
            return self._check_trailing(symbol, current_price, order)
        triggered = (order.side=='buy' and current_price>=order.tp_price) or (order.side=='sell' and current_price<=order.tp_price)
        if triggered:
            pp = self._calc_pnl_pct(order, current_price)
            return True, {'symbol':symbol,'side':order.side,'price':current_price,'quantity':order.quantity,'type':order.tp_type.value,'pnl_pct':round(pp,4)}
        return False, None

    def _check_tiered(self, symbol, current_price, order):
        actions = []
        closed = 0.0
        for tier in order.tiers:
            if tier.level in order.triggered_tiers:
                closed += tier.close_fraction
                continue
            tp = order.entry_price*(1+tier.percent/100) if order.side=='buy' else order.entry_price*(1-tier.percent/100)
            hit = (order.side=='buy' and current_price>=tp) or (order.side=='sell' and current_price<=tp)
            if hit:
                order.triggered_tiers.append(tier.level)
                cq = order.quantity * tier.close_fraction
                closed += tier.close_fraction
                actions.append({'tier':tier.level,'close_fraction':tier.close_fraction,'close_quantity':round(cq,6),'price':round(tp,2)})
        if actions:
            rem = max(0, 1.0 - closed)
            return True, {'symbol':symbol,'side':order.side,'type':'tiered','actions':actions,'remaining_quantity':round(order.quantity*rem,6),'all_tiers_hit':len(order.triggered_tiers)==len(order.tiers)}
        return False, None

    def _check_trailing(self, symbol, current_price, order):
        activated = (order.side=='buy' and current_price>=order.original_tp) or (order.side=='sell' and current_price<=order.original_tp)
        if not activated: return False, None
        if order.side=='buy':
            if current_price > order.highest_since_tp: order.highest_since_tp = current_price
            tp = order.highest_since_tp*(1-order.trail_offset/100)
        else:
            if current_price < order.lowest_since_tp: order.lowest_since_tp = current_price
            tp = order.lowest_since_tp*(1+order.trail_offset/100)
        tp = round(tp, 2)
        order.tp_price = tp
        hit = (order.side=='buy' and current_price<=tp) or (order.side=='sell' and current_price>=tp)
        if hit:
            pp = self._calc_pnl_pct(order, current_price)
            return True, {'symbol':symbol,'side':order.side,'price':current_price,'quantity':order.quantity,'type':'trailing','pnl_pct':round(pp,4)}
        return False, None

    def remove_tp(self, symbol):
        return self._active_tps.pop(symbol, None) is not None

    def get_tp(self, symbol): return self._active_tps.get(symbol)
    def get_all_tps(self): return dict(self._active_tps)
    def _calc_pnl_pct(self, order, current_price):
        if order.side == 'buy': return (current_price - order.entry_price) / order.entry_price * 100
        return (order.entry_price - current_price) / order.entry_price * 100

    def get_status(self):
        return {'active_tps': len(self._active_tps), 'symbols': list(self._active_tps.keys()),
            'details': [{'symbol':o.symbol,'type':o.tp_type.value,'tp':o.tp_price,'trailing':o.is_trailing} for o in self._active_tps.values()]}