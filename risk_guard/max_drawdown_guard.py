import logging
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger(__name__)


class DrawdownAction(Enum):
    NONE = "none"
    REDUCE_POSITIONS = "reduce_positions"
    CLOSE_ALL = "close_all"
    HALT_TRADING = "halt_trading"
    NOTIFY_ONLY = "notify_only"


@dataclass
class DrawdownLevel:
    threshold_pct: float
    action: DrawdownAction
    description: str
    reduction_pct: float = 0.0


@dataclass
class DrawdownState:
    current_drawdown_pct: float
    peak_equity: float
    current_equity: float
    is_in_drawdown: bool
    drawdown_start: Optional[str]
    drawdown_duration_days: int
    active_level: Optional[DrawdownLevel]
    recovery_start: Optional[str]
    consecutive_loss_days: int
    is_halted: bool
    halt_reason: str


@dataclass
class DrawdownConfig:
    max_drawdown_pct: float = 15.0
    warning_drawdown_pct: float = 10.0
    moderate_drawdown_pct: float = 12.0
    severe_drawdown_pct: float = 15.0
    critical_drawdown_pct: float = 20.0
    max_recovery_days: int = 30
    max_consecutive_loss_days: int = 5
    daily_loss_limit_pct: float = 3.0
    weekly_loss_limit_pct: float = 7.0
    reduction_on_warning: float = 25.0
    reduction_on_moderate: float = 50.0
    reduction_on_severe: float = 75.0
    auto_halt_on_critical: bool = True
    trailing_peak_window_days: int = 30


class MaxDrawdownGuard:

    def __init__(self, config: DrawdownConfig = None, on_alert: Optional[Callable] = None):
        self.config = config or DrawdownConfig()
        self._on_alert = on_alert
        self._peak_equity: float = 0.0
        self._current_equity: float = 0.0
        self._initial_equity: float = 0.0
        self._is_in_drawdown: bool = False
        self._drawdown_start: Optional[datetime] = None
        self._recovery_start: Optional[datetime] = None
        self._consecutive_loss_days: int = 0
        self._is_halted: bool = False
        self._halt_reason: str = ""
        self._daily_pnl_history: List[Dict] = []
        self._equity_curve: List[Dict] = []
        self._drawdown_levels: List[DrawdownLevel] = []
        self._setup_levels()
        logger.info(f"MaxDrawdownGuard initialized (max_dd={self.config.max_drawdown_pct}%)")

    def _setup_levels(self):
        self._drawdown_levels = [
            DrawdownLevel(self.config.warning_drawdown_pct, DrawdownAction.NOTIFY_ONLY, "Warning drawdown"),
            DrawdownLevel(self.config.moderate_drawdown_pct, DrawdownAction.REDUCE_POSITIONS, "Moderate drawdown", self.config.reduction_on_moderate),
            DrawdownLevel(self.config.severe_drawdown_pct, DrawdownAction.REDUCE_POSITIONS, "Severe drawdown", self.config.reduction_on_severe),
            DrawdownLevel(self.config.critical_drawdown_pct, DrawdownAction.HALT_TRADING if self.config.auto_halt_on_critical else DrawdownAction.CLOSE_ALL, "Critical drawdown"),
        ]

    def initialize(self, initial_equity: float):
        self._initial_equity = initial_equity
        self._peak_equity = initial_equity
        self._current_equity = initial_equity
        self._equity_curve = [{"timestamp": datetime.now().isoformat(), "equity": initial_equity}]
        logger.info(f"DrawdownGuard initialized with equity: {initial_equity:.2f}")

    def update_equity(self, current_equity: float) -> DrawdownState:
        self._current_equity = current_equity
        self._equity_curve.append({"timestamp": datetime.now().isoformat(), "equity": current_equity})
        if current_equity > self._peak_equity:
            old_peak = self._peak_equity
            self._peak_equity = current_equity
            if self._is_in_drawdown:
                self._is_in_drawdown = False
                self._recovery_start = datetime.now()
                dd_recovered = (old_peak - current_equity) / old_peak * 100 if old_peak > 0 else 0
                logger.info(f"Drawdown recovered! Peak updated: {old_peak:.2f} -> {current_equity:.2f}, recovered DD: {dd_recovered:.2f}%")
                self._consecutive_loss_days = 0
        dd_pct = self._calc_drawdown()
        if dd_pct > 0 and not self._is_in_drawdown:
            self._is_in_drawdown = True
            self._drawdown_start = datetime.now()
            logger.warning(f"Drawdown started! DD: {dd_pct:.2f}%")
        active_level = self._get_active_level(dd_pct)
        if active_level:
            self._execute_action(active_level, dd_pct)
        if self._is_in_drawdown and self._drawdown_start:
            duration = (datetime.now() - self._drawdown_start).days
            if duration > self.config.max_recovery_days:
                logger.error(f"Drawdown duration {duration}d exceeds max {self.config.max_recovery_days}d. Halting.")
                self._is_halted = True
                self._halt_reason = f"Drawdown lasted {duration} days without recovery"
                self._fire_alert("DRAWDOWN_DURATION", f"Duration {duration}d > {self.config.max_recovery_days}d")
        return self._build_state(dd_pct, active_level)

    def record_daily_pnl(self, daily_pnl: float, portfolio_value: float):
        pnl_pct = daily_pnl / portfolio_value * 100 if portfolio_value > 0 else 0
        self._daily_pnl_history.append({"date": datetime.now().strftime("%Y-%m-%d"), "pnl": daily_pnl, "pnl_pct": round(pnl_pct, 4)})
        if daily_pnl < 0:
            self._consecutive_loss_days += 1
        else:
            self._consecutive_loss_days = 0
        if self._consecutive_loss_days >= self.config.max_consecutive_loss_days:
            logger.warning(f"Consecutive loss days: {self._consecutive_loss_days}")
            self._fire_alert("CONSECUTIVE_LOSSES", f"{self._consecutive_loss_days} consecutive losing days")
        if pnl_pct < 0 and abs(pnl_pct) > self.config.daily_loss_limit_pct:
            logger.error(f"Daily loss {abs(pnl_pct):.2f}% exceeds limit {self.config.daily_loss_limit_pct}%")
            self._fire_alert("DAILY_LOSS_LIMIT", f"Daily loss: {abs(pnl_pct):.2f}%")
        weekly_pnl = sum(d["pnl"] for d in self._daily_pnl_history[-5:])
        weekly_pct = weekly_pnl / portfolio_value * 100 if portfolio_value > 0 else 0
        if weekly_pct < 0 and abs(weekly_pct) > self.config.weekly_loss_limit_pct:
            logger.error(f"Weekly loss {abs(weekly_pct):.2f}% exceeds limit {self.config.weekly_loss_limit_pct}%")
            self._fire_alert("WEEKLY_LOSS_LIMIT", f"Weekly loss: {abs(weekly_pct):.2f}%")

    def _calc_drawdown(self) -> float:
        if self._peak_equity <= 0:
            return 0.0
        return (self._peak_equity - self._current_equity) / self._peak_equity * 100

    def _get_active_level(self, dd_pct: float) -> Optional[DrawdownLevel]:
        active = None
        for level in self._drawdown_levels:
            if dd_pct >= level.threshold_pct:
                active = level
        return active

    def _execute_action(self, level: DrawdownLevel, dd_pct: float):
        self._fire_alert("DRAWDOWN_LEVEL", f"{level.description}: {dd_pct:.2f}% (threshold: {level.threshold_pct}%)")
        if level.action == DrawdownAction.REDUCE_POSITIONS:
            logger.warning(f"Action: Reduce positions by {level.reduction_pct}%")
        elif level.action == DrawdownAction.CLOSE_ALL:
            logger.error("Action: Close ALL positions!")
        elif level.action == DrawdownAction.HALT_TRADING:
            self._is_halted = True
            self._halt_reason = f"{level.description}: {dd_pct:.2f}%"
            logger.critical(f"Action: HALT TRADING! {self._halt_reason}")

    def _fire_alert(self, alert_type: str, message: str):
        if self._on_alert:
            try:
                self._on_alert(alert_type, message, self._build_state(self._calc_drawdown(), None))
            except Exception as e:
                logger.error(f"Alert callback failed: {e}")

    def _build_state(self, dd_pct: float, active_level: Optional[DrawdownLevel]) -> DrawdownState:
        duration = 0
        if self._is_in_drawdown and self._drawdown_start:
            duration = (datetime.now() - self._drawdown_start).days
        return DrawdownState(
            current_drawdown_pct=round(dd_pct, 4),
            peak_equity=round(self._peak_equity, 2),
            current_equity=round(self._current_equity, 2),
            is_in_drawdown=self._is_in_drawdown,
            drawdown_start=self._drawdown_start.isoformat() if self._drawdown_start else None,
            drawdown_duration_days=duration,
            active_level=active_level,
            recovery_start=self._recovery_start.isoformat() if self._recovery_start else None,
            consecutive_loss_days=self._consecutive_loss_days,
            is_halted=self._is_halted,
            halt_reason=self._halt_reason,
        )

    def reset_halt(self):
        self._is_halted = False
        self._halt_reason = ""
        logger.info("Trading halt reset")

    def is_halted(self) -> bool:
        return self._is_halted

    def get_required_reduction(self) -> float:
        dd_pct = self._calc_drawdown()
        level = self._get_active_level(dd_pct)
        if level and level.action == DrawdownAction.REDUCE_POSITIONS:
            return level.reduction_pct
        return 0.0

    def get_status(self) -> Dict:
        dd_pct = self._calc_drawdown()
        return {
            "current_drawdown_pct": round(dd_pct, 4),
            "peak_equity": round(self._peak_equity, 2),
            "current_equity": round(self._current_equity, 2),
            "is_in_drawdown": self._is_in_drawdown,
            "is_halted": self._is_halted,
            "halt_reason": self._halt_reason,
            "consecutive_loss_days": self._consecutive_loss_days,
            "required_reduction_pct": self.get_required_reduction(),
        }
