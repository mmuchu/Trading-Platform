import logging
import math
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class SizingMethod(Enum):
    FIXED_FRACTION = "fixed_fraction"
    KELLY = "kelly"
    VOLATILITY = "volatility"
    ATR = "atr"
    RISK_PER_TRADE = "risk_per_trade"
    EQUAL_WEIGHT = "equal_weight"


@dataclass
class PositionSize:
    symbol: str
    method: SizingMethod
    quantity: float
    position_value: float
    risk_amount: float
    risk_pct: float
    max_loss: float
    details: Dict


@dataclass
class SizingConfig:
    default_method: SizingMethod = SizingMethod.FIXED_FRACTION
    max_position_pct: float = 20.0
    max_single_risk_pct: float = 2.0
    min_position_size: float = 0.001
    max_position_size: float = 1000.0
    kelly_fraction_half: bool = True
    volatility_window: int = 20
    atr_period: int = 14
    equal_weight_pct: float = 10.0


class PositionSizer:

    def __init__(self, config: SizingConfig = None):
        self.config = config or SizingConfig()
        logger.info(f"PositionSizer initialized (method={self.config.default_method.value})")

    def calculate_size(self, method: Optional[SizingMethod] = None, symbol: str = "", entry_price: float = 0,
                       stop_loss_price: float = 0, portfolio_value: float = 10000,
                       signal_strength: float = 0.5, win_rate: float = 0.5,
                       avg_win_pct: float = 2.0, avg_loss_pct: float = 1.0,
                       current_volatility: Optional[float] = None,
                       atr_value: Optional[float] = None,
                       num_symbols: int = 1) -> PositionSize:
        method = method or self.config.default_method
        if entry_price <= 0 or portfolio_value <= 0:
            return self._zero_size(symbol, method)
        if method == SizingMethod.FIXED_FRACTION:
            return self._fixed_fraction(symbol, entry_price, stop_loss_price, portfolio_value, signal_strength)
        elif method == SizingMethod.KELLY:
            return self._kelly(symbol, entry_price, stop_loss_price, portfolio_value, win_rate, avg_win_pct, avg_loss_pct)
        elif method == SizingMethod.VOLATILITY:
            vol = current_volatility or 0.02
            return self._volatility(symbol, entry_price, stop_loss_price, portfolio_value, vol, signal_strength)
        elif method == SizingMethod.ATR:
            atr = atr_value or 0
            return self._atr(symbol, entry_price, stop_loss_price, portfolio_value, atr)
        elif method == SizingMethod.RISK_PER_TRADE:
            return self._risk_per_trade(symbol, entry_price, stop_loss_price, portfolio_value)
        elif method == SizingMethod.EQUAL_WEIGHT:
            return self._equal_weight(symbol, entry_price, portfolio_value, num_symbols)
        return self._zero_size(symbol, method)

    def _fixed_fraction(self, symbol: str, entry: float, sl: float, pv: float, strength: float) -> PositionSize:
        risk_pct = self.config.max_single_risk_pct * (0.5 + 0.5 * strength)
        risk_amount = pv * risk_pct / 100
        if sl <= 0 or sl >= entry:
            return self._zero_size(symbol, SizingMethod.FIXED_FRACTION)
        risk_per_unit = entry - sl
        quantity = risk_amount / risk_per_unit
        quantity = self._clamp(symbol, quantity, entry, pv)
        pos_value = quantity * entry
        max_loss = risk_per_unit * quantity
        return PositionSize(symbol=symbol, method=SizingMethod.FIXED_FRACTION, quantity=quantity,
                            position_value=round(pos_value, 2), risk_amount=round(risk_amount, 2),
                            risk_pct=round(risk_pct, 4), max_loss=round(max_loss, 2),
                            details={"risk_pct": risk_pct, "strength": strength})

    def _kelly(self, symbol: str, entry: float, sl: float, pv: float, wr: float, avg_w: float, avg_l: float) -> PositionSize:
        if avg_l <= 0:
            return self._zero_size(symbol, SizingMethod.KELLY)
        b = avg_w / avg_l
        kelly = wr - ((1 - wr) / b)
        kelly = max(0, kelly)
        if self.config.kelly_fraction_half:
            kelly *= 0.5
        kelly = min(kelly, self.config.max_position_pct / 100)
        risk_amount = pv * kelly
        if sl <= 0 or sl >= entry:
            return self._zero_size(symbol, SizingMethod.KELLY)
        risk_per_unit = abs(entry - sl)
        quantity = risk_amount / risk_per_unit if risk_per_unit > 0 else 0
        quantity = self._clamp(symbol, quantity, entry, pv)
        pos_value = quantity * entry
        max_loss = risk_per_unit * quantity
        return PositionSize(symbol=symbol, method=SizingMethod.KELLY, quantity=quantity,
                            position_value=round(pos_value, 2), risk_amount=round(risk_amount, 2),
                            risk_pct=round(kelly * 100, 4), max_loss=round(max_loss, 2),
                            details={"kelly_raw": round(kelly * (2 if self.config.kelly_fraction_half else 1), 4),
                                     "kelly_used": round(kelly, 4), "win_rate": wr})

    def _volatility(self, symbol: str, entry: float, sl: float, pv: float, vol: float, strength: float) -> PositionSize:
        target_vol = 0.01 * (0.5 + 0.5 * strength)
        if vol <= 0:
            vol = 0.02
        vol_adj = target_vol / vol
        pos_value = pv * min(vol_adj, self.config.max_position_pct / 100)
        quantity = pos_value / entry
        quantity = self._clamp(symbol, quantity, entry, pv)
        pos_value = quantity * entry
        risk_per_unit = abs(entry - sl) if sl > 0 else entry * 0.02
        max_loss = risk_per_unit * quantity
        return PositionSize(symbol=symbol, method=SizingMethod.VOLATILITY, quantity=quantity,
                            position_value=round(pos_value, 2), risk_amount=round(max_loss, 2),
                            risk_pct=round(max_loss / pv * 100, 4), max_loss=round(max_loss, 2),
                            details={"volatility": round(vol, 6), "target_vol": round(target_vol, 6), "vol_adj": round(vol_adj, 4)})

    def _atr(self, symbol: str, entry: float, sl: float, pv: float, atr: float) -> PositionSize:
        if atr <= 0:
            return self._zero_size(symbol, SizingMethod.ATR)
        risk_amount = pv * self.config.max_single_risk_pct / 100
        atr_multiplier = 2.0
        risk_per_unit = atr * atr_multiplier
        quantity = risk_amount / risk_per_unit
        quantity = self._clamp(symbol, quantity, entry, pv)
        pos_value = quantity * entry
        max_loss = risk_per_unit * quantity
        return PositionSize(symbol=symbol, method=SizingMethod.ATR, quantity=quantity,
                            position_value=round(pos_value, 2), risk_amount=round(risk_amount, 2),
                            risk_pct=round(max_loss / pv * 100, 4), max_loss=round(max_loss, 2),
                            details={"atr": round(atr, 6), "atr_multiplier": atr_multiplier})

    def _risk_per_trade(self, symbol: str, entry: float, sl: float, pv: float) -> PositionSize:
        risk_amount = pv * self.config.max_single_risk_pct / 100
        if sl <= 0 or sl >= entry:
            return self._zero_size(symbol, SizingMethod.RISK_PER_TRADE)
        risk_per_unit = abs(entry - sl)
        quantity = risk_amount / risk_per_unit
        quantity = self._clamp(symbol, quantity, entry, pv)
        pos_value = quantity * entry
        max_loss = risk_per_unit * quantity
        return PositionSize(symbol=symbol, method=SizingMethod.RISK_PER_TRADE, quantity=quantity,
                            position_value=round(pos_value, 2), risk_amount=round(risk_amount, 2),
                            risk_pct=round(self.config.max_single_risk_pct, 4), max_loss=round(max_loss, 2),
                            details={})

    def _equal_weight(self, symbol: str, entry: float, pv: float, num_symbols: int) -> PositionSize:
        weight_pct = min(self.config.equal_weight_pct, self.config.max_position_pct) / num_symbols
        pos_value = pv * weight_pct / 100
        quantity = pos_value / entry
        quantity = self._clamp(symbol, quantity, entry, pv)
        pos_value = quantity * entry
        return PositionSize(symbol=symbol, method=SizingMethod.EQUAL_WEIGHT, quantity=quantity,
                            position_value=round(pos_value, 2), risk_amount=0,
                            risk_pct=round(weight_pct, 4), max_loss=0,
                            details={"weight_pct": round(weight_pct, 4), "num_symbols": num_symbols})

    def _clamp(self, symbol: str, quantity: float, entry: float, pv: float) -> float:
        max_qty = (pv * self.config.max_position_pct / 100) / entry
        quantity = min(quantity, max_qty)
        min_qty = self.config.min_position_size
        if quantity < min_qty:
            return 0.0
        return round(quantity, 6)

    def _zero_size(self, symbol: str, method: SizingMethod) -> PositionSize:
        return PositionSize(symbol=symbol, method=method, quantity=0, position_value=0,
                            risk_amount=0, risk_pct=0, max_loss=0, details={"reason": "invalid_params"})

    def batch_size(self, signals: List[Dict], portfolio_value: float, total_budget_pct: float = 50.0) -> List[PositionSize]:
        budget = portfolio_value * total_budget_pct / 100
        results = []
        total_value = 0
        for sig in signals:
            remaining = budget - total_value
            if remaining <= 0:
                break
            ps = self.calculate_size(
                symbol=sig.get("symbol", ""), entry_price=sig.get("entry_price", 0),
                stop_loss_price=sig.get("stop_loss", 0), portfolio_value=remaining,
                signal_strength=sig.get("strength", 0.5), method=sig.get("method"),
            )
            if ps.quantity > 0:
                results.append(ps)
                total_value += ps.position_value
        return results

    def get_status(self) -> Dict:
        return {"method": self.config.default_method.value,
                "max_position_pct": self.config.max_position_pct,
                "max_single_risk_pct": self.config.max_single_risk_pct}
