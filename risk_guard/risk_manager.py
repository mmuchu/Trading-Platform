import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RiskAssessment:
    symbol: str
    side: str
    risk_level: RiskLevel
    max_position_size: float
    suggested_stop_loss: float
    suggested_take_profit: float
    risk_reward_ratio: float
    reasons: List[str] = field(default_factory=list)
    approved: bool = True


@dataclass
class RiskConfig:
    max_portfolio_risk_pct: float = 2.0
    max_total_exposure_pct: float = 80.0
    max_correlated_positions: int = 3
    max_drawdown_pct: float = 15.0
    min_risk_reward_ratio: float = 1.5
    default_stop_loss_pct: float = 2.0
    default_take_profit_pct: float = 3.0
    max_daily_trades: int = 20
    max_open_positions: int = 10
    cooldown_after_loss_minutes: int = 30


class RiskManager:

    def __init__(self, config=None):
        self.config = config or RiskConfig()
        self._daily_trade_count = 0
        self._last_trade_date = None
        self._last_loss_time = None
        self._is_circuit_breaker_active = False
        self._circuit_breaker_reason = ""
        self._trade_history = []
        logger.info("RiskManager initialized")

    def reset_daily_counters(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_trade_date != today:
            self._daily_trade_count = 0
            self._last_trade_date = today

    def record_trade(self, trade_info):
        self.reset_daily_counters()
        self._daily_trade_count += 1
        self._trade_history.append({**trade_info, "timestamp": datetime.now().isoformat()})
        if trade_info.get("pnl", 0) < 0:
            self._last_loss_time = datetime.now()

    def assess_trade(self, symbol, side, entry_price, portfolio_value, current_exposure, open_positions, signal_strength=0.5):
        reasons = []
        risk_level = RiskLevel.LOW
        if self._is_circuit_breaker_active:
            return RiskAssessment(symbol=symbol, side=side, risk_level=RiskLevel.CRITICAL, max_position_size=0.0, suggested_stop_loss=0.0, suggested_take_profit=0.0, risk_reward_ratio=0.0, reasons=["Circuit breaker active"], approved=False)
        self.reset_daily_counters()
        if self._daily_trade_count >= self.config.max_daily_trades:
            return RiskAssessment(symbol=symbol, side=side, risk_level=RiskLevel.HIGH, max_position_size=0.0, suggested_stop_loss=0.0, suggested_take_profit=0.0, risk_reward_ratio=0.0, reasons=["Daily limit reached"], approved=False)
        if open_positions >= self.config.max_open_positions:
            return RiskAssessment(symbol=symbol, side=side, risk_level=RiskLevel.HIGH, max_position_size=0.0, suggested_stop_loss=0.0, suggested_take_profit=0.0, risk_reward_ratio=0.0, reasons=["Max positions reached"], approved=False)
        if self._last_loss_time:
            elapsed = (datetime.now() - self._last_loss_time).total_seconds() / 60
            if elapsed < self.config.cooldown_after_loss_minutes:
                remaining = self.config.cooldown_after_loss_minutes - elapsed
                return RiskAssessment(symbol=symbol, side=side, risk_level=RiskLevel.MEDIUM, max_position_size=0.0, suggested_stop_loss=0.0, suggested_take_profit=0.0, risk_reward_ratio=0.0, reasons=[f"Cooldown {remaining:.0f}min"], approved=False)
        exposure_pct = (current_exposure / portfolio_value * 100) if portfolio_value > 0 else 0
        if exposure_pct >= self.config.max_total_exposure_pct:
            return RiskAssessment(symbol=symbol, side=side, risk_level=RiskLevel.HIGH, max_position_size=0.0, suggested_stop_loss=0.0, suggested_take_profit=0.0, risk_reward_ratio=0.0, reasons=["Max exposure"], approved=False)
        risk_amount = portfolio_value * (self.config.max_portfolio_risk_pct / 100)
        stop_loss_pct = self.config.default_stop_loss_pct * (1.0 + (1.0 - signal_strength))
        max_size = risk_amount / (entry_price * stop_loss_pct / 100)
        remaining_exp_pct = self.config.max_total_exposure_pct - exposure_pct
        exposure_limited_size = (portfolio_value * remaining_exp_pct / 100) / entry_price
        max_size = min(max_size, exposure_limited_size)
        suggested_sl = entry_price * (1 - stop_loss_pct / 100) if side == "buy" else entry_price * (1 + stop_loss_pct / 100)
        tp_pct = stop_loss_pct * self.config.min_risk_reward_ratio
        suggested_tp = entry_price * (1 + tp_pct / 100) if side == "buy" else entry_price * (1 - tp_pct / 100)
        risk_reward = abs(suggested_tp - entry_price) / abs(entry_price - suggested_sl) if suggested_sl != entry_price else 0.0
        if risk_reward < self.config.min_risk_reward_ratio:
            risk_level = RiskLevel.HIGH
            reasons.append("R:R too low")
        elif exposure_pct > self.config.max_total_exposure_pct * 0.7:
            risk_level = RiskLevel.MEDIUM
            reasons.append("High exposure")
        else:
            reasons.append("All params OK")
        approved = risk_reward >= self.config.min_risk_reward_ratio and max_size > 0
        return RiskAssessment(symbol=symbol, side=side, risk_level=risk_level, max_position_size=round(max_size, 6), suggested_stop_loss=round(suggested_sl, 2), suggested_take_profit=round(suggested_tp, 2), risk_reward_ratio=round(risk_reward, 2), reasons=reasons, approved=approved)

    def activate_circuit_breaker(self, reason):
        self._is_circuit_breaker_active = True
        self._circuit_breaker_reason = reason

    def deactivate_circuit_breaker(self):
        self._is_circuit_breaker_active = False
        self._circuit_breaker_reason = ""

    @property
    def is_circuit_breaker_active(self):
        return self._is_circuit_breaker_active

    def get_status(self):
        return {"circuit_breaker_active": self._is_circuit_breaker_active, "daily_trade_count": self._daily_trade_count, "total_trades_recorded": len(self._trade_history)}
