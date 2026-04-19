import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class StopLossType(Enum):
    FIXED_PERCENT = "fixed_percent"
    FIXED_PRICE = "fixed_price"
    TRAILING_PERCENT = "trailing_percent"
    TRAILING_ATR = "trailing_atr"
    SUPPORT_LEVEL = "support_level"


@dataclass
class StopLossOrder:
    symbol: str
    side: str
    entry_price: float
    stop_loss_price: float
    stop_type: StopLossType
    quantity: float
    is_trailing: bool = False
    trail_offset: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = 0.0
    activation_price: Optional[float] = None
    original_stop: float = 0.0


class StopLossManager:

    def __init__(self):
        self._active_stops: Dict[str, StopLossOrder] = {}
        logger.info("StopLossManager initialized")

    def set_fixed_stop(self, symbol: str, side: str, entry_price: float, stop_pct: float, quantity: float) -> StopLossOrder:
        if side == "buy":
            sl_price = entry_price * (1 - stop_pct / 100)
        else:
            sl_price = entry_price * (1 + stop_pct / 100)
        order = StopLossOrder(
            symbol=symbol, side=side, entry_price=entry_price,
            stop_loss_price=round(sl_price, 2), stop_type=StopLossType.FIXED_PERCENT,
            quantity=quantity, original_stop=round(sl_price, 2),
        )
        self._active_stops[symbol] = order
        logger.info(f"Fixed stop set for {symbol}: {sl_price:.2f} ({stop_pct:.2f}%)")
        return order

    def set_fixed_price_stop(self, symbol: str, side: str, entry_price: float, stop_price: float, quantity: float) -> StopLossOrder:
        order = StopLossOrder(
            symbol=symbol, side=side, entry_price=entry_price,
            stop_loss_price=stop_price, stop_type=StopLossType.FIXED_PRICE,
            quantity=quantity, original_stop=stop_price,
        )
        self._active_stops[symbol] = order
        logger.info(f"Fixed price stop set for {symbol}: {stop_price:.2f}")
        return order

    def set_trailing_stop(self, symbol: str, side: str, entry_price: float, trail_pct: float, quantity: float, activation_pct: Optional[float] = None) -> StopLossOrder:
        initial_sl = self._calc_initial_sl(side, entry_price, trail_pct)
        act_price = None
        if activation_pct is not None:
            act_price = entry_price * (1 + activation_pct / 100) if side == "buy" else entry_price * (1 - activation_pct / 100)
        order = StopLossOrder(
            symbol=symbol, side=side, entry_price=entry_price,
            stop_loss_price=round(initial_sl, 2), stop_type=StopLossType.TRAILING_PERCENT,
            quantity=quantity, is_trailing=True, trail_offset=trail_pct,
            highest_price=entry_price, lowest_price=entry_price,
            activation_price=round(act_price, 2) if act_price else None,
            original_stop=round(initial_sl, 2),
        )
        self._active_stops[symbol] = order
        logger.info(f"Trailing stop set for {symbol}: trail={trail_pct:.2f}%, initial_sl={initial_sl:.2f}")
        return order

    def set_atr_stop(self, symbol: str, side: str, entry_price: float, atr_value: float, atr_multiplier: float, quantity: float) -> StopLossOrder:
        atr_dist = atr_value * atr_multiplier
        if side == "buy":
            sl_price = entry_price - atr_dist
        else:
            sl_price = entry_price + atr_dist
        order = StopLossOrder(
            symbol=symbol, side=side, entry_price=entry_price,
            stop_loss_price=round(sl_price, 2), stop_type=StopLossType.TRAILING_ATR,
            quantity=quantity, is_trailing=True, trail_offset=atr_dist,
            highest_price=entry_price, lowest_price=entry_price,
            original_stop=round(sl_price, 2),
        )
        self._active_stops[symbol] = order
        logger.info(f"ATR stop set for {symbol}: ATR={atr_value:.4f} x{atr_multiplier:.1f}, sl={sl_price:.2f}")
        return order

    def set_support_stop(self, symbol: str, side: str, entry_price: float, support_price: float, buffer_pct: float, quantity: float) -> StopLossOrder:
        if side == "buy":
            sl_price = support_price * (1 - buffer_pct / 100)
        else:
            sl_price = support_price * (1 + buffer_pct / 100)
        order = StopLossOrder(
            symbol=symbol, side=side, entry_price=entry_price,
            stop_loss_price=round(sl_price, 2), stop_type=StopLossType.SUPPORT_LEVEL,
            quantity=quantity, original_stop=round(sl_price, 2),
        )
        self._active_stops[symbol] = order
        logger.info(f"Support stop set for {symbol}: support={support_price:.2f}, buffer={buffer_pct:.1f}%, sl={sl_price:.2f}")
        return order

    def update_trailing_stops(self, symbol: str, current_price: float) -> Optional[StopLossOrder]:
        order = self._active_stops.get(symbol)
        if not order or not order.is_trailing:
            return None
        if order.activation_price is not None:
            if order.side == "buy" and current_price < order.activation_price:
                return order
            if order.side == "sell" and current_price > order.activation_price:
                return order
        old_sl = order.stop_loss_price
        if order.side == "buy":
            if current_price > order.highest_price:
                order.highest_price = current_price
                new_sl = current_price * (1 - order.trail_offset / 100) if order.stop_type == StopLossType.TRAILING_PERCENT else current_price - order.trail_offset
                new_sl = round(new_sl, 2)
                if new_sl > old_sl:
                    order.stop_loss_price = new_sl
        else:
            if current_price < order.lowest_price:
                order.lowest_price = current_price
                new_sl = current_price * (1 + order.trail_offset / 100) if order.stop_type == StopLossType.TRAILING_PERCENT else current_price + order.trail_offset
                new_sl = round(new_sl, 2)
                if new_sl < old_sl:
                    order.stop_loss_price = new_sl
        if order.stop_loss_price != old_sl:
            logger.debug(f"{symbol} trailing stop updated: {old_sl:.2f} -> {order.stop_loss_price:.2f}")
        return order

    def check_stop_triggered(self, symbol: str, current_price: float) -> Tuple[bool, Optional[StopLossOrder]]:
        order = self._active_stops.get(symbol)
        if not order:
            return False, None
        self.update_trailing_stops(symbol, current_price)
        triggered = False
        if order.side == "buy" and current_price <= order.stop_loss_price:
            triggered = True
        elif order.side == "sell" and current_price >= order.stop_loss_price:
            triggered = True
        if triggered:
            logger.warning(f"STOP LOSS TRIGGERED for {symbol}: price={current_price:.2f}, stop={order.stop_loss_price:.2f}")
        return triggered, order if triggered else None

    def remove_stop(self, symbol: str) -> bool:
        if symbol in self._active_stops:
            del self._active_stops[symbol]
            logger.info(f"Stop removed for {symbol}")
            return True
        return False

    def get_stop(self, symbol: str) -> Optional[StopLossOrder]:
        return self._active_stops.get(symbol)

    def get_all_stops(self) -> Dict[str, StopLossOrder]:
        return dict(self._active_stops)

    def _calc_initial_sl(self, side: str, entry: float, trail_pct: float) -> float:
        if side == "buy":
            return entry * (1 - trail_pct / 100)
        else:
            return entry * (1 + trail_pct / 100)

    
    def get_status(self) -> Dict:
        return {'active_stops': len(self._active_stops), 'symbols': list(self._active_stops.keys()), 'details': [{'symbol': o.symbol, 'type': o.stop_type.value, 'sl': o.stop_loss_price, 'trailing': o.is_trailing, 'original': o.original_stop} for o in self._active_stops.values()]}
